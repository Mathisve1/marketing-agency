import Link from "next/link";
import { notFound } from "next/navigation";
import { ClientFeedbackPanel } from "@/components/content/client-feedback-panel";
import { ClientReviewPlayer } from "@/components/content/client-review-player";
import { ClientStatusChip } from "@/components/content/status-chip";
import { NextWeekRequestForm } from "@/components/content/next-week-request-form";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getClientContentItem } from "@/lib/data/content";
import { getContentFeedback } from "@/lib/data/feedback";
import { hasSupabaseEnv } from "@/lib/supabase/server";
import { getDataSource } from "@/lib/data/_source";
import { requireClientPortalAccess } from "@/lib/auth/guard-client-portal";

interface PageProps {
  params: Promise<{ portalSlug: string; contentId: string }>;
}

const PLATFORM_LABEL: Record<string, string> = {
  instagram_reels: "Instagram Reels",
  tiktok: "TikTok",
  meta_ads: "Meta Ads",
  youtube_shorts: "YouTube Shorts",
};

export default async function ClientContentDetail({ params }: PageProps) {
  const { portalSlug, contentId } = await params;
  // Phase 1K — gate at the page so the sibling /login is reachable.
  await requireClientPortalAccess(portalSlug);
  // getClientContentItem validates that the content belongs to this
  // portal's campaign AND is in the client-visible status set. Returns
  // null in every other case → 404.
  const view = await getClientContentItem(portalSlug, contentId);
  if (!view) notFound();

  // Pre-load comments server-side so first render has the thread.
  const comments = await getContentFeedback(contentId);
  // Filter to client-authored entries; operator notes intended for
  // internal use don't belong on the client side.
  const clientVisibleComments = comments.filter((c) => c.author === "client");

  const isLive = getDataSource() === "supabase" && hasSupabaseEnv();
  const platforms = view.platforms
    .map((p) => PLATFORM_LABEL[p] ?? p)
    .join(" · ");
  const scheduled = view.scheduledFor
    ? new Date(view.scheduledFor + "T00:00:00Z").toLocaleDateString("en-GB", {
        weekday: "long",
        day: "numeric",
        month: "long",
      })
    : null;

  // Phase 2G — split the layout based on whether this is a video
  // item or a non-video copy item. Non-video items skip the
  // <video>/<image> player entirely and render the operator-prepared
  // `clientSafeCopyPreview` as the primary surface. The raw
  // caption_draft is NEVER shown for non-video items because it
  // contains operator-internal Copy Draft Agent markup; the client
  // sees only the clean preview the operator explicitly prepared.
  const isCopyOnly =
    view.mediaType === "none" &&
    typeof view.clientSafeCopyPreview === "string" &&
    view.clientSafeCopyPreview.trim() !== "";

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/client/${portalSlug}`}
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← Overview
        </Link>
        <h1 className="mt-1 text-2xl sm:text-3xl font-semibold leading-tight">
          {view.title}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <ClientStatusChip status={view.status} />
          <span className="text-xs text-[color:var(--color-ink-muted)]">
            {platforms}
            {scheduled && <> · scheduled {scheduled}</>}
            {!isCopyOnly && view.durationSec ? (
              <> · {view.durationSec}s</>
            ) : null}
          </span>
        </div>
      </div>

      {isCopyOnly ? (
        // Non-video copy layout: single-column, no media player. The
        // <pre> preserves whitespace from the operator-prepared
        // preview (line breaks, lists, paragraph spacing).
        <div className="grid lg:grid-cols-[1fr_360px] gap-6 items-start">
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Preview</CardTitle>
              </CardHeader>
              <CardBody>
                <pre className="whitespace-pre-wrap break-words text-sm leading-relaxed font-sans">
                  {view.clientSafeCopyPreview}
                </pre>
              </CardBody>
            </Card>
            {view.hook && (
              <Card>
                <CardHeader>
                  <CardTitle>Hook</CardTitle>
                </CardHeader>
                <CardBody>
                  <p className="text-sm leading-relaxed">{view.hook}</p>
                </CardBody>
              </Card>
            )}
          </div>
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Review this post</CardTitle>
              </CardHeader>
              <CardBody className="space-y-3">
                <p className="text-sm text-[color:var(--color-ink-muted)]">
                  Read the preview, then approve, request changes, or
                  leave a comment below. Your feedback goes directly to
                  the Yuvo team — nothing is published yet.
                </p>
                <ClientFeedbackPanel
                  portalSlug={portalSlug}
                  contentId={contentId}
                  initialStatus={view.status}
                  initialComments={clientVisibleComments}
                  isLive={isLive}
                />
              </CardBody>
            </Card>
          </div>
        </div>
      ) : (
        <div className="grid lg:grid-cols-[360px_1fr] gap-6 items-start">
          <ClientReviewPlayer
            videoUrl={view.videoUrl}
            posterUrl={view.posterUrl}
            mediaType={view.mediaType}
            title={view.title}
            durationSec={view.durationSec}
          />
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Review this video</CardTitle>
              </CardHeader>
              <CardBody className="space-y-3">
                <p className="text-sm text-[color:var(--color-ink-muted)]">
                  Watch the clip on the left, then approve, request changes,
                  or leave a comment below. Your feedback goes directly to
                  the Yuvo team.
                </p>
                <ClientFeedbackPanel
                  portalSlug={portalSlug}
                  contentId={contentId}
                  initialStatus={view.status}
                  initialComments={clientVisibleComments}
                  isLive={isLive}
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Hook</CardTitle>
              </CardHeader>
              <CardBody>
                <p className="text-sm leading-relaxed">{view.hook}</p>
              </CardBody>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Caption draft</CardTitle>
              </CardHeader>
              <CardBody>
                <p className="text-sm leading-relaxed text-[color:var(--color-ink-muted)]">
                  {view.captionDraft}
                </p>
              </CardBody>
            </Card>
          </div>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Anything else for next week?</CardTitle>
        </CardHeader>
        <CardBody className="space-y-3">
          <p className="text-sm text-[color:var(--color-ink-muted)]">
            A different angle, a different length, an ingredient you want
            us to lean into — leave a short note and we&rsquo;ll plan it
            in.
          </p>
          <NextWeekRequestForm portalSlug={portalSlug} isLive={isLive} />
        </CardBody>
      </Card>
    </div>
  );
}
