"""Safe arithmetic evaluator tests.

Security tests are the load-bearing piece: the calculator runs untrusted
LLM-supplied expressions, so the allow-list must reject anything that
could leak state or compute beyond pure arithmetic.
"""

from __future__ import annotations

import pytest

from orc.llm.tools.calculate import (
    CalculatorError,
    execute,
    safe_eval,
)


def test_safe_eval_basic_arithmetic() -> None:
    assert safe_eval("1+2") == 3.0
    assert safe_eval("10-3") == 7.0
    assert safe_eval("4*5") == 20.0
    assert safe_eval("10/4") == 2.5
    assert safe_eval("10//3") == 3.0
    assert safe_eval("10%3") == 1.0
    assert safe_eval("2**8") == 256.0


def test_safe_eval_parens_and_precedence() -> None:
    assert safe_eval("(1+2)*3") == 9.0
    assert safe_eval("1+2*3") == 7.0
    assert safe_eval("-(5)") == -5.0
    assert safe_eval("+3") == 3.0


def test_safe_eval_floats() -> None:
    assert safe_eval("1.5/3") == 0.5
    assert safe_eval("1234.5 / 8000 * 100") == pytest.approx(15.43125)


def test_safe_eval_rejects_function_calls() -> None:
    """The classic exploit vector — must be blocked."""
    with pytest.raises(CalculatorError):
        safe_eval("__import__('os').system('echo pwned')")
    with pytest.raises(CalculatorError):
        safe_eval("len([1,2,3])")
    with pytest.raises(CalculatorError):
        safe_eval("abs(-5)")


def test_safe_eval_rejects_names() -> None:
    """No variable lookups — that would leak Python builtins."""
    with pytest.raises(CalculatorError):
        safe_eval("x + 1")
    with pytest.raises(CalculatorError):
        safe_eval("os.path")


def test_safe_eval_rejects_attribute_access() -> None:
    with pytest.raises(CalculatorError):
        safe_eval("(1).bit_length")


def test_safe_eval_rejects_strings() -> None:
    """Non-numeric literals are blocked."""
    with pytest.raises(CalculatorError):
        safe_eval("'abc'")
    with pytest.raises(CalculatorError):
        safe_eval("True")


def test_safe_eval_rejects_subscripts() -> None:
    with pytest.raises(CalculatorError):
        safe_eval("[1,2,3][0]")


def test_safe_eval_rejects_large_exponents() -> None:
    """Block CPU/memory DoS via deep exponentiation."""
    with pytest.raises(CalculatorError, match="exponent too large"):
        safe_eval("2 ** 100")


def test_safe_eval_division_by_zero_is_user_error() -> None:
    """Surface as CalculatorError so the model can react in-band, not crash."""
    with pytest.raises(CalculatorError, match="division by zero"):
        safe_eval("1 / 0")


def test_safe_eval_rejects_empty_and_oversized() -> None:
    with pytest.raises(CalculatorError):
        safe_eval("")
    with pytest.raises(CalculatorError):
        safe_eval("   ")
    with pytest.raises(CalculatorError):
        safe_eval("1+" * 200)  # 400+ chars, exceeds 256 limit


def test_execute_returns_string_results() -> None:
    """execute() adapts safe_eval into the string format the agentic loop
    feeds back via tool_result."""
    assert execute({"expression": "1+2"}) == "3"
    assert execute({"expression": "1.5/3"}) == "0.5"
    # Error messages come back in-band as "ERROR: ..." so the LLM can react.
    assert execute({"expression": "abs(-1)"}).startswith("ERROR")


def test_execute_handles_missing_input() -> None:
    """Empty/missing expression must surface as in-band error, not crash."""
    assert execute({}).startswith("ERROR")
    assert execute({"expression": ""}).startswith("ERROR")
