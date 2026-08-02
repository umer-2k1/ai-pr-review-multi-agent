# pr-review

A multi-agent pull-request reviewer. Four specialist reasoners fan out over a
diff, an aggregator merges and deduplicates their findings, and a human approves
the draft before anything is posted.

Built from `Designing an AI Pull-Request Review Agent.pdf` (Antern architecture
study) as a **prototype subset** — see [Scope](#scope) for what is deliberately
absent.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# no API key needed — the offline reviewer is deterministic
.venv/bin/pr-review run --diff fixtures/sample.diff --offline
```

```
review 164cdd9b3ac6
agents run: 4   findings: 5
confidence: 0.74   gate: HOLD — a CRITICAL finding is present

  [CRITICAL] app/db.py:10  (sql-injection, security)
             why: String-interpolated SQL reaches the driver; a hostile value changes the query shape.
             fix: Use parameterised queries: cursor.execute(sql, (value,)).
             confidence: 0.92
  ...
  [MINOR   ] app/client.py:12  (unresolved-marker, tests)  [2 lanes agree]
```

## How it works

```
diff ──► parse_diff ──► orchestrator ──┬──► security ──┐
                       (4 threads,     ├──► quality   │ each returns Finding[]
                        one deadline)  ├──► tests     │ with confidence + rationale
                                       └──► docs   ───┘
                                              │
                                     aggregator: dedupe by (file, line, category),
                                     count agreeing lanes, worst severity wins
                                              │
                                     HITL gate: HOLD or DRAFT_READY
                                              │
                                     draft markdown ──► a human posts it
```

Every action appends one row to an append-only events spine, so a single stream
answers three questions: what happened, who did what, and what it cost.

## Commands

| Command | What it does |
|---|---|
| `pr-review run --diff FILE` | Review a diff file with all four specialists |
| `pr-review run --pr N --repo O/R` | Fetch a PR diff (read-only) and review it |
| `pr-review run ... --agent security` | Run one lane, for debugging a concern |
| `pr-review run ... --json` | Machine-readable output |
| `pr-review run ... --draft` | The markdown a human reads before posting |
| `pr-review run ... --offline` | Deterministic, no network, no API key |
| `pr-review trace --last` | Reconstruct the most recent review from the spine |

**Exit codes:** `0` ran · `1` a specialist lane failed (review is incomplete) ·
`2` bad input. These are kept distinct on purpose — "we could not look" and "we
looked and something broke" are different problems.

## The four invariants

Enforced by `.genesis/context-graph.json`, checked in CI:

1. **Dependency direction is inward only.** `contracts.py` and `diff.py` import
   nothing from the package, so the specialists stay runnable with no network.
2. **Every outbound call has an explicit timeout.** All network I/O goes through
   `llm.py` or `github.py`; a CI job that hangs forever is the likeliest
   real-world failure and is invisible until it happens.
3. **No ungrounded finding.** Every finding's file and line range must lie inside
   a hunk of the parsed diff. Ones that do not are dropped *and counted* — a lane
   whose every finding was hallucinated must not look like a clean lane.
4. **No unattended write.** No code path posts to GitHub, and the workflow grants
   `pull-requests: read` only. The human-approval gate is enforced at the
   credential layer as well as in code, because a token that cannot write is a
   guarantee a bug cannot undo.

## GitHub Action

```yaml
permissions:
  contents: read
  pull-requests: read        # deliberately never write

jobs:
  review:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: ./
        with:
          pr: ${{ github.event.pull_request.number }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The draft lands in the job summary and as an artifact. Nothing is posted to the
pull request.

## Scope

**Built:** the Finding contract, the diff parser, four specialists, the
aggregator, the confidence roll-up, the HITL gate, the events spine, the Action.

**Deliberately deferred** (documented graduation path, not rejected): FastAPI
webhook ingress, Redis/ARQ queue, pgvectorscale RAG, Tiger Cloud hypertables and
continuous aggregates, the Next.js dashboard. See
`.genesis/decisions/decisions-manifest.md`.

## Development

```bash
.venv/bin/python -m pytest -q      # 206 tests
.venv/bin/python -m mypy src/      # strict
```

Every milestone was verified by an independent model before being marked done.
Across five milestones that process rejected the work **nine times** and found
**sixteen** blocking defects — several of which passed a fully green test suite.
The per-milestone write-ups are in `.genesis/explanations/`.
