// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// cuDSS support utilities: error checking macro, type mapping.
//
// cuDSS is NVIDIA's sparse direct solver library, supporting Cholesky (LL^T),
// LDL^T, and LU factorization on GPU. It requires CUDA 12.0+ and is
// distributed separately from the CUDA Toolkit.

#ifndef EIGEN_GPU_CUDSS_SUPPORT_H
#define EIGEN_GPU_CUDSS_SUPPORT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSupport.h"
#include <cudss.h>

namespace Eigen {
namespace gpu {
namespace internal {

// ---- Error checking ---------------------------------------------------------

#define EIGEN_CUDSS_CHECK(x)                                              \
  do {                                                                    \
    cudssStatus_t _s = (x);                                               \
    eigen_assert(_s == CUDSS_STATUS_SUCCESS && "cuDSS call failed: " #x); \
    EIGEN_UNUSED_VARIABLE(_s);                                            \
  } while (0)

// ---- Scalar → cudssMatrixType_t for SPD/HPD ---------------------------------

template <typename Scalar>
struct cudss_spd_type;

template <>
struct cudss_spd_type<float> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_SPD;
};
template <>
struct cudss_spd_type<double> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_SPD;
};
template <>
struct cudss_spd_type<std::complex<float>> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_HPD;
};
template <>
struct cudss_spd_type<std::complex<double>> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_HPD;
};

// ---- Scalar → cudssMatrixType_t for symmetric/Hermitian ---------------------

template <typename Scalar>
struct cudss_symmetric_type;

template <>
struct cudss_symmetric_type<float> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_SYMMETRIC;
};
template <>
struct cudss_symmetric_type<double> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_SYMMETRIC;
};
template <>
struct cudss_symmetric_type<std::complex<float>> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_HERMITIAN;
};
template <>
struct cudss_symmetric_type<std::complex<double>> {
  static constexpr cudssMatrixType_t value = CUDSS_MTYPE_HERMITIAN;
};

// ---- StorageIndex → cudaDataType_t ------------------------------------------

template <typename StorageIndex>
struct cudss_index_type;

template <>
struct cudss_index_type<int> {
  static constexpr cudaDataType_t value = CUDA_R_32I;
};
template <>
struct cudss_index_type<int64_t> {
  static constexpr cudaDataType_t value = CUDA_R_64I;
};

// ---- UpLo → cudssMatrixViewType_t -------------------------------------------
// For symmetric matrices stored as CSC (ColMajor), cuDSS sees CSR of A^T.
// Since A = A^T, the data is the same, but the triangle view must be swapped.

template <int UpLo, int StorageOrder>
struct cudss_view_type;

// ColMajor (CSC) passed as CSR: lower ↔ upper swap.
template <>
struct cudss_view_type<Lower, ColMajor> {
  static constexpr cudssMatrixViewType_t value = CUDSS_MVIEW_UPPER;
};
template <>
struct cudss_view_type<Upper, ColMajor> {
  static constexpr cudssMatrixViewType_t value = CUDSS_MVIEW_LOWER;
};

// RowMajor (CSR) passed directly: no swap needed.
template <>
struct cudss_view_type<Lower, RowMajor> {
  static constexpr cudssMatrixViewType_t value = CUDSS_MVIEW_LOWER;
};
template <>
struct cudss_view_type<Upper, RowMajor> {
  static constexpr cudssMatrixViewType_t value = CUDSS_MVIEW_UPPER;
};

}  // namespace internal

// ---- Ordering enum ----------------------------------------------------------

enum class GpuSparseOrdering {
  AMD,    // Default fill-reducing ordering
  METIS,  // METIS nested dissection
  RCM     // Reverse Cuthill-McKee
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_CUDSS_SUPPORT_H
