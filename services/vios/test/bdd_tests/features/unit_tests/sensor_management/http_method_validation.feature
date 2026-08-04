# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

Feature: VST HTTP method validation returns 405 uniformly (NVBug 6267433)
  Wrong HTTP verbs on routed VST endpoints must be rejected uniformly with
  405 MethodNotAllowedError and a consistent {error_code, error_message} body,
  instead of the old inconsistent mix of 200 / 400 / 501. This spans the shared
  HTTP request handler used by sensor, storage and recorder endpoints.

  All wrong-verb requests below are non-mutating: a write verb on a read-only
  endpoint (and a read verb on a write-only endpoint) must be rejected BEFORE
  the handler runs, so the test never changes server state on a fixed build.

  Background:
    Given the VST API is accessible

  Scenario Outline: Unsupported method on a routed endpoint returns 405 with MethodNotAllowedError
    When I send a <method> request to "<path>"
    Then the response status code is 405
    And the response error_code is "MethodNotAllowedError"
    And the response error_message is not empty

    # Read-only (GET) endpoints must reject write verbs.
    # Previously: 200 (+ data / null) on sensor paths, 501 on storage/info & storage/size.
    Examples: GET-only endpoints reject POST/PUT/DELETE
      | method | path                          |
      | POST   | /vst/api/v1/sensor/list       |
      | PUT    | /vst/api/v1/sensor/list       |
      | DELETE | /vst/api/v1/sensor/list       |
      | POST   | /vst/api/v1/sensor/streams    |
      | POST   | /vst/api/v1/sensor/status     |
      | POST   | /vst/api/v1/sensor/timelines  |
      | POST   | /vst/api/v1/sensor/version    |
      | POST   | /vst/api/v1/record/status     |
      | POST   | /vst/api/v1/storage/info      |
      | POST   | /vst/api/v1/storage/size      |

    # Write-only (POST) endpoints must reject read/other verbs.
    # Previously: GET/DELETE on sensor/add returned 400 InvalidParameterError.
    Examples: POST-only endpoints reject GET/DELETE
      | method | path                     |
      | GET    | /vst/api/v1/sensor/add   |
      | DELETE | /vst/api/v1/sensor/add   |
      | GET    | /vst/api/v1/sensor/scan  |

  Scenario Outline: The supported verb still succeeds (the guard does not block valid requests)
    When I send a <method> request to "<path>"
    Then the response status code is 200

    Examples: Control — read endpoints still serve GET
      | method | path                       |
      | GET    | /vst/api/v1/sensor/list    |
      | GET    | /vst/api/v1/sensor/status  |
      | GET    | /vst/api/v1/sensor/version |
