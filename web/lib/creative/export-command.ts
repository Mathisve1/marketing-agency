// Yuvo Studio — Phase 4G shared export-command contract.
//
// Pure, deterministic. No I/O, no fetch, no DB call, no clipboard
// call, no execution. Builds the operator-facing CLI command + argv +
// filename suggestion for a single visual preview export. Consumed by:
//   - web/components/creative-preview/copy-export-command-button.tsx
//     (clipboard-only handoff; never executes)
//   - scripts/export_visual_preview_stub.py
//     (parses an equivalent shape via argparse; refuses to execute
//      until a real implementation lands)
//   - script-level tests (scripts/test_export_visual_preview_stub.py)
//
// HARD RULES (carried forward from Phase 4C-4F):
//   - Returns plain strings only. No process.spawn / child_process /
//     fetch / Supabase / Anthropic / OpenAI / publishing / email
//     reference exists in this file or any of its dependents on the
//     web side.
//   - Output is "future-safe": if the operator pastes the command
//     into a terminal today it hits the stub script, which validates
//     args and prints intent without rendering pixels.
//   - The shared contract documents WHAT a real export will receive
//     when it lands (Phase 4H+). Adding a real export pipeline later
//     should only require flipping the stub to a real implementation
//     — the dashboard UI and the operator workflow stay unchanged.

export type ExportFormat = "png" | "jpg";

export interface VisualExportCommandInput {
  /** uuid of the content item the brief belongs to. */
  contentItemId: string;
  /** Path or absolute URL to the dashboard preview page. When the
   *  caller does not know the host (server components on Cloudflare
   *  Workers can't always resolve the request origin without an
   *  explicit X-Forwarded-Host), pass the relative path and let the
   *  operator prepend their dashboard origin manually. The stub
   *  validates both shapes. */
  previewUrl: string;
  /** Resolved preview mode (`carousel` / `story` / `feed_post` / …).
   *  Used purely as a metadata hint on the command — the script will
   *  re-derive it from the live dashboard render when it ships. */
  mode: string;
  /** Resolved active template id (e.g. `feed_post_editorial_v1`) or
   *  `null` when no template is registered for the mode. */
  templateId: string | null;
  /** Resolved theme preset id (always non-null; falls back to
   *  `"neutral"` upstream). */
  themeId: string;
  /** Template `exportSize.width / height` (e.g. 1080 × 1920). */
  width: number | null;
  height: number | null;
  /** `"png"` is the only Phase 4G default; `"jpg"` accepted as a
   *  hint for the future implementation. */
  format?: ExportFormat;
  /** 1-indexed slide selector (carousels). When omitted the future
   *  script will export every slide. */
  slideNumber?: number | null;
  /** 1-indexed frame selector (stories). Same semantics as slide. */
  frameNumber?: number | null;
  /** Local directory the future script should write PNGs into. The
   *  builder slugifies / normalises but does not create any directory
   *  itself — the dashboard never touches the local filesystem. */
  outputDir?: string;
  /** Phase 4H — optional local `.html` / `.htm` path the future
   *  exporter will save the rendered preview into. Validated as a
   *  string only; no file is written. The dashboard surfaces this so
   *  the operator can plan where the HTML snapshot will land before
   *  ever running the real script. */
  htmlSnapshotPath?: string;
  /** When `true` (default) the command embeds `--dry-run`. The stub
   *  refuses to execute regardless until a real implementation lands. */
  dryRun?: boolean;
  /** Phase 4H — when `true` the command includes `--json` so the
   *  stub emits a single machine-readable JSON payload on stdout.
   *  Defaults to `false` so the operator-facing `Copy local export
   *  command` button keeps the prose-friendly mode. Tests + future
   *  automation flip this to `true`. */
  emitJson?: boolean;
}

