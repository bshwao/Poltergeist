"""Optional sequential Optuna TPE sampler; no CARLA or database ownership."""

import importlib

from scenarios import build_scenario
from shared.models import Scenario

from .random_search import SearchSpace


class OptimizationDependencyError(RuntimeError):
    """The explicitly requested optional optimizer is unavailable or unsupported."""


class BayesianSearch:
    """Ask/tell adapter over an in-memory, maximized, seeded TPE study.

    Persistent callers replay the exact ordered suggestions and observations.
    An unknown score fails the Optuna trial; it is never converted into zero.
    The package version is part of persisted provenance because sampler behavior
    can change between releases.
    """

    def __init__(self, space: SearchSpace, seed: int) -> None:
        try:
            optuna = importlib.import_module("optuna")
        except ImportError as error:
            raise OptimizationDependencyError(
                "Optuna search requires the optional optimization dependencies: "
                'pip install -e ".[optimization]"'
            ) from error
        version = getattr(optuna, "__version__", "unknown")
        if version.split(".", 1)[0] != "4":
            raise OptimizationDependencyError(
                f"Unsupported Optuna version {version}; install the declared optuna>=4,<5 dependency"
            )
        self._optuna = optuna
        self.version = f"optuna-{version}"
        self._space = space
        self._seed = seed
        self._pending = None
        self._study = optuna.create_study(
            direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed)
        )

    def suggest(self, index: int) -> Scenario:
        if self._pending is not None:
            raise RuntimeError("Observe the pending Optuna trial before requesting another")
        trial = self._study.ask()
        try:
            values = dict(self._space.parameters)
            for name, spec in self._space.specs.items():
                if name in values:
                    continue
                minimum, maximum = self._space.ranges[name]
                values[name] = (
                    trial.suggest_int(name, int(minimum), int(maximum))
                    if spec.integer
                    else trial.suggest_float(name, float(minimum), float(maximum))
                )
            seed = (self._seed + index) % (2**31)
            scenario = build_scenario(
                self._space.template,
                parameters=values,
                seed=seed,
                name=f"{self._space.template.value}-{seed}",
            )
        except BaseException:
            self._study.tell(trial, state=self._optuna.trial.TrialState.FAIL)
            raise
        self._pending = trial
        return scenario

    def observe(self, score: float | None) -> None:
        if self._pending is None:
            raise RuntimeError("No pending Optuna trial to observe")
        if score is None:
            self._study.tell(self._pending, state=self._optuna.trial.TrialState.FAIL)
        else:
            self._study.tell(self._pending, score)
        self._pending = None
