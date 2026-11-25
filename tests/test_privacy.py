"""Secret filtering: credentials are redacted before they reach the graph."""

from __future__ import annotations

import pytest

from recall import Memory
from recall.privacy import find_secrets, scrub

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7"
OPENAI_KEY = "sk-proj-Abc123Def456Ghi789Jkl012Mno345"


def test_find_secrets_names_the_kinds():
    assert "aws_access_key" in find_secrets(f"key {AWS_KEY} here")
    assert "github_token" in find_secrets(f"token {GITHUB_TOKEN}")
    assert "openai_key" in find_secrets(f"api {OPENAI_KEY}")
    assert find_secrets("nothing secret here") == []


def test_scrub_replaces_the_secret_not_the_sentence():
    clean, kinds = scrub(f"deploy used {AWS_KEY} to upload")
    assert AWS_KEY not in clean
    assert "deploy used" in clean and "to upload" in clean
    assert "aws_access_key" in kinds
    assert "«redacted:aws_access_key»" in clean


def test_scrub_handles_password_assignments():
    clean, kinds = scrub("db password: hunter2secret")
    assert "hunter2secret" not in clean
    assert "assigned_secret" in kinds


def test_scrub_passes_through_clean_text():
    assert scrub("Priya lives in Paris.") == ("Priya lives in Paris.", [])
    assert scrub(None) == (None, [])


def test_remember_scrubs_fact_and_evidence(mem: Memory):
    report = mem.remember(
        f"CI bot logged in with {GITHUB_TOKEN} during the deploy.",
        evidence=f"raw log: auth={GITHUB_TOKEN} status=ok",
        about=["CI"],
        id="f1",
    )
    fact = report["fact"]
    assert GITHUB_TOKEN not in fact["text"]
    assert "«redacted:github_token»" in fact["text"]
    assert report["sensitive"] is True
    assert "github_token" in report["redacted"]

    why = mem.why("f1")
    assert GITHUB_TOKEN not in why["evidence"][0]["text"]


def test_sensitive_flag_hides_facts_from_recall(mem: Memory):
    mem.remember("The staging box uses port 8443.", sensitive=True, id="f1")
    mem.remember("Priya lives in Paris.", about=["Priya"], id="f2")

    shown = {h["id"] for h in mem.recall("staging box port", k=5)}
    hidden = {h["id"] for h in mem.recall("staging box port", k=5, include_sensitive=False)}
    assert "f1" in shown
    assert "f1" not in hidden
    assert mem.get_fact("f1")["sensitive"] is True


def test_explicit_sensitive_argument(mem: Memory):
    report = mem.remember("Our anniversary is on June 12.", sensitive=True, id="f1")
    assert report["sensitive"] is True
    assert report["redacted"] == []
    assert mem.get_fact("f1")["sensitive"] is True


def test_scrubbing_can_be_disabled(tmp_path):
    mem = Memory(str(tmp_path / "p.rl"), scrub_secrets=False)
    try:
        report = mem.remember(f"token {OPENAI_KEY}", id="f1")
        assert OPENAI_KEY in report["fact"]["text"]
        assert report["sensitive"] is False
    finally:
        mem.close()


def test_stats_count_sensitive_facts(mem: Memory):
    mem.remember("Public fact.", id="f1")
    mem.remember("Private fact.", sensitive=True, id="f2")
    stats = mem.stats()
    assert stats["facts"]["sensitive"] == 1
