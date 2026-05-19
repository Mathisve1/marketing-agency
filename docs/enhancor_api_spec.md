# Enhancor API — structured spec

**Source of truth:** [`enhancor_dashboard_raw.md`](enhancor_dashboard_raw.md).
Anything not preserved there is marked `UNKNOWN / NEEDS TEST` and must be
captured from the authenticated dashboard before code depends on it.

**Last updated:** 2026-05-16.

**Provider abstraction (added 2026-05-16):** the wire-protocol details
below are wrapped by a single `Provider` protocol in
[`agents/producer/providers/base.py`](../agents/producer/providers/base.py)
with concrete adapters at
[`enhancor_seedance.py`](../agents/producer/providers/enhancor_seedance.py)
and
[`enhancor_audio_fixer.py`](../agents/producer/providers/enhancor_audio_fixer.py).
Every adapter shares the same `ProviderJobRequest` / `ProviderJobResponse`
/ `ProviderJobStatus` / `ProviderGenerationResult` / `ProviderError`
shapes and the same 5-state `ProviderStatus` enum (`QUEUED`,
`IN_PROGRESS`, `COMPLETED`, `FAILED`, `UNKNOWN`). Callers depend on the
protocol; the concrete provider stays swappable. The adapters preserve
raw provider responses verbatim so undocumented fields (e.g. the
Seedance `thumbnail` field below) remain discoverable without rerunning
paid jobs. **Adapters MUST NOT assume a specific CDN host on `result`
URLs** — Seedance currently emits CloudFront, the Audio Fixer emits
`v3b.fal.media`, future providers will emit other hosts.

This file holds the structured per-endpoint contract. Three APIs are
covered with enough detail to write a smoke test against them today:

- A. **Seedance 2.0 Full Access** — primary video generator.
- B. **UGC Audio Fixer** — post-generation audio repair.
- C. **Enhancor Video Pro** — close cousin of A; documented for diff reference.

Three additional APIs are referenced for completeness but flagged
`NEEDS DASHBOARD COPY` until the operator pastes their exact shapes:

- D. **Enhancor Video Full Access — Text to Video Standard**
- E. **Enhancor Text to Image Full Access**
- F. **Enhancor Image Editor Full Access**

Plus G. **Skin Enhancor V4** as an already-public supporting API.

---

## A. Seedance 2.0 Full Access API

| Field | Value |
|---|---|
| API name | Seedance 2.0 Full Access |
| Base URL | `https://apireq.enhancor.ai/api/enhancor-ugc-full-access/v1` |
| Auth method | header `x-api-key: <ENHANCOR_API_KEY>` |
| Queue endpoint | `POST /queue` |
| Status endpoint | `POST /status` |
| Content-Type | `application/json` (assumed; **NEEDS TEST** for any odd content types) |
| Idempotency key header | UNKNOWN / NEEDS TEST |

### Required request fields

| Field | Type | Notes |
|---|---|---|
| `type` | string | `text-to-video` \| `image-to-video` |
| `prompt` | string | required for every call |
| `webhook_url` | string (URL) | **mandatory on every queue submission** |
| `duration` | string | one of `"4"`..`"15"`, omitted for `multi_frame` |
| `resolution` | string | `"480p"` \| `"720p"` \| `"1080p"` |
| `aspect_ratio` | string | one of `"16:9"`, `"9:16"`, `"4:3"`, `"3:4"`, `"1:1"`, `"21:9"` |

### Conditional / mode-specific fields

| Field | Required when | Notes |
|---|---|---|
| `mode` | `type=image-to-video` | one of `ugc`, `multi_reference`, `extend`, `multi_frame`, `lipsyncing`, `voice_clone`, `first_n_last_frames` |
| `full_access` | `true` whenever the generation will show a human face | boolean |
| `fast_mode` | optional; **must be `false` if `resolution=1080p`** | boolean |
| `products` | `mode=ugc` | array of public image URLs; counts toward the ≤9 limit |
| `influencers` | `mode=ugc` | array of public image URLs; counts toward the ≤9 limit |
| `images` | `multi_reference`, `extend`, `first_n_last_frames` (any mode that benefits from image references) | array of public image URLs; max 9 standalone or combined with `products`+`influencers` in UGC |
| `videos` | `extend`, `multi_reference`, `lipsyncing`, `voice_clone` (whenever video refs apply) | array of public video URLs; max 3; combined duration < 15 s |
| `audios` | `lipsyncing`, `voice_clone`, `multi_reference` (whenever audio refs apply) | array of public audio URLs; max 3; combined duration < 15 s |
| `multi_frame_prompts` | `mode=multi_frame` | array of per-frame prompt blocks. **Top-level `duration` is omitted; the effective duration is the sum across this array.** |

