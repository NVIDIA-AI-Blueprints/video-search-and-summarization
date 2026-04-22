// SPDX-License-Identifier: MIT
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    '^@nemo-agent-toolkit/ui$': '<rootDir>/../__mocks__/@nemo-agent-toolkit-ui.js',
    '^@aiqtoolkit-ui/common$': '<rootDir>/../../common/lib-src/index.ts',
    '^@nvidia/foundations-react-core$': '<rootDir>/../__mocks__/@nvidia-foundations-react-core.js',
    '\\.(css|less|scss|sass)$': 'identity-obj-proxy',
  },
  testMatch: [
    '**/__tests__/**/*.(ts|tsx|js)',
    '**/*.(test|spec).(ts|tsx|js)'
  ],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  transform: {
    '^.+\\.(ts|tsx)$': ['ts-jest', {
      tsconfig: {
        jsx: 'react',
      }
    }]
  },
  collectCoverageFrom: [
    'lib-src/**/*.{ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
    '!**/lib/**',
  ],
  clearMocks: true,
  restoreMocks: true,
};

