// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2009 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_SELFADJOINTRANK2UPTADE_H
#define EIGEN_SELFADJOINTRANK2UPTADE_H

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/* Optimized selfadjoint matrix += alpha * uv' + conj(alpha)*vu'
 * It corresponds to the Level2 syr2 BLAS routine
 */

template <typename Scalar, typename Index, int UpLo>
struct selfadjoint_rank2_update_selector;

template <typename Scalar, typename Index>
struct selfadjoint_rank2_update_selector<Scalar, Index, Lower> {
  static void run(Index size, Scalar* mat, Index stride, const Scalar* u, const Scalar* v, const Scalar& alpha) {
    typedef typename packet_traits<Scalar>::type Packet;
    const Index PacketSize = unpacket_traits<Packet>::size;
    const Scalar cAlpha = numext::conj(alpha);

    // Process 2 columns at a time to share u/v loads and reduce loop overhead.
    Index j = 0;
    for (; j + 1 < size; j += 2) {
      // Scale factors: col[j:] += s0u * v[j:] + s0v * u[j:]
      Scalar s0u = cAlpha * numext::conj(u[j]);
      Scalar s0v = alpha * numext::conj(v[j]);
      Scalar s1u = cAlpha * numext::conj(u[j + 1]);
      Scalar s1v = alpha * numext::conj(v[j + 1]);

      Packet ps0u = pset1<Packet>(s0u);
      Packet ps0v = pset1<Packet>(s0v);
      Packet ps1u = pset1<Packet>(s1u);
      Packet ps1v = pset1<Packet>(s1v);

      Scalar* EIGEN_RESTRICT col0 = mat + stride * j + j;
      Scalar* EIGEN_RESTRICT col1 = mat + stride * (j + 1) + (j + 1);

      // Diagonal and cross-diagonal scalar elements
      col0[0] += s0u * v[j] + s0v * u[j];
      col0[1] += s0u * v[j + 1] + s0v * u[j + 1];
      col1[0] += s1u * v[j + 1] + s1v * u[j + 1];

      // Shared vectorized loop for rows j+2..size-1
      Index len = size - j - 2;
      const Scalar* EIGEN_RESTRICT up = u + j + 2;
      const Scalar* EIGEN_RESTRICT vp = v + j + 2;
      Scalar* EIGEN_RESTRICT d0 = col0 + 2;
      Scalar* EIGEN_RESTRICT d1 = col1 + 1;

      Index k = 0;
      Index vectorizedEnd = (len / PacketSize) * PacketSize;
      for (; k < vectorizedEnd; k += PacketSize) {
        Packet ui = ploadu<Packet>(up + k);
        Packet vi = ploadu<Packet>(vp + k);
        Packet m0 = ploadu<Packet>(d0 + k);
        m0 = pmadd(vi, ps0u, m0);
        m0 = pmadd(ui, ps0v, m0);
        pstoreu(d0 + k, m0);
        Packet m1 = ploadu<Packet>(d1 + k);
        m1 = pmadd(vi, ps1u, m1);
        m1 = pmadd(ui, ps1v, m1);
        pstoreu(d1 + k, m1);
      }
      for (; k < len; ++k) {
        d0[k] += s0u * vp[k] + s0v * up[k];
        d1[k] += s1u * vp[k] + s1v * up[k];
      }
    }

    // Handle last column if size is odd
    if (j < size) {
      Scalar su = cAlpha * numext::conj(u[j]);
      Scalar sv = alpha * numext::conj(v[j]);
      Packet psu = pset1<Packet>(su);
      Packet psv = pset1<Packet>(sv);

      Scalar* EIGEN_RESTRICT dst = mat + stride * j + j;
      const Scalar* EIGEN_RESTRICT up = u + j;
      const Scalar* EIGEN_RESTRICT vp = v + j;
      Index len = size - j;

      Index k = 0;
      Index vectorizedEnd = (len / PacketSize) * PacketSize;
      for (; k < vectorizedEnd; k += PacketSize) {
        Packet ui = ploadu<Packet>(up + k);
        Packet vi = ploadu<Packet>(vp + k);
        Packet di = ploadu<Packet>(dst + k);
        di = pmadd(vi, psu, di);
        di = pmadd(ui, psv, di);
        pstoreu(dst + k, di);
      }
      for (; k < len; ++k) {
        dst[k] += su * vp[k] + sv * up[k];
      }
    }
  }
};

