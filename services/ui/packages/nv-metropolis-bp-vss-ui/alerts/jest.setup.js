// SPDX-License-Identifier: MIT
require('@testing-library/jest-dom');
require('whatwg-fetch');

// Mock IntersectionObserver
globalThis.IntersectionObserver = jest.fn(() => ({
  disconnect: jest.fn(),
  observe: jest.fn(),
  unobserve: jest.fn(),
}));

// Mock ResizeObserver
globalThis.ResizeObserver = jest.fn(() => ({
  disconnect: jest.fn(),
  observe: jest.fn(),
  unobserve: jest.fn(),
}));

// Mock window-specific globals (only in browser/jsdom environment)
if (globalThis.window !== undefined) {
  // Mock window.matchMedia
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    value: jest.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: jest.fn(), // deprecated
      removeListener: jest.fn(), // deprecated
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    })),
  });

  // Mock window.scrollTo
  Object.defineProperty(globalThis, 'scrollTo', {
    writable: true,
    value: jest.fn(),
  });

  // Mock sessionStorage
  const localStorageMock = {
    getItem: jest.fn(),
    setItem: jest.fn(),
    removeItem: jest.fn(),
    clear: jest.fn(),
  };

  Object.defineProperty(globalThis, 'sessionStorage', {
    value: localStorageMock,
  });

  Object.defineProperty(globalThis, 'localStorage', {
    value: localStorageMock,
  });

  // Mock window.open for OAuth testing
  Object.defineProperty(globalThis, 'open', {
    writable: true,
    value: jest.fn(() => ({
      close: jest.fn(),
      closed: false,
    })),
  });
}

// Mock TextEncoder and TextDecoder for Edge runtime compatibility
globalThis.TextEncoder = class TextEncoder {
  encode(string) {
    return new Uint8Array(Buffer.from(string, 'utf8'));
  }
};

globalThis.TextDecoder = class TextDecoder {
  decode(bytes) {
    return Buffer.from(bytes).toString('utf8');
  }
};

// Reset all mocks before each test
beforeEach(() => {
  jest.clearAllMocks();
  if (globalThis.window !== undefined && globalThis.localStorage) {
    globalThis.localStorage.getItem.mockClear();
    globalThis.localStorage.setItem.mockClear();
    globalThis.localStorage.removeItem.mockClear();
    globalThis.localStorage.clear.mockClear();
  }
});
