"""The pilot CLI saves reproducible evidence and never invents live validation.

The trusted package fixture exercises the worker and evidence pipeline only;
it intentionally is not claimed to be a trained model.
"""

import hashlib
import json
from pathlib import Path
import sys

import pytest

from scripts import run_reference_ai as cli
from simulator import reference_evidence


@pytest.fixture
def reference_package(tmp_path, monkeypatch):
    package = tmp_path / "reference"
    package.mkdir()
    weights = b"fixture checkpoint; not a trained policy"
    sha = hashlib.sha256(weights).hexdigest()
    (package / "weights.bin").write_bytes(weights)
    manifest = {"agent_id": "reference-test", "name": "Reference test fixture", "version": "1",
                "entry_point": "agent:FixtureAgent", "assets": ["weights.bin"], "configuration": {"sha256": sha}}
    (package / "agent.json").write_text(json.dumps(manifest))
    (package / "agent.py").write_text('''
from simulator.agents import AutonomousAgent
class FixtureAgent(AutonomousAgent):
    def load(self, configuration):
        self.configuration = configuration
    def reset(self, context):
        pass
    def step(self, observation):
        if self.configuration.get("fail_step"):
            raise RuntimeError("fixture inference failed")
        return {"frame": observation.frame, "control": {"throttle": 0.5},
                "diagnostics": {"metadata": {"checkpoint_sha256": self.configuration["sha256"], "model_kind": "test_fixture_not_learned"}}}
    def close(self):
        pass
''')
    monkeypatch.setattr(cli, "REFERENCE_PACKAGE", package)
    monkeypatch.setattr(cli, "hardware_info", lambda: {"gpu_before": None, "parent_python_executable": sys.executable})
    monkeypatch.setattr(cli, "gpu_snapshot", lambda: None)
    return package, manifest


def invoke(output, *args):
    return cli.main(["--mode", "test", "--duration", "0.1", "--output", str(output), "--agent-python", sys.executable, "--sensor-settle", "0.001", *args])


def read_report(output):
    return json.loads((output / "report.json").read_text())


def test_cli_runs_reference_worker_and_retains_manifest_result_and_trace(reference_package, tmp_path, capsys):
    destination = tmp_path / "evidence"
    assert invoke(destination) == 0
    report = read_report(destination)
    assert report["schema_version"] == "reference-ai-pilot-1.0"
    assert report["model"]["manifest"]["agent_id"] == "reference-test"
    assert not report["verification"]["live_carla_trials_completed"]
    assert not report["verification"]["repeated_live_throughput_gate"]
    assert report["verification"]["checkpoint_identity_matches_declared_assets"]
    assert report["verification"]["all_requested_runs_completed"]
    summary = report["runs"][0]
    assert summary["process"]["worker_pid"] is not None
    assert summary["process"]["steps"] == 2
    folder = destination / summary["evidence_directory"]
    assert {path.name for path in folder.iterdir()} == {"decisions.jsonl", "telemetry.jsonl", "result.json", "summary.json", "recipe.json"}
    trace = [json.loads(line) for line in (folder / "decisions.jsonl").read_text().splitlines()]
    assert len(trace) == 2
    assert all(row["agent_result"]["control"] == row["applied_control"] for row in trace)
    assert "Live CARLA control is not verified" in (destination / "report.md").read_text()
    printed = json.loads(capsys.readouterr().out)
    assert printed["report"] == str(destination / "report.json")


def test_cli_runs_labelled_baseline_and_control_ablation(reference_package, tmp_path):
    destination = tmp_path / "comparisons"
    assert invoke(destination, "--compare-baseline", "--ablate") == 0
    report = read_report(destination)
    assert [item["variant"] for item in report["runs"]] == ["reference_ai", "baseline", "control_ablation"]
    assert all(item["same_recipe"] for item in report["comparisons"])
    ablation = report["comparisons"][1]
    assert ablation["changed_applied_control_frames"] == 2
    assert ablation["motion_changed_over_1cm"]
    folder = destination / report["runs"][2]["evidence_directory"]
    trace = [json.loads(line) for line in (folder / "decisions.jsonl").read_text().splitlines()]
    assert all("diagnostic_full_braking_control_ablation" in row["interventions"] for row in trace)
    for row in trace:
        assert row["applied_control"]["brake"] == 1
        assert row["agent_result"]["diagnostics"]["metadata"]["control_ablation"]["learned_control"]["throttle"] == 0.5


