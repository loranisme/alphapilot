"""Opt-in: really calls the API. Run with RUN_LLM_TESTS=1."""
from __future__ import annotations
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_LLM_TESTS"),
    reason="set RUN_LLM_TESTS=1 to exercise the live Claude translator",
)


def test_expressible_idea_translates_to_a_valid_formula():
    from research_platform.nl_translator import resolve_translator, translate_idea
    translator = resolve_translator()
    assert translator is not None, "no credentials; cannot run the live test"
    result = translate_idea("过去 5 天跌得越多的股票越买", translator)
    assert result.feasible is True
    assert result.formula                      # already proven legal by validate_ast


def test_idea_needing_absent_data_is_refused():
    from research_platform.nl_translator import resolve_translator, translate_idea
    translator = resolve_translator()
    assert translator is not None
    result = translate_idea("按分析师一致预期 EPS 的上调幅度选股", translator)
    assert result.feasible is False
    assert result.missing_data                 # must say what is missing
    assert result.formula is None              # must not invent one
