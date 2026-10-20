"""SQLAlchemy records. Shared Pydantic models remain the public data contracts."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from .scenario import ScenarioRecord
from .simulation import SimulationRecord
from .failure import FailureRecord
from .simulation_job import SimulationJobRecord
from .optimization import OptimizationOperationRecord, OptimizationTrialRecord
from .agent_registration import AgentRegistrationRecord

__all__ = ["Base", "ScenarioRecord", "SimulationRecord", "FailureRecord", "SimulationJobRecord",
           "OptimizationOperationRecord", "OptimizationTrialRecord", "AgentRegistrationRecord"]
