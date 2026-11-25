"""The independent eval suite: different data, same repo, Recall's own scorers."""

from __future__ import annotations

import os

from recall.evaluation import classify, eval_contradictions, eval_retrieval, load_datasets, run_eval

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "evals", "data")


def test_datasets_are_independent_of_the_demo_story():
    """The eval data must not re-use the README/tests narrative."""
    import re

    datasets = load_datasets(DATA_DIR)
    assert {d["task"] for d in datasets} == {"contradiction", "retrieval"}
    blob = " ".join(
        str(c) for d in datasets for c in (d.get("cases") or d.get("facts") or [])
    ).lower()
    for demo_name in ("grace", "noor", "relate", "priya", "paris"):
        # word-boundary match: the label "unrelated" contains "relate"
        assert not re.search(rf"\b{demo_name}\b", blob), (
            f"eval data leaks demo entity {demo_name!r}"
        )


def test_contradiction_task_meets_thresholds():
    ds = [d for d in load_datasets(DATA_DIR) if d["task"] == "contradiction"][0]
    rep = eval_contradictions(ds)
    metrics = {m["name"]: m["value"] for m in rep["metrics"]}
    assert metrics["accuracy"] >= ds["thresholds"]["accuracy"]
    assert metrics["macro_f1"] >= ds["thresholds"]["macro_f1"]
    assert rep["errors"] == []


def test_retrieval_task_meets_thresholds():
    ds = [d for d in load_datasets(DATA_DIR) if d["task"] == "retrieval"][0]
    rep = eval_retrieval(ds)
    metrics = {m["name"]: m["value"] for m in rep["metrics"]}
    assert metrics["recall@3"] >= ds["thresholds"]["recall@3"]
    assert metrics["mrr"] >= ds["thresholds"]["mrr"]
    assert rep["errors"] == []


def test_full_suite_passes():
    report = run_eval(DATA_DIR)
    assert report["passed"], [c for c in report["checks"] if not c["ok"]]
    assert report["datasets"] == 2
    assert report["cases"] >= 30


def test_classify_decides_the_three_relationships():
    assert classify("The museum opens at 9 a.m.", "The museum opens at 5 p.m.") == "contradiction"
    assert classify("Entry costs 20 euros.", "Entry costs 20 euros!") == "duplicate"
    assert classify("The guide speaks German.", "The ticket includes lunch.") == "unrelated"


def test_classify_never_calls_a_negation_flip_a_duplicate():
    assert classify("The API is public.", "The API is not public.") == "contradiction"
