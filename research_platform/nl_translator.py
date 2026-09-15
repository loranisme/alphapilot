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

    An illegal formula is not a failure to report -- it is fed back to the
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
        explanation=(f"翻译器连续 {max_retries + 1} 次生成非法公式，最后一次的错误：{feedback}"),
    )


MODEL = "claude-opus-5"
MAX_TOKENS = 8000

SYSTEM_TEMPLATE = """你是一个量化因子公式翻译器。把用户用自然语言描述的因子想法，翻译成本平台 DSL 的单个表达式。

{vocabulary}

## 铁律

1. 只准使用上面 INPUTS 里列出的字段和 OPERATORS 里列出的算子。表里没有的名字一律不许出现。
2. 如果这个想法需要表里没有的数据（分析师预期、期权隐含波动率、情绪分、新闻、
   库存、现金流、基本面科目、指数或 ETF 价格等），必须 feasible=false，
   并在 missing_data 里用业务语言列出缺的数据字段。
3. **绝对禁止发明字段，也禁止用相近字段顶替。** 例如不许用 volume 冒充“机构成交额”，
   不许用 returns 冒充“超额收益”。宁可拒绝，也不要悄悄换一个近似的东西。
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
    unavailable = tuple(
        t for t in (getattr(anthropic, "AuthenticationError", None),
                    getattr(anthropic, "PermissionDeniedError", None))
        if isinstance(t, type)
    )
    if unavailable and isinstance(exc, unavailable):
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
