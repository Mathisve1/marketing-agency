// Yuvo Studio — Phase 5B visual asset storage path helper.
//
// Pure, deterministic. No I/O, no fetch, no Supabase, no R2 SDK,
// no upload, no env read. Builds the canonical storage key the
// future R2 (or fallback) bucket will use for visual exports, and
// the deterministic filename the local Phase 5A exporter writes.
//
// HARD RULES:
//   - Returns plain strings. No external call. No process spawn.
//   - Rejects traversal (`..`, leading `/`, control chars, NUL).
//   - Normalises arbitrary user input to a slug; empty input falls
//     back to a stable placeholder.
//   - Preserves `_` and `-` (template ids and theme ids use them as
//     semantic separators); collapses anything else to a single `-`.
//   - Mirrors the deterministic filename helper in
//     `scripts/upload_visual_asset_stub.py` (`_filename_suggestion`).
//   - Allowed extensions are pinned to `png` / `jpg`.

export type VisualAssetFormat = "png" | "jpg";

export interface VisualAssetPathInput {
  workspaceId: string;
  contentItemId: string;
  /** Resolved preview mode (carousel / story / feed_post / …). */
  mode: string;
  /** Recorded creative-brief template id, e.g. `feed_post_neutral_v1`.
   *  Null falls back to `<mode>_default`. */
  templateId?: string | null;
  /** Resolved theme id, e.g. `neutral` / `premium_dark`. */
  themeId: string;
  format: VisualAssetFormat;
  width?: number | null;
  height?: number | null;
  /** Carousel only. */
  slideNumber?: number | null;
  /** Story only. */
  frameNumber?: number | null;
}

export interface VisualAssetPathPlan {
  /** Deterministic R2 / Supabase Storage object key. */
  storageKey: string;
  /** Deterministic filename (last segment of the key). */
  filename: string;
  /** The directory prefix the key lives under, suitable for
   *  list-and-delete operations in a future cleanup tool. */
  directoryPrefix: string;
  /** The set of segments the caller may want to surface in the
   *  manifest / UI ("workspace=…, content=…, template=…"). */
  segments: {
    workspaceId: string;
    contentItemId: string;
    templateId: string;
    themeId: string;
    filename: string;
  };
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const ID_SEGMENT_RE = /^[a-z0-9_-]{1,80}$/;

const ALLOWED_FORMATS: readonly VisualAssetFormat[] = ["png", "jpg"];

const FALLBACK_SLUG = "content";

// Control-character set built via String.fromCharCode so the
// `\x00-\x1f` escape sequence cannot be mangled by source-write
// tooling (an issue caught in Phase 5B when an inline regex literal
// got rewritten to literal control bytes). The check then becomes a
// simple set membership test.
const _CONTROL_CHARS: ReadonlySet<string> = new Set(
  (() => {
    const out: string[] = [];
    for (let i = 0; i < 32; i++) out.push(String.fromCharCode(i));
    out.push(String.fromCharCode(127));
    return out;
  })(),
);

function hasControlChars(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    if (_CONTROL_CHARS.has(s[i])) return true;
  }
  return false;
}

/** Strict slug helper. Lowercase, [a-z0-9_-]+, length-capped.
 *  Preserves `_` and `-`; collapses anything else to a single `-`.
 *  Falls back to `FALLBACK_SLUG` if the input reduces to empty. */
export function sanitizeSegment(raw: string, maxLen = 60): string {
  const stripped = raw
    .toLowerCase()
    .normalize("NFKD")
    .split("")
    .filter((ch) => !_CONTROL_CHARS.has(ch))
    .join("");
  const cleaned = stripped
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, maxLen);
  return cleaned || FALLBACK_SLUG;
}

/** Throwing validator for inputs that MUST already be safe (UUIDs,
 *  known mode/theme/template ids). Used by `buildVisualAssetPath` to
 *  reject traversal-style input up front. */