### Forbidden combinations

- `type=text-to-video` MUST NOT include `images`, `videos`, `audios`,
  `products`, or `influencers`.
- `resolution=1080p` MUST NOT be combined with `fast_mode=true`.
- `images.length` MUST be ≤ 9.
- `videos.length` MUST be ≤ 3 AND combined duration < 15 s.
- `audios.length` MUST be ≤ 3 AND combined duration < 15 s.
- In `mode=ugc`: `len(products) + len(influencers) + len(images)` MUST be ≤ 9.

### Optional fields — UNKNOWN / NEEDS DASHBOARD COPY

- `seed`, `fps`, `cfg_scale`, regional hint, voice id (for `voice_clone`),
  per-segment timing inside `multi_frame_prompts`, `keep_original_sound`
  on video refs, output-resolution hints, callback-secret token field.

### Request example — `text-to-video`

```json
{
  "type": "text-to-video",
  "prompt": "A simple product bottle on a neutral background, soft daylight, short cinematic test.",
  "webhook_url": "https://example.com/webhooks/enhancor",
  "duration": "4",
  "resolution": "480p",
  "aspect_ratio": "9:16",
  "fast_mode": true
}
```

### Request example — `image-to-video` / `ugc`

```json
{
  "type": "image-to-video",
  "mode": "ugc",
  "prompt": "A real woman in soft daylight holding the product near her shoulder, talking calmly.",
  "webhook_url": "https://example.com/webhooks/enhancor",
  "duration": "5",
  "resolution": "480p",
  "aspect_ratio": "9:16",
  "fast_mode": true,
  "full_access": true,
  "products": ["https://example.com/product.jpg"],
  "influencers": ["https://example.com/influencer.jpg"]
}
```

### Request example — `image-to-video` / `multi_reference`

```json
{
  "type": "image-to-video",
  "mode": "multi_reference",
  "prompt": "Pacing reference applied to the provided product image with calm UGC framing.",
  "webhook_url": "https://example.com/webhooks/enhancor",
  "duration": "5",
  "resolution": "480p",
  "aspect_ratio": "9:16",
  "fast_mode": true,
  "images": ["https://example.com/product.jpg"],
  "videos": ["https://example.com/motion-reference-9s.mp4"]
}
```

### Response — queue submission

```json
{ "success": true, "requestId": "<opaque-string>" }
```

Any additional fields (cost, eta) surfaced by the dashboard: UNKNOWN / NEEDS TEST.

### Response — `/status`

Confirmed shape (smoke test 2026-05-16, requestId `6a0850e96c164b8f24cb7d05`):

```json
{
  "success": true,
  "requestId": "<opaque-string>",
  "status": "IN_PROGRESS | COMPLETED | ...",
  "result": "<CloudFront mp4 URL when terminal-success>",
  "thumbnail": "<CloudFront webp URL when terminal-success>",
  "cost": 264
}
```

**Confirmed via smoke test 2026-05-16 (text-to-video, requestId `6a0850e96c164b8f24cb7d05`):**

- `/queue` accepted the cheap text-to-video payload (`type=text-to-video`,
  `duration="4"`, `resolution="480p"`, `aspect_ratio="9:16"`, `fast_mode=true`)
  and returned HTTP 200 `{ "success": true, "requestId": "..." }`.
- `/status` returned `IN_PROGRESS` while in flight and `COMPLETED` on terminal
  success.
- The completed payload carries an undocumented **`thumbnail`** field (a
  CloudFront `.webp`) in addition to `result` (a CloudFront `.mp4`). Useful for
  review-page posters without re-encoding the mp4.
- `cost` echoed as the integer `264`; **units still UNKNOWN** (credits /
  sub-cents / proprietary) — confirm against the dashboard billing page.
- `result` is a CloudFront URL; the download succeeded immediately.
- **Output URL TTL still UNKNOWN.** The conservative product rule is unchanged:
  mirror the `result` to Supabase Storage on terminal-success, never link
  to the CloudFront URL from any operator-facing surface.
