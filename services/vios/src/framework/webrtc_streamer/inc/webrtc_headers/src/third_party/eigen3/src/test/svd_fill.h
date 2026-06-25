// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2014-2015 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TEST_SVD_FILL_H
#define EIGEN_TEST_SVD_FILL_H

template <typename T>
Array<T, 4, 1> four_denorms();

template <>
Array4f four_denorms() {
  return Array4f(5.60844e-39f, -5.60844e-39f, 4.94e-44f, -4.94e-44f);
}
template <>
Array4d four_denorms() {
  return Array4d(5.60844e-313, -5.60844e-313, 4.94e-324, -4.94e-324);
}
template <typename T>
Array<T, 4, 1> four_denorms() {
  return four_denorms<double>().cast<T>();
}

template <typename Scalar, bool IsComplex = NumTraits<Scalar>::IsComplex>
struct maybe_set_imag_part {
  static void run(Scalar& x, typename NumTraits<Scalar>::Real y) { numext::imag_ref(x) = y; }
};
template <typename Scalar>
struct maybe_set_imag_part<Scalar, false> {
  static void run(Scalar&, typename NumTraits<Scalar>::Real) {}
};

template <typename MatrixType>
void svd_fill_random(MatrixType& m, int Option = 0) {
  using std::pow;
  typedef typename MatrixType::Scalar Scalar;
  typedef typename MatrixType::RealScalar RealScalar;
  Index diagSize = (std::min)(m.rows(), m.cols());
  RealScalar s = std::numeric_limits<RealScalar>::max_exponent10 / 4;
  s = internal::random<RealScalar>(1, s);
  Matrix<RealScalar, Dynamic, 1> d = Matrix<RealScalar, Dynamic, 1>::Random(diagSize);
  for (Index k = 0; k < diagSize; ++k) d(k) = d(k) * pow(RealScalar(10), internal::random<RealScalar>(-s, s));

  bool dup = internal::random<int>(0, 10) < 3;
  // For SelfAdjoint, always use unitary U so the eigenvalues are exactly d
  // and the conditioning is controlled.  For Symmetric/general SVD, randomly
  // choose unitary or arbitrary U/V.
  bool unit_uv = (Option == SelfAdjoint) ||
                 internal::random<int>(0, 10) < (dup ? 7 : 3);  // if we duplicate some diagonal entries, then increase
                                                                // the chance to preserve them using unitary U and V

  // duplicate some singular values
  if (dup) {
    Index n = internal::random<Index>(0, d.size() - 1);
    for (Index i = 0; i < n; ++i)
      d(internal::random<Index>(0, d.size() - 1)) = d(internal::random<Index>(0, d.size() - 1));
  }

  Matrix<Scalar, Dynamic, Dynamic> U(m.rows(), diagSize);
  Matrix<Scalar, Dynamic, Dynamic> VT(diagSize, m.cols());
  if (unit_uv) {
    // in very rare cases let's try with a pure diagonal matrix
    if (internal::random<int>(0, 10) < 1) {
      U.setIdentity();
      VT.setIdentity();
    } else {
      createRandomPIMatrixOfRank(diagSize, U.rows(), U.cols(), U);
      createRandomPIMatrixOfRank(diagSize, VT.rows(), VT.cols(), VT);
    }
  } else {
    U.setRandom();
    VT.setRandom();
  }

  Matrix<Scalar, Dynamic, 1> samples(9);
  samples << Scalar(0), four_denorms<RealScalar>(), -RealScalar(1) / NumTraits<RealScalar>::highest(),
      RealScalar(1) / NumTraits<RealScalar>::highest(), (std::numeric_limits<RealScalar>::min)(),
      pow((std::numeric_limits<RealScalar>::min)(), RealScalar(0.8));

  if (Option == Symmetric || Option == SelfAdjoint) {
    if (Option == SelfAdjoint)
      m = U * d.asDiagonal() * U.adjoint();
    else
      m = U * d.asDiagonal() * U.transpose();

    // randomly nullify some rows/columns
    {
      Index count = internal::random<Index>(-diagSize, diagSize);
      for (Index k = 0; k < count; ++k) {
        Index i = internal::random<Index>(0, diagSize - 1);
        m.row(i).setZero();
        m.col(i).setZero();
      }
      if (count < 0)
        // (partly) cancel some coeffs
        if (!(dup && unit_uv)) {
          Index n = internal::random<Index>(0, m.size() - 1);
          for (Index k = 0; k < n; ++k) {
            Index i = internal::random<Index>(0, m.rows() - 1);
            Index j = internal::random<Index>(0, m.cols() - 1);
            Scalar val = samples(internal::random<Index>(0, samples.size() - 1));
            maybe_set_imag_part<Scalar>::run(val, samples.real()(internal::random<Index>(0, samples.size() - 1)));
            if (Option == SelfAdjoint) {
              if (i == j) {
                // Diagonal entries of a Hermitian matrix must be real.
                m(i, i) = numext::real(val);
              } else {
                // Off-diagonal: m(j,i) = conj(m(i,j)) for Hermitian.
                m(i, j) = val;
                m(j, i) = numext::conj(val);
              }
            } else {
              m(i, j) = val;
              m(j, i) = val;
            }
          }
        }
    }
  } else {
    m = U * d.asDiagonal() * VT;
    // (partly) cancel some coeffs
    if (!(dup && unit_uv)) {
      Index n = internal::random<Index>(0, m.size() - 1);
      for (Index k = 0; k < n; ++k) {
        Index i = internal::random<Index>(0, m.rows() - 1);
        Index j = internal::random<Index>(0, m.cols() - 1);
        m(i, j) = samples(internal::random<Index>(0, samples.size() - 1));
        maybe_set_imag_part<Scalar>::run(m(i, j), samples.real()(internal::random<Index>(0, samples.size() - 1)));
      }
    }
  }
}

#endif  // EIGEN_TEST_SVD_FILL_H
