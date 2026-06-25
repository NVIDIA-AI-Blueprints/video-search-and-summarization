// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2007 Julien Pommier
// Copyright (C) 2009-2019 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_ARCH_GENERIC_PACKET_MATH_POLYNOMIALS_H
#define EIGEN_ARCH_GENERIC_PACKET_MATH_POLYNOMIALS_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/* polevl (modified for Eigen)
 *
 *      Evaluate polynomial
 *
 *
 *
 * SYNOPSIS:
 *
 * int N;
 * Scalar x, y, coef[N+1];
 *
 * y = polevl<decltype(x), N>( x, coef);
 *
 *
 *
 * DESCRIPTION:
 *
 * Evaluates polynomial of degree N:
 *
 *                     2          N
 * y  =  C  + C x + C x  +...+ C x
 *        0    1     2          N
 *
 * Coefficients are stored in reverse order:
 *
 * coef[0] = C  , ..., coef[N] = C  .
 *            N                   0
 *
 *  The function p1evl() assumes that coef[N] = 1.0 and is
 * omitted from the array.  Its calling arguments are
 * otherwise the same as polevl().
 *
 *
 * The Eigen implementation is templatized.  For best speed, store
 * coef as a const array (constexpr), e.g.
 *
 * const double coef[] = {1.0, 2.0, 3.0, ...};
 *
 */
template <typename Packet, int N>
struct ppolevl {
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x,
                                                          const typename unpacket_traits<Packet>::type coeff[]) {
    EIGEN_STATIC_ASSERT((N > 0), YOU_MADE_A_PROGRAMMING_MISTAKE);
    return pmadd(ppolevl<Packet, N - 1>::run(x, coeff), x, pset1<Packet>(coeff[N]));
  }
};

template <typename Packet>
struct ppolevl<Packet, 0> {
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Packet run(const Packet& x,
                                                          const typename unpacket_traits<Packet>::type coeff[]) {
    EIGEN_UNUSED_VARIABLE(x);
    return pset1<Packet>(coeff[0]);
  }
};

/* chbevl (modified for Eigen)
 *
 *     Evaluate Chebyshev series
 *
 *
 *
 * SYNOPSIS:
 *
 * int N;
 * Scalar x, y, coef[N], chebevl();
 *
 * y = chbevl( x, coef, N );
 *
 *
 *
 * DESCRIPTION:
 *
 * Evaluates the series
 *
 *        N-1
 *         - '
 *  y  =   >   coef[i] T (x/2)
 *         -            i
 *        i=0
 *
 * of Chebyshev polynomials Ti at argument x/2.
 *
 * Coefficients are stored in reverse order, i.e. the zero
 * order term is last in the array.  Note N is the number of
 * coefficients, not the order.
 *
 * If coefficients are for the interval a to b, x must
 * have been transformed to x -> 2(2x - b - a)/(b-a) before
 * entering the routine.  This maps x from (a, b) to (-1, 1),
 * over which the Chebyshev polynomials are defined.
 *
 * If the coefficients are for the inverted interval, in
 * which (a, b) is mapped to (1/b, 1/a), the transformation
 * required is x -> 2(2ab/x - b - a)/(b-a).  If b is infinity,
 * this becomes x -> 4a/x - 1.
 *
 *
 *
 * SPEED:
 *
 * Taking advantage of the recurrence properties of the
 * Chebyshev polynomials, the routine requires one more
 * addition per loop than evaluating a nested polynomial of
 * the same degree.
 *
 */

template <typename Packet, int N>
struct pchebevl {
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE Packet run(Packet x,
                                                          const typename unpacket_traits<Packet>::type coef[]) {
    typedef typename unpacket_traits<Packet>::type Scalar;
    Packet b0 = pset1<Packet>(coef[0]);
    Packet b1 = pset1<Packet>(static_cast<Scalar>(0.f));
    Packet b2;

    for (int i = 1; i < N; i++) {
      b2 = b1;
      b1 = b0;
      b0 = psub(pmadd(x, b1, pset1<Packet>(coef[i])), b2);
    }

    return pmul(pset1<Packet>(static_cast<Scalar>(0.5f)), psub(b0, b2));
  }
};

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_ARCH_GENERIC_PACKET_MATH_POLYNOMIALS_H
