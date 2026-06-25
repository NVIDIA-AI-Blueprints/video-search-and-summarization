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

// computes the sum of magnitudes of all vector elements or, for a complex vector x, the sum
// res = |Rex1| + |Imx1| + |Rex2| + |Imx2| + ... + |Rexn| + |Imxn|, where x is a vector of order n
extern "C" RealScalar EIGEN_BLAS_FUNC_NAME(asum)(int *n, Scalar *px, int *incx) {
  //   std::cerr << "_asum " << *n << " " << *incx << "\n";

  Scalar *x = reinterpret_cast<Scalar *>(px);

  if (*n <= 0) return 0;

  if (*incx == 1)
    return make_vector(x, *n).cwiseAbs().sum();
  else
    return make_vector(x, *n, std::abs(*incx)).cwiseAbs().sum();
}

extern "C" int EIGEN_CAT(i, EIGEN_BLAS_FUNC_NAME(amax))(int *n, Scalar *px, int *incx) {
  if (*n <= 0) return 0;
  Scalar *x = reinterpret_cast<Scalar *>(px);

  Eigen::DenseIndex ret;
  if (*incx == 1)
    make_vector(x, *n).cwiseAbs().maxCoeff(&ret);
  else
    make_vector(x, *n, std::abs(*incx)).cwiseAbs().maxCoeff(&ret);
  return int(ret) + 1;
}

extern "C" int EIGEN_CAT(i, EIGEN_BLAS_FUNC_NAME(amin))(int *n, Scalar *px, int *incx) {
  if (*n <= 0) return 0;
  Scalar *x = reinterpret_cast<Scalar *>(px);

  Eigen::DenseIndex ret;
  if (*incx == 1)
    make_vector(x, *n).cwiseAbs().minCoeff(&ret);
  else
    make_vector(x, *n, std::abs(*incx)).cwiseAbs().minCoeff(&ret);
  return int(ret) + 1;
}

// computes a vector-vector dot product.
extern "C" Scalar EIGEN_BLAS_FUNC_NAME(dot)(int *n, Scalar *px, int *incx, Scalar *py, int *incy) {
  //   std::cerr << "_dot " << *n << " " << *incx << " " << *incy << "\n";

  if (*n <= 0) return 0;

  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);

  if (*incx == 1 && *incy == 1)
    return make_vector(x, *n).dot(make_vector(y, *n));
  else if (*incx > 0 && *incy > 0)
    return make_vector(x, *n, *incx).dot(make_vector(y, *n, *incy));
  else if (*incx < 0 && *incy > 0)
    return make_vector(x, *n, -*incx).reverse().dot(make_vector(y, *n, *incy));
  else if (*incx > 0 && *incy < 0)
    return make_vector(x, *n, *incx).dot(make_vector(y, *n, -*incy).reverse());
  else if (*incx < 0 && *incy < 0)
    return make_vector(x, *n, -*incx).reverse().dot(make_vector(y, *n, -*incy).reverse());
  else
    return 0;
}

// computes the Euclidean norm of a vector.
extern "C" Scalar EIGEN_BLAS_FUNC_NAME(nrm2)(int *n, Scalar *px, int *incx) {
  if (*n <= 0) return 0;

  Scalar *x = reinterpret_cast<Scalar *>(px);

  if (*incx == 1)
    return make_vector(x, *n).stableNorm();
  else
    return make_vector(x, *n, std::abs(*incx)).stableNorm();
}

EIGEN_BLAS_FUNC(rot)(int *n, Scalar *px, int *incx, Scalar *py, int *incy, Scalar *pc, Scalar *ps) {
  //   std::cerr << "_rot " << *n << " " << *incx << " " << *incy << "\n";
  if (*n <= 0) return;

  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);
  Scalar c = *reinterpret_cast<Scalar *>(pc);
  Scalar s = *reinterpret_cast<Scalar *>(ps);

  StridedVectorType vx(make_vector(x, *n, std::abs(*incx)));
  StridedVectorType vy(make_vector(y, *n, std::abs(*incy)));

  Eigen::Reverse<StridedVectorType> rvx(vx);
  Eigen::Reverse<StridedVectorType> rvy(vy);

  if (*incx < 0 && *incy > 0)
    Eigen::internal::apply_rotation_in_the_plane(rvx, vy, Eigen::JacobiRotation<Scalar>(c, s));
  else if (*incx > 0 && *incy < 0)
    Eigen::internal::apply_rotation_in_the_plane(vx, rvy, Eigen::JacobiRotation<Scalar>(c, s));
  else
    Eigen::internal::apply_rotation_in_the_plane(vx, vy, Eigen::JacobiRotation<Scalar>(c, s));
}

