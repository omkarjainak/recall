"""Recall's evaluation suite: independent labeled data, scored with Recall itself.

The datasets live in ``evals/data`` and are deliberately *not* the demo
story — a hand-written travel-planning memory with gold labels, unrelated
to the relate-release narrative in ``README`` and ``tests/``. Two tasks:

- **contradiction** — given two statements, predict ``contradiction``,
  ``duplicate`` or ``unrelated`` using exactly the scoring path
  ``Memory.remember`` uses (embedding similarity + claim/frame analysis,
  same thresholds).
- **retrieval** — seed a fresh store with labeled facts, ask queries, and
  measure whether the gold facts come back: recall@k, precision@k and MRR.

Everything is deterministic (the offline ``HashingEmbedder``), so numbers
reproduce run to run. Run it with ``recall eval`` or
``python -m recall.evaluation``; each dataset declares thresholds, and
``tests/test_eval.py`` asserts them, so a regression in the claim analysis
or the hybrid ranker fails the build.

Data format (``evals/data/*.json``)::

    {"name": ..., "task": "contradiction" | "retrieval",
     "thresholds": {"accuracy": 0.85}, ...}
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .embed import Embedder, HashingEmbedder, cosine
from .memory import CONFLICT_THRESHOLD, DUPLICATE_THRESHOLD, Memory
from .tokens import contradiction_score, duplicate_score

Row = dict[str, Any]

DEFAULT_DATA_DIR = os.environ.get("RECALL_EVAL_DATA", "evals/data")

LABELS = ("contradiction", "duplicate", "unrelated")


# ------------------------------------------------------------------
# data loading
# ------------------------------------------------------------------
def load_datasets(data_dir: str = DEFAULT_DATA_DIR) -> list[Row]:
    """Every ``*.json`` dataset in ``data_dir``, sorted by name."""
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(
            f"no eval data directory at {data_dir!r} — run from the repo root "
            "or pass --data"
        )
    datasets: list[Row] = []
    for name in sorted(os.listdir(data_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(data_dir, name)) as fh:
            ds = json.load(fh)
        ds.setdefault("name", name[: -len(".json")])
        datasets.append(ds)
    if not datasets:
        raise FileNotFoundError(f"no *.json datasets in {data_dir!r}")
    return datasets


# ------------------------------------------------------------------
# task 1: contradiction classification
# ------------------------------------------------------------------
def classify(
    text_a: str,
    text_b: str,
    *,
    embedder: Embedder | None = None,
    conflict_threshold: float = CONFLICT_THRESHOLD,
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
) -> str:
    """Predict the relationship of a pair — the same decision
    ``Memory.remember`` makes when it sees two facts."""
    emb = embedder or HashingEmbedder()
    sim = cosine(emb.embed(text_a), emb.embed(text_b))
    if duplicate_score(text_a, text_b, sim) >= duplicate_threshold:
        return "duplicate"
    if contradiction_score(text_a, text_b, sim) >= conflict_threshold:
        return "contradiction"
    return "unrelated"


def eval_contradictions(ds: Row, *, embedder: Embedder | None = None) -> Row:
    cases = ds["cases"]
    confusion = {gold: {pred: 0 for pred in LABELS} for gold in LABELS}
    for case in cases:
        pred = classify(case["a"], case["b"], embedder=embedder)
        confusion[case["label"]][pred] += 1

    correct = sum(confusion[g][g] for g in LABELS)
    accuracy = correct / len(cases)

    f1s: list[float] = []
    detail_parts: list[str] = []
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in LABELS if other != label)
        fn = sum(confusion[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1s.append(f1)
        detail_parts.append(
            f"{label}: P={precision:.2f} R={recall:.2f} F={f1:.2f} (n={tp + fn})"
        )
    macro_f1 = sum(f1s) / len(f1s)

    errors = [
        f"{case['a']!r} vs {case['b']!r}: gold={case['label']}, "
        f"got={classify(case['a'], case['b'], embedder=embedder)}"
        for case in cases
        if classify(case["a"], case["b"], embedder=embedder) != case["label"]
    ]
    return {
        "name": ds["name"],
        "task": "contradiction",
        "cases": len(cases),
        "metrics": [
            {"name": "accuracy", "value": round(accuracy, 4)},
            {"name": "macro_f1", "value": round(macro_f1, 4), "detail": "; ".join(detail_parts)},
        ],
        "thresholds": ds.get("thresholds", {}),
        "errors": errors,
    }


# ------------------------------------------------------------------
# task 2: retrieval
# ------------------------------------------------------------------
def eval_retrieval(ds: Row, *, embedder: Embedder | None = None) -> Row:
    k = int(ds.get("k", 3))
    mem = Memory(":memory:", embedder=embedder)
    try:
        for fact in ds["facts"]:
            mem.remember(
                fact["text"],
                id=fact["id"],
                episode_id=f"ev-{fact['id']}",
                slot=fact["slot"],
                about=fact.get("about"),
                at=fact.get("at"),
                valid_from=fact.get("valid_from"),
                importance=fact.get("importance", 0.5),
            )

        recalls: list[float] = []
        precisions: list[float] = []
        rrs: list[float] = []
        misses: list[str] = []
        for q in ds["queries"]:
            gold = set(q["relevant"])
            hits = mem.recall(q["query"], k=k, touch=False)
            top = [h["id"] for h in hits]
            found = [i for i in top if i in gold]
            recalls.append(len(found) / len(gold))
            precisions.append(len(found) / k)
            rank = top.index(found[0]) + 1 if found else 0
            rrs.append(1.0 / rank if rank else 0.0)
            if not found:
                misses.append(f"{q['query']!r} wanted {sorted(gold)}, got {top}")
    finally:
        mem.close()

    n = len(ds["queries"])
    return {
        "name": ds["name"],
        "task": "retrieval",
        "cases": n,
        "metrics": [
            {"name": f"recall@{k}", "value": round(sum(recalls) / n, 4)},
            {"name": f"precision@{k}", "value": round(sum(precisions) / n, 4)},
            {"name": "mrr", "value": round(sum(rrs) / n, 4)},
        ],
        "thresholds": ds.get("thresholds", {}),
        "errors": misses,
    }


# ------------------------------------------------------------------
# runner
# ------------------------------------------------------------------
TASKS = {
    "contradiction": eval_contradictions,
    "retrieval": eval_retrieval,
}


def run_eval(data_dir: str = DEFAULT_DATA_DIR, *, embedder: Embedder | None = None) -> Row:
    """Run every dataset; return the report (metrics, thresholds, verdict)."""
    started = time.perf_counter()
    datasets = load_datasets(data_dir)
    tasks = [TASKS[ds["task"]](ds, embedder=embedder) for ds in datasets]

    checks: list[Row] = []
    for task in tasks:
        for metric in task["metrics"]:
            minimum = task["thresholds"].get(metric["name"])
            if minimum is None:
                continue
            checks.append(
                {
                    "dataset": task["name"],
                    "metric": metric["name"],
                    "value": metric["value"],
                    "min": minimum,
                    "ok": metric["value"] >= minimum,
                }
            )

    return {
        "datasets": len(datasets),
        "cases": sum(t["cases"] for t in tasks),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "tasks": tasks,
        "checks": checks,
        "passed": all(c["ok"] for c in checks) and bool(checks),
    }


def main(argv: list[str] | None = None) -> None:
    import sys

    from .cli import main as cli_main

    args = list(argv) if argv is not None else sys.argv[1:]
    cli_main(["eval", *args])


if __name__ == "__main__":
    main()
