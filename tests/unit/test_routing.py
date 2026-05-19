"""Tests for the domain-to-mode router."""

from __future__ import annotations

import pytest

from orc.directives.research.routing import (
    DOMAIN_TO_MODE,
    UnknownDomainError,
    route_to_mode,
)


def test_route_to_mode_returns_expected_modes_for_each_domain() -> None:
    # The mapping is the load-bearing piece: per-source-ds F1 in the
    # source-routed HaluBench result. If this changes without a benchmark
    # re-run, the public F1 claim drifts from reality.
    assert DOMAIN_TO_MODE["covidQA"] == "evidence"
    assert DOMAIN_TO_MODE["RAGTruth"] == "evidence"
    assert DOMAIN_TO_MODE["halueval"] == "judgment"
    assert DOMAIN_TO_MODE["pubmedQA"] == "binary"
    assert DOMAIN_TO_MODE["FinanceBench"] == "arithmetic"
    assert DOMAIN_TO_MODE["DROP"] == "binary"
    # Every value must be one of the modes verify_claim accepts.
    valid_modes = {"evidence", "judgment", "binary", "decomposed", "arithmetic"}
    assert set(DOMAIN_TO_MODE.values()) <= valid_modes


def test_route_to_mode_none_returns_none() -> None:
    """None in → None out so verify_claim can fall back to its default mode."""
    assert route_to_mode(None) is None


def test_route_to_mode_known_domain_returns_mode() -> None:
    assert route_to_mode("pubmedQA") == "binary"
    assert route_to_mode("covidQA") == "evidence"


def test_route_to_mode_unknown_domain_raises() -> None:
    """Silent fall-through would mask config typos and break replay determinism."""
    with pytest.raises(UnknownDomainError) as excinfo:
        route_to_mode("MadeUpDomain")
    # Error message should help the caller discover valid options.
    assert "MadeUpDomain" in str(excinfo.value)
    assert "covidQA" in str(excinfo.value)