// Applies modified Givens rotation H to vectors x and y.
//   param[0] = flag:
//     -1: H = [[h11, h12], [h21, h22]]         (all 4 elements from param)
//      0: H = [[1, h12], [h21, 1]]              (h12, h21 from param)
//      1: H = [[h11, 1], [-1, h22]]             (h11, h22 from param)
//     -2: H = identity                           (no-op)
//   param[1..4] = h11, h21, h12, h22
EIGEN_BLAS_FUNC(rotm)(int *n, Scalar *px, int *incx, Scalar *py, int *incy, Scalar *param) {
  Scalar *x = reinterpret_cast<Scalar *>(px);
  Scalar *y = reinterpret_cast<Scalar *>(py);

  Scalar flag = param[0];
  if (*n <= 0 || flag == Scalar(-2)) return;

  Scalar h11, h12, h21, h22;
  if (flag < Scalar(0)) {
    h11 = param[1];
    h21 = param[2];
    h12 = param[3];
    h22 = param[4];
  } else if (flag == Scalar(0)) {
    h11 = Scalar(1);
    h21 = param[2];
    h12 = param[3];
    h22 = Scalar(1);
  } else {
    h11 = param[1];
    h21 = Scalar(-1);
    h12 = Scalar(1);
    h22 = param[4];
  }

  int kx = *incx > 0 ? 0 : (1 - *n) * *incx;
  int ky = *incy > 0 ? 0 : (1 - *n) * *incy;

  for (int i = 0; i < *n; ++i) {
    Scalar w = x[kx];
    Scalar z = y[ky];
    x[kx] = h11 * w + h12 * z;
    y[ky] = h21 * w + h22 * z;
    kx += *incx;
    ky += *incy;
  }
}

// Constructs the modified Givens transformation matrix H which zeros the second
// component of (sqrt(d1)*x1, sqrt(d2)*y1)^T.
EIGEN_BLAS_FUNC(rotmg)(Scalar *d1, Scalar *d2, Scalar *x1, Scalar *y1, Scalar *param) {
  using std::abs;

  const Scalar gam = Scalar(4096);
  const Scalar gamsq = gam * gam;
  const Scalar rgamsq = Scalar(1) / gamsq;

  Scalar flag, h11 = Scalar(0), h12 = Scalar(0), h21 = Scalar(0), h22 = Scalar(0);

  if (*d1 < Scalar(0)) {
    // Negative d1: zero everything.
    flag = Scalar(-1);
    *d1 = *d2 = *x1 = Scalar(0);
  } else {
    Scalar p2 = *d2 * *y1;
    if (p2 == Scalar(0)) {
      // d2*y1 == 0: identity transform.
      param[0] = Scalar(-2);
      return;
    }

    Scalar p1 = *d1 * *x1;
    Scalar q2 = p2 * *y1;
    Scalar q1 = p1 * *x1;
    bool do_scale = true;

    if (abs(q1) > abs(q2)) {
      h21 = -(*y1) / *x1;
      h12 = p2 / p1;
      Scalar u = Scalar(1) - h12 * h21;
      if (u <= Scalar(0)) {
        flag = Scalar(-1);
        h11 = h12 = h21 = h22 = Scalar(0);
        *d1 = *d2 = *x1 = Scalar(0);
        do_scale = false;
      } else {
        flag = Scalar(0);
        *d1 /= u;
        *d2 /= u;
        *x1 *= u;
      }
    } else if (q2 < Scalar(0)) {
      flag = Scalar(-1);
      h11 = h12 = h21 = h22 = Scalar(0);
      *d1 = *d2 = *x1 = Scalar(0);
      do_scale = false;
    } else {
      flag = Scalar(1);
      h11 = p1 / p2;
      h22 = *x1 / *y1;
      Scalar u = Scalar(1) + h11 * h22;
      Scalar temp = *d2 / u;
      *d2 = *d1 / u;
      *d1 = temp;
      *x1 = *y1 * u;
    }

    if (do_scale) {
      // Converts compact H representation (flag 0 or 1) to full form (flag -1)
      // so that scaling factors can be absorbed into all four elements.
      auto fix_h = [&]() {
        if (flag >= Scalar(0)) {
          if (flag == Scalar(0)) {
            h11 = Scalar(1);
            h22 = Scalar(1);
          } else {
            h21 = Scalar(-1);
            h12 = Scalar(1);
          }
          flag = Scalar(-1);
        }
      };

      // Scale d1 up if too small.
      while (*d1 <= rgamsq && *d1 != Scalar(0)) {
        fix_h();
        *d1 *= gamsq;
        *x1 /= gam;
        h11 /= gam;
        h12 /= gam;
      }
      // Scale d1 down if too large.
      while (*d1 >= gamsq) {
        fix_h();
        *d1 /= gamsq;
        *x1 *= gam;
        h11 *= gam;
        h12 *= gam;
      }
      // Scale |d2| up if too small.
      while (abs(*d2) <= rgamsq && *d2 != Scalar(0)) {
        fix_h();
        *d2 *= gamsq;
        h21 /= gam;
        h22 /= gam;
      }
      // Scale |d2| down if too large.
      while (abs(*d2) >= gamsq) {
        fix_h();
        *d2 /= gamsq;
        h21 *= gam;
        h22 *= gam;
      }
    }
  }

  // Store result in param array.
  if (flag < Scalar(0)) {
    param[1] = h11;
    param[2] = h21;
    param[3] = h12;
    param[4] = h22;
  } else if (flag == Scalar(0)) {
    param[2] = h21;
    param[3] = h12;
  } else {
    param[1] = h11;
    param[4] = h22;
  }
  param[0] = flag;
}
