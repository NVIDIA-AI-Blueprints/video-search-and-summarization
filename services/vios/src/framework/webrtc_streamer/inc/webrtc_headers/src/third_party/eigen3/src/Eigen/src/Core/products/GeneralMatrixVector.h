// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008-2016 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_GENERAL_MATRIX_VECTOR_H
#define EIGEN_GENERAL_MATRIX_VECTOR_H

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

// C4804: unsafe use of type 'bool' in operation. Unavoidable in generic code
// instantiated with bool scalars (e.g. += and * on bool).
#if EIGEN_COMP_MSVC
#pragma warning(push)
#pragma warning(disable : 4804)
#endif

namespace Eigen {

namespace internal {

enum GEMVPacketSizeType { GEMVPacketFull = 0, GEMVPacketHalf, GEMVPacketQuarter };

template <int N, typename T1, typename T2, typename T3>
struct gemv_packet_cond {
  typedef T3 type;
};

template <typename T1, typename T2, typename T3>
struct gemv_packet_cond<GEMVPacketFull, T1, T2, T3> {
  typedef T1 type;
};

template <typename T1, typename T2, typename T3>
struct gemv_packet_cond<GEMVPacketHalf, T1, T2, T3> {
  typedef T2 type;
};

template <typename LhsScalar, typename RhsScalar, int PacketSize_ = GEMVPacketFull>
class gemv_traits {
  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;

#define PACKET_DECL_COND_POSTFIX(postfix, name, packet_size)                                               \
  typedef typename gemv_packet_cond<                                                                       \
      packet_size, typename packet_traits<name##Scalar>::type, typename packet_traits<name##Scalar>::half, \
      typename unpacket_traits<typename packet_traits<name##Scalar>::half>::half>::type name##Packet##postfix

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);
#undef PACKET_DECL_COND_POSTFIX

 public:
  enum {
    Vectorizable = unpacket_traits<LhsPacket_>::vectorizable && unpacket_traits<RhsPacket_>::vectorizable &&
                   int(unpacket_traits<LhsPacket_>::size) == int(unpacket_traits<RhsPacket_>::size),
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsPacket_>::size : 1,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1
  };

  typedef std::conditional_t<Vectorizable, LhsPacket_, LhsScalar> LhsPacket;
  typedef std::conditional_t<Vectorizable, RhsPacket_, RhsScalar> RhsPacket;
  typedef std::conditional_t<Vectorizable, ResPacket_, ResScalar> ResPacket;
};

/* Optimized col-major matrix * vector product:
 * This algorithm processes the matrix per vertical panels,
 * which are then processed horizontally per chunk of 8*PacketSize x 1 vertical segments.
 *
 * Mixing type logic: C += alpha * A * B
 *  |  A  |  B  |alpha| comments
 *  |real |cplx |cplx | no vectorization
 *  |real |cplx |real | alpha is converted to a cplx when calling the run function, no vectorization
 *  |cplx |real |cplx | invalid, the caller has to do tmp: = A * B; C += alpha*tmp
 *  |cplx |real |real | optimal case, vectorization possible via real-cplx mul
 *
 * The same reasoning apply for the transposed case.
 */
template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
struct general_matrix_vector_product<Index, LhsScalar, LhsMapper, ColMajor, ConjugateLhs, RhsScalar, RhsMapper,
                                     ConjugateRhs, Version> {
  typedef gemv_traits<LhsScalar, RhsScalar> Traits;
  typedef gemv_traits<LhsScalar, RhsScalar, GEMVPacketHalf> HalfTraits;
  typedef gemv_traits<LhsScalar, RhsScalar, GEMVPacketQuarter> QuarterTraits;

  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;

  typedef typename Traits::LhsPacket LhsPacket;
  typedef typename Traits::RhsPacket RhsPacket;
  typedef typename Traits::ResPacket ResPacket;

  typedef typename HalfTraits::LhsPacket LhsPacketHalf;
  typedef typename HalfTraits::RhsPacket RhsPacketHalf;
  typedef typename HalfTraits::ResPacket ResPacketHalf;

  typedef typename QuarterTraits::LhsPacket LhsPacketQuarter;
  typedef typename QuarterTraits::RhsPacket RhsPacketQuarter;
  typedef typename QuarterTraits::ResPacket ResPacketQuarter;

  EIGEN_DEVICE_FUNC inline static void run(Index rows, Index cols, const LhsMapper& lhs, const RhsMapper& rhs,
                                           ResScalar* res, Index resIncr, RhsScalar alpha);

  template <int N>
  EIGEN_DEVICE_FUNC static EIGEN_ALWAYS_INLINE void process_rows(
      Index i, Index j2, Index jend, const LhsMapper& lhs, const RhsMapper& rhs, ResScalar* res,
      const ResPacket& palpha, conj_helper<LhsPacket, RhsPacket, ConjugateLhs, ConjugateRhs>& pcj);
};

// Recursive template unroller for col-major GEMV full-packet row blocks.
// Unrolls the packet dimension (K = 0..N-1) at compile time, guaranteeing
// that each accumulator lives in its own register variable.
template <int K, int N>
struct gemv_colmajor_unroller {
  template <typename Packet>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void init_zero(Packet* c) {
    gemv_colmajor_unroller<K - 1, N>::init_zero(c);
    c[K] = pzero(Packet{});
  }

