# Repository Security & Data-Safety Guide

This repo runs a multi-tenant performance marketing agency. Every commit
to a public mirror is irreversible — assume anything you push will be
indexed by search engines and pulled into someone else's training set.

## What MUST NEVER be committed

| Path | Why |
|---|---|
| `.env` and `clients/*/.env` | API keys for Anthropic, Tavily, Apify, Kling, Meta. |
| `clients/*/client_data.db*` | Client's winning hooks, motions, negative constraints. Competitive intelligence. |
| `clients/*/performance_log.json` | Kling task IDs, asset paths, render history per client. |
| `clients/*/references/` | Customer-supplied character/product imagery — may contain PII (faces) or pre-launch product designs. |
| `clients/*/outputs/` | Generated MP4s and PDF reports — large + commercially sensitive. |
| `prospects/` | Scraped Meta ad libraries + pitch PDFs for cold-outreach targets. Your sales pipeline. |
| `logs/kling-api.jsonl` | Full Kling request/response bodies including prompts and Meta ad copy. |

All of the above are covered by `.gitignore`. If you add a new runtime
output kind, **update `.gitignore` in the same commit**.

## What IS safe to commit

- `clients/_template/` — the seed template cloned by `ClientContext.onboard`.
  Contains `MASTER_CONTEXT.md` placeholder, `performance_log.json` with `[]`,
  and a `.env.example`. **Never put real client data here.**
- `.env.example` — keys with empty values only.
- Everything under `agents/`, `core/`, `ui/`, `tests/`, `scripts/`.

## Pre-push checklist

Run before every push, especially after a long agent session:

```bash
git status
# Look for any path under prospects/, logs/, clients/<real-id>/.
# If you see one, do NOT proceed — add it to .gitignore first.

git diff --cached --stat
# Confirm only source-code paths appear in the diff.
```

## On a fresh clone

```bash
cp .env.example .env        # then fill in your keys
python scripts/bootstrap_env.py   # ensures template subdirs exist
```

`ClientContext.onboard()` re-creates required subdirectories per silo, so
the bootstrap script is defensive — not strictly required for onboarding —
but recommended after a fresh clone.

## Path-traversal protection

Both `client_id` and `prospect_id` are validated against
`^[a-z0-9][a-z0-9_-]{0,63}$` in `core/client_context.py` and
`agents/outreach/prospect_store.py`. This rejects `..`, `/`, `\`,
uppercase, and any leading symbol — preventing user input from escaping
the `clients/` or `prospects/` root. **Do not loosen this regex.**

## If you suspect a leak

1. Stop pushing.
2. Rotate the affected API key immediately at the provider console.
3. If a customer asset was committed: `git rm` it, force-push the cleaned
   history, *and* contact the customer. Force-push only after rotating
   keys — anyone who already pulled the leaked commit still has the data.
4. File an internal incident note (date, commit SHA, what leaked, who
   was notified, what was rotated).

## Known accepted risks (not addressed in this pass)

- **MCP `_PENDING_RUNS` registry is in-memory only.** A process restart
  loses paused HITL checkpoints. Acceptable for single-operator MVP.
- **Kling JWTs are minted in-process.** Never logged (the audit-log code
  redacts `Authorization` headers by not capturing them). If you add HTTP
  request logging in the future, re-verify nothing in the headers leaks.
- **No secret-scanning in CI.** Add `gitleaks` or GitHub's secret scanning
  as a follow-up if the repo goes public.
