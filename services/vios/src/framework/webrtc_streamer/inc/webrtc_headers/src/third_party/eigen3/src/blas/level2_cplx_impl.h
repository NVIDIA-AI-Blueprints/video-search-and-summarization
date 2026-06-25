// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2009-2010 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#include "common.h"

/**  ZHEMV  performs the matrix-vector  operation
 *
 *     y := alpha*A*x + beta*y,
 *
 *  where alpha and beta are scalars, x and y are n element vectors and
 *  A is an n by n hermitian matrix.
 */
EIGEN_BLAS_FUNC(hemv)
(const char *uplo, const int *n, const RealScalar *palpha, const RealScalar *pa, const int *lda, const RealScalar *px,
 const int *incx, const RealScalar *pbeta, RealScalar *py, const int *incy) {
  typedef void (*functype)(int, const Scalar *, int, const Scalar *, Scalar *, Scalar);
  static const functype func[2] = {
      // array index: UP
      (Eigen::internal::selfadjoint_matrix_vector_product<Scalar, int, Eigen::ColMajor, Eigen::Upper, false,
                                                          false>::run),
      // array index: LO
      (Eigen::internal::selfadjoint_matrix_vector_product<Scalar, int, Eigen::ColMajor, Eigen::Lower, false,
                                                          false>::run),
  };

  const Scalar *a = reinterpret_cast<const Scalar *>(pa);
  const Scalar *x = reinterpret_cast<const Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);
  Scalar alpha = *reinterpret_cast<const Scalar *>(palpha);
  Scalar beta = *reinterpret_cast<const Scalar *>(pbeta);

  // check arguments
  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*lda < std::max(1, *n))
    info = 5;
  else if (*incx == 0)
    info = 7;
  else if (*incy == 0)
    info = 10;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HEMV ", &info);

  if (*n == 0) return;

  const Scalar *actual_x = get_compact_vector(x, *n, *incx);
  Scalar *actual_y = get_compact_vector(y, *n, *incy);

  if (beta != Scalar(1)) {
    if (beta == Scalar(0))
      make_vector(actual_y, *n).setZero();
    else
      make_vector(actual_y, *n) *= beta;
  }

  if (alpha != Scalar(0)) {
    int code = UPLO(*uplo);
    if (code >= 2 || func[code] == 0) return;

    func[code](*n, a, *lda, actual_x, actual_y, alpha);
  }

  if (actual_x != x) delete[] actual_x;
  if (actual_y != y) delete[] copy_back(actual_y, y, *n, *incy);
}

/**  HBMV  performs the matrix-vector operation
 *
 *     y := alpha*A*x + beta*y,
 *
 *  where alpha and beta are scalars, x and y are n element vectors and
 *  A is an n by n hermitian band matrix, with k super-diagonals.
 *  Diagonal elements are real; off-diagonal contributions use conjugation.
 */
