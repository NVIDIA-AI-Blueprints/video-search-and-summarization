// SPDX-License-Identifier: MIT
require('@testing-library/jest-dom');
require('whatwg-fetch');

// jsdom's crypto doesn't expose randomUUID; patch it from Node's crypto so
// code that relies on crypto.randomUUID() (e.g. chunkedUpload identifiers)
// works under test without needing to mock the callers.
if (globalThis.crypto && !globalThis.crypto.randomUUID) {
  globalThis.crypto.randomUUID = require('crypto').randomUUID;
}

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
