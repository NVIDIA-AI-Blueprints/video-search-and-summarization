// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    '^common/warehouse/mui$': '<rootDir>/lib-src/common/warehouse/mui.tsx',
    '^common/warehouse/mui-icons$': '<rootDir>/lib-src/common/warehouse/mui-icons.tsx',
    '^common/warehouse/shell(.*)$': '<rootDir>/lib-src/common/warehouse/shell$1',
    '^common/warehouse$': '<rootDir>/lib-src/common/warehouse/index.ts',
    '^@nvidia/foundations-react-core$': '<rootDir>/../__mocks__/@nvidia-foundations-react-core.js',
    '\\.(css|less|scss|sass)$': 'identity-obj-proxy',
    '\\.(png|jpg|jpeg|gif|svg)$': '<rootDir>/__mocks__/fileMock.js',
  },
  testMatch: [
    '**/__tests__/**/*.(ts|tsx|js)',
    '**/*.(test|spec).(ts|tsx|js)',
  ],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  transform: {
    '^.+\\.(ts|tsx)$': ['ts-jest', {
      tsconfig: {
        jsx: 'react',
        allowJs: true,
      },
    }],
  },
  clearMocks: true,
  restoreMocks: true,
};
