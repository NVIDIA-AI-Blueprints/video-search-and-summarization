// SPDX-License-Identifier: MIT
/**
 * Session cookie middleware.
 *
 * The chat tags requests with a stable session id so a conversation can be
 * correlated across reloads and in backend traces. Issued here rather than in
 * the client so the first request of a visit already carries one.
 *
 * Runs on the edge runtime: nothing here may reach a Node built-in.
 */
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

import { SESSION_COOKIE_NAME } from './constants';

/** Thirty days — long enough to outlive a working week away. */
const SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

function newSessionId(): string {
  // randomUUID needs a secure context; middleware may run against plain http
  // in development, where it is absent.
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `session_${crypto.randomUUID()}`;
  }

  return `session_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`;
}

/**
 * Ensures every response carries a session cookie, and mirrors the id onto
 * `x-session-id` for API routes, which cannot read cookies set on the same
 * response they are handling.
 */
export function chatSessionMiddleware(req: NextRequest): NextResponse {
  const { pathname } = req.nextUrl;

  // Static assets and auth callbacks gain nothing from a session and are the
  // hottest paths through middleware.
  if (
    pathname.startsWith('/_next/') ||
    pathname.startsWith('/api/auth/') ||
    pathname.startsWith('/favicon.ico') ||
    pathname.startsWith('/public/')
  ) {
    return NextResponse.next();
  }

  const response = NextResponse.next();
  const existing = req.cookies.get(SESSION_COOKIE_NAME);
  const sessionId = existing?.value ?? newSessionId();

  if (!existing) {
    response.cookies.set(SESSION_COOKIE_NAME, sessionId, {
      // Readable by the chat, which sends it as a query param on the socket.
      httpOnly: false,
      sameSite: 'lax',
      path: '/',
      secure: process.env.NODE_ENV === 'production',
      maxAge: SESSION_MAX_AGE_SECONDS,
    });
  }

  if (pathname.startsWith('/api/')) {
    response.headers.set('x-session-id', sessionId);
  }

  return response;
}

export default chatSessionMiddleware;

/** Everything except static assets and auth routes. */
export const chatSessionMiddlewareMatcher = [
  '/((?!api/auth|_next/static|_next/image|favicon.ico|public).*)',
];