  template <typename LhsPacket, int LhsStride, int Alignment, typename AccPacket, typename RhsPacket,
            typename ConjHelper, typename LhsMapper, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void madd(AccPacket* c, const LhsMapper& lhs, Index i, Index j,
                                                         const RhsPacket& b0, ConjHelper& pcj) {
    gemv_colmajor_unroller<K - 1, N>::template madd<LhsPacket, LhsStride, Alignment>(c, lhs, i, j, b0, pcj);
    c[K] = pcj.pmadd(lhs.template load<LhsPacket, Alignment>(i + LhsStride * K, j), b0, c[K]);
  }

  template <typename ResPacket, int ResStride, typename ResScalar>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void store(const ResPacket* c, ResScalar* res, Index i,
                                                          const ResPacket& palpha) {
    gemv_colmajor_unroller<K - 1, N>::template store<ResPacket, ResStride>(c, res, i, palpha);
    pstoreu(res + i + ResStride * K, pmadd(c[K], palpha, ploadu<ResPacket>(res + i + ResStride * K)));
  }
};

template <int N>
struct gemv_colmajor_unroller<0, N> {
  template <typename Packet>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void init_zero(Packet* c) {
    c[0] = pzero(Packet{});
  }

  template <typename LhsPacket, int LhsStride, int Alignment, typename AccPacket, typename RhsPacket,
            typename ConjHelper, typename LhsMapper, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void madd(AccPacket* c, const LhsMapper& lhs, Index i, Index j,
                                                         const RhsPacket& b0, ConjHelper& pcj) {
    c[0] = pcj.pmadd(lhs.template load<LhsPacket, Alignment>(i, j), b0, c[0]);
  }

