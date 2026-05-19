# Enhancor dashboard — raw API documentation

**Source:** `https://app.enhancor.ai/api-dashboard` (authenticated; copied/described
by the operator into this file on 2026-05-16).

**Preservation policy:** this file preserves the structured facts the operator
extracted from the authenticated dashboard. It is the only canonical source we
trust for endpoint shapes; any other Enhancor / Seedance reference (third-party
wrappers, blog posts, the public marketing page) is **not** authoritative.

Anything not explicitly listed below is marked **UNKNOWN / NEEDS TEST** in the
companion [`enhancor_api_spec.md`](enhancor_api_spec.md) and
[`enhancor_capability_matrix.md`](enhancor_capability_matrix.md).

---

## 1. Skin Enhancor API V4

Confirmed elsewhere (public GitHub mirror, `rohan-kulkarni-25/enhancor-api-docs`):

- POST `https://apireq.enhancor.ai/api/realistic-skin/v1/queue`
- POST `https://apireq.enhancor.ai/api/realistic-skin/v1/status`
- Auth: header `x-api-key`
- Request: `img_url`, `webhookUrl`, `model_version` (`enhancorv1` | `enhancorv3`),
  `enhancementMode` (`standard` | `heavy`), `enhancementType` (`face` | `body`),
  area-mask flags, `output_resolution` 1024-3072, `skin_refinement_level` 0-100, etc.
- Response: `{success: bool, requestId: string}`
- Webhook payload: `{request_id, result, status}`
- Status enum: `PENDING`, `IN_QUEUE`, `IN_PROGRESS`, `COMPLETED`, `FAILED`
- Dashboard surface for **V4** specifically: any deltas from V1/V3 are
  **UNKNOWN / NEEDS DASHBOARD COPY**. Treat the V3-confirmed schema as the
  conservative baseline.

---

## 2. Enhancor Video Full Access API — Text to Video Standard

- Base URL: **`https://apireq.enhancor.ai/api/enhancor-video-full-access/v1`**
- Endpoints: presumed `POST /queue` + `POST /status` based on the
  Enhancor-wide async pattern; exact paths **UNKNOWN / NEEDS DASHBOARD COPY**.
- Auth: header `x-api-key` (Enhancor-wide convention).
- Request body, status enum, webhook payload: **UNKNOWN / NEEDS DASHBOARD COPY**.

This is a separate surface from the Seedance Full Access API (§ 5/6). Until the
dashboard contents for this endpoint are pasted, the Phase-0 smoke test does NOT
target it.

---

## 3. Enhancor Text to Image Full Access API

- Base URL: **UNKNOWN / NEEDS DASHBOARD COPY**.
- Endpoints, request body, status enum, webhook payload: **UNKNOWN / NEEDS DASHBOARD COPY**.
- Not a target for Phase 0.

---

## 4. Enhancor Image Editor Full Access API

- Base URL: **UNKNOWN / NEEDS DASHBOARD COPY**.
- Endpoints, request body, status enum, webhook payload: **UNKNOWN / NEEDS DASHBOARD COPY**.
- Not a target for Phase 0.

---

## 5. Seedance 2.0 Full Access API — Claude

> This is the primary target for Phase 0. The structured facts below come
> from the operator's transcription of the authenticated dashboard.

- **Base URL:** `https://apireq.enhancor.ai/api/enhancor-ugc-full-access/v1`
- **Endpoints:**
  - `POST /queue`
  - `POST /status`
- **Auth header:** `x-api-key`

### Supported generation types

- `text-to-video`
- `image-to-video`

### Supported `image-to-video` modes

- `ugc`
- `multi_reference`
- `extend`
- `multi_frame`
- `lipsyncing`
- `voice_clone`
- `first_n_last_frames`

### Hard rules (preserved verbatim from the operator's brief)

- Always include `webhook_url`.
- Set `full_access: true` when generation involves human faces.
- Webhook callbacks may arrive more than once; dedupe by `request_id`.
- `duration` must be **4–15 seconds**, except `multi_frame` where the
  duration is the sum of `multi_frame_prompts`.
- `resolution` allowed: `480p`, `720p`, `1080p`.
- `1080p` is only supported when `fast_mode` is `false`.
- `aspect_ratio` allowed: `16:9`, `9:16`, `4:3`, `3:4`, `1:1`, `21:9`.
- `images` max **9**.
- `videos` max **3**, combined duration under 15 s.
- `audios` max **3**, combined duration under 15 s.
- `products` + `influencers` + `images` combined max **9** in `ugc` mode.
- `text-to-video` should NOT send `images` / `videos` / `audios`.

### Request body — confirmed field names

