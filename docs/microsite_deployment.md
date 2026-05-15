# Outreach microsite deployment

## What this is

Every audited prospect can be turned into a private, unlisted microsite:

- Built by `agents.outreach.reporting.microsite_builder`
- Lives locally at `prospects/<id>/site/`
- Deploys to `https://yuvo-pitches.pages.dev/p/<prospect-slug>-<token>/`

The microsite is the same 8-slide creative note rendered by the deck
builder, plus a `manifest.json` carrying routing + provenance, an
`assets/` subfolder that owns the file references, and a
`<meta name="robots" content="noindex,nofollow">` tag in the head.

URLs are deliberately unguessable: the token is six random characters
from `[a-z0-9]`. That is ~2.2 billion possibilities — fine for
"private/unlisted" distribution; **not** a substitute for an auth
gate. Treat the URL as a bearer token.

## MVP: free Cloudflare Pages domain

For the MVP the deploy target is the **free** `pages.dev` URL — **no
custom domain is required**. The Cloudflare Pages project name doubles
as the subdomain:

| Setting | MVP value |
|---|---|
| Cloudflare Pages project | `yuvo-pitches` |
| Live URL | `https://yuvo-pitches.pages.dev/p/<slug>/` |
| `PITCH_BASE_URL` | `https://yuvo-pitches.pages.dev` |

You can change the Pages project name later; the prospect slug + token
stay stable because they are persisted in `manifest.json`.

### Custom domain (future option, not required)

When you are ready to register a domain (e.g. `pitch.yuvostudio.com`),
add it as a custom domain on the same Cloudflare Pages project, then
switch the env var:

```
# .env (today)
PITCH_BASE_URL=https://yuvo-pitches.pages.dev

# .env (after custom domain is live)
PITCH_BASE_URL=https://pitch.yuvostudio.com
```

The same `build/pitches/` deploy folder serves both URLs — only the
public-URL prefix in the manifest changes. No code change required.

## Required environment variables

Set these in `.env` (or your shell) before running the deploy script:

```ini
# Required for `wrangler pages deploy`
CLOUDFLARE_ACCOUNT_ID=...                          # Cloudflare dashboard > right sidebar
CLOUDFLARE_API_TOKEN=...                           # Pages:Edit + Account:Read scopes
CLOUDFLARE_PAGES_PROJECT=yuvo-pitches              # Pages project name

# Optional
PITCH_BASE_URL=https://yuvo-pitches.pages.dev      # default if unset
WRANGLER_BIN=                                      # Windows: see "Install wrangler" below
```

`CLOUDFLARE_API_TOKEN` is **never printed** by the deploy script. Wrangler
reads it from the child process env, so even the command line you see
in the deploy log carries no secret.

## Generating a microsite (local)

```powershell
py -3.11 -m agents.outreach.reporting.microsite_builder haeckels
```

That writes:

```
prospects/haeckels/site/
  index.html
  manifest.json
  assets/                  # copies of every image the deck references
```

The first run mints the token. Every subsequent run reads the existing
`manifest.json`, reuses the token, and refreshes the HTML + asset
copies. **The public URL stays stable across rebuilds.**

## Deploying to Cloudflare Pages

### Dry-run (safe, never contacts Cloudflare)

```powershell
py -3.11 scripts/deploy_pitch_microsite.py haeckels --dry-run
```

Prints the exact `wrangler` command that would run, including the
`build/pitches` deploy folder and the `--project-name=yuvo-pitches`
flag. No secrets are echoed; nothing is uploaded.

### Real deploy

```powershell
py -3.11 scripts/deploy_pitch_microsite.py haeckels
```

This:

1. Rebuilds `prospects/haeckels/site/` and `build/pitches/p/<slug>/`.
   The HTML rendered for a real (non-dry-run) deploy already carries
   the **Live** banner — the deploy script passes
   `intended_status="deployed"` into `build_microsite()` so the page
   that gets uploaded is the one the prospect should see. Dry-runs
   keep the amber Draft banner.
