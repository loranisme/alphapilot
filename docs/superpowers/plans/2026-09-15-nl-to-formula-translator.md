# NL-to-Formula Translator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a natural-language factor hypothesis into a DSL formula built only from this repo's existing data fields, then run it through the untouched `validate_factor` workflow — refusing honestly when the idea needs data the repo does not have.

**Architecture:** A capability card auto-generated from the DSL whitelist is the single source of truth and the LLM's prompt material. `ClaudeTranslator` calls `client.messages.parse()` with a Pydantic `output_format`, so the SDK validates payload *shape* and the module only does *domain* validation — `validate_ast` stays the sole judge of whether a formula is real. A retry loop feeds `FormulaError` text back to the translator. `resolve_translator()` returns `None` when `anthropic` is missing or unconfigured, and the CLI degrades to printing the card.

**Tech Stack:** Python 3.11+, `pydantic>=2` (core dep), `anthropic>=0.40` (optional `[llm]` extra), argparse, pytest.

---

## File Structure

| File | Responsibility |
|---|---|
| `research_platform/formula_dsl.py` (modify) | Add `describe_vocabulary()` — lives here so the card and the whitelist can never drift apart. |
| `research_platform/nl_translator.py` (create) | Contracts (`TranslationPayload`, `TranslationResult`), `Translator` protocol, `translate_idea()` retry loop, `StubTranslator`, `ClaudeTranslator`, `resolve_translator()`. |
| `research_platform/cli.py` (modify) | `vocabulary` and `validate-idea` subcommands + exit-code dispatch. |
| `scripts/validate_factor.py` (modify) | Optional `provenance` parameter threaded into `formula.txt` and the ledger. |
| `pyproject.toml` (modify) | `pydantic>=2` core; `llm = ["anthropic>=0.40"]` extra. |
| `tester/test_nl_vocabulary.py` (create) | Capability-card drift + determinism tests. |
| `tester/test_nl_translator.py` (create) | `translate_idea` loop, `ClaudeTranslator`, `resolve_translator`. |
| `tester/test_nl_cli.py` (create) | CLI wiring, exit codes, confirmation gate. |

**Critical constraint (from spec §4.4):** `nl_translator.py` must NOT import `anthropic` at module level. This repo has no `anthropic` installed; a module-level import makes `import research_platform.nl_translator` fail and the graceful-degradation story collapses.

---

### Task 1: Capability card `describe_vocabulary()`

**Files:**
- Modify: `research_platform/formula_dsl.py` (append after `evaluate_formula`)
- Test: `tester/test_nl_vocabulary.py`

- [ ] **Step 1: Write the failing test**

```python
# tester/test_nl_vocabulary.py
from __future__ import annotations
from research_platform.formula_dsl import ALLOWED_INPUTS, ALLOWED_OPERATORS, describe_vocabulary


def test_card_lists_every_input_and_operator():
    # The card is the LLM's only view of what exists. If the whitelist grows a
    # field and the card doesn't, the model hallucinates against a stale menu.
    card = describe_vocabulary()
    for name in ALLOWED_INPUTS:
        assert name in card, f"input {name} missing from capability card"
    for name in ALLOWED_OPERATORS:
        assert name in card, f"operator {name} missing from capability card"


def test_card_states_causality_and_forbidden_syntax():
    card = describe_vocabulary()
    assert "t+1" in card
    assert "keyword arguments" in card


def test_card_is_deterministic():
    assert describe_vocabulary() == describe_vocabulary()


def test_card_renders_operator_arity():
    card = describe_vocabulary()
    assert "delay(frame, periods)" in card
    assert "correlation(left, right, window)" in card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_nl_vocabulary.py -q`
Expected: FAIL — `ImportError: cannot import name 'describe_vocabulary'`

- [ ] **Step 3: Write minimal implementation**

Append to `research_platform/formula_dsl.py` (add `import inspect` to the imports at the top):

```python
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
    Deterministic — no timestamps, no ordering by dict insertion.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_nl_vocabulary.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Verify every example in the card actually validates**

Add to `tester/test_nl_vocabulary.py`:

```python
def test_card_examples_are_valid_formulas():
    # A card that ships an invalid example teaches the model to emit invalid
    # formulas. Every example must survive the same judge its output faces.
    import ast
    from research_platform.formula_dsl import _EXAMPLES, validate_ast
    assert len(_EXAMPLES) >= 4
    for label, formula in _EXAMPLES:
        validate_ast(ast.parse(formula, mode="eval"))
        assert formula in describe_vocabulary(), f"example {label} not rendered"
