/**
 * @jest-environment node
 */
import { NextRequest } from 'next/server';

import { SESSION_COOKIE_NAME } from '../lib-src/constants';
import { chatSessionMiddleware } from '../lib-src/middleware';

function request(path: string, cookie?: string): NextRequest {
  const headers = new Headers();
  if (cookie) headers.set('cookie', `${SESSION_COOKIE_NAME}=${cookie}`);
  return new NextRequest(new URL(`http://localhost:3000${path}`), { headers });
}

describe('chatSessionMiddleware', () => {
  it('issues a session cookie when the visitor has none', () => {
    const response = chatSessionMiddleware(request('/'));
    const cookie = response.cookies.get(SESSION_COOKIE_NAME);

    expect(cookie?.value).toMatch(/^session_/);
  });

  it('keeps an existing session rather than reissuing', () => {
    const response = chatSessionMiddleware(request('/', 'session_existing'));

    expect(response.cookies.get(SESSION_COOKIE_NAME)).toBeUndefined();
  });

  it('mirrors the id onto x-session-id for API routes', () => {
    const response = chatSessionMiddleware(request('/api/chat', 'session_existing'));

    expect(response.headers.get('x-session-id')).toBe('session_existing');
  });

  it('mirrors a freshly issued id onto x-session-id', () => {
    const response = chatSessionMiddleware(request('/api/chat'));
    const issued = response.cookies.get(SESSION_COOKIE_NAME)?.value;

    expect(response.headers.get('x-session-id')).toBe(issued);
  });

  it('does not set the header for page routes', () => {
    const response = chatSessionMiddleware(request('/'));

    expect(response.headers.get('x-session-id')).toBeNull();
  });

  it.each(['/_next/static/chunk.js', '/api/auth/callback', '/favicon.ico', '/public/logo.svg'])(
    'skips %s',
    (path) => {
      const response = chatSessionMiddleware(request(path));

      expect(response.cookies.get(SESSION_COOKIE_NAME)).toBeUndefined();
      expect(response.headers.get('x-session-id')).toBeNull();
    },
  );

  it('marks the cookie readable by the client and scoped to the site', () => {
    const cookie = chatSessionMiddleware(request('/')).cookies.get(SESSION_COOKIE_NAME);

    // The chat reads this value to pass as a socket query param.
    expect(cookie?.httpOnly).toBe(false);
    expect(cookie?.sameSite).toBe('lax');
    expect(cookie?.path).toBe('/');
  });
});