def test_custom_recipe_seed_is_preserved_unless_explicitly_overridden(reference_package, tmp_path):
    source = cli._recipe("road_following", None, .1, 17)
    recipe_path = tmp_path / "saved.json"
    recipe_path.write_text(source.model_dump_json())
    destination = tmp_path / "saved-seed"
    assert invoke(destination, "--scenario", str(recipe_path)) == 0
    assert read_report(destination)["runs"][0]["seed"] == 17
    override = tmp_path / "explicit-seed"
    assert invoke(override, "--scenario", str(recipe_path), "--seed", "29") == 0
    assert read_report(override)["runs"][0]["seed"] == 29


def test_cancelling_a_trial_stops_the_remaining_suite_variants(reference_package, tmp_path, monkeypatch):
    original = cli.SimulationRunner.run
    calls = []
    def cancel_before_setup(runner, *args, **kwargs):
        calls.append(None)
        runner.cancel()
        return original(runner, *args, **kwargs)
    monkeypatch.setattr(cli.SimulationRunner, "run", cancel_before_setup)
    destination = tmp_path / "cancelled"
    assert invoke(destination, "--compare-baseline", "--ablate") == 130
    report = read_report(destination)
    assert len(calls) == report["executed_run_count"] == 1
    assert report["requested_run_count"] == 3 and report["skipped_run_count"] == 2
    assert report["runs"][0]["status"] == "cancelled"
    assert not report["verification"]["all_requested_runs_completed"]


def test_cli_inference_failure_is_explicit_and_reported(reference_package, tmp_path):
    package, manifest = reference_package
    manifest["configuration"]["fail_step"] = True
    (package / "agent.json").write_text(json.dumps(manifest))
    destination = tmp_path / "failure"
    assert invoke(destination) == 1
    report = read_report(destination)
    assert not report["verification"]["all_requested_runs_completed"]
    assert not report["verification"]["live_carla_trials_completed"]
    assert "fixture inference failed" in report["runs"][0]["error"]
    assert report["runs"][0]["process"]["steps"] == 0
    assert report["runs"][0]["control_evidence"]["reported_checkpoint_sha256"] == []


def test_missing_checkpoint_fails_before_any_simulation_or_report_claim(reference_package, tmp_path, capsys):
    package, _ = reference_package
    (package / "weights.bin").unlink()
    destination = tmp_path / "missing"
    assert invoke(destination) == 1
    assert not destination.exists()
    assert "model asset" in capsys.readouterr().err


def test_output_directory_is_never_overwritten_or_modified(reference_package, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "report.json").write_text("keep this")
    assert invoke(destination) == 1
    assert (destination / "report.json").read_text() == "keep this"
    assert {path.name for path in destination.iterdir()} == {"report.json"}


def test_duplicate_seeds_and_event_truncation_fail_before_output_creation(reference_package, tmp_path):
    destination = tmp_path / "invalid"
    assert invoke(destination, "--seed", "3", "--seed", "3") == 1
    assert not destination.exists()
    assert invoke(destination, "--template", "sudden_braking") == 1
    assert not destination.exists()


@pytest.mark.parametrize("flag,value", [("--duration", "nan"), ("--agent-step-timeout", "0"), ("--sensor-settle", "-1"), ("--seed", "-1")])
def test_cli_rejects_invalid_numeric_options(tmp_path, flag, value):
    with pytest.raises(SystemExit) as error:
        cli.main(["--output", str(tmp_path / "bad"), flag, value])
    assert error.value.code == 2


def test_suite_builds_four_distinct_recipes_for_each_seed_without_clipping_hazards(reference_package, tmp_path):
    recipes = [cli._recipe(name, None, 8, 42) for name in cli.TRIALS]
    assert len(recipes) == 4
    assert len(recipes[0].actors) == 1 and not recipes[0].events
    assert recipes[0].template is None
    assert all(recipe.seed == 42 and recipe.duration_s == 8 for recipe in recipes)
    assert any(recipe.events for recipe in recipes[1:])


def test_gpu_snapshot_honestly_reports_unavailable_tool(monkeypatch):
    monkeypatch.setattr(reference_evidence.shutil, "which", lambda name: None)
    snapshot = reference_evidence.gpu_snapshot()
    assert not snapshot["available"]
    assert snapshot["devices"] is None
