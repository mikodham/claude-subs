# subs — terminal shell integration (zero Claude Code involvement)
#
# Add this ONE line to your ~/.bashrc (or ~/.zshrc):
#
#     source /path/to/claude-subs/subs/scripts/subs-init.sh
#
# Then, in any terminal:
#     subs                       # OPEN the interactive fuzzy-search picker (like a GUI)
#     subs track                 # list accounts
#     subs switch personal       # swap active account (substring ok: `subs switch pers`)
#     subs login work "comment"  # track the current login
#     subs <TAB>                 # complete subcommands
#     subs switch <TAB>          # complete account labels/slots
#
# This runs subs.py directly — instant, no model, no permission prompts.

# Resolve the directory of this script at source time (bash & zsh).
if [ -n "${BASH_SOURCE:-}" ]; then
  __SUBS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
elif [ -n "${(%):-%x}" ] 2>/dev/null; then
  __SUBS_DIR="$(cd "$(dirname "${(%):-%x}")" && pwd)"   # zsh
else
  __SUBS_DIR="$(cd "$(dirname "$0")" && pwd)"
fi

subs() {
  local py
  py="$(command -v python3 || command -v python)"
  if [ -z "$py" ]; then
    echo "subs: python3 not found on PATH" >&2
    return 1
  fi
  # Bare `subs` opens the interactive picker.
  if [ "$#" -eq 0 ]; then
    set -- pick
  fi
  "$py" "$__SUBS_DIR/subs.py" "$@"
}

# --- bash completion: subcommands, then account labels/slots for switch/track ---
_subs_complete() {
  local cur prev words cword
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"

  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=( $(compgen -W "pick login switch track new" -- "$cur") )
    return
  fi

  case "${COMP_WORDS[1]}" in
    switch|track)
      # complete against tracked labels + slot numbers, matching by SUBSTRING
      # (case-insensitive) — same semantics subs uses to resolve targets.
      local opts w lc_cur
      opts="$(subs --json track 2>/dev/null | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
    for a in d.get("accounts",[]):
        print(a.get("label","")); print(a.get("slot",""))
except Exception:
    pass
' 2>/dev/null)"
      # Portable lowercase (macOS ships bash 3.2, which lacks ${var,,}).
      lc_cur="$(printf '%s' "$cur" | tr '[:upper:]' '[:lower:]')"
      COMPREPLY=()
      while IFS= read -r w; do
        [ -z "$w" ] && continue
        local lc_w
        lc_w="$(printf '%s' "$w" | tr '[:upper:]' '[:lower:]')"
        if [ -z "$cur" ] || [[ "$lc_w" == *"$lc_cur"* ]]; then
          COMPREPLY+=( "$w" )
        fi
      done <<< "$opts"
      ;;
  esac
}
if [ -n "${BASH_VERSION:-}" ]; then
  complete -F _subs_complete subs
fi