- **Text-to-video output is silent (no audio track).** Audio Fixer is therefore
  not a no-op on t2v: it adds a synthesised audio track. Validate downstream
  whether the synthesised track has value for silent ads or whether t2v outputs
  should bypass Audio Fixer.

**Confirmed via UGC smoke test 2026-05-16 (image-to-video / `mode=ugc`,
requestId `6a08562a60cece3ba3062062`):**

- Payload that worked:
  ```json
  {
    "type": "image-to-video",
    "mode": "ugc",
    "prompt": "<short UGC prompt>",
    "webhook_url": "https://...",
    "duration": "5",
    "resolution": "480p",
    "aspect_ratio": "9:16",
    "fast_mode": true,
    "full_access": true,
    "products": ["<public HTTPS URL of Pai packshot>"],
    "influencers": ["<public HTTPS URL of synthetic StyleGAN face>"]
  }
  ```
- `/queue` returned HTTP 200 `{ "success": true, "requestId": "..." }` —
  exactly the same shape as text-to-video.
- `/status` walked `IN_PROGRESS ×7 → COMPLETED` over ~2 min wall-clock.
- Completed payload shape **identical** to the text-to-video case
  (`requestId / status / result / thumbnail / cost`); the `thumbnail` field is
  the right place to grab a poster frame that already shows the influencer +
  product framed correctly.
- `cost` echoed as the integer `330` (compare text-to-video `264` for 4 s at
  480p; UGC at 5 s at 480p = 330, scaling roughly with duration when other
  knobs match).
- **Output is NOT silent — UGC mp4 contains 1 video track + 1 audio track**
  (parsed via mp4 `hdlr` atom walk). Seedance generates audio for UGC mode
  natively. Implication: Audio Fixer becomes a cleanup pass, not a "add audio
  where there is none" pass.
- **Both references were honoured:** the thumbnail clearly shows the same
  StyleGAN face from the influencer URL holding the Pai bottle from the product
  URL with the "pai" wordmark legible. The model honoured the synthetic
  face-identity AND the product packaging in one call.
- `full_access: true` was sent and accepted with no rejection — confirms the
  human-face flag is correctly required and accepted for UGC.

**Provider-layer surface (2026-05-16):** the UGC native-audio finding
is exposed on the Seedance adapter as the named constant
`UGC_OUTPUT_INCLUDES_NATIVE_AUDIO = True`; the t2v-silent finding as
`TEXT_TO_VIDEO_OUTPUT_IS_SILENT = True`; `multi_reference` audio
behaviour is `MULTI_REFERENCE_OUTPUT_AUDIO_BEHAVIOUR = "unknown"`.
Downstream callers (cost ledger, Audio Fixer chaining, dashboard)
branch on these constants rather than scraping docstrings.

Failure-side enum values (the exact strings emitted on a real failure) are
still UNKNOWN / NEEDS TEST. The smoke test classifies any string containing
`complet` as terminal-success and any string containing `fail` / `error` as
terminal-failure; everything else is in-flight.

Failure-side enum values (the exact strings emitted on a real failure) are
still UNKNOWN / NEEDS TEST. The smoke test classifies any string containing
`complet` as terminal-success and any string containing `fail` / `error` as
terminal-failure; everything else is in-flight.

### Webhook payload

Conservative baseline mirroring the public skin-endpoint webhook:

```json
{
  "request_id": "<opaque-string>",
  "status": "COMPLETED" | "FAILED" | "...",
  "result": "<URL when terminal-success>",
  "cost": <number when terminal-success>
}
```

- Webhook callbacks may arrive **more than once** — dedupe by `request_id`
  plus terminal `status`.
- Signature header / shared-secret: UNKNOWN / NEEDS TEST.
- Retry policy: UNKNOWN / NEEDS TEST.
- `User-Agent`: UNKNOWN / NEEDS TEST.

### Media requirements

| Constraint | Value | Source |
|---|---|---|
| Max `duration` | 15 s | dashboard |
| Min `duration` | 4 s | dashboard |
| Resolution options | `480p`, `720p`, `1080p` | dashboard |
| Aspect-ratio options | `16:9`, `9:16`, `4:3`, `3:4`, `1:1`, `21:9` | dashboard |
| `1080p` ⇔ `fast_mode=false` | enforced | dashboard |
| Max `images` | 9 | dashboard |
| Max `videos` | 3 (combined < 15 s) | dashboard |
| Max `audios` | 3 (combined < 15 s) | dashboard |
| Max `products+influencers+images` in UGC | 9 | dashboard |
| Allowed image / video / audio MIME types | UNKNOWN / NEEDS TEST | needs paste |
| Max file size per asset | UNKNOWN / NEEDS TEST | needs paste |
| Input as base64 vs public URL only | UNKNOWN / NEEDS TEST (the smoke test always uses public URLs as the safe default) | needs paste |
| Output URL TTL | UNKNOWN / NEEDS TEST | needs paste |

