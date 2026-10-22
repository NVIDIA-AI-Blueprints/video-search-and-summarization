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
