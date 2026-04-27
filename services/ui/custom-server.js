// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// SPDX-License-Identifier: MIT
// used for prod server inside docker image
// only run one app at a time

const { configureRuntimeEnv } = require('next-runtime-env/build/configure');

// Get the app name from environment variable
const RUN_APP_NAME = process.env.RUN_APP_NAME || 'nemo-agent-toolkit-ui'

// Dynamically construct the path to the server.js based on RUN_APP_NAME
const serverPath = `/repo/apps/${RUN_APP_NAME}/apps/${RUN_APP_NAME}/server.js`;
const appPath = `/repo/apps/${RUN_APP_NAME}/apps/${RUN_APP_NAME}`;

console.log(`Starting server for app: ${RUN_APP_NAME}`);
console.log(`Server path: ${serverPath}`);
console.log(`App path: ${appPath}`);

// Check if the server file exists before requiring it
const fs = require('fs');
if (!fs.existsSync(serverPath)) {
  console.error(`Error: Server file not found at ${serverPath}`);
  console.error(`Available apps should match the RUN_APP_NAME environment variable.`);
  process.exit(1);
}

// Change to the app directory so next-runtime-env writes to the correct public folder
process.chdir(appPath);
configureRuntimeEnv();

// Import the standalone server
require(serverPath);

// Handle SIGINT (Ctrl+C) and SIGTERM signals
const shutdown = async () => {
  console.log('Shutting down gracefully...');
  process.exit(0);
};

// Use once() to ensure the handler is only called once
process.once('SIGINT', shutdown);  // Ctrl+C
process.once('SIGTERM', shutdown); // Docker/Kubernetes termination signal