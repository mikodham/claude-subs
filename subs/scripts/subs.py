#!/usr/bin/env python3
"""subs — minimal Claude Code account switcher.

Swaps ~/.claude/.credentials.json between tracked accounts, with per-account
backups stored outside the repo (default ~/.claude/subs-backups/). Optionally
also swaps the `oauthAccount` identity block in ~/.claude.json (full mode).

Stdlib only.

Subcommands:
  login  [label] [comment...]        capture the current live login as a tracked account
  switch [n|email|label]             swap credentials.json to a tracked account (empty = rotate)
  track                              list tracked accounts (read-only)
  track  <n|email> [label] [comment...]   set a slot's label/comment
  track  --sync                      re-derive `active` from the live credentials

Common flags: --json, --backup-dir DIR, --identity/--no-identity
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # POSIX

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows fallback
    _HAVE_FCNTL = False

REGISTRY_VERSION = 1

# macOS: Claude Code stores the credential blob in the login Keychain as a
# generic password under this service name (overridable for testing).
KEYCHAIN_SERVICE = os.environ.get("SUBS_KEYCHAIN_SERVICE") or "Claude Code-credentials"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class SubsError(Exception):
    """A handled, user-facing error carrying a machine-readable type."""

    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_
        self.message = message


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def home() -> Path:
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    """Claude Code's config dir — honors CLAUDE_CONFIG_DIR, else ~/.claude."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else home() / ".claude"


def credentials_path() -> Path:
    return config_dir() / ".credentials.json"


def claude_json_path() -> Path:
    """The config file holding `oauthAccount`. Prefer ~/.claude.json (the
    well-known location); fall back to one inside the config dir."""
    primary = home() / ".claude.json"
    if primary.exists():
        return primary
    alt = config_dir() / ".claude.json"
    if alt.exists():
        return alt
    return primary  # default target if neither exists yet


def backup_dir(arg: str | None) -> Path:
    """Backups live OUTSIDE the repo, resolved from real $HOME (not
    CLAUDE_CONFIG_DIR) so relocating the config dir never orphans them."""
    if arg:
        return Path(os.path.expanduser(arg))
    env = os.environ.get("SUBS_BACKUP_DIR")
    if env:
        return Path(os.path.expanduser(env))
    return home() / ".claude" / "subs-backups"


# --------------------------------------------------------------------------- #
# Small IO helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise SubsError("corrupt_file", f"{path} is not valid JSON: {exc}") from exc


def atomic_write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    """Write JSON via tmp file + fsync + rename, then chmod. Atomic per-file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".subs.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def slugify(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
    return slug or "account"


# --------------------------------------------------------------------------- #
# Credential backend — file (Linux/WSL) vs macOS login Keychain
# --------------------------------------------------------------------------- #
def _is_macos() -> bool:
    return sys.platform == "darwin"


def keychain_present() -> bool:
    """True if the Claude Code credential item exists in the login Keychain."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    return r.returncode == 0


def credential_backend() -> str:
    """Where the LIVE credential blob lives: 'file' or 'keychain'.

    macOS keeps it in the login Keychain (no ~/.claude/.credentials.json);
    everything else uses the file. Override with
    SUBS_CREDENTIAL_BACKEND=file|keychain (anything else = auto).
    """
    override = os.environ.get("SUBS_CREDENTIAL_BACKEND", "").strip().lower()
    if override in ("file", "keychain"):
        return override
    if _is_macos() and shutil.which("security"):
        # Keychain is authoritative on macOS; fall back to a real file only if
        # one exists and the Keychain has nothing.
        if keychain_present() or not credentials_path().exists():
            return "keychain"
        return "file"
    return "file"


def keychain_account() -> str:
    """The `acct` of the existing Claude item, else the current macOS user
    (matches what Claude Code uses so it reads back the same item)."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            m = re.search(r'"acct"<blob>="((?:[^"\\]|\\.)*)"', r.stdout)
            if m:
                return m.group(1)
    except OSError:
        pass
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or "user"


def keychain_read() -> dict | None:
    """Read + parse the credential JSON from the login Keychain, or None."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True,
        )
    except OSError as exc:  # `security` missing (non-macOS) — treat as absent
        raise SubsError("keychain_unavailable", f"Could not run `security`: {exc}") from exc
    if r.returncode != 0:
        return None  # no such item
    raw = r.stdout.rstrip("\n")  # `security -w` appends a newline
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SubsError(
            "corrupt_keychain",
            f"Keychain item {KEYCHAIN_SERVICE!r} is not valid JSON: {exc}",
        ) from exc


