// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import rootConfig from '../../../eslint.config.mjs';

// Turbo runs `eslint .` in this package. The root override matches paths that
// include the package name, which are not the paths ESLint sees from here.
export default [
  ...rootConfig,
  {
    files: ['**/*.{ts,tsx}'],
    rules: {
      '@typescript-eslint/ban-ts-comment': 'off',
      '@typescript-eslint/no-unused-expressions': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
      'prefer-const': 'off',
      'prefer-rest-params': 'off',
    },
  },
];
