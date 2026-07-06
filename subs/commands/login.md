---
description: Track the current Claude login, backing up its credentials (via the subs hook).
argument-hint: "[label] [comment...]   |   label=.. email=.. comment=.. from=.."
disable-model-invocation: true
---

This command is executed **locally** by the `subs` `UserPromptSubmit` hook — it
captures the currently-live credentials into a tracked slot with no model turn.

If you are reading this text, the hook is not active. Either enable it (see the
plugin README → "Model-free in-chat mode"), or run `subs login <label>` in your
terminal.
