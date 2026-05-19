# Enhancor capability matrix

Source: [`enhancor_dashboard_raw.md`](enhancor_dashboard_raw.md) +
[`enhancor_api_spec.md`](enhancor_api_spec.md). Updated 2026-05-16.

**Provider abstraction (2026-05-16):** Phase-0 surfaces are now wrapped
by concrete adapters at
[`agents/producer/providers/enhancor_seedance.py`](../agents/producer/providers/enhancor_seedance.py)
and
[`agents/producer/providers/enhancor_audio_fixer.py`](../agents/producer/providers/enhancor_audio_fixer.py),
both implementing the `Provider` protocol from
[`agents/producer/providers/base.py`](../agents/producer/providers/base.py).
The abstraction normalises status onto a 5-state enum (`QUEUED`,
`IN_PROGRESS`, `COMPLETED`, `FAILED`, `UNKNOWN`); adapters preserve raw
provider JSON verbatim so undocumented fields stay discoverable.
**Adapters MUST NOT assume a single CDN host** on terminal-success
result URLs — Seedance currently emits CloudFront, Audio Fixer emits
`v3b.fal.media`, future providers will emit other hosts. The mirror-to-
storage step (out of scope for the Phase-0 layer) handles host
normalisation.

**Confirmed by smoke tests 2026-05-16**:

- **Seedance text-to-video** (requestId `6a0850e96c164b8f24cb7d05`):
  `/queue` + `/status` end-to-end ✓ · `IN_PROGRESS` / `COMPLETED` enums ✓ ·
  undocumented `thumbnail` webp on terminal-success ✓ · `cost=264` (units
  UNKNOWN) ✓ · CloudFront `.mp4` (703,428 B) ✓ · **silent** (no audio track) ·
  output URL TTL still UNKNOWN.
- **UGC Audio Fixer** (requestId `6a0852fcdb43fe5882998b35`): `/queue` +
  `/status` end-to-end ✓ · `PENDING` / `COMPLETED` enums ✓ · accepted the
  Seedance CloudFront URL directly with no mirroring step required · result
  hosted on `v3b.fal.media` (different CDN from Seedance CloudFront) · output
  is a single fully-muxed mp4 (`*_combined_output.mp4`, 347,804 B, 4.04 s,
  video+audio) ✓ · `cost=561`.
- **Seedance UGC** (requestId `6a08562a60cece3ba3062062`):
  `type=image-to-video / mode=ugc` with one product URL + one influencer URL
  (synthetic StyleGAN face) + `full_access=true` ✓ · `IN_PROGRESS ×7 →
  COMPLETED` over ~2 min wall-clock ✓ · same terminal payload shape as
  text-to-video (`result`, `thumbnail`, `cost`) ✓ · **output already contains
  an audio track natively** (1 video + 1 audio stream, 5.06 s, 913,922 B) ·
  thumbnail visually confirms both references were honoured (face identity
  preserved AND product packaging with `pai` wordmark visible) · `cost=330`
  (cf. t2v `264` for 4 s at 480p; UGC at 5 s at 480p scales roughly with
  duration when knobs match).

**Read this column key first:**

- **Confirmed?** — `✅ DOC` (from the authenticated dashboard, preserved in raw
  docs), `🧪 TEST` (will be confirmed by the Phase-0 smoke test), `❓ UNKNOWN`
  (must be pasted from the dashboard before code can depend on it).
- **API / endpoint** — which Enhancor API and route this lives on.
- **Relevant fields** — the field names we know from the dashboard.
- **Limits** — the hard numbers the dashboard enforces.
- **Product implication** — how this constrains the AI Creative OS we're
  building (Lovable / Next.js / Supabase / Enhancor).
- **Unknowns** — what still needs paste/test.

---

## Generation capabilities

