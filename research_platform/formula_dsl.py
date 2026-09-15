# research_platform/formula_dsl.py
"""Safe formula DSL: parse a restricted Python expression into a causal factor panel.

A factor formula is a single Python expression over OHLCV-derived panels and the
platform's causal Alpha101 operators. Parsing goes through an AST whitelist
(deny-by-default), so the only expressible computations are causal by
construction — no look-ahead is possible and no arbitrary code executes.
"""
from __future__ import annotations
import ast
import inspect
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
        if isinstance(node, ast.Constant) and (
            not isinstance(node.value, (int, float)) or isinstance(node.value, bool)
        ):
            # bool is a subclass of int in Python; reject it so True/False can't
            # slip past the "numeric constants only" rule.
            raise FormulaError(f"only numeric constants allowed, got {node.value!r}")

_MAX_FORMULA_LEN = 2000

def evaluate_formula(expr: str, panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # This is a boundary over user-supplied strings; every failure mode must
    # surface as FormulaError, never a raw parser/eval exception.
    if len(expr) > _MAX_FORMULA_LEN:
        raise FormulaError(f"formula too long ({len(expr)} > {_MAX_FORMULA_LEN} chars)")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise FormulaError(f"could not parse formula: {exc}") from exc
    validate_ast(tree)
    namespace = {**ALLOWED_OPERATORS, **{k: panels[k] for k in ALLOWED_INPUTS if k in panels}}
    try:
        result = eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, namespace)
    except NameError as exc:
        raise FormulaError(f"formula uses an input not provided by the caller: {exc}") from exc
    if not isinstance(result, pd.DataFrame):
        raise FormulaError("formula must evaluate to a panel (DataFrame), got a scalar/other")
    return _finite(result)

_INPUT_NOTES = {
    "open": "当日开盘价",
    "high": "当日最高价",
    "low": "当日最低价",
    "close": "当日收盘价",
    "volume": "当日美元成交量 (OHLC4 × 股数, Alpha101 口径)",
    "returns": "当日收益率 close.pct_change()",
    "vwap": "成交量加权均价 (以 OHLC4 近似)",
    "adv20": "20 日平均美元成交量",
}

_EXAMPLES = (
    ("5 日反转", "-(close / delay(close,5) - 1)"),
    ("量价背离 (Alpha101 #12)", "sign(delta(volume,1)) * (-delta(close,1))"),
    ("6 个月动量, 跳过最近 5 日", "delay(close,5) / delay(close,126) - 1"),
    ("量价 20 日相关性", "-rank(correlation(close, log(volume), 20))"),
)


def describe_vocabulary() -> str:
    """Render the DSL whitelist as a capability card.

    This is the single source of truth handed to a translator: it is generated
    from ``ALLOWED_INPUTS``/``ALLOWED_OPERATORS`` rather than maintained by
    hand, so the menu can never drift from what ``validate_ast`` accepts.
    Deterministic -- no timestamps, no ordering by dict insertion.
    """
    lines = ["# 因子公式能力卡", "", "## INPUTS (只有这 8 个字段可用)", ""]
    for name in ALLOWED_INPUTS:
        lines.append(f"- {name}: {_INPUT_NOTES[name]}")

    lines += ["", "## OPERATORS (只有这 17 个算子可用)", ""]
    for name in sorted(ALLOWED_OPERATORS):
        params = ", ".join(inspect.signature(ALLOWED_OPERATORS[name]).parameters)
        lines.append(f"- {name}({params})")

    lines += [
        "",
        "## 因果性与语法约定",
        "",
        "- 所有算子只向后看；不存在任何前视构造。",
        "- 信号在交易日 t 生成，组合从 t+1 开始持有。",
        "- 公式是单个 Python 表达式，只允许 + - * / % ** 与一元正负号。",
        "- 不支持 keyword arguments、属性访问、下标、lambda、推导式、布尔常量。",
        "- 常量只能是数字。",
        "",
        "## EXAMPLES",
        "",
    ]
    for label, formula in _EXAMPLES:
        lines.append(f"- {label}: {formula}")
    return "\n".join(lines) + "\n"
