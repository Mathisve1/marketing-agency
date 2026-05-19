# Enhancor webhook contract

Source: [`enhancor_dashboard_raw.md`](enhancor_dashboard_raw.md) +
[`enhancor_api_spec.md`](enhancor_api_spec.md). Updated 2026-05-16.

Two endpoints fire webhooks: **Seedance 2.0 Full Access** and **UGC Audio
Fixer**. Both require `webhook_url` on every submit and both can deliver the
same notification more than once. The contract below is the conservative
baseline derived from the publicly-documented skin-endpoint webhook and the
hard rules the operator preserved in the raw docs; every UNKNOWN row must be
confirmed against the dashboard before this contract becomes binding in
production.

---

## 1. When webhooks fire

- **Mandatory:** every `POST /queue` submission MUST include `webhook_url`
  in the request body. The receiver URL is the only place Enhancor delivers
  terminal events.
- **Endpoints that fire webhooks:**
  - Seedance 2.0 Full Access — `POST /queue` → webhook on terminal state.
  - UGC Audio Fixer — `POST /queue` → webhook on terminal state.
- **Whether interim states fire webhooks** (e.g. `IN_QUEUE` → `IN_PROGRESS`):
  UNKNOWN / NEEDS TEST. The conservative baseline is **terminal-only**.

---

## 2. Receiver URL requirements

- **Public HTTPS URL.** Local URLs and base64 callback addresses are not
  documented as supported. Use ngrok / Cloudflare Tunnel / Pages function
  during development; deploy to a Supabase Edge Function (or Next.js `/api`)
  in production.
- **Recommended URL shape (per-prospect-safe, per-provider-explicit):**
  - `https://{host}/api/webhooks/enhancor/seedance`
  - `https://{host}/api/webhooks/enhancor/audio_fixer`
- **HTTPS only.** HTTP is not documented as supported.
- **One URL per submit.** A submission cannot multi-cast to multiple webhook
  URLs in a single call — if a second receiver needs the same event, fan out
  on our side.

---

## 3. Success payload (terminal state)

Conservative baseline mirroring the publicly-documented skin-endpoint
webhook **AND** the Seedance `/status` body observed in smoke 2026-05-16
(see `enhancor_api_spec.md § A`). The webhook itself was not received
in that smoke run (polling carried the test); the receiver-side schema
below is the conservative shape until a real webhook is captured:

```json
{
  "request_id": "<opaque string echoed from queue response>",
  "status": "COMPLETED",
  "result": "https://...<media URL>",
  "thumbnail": "https://...<webp poster URL>",
  "cost": 264
}
```

**Confirmed via Seedance smoke 2026-05-16:**