template <typename Scalar, typename Index>
struct selfadjoint_rank2_update_selector<Scalar, Index, Upper> {
  static void run(Index size, Scalar* mat, Index stride, const Scalar* u, const Scalar* v, const Scalar& alpha) {
    typedef typename packet_traits<Scalar>::type Packet;
    const Index PacketSize = unpacket_traits<Packet>::size;
    const Scalar cAlpha = numext::conj(alpha);

    // Process 2 columns at a time to share u/v loads and reduce loop overhead.
    Index j = 0;
    for (; j + 1 < size; j += 2) {
      Scalar s0u = cAlpha * numext::conj(u[j]);
      Scalar s0v = alpha * numext::conj(v[j]);
      Scalar s1u = cAlpha * numext::conj(u[j + 1]);
      Scalar s1v = alpha * numext::conj(v[j + 1]);

      Packet ps0u = pset1<Packet>(s0u);
      Packet ps0v = pset1<Packet>(s0v);
      Packet ps1u = pset1<Packet>(s1u);
      Packet ps1v = pset1<Packet>(s1v);

      Scalar* EIGEN_RESTRICT col0 = mat + stride * j;
      Scalar* EIGEN_RESTRICT col1 = mat + stride * (j + 1);

      // Shared vectorized loop for rows 0..j-1
      Index len = j;
      Index k = 0;
      Index vectorizedEnd = (len / PacketSize) * PacketSize;
      for (; k < vectorizedEnd; k += PacketSize) {
        Packet ui = ploadu<Packet>(u + k);
        Packet vi = ploadu<Packet>(v + k);
        Packet m0 = ploadu<Packet>(col0 + k);
        m0 = pmadd(vi, ps0u, m0);
        m0 = pmadd(ui, ps0v, m0);
        pstoreu(col0 + k, m0);
        Packet m1 = ploadu<Packet>(col1 + k);
        m1 = pmadd(vi, ps1u, m1);
        m1 = pmadd(ui, ps1v, m1);
        pstoreu(col1 + k, m1);
      }
      for (; k < len; ++k) {
        col0[k] += s0u * v[k] + s0v * u[k];
        col1[k] += s1u * v[k] + s1v * u[k];
      }

      // Diagonal and cross-diagonal scalar elements
      col0[j] += s0u * v[j] + s0v * u[j];
      col1[j] += s1u * v[j] + s1v * u[j];
      col1[j + 1] += s1u * v[j + 1] + s1v * u[j + 1];
    }

    // Handle last column if size is odd
    if (j < size) {
      Scalar su = cAlpha * numext::conj(u[j]);
      Scalar sv = alpha * numext::conj(v[j]);
      Packet psu = pset1<Packet>(su);
      Packet psv = pset1<Packet>(sv);

      Scalar* EIGEN_RESTRICT dst = mat + stride * j;
      Index len = j + 1;

      Index k = 0;
      Index vectorizedEnd = (len / PacketSize) * PacketSize;
      for (; k < vectorizedEnd; k += PacketSize) {
        Packet ui = ploadu<Packet>(u + k);
        Packet vi = ploadu<Packet>(v + k);
        Packet di = ploadu<Packet>(dst + k);
        di = pmadd(vi, psu, di);
        di = pmadd(ui, psv, di);
        pstoreu(dst + k, di);
      }
      for (; k < len; ++k) {
        dst[k] += su * v[k] + sv * u[k];
      }
    }
  }
};

template <bool Cond, typename T>
using conj_expr_if =
    std::conditional_t<!Cond, const T&, CwiseUnaryOp<scalar_conjugate_op<typename traits<T>::Scalar>, T>>;

}  // end namespace internal

