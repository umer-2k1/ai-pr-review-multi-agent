# decisions-manifest — ai-pr-review-multi-agent
Generated: 2026-08-02 via KICKOFF-INTERVIEW.md (G0)

Source of record: `Designing an AI Pull-Request Review Agent.pdf` (Antern architecture
study, published 2026-07-22, 45pp) — referenced below as **the study**.

## G0 diagnostic (the 5 questions)
| Question | Answer | Evidence |
|---|---|---|
| Scope | **new** (greenfield) | repo had 1 commit, no source files |
| AI components? | **yes** — 4 LLM specialists + aggregator | study §3.4 |
| Distributed? | **no** — GitHub Action / CI job, ephemeral | user decision, diverges from study §3.1 |
| Trust boundary | internal/private repo; diff still untrusted LLM input | user decision |
| Current phase | Phase 0 → 1 (Cognitive Design → Architecture) | study §4.1 |

## Trade-off ranking
1. maintainability — this is a learning build; the code is the artifact
2. reliability — a confidently-wrong review destroys trust in the tool
3. cost — LLM calls fan out 4× per PR, so cost is structural, not incidental
4. speed — a CI job may take minutes; nobody is waiting on a spinner

## Scale
Launch: single repo, a handful of PRs/day, run by hand or per-PR in CI
12 months: not projected — prototype. If it graduates, the study's queue+service
design (§3.1) is the documented path.

## Project type
prototype / learning project

## Performance constraints (non-negotiable)
- A review must finish inside the CI job's wall-clock budget or fail loudly — never hang
- Every outbound call (LLM, GitHub API) has an explicit timeout; no unbounded waits

## UX / brand constraints
CLI-first. Output is a markdown draft review a human reads before posting.
No dashboard in this scope (study's Next.js frontend deferred).

## Failure behaviour
Degrade to slower-but-correct, never fast-but-wrong (study §0.1 Move 5).
A specialist that errors or times out yields a **partial** review explicitly flagged
incomplete. The aggregator never silently drops a lane.

## Integration points
- GitHub API (read PR diff; post draft as comment only after human approval)
- An LLM provider (Anthropic) for the four specialists
- Local filesystem for fixtures/golden diffs
Deferred: Redis/ARQ, Postgres/Tiger, pgvectorscale, FastAPI webhook

## Auth requirements
A GitHub token scoped read-only for diffs. Write scope is NOT granted in this scope —
the human posts. This enforces the HITL gate at the credential level, not just in code.

## Compliance constraints
None formally. Practical rule: no secrets or diff content in logs at INFO or above.

## Primary failure mode (the honest one)
The "almost-right" problem (study §0.2): reviews that are 90% correct and 10% subtly
wrong, while the reader drifts into complacency and rubber-stamps them. This is why
`confidence` and `rationale` are required fields on every Finding, not optional ones.

Secondary: scope creep toward the study's full 20-phase production system, which would
stall the prototype before M1 ever demos.

## Quality bar ("embarrassed to ship if...")
- A finding cites a file/line that does not exist in the diff (ungrounded hallucination)
- The tool posts to a PR without a human clicking approve
- A crashed specialist is reported as a clean review

## Known unknowns → research spikes needed
- Structured-output reliability: how often do the specialists return schema-valid
  Findings under Anthropic tool-use? → L3 spike before M2
- Grounding without RAG: is diff + touched-file context enough at M1, or does the
  prototype need retrieval sooner than planned? → L3 spike before M3
- Dedup semantics: two agents flag the same line for different reasons — merge or keep
  both? The study says keep highest-confidence and note agreement (§3.4); unvalidated.

## Assumptions never stated aloud (agent-inferred)
- The four specialist concerns from the study (security, quality, tests, docs) are
  accepted as-is rather than re-derived — they are load-bearing and carried forward.
- "Prototype" licenses deferring infra, NOT deferring the Finding contract, the
  confidence field, or the events spine — those are the study's actual insight.
- Deferred pieces are additive: nothing in M1–M5 should have to be un-built to later
  adopt the queue/service architecture.
- The study's Tiger Cloud advocacy (§2.4) is a vendor argument, not a requirement of
  the reasoning; one append-only table satisfies the events-spine idea at this scale.
