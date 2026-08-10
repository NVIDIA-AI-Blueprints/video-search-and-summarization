// SPDX-License-Identifier: MIT
/**
 * /view/<id> - read-only page for a published result set.
 *
 * Served by the `vss-ui-view` container (NEXT_PUBLIC_VIEW_ONLY=true) behind the
 * public origin. Deliberately does not mount Home: the full app carries
 * destructive controls (video delete, RTSP management) that must never be
 * publicly routable. This page renders results and nothing else.
 *
 * Open Graph tags are the payload for Telegram and WhatsApp, which cannot
 * render HTML and unfurl a link into a preview card instead.
 */
import { SharedViewComponent } from '@nv-metropolis-bp-vss-ui/all';
import type { GetServerSideProps } from 'next';
import Head from 'next/head';
import React from 'react';

interface ViewPageProps {
  viewId: string;
  sessionId: string | null;
  apiBase: string;
  publicBaseUrl: string;
  title: string;
  description: string;
}

const ViewPage = ({ viewId, sessionId, apiBase, publicBaseUrl, title, description }: ViewPageProps) => {
  // Absolute URLs: Telegram and WhatsApp fetch OG images from their own
  // servers, so a relative path never resolves for them.
  const previewImage = publicBaseUrl ? `${publicBaseUrl}/api/view/${viewId}/preview.png` : '';
  const canonical = publicBaseUrl ? `${publicBaseUrl}/view/${viewId}` : '';

  return (
    <>
      <Head>
        <title>{title}</title>
        <meta name="description" content={description} />
        <meta property="og:type" content="website" />
        <meta property="og:title" content={title} />
        <meta property="og:description" content={description} />
        {canonical ? <meta property="og:url" content={canonical} /> : null}
        {previewImage ? <meta property="og:image" content={previewImage} /> : null}
        {previewImage ? <meta property="og:image:width" content="1200" /> : null}
        {previewImage ? <meta property="og:image:height" content="630" /> : null}
        <meta name="twitter:card" content="summary_large_image" />
        {/* A shared view is unlisted by design; its id is the only credential. */}
        <meta name="robots" content="noindex, nofollow" />
      </Head>
      <main className="h-screen w-screen overflow-hidden">
        <SharedViewComponent
          viewId={sessionId ? undefined : viewId}
          sessionId={sessionId ?? undefined}
          apiBase={apiBase}
          isDark
        />
      </main>
    </>
  );
};

export const getServerSideProps: GetServerSideProps<ViewPageProps> = async (ctx) => {
  const rawId = ctx.params?.id;
  const viewId = Array.isArray(rawId) ? rawId[0] : (rawId ?? '');
  if (!viewId) {
    return { notFound: true };
  }

  const rawSession = ctx.query?.session;
  const sessionId = Array.isArray(rawSession) ? rawSession[0] : (rawSession ?? null);

  const apiBase = process.env.NEXT_PUBLIC_SHARE_API_BASE ?? '';
  const publicBaseUrl = (process.env.NEXT_PUBLIC_SHARE_PUBLIC_BASE_URL ?? '').replace(/\/$/, '');

  // Fetch server-side purely so the OG card carries a real title and count --
  // the crawler never executes the client fetch. Rendering still works when
  // this fails; only the unfurled preview text degrades.
  let title = 'VSS results';
  let description = 'Shared video search results from NVIDIA VSS.';

  const internalBase = process.env.SHARE_INTERNAL_BASE_URL ?? apiBase;
  if (internalBase) {
    try {
      const response = await fetch(`${internalBase.replace(/\/$/, '')}/api/view/${encodeURIComponent(viewId)}`);
      if (response.ok) {
        const payload = await response.json();
        title = payload.title || title;
        const count = payload.count ?? 0;
        description = `${count} result${count === 1 ? '' : 's'} from NVIDIA VSS.`;
      } else if (response.status === 404) {
        return { notFound: true };
      }
    } catch {
      // Leave the defaults; the page still renders client-side.
    }
  }

  return { props: { viewId, sessionId, apiBase, publicBaseUrl, title, description } };
};

export default ViewPage;
