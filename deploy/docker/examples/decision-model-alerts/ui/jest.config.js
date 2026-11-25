// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
module.exports = {
  preset: 'ts-jest', testEnvironment: 'jsdom',
  modulePathIgnorePatterns: ['<rootDir>/.next/'],
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: { '\\.(css|less|scss|sass)$': 'identity-obj-proxy' },
  testMatch: ['<rootDir>/__tests__/**/*.test.ts', '<rootDir>/__tests__/**/*.test.tsx'],
  transform: { '^.+\\.(ts|tsx)$': ['ts-jest', { tsconfig: { jsx: 'react' } }] },
  clearMocks: true, restoreMocks: true,
};
