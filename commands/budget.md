---
description: Cross-account token/quota status — what's left in each pool (Claude, Codex, Gemini, Grok, API dollars)
---

Show the cross-account budget picture:

1. Run `python budget/budget.py status` and present the result readably: one line
   per pool with spent/cap, the period window, and the exact-vs-estimated line mix.
   Flag any pool below its reserve prominently.
2. If `budget/budget.json` doesn't exist yet, say so and offer to copy
   `budget/budget.example.json` into place so the user can set real caps.
3. If the ledger shows pools with spend but no configured cap, point them out —
   unlimited pools are fine, but only when intentional.

Do not editorialize the numbers; estimated lines are estimates and the display
must say so.
