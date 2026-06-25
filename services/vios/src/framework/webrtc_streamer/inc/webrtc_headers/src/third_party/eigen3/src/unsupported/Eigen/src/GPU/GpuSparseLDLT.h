// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU sparse LDL^T / LDL^H factorization via cuDSS.
//
// For symmetric indefinite (or Hermitian indefinite) sparse matrices.
// Same three-phase workflow as SparseLLT.
//
// Usage:
//   SparseLDLT<double> ldlt(A);      // analyze + factorize
//   VectorXd x = ldlt.solve(b);         // solve

#ifndef EIGEN_GPU_SPARSE_LDLT_H
#define EIGEN_GPU_SPARSE_LDLT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSparseSolverBase.h"

namespace Eigen {
namespace gpu {

/** GPU sparse LDL^T factorization (symmetric indefinite / Hermitian indefinite).
 *
 * Wraps cuDSS with CUDSS_MTYPE_SYMMETRIC (real) or CUDSS_MTYPE_HERMITIAN (complex).
 * Uses pivoting for numerical stability.
 *
 * \tparam Scalar_  float, double, complex<float>, or complex<double>
 * \tparam UpLo_    Lower (default) or Upper — which triangle of A is stored
 */
template <typename Scalar_, int UpLo_ = Lower>
class SparseLDLT : public internal::SparseSolverBase<Scalar_, SparseLDLT<Scalar_, UpLo_>> {
  using Base = internal::SparseSolverBase<Scalar_, SparseLDLT>;
  friend Base;

 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  SparseLDLT() = default;

  template <typename InputType>
  explicit SparseLDLT(const SparseMatrixBase<InputType>& A) {
    this->compute(A);
  }

  static constexpr bool needs_csr_conversion() { return false; }
  static constexpr cudssMatrixType_t cudss_matrix_type() { return internal::cudss_symmetric_type<Scalar>::value; }
  static constexpr cudssMatrixViewType_t cudss_matrix_view() {
    return internal::cudss_view_type<UpLo, ColMajor>::value;
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_SPARSE_LDLT_H
