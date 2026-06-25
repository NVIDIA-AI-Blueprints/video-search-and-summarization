// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2010 Vincent Lejeune
// Copyright (C) 2010 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_BLOCK_HOUSEHOLDER_H
#define EIGEN_BLOCK_HOUSEHOLDER_H

// This file contains some helper function to deal with block householder reflectors

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/** \internal */
// This variant avoid modifications in vectors
template <typename TriangularFactorType, typename VectorsType, typename CoeffsType>
void make_block_householder_triangular_factor(TriangularFactorType& triFactor, const VectorsType& vectors,
                                              const CoeffsType& hCoeffs) {
  const Index nbVecs = vectors.cols();
  eigen_assert(triFactor.rows() == nbVecs && triFactor.cols() == nbVecs && vectors.rows() >= nbVecs);

  for (Index i = nbVecs - 1; i >= 0; --i) {
    Index rs = vectors.rows() - i - 1;
    Index rt = nbVecs - i - 1;

    if (rt > 0) {
      triFactor.row(i).tail(rt).noalias() = -hCoeffs(i) * vectors.col(i).tail(rs).adjoint() *
                                            vectors.bottomRightCorner(rs, rt).template triangularView<UnitLower>();

      triFactor.row(i).tail(rt) =
          (triFactor.row(i).tail(rt) * triFactor.bottomRightCorner(rt, rt).template triangularView<Upper>()).eval();
    }
    triFactor(i, i) = hCoeffs(i);
  }
}

/** \internal
 * if forward then perform   mat = H0 * H1 * H2 * mat
 * otherwise perform         mat = H2 * H1 * H0 * mat
 */
template <typename MatrixType, typename VectorsType, typename CoeffsType>
void apply_block_householder_on_the_left(MatrixType& mat, const VectorsType& vectors, const CoeffsType& hCoeffs,
                                         bool forward) {
  enum { TFactorSize = VectorsType::ColsAtCompileTime };
  Index nbVecs = vectors.cols();
  Matrix<typename MatrixType::Scalar, TFactorSize, TFactorSize, RowMajor> T(nbVecs, nbVecs);

  if (forward)
    make_block_householder_triangular_factor(T, vectors, hCoeffs);
  else
    make_block_householder_triangular_factor(T, vectors, hCoeffs.conjugate());
  const TriangularView<const VectorsType, UnitLower> V(vectors);

  // A -= V T V^* A
  Matrix<typename MatrixType::Scalar, VectorsType::ColsAtCompileTime, MatrixType::ColsAtCompileTime,
         (VectorsType::MaxColsAtCompileTime == 1 && MatrixType::MaxColsAtCompileTime != 1) ? RowMajor : ColMajor,
         VectorsType::MaxColsAtCompileTime, MatrixType::MaxColsAtCompileTime>
      tmp = V.adjoint() * mat;
  if (forward)
    tmp = (T.template triangularView<Upper>() * tmp).eval();
  else
    tmp = (T.template triangularView<Upper>().adjoint() * tmp).eval();
  mat.noalias() -= V * tmp;
}

/** \internal
 * if forward then perform   mat = mat * H0 * H1 * H2
 * otherwise perform         mat = mat * H2 * H1 * H0
 */
template <typename MatrixType, typename VectorsType, typename CoeffsType>
void apply_block_householder_on_the_right(MatrixType& mat, const VectorsType& vectors, const CoeffsType& hCoeffs,
                                          bool forward) {
  enum { TFactorSize = VectorsType::ColsAtCompileTime };
  Index nbVecs = vectors.cols();
  Matrix<typename MatrixType::Scalar, TFactorSize, TFactorSize, RowMajor> T(nbVecs, nbVecs);

  if (forward)
    make_block_householder_triangular_factor(T, vectors, hCoeffs);
  else
    make_block_householder_triangular_factor(T, vectors, hCoeffs.conjugate());
  const TriangularView<const VectorsType, UnitLower> V(vectors);

  // A -= (A * V) * T * V^*   (forward)
  // A -= (A * V) * T^* * V^* (backward)
  Matrix<typename MatrixType::Scalar, MatrixType::RowsAtCompileTime, VectorsType::ColsAtCompileTime,
         (MatrixType::MaxRowsAtCompileTime == 1 && VectorsType::MaxColsAtCompileTime != 1) ? ColMajor : RowMajor,
         MatrixType::MaxRowsAtCompileTime, VectorsType::MaxColsAtCompileTime>
      tmp = mat * V;
  if (forward)
    tmp = (tmp * T.template triangularView<Upper>()).eval();
  else
    tmp = (tmp * T.template triangularView<Upper>().adjoint()).eval();
  mat.noalias() -= tmp * V.adjoint();
}

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_BLOCK_HOUSEHOLDER_H
