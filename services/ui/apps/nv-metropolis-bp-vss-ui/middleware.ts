// SPDX-License-Identifier: MIT
/**
 * Issues the chat session cookie. Previously supplied by the NAT UI package's
 * own middleware, which this app inherited implicitly.
 */
export {
  chatSessionMiddleware as default,
  chatSessionMiddlewareMatcher,
} from '@nv-metropolis-bp-vss-ui/chat/middleware';

export const config = {
  matcher: ['/((?!api/auth|_next/static|_next/image|favicon.ico|public).*)'],
};
