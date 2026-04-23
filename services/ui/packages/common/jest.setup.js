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
