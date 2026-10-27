"""Pinned upstream assets for the supported reference pilot."""

import hashlib
from pathlib import Path

UPSTREAM_REVISION = "6269e5a43c6f6b984d2bd97e3ec4a025ebefbeef"
MODEL_REPOSITORY = "SimonGer/PlanT2"
MODEL_REVISION = "e81b261f8b92b20225d020fe9b7a0470c1434d96"
CHECKPOINT_FILENAME = "epoch=029_final_1.ckpt"
CHECKPOINT_BYTES = 446_559_352
CHECKPOINT_SHA256 = "2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4"
CHECKPOINT_URL = (
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{CHECKPOINT_FILENAME}"
)
DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[3] / "storage" / "models" / "plant2" / CHECKPOINT_FILENAME


def verify_checkpoint(path: str | Path) -> str:
    """Verify the official bytes before deserializing the trusted checkpoint.

    Lightning's upstream checkpoint contains OmegaConf metadata and requires
    Python pickle. Only this pinned, hash-verified release is accepted here.
    """
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Reference AI checkpoint is missing: {candidate}. "
            "Run python -m scripts.setup_reference_ai --download first."
        )
    if candidate.stat().st_size != CHECKPOINT_BYTES:
        raise ValueError(f"Reference AI checkpoint has an unexpected size: {candidate}")
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != CHECKPOINT_SHA256:
        raise ValueError(f"Reference AI checkpoint SHA-256 mismatch: {actual}")
    return actual
