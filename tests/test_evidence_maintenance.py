"""Desktop evidence export/removal retain saved measurements and run identities."""

import json
from zipfile import ZipFile

import pytest

from companion.evidence import read_evidence
from shared.models.evidence import EvidenceRequest
from uuid import uuid4
from test_companion import desktop, completed


def test_desktop_export_and_idempotent_retention_preserve_result(desktop, tmp_path):
    desktop.start()
    stored = desktop.submit({"name": "Retain the summary", "duration_s": .1,
        "actors": [{"id": "ego", "type": "ego_vehicle"}]}, mode="test")
    finished = completed(desktop, stored.simulation.id)
    original = finished.result.model_dump_json()
    export = tmp_path / "evidence.zip"
    capacity = desktop.maintain_evidence(stored.simulation.id, export=export)
    assert capacity["used_bytes"] > 0
    with ZipFile(export) as archive:
        assert json.loads(archive.read("result.json"))["simulation"]["id"] == str(stored.simulation.id)
        assert any(name.endswith("frames.ndjson") for name in archive.namelist())
    with pytest.raises(FileExistsError):
        desktop.maintain_evidence(stored.simulation.id, export=export)
    desktop.maintain_evidence(stored.simulation.id, delete=True)
    desktop.maintain_evidence(stored.simulation.id, delete=True)
    assert desktop._worker.get(stored.simulation.id).result.model_dump_json() == original
    request = EvidenceRequest(request_id=uuid4(), simulation_id=stored.simulation.id)
    assert read_evidence(desktop._worker.persistence.artifacts, finished.result, request).state == "deleted"
