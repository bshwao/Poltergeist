"""Revalidate telemetry boundaries and reject ambiguous trace ordering."""

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from shared.models import ActorState, TelemetryFrame


class EvaluationError(ValueError):
    """Telemetry cannot be evaluated without inventing or discarding data."""


def _plain(value: Any) -> Any:
    # Reading declared fields avoids serializer warnings when callers mutate a
    # model to an invalid type. The full fresh validation below rejects it.
    if isinstance(value, BaseModel):
        return {name: _plain(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def validate_actor(actor: ActorState | Mapping[str, Any]) -> ActorState:
    try:
        return ActorState.model_validate(_plain(actor))
    except (ValueError, TypeError, RecursionError, AttributeError) as error:
        raise EvaluationError(f"Invalid actor state: {error}") from error


def validate_frames(frames: Iterable[TelemetryFrame | Mapping[str, Any]]) -> list[TelemetryFrame]:
    if isinstance(frames, (str, bytes, Mapping)):
        raise EvaluationError("Telemetry must be an iterable of frames")
    validated: list[TelemetryFrame] = []
    try:
        for index, value in enumerate(frames):
            frame = TelemetryFrame.model_validate(_plain(value))
            if validated:
                previous = validated[-1]
                if frame.frame <= previous.frame or frame.time_s <= previous.time_s:
                    raise EvaluationError(f"Frame {index}: frame numbers and times must strictly increase")
                if frame.ego.actor_id != previous.ego.actor_id:
                    raise EvaluationError(f"Frame {index}: ego actor ID changed within the trace")
            validated.append(frame)
    except (ValueError, TypeError, RecursionError, AttributeError) as error:
        raise EvaluationError(f"Invalid telemetry: {error}") from error
    return validated
