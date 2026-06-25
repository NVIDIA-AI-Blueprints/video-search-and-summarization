// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// Lightweight expression types for DeviceMatrix operations.
//
// These are NOT Eigen expression templates. Each type maps 1:1 to a single
// NVIDIA library call (cuBLAS or cuSOLVER). There is no coefficient-level
// evaluation, no lazy fusion, no packet operations.
//
// Expression types:
//   AdjointView<S>   — d_A.adjoint()  → marks ConjTrans for GEMM
//   TransposeView<S> — d_A.transpose() → marks Trans for GEMM
//   Scaled<Expr>     — alpha * expr    → carries scalar factor
//   gpu::GemmExpr<Lhs, Rhs> — lhs * rhs    → dispatches to cublasXgemm

#ifndef EIGEN_GPU_DEVICE_EXPR_H
#define EIGEN_GPU_DEVICE_EXPR_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./CuBlasSupport.h"

namespace Eigen {
namespace gpu {

namespace internal {
// Forward declaration — specializations follow below, after the class definitions.
template <typename Expr>
struct device_expr_traits;
}  // namespace internal

// Forward declaration.
template <typename Scalar_>
class DeviceMatrix;

// ---- AdjointView: marks ConjTrans -------------------------------------------
// Returned by DeviceMatrix::adjoint(). Maps to cublasXgemm transA/B = C.

template <typename Scalar_>
class AdjointView {
 public:
  using Scalar = Scalar_;
  explicit AdjointView(const DeviceMatrix<Scalar>& m) : mat_(m) {}
  const DeviceMatrix<Scalar>& matrix() const { return mat_; }

 private:
  const DeviceMatrix<Scalar>& mat_;
};

// ---- TransposeView: marks Trans ---------------------------------------------
// Returned by DeviceMatrix::transpose(). Maps to cublasXgemm transA/B = T.

template <typename Scalar_>
class TransposeView {
 public:
  using Scalar = Scalar_;
  explicit TransposeView(const DeviceMatrix<Scalar>& m) : mat_(m) {}
  const DeviceMatrix<Scalar>& matrix() const { return mat_; }

 private:
  const DeviceMatrix<Scalar>& mat_;
};

// ---- Scaled: alpha * expr ---------------------------------------------------
// Returned by operator*(Scalar, DeviceMatrix/View). Carries the scalar factor.

template <typename Inner>
class Scaled {
 public:
  using Scalar = typename internal::device_expr_traits<Inner>::scalar_type;
  Scaled(Scalar alpha, const Inner& inner) : alpha_(alpha), inner_(inner) {}
  Scalar scalar() const { return alpha_; }
  const Inner& inner() const { return inner_; }

 private:
  Scalar alpha_;
  const Inner& inner_;
};

// ---- GemmExpr: lhs * rhs -> cublasXgemm ------------------------------------
// Returned by operator*(lhs_expr, rhs_expr). Dispatches to cuBLAS GEMM.

template <typename Lhs, typename Rhs>
class GemmExpr {
 public:
  using Scalar = typename internal::device_expr_traits<Lhs>::scalar_type;
  static_assert(std::is_same<Scalar, typename internal::device_expr_traits<Rhs>::scalar_type>::value,
                "DeviceMatrix GEMM: LHS and RHS must have the same scalar type");

  GemmExpr(const Lhs& lhs, const Rhs& rhs) : lhs_(lhs), rhs_(rhs) {}
  const Lhs& lhs() const { return lhs_; }
  const Rhs& rhs() const { return rhs_; }

 private:
  // Stored by reference — like Eigen's CPU expression templates, these must
  // not be captured with auto (the references will dangle). Use .eval() or
  // assign to a DeviceMatrix immediately.
  const Lhs& lhs_;
  const Rhs& rhs_;
};

// ---- Free operator* overloads that produce GemmExpr -------------------------
// Defined after device_expr_traits so it can accept any supported view pair.

// ---- Scalar * Matrix / View -> Scaled ---------------------------------------

template <typename S>
Scaled<DeviceMatrix<S>> operator*(S alpha, const DeviceMatrix<S>& m) {
  return {alpha, m};
}

template <typename S>
Scaled<AdjointView<S>> operator*(S alpha, const AdjointView<S>& m) {
  return {alpha, m};
}

template <typename S>
Scaled<TransposeView<S>> operator*(S alpha, const TransposeView<S>& m) {
  return {alpha, m};
}

namespace internal {

// ---- Traits: extract operation info from expression types -------------------

// Default: a DeviceMatrix is NoTrans.
template <typename T>
struct device_expr_traits {
  static constexpr bool is_device_expr = false;
};

template <typename Scalar>
struct device_expr_traits<DeviceMatrix<Scalar>> {
  using scalar_type = Scalar;
  static constexpr GpuOp op = GpuOp::NoTrans;
  static constexpr bool is_device_expr = true;
  static const DeviceMatrix<Scalar>& matrix(const DeviceMatrix<Scalar>& x) { return x; }
  static Scalar alpha(const DeviceMatrix<Scalar>&) { return Scalar(1); }
};

template <typename Scalar>
struct device_expr_traits<AdjointView<Scalar>> {
  using scalar_type = Scalar;
  static constexpr GpuOp op = GpuOp::ConjTrans;
  static constexpr bool is_device_expr = true;
  static const DeviceMatrix<Scalar>& matrix(const AdjointView<Scalar>& x) { return x.matrix(); }
  static Scalar alpha(const AdjointView<Scalar>&) { return Scalar(1); }
};

template <typename Scalar>
struct device_expr_traits<TransposeView<Scalar>> {
  using scalar_type = Scalar;
  static constexpr GpuOp op = GpuOp::Trans;
  static constexpr bool is_device_expr = true;
  static const DeviceMatrix<Scalar>& matrix(const TransposeView<Scalar>& x) { return x.matrix(); }
  static Scalar alpha(const TransposeView<Scalar>&) { return Scalar(1); }
};

template <typename Inner>
struct device_expr_traits<Scaled<Inner>> {
  using scalar_type = typename device_expr_traits<Inner>::scalar_type;
  static constexpr GpuOp op = device_expr_traits<Inner>::op;
  static constexpr bool is_device_expr = true;
  static const DeviceMatrix<scalar_type>& matrix(const Scaled<Inner>& x) {
    return device_expr_traits<Inner>::matrix(x.inner());
  }
  static scalar_type alpha(const Scaled<Inner>& x) { return x.scalar() * device_expr_traits<Inner>::alpha(x.inner()); }
};

}  // namespace internal

template <typename Lhs, typename Rhs,
          typename std::enable_if<internal::device_expr_traits<Lhs>::is_device_expr &&
                                      internal::device_expr_traits<Rhs>::is_device_expr,
                                  int>::type = 0>
GemmExpr<Lhs, Rhs> operator*(const Lhs& a, const Rhs& b) {
  return {a, b};
}

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_DEVICE_EXPR_H
