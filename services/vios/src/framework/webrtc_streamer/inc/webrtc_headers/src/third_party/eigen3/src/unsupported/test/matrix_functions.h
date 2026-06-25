// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2009-2011 Jitse Niesen <jitse@maths.leeds.ac.uk>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_UNSUPPORTED_TEST_MATRIX_FUNCTIONS_H
#define EIGEN_UNSUPPORTED_TEST_MATRIX_FUNCTIONS_H

#include "main.h"
#include <unsupported/Eigen/MatrixFunctions>

// For complex matrices, any matrix is fine.
template <typename MatrixType, int IsComplex = NumTraits<typename internal::traits<MatrixType>::Scalar>::IsComplex>
struct processTriangularMatrix {
  static void run(MatrixType&, MatrixType&, const MatrixType&) {}
};

// For real matrices, ensure all eigenvalues have positive real parts
// (needed for matrix log) and cap the condition number.
template <typename MatrixType>
struct processTriangularMatrix<MatrixType, 0> {
  typedef typename MatrixType::Scalar Scalar;
  static void run(MatrixType& m, MatrixType& T, const MatrixType& U) {
    using std::abs;
    const Index size = m.cols();
    Scalar maxDiag(0);

    for (Index i = 0; i < size; ++i) {
      if (i == size - 1 || numext::is_exactly_zero(T.coeff(i + 1, i))) {
        // 1x1 block (real eigenvalue): make positive.
        T.coeffRef(i, i) = abs(T.coeff(i, i));
      } else {
        // 2x2 block (complex conjugate pair): eigenvalues are T(i,i) ± bi.
        // Negate the block if the real part is negative so that the matrix
        // log is well-defined (avoids the branch cut on the negative real axis).
        if (T.coeff(i, i) < Scalar(0)) {
          T.coeffRef(i, i) = -T.coeff(i, i);
          T.coeffRef(i + 1, i + 1) = -T.coeff(i + 1, i + 1);
          T.coeffRef(i, i + 1) = -T.coeff(i, i + 1);
          T.coeffRef(i + 1, i) = -T.coeff(i + 1, i);
        }
        ++i;
      }
      maxDiag = (std::max)(maxDiag, abs(T.coeff(i, i)));
    }
    // Clamp small eigenvalues to limit condition number. Matrix power and
    // matrix function tests lose too many digits on ill-conditioned matrices.
    if (maxDiag > Scalar(0)) {
      Scalar minAllowed = maxDiag / Scalar(100);
      for (Index i = 0; i < size; ++i) {
        if (abs(T.coeff(i, i)) < minAllowed) T.coeffRef(i, i) = minAllowed;
      }
    }
    m = U * T * U.transpose();
  }
};

template <typename MatrixType, int IsComplex = NumTraits<typename internal::traits<MatrixType>::Scalar>::IsComplex>
struct generateTestMatrix;

template <typename MatrixType>
struct generateTestMatrix<MatrixType, 0> {
  static void run(MatrixType& result, typename MatrixType::Index size) {
    result = MatrixType::Random(size, size);
    RealSchur<MatrixType> schur(result);
    MatrixType T = schur.matrixT();
    processTriangularMatrix<MatrixType>::run(result, T, schur.matrixU());
  }
};

template <typename MatrixType>
struct generateTestMatrix<MatrixType, 1> {
  static void run(MatrixType& result, typename MatrixType::Index size) { result = MatrixType::Random(size, size); }
};

template <typename Derived, typename OtherDerived>
typename Derived::RealScalar relerr(const MatrixBase<Derived>& A, const MatrixBase<OtherDerived>& B) {
  return std::sqrt((A - B).cwiseAbs2().sum() / (std::min)(A.cwiseAbs2().sum(), B.cwiseAbs2().sum()));
}

#endif  // EIGEN_UNSUPPORTED_TEST_MATRIX_FUNCTIONS_H