  template <typename ResPacket, int ResStride, typename ResScalar>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void store(const ResPacket* c, ResScalar* res, Index i,
                                                          const ResPacket& palpha) {
    pstoreu(res + i, pmadd(c[0], palpha, ploadu<ResPacket>(res + i)));
  }
};

template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void general_matrix_vector_product<
    Index, LhsScalar, LhsMapper, ColMajor, ConjugateLhs, RhsScalar, RhsMapper, ConjugateRhs,
    Version>::process_rows(Index i, Index j2, Index jend, const LhsMapper& lhs, const RhsMapper& rhs, ResScalar* res,
                           const ResPacket& palpha,
                           conj_helper<LhsPacket, RhsPacket, ConjugateLhs, ConjugateRhs>& pcj) {
  enum { LhsAlignment = Unaligned, LhsPacketSize = Traits::LhsPacketSize, ResPacketSize = Traits::ResPacketSize };
  using Unroller = gemv_colmajor_unroller<N - 1, N>;

  ResPacket c[N];
  Unroller::init_zero(c);
  for (Index j = j2; j < jend; ++j) {
    RhsPacket b0 = pset1<RhsPacket>(rhs(j, 0));
    Unroller::template madd<LhsPacket, LhsPacketSize, LhsAlignment>(c, lhs, i, j, b0, pcj);
  }
  Unroller::template store<ResPacket, ResPacketSize>(c, res, i, palpha);
}

template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
EIGEN_DEVICE_FUNC inline void
general_matrix_vector_product<Index, LhsScalar, LhsMapper, ColMajor, ConjugateLhs, RhsScalar, RhsMapper, ConjugateRhs,
                              Version>::run(Index rows, Index cols, const LhsMapper& alhs, const RhsMapper& rhs,
                                            ResScalar* res, Index resIncr, RhsScalar alpha) {
  EIGEN_UNUSED_VARIABLE(resIncr);
  eigen_internal_assert(resIncr == 1);

  // BLAS contract: if alpha == 0, the result is unchanged (and lhs/rhs need not be read).
  if (numext::is_exactly_zero(alpha)) return;

  // The following copy tells the compiler that lhs's attributes are not modified outside this function
  // This helps GCC to generate proper code.
  LhsMapper lhs(alhs);

  conj_helper<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs> cj;
  conj_helper<LhsPacket, RhsPacket, ConjugateLhs, ConjugateRhs> pcj;
  conj_helper<LhsPacketHalf, RhsPacketHalf, ConjugateLhs, ConjugateRhs> pcj_half;
  conj_helper<LhsPacketQuarter, RhsPacketQuarter, ConjugateLhs, ConjugateRhs> pcj_quarter;

  const Index lhsStride = lhs.stride();
  // LhsAlignment stays Unaligned; enabling aligned reads would require
  // propagating the Mapper's Alignment through the run() template, and on
  // modern x86 aligned/unaligned packet loads are equivalent anyway.
  enum {
    LhsAlignment = Unaligned,
    ResPacketSize = Traits::ResPacketSize,
    ResPacketSizeHalf = HalfTraits::ResPacketSize,
    ResPacketSizeQuarter = QuarterTraits::ResPacketSize,
    LhsPacketSize = Traits::LhsPacketSize,
    HasHalf = (int)ResPacketSizeHalf < (int)ResPacketSize,
    HasQuarter = (int)ResPacketSizeQuarter < (int)ResPacketSizeHalf
  };

  const Index n8 = rows - 8 * ResPacketSize + 1;
  const Index n4 = rows - 4 * ResPacketSize + 1;
  const Index n3 = rows - 3 * ResPacketSize + 1;
  const Index n2 = rows - 2 * ResPacketSize + 1;
  const Index n1 = rows - 1 * ResPacketSize + 1;
  const Index n_half = rows - 1 * ResPacketSizeHalf + 1;
  const Index n_quarter = rows - 1 * ResPacketSizeQuarter + 1;

  // Choose block_cols so that one column slice of the LHS roughly fits in L1.
  // When it does not, fall back to a smaller batch to keep cache pressure down.
  std::ptrdiff_t l1, l2, l3;
  manage_caching_sizes(GetAction, &l1, &l2, &l3);
  const Index block_cols =
      cols < 128 ? cols : (lhsStride * Index(sizeof(LhsScalar)) < Index(l1) ? Index(16) : Index(4));
  ResPacket palpha = pset1<ResPacket>(alpha);
  ResPacketHalf palpha_half = pset1<ResPacketHalf>(alpha);
  ResPacketQuarter palpha_quarter = pset1<ResPacketQuarter>(alpha);

  for (Index j2 = 0; j2 < cols; j2 += block_cols) {
    Index jend = numext::mini(j2 + block_cols, cols);
    Index i = 0;
    for (; i < n8; i += ResPacketSize * 8) process_rows<8>(i, j2, jend, lhs, rhs, res, palpha, pcj);
    if (i < n4) {
      process_rows<4>(i, j2, jend, lhs, rhs, res, palpha, pcj);
      i += ResPacketSize * 4;
    }
    if (i < n3) {
      process_rows<3>(i, j2, jend, lhs, rhs, res, palpha, pcj);
      i += ResPacketSize * 3;
    }
    if (i < n2) {
      process_rows<2>(i, j2, jend, lhs, rhs, res, palpha, pcj);
      i += ResPacketSize * 2;
    }
    if (i < n1) {
      process_rows<1>(i, j2, jend, lhs, rhs, res, palpha, pcj);
      i += ResPacketSize;
    }
    if (HasHalf && i < n_half) {
      ResPacketHalf c0 = pzero(ResPacketHalf{});
      for (Index j = j2; j < jend; j += 1) {
        RhsPacketHalf b0 = pset1<RhsPacketHalf>(rhs(j, 0));
        c0 = pcj_half.pmadd(lhs.template load<LhsPacketHalf, LhsAlignment>(i + 0, j), b0, c0);
      }
      pstoreu(res + i + ResPacketSizeHalf * 0,
              pmadd(c0, palpha_half, ploadu<ResPacketHalf>(res + i + ResPacketSizeHalf * 0)));
      i += ResPacketSizeHalf;
    }
    if (HasQuarter && i < n_quarter) {
      ResPacketQuarter c0 = pzero(ResPacketQuarter{});
      for (Index j = j2; j < jend; j += 1) {
        RhsPacketQuarter b0 = pset1<RhsPacketQuarter>(rhs(j, 0));
        c0 = pcj_quarter.pmadd(lhs.template load<LhsPacketQuarter, LhsAlignment>(i + 0, j), b0, c0);
      }
      pstoreu(res + i + ResPacketSizeQuarter * 0,
              pmadd(c0, palpha_quarter, ploadu<ResPacketQuarter>(res + i + ResPacketSizeQuarter * 0)));
      i += ResPacketSizeQuarter;
    }
    for (; i < rows; ++i) {
      ResScalar c0(0);
      for (Index j = j2; j < jend; j += 1) c0 += cj.pmul(lhs(i, j), rhs(j, 0));
      res[i] += alpha * c0;
    }
  }
}

/* Optimized row-major matrix * vector product:
 * This algorithm processes 4 rows at once that allows to both reduce
 * the number of load/stores of the result by a factor 4 and to reduce
 * the instruction dependency. Moreover, we know that all bands have the
 * same alignment pattern.
 *
 * Mixing type logic:
 *  - alpha is always a complex (or converted to a complex)
 *  - no vectorization
 */
template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
struct general_matrix_vector_product<Index, LhsScalar, LhsMapper, RowMajor, ConjugateLhs, RhsScalar, RhsMapper,
                                     ConjugateRhs, Version> {
  typedef gemv_traits<LhsScalar, RhsScalar> Traits;
  typedef gemv_traits<LhsScalar, RhsScalar, GEMVPacketHalf> HalfTraits;
  typedef gemv_traits<LhsScalar, RhsScalar, GEMVPacketQuarter> QuarterTraits;

  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;

  typedef typename Traits::LhsPacket LhsPacket;
  typedef typename Traits::RhsPacket RhsPacket;
  typedef typename Traits::ResPacket ResPacket;

  typedef typename HalfTraits::LhsPacket LhsPacketHalf;
  typedef typename HalfTraits::RhsPacket RhsPacketHalf;
  typedef typename HalfTraits::ResPacket ResPacketHalf;

  typedef typename QuarterTraits::LhsPacket LhsPacketQuarter;
  typedef typename QuarterTraits::RhsPacket RhsPacketQuarter;
  typedef typename QuarterTraits::ResPacket ResPacketQuarter;

  EIGEN_DEVICE_FUNC static inline void run(Index rows, Index cols, const LhsMapper& lhs, const RhsMapper& rhs,
                                           ResScalar* res, Index resIncr, ResScalar alpha);

  // Specialized path for when cols < full packet size. Kept noinline to avoid
  // bloating the main run() function and causing icache pressure.
  EIGEN_DEVICE_FUNC EIGEN_DONT_INLINE static void run_small_cols(Index rows, Index cols, const LhsMapper& lhs,
                                                                 const RhsMapper& rhs, ResScalar* res, Index resIncr,
                                                                 ResScalar alpha);

  // Templated helper that processes N rows in run_small_cols. N is a compile-time
  // constant; row-dimension unrolling is done via recursive templates to guarantee
  // full unrolling regardless of compiler heuristics.
  template <int N>
  EIGEN_DEVICE_FUNC static EIGEN_ALWAYS_INLINE void process_rows_small_cols(Index i, Index cols, const LhsMapper& lhs,
                                                                            const RhsMapper& rhs, ResScalar* res,
                                                                            Index resIncr, ResScalar alpha,
                                                                            Index halfColBlockEnd,
                                                                            Index quarterColBlockEnd);
};

template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
EIGEN_DEVICE_FUNC inline void
general_matrix_vector_product<Index, LhsScalar, LhsMapper, RowMajor, ConjugateLhs, RhsScalar, RhsMapper, ConjugateRhs,
                              Version>::run(Index rows, Index cols, const LhsMapper& alhs, const RhsMapper& rhs,
                                            ResScalar* res, Index resIncr, ResScalar alpha) {
  // BLAS contract: if alpha == 0, the result is unchanged (and lhs/rhs need not be read).
  if (numext::is_exactly_zero(alpha)) return;

  // When cols < full packet size, the main vectorized loops are empty.
  // Dispatch to a separate noinline function to avoid polluting the icache.
  // Only dispatch when cols is large enough that half or quarter packets can be used;
  // otherwise the helper would just do scalar work with extra function call overhead.
  enum {
    LhsPacketSize_ = Traits::LhsPacketSize,
    MinUsefulCols_ =
        ((int)QuarterTraits::LhsPacketSize < (int)HalfTraits::LhsPacketSize)
            ? (int)QuarterTraits::LhsPacketSize
            : (((int)HalfTraits::LhsPacketSize < (int)Traits::LhsPacketSize) ? (int)HalfTraits::LhsPacketSize
                                                                             : (int)Traits::LhsPacketSize),
    HasSubPackets_ = (int)MinUsefulCols_ < (int)LhsPacketSize_
  };
  if (HasSubPackets_ && cols >= MinUsefulCols_ && cols < LhsPacketSize_) {
    run_small_cols(rows, cols, alhs, rhs, res, resIncr, alpha);
    return;
  }

  // The following copy tells the compiler that lhs's attributes are not modified outside this function
  // This helps GCC to generate proper code.
  LhsMapper lhs(alhs);

  eigen_internal_assert(rhs.stride() == 1);
  conj_helper<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs> cj;
  conj_helper<LhsPacket, RhsPacket, ConjugateLhs, ConjugateRhs> pcj;
  conj_helper<LhsPacketHalf, RhsPacketHalf, ConjugateLhs, ConjugateRhs> pcj_half;
  conj_helper<LhsPacketQuarter, RhsPacketQuarter, ConjugateLhs, ConjugateRhs> pcj_quarter;

  // Disable the 8-row inner unroll once a single column slice no longer fits in L1; with very
  // large LHS strides each unrolled iteration evicts the previously-loaded rows from cache.
  std::ptrdiff_t l1, l2, l3;
  manage_caching_sizes(GetAction, &l1, &l2, &l3);
  const Index n8 = lhs.stride() * Index(sizeof(LhsScalar)) > Index(l1) ? 0 : rows - 7;
  const Index n4 = rows - 3;
  const Index n2 = rows - 1;

  // LhsAlignment stays Unaligned; enabling aligned reads would require
  // propagating the Mapper's Alignment through the run() template, and on
  // modern x86 aligned/unaligned packet loads are equivalent anyway.
  enum {
    LhsAlignment = Unaligned,
    ResPacketSize = Traits::ResPacketSize,
    ResPacketSizeHalf = HalfTraits::ResPacketSize,
    ResPacketSizeQuarter = QuarterTraits::ResPacketSize,
    LhsPacketSize = Traits::LhsPacketSize,
    LhsPacketSizeHalf = HalfTraits::LhsPacketSize,
    LhsPacketSizeQuarter = QuarterTraits::LhsPacketSize,
    HasHalf = (int)ResPacketSizeHalf < (int)ResPacketSize,
    HasQuarter = (int)ResPacketSizeQuarter < (int)ResPacketSizeHalf
  };

  using UnsignedIndex = std::make_unsigned_t<Index>;
  const Index fullColBlockEnd = LhsPacketSize * (UnsignedIndex(cols) / LhsPacketSize);
  const Index halfColBlockEnd = LhsPacketSizeHalf * (UnsignedIndex(cols) / LhsPacketSizeHalf);
  const Index quarterColBlockEnd = LhsPacketSizeQuarter * (UnsignedIndex(cols) / LhsPacketSizeQuarter);

  Index i = 0;
  for (; i < n8; i += 8) {
    ResPacket c0 = pzero(ResPacket{}), c1 = pzero(ResPacket{}), c2 = pzero(ResPacket{}), c3 = pzero(ResPacket{}),
              c4 = pzero(ResPacket{}), c5 = pzero(ResPacket{}), c6 = pzero(ResPacket{}), c7 = pzero(ResPacket{});

    for (Index j = 0; j < fullColBlockEnd; j += LhsPacketSize) {
      RhsPacket b0 = rhs.template load<RhsPacket, Unaligned>(j, 0);

      c0 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 0, j), b0, c0);
      c1 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 1, j), b0, c1);
      c2 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 2, j), b0, c2);
      c3 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 3, j), b0, c3);
      c4 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 4, j), b0, c4);
      c5 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 5, j), b0, c5);
      c6 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 6, j), b0, c6);
      c7 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 7, j), b0, c7);
    }
    ResScalar cc0 = predux(c0);
    ResScalar cc1 = predux(c1);
    ResScalar cc2 = predux(c2);
    ResScalar cc3 = predux(c3);
    ResScalar cc4 = predux(c4);
    ResScalar cc5 = predux(c5);
    ResScalar cc6 = predux(c6);
    ResScalar cc7 = predux(c7);

    for (Index j = fullColBlockEnd; j < cols; ++j) {
      RhsScalar b0 = rhs(j, 0);

      cc0 += cj.pmul(lhs(i + 0, j), b0);
      cc1 += cj.pmul(lhs(i + 1, j), b0);
      cc2 += cj.pmul(lhs(i + 2, j), b0);
      cc3 += cj.pmul(lhs(i + 3, j), b0);
      cc4 += cj.pmul(lhs(i + 4, j), b0);
      cc5 += cj.pmul(lhs(i + 5, j), b0);
      cc6 += cj.pmul(lhs(i + 6, j), b0);
      cc7 += cj.pmul(lhs(i + 7, j), b0);
    }
    res[(i + 0) * resIncr] += alpha * cc0;
    res[(i + 1) * resIncr] += alpha * cc1;
    res[(i + 2) * resIncr] += alpha * cc2;
    res[(i + 3) * resIncr] += alpha * cc3;
    res[(i + 4) * resIncr] += alpha * cc4;
    res[(i + 5) * resIncr] += alpha * cc5;
    res[(i + 6) * resIncr] += alpha * cc6;
    res[(i + 7) * resIncr] += alpha * cc7;
  }
  for (; i < n4; i += 4) {
    ResPacket c0 = pzero(ResPacket{}), c1 = pzero(ResPacket{}), c2 = pzero(ResPacket{}), c3 = pzero(ResPacket{});

    for (Index j = 0; j < fullColBlockEnd; j += LhsPacketSize) {
      RhsPacket b0 = rhs.template load<RhsPacket, Unaligned>(j, 0);

      c0 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 0, j), b0, c0);
      c1 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 1, j), b0, c1);
      c2 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 2, j), b0, c2);
      c3 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 3, j), b0, c3);
    }
    ResScalar cc0 = predux(c0);
    ResScalar cc1 = predux(c1);
    ResScalar cc2 = predux(c2);
    ResScalar cc3 = predux(c3);

    for (Index j = fullColBlockEnd; j < cols; ++j) {
      RhsScalar b0 = rhs(j, 0);

      cc0 += cj.pmul(lhs(i + 0, j), b0);
      cc1 += cj.pmul(lhs(i + 1, j), b0);
      cc2 += cj.pmul(lhs(i + 2, j), b0);
      cc3 += cj.pmul(lhs(i + 3, j), b0);
    }
    res[(i + 0) * resIncr] += alpha * cc0;
    res[(i + 1) * resIncr] += alpha * cc1;
    res[(i + 2) * resIncr] += alpha * cc2;
    res[(i + 3) * resIncr] += alpha * cc3;
  }
  for (; i < n2; i += 2) {
    ResPacket c0 = pzero(ResPacket{}), c1 = pzero(ResPacket{});

    for (Index j = 0; j < fullColBlockEnd; j += LhsPacketSize) {
      RhsPacket b0 = rhs.template load<RhsPacket, Unaligned>(j, 0);

      c0 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 0, j), b0, c0);
      c1 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i + 1, j), b0, c1);
    }
    ResScalar cc0 = predux(c0);
    ResScalar cc1 = predux(c1);

    for (Index j = fullColBlockEnd; j < cols; ++j) {
      RhsScalar b0 = rhs(j, 0);

      cc0 += cj.pmul(lhs(i + 0, j), b0);
      cc1 += cj.pmul(lhs(i + 1, j), b0);
    }
    res[(i + 0) * resIncr] += alpha * cc0;
    res[(i + 1) * resIncr] += alpha * cc1;
  }
  for (; i < rows; ++i) {
    ResPacket c0 = pzero(ResPacket{});
    ResPacketHalf c0_h = pzero(ResPacketHalf{});
    ResPacketQuarter c0_q = pzero(ResPacketQuarter{});

    for (Index j = 0; j < fullColBlockEnd; j += LhsPacketSize) {
      RhsPacket b0 = rhs.template load<RhsPacket, Unaligned>(j, 0);
      c0 = pcj.pmadd(lhs.template load<LhsPacket, LhsAlignment>(i, j), b0, c0);
    }
    ResScalar cc0 = predux(c0);
    if (HasHalf) {
      for (Index j = fullColBlockEnd; j < halfColBlockEnd; j += LhsPacketSizeHalf) {
        RhsPacketHalf b0 = rhs.template load<RhsPacketHalf, Unaligned>(j, 0);
        c0_h = pcj_half.pmadd(lhs.template load<LhsPacketHalf, LhsAlignment>(i, j), b0, c0_h);
      }
      cc0 += predux(c0_h);
    }
    if (HasQuarter) {
      for (Index j = halfColBlockEnd; j < quarterColBlockEnd; j += LhsPacketSizeQuarter) {
        RhsPacketQuarter b0 = rhs.template load<RhsPacketQuarter, Unaligned>(j, 0);
        c0_q = pcj_quarter.pmadd(lhs.template load<LhsPacketQuarter, LhsAlignment>(i, j), b0, c0_q);
      }
      cc0 += predux(c0_q);
    }
    for (Index j = quarterColBlockEnd; j < cols; ++j) {
      cc0 += cj.pmul(lhs(i, j), rhs(j, 0));
    }
    res[i * resIncr] += alpha * cc0;
  }
}

