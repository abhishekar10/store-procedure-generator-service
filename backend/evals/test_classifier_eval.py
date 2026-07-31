"""Eval, not a unit test: calls the live Gemini classifier for every case
(real API cost/latency, ~2-3s each). Run explicitly with `pytest evals/`,
not part of the default `pytest` run.

The golden set includes the exact follow-up phrasing that was found, during
review, to be misclassified when the classifier had no conversation context
(`classify_request` used to take only the latest message). Those cases stay
in this eval specifically as a regression guard -- if context handling ever
breaks again, this is what should catch it before a user does.
"""

import pytest

from app.rag.generator import classify_request

PAYROLL_PRIOR_MESSAGES = [
    {"role": "user", "content": "Process payroll for a given pay period"},
    {"role": "assistant", "kind": "generation", "full_script": "-- SECTION: PRE_VALIDATION\n..."},
]

CASES = [
    pytest.param("Process payroll for a given pay period", None, True, id="first_turn_on_topic_payroll"),
    pytest.param("Onboard a new employee and record their starting salary", None, True, id="first_turn_on_topic_onboarding"),
    pytest.param("Write a python program to add two numbers", None, False, id="first_turn_off_topic_python"),
    pytest.param("Tell me a joke", None, False, id="first_turn_off_topic_chitchat"),
    pytest.param("What is the weather today", None, False, id="first_turn_off_topic_weather"),
    # regression cases: previously misclassified without conversation context
    pytest.param("Make it handle nulls too", PAYROLL_PRIOR_MESSAGES, True, id="followup_nulls_regression"),
    pytest.param("What about weekends and holidays", PAYROLL_PRIOR_MESSAGES, True, id="followup_holidays_regression"),
    pytest.param("Also add validation for negative values", PAYROLL_PRIOR_MESSAGES, True, id="followup_validation"),
    pytest.param("Can you make the rollback more robust", PAYROLL_PRIOR_MESSAGES, True, id="followup_rollback"),
    # a genuine topic pivot mid-conversation should still be rejected
    pytest.param("actually forget that, write me a poem instead", PAYROLL_PRIOR_MESSAGES, False, id="followup_genuine_pivot"),
]


@pytest.mark.eval
@pytest.mark.parametrize("prompt, prior_messages, expected_valid", CASES)
def test_classifier_matches_expected_label(prompt, prior_messages, expected_valid):
    is_valid, reason = classify_request(prompt, prior_messages)

    assert is_valid == expected_valid, f"prompt={prompt!r} expected valid={expected_valid}, got {is_valid} ({reason!r})"
