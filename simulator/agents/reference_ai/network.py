"""Inference-only PlanT 2.0 network, adapted from PlanT/model.py.

Upstream: autonomousvision/plant2, revision
6269e5a43c6f6b984d2bd97e3ec4a025ebefbeef (MIT, see LICENSE.plant2).
Training/Leaderboard dependencies and online architecture/pretraining downloads
are removed. All learned parameters must be supplied by a strict checkpoint
load. CPU uses the upstream eager attention. CUDA can use PyTorch's equivalent
scaled dot-product attention without allocating attention weights that are not
consumed by the driving policy.
"""

import torch
from torch import nn
from transformers import BertConfig, BertModel


class LinearWaypoints(nn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.wp_decoder = nn.Linear(hidden_size, 2)

    def forward(self, tokens):
        return torch.cumsum(self.wp_decoder(tokens), dim=1)


class PlanT2Network(nn.Module):
    """The released model's path+wps / linear architecture, without a trainer."""

    def __init__(self, configuration, *, attention_implementation="eager"):
        super().__init__()
        cfg = configuration["model"]
        net, training, waypoints = cfg["network"], cfg["training"], cfg["waypoints"]
        if net["hf_checkpoint"] != "prajjwal1/bert-medium":
            raise ValueError("The reference integration supports the released BERT-medium checkpoint only")
        if (waypoints["representation"], waypoints["generator"]) != ("path+wps", "linear"):
            raise ValueError("The reference integration requires the released path+wps linear architecture")
        bert_cfg = BertConfig(
            hidden_size=512, hidden_act="gelu", initializer_range=0.02,
            vocab_size=30522, hidden_dropout_prob=0.1, num_attention_heads=8,
            type_vocab_size=2, max_position_embeddings=512, num_hidden_layers=8,
            intermediate_size=2048, attention_probs_dropout_prob=0.1,
            _attn_implementation=attention_implementation,
        )
        self.n_embd = bert_cfg.hidden_size
        self.model = BertModel(bert_cfg)
        if attention_implementation == "sdpa":
            # BertModel's SDPA mask helper reads a CUDA scalar to detect an
            # all-valid mask. Every token here is real (no padding), so use
            # its equivalent eager additive mask builder while retaining
            # BertSdpaSelfAttention in every encoder layer. This avoids a
            # host synchronization and permits CUDA graph capture.
            self.model.attn_implementation = "eager"
        self.model.embeddings.word_embeddings = None
        self.model.pooler = None
        self.input_bev = bool(training.get("input_bev", False))
        if self.input_bev:
            import timm

            # The official release supplies all ResNet weights; downloading
            # ImageNet weights here would be redundant and break offline use.
            self.bev_encoder = timm.create_model("resnet18", pretrained=False, num_classes=512)
        self.tok_emb = nn.ParameterList(nn.Linear(6, self.n_embd) for _ in range(7))
        self.wp_len = int(waypoints["wps_len"])
        self.path_len = int(waypoints["path_len"])
        self.wp_token = nn.Parameter(torch.empty(self.wp_len + self.path_len, self.n_embd))
        self.use_dropout = bool(net.get("use_dropout", False))
        if self.use_dropout:
            self.drop = nn.Dropout(float(net["embd_pdrop"]))
        self.route_emb = nn.Linear(40, self.n_embd)
        self.speed_emb = nn.Embedding(4, self.n_embd)
        self.input_ego_speed = bool(training.get("input_ego_speed", False))
        if self.input_ego_speed:
            self.ego_speed_emb = nn.Linear(1, self.n_embd)
        pre = cfg["pre_training"]
        precision_pos = int(pre.get("precision_pos", 4))
        vocab = [2**precision_pos, 2**precision_pos,
                 2**int(pre.get("precision_angle", 4)), 2**int(pre.get("precision_speed", 4))]
        vocab[0] = int((1 + float(training.get("range_factor_front", 1))) / 2 * vocab[0])
        self.heads = nn.ModuleList(nn.Linear(self.n_embd, count) for count in vocab)
        self.wp_generator = LinearWaypoints(self.n_embd)
        self.path_generator = LinearWaypoints(self.n_embd)

    def object_embeddings(self, objects):
        # Boolean indexing has dynamic output shapes and synchronizes CUDA
        # repeatedly. These tiny projections can be evaluated for every row,
        # then gathered by the observed class without changing learned math.
        projections = torch.stack([projection(objects[..., 1:]) for projection in self.tok_emb], dim=1)
        indices = objects[..., 0].long()[:, None, None].expand(-1, 1, self.n_embd)
        return torch.gather(projections, dim=1, index=indices).squeeze(1)

    def forward(self, batch):
        embedding = self.object_embeddings(batch["x_objs"])[batch["idxs"]]
        embedding = torch.cat((self.route_emb(batch["route_original"].flatten(1))[:, None], embedding), dim=1)
        embedding = torch.cat((self.speed_emb(batch["speed_limit"])[:, None], embedding), dim=1)
        if self.input_ego_speed:
            embedding = torch.cat((self.ego_speed_emb(batch["input_ego_speed"][:, None])[:, None], embedding), dim=1)
        if self.input_bev:
            embedding = torch.cat((self.bev_encoder(batch["BEV"])[:, None], embedding), dim=1)
        embedding = torch.cat((self.wp_token.expand(embedding.shape[0], *self.wp_token.shape), embedding), dim=1)
        if self.use_dropout:
            embedding = self.drop(embedding)
        encoded = self.model(inputs_embeds=embedding, output_attentions=False).last_hidden_state
        waypoints = self.wp_generator(encoded[:, :self.wp_len])
        path = self.path_generator(encoded[:, self.wp_len:self.wp_len + self.path_len])
        return path, waypoints
