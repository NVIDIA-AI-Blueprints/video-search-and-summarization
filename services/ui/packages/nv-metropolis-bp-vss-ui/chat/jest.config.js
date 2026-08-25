// SPDX-License-Identifier: MIT
// No NeMo Agent Toolkit mocks here on purpose: this package has no toolkit
// dependency, which is the reason it exists.
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  moduleNameMapper: {
    '\\.(css|less|scss|sass)$': 'identity-obj-proxy',
  },
  testMatch: ['<rootDir>/__tests__/**/*.test.ts?(x)'],
};