export interface VisualExportCommand {
  /** The script path the command invokes (relative to the repo root). */
  scriptPath: string;
  /** Ordered argv array — same shape `subprocess.run([…])` would take.
   *  Each entry is already shell-safe (no quoting required by callers
   *  that pass argv directly). */
  argv: string[];
  /** Human-readable bash command (multi-line, line-continuated). The
   *  clipboard-handoff button copies this verbatim. */
  command: string;
  /** Deterministic, slug-safe local filename suggestion (no path). */
  filenameSuggestion: string;
  /** Phase 4H — deterministic local path the future executor would
   *  write to (`<outputDir>/<filenameSuggestion>`). The dashboard
   *  surfaces this so the operator sees the expected on-disk
   *  destination before pasting the command. */
  plannedOutputPath: string;
  /** True when this command requires a real operator (i.e. still
   *  true in Phase 4H — there is no automation surface that runs
   *  it). Documented on the type so callers don't accidentally
   *  spawn it from a server action. */
  requiresOperatorExecution: true;
}

// ---------------------------------------------------------------------------
// Sanitization helpers — keep filenames + dirs predictable on every OS.
// ---------------------------------------------------------------------------

const SLUG_FALLBACK = "content";

function slugify(s: string, max: number): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, max) || SLUG_FALLBACK
  );
}

/** Strip anything that could escape a directory (parent-traversal,
 *  whitespace runs, quotes). Returns an empty string when nothing
 *  remains, so callers can decide whether to fall back. */