// Recursive template unroller for process_rows_small_cols.
// Unrolls the row dimension (K = 0..N-1) at compile time, guaranteeing
// that each accumulator lives in its own register variable regardless
// of compiler unrolling heuristics.
template <int K, int N>
struct gemv_small_cols_unroller {
  template <typename LhsPacket, typename AccPacket, int Alignment, typename RhsType, typename ConjHelper,
            typename LhsMapper, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void madd(AccPacket* acc, const LhsMapper& lhs, Index i, Index j,
                                                         const RhsType& b0, ConjHelper& pcj) {
    gemv_small_cols_unroller<K - 1, N>::template madd<LhsPacket, AccPacket, Alignment>(acc, lhs, i, j, b0, pcj);
    acc[K] = pcj.pmadd(lhs.template load<LhsPacket, Alignment>(i + K, j), b0, acc[K]);
  }

  template <typename ResScalar, typename RhsScalar, typename ConjHelper, typename LhsMapper, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void scalar_madd(ResScalar* cc, const LhsMapper& lhs, Index i, Index j,
                                                                const RhsScalar& b0, ConjHelper& cj) {
    gemv_small_cols_unroller<K - 1, N>::scalar_madd(cc, lhs, i, j, b0, cj);
    cc[K] += cj.pmul(lhs(i + K, j), b0);
  }

