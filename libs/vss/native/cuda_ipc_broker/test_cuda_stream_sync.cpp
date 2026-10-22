// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
#include "cuda_stream_sync.h"

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>

int main() {
  constexpr size_t kBytes = 32 * 1024 * 1024;
  unsigned char *source = nullptr;
  unsigned char *destination = nullptr;
  cudaStream_t producer = nullptr;
  cudaStream_t copy = nullptr;
  assert(cudaMalloc(reinterpret_cast<void **>(&source), kBytes) == cudaSuccess);
  assert(cudaMalloc(reinterpret_cast<void **>(&destination), kBytes) ==
         cudaSuccess);
  assert(cudaStreamCreateWithFlags(&producer, cudaStreamNonBlocking) ==
         cudaSuccess);
  assert(cudaStreamCreateWithFlags(&copy, cudaStreamNonBlocking) == cudaSuccess);

  // Queue enough producer work that the copy races without an inter-stream
  // dependency, then make the final producer value observable.
  for (int index = 0; index < 8; ++index) {
    assert(cudaMemsetAsync(source, index, kBytes, producer) == cudaSuccess);
  }
  assert(cudaMemsetAsync(source, 0xA5, kBytes, producer) == cudaSuccess);

  cudaEvent_t producer_ready = nullptr;
  assert(vss_wait_for_producer(
             copy, reinterpret_cast<uint64_t>(producer), &producer_ready) ==
         cudaSuccess);
  assert(cudaMemcpyAsync(destination, source, kBytes, cudaMemcpyDeviceToDevice,
                         copy) == cudaSuccess);
  assert(cudaStreamSynchronize(copy) == cudaSuccess);

  std::vector<unsigned char> host(kBytes);
  assert(cudaMemcpy(host.data(), destination, kBytes, cudaMemcpyDeviceToHost) ==
         cudaSuccess);
  for (unsigned char value : host) {
    assert(value == 0xA5);
  }

  assert(cudaEventDestroy(producer_ready) == cudaSuccess);
  assert(cudaStreamDestroy(copy) == cudaSuccess);
  assert(cudaStreamDestroy(producer) == cudaSuccess);
  assert(cudaFree(destination) == cudaSuccess);
  assert(cudaFree(source) == cudaSuccess);
  return 0;
}
