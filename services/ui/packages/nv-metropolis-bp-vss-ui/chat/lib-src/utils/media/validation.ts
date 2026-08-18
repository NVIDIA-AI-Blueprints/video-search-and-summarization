// SPDX-License-Identifier: MIT
/**
 * Media URL validation.
 *
 * Media URLs reach `<img>` / `<video>` src, so a bad one is an XSS or SSRF
 * vector rather than a broken image. Everything here is deny-by-default: a URL
 * has to parse, use an allowed scheme, and resolve to a host we are willing to
 * fetch from.
 */

/** C0 controls plus DEL. */
const CONTROL_CHARS = /[\x00-\x1F\x7F]/;

const ALLOWED_SCHEMES = new Set(['http:', 'https:']);

/**
 * Raster formats only. SVG is deliberately excluded: it can carry <script> and
 * event handlers, so a data: SVG is an XSS payload wearing an image's clothes.
 */
const ALLOWED_DATA_MEDIA_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/gif',
  'image/webp',
  'image/avif',
]);

const DATA_URL = /^data:([a-z0-9.+-]+\/[a-z0-9.+-]+)\s*;\s*base64\s*,/i;

/**
 * Blocked IPv4 space. Loopback (127/8) and RFC1918 are intentionally *allowed*:
 * VSS deployments serve media from private addresses and from localhost during
 * development, so blocking them would break the product to no benefit.
 *
 * What is blocked is space that should never host media and is a classic SSRF
 * target: "this host" (0/8), and everything from 224.0.0.0 up — multicast,
 * reserved, and the broadcast address.
 */
function isBlockedIPv4(hostname: string): boolean {
  const octets = hostname.split('.');
  if (octets.length !== 4) return false;

  const parsed = octets.map((o) => (/^\d{1,3}$/.test(o) ? Number(o) : NaN));
  if (parsed.some((n) => Number.isNaN(n) || n > 255)) return false;

  const [first] = parsed;
  return first === 0 || first >= 224;
}

/** Strips the brackets WHATWG keeps around IPv6 hosts. */
function bareHostname(hostname: string): string {
  return hostname.startsWith('[') && hostname.endsWith(']')
    ? hostname.slice(1, -1)
    : hostname;
}

export function isValidMediaURL(url: unknown): boolean {
  if (typeof url !== 'string' || url.length === 0) return false;

  // Checked before parsing: the URL parser silently strips tabs and newlines,
  // so a smuggled control character would survive as a "valid" URL.
  if (CONTROL_CHARS.test(url) || url.includes(' ')) return false;

  const dataMatch = DATA_URL.exec(url);
  if (dataMatch) {
    return ALLOWED_DATA_MEDIA_TYPES.has(dataMatch[1].toLowerCase());
  }
  // A data: URL that is not base64-encoded raster media (e.g. inline SVG markup).
  if (/^data:/i.test(url)) return false;

  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }

  if (!ALLOWED_SCHEMES.has(parsed.protocol)) return false;

  // Credentials in a media URL mean someone is trying to leak them to whatever
  // renders it, or to smuggle a different host past a naive prefix check.
  if (parsed.username !== '' || parsed.password !== '') return false;

  const hostname = bareHostname(parsed.hostname);
  if (hostname === '') return false;
  if (isBlockedIPv4(hostname)) return false;

  return true;
}