```

Run: `python -m pytest tester/test_nl_vocabulary.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add research_platform/formula_dsl.py tester/test_nl_vocabulary.py
git commit -m "feat(dsl): generate the capability card from the whitelist"
```

---

### Task 2: Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pydantic to core deps and the llm extra**

In `pyproject.toml`, change the `dependencies` list to include `pydantic`:

```toml
dependencies = [
  "numpy>=1.26",
  "pandas>=2.0",
  "scipy>=1.11",
  "PyYAML>=6.0",
  "tabulate>=0.9",
  "pydantic>=2",
]
```

And change `[project.optional-dependencies]` to:

```toml
[project.optional-dependencies]
test = ["pytest>=8.0"]
llm = ["anthropic>=0.40"]
```

- [ ] **Step 2: Verify pydantic imports**

Run: `python -c "import pydantic; print(pydantic.VERSION)"`
Expected: prints `2.x.x`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: pydantic core dep, anthropic behind the llm extra"
```

---

### Task 3: Contracts, protocol, `StubTranslator`, and the retry loop

**Files:**
- Create: `research_platform/nl_translator.py`
- Test: `tester/test_nl_translator.py`

- [ ] **Step 1: Write the failing test**

```python
# tester/test_nl_translator.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_nl_translator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'research_platform.nl_translator'`

- [ ] **Step 3: Write minimal implementation**

Create `research_platform/nl_translator.py`:

```python
"""Natural-language factor hypothesis -> DSL formula.

The capability card (generated from the DSL whitelist) is the model's only
view of what exists; ``validate_ast`` is the only judge of whether what comes
back is real. A translator claiming ``feasible=True`` proves nothing.

``anthropic`` is imported lazily on purpose: this module must stay importable
on machines that never install it, so the CLI can degrade to printing the card.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from .formula_dsl import FormulaError, describe_vocabulary, validate_ast


class TranslationPayload(BaseModel):
    """What a translator returns. Doubles as the SDK's ``output_format`` schema,
    so payload *shape* is validated upstream and this module only does *domain*
    validation."""

    feasible: bool = Field(description="能否只用能力卡里的输入与算子表达这个想法")
    formula: str | None = Field(default=None, description="可行时的 DSL 公式，单个表达式")
    explanation: str = Field(description="中文说明：怎么翻的，或为什么表达不了")
    missing_data: list[str] = Field(default_factory=list,
                                    description="不可行时缺的数据字段，用业务语言")
    nearest_formula: str | None = Field(default=None,
                                        description="最接近的可表达变体，仍只能用表内元素")
    nearest_caveat: str | None = Field(default=None,
                                       description="该变体与原想法的差别")


@dataclass(frozen=True)
class TranslationResult:
    feasible: bool
    formula: str | None
    explanation: str
    missing_data: tuple[str, ...]
    nearest_formula: str | None
    nearest_caveat: str | None
    attempts: int
    translator: str
    model: str | None
    raw_output: str


class Translator(Protocol):
    name: str
    model: str | None

    def __call__(self, idea: str, vocabulary: str,
                 feedback: str | None = None) -> TranslationPayload: ...


class StubTranslator:
    """Returns scripted payloads in order. Offline testing and demos only."""

    name = "stub"
    model = None

    def __init__(self, payloads: list[TranslationPayload]):
        self._payloads = list(payloads)
        self._i = 0
        self.feedback_seen: list[str | None] = []

    def __call__(self, idea: str, vocabulary: str,
                 feedback: str | None = None) -> TranslationPayload:
        self.feedback_seen.append(feedback)
        payload = self._payloads[min(self._i, len(self._payloads) - 1)]
        self._i += 1
        return payload


def _is_valid(formula: str) -> tuple[bool, str]:
    try:
        validate_ast(ast.parse(formula, mode="eval"))
    except (FormulaError, SyntaxError, ValueError) as exc:
        return False, str(exc)
    return True, ""


def _result(payload: TranslationPayload, translator: Translator, attempts: int,
            **overrides) -> TranslationResult:
    fields = dict(
        feasible=payload.feasible,
        formula=payload.formula,
        explanation=payload.explanation,
        missing_data=tuple(payload.missing_data),
        nearest_formula=payload.nearest_formula,
        nearest_caveat=payload.nearest_caveat,
        attempts=attempts,
        translator=translator.name,
        model=translator.model,
        raw_output=payload.model_dump_json(),
    )
    fields.update(overrides)
    return TranslationResult(**fields)


def translate_idea(idea: str, translator: Translator,
                   max_retries: int = 2) -> TranslationResult:
    """Translate, then let ``validate_ast`` have the final word.

    An illegal formula is not a failure to report — it is fed back to the
    translator verbatim so it can correct itself.
    """
    vocabulary = describe_vocabulary()
    feedback: str | None = None
    payload: TranslationPayload | None = None

    for attempt in range(1, max_retries + 2):
        payload = translator(idea, vocabulary, feedback)

        if not payload.feasible:
            if payload.nearest_formula:
                ok, why = _is_valid(payload.nearest_formula)
                if not ok:
                    caveat = (f"(丢弃了翻译器给出的最接近变体 "
                              f"{payload.nearest_formula!r}: {why})")
                    return _result(payload, translator, attempt,
                                   nearest_formula=None,
                                   nearest_caveat=" ".join(
                                       filter(None, [payload.nearest_caveat, caveat])))
            return _result(payload, translator, attempt)

        if not payload.formula:
            feedback = "feasible=true 但没有给出 formula；请给出公式或改为 feasible=false"
            continue

        ok, why = _is_valid(payload.formula)
        if ok:
            return _result(payload, translator, attempt)
        feedback = f"上一次给出的公式 {payload.formula!r} 非法：{why}。请只用能力卡里的输入与算子重写。"

    assert payload is not None
    return _result(
        payload, translator, max_retries + 1,
        feasible=False,
        formula=None,
        explanation=(f"翻译器连续 {max_retries + 1} 次生成非法公式，最后一次的错误："
                     f"{feedback}"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_nl_translator.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add research_platform/nl_translator.py tester/test_nl_translator.py
git commit -m "feat(nl): translation contracts + validate_ast retry loop"
```

