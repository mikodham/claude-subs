# subs — minimal Claude Code account switcher (plugin)

Switch between multiple Claude Code accounts without re-logging in, by swapping
`~/.claude/.credentials.json` between per-account backups. Pure stdlib Python;
no external dependencies.

> **Inspired by [claude-swap (`cswap`)](https://github.com/realiti4/claude-swap).**
> That's a fuller, cross-platform Python tool you install separately; `subs`
> reimplements a small, focused subset of the same credential-swapping idea and
> **embeds it directly into the plugin** for convenience — so there's nothing
> extra to install and everything ships with the `/subs:*` commands.

> `login` accepts easy args — positional **or** `key=value` (mix freely):
> `subs login work "team plan"` · `subs login label=work email=alice@x.com` ·
> `subs login --from ~/creds.json label=old`. Labels must be unique across slots.

## Install & set up

**Prerequisite:** `python3` on your PATH (stdlib only — nothing to `pip install`).

### 1. Install the plugin

**Option A — run straight from this folder (dev / personal use):**

```bash
# from the repo root (the folder containing this `subs/` plugin):
claude --plugin-dir ./subs
# inside the session, after edits:
/reload-plugins
```

**Option B — install via the bundled marketplace:**

```
# point at the repo root — the dir containing .claude-plugin/marketplace.json
/plugin marketplace add /path/to/claude-subs    # local path, or owner/repo once pushed to GitHub
/plugin install subs@claude-subs-marketplace
```

(The marketplace manifest is at `<repo-root>/.claude-plugin/marketplace.json`.)

### 2. Track your accounts

```
/login                       # log in as your first account
/subs:login work "main"      # remember it  -> slot 1 (active)

/login                       # log in as your second account (overwrites live creds)
/subs:login personal "pro"   # remember it  -> slot 2

# already have a stashed .credentials.json for another account?
/subs:login --from ~/.claude/backups/.credentials.json personal "pro"
```

### 3. Switch

```
/subs:track                  # see everything
/subs:switch personal        # swap active -> personal  (substring ok: /subs:switch pers)
# then restart Claude Code (/exit, relaunch) for it to take effect
```

### Where things live

- Registry + per-account backups: `~/.claude/subs-backups/` (override with
  `--backup-dir` or `$SUBS_BACKUP_DIR`).
- Live credentials it swaps:
  - **Linux / WSL:** `~/.claude/.credentials.json` (honors `$CLAUDE_CONFIG_DIR`).
  - **macOS:** the **login Keychain** — a generic-password item named
    `Claude Code-credentials`. macOS Claude Code keeps the credential there, not
    in a file, so `subs` reads/writes it via `security(1)`. Everything else
    (backups, registry, the `~/.claude.json` identity block) is identical to
    Linux. See "macOS" below.

## macOS

On macOS, Claude Code does **not** keep `~/.claude/.credentials.json` — the
credential blob lives in the **login Keychain** (item `Claude Code-credentials`).
`subs` detects this automatically and swaps the Keychain item instead of a file;
no configuration needed. `python3` and the built-in `security` tool (ships with
macOS) are all it requires.

- **Backend selection** is automatic: Keychain on macOS, file elsewhere. Force it
  with `SUBS_CREDENTIAL_BACKEND=keychain|file` if you run a non-standard setup
  (e.g. a file-based login on a headless Mac).
- **One-time access prompt:** the first time Claude Code reads the item after
  `subs` rewrites it, macOS may show a Keychain prompt — choose **Always Allow**.
- **Restart to take effect:** as on every platform, a running Claude Code process
  has the old token cached; the swap is picked up on the next start.
- **`security` & `ps`:** writing the Keychain passes the token as a command
  argument (the `security` CLI has no stdin password mode), so it is briefly
  visible in the local process table — the same token is already plaintext in the
  Keychain and in `subs` backups. Local-only, sub-second, and documented.
- **Terminal integration works on macOS bash 3.2** (the default `/bin/bash`) and
  zsh — `source .../subs-init.sh` and the `subs` picker/completion are portable.

## Terminal use (instant — no model turn)

An in-chat `/subs:*` command is a prompt template: Claude Code runs it *through
the model*, so it always produces a Claude turn (this is by design — a plugin
command cannot be a pure local action like `/resume`). For **instant** switching
with **zero** model involvement, use the shell integration and run `subs`
directly in your terminal:

```bash
# add to ~/.bashrc (or ~/.zshrc):
source /path/to/claude-subs/subs/scripts/subs-init.sh
```

Then:

```bash
subs                       # ← interactive picker: type to search, ↑/↓, Enter to switch
subs track                 # list accounts
subs switch personal       # swap directly (substring ok: `subs switch pers`)
subs login work "comment"  # track the current login
subs <TAB>                 # complete subcommands
subs switch <TAB>          # complete account labels/slots (substring match)
```

**The picker (`subs` or `subs pick`)** is the closest thing to a `/rewind`-style
GUI: a live, searchable list you navigate with the arrow keys — **no model, no
API call, instant.** It uses [`fzf`](https://github.com/junegunn/fzf) if
installed (nicer fuzzy UX), otherwise a built-in stdlib-curses picker (zero
dependencies). Type to filter by label/email, `Enter` to switch, `Esc` to
cancel.

Global flags work in any position (`subs track --json` or `subs --json track`).

## Model-free in-chat mode (hook) — keeps the `/subs` menu

You can run `subs` **inside the Claude Code chat with no model turn** *and* keep
the `/subs:*` slash menu. This works because:

- The `commands/*.md` files register `/subs:login|switch|track` — so typing
  `/subs` still shows them in the slash menu (discoverability).
- A `UserPromptSubmit` **hook** (`hooks/hooks.json` + `hooks/subs-hook.sh`)
  intercepts the raw prompt *before* the command expands, runs `subs` locally,
  and returns `{"decision":"block","suppressOriginalPrompt":true}`. Per the CLI
  dispatch code, that sets `shouldQuery:false` — **the model is never called** —
  and shows the output in the transcript.

So with the hook enabled, **both** of these run locally, model-free:

```
/subs:track            # picked from the /subs menu — still model-free
/subs:switch 20x
subs track             # plain text works too
subs track 20x
```

Nice fallback: if the hook is ever disabled, `/subs:*` still works — it just
goes back to a (model-routed) command. The hook is a pure upgrade.

Anything the hook doesn't recognize (`/subs:pick`, `/help`, normal prompts)
passes straight through untouched. `pick` is excluded — a hook has no TTY for the
interactive picker; use it from the terminal.

**Enable it** (either):
- *Plugin route:* the hook ships in `hooks/hooks.json` (v0.2.0+). Update the
  marketplace and reinstall so the cache picks it up, then `/reload-plugins`
  (it should report `1 hook`).
- *Quick test:* add to `~/.claude/settings.json` (adjust the path):
  ```json
  {
    "hooks": {
      "UserPromptSubmit": [
        { "hooks": [ { "type": "command",
          "command": "bash \"/path/to/claude-subs/subs/hooks/subs-hook.sh\"" } ] }
      ]
    }
  }
  ```

**Caveats — read before relying on it:**
- **Undocumented & version-specific.** Verified against CLI v2.1.198 by reading
  the dispatch code (`docs_devel/study/04-cli-command-dispatch.md`). A Claude
  Code update could change it.
- **The `UserPromptSubmit operation blocked by hook:` header line cannot be
  removed.** It is hard-coded in the CLI (function `Dzo`) and fires on *every*
  model-free hook path (block or `continue:false`) — it is the price of ending
  the turn without the model. `suppressOriginalPrompt:true` at least hides your
  raw `/subs:…` line, and the output below the header renders as a table.
- The hook runs on **every** prompt you submit (it exits immediately for
  non-`subs` prompts).
- It intercepts before the model, so an intercepted `subs …` message never
  reaches Claude — by design.

## Commands

| Command | What it does |
|---|---|
| `/subs:login [label] [comment...]` | Track the **current** login (backs up its credentials). Run `/login` as an account first, then this. |
| `/subs:login --from <file> <label> [comment...]` | Import credentials from an existing file (e.g. a stashed `.credentials.json`) as a tracked account. Does not change the active account. |
| `/subs:new [label]` | **Safely start adding a new account:** backs up the current one first, then tells you to `/login` as the new account and `/subs:login <label>`. |
| `/subs:switch [slot\|email\|label\|substring]` | Swap the active credentials to a tracked account. Empty arg = rotate to next. Takes effect immediately (no restart). |
| `/subs:track` | List tracked accounts (slot, label, comment, email, org, active, token state). |
| `/subs:track <substring>` | Filter the list to accounts whose label/email contains the substring. |
| `/subs:track <slot\|email> <label> [comment...]` | Set a slot's label/comment. |
| `/subs:track --sync` | Re-derive the active slot from the live credentials. |

### Logging in & adding accounts

`subs` never drives Claude Code's OAuth flow itself — it only **captures**
whatever login is currently live. So:

- **First account:** you're already logged into Claude Code (or run `/login`).
  Then `/subs:login <label>` records it as slot 1.
- **Each additional account** is inherently a 3-step dance, because signing into
  a new account overwrites the live credentials:
  1. `/subs:new <label>` — **backs up the current account first** (so it's never
     lost), and reminds you of the next steps.
  2. `/login` — sign in as the new account (this replaces the live credentials).
  3. `/subs:login <label>` — captures the new account into its own slot.

  (`/subs:login` alone also works — it creates a new slot whenever the live
  identity `(email, org)` isn't tracked yet. `/subs:new` just adds the safety
  backup + guidance so you can't accidentally lose an untracked account.)

### Fuzzy matching

- **Account targets** (`switch`, and `track` filtering) match by **substring**,
  case-insensitively, over label and email. `/subs:switch ent` → `5x-enterprise`.
  If a substring matches more than one account, the command returns a
  `candidates` list so you can pick the exact slot.
- **Command names** are discovered by Claude Code's built-in slash menu: type
  `/subs` (or a fragment) and `login` / `switch` / `track` surface with their
  descriptions — no extra config needed.

## Typical flow

```
/login                 # log in as your first account (creates ~/.claude/.credentials.json)
/subs:login work "main max plan"       # -> slot 1 tracked & backed up

/login                 # log in as your second account (overwrites credentials.json)
/subs:login personal "pro plan"        # -> slot 2 tracked & backed up

/subs:track            # see both
/subs:switch personal  # swap active -> personal, then RESTART Claude Code
```

## How it works

- **What an account is:** a slot in `registry.json` with `label`, `comment`,
  `email`, `orgUuid`, plus a backup file holding the verbatim
  `.credentials.json` (and the `oauthAccount` identity block).
- **Identity key** is `(email, orgUuid)` — the same email on a personal vs. an
  org account are distinct slots. Re-running `login` on an already-tracked
  account refreshes its backup (tokens rotate) rather than duplicating it.
- **Switch** (under a file lock, with rollback): re-capture the live creds into
  the current slot → write the target's creds into `~/.claude/.credentials.json`
  atomically → set active. By default it touches **only** `credentials.json`
  (`swapIdentity:false`); flip `swapIdentity` to `true` in `registry.json` (or
  pass `--identity`) to also swap the `oauthAccount` identity so `/status`
  displays the correct email.

## Storage & security

- Backups live **outside the repo** at `~/.claude/subs-backups/` by default
  (override with `--backup-dir` or `$SUBS_BACKUP_DIR`), resolved from real
  `$HOME` — not `$CLAUDE_CONFIG_DIR` — so relocating your config dir never
  orphans them.
- Backups are **plaintext OAuth tokens** (same as `.credentials.json` itself).
  The dir is created `0700` and files `0600`. Do **not** point `--backup-dir` at
  a git-tracked folder; if you must, gitignore it.

## Caveats

- **Restart required:** a switch takes effect on the next Claude Code start — the
  running session has the old token cached.
- **Login is capture-only:** the plugin can't drive the OAuth browser flow; you
  `/login` normally, then `/subs:login` remembers that account.
- **Stale refresh tokens:** a long-unused backup may need a one-time `/login`;
  `/subs:track` flags expired tokens.
- **In-chat commands always produce a Claude turn** and, on first run, a Bash
  permission check. This is inherent to plugin slash commands — for a fully
  local, instant experience use the terminal integration above. If the in-chat
  permission check ever errors, just re-run and choose *"Yes, don't ask again"*
  so Claude Code persists the approval.
- **Installed = a cached copy:** `/plugin install` copies the plugin to
  `~/.claude/plugins/cache/.../subs/<version>/`. Edits to this repo do **not**
  affect the installed commands until you bump the version and reinstall (or use
  `claude --plugin-dir ./subs` for live development). The terminal `subs`
  function, by contrast, runs this repo's `subs.py` directly and always reflects
  your latest code.

## Local testing

```
claude --plugin-dir ./subs
# then, inside the session:
/subs:track
```
Use `/reload-plugins` to pick up edits.
