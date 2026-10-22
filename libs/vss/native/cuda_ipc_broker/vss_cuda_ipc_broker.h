// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
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
