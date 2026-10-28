// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    '^common/warehouse/mdat$': '<rootDir>/lib-src/warehouse/mdat/index.ts',
    '^common/warehouse/mui$': '<rootDir>/lib-src/warehouse/mui.tsx',
    '^common/warehouse/mui-icons$': '<rootDir>/lib-src/warehouse/mui-icons.tsx',
    '^common/warehouse$': '<rootDir>/lib-src/warehouse/index.ts',
    '^common$': '<rootDir>/lib-src/index.ts',
    '\\.(css|less|scss|sass)$': 'identity-obj-proxy',
    '\\.(png|jpg|jpeg|gif|svg)$': '<rootDir>/__mocks__/fileMock.js',
  },
  testMatch: [
    '**/__tests__/**/*.(ts|tsx|js)',
    '**/*.(test|spec).(ts|tsx|js)',
  ],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  transform: {
    '^.+\\.(ts|tsx)$': [
      'ts-jest',
      {
        tsconfig: {
          jsx: 'react',
        },
      },
    ],
  },
  collectCoverageFrom: [
    'lib-src/**/*.{ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
    '!**/lib/**',
    '!**/index.ts',
  ],
  clearMocks: true,
  restoreMocks: true,
};
