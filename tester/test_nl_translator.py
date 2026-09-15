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


class _FakeMessages:
    def __init__(self, payload, recorder):
        self._payload = payload
        self._recorder = recorder

    def parse(self, **kwargs):
        self._recorder.append(kwargs)
        payload = self._payload

        class _Resp:
            parsed_output = payload
        return _Resp()


class _FakeClient:
    def __init__(self, payload, recorder):
        self.messages = _FakeMessages(payload, recorder)


def test_claude_translator_passes_card_and_model_and_returns_parsed_output():
    # duck-typed fake client: this test must run with anthropic absent
    from research_platform.nl_translator import ClaudeTranslator, MODEL
    calls = []
    translator = ClaudeTranslator(client=_FakeClient(_payload(), calls))
    out = translator("5 日反转", "CARD-TEXT", feedback=None)
    assert out.formula == "-(close / delay(close,5) - 1)"
    assert translator.name == "claude"
    assert translator.model == MODEL
    sent = calls[0]
    assert sent["model"] == MODEL
    assert sent["output_format"] is TranslationPayload
    assert "CARD-TEXT" in sent["system"]
    assert "5 日反转" in sent["messages"][0]["content"]


def test_claude_translator_forwards_feedback_into_the_user_message():
    from research_platform.nl_translator import ClaudeTranslator
    calls = []
    translator = ClaudeTranslator(client=_FakeClient(_payload(), calls))
    translator("想法", "CARD", feedback="公式非法：unknown name 'foo'")
    assert "unknown name 'foo'" in calls[0]["messages"][0]["content"]


def test_module_imports_without_anthropic_installed():
    # the whole graceful-degradation story rests on this
    import importlib
    mod = importlib.import_module("research_platform.nl_translator")
    assert hasattr(mod, "ClaudeTranslator")


def test_resolve_translator_returns_none_when_anthropic_is_missing(monkeypatch):
    import builtins
    from research_platform import nl_translator
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert nl_translator.resolve_translator() is None


def test_resolve_translator_returns_none_when_client_construction_fails(monkeypatch):
    import sys, types
    from research_platform import nl_translator
    fake = types.ModuleType("anthropic")

    def boom(*a, **k):
        raise RuntimeError("could not resolve authentication method")

    fake.Anthropic = boom
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert nl_translator.resolve_translator() is None


def test_resolve_translator_returns_claude_when_configured(monkeypatch):
    import sys, types
    from research_platform import nl_translator
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    translator = nl_translator.resolve_translator()
    assert translator is not None and translator.name == "claude"


def test_auth_error_is_classified_unavailable_and_ratelimit_runtime():
    anthropic = pytest.importorskip("anthropic")
    import httpx
    from research_platform.nl_translator import TranslatorUnavailable, classify_exception
    req = httpx.Request("POST", "https://api.anthropic.com")
    auth = anthropic.AuthenticationError(
        "bad key", response=httpx.Response(401, request=req), body=None)
    assert isinstance(classify_exception(auth), TranslatorUnavailable)
    rate = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=req), body=None)
    assert not isinstance(classify_exception(rate), TranslatorUnavailable)
