"""Exact official CVPR 2021 checkpoint; no bundled weights or inferred license."""
import hashlib
from pathlib import Path

SOURCE_REVISION = "6d1d0421598e3767ced97a113739a40a621cdc25"
CHECKPOINT_BYTES = 265_617_761
CHECKPOINT_SHA256 = "16256a96134d9f83fdea30bfaf04b26a3710896e64be11be03d8f282946f5994"
CHECKPOINT_FILENAME = "transfuser-best_model.pth"
CHECKPOINT_URL = "https://s3.eu-central-1.amazonaws.com/avg-projects/transfuser/models.zip"
SOURCE_BYTES = 697_106_220
SOURCE_OFFSET = 431_487_567
MODEL_ID = "transfuser-fusion-" + SOURCE_REVISION[:12]


def checkpoint_bytes(path):
    """Verify bounded bytes before restricted loading of those same bytes."""
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError("Prepare the pinned TransFuser checkpoint first")
    if candidate.stat().st_size != CHECKPOINT_BYTES:
        raise ValueError("TransFuser checkpoint size does not match the supported release")
    with candidate.open("rb") as stream:
        data = stream.read(CHECKPOINT_BYTES + 1)
    if len(data) != CHECKPOINT_BYTES or hashlib.sha256(data).hexdigest() != CHECKPOINT_SHA256:
        raise ValueError("TransFuser checkpoint hash does not match the supported release")
    return data
