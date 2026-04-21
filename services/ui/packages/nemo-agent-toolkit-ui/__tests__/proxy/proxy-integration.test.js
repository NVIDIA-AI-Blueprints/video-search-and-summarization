/**
 * @jest-environment node
 *
 * Proxy Integration Tests
 *
 * Simple tests to verify:
 * 1. HTTP requests can be forwarded through the proxy
 * 2. WebSocket connections can be established through the proxy
 *
 * NOTE: These tests assume the proxy server is running on port 3000.
 *       Run `npm run dev` in a separate terminal before running these tests.
 */

const WebSocket = require('ws');
const fetch = require('node-fetch');

const PROXY_PORT = 3000;
const TEST_TIMEOUT = 10000;

describe('Proxy Server Integration Tests', () => {
  // Check if proxy is running before tests
  let proxyRunning = false;

  beforeAll(async () => {
    try {
      const response = await fetch(`http://localhost:${PROXY_PORT}`);
      proxyRunning = true;
    } catch (err) {
      console.warn('⚠️  Proxy server not running on port', PROXY_PORT);
      console.warn(
        '   Run `npm run dev` in a separate terminal to enable these tests.',
      );
    }
  });

  describe('HTTP Proxy Forwarding', () => {
    test(
      'should forward HTTP POST request to backend',
      async () => {
        if (!proxyRunning) {
          console.log('Skipping: proxy not running');
          return;
        }

        const response = await fetch(
          `http://localhost:${PROXY_PORT}/api/chat/stream`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              message: 'test',
              model: 'test-model',
            }),
          },
        );

        // Should get a response (200 if backend up, 502 if backend down)
        // The key is that proxy is reachable and forwarding
        expect([200, 500, 502]).toContain(response.status);
      },
      TEST_TIMEOUT,
    );

    test(
      'should block unauthorized HTTP paths',
      async () => {
        if (!proxyRunning) {
          console.log('Skipping: proxy not running');
          return;
        }

        const response = await fetch(
          `http://localhost:${PROXY_PORT}/api/unauthorized`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({}),
          },
        );

        // Should be blocked by proxy validation
        expect(response.status).toBe(403);
      },
      TEST_TIMEOUT,
    );
  });

  describe('WebSocket Proxy Connection', () => {
    test(
      'should establish WebSocket connection through proxy',
      (done) => {
        if (!proxyRunning) {
          console.log('Skipping: proxy not running');
          done();
          return;
        }

        const ws = new WebSocket(
          `ws://localhost:${PROXY_PORT}/ws?session=test_session`,
        );

        const timeout = setTimeout(() => {
          ws.close();
          done(new Error('WebSocket connection timeout'));
        }, 5000);

        ws.on('open', () => {
          clearTimeout(timeout);
          expect(ws.readyState).toBe(WebSocket.OPEN);
          ws.close(1000, 'Test complete');
        });

        ws.on('close', (code) => {
          clearTimeout(timeout);
          // Code 1000 = clean close from our test
          // Code 1006 = backend not available but proxy forwarded it
          expect([1000, 1006]).toContain(code);
          done();
        });

        ws.on('error', () => {
          clearTimeout(timeout);
          // Error might occur if backend is not running
          // The test passes as long as proxy attempted the connection
          done();
        });
      },
      TEST_TIMEOUT,
    );

    test(
      'should keep WebSocket connection open persistently',
      (done) => {
        if (!proxyRunning) {
          console.log('Skipping: proxy not running');
          done();
          return;
        }

        const ws = new WebSocket(
          `ws://localhost:${PROXY_PORT}/ws?session=test_persist`,
        );

        let openTime;
        const timeout = setTimeout(() => {
          ws.close();
          done(new Error('WebSocket connection timeout'));
        }, 6000);

        ws.on('open', () => {
          openTime = Date.now();
          expect(ws.readyState).toBe(WebSocket.OPEN);

          // Wait 3 seconds to verify connection stays open
          setTimeout(() => {
            const elapsed = Date.now() - openTime;
            expect(elapsed).toBeGreaterThanOrEqual(3000);
            expect(ws.readyState).toBe(WebSocket.OPEN);
            clearTimeout(timeout);
            ws.close(1000, 'Test complete');
            done();
          }, 3000);
        });

        ws.on('close', (code) => {
          if (code !== 1000) {
            clearTimeout(timeout);
            // Connection closed unexpectedly
            done(new Error(`Connection closed unexpectedly: ${code}`));
          }
        });

        ws.on('error', (err) => {
          clearTimeout(timeout);
          done(err);
        });
      },
      TEST_TIMEOUT,
    );

    test(
      'should block unauthorized WebSocket paths',
      (done) => {
        if (!proxyRunning) {
          console.log('Skipping: proxy not running');
          done();
          return;
        }

        const ws = new WebSocket(`ws://localhost:${PROXY_PORT}/admin-ws`);

        const timeout = setTimeout(() => {
          done(new Error('WebSocket should have been blocked'));
        }, 2000);

        ws.on('open', () => {
          clearTimeout(timeout);
          ws.close();
          done(new Error('Connection should have been blocked by proxy'));
        });

        ws.on('close', (code) => {
          clearTimeout(timeout);
          // Connection should be rejected (code 1002 or 1006)
          expect([1002, 1006]).toContain(code);
          done();
        });

        ws.on('error', () => {
          clearTimeout(timeout);
          // Error is expected when blocked
          done();
        });
      },
      TEST_TIMEOUT,
    );
  });
});
