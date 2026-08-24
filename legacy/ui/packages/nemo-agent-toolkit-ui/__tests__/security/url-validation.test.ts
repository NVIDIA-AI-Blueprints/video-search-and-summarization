/**
 * URL Validation Tests
 * 
 * Tests for Media URL, OAuth URL, and Path Normalization validation.
 * HTTP/WebSocket path validation is handled server-side in the proxy layer.
 */
import { isValidMediaURL } from '@/utils/media/validation';
import { isValidConsentPromptURL } from '@/utils/security/oauth-validation';

const {
  validateProxyHttpPath,
} = require('../../utils/security/url-validation');

describe('URL Validation Tests', () => {
  const originalEnv = process.env.NODE_ENV;

  beforeAll(() => {
    // @ts-ignore - Modifying NODE_ENV for test purposes
    process.env.NODE_ENV = 'development';
  });

  afterAll(() => {
    // @ts-ignore - Restoring NODE_ENV after test
    process.env.NODE_ENV = originalEnv;
  });

  // ============================================================================
  // MEDIA URL VALIDATION
  // ============================================================================
  describe('Media URL Validation', () => {
    describe('Positive Tests - Valid media URLs should pass', () => {
      it('accepts valid HTTPS image URLs', () => {
        const validUrls = [
          'https://cdn.example.com/image.jpg',
          'https://images.unsplash.com/photo.png',
          'https://static.website.com/video.mp4',
          'https://media.company.org/assets/logo.svg',
          // truncated
          'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA',
          'data:image/jpeg;base64,iVBORw0KGgoAAAANSUhEUgAABXg'
        ];

        validUrls.forEach(url => {
          expect(isValidMediaURL(url)).toBe(true);
        });
      });

      it('accepts valid HTTP media URLs', () => {
        expect(isValidMediaURL('http://example.com/image.jpg')).toBe(true);
        expect(isValidMediaURL('http://media.site.com/video.webm')).toBe(true);
      });

      it('accepts localhost URLs for development environments', () => {
        const devUrls = [
          'http://localhost/image.jpg',
          'https://localhost:3000/video.mp4',
          'http://127.0.0.1:8080/media.png',
          'https://127.1.1.1:5000/asset.gif',
          'http://[::1]:3000/image.jpg'
        ];

        devUrls.forEach(url => {
          expect(isValidMediaURL(url)).toBe(true);
        });
      });
    });

    describe('Negative Tests - Invalid URLs should be blocked', () => {
      it('blocks dangerous protocol schemes', () => {
        const dangerousUrls = [
          'javascript:alert("xss")',
          'data:image/svg+xml,<svg><script>alert("xss")</script></svg>',
          'data:image/svg+xml,<svg><SCRIPT>alert("xss")</SCRIPT></svg>',
          'data:image/svg+xml,<svg onload="alert(1)"></svg>',
          // temorary, add to allowed list when we add support for SVG
          'data:image/svg+xml,<svg><text>Hello World</text></svg>',
          'file:///etc/passwd',
          'ftp://evil.com/image.jpg'
        ];

        dangerousUrls.forEach(url => {
          expect(isValidMediaURL(url)).toBe(false);
        });
      });

      it('blocks URLs with embedded credentials', () => {
        const credentialUrls = [
          'https://user:password@example.com/image.jpg',
          'http://admin:secret@cdn.com/video.mp4'
        ];

        credentialUrls.forEach(url => {
          expect(isValidMediaURL(url)).toBe(false);
        });
      });

      it('blocks reserved IP ranges to prevent SSRF', () => {
        const ssrfUrls = [
          'http://0.0.0.0/image.jpg',
          'https://224.0.0.1/video.mp4', // Multicast
          'http://240.1.1.1/media.png',   // Multicast
          'https://255.255.255.255/image.gif' // Broadcast
        ];

        ssrfUrls.forEach(url => {
          expect(isValidMediaURL(url)).toBe(false);
        });
      });

      it('blocks malformed and empty URLs', () => {
        const invalidUrls = [
          '',
          'not-a-url',
          'ht tp://broken.com/image.jpg',
          '://no-protocol.com/video.mp4',
          null,
          undefined,
          123
        ];

        invalidUrls.forEach(url => {
          expect(isValidMediaURL(url as any)).toBe(false);
        });
      });

      it('blocks URLs with control characters', () => {
        const controlCharUrls = [
          'https://example.com\x00/image.jpg',
          'https://example.com\x0a/video.mp4',
          'https://example.com\x0d/media.png',
          'https://example.com\x7f/image.gif'
        ];

        controlCharUrls.forEach(url => {
          expect(isValidMediaURL(url)).toBe(false);
        });
      });
    });
  });

  // ============================================================================
  // OAUTH URL VALIDATION
  // ============================================================================
  describe('OAuth URL Validation', () => {
    describe('Positive Tests - Valid URLs should pass', () => {
      it('accepts valid HTTPS OAuth URLs', () => {
        const validUrls = [
          'https://accounts.google.com/oauth/authorize',
          'https://login.microsoftonline.com/oauth2/authorize',
          'https://github.com/login/oauth/authorize',
          'https://api.example.com/oauth/consent'
        ];

        validUrls.forEach(url => {
          expect(isValidConsentPromptURL(url)).toBe(true);
        });
      });

      it('accepts valid HTTP URLs', () => {
        expect(isValidConsentPromptURL('http://example.com/oauth')).toBe(true);
      });
    });

    describe('Negative Tests - Invalid URLs should be blocked', () => {
      it('blocks dangerous protocol schemes', () => {
        const dangerousUrls = [
          'javascript:alert("xss")',
          'data:text/html,<script>alert("xss")</script>',
          'vbscript:msgbox("xss")',
          'file:///etc/passwd',
          'ftp://evil.com/malware'
        ];

        dangerousUrls.forEach(url => {
          expect(isValidConsentPromptURL(url)).toBe(false);
        });
      });

      it('blocks URLs with embedded credentials', () => {
        const credentialUrls = [
          'https://user:password@example.com/oauth',
          'http://admin:secret@malicious.com',
          'https://attacker:token@legitimate-site.com/oauth'
        ];

        credentialUrls.forEach(url => {
          expect(isValidConsentPromptURL(url)).toBe(false);
        });
      });

      it('blocks malformed URLs', () => {
        const malformedUrls = [
          '',
          'not-a-url',
          'ht tp://broken.com',
          '://no-protocol.com',
          null,
          undefined
        ];

        malformedUrls.forEach(url => {
          expect(isValidConsentPromptURL(url as any)).toBe(false);
        });
      });

      it('blocks URLs with control characters', () => {
        const controlCharUrls = [
          'https://example.com /oauth',  // space character
          'https://example.com\t/oauth', // tab character
          'https://example.com\n/oauth', // newline character
          'https://example.com\r/oauth'  // carriage return
        ];

        controlCharUrls.forEach(url => {
          expect(isValidConsentPromptURL(url)).toBe(false);
        });
      });
    });
  });

  // ============================================================================
  // PATH NORMALIZATION TESTS
  // ============================================================================
  describe('Path Normalization Tests', () => {
    describe('Path Traversal Prevention', () => {
      it('should block simple path traversal', () => {
        const result = validateProxyHttpPath('/api/../admin');
        expect(result.isValid).toBe(false);
      });

      it('should block encoded path traversal', () => {
        const result = validateProxyHttpPath('/api/%2E%2E/admin');
        expect(result.isValid).toBe(false);
      });

      it('should block double-encoded path traversal', () => {
        const result = validateProxyHttpPath('/api/%252E%252E%252Fadmin');
        expect(result.isValid).toBe(false);
      });

      it('should block complex traversal attempts', () => {
        const result = validateProxyHttpPath('/api/chat/../../admin');
        expect(result.isValid).toBe(false);
      });
    });

    describe('Valid Paths', () => {
      it('should allow valid paths', () => {
        const result = validateProxyHttpPath('/api/chat/stream');
        expect(result.isValid).toBe(true);
      });

      it('should normalize and allow paths with dots', () => {
        const result = validateProxyHttpPath('/api/./chat/stream');
        expect(result.isValid).toBe(true);
      });

      it('should handle query parameters', () => {
        const result = validateProxyHttpPath('/api/chat/stream?session=123');
        expect(result.isValid).toBe(true);
      });
    });

    describe('Edge Cases', () => {
      it('should reject empty path', () => {
        const result = validateProxyHttpPath('');
        expect(result.isValid).toBe(false);
      });

      it('should reject non-string input', () => {
        const result = validateProxyHttpPath(null as any);
        expect(result.isValid).toBe(false);
      });
    });
  });
});
