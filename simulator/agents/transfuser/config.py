"""Inference settings from the pinned CVPR 2021 release, without training paths."""
from types import SimpleNamespace


def inference_config():
    return SimpleNamespace(
        seq_len=1, pred_len=4, n_views=1, input_resolution=256,
        vert_anchors=8, horz_anchors=8, n_head=4, block_exp=4, n_layer=8,
        embd_pdrop=.1, resid_pdrop=.1, attn_pdrop=.1,
        turn_KP=1.25, turn_KI=.75, turn_KD=.3, turn_n=40,
        speed_KP=5., speed_KI=.5, speed_KD=1., speed_n=40,
        max_throttle=.75, brake_speed=.1, brake_ratio=1.1, clip_delta=.25,
    )
