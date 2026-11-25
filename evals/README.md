# Recall evals

Independent labeled data that scores **Recall itself** — deliberately not
the demo story (Grace/Noor/Ada/relate) and not the test fixtures
(Priya/Paris). The domain here is a **travel-planning assistant's memory**.

Run from the repo root:

```bash
recall eval              # or: python -m recall.evaluation
```

## Datasets

| File | Task | Cases | What it measures |
|---|---|---|---|
| [`data/contradictions.json`](data/contradictions.json) | contradiction classification | 24 pairs | Given two statements, predict `contradiction` / `duplicate` / `unrelated` — the exact decision `Memory.remember` makes when it meets a pair. Metrics: accuracy, macro-F1. |
| [`data/retrieval.json`](data/retrieval.json) | retrieval | 10 facts, 8 queries | Seed a fresh store, ask a question, check the gold facts come back. Metrics: recall@3, precision@3, MRR. |

Each dataset declares `thresholds`; `tests/test_eval.py` enforces them, so a
regression in the claim analysis or the hybrid ranker fails the build. All
scoring is deterministic (the offline `HashingEmbedder`) — the numbers
reproduce run to run.

## Adding a dataset

Drop a `*.json` file in `data/` with `"task": "contradiction"` or
`"task": "retrieval"` (formats below) and `thresholds`. `recall eval`
picks it up automatically.

```json
// contradiction
{"name": "...", "task": "contradiction",
 "thresholds": {"accuracy": 0.85, "macro_f1": 0.8},
 "cases": [{"a": "...", "b": "...", "label": "contradiction"}]}

// retrieval
{"name": "...", "task": "retrieval", "k": 3,
 "thresholds": {"recall@3": 0.85, "mrr": 0.8},
 "facts": [{"id": "r1", "text": "...", "slot": "unique/slot", "about": ["..."]}],
 "queries": [{"query": "...", "relevant": ["r1"]}]}
```

The runner lives in [`src/recall/evaluation.py`](../src/recall/evaluation.py)
and calls the same public API (`Memory`, `duplicate_score`,
`contradiction_score`) an application would.
