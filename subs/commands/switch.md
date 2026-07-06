---
description: Switch the active Claude account (model-free via the subs hook).
argument-hint: "[slot | email | label | substring]   (empty = rotate)"
disable-model-invocation: true
---

This command is executed **locally** by the `subs` `UserPromptSubmit` hook — it
swaps `~/.claude/.credentials.json` with no model turn and takes effect
immediately (the statusline updates on your next message; no restart needed).

If you are reading this text, the hook is not active. Either enable it (see the
plugin README → "Model-free in-chat mode"), or run `subs switch <target>` in your
terminal.
