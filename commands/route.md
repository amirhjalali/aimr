---
description: Route a task to the best specialist CLI you can afford — reads registry.json, checks budget, loads the winning lane's skill
argument-hint: <describe the task, e.g. "generate a vintage badge logo" or "recon the auth flow in this repo">
---

Route this task through the AIMR capability registry: $ARGUMENTS

Follow the `routing` skill (skills/routing/SKILL.md) exactly:

1. Map the task onto a registry capability key (note the from-scratch vs
   reference-driven distinction for image work).
2. Read `registry.json` for that capability's ranked providers; skip
   `invocation.type: "web-ui"` entries but mention them if they outrank everything
   scriptable.
3. Check the top candidate's pool: `python budget/budget.py remaining --pool <pool>`
   (exit 3 = below reserve → fall to next provider).
4. Read the winning provider's `gotchas`, load its skill, and execute the dispatch.
5. Log the spend and verify the artifact contract before reporting done.

Report: which lane won and why (score, suite, date), what it cost, where the
artifact landed. If nothing scriptable is affordable or suitable, present the
ranked options including web-ui ones and stop.
