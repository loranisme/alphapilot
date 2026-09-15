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
