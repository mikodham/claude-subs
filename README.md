# claude-subs

A tiny **Claude Code account switcher**, shipped as a plugin. Switch between
multiple Claude logins (Pro / Max / Team / enterprise / personal) without
re-running `/login` every time — by swapping the credential file in place.

This repository (`claude-subs`) is the container. The actual product lives in
[`subs/`](subs/) — the plugin you install, which gives you `/subs:*` commands.

```
claude-subs/                 ← this repo (the box)
├── subs/                    ← the plugin (the product)  →  see subs/README.md
│   ├── commands/            ← /subs:login, :switch, :track, :new
│   ├── hooks/               ← model-free in-chat execution
│   └── scripts/subs.py      ← the engine (stdlib Python, no deps)
└── .claude-plugin/
    └── marketplace.json     ← install source (claude-subs-marketplace)
```

## Quick start

On your Claude Code
```
/plugin marketplace add /path/to/claude-subs
/plugin install subs@claude-subs-marketplace
/reload-plugins
```

Then track your logins and switch between them — full usage is in
**[subs/README.md](subs/README.md)**, including the terminal integration
(`subs` picker), the model-free in-chat hook, and how adding accounts works.

## What it does, in one line

Claude Code keeps one active login in a fixed spot — `~/.claude/.credentials.json`
on Linux/WSL, the **login Keychain** on macOS. `subs` keeps per-account backups
of that blob and swaps them in and out (picking the right store per platform) —
the restored blob already holds valid tokens, so Claude Code just resumes. No
re-login. Works on Linux, WSL, and macOS.

## About this project

`claude-subs` is, honestly, **fully vibe-coded**: it was built through
conversation with an AI coding agent, with the author acting mainly as the
*philosopher and director* — shaping the idea, making the design calls,
questioning assumptions, and deciding what "good" looked like — rather than
hand-writing the implementation. It's an experiment in building software this
way, shared in that spirit. Rough edges are expected, and suggestions for making
it better are genuinely welcome.

> *"A man who has not hit his [Claude] usage limits by noon has wasted his
> morning."*
> — Aristotle (or Marcus Aurelius)

## Credits

The account-switching idea is inspired by
[**claude-swap** (`cswap`)](https://github.com/realiti4/claude-swap), a more
full-featured, cross-platform Python tool. `subs` reimplements a small, focused
subset of that idea directly inside a Claude Code plugin, so there's nothing
extra to install. Credit to that project for charting the approach.

## License

See the plugin directory. This is a personal project provided as-is.
