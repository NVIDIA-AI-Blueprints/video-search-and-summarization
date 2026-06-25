// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// cuSOLVER-specific support types:
//   - cuSOLVER error-checking macro
//   - RAII wrapper for cusolverDnParams
//   - Scalar → cudaDataType_t mapping
//   - (UpLo, StorageOrder) → cublasFillMode_t mapping
//
// Generic CUDA runtime utilities (DeviceBuffer, EIGEN_CUDA_RUNTIME_CHECK)
// are in GpuSupport.h.

#ifndef EIGEN_GPU_CUSOLVER_SUPPORT_H
#define EIGEN_GPU_CUSOLVER_SUPPORT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSupport.h"
#include <cusolverDn.h>
#include <cstdio>

namespace Eigen {
namespace gpu {
namespace internal {

// ---- Error-checking macros --------------------------------------------------

// cuSOLVER does not ship a cusolverGetErrorString() in the public API, so we
// stringify the codes ourselves. Keeps failed asserts actionable.
inline const char* cusolver_status_name(cusolverStatus_t s) {
  switch (s) {
    case CUSOLVER_STATUS_SUCCESS:
      return "CUSOLVER_STATUS_SUCCESS";
    case CUSOLVER_STATUS_NOT_INITIALIZED:
      return "CUSOLVER_STATUS_NOT_INITIALIZED";
    case CUSOLVER_STATUS_ALLOC_FAILED:
      return "CUSOLVER_STATUS_ALLOC_FAILED";
    case CUSOLVER_STATUS_INVALID_VALUE:
      return "CUSOLVER_STATUS_INVALID_VALUE";
    case CUSOLVER_STATUS_ARCH_MISMATCH:
      return "CUSOLVER_STATUS_ARCH_MISMATCH";
    case CUSOLVER_STATUS_EXECUTION_FAILED:
      return "CUSOLVER_STATUS_EXECUTION_FAILED";
    case CUSOLVER_STATUS_INTERNAL_ERROR:
      return "CUSOLVER_STATUS_INTERNAL_ERROR";
    case CUSOLVER_STATUS_MATRIX_TYPE_NOT_SUPPORTED:
      return "CUSOLVER_STATUS_MATRIX_TYPE_NOT_SUPPORTED";
    case CUSOLVER_STATUS_NOT_SUPPORTED:
      return "CUSOLVER_STATUS_NOT_SUPPORTED";
    default:
      return "CUSOLVER_STATUS_UNKNOWN";
  }
}

inline bool report_cusolver_failure(cusolverStatus_t s, const char* expr, const char* file, int line) {
  std::fprintf(stderr,
               "cuSOLVER call failed\n"
               "  expr:   %s\n"
               "  status: %s (%d)\n"
               "  at:     %s:%d\n",
               expr, cusolver_status_name(s), static_cast<int>(s), file, line);
  return false;
}

#define EIGEN_CUSOLVER_CHECK(expr)                                                                \
  do {                                                                                            \
    cusolverStatus_t _s = (expr);                                                                 \
    eigen_assert(_s == CUSOLVER_STATUS_SUCCESS ||                                                 \
                 ::Eigen::gpu::internal::report_cusolver_failure(_s, #expr, __FILE__, __LINE__)); \
  } while (0)

// ---- RAII: cusolverDnParams -------------------------------------------------

struct CusolverParams {
  cusolverDnParams_t p = nullptr;

  CusolverParams() { EIGEN_CUSOLVER_CHECK(cusolverDnCreateParams(&p)); }

  ~CusolverParams() {
    if (p) (void)cusolverDnDestroyParams(p);  // destructor: can't propagate
  }

  // Move-only.
  CusolverParams(CusolverParams&& o) noexcept : p(o.p) { o.p = nullptr; }
  CusolverParams& operator=(CusolverParams&& o) noexcept {
    if (this != &o) {
      if (p) EIGEN_CUSOLVER_CHECK(cusolverDnDestroyParams(p));
      p = o.p;
      o.p = nullptr;
    }
    return *this;
  }

  CusolverParams(const CusolverParams&) = delete;
  CusolverParams& operator=(const CusolverParams&) = delete;
};

// ---- Scalar → cudaDataType_t ------------------------------------------------
// Alias for backward compatibility. The canonical trait is cuda_data_type<> in GpuSupport.h.
template <typename Scalar>
using cusolver_data_type = cuda_data_type<Scalar>;

// ---- UpLo → cublasFillMode_t ------------------------------------------------
// cuSOLVER always interprets the matrix as column-major. Callers pass the
// triangle that holds the data in column-major layout.

template <int UpLo>
struct cusolver_fill_mode;

template <>
struct cusolver_fill_mode<Lower> {
  static constexpr cublasFillMode_t value = CUBLAS_FILL_MODE_LOWER;
};
template <>
struct cusolver_fill_mode<Upper> {
  static constexpr cublasFillMode_t value = CUBLAS_FILL_MODE_UPPER;
};

// ---- Type-specific cuSOLVER wrappers ----------------------------------------
// cuSOLVER does not provide generic X variants for ormqr/unmqr. These overloaded
// wrappers dispatch to the correct type-specific function.
// For real types: ormqr (orthogonal Q). For complex types: unmqr (unitary Q).

inline cusolverStatus_t cusolverDnXormqr(cusolverDnHandle_t h, cublasSideMode_t side, cublasOperation_t trans, int m,
                                         int n, int k, const float* A, int lda, const float* tau, float* C, int ldc,
                                         float* work, int lwork, int* info) {
  return cusolverDnSormqr(h, side, trans, m, n, k, A, lda, tau, C, ldc, work, lwork, info);
}
inline cusolverStatus_t cusolverDnXormqr(cusolverDnHandle_t h, cublasSideMode_t side, cublasOperation_t trans, int m,
                                         int n, int k, const double* A, int lda, const double* tau, double* C, int ldc,
                                         double* work, int lwork, int* info) {
  return cusolverDnDormqr(h, side, trans, m, n, k, A, lda, tau, C, ldc, work, lwork, info);
}
inline cusolverStatus_t cusolverDnXormqr(cusolverDnHandle_t h, cublasSideMode_t side, cublasOperation_t trans, int m,
                                         int n, int k, const std::complex<float>* A, int lda,
                                         const std::complex<float>* tau, std::complex<float>* C, int ldc,
                                         std::complex<float>* work, int lwork, int* info) {
  return cusolverDnCunmqr(h, side, trans, m, n, k, reinterpret_cast<const cuComplex*>(A), lda,
                          reinterpret_cast<const cuComplex*>(tau), reinterpret_cast<cuComplex*>(C), ldc,
                          reinterpret_cast<cuComplex*>(work), lwork, info);
}
inline cusolverStatus_t cusolverDnXormqr(cusolverDnHandle_t h, cublasSideMode_t side, cublasOperation_t trans, int m,
                                         int n, int k, const std::complex<double>* A, int lda,
                                         const std::complex<double>* tau, std::complex<double>* C, int ldc,
                                         std::complex<double>* work, int lwork, int* info) {
  return cusolverDnZunmqr(h, side, trans, m, n, k, reinterpret_cast<const cuDoubleComplex*>(A), lda,
                          reinterpret_cast<const cuDoubleComplex*>(tau), reinterpret_cast<cuDoubleComplex*>(C), ldc,
                          reinterpret_cast<cuDoubleComplex*>(work), lwork, info);
}

// Buffer size wrappers for ormqr/unmqr.
inline cusolverStatus_t cusolverDnXormqr_bufferSize(cusolverDnHandle_t h, cublasSideMode_t side,
                                                    cublasOperation_t trans, int m, int n, int k, const float* A,
                                                    int lda, const float* tau, const float* C, int ldc, int* lwork) {
  return cusolverDnSormqr_bufferSize(h, side, trans, m, n, k, A, lda, tau, C, ldc, lwork);
}
inline cusolverStatus_t cusolverDnXormqr_bufferSize(cusolverDnHandle_t h, cublasSideMode_t side,
                                                    cublasOperation_t trans, int m, int n, int k, const double* A,
                                                    int lda, const double* tau, const double* C, int ldc, int* lwork) {
  return cusolverDnDormqr_bufferSize(h, side, trans, m, n, k, A, lda, tau, C, ldc, lwork);
}
inline cusolverStatus_t cusolverDnXormqr_bufferSize(cusolverDnHandle_t h, cublasSideMode_t side,
                                                    cublasOperation_t trans, int m, int n, int k,
                                                    const std::complex<float>* A, int lda,
                                                    const std::complex<float>* tau, const std::complex<float>* C,
                                                    int ldc, int* lwork) {
  return cusolverDnCunmqr_bufferSize(h, side, trans, m, n, k, reinterpret_cast<const cuComplex*>(A), lda,
                                     reinterpret_cast<const cuComplex*>(tau), reinterpret_cast<const cuComplex*>(C),
                                     ldc, lwork);
}
inline cusolverStatus_t cusolverDnXormqr_bufferSize(cusolverDnHandle_t h, cublasSideMode_t side,
                                                    cublasOperation_t trans, int m, int n, int k,
                                                    const std::complex<double>* A, int lda,
                                                    const std::complex<double>* tau, const std::complex<double>* C,
                                                    int ldc, int* lwork) {
  return cusolverDnZunmqr_bufferSize(h, side, trans, m, n, k, reinterpret_cast<const cuDoubleComplex*>(A), lda,
                                     reinterpret_cast<const cuDoubleComplex*>(tau),
                                     reinterpret_cast<const cuDoubleComplex*>(C), ldc, lwork);
}

}  // namespace internal
}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_CUSOLVER_SUPPORT_H
