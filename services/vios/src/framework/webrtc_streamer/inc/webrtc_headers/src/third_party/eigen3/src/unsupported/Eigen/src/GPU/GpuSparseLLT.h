// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU sparse Cholesky (LL^T / LL^H) via cuDSS.
//
// Usage:
//   SparseLLT<double> llt(A);        // analyze + factorize
//   VectorXd x = llt.solve(b);          // solve
//   llt.analyzePattern(A);              // or separate phases
//   llt.factorize(A_new);              // reuse symbolic analysis

#ifndef EIGEN_GPU_SPARSE_LLT_H
#define EIGEN_GPU_SPARSE_LLT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSparseSolverBase.h"

namespace Eigen {
namespace gpu {

/** GPU sparse Cholesky factorization (LL^T for real, LL^H for complex).
 *
 * Wraps cuDSS with CUDSS_MTYPE_SPD (real) or CUDSS_MTYPE_HPD (complex).
 * Accepts ColMajor SparseMatrix (CSC), reinterpreted as CSR with swapped
 * triangle view for zero-copy upload.
 *
 * \tparam Scalar_  float, double, complex<float>, or complex<double>
 * \tparam UpLo_    Lower (default) or Upper — which triangle of A is stored
 */
template <typename Scalar_, int UpLo_ = Lower>
class SparseLLT : public internal::SparseSolverBase<Scalar_, SparseLLT<Scalar_, UpLo_>> {
  using Base = internal::SparseSolverBase<Scalar_, SparseLLT>;
  friend Base;

 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  SparseLLT() = default;

  template <typename InputType>
  explicit SparseLLT(const SparseMatrixBase<InputType>& A) {
    this->compute(A);
  }

  static constexpr bool needs_csr_conversion() { return false; }
  static constexpr cudssMatrixType_t cudss_matrix_type() { return internal::cudss_spd_type<Scalar>::value; }
  static constexpr cudssMatrixViewType_t cudss_matrix_view() {
    return internal::cudss_view_type<UpLo, ColMajor>::value;
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_SPARSE_LLT_H
