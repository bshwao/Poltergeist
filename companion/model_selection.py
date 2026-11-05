"""Atomic local model preference, separate from model bytes and credentials."""
import json
import os
from pathlib import Path
from uuid import uuid4
from .model_assets import AssetError, _plain


class ModelSelection:
    def __init__(self, profile):
        self.profile = Path(profile)
        if not self.profile.is_absolute():
            raise ValueError("Model preferences require an absolute profile")
        self.path = self.profile / "model-selection.json"

    def read(self):
        try:
            _plain(self.path)
            if not self.path.exists():
                return "plant", None
            if self.path.stat().st_size > 1024:
                raise ValueError("Oversized preference")
            with self.path.open("rb") as source:
                data = source.read(1025)
            if len(data) > 1024:
                raise ValueError("Oversized preference")
            value = json.loads(data)
            if (not isinstance(value, dict) or set(value) != {"schema", "model"}
                    or value["schema"] != "poltergeist-model-selection-v1" or value["model"] not in ("plant", "aim", "transfuser")):
                raise ValueError("Unknown preference")
            return value["model"], None
        except (OSError, ValueError, AssetError):
            return "plant", "Saved AI selection could not be read. Select a supported model again before preparing it."

    def save(self, kind):
        if kind not in ("plant", "aim", "transfuser"):
            raise ValueError("Unknown supported AI model")
        _plain(self.path)
        self.profile.mkdir(parents=True, exist_ok=True)
        temporary = self.profile / (".model-selection-" + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump({"schema": "poltergeist-model-selection-v1", "model": kind}, output)
                output.flush()
                os.fsync(output.fileno())
            _plain(self.path)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
