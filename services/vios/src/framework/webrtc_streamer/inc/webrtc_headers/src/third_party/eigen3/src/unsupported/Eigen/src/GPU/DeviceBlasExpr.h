// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// BLAS Level 3 expression types for gpu::DeviceMatrix (beyond GEMM):
//   TrsmExpr           -> cublasXtrsm   (triangular solve)
//   SymmExpr           -> cublasXsymm   (symmetric multiply, real)
//                      -> cublasXhemm   (Hermitian multiply, complex)
//   SyrkExpr           -> cublasXsyrk   (symmetric rank-k update, real)
//                      -> cublasXherk   (Hermitian rank-k update, complex)

#ifndef EIGEN_GPU_DEVICE_BLAS_EXPR_H
#define EIGEN_GPU_DEVICE_BLAS_EXPR_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include <functional>

namespace Eigen {
namespace gpu {

template <typename Scalar_>
class DeviceMatrix;
template <typename Scalar_, int UpLo_>
class TrsmExpr;

// ---- TriangularView --------------------------------------------------------
// d_A.triangularView<Lower>() -> view with .solve(d_B)

template <typename Scalar_, int UpLo_>
class TriangularView {
 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  explicit TriangularView(const DeviceMatrix<Scalar>& m) : mat_(m) {}
  const DeviceMatrix<Scalar>& matrix() const { return mat_; }

  /** Build a TRSM solve expression. */
  TrsmExpr<Scalar, UpLo_> solve(const DeviceMatrix<Scalar>& rhs) const { return {mat_, rhs}; }

 private:
  std::reference_wrapper<const DeviceMatrix<Scalar>> mat_;
};

// ---- TrsmExpr: triangularView<UpLo>().solve(B) -> cublasXtrsm --------------

template <typename Scalar_, int UpLo_>
class TrsmExpr {
 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  TrsmExpr(const DeviceMatrix<Scalar>& A, const DeviceMatrix<Scalar>& B) : A_(A), B_(B) {}
  const DeviceMatrix<Scalar>& matrix() const { return A_; }
  const DeviceMatrix<Scalar>& rhs() const { return B_; }

 private:
  std::reference_wrapper<const DeviceMatrix<Scalar>> A_;
  std::reference_wrapper<const DeviceMatrix<Scalar>> B_;
};

// ---- SelfAdjointView -------------------------------------------------------
// d_A.selfadjointView<Lower>() -> view that can multiply: view * d_B

template <typename Scalar_, int UpLo_>
class SelfAdjointView {
 public:
  using Scalar = Scalar_;
  using RealScalar = typename NumTraits<Scalar>::Real;
  static constexpr int UpLo = UpLo_;

  explicit SelfAdjointView(DeviceMatrix<Scalar>& m) : mat_(m) {}
  const DeviceMatrix<Scalar>& matrix() const { return mat_; }
  DeviceMatrix<Scalar>& matrix() { return mat_; }

  /** Rank-k update: C.selfadjointView<Lower>().rankUpdate(A, alpha)
   * computes C = alpha * A * A^H + C (lower triangle only).
   * Maps to cublasXsyrk (real) or cublasXherk (complex). */
  void rankUpdate(const DeviceMatrix<Scalar>& A, RealScalar alpha = RealScalar(1));

 private:
  std::reference_wrapper<DeviceMatrix<Scalar>> mat_;
};

// Const variant for multiplication only (no rankUpdate).
template <typename Scalar_, int UpLo_>
class ConstSelfAdjointView {
 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  explicit ConstSelfAdjointView(const DeviceMatrix<Scalar>& m) : mat_(m) {}
  const DeviceMatrix<Scalar>& matrix() const { return mat_; }

 private:
  std::reference_wrapper<const DeviceMatrix<Scalar>> mat_;
};

// ---- SymmExpr: selfadjointView<UpLo>() * B -> cublasXsymm/Xhemm -----------

template <typename Scalar_, int UpLo_>
class SymmExpr {
 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  SymmExpr(const DeviceMatrix<Scalar>& A, const DeviceMatrix<Scalar>& B) : A_(A), B_(B) {}
  const DeviceMatrix<Scalar>& matrix() const { return A_; }
  const DeviceMatrix<Scalar>& rhs() const { return B_; }

 private:
  std::reference_wrapper<const DeviceMatrix<Scalar>> A_;
  std::reference_wrapper<const DeviceMatrix<Scalar>> B_;
};

// operator*: SelfAdjointView * Matrix -> SymmExpr (mutable and const variants)
template <typename S, int UpLo>
SymmExpr<S, UpLo> operator*(const SelfAdjointView<S, UpLo>& a, const DeviceMatrix<S>& b) {
  return {a.matrix(), b};
}
template <typename S, int UpLo>
SymmExpr<S, UpLo> operator*(const ConstSelfAdjointView<S, UpLo>& a, const DeviceMatrix<S>& b) {
  return {a.matrix(), b};
}

// ---- SyrkExpr: rankUpdate(A) -> cublasXsyrk/Xherk --------------------------
// C.rankUpdate(A) computes C += A * A^H (or A^H * A depending on convention).

template <typename Scalar_, int UpLo_>
class SyrkExpr {
 public:
  using Scalar = Scalar_;
  static constexpr int UpLo = UpLo_;

  SyrkExpr(const DeviceMatrix<Scalar>& A) : A_(A) {}
  const DeviceMatrix<Scalar>& matrix() const { return A_; }

 private:
  std::reference_wrapper<const DeviceMatrix<Scalar>> A_;
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_DEVICE_BLAS_EXPR_H