2. Runs `wrangler pages deploy build/pitches --project-name=yuvo-pitches --branch=main`.
   `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are passed via
   the child environment so they never appear on the command line.
3. On success, flips the manifest to:
   - `status = "deployed"`
   - `deployment_provider = "cloudflare_pages"`
   - `deployed_at = <UTC ISO-8601>`
   - `deployment_url = <wrangler-reported .pages.dev URL>`
4. Prints the final public URL.

Flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Print the command; don't contact Cloudflare. |
| `--skip-build` | Reuse the existing `build/pitches/p/<slug>/`. |
| `--branch <name>` | Cloudflare Pages branch / environment (default: `main`). |
| `--prospects-root <path>` | Override the `prospects/` root. |
| `--build-root <path>` | Override the `build/` root. |
| `-v / --verbose` | DEBUG-level logging. |

The script exits with:

| Code | Meaning |
|---|---|
| `0` | Deploy succeeded (or dry-run completed). |
| `2` | Required env var missing. |
| `3` | `--skip-build` but no existing manifest. |
| `4` | Deploy folder missing. |
| `5` | Wrangler failed (see `result.message`). |

## One-time Cloudflare Pages setup

1. Sign into the Cloudflare dashboard.
2. **Workers & Pages → Create application → Pages → Direct upload**.
3. Project name: `yuvo-pitches`. Click **Create project**.
4. Skip the first manual upload; the deploy script will push the
   first build.
5. **Cloudflare API token:**
   - **My Profile → API Tokens → Create Token → Custom token**.
   - Permissions: `Account → Cloudflare Pages → Edit` and
     `Account → Account Settings → Read`.
   - Account resources: `Include → <your account>`.
   - Copy the token and put it in `.env` as `CLOUDFLARE_API_TOKEN`.
6. **Account ID:** Cloudflare dashboard right sidebar → copy → put it
   in `.env` as `CLOUDFLARE_ACCOUNT_ID`.

You only do this once.

## Install wrangler

```powershell
npm install -g wrangler
```

The deploy script calls `wrangler pages deploy ...` via subprocess.

### Windows: set `WRANGLER_BIN`

On Windows, Python's `subprocess.run` uses `CreateProcess`, which does
**not** honour `PATHEXT`. A bare `"wrangler"` argv resolves to the
npm-installed shell shim (no extension), and `CreateProcess` refuses
to spawn it with:

```
[WinError 2] The system cannot find the file specified
```

The `.cmd` next to the shim does work. The deploy script tries
`shutil.which("wrangler.cmd")` first and falls back to
`shutil.which("wrangler")` — both honour `PATHEXT` — so on most
Windows installs the absolute path of `wrangler.cmd` resolves
without any extra setup. If your install lives somewhere unusual,
or if the auto-resolution does not pick the right binary, set the
full path explicitly:

```ini
# .env (Windows)
WRANGLER_BIN=C:/Users/<you>/AppData/Roaming/npm/wrangler.cmd
```

On POSIX (`macOS`, `Linux`), leave `WRANGLER_BIN` unset; bare
`wrangler` works because the shell shim is directly executable.

### Safe UTF-8 decoding of wrangler output

`wrangler pages deploy` prints UTF-8 box-drawing characters and
emoji in its progress output. The deploy script reads the
subprocess streams with `encoding="utf-8", errors="replace"` so a
noisy line on Windows (where the platform default is `cp1252`)
never crashes the capture with `UnicodeDecodeError`. The
`deployment_url` parser is also resilient to replacement
characters in the surrounding lines, so the `.pages.dev` URL is
still extracted even when other characters in the output were
unrepresentable.

### First deploy already ships the Live banner

The deploy script passes `intended_status="deployed"` into
`build_microsite()` for a real (non-dry-run) deploy. That means
the HTML uploaded to Cloudflare on the **first** deploy already
carries the green Live banner instead of the amber Draft banner —
no second deploy is needed to swap it. Dry-runs deliberately keep
`intended_status` unset so the local preview operators eyeball
before deploying still shows Draft.

On a real deploy the manifest only flips to `status="deployed"`
**after** wrangler reports success: `mark_manifest_deployed()`
stamps `deployed_at`, `deployment_provider`, and the wrangler-
reported `deployment_url`. So if the upload fails, the on-disk
manifest stays honest about the public URL not yet being live.

## Interest form

Every microsite carries a closing **"Want the first route?"** section
with a name / email / preferred-times / message form. The form has two
operating modes; the active mode is decided at build time by the env.

### MVP mode: `mailto:` fallback

When `PITCH_FORM_ENDPOINT` is unset (the default today), the form
renders as a polished card whose submit button is a `mailto:` link
pre-addressed to the configured contact email. Clicking the button
opens the prospect's email client with:

- Subject: `Interested in the first route for <Brand>`
- Body: pre-filled `Brand: / Prospect ID: / Private slug: / Public URL:`
  identifiers plus blank `Name / Email / Preferred call times / Message`
  lines for the prospect to fill in.

The only secret-shaped value in the HTML is the destination email,
which is **public by design** (it is just a mailbox we already own).
**No API token or credential ever ships in the HTML.**

Configure the destination:

```ini
# .env
PITCH_CONTACT_EMAIL=hello@yuvostudio.com   # defaults to this if unset
```

This works **today** with no Cloudflare configuration. Until we
register a domain and set up Email Routing, this is the path we use.

### Future mode: Cloudflare endpoint POST

Once a backend endpoint exists at `/api/interest`, switch the form to
real POST mode by setting:

```ini
# .env
PITCH_FORM_ENDPOINT=/api/interest
```

When this is set, the form renders as a real
`<form method="POST" action="/api/interest">` with hidden
`prospect_id`, `private_slug`, `brand_name`, and `public_url` fields,
plus a helper note that the submission "submits securely through our
Cloudflare form endpoint". The mailto fallback is suppressed.

#### Building the endpoint (separate task)

The frontend is future-ready, but the backend endpoint is **not**
shipped in this repo yet. Two paths are open when you build it:

1. **Cloudflare Pages Function**: add `functions/api/interest.ts` to
   the Pages project. Receives the form POST, validates fields,
   and forwards via:
   - **Cloudflare Email Routing `send_email` binding** - requires a
     verified destination address on a verified zone. Today we have
     neither, so this path is not yet usable.
   - **Resend** (or any HTTP email API) - simpler to start with: store
     the Resend API key as a Pages secret and call their REST API from
     the function. No DNS work required.
2. **Cloudflare Worker**: same idea, decoupled from the Pages project.
   Useful if you want one shared endpoint across multiple microsites.

In all cases the credential lives **only** in the Pages/Worker
environment - the HTML carries no token. Until the endpoint is live,
leave `PITCH_FORM_ENDPOINT` unset and the deck stays in mailto mode.

> **We deliberately do not claim "automatic email" anywhere in the
> deck until the backend endpoint is actually deployed and we have
> tested it end-to-end with a real send.**

## robots.txt

Add a single `robots.txt` at the deployment root before going live:

```
User-agent: *
Disallow: /
```

This complements the per-page `noindex` meta tag and stops crawlers
from indexing the bare prospect URLs.

## No public index

Do **not** publish an index of prospects — neither at the root of
`yuvo-pitches.pages.dev` nor anywhere on `yuvostudio.com`. The point of
the private-token system is that the URL is the only handle. The repo
hygiene check (`scripts/check_repo_hygiene.py --tracked`) refuses to
ship a tracked `index.html` listing prospects.

## Manual review before sending

The manifest carries `"status": "draft"` and `"review_required": true`
by default. The operator workflow is:

1. Run `build_microsite()` (or `deploy_pitch_microsite.py --dry-run`).
2. Open the local `prospects/<id>/site/index.html` in a browser; check
   the deck, ad proof links, and (if present) the watermarked preview
   video.
3. Run `deploy_pitch_microsite.py <prospect_id>` — this is the only
   step that hits Cloudflare. After it returns, `status` is flipped to
   `"deployed"`.
4. Manually share the manifest's `public_url` with the prospect.

Nothing in the codebase auto-sends a microsite URL today. That gate
exists for a reason — keep it manual.

## Watermarked preview videos

Microsite preview videos are **opt-in** and **watermarked**:

- The builder auto-detects `prospects/<id>/assets/preview_watermarked.mp4`.
- A bare `preview.mp4` (without `_watermarked`) is logged and ignored.
  This is intentional — never upload an unwatermarked cut to a
  public URL.
- The preview slide carries explicit copy that the video is
  watermarked, with a "Want the clean version?" CTA so the prospect
  knows to reply for the unbranded MP4.

### File size

Cloudflare Pages serves static assets up to ~25 MB without complaint.
Anything bigger triggers a `warnings` entry in the manifest. Compress
to **under 25 MB** before deploying — 720p, 8-12 Mbps, MP4 (H.264 +
AAC) is the sweet spot for a 15-second preview.

A future helper (`microsite_builder.watermark_preview_video`) will
produce the watermarked MP4 from a raw `preview.mp4` via ffmpeg. Until
that ships, watermark manually (CapCut / Premiere / ffmpeg one-liner:
`ffmpeg -i preview.mp4 -vf "drawtext=text='YUVO PREVIEW':fontsize=36:
fontcolor=white@0.6:x=20:y=H-th-20" -c:a copy preview_watermarked.mp4`).

