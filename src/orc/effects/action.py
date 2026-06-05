"""The typed Action envelope — the validated contract between the two planes.

The analysis plane can only *describe* an effect as an `Action`; it never executes
one. `params` is validated against the target executor's JSON-Schema-subset both
at propose time (analysis plane) and at execute time (effect plane).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orc.errors import OrcError


class ActionValidationError(OrcError):
    """An Action envelope is malformed, or its params violate the executor schema."""


@dataclass(frozen=True)
class Action:
    executor: str
    version: int
    params: dict[str, Any]
    idempotency_key: str
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "version": self.version,
            "params": self.params,
            "idempotency_key": self.idempotency_key,
            "constraints": self.constraints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        required = ("executor", "version", "params", "idempotency_key")
        missing = [k for k in required if k not in data]
        if missing:
            raise ActionValidationError(f"Action missing required fields: {missing}")
        return cls(
            executor=str(data["executor"]),
            version=int(data["version"]),
            params=dict(data["params"]),
            idempotency_key=str(data["idempotency_key"]),
            constraints=dict(data.get("constraints", {})),
        )


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
}


def validate_params(params: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate params against a JSON-Schema subset (type/required/properties/items).

    Deliberately small: enough to constrain the closed set of executor params
    without pulling in a full JSON-Schema engine. Raises ActionValidationError.
    """
    _validate(params, schema, path="params")


def _validate(value: Any, schema: dict[str, Any], *, path: str) -> None:
    expected = schema.get("type")
    if expected is not None:
        # bool is a subclass of int — reject it for numeric fields explicitly.
        if expected in ("integer", "number") and isinstance(value, bool):
            raise ActionValidationError(f"{path}: expected {expected}, got boolean")
        py_type = _JSON_TYPES.get(expected)
        if py_type is not None and not isinstance(value, py_type):
            raise ActionValidationError(
                f"{path}: expected {expected}, got {type(value).__name__}"
            )

    if expected == "object":
        for req in schema.get("required", []):
            if req not in value:
                raise ActionValidationError(f"{path}: missing required field {req!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ActionValidationError(f"{path}: unexpected properties {extra}")
        for key, subschema in properties.items():
            if key in value:
                _validate(value[key], subschema, path=f"{path}.{key}")

    if expected == "array":
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                _validate(item, item_schema, path=f"{path}[{i}]")
