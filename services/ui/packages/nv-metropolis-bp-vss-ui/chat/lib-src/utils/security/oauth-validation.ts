// SPDX-License-Identifier: MIT
/**
 * OAuth consent URL validation.
 *
 * A consent URL is handed to `window.open`, so anything that parses as a
 * script-bearing scheme executes in our origin. Stricter than media validation:
 * only http(s), no credentials, and no whitespace at all.
 */

/** C0 controls, DEL, and space — none are legal in a URL we would open. */
const DISALLOWED_CHARS = /[\x00-\x20\x7F]/;

const ALLOWED_SCHEMES = new Set(['http:', 'https:']);

export function isValidConsentPromptURL(url: unknown): boolean {
  if (typeof url !== 'string' || url.length === 0) return false;

  // Checked before parsing: the URL parser strips tab/newline and trims
  // surrounding whitespace, which would let a smuggled character through.
  if (DISALLOWED_CHARS.test(url)) return false;

  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }

  if (!ALLOWED_SCHEMES.has(parsed.protocol)) return false;

  // Credentials in a consent URL are either a phishing lure or an attempt to
  // make the host look like somewhere the user trusts.
  if (parsed.username !== '' || parsed.password !== '') return false;

  return parsed.hostname !== '';
}