### Cost / credits

Surfaced on the `/status` and (presumably) webhook payloads as `cost`. The
per-second unit cost and currency: UNKNOWN / NEEDS TEST.

### Recommended quality tiers

A real Pai 15 s 1080p generation in May 2026 cost **5 940** credits on the
raw Seedance pass and **2 103.75** credits on the follow-up Audio Fixer
pass (total **≈ 8 043.75** credits for a single 15 s deliverable). At that
price point we do not want 1080p as the default and we do not want
automatic Audio Fixer. The product surfaces three tiers:

| Tier | Resolution | Per-second cost (no video input) | 15 s cost | Use case | Audio Fixer |
|---|---|---|---|---|---|
| **Draft / test** | `480p` | 82 credits/s | **1 230 credits** | Cheap prompt-iteration runs while a concept is being shaped. Never shared with the client. | manual only |
| **Standard / client preview** *(default)* | `720p` | 176.4 credits/s | **2 646 credits** | Default for real UGC work and for the first cut shared with the client. | manual only |
| **Premium / final** | `1080p` | 396 credits/s | **5 940 credits** | Operator must explicitly opt-in. May read as "too AI-polished" for UGC; review the 720p take first. | manual only |
| **Audio Fixer** | n/a | n/a | **≈ 2 100 credits** *(Pai 15 s reference)* | Operator runs only after the raw audio has been reviewed and judged worth fixing. | n/a |

**Hard rules in the UI:**

- 720p is the default tier on every "Generate" action.
- 1080p requires explicit selection plus a visible cost-warning chip.
- Audio Fixer is never automatic — every Audio Fixer submit is a separate
  explicit operator action with its own ≈ 2 100-credit estimate displayed.
- Cost estimates above are floors; `cost` echoed by the provider on
  `/status` and the webhook is the source of truth for the cost ledger.

---

## B. UGC Audio Fixer API

| Field | Value |
|---|---|
| API name | UGC Audio Fixer |
| Base URL | `https://apireq.enhancor.ai/api/fix-audio/v1` |
| Auth | header `x-api-key: <ENHANCOR_API_KEY>` |
| Queue endpoint | `POST /queue` |
| Status endpoint | `POST /status` |
| Content-Type | `application/json` |

### Required request fields

| Field | Type | Notes |
|---|---|---|
| `inputVideo` | string (URL) | the raw mp4 to repair |
| `webhook_url` | string (URL) | mandatory |

### Response — queue submission

```json
{ "success": true, "requestId": "<opaque-string>" }
```

### Response — `/status` (completed)

```json
{
  "requestId": "<opaque-string>",
  "status": "COMPLETED",
  "result": "<URL of audio-fixed video>",
  "cost": <number>
}
```

### Confirmed behaviour (smoke 2026-05-16, requestId `6a0852fcdb43fe5882998b35`)

- `/queue` accepted the Seedance terminal-success `result` URL **directly**
  as `inputVideo` — no operator-side mirroring step required between the
  two providers.
- `/status` walked `PENDING → COMPLETED`; the terminal payload carries
  `result`, `status` and `cost` but no `thumbnail` (Audio Fixer does not
  emit a poster frame).
- **Result URL host is NOT CloudFront** — Audio Fixer emits
  `v3b.fal.media/...`. Adapters MUST NOT discriminate by CDN host.
- **Output is a single fully-muxed mp4** (`*_combined_output.mp4`,
  observed 347,804 B for a ~4 s input; one video track + one audio
  track per `mp4 hdlr` atom walk). The downstream consumer gets one
  artefact, not a separate audio sidecar.
- `cost` echoed as the integer `561`. **Per-second pricing notice:**
  Audio Fixer is meaningfully more expensive per second of input than
  Seedance generation in current smoke data (Audio Fixer `561` on ~4 s
  vs Seedance t2v `264` on the same 4 s, ~2.1×). Plan the cost ledger
  to track both providers' `cost` fields independently; do not assume
  the Audio Fixer is "free" because it's a post-pass.