template <typename MatrixType, unsigned int UpLo>
template <typename DerivedU, typename DerivedV>
EIGEN_DEVICE_FUNC SelfAdjointView<MatrixType, UpLo>& SelfAdjointView<MatrixType, UpLo>::rankUpdate(
    const MatrixBase<DerivedU>& u, const MatrixBase<DerivedV>& v, const Scalar& alpha) {
  typedef internal::blas_traits<DerivedU> UBlasTraits;
  typedef typename UBlasTraits::DirectLinearAccessType ActualUType;
  typedef internal::remove_all_t<ActualUType> ActualUType_;
  internal::add_const_on_value_type_t<ActualUType> actualU = UBlasTraits::extract(u.derived());

  typedef internal::blas_traits<DerivedV> VBlasTraits;
  typedef typename VBlasTraits::DirectLinearAccessType ActualVType;
  typedef internal::remove_all_t<ActualVType> ActualVType_;
  internal::add_const_on_value_type_t<ActualVType> actualV = VBlasTraits::extract(v.derived());

  // If MatrixType is row major, then we use the routine for lower triangular in the upper triangular case and
  // vice versa, and take the complex conjugate of all coefficients and vector entries.
  enum {
    IsRowMajor = (internal::traits<MatrixType>::Flags & RowMajorBit) ? 1 : 0,
    // Only need to conjugate if complex and the condition triggers
    NeedConjU = (int(IsRowMajor) ^ int(UBlasTraits::NeedToConjugate)) && NumTraits<Scalar>::IsComplex,
    NeedConjV = (int(IsRowMajor) ^ int(VBlasTraits::NeedToConjugate)) && NumTraits<Scalar>::IsComplex,
    UseUDirectly = ActualUType_::InnerStrideAtCompileTime == 1 && !NeedConjU,
    UseVDirectly = ActualVType_::InnerStrideAtCompileTime == 1 && !NeedConjV
  };

  Scalar actualAlpha = alpha * UBlasTraits::extractScalarFactor(u.derived()) *
                       numext::conj(VBlasTraits::extractScalarFactor(v.derived()));
  if (IsRowMajor) actualAlpha = numext::conj(actualAlpha);

  const Index size = u.size();

  // Copy u to contiguous buffer, applying conjugation if needed
  internal::gemv_static_vector_if<Scalar, DerivedU::SizeAtCompileTime, DerivedU::MaxSizeAtCompileTime, !UseUDirectly>
      static_u;
  ei_declare_aligned_stack_constructed_variable(Scalar, uPtr, size,
                                                (UseUDirectly ? const_cast<Scalar*>(actualU.data()) : static_u.data()));
  if (!UseUDirectly) {
    if (NeedConjU)
      Map<typename ActualUType_::PlainObject>(uPtr, size) = actualU.conjugate();
    else
      Map<typename ActualUType_::PlainObject>(uPtr, size) = actualU;
  }

  // Copy v to contiguous buffer, applying conjugation if needed
  internal::gemv_static_vector_if<Scalar, DerivedV::SizeAtCompileTime, DerivedV::MaxSizeAtCompileTime, !UseVDirectly>
      static_v;
  ei_declare_aligned_stack_constructed_variable(Scalar, vPtr, size,
                                                (UseVDirectly ? const_cast<Scalar*>(actualV.data()) : static_v.data()));
  if (!UseVDirectly) {
    if (NeedConjV)
      Map<typename ActualVType_::PlainObject>(vPtr, size) = actualV.conjugate();
    else
      Map<typename ActualVType_::PlainObject>(vPtr, size) = actualV;
  }

  internal::selfadjoint_rank2_update_selector<
      Scalar, Index, (IsRowMajor ? int(UpLo == Upper ? Lower : Upper) : UpLo)>::run(size, nestedExpression().data(),
                                                                                    nestedExpression().outerStride(),
                                                                                    uPtr, vPtr, actualAlpha);

  return *this;
}

}  // end namespace Eigen

#endif  // EIGEN_SELFADJOINTRANK2UPTADE_H
