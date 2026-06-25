// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008-2009 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_SELFADJOINT_MATRIX_VECTOR_H
#define EIGEN_SELFADJOINT_MATRIX_VECTOR_H

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/* Optimized selfadjoint matrix * vector product:
 * This algorithm processes 4 columns at once to reduce the number of
 * load/stores of the result vector by a factor of 4 compared to the
 * naive approach, and to increase instruction-level parallelism.
 * A 2-column cleanup handles the remaining even columns, and a
 * 1-column loop handles any final odd column.
 */

template <typename Scalar, typename Index, int StorageOrder, int UpLo, bool ConjugateLhs, bool ConjugateRhs,
          int Version = Specialized>
struct selfadjoint_matrix_vector_product;

template <typename Scalar, typename Index, int StorageOrder, int UpLo, bool ConjugateLhs, bool ConjugateRhs,
          int Version>
struct selfadjoint_matrix_vector_product

{
  static EIGEN_DONT_INLINE EIGEN_DEVICE_FUNC void run(Index size, const Scalar* lhs, Index lhsStride, const Scalar* rhs,
                                                      Scalar* res, Scalar alpha);
};

template <typename Scalar, typename Index, int StorageOrder, int UpLo, bool ConjugateLhs, bool ConjugateRhs,
          int Version>
