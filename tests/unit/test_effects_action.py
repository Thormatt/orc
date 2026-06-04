"""Action envelope: round-trip + JSON-Schema-subset param validation."""

from __future__ import annotations

import pytest

from orc.effects.action import Action, ActionValidationError, validate_params


def test_action_round_trips_through_dict() -> None:
    action = Action(
        executor="fs.write_file",
        version=1,
        params={"path": "out.txt", "content": "hi"},
        idempotency_key="abc123",
    )
    restored = Action.from_dict(action.to_dict())
    assert restored == action
    assert restored.constraints == {}


def test_from_dict_rejects_missing_fields() -> None:
    with pytest.raises(ActionValidationError):
        Action.from_dict({"executor": "fs.write_file", "version": 1})


_SCHEMA = {
    "type": "object",
    "required": ["path", "content"],
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "mode": {"type": "integer"},
    },
}


def test_validate_params_accepts_valid() -> None:
    validate_params({"path": "out.txt", "content": "hi"}, _SCHEMA)  # no raise


def test_validate_params_rejects_missing_required() -> None:
    with pytest.raises(ActionValidationError):
        validate_params({"path": "out.txt"}, _SCHEMA)


def test_validate_params_rejects_wrong_type() -> None:
    with pytest.raises(ActionValidationError):
        validate_params({"path": 123, "content": "hi"}, _SCHEMA)


def test_validate_params_rejects_bool_as_integer() -> None:
    # bool is a subclass of int in Python; an integer field must not accept True.
    with pytest.raises(ActionValidationError):
        validate_params({"path": "p", "content": "c", "mode": True}, _SCHEMA)
