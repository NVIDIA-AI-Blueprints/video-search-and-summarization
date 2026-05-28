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
 * Test Results Checker for CI/CD
 * 
 * This script reads the test-results.json file and exits with:
 * - Exit code 0 if all tests passed
 * - Exit code 1 if any tests failed
 * 
 * Usage: node check-test-results.js [path-to-results]
 */

const fs = require('fs');
const path = require('path');

// Detect if running in Jenkins or CI environment
const isCI = process.env.JENKINS_HOME || process.env.CI || process.env.GITLAB_CI;

// ANSI color codes (disabled in CI for better readability)
const colors = isCI ? {
    reset: '',
    red: '',
    green: '',
    yellow: '',
    blue: '',
    bold: '',
    dim: '',
} : {
    reset: '\x1b[0m',
    red: '\x1b[31m',
    green: '\x1b[32m',
    yellow: '\x1b[33m',
    blue: '\x1b[34m',
    bold: '\x1b[1m',
    dim: '\x1b[2m',
};

function main() {
    // If path passed on CLI, use it; otherwise try known locations (nyc --cwd=.. can write to repo root coverage/)
    const candidates = process.argv[2]
        ? [process.argv[2]]
        : [
            path.join(__dirname, 'coverage', 'test-results.json'),
            path.join(__dirname, '..', 'coverage', 'test-results.json')
          ];
    const resultsPath = candidates.find(p => fs.existsSync(p));

    if (!resultsPath) {
        console.error(`${colors.red}${colors.bold}✗ Test results file not found. Tried:${colors.reset}`);
        candidates.forEach(p => console.error(`   ${p}`));
        console.error(`${colors.yellow}Run 'npm run coverage' first to generate test results.${colors.reset}`);
        process.exit(1);
    }

    let results;
    try {
        results = JSON.parse(fs.readFileSync(resultsPath, 'utf-8'));
    } catch (error) {
        console.error(`${colors.red}${colors.bold}✗ Failed to parse test results: ${error.message}${colors.reset}`);
        process.exit(1);
    }

    const stats = results.stats || {};
    const failures = results.failures || [];
    const passes = stats.passes || 0;
    const failCount = stats.failures || 0;
    const pending = stats.pending || 0;
    const total = stats.tests || 0;

    const separator = isCI ? '=================================================' : '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
    
    console.log(`\n${colors.bold}${colors.blue}${separator}${colors.reset}`);
    console.log(`${colors.bold}📊 Test Results Summary${colors.reset}`);
    console.log(`${colors.blue}${separator}${colors.reset}\n`);

    console.log(`  Total Tests:    ${colors.bold}${total}${colors.reset}`);
    console.log(`  ${colors.green}✓ Passed:${colors.reset}       ${colors.green}${passes}${colors.reset}`);
    console.log(`  ${colors.red}✗ Failed:${colors.reset}       ${colors.red}${failCount}${colors.reset}`);
    console.log(`  ${colors.yellow}○ Pending:${colors.reset}      ${colors.yellow}${pending}${colors.reset}`);
    console.log(`  Duration:       ${stats.duration || 0}ms\n`);

    if (failCount > 0) {
        console.log(`${colors.red}${colors.bold}Failed Tests:${colors.reset}\n`);
        failures.forEach((failure, index) => {
            console.log(`${colors.red}${index + 1}) ${failure.fullTitle}${colors.reset}`);
            console.log(`   ${colors.dim}${failure.err.message}${colors.reset}`);
            if (failure.err.stack) {
                const stackLines = failure.err.stack.split('\n').slice(0, 3);
                stackLines.forEach(line => console.log(`   ${colors.dim}${line}${colors.reset}`));
            }
            console.log();
        });
    }

    console.log(`${colors.blue}${separator}${colors.reset}`);

    if (failCount > 0) {
        console.log(`${colors.red}${colors.bold}✗ TESTS FAILED${colors.reset}\n`);
        process.exit(1);
    } else {
        console.log(`${colors.green}${colors.bold}✓ ALL TESTS PASSED${colors.reset}\n`);
        process.exit(0);
    }
}

main();