EIGEN_BLAS_FUNC(hbmv)
(char *uplo, int *n, int *k, RealScalar *palpha, RealScalar *pa, int *lda, RealScalar *px, int *incx, RealScalar *pbeta,
 RealScalar *py, int *incy) {
  const Scalar alpha = *reinterpret_cast<const Scalar *>(palpha);
  const Scalar beta = *reinterpret_cast<const Scalar *>(pbeta);
  const Scalar *a = reinterpret_cast<const Scalar *>(pa);
  const Scalar *x = reinterpret_cast<const Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);

  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*k < 0)
    info = 3;
  else if (*lda < *k + 1)
    info = 6;
  else if (*incx == 0)
    info = 8;
  else if (*incy == 0)
    info = 11;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HBMV ", &info);

  if (*n == 0 || (alpha == Scalar(0) && beta == Scalar(1))) return;

  const Scalar *actual_x = get_compact_vector(x, *n, *incx);
  Scalar *actual_y = get_compact_vector(y, *n, *incy);

  // First form y := beta*y.
  if (beta != Scalar(1)) {
    if (beta == Scalar(0))
      make_vector(actual_y, *n).setZero();
    else
      make_vector(actual_y, *n) *= beta;
  }

  if (alpha == Scalar(0)) {
    if (actual_x != x) delete[] actual_x;
    if (actual_y != y) delete[] copy_back(actual_y, y, *n, *incy);
    return;
  }

  if (*k >= 8) {
    // Vectorized path: use Eigen Map segments for the inner band operations.
    ConstMatrixType band(a, *k + 1, *n, *lda);
    if (UPLO(*uplo) == UP) {
      for (int j = 0; j < *n; ++j) {
        int start = std::max(0, j - *k);
        int len = j - start;
        int offset = *k - (j - start);
        Scalar temp1 = alpha * actual_x[j];
        actual_y[j] += Scalar(Eigen::numext::real(band(*k, j))) * temp1;
        if (len > 0) {
          make_vector(actual_y + start, len) += temp1 * band.col(j).segment(offset, len);
          actual_y[j] += alpha * band.col(j).segment(offset, len).dot(make_vector(actual_x + start, len));
        }
      }
    } else {
      for (int j = 0; j < *n; ++j) {
        int len = std::min(*n - 1, j + *k) - j;
        Scalar temp1 = alpha * actual_x[j];
        actual_y[j] += Scalar(Eigen::numext::real(band(0, j))) * temp1;
        if (len > 0) {
          make_vector(actual_y + j + 1, len) += temp1 * band.col(j).segment(1, len);
          actual_y[j] += alpha * band.col(j).segment(1, len).dot(make_vector(actual_x + j + 1, len));
        }
      }
    }
  } else {
    // Scalar path: for narrow bandwidth, avoid Map overhead.
    if (UPLO(*uplo) == UP) {
      for (int j = 0; j < *n; ++j) {
        Scalar temp1 = alpha * actual_x[j];
        Scalar temp2 = Scalar(0);
        for (int i = std::max(0, j - *k); i < j; ++i) {
          Scalar aij = a[(*k + i - j) + j * *lda];
          actual_y[i] += temp1 * aij;
          temp2 += Eigen::numext::conj(aij) * actual_x[i];
        }
        actual_y[j] += Scalar(Eigen::numext::real(a[*k + j * *lda])) * temp1 + alpha * temp2;
      }
    } else {
      for (int j = 0; j < *n; ++j) {
        Scalar temp1 = alpha * actual_x[j];
        Scalar temp2 = Scalar(0);
        actual_y[j] += Scalar(Eigen::numext::real(a[j * *lda])) * temp1;
        for (int i = j + 1; i <= std::min(*n - 1, j + *k); ++i) {
          Scalar aij = a[(i - j) + j * *lda];
          actual_y[i] += temp1 * aij;
          temp2 += Eigen::numext::conj(aij) * actual_x[i];
        }
        actual_y[j] += alpha * temp2;
      }
    }
  }

  if (actual_x != x) delete[] actual_x;
  if (actual_y != y) delete[] copy_back(actual_y, y, *n, *incy);
}

/**  HPMV  performs the matrix-vector operation
 *
 *     y := alpha*A*x + beta*y,
 *
 *  where alpha and beta are scalars, x and y are n element vectors and
 *  A is an n by n hermitian matrix, supplied in packed form.
 *  Diagonal elements are real; off-diagonal contributions use conjugation.
 */
EIGEN_BLAS_FUNC(hpmv)
(char *uplo, int *n, RealScalar *palpha, RealScalar *pap, RealScalar *px, int *incx, RealScalar *pbeta, RealScalar *py,
 int *incy) {
  const Scalar alpha = *reinterpret_cast<const Scalar *>(palpha);
  const Scalar beta = *reinterpret_cast<const Scalar *>(pbeta);
  const Scalar *ap = reinterpret_cast<const Scalar *>(pap);
  const Scalar *x = reinterpret_cast<const Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);

  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 6;
  else if (*incy == 0)
    info = 9;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HPMV ", &info);

  if (*n == 0 || (alpha == Scalar(0) && beta == Scalar(1))) return;

  const Scalar *actual_x = get_compact_vector(x, *n, *incx);
  Scalar *actual_y = get_compact_vector(y, *n, *incy);

  // First form y := beta*y.
  if (beta != Scalar(1)) {
    if (beta == Scalar(0))
      make_vector(actual_y, *n).setZero();
    else
      make_vector(actual_y, *n) *= beta;
  }

  if (alpha == Scalar(0)) {
    if (actual_x != x) delete[] actual_x;
    if (actual_y != y) delete[] copy_back(actual_y, y, *n, *incy);
    return;
  }

  int kk = 0;
  if (UPLO(*uplo) == UP) {
    // Upper triangle packed: column j occupies ap[kk..kk+j].
    for (int j = 0; j < *n; ++j) {
      Scalar temp1 = alpha * actual_x[j];
      // Diagonal is real.
      actual_y[j] += Scalar(Eigen::numext::real(ap[kk + j])) * temp1;
      if (j > 0) {
        make_vector(actual_y, j) += temp1 * make_vector(ap + kk, j);
        actual_y[j] += alpha * make_vector(ap + kk, j).dot(make_vector(actual_x, j));
      }
      kk += j + 1;
    }
  } else {
    // Lower triangle packed: column j occupies ap[kk..kk+(n-j-1)].
    for (int j = 0; j < *n; ++j) {
      int len = *n - j - 1;
      Scalar temp1 = alpha * actual_x[j];
      // Diagonal is real.
      actual_y[j] += Scalar(Eigen::numext::real(ap[kk])) * temp1;
      if (len > 0) {
        make_vector(actual_y + j + 1, len) += temp1 * make_vector(ap + kk + 1, len);
        actual_y[j] += alpha * make_vector(ap + kk + 1, len).dot(make_vector(actual_x + j + 1, len));
      }
      kk += *n - j;
    }
  }

  if (actual_x != x) delete[] actual_x;
  if (actual_y != y) delete[] copy_back(actual_y, y, *n, *incy);
}