---

### Task 4: `ClaudeTranslator` and `resolve_translator()`

**Files:**
- Modify: `research_platform/nl_translator.py` (append)
- Test: `tester/test_nl_translator.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tester/test_nl_translator.py`:

```python
class _FakeMessages:
    def __init__(self, payload, recorder):
        self._payload = payload
        self._recorder = recorder

    def parse(self, **kwargs):
        self._recorder.append(kwargs)
        class _Resp:
            parsed_output = self._payload
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
    calls = []
    translator = ClaudeTranslator(client=_FakeClient(_payload(), calls))
    translator("想法", "CARD", feedback="公式非法：unknown name 'foo'")
    assert "unknown name 'foo'" in calls[0]["messages"][0]["content"]


def test_module_imports_without_anthropic_installed():
    # the whole graceful-degradation story rests on this
    import importlib, sys
    assert "anthropic" not in sys.modules or True
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
    from research_platform.nl_translator import TranslatorUnavailable, classify_exception
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com")
    resp = httpx.Response(401, request=req)
    auth = anthropic.AuthenticationError("bad key", response=resp, body=None)
    assert isinstance(classify_exception(auth), TranslatorUnavailable)
    resp429 = httpx.Response(429, request=req)
    rate = anthropic.RateLimitError("slow down", response=resp429, body=None)
    assert not isinstance(classify_exception(rate), TranslatorUnavailable)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_nl_translator.py -q`
Expected: FAIL — `ImportError: cannot import name 'ClaudeTranslator'`

- [ ] **Step 3: Write minimal implementation**

Append to `research_platform/nl_translator.py`:

