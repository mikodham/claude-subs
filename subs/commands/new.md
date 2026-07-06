---
description: Safely start adding a NEW Claude account (backs up the current one first).
argument-hint: "[label-for-the-new-account]"
disable-model-invocation: true
---

This command is executed **locally** by the `subs` `UserPromptSubmit` hook — it
backs up the currently-active account (so it can't be lost), then tells you to
`/login` as the new account and `/subs:login <label>` to track it.

If you are reading this text, the hook is not active. Either enable it (see the
plugin README → "Model-free in-chat mode"), or run `subs new <label>` in your
terminal.
