# CURRENT
- active_loop: NONE
- target: M1 — Finding contract + one specialist, fully offline
- iteration: 0
- last_gate: G2 PASS (computed: 0 cycles, 0 inward-only violations, 4 invariants)
- last_action: genesis ritual G0-G6 complete; spine filled, no code written yet
- next_action: run G0 EXISTENCE PRE-FLIGHT on M1, then L1 BUILD
- model: claude-sonnet-5
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## Notes for a cold session
- Scope is a PROTOTYPE SUBSET of the study PDF, not the full 20-phase build.
  See PLAN.md header + decisions/decisions-manifest.md before proposing any deferred subsystem.
- Toolchain: Python, `pytest -q`, `mypy src/`. Nothing installed yet — M1 sets up the package.
- EXPLAIN_DIFF is ON: after each L4 APPROVE, run explain-diff-html then quiz-me.
- L4 VERIFY must run as a SEPARATE session on claude-opus-5. The maker never grades itself.
