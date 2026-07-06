---
description: List/search tracked Claude accounts (model-free via the subs hook).
argument-hint: "(empty = list all)  |  <substring> (filter)"
---

This command is executed **locally** by the `subs` `UserPromptSubmit` hook — it
runs with no model turn and shows the account table directly.

If you are reading this text, the hook is not active. Either enable it (see the
plugin README → "Model-free in-chat mode"), or run `subs track` in your terminal.
