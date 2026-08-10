# research_platform/formula_dsl.py
"""Safe formula DSL: parse a restricted Python expression into a causal factor panel.

A factor formula is a single Python expression over OHLCV-derived panels and the
platform's causal Alpha101 operators. Parsing goes through an AST whitelist
(deny-by-default), so the only expressible computations are causal by
construction — no look-ahead is possible and no arbitrary code executes.
"""
from __future__ import annotations
import ast
import numpy as np
import pandas as pd
from factor_section.alpha101 import (
    _finite, adv, correlation, covariance, delay, delta, rank, safe_divide,
    sign, signed_power, stddev, ts_max, ts_min, ts_rank, ts_sum, where,
)

class FormulaError(ValueError):
    """Raised when a formula uses a construct or name outside the whitelist."""

def _log(frame): return _finite(np.log(frame.where(frame > 0)))
def _abs(frame): return _finite(frame.abs())

ALLOWED_OPERATORS = {
    "rank": rank, "delay": delay, "delta": delta, "ts_sum": ts_sum,
    "ts_min": ts_min, "ts_max": ts_max, "stddev": stddev, "ts_rank": ts_rank,
    "correlation": correlation, "covariance": covariance, "sign": sign,
    "signed_power": signed_power, "safe_divide": safe_divide, "where": where,
    "adv": adv, "log": _log, "abs": _abs,
}
ALLOWED_INPUTS = ("open", "high", "low", "close", "volume", "returns", "vwap", "adv20")

_ALLOWED_NODES = (
    ast.Expression, ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.USub, ast.UAdd,
)

def validate_ast(tree: ast.AST) -> None:
    allowed_names = set(ALLOWED_OPERATORS) | set(ALLOWED_INPUTS)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_OPERATORS:
                raise FormulaError("call to non-whitelisted function")
            if node.keywords:
                raise FormulaError("keyword arguments are not allowed in formulas")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise FormulaError(
                f"unknown name '{node.id}'. allowed inputs: {sorted(ALLOWED_INPUTS)}; "
                f"allowed operators: {sorted(ALLOWED_OPERATORS)}"
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise FormulaError(f"only numeric constants allowed, got {node.value!r}")

def evaluate_formula(expr: str, panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"could not parse formula: {exc}") from exc
    validate_ast(tree)
    namespace = {**ALLOWED_OPERATORS, **{k: panels[k] for k in ALLOWED_INPUTS if k in panels}}
    result = eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, namespace)
    if not isinstance(result, pd.DataFrame):
        raise FormulaError("formula must evaluate to a panel (DataFrame), got a scalar/other")
    return _finite(result)
