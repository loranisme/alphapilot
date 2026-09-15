from __future__ import annotations
import pytest
from research_platform.cli import main
from research_platform.nl_translator import StubTranslator, TranslationPayload


def _payload(**kw):
    base = dict(feasible=True, formula="-(close / delay(close,5) - 1)",
                explanation="5 日反转", missing_data=[],
                nearest_formula=None, nearest_caveat=None)
    base.update(kw)
    return TranslationPayload(**base)


def test_vocabulary_prints_the_card(capsys):
    assert main(["vocabulary"]) == 0
    out = capsys.readouterr().out
    assert "adv20" in out and "signed_power" in out


def test_validate_idea_without_a_translator_prints_the_card_and_exits_2(monkeypatch, capsys):
    from research_platform import cli
    monkeypatch.setattr(cli, "resolve_translator", lambda: None)
    assert main(["validate-idea", "--idea", "5 日反转"]) == 2
    out = capsys.readouterr().out
    assert "adv20" in out                      # the card itself
    assert "validate-factor" in out            # an actionable next step


def test_validate_idea_infeasible_exits_2_and_never_runs_the_backtest(monkeypatch, capsys):
    from research_platform import cli
    ran = []
    payload = _payload(feasible=False, formula=None, explanation="缺数据",
                       missing_data=["分析师一致预期 EPS"])
    monkeypatch.setattr(cli, "resolve_translator", lambda: StubTranslator([payload]))
    monkeypatch.setattr(cli, "_run_validate_factor", lambda **kw: ran.append(kw))
    assert main(["validate-idea", "--idea", "按分析师上调选股"]) == 2
    assert ran == []                            # the whole point of refusing
    assert "分析师一致预期 EPS" in capsys.readouterr().out


def test_validate_idea_yes_skips_confirmation_and_runs(monkeypatch, capsys):
    from research_platform import cli
    ran = []
    monkeypatch.setattr(cli, "resolve_translator", lambda: StubTranslator([_payload()]))
    monkeypatch.setattr(cli, "_run_validate_factor",
                        lambda **kw: ran.append(kw) or {"slug": "abc", "summary": "s",
                                                        "output_dir": "outputs/x"})
    assert main(["validate-idea", "--idea", "5 日反转", "--name", "rev5", "--yes"]) == 0
    assert ran[0]["formula"] == "-(close / delay(close,5) - 1)"
    assert ran[0]["name"] == "rev5"
    assert ran[0]["provenance"]["idea"] == "5 日反转"


def test_validate_idea_declined_confirmation_exits_3_without_running(monkeypatch):
    from research_platform import cli
    ran = []
    monkeypatch.setattr(cli, "resolve_translator", lambda: StubTranslator([_payload()]))
    monkeypatch.setattr(cli, "_run_validate_factor", lambda **kw: ran.append(kw))
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert main(["validate-idea", "--idea", "5 日反转"]) == 3
    assert ran == []


def test_validate_idea_auth_failure_degrades_to_the_card(monkeypatch, capsys):
    from research_platform import cli
    from research_platform.nl_translator import TranslatorUnavailable

    def boom(idea, vocabulary, feedback=None):
        raise TranslatorUnavailable("bad key")
    boom.name, boom.model = "claude", "claude-opus-5"
    monkeypatch.setattr(cli, "resolve_translator", lambda: boom)
    assert main(["validate-idea", "--idea", "5 日反转"]) == 2
    assert "adv20" in capsys.readouterr().out
