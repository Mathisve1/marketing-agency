// Phase 1A: we do NOT serve production MP4s from web/public. The video
// card renders the local poster image and shows the operator-only file
// path as text. The client-portal variant hides the path entirely.

import Image from "next/image";

interface VideoCardProps {
  posterUrl?: string;
  title: string;
  durationSec: number;
  /** When `audience === "operator"`, the internal MP4 path is shown
   *  beneath the poster. Never on the client portal. */
  audience: "operator" | "client";
  internalRawPath?: string;
  internalAudioFixedPath?: string;
}

export function VideoCard({
  posterUrl,
  title,
  durationSec,
  audience,
  internalRawPath,
  internalAudioFixedPath,
}: VideoCardProps) {
  return (
    <div className="rounded-lg overflow-hidden border border-[color:var(--color-hairline)] bg-white">
      <div className="relative bg-[color:var(--color-cream-soft)] aspect-[9/16] w-full max-w-[280px] mx-auto">
        {posterUrl ? (
          <Image
            src={posterUrl}
            alt={title}
            fill
            sizes="280px"
            className="object-cover"
            priority={false}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-[color:var(--color-ink-faint)] text-sm">
            No preview yet
          </div>
        )}
        <div className="absolute bottom-2 right-2 bg-black/65 text-white text-[11px] px-2 py-0.5 rounded-md">
          {durationSec}s · 9:16
        </div>
      </div>

      {audience === "operator" && (internalRawPath || internalAudioFixedPath) && (
        <div className="text-[11px] text-[color:var(--color-ink-faint)] px-3 py-2 border-t border-[color:var(--color-hairline)] space-y-0.5">
          {internalAudioFixedPath && (
            <div className="truncate">
              <span className="text-[color:var(--color-ink-muted)]">Audio-fixed:</span>{" "}
              <code>{internalAudioFixedPath}</code>
            </div>
          )}
          {internalRawPath && (
            <div className="truncate">
              <span className="text-[color:var(--color-ink-muted)]">Raw:</span>{" "}
              <code>{internalRawPath}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
