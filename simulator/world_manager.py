"""Scoped CARLA world changes, with restoration after partial setup failures.

CARLA is injected so importing this module never requires the simulation extra.
A map reload is destructive to an episode: it is opt-in and refused when the
world contains existing vehicles, walkers, sensors, or controllers.
"""

import math
from typing import Any

from shared.models import Scenario

from .errors import SimulationError


_SETTINGS_FIELDS = (
    "synchronous_mode", "no_rendering_mode", "fixed_delta_seconds", "substepping",
    "max_substep_delta_time", "max_substeps", "max_culling_distance",
    "deterministic_ragdolls", "tile_stream_distance", "actor_active_distance",
    "spectator_as_ego",
)
_WEATHER_FIELDS = (
    "cloudiness", "precipitation", "precipitation_deposits", "wind_intensity",
    "sun_azimuth_angle", "sun_altitude_angle", "fog_density", "fog_distance",
    "fog_falloff", "wetness", "scattering_intensity", "mie_scattering_scale",
    "rayleigh_scattering_scale", "dust_storm",
)


def _clone(value: Any, constructor: Any, fields: tuple[str, ...]) -> Any:
    result = constructor()
    for name in fields:
        if hasattr(value, name):
            setattr(result, name, getattr(value, name))
    return result


def _map_name(name: str) -> str:
    return name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _dynamic_actors(world: Any) -> list[Any]:
    return [actor for actor in world.get_actors()
            if actor.type_id.startswith(("vehicle.", "walker.", "sensor.", "controller."))]


class WorldManager:
    def __init__(self, client: Any, carla_module: Any, *, allow_map_reload: bool = False, no_rendering_mode: bool | None = None, endpoint_lease=None):
        self.client = client
        self.carla = carla_module
        self.allow_map_reload = allow_map_reload
        self.endpoint_lease = endpoint_lease
        if no_rendering_mode is not None and type(no_rendering_mode) is not bool:
            raise ValueError("no_rendering_mode must be a boolean or None")
        self.no_rendering_mode = no_rendering_mode
        self.world: Any | None = None
        self._original_map: str | None = None
        self._original_settings: Any | None = None
        self._original_weather: Any | None = None
        self._loaded_map = False
        self._changed = False

    def open(self, scenario: Scenario) -> Any:
        if self.world is not None:
            raise SimulationError("WorldManager is already open")
        world = self.client.get_world()
        original_map = world.get_map().name
        mismatch = _map_name(original_map) != _map_name(scenario.map)
        if mismatch and not self.allow_map_reload:
            raise SimulationError(
                f"Requested map {scenario.map!r} differs from current map {original_map!r}; "
                "start CARLA on that map or explicitly allow map reload"
            )
        if mismatch and _dynamic_actors(world):
            raise SimulationError("Cannot reload a map containing unmanaged dynamic actors")
        if mismatch:
            available = self.client.get_available_maps()
            matches = [name for name in available if _map_name(name) == _map_name(scenario.map)]
            if not matches:
                raise SimulationError(f"Requested CARLA map is unavailable: {scenario.map}")
            target_map = matches[0]

        original_settings = world.get_settings()
        original_weather = world.get_weather()
        if self.endpoint_lease is not None:
            self.endpoint_lease.begin(world, map_reload=mismatch)
        self.world = world
        self._original_map = original_map
        self._original_settings = original_settings
        self._original_weather = original_weather
        try:
            if mismatch:
                # Record intent before load_world: it may change the server then
                # raise (for example, while waiting for the new episode).
                self._loaded_map = True
                world = self.client.load_world(target_map)
                self.world = world
            settings = _clone(world.get_settings(), self.carla.WorldSettings, _SETTINGS_FIELDS)
            settings.synchronous_mode = True
            if self.no_rendering_mode is not None:
                settings.no_rendering_mode = self.no_rendering_mode
            settings.fixed_delta_seconds = scenario.fixed_delta_seconds
            settings.substepping = True
            settings.max_substep_delta_time = 0.01
            settings.max_substeps = max(1, math.ceil(scenario.fixed_delta_seconds / 0.01))
            settings.deterministic_ragdolls = True
            self._changed = True
            world.apply_settings(settings)
            weather = _clone(world.get_weather(), self.carla.WeatherParameters, _WEATHER_FIELDS)
            for field in ("cloudiness", "precipitation", "wetness", "fog_density"):
                setattr(weather, field, getattr(scenario.weather, field))
            weather.sun_altitude_angle = scenario.weather.sun_altitude_deg
            world.set_weather(weather)
            return world
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup:
                raise SimulationError(f"World setup failed: {exc}; restoration failed: {cleanup}") from exc
            raise SimulationError(f"World setup failed: {exc}") from exc

    def close(self) -> None:
        if self.world is None:
            return
        errors: list[str] = []
        world = self.world
        if self._loaded_map:
            try:
                world = self.client.get_world()
                if _dynamic_actors(world):
                    raise SimulationError("Cannot restore original map while unmanaged dynamic actors exist")
                world = self.client.load_world(self._original_map)
                # The original episode is back even if a later settings or
                # weather RPC fails. A retry must not reload this map again.
                self._loaded_map = False
                self._changed = True
            except Exception as exc:
                errors.append(f"map restoration: {exc}")
        restoration_failed = False
        if self._changed or self._loaded_map:
            for label, action, value in (
                ("settings", world.apply_settings, self._original_settings),
                ("weather", world.set_weather, self._original_weather),
            ):
                try:
                    action(value)
                except Exception as exc:
                    restoration_failed = True
                    errors.append(f"{label} restoration: {exc}")
        self._changed = restoration_failed
        if errors:
            # Retain the current episode and pending restoration flags so the
            # adapter (or caller) can retry after a transient RPC failure.
            self.world = world
            raise SimulationError("; ".join(errors))
        self.world = None
        self._loaded_map = self._changed = False
