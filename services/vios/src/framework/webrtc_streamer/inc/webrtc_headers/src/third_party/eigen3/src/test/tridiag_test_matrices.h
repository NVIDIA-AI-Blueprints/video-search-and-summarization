// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TEST_TRIDIAG_TEST_MATRICES_H
#define EIGEN_TEST_TRIDIAG_TEST_MATRICES_H

// Structured tridiagonal test matrices from the numerical linear algebra
// literature. Used by both the bidiagonal SVD and symmetric eigenvalue tests.
//
// Each generator writes into pre-allocated (diag, offdiag) vectors.
// For SVD, offdiag is the superdiagonal of a bidiagonal matrix.
// For eigenvalues, offdiag is the subdiagonal of a symmetric tridiagonal matrix.
//
// Usage:
//   Matrix<RealScalar, Dynamic, 1> diag(n), offdiag(n-1);
//   tridiag_identity(diag, offdiag);           // fills diag and offdiag
//   my_verify(diag, offdiag);                  // solver-specific verification

#include <Eigen/Core>

namespace Eigen {
namespace test {

// 1. Identity: d=[1,...,1], e=[0,...,0]
template <typename VectorType>
void tridiag_identity(VectorType& diag, VectorType& offdiag) {
  diag.setOnes();
  offdiag.setZero();
}

// 2. Zero: d=[0,...,0], e=[0,...,0]
template <typename VectorType>
void tridiag_zero(VectorType& diag, VectorType& offdiag) {
  diag.setZero();
  offdiag.setZero();
}

// 3. Constant: d=[c,...,c], e=[c,...,c]
template <typename VectorType>
void tridiag_constant(VectorType& diag, VectorType& offdiag,
                      typename VectorType::Scalar c = typename VectorType::Scalar(2.5)) {
  diag.setConstant(c);
  offdiag.setConstant(c);
}

// 4. 1-2-1 Toeplitz: d=[2,...,2], e=[1,...,1]
// Eigenvalues: 2 - 2*cos(k*pi/(n+1)) for k=1,...,n
template <typename VectorType>
void tridiag_1_2_1(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  diag.setConstant(Scalar(2));
  offdiag.setOnes();
}

// 5. Wilkinson W_{2m+1}: d_i = |m - i|, e=[1,...,1]
// Has pairs of eigenvalues agreeing to many digits; stresses deflation.
template <typename VectorType>
void tridiag_wilkinson(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = numext::abs(Scalar(n / 2) - Scalar(i));
  offdiag.setOnes();
}

// 6. Clement matrix: d=[0,...,0], e_i = sqrt(i*(n-1-i))
// Known eigenvalues: -(n-1), -(n-3), ..., (n-3), (n-1)
template <typename VectorType>
void tridiag_clement(VectorType& diag, VectorType& offdiag) {
  EIGEN_USING_STD(sqrt);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  diag.setZero();
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = sqrt(Scalar(i + 1) * Scalar(n - 1 - i));
}

// 7. Kahan-style: d_i = s^i, e_i = -c*s^i with s=sin(theta), c=cos(theta).
// Geometric decay with controlled condition number.
template <typename VectorType>
void tridiag_kahan(VectorType& diag, VectorType& offdiag,
                   typename VectorType::Scalar theta = typename VectorType::Scalar(0.3)) {
  EIGEN_USING_STD(sin);
  EIGEN_USING_STD(cos);
  EIGEN_USING_STD(pow);
  EIGEN_USING_STD(log);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  const Scalar s = sin(theta);
  const Scalar c = cos(theta);
  const Scalar maxPower = -log(eps) / (-log(s));
  for (Index i = 0; i < n; ++i) diag(i) = pow(s, numext::mini(Scalar(i), maxPower));
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = -c * pow(s, numext::mini(Scalar(i), maxPower));
}

// 8. Graded: d_i = base^(-i), e_i = base^(-i)
template <typename VectorType>
void tridiag_graded(VectorType& diag, VectorType& offdiag,
                    typename VectorType::Scalar base = typename VectorType::Scalar(10)) {
  EIGEN_USING_STD(pow);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = pow(base, -Scalar(i));
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = pow(base, -Scalar(i));
}

// 9. Geometric decay diagonal: d_i = base^i, e=[0,...,0]
template <typename VectorType>
void tridiag_geometric_diagonal(VectorType& diag, VectorType& offdiag,
                                typename VectorType::Scalar base = typename VectorType::Scalar(0.5)) {
  EIGEN_USING_STD(pow);
  EIGEN_USING_STD(log);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  const Scalar maxPower = -log(eps) / (-log(base));
  for (Index i = 0; i < n; ++i) diag(i) = pow(base, numext::mini(Scalar(i), maxPower));
  offdiag.setZero();
}

// 10. Geometric decay offdiagonal: d=[1,...,1], e_i = base^i
template <typename VectorType>
void tridiag_geometric_offdiag(VectorType& diag, VectorType& offdiag,
                               typename VectorType::Scalar base = typename VectorType::Scalar(0.5)) {
  EIGEN_USING_STD(pow);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  diag.setOnes();
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = pow(base, Scalar(i));
}

// 11. Clustered eigenvalues: d_i = 1 + i*eps, e=[0,...,0]
template <typename VectorType>
void tridiag_clustered(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  for (Index i = 0; i < n; ++i) diag(i) = Scalar(1) + Scalar(i) * eps;
  offdiag.setZero();
}

// 12. Two clusters: half at 1, half at eps.
template <typename VectorType>
void tridiag_two_clusters(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  for (Index i = 0; i < n; ++i) diag(i) = (i < n / 2) ? Scalar(1) : eps;
  offdiag.setZero();
}

// 13. Single tiny value: d=[1,...,1,eps], e=[eps^2,...,eps^2]
template <typename VectorType>
void tridiag_single_tiny(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  diag.setOnes();
  diag(n - 1) = eps;
  offdiag.setConstant(eps * eps);
}

// 14. Overflow/underflow: alternating big/tiny diagonal and offdiagonal.
template <typename VectorType>
void tridiag_overflow_underflow(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar big = (std::numeric_limits<Scalar>::max)() / Scalar(1000);
  const Scalar tiny = (std::numeric_limits<Scalar>::min)() * Scalar(1000);
  for (Index i = 0; i < n; ++i) diag(i) = (i % 2 == 0) ? big : tiny;
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = (i % 2 == 0) ? tiny : big;
}

// 15. Prescribed condition number: d_i = kappa^(-i/(n-1)), e_i = eps * random.
template <typename VectorType>
void tridiag_prescribed_cond(VectorType& diag, VectorType& offdiag) {
  EIGEN_USING_STD(pow);
  EIGEN_USING_STD(abs);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  const Scalar kappa = Scalar(1) / eps;
  for (Index i = 0; i < n; ++i) diag(i) = pow(kappa, -Scalar(i) / Scalar(n - 1));
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = eps * abs(internal::random<Scalar>());
}

// 16. Rank-deficient: d=[1,..,0,..,0,..,1], e=[0,...,0]
template <typename VectorType>
void tridiag_rank_deficient(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = (i < n / 3 || i >= 2 * n / 3) ? Scalar(1) : Scalar(0);
  offdiag.setZero();
}

// 17. Arrowhead-like: d_i = linspace(1,n), e_i = 1/(i+1)
template <typename VectorType>
void tridiag_arrowhead(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = Scalar(1) + Scalar(i);
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = Scalar(1) / Scalar(i + 1);
}

// 18. Repeated values: d=[1,2,3,1,2,3,...], e=[0,...,0]
template <typename VectorType>
void tridiag_repeated(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = Scalar((i % 3) + 1);
  offdiag.setZero();
}

// 19. Glued blocks: d=[1,...,1], e=0 except e[n/2-1]=eps.
// Two identity blocks coupled by a tiny off-diagonal entry.
template <typename VectorType>
void tridiag_glued(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  diag.setOnes();
  offdiag.setZero();
  if (n > 2) offdiag(n / 2 - 1) = NumTraits<Scalar>::epsilon();
}

// 20. Nearly diagonal: random diag, eps * random offdiag.
template <typename VectorType>
void tridiag_nearly_diagonal(VectorType& diag, VectorType& offdiag) {
  EIGEN_USING_STD(abs);
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  const Scalar eps = NumTraits<Scalar>::epsilon();
  diag = VectorType::Random(n).cwiseAbs() + VectorType::Constant(n, Scalar(0.1));
  for (Index i = 0; i < n - 1; ++i) offdiag(i) = eps * (Scalar(0.5) + abs(internal::random<Scalar>()));
}

// 21. Negative eigenvalues: d_i = -i, e=[1,...,1]
// (Only meaningful for symmetric eigenvalue problems, not SVD.)
template <typename VectorType>
void tridiag_negative(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = -Scalar(i + 1);
  offdiag.setOnes();
}

// 22. Mixed sign diagonal: d_i = (-1)^i * (i+1), e=[1,...,1]
// (Only meaningful for symmetric eigenvalue problems, not SVD.)
template <typename VectorType>
void tridiag_mixed_sign(VectorType& diag, VectorType& offdiag) {
  typedef typename VectorType::Scalar Scalar;
  Index n = diag.size();
  for (Index i = 0; i < n; ++i) diag(i) = ((i % 2 == 0) ? Scalar(1) : Scalar(-1)) * Scalar(i + 1);
  offdiag.setOnes();
}

// Helper: iterate over a set of sizes and call a functor with each (diag, offdiag) pair
// generated by a generator function.
//
// Usage:
//   for_tridiag_sizes([](auto& diag, auto& offdiag) {
//       tridiag_wilkinson(diag, offdiag);
//       my_verify(diag, offdiag);
//   });
template <typename Scalar, typename Func>
void for_tridiag_sizes(Func&& func) {
  const int sizes[] = {1, 2, 3, 5, 10, 16, 20, 50, 100};
  typedef Matrix<Scalar, Dynamic, 1> VectorType;
  for (int si = 0; si < int(sizeof(sizes) / sizeof(sizes[0])); ++si) {
    const Index n = sizes[si];
    VectorType diag(n), offdiag(n > 1 ? n - 1 : 0);
    func(diag, offdiag);
  }
}

// Helper: run all generators (suitable for both SVD and eigenvalue problems).
// The callback receives (diag, offdiag) after each generator fills them.
template <typename Scalar, typename Func>
void for_all_tridiag_test_matrices(Func&& verify) {
  const int sizes[] = {1, 2, 3, 5, 10, 16, 20, 50, 100};
  typedef Matrix<Scalar, Dynamic, 1> VectorType;

  for (int si = 0; si < int(sizeof(sizes) / sizeof(sizes[0])); ++si) {
    const Index n = sizes[si];
    VectorType diag(n), offdiag(n > 1 ? n - 1 : 0);

    tridiag_identity(diag, offdiag);
    verify(diag, offdiag);

    tridiag_zero(diag, offdiag);
    verify(diag, offdiag);

    tridiag_constant(diag, offdiag);
    verify(diag, offdiag);

    tridiag_1_2_1(diag, offdiag);
    verify(diag, offdiag);

    tridiag_wilkinson(diag, offdiag);
    verify(diag, offdiag);

    if (n > 1) {
      tridiag_clement(diag, offdiag);
      verify(diag, offdiag);
    }

    tridiag_kahan(diag, offdiag);
    verify(diag, offdiag);

    tridiag_graded(diag, offdiag);
    verify(diag, offdiag);

    tridiag_geometric_diagonal(diag, offdiag);
    verify(diag, offdiag);

    if (n > 1) {
      tridiag_geometric_offdiag(diag, offdiag);
      verify(diag, offdiag);
    }

    tridiag_clustered(diag, offdiag);
    verify(diag, offdiag);

    tridiag_two_clusters(diag, offdiag);
    verify(diag, offdiag);

    tridiag_single_tiny(diag, offdiag);
    verify(diag, offdiag);

    tridiag_overflow_underflow(diag, offdiag);
    verify(diag, offdiag);

    if (n > 1) {
      tridiag_prescribed_cond(diag, offdiag);
      verify(diag, offdiag);
    }

    tridiag_rank_deficient(diag, offdiag);
    verify(diag, offdiag);

    tridiag_arrowhead(diag, offdiag);
    verify(diag, offdiag);

    tridiag_repeated(diag, offdiag);
    verify(diag, offdiag);

    tridiag_glued(diag, offdiag);
    verify(diag, offdiag);

    tridiag_nearly_diagonal(diag, offdiag);
    verify(diag, offdiag);
  }
}

// Helper: run all generators, including those with negative values
// (suitable only for symmetric eigenvalue problems, not SVD).
template <typename Scalar, typename Func>
void for_all_symmetric_tridiag_test_matrices(Func&& verify) {
  for_all_tridiag_test_matrices<Scalar>(verify);

  const int sizes[] = {1, 2, 3, 5, 10, 16, 20, 50, 100};
  typedef Matrix<Scalar, Dynamic, 1> VectorType;

  for (int si = 0; si < int(sizeof(sizes) / sizeof(sizes[0])); ++si) {
    const Index n = sizes[si];
    VectorType diag(n), offdiag(n > 1 ? n - 1 : 0);

    tridiag_negative(diag, offdiag);
    verify(diag, offdiag);

    tridiag_mixed_sign(diag, offdiag);
    verify(diag, offdiag);
  }
}

}  // namespace test
}  // namespace Eigen

#endif  // EIGEN_TEST_TRIDIAG_TEST_MATRICES_H