EIGEN_DONT_INLINE EIGEN_DEVICE_FUNC void
selfadjoint_matrix_vector_product<Scalar, Index, StorageOrder, UpLo, ConjugateLhs, ConjugateRhs, Version>::run(
    Index size, const Scalar* lhs, Index lhsStride, const Scalar* rhs, Scalar* res, Scalar alpha) {
  typedef typename packet_traits<Scalar>::type Packet;
  typedef typename NumTraits<Scalar>::Real RealScalar;
  const Index PacketSize = sizeof(Packet) / sizeof(Scalar);

  enum {
    IsRowMajor = StorageOrder == RowMajor ? 1 : 0,
    IsLower = UpLo == Lower ? 1 : 0,
    FirstTriangular = IsRowMajor == IsLower
  };

  conj_helper<Scalar, Scalar, NumTraits<Scalar>::IsComplex && logical_xor(ConjugateLhs, IsRowMajor), ConjugateRhs> cj0;
  conj_helper<Scalar, Scalar, NumTraits<Scalar>::IsComplex && logical_xor(ConjugateLhs, !IsRowMajor), ConjugateRhs> cj1;
  conj_helper<RealScalar, Scalar, false, ConjugateRhs> cjd;

  conj_helper<Packet, Packet, NumTraits<Scalar>::IsComplex && logical_xor(ConjugateLhs, IsRowMajor), ConjugateRhs> pcj0;
  conj_helper<Packet, Packet, NumTraits<Scalar>::IsComplex && logical_xor(ConjugateLhs, !IsRowMajor), ConjugateRhs>
      pcj1;

  Scalar cjAlpha = ConjugateRhs ? numext::conj(alpha) : alpha;

  // Compute column counts for 4-col, 2-col, and 1-col processing phases.
  // We leave up to ~8 columns near the diagonal for cleanup (short off-diagonal ranges).
  Index n4 = (numext::maxi(Index(0), size - 8) / 4) * 4;
  Index n2 = ((size - n4) / 2) * 2;
  // Remaining (size - n4 - n2) is 0 or 1 columns.

  // For !FirstTriangular: 4-col [0, n4), 2-col [n4, n4+n2), 1-col [n4+n2, size)
  // For FirstTriangular:  1-col [0, size-n4-n2), 2-col [size-n4-n2, size-n4), 4-col [size-n4, size)

  // === Phase 1: 4 columns at a time ===
  {
    Index jStart = FirstTriangular ? (size - n4) : 0;
    Index jEnd = FirstTriangular ? size : n4;

    for (Index j = jStart; j < jEnd; j += 4) {
      const Scalar* EIGEN_RESTRICT A0 = lhs + j * lhsStride;
      const Scalar* EIGEN_RESTRICT A1 = lhs + (j + 1) * lhsStride;
      const Scalar* EIGEN_RESTRICT A2 = lhs + (j + 2) * lhsStride;
      const Scalar* EIGEN_RESTRICT A3 = lhs + (j + 3) * lhsStride;

      Scalar t0 = cjAlpha * rhs[j];
      Scalar t1 = cjAlpha * rhs[j + 1];
      Scalar t2 = cjAlpha * rhs[j + 2];
      Scalar t3 = cjAlpha * rhs[j + 3];
      Packet ptmp0 = pset1<Packet>(t0);
      Packet ptmp1 = pset1<Packet>(t1);
      Packet ptmp2 = pset1<Packet>(t2);
      Packet ptmp3 = pset1<Packet>(t3);

      Scalar t4(0), t5(0), t6(0), t7(0);
      Packet ptmp4 = pzero(Packet{});
      Packet ptmp5 = pzero(Packet{});
      Packet ptmp6 = pzero(Packet{});
      Packet ptmp7 = pzero(Packet{});

      Index starti = FirstTriangular ? 0 : j + 4;
      Index endi = FirstTriangular ? j : size;
      Index alignedStart = starti + internal::first_default_aligned(&res[starti], endi - starti);
      Index alignedEnd = alignedStart + ((endi - alignedStart) / PacketSize) * PacketSize;

      // Handle the 4x4 diagonal block: diagonal elements
      res[j] += cjd.pmul(numext::real(A0[j]), t0);
      res[j + 1] += cjd.pmul(numext::real(A1[j + 1]), t1);
      res[j + 2] += cjd.pmul(numext::real(A2[j + 2]), t2);
      res[j + 3] += cjd.pmul(numext::real(A3[j + 3]), t3);

      // Handle the 4x4 diagonal block: off-diagonal cross terms
      if (FirstTriangular) {
        // Upper triangle stored (A_k[l] for l <= k)
        res[j] += cj0.pmul(A1[j], t1) + cj0.pmul(A2[j], t2) + cj0.pmul(A3[j], t3);
        res[j + 1] += cj0.pmul(A2[j + 1], t2) + cj0.pmul(A3[j + 1], t3);
        res[j + 2] += cj0.pmul(A3[j + 2], t3);

        t5 += cj1.pmul(A1[j], rhs[j]);
        t6 += cj1.pmul(A2[j], rhs[j]) + cj1.pmul(A2[j + 1], rhs[j + 1]);
        t7 += cj1.pmul(A3[j], rhs[j]) + cj1.pmul(A3[j + 1], rhs[j + 1]) + cj1.pmul(A3[j + 2], rhs[j + 2]);
      } else {
        // Lower triangle stored (A_k[l] for l >= k)
        res[j + 1] += cj0.pmul(A0[j + 1], t0);
        res[j + 2] += cj0.pmul(A0[j + 2], t0) + cj0.pmul(A1[j + 2], t1);
        res[j + 3] += cj0.pmul(A0[j + 3], t0) + cj0.pmul(A1[j + 3], t1) + cj0.pmul(A2[j + 3], t2);

        t4 += cj1.pmul(A0[j + 1], rhs[j + 1]) + cj1.pmul(A0[j + 2], rhs[j + 2]) + cj1.pmul(A0[j + 3], rhs[j + 3]);
        t5 += cj1.pmul(A1[j + 2], rhs[j + 2]) + cj1.pmul(A1[j + 3], rhs[j + 3]);
        t6 += cj1.pmul(A2[j + 3], rhs[j + 3]);
      }

      // Pre-alignment scalar loop
      for (Index i = starti; i < alignedStart; ++i) {
        res[i] += cj0.pmul(A0[i], t0) + cj0.pmul(A1[i], t1) + cj0.pmul(A2[i], t2) + cj0.pmul(A3[i], t3);
        t4 += cj1.pmul(A0[i], rhs[i]);
        t5 += cj1.pmul(A1[i], rhs[i]);
        t6 += cj1.pmul(A2[i], rhs[i]);
        t7 += cj1.pmul(A3[i], rhs[i]);
      }

      // Main vectorized loop: 4 matrix column loads, 1 rhs load, 1 result load/store
      const Scalar* EIGEN_RESTRICT a0It = A0 + alignedStart;
      const Scalar* EIGEN_RESTRICT a1It = A1 + alignedStart;
      const Scalar* EIGEN_RESTRICT a2It = A2 + alignedStart;
      const Scalar* EIGEN_RESTRICT a3It = A3 + alignedStart;
      const Scalar* EIGEN_RESTRICT rhsIt = rhs + alignedStart;
      Scalar* EIGEN_RESTRICT resIt = res + alignedStart;
      for (Index i = alignedStart; i < alignedEnd; i += PacketSize) {
        Packet A0i = ploadu<Packet>(a0It);
        a0It += PacketSize;
        Packet A1i = ploadu<Packet>(a1It);
        a1It += PacketSize;
        Packet A2i = ploadu<Packet>(a2It);
        a2It += PacketSize;
        Packet A3i = ploadu<Packet>(a3It);
        a3It += PacketSize;
        Packet Bi = ploadu<Packet>(rhsIt);
        rhsIt += PacketSize;
        Packet Xi = pload<Packet>(resIt);

        Xi = pcj0.pmadd(A0i, ptmp0, Xi);
        Xi = pcj0.pmadd(A1i, ptmp1, Xi);
        Xi = pcj0.pmadd(A2i, ptmp2, Xi);
        Xi = pcj0.pmadd(A3i, ptmp3, Xi);
        pstore(resIt, Xi);
        resIt += PacketSize;

        ptmp4 = pcj1.pmadd(A0i, Bi, ptmp4);
        ptmp5 = pcj1.pmadd(A1i, Bi, ptmp5);
        ptmp6 = pcj1.pmadd(A2i, Bi, ptmp6);
        ptmp7 = pcj1.pmadd(A3i, Bi, ptmp7);
      }

      // Post-alignment scalar loop
      for (Index i = alignedEnd; i < endi; ++i) {
        res[i] += cj0.pmul(A0[i], t0) + cj0.pmul(A1[i], t1) + cj0.pmul(A2[i], t2) + cj0.pmul(A3[i], t3);
        t4 += cj1.pmul(A0[i], rhs[i]);
        t5 += cj1.pmul(A1[i], rhs[i]);
        t6 += cj1.pmul(A2[i], rhs[i]);
        t7 += cj1.pmul(A3[i], rhs[i]);
      }

      res[j] += alpha * (t4 + predux(ptmp4));
      res[j + 1] += alpha * (t5 + predux(ptmp5));
      res[j + 2] += alpha * (t6 + predux(ptmp6));
      res[j + 3] += alpha * (t7 + predux(ptmp7));
    }
  }

  // === Phase 2: 2 columns at a time ===
  {
    Index jStart = FirstTriangular ? (size - n4 - n2) : n4;
    Index jEnd = FirstTriangular ? (size - n4) : (n4 + n2);

    for (Index j = jStart; j < jEnd; j += 2) {
      const Scalar* EIGEN_RESTRICT A0 = lhs + j * lhsStride;
      const Scalar* EIGEN_RESTRICT A1 = lhs + (j + 1) * lhsStride;

      Scalar t0 = cjAlpha * rhs[j];
      Packet ptmp0 = pset1<Packet>(t0);
      Scalar t1 = cjAlpha * rhs[j + 1];
      Packet ptmp1 = pset1<Packet>(t1);

      Scalar t2(0);
      Packet ptmp2 = pzero(Packet{});
      Scalar t3(0);
      Packet ptmp3 = pzero(Packet{});

      Index starti = FirstTriangular ? 0 : j + 2;
      Index endi = FirstTriangular ? j : size;
      Index alignedStart = starti + internal::first_default_aligned(&res[starti], endi - starti);
      Index alignedEnd = alignedStart + ((endi - alignedStart) / PacketSize) * PacketSize;

      res[j] += cjd.pmul(numext::real(A0[j]), t0);
      res[j + 1] += cjd.pmul(numext::real(A1[j + 1]), t1);
      if (FirstTriangular) {
        res[j] += cj0.pmul(A1[j], t1);
        t3 += cj1.pmul(A1[j], rhs[j]);
      } else {
        res[j + 1] += cj0.pmul(A0[j + 1], t0);
        t2 += cj1.pmul(A0[j + 1], rhs[j + 1]);
      }

      for (Index i = starti; i < alignedStart; ++i) {
        res[i] += cj0.pmul(A0[i], t0) + cj0.pmul(A1[i], t1);
        t2 += cj1.pmul(A0[i], rhs[i]);
        t3 += cj1.pmul(A1[i], rhs[i]);
      }
      const Scalar* EIGEN_RESTRICT a0It = A0 + alignedStart;
      const Scalar* EIGEN_RESTRICT a1It = A1 + alignedStart;
      const Scalar* EIGEN_RESTRICT rhsIt = rhs + alignedStart;
      Scalar* EIGEN_RESTRICT resIt = res + alignedStart;
      for (Index i = alignedStart; i < alignedEnd; i += PacketSize) {
        Packet A0i = ploadu<Packet>(a0It);
        a0It += PacketSize;
        Packet A1i = ploadu<Packet>(a1It);
        a1It += PacketSize;
        Packet Bi = ploadu<Packet>(rhsIt);
        rhsIt += PacketSize;
        Packet Xi = pload<Packet>(resIt);

        Xi = pcj0.pmadd(A0i, ptmp0, pcj0.pmadd(A1i, ptmp1, Xi));
        ptmp2 = pcj1.pmadd(A0i, Bi, ptmp2);
        ptmp3 = pcj1.pmadd(A1i, Bi, ptmp3);
        pstore(resIt, Xi);
        resIt += PacketSize;
      }
      for (Index i = alignedEnd; i < endi; i++) {
        res[i] += cj0.pmul(A0[i], t0) + cj0.pmul(A1[i], t1);
        t2 += cj1.pmul(A0[i], rhs[i]);
        t3 += cj1.pmul(A1[i], rhs[i]);
      }

      res[j] += alpha * (t2 + predux(ptmp2));
      res[j + 1] += alpha * (t3 + predux(ptmp3));
    }
  }

  // === Phase 3: 1 column at a time ===
  {
    Index jStart = FirstTriangular ? 0 : (n4 + n2);
    Index jEnd = FirstTriangular ? (size - n4 - n2) : size;

    for (Index j = jStart; j < jEnd; j++) {
      const Scalar* EIGEN_RESTRICT A0 = lhs + j * lhsStride;

      Scalar t1 = cjAlpha * rhs[j];
      Scalar t2(0);
      Packet ptmp1 = pset1<Packet>(t1);
      Packet ptmp2 = pzero(Packet{});

      res[j] += cjd.pmul(numext::real(A0[j]), t1);

      Index starti = FirstTriangular ? 0 : j + 1;
      Index endi = FirstTriangular ? j : size;
      Index alignedStart = starti + internal::first_default_aligned(&res[starti], endi - starti);
      Index alignedEnd = alignedStart + ((endi - alignedStart) / PacketSize) * PacketSize;

      for (Index i = starti; i < alignedStart; ++i) {
        res[i] += cj0.pmul(A0[i], t1);
        t2 += cj1.pmul(A0[i], rhs[i]);
      }
      const Scalar* EIGEN_RESTRICT a0It = A0 + alignedStart;
      const Scalar* EIGEN_RESTRICT rhsIt = rhs + alignedStart;
      Scalar* EIGEN_RESTRICT resIt = res + alignedStart;
      for (Index i = alignedStart; i < alignedEnd; i += PacketSize) {
        Packet A0i = ploadu<Packet>(a0It);
        a0It += PacketSize;
        Packet Bi = ploadu<Packet>(rhsIt);
        rhsIt += PacketSize;
        Packet Xi = pload<Packet>(resIt);

        Xi = pcj0.pmadd(A0i, ptmp1, Xi);
        pstore(resIt, Xi);
        resIt += PacketSize;

        ptmp2 = pcj1.pmadd(A0i, Bi, ptmp2);
      }
      for (Index i = alignedEnd; i < endi; i++) {
        res[i] += cj0.pmul(A0[i], t1);
        t2 += cj1.pmul(A0[i], rhs[i]);
      }
      res[j] += alpha * (t2 + predux(ptmp2));
    }
  }
}

}  // end namespace internal