def keychain_write(creds: dict) -> None:
    """Write the credential JSON into the login Keychain (create or update).

    `add-generic-password -U` atomically replaces the item's data. The secret is
    passed as an argv, so it is briefly visible in `ps` — the `security` CLI has
    no stdin password mode. See design doc's security section.
    """
    acct = keychain_account()
    blob = json.dumps(creds, separators=(",", ":"))
    try:
        r = subprocess.run(
            ["security", "add-generic-password", "-U",
             "-a", acct, "-s", KEYCHAIN_SERVICE, "-w", blob],
            capture_output=True, text=True,
        )
    except OSError as exc:
        raise SubsError("keychain_write_failed", f"Could not run `security`: {exc}") from exc
    if r.returncode != 0:
        raise SubsError(
            "keychain_write_failed",
            f"`security add-generic-password` failed: {r.stderr.strip() or r.stdout.strip()}",
        )


# --------------------------------------------------------------------------- #
# Live credential / identity access
# --------------------------------------------------------------------------- #
def read_live_credentials() -> dict | None:
    """The live Claude Code credential blob — from the Keychain on macOS,
    else from ~/.claude/.credentials.json."""
    if credential_backend() == "keychain":
        return keychain_read()
    return read_json(credentials_path())


def write_live_credentials(creds: dict) -> None:
    if credential_backend() == "keychain":
        keychain_write(creds)
    else:
        atomic_write_json(credentials_path(), creds, mode=0o600)


def read_live_oauth_account() -> dict | None:
    cfg = read_json(claude_json_path())
    if not cfg:
        return None
    acct = cfg.get("oauthAccount")
    return acct if isinstance(acct, dict) else None


def write_live_oauth_account(oauth_account: dict) -> None:
    """Splice `oauthAccount` into ~/.claude.json, preserving everything else
    and the file's existing permissions."""
    path = claude_json_path()
    cfg = read_json(path) or {}
    cfg["oauthAccount"] = oauth_account
    try:
        prev_mode = path.stat().st_mode & 0o777
    except OSError:
        prev_mode = 0o600
    atomic_write_json(path, cfg, mode=prev_mode)


def identity_from_oauth(acct: dict | None) -> dict:
    """Normalize the fields we care about out of an oauthAccount block."""
    acct = acct or {}
    # Field names per the credential study; tolerate `email` vs `emailAddress`.
    email = acct.get("emailAddress") or acct.get("email") or ""
    return {
        "email": email,
        "orgUuid": acct.get("organizationUuid") or "",
        "orgName": acct.get("organizationName") or "",
        "accountUuid": acct.get("accountUuid") or "",
    }


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def registry_path(bdir: Path) -> Path:
    return bdir / "registry.json"


def load_registry(bdir: Path) -> dict:
    reg = read_json(registry_path(bdir))
    if reg is None:
        reg = {
            "version": REGISTRY_VERSION,
            "active": None,
            "swapIdentity": False,
            "accounts": {},
        }
    reg.setdefault("accounts", {})
    reg.setdefault("swapIdentity", False)
    reg.setdefault("active", None)
    return reg


def save_registry(bdir: Path, reg: dict) -> None:
    atomic_write_json(registry_path(bdir), reg)


@contextmanager
def registry_lock(bdir: Path):
    """Single-writer lock on a dedicated .lock file (never the file we rewrite)."""
    bdir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(bdir, 0o700)
    except OSError:
        pass
    lockfile = bdir / ".lock"
    fh = lockfile.open("w")
    try:
        if _HAVE_FCNTL:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if _HAVE_FCNTL:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def find_slot_by_identity(reg: dict, email: str, org: str) -> str | None:
    for num, acct in reg["accounts"].items():
        if acct.get("email", "") == email and acct.get("orgUuid", "") == org:
            return num
    return None


