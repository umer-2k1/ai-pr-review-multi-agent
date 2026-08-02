# CURRENT
- active_loop: NONE
- target: M2 — Four specialists in parallel + aggregator
- iteration: 0
- last_gate: M1 L4 VERIFY **APPROVE** (round 6, separate opus-5 session)
- last_action: M1 complete and approved; explain-diff written
- next_action: G0 existence pre-flight on M2, then L1 BUILD
- model: claude-sonnet-5
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## M1 — DONE (verified)
Approved round 6 after 5 rejections / 10 blocking defects. Gates at approval:
- `pytest -q` 93 passed, exit 0
- `mypy src/` strict clean, exit 0
- demo `pr-review run --diff fixtures/sample.diff --agent security --offline` exit 0,
  findings at app/db.py:10 (sql-injection) and app/db.py:14 (hardcoded-secret),
  both confirmed against a genuinely applied post-image
- INV-1/2/3/4 all exit 0
- Verifier also confirmed on CPython 3.11.15 and 3.12.13, and ran 431 real-git-diff
  trials + a 148-run CLI fuzz with 0 wrong locations.

**NOT yet reflected in DONE.html / PLAN.md** — those two files need the user's
explicit go-ahead to edit (standing rule). M1's row still reads `todo` there.
Pending edits when approved:
- DONE.html §3: M1 status todo -> done
- PLAN.md Progress: append the M1 completion row
- PLAN.md M1 demo command: add `--json` (its success criterion says "as JSON" but
  the command string omits the flag; `--json` works and is asserted in tests)

## Notes for a cold session
- Scope is a PROTOTYPE SUBSET of the study PDF. See PLAN.md header +
  decisions/decisions-manifest.md before proposing any deferred subsystem.
- Toolchain: `.venv/bin/python`, `pytest -q`, `mypy src/`. venv is gitignored;
  recreate with `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`.
- EXPLAIN_DIFF is ON. M1's page: .genesis/explanations/2026-08-02-explanation-M1.html
- L4 VERIFY must run as a SEPARATE session on claude-opus-5. It rejected 5 times
  on M1 and every finding was real — do not treat approval as a formality.
- The parser is the highest-risk code in the repo: 8 of 10 defects were in or
  adjacent to it, and 3 were introduced by fixes to it.
