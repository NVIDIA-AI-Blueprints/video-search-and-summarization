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

Feature: Enforce configured maximum upload file size (Bug 6193881)
  The NVStreamer / storage file upload API must honor the configured
  nv_streamer_max_upload_file_size_MB limit. A file whose Content-Length
  exceeds the configured limit must be rejected with an HTTP error and must
  not create a stream. Files within the limit must continue to upload
  successfully.

  Background:
    Given the storage upload API is reachable

  Scenario: An upload larger than the configured max upload size is rejected
    When I upload a media file larger than the configured max upload size
    Then the upload is rejected with an HTTP error status
    And no stream is created for the rejected upload

  Scenario: An upload within the configured max upload size still succeeds
    When I upload a media file smaller than the configured max upload size
    Then the within-limit upload succeeds
