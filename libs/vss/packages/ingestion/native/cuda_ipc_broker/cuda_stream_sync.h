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

#include <cuda_runtime_api.h>

#include <cstdint>

inline cudaError_t vss_wait_for_producer(cudaStream_t copy_stream,
                                         uint64_t producer_stream,
                                         cudaEvent_t *producer_ready) {
  *producer_ready = nullptr;
  if (producer_stream == 0) {
    return cudaSuccess;
  }
  cudaError_t status =
      cudaEventCreateWithFlags(producer_ready, cudaEventDisableTiming);
  if (status == cudaSuccess) {
    status = cudaEventRecord(
        *producer_ready, reinterpret_cast<cudaStream_t>(producer_stream));
  }
  if (status == cudaSuccess) {
    status = cudaStreamWaitEvent(copy_stream, *producer_ready, 0);
  }
  return status;
}