  template <typename Scalar, typename Packet>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void predux_accum(Scalar* cc, const Packet* acc) {
    gemv_small_cols_unroller<K - 1, N>::predux_accum(cc, acc);
    cc[K] += predux(acc[K]);
  }

  template <typename Packet>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void init_zero(Packet* acc) {
    gemv_small_cols_unroller<K - 1, N>::init_zero(acc);
    acc[K] = pzero(Packet{});
  }

  template <typename Scalar, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void write_result(Scalar* res, Index resIncr, Index i, Scalar alpha,
                                                                 const Scalar* cc) {
    gemv_small_cols_unroller<K - 1, N>::write_result(res, resIncr, i, alpha, cc);
    res[(i + K) * resIncr] += alpha * cc[K];
  }
};

template <int N>
struct gemv_small_cols_unroller<0, N> {
  template <typename LhsPacket, typename AccPacket, int Alignment, typename RhsType, typename ConjHelper,
            typename LhsMapper, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void madd(AccPacket* acc, const LhsMapper& lhs, Index i, Index j,
                                                         const RhsType& b0, ConjHelper& pcj) {
    acc[0] = pcj.pmadd(lhs.template load<LhsPacket, Alignment>(i, j), b0, acc[0]);
  }

  template <typename ResScalar, typename RhsScalar, typename ConjHelper, typename LhsMapper, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void scalar_madd(ResScalar* cc, const LhsMapper& lhs, Index i, Index j,
                                                                const RhsScalar& b0, ConjHelper& cj) {
    cc[0] += cj.pmul(lhs(i, j), b0);
  }

