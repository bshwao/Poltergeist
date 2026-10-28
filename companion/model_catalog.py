"""Model data shipped by this private build, pinned by its bundle inventory."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .model_capsule import CapsuleError, CapsuleSpec, _json, _plain, _read


@dataclass(frozen=True)
class BundledCapsule:
    source: Path
    spec: CapsuleSpec


def load_bundled_capsule(bundle):
    if bundle is None:
        return None
    bundle = Path(bundle)
    catalogue = bundle / "modelpacks/catalog.json"
    _plain(catalogue)
    if not catalogue.exists():
        return None
    raw = _read(catalogue, 64 * 1024)
    inventory = _json(_read(bundle / "bundle.json", 4 * 1024**2))
    if (not isinstance(inventory, dict) or inventory.get("schema_version") != "poltergeist-portable-v1"
            or not isinstance(inventory.get("files"), dict)
            or inventory["files"].get("modelpacks/catalog.json") != hashlib.sha256(raw).hexdigest()):
        raise CapsuleError("The bundled model catalogue does not match this app build")
    value = _json(raw)
    if (not isinstance(value, dict) or set(value) != {"schema", "capsules"}
            or value["schema"] != "poltergeist-bundled-models-v1"
            or not isinstance(value["capsules"], list) or len(value["capsules"]) != 1):
        raise CapsuleError("Unsupported bundled model catalogue")
    entry = value["capsules"][0]
    if not isinstance(entry, dict) or set(entry) != {"id", "filename", "size", "sha256", "manifest_sha256", "target"}:
        raise CapsuleError("Invalid bundled model declaration")
    if not isinstance(entry["id"], str) or not re.fullmatch(r"plant2-cpu-win312-r[1-9][0-9]{0,3}", entry["id"]):
        raise CapsuleError("This app does not support that model capsule")
    spec = CapsuleSpec(entry["id"], entry["size"], entry["sha256"], entry["manifest_sha256"], entry["target"])
    if entry["filename"] != spec.id + ".zip":
        raise CapsuleError("Invalid bundled model filename")
    relative = "modelpacks/" + entry["filename"]
    source = bundle / relative
    _plain(source)
    if inventory["files"].get(relative) != spec.sha256 or not source.is_file() or source.stat().st_size != spec.size:
        raise CapsuleError("The bundled model capsule is missing or changed")
    return BundledCapsule(source, spec)
