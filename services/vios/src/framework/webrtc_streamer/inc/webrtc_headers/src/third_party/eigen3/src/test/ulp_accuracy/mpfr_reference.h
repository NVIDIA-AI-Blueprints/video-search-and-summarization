// MPFR high-precision reference implementations for ULP accuracy testing.
//
// This header provides MPFR-based reference functions for all math operations
// tested by the ulp_accuracy tool. It also includes scalar conversion helpers
// between float/double and mpfr_t.
//
// Only compiled when EIGEN_HAS_MPFR is defined (i.e., MPFR and GMP are found).
// SPDX-FileCopyrightText: The Eigen Authors
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_ULP_ACCURACY_MPFR_REFERENCE_H
#define EIGEN_ULP_ACCURACY_MPFR_REFERENCE_H

#ifdef EIGEN_HAS_MPFR
#include <mpfr.h>

// ---------------------------------------------------------------------------
// Scalar <-> mpfr_t conversion
// ---------------------------------------------------------------------------

template <typename Scalar>
static void mpfr_set_scalar(mpfr_t rop, Scalar x, mpfr_rnd_t rnd);
template <>
void mpfr_set_scalar<float>(mpfr_t rop, float x, mpfr_rnd_t rnd) {
  mpfr_set_flt(rop, x, rnd);
}
template <>
void mpfr_set_scalar<double>(mpfr_t rop, double x, mpfr_rnd_t rnd) {
  mpfr_set_d(rop, x, rnd);
}

template <typename Scalar>
static Scalar mpfr_get_scalar(mpfr_t op, mpfr_rnd_t rnd);
template <>
float mpfr_get_scalar<float>(mpfr_t op, mpfr_rnd_t rnd) {
  return mpfr_get_flt(op, rnd);
}
template <>
double mpfr_get_scalar<double>(mpfr_t op, mpfr_rnd_t rnd) {
  return mpfr_get_d(op, rnd);
}

// ---------------------------------------------------------------------------
// MPFR wrappers for functions not directly provided by libmpfr
// ---------------------------------------------------------------------------

// logistic(x) = 1 / (1 + exp(-x))
static int mpfr_logistic(mpfr_t rop, const mpfr_t op, mpfr_rnd_t rnd) {
  mpfr_t tmp, one;
  mpfr_init2(tmp, mpfr_get_prec(rop));
  mpfr_init2(one, mpfr_get_prec(rop));
  mpfr_set_ui(one, 1, rnd);
  mpfr_neg(tmp, op, rnd);
  mpfr_exp(tmp, tmp, rnd);
  mpfr_add(tmp, tmp, one, rnd);
  int ret = mpfr_div(rop, one, tmp, rnd);
  mpfr_clear(tmp);
  mpfr_clear(one);
  return ret;
}

// rsqrt(x) = 1 / sqrt(x)
static int mpfr_rsqrt(mpfr_t rop, const mpfr_t op, mpfr_rnd_t rnd) { return mpfr_rec_sqrt(rop, op, rnd); }

// exp2(x) = 2^x
static int mpfr_exp2_wrap(mpfr_t rop, const mpfr_t op, mpfr_rnd_t rnd) {
  mpfr_t two;
  mpfr_init2(two, mpfr_get_prec(rop));
  mpfr_set_ui(two, 2, rnd);
  int ret = mpfr_pow(rop, two, op, rnd);
  mpfr_clear(two);
  return ret;
}

// log2(x) — thin wrapper to match the function signature convention.
static int mpfr_log2_wrap(mpfr_t rop, const mpfr_t op, mpfr_rnd_t rnd) { return mpfr_log2(rop, op, rnd); }

#endif  // EIGEN_HAS_MPFR

#endif  // EIGEN_ULP_ACCURACY_MPFR_REFERENCE_H