| Capability | Confirmed? | API / endpoint | Relevant fields | Limits | Product implication | Unknowns |
|---|---|---|---|---|---|---|
| text-to-video | ✅ DOC | Seedance Full Access · `POST /queue` | `type=text-to-video`, `prompt`, `duration`, `resolution`, `aspect_ratio`, `fast_mode`, `webhook_url` | duration 4-15 s; resolutions 480p/720p/1080p; ratios 16:9/9:16/4:3/3:4/1:1/21:9 | Cheapest probe path → first smoke test mode | exact `status` enum, idempotency-key header |
| image-to-video (general) | ✅ DOC | Seedance Full Access · `POST /queue` | `type=image-to-video`, `mode` | same as above | Required for any product-shot continuation | per-mode payload field surface |
| UGC mode | ✅ DOC | Seedance Full Access · `POST /queue` | `mode=ugc`, `products[]`, `influencers[]`, `images[]`, `full_access=true` | `len(products)+len(influencers)+len(images) ≤ 9` | The "real person + product" path that mattered for Pai | influencer-image content rules, consent/licensing requirements |
| product references | ✅ DOC | Seedance Full Access · `POST /queue` | `products[]` (URLs) in UGC mode | counts toward the ≤9 limit | The user's "paste website → AI strategy → review → generate" flow lands here | max product count per call, allowed formats, file-size cap |
| influencer references | ✅ DOC | Seedance Full Access · `POST /queue` | `influencers[]` (URLs) in UGC mode | counts toward the ≤9 limit | Operator must supply influencer asset; we don't synthesize a person | consent/usage rules, recommended pose/lighting, MIME types |
| multi_reference | ✅ DOC | Seedance Full Access · `POST /queue` | `mode=multi_reference`, `images[]`, `videos[]`, `audios[]` | images ≤9; videos ≤3 combined <15 s; audios ≤3 combined <15 s | Lets us combine product image + motion-reference video + voice sample | exact per-ref-type semantics, whether `videos[]` carries motion only or full style |
| reference images | ✅ DOC | Seedance Full Access | `images[]` | ≤9 | Foundation for product-shot continuation + style anchors | max bytes / pixel dims |
| reference videos | ✅ DOC | Seedance Full Access | `videos[]` | ≤3 combined <15 s | Lets us hand the operator-approved motion reference to Seedance directly | whether base64 is accepted or only public URL |
| reference audios | ✅ DOC | Seedance Full Access | `audios[]` | ≤3 combined <15 s | Foundation for `voice_clone` and `lipsyncing` | format support (mp3/wav?), max bytes |
| native audio / Seedance soundtrack | ✅ DOC (per-mode) | Seedance Full Access | `mode` | `UGC` mode = native audio present; `text-to-video` = silent; `multi_reference` = NEEDS TEST | UGC mode → Audio Fixer is a *cleanup* pass; t2v → Audio Fixer would *add* a synthesised track; multi_reference behaviour not yet measured | Surfaced as named constants on the Seedance adapter: `UGC_OUTPUT_INCLUDES_NATIVE_AUDIO=True`, `TEXT_TO_VIDEO_OUTPUT_IS_SILENT=True`, `MULTI_REFERENCE_OUTPUT_AUDIO_BEHAVIOUR="unknown"`. Per-mode confirmation timing in smoke run 2026-05-16. |
| lipsyncing | ✅ DOC (mode listed) | Seedance Full Access · `POST /queue` | `mode=lipsyncing`, presumably `videos[]` + `audios[]` | within base limits | Lets us hand a generated talking-head + an external voice take to produce a lip-sync match | exact field combination; what to do if the audio is shorter than the video |
| voice clone | ✅ DOC (mode listed) | Seedance Full Access · `POST /queue` | `mode=voice_clone`, presumably `audios[]` | audio combined <15 s | Voice consistency across scenes | sample-quality requirements, consent gating |
| multi-frame scenes | ✅ DOC | Seedance Full Access · `POST /queue` | `mode=multi_frame`, `multi_frame_prompts[]` | **`duration` omitted; effective duration = sum of `multi_frame_prompts`** | The path to ≥15 s pieces inside a single call; affects how we map our `scenes` table | per-entry shape of `multi_frame_prompts[]`; whether each entry takes its own image/video refs |
| first / last frame | ✅ DOC (mode listed) | Seedance Full Access · `POST /queue` | `mode=first_n_last_frames`, presumably `images[]` of length 1-2 | base image limits | Story-arc control for product-reveal beats | exact field structure for which image is first vs last |
| extend (continuation of a generated clip) | ✅ DOC (mode listed) | Seedance Full Access · `POST /queue` | `mode=extend`, presumably `videos[]` (the prior clip) | base video limits | Lets us push a 10 s base to 13-15 s without re-generating | whether the extend input must be an Enhancor-produced URL or any URL is accepted; how the extended segment is timed |
| Audio Fixer | ✅ DOC | UGC Audio Fixer · `POST /queue` | `inputVideo`, `webhook_url` | UNKNOWN file-size cap | Every Seedance success auto-enqueues an Audio Fixer pass before the operator reviews it. **Output is a single fully-muxed mp4** (video + repaired audio in one file) — confirmed by smoke 2026-05-16. | supported input codecs; max input duration |
| Text-to-Image | ❓ UNKNOWN (API listed in dashboard, payload not yet pasted) | Enhancor Text to Image Full Access | UNKNOWN | UNKNOWN | Useful for product-mock generation when the operator lacks a packshot | base URL, endpoints, fields |
| Image Editor | ❓ UNKNOWN | Enhancor Image Editor Full Access | UNKNOWN | UNKNOWN | Useful for prep-stage product retouch | base URL, endpoints, fields |
| Skin Enhancer V4 | ✅ DOC (V1/V3 public, V4 deltas UNKNOWN) | Skin Enhancor · `POST /queue` | publicly documented | publicly documented | Skin-realism post-pass on UGC frames if dashboard surfaces v4 | V4-specific delta fields |
| Text-to-Video Standard | ❓ UNKNOWN | Enhancor Video Full Access — Text to Video Standard | UNKNOWN | UNKNOWN | Cheaper alternative path? Or older surface? Need paste to decide | base URL endpoints, field surface |
| Enhancor Video Pro | ✅ DOC (base URL + `is_uncensored` delta) | Enhancor Video Pro | `is_uncensored` (vs Seedance's `full_access`) | UNKNOWN per-field | Adapter can register Pro side-by-side with Full Access | exact field-by-field delta |

---

## Async / orchestration capabilities

| Capability | Confirmed? | API / endpoint | Relevant fields | Limits | Product implication | Unknowns |
|---|---|---|---|---|---|---|
| Async queue | ✅ DOC | Seedance Full Access · `POST /queue` + `POST /status`; UGC Audio Fixer same shape | `requestId` on response | UNKNOWN concurrent-job cap | Workers reuse one pattern across both providers | per-key concurrency cap; per-IP cap |
| webhook delivery | ✅ DOC (`webhook_url` mandatory) | Both | `webhook_url` on submit; payload mirrors skin pattern | duplicates possible | All webhook receivers must dedupe by `request_id` | signature header; retry curve; `User-Agent` |
| status polling | ✅ DOC | Both · `POST /status` | `requestId` body | UNKNOWN per-min polling cap | Polling is the fallback when webhooks are silent > 60 s | rate limit on `/status` |
| retries | ❓ UNKNOWN | n/a | n/a | n/a | Our worker controls retries; Enhancor side retry policy is unknown | server-side retry; webhook redelivery curve |
| output download | ✅ DOC | Both | `result` URL on terminal-success | UNKNOWN TTL on `result` | We must mirror every `result` to Supabase Storage immediately | exact TTL of the `result` URL |
| cost tracking | ✅ DOC | Both | `cost` on `/status` (and presumably webhook) | per-call cost numbers UNKNOWN | We log per-job cost into `costs` table | currency, unit, whether cost echoes on the queue response too |
| deduplication | ✅ DOC ("callbacks may arrive more than once; dedupe by request_id") | Webhook receivers | `(request_id, status, result)` triple | n/a | Receiver must be idempotent | idempotency-key header on inbound submissions: UNKNOWN |
| retry handling (failures) | ❓ UNKNOWN | n/a | n/a | n/a | Our policy engine decides; Enhancor side: unknown | does Enhancor auto-retry transient failures? |
| Audio Fixer chaining | 🧪 TEST | UGC Audio Fixer | `inputVideo` field accepts a public URL | UNKNOWN file-size cap | Auto-chain on every Seedance success in the worker | whether the Seedance `result` URL is directly accepted (cross-domain pull) |

---

## Inputs / outputs

| Capability | Confirmed? | API / endpoint | Relevant fields | Limits | Product implication | Unknowns |
|---|---|---|---|---|---|---|
| Input as base64 vs public URL | ❓ UNKNOWN | Seedance Full Access | URL fields look URL-only | n/a | Smoke test defaults to public URLs (safe) | whether any field accepts base64 |
| Output as URL | ✅ DOC | Both | `result` URL | UNKNOWN TTL; **any HTTPS host** (Seedance → CloudFront, Audio Fixer → `v3b.fal.media`; future providers will emit others) | Mirror to Supabase Storage immediately on terminal success. Adapters must NOT discriminate by CDN host. | TTL |
| Output thumbnail | ✅ DOC (observed Seedance only) | Seedance Full Access · `/status` | `thumbnail` (CloudFront `.webp`) on terminal-success | n/a | Use directly as the operator-review poster frame; no re-encode needed. Audio Fixer does NOT emit a thumbnail. | whether other modes / future Enhancor surfaces also emit this; whether the webhook payload carries it |
| Supported image formats | ❓ UNKNOWN | All | UNKNOWN | UNKNOWN | Smoke test defaults to JPEG / PNG | exact MIME list |
| Supported video formats | ❓ UNKNOWN | All | UNKNOWN | UNKNOWN | Smoke test defaults to mp4 | exact MIME list |
| Supported audio formats | ❓ UNKNOWN | All | UNKNOWN | UNKNOWN | Smoke test defaults to mp3/wav | exact MIME list |
| Max file size per asset | ❓ UNKNOWN | All | UNKNOWN | UNKNOWN | Pre-flight should bail above the limit | needs paste |
| Max duration per asset | ✅ DOC for the video/audio array combined caps (< 15 s) | Seedance | `videos[]`, `audios[]` | combined < 15 s each | Pre-flight enforces this | per-file cap (vs combined) |

---

## Output rules

| Capability | Confirmed? | API / endpoint | Relevant fields | Limits | Product implication | Unknowns |
|---|---|---|---|---|---|---|
| Duration limits | ✅ DOC | Seedance | `duration` 4-15 s; `multi_frame` is sum | enforce in payload builder | Cap our `scene.duration_sec_max` to 15 s; multi-scene routes use `multi_frame` or chained `extend` | per-mode duration overrides |
| Resolution limits | ✅ DOC | Seedance | `resolution` ∈ {480p, 720p, 1080p} | 1080p requires `fast_mode=false` | Default draft mode to 480p+fast for cheap iteration; final mode to 1080p+`fast_mode=false` | does Enhancor reject the call or silently downgrade if `fast_mode=true` with 1080p? |
| Aspect-ratio options | ✅ DOC | Seedance | `aspect_ratio` ∈ {16:9, 9:16, 4:3, 3:4, 1:1, 21:9} | enforce on review surface | The aspect-ratio drift we hit with Kling is fixed by always setting this explicitly | whether the chosen ratio is honored exactly or letterboxed |
| Cost / credits | ✅ DOC for `cost` echo | Both | `cost` on `/status` and webhook | per-call numbers UNKNOWN; **Audio Fixer per-second cost > Seedance generation per-second cost** in current smoke data (Audio Fixer `561` for ~4 s input vs Seedance t2v `264` for the same 4 s, ~2.1×) | Cost ledger must track each provider's `cost` independently; do NOT assume Audio Fixer is "free" because it's a post-pass | per-second pricing; tier discounts; whether cost units differ between providers |

---

## Summary signal

- **Phase-0 ready endpoints:** Seedance 2.0 Full Access (queue/status) + UGC
  Audio Fixer (queue/status).
- **Phase-0 ready modes:** `text-to-video` (cheapest probe) and `image-to-video / ugc`
  (validates the full UGC payload).
- **Untouched until dashboard pastes land:** Enhancor Video Pro, Text to Video
  Standard, Text to Image Full Access, Image Editor Full Access.
- **Required dashboard paste before any code goes to production:** status enum,
  webhook signature / retry curve, per-key rate limit, output URL TTL, allowed
  MIME types per input field, per-call cost table.

---

## Recommended quality tiers (Phase-1A product surface)

Pai's live 15 s 1080p generation cost **5 940** credits and the follow-up
Audio Fixer pass cost **2 103.75** credits — **≈ 8 044** credits for a single
15 s deliverable. The dashboard exposes three explicit tiers; 1080p is no
longer the default, and Audio Fixer is no longer automatic.

| Tier | Resolution | Per-second (no video input) | 15 s cost | Audio Fixer | Default? |
|---|---|---|---|---|---|
| Draft / test | `480p` | 82 cr/s | **1 230 cr** | manual | — |
| **Standard / client preview** | `720p` | 176.4 cr/s | **2 646 cr** | manual | **yes** |
| Premium / final | `1080p` | 396 cr/s | **5 940 cr** | manual | opt-in |
| Audio Fixer | — | — | **≈ 2 100 cr** *(Pai 15 s reference)* | — | manual-only |

**Product rules locked in:**

- 720p is the default tier on every "Generate" action; the picker lands on
  Standard, not Premium.
- 1080p requires explicit operator selection plus a visible cost-warning
  chip.
- Audio Fixer is never automatic. Every Audio Fixer run is a separate
  explicit operator action with its own ≈ 2 100-cr estimate displayed
  next to the button.
- Estimates above are floors; the `cost` integer echoed by the provider on
  `/status` and the webhook is the canonical value the cost ledger writes.
- The client portal does NOT see costs. It sees status chips only.

This tier policy is mirrored in
[`web/lib/quality-tiers.ts`](../web/lib/quality-tiers.ts) so the UI labels,
default selection, and cost hints all derive from a single source.

---

## Provider abstraction (2026-05-16)

The Phase-0 surfaces are now consumed via a thin protocol-based adapter
layer; the per-provider wire details stay hidden from callers.

| Layer | Path | Role |
|---|---|---|
| Base protocol + dataclasses | [`agents/producer/providers/base.py`](../agents/producer/providers/base.py) | `Provider` protocol, `ProviderJobRequest` / `ProviderJobResponse` / `ProviderJobStatus` / `ProviderGenerationResult` dataclasses, `ProviderStatus` enum, `classify_provider_status()`, `redact_api_key_headers()` |
| Seedance adapter | [`agents/producer/providers/enhancor_seedance.py`](../agents/producer/providers/enhancor_seedance.py) | `EnhancorSeedanceProvider` (`name="enhancor_seedance"`) + module-level payload builders + capability constants |
| Audio Fixer adapter | [`agents/producer/providers/enhancor_audio_fixer.py`](../agents/producer/providers/enhancor_audio_fixer.py) | `EnhancorAudioFixerProvider` (`name="enhancor_audio_fixer"`) + `submit_audio_fix()` one-shot helper |

### Status enum (`ProviderStatus`)

| Member | Wire-protocol provenance |
|---|---|
| `QUEUED` | Enhancor `PENDING` / `IN_QUEUE`, fal.ai `IN_QUEUE`, generic `queued` / `waiting` / `scheduled` |
| `IN_PROGRESS` | Enhancor `IN_PROGRESS`, generic `processing` / `running` / `active` |
| `COMPLETED` | Enhancor `COMPLETED`, generic `success` / `complete` / `done` / `succeeded` |
| `FAILED` | `FAILED` / `FAILURE` / any string containing `error` / `fail` / `rejected` / `cancelled` |
| `UNKNOWN` | Anything else, or empty / None. **Callers MUST treat as in-flight, never as terminal-success.** |

Classifier is case-insensitive and substring-based; terminal states are
checked before in-flight states so a string like
`complete_with_warnings` resolves to `COMPLETED`, not `UNKNOWN`.

### Architectural rules the adapters enforce

1. **No API-key leakage.** `redact_api_key_headers()` is the only public
   helper for header dumps; adapters never override `__repr__` to leak
   the key; `ProviderError.raw_response` is expected to be pre-redacted
   by the caller.
2. **No CDN assumption.** Result / thumbnail URLs are typed as plain
   `str`. The Seedance smoke emits CloudFront, the Audio Fixer emits
   `v3b.fal.media`, future providers will emit others. The
   mirror-to-storage step (out of scope for the Phase-0 layer) handles
   host normalisation.
3. **Raw-payload preservation.** Every response dataclass carries the
   provider's raw JSON in `raw_request` / `raw_response` /
   `raw_status_response` / `raw_completed_response`. Adapters MUST NOT
   strip undocumented fields; the `thumbnail` discovery happened on
   raw payload, not on a typed field.
4. **No loop policy in the adapter surface.** `poll_status()` is a
   single call; `wait_for_completion()` is the helper that loops on
   top of it. Callers (worker, dashboard) own the loop policy.
5. **No Supabase / no dashboard.** The provider layer is wire-protocol
   only.
