#!/usr/bin/env bash
# UserPromptSubmit hook — EXPERIMENTAL model-free in-chat `subs`.
#
# When you type a prompt like `subs track` or `subs switch 20x` in the Claude
# Code chat, this hook intercepts it, runs subs.py LOCALLY, and returns a
# "block" decision. Per the v2.1.198 CLI dispatch code, blocking a
# UserPromptSubmit hook renders the hook's text to the transcript and sets
# shouldQuery=false — i.e. the model is NEVER called (no API turn).
#
# Only prompts whose first word is `subs` AND whose second word is a known
# non-interactive subcommand are intercepted; everything else passes straight
# through to the model untouched (so "subs plugin is broken, help" still reaches
# Claude). `pick` is excluded — it needs a real TTY, which a hook doesn't have.
#
# This is UNDOCUMENTED, version-specific behavior. Treat it as a proof of
# concept; it may change on a Claude Code update.
set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBS_PY="$HOOK_DIR/../scripts/subs.py"

input="$(cat)"
prompt="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null || true)"

# Recognize all of these forms and reduce to the args after the `subs` prefix:
#   /subs:track 20x   (slash-command form — stays in the /subs menu)
#   /subs switch 20x
#   subs track        (plain-text form)
#   /subs  |  subs    (bare -> track)
case "$prompt" in
  /subs:*) args="${prompt#/subs:}" ;;
  "/subs "*) args="${prompt#/subs }" ;;
  /subs)   args="track" ;;
  "subs "*) args="${prompt#subs }" ;;
  subs)    args="track" ;;
  *) exit 0 ;;                 # not ours: let the prompt reach the model
esac
[ -z "$args" ] && args="track"

# Only intercept known, non-interactive subcommands; anything else passes
# through to the model. `pick` is excluded — a hook has no TTY for curses/fzf.
sub="$(printf '%s' "$args" | awk '{print $1}')"
case "$sub" in
  track|switch|login|new|status) ;;
  *) exit 0 ;;
esac

# shellcheck disable=SC2086
out="$(python3 "$SUBS_PY" $args 2>&1 || true)"

# Block the turn: replace the expanded prompt, do NOT query the model, and show
# `out` in the transcript. `suppressOriginalPrompt` drops the raw `/subs:…` line.
python3 - "$out" <<'PY'
import json, sys
print(json.dumps({
    "decision": "block",
    "reason": sys.argv[1] if len(sys.argv) > 1 else "",
    "suppressOriginalPrompt": True,
}))
PY