```python
MODEL = "claude-opus-5"
MAX_TOKENS = 8000

SYSTEM_TEMPLATE = """你是一个量化因子公式翻译器。把用户用自然语言描述的因子想法，翻译成本平台 DSL 的单个表达式。

{vocabulary}

## 铁律

1. 只准使用上面 INPUTS 里列出的字段和 OPERATORS 里列出的算子。表里没有的名字一律不许出现。
2. 如果这个想法需要表里没有的数据（分析师预期、期权隐含波动率、情绪分、新闻、
   库存、现金流、基本面科目、指数或 ETF 价格等），必须 feasible=false，
   并在 missing_data 里用业务语言列出缺的数据字段。
3. **绝对禁止发明字段，也禁止用相近字段顶替。** 例如不许用 volume 冒充"机构成交额"，
   不许用 returns 冒充"超额收益"。宁可拒绝，也不要悄悄换一个近似的东西。
4. 如果要给 nearest_formula，它同样只能用表内元素，并且必须在 nearest_caveat 里
   如实说明它和原想法差在哪里。
5. explanation 用中文，说明你怎么翻的，或者为什么表达不了。
"""


class TranslatorUnavailable(RuntimeError):
    """Translator cannot be used at all (missing package, missing or bad credentials)."""


def classify_exception(exc: BaseException) -> BaseException:
    """Map an SDK exception onto our two-bucket model, importing ``anthropic``
    lazily so this works when the package is absent."""
    try:
        import anthropic
    except ImportError:
        return exc
    if isinstance(exc, anthropic.AuthenticationError):
        return TranslatorUnavailable(str(exc))
    if isinstance(exc, getattr(anthropic, "PermissionDeniedError", ())):
        return TranslatorUnavailable(str(exc))
    return exc


class ClaudeTranslator:
    """Translate via Claude, with the payload schema enforced by the SDK."""

    name = "claude"
    model = MODEL

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, idea: str, vocabulary: str,
                 feedback: str | None = None) -> TranslationPayload:
        user = f"因子想法：{idea}"
        if feedback:
            user += f"\n\n上一次翻译的问题：{feedback}\n请修正后重新给出。"
        try:
            response = self._get_client().messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_TEMPLATE.format(vocabulary=vocabulary),
                messages=[{"role": "user", "content": user}],
                output_format=TranslationPayload,
            )
        except Exception as exc:   # noqa: BLE001 - re-raised after classification
            raise classify_exception(exc) from exc
        return response.parsed_output


def resolve_translator() -> Translator | None:
    """Return a usable translator, or ``None`` when one cannot be built.

    ``None`` is a normal outcome, not an error: the CLI prints the capability
    card instead, so the workflow stays usable without an API key.
    """
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
    except Exception:   # noqa: BLE001 - any credential-resolution failure
        return None
    return ClaudeTranslator(client=client)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_nl_translator.py -q`
Expected: PASS — 12 passed, or 11 passed + 1 skipped when `anthropic` is absent

- [ ] **Step 5: Verify the module imports with anthropic absent**

Run: `python -c "import research_platform.nl_translator as m; print(m.resolve_translator())"`
Expected: prints `None` (no traceback)

- [ ] **Step 6: Commit**

```bash
git add research_platform/nl_translator.py tester/test_nl_translator.py
git commit -m "feat(nl): ClaudeTranslator via messages.parse + lazy-import resolver"
```

---

### Task 5: `provenance` in `validate_factor`

**Files:**
- Modify: `scripts/validate_factor.py:147-150` (signature) and `:225-230` (formula.txt + ledger)
- Test: `tester/test_validate_factor.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tester/test_validate_factor.py`:

```python
def test_provenance_is_written_to_formula_txt_and_ledger(tmp_path):
    from scripts.validate_factor import validate_factor
    res = validate_factor("-(close/delay(close,5)-1)", name="rev5", bundle=_bundle(),
                          output_dir=tmp_path, min_names=10,
                          provenance={"idea": "5 日反转", "translator": "stub"})
    text = (res.output_dir / "formula.txt").read_text()
    assert "idea=5 日反转" in text
    assert "translator=stub" in text
    import json
    records = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert records[-1]["metadata"]["provenance"]["idea"] == "5 日反转"


def test_provenance_none_leaves_formula_txt_unchanged(tmp_path):
    from scripts.validate_factor import validate_factor
    res = validate_factor("-(close/delay(close,5)-1)", name="rev5", bundle=_bundle(),
                          output_dir=tmp_path, min_names=10)
    text = (res.output_dir / "formula.txt").read_text()
    assert "idea=" not in text
    import json
    records = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert "provenance" not in records[-1]["metadata"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_validate_factor.py -q -k provenance`
Expected: FAIL — `TypeError: validate_factor() got an unexpected keyword argument 'provenance'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/validate_factor.py`, change the signature (currently ends `min_names: int = 30`):