function sanitizeOutputDir(raw: string | undefined): string {
  if (!raw) return "";
  const cleaned = raw
    .replace(/\\+/g, "/")
    .replace(/\.+\//g, "")
    .replace(/^\/+/, "")
    .replace(/\s+/g, "-")
    .replace(/["'`]/g, "");
  return cleaned;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// ---------------------------------------------------------------------------
// Builders
// ---------------------------------------------------------------------------

/** Compute the deterministic local-filename suggestion for a single
 *  export. The builder is exported because tests + the stub script
 *  re-derive the same filename when validating args. */
export function buildVisualExportFilenameSuggestion(
  input: VisualExportCommandInput,
): string {
  const fmt = input.format ?? "png";
  const idStub = UUID_RE.test(input.contentItemId)
    ? input.contentItemId.slice(0, 8)
    : slugify(input.contentItemId, 8);
  const tpl = input.templateId
    ? slugify(input.templateId, 32)
    : slugify(`${input.mode}_default`, 32);
  const parts: string[] = [tpl, idStub];
  if (typeof input.slideNumber === "number" && input.slideNumber > 0) {
    parts.push(`slide${String(input.slideNumber).padStart(2, "0")}`);
  } else if (typeof input.frameNumber === "number" && input.frameNumber > 0) {
    parts.push(`frame${String(input.frameNumber).padStart(2, "0")}`);
  }
  return `${parts.join("-")}.${fmt}`;
}

/** Normalise / sanitize the html-snapshot path. Returns the cleaned
 *  string, or empty when nothing usable remains. Mirrors the
 *  validation the Python stub performs (POSIX separators,
 *  `[A-Za-z0-9_./-]` only, no traversal prefixes). */
function sanitizeHtmlSnapshotPath(raw: string | undefined): string {
  if (!raw) return "";
  let cleaned = raw.replace(/\\+/g, "/");
  cleaned = cleaned.replace(/\.+\//g, "").replace(/^\/+/, "");
  cleaned = cleaned.replace(/["'`]/g, "");
  cleaned = cleaned.replace(/\s+/g, "-");
  // Drop characters outside the stub's whitelist.
  cleaned = cleaned.replace(/[^A-Za-z0-9_./\-]/g, "");
  return cleaned;
}

/** Compute the planned local on-disk path (`outputDir/filename`).
 *  Pure derivation — never touches the filesystem. Exposed so the
 *  dashboard can surface the destination next to the command. */
export function buildVisualExportPlannedOutputPath(
  input: VisualExportCommandInput,
): string {
  const outDir = (sanitizeOutputDir(input.outputDir) || "./exports").replace(
    /\/+$/,
    "",
  );
  return `${outDir}/${buildVisualExportFilenameSuggestion(input)}`;
}

/** Compute the ordered argv that the local stub script accepts.
 *  Useful for tests that bypass the multi-line bash rendering. */
export function buildVisualExportArgs(
  input: VisualExportCommandInput,
): string[] {
  const fmt = input.format ?? "png";
  const dryRun = input.dryRun !== false; // default true
  const outDir = sanitizeOutputDir(input.outputDir) || "./exports";
  const argv: string[] = [
    "--content-item-id",
    input.contentItemId,
    "--preview-url",
    input.previewUrl,
    "--mode",
    input.mode,
    "--theme-id",
    input.themeId,
    "--format",
    fmt,
    "--output-dir",
    outDir,
  ];
  if (input.templateId) argv.push("--template-id", input.templateId);
  if (typeof input.width === "number" && input.width > 0) {
    argv.push("--width", String(input.width));
  }
  if (typeof input.height === "number" && input.height > 0) {
    argv.push("--height", String(input.height));
  }
  if (typeof input.slideNumber === "number" && input.slideNumber > 0) {
    argv.push("--slide-number", String(input.slideNumber));
  }
  if (typeof input.frameNumber === "number" && input.frameNumber > 0) {
    argv.push("--frame-number", String(input.frameNumber));
  }
  const snapshot = sanitizeHtmlSnapshotPath(input.htmlSnapshotPath);
  if (snapshot) argv.push("--html-snapshot-path", snapshot);
  if (dryRun) argv.push("--dry-run");
  if (input.emitJson) argv.push("--json");
  return argv;
}

/** Shell-quote a single argument the conservative way — wrap in
 *  double quotes and escape embedded quotes + backslashes. Used only
 *  for the human-readable `command` string (argv is shell-safe by
 *  construction).
 */
function shellQuote(arg: string): string {
  if (arg === "" || /[^A-Za-z0-9_\-./:=]/.test(arg)) {
    return `"${arg.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }
  return arg;
}

/** Build the full command artefact (script path + argv + bash string
 *  + filename). Pure: no clipboard, no DOM, no fetch, no exec. */
export function buildVisualExportCommand(
  input: VisualExportCommandInput,
): VisualExportCommand {
  const scriptPath = "scripts/export_visual_preview_stub.py";
  const argv = buildVisualExportArgs(input);
  const filenameSuggestion = buildVisualExportFilenameSuggestion(input);
  const plannedOutputPath = buildVisualExportPlannedOutputPath(input);

  // Render the argv as a multi-line bash command. We group each flag
  // with its value on the same line for readability and use `\` line
  // continuations like the existing stub doc-comment does.
  const grouped: string[][] = [];
  for (let i = 0; i < argv.length; ) {
    const flag = argv[i];
    if (flag.startsWith("--") && i + 1 < argv.length && !argv[i + 1].startsWith("--")) {
      grouped.push([flag, argv[i + 1]]);
      i += 2;
    } else {
      grouped.push([flag]);
      i += 1;
    }
  }

  const header = [
    "# Phase 4G — local visual export command (dry-run by default).",
    "# This command does NOT execute from the dashboard. Paste into",
    "# a terminal on the operator's machine. The stub script today",
    "# validates args and prints intent without rendering pixels.",
    "# A real export implementation lands in a later phase (Phase 4H).",
  ].join("\n");

  const body = [`py -3.11 ${scriptPath} \\`]
    .concat(
      grouped.map((g, idx) => {
        const isLast = idx === grouped.length - 1;
        const segment = g.map(shellQuote).join(" ");
        return `  ${segment}${isLast ? "" : " \\"}`;
      }),
    )
    .join("\n");

  const footer = [
    "",
    `# Suggested local filename: ${filenameSuggestion}`,
    `# Planned on-disk path:    ${plannedOutputPath}`,
    "# Notes:",
    "#   - --dry-run is the default. The stub refuses --execute today.",
    "#   - The dashboard never spawns this command for you.",
    "#   - Nothing is uploaded, shared, or published by this step.",
    "#   - The future real exporter (Phase 4I+) will reuse this exact",
    "#     argv contract — flags here will not break.",
  ].join("\n");

  return {
    scriptPath,
    argv,
    command: `${header}\n${body}\n${footer}`,
    filenameSuggestion,
    plannedOutputPath,
    requiresOperatorExecution: true,
  };
}