function assertSafeId(label: string, value: string): void {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} must be a non-empty string.`);
  }
  if (value.includes("..") || value.includes("/") || value.includes("\\")) {
    throw new Error(`${label} must not contain path separators or '..'.`);
  }
  if (hasControlChars(value)) {
    throw new Error(`${label} must not contain control characters.`);
  }
}

function assertUuid(label: string, value: string): void {
  assertSafeId(label, value);
  if (!UUID_RE.test(value)) {
    throw new Error(`${label} must be a UUID.`);
  }
}

function assertIdSegment(label: string, value: string): void {
  assertSafeId(label, value);
  if (!ID_SEGMENT_RE.test(value)) {
    throw new Error(
      `${label} must match [a-z0-9_-]{1,80} after lowercasing; got "${value}".`,
    );
  }
}

function assertFormat(value: string): asserts value is VisualAssetFormat {
  if (!ALLOWED_FORMATS.includes(value as VisualAssetFormat)) {
    throw new Error(
      `format must be one of ${ALLOWED_FORMATS.join(" / ")}; got "${value}".`,
    );
  }
}

function assertPositiveInt(label: string, value: number | null | undefined): void {
  if (value === null || value === undefined) return;
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${label} must be a positive integer when set.`);
  }
}

/** Deterministic filename. Mirrors the Python helper in the upload
 *  stub so the dashboard, the local exporter, and the future upload
 *  CLI all agree on naming. */
export function buildVisualAssetFilename(input: VisualAssetPathInput): string {
  assertFormat(input.format);
  assertUuid("contentItemId", input.contentItemId);
  if (input.templateId) assertIdSegment("templateId", input.templateId);
  assertIdSegment("themeId", input.themeId);
  assertSafeId("mode", input.mode);
  assertPositiveInt("slideNumber", input.slideNumber);
  assertPositiveInt("frameNumber", input.frameNumber);
  if (input.slideNumber && input.frameNumber) {
    throw new Error("Pass only one of slideNumber / frameNumber.");
  }

  const idStub = input.contentItemId.slice(0, 8);
  const template = input.templateId
    ? sanitizeSegment(input.templateId, 40)
    : sanitizeSegment(`${input.mode}_default`, 40);
  const parts: string[] = [template, idStub];
  if (input.slideNumber && input.slideNumber > 0) {
    parts.push(`slide${String(input.slideNumber).padStart(2, "0")}`);
  } else if (input.frameNumber && input.frameNumber > 0) {
    parts.push(`frame${String(input.frameNumber).padStart(2, "0")}`);
  }
  if (
    typeof input.width === "number" &&
    typeof input.height === "number" &&
    input.width > 0 &&
    input.height > 0
  ) {
    parts.push(`${input.width}x${input.height}`);
  }
  return `${parts.join("-")}.${input.format}`;
}

/** Build the canonical object key for a visual asset:
 *    `visual-assets/{workspace_id}/{content_item_id}/{template_id}/{theme_id}/{filename}`
 *  All segments are validated. Throws on traversal / unsafe input. */
export function buildVisualAssetPath(
  input: VisualAssetPathInput,
): VisualAssetPathPlan {
  assertUuid("workspaceId", input.workspaceId);
  assertUuid("contentItemId", input.contentItemId);
  assertIdSegment("themeId", input.themeId);
  // `templateId` is validated inside buildVisualAssetFilename.
  const filename = buildVisualAssetFilename(input);
  const templateSegment = input.templateId
    ? sanitizeSegment(input.templateId, 40)
    : sanitizeSegment(`${input.mode}_default`, 40);
  const themeSegment = sanitizeSegment(input.themeId, 30);
  const directoryPrefix = [
    "visual-assets",
    input.workspaceId,
    input.contentItemId,
    templateSegment,
    themeSegment,
  ].join("/");
  const storageKey = `${directoryPrefix}/${filename}`;
  return {
    storageKey,
    filename,
    directoryPrefix,
    segments: {
      workspaceId: input.workspaceId,
      contentItemId: input.contentItemId,
      templateId: templateSegment,
      themeId: themeSegment,
      filename,
    },
  };
}

/** Convenience for the future upload CLI / dashboard panel: reject
 *  obviously-unsafe local file paths (the actual file existence
 *  check belongs to the runtime, not this pure helper). */
export function assertLocalUploadPathSafe(localPath: string): void {
  if (typeof localPath !== "string" || localPath.trim() === "") {
    throw new Error("local file path must be a non-empty string.");
  }
  if (
    localPath.includes("..") ||
    localPath.startsWith("/etc") ||
    localPath.startsWith("/root")
  ) {
    throw new Error(
      "local file path must not traverse outside the workspace " +
        "(no '..', no /etc, no /root).",
    );
  }
  if (hasControlChars(localPath)) {
    throw new Error("local file path must not contain control characters.");
  }
  if (!/\.(png|jpg|jpeg)$/i.test(localPath)) {
    throw new Error(
      "local file path must end in .png / .jpg / .jpeg (visual asset).",
    );
  }
}