/***************************************************************************
 * Wrapper to product_selfadjoint_vector
 ***************************************************************************/

namespace internal {

template <typename Lhs, int LhsMode, typename Rhs>
struct selfadjoint_product_impl<Lhs, LhsMode, false, Rhs, 0, true> {
  typedef typename Product<Lhs, Rhs>::Scalar Scalar;

  typedef internal::blas_traits<Lhs> LhsBlasTraits;
  typedef typename LhsBlasTraits::DirectLinearAccessType ActualLhsType;
  typedef internal::remove_all_t<ActualLhsType> ActualLhsTypeCleaned;

  typedef internal::blas_traits<Rhs> RhsBlasTraits;
  typedef typename RhsBlasTraits::DirectLinearAccessType ActualRhsType;
  typedef internal::remove_all_t<ActualRhsType> ActualRhsTypeCleaned;

  enum { LhsUpLo = LhsMode & (Upper | Lower) };

  // Verify that the Rhs is a vector in the correct orientation.
  // Otherwise, we break the assumption that we are multiplying
  // MxN * Nx1.
  static_assert(Rhs::ColsAtCompileTime == 1, "The RHS must be a column vector.");

  template <typename Dest>
  static EIGEN_DEVICE_FUNC void run(Dest& dest, const Lhs& a_lhs, const Rhs& a_rhs, const Scalar& alpha) {
    typedef typename Dest::Scalar ResScalar;
    typedef typename Rhs::Scalar RhsScalar;
    typedef Map<Matrix<ResScalar, Dynamic, 1>, plain_enum_min(AlignedMax, internal::packet_traits<ResScalar>::size)>
        MappedDest;

    eigen_assert(dest.rows() == a_lhs.rows() && dest.cols() == a_rhs.cols());

    add_const_on_value_type_t<ActualLhsType> lhs = LhsBlasTraits::extract(a_lhs);
    add_const_on_value_type_t<ActualRhsType> rhs = RhsBlasTraits::extract(a_rhs);

    Scalar actualAlpha = alpha * LhsBlasTraits::extractScalarFactor(a_lhs) * RhsBlasTraits::extractScalarFactor(a_rhs);

    enum {
      EvalToDest = (Dest::InnerStrideAtCompileTime == 1),
      UseRhs = (ActualRhsTypeCleaned::InnerStrideAtCompileTime == 1)
    };

    internal::gemv_static_vector_if<ResScalar, Dest::SizeAtCompileTime, Dest::MaxSizeAtCompileTime, !EvalToDest>
        static_dest;
    internal::gemv_static_vector_if<RhsScalar, ActualRhsTypeCleaned::SizeAtCompileTime,
                                    ActualRhsTypeCleaned::MaxSizeAtCompileTime, !UseRhs>
        static_rhs;

    ei_declare_aligned_stack_constructed_variable(ResScalar, actualDestPtr, dest.size(),
                                                  EvalToDest ? dest.data() : static_dest.data());

    ei_declare_aligned_stack_constructed_variable(RhsScalar, actualRhsPtr, rhs.size(),
                                                  UseRhs ? const_cast<RhsScalar*>(rhs.data()) : static_rhs.data());

    if (!EvalToDest) {
#ifdef EIGEN_DENSE_STORAGE_CTOR_PLUGIN
      constexpr int Size = Dest::SizeAtCompileTime;
      Index size = dest.size();
      EIGEN_DENSE_STORAGE_CTOR_PLUGIN
#endif
      MappedDest(actualDestPtr, dest.size()) = dest;
    }

    if (!UseRhs) {
#ifdef EIGEN_DENSE_STORAGE_CTOR_PLUGIN
      constexpr int Size = ActualRhsTypeCleaned::SizeAtCompileTime;
      Index size = rhs.size();
      EIGEN_DENSE_STORAGE_CTOR_PLUGIN
#endif
      Map<typename ActualRhsTypeCleaned::PlainObject>(actualRhsPtr, rhs.size()) = rhs;
    }

    internal::selfadjoint_matrix_vector_product<
        Scalar, Index, (internal::traits<ActualLhsTypeCleaned>::Flags & RowMajorBit) ? RowMajor : ColMajor,
        int(LhsUpLo), bool(LhsBlasTraits::NeedToConjugate),
        bool(RhsBlasTraits::NeedToConjugate)>::run(lhs.rows(),                              // size
                                                   &lhs.coeffRef(0, 0), lhs.outerStride(),  // lhs info
                                                   actualRhsPtr,                            // rhs info
                                                   actualDestPtr,                           // result info
                                                   actualAlpha                              // scale factor
    );

    if (!EvalToDest) dest = MappedDest(actualDestPtr, dest.size());
  }
};

template <typename Lhs, typename Rhs, int RhsMode>
struct selfadjoint_product_impl<Lhs, 0, true, Rhs, RhsMode, false> {
  typedef typename Product<Lhs, Rhs>::Scalar Scalar;
  enum { RhsUpLo = RhsMode & (Upper | Lower) };

  template <typename Dest>
  static void run(Dest& dest, const Lhs& a_lhs, const Rhs& a_rhs, const Scalar& alpha) {
    // let's simply transpose the product
    Transpose<Dest> destT(dest);
    selfadjoint_product_impl<Transpose<const Rhs>, int(RhsUpLo) == Upper ? Lower : Upper, false, Transpose<const Lhs>,
                             0, true>::run(destT, a_rhs.transpose(), a_lhs.transpose(), alpha);
  }
};

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_SELFADJOINT_MATRIX_VECTOR_H