- `type` (`text-to-video` | `image-to-video`)
- `mode` (one of the modes above) — present when `type=image-to-video`
- `prompt`
- `webhook_url` (mandatory)
- `full_access` (boolean; `true` whenever a human face will appear)
- `duration` (string; "4"–"15"). Omitted for `multi_frame`.
- `resolution` (`"480p" | "720p" | "1080p"`)
- `aspect_ratio` (one of the allowed values above)
- `fast_mode` (boolean; gate for `1080p`)
- `images` (array of URLs, ≤ 9)
- `videos` (array of URLs, ≤ 3, combined ≤ 15 s)
- `audios` (array of URLs, ≤ 3, combined ≤ 15 s)
- `products` (array of URLs; UGC mode)
- `influencers` (array of URLs; UGC mode)
- `multi_frame_prompts` (array of per-frame prompt blocks; `multi_frame` mode)

Any field not listed above is **UNKNOWN / NEEDS DASHBOARD COPY** (e.g. seed,
`fps`, `cfg_scale`, idempotency-key header, optional `keep_original_sound`,
the exact `multi_frame_prompts` per-entry shape).

### Response shape — request submission

- `success` (bool)
- `requestId` (string)

Any additional fields surfaced by the dashboard (e.g. estimated cost,
estimated finish time, regional hint) are **UNKNOWN / NEEDS DASHBOARD COPY**.

### Response shape — `/status` poll

Conservative baseline (matches Enhancor-wide pattern on the skin endpoint):

- `requestId`
- `status` (string)
- `result` (URL, present when terminal-success)
- `cost` (numeric, present when terminal-success)

Exact `status` enum values surfaced by the dashboard for this endpoint:
**UNKNOWN / NEEDS DASHBOARD COPY**. The smoke test treats `COMPLETED` (or any
status containing the substring `complet`) as terminal-success and `FAILED`
(or any status containing `fail` / `error`) as terminal-failure, with all
other strings treated as in-flight.

### Webhook payload

Conservative baseline (mirrors the skin endpoint):

- `request_id`
- `status`
- `result` (when terminal-success)
- `cost` (when terminal-success)

Whether failures carry an `error` / `message` field, whether the payload
carries a signature header, and whether `User-Agent` is documented:
**UNKNOWN / NEEDS DASHBOARD COPY**.

---

## 6. Seedance 2.0 Full Access API — MCP

Same surface as § 5, exposed as an MCP server in the dashboard for
Claude-via-MCP clients. Same base URL, same endpoints, same auth, same
rules. The MCP wrapper does not (per the operator's brief) add or remove
endpoints; it just exposes the same payload shape as MCP tools.

---

## 7. Enhancor Video Pro API — Claude

- **Base URL:** `https://apireq.enhancor.ai/api/enhancor-video-pro/v1`
- Same general modes and async pattern as § 5.
- **Key delta:** uses **`is_uncensored`** instead of **`full_access`**.
- Exact field-by-field deltas (request body, response, status enum, webhook
  shape): **UNKNOWN / NEEDS DASHBOARD COPY**.

Not a Phase-0 smoke-test target; the Phase-0 smoke test pins to the
Seedance Full Access surface in § 5 to keep the contract single-flavoured.

---

## 8. UGC Audio Fixer API

- **Base URL:** `https://apireq.enhancor.ai/api/fix-audio/v1`
- **Endpoints:**
  - `POST /queue`
  - `POST /status`
- **Auth header:** `x-api-key`

### Queue request

- `inputVideo` (URL)
- `webhook_url`

### Queue response

- `success`
- `requestId`

### `/status` completed response

- `requestId`
- `status`
- `result`
- `cost`

### Error codes (documented in the dashboard)

- `400`
- `401`
- `403`
- `500`

Per-code messages, expected operator actions, rate limits, file-size limits,
input format constraints, output format, output URL TTL: **UNKNOWN / NEEDS
DASHBOARD COPY**.

---

## Cross-surface unknowns (must be pasted from the dashboard before code goes live)

- Exact `status` enum values for the video endpoints.
- Webhook signature header (name + algorithm + secret distribution).
- Webhook retry policy (backoff curve + dead-letter behaviour).
- Idempotency-key header name (if any).
- Rate limits per key (per-second / per-minute / per-day).
- File size / duration limits beyond what is explicitly listed in § 5.
- Output URL TTL (how long the `result` URL stays live).
- Whether base64 input is accepted anywhere or every media field must be a public URL.
- Cost echoed on the queue response or only on `/status` and the webhook.
- Whether `fast_mode` and `full_access` flags appear on Video Pro under different names.

These items live as `UNKNOWN / NEEDS DASHBOARD COPY` rows in the capability
matrix and in the smoke-test script's defensive handling.
