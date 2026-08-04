/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import globals from 'globals';

// Flat config (ESLint 9). Mirrors the previous .eslintrc.cjs rule set.
export default tseslint.config(
	// Scope matches the previous `eslint . --ext .ts`: TypeScript sources only.
	{
		ignores: ['**/*.js', '**/*.cjs', '**/*.mjs', 'dist/**', 'coverage/**', 'node_modules/**'],
	},
	js.configs.recommended,
	...tseslint.configs.recommended,
	{
		files: ['**/*.ts'],
		languageOptions: {
			ecmaVersion: 'latest',
			sourceType: 'module',
			globals: {
				...globals.browser,
				...globals.node,
				...globals.es2020,
			},
		},
		rules: {
			'@typescript-eslint/explicit-function-return-type': 'warn',
			'@typescript-eslint/no-unused-vars': 'error',
			'@typescript-eslint/no-explicit-any': 'warn',
			'no-console': 'warn',
			'no-use-before-define': 'off',
			'@typescript-eslint/no-use-before-define': 'off',
		},
	}
);