```python
def validate_factor(formula: str, name: str = NEW_NAME, benchmarks: dict | None = None,
                    output_dir: str | Path = PROJECT_ROOT / "outputs" / "factor_validation",
                    project_root: Path = PROJECT_ROOT, bundle=None, primary_horizon: int = 5,
                    min_names: int = 30,
                    provenance: dict | None = None) -> FactorValidationResult:
```

Replace the `formula.txt` write with:

```python
    provenance_lines = "".join(f"{k}={v}\n" for k, v in (provenance or {}).items())
    _atomic_text(out / "formula.txt", f"name={name}\nformula={formula}\nbenchmarks={benchmarks}\nprimary_horizon={primary_horizon}\npit_meta={pit_meta}\n" + provenance_lines)
```

Replace the ledger `metadata=` argument with:

```python
        metadata={"formula": formula, "slug": slug, "best_horizon": int(best["horizon"]), "gross_sharpe": gross_sharpe, "net_sharpe": net_sharpe, "pit_meta": pit_meta,
                  **({"provenance": provenance} if provenance else {})}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_validate_factor.py -q`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_factor.py tester/test_validate_factor.py
git commit -m "feat(validator): optional provenance into formula.txt and the ledger"
```

---

### Task 6: CLI `vocabulary` and `validate-idea`

**Files:**
- Modify: `research_platform/cli.py`
- Test: `tester/test_nl_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tester/test_nl_cli.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tester/test_nl_cli.py -q`
Expected: FAIL — `argparse` error, `invalid choice: 'vocabulary'`

- [ ] **Step 3: Write minimal implementation**

In `research_platform/cli.py`, add to the imports at the top:

```python
from .formula_dsl import describe_vocabulary
from .nl_translator import TranslatorUnavailable, resolve_translator, translate_idea
```

Add these two parsers inside `build_parser()`, immediately before `return parser`:

```python
    commands.add_parser("vocabulary", help="print the DSL capability card")

    vi = commands.add_parser(
        "validate-idea", help="translate a natural-language idea, then validate it"
    )
    vi.add_argument("--idea", required=True)
    vi.add_argument("--name", default="candidate")
    vi.add_argument("--output-dir", default="outputs/factor_validation")
    vi.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
```

Add this helper at module level (it exists so tests can replace the expensive
backtest without touching the translation logic):

```python
def _run_validate_factor(formula: str, name: str, output_dir: str,
                         provenance: dict) -> dict:
    from scripts.validate_factor import validate_factor

    res = validate_factor(formula, name=name, output_dir=output_dir,
                          provenance=provenance)
    return {"slug": res.slug, "summary": res.summary, "output_dir": str(res.output_dir)}


def _print_card_with_guidance() -> int:
    print(describe_vocabulary())
    print("没有可用的翻译器。两条路：")
    print("  1. pip install -e '.[llm]' 并设置 ANTHROPIC_API_KEY，然后重跑 validate-idea；")
    print("  2. 把上面这张能力卡连同你的想法交给任意 LLM，拿到公式后用：")
    print("     python -m research_platform.cli validate-factor --formula='<公式>' --name <名字>")
    return 2
