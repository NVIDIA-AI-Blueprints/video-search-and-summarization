// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void *vss_cuda_ipc_broker_create(const char *socket_path, int device_id,
                                 unsigned int width, unsigned int height,
                                 unsigned int slot_count, int color_matrix,
                                 int full_range, char *error, size_t error_size);

int vss_cuda_ipc_broker_publish(void *broker, uint64_t device_pointer,
                                size_t source_pitch, uint64_t timestamp_ns,
                                uint64_t producer_stream, char *error,
                                size_t error_size);

int vss_cuda_ipc_broker_eos(void *broker, char *error, size_t error_size);

void vss_cuda_ipc_broker_stop(void *broker);

void vss_cuda_ipc_broker_destroy(void *broker);

#ifdef __cplusplus
}
#endif
