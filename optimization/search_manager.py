"""Bounded, sequential search suggestions independent from run execution."""

from collections.abc import Iterable, Mapping
import math
from typing import TYPE_CHECKING

from shared.models import Scenario

from .bayesian_search import BayesianSearch
from .random_search import RandomSearch, normalize_search_space

if TYPE_CHECKING:
    from shared.models.optimization import SearchRequest


class SearchProtocolError(RuntimeError):
    """A caller exceeded the budget or broke the sequential ask/observe protocol."""


class SearchReplayError(ValueError):
    """A persisted history cannot reproduce the sampler's exact recipe sequence."""


class SearchManager:
    """Suggest one full recipe, observe its result, then suggest the next.

    The manager never runs simulations or writes files. A persistent service
    controls one simulation queue and stores each recipe before enqueueing it.
    Invalid or incomplete runs are represented by ``observe(None)``.
    """

    def __init__(self, request: "SearchRequest | Mapping[str, object]") -> None:
        # Imported here so importing optimization never imports optional Optuna
        # or acquires service/database resources.
        from shared.models.optimization import SearchRequest

        self._request = SearchRequest.model_validate(
            request.model_dump(mode="python") if isinstance(request, SearchRequest) else request
        )
        space = normalize_search_space(
            self._request.template,
            parameters=self._request.parameters,
            ranges=self._request.ranges,
        )
        strategy = self._request.strategy
        self._sampler = (
            RandomSearch(space, self._request.seed)
            if strategy == "random"
            else BayesianSearch(space, self._request.seed)
        )
        self._completed = 0
        self._pending = False

    @property
    def version(self) -> str:
        return self._sampler.version

    @property
    def completed_count(self) -> int:
        return self._completed

    @property
    def pending(self) -> bool:
        return self._pending

    @property
    def exhausted(self) -> bool:
        return self._completed >= self._request.trials

    def suggest(self) -> Scenario:
        if self._pending:
            raise SearchProtocolError("Observe the pending scenario before requesting another")
        if self.exhausted:
            raise SearchProtocolError("Search trial budget exhausted")
        scenario = self._sampler.suggest(self._completed)
        self._pending = True
        return scenario

    def observe(self, score: float | None) -> None:
        if not self._pending:
            raise SearchProtocolError("No pending scenario to observe")
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (float, int)):
                raise ValueError("A search score must be finite and within [0, 100], or None")
            try:
                valid = math.isfinite(score) and 0 <= score <= 100
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("A search score must be finite and within [0, 100], or None")
            score = float(score)
        self._sampler.observe(score)
        self._completed += 1
        self._pending = False

    def replay(self, history: Iterable[tuple[Scenario, float | None]]) -> None:
        """Restore a fresh manager by reproducing and verifying ordered recipes.

        Compare the persisted manager version before replaying. Version checks
        belong to the persistent service because this manager owns no records.
        """
        if self._completed or self._pending:
            raise SearchProtocolError("Replay requires a fresh search manager")
        for stored_scenario, score in history:
            expected = Scenario.model_validate(stored_scenario)
            generated = self.suggest()
            if generated.model_dump(mode="json") != expected.model_dump(mode="json"):
                raise SearchReplayError(
                    f"Search history differs from generated recipe at trial {self._completed}"
                )
            self.observe(score)