## Manifest schema

Every microsite emits `prospects/<id>/site/manifest.json` with:

| Field | Type | Notes |
|---|---|---|
| `prospect_id` | str | Stable internal ID (matches `prospects/<id>/`). |
| `brand_name` | str | Display name from the audit. |
| `private_slug` | str | `<brand-slug>-<6char-token>`. |
| `local_path` | str (abs) | Absolute path to `site/index.html`. |
| `public_url` | str | Full URL on the active `PITCH_BASE_URL`. |
| `status` | str | `"draft"` until the deploy script flips it to `"deployed"`. |
| `review_required` | bool | Always `true` on first build. |
| `created_at` | ISO-8601 | Sticky across rebuilds. |
| `updated_at` | ISO-8601 | Refreshed on every build. |
| `assets_used` | list[str] | Relative paths inside `site/`. |
| `open_ad_links` | list[str] | Public Meta Ads Library URLs. |
| `website_url` | str / null | Prospect's homepage. |
| `preview_video_path` | str / absent | Relative path; only when a watermarked preview exists. |
| `watermarked_video` | bool / absent | `true` when the preview is embedded. |
| `preview_video_bytes` | int / absent | Useful for the file-size warning. |
| `warnings` | list[str] / absent | Soft alerts (oversize video, etc.). |
| `deployment_provider` | str / absent | `"cloudflare_pages"` after a real deploy. |
| `deployed_at` | ISO-8601 / absent | When the deploy script last succeeded. |
| `deployment_url` | str / absent | Wrangler-reported `.pages.dev` URL (transient per-deploy). |

The manifest is the single source of truth for the public URL — never
hand-edit the slug or token there. Re-run `build_microsite()` if you
need a fresh token (deleting the existing manifest first).
