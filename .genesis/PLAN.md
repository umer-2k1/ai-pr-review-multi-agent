# PLAN — ai-pr-review-multi-agent

The machine-parseable implementation plan. Mirrors the milestone table in `DONE.html` (DONE.html is the
human/visual view; this is the one loops read). Sliced so each milestone ships in one L1 BUILD pass.

> Slicing rule: a milestone must have (a) a single clear outcome, (b) an exact **demo command** that
> proves it, and (c) a freeze boundary of files it may touch. If you can't write the demo command,
> the milestone is too vague — split it.

**Scope decision (2026-08-02):** the spine encodes a *prototype subset* of
`Designing an AI Pull-Request Review Agent.pdf`, not the full 20-phase / 23-module build.
Kept: four specialists, the Finding contract, aggregator + overall_confidence, HITL gate,
events spine. Deferred: FastAPI webhook, Redis/ARQ queue, pgvectorscale RAG, Next.js
dashboard, Tiger continuous aggregates. Every deferred piece is additive — see
`decisions/decisions-manifest.md`.

---

## Brainstorm (G0.5 — fill before slicing milestones)

> Three fundamentally different approaches to the cognitive job. Pick one. Record the rationale.
> This is the cheapest design decision — you haven't written a line of code yet.

### Approach A — One Big Reviewer
A single LLM call receives the whole diff plus a long prompt covering security, quality, tests
and docs, and returns one prose review. No orchestration, no contracts, no merge step.
- Strengths: fastest to build (an afternoon); cheapest per PR — one call, one context; nothing to
  deduplicate because nothing forks.
- Weaknesses: the four concerns compete for attention inside one context window and the model
  reliably shortchanges whichever comes last in the prompt; prose output makes the HITL gate
  un-computable — you cannot threshold on a confidence field that does not exist.

### Approach B — Parallel Specialist Fan-Out
An orchestrator dispatches the diff to four independent specialists (security, quality, tests,
docs) concurrently. Each returns `Finding[]` against a shared schema. An aggregator dedupes by
file+line, computes `overall_confidence`, and applies the HITL gate.
- Strengths: each specialist owns one concern with its full context budget, which is exactly the
  study's §3.4 argument; structured output makes the downstream aggregator deterministic — it
  merges data, not prose, so the HITL gate becomes a computed threshold rather than a vibe.
- Weaknesses: 4× the LLM cost and 4× the surface area for schema-validation failures; needs
  genuine dedup semantics for the case where two agents flag the same line for different reasons
  (listed as a known unknown — unvalidated).

### Approach C — Deterministic Linters + LLM Triage
Run existing tools (bandit, ruff, mypy, coverage) over the diff. An LLM never finds issues; it
only explains, ranks, and filters the tools' output into human-readable review comments.
- Strengths: near-zero hallucination — every finding traces to a real tool hit, so INV-3 is
  satisfied by construction; dramatically cheaper and fully reproducible.
- Weaknesses: can only find what a linter can already find, which excludes the entire class of
  judgment the study exists to automate ("this abstraction leaks", "this test asserts nothing");
  the study's §0.1 Move 3 warns the inverse mistake, but here the loss is real — it is a
  different, smaller product.

### Chosen: **Approach B — Parallel Specialist Fan-Out** — it is the only one of the three whose
output shape (`Finding` with `confidence` + `rationale`) makes the human-approval gate a computed
decision rather than a judgment call, and that gate is the whole point of the chosen autonomy
level. Approach C's grounding discipline is adopted *inside* B as INV-3 rather than instead of B;
Approach A is rejected outright because prose output cannot be thresholded, deduped, or evaluated.

---

## Milestones

### M1 — Finding contract + one specialist, fully offline
- **Outcome:** A `Finding` pydantic model and a diff parser exist; the security specialist runs
  against a fixture diff and returns schema-valid, grounded findings. No network in the test path.
- **Phase (swe-master):** 1 System Architecture → 5 LLM & Reasoning (study phases 1, 5, 8)
- **Files / freeze boundary:** `src/prreview/contracts.py`, `src/prreview/diff.py`,
  `src/prreview/llm.py`, `src/prreview/agents/{base,security}.py`, `src/prreview/cli.py`,
  `tests/**`, `fixtures/**`
