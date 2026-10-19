"""Bounded CUDA graph replay for the exact, float32 inference computation.

Only setup captures graphs. Every step copies the complete current batch and
executes the neural network; neither decisions nor observations are reused.
Uncached object counts use ordinary SDPA inference with the same parameters.
"""

from time import perf_counter


class CudaInference:
    # Nine small, exact input shapes cover the pilot's usual nearby actors.
    # Larger observations remain supported without padding, truncation, or a
    # graph capture pause inside the control loop.
    CAPTURED_OBJECT_COUNTS = tuple(range(9))

    def __init__(self, model, torch, device, *, graphs=True):
        self.model, self.torch, self.device = model, torch, device
        self.graphs = {}
        self.last_backend = "cuda_float32_sdpa"
        self.reset_warmup_wall_time_s = 0.
        self.reset_warmup_passes = 0
        allocated_before = torch.cuda.memory_allocated(device)
        started = perf_counter()
        counts = self.CAPTURED_OBJECT_COUNTS if graphs else (0, 1)
        stream = torch.cuda.Stream(device=device)
        stream.wait_stream(torch.cuda.current_stream(device))
        for count in counts:
            batch = self._empty_batch(count)
            # cuBLAS/cuDNN and attention kernels must initialize outside the
            # capture. This also keeps their first-use cost out of step timing.
            with torch.cuda.stream(stream), torch.inference_mode():
                for _ in range(3):
                    model(batch)
            torch.cuda.current_stream(device).wait_stream(stream)
            if graphs:
                graph = torch.cuda.CUDAGraph()
                with torch.cuda.graph(graph), torch.inference_mode():
                    outputs = model(batch)
                self.graphs[count] = (graph, batch, outputs)
        torch.cuda.synchronize(device)
        self.warmup_wall_time_s = perf_counter() - started
        self.graph_allocated_bytes = max(0, torch.cuda.memory_allocated(device) - allocated_before)

    def prepare_run(self):
        """Warm kernels after simulator setup, before any observed decision.

        Map loading and actor creation can leave the GPU idle after load(). A
        bounded burst at reset avoids charging initial idle-clock work to the
        first driving frames. Zero-input results are never returned or used by
        the tracking controller, and this does not capture any new graphs.
        """
        torch = self.torch
        started = perf_counter()
        batch = self._empty_batch(0)
        cached = self.graphs.get(0)
        with torch.inference_mode():
            if cached is not None:
                graph, inputs, _ = cached
                for key, value in batch.items():
                    inputs[key].copy_(value)
                for _ in range(30):
                    graph.replay()
            else:
                for _ in range(30):
                    self.model(batch)
        torch.cuda.synchronize(self.device)
        self.reset_warmup_wall_time_s = perf_counter() - started
        self.reset_warmup_passes = 30

    def _empty_batch(self, count):
        torch, device = self.torch, self.device
        batch = {
            "x_objs": torch.zeros((count + 1, 7), dtype=torch.float32, device=device),
            "idxs": torch.arange(1, count + 1, dtype=torch.long, device=device)[None],
            "route_original": torch.zeros((1, 20, 2), dtype=torch.float32, device=device),
            "speed_limit": torch.zeros((1,), dtype=torch.long, device=device),
            "input_ego_speed": torch.zeros((1,), dtype=torch.float32, device=device),
        }
        if self.model.input_bev:
            batch["BEV"] = torch.zeros((1, 3, 128, 128), dtype=torch.float32, device=device)
        return batch

    def __call__(self, batch):
        count = batch["idxs"].shape[1]
        cached = self.graphs.get(count)
        if cached is None:
            self.last_backend = "cuda_float32_sdpa"
            return self.model(batch)
        graph, inputs, outputs = cached
        if batch.keys() != inputs.keys():
            raise ValueError("CUDA graph inputs do not match the complete model batch")
        for key, value in batch.items():
            target = inputs[key]
            if value.shape != target.shape or value.dtype != target.dtype or value.device != target.device:
                raise ValueError(f"CUDA graph input {key} has an unexpected shape, dtype, or device")
            target.copy_(value)
        graph.replay()
        self.last_backend = "cuda_graph_float32_sdpa"
        # A later replay overwrites graph-owned output buffers. Give callers
        # their own results so no subsequent observation can mutate a decision.
        return tuple(value.clone() for value in outputs)

    def close(self):
        self.graphs.clear()
        self.model = None
