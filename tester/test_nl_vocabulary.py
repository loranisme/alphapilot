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


def test_card_examples_are_valid_formulas():
    # A card that ships an invalid example teaches the model to emit invalid
    # formulas. Every example must survive the same judge its output faces.
    import ast
    from research_platform.formula_dsl import _EXAMPLES, validate_ast
    assert len(_EXAMPLES) >= 4
    for label, formula in _EXAMPLES:
        validate_ast(ast.parse(formula, mode="eval"))
        assert formula in describe_vocabulary(), f"example {label} not rendered"
