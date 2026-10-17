"""Three-valued reproduction decisions over an explicit experiment budget."""

from collections.abc import Iterable


def summarize_reproduction(
    outcomes: Iterable[bool | None], *, total_repeats: int,
    required_successes: int,
) -> bool | None:
    """Return whether the configured success requirement was established.

    Missing repeats count as unknown, as do failed or unscored child runs passed
    as ``None``. The caller decides whether an operation is complete enough to
    publish this decision to a failure report.
    """
    if type(total_repeats) is not int or not 1 <= total_repeats <= 20:
        raise ValueError("total_repeats must be an integer between 1 and 20")
    if type(required_successes) is not int or not 1 <= required_successes <= total_repeats:
        raise ValueError("required_successes must be an integer between 1 and total_repeats")
    values = list(outcomes)
    if len(values) > total_repeats:
        raise ValueError("outcomes exceed total_repeats")
    if any(value is not None and type(value) is not bool for value in values):
        raise ValueError("outcomes must contain only true, false, or null")
    successes = sum(value is True for value in values)
    unknown = total_repeats - len(values) + sum(value is None for value in values)
    if successes >= required_successes:
        return True
    if successes + unknown < required_successes:
        return False
    return None