def label_conflict(reg: dict, label: str, keep_slot: str | None) -> str | None:
    """Return the slot already using `label` (other than keep_slot), or None."""
    for num, acct in reg["accounts"].items():
        if num != keep_slot and acct.get("label") == label:
            return num
    return None


def resolve_target(reg: dict, token: str | None) -> str:
    """Resolve a switch/track target token to a slot number (string key)."""
    accounts = reg["accounts"]
    if not accounts:
        raise SubsError("no_accounts", "No tracked accounts yet. Run login first.")
    nums_sorted = sorted(accounts, key=lambda n: int(n))

    if token is None or token == "":
        # rotate: next slot after active in sorted order (wraps)
        active = str(reg.get("active")) if reg.get("active") is not None else None
        if active in nums_sorted:
            idx = nums_sorted.index(active)
            return nums_sorted[(idx + 1) % len(nums_sorted)]
        return nums_sorted[0]

    if token in accounts:  # exact slot number
        return token

    # exact label/email match wins outright
    exact = [n for n, a in accounts.items()
             if a.get("label") == token or a.get("email") == token]
    if len(exact) == 1:
        return exact[0]

    # otherwise, case-insensitive substring match over label + email
    needle = token.lower()
    subs_matches = [
        n for n, a in accounts.items()
        if needle in (a.get("label") or "").lower()
        or needle in (a.get("email") or "").lower()
    ]
    if len(subs_matches) == 1:
        return subs_matches[0]

    candidates = exact if len(exact) > 1 else subs_matches
    if candidates:
        listing = [
            {"slot": int(n), "label": accounts[n].get("label"),
             "email": accounts[n].get("email")}
            for n in sorted(candidates, key=lambda n: int(n))
        ]
        err = SubsError(
            "ambiguous",
            f"{token!r} matches {len(candidates)} accounts: "
            + ", ".join(f"[{c['slot']}] {c['label']} <{c['email']}>" for c in listing),
        )
        err.candidates = listing  # surfaced in JSON for a pick-list UI
        raise err
    raise SubsError("unknown_account", f"No tracked account matches {token!r}.")


def backup_file(bdir: Path, slot: str, label: str) -> Path:
    return bdir / f"{slot}-{slugify(label)}.json"


def save_backup(bdir: Path, slot: str, label: str, creds: dict | None,
                oauth_account: dict | None) -> str:
    """Write a slot's backup file; return its filename."""
    fname = f"{slot}-{slugify(label)}.json"
    atomic_write_json(
        bdir / fname,
        {
            "capturedAt": now_iso(),
            "credentials": creds,
            "oauthAccount": oauth_account,
        },
    )
    return fname


