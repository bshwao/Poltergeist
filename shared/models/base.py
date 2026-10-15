"""Common validation rules and JSON-safe numeric types."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]
NonNegativeFloat = Annotated[float, Field(ge=0, strict=True)]
PositiveFloat = Annotated[float, Field(gt=0, strict=True)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
Seed = Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]


class ContractModel(BaseModel):
    """Reject unknown fields and non-finite numbers at every contract boundary."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
        validate_default=True,
        json_schema_serialization_defaults_required=True,
    )
