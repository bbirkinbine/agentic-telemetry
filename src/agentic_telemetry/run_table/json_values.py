"""Narrowing helpers for values decoded by `json.loads`.

Evidence files are written by other tools, so every field is checked before use.
These helpers turn loosely typed JSON into the exact types the run table stores,
which keeps `Any` out of the rest of the package.
"""

import json

JsonObject = dict[str, object]


def as_object(value: object) -> JsonObject | None:
    """Return `value` as a JSON object, or None when it is not one.

    Args:
        value: A decoded JSON value.

    Returns:
        The object with its keys as strings, or None for any other JSON type.
    """
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def as_json_text(value: object) -> str | None:
    """Return a string unchanged, None for null, and JSON text for anything else."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def opt_str(value: object) -> str | None:
    """Return `value` when it is a string, otherwise None."""
    return value if isinstance(value, str) else None


def opt_bool(value: object) -> bool | None:
    """Return `value` when it is a JSON boolean, otherwise None."""
    return value if isinstance(value, bool) else None


def opt_int(value: object) -> int | None:
    """Return `value` when it is a JSON integer (not a boolean), otherwise None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def opt_number(value: object) -> int | float | None:
    """Return `value` when it is a JSON number (not a boolean), otherwise None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def str_list(value: object) -> tuple[str, ...]:
    """Return a JSON array as strings; non-string items become JSON text.

    Args:
        value: A decoded JSON value, expected to be an array.

    Returns:
        One string per array item, or an empty tuple when `value` is not an array.
    """
    if not isinstance(value, list):
        return ()
    return tuple(item if isinstance(item, str) else json.dumps(item) for item in value)


def object_list(value: object) -> list[JsonObject]:
    """Return the JSON objects inside an array, skipping any other items."""
    if not isinstance(value, list):
        return []
    objects = [as_object(item) for item in value]
    return [item for item in objects if item is not None]