- Terminal-success status string: `COMPLETED` (matches the conservative
  baseline; also matches the skin endpoint's enum).
- The undocumented `thumbnail` field appears alongside `result` on the
  `/status` poll body; receivers should accept (and store) it but never
  treat its absence as an error.
- `cost` is an integer (`264`); units UNKNOWN.

- `request_id` — string. Mirrors the `requestId` returned by `/queue`.
- `status` — string. The smoke test treats any value containing the
  substring `complet` (case-insensitive) as terminal-success.
- `result` — string (URL). The output media. **NEEDS TEST:** URL TTL.
- `cost` — number. Per-job cost in the provider's unit. Currency UNKNOWN.

**UNKNOWN / NEEDS TEST:** whether `result` is always a single URL or can be
an object with multiple URLs (e.g. `{video, thumbnail}` for Seedance,
`{video, audio_only}` for Audio Fixer).

---

## 4. Failure payload (terminal state)

Conservative baseline:

```json
{
  "request_id": "<opaque string>",
  "status": "FAILED",
  "error": "<short string, optional>",
  "code": "<short string, optional>"
}
```

- `status` — any value containing the substring `fail` or `error`
  (case-insensitive) is treated as terminal-failure.
- `error` / `code` field names: UNKNOWN / NEEDS TEST.
- Error categories surfaced (vs. just generic `FAILED`): UNKNOWN.

---

## 5. Duplicate webhook handling — confirmed rule

From the operator's preserved doc:

> **Webhook callbacks may arrive more than once; dedupe by `request_id`.**

Our receiver **must** be idempotent on `(request_id, status, result_hash)`.
A repeat of an already-recorded `(request_id, status)` pair is acknowledged
with `200 OK` and the body is dropped silently — we do NOT re-enqueue
Audio Fixer, do NOT re-trigger a download, do NOT double-charge cost.

---

## 6. Signature verification

UNKNOWN / NEEDS DASHBOARD COPY.

**Until confirmed**, the receiver MUST authenticate every inbound webhook
using a shared-secret query token we add to the `webhook_url` ourselves:

```
https://{host}/api/webhooks/enhancor/seedance?token=<random per-workspace secret>
```

The secret lives in `webhooks` config (Supabase secrets table or workspace
env). The receiver rejects any inbound request whose `?token=` does not
match. This is a fallback; the moment Enhancor publishes a signature header
(`X-Enhancor-Signature` or similar), the receiver verifies that **in
addition** to the shared-secret token, never as a replacement.

---

## 7. Retry behaviour (Enhancor → us)

UNKNOWN / NEEDS DASHBOARD COPY.

- Whether Enhancor retries on receiver non-2xx: UNKNOWN.
- Retry curve: UNKNOWN.
- Dead-letter behaviour: UNKNOWN.

**Our defensive behaviour:** always return `200 OK` as fast as possible from
the webhook receiver. Heavy work (download, Audio Fixer enqueue) happens
**after** the response, off the request thread, via a background worker
reading the `webhooks_inbox` table.

---

## 8. Idempotency strategy (us → state)

The receiver runs this sequence on every inbound POST:

1. **Accept POST.** Return `200 OK` as soon as the body is parsed and the
   raw payload is durably persisted.
2. **Store the raw payload** into `webhooks_inbox` with: `at`,
   `provider ∈ {enhancor_seedance, enhancor_audio_fixer}`, `path`,
   `headers_jsonb`, `body_jsonb`, `signature_ok`, `processed_ok` (false at
   first).
3. **Verify `request_id` exists in `generation_jobs.provider_job_id`**
   (Seedance webhooks) or `audio_fixer_jobs.provider_job_id` (Audio Fixer
   webhooks). If not, set `processed_ok=true` (it was a duplicate of an
   already-deleted job or a stray inbound) and stop.
4. **Dedupe by `(provider_job_id, status, sha256(result || ""))`.** If a
   prior `generation_job_events` row already carries the same triple,
   mark `processed_ok=true` and stop.
5. **Update the job status.** Compute the new state per the rules in
   § 3 / § 4 of the spec and write a `generation_job_events` row.
6. **If `status` is terminal-success:**
   - For a Seedance webhook → enqueue an Audio Fixer job referencing
     `result` as `inputVideo`, OR (when the workspace flag disables
     auto-fixer) jump straight to `READY_FOR_REVIEW`.
   - For an Audio Fixer webhook → mark the parent job
     `AUDIO_FIXED` → `READY_FOR_REVIEW` and download the final file.
7. **If `status` is terminal-failure:** mark `FAILED` with the error /
   code captured; the retry policy engine decides whether to retry.
8. **Return 200 quickly.** No heavy processing in the request thread.
9. **Never do heavy processing inside the webhook request.** All file
   downloads, ffmpeg muxing, Audio Fixer submits, vision-QC calls happen in
   worker processes reading the `webhooks_inbox` and `generation_jobs`
   tables.

---

## 9. Content-Type / User-Agent

- **Content-Type:** UNKNOWN. The receiver accepts `application/json` by
  default and falls back to parsing the body as JSON regardless of the
  declared header (defensive).
- **User-Agent:** UNKNOWN. Do not gate on a specific UA string until the
  dashboard confirms one — false-positive blocks here cost us real events.
- **Origin region / source IP:** UNKNOWN. Do not IP-allowlist until
  Enhancor publishes a documented egress range.

---

## 10. Worked example (Seedance text-to-video)

1. We `POST /queue` with `webhook_url=https://h.example.com/api/webhooks/enhancor/seedance?token=ABC123`.
2. Enhancor returns `{ "success": true, "requestId": "rq-7d3..." }`.
3. We insert `generation_jobs` row, `provider_job_id="rq-7d3..."`, state
   `SUBMITTED`.
4. Job runs server-side. Eventually Enhancor fires:

   ```http
   POST /api/webhooks/enhancor/seedance?token=ABC123 HTTP/1.1
   Host: h.example.com
   Content-Type: application/json

   { "request_id": "rq-7d3...", "status": "COMPLETED",
     "result": "https://cdn.enhancor.../rq-7d3.mp4", "cost": 0.42 }
   ```

5. Receiver validates `?token=ABC123`, stores raw, returns `200 OK`.
6. Worker picks up the inbox row, dedupes against prior events for
   `rq-7d3...`, finds none → writes `generation_job_events(event=generated_raw)`,
   downloads the mp4 to `generated-raw` bucket, enqueues Audio Fixer with
   `inputVideo=<signed mirror URL>`, transitions main job to `AUDIO_FIXING`.
7. Audio Fixer eventually fires its own webhook with its own `request_id`,
   following the same machinery.

---

## 11. Receiver behaviour cheatsheet

| Step | What | SLO |
|---|---|---|
| HTTP 200 ACK | parse JSON, persist `webhooks_inbox` row | < 200 ms |
| Dedupe | `(provider_job_id, status, sha256(result))` lookup | < 50 ms (indexed) |
| State transition | update `generation_jobs` + `generation_job_events` | < 100 ms |
| Chained enqueue (Audio Fixer / download) | OFF the request thread | n/a |

---

## 12. Open dashboard items that change this document if confirmed

| Item | Where it lands |
|---|---|
| Signature header name + algorithm | § 6 — promotes shared-secret to belt-and-braces |
| Retry policy (backoff curve, dead-letter behaviour) | § 7 |
| Interim-state webhooks (yes/no) | § 1 |
| Terminal `status` enum values | § 3 / § 4 |
| Failure payload field names (`error`, `code`, both, neither) | § 4 |
| `result` shape: single URL vs object | § 3 |
| URL TTL on `result` | § 3 + downstream "must mirror immediately" rule |
| Source IP / region | § 9 |
| `Content-Type` actually sent | § 9 |
| `User-Agent` actually sent | § 9 |

Until those are pasted, the receiver and the smoke test stay on the
conservative defaults above.
