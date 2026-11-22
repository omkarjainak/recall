"""Tests for the claim analysis that powers contradiction detection."""

from recall.tokens import (
    claim_tokens,
    contradiction_score,
    duplicate_score,
    frame_tokens,
    has_negation,
)


def test_claim_tokens_drop_frame_and_filler():
    assert claim_tokens("Priya lives in Paris.") == {"priya", "paris"}
    assert claim_tokens("Grace reviews each relate release.") == {"grace", "relate", "release"}


def test_frame_tokens_are_the_relations():
    assert "lives" in frame_tokens("Priya lives in Paris.")
    assert "reviews" in frame_tokens("Grace reviews each relate release.")


def test_negation_detection():
    assert has_negation("the API is not public")
    assert has_negation("the key never rotates")
    assert not has_negation("the API is public")


def test_same_frame_different_claim_is_contradiction():
    # Paris vs Berlin: same frame ("lives in"), different claim
    score = contradiction_score("Priya lives in Paris.", "Priya lives in Berlin.", 0.6)
    assert score >= 0.18


def test_negation_flip_is_contradiction():
    score = contradiction_score("The API is public.", "The API is not public.", 0.9)
    assert score >= 0.5


def test_different_frames_are_not_contradictory():
    # "lives in Paris" and "works at Acme" can both be true
    score = contradiction_score("Priya lives in Paris.", "Priya works at Acme.", 0.5)
    assert score < 0.18


def test_unrelated_facts_are_not_contradictory():
    score = contradiction_score(
        "The WAL replays on open.", "Grace reviews each relate release.", 0.1
    )
    assert score < 0.18


def test_same_claim_different_subjects_is_contradiction():
    score = contradiction_score(
        "Grace reviews each relate release.", "Ada reviews each relate release.", 0.6
    )
    assert score >= 0.18


def test_duplicates():
    d = duplicate_score("Priya lives in Berlin.", "Priya lives in Berlin.", 1.0)
    assert d >= 0.95
    d2 = duplicate_score("Priya lives in Berlin.", "Priya lives in Paris.", 0.6)
    assert d2 < 0.5


def test_scores_are_symmetric():
    a, b = "Priya lives in Paris.", "Priya lives in Berlin."
    assert contradiction_score(a, b, 0.6) == contradiction_score(b, a, 0.6)
