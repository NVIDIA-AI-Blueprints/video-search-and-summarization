import '@testing-library/jest-dom';
import 'whatwg-fetch';

// Mock IntersectionObserver
global.IntersectionObserver = class IntersectionObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  unobserve() {}
};

// Mock ResizeObserver
global.ResizeObserver = class ResizeObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  unobserve() {}
};

// Mock window-specific globals (only in browser/jsdom environment)
if (typeof window !== 'undefined') {
  // Mock window.matchMedia
  Object.defineProperty(window, 'matchMedia', {
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
  Object.defineProperty(window, 'scrollTo', {
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

  Object.defineProperty(window, 'sessionStorage', {
    value: localStorageMock,
  });

  Object.defineProperty(window, 'localStorage', {
    value: localStorageMock,
  });

  // Mock window.open for OAuth testing
  Object.defineProperty(window, 'open', {
    writable: true,
    value: jest.fn(() => ({
      close: jest.fn(),
      closed: false,
    })),
  });
}

// Mock TextEncoder and TextDecoder for Edge runtime compatibility
global.TextEncoder = class TextEncoder {
  encode(string) {
    return new Uint8Array(Buffer.from(string, 'utf8'));
  }
};

global.TextDecoder = class TextDecoder {
  decode(bytes, options = {}) {
    return Buffer.from(bytes).toString('utf8');
  }
};

// Reset all mocks before each test
beforeEach(() => {
  jest.clearAllMocks();
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.getItem.mockClear();
    window.localStorage.setItem.mockClear();
    window.localStorage.removeItem.mockClear();
    window.localStorage.clear.mockClear();
  }
});