- **Demo command:** `pr-review run --diff fixtures/sample.diff --agent security --offline`
- **Success criteria:** exits 0; prints ≥1 Finding as JSON; every finding's file_path + line range
  verified present in the parsed diff (INV-3); `pytest -q` green; `mypy src/` clean
- **Loops:** L1, L4
- **Skills:** canon + tdd + modular-architecture
- **Token budget:** 50000

### M2 — Four specialists in parallel + aggregator
- **Outcome:** All four specialists run concurrently; the aggregator merges four `Finding[]`,
  dedupes by (file_path, line_start) keeping highest confidence and noting agreement.
- **Phase:** 4 Workflow Orchestration + 8 Multi-Agent Systems
- **Files:** `src/prreview/agents/{quality,tests,docs}.py`, `src/prreview/aggregator.py`,
  `src/prreview/orchestrator.py`, `tests/**`
- **Demo command:** `pr-review run --diff fixtures/sample.diff --offline --json | jq '.findings | length, (.agents_run | length)'`
- **Success criteria:** `agents_run == 4`; a deliberately duplicated fixture finding appears once
  with `agreement: 2`; one specialist forced to raise still yields a partial review flagged
  `incomplete: true` rather than a crash or a silent clean result
- **Loops:** L1, L3 (research: structured-output reliability spike), L4
- **Skills:** canon + tdd + llmops-ai-agents
- **Token budget:** 50000

### M3 — overall_confidence + the HITL gate
- **Outcome:** The aggregator computes `overall_confidence` and routes: high confidence and no
  CRITICAL → emit approved-draft; otherwise → hold for human. Nothing posts unattended (INV-4).
- **Phase:** 19 Human-in-the-Loop (study §0.3 level 2)
- **Files:** `src/prreview/aggregator.py`, `src/prreview/hitl.py`, `tests/**`
- **Demo command:** `pr-review run --diff fixtures/critical.diff --offline --json | jq '.hitl_verdict, .overall_confidence'`
- **Success criteria:** a fixture containing a CRITICAL finding returns `hitl_verdict:"HOLD"`
  regardless of confidence; grep for GitHub write calls returns empty (INV-4 check passes)
- **Loops:** L1, L4
- **Skills:** canon + tdd + security-engineering
- **Token budget:** 50000

### M4 — The events spine
- **Outcome:** Every action (fan-out start, each specialist call, aggregation, gate decision)
  appends one time-ordered row. A single query reconstructs a full review trace with token cost.
- **Phase:** 10 Observability & Tracing
- **Files:** `src/prreview/events.py`, `src/prreview/orchestrator.py`, `tests/**`
- **Demo command:** `pr-review run --diff fixtures/sample.diff --offline && pr-review trace --last`
- **Success criteria:** ≥7 events for a 4-specialist run, monotonically ordered; each carries
  `review_id`, `event_type`, `tokens`, `duration_ms`; total cost printed
- **Loops:** L1, L4
- **Skills:** canon + tdd + production-readiness
- **Token budget:** 50000

### M5 — Package as a GitHub Action
- **Outcome:** The reviewer runs in CI against a real PR diff with a read-only token and publishes
  the draft as a job artifact/summary. Still no unattended posting.
- **Phase:** 18 CI/CD for AI
- **Files:** `action.yml`, `.github/workflows/review.yml`, `src/prreview/github.py`, `tests/**`
- **Demo command:** `act pull_request -W .github/workflows/review.yml` (or: open a test PR and
  confirm the job summary renders the draft review)
- **Success criteria:** job completes inside its timeout; draft appears in the run summary; token
  has no write scope; every outbound call has an explicit timeout (INV-2 check passes)
- **Loops:** L1, L4
- **Skills:** canon + tdd + production-readiness
- **Token budget:** 50000

<!-- duplicate the block per milestone -->

---

## Progress (loops append here on milestone completion — newest last)

- _(none yet — first loop fills this)_
