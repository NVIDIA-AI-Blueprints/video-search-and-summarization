const {
  ALLOWED_PATHS,
  HTTP_PROXY_PATH,
  WEBSOCKET_PROXY_PATH,
} = require('../../constants');

/**
 * @typedef {Object} ValidationResult
 * @property {boolean} isValid
 * @property {string} [error]
 */

/**
 * SSRF Prevention: Validates HTTP proxy paths
 *
 * Ensures incoming requests only access allowed backend endpoints.
 * Uses URL constructor for safe normalization of:
 * - Percent-encoding/decoding
 * - Duplicate slashes
 * - Dot-segments (., ..)
 * - Path traversal attempts
 *
 * @param {string} pathname - The full pathname from the request (e.g., '/api/chat/stream')
 * @returns {ValidationResult} Validation result with error message if invalid
 */
function validateProxyHttpPath(pathname) {
  if (typeof pathname !== 'string' || pathname.length === 0) {
    return {
      isValid: false,
      error: 'Path must be a non-empty string',
    };
  }

  // Use URL constructor to safely normalize the path
  let normalizedPath;
  try {
    const url = new URL(pathname, 'http://localhost');
    normalizedPath = url.pathname;
  } catch (err) {
    return {
      isValid: false,
      error: 'Invalid or malformed path',
    };
  }

  // Must start with /api/
  if (!normalizedPath.startsWith(HTTP_PROXY_PATH + '/')) {
    return {
      isValid: false,
      error: `Path must start with ${HTTP_PROXY_PATH}/`,
    };
  }

  // Strip /api prefix to get backend path
  const backendPath = normalizedPath.substring(HTTP_PROXY_PATH.length);

  // Detect any remaining traversal attempts (shouldn't happen after URL normalization, but defense in depth)
  if (backendPath.includes('..')) {
    return {
      isValid: false,
      error: 'Path traversal is not allowed',
    };
  }

  // Check against allowlist
  const isAllowed = ALLOWED_PATHS.some(
    (allowed) =>
      backendPath === allowed || backendPath.startsWith(allowed + '/'),
  );

  if (!isAllowed) {
    return {
      isValid: false,
      error: `Backend path '${backendPath}' is not in allowed list`,
    };
  }

  return { isValid: true };
}

/**
 * SSRF Prevention: Validates WebSocket proxy path
 *
 * Ensures WebSocket connections only use the allowed endpoint.
 * Used by proxy server to prevent unauthorized WebSocket access.
 *
 * @param {string} pathname - The pathname from the WebSocket upgrade request
 * @returns {ValidationResult} Validation result with error message if invalid
 */
function validateProxyWebSocketPath(pathname) {
  if (pathname !== WEBSOCKET_PROXY_PATH) {
    return {
      isValid: false,
      error: `WebSocket path '${pathname}' is not allowed. Expected: ${WEBSOCKET_PROXY_PATH}`,
    };
  }

  return { isValid: true };
}

/**
 * SSRF Prevention: Validates backend URLs
 *
 * Ensures server-side fetch requests only target safe URLs.
 * Use this before making any fetch() calls in API routes.
 *
 * @param {string} url - The URL to validate
 * @returns {ValidationResult} Validation result with error message if invalid
 */
function validateBackendUrl(url) {
  let parsedUrl;

  try {
    parsedUrl = new URL(url);
  } catch (err) {
    return {
      isValid: false,
      error: 'Invalid URL format',
    };
  }

  // Only allow http/https protocols
  if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
    return {
      isValid: false,
      error: `Protocol '${parsedUrl.protocol}' is not allowed. Use http or https.`,
    };
  }

  return { isValid: true };
}

module.exports = {
  validateProxyHttpPath,
  validateProxyWebSocketPath,
  validateBackendUrl,
};
