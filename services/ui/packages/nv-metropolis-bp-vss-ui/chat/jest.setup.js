// SPDX-License-Identifier: MIT
require('@testing-library/jest-dom');

// whatwg-fetch replaces Response with one that cannot accept a ReadableStream
// body, so it is loaded only for DOM tests. Server tests declare
// `@jest-environment node` and use Node's native fetch primitives instead.
if (typeof globalThis.window !== 'undefined') {
  require('whatwg-fetch');
}

// jsdom ships no web streams; the SSE decoding path needs them.
const { ReadableStream, WritableStream, TransformStream } = require('node:stream/web');
globalThis.ReadableStream = globalThis.ReadableStream || ReadableStream;
globalThis.WritableStream = globalThis.WritableStream || WritableStream;
globalThis.TransformStream = globalThis.TransformStream || TransformStream;

// Node's own codecs: the decoder must honour { stream: true } so multi-byte
// characters split across chunks survive reassembly.
const { TextEncoder, TextDecoder } = require('node:util');
globalThis.TextEncoder = globalThis.TextEncoder || TextEncoder;
globalThis.TextDecoder = globalThis.TextDecoder || TextDecoder;

globalThis.IntersectionObserver = jest.fn(() => ({
  disconnect: jest.fn(),
  observe: jest.fn(),
  unobserve: jest.fn(),
}));

globalThis.ResizeObserver = jest.fn(() => ({
  disconnect: jest.fn(),
  observe: jest.fn(),
  unobserve: jest.fn(),
}));

if (typeof globalThis.window !== 'undefined') {
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    value: jest.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
  });

  Object.defineProperty(globalThis, 'scrollTo', { writable: true, value: jest.fn() });

  const storageMock = {
    getItem: jest.fn(),
    setItem: jest.fn(),
    removeItem: jest.fn(),
    clear: jest.fn(),
  };
  Object.defineProperty(globalThis, 'sessionStorage', { value: storageMock });
  Object.defineProperty(globalThis, 'localStorage', { value: storageMock });

  Object.defineProperty(globalThis, 'open', {
    writable: true,
    value: jest.fn(() => ({ close: jest.fn(), closed: false })),
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  if (typeof globalThis.window !== 'undefined' && globalThis.localStorage) {
    globalThis.localStorage.getItem.mockClear();
    globalThis.localStorage.setItem.mockClear();
    globalThis.localStorage.removeItem.mockClear();
    globalThis.localStorage.clear.mockClear();
  }
});
