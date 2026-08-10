// SPDX-License-Identifier: MIT
/**
 * View-only enforcement.
 *
 * The `vss-ui-view` container runs the same image as `vss-ui` but sits behind a
 * public origin, so it must never serve the full app -- video-management alone
 * can delete videos and tear down RTSP streams.
 *
 * This is the single choke point rather than a per-page check: a page added
 * later is blocked by default instead of being publicly routable until someone
 * remembers to gate it.
 *
 * `VSS_VIEW_ONLY` is deliberately NOT a `NEXT_PUBLIC_*` variable. Those are
 * inlined at build time, and both containers share one image -- a build-time
 * value could not distinguish them at runtime.
 */
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/** Paths a view-only deployment may serve. Everything else 404s. */
const VIEW_ONLY_ALLOWED = [
  /^\/view\/[^/]+\/?$/, // the published view page
  /^\/api\/view(\/|$)/, // share service, when proxied through this origin
  /^\/_next\//, // build assets
  /^\/favicon\.ico$/,
  /^\/locales\//, // i18n bundles
];

export function middleware(request: NextRequest) {
  if (process.env.VSS_VIEW_ONLY !== 'true') {
    return NextResponse.next();
  }

  const { pathname } = request.nextUrl;
  if (VIEW_ONLY_ALLOWED.some((pattern) => pattern.test(pathname))) {
    return NextResponse.next();
  }

  // 404 rather than 403: a view-only origin should not advertise that a
  // fuller application exists behind the same image.
  return new NextResponse(null, { status: 404 });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image).*)'],
};
