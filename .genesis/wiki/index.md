# Wiki Index — ai-pr-review-multi-agent

The project knowledge base. Same schema as the agentic-swe-kit wiki: concept pages in `concepts/`,
each with frontmatter and ≥2 `[[wikilinks]]`. The L3 RESEARCH loop writes here; G0 reads here first.

> **Read this file before any milestone (G0 step 1).** Pick candidate pages by name-matching the
> milestone's nouns, then drill in. The wiki is what prevents rebuilding work that already exists.

## Entities (the things this system has)
<!-- Stubs — L3 RESEARCH fills these as they are learned. Named now so milestone nouns match. -->
- `Finding` — one structured observation: agent_type, severity, category, summary, file_path,
  line_start/line_end, suggestion, confidence, rationale. The contract every specialist returns.
- `Review` — the aggregated output: deduped Finding[], overall_confidence, HITL verdict.
- `Specialist` — one of four grounded reasoners: security, quality, tests, docs.
- `Aggregator` — merges the four lanes, dedupes by file+line, computes overall_confidence.
- `EventsSpine` — append-only, time-ordered row per action; feeds trace + audit + cost.
- `Diff` — parsed unified diff: files, hunks, valid line ranges. The grounding truth for INV-3.

## Concepts (how it works)
<!-- - [[concepts/<Concept>]] — one-line summary -->
_(none yet — L3 RESEARCH writes here; the three known unknowns in
`decisions/decisions-manifest.md` are the first three spikes)_

## Sources (research distilled by L3)
- `Designing an AI Pull-Request Review Agent.pdf` — Antern architecture study, pub. 2026-07-22,
  45pp, in repo root. The derivation this project follows. Key sections:
  §0.2 failure-mode catalog · §0.3 HITL spectrum · §3.4 specialists & aggregator ·
  §4.1 20-phase roadmap · §4.2 the 23-module map. | filed 2026-08-02

## Seeded from agentic-swe-kit
Relevant global concept pages for this project's phases (pointers only — read on demand).
Root: `$AGENTIC_SWE_WIKI_ROOT` = `~/.agentic-swe-kit/wiki`. All paths below verified to exist.

**M1–M2 · the fan-out (study §3.4, phases 4 & 8)**
- `llmops-ai-agents/concepts/Parallel-and-Fan-Out-Agents.md` — when parallel specialists beat one
  prompt; read before building the orchestrator
- `llmops-ai-agents/concepts/Orchestrator-Worker-Architecture.md` — the shape M2 implements
- `llmops-ai-agents/concepts/Multi-Agent-Orchestration.md` — agent contracts and merge strategy
- `clean-architecture/concepts/Boundary-Lines.md` — where to cut modules; the source of INV-1
- `clean-architecture/concepts/Clean-Architecture-Pattern.md` — the inward-only dependency rule

**M3 · confidence + the HITL gate (study §0.3, phase 19)**
- `llmops-ai-agents/concepts/Agentic-Design-Patterns.md` — reflection/critique patterns behind
  a defensible confidence score

**M4 · the events spine (study §3.6, phase 10)**
- `llmops-ai-agents/concepts/Observability-and-Cost-Control.md` — what to record per action, and
  how the same rows price the system

**M5 + reliability (study §0.2, phase 12) — source of INV-2**
- `release-it/concepts/Timeouts.md` — read before writing any outbound call
- `release-it/concepts/Circuit-Breaker.md` — when retry stops helping
- `release-it/concepts/Fail-Fast.md` — degrade to slower-but-correct, never fast-but-wrong
- `llmops-ai-agents/concepts/Production-Hardening.md` — the graduation checklist

**Deferred but referenced (out of scope for M1–M5, kept so the path is documented)**
- `llmops-ai-agents/concepts/RAG-Architecture.md` — needed if the grounding spike says diff
  context is insufficient (study §3.5)
- `llmops-ai-agents/concepts/Evaluation-Frameworks.md` — golden dataset + LLM-as-judge (phase 9)
- `security-engineering/concepts/Access-Control.md` — RBAC, if this ever grows a service surface

> Note: the 7 swe-foundations/mlops SKILL.md files installed only to `~/.hermes/skills`, which
> Claude Code does not read. The wiki content above is the part that matters and is readable
> directly at the paths listed.
