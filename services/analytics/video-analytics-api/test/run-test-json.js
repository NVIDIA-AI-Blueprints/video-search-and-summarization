#!/usr/bin/env node

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
 * Run Mocha with JSON + JUnit reporters from the test directory.
 * Runs mocha twice so each reporter gets its options in the shape it expects
 * (multi-reporters passes reporterOptions; Mocha's json reporter expects reporterOption.output).
 * Uses absolute paths so output is always in test/coverage/ when nyc uses --cwd=..
 */
const path = require('path');
const { spawnSync } = require('child_process');
const fs = require('fs');

const testDir = path.resolve(__dirname);
const coverageDir = path.join(testDir, 'coverage');
fs.mkdirSync(coverageDir, { recursive: true });

const jsonOutput = path.join(coverageDir, 'test-results.json');
const xmlOutput = path.join(coverageDir, 'test-results.xml');
const requirePath = path.join(testDir, 'coverage-setup.js');
const spec = 'unit-test/**/*.test.js';

function runMocha(args) {
  const r = spawnSync('npx', ['mocha', ...args], { cwd: testDir, stdio: 'inherit' });
  if (r.status !== 0) process.exit(r.status === null ? 1 : r.status);
}

// 1) JSON reporter (Mocha expects reporterOption.output; use native CLI so file is written)
runMocha(['--require', requirePath, '--reporter', 'json', '--reporter-options', `output=${jsonOutput}`, spec]);

// 2) JUnit reporter
runMocha(['--require', requirePath, '--reporter', 'mocha-junit-reporter', '--reporter-options', `mochaFile=${xmlOutput}`, spec]);

// Sanitize JUnit XML: parsers (Jenkins JUnit, summarizeTests) can fail on empty/missing numeric attributes (e.g. getAttribute("errors") => "").
const xml = fs.readFileSync(xmlOutput, 'utf8');
const NUMERIC_ATTRS = 'time|tests|failures|errors|skipped|disabled';
let sanitized = xml
  // Empty or whitespace-only numeric attributes -> "0"
  .replace(new RegExp(`(${NUMERIC_ATTRS})="\\s*"`, 'g'), '$1="0"')
  .replace(/time='\s*'/g, 'time="0"')
  .replace(/tests='\s*'/g, 'tests="0"')
  .replace(/failures='\s*'/g, 'failures="0"')
  .replace(/errors='\s*'/g, 'errors="0"');
// Root <testsuites> often has no "errors" attribute -> parser gets "" and Integer.parseInt("") throws
sanitized = sanitized.replace(
  /<testsuites(\s+)(?![^>]*\berrors=)/g,
  '<testsuites$1errors="0" '
);
// Ensure all elements have explicit numeric attrs when missing (parser getAttribute returns "" otherwise)
sanitized = sanitized
  .replace(/<testsuites(\s+)(?![^>]*\btime=)/g, '<testsuites$1time="0" ')
  .replace(/<testsuites(\s+)(?![^>]*\btests=)/g, '<testsuites$1tests="0" ')
  .replace(/<testsuites(\s+)(?![^>]*\bfailures=)/g, '<testsuites$1failures="0" ')
  .replace(/<testsuite(\s+)(?![^>]*\btime=)/g, '<testsuite$1time="0" ')
  .replace(/<testsuite(\s+)(?![^>]*\btests=)/g, '<testsuite$1tests="0" ')
  .replace(/<testsuite(\s+)(?![^>]*\bfailures=)/g, '<testsuite$1failures="0" ')
  .replace(/<testsuite(\s+)(?![^>]*\berrors=)/g, '<testsuite$1errors="0" ')
  .replace(/<testsuite(\s+)(?![^>]*\bskipped=)/g, '<testsuite$1skipped="0" ')
  .replace(/<testcase(\s+)(?![^>]*\btime=)/g, '<testcase$1time="0" ');
fs.writeFileSync(xmlOutput, sanitized);