def token_expiry(creds: dict | None) -> int | None:
    if not creds:
        return None
    oauth = creds.get("claudeAiOauth") or {}
    exp = oauth.get("expiresAt")
    return int(exp) if isinstance(exp, (int, float)) else None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_login(args) -> dict:
    bdir = backup_dir(args.backup_dir)
    label_arg, email_arg, comment_arg, from_path = parse_login_tokens(
        getattr(args, "tokens", None), getattr(args, "from_path", None))

    if from_path:
        # Import from an arbitrary credentials file. Such a file carries no
        # identity (oauthAccount) and importing it does NOT make it the active
        # account — it just stashes it as a tracked backup.
        src = Path(os.path.expanduser(from_path))
        creds = read_json(src)
        if not creds or not creds.get("claudeAiOauth"):
            raise SubsError(
                "bad_source",
                f"{src} is missing or has no `claudeAiOauth` block.",
            )
        oauth_account = None
        set_active = False
    else:
        creds = read_live_credentials()
        if not creds:
            where = ("the login Keychain (item 'Claude Code-credentials')"
                     if credential_backend() == "keychain"
                     else "~/.claude/.credentials.json")
            raise SubsError(
                "not_logged_in",
                f"No live Claude Code credentials found in {where}. Run `/login` "
                "(or `claude login`) first, then re-run login to track this account.",
            )
        oauth_account = read_live_oauth_account()
        set_active = True

    ident = identity_from_oauth(oauth_account)
    if email_arg:  # explicit email= overrides / supplies the identity email
        ident["email"] = email_arg

    with registry_lock(bdir):
        reg = load_registry(bdir)
        if ident["email"]:
            slot = find_slot_by_identity(reg, ident["email"], ident["orgUuid"])
        else:
            # identity-less import: dedup by label so re-runs are idempotent
            slot = next(
                (n for n, a in reg["accounts"].items() if a.get("label") == label_arg),
                None,
            ) if label_arg else None
        created = slot is None
        if created:
            existing = [int(n) for n in reg["accounts"]]
            slot = str(max(existing) + 1 if existing else 1)

        acct = reg["accounts"].get(slot, {})
        # label: explicit arg > existing > email local-part > account-N
        if label_arg:
            label = label_arg
        elif acct.get("label"):
            label = acct["label"]
        elif ident["email"]:
            label = ident["email"].split("@", 1)[0]
        else:
            label = f"account-{slot}"

        # Labels must be unique across slots. If the chosen label collides with a
        # DIFFERENT slot: reject a user-supplied one; auto-disambiguate a derived one.
        conflict = label_conflict(reg, label, slot)
        if conflict:
            if label_arg:
                raise SubsError(
                    "duplicate_label",
                    f"Label {label!r} is already used by slot {conflict}. "
                    "Pick a different label (labels must be unique).",
                )
            label = f"{label}-{slot}"

        comment = comment_arg if comment_arg is not None else acct.get("comment", "")

        # remove a stale backup file if the label changed
        old_backup = acct.get("backup")
        fname = save_backup(bdir, slot, label, creds, oauth_account)
        if old_backup and old_backup != fname:
            try:
                (bdir / old_backup).unlink()
            except OSError:
                pass

        reg["accounts"][slot] = {
            "label": label,
            "comment": comment,
            "email": ident["email"],
            "orgUuid": ident["orgUuid"],
            "orgName": ident["orgName"],
            "accountUuid": ident["accountUuid"],
            "backup": fname,
            "added": acct.get("added", now_iso()),
            "updated": now_iso(),
        }
        if set_active:
            reg["active"] = int(slot)
        save_registry(bdir, reg)

    return {
        "ok": True,
        "action": "login",
        "created": created,
        "imported": bool(from_path),
        "active": reg.get("active"),
        "slot": int(slot),
        "label": label,
        "comment": comment,
        "email": ident["email"],
        "org": ident["orgName"] or ("personal" if not ident["orgUuid"] else ident["orgUuid"]),
        "total": len(reg["accounts"]),
    }


def cmd_switch(args) -> dict:
    return perform_switch(backup_dir(args.backup_dir), args.target, args.identity)


def perform_switch(bdir: Path, target_token: str | None,
                   identity_override: bool | None = None) -> dict:
    """Core switch: swap the active credentials to the resolved target slot.
    Shared by `cmd_switch` and the interactive picker (`cmd_pick`)."""
    with registry_lock(bdir):
        reg = load_registry(bdir)
        target = resolve_target(reg, target_token)
        active = str(reg["active"]) if reg.get("active") is not None else None

        if target == active:
            acct = reg["accounts"][target]
            return {
                "ok": True,
                "action": "switch",
                "noop": True,
                "slot": int(target),
                "label": acct.get("label"),
                "email": acct.get("email"),
                "message": f"Already on {acct.get('email') or acct.get('label')}.",
            }

        swap_identity = identity_override if identity_override is not None else reg.get("swapIdentity", False)

        # 1) save-current: re-capture the live creds into the active slot
        #    (tokens rotate; keep the active slot's backup fresh before leaving).
        live = read_live_credentials()
        if active and active in reg["accounts"] and live:
            a = reg["accounts"][active]
            save_backup(bdir, active, a.get("label", active), live,
                        read_live_oauth_account())

        # 2) load target backup
        tgt_acct = reg["accounts"][target]
        tbk = read_json(bdir / tgt_acct["backup"])
        if not tbk or not tbk.get("credentials"):
            raise SubsError(
                "missing_backup",
                f"Backup for slot {target} ({tgt_acct.get('email')}) is missing or has no "
                "credentials. Re-run login while that account is active.",
            )

        # 3) write target auth (atomic), keeping prior bytes for rollback
        prev_creds = read_live_credentials()
        prev_oauth = read_live_oauth_account()
        try:
            write_live_credentials(tbk["credentials"])
            # 4) optionally splice identity
            if swap_identity and tbk.get("oauthAccount"):
                write_live_oauth_account(tbk["oauthAccount"])
        except Exception as exc:  # rollback in reverse
            if prev_creds is not None:
                try:
                    write_live_credentials(prev_creds)
                except Exception:
                    pass
            if swap_identity and prev_oauth is not None:
                try:
                    write_live_oauth_account(prev_oauth)
                except Exception:
                    pass
            raise SubsError("switch_failed", f"Switch failed and was rolled back: {exc}") from exc

        # 5) commit active pointer
        reg["active"] = int(target)
        save_registry(bdir, reg)

    exp = token_expiry(tbk.get("credentials"))
    warn = None
    if exp is not None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if exp <= now_ms:
            warn = "This account's stored token is expired — you may need to `/login` for it once."

    return {
        "ok": True,
        "action": "switch",
        "slot": int(target),
        "label": tgt_acct.get("label"),
        "email": tgt_acct.get("email"),
        "org": tgt_acct.get("orgName") or ("personal" if not tgt_acct.get("orgUuid") else tgt_acct.get("orgUuid")),
        "comment": tgt_acct.get("comment", ""),
        "swappedIdentity": bool(swap_identity),
        "warning": warn,
        "restartRequired": False,
        "message": "Active immediately — the statusline updates on your next message.",
    }


