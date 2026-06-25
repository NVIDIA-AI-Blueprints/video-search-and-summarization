// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2016 Rasmus Munk Larsen (rmlarsen@gmail.com)
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_CONDITIONESTIMATOR_H
#define EIGEN_CONDITIONESTIMATOR_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <typename Vector, typename RealVector, bool IsComplex>
struct rcond_compute_sign {
  static inline Vector run(const Vector& v) {
    const RealVector v_abs = v.cwiseAbs();
    return (v_abs.array() == static_cast<typename Vector::RealScalar>(0))
        .select(Vector::Ones(v.size()), v.cwiseQuotient(v_abs));
  }
};

// Partial specialization to avoid elementwise division for real vectors.
template <typename Vector>
struct rcond_compute_sign<Vector, Vector, false> {
  static inline Vector run(const Vector& v) {
    return (v.array() < static_cast<typename Vector::RealScalar>(0))
        .select(-Vector::Ones(v.size()), Vector::Ones(v.size()));
  }
};

/**
 * \returns an estimate of ||inv(matrix)||_1 given a decomposition of
 * \a matrix that implements .solve() and .adjoint().solve() methods.
 *
 * This function implements Algorithms 4.1 and 5.1 from
 *   Higham, "Experience with a Matrix Norm Estimator",
 *   SIAM J. Sci. Stat. Comput., 11(4):804-809, 1990.
 * with Higham's alternating-sign safety-net estimate from
 *   Higham and Tisseur, "A Block Algorithm for Matrix 1-Norm Estimation,
 *   with an Application to 1-Norm Pseudospectra", SIAM J. Matrix Anal. Appl.,
 *   21(4):1185-1201, 2000.
 *
 * The Hager/Higham gradient ascent uses at most 5 iterations of 2 solves
 * each, giving a total cost of O(n^2).
 *
 * Supports the following decompositions: FullPivLU, PartialPivLU, LDLT, LLT.
 *
 * \sa FullPivLU, PartialPivLU, LDLT, LLT.
 */
template <typename Decomposition>
typename Decomposition::RealScalar rcond_invmatrix_L1_norm_estimate(const Decomposition& dec) {
  typedef typename Decomposition::MatrixType MatrixType;
  typedef typename Decomposition::Scalar Scalar;
  typedef typename Decomposition::RealScalar RealScalar;
  typedef typename internal::plain_col_type<MatrixType>::type Vector;
  typedef typename internal::plain_col_type<MatrixType, RealScalar>::type RealVector;
  const bool is_complex = (NumTraits<Scalar>::IsComplex != 0);

  eigen_assert(dec.rows() == dec.cols());
  const Index n = dec.rows();
  if (n == 0) return RealScalar(0);

    // Disable Index to float conversion warning
#ifdef __INTEL_COMPILER
#pragma warning push
#pragma warning(disable : 2259)
#endif
  Vector v = dec.solve(Vector::Ones(n) / Scalar(n));
#ifdef __INTEL_COMPILER
#pragma warning pop
#endif

  // lower_bound is a lower bound on
  //   ||inv(matrix)||_1  = sup_v ||inv(matrix) v||_1 / ||v||_1
  // and is the objective maximized by the supergradient ascent algorithm below.
  RealScalar lower_bound = v.template lpNorm<1>();
  if (n == 1) return lower_bound;

  // Gradient ascent: the optimum is achieved at a unit vector e_j. Each
  // iteration follows the supergradient to find which unit vector to probe next.
  RealScalar old_lower_bound = lower_bound;
  Vector sign_vector(n);
  Vector old_sign_vector;
  Index v_max_abs_index = -1;
  Index old_v_max_abs_index = v_max_abs_index;
  for (int k = 0; k < 4; ++k) {
    sign_vector = internal::rcond_compute_sign<Vector, RealVector, is_complex>::run(v);
    if (k > 0 && !is_complex && sign_vector == old_sign_vector) {
      // Break if the sign vector stagnated.
      break;
    }
    // Supergradient: z = A^{-T} * sign(v), pick argmax |z_i|.
    v = dec.adjoint().solve(sign_vector);
    v.real().cwiseAbs().maxCoeff(&v_max_abs_index);
    if (v_max_abs_index == old_v_max_abs_index) {
      // Optimality: supergradient points to the same unit vector.
      break;
    }
    // Probe the best unit vector: v = A^{-1} * e_j.
    v = dec.solve(Vector::Unit(n, v_max_abs_index));
    lower_bound = v.template lpNorm<1>();
    if (lower_bound <= old_lower_bound) {
      // No improvement from the gradient step.
      break;
    }
    if (!is_complex) {
      old_sign_vector = sign_vector;
    }
    old_v_max_abs_index = v_max_abs_index;
    old_lower_bound = lower_bound;
  }
  // Higham's alternating-sign estimate: an independent safety-net that catches
  // cases where the gradient ascent converges to a local maximum due to exact
  // cancellation patterns (especially with permutations and backsubstitutions).
  //   v_i = (-1)^i * (1 + i/(n-1)), then estimate = 2*||A^{-1}*v||_1 / (3*n).
  Scalar alternating_sign(RealScalar(1));
  for (Index i = 0; i < n; ++i) {
    // The static_cast is needed when Scalar is complex and RealScalar uses expression templates.
    v[i] = alternating_sign * static_cast<RealScalar>(RealScalar(1) + (RealScalar(i) / (RealScalar(n - 1))));
    alternating_sign = -alternating_sign;
  }
  v = dec.solve(v);
  const RealScalar alt_est = (RealScalar(2) * v.template lpNorm<1>()) / (RealScalar(3) * RealScalar(n));
  return numext::maxi(lower_bound, alt_est);
}

/** \brief Reciprocal condition number estimator.
 *
 * Computing a decomposition of a dense matrix takes O(n^3) operations, while
 * this method estimates the condition number quickly and reliably in O(n^2)
 * operations.
 *
 * \returns an estimate of the reciprocal condition number
 * (1 / (||matrix||_1 * ||inv(matrix)||_1)) of matrix, given ||matrix||_1 and
 * its decomposition. Supports the following decompositions: FullPivLU,
 * PartialPivLU, LDLT, and LLT.
 *
 * \sa FullPivLU, PartialPivLU, LDLT, LLT.
 */
template <typename Decomposition>
typename Decomposition::RealScalar rcond_estimate_helper(typename Decomposition::RealScalar matrix_norm,
                                                         const Decomposition& dec) {
  typedef typename Decomposition::RealScalar RealScalar;
  eigen_assert(dec.rows() == dec.cols());
  if (dec.rows() == 0) return NumTraits<RealScalar>::infinity();
  if (numext::is_exactly_zero(matrix_norm)) return RealScalar(0);
  if (dec.rows() == 1) return RealScalar(1);
  const RealScalar inverse_matrix_norm = rcond_invmatrix_L1_norm_estimate(dec);
  return (numext::is_exactly_zero(inverse_matrix_norm) ? RealScalar(0)
                                                       : (RealScalar(1) / inverse_matrix_norm) / matrix_norm);
}

}  // namespace internal

}  // namespace Eigen

#endif