  template <typename Scalar, typename Packet>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void predux_accum(Scalar* cc, const Packet* acc) {
    cc[0] += predux(acc[0]);
  }

  template <typename Packet>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void init_zero(Packet* acc) {
    acc[0] = pzero(Packet{});
  }

  template <typename Scalar, typename Index>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE void write_result(Scalar* res, Index resIncr, Index i, Scalar alpha,
                                                                 const Scalar* cc) {
    res[i * resIncr] += alpha * cc[0];
  }
};

template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void
general_matrix_vector_product<Index, LhsScalar, LhsMapper, RowMajor, ConjugateLhs, RhsScalar, RhsMapper, ConjugateRhs,
                              Version>::process_rows_small_cols(Index i, Index cols, const LhsMapper& lhs,
                                                                const RhsMapper& rhs, ResScalar* res, Index resIncr,
                                                                ResScalar alpha, Index halfColBlockEnd,
                                                                Index quarterColBlockEnd) {
  conj_helper<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs> cj;
  conj_helper<LhsPacketHalf, RhsPacketHalf, ConjugateLhs, ConjugateRhs> pcj_half;
  conj_helper<LhsPacketQuarter, RhsPacketQuarter, ConjugateLhs, ConjugateRhs> pcj_quarter;

  enum {
    LhsAlignment = Unaligned,
    ResPacketSizeHalf = HalfTraits::ResPacketSize,
    ResPacketSizeQuarter = QuarterTraits::ResPacketSize,
    LhsPacketSizeHalf = HalfTraits::LhsPacketSize,
    LhsPacketSizeQuarter = QuarterTraits::LhsPacketSize,
    HasHalf = (int)ResPacketSizeHalf < (int)Traits::ResPacketSize,
    HasQuarter = (int)ResPacketSizeQuarter < (int)ResPacketSizeHalf
  };

  using Unroll = gemv_small_cols_unroller<N - 1, N>;

  ResScalar cc[N] = {};
  if (HasHalf) {
    ResPacketHalf h[N];
    Unroll::init_zero(h);
    for (Index j = 0; j < halfColBlockEnd; j += LhsPacketSizeHalf) {
      RhsPacketHalf b0 = rhs.template load<RhsPacketHalf, Unaligned>(j, 0);
      Unroll::template madd<LhsPacketHalf, ResPacketHalf, LhsAlignment>(h, lhs, i, j, b0, pcj_half);
    }
    Unroll::predux_accum(cc, h);
  }
  if (HasQuarter) {
    ResPacketQuarter q[N];
    Unroll::init_zero(q);
    for (Index j = halfColBlockEnd; j < quarterColBlockEnd; j += LhsPacketSizeQuarter) {
      RhsPacketQuarter b0 = rhs.template load<RhsPacketQuarter, Unaligned>(j, 0);
      Unroll::template madd<LhsPacketQuarter, ResPacketQuarter, LhsAlignment>(q, lhs, i, j, b0, pcj_quarter);
    }
    Unroll::predux_accum(cc, q);
  }
  for (Index j = quarterColBlockEnd; j < cols; ++j) {
    RhsScalar b0 = rhs(j, 0);
    Unroll::scalar_madd(cc, lhs, i, j, b0, cj);
  }
  Unroll::write_result(res, resIncr, i, alpha, cc);
}

