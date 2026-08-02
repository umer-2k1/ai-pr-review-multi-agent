# CURRENT
- active_loop: NONE
- target: M3 — overall_confidence + the HITL gate
- iteration: 0
- last_gate: M2 L4 VERIFY **APPROVE** (round 2, separate opus-5 session)
- last_action: M2 complete and approved; explain-diff written
- next_action: G0 existence pre-flight on M3, then L1 BUILD
- model: claude-sonnet-5
- tokens_used: 0
- tokens_budget: 50000
- skills_loaded: []

## M2 — DONE (verified)
Approved round 2 after 1 rejection / 2 blocking defects (B1 fabricated agreement +
lost a finding; B2 the criterion fixture contained no duplicate). Gates: pytest 124
passed, mypy strict clean, demo prints 5 then 4, INV-1/2/3/4 exit 0.

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
- DONE.html §3: M1 and M2 status todo -> done
- PLAN.md Progress: append the M1 and M2 completion rows
- PLAN.md M1 demo command: add `--json` (its success criterion says "as JSON" but
  the command string omits the flag; `--json` works and is asserted in tests)
- PLAN.md M2 demo command: the jq is malformed. `.findings | length, (.agents_run |
  length)` parses as `.findings | (length, ...)`, so jq exits 5. Should be
  `jq '(.findings|length), (.agents_run|length)'` — the CLI output is correct.
- PLAN.md M2 text says dedup is by (file_path, line_start); the key is now the
  triple (file_path, line_start, category) after L4 B1. Needs a decision record.

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
