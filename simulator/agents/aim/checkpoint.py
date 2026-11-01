"""Exact official AIM checkpoint, obtained from a stored upstream ZIP member."""
import hashlib
from pathlib import Path

SOURCE_REVISION = "6d1d0421598e3767ced97a113739a40a621cdc25"
CHECKPOINT_BYTES = 85_999_854
CHECKPOINT_SHA256 = "176d7a613bd476dd3be5863d42be4a1ea5d6697781c835d9e2a65e17cd89d748"
CHECKPOINT_FILENAME = "aim-best_model.pth"
CHECKPOINT_URL = "https://s3.eu-central-1.amazonaws.com/avg-projects/transfuser/models.zip"
SOURCE_BYTES = 697_106_220
SOURCE_OFFSET = 138
MODEL_ID = "aim-camera-" + SOURCE_REVISION[:12]


def checkpoint_bytes(path):
    """Load bounded, verified bytes so deserialization cannot reopen a new path."""
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError("AIM weights are missing. Prepare the managed AIM camera pack first.")
    if candidate.stat().st_size != CHECKPOINT_BYTES:
        raise ValueError("AIM checkpoint size does not match the supported release")
    with candidate.open("rb") as stream:
        data = stream.read(CHECKPOINT_BYTES + 1)
    if len(data) != CHECKPOINT_BYTES or hashlib.sha256(data).hexdigest() != CHECKPOINT_SHA256:
        raise ValueError("AIM checkpoint hash does not match the supported release")
    return data
