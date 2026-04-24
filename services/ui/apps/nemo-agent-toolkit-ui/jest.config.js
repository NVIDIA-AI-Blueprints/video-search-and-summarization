// SPDX-License-Identifier: MIT
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    '^@nemo-agent-toolkit/ui$': '<rootDir>/__mocks__/@nemo-agent-toolkit-ui.js',
    '^@nemo-agent-toolkit/ui/server$': '<rootDir>/__mocks__/@nemo-agent-toolkit-ui-server.js',
    '^next-i18next$': '<rootDir>/__mocks__/next-i18next.js',
    '\\.(css|less|scss|sass)$': 'identity-obj-proxy',
  },
  testMatch: [
    '**/__tests__/pages/**/*.(ts|tsx|js)',
  ],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  transform: {
    '^.+\\.(ts|tsx)$': ['ts-jest', {
      tsconfig: {
        jsx: 'react-jsx',
      },
    }],
  },
  clearMocks: true,
  restoreMocks: true,
};
