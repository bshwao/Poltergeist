"""Search recipes, replay, optional dependencies, and truthful unknown scores."""

from dataclasses import replace
import random
from types import SimpleNamespace

import pytest

from optimization import (
    OptimizationDependencyError,
    SearchManager,
    SearchProtocolError,
    SearchReplayError,
)
from scenarios import generate_scenario, get_parameter_specs, validate_scenario
from shared.models import ScenarioTemplate
from shared.models.optimization import SearchRequest


def request(**kwargs):
    return SearchRequest.model_validate({"template": "sudden_braking", "mode": "test", **kwargs})


@pytest.mark.parametrize("template", list(ScenarioTemplate))
def test_random_replays_generator_recipes_and_full_parameter_bindings(template):
    manager = SearchManager(request(template=template, seed=71, trials=8))
    assert manager.version == "random-v1"
    for index in range(8):
        scenario = manager.suggest()
        assert scenario == generate_scenario(template, seed=71 + index)
        assert validate_scenario(scenario) == scenario
        assert scenario.parameters.keys() == get_parameter_specs(template).keys()
        manager.observe(None if index % 2 else 42.0)
    assert manager.completed_count == 8
    assert manager.exhausted


def test_random_seed_wrap_prefix_stability_and_independence_from_scores():
    short = SearchManager(request(seed=2**31 - 1, trials=2))
    longer = SearchManager(request(seed=2**31 - 1, trials=5))
    before = random.getstate()
    for seed in (2**31 - 1, 0):
        first, second = short.suggest(), longer.suggest()
        assert first == second
        assert first.seed == seed
        short.observe(None)
        longer.observe(100.0)
    assert random.getstate() == before


@pytest.mark.parametrize("strategy", ["random", "optuna"])
def test_fixed_and_narrowed_values_preserve_inputs_and_full_recipe(strategy):
    if strategy == "optuna":
        pytest.importorskip("optuna")
    original = request(
        strategy=strategy, trials=12,
        parameters={"ego_speed_mps": 40},
        ranges={"ego_speed_mps": (3, 4), "lead_speed_mps": (8, 10), "brake_delay_s": (2, 2)},
    )
    initial = original.model_dump(mode="json")
    manager = SearchManager(original)
    for _ in range(12):
        scenario = manager.suggest()
        assert scenario.parameters["ego_speed_mps"] == 40
        assert 8 <= scenario.parameters["lead_speed_mps"] <= 10
        assert scenario.parameters["brake_delay_s"] == 2
        assert validate_scenario(scenario) == scenario
        manager.observe(50.0)
    assert original.model_dump(mode="json") == initial


def test_request_snapshot_cannot_be_changed_after_construction():
    original = request(seed=5, trials=1, parameters={"ego_speed_mps": 12})
    manager = SearchManager(original)
    original.parameters["ego_speed_mps"] = 24
    original.seed = 500
    original.trials = 100
    scenario = manager.suggest()
    assert scenario.seed == 5
    assert scenario.parameters["ego_speed_mps"] == 12
    manager.observe(0.0)
    with pytest.raises(SearchProtocolError, match="budget"):
        manager.suggest()


@pytest.mark.parametrize("changes", [
    {"parameters": {"missing": 3}},
    {"parameters": {"ego_speed_mps": -0.1}},
    {"parameters": {"ego_speed_mps": True}},
    {"ranges": {"missing": (1, 2)}},
    {"ranges": {"ego_speed_mps": (5, 4)}},
    {"ranges": {"ego_speed_mps": (0, 2)}},
    {"parameters": {"ego_speed_mps": 12}, "ranges": {"ego_speed_mps": (1, 26)}},
    {"ranges": {"ego_speed_mps": (1, float("inf"))}},
])
def test_space_validated_before_suggesting(changes):
    before = random.getstate()
    with pytest.raises(ValueError):
        SearchManager(request(**changes))
    assert random.getstate() == before


@pytest.mark.parametrize("score", [True, "25", float("nan"), float("inf"), -1, 100.1, 10**500])
def test_invalid_observation_keeps_pending_trial_for_retry(score):
    manager = SearchManager(request(trials=1))
    manager.suggest()
    with pytest.raises(ValueError, match="score"):
        manager.observe(score)
    assert manager.pending
    assert manager.completed_count == 0
    manager.observe(None)
    assert manager.exhausted


def test_budget_and_pending_protocol_guards():
    manager = SearchManager(request(trials=1))
    with pytest.raises(SearchProtocolError, match="No pending"):
        manager.observe(10)
    manager.suggest()
    with pytest.raises(SearchProtocolError, match="pending"):
        manager.suggest()
    manager.observe(0)
    assert not manager.pending
    with pytest.raises(SearchProtocolError, match="budget"):
        manager.suggest()
    with pytest.raises(SearchProtocolError, match="No pending"):
        manager.observe(None)