/**  ZHPR    performs the hermitian rank 1 operation
 *
 *     A := alpha*x*conjg( x' ) + A,
 *
 *  where alpha is a real scalar, x is an n element vector and A is an
 *  n by n hermitian matrix, supplied in packed form.
 */
EIGEN_BLAS_FUNC(hpr)(char *uplo, int *n, RealScalar *palpha, RealScalar *px, int *incx, RealScalar *pap) {
  typedef void (*functype)(int, Scalar *, const Scalar *, RealScalar);
  static const functype func[2] = {
      // array index: UP
      (Eigen::internal::selfadjoint_packed_rank1_update<Scalar, int, Eigen::ColMajor, Eigen::Upper, false, Conj>::run),
      // array index: LO
      (Eigen::internal::selfadjoint_packed_rank1_update<Scalar, int, Eigen::ColMajor, Eigen::Lower, false, Conj>::run),
  };

  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *ap = reinterpret_cast<Scalar *>(pap);
  RealScalar alpha = *palpha;

  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 5;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HPR  ", &info);

  if (alpha == Scalar(0)) return;

  Scalar *x_cpy = get_compact_vector(x, *n, *incx);

  int code = UPLO(*uplo);
  if (code >= 2 || func[code] == 0) return;

  func[code](*n, ap, x_cpy, alpha);

  if (x_cpy != x) delete[] x_cpy;
}

/**  ZHPR2  performs the hermitian rank 2 operation
 *
 *     A := alpha*x*conjg( y' ) + conjg( alpha )*y*conjg( x' ) + A,
 *
 *  where alpha is a scalar, x and y are n element vectors and A is an
 *  n by n hermitian matrix, supplied in packed form.
 */
EIGEN_BLAS_FUNC(hpr2)
(char *uplo, int *n, RealScalar *palpha, RealScalar *px, int *incx, RealScalar *py, int *incy, RealScalar *pap) {
  typedef void (*functype)(int, Scalar *, const Scalar *, const Scalar *, Scalar);
  static const functype func[2] = {
      // array index: UP
      (Eigen::internal::packed_rank2_update_selector<Scalar, int, Eigen::Upper>::run),
      // array index: LO
      (Eigen::internal::packed_rank2_update_selector<Scalar, int, Eigen::Lower>::run),
  };

  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);
  Scalar *ap = reinterpret_cast<Scalar *>(pap);
  Scalar alpha = *reinterpret_cast<Scalar *>(palpha);

  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 5;
  else if (*incy == 0)
    info = 7;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HPR2 ", &info);

  if (alpha == Scalar(0)) return;

  Scalar *x_cpy = get_compact_vector(x, *n, *incx);
  Scalar *y_cpy = get_compact_vector(y, *n, *incy);

  int code = UPLO(*uplo);
  if (code >= 2 || func[code] == 0) return;

  func[code](*n, ap, x_cpy, y_cpy, alpha);

  if (x_cpy != x) delete[] x_cpy;
  if (y_cpy != y) delete[] y_cpy;
}

/**  ZHER   performs the hermitian rank 1 operation
 *
 *     A := alpha*x*conjg( x' ) + A,
 *
 *  where alpha is a real scalar, x is an n element vector and A is an
 *  n by n hermitian matrix.
 */
EIGEN_BLAS_FUNC(her)(char *uplo, int *n, RealScalar *palpha, RealScalar *px, int *incx, RealScalar *pa, int *lda) {
  typedef void (*functype)(int, Scalar *, int, const Scalar *, const Scalar *, const Scalar &);
  static const functype func[2] = {
      // array index: UP
      (Eigen::selfadjoint_rank1_update<Scalar, int, Eigen::ColMajor, Eigen::Upper, false, Conj>::run),
      // array index: LO
      (Eigen::selfadjoint_rank1_update<Scalar, int, Eigen::ColMajor, Eigen::Lower, false, Conj>::run),
  };

  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *a = reinterpret_cast<Scalar *>(pa);
  RealScalar alpha = *reinterpret_cast<RealScalar *>(palpha);

  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 5;
  else if (*lda < std::max(1, *n))
    info = 7;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HER  ", &info);

  if (alpha == RealScalar(0)) return;

  Scalar *x_cpy = get_compact_vector(x, *n, *incx);

  int code = UPLO(*uplo);
  if (code >= 2 || func[code] == 0) return;

  func[code](*n, a, *lda, x_cpy, x_cpy, alpha);

  matrix(a, *n, *n, *lda).diagonal().imag().setZero();

  if (x_cpy != x) delete[] x_cpy;
}

