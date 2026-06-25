// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Pavel Guzenfeld
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_STRUCTURED_BINDINGS_H
#define EIGEN_STRUCTURED_BINDINGS_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#if EIGEN_MAX_CPP_VER >= 17 && EIGEN_COMP_CXXVER >= 17

#include <tuple>
#include <type_traits>

// Structured bindings support for fixed-size Eigen vectors and matrices.
//
// Enables:
//   Eigen::Vector3d v(1, 2, 3);
//   auto [x, y, z] = v;
//
//   Eigen::Array3i a(4, 5, 6);
//   auto& [a0, a1, a2] = a;
//
// Decomposition order follows storage order: column-major by default,
// so Matrix2d decomposes as (0,0), (1,0), (0,1), (1,1). Only fixed-size
// column-major Matrix and Array specialize here; Map, Ref, and fixed-size
// Block intentionally do not participate.

namespace std {

// std::tuple_size for fixed-size Matrix.
//
// Deliberately NOT SFINAE-gated on (Rows, Cols) because base-class-specifier
// substitution is not a SFINAE context (a malformed base via enable_if_t
// produces a non-SFINAE hard error rather than letting the primary template
// stay incomplete). The static_assert below produces a friendly diagnostic
// if generic code probes tuple_size<MatrixXd>.
template <typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
struct tuple_size<Eigen::Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>>
    : std::integral_constant<size_t, static_cast<size_t>((Rows_ > 0 && Cols_ > 0) ? Rows_* Cols_ : 0)> {
  static_assert(Rows_ != Eigen::Dynamic && Cols_ != Eigen::Dynamic,
                "Structured bindings require fixed-size Eigen types (e.g. Vector3d, not VectorXd).");
};

// std::tuple_element for fixed-size Matrix.
// Note: uses Idx_ instead of I to avoid conflict with Eigen's test framework macro.
template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
struct tuple_element<Idx_, Eigen::Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>> {
  static_assert(Rows_ != Eigen::Dynamic && Cols_ != Eigen::Dynamic,
                "Structured bindings require fixed-size Eigen types (e.g. Vector3d, not VectorXd).");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  using type = Scalar_;
};

// std::tuple_size for fixed-size Array. See note on Matrix specialization above.
template <typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
struct tuple_size<Eigen::Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>>
    : std::integral_constant<size_t, static_cast<size_t>((Rows_ > 0 && Cols_ > 0) ? Rows_* Cols_ : 0)> {
  static_assert(Rows_ != Eigen::Dynamic && Cols_ != Eigen::Dynamic,
                "Structured bindings require fixed-size Eigen types (e.g. Array3d, not ArrayXd).");
};

// std::tuple_element for fixed-size Array.
template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
struct tuple_element<Idx_, Eigen::Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>> {
  static_assert(Rows_ != Eigen::Dynamic && Cols_ != Eigen::Dynamic,
                "Structured bindings require fixed-size Eigen types (e.g. Array3d, not ArrayXd).");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  using type = Scalar_;
};

}  // namespace std

namespace Eigen {

// Until the decomposition order for genuinely 2D RowMajor storage is agreed
// upon, reject row-major matrices at the get<I> level. coeffRef(Index) is
// linear in storage order, so permitting both orientations for 2D types would
// silently flip the binding order between Matrix<T,R,C> and Matrix<T,R,C,RowMajor>.
// Vector types (Rows==1 or Cols==1) are unaffected because storage order
// does not change element order for a 1×N or N×1 shape — and Eigen forces
// row-major on 1×N regardless, so we must allow it for row vectors.
#define EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(ROWS, COLS, OPTIONS)                                       \
  static_assert((ROWS) == 1 || (COLS) == 1 || ((OPTIONS) & Eigen::RowMajorBit) == 0,                          \
                "Structured bindings on 2D RowMajor Eigen types are not supported: coeffRef(Index) follows "  \
                "storage order, so decomposition order would silently flip versus the column-major default. " \
                "Use a column-major type, or transpose first. Row/column vectors are unaffected.")

// get<Idx_> free functions for Matrix.
template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar_& get(
    Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>& m) noexcept {
  static_assert(Rows_ != Dynamic && Cols_ != Dynamic, "Structured bindings require fixed-size Eigen types.");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(Rows_, Cols_, Options_);
  return m.coeffRef(static_cast<Index>(Idx_));
}

template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const Scalar_& get(
    const Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>& m) noexcept {
  static_assert(Rows_ != Dynamic && Cols_ != Dynamic, "Structured bindings require fixed-size Eigen types.");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(Rows_, Cols_, Options_);
  return m.coeffRef(static_cast<Index>(Idx_));
}

template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar_&& get(
    Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>&& m) noexcept {
  static_assert(Rows_ != Dynamic && Cols_ != Dynamic, "Structured bindings require fixed-size Eigen types.");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(Rows_, Cols_, Options_);
  return std::move(m.coeffRef(static_cast<Index>(Idx_)));
}

// get<Idx_> free functions for Array.
template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar_& get(
    Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>& a) noexcept {
  static_assert(Rows_ != Dynamic && Cols_ != Dynamic, "Structured bindings require fixed-size Eigen types.");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(Rows_, Cols_, Options_);
  return a.coeffRef(static_cast<Index>(Idx_));
}

template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const Scalar_& get(
    const Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>& a) noexcept {
  static_assert(Rows_ != Dynamic && Cols_ != Dynamic, "Structured bindings require fixed-size Eigen types.");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(Rows_, Cols_, Options_);
  return a.coeffRef(static_cast<Index>(Idx_));
}

template <size_t Idx_, typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar_&& get(
    Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>&& a) noexcept {
  static_assert(Rows_ != Dynamic && Cols_ != Dynamic, "Structured bindings require fixed-size Eigen types.");
  static_assert(Idx_ < static_cast<size_t>(Rows_ * Cols_), "Index out of range.");
  EIGEN_STRUCTURED_BINDINGS_ASSERT_COL_MAJOR(Rows_, Cols_, Options_);
  return std::move(a.coeffRef(static_cast<Index>(Idx_)));
}

}  // namespace Eigen

#endif  // C++17

#endif  // EIGEN_STRUCTURED_BINDINGS_H