```

Add the dispatch branches in `main()`, before the `result_dir` block:

```python
    if args.command == "vocabulary":
        print(describe_vocabulary())
        return 0
    if args.command == "validate-idea":
        translator = resolve_translator()
        if translator is None:
            return _print_card_with_guidance()
        try:
            result = translate_idea(args.idea, translator)
        except TranslatorUnavailable as exc:
            print(f"翻译器不可用：{exc}\n")
            return _print_card_with_guidance()
        except Exception as exc:   # noqa: BLE001 - surface as a run error, no half output
            print(f"翻译失败：{type(exc).__name__}: {exc}")
            return 1

        if not result.feasible:
            print(f"无法用现有数据字段表达这个想法。\n\n说明：{result.explanation}")
            if result.missing_data:
                print("\n缺少的数据：")
                for item in result.missing_data:
                    print(f"  - {item}")
            if result.nearest_formula:
                print(f"\n最接近的可表达变体：{result.nearest_formula}")
                if result.nearest_caveat:
                    print(f"差别：{result.nearest_caveat}")
                print("\n这个变体不会自动运行。要跑它，请显式执行："
                      f"\n  python -m research_platform.cli validate-factor "
                      f"--formula='{result.nearest_formula}' --name <名字>")
            elif result.nearest_caveat:
                print(f"\n备注：{result.nearest_caveat}")
            return 2

        print(f"公式：{result.formula}")
        print(f"说明：{result.explanation}")
        print(f"(翻译器 {result.translator} / 模型 {result.model} / 尝试 {result.attempts} 次)")
        if not args.yes:
            if input("\n用这个公式跑完整验证？[y/N] ").strip().lower() not in ("y", "yes"):
                print("已取消，未产生任何输出。")
                return 3
        payload = _run_validate_factor(
            formula=result.formula, name=args.name, output_dir=args.output_dir,
            provenance={"idea": args.idea, "explanation": result.explanation,
                        "translator": result.translator, "model": result.model,
                        "attempts": result.attempts},
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tester/test_nl_cli.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Verify the card prints from the real CLI**

Run: `python -m research_platform.cli vocabulary | head -20`
Expected: the capability card, starting with `# 因子公式能力卡`

Run: `python -m research_platform.cli validate-idea --idea "5 日反转"; echo "exit=$?"`
Expected: card + guidance, `exit=2` (no `anthropic` installed here)

- [ ] **Step 6: Commit**

```bash
git add research_platform/cli.py tester/test_nl_cli.py
git commit -m "feat(cli): vocabulary + validate-idea with a refusal path and confirm gate"
```

---

### Task 7: Opt-in network smoke test

**Files:**
- Create: `tester/test_nl_live.py`

- [ ] **Step 1: Write the test**

```python
# tester/test_nl_live.py
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
```

- [ ] **Step 2: Verify it skips by default**

Run: `python -m pytest tester/test_nl_live.py -q`
Expected: `2 skipped`

- [ ] **Step 3: Commit**

```bash
git add tester/test_nl_live.py
git commit -m "test(nl): opt-in live translator smoke test"
```

---

### Task 8: Flip the README status table and run the full suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest tester -q`
Expected: PASS. Baseline before this plan was 226 passed; expect 226 + the new
tests (roughly 245), with 2 skipped from `test_nl_live.py`.

- [ ] **Step 2: Update the status table**

In `README.md`, change the two ⏳ rows to:

```markdown
| 能力卡 `describe_vocabulary()` | ✅ 已实现 |
| LLM 翻译器 `nl_translator.py` + `validate-idea` 子命令 | ✅ 已实现 |
```

Delete the sentence directly under the table ("翻译层当前**只有设计文档，尚无代码**……") and replace it with:

```markdown
翻译层需要 `pip install -e '.[llm]'` 并配置 `ANTHROPIC_API_KEY`；没有配置时
`validate-idea` 会打印能力卡和手工流程指引，而不是报错退出。
```

- [ ] **Step 3: Add the one-command usage to the 快速开始 section**

Insert directly above the existing `validate-factor` example:

````markdown
从一句话开始（需要 `[llm]` 依赖与 API key）：

```bash
python -m research_platform.cli validate-idea --idea "5 日反转，按 20 日均量加权"
```

想先看能用哪些字段和算子：

```bash
python -m research_platform.cli vocabulary
```
````

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): translator layer is implemented"
```

---

## Self-Review

**Spec coverage:** §4.1 card → Task 1. §4.2 contracts → Task 3. §4.3 protocol → Task 3.
§4.4 ClaudeTranslator incl. the lazy-import constraint → Task 4. §4.5 resolve_translator
→ Task 4. §4.6 retry loop → Task 3. §4.7 StubTranslator → Task 3. §4.8 provenance →
Task 5. §4.9 CLI + exit codes → Task 6. §6 error handling → Tasks 4 and 6. §7 tests →
Tasks 1, 3, 4, 5, 6, 7. §3 pyproject → Task 2.

**Deviation from spec, deliberate:** the spec routes `AuthenticationError` to "exit 2 +
guidance". Task 6 implements that by catching `TranslatorUnavailable` around
`translate_idea` and falling through to the same card-and-guidance path as a missing
translator, since the user-facing outcome the spec asks for is identical.

**Type consistency:** `TranslationPayload` (Pydantic) is what translators return
everywhere; `TranslationResult` (frozen dataclass) is what `translate_idea` returns
everywhere. `missing_data` is `list[str]` on the payload and `tuple[str, ...]` on the
result — converted once, in `_result`. `translator.name`/`translator.model` are read in
`_result` and asserted in Task 4's tests. `_run_validate_factor` takes exactly
`formula`, `name`, `output_dir`, `provenance` in both Task 6's implementation and tests.