/**  ZHER2  performs the hermitian rank 2 operation
 *
 *     A := alpha*x*conjg( y' ) + conjg( alpha )*y*conjg( x' ) + A,
 *
 *  where alpha is a scalar, x and y are n element vectors and A is an n
 *  by n hermitian matrix.
 */
EIGEN_BLAS_FUNC(her2)
(char *uplo, int *n, RealScalar *palpha, RealScalar *px, int *incx, RealScalar *py, int *incy, RealScalar *pa,
 int *lda) {
  typedef void (*functype)(int, Scalar *, int, const Scalar *, const Scalar *, Scalar);
  static const functype func[2] = {
      // array index: UP
      (Eigen::internal::rank2_update_selector<Scalar, int, Eigen::Upper>::run),
      // array index: LO
      (Eigen::internal::rank2_update_selector<Scalar, int, Eigen::Lower>::run),
  };

  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);
  Scalar *a = reinterpret_cast<Scalar *>(pa);
  Scalar alpha = *reinterpret_cast<Scalar *>(palpha);

  int info = 0;
  if (UPLO(*uplo) == INVALID)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 5;
  else if (*incy == 0)
    info = 7;
  else if (*lda < std::max(1, *n))
    info = 9;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "HER2 ", &info);

  if (alpha == Scalar(0)) return;

  Scalar *x_cpy = get_compact_vector(x, *n, *incx);
  Scalar *y_cpy = get_compact_vector(y, *n, *incy);

  int code = UPLO(*uplo);
  if (code >= 2 || func[code] == 0) return;

  func[code](*n, a, *lda, x_cpy, y_cpy, alpha);

  matrix(a, *n, *n, *lda).diagonal().imag().setZero();

  if (x_cpy != x) delete[] x_cpy;
  if (y_cpy != y) delete[] y_cpy;
}

/**  ZGERU  performs the rank 1 operation
 *
 *     A := alpha*x*y' + A,
 *
 *  where alpha is a scalar, x is an m element vector, y is an n element
 *  vector and A is an m by n matrix.
 */
EIGEN_BLAS_FUNC(geru)
(int *m, int *n, RealScalar *palpha, RealScalar *px, int *incx, RealScalar *py, int *incy, RealScalar *pa, int *lda) {
  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);
  Scalar *a = reinterpret_cast<Scalar *>(pa);
  Scalar alpha = *reinterpret_cast<Scalar *>(palpha);

  int info = 0;
  if (*m < 0)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 5;
  else if (*incy == 0)
    info = 7;
  else if (*lda < std::max(1, *m))
    info = 9;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "GERU ", &info);

  if (alpha == Scalar(0)) return;

  Scalar *x_cpy = get_compact_vector(x, *m, *incx);
  Scalar *y_cpy = get_compact_vector(y, *n, *incy);

  Eigen::internal::general_rank1_update<Scalar, int, Eigen::ColMajor, false, false>::run(*m, *n, a, *lda, x_cpy, y_cpy,
                                                                                         alpha);

  if (x_cpy != x) delete[] x_cpy;
  if (y_cpy != y) delete[] y_cpy;
}

/**  ZGERC  performs the rank 1 operation
 *
 *     A := alpha*x*conjg( y' ) + A,
 *
 *  where alpha is a scalar, x is an m element vector, y is an n element
 *  vector and A is an m by n matrix.
 */
EIGEN_BLAS_FUNC(gerc)
(int *m, int *n, RealScalar *palpha, RealScalar *px, int *incx, RealScalar *py, int *incy, RealScalar *pa, int *lda) {
  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);
  Scalar *a = reinterpret_cast<Scalar *>(pa);
  Scalar alpha = *reinterpret_cast<Scalar *>(palpha);

  int info = 0;
  if (*m < 0)
    info = 1;
  else if (*n < 0)
    info = 2;
  else if (*incx == 0)
    info = 5;
  else if (*incy == 0)
    info = 7;
  else if (*lda < std::max(1, *m))
    info = 9;
  if (info) return xerbla_(SCALAR_SUFFIX_UP "GERC ", &info);

  if (alpha == Scalar(0)) return;

  Scalar *x_cpy = get_compact_vector(x, *m, *incx);
  Scalar *y_cpy = get_compact_vector(y, *n, *incy);

  Eigen::internal::general_rank1_update<Scalar, int, Eigen::ColMajor, false, Conj>::run(*m, *n, a, *lda, x_cpy, y_cpy,
                                                                                        alpha);

  if (x_cpy != x) delete[] x_cpy;
  if (y_cpy != y) delete[] y_cpy;
}
