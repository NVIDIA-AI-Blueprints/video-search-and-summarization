// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2009 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_SELFADJOINT_PRODUCT_H
#define EIGEN_SELFADJOINT_PRODUCT_H

/**********************************************************************
 * This file implements a self adjoint product: C += A A^T updating only
 * half of the selfadjoint matrix C.
 * It corresponds to the level 3 SYRK and level 2 SYR Blas routines.
 **********************************************************************/

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

namespace Eigen {

template <typename Scalar, typename Index, int UpLo, bool ConjLhs, bool ConjRhs>
struct selfadjoint_rank1_update<Scalar, Index, ColMajor, UpLo, ConjLhs, ConjRhs> {
  static void run(Index size, Scalar* mat, Index stride, const Scalar* vecX, const Scalar* vecY, const Scalar& alpha) {
    typedef typename internal::packet_traits<Scalar>::type Packet;
    const Index PacketSize = internal::unpacket_traits<Packet>::size;

    internal::conj_if<ConjRhs> cjy;
    internal::conj_if<ConjLhs> cjx;
    internal::conj_helper<Packet, Packet, ConjLhs, false> pcj;

    // Process 2 columns at a time to share vecX loads and reduce loop overhead.
    Index j = 0;
    for (; j + 1 < size; j += 2) {
      Scalar s0 = alpha * cjy(vecY[j]);
      Scalar s1 = alpha * cjy(vecY[j + 1]);
      Packet ps0 = internal::pset1<Packet>(s0);
      Packet ps1 = internal::pset1<Packet>(s1);

      if (UpLo == Lower) {
        Scalar* EIGEN_RESTRICT col0 = mat + stride * j + j;
        Scalar* EIGEN_RESTRICT col1 = mat + stride * (j + 1) + (j + 1);

        // Diagonal and cross-diagonal scalar elements
        col0[0] += s0 * cjx(vecX[j]);
        col0[1] += s0 * cjx(vecX[j + 1]);
        col1[0] += s1 * cjx(vecX[j + 1]);

        // Shared vectorized loop for rows j+2..size-1
        Index len = size - j - 2;
        const Scalar* EIGEN_RESTRICT xp = vecX + j + 2;
        Scalar* EIGEN_RESTRICT d0 = col0 + 2;
        Scalar* EIGEN_RESTRICT d1 = col1 + 1;

        Index k = 0;
        Index vectorizedEnd = (len / PacketSize) * PacketSize;
        for (; k < vectorizedEnd; k += PacketSize) {
          Packet xi = internal::ploadu<Packet>(xp + k);
          Packet m0 = internal::ploadu<Packet>(d0 + k);
          m0 = pcj.pmadd(xi, ps0, m0);
          internal::pstoreu(d0 + k, m0);
          Packet m1 = internal::ploadu<Packet>(d1 + k);
          m1 = pcj.pmadd(xi, ps1, m1);
          internal::pstoreu(d1 + k, m1);
        }
        for (; k < len; ++k) {
          Scalar cx = cjx(xp[k]);
          d0[k] += s0 * cx;
          d1[k] += s1 * cx;
        }
      } else {
        // UpLo == Upper
        Scalar* EIGEN_RESTRICT col0 = mat + stride * j;
        Scalar* EIGEN_RESTRICT col1 = mat + stride * (j + 1);

        // Shared vectorized loop for rows 0..j-1
        const Scalar* EIGEN_RESTRICT xp = vecX;
        Index len = j;
        Index k = 0;
        Index vectorizedEnd = (len / PacketSize) * PacketSize;
        for (; k < vectorizedEnd; k += PacketSize) {
          Packet xi = internal::ploadu<Packet>(xp + k);
          Packet m0 = internal::ploadu<Packet>(col0 + k);
          Packet m1 = internal::ploadu<Packet>(col1 + k);
          m0 = pcj.pmadd(xi, ps0, m0);
          m1 = pcj.pmadd(xi, ps1, m1);
          internal::pstoreu(col0 + k, m0);
          internal::pstoreu(col1 + k, m1);
        }
        for (; k < len; ++k) {
          Scalar cx = cjx(xp[k]);
          col0[k] += s0 * cx;
          col1[k] += s1 * cx;
        }

        // Diagonal and cross-diagonal scalar elements
        col0[j] += s0 * cjx(vecX[j]);
        col1[j] += s1 * cjx(vecX[j]);
        col1[j + 1] += s1 * cjx(vecX[j + 1]);
      }
    }

    // Handle last column if size is odd
    if (j < size) {
      Scalar s = alpha * cjy(vecY[j]);
      Packet ps = internal::pset1<Packet>(s);
      Index start = UpLo == Lower ? j : 0;
      Index len = UpLo == Lower ? size - j : j + 1;
      Scalar* EIGEN_RESTRICT dst = mat + stride * j + start;
      const Scalar* EIGEN_RESTRICT xp = vecX + start;

      Index k = 0;
      Index vectorizedEnd = (len / PacketSize) * PacketSize;
      for (; k < vectorizedEnd; k += PacketSize) {
        Packet xi = internal::ploadu<Packet>(xp + k);
        Packet di = internal::ploadu<Packet>(dst + k);
        di = pcj.pmadd(xi, ps, di);
        internal::pstoreu(dst + k, di);
      }
      for (; k < len; ++k) {
        dst[k] += s * cjx(xp[k]);
      }
    }
  }
};

template <typename Scalar, typename Index, int UpLo, bool ConjLhs, bool ConjRhs>
struct selfadjoint_rank1_update<Scalar, Index, RowMajor, UpLo, ConjLhs, ConjRhs> {
  static void run(Index size, Scalar* mat, Index stride, const Scalar* vecX, const Scalar* vecY, const Scalar& alpha) {
    selfadjoint_rank1_update<Scalar, Index, ColMajor, UpLo == Lower ? Upper : Lower, ConjRhs, ConjLhs>::run(
        size, mat, stride, vecY, vecX, alpha);
  }
};

template <typename MatrixType, typename OtherType, int UpLo, bool OtherIsVector = OtherType::IsVectorAtCompileTime>
struct selfadjoint_product_selector;

template <typename MatrixType, typename OtherType, int UpLo>
struct selfadjoint_product_selector<MatrixType, OtherType, UpLo, true> {
  static void run(MatrixType& mat, const OtherType& other, const typename MatrixType::Scalar& alpha) {
    typedef typename MatrixType::Scalar Scalar;
    typedef internal::blas_traits<OtherType> OtherBlasTraits;
    typedef typename OtherBlasTraits::DirectLinearAccessType ActualOtherType;
    typedef internal::remove_all_t<ActualOtherType> ActualOtherType_;
    internal::add_const_on_value_type_t<ActualOtherType> actualOther = OtherBlasTraits::extract(other.derived());

    Scalar actualAlpha = alpha * OtherBlasTraits::extractScalarFactor(other.derived());

    enum {
      StorageOrder = (internal::traits<MatrixType>::Flags & RowMajorBit) ? RowMajor : ColMajor,
      UseOtherDirectly = ActualOtherType_::InnerStrideAtCompileTime == 1
    };
    internal::gemv_static_vector_if<Scalar, OtherType::SizeAtCompileTime, OtherType::MaxSizeAtCompileTime,
                                    !UseOtherDirectly>
        static_other;

    ei_declare_aligned_stack_constructed_variable(
        Scalar, actualOtherPtr, other.size(),
        (UseOtherDirectly ? const_cast<Scalar*>(actualOther.data()) : static_other.data()));

    if (!UseOtherDirectly)
      Map<typename ActualOtherType_::PlainObject>(actualOtherPtr, actualOther.size()) = actualOther;

    selfadjoint_rank1_update<
        Scalar, Index, StorageOrder, UpLo, OtherBlasTraits::NeedToConjugate && NumTraits<Scalar>::IsComplex,
        (!OtherBlasTraits::NeedToConjugate) && NumTraits<Scalar>::IsComplex>::run(other.size(), mat.data(),
                                                                                  mat.outerStride(), actualOtherPtr,
                                                                                  actualOtherPtr, actualAlpha);
  }
};

template <typename MatrixType, typename OtherType, int UpLo>
struct selfadjoint_product_selector<MatrixType, OtherType, UpLo, false> {
  static void run(MatrixType& mat, const OtherType& other, const typename MatrixType::Scalar& alpha) {
    typedef typename MatrixType::Scalar Scalar;
    typedef internal::blas_traits<OtherType> OtherBlasTraits;
    typedef typename OtherBlasTraits::DirectLinearAccessType ActualOtherType;
    typedef internal::remove_all_t<ActualOtherType> ActualOtherType_;
    internal::add_const_on_value_type_t<ActualOtherType> actualOther = OtherBlasTraits::extract(other.derived());

    Scalar actualAlpha = alpha * OtherBlasTraits::extractScalarFactor(other.derived());

    enum {
      IsRowMajor = (internal::traits<MatrixType>::Flags & RowMajorBit) ? 1 : 0,
      OtherIsRowMajor = ActualOtherType_::Flags & RowMajorBit ? 1 : 0
    };

    Index size = mat.cols();
    Index depth = actualOther.cols();

    typedef internal::gemm_blocking_space<IsRowMajor ? RowMajor : ColMajor, Scalar, Scalar,
                                          MatrixType::MaxColsAtCompileTime, MatrixType::MaxColsAtCompileTime,
                                          ActualOtherType_::MaxColsAtCompileTime>
        BlockingType;

    BlockingType blocking(size, size, depth, 1, false);

    internal::general_matrix_matrix_triangular_product<
        Index, Scalar, OtherIsRowMajor ? RowMajor : ColMajor,
        OtherBlasTraits::NeedToConjugate && NumTraits<Scalar>::IsComplex, Scalar, OtherIsRowMajor ? ColMajor : RowMajor,
        (!OtherBlasTraits::NeedToConjugate) && NumTraits<Scalar>::IsComplex, IsRowMajor ? RowMajor : ColMajor,
        MatrixType::InnerStrideAtCompileTime, UpLo>::run(size, depth, actualOther.data(), actualOther.outerStride(),
                                                         actualOther.data(), actualOther.outerStride(), mat.data(),
                                                         mat.innerStride(), mat.outerStride(), actualAlpha, blocking);
  }
};

// high level API

template <typename MatrixType, unsigned int UpLo>
template <typename DerivedU>
EIGEN_DEVICE_FUNC SelfAdjointView<MatrixType, UpLo>& SelfAdjointView<MatrixType, UpLo>::rankUpdate(
    const MatrixBase<DerivedU>& u, const Scalar& alpha) {
  selfadjoint_product_selector<MatrixType, DerivedU, UpLo>::run(nestedExpression(), u.derived(), alpha);

  return *this;
}

}  // end namespace Eigen

#endif  // EIGEN_SELFADJOINT_PRODUCT_H