@pytest.mark.parametrize("strategy", ["random", "optuna"])
def test_history_replay_restores_next_suggestion_after_known_and_unknown_trials(strategy):
    if strategy == "optuna":
        pytest.importorskip("optuna")
    original_request = request(strategy=strategy, trials=25, seed=318)
    first = SearchManager(original_request)
    history = []
    for index in range(18):
        scenario = first.suggest()
        score = None if index in (0, 7, 13) else float((index * 7) % 101)
        history.append((scenario, score))
        first.observe(score)
    resumed = SearchManager(original_request)
    assert resumed.version == first.version
    resumed.replay(history)
    assert resumed.completed_count == 18
    assert resumed.suggest() == first.suggest()


def test_replay_refuses_mutated_history_and_nonfresh_manager():
    manager = SearchManager(request(trials=2))
    stored = generate_scenario("sudden_braking", seed=0)
    stored.name = "changed"
    with pytest.raises(SearchReplayError, match="trial 0"):
        manager.replay([(stored, 25.0)])
    assert manager.pending
    with pytest.raises(SearchProtocolError, match="fresh"):
        manager.replay([])


def test_random_does_not_require_optional_dependency_and_selected_optuna_fails_clearly(monkeypatch):
    from optimization import bayesian_search

    original_import = bayesian_search.importlib.import_module

    def missing(name):
        if name == "optuna":
            raise ModuleNotFoundError("missing optional package")
        return original_import(name)

    monkeypatch.setattr(bayesian_search.importlib, "import_module", missing)
    assert SearchManager(request()).suggest().seed == 0
    with pytest.raises(OptimizationDependencyError, match="optional.*optimization"):
        SearchManager(request(strategy="optuna"))


def test_unsupported_optuna_major_is_rejected_before_study_creation(monkeypatch):
    from optimization import bayesian_search

    monkeypatch.setattr(
        bayesian_search.importlib, "import_module", lambda name: SimpleNamespace(__version__="5.0.0")
    )
    with pytest.raises(OptimizationDependencyError, match="Unsupported Optuna version 5"):
        SearchManager(request(strategy="optuna"))


def test_actual_optuna_unknown_is_failed_without_value_not_zero():
    optuna = pytest.importorskip("optuna")
    manager = SearchManager(request(strategy="optuna", trials=3))
    assert manager.version == f"optuna-{optuna.__version__}"
    for score in (None, 0.0, 100.0):
        manager.suggest()
        manager.observe(score)
    trials = manager._sampler._study.trials
    assert [trial.state for trial in trials] == [
        optuna.trial.TrialState.FAIL,
        optuna.trial.TrialState.COMPLETE,
        optuna.trial.TrialState.COMPLETE,
    ]
    assert [trial.value for trial in trials] == [None, 0.0, 100.0]


def test_optuna_all_parameters_fixed_has_no_artificial_sampled_parameters():
    pytest.importorskip("optuna")
    fixed = {name: spec.default for name, spec in get_parameter_specs("sudden_braking").items()}
    manager = SearchManager(request(strategy="optuna", parameters=fixed, trials=1))
    assert manager.suggest().parameters == fixed
    manager.observe(25.0)
    assert manager._sampler._study.trials[0].params == {}


@pytest.mark.parametrize("strategy", ["random", "optuna"])
def test_future_integer_parameter_uses_integer_sampling_and_rejects_fractional_bounds(monkeypatch, strategy):
    if strategy == "optuna":
        pytest.importorskip("optuna")
    from scenarios import parameters

    registry = parameters._SPECS[ScenarioTemplate.SUDDEN_BRAKING]
    monkeypatch.setitem(registry, "ego_speed_mps", replace(registry["ego_speed_mps"], integer=True))
    manager = SearchManager(request(strategy=strategy, trials=8, ranges={"ego_speed_mps": (5, 7)}))
    for _ in range(8):
        value = manager.suggest().parameters["ego_speed_mps"]
        assert type(value) is int
        assert 5 <= value <= 7
        manager.observe(0.0)
    with pytest.raises(ValueError, match="integer"):
        SearchManager(request(strategy=strategy, ranges={"ego_speed_mps": (5.1, 7)}))


def test_actual_optuna_keeps_process_random_generators_unchanged():
    pytest.importorskip("optuna")
    import numpy as np

    python_before = random.getstate()
    numpy_before = np.random.get_state()
    manager = SearchManager(request(strategy="optuna", seed=8, trials=12))
    for _ in range(12):
        manager.suggest()
        manager.observe(25.0)
    assert random.getstate() == python_before
    numpy_after = np.random.get_state()
    assert numpy_before[0] == numpy_after[0]
    np.testing.assert_array_equal(numpy_before[1], numpy_after[1])
    assert numpy_before[2:] == numpy_after[2:]
