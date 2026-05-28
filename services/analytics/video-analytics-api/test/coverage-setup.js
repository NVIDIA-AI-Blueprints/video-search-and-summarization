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
 * Coverage Setup
 * 
 * This file imports all modules from web-api-core and app to ensure
 * they appear in the coverage report even if not directly tested.
 * It's required by mocha before running tests.
 */

// ============================================================================
// Errors
// ============================================================================
require('../src/web-api-core/Errors/BadRequestError');
require('../src/web-api-core/Errors/IndexNotFoundError');
require('../src/web-api-core/Errors/InternalServerError');
require('../src/web-api-core/Errors/InvalidInputError');
require('../src/web-api-core/Errors/ResourceNotFoundError');
require('../src/web-api-core/Errors/ServiceUnavailableError');

// ============================================================================
// Metrics
// ============================================================================
require('../src/web-api-core/Metrics/Behavior');
require('../src/web-api-core/Metrics/LastProcessedTimestamp');
require('../src/web-api-core/Metrics/Occupancy');
require('../src/web-api-core/Metrics/SpaceUtilization');
require('../src/web-api-core/Metrics/TripwireEvent');

// ============================================================================
// Services
// ============================================================================
require('../src/web-api-core/Services/Alerts');
require('../src/web-api-core/Services/Behavior');
require('../src/web-api-core/Services/Calibration');
require('../src/web-api-core/Services/Clustering');
require('../src/web-api-core/Services/ConfigManager');
require('../src/web-api-core/Services/Events');
require('../src/web-api-core/Services/Frames');
require('../src/web-api-core/Services/Incidents');
require('../src/web-api-core/Services/MTMC');
require('../src/web-api-core/Services/NotificationManager');
require('../src/web-api-core/Services/Place');
require('../src/web-api-core/Services/RoadNetwork');
require('../src/web-api-core/Services/Sensor');
require('../src/web-api-core/Services/UsdAssets');

// ============================================================================
// Utils
// ============================================================================
require('../src/web-api-core/Utils/Config');
require('../src/web-api-core/Utils/Database');
require('../src/web-api-core/Utils/Elasticsearch');
require('../src/web-api-core/Utils/FileUploadHandler');
require('../src/web-api-core/Utils/Histogram');
require('../src/web-api-core/Utils/Kafka');
require('../src/web-api-core/Utils/MessageBroker');
require('../src/web-api-core/Utils/Utils');
require('../src/web-api-core/Utils/Validator');

// ============================================================================
// App - Initializers
// ============================================================================
require('../src/app/initializers/cache');

// ============================================================================
// App - Controllers
// 
// Controllers are loaded with proxyquire to mock elastic/kafka dependencies
// since they require runtime initialization that's not available during tests.
// ============================================================================
const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

// Mock elastic and kafka initializers for controller loading
const mockElastic = {
    getName: () => 'Elasticsearch',
    getClient: () => ({}),
    getConfigs: () => new Map([['indexPrefix', 'mdx-']])
};

const mockKafka = {
    getName: () => 'Kafka',
    getClient: () => ({}),
    getAdminClient: () => ({})
};

const mockCache = {
    get: () => undefined,
    set: () => {}
};

// Load all REST API controllers with mocked dependencies
proxyquire('../src/app/controllers/rest-apis/alerts', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/behavior', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/clustering', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/config', {
    '../../initializers/elastic': mockElastic,
    '../../initializers/kafka': mockKafka
});
proxyquire('../src/app/controllers/rest-apis/events', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/frames', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/incidents', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/livez', {});
proxyquire('../src/app/controllers/rest-apis/metrics', {
    '../../initializers/elastic': mockElastic,
    '../../initializers/kafka': mockKafka
});
proxyquire('../src/app/controllers/rest-apis/sensor', {
    '../../initializers/elastic': mockElastic
});
proxyquire('../src/app/controllers/rest-apis/tracker', {
    '../../initializers/elastic': mockElastic,
    '../../initializers/kafka': mockKafka,
    '../../initializers/cache': mockCache
});

// Note: server.js, elastic.js, kafka.js, routes.js require runtime dependencies
// and would need to be tested via E2E tests with a real Elasticsearch/Kafka instance