def _account_rows(bdir: Path, reg: dict) -> list[dict]:
    """Display rows for the picker, sorted by slot, with token freshness."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = []
    for num in sorted(reg["accounts"], key=lambda n: int(n)):
        a = reg["accounts"][num]
        tbk = read_json(bdir / a.get("backup", "")) if a.get("backup") else None
        exp = token_expiry(tbk.get("credentials") if tbk else None)
        token = "unknown" if exp is None else ("expired" if exp <= now_ms else "valid")
        rows.append({
            "slot": num,
            "label": a.get("label") or "",
            "email": a.get("email") or "",
            "org": a.get("orgName") or ("personal" if not a.get("orgUuid") else a.get("orgUuid")),
            "active": reg.get("active") == int(num),
            "token": token,
        })
    return rows


def _fzf_pick(rows: list[dict]) -> str | None:
    """Fuzzy picker via fzf if available. Returns the chosen slot or None."""
    lines = []
    for r in rows:
        marker = "*" if r["active"] else " "
        lines.append(f"{r['slot']}\t{marker} {r['label']}\t{r['email']}\t{r['org']}\t[{r['token']}]")
    try:
        proc = subprocess.run(
            ["fzf", "--prompt=switch account > ", "--with-nth=2..",
             "--delimiter=\t", "--height=~50%", "--reverse", "--no-multi"],
            input="\n".join(lines), capture_output=True, text=True,
        )
    except OSError:
        return None
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return None  # cancelled (fzf exits 130 on Esc)
    return out.split("\t", 1)[0]


def _curses_pick(rows: list[dict]) -> str | None:
    """Stdlib-curses picker: type to filter (substring), arrows to move,
    Enter to select, Esc to cancel. Returns the chosen slot or None."""
    import curses

    def run(stdscr):
        curses.curs_set(1)
        query, idx = "", 0
        while True:
            q = query.lower()
            shown = [r for r in rows if q in (r["label"] + " " + r["email"]).lower()]
            idx = max(0, min(idx, len(shown) - 1)) if shown else 0
            stdscr.erase()
            stdscr.addstr(0, 0, "Switch account — type to search, ↑/↓ move, Enter select, Esc cancel"[:curses.COLS - 1])
            stdscr.addstr(1, 0, f"> {query}"[:curses.COLS - 1])
            for i, r in enumerate(shown):
                marker = "▶" if r["active"] else " "
                line = f"{marker} [{r['slot']}] {r['label']:<16} {r['email']:<26} {r['org']:<14} [{r['token']}]"
                attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
                try:
                    stdscr.addstr(3 + i, 0, line[:curses.COLS - 1], attr)
                except curses.error:
                    pass
            stdscr.refresh()
            ch = stdscr.getch()
            if ch == 27:                      # Esc
                return None
            elif ch == curses.KEY_UP:
                idx = max(0, idx - 1)
            elif ch == curses.KEY_DOWN:
                idx = min(len(shown) - 1, idx + 1) if shown else 0
            elif ch in (10, 13, curses.KEY_ENTER):
                if shown:
                    return shown[idx]["slot"]
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
            elif 32 <= ch < 127:
                query += chr(ch)

    try:
        return curses.wrapper(run)
    except curses.error:
        return None


def cmd_pick(args) -> dict:
    """Interactive, model-free account switcher for the terminal."""
    bdir = backup_dir(args.backup_dir)
    reg = load_registry(bdir)
    if not reg["accounts"]:
        raise SubsError("no_accounts", "No tracked accounts yet. Run `subs login` first.")

    rows = _account_rows(bdir, reg)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SubsError("not_a_tty", "`subs pick` needs an interactive terminal. "
                                     "Use `subs switch <slot|label>` instead.")

    slot = _fzf_pick(rows) if shutil.which("fzf") else _curses_pick(rows)
    if slot is None:
        return {"ok": True, "action": "pick", "cancelled": True,
                "message": "Cancelled — no change."}
    return perform_switch(bdir, slot, args.identity)


class _Args:
    """Lightweight args holder for internal command reuse (mirrors login args)."""
    def __init__(self, **kw):
        self.backup_dir = kw.get("backup_dir")
        self.tokens = kw.get("tokens", [])
        self.from_path = kw.get("from_path")
        self.identity = kw.get("identity")


def cmd_new(args) -> dict:
    """Safely begin adding a NEW account: first back up (capture/refresh) the
    account that is live RIGHT NOW so it can't be lost when you overwrite the
    live credentials with `/login`, then guide the user through the rest.

    We cannot drive Claude Code's OAuth browser flow from here, so adding an
    account is inherently: (1) protect current [this command], (2) `/login` as
    the new account, (3) `subs login <label>` to capture it.
    """
    bdir = backup_dir(args.backup_dir)
    if not read_live_credentials():
        raise SubsError(
            "not_logged_in",
            "You're not logged into Claude Code yet. Run `/login` first, then "
            "`subs login <label>` to track that account.",
        )
    # Capture/refresh whatever is live now (creates a slot if it isn't tracked).
    cap = cmd_login(_Args(backup_dir=args.backup_dir))
    new_label = args.label or "<label>"
    return {
        "ok": True,
        "action": "new",
        "current": {"slot": cap["slot"], "label": cap["label"], "email": cap["email"]},
        "newLabel": args.label,
        "message": (
            f"Now: (1) run `/login` and sign in as the NEW account "
            f"(this replaces the live credentials — the current one is already "
            f"backed up above), then (2) run `subs login {new_label}` to track it."
        ),
    }


def cmd_track(args) -> dict:
    bdir = backup_dir(args.backup_dir)

    if args.sync:
        with registry_lock(bdir):
            reg = load_registry(bdir)
            ident = identity_from_oauth(read_live_oauth_account())
            slot = find_slot_by_identity(reg, ident["email"], ident["orgUuid"])
            reg["active"] = int(slot) if slot else None
            save_registry(bdir, reg)
        return {"ok": True, "action": "track.sync", "active": reg["active"]}

    # set mode: a target token AND a new label/comment were supplied
    if args.target and (args.label or args.comment is not None):
        with registry_lock(bdir):
            reg = load_registry(bdir)
            slot = resolve_target(reg, args.target)
            acct = reg["accounts"][slot]
            if args.label:
                new_label = args.label
                conflict = label_conflict(reg, new_label, slot)
                if conflict:
                    raise SubsError(
                        "duplicate_label",
                        f"Label {new_label!r} is already used by slot {conflict}. "
                        "Pick a different label (labels must be unique).",
                    )
                old_backup = acct.get("backup")
                new_backup = f"{slot}-{slugify(new_label)}.json"
                if old_backup and old_backup != new_backup and (bdir / old_backup).exists():
                    (bdir / old_backup).rename(bdir / new_backup)
                    acct["backup"] = new_backup
                acct["label"] = new_label
            if args.comment is not None:
                acct["comment"] = args.comment
            acct["updated"] = now_iso()
            save_registry(bdir, reg)
        return {
            "ok": True,
            "action": "track.set",
            "slot": int(slot),
            "label": acct.get("label"),
            "comment": acct.get("comment", ""),
        }

    # list mode (read-only) — optionally filtered by a substring in args.target
    reg = load_registry(bdir)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    needle = (args.target or "").lower()
    accounts = []
    for num in sorted(reg["accounts"], key=lambda n: int(n)):
        a = reg["accounts"][num]
        if needle and not (
            needle in (a.get("label") or "").lower()
            or needle in (a.get("email") or "").lower()
            or needle == num
        ):
            continue
        tbk = read_json(bdir / a.get("backup", "")) if a.get("backup") else None
        exp = token_expiry(tbk.get("credentials") if tbk else None)
        if exp is None:
            token_state = "unknown"
        elif exp <= now_ms:
            token_state = "expired"
        else:
            token_state = "valid"
        accounts.append({
            "slot": int(num),
            "label": a.get("label"),
            "comment": a.get("comment", ""),
            "email": a.get("email"),
            "org": a.get("orgName") or ("personal" if not a.get("orgUuid") else a.get("orgUuid")),
            "active": reg.get("active") == int(num),
            "tokenState": token_state,
            "tokenExpiresAt": exp,
            "updated": a.get("updated"),
        })
    return {
        "ok": True,
        "action": "track.list",
        "active": reg.get("active"),
        "swapIdentity": reg.get("swapIdentity", False),
        "backupDir": str(bdir),
        "accounts": accounts,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="subs", description="Minimal Claude Code account switcher.")
    # NOTE: --json and --backup-dir are global and accepted in ANY position;
    # they are pre-extracted from argv in main() before this parser runs, so
    # both `subs --json track` and `subs track --json` work.

    def add_identity(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--identity", dest="identity", action="store_true", default=None,
                       help="also swap oauthAccount identity in ~/.claude.json")
        g.add_argument("--no-identity", dest="identity", action="store_false",
                       help="credentials.json only (default)")

    sub = p.add_subparsers(dest="command", required=True)

    sp_login = sub.add_parser("login", help="capture the current live login")
    sp_login.add_argument("tokens", nargs="*",
                          help="[label] [comment...]  or  key=value: label= email= comment= from=")
    sp_login.add_argument("--from", dest="from_path", default=None,
                          help="import credentials from a file instead of the live login "
                               "(does not change the active account)")
    add_identity(sp_login)

    sp_switch = sub.add_parser("switch", help="swap to a tracked account")
    sp_switch.add_argument("target", nargs="?", help="slot number, email, or label (empty = rotate)")
    add_identity(sp_switch)

    sp_pick = sub.add_parser("pick", help="interactive fuzzy-search account picker (terminal)")
    add_identity(sp_pick)

    sp_new = sub.add_parser("new", help="safely start adding a NEW account (backs up current, then guides)")
    sp_new.add_argument("label", nargs="?", help="intended label for the new account")

    sp_track = sub.add_parser("track", help="list or edit tracked accounts")
    sp_track.add_argument("target", nargs="?", help="slot/email/label to edit (omit to list)")
    sp_track.add_argument("label", nargs="?", help="new label when editing")
    sp_track.add_argument("comment", nargs="*", help="new comment when editing")
    sp_track.add_argument("--sync", action="store_true", help="re-derive active from live creds")

    return p


def parse_login_tokens(tokens, from_flag=None):
    """Flexible `login` args — both styles work and mix freely:

        subs login work "team plan"                     # positional
        subs login label=work comment="team plan"       # key=value
        subs login email=alice@x.com                    # label auto-derived
        subs login work email=alice@x.com "team plan"   # mixed

    Recognized keys: label=, email=, comment=, from=. Any bare token is
    positional: the first becomes the label (if none was given via label=), the
    rest join into the comment. Returns (label, email, comment, from_path).
    """
    label = email = from_path = None
    comment_parts, positional = [], []
    for t in tokens or []:
        k, sep, v = t.partition("=")
        if sep and k in ("label", "email", "comment", "from"):
            v = v.strip().strip('"').strip("'")
            if k == "label":
                label = v
            elif k == "email":
                email = v
            elif k == "from":
                from_path = v
            else:
                comment_parts.append(v)
        else:
            positional.append(t)
    if positional:
        if label is None:
            label, positional = positional[0], positional[1:]
        comment_parts = positional + comment_parts
    comment = " ".join(p for p in comment_parts if p) if comment_parts else None
    return label, email, comment, (from_flag or from_path)


def normalize_comment(value) -> str | None:
    """argparse nargs='*' gives a list; join to a string, or None if absent."""
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(value) if value else None
    return value


def extract_global_flags(argv: list[str]) -> tuple[list[str], bool, str | None]:
    """Pull --json / --backup-dir out of argv wherever they appear, so they are
    position-independent (before or after the subcommand)."""
    json_flag = False
    backup = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            json_flag = True
        elif a == "--backup-dir":
            backup = argv[i + 1] if i + 1 < len(argv) else None
            i += 1
        elif a.startswith("--backup-dir="):
            backup = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    return rest, json_flag, backup


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    cleaned, json_flag, backup = extract_global_flags(raw)
    parser = build_parser()
    args = parser.parse_args(cleaned)
    args.json = json_flag
    args.backup_dir = backup
    args.comment = normalize_comment(getattr(args, "comment", None))

    try:
        if args.command == "login":
            result = cmd_login(args)
        elif args.command == "switch":
            result = cmd_switch(args)
        elif args.command == "pick":
            result = cmd_pick(args)
        elif args.command == "new":
            result = cmd_new(args)
        elif args.command == "track":
            result = cmd_track(args)
        else:  # pragma: no cover
            raise SubsError("bad_command", f"Unknown command {args.command!r}")
    except SubsError as err:
        payload = {"error": {"type": err.type, "message": err.message}}
        if getattr(err, "candidates", None):
            payload["error"]["candidates"] = err.candidates
        if args.json:
            print(json.dumps(payload))
        else:
            print(f"error [{err.type}]: {err.message}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result))
    else:
        _print_human(result)
    return 0


def _md_table(rows: list[tuple]) -> str:
    """Render a padded Markdown table (aligned as plain text too)."""
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    def fmt(r):
        return "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)) + " |"
    header, *body = rows
    out = [fmt(header), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out += [fmt(r) for r in body]
    return "\n".join(out)


def _print_human(result: dict) -> None:
    action = result.get("action")

    if action == "track.list":
        accts = result["accounts"]
        if not accts:
            print("No tracked accounts yet — run:  subs login <label>")
            return
        rows = [("#", "Label", "Email", "Org", "", "Token", "Comment")]
        for a in accts:
            rows.append((
                a["slot"],
                a["label"] or "",
                a["email"] or "—",
                a["org"] or "",
                "▶ active" if a["active"] else "",
                a["tokenState"],
                a["comment"] or "",
            ))
        print(_md_table(rows))
        swap = "on" if result["swapIdentity"] else "off"
        print(f"\n_backups: {result['backupDir']} · identity-swap: {swap}_")
        return

    if action == "pick" and result.get("cancelled"):
        print(result["message"])
        return

    if action == "switch":
        if result.get("noop"):
            print(result["message"])
            return
        print(f"✅ Switched to [{result['slot']}] {result['label']} "
              f"<{result['email'] or '—'}> ({result['org']}).")
        if result.get("warning"):
            print(f"⚠ {result['warning']}")
        print(result["message"])
        return

    if action == "login":
        verb = "Tracked" if result.get("created") else "Refreshed"
        src = " (imported from file)" if result.get("imported") else ""
        print(f"✅ {verb} [{result['slot']}] {result['label']} "
              f"<{result['email'] or '—'}> ({result['org']}){src}. "
              f"{result['total']} account(s) total.")
        return

    if action == "new":
        c = result["current"]
        print(f"✅ Current account [{c['slot']}] {c['label']} backed up (safe).")
        print(result["message"])
        return

    if action == "track.set":
        print(f"✅ Updated [{result['slot']}]: label={result['label']!r}, "
              f"comment={result.get('comment', '')!r}")
        return

    if action == "track.sync":
        print(f"✅ Active re-derived from live credentials: slot {result.get('active')}")
        return

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
