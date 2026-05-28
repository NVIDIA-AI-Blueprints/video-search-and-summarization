/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/**
 * Mocha multi-reporters config with absolute paths so JSON and JUnit output
 * are always written to test/coverage/ regardless of process cwd (e.g. when nyc uses --cwd=..).
 */
const path = require('path');

const testDir = __dirname;

module.exports = {
  reporterEnabled: 'json, mocha-junit-reporter',
  jsonReporterOptions: {
    output: path.join(testDir, 'coverage', 'test-results.json')
  },
  mochaJunitReporterReporterOptions: {
    mochaFile: path.join(testDir, 'coverage', 'test-results.xml')
  }
};
