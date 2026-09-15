from __future__ import annotations
import pytest
from research_platform.nl_translator import (
    StubTranslator, TranslationPayload, translate_idea,
)


def _payload(**kw):
    base = dict(feasible=True, formula="-(close / delay(close,5) - 1)",
                explanation="5 日反转", missing_data=[],
                nearest_formula=None, nearest_caveat=None)
    base.update(kw)
    return TranslationPayload(**base)


def test_feasible_formula_passes_through():
    result = translate_idea("5 日反转", StubTranslator([_payload()]))
    assert result.feasible is True
    assert result.formula == "-(close / delay(close,5) - 1)"
    assert result.attempts == 1
    assert result.translator == "stub"


def test_infeasible_reports_missing_data_and_never_invents_a_formula():
    payload = _payload(feasible=False, formula=None,
                       explanation="需要分析师预期数据",
                       missing_data=["分析师一致预期 EPS"])
    result = translate_idea("按分析师上调幅度选股", StubTranslator([payload]))
    assert result.feasible is False
    assert result.formula is None
    assert result.missing_data == ("分析师一致预期 EPS",)


def test_illegal_formula_is_retried_with_the_error_as_feedback():
    bad = _payload(formula="close / analyst_eps")      # unknown name
    good = _payload()
    stub = StubTranslator([bad, good])
    result = translate_idea("随便", stub)
    assert result.feasible is True
    assert result.attempts == 2
    # the retry must actually carry the parser's complaint back to the model
    assert "analyst_eps" in stub.feedback_seen[-1]


def test_retries_exhausted_returns_infeasible_with_raw_output():
    bad = _payload(formula="close / analyst_eps")
    stub = StubTranslator([bad, bad, bad])
    result = translate_idea("随便", stub, max_retries=2)
    assert result.feasible is False
    assert result.attempts == 3
    assert "analyst_eps" in result.raw_output


def test_illegal_nearest_formula_is_discarded_with_a_caveat():
    payload = _payload(feasible=False, formula=None, explanation="缺数据",
                       missing_data=["期权隐含波动率"],
                       nearest_formula="iv_skew * close",   # invalid
                       nearest_caveat="用 IV 偏斜近似")
    result = translate_idea("IV skew 因子", StubTranslator([payload]))
    assert result.nearest_formula is None
    assert "iv_skew" in result.nearest_caveat


def test_valid_nearest_formula_is_kept():
    payload = _payload(feasible=False, formula=None, explanation="缺数据",
                       missing_data=["新闻情绪"],
                       nearest_formula="-(close / delay(close,5) - 1)",
                       nearest_caveat="用价格反转近似情绪反转")
    result = translate_idea("新闻情绪反转", StubTranslator([payload]))
    assert result.nearest_formula == "-(close / delay(close,5) - 1)"
