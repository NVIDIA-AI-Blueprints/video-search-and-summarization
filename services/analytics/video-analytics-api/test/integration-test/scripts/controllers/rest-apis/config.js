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

/*
 * Integration tests for GET /config/* (mirrors app/controllers/rest-apis/config.js).
 * POST /config/upload-file/* is run from main runner with fixtures.
 */

'use strict';

function getTests(c) {
    return [
        { name: 'GET /config/calibration', path: '/config/calibration', method: 'GET', expectedStatus: 200 },
        { name: 'GET /config/calibration/last-modified-timestamp', path: '/config/calibration/last-modified-timestamp', method: 'GET', expectedStatus: 200 },
        { name: 'GET /config/road-network', path: '/config/road-network', method: 'GET', expectedStatus: 200 },
        { name: 'GET /config/usd-assets', path: '/config/usd-assets', method: 'GET', expectedStatus: 200 },

        // Calibration image endpoints (POST /config/calibration/images upload runs before controller tests)
        { name: 'GET /config/calibration/image-metadata', path: '/config/calibration/image-metadata', method: 'GET', expectedStatus: 200 },
        { name: 'GET /config/calibration/image-metadata (place)', path: `/config/calibration/image-metadata?place=${encodeURIComponent(c.PLACE)}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /config/calibration/image (place+view)', path: `/config/calibration/image?place=${encodeURIComponent(c.PLACE)}&view=plan-view`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true },
        { name: 'GET /config/calibration/image (missing view)', path: `/config/calibration/image?place=${encodeURIComponent(c.PLACE)}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /config/calibration/image (no sensorId/place)', path: '/config/calibration/image?view=plan-view', method: 'GET', expectedStatus: 400 },
        {
            name: 'POST /config/calibration/delete-images (valid body)',
            path: '/config/calibration/delete-images',
            method: 'POST',
            expectedStatus: 201,
            body: { calibrationImages: [{ place: c.PLACE, view: 'plan-view' }] },
        },
        {
            name: 'POST /config/calibration/delete-images (missing required)',
            path: '/config/calibration/delete-images',
            method: 'POST',
            expectedStatus: 400,
            body: {},
        },
    ];
}

module.exports = { getTests };