template <typename Index, typename LhsScalar, typename LhsMapper, bool ConjugateLhs, typename RhsScalar,
          typename RhsMapper, bool ConjugateRhs, int Version>
EIGEN_DEVICE_FUNC EIGEN_DONT_INLINE void
general_matrix_vector_product<Index, LhsScalar, LhsMapper, RowMajor, ConjugateLhs, RhsScalar, RhsMapper, ConjugateRhs,
                              Version>::run_small_cols(Index rows, Index cols, const LhsMapper& alhs,
                                                       const RhsMapper& rhs, ResScalar* res, Index resIncr,
                                                       ResScalar alpha) {
  LhsMapper lhs(alhs);
  eigen_internal_assert(rhs.stride() == 1);

  enum {
    LhsPacketSizeHalf = HalfTraits::LhsPacketSize,
    LhsPacketSizeQuarter = QuarterTraits::LhsPacketSize,
  };

  using UnsignedIndex = std::make_unsigned_t<Index>;
  const Index halfColBlockEnd = LhsPacketSizeHalf * (UnsignedIndex(cols) / LhsPacketSizeHalf);
  const Index quarterColBlockEnd = LhsPacketSizeQuarter * (UnsignedIndex(cols) / LhsPacketSizeQuarter);

  // Disable the 8-row inner unroll once a single column slice no longer fits in L1; with very
  // large LHS strides each unrolled iteration evicts the previously-loaded rows from cache.
  std::ptrdiff_t l1, l2, l3;
  manage_caching_sizes(GetAction, &l1, &l2, &l3);
  const Index n8 = lhs.stride() * Index(sizeof(LhsScalar)) > Index(l1) ? 0 : rows - 7;
  const Index n4 = rows - 3;
  const Index n2 = rows - 1;

  Index i = 0;
  for (; i < n8; i += 8) {
    process_rows_small_cols<8>(i, cols, lhs, rhs, res, resIncr, alpha, halfColBlockEnd, quarterColBlockEnd);
  }
  // Process remaining groups of 4 rows in case n8 was 0.
  for (; i < n4; i += 4) {
    process_rows_small_cols<4>(i, cols, lhs, rhs, res, resIncr, alpha, halfColBlockEnd, quarterColBlockEnd);
  }
  if (i < n2) {
    process_rows_small_cols<2>(i, cols, lhs, rhs, res, resIncr, alpha, halfColBlockEnd, quarterColBlockEnd);
    i += 2;
  }
  if (i < rows) {
    process_rows_small_cols<1>(i, cols, lhs, rhs, res, resIncr, alpha, halfColBlockEnd, quarterColBlockEnd);
  }
}

}  // end namespace internal

}  // end namespace Eigen

#if EIGEN_COMP_MSVC
#pragma warning(pop)
#endif

#endif  // EIGEN_GENERAL_MATRIX_VECTOR_H
