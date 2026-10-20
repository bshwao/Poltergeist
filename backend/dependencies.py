"""Lazy application services: health and pure scenario endpoints need no storage."""

import logging
from threading import RLock

from fastapi import Request

from backend.config import Settings
from backend.persistence_errors import DatabaseInitializationError, PersistenceError
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import QueueUnavailableError, SimulationService

logger = logging.getLogger(__name__)


class ApiRuntime:
    def __init__(self, settings: Settings, *, runner_factory=None):
        self.settings = settings
        self.runner_factory = runner_factory or self._new_runner
        self._persistence = None
        self._worker = None
        self._optimizer = None
        self._registry = None
        self._carla_launcher = None
        self._lock = RLock()
        self._closed = False

    def _new_runner(self, *, options, evaluation_settings):
        from simulator import SimulationRunner
        from simulator.carla_client import CarlaConnectionSettings
        from simulator.carla_launcher import LocalCarlaLauncher

        if self._carla_launcher is None:
            self._carla_launcher = LocalCarlaLauncher(self.settings)

        return SimulationRunner(
            options=options, evaluation_settings=evaluation_settings,
            connection_settings=CarlaConnectionSettings.from_settings(self.settings),
            carla_launcher=self._carla_launcher,
        )

    def persistence(self) -> PersistenceService:
        with self._lock:
            if self._closed:
                raise QueueUnavailableError("Application is shutting down")
            if self._persistence is None:
                persistence = None
                try:
                    persistence = PersistenceService.from_settings(self.settings)
                    persistence.initialize()
                except PersistenceError as error:
                    if persistence is not None:
                        persistence.database.dispose()
                    raise DatabaseInitializationError("Configured persistence could not be initialized") from error
                except BaseException:
                    if persistence is not None:
                        persistence.database.dispose()
                    raise
                self._persistence = persistence
            return self._persistence

    def worker(self) -> SimulationService:
        with self._lock:
            persistence = self.persistence()
            if self._worker is None:
                worker = SimulationService(
                    persistence, runner_factory=self.runner_factory,
                    max_pending=self.settings.simulation_queue_size,
                    progress_interval_s=self.settings.simulation_progress_interval_s,
                    shutdown_timeout_s=self.settings.simulation_shutdown_timeout_s,
                )
                worker.start()
                self._worker = worker
            return self._worker

    def optimizer(self):
        from backend.services.optimization_service import OptimizationService

        with self._lock:
            worker = self.worker()
            if self._optimizer is None:
                optimizer = OptimizationService(worker, max_pending=self.settings.simulation_queue_size,
                                                shutdown_timeout_s=self.settings.simulation_shutdown_timeout_s)
                optimizer.start()
                self._optimizer = optimizer
            return self._optimizer

    def registry(self):
        from backend.services.agent_registry_service import AgentRegistryService

        with self._lock:
            persistence = self.persistence()
            if self._registry is None:
                self._registry = AgentRegistryService(persistence.database)
            return self._registry

    def close(self) -> bool:
        with self._lock:
            self._closed = True
            optimization_clean = self._optimizer is None or self._optimizer.close()
            simulation_clean = self._worker is None or self._worker.close()
            clean = optimization_clean and simulation_clean
            if clean and self._persistence is not None:
                self._persistence.database.dispose()
            if not clean:
                logger.error("Simulation worker is still inside a blocking native call; its database and lease remain owned")
            return clean


def get_persistence(request: Request) -> PersistenceService:
    return request.app.state.runtime.persistence()


def get_worker(request: Request) -> SimulationService:
    return request.app.state.runtime.worker()


def get_optimizer(request: Request):
    return request.app.state.runtime.optimizer()


def get_registry(request: Request):
    return request.app.state.runtime.registry()
