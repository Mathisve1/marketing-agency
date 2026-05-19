// Yuvo Studio — OpenNext Cloudflare adapter config.
//
// Minimal config: no R2 incremental cache (every dashboard page is
// dynamic / server-rendered against Supabase, nothing to cache safely
// pre-Phase 1F caching). No KV bindings. No Images binding (we have
// `images: { unoptimized: true }` in next.config.ts).
//
// Phase 1M scope is: get a public HTTPS URL up so Supabase magic-link
// auth can be tested away from localhost. Caching / images come later.

import { defineCloudflareConfig } from "@opennextjs/cloudflare";

export default defineCloudflareConfig({});
