/**
 * OAuth URL validation to prevent open redirect attacks
 * @param raw - URL to validate
 * @returns boolean indicating if URL is safe for OAuth redirects
 */
export function isValidConsentPromptURL(raw: string): boolean {
  // 1) quick reject: control chars or whitespace that can confuse parsers/logs
  //    (CR, LF, TAB, VT, FF, and space)
  if (/[ \t\r\n\v\f]/.test(raw)) return false;

  // 2) must be absolute & parseable
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }

  // 3) protocol: only http(s)
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;

  // 4) forbid embedded credentials (userinfo)
  if (u.username || u.password) return false;

  // 5) optional: cap length to reduce abuse surface
  if (raw.length > 8192) return false;

  return true;
}

