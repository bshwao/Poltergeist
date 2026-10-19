"""Bounded, newline-delimited JSON messages for the local agent process.

This is an internal transport around the public SDK contracts. It contains no
simulator imports and rejects ambiguous JSON before model validation.
"""

import json
from typing import Literal, Self

from pydantic import Field, JsonValue, StrictBool, StrictStr, ValidationInfo, field_validator, model_validator

from shared.models.agent import _require_json_object
from shared.models.base import ContractModel, NonNegativeInt


DEFAULT_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
ProcessOperation = Literal["hello", "load", "reset", "step", "close"]
_DECODED_JSON = object()


def _json_payload(value, info: ValidationInfo):
    if info.context is _DECODED_JSON:
        # json.loads has already established string keys, acyclic containers,
        # and JSON primitive types. The compiled JsonValue validator below
        # still checks every value, including overflowed non-finite floats.
        # Rewalking the complete scene in Python adds no extra constraint.
        return value
    return _require_json_object(value)


class AgentWorkerError(ContractModel):
    type: StrictStr = Field(min_length=1)
    message: StrictStr


class AgentProcessRequest(ContractModel):
    protocol_version: Literal["1.0"]
    request_id: NonNegativeInt
    operation: ProcessOperation
    payload: dict[str, JsonValue]

    _json_payload = field_validator("payload", mode="before")(_json_payload)


class AgentProcessResponse(ContractModel):
    protocol_version: Literal["1.0"]
    request_id: NonNegativeInt
    operation: ProcessOperation
    ok: StrictBool
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    error: AgentWorkerError | None = None

    _json_payload = field_validator("payload", mode="before")(_json_payload)

    @model_validator(mode="after")
    def validate_error(self) -> Self:
        if self.ok == (self.error is not None):
            raise ValueError("Successful responses must omit errors; failed responses must include an error")
        return self


def _message_limit(max_message_bytes: int) -> None:
    if type(max_message_bytes) is not int or max_message_bytes < 1:
        raise ValueError("max_message_bytes must be a positive integer")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key in agent process message: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number in agent process message: {value}")


def _decode(raw: bytes | str, model, max_message_bytes: int):
    _message_limit(max_message_bytes)
    if not isinstance(raw, (bytes, str)):
        raise TypeError("Agent process messages must be UTF-8 bytes or text")
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > max_message_bytes:
        raise ValueError(f"Agent process message exceeds {max_message_bytes} bytes")
    line = encoded.decode("utf-8")
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
    if "\n" in line or "\r" in line:
        raise ValueError("Agent process messages must occupy a single line")
    try:
        payload = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return model.model_validate(payload, context=_DECODED_JSON)
    except RecursionError as error:
        raise ValueError("Agent process message is too deeply nested") from error


def decode_request(raw: bytes | str, max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES) -> AgentProcessRequest:
    return _decode(raw, AgentProcessRequest, max_message_bytes)


def decode_response(raw: bytes | str, max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES) -> AgentProcessResponse:
    return _decode(raw, AgentProcessResponse, max_message_bytes)


def encode_message(
    message: AgentProcessRequest | AgentProcessResponse,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> bytes:
    """Encode one validated message, counting its newline against the limit."""
    _message_limit(max_message_bytes)
    if type(message) not in (AgentProcessRequest, AgentProcessResponse):
        raise TypeError("Expected an agent process request or response")
    validated = type(message).model_validate(message.model_dump(mode="python", warnings=False))
    return _encode_validated(validated, max_message_bytes)


def _encode_validated(message, max_message_bytes: int) -> bytes:
    """Serialize a fresh validated envelope owned exclusively by this module."""
    try:
        # The model was fully validated before this call, including finite JSON values.
        # Serialize directly in Pydantic's compiled serializer instead of
        # materializing another dict tree for the standard-library encoder.
        encoded = (message.model_dump_json() + "\n").encode("utf-8")
    except RecursionError as error:
        raise ValueError("Agent process message is too deeply nested") from error
    if len(encoded) > max_message_bytes:
        raise ValueError(f"Agent process message exceeds {max_message_bytes} bytes")
    return encoded


def encode_request(request_id, operation, payload, max_message_bytes=DEFAULT_MAX_MESSAGE_BYTES) -> bytes:
    """Construct, validate, and encode an owned request exactly once.

    Unlike ``encode_message``, no mutable envelope escapes to a caller between
    construction and encoding. Payload validation also creates owned JSON
    containers, so a second dump/revalidation adds no boundary protection.
    """
    _message_limit(max_message_bytes)
    message = AgentProcessRequest(
        protocol_version="1.0", request_id=request_id, operation=operation, payload=payload,
    )
    return _encode_validated(message, max_message_bytes)


def encode_response(
    request_id, operation, *, payload=None, error=None, max_message_bytes=DEFAULT_MAX_MESSAGE_BYTES,
) -> bytes:
    """Construct, validate, and encode an owned response exactly once."""
    _message_limit(max_message_bytes)
    message = AgentProcessResponse(
        protocol_version="1.0", request_id=request_id, operation=operation,
        ok=error is None, payload={} if payload is None else payload,
        error=error.model_dump(mode="python") if isinstance(error, AgentWorkerError) else error,
    )
    return _encode_validated(message, max_message_bytes)
