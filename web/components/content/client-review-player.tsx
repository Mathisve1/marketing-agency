// Yuvo Studio — Phase 1O client review player.
//
// CLIENT-SAFE. Renders a real `<video>` element when the operator has
// shared a CDN MP4 URL, an `<Image>` poster fallback when only a
// thumbnail is available, and an empty placeholder when neither
// exists. No operator-only metadata reaches this component — only
// `videoUrl`, `posterUrl`, `mediaType`, `title`, `durationSec`.
//
// Mobile-friendly 9:16 frame, no controls overlap, no autoplay (so the
// client decides). preload="metadata" lets the poster show fast
// without buffering the whole MP4.

import Image from "next/image";
import type { ClientMediaType } from "@/lib/types";

interface Props {
  videoUrl?: string;
  posterUrl?: string;
  mediaType: ClientMediaType;
  title: string;
  durationSec: number;
}

export function ClientReviewPlayer({
  videoUrl,
  posterUrl,
  mediaType,
  title,
  durationSec,
}: Props) {
  return (
    <div className="rounded-lg overflow-hidden border border-[color:var(--color-hairline)] bg-black">
      <div className="relative aspect-[9/16] w-full max-w-[360px] mx-auto bg-black">
        {mediaType === "video" && videoUrl ? (
          <video
            controls
            playsInline
            preload="metadata"
            poster={posterUrl}
            className="absolute inset-0 w-full h-full object-contain bg-black"
            aria-label={title}
          >
            <source src={videoUrl} type="video/mp4" />
            Your browser does not support inline video. The clip is at{" "}
            <a href={videoUrl} className="underline">
              this link
            </a>
            .
          </video>
        ) : mediaType === "image" && posterUrl ? (
          <Image
            src={posterUrl}
            alt={title}
            fill
            sizes="360px"
            className="object-cover"
            priority={false}
            unoptimized
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-[color:var(--color-ink-faint)] text-sm bg-[color:var(--color-cream-soft)]">
            Nothing to preview yet.
          </div>
        )}
        <div className="absolute bottom-2 right-2 bg-black/65 text-white text-[11px] px-2 py-0.5 rounded-md">
          {durationSec}s · 9:16
        </div>
      </div>
    </div>
  );
}