- Provider-layer surface (2026-05-16): the
  [`EnhancorAudioFixerProvider`](../agents/producer/providers/enhancor_audio_fixer.py)
  adapter exposes `submit_audio_fix(input_video_url=..., webhook_url=...)`
  which builds the payload and submits in one call; the same
  `Provider` protocol as Seedance.

### Error codes (documented in the dashboard)

| Code | Documented? | Treatment in smoke test |
|---|---|---|
| `400` | yes | invalid payload — abort, do not retry |
| `401` | yes | auth failure — print key fingerprint (never the key), abort |
| `403` | yes | forbidden / unauthorized for the endpoint — abort, surface to operator |
| `500` | yes | provider error — retry once with backoff, then surface |

Per-code message text: UNKNOWN / NEEDS TEST.

### Unknowns

- Whether the Audio Fixer accepts the Seedance `result` URL directly or
  requires the operator to mirror the file (UNKNOWN; the smoke test assumes
  direct URL hand-off and documents this as a NEEDS TEST item).
- Output format (audio-mux'd mp4 vs audio-only stream): UNKNOWN.
- Webhook payload field names: assumed to mirror the Seedance pattern
  (`request_id`, `status`, `result`, `cost`) but UNKNOWN until tested.

---

## C. Enhancor Video Pro API

| Field | Value |
|---|---|
| API name | Enhancor Video Pro |
| Base URL | `https://apireq.enhancor.ai/api/enhancor-video-pro/v1` |
| Auth | header `x-api-key` |
| Endpoints | presumed `POST /queue` + `POST /status` (Enhancor-wide pattern) |
| Key delta vs Seedance Full Access | uses **`is_uncensored`** boolean instead of **`full_access`** |
| Modes | same family as Seedance Full Access (UNKNOWN whether identical) |
| Async pattern | same as Seedance (queue → webhook + status poll) |

Detailed request fields, status enum, webhook payload: UNKNOWN / NEEDS
DASHBOARD COPY. The Phase-0 smoke test does NOT target this endpoint; if we
later want a side-by-side A/B with Seedance Full Access, this is the place
to wire it after pasting the dashboard contents.

---

## D. Enhancor Video Full Access — Text to Video Standard

| Field | Value |
|---|---|
| Base URL | `https://apireq.enhancor.ai/api/enhancor-video-full-access/v1` |
| Auth | header `x-api-key` (Enhancor-wide convention) |
| Endpoints | presumed `POST /queue` + `POST /status` — UNKNOWN until pasted |
| Request fields | UNKNOWN / NEEDS DASHBOARD COPY |
| Status enum | UNKNOWN |
| Webhook payload | UNKNOWN |
| Phase-0 smoke target | no |

This is a separate surface from the Seedance Full Access API. Treat it as a
candidate adapter only after the dashboard contents are pasted.

---

## E. Enhancor Text to Image Full Access API

| Field | Value |
|---|---|
| Base URL | UNKNOWN / NEEDS DASHBOARD COPY |
| Auth | header `x-api-key` (Enhancor-wide convention) |
| Endpoints | UNKNOWN |
| Request fields | UNKNOWN |
| Status enum | UNKNOWN |
| Webhook payload | UNKNOWN |
| Phase-0 smoke target | no |

---

## F. Enhancor Image Editor Full Access API

| Field | Value |
|---|---|
| Base URL | UNKNOWN / NEEDS DASHBOARD COPY |
| Auth | header `x-api-key` (Enhancor-wide convention) |
| Endpoints | UNKNOWN |
| Request fields | UNKNOWN |
| Status enum | UNKNOWN |
| Webhook payload | UNKNOWN |
| Phase-0 smoke target | no |

---

## G. Skin Enhancor API V4 (supporting reference)

| Field | Value |
|---|---|
| Base URL | `https://apireq.enhancor.ai/api/realistic-skin/v1` (V1/V3 confirmed publicly; V4 deltas UNKNOWN / NEEDS DASHBOARD COPY) |
| Auth | header `x-api-key` |
| Endpoints | `POST /queue`, `POST /status` |
| Webhook payload | `{ request_id, result, status: "success" }` (publicly documented) |
| Status enum | `PENDING`, `IN_QUEUE`, `IN_PROGRESS`, `COMPLETED`, `FAILED` |
| Phase-0 smoke target | no |

Useful as the **status-enum baseline** the smoke test falls back on when the
Seedance video endpoint's exact enum is not yet pasted.
