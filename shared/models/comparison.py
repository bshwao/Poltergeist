"""Descriptive comparisons with explicit observed denominators and paired evidence."""

from typing import Literal
from uuid import UUID
from pydantic import Field

from .base import ContractModel, NonEmptyString, NonNegativeInt
from .optimization import OptimizationView


class ComparisonDistribution(ContractModel):
    eligible_results: NonNegativeInt
    observed_results: NonNegativeInt
    unavailable_results: NonNegativeInt
    unit: NonEmptyString
    mean: float | None
    minimum: float | None
    maximum: float | None
    sample_stddev: float | None


class ComparisonGroup(ContractModel):
    agent_index: NonNegativeInt
    registration_id: UUID | None
    name: NonEmptyString
    version: str | None
    planned_runs: NonNegativeInt
    simulation_ids: list[UUID]
    status_counts: dict[str, NonNegativeInt]
    assessed_runs: NonNegativeInt
    matching_runs: NonNegativeInt
    negative_runs: NonNegativeInt
    unknown_runs: NonNegativeInt
    unassessed_runs: NonNegativeInt
    completed_runs: NonNegativeInt
    partial_coverage_results: NonNegativeInt
    input_sources: dict[str, NonNegativeInt]
    task_outcomes: dict[str, NonNegativeInt]
    metrics: dict[str, ComparisonDistribution]


class ComparisonPair(ContractModel):
    seed: NonNegativeInt
    repeat_index: NonNegativeInt
    reference_simulation_id: UUID | None
    compared_simulation_id: UUID | None
    severity_difference: float | None
    reason: NonEmptyString


class PairedComparison(ContractModel):
    reference_agent_index: Literal[0] = 0
    compared_agent_index: NonNegativeInt
    difference: ComparisonDistribution
    pairs: list[ComparisonPair] = Field(max_length=200)


class ComparisonReport(ContractModel):
    schema_version: Literal["poltergeist-comparison-v1"] = "poltergeist-comparison-v1"
    evidence: OptimizationView
    groups: list[ComparisonGroup]
    paired_comparisons: list[PairedComparison]
    notes: list[NonEmptyString]
