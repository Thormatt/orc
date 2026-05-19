"""Safe arithmetic evaluator + Anthropic tool schema.

Used by verify_claim's `arithmetic` mode for FinanceBench-style numeric claims
("FY2022 EBITDA margin was 15.2%"). The LLM emits a `calculate` tool_use block
with an expression; the runtime evaluates it via `safe_eval` and feeds the
result back. The expression and its result are recorded in the trace so an
auditor can spot-check the math.

Security: no `eval()`, no `exec()`. The evaluator walks the AST and only
permits literal numbers, parens, and the six standard arithmetic operators
(plus unary +/-). Anything else — function calls, names, attribute access,
subscripts, string ops — raises CalculatorError before any value is produced.
"""

from __future__ import annotations

import ast
from typing import Any


class CalculatorError(ValueError):
    """Raised when an expression cannot be safely evaluated."""


CALCULATE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "calculate",
    "description": (
        "Evaluate a pure arithmetic expression and return the numeric result. "
        "Supported: numbers (int/float), parentheses, operators +, -, *, /, "
        "//, %, **, and unary -. NO variables, function calls, or names — "
        "literal arithmetic only. Use this when the claim requires a "
        "computation against numbers found in the passage."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "A pure arithmetic expression. Example: '1234.5 / 8000 * 100'."
                ),
            },
        },
        "required": ["expression"],
    },
}


_ALLOWED_BINOPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


def safe_eval(expression: str) -> float:
    """Evaluate `expression` as pure arithmetic. Returns a float.

    Raises CalculatorError for syntax errors, disallowed AST nodes, or
    runtime arithmetic failures (e.g. division by zero, expression too long).
    """
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("expression must be a non-empty string")
    if len(expression) > 256:
        raise CalculatorError("expression too long (max 256 chars)")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"syntax error: {exc.msg}") from exc

    _assert_safe(tree)
    try:
        result = _eval_node(tree.body)
    except ZeroDivisionError as exc:
        raise CalculatorError("division by zero") from exc
    except OverflowError as exc:
        raise CalculatorError(f"overflow: {exc}") from exc

    return float(result)


def execute(input: dict[str, Any]) -> str:
    """Adapter the agentic loop calls when the model invokes the `calculate`
    tool. Returns a string the runtime appends as a tool_result message.
    Errors are returned in-band as `"ERROR: ..."` strings — the model can
    react and try a different expression on the next turn."""
    expression = input.get("expression", "")
    try:
        value = safe_eval(expression)
    except CalculatorError as exc:
        return f"ERROR: {exc}"
    # Prefer a stable, readable repr: integers without ".0", floats with
    # enough precision for the judge to compare against the passage.
    if value == int(value):
        return str(int(value))
    return f"{value:.6g}"


def _assert_safe(tree: ast.AST) -> None:
    """Walk the AST once and reject anything other than the whitelist."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Expression):
            continue
        if isinstance(node, ast.Constant):
            # bool is a subclass of int in Python — reject True/False explicitly
            # so the model can't smuggle in non-arithmetic flow via boolean literals.
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise CalculatorError(
                    f"only numeric literals allowed, got {type(node.value).__name__}"
                )
            continue
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise CalculatorError(f"operator not allowed: {type(node.op).__name__}")
            continue
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise CalculatorError(f"unary operator not allowed: {type(node.op).__name__}")
            continue
        if isinstance(node, (*_ALLOWED_BINOPS, *_ALLOWED_UNARYOPS)):
            continue
        raise CalculatorError(f"AST node not allowed: {type(node).__name__}")


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            # Bound the exponent to keep the AST evaluator from being used as
            # a CPU/memory DoS via something like `2**(2**30)`.
            if abs(right) > 64:
                raise CalculatorError("exponent too large (max abs 64)")
            return left**right
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
    raise CalculatorError(f"unexpected node during eval: {type(node).__name__}")
