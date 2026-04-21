module.exports = {
  extends: ['next/core-web-vitals'],
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
    jest: true,
  },
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: {
      jsx: true,
    },
  },
  rules: {
    // TypeScript specific rules (using ESLint equivalents)
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],

    // React specific rules
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
    'react-hooks/rules-of-hooks': 'warn',
    'react-hooks/exhaustive-deps': 'warn',
    'react/display-name': 'warn',
    'react/no-unescaped-entities': 'warn',
    '@next/next/no-img-element': 'warn',
    '@next/next/no-sync-scripts': 'warn',

    // General rules
    'no-console': 'warn',
    'no-debugger': 'error',
    'prefer-const': 'warn',
    'no-var': 'error',

    // Import rules
    'import/order': [
      'warn',
      {
        groups: [
          'builtin',
          'external',
          'internal',
          'parent',
          'sibling',
          'index',
        ],
        'newlines-between': 'always',
      },
    ],
  },
  overrides: [
    {
      files: ['**/__tests__/**/*', '**/*.test.*', '**/*.spec.*'],
      env: {
        jest: true,
      },
      rules: {
        'no-unused-vars': 'off',
        'no-console': 'off',
      },
    },
    {
      files: ['**/*.config.js', '**/*.config.ts'],
      rules: {
        'no-var': 'off',
      },
    },
  ],
  settings: {
    react: {
      version: 'detect',
    },
  },
};
