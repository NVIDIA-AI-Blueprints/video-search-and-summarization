// SPDX-License-Identifier: MIT
/**
 * Proxy path validation.
 *
 * The chat proxy forwards a caller-supplied path to the agent backend, so a
 * traversal sequence that survives to the upstream request can reach endpoints
 * the proxy was never meant to expose.
 */

export type PathValidationResult = {
  isValid: boolean;
  /** Present when invalid; safe to log, never echoed to the caller verbatim. */
  reason?: string;
};

/** Bound on decode passes — enough for double-encoding, short of a decode bomb. */
const MAX_DECODE_PASSES = 3;

const TRAVERSAL = /(^|[/\\])\.\.([/\\]|$)/;

/**
 * Percent-decodes repeatedly so that `%252E%252E%252F` ("%2E%2E%2F" -> "../")
 * is caught. Decoding stops early once a pass changes nothing, and a malformed
 * escape aborts rather than throwing.
 */
function decodeDeep(path: string): string {
  let current = path;

  for (let pass = 0; pass < MAX_DECODE_PASSES; pass += 1) {
    let next: string;
    try {
      next = decodeURIComponent(current);
    } catch {
      // Malformed escape sequence. Treat what we have as final; the traversal
      // check below still runs against it.
      break;
    }
    if (next === current) break;
    current = next;
  }

  return current;
}

export function validateProxyHttpPath(path: unknown): PathValidationResult {
  if (typeof path !== 'string' || path.length === 0) {
    return { isValid: false, reason: 'path must be a non-empty string' };
  }

  // The query string is forwarded verbatim and is not part of the path the
  // upstream resolves, so traversal checks apply to the path portion only.
  const [pathname] = path.split('?', 1);
  const decoded = decodeDeep(pathname);

  if (decoded.includes('\0')) {
    return { isValid: false, reason: 'path contains a null byte' };
  }

  if (TRAVERSAL.test(decoded)) {
    return { isValid: false, reason: 'path contains a traversal sequence' };
  }

  return { isValid: true };
}
