"""Tests for the domain-to-mode router."""

from __future__ import annotations

import pytest

from orc.directives.research.routing import (
    BENCHMARK_SOURCE_TO_MODE,
    DOMAIN_TO_MODE,
    UnknownDomainError,
    route_to_mode,
)


def test_benchmark_source_map_returns_expected_modes_for_each_source() -> None:
    # The mapping is the load-bearing piece: per-source-ds F1 in the
    # source-routed HaluBench result. If this changes without a benchmark
    # re-run, the public F1 claim drifts from reality.
    assert BENCHMARK_SOURCE_TO_MODE["covidQA"] == "evidence"
    assert BENCHMARK_SOURCE_TO_MODE["RAGTruth"] == "evidence"
    assert BENCHMARK_SOURCE_TO_MODE["halueval"] == "judgment"
    assert BENCHMARK_SOURCE_TO_MODE["pubmedQA"] == "binary"
    assert BENCHMARK_SOURCE_TO_MODE["FinanceBench"] == "arithmetic"
    assert BENCHMARK_SOURCE_TO_MODE["DROP"] == "binary"
    # Every value must be one of the modes verify_claim accepts.
    valid_modes = {"evidence", "judgment", "binary", "decomposed", "arithmetic"}
    assert set(BENCHMARK_SOURCE_TO_MODE.values()) <= valid_modes
    assert set(DOMAIN_TO_MODE.values()) <= valid_modes


def test_benchmark_source_map_contains_exactly_the_six_halubench_sources() -> None:
    """Published benchmark numbers were produced with exactly these six
    source_ds names — extra or missing keys mean reproducibility drift."""
    assert set(BENCHMARK_SOURCE_TO_MODE) == {
        "covidQA",
        "RAGTruth",
        "halueval",
        "pubmedQA",
        "FinanceBench",
        "DROP",
    }


def test_route_to_mode_none_returns_none() -> None:
    """None in → None out so verify_claim can fall back to its default mode."""
    assert route_to_mode(None) is None


def test_route_to_mode_benchmark_source_aliases_still_route() -> None:
    """Dataset names predate the product domains; existing callers passing
    them must keep routing identically."""
    assert route_to_mode("pubmedQA") == "binary"
    assert route_to_mode("covidQA") == "evidence"


def test_route_to_mode_routes_each_product_domain() -> None:
    """Product domains are the real surface — each routes to the mode derived
    from the benchmark family it generalizes."""
    assert route_to_mode("general") == "evidence"
    assert route_to_mode("legal") == "evidence"
    assert route_to_mode("clinical") == "binary"
    assert route_to_mode("biomedical") == "binary"
    assert route_to_mode("financial") == "arithmetic"
    assert route_to_mode("numeric") == "binary"


def test_route_to_mode_unknown_domain_message_lists_product_domains() -> None:
    """The error must teach the product surface first; benchmark dataset
    names are aliases and should be mentioned separately, not as peers."""
    with pytest.raises(UnknownDomainError) as excinfo:
        route_to_mode("MadeUpDomain")
    message = str(excinfo.value)
    for product_domain in ("general", "legal", "clinical", "financial"):
        assert product_domain in message
    assert "alias" in message


def test_route_to_mode_unknown_domain_raises() -> None:
    """Silent fall-through would mask config typos and break replay determinism."""
    with pytest.raises(UnknownDomainError) as excinfo:
        route_to_mode("MadeUpDomain")
    # Error message should help the caller discover valid options.
    assert "MadeUpDomain" in str(excinfo.value)
    assert "covidQA" in str(excinfo.value)
