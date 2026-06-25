// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008-2009 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_GENERAL_BLOCK_PANEL_H
#define EIGEN_GENERAL_BLOCK_PANEL_H

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

enum GEBPPacketSizeType { GEBPPacketFull = 0, GEBPPacketHalf, GEBPPacketQuarter };

template <typename LhsScalar_, typename RhsScalar_, bool ConjLhs_ = false, bool ConjRhs_ = false,
          int Arch = Architecture::Target, int PacketSize_ = GEBPPacketFull>
class gebp_traits;

/** \internal \returns b if a<=0, and returns a otherwise. */
inline std::ptrdiff_t manage_caching_sizes_helper(std::ptrdiff_t a, std::ptrdiff_t b) { return a <= 0 ? b : a; }

#if defined(EIGEN_DEFAULT_L1_CACHE_SIZE)
#define EIGEN_SET_DEFAULT_L1_CACHE_SIZE(val) EIGEN_DEFAULT_L1_CACHE_SIZE
#else
#define EIGEN_SET_DEFAULT_L1_CACHE_SIZE(val) val
#endif  // defined(EIGEN_DEFAULT_L1_CACHE_SIZE)

#if defined(EIGEN_DEFAULT_L2_CACHE_SIZE)
#define EIGEN_SET_DEFAULT_L2_CACHE_SIZE(val) EIGEN_DEFAULT_L2_CACHE_SIZE
#else
#define EIGEN_SET_DEFAULT_L2_CACHE_SIZE(val) val
#endif  // defined(EIGEN_DEFAULT_L2_CACHE_SIZE)

#if defined(EIGEN_DEFAULT_L3_CACHE_SIZE)
#define EIGEN_SET_DEFAULT_L3_CACHE_SIZE(val) EIGEN_DEFAULT_L3_CACHE_SIZE
#else
#define EIGEN_SET_DEFAULT_L3_CACHE_SIZE(val) val
#endif  // defined(EIGEN_DEFAULT_L3_CACHE_SIZE)

#if EIGEN_ARCH_i386_OR_x86_64
const std::ptrdiff_t defaultL1CacheSize = EIGEN_SET_DEFAULT_L1_CACHE_SIZE(32 * 1024);
const std::ptrdiff_t defaultL2CacheSize = EIGEN_SET_DEFAULT_L2_CACHE_SIZE(256 * 1024);
const std::ptrdiff_t defaultL3CacheSize = EIGEN_SET_DEFAULT_L3_CACHE_SIZE(2 * 1024 * 1024);
#elif EIGEN_ARCH_PPC
const std::ptrdiff_t defaultL1CacheSize = EIGEN_SET_DEFAULT_L1_CACHE_SIZE(64 * 1024);
#ifdef _ARCH_PWR10
const std::ptrdiff_t defaultL2CacheSize = EIGEN_SET_DEFAULT_L2_CACHE_SIZE(2 * 1024 * 1024);
const std::ptrdiff_t defaultL3CacheSize = EIGEN_SET_DEFAULT_L3_CACHE_SIZE(8 * 1024 * 1024);
#else
const std::ptrdiff_t defaultL2CacheSize = EIGEN_SET_DEFAULT_L2_CACHE_SIZE(512 * 1024);
const std::ptrdiff_t defaultL3CacheSize = EIGEN_SET_DEFAULT_L3_CACHE_SIZE(4 * 1024 * 1024);
#endif
#elif EIGEN_ARCH_ARM_OR_ARM64
const std::ptrdiff_t defaultL1CacheSize = EIGEN_SET_DEFAULT_L1_CACHE_SIZE(64 * 1024);
const std::ptrdiff_t defaultL2CacheSize = EIGEN_SET_DEFAULT_L2_CACHE_SIZE(1024 * 1024);
const std::ptrdiff_t defaultL3CacheSize = EIGEN_SET_DEFAULT_L3_CACHE_SIZE(4 * 1024 * 1024);
#else
const std::ptrdiff_t defaultL1CacheSize = EIGEN_SET_DEFAULT_L1_CACHE_SIZE(16 * 1024);
const std::ptrdiff_t defaultL2CacheSize = EIGEN_SET_DEFAULT_L2_CACHE_SIZE(512 * 1024);
const std::ptrdiff_t defaultL3CacheSize = EIGEN_SET_DEFAULT_L3_CACHE_SIZE(512 * 1024);
#endif

#undef EIGEN_SET_DEFAULT_L1_CACHE_SIZE
#undef EIGEN_SET_DEFAULT_L2_CACHE_SIZE
#undef EIGEN_SET_DEFAULT_L3_CACHE_SIZE

/** \internal */
struct CacheSizes {
  CacheSizes() : m_l1(-1), m_l2(-1), m_l3(-1) {
    std::ptrdiff_t l1CacheSize, l2CacheSize, l3CacheSize;
    queryCacheSizes(l1CacheSize, l2CacheSize, l3CacheSize);
    m_l1 = manage_caching_sizes_helper(l1CacheSize, defaultL1CacheSize);
    m_l2 = manage_caching_sizes_helper(l2CacheSize, defaultL2CacheSize);
    m_l3 = manage_caching_sizes_helper(l3CacheSize, defaultL3CacheSize);
  }

  std::ptrdiff_t m_l1;
  std::ptrdiff_t m_l2;
  std::ptrdiff_t m_l3;
};

/** \internal */
inline void manage_caching_sizes(Action action, std::ptrdiff_t* l1, std::ptrdiff_t* l2, std::ptrdiff_t* l3) {
  static CacheSizes m_cacheSizes;

  if (action == SetAction) {
    // set the cpu cache size and cache all block sizes from a global cache size in byte
    eigen_internal_assert(l1 != 0 && l2 != 0);
    m_cacheSizes.m_l1 = *l1;
    m_cacheSizes.m_l2 = *l2;
    m_cacheSizes.m_l3 = *l3;
  } else if (action == GetAction) {
    eigen_internal_assert(l1 != 0 && l2 != 0);
    *l1 = m_cacheSizes.m_l1;
    *l2 = m_cacheSizes.m_l2;
    *l3 = m_cacheSizes.m_l3;
  } else {
    eigen_internal_assert(false);
  }
}

/* Helper for computeProductBlockingSizes.
 *
 * Given a m x k times k x n matrix product of scalar types \c LhsScalar and \c RhsScalar,
 * this function computes the blocking size parameters along the respective dimensions
 * for matrix products and related algorithms. The blocking sizes depends on various
 * parameters:
 * - the L1 and L2 cache sizes,
 * - the register level blocking sizes defined by gebp_traits,
 * - the number of scalars that fit into a packet (when vectorization is enabled).
 *
 * \sa setCpuCacheSizes */

template <typename LhsScalar, typename RhsScalar, int KcFactor, typename Index>
void evaluateProductBlockingSizesHeuristic(Index& k, Index& m, Index& n, Index num_threads = 1) {
  typedef gebp_traits<LhsScalar, RhsScalar> Traits;

  // Explanations:
  // Let's recall that the product algorithms form mc x kc vertical panels A' on the lhs and
  // kc x nc blocks B' on the rhs. B' has to fit into L2/L3 cache. Moreover, A' is processed
  // per mr x kc horizontal small panels where mr is the blocking size along the m dimension
  // at the register level. This small horizontal panel has to stay within L1 cache.
  std::ptrdiff_t l1, l2, l3;
  manage_caching_sizes(GetAction, &l1, &l2, &l3);
#ifdef EIGEN_VECTORIZE_AVX512
  const std::ptrdiff_t phys_l1 = l1;
  // We need to find a rationale for that, but without this adjustment,
  // performance with AVX512 is pretty bad, like -20% slower.
  // One reason is that with increasing packet-size, the blocking size k
  // has to become pretty small if we want that 1 lhs panel fit within L1.
  // For instance, with the 3pX4 kernel and double, the size of the lhs+rhs panels are:
  //   k*(3*64 + 4*8) Bytes, with l1=32kBytes, and k%8=0, we have k=144.
  // This is quite small for a good reuse of the accumulation registers.
  l1 *= 4;
#endif

  if (num_threads > 1) {
    typedef typename Traits::ResScalar ResScalar;
    enum {
      kdiv = KcFactor * (Traits::mr * sizeof(LhsScalar) + Traits::nr * sizeof(RhsScalar)),
      ksub = Traits::mr * (Traits::nr * sizeof(ResScalar)),
      kr = 8,
      mr = Traits::mr,
      nr = Traits::nr
    };
    // Increasing k gives us more time to prefetch the content of the "C"
    // registers. However once the latency is hidden there is no point in
    // increasing the value of k, so we'll cap it at 320 (value determined
    // experimentally).
    // To avoid that k vanishes, we make k_cache at least as big as kr
    const Index k_cache = numext::maxi<Index>(kr, (numext::mini<Index>)(static_cast<Index>((l1 - ksub) / kdiv), 320));
    if (k_cache < k) {
      k = k_cache - (k_cache % kr);
      eigen_internal_assert(k > 0);
    }

    const Index n_cache = static_cast<Index>((l2 - l1) / (nr * sizeof(RhsScalar) * k));
    const Index n_per_thread = numext::div_ceil(n, num_threads);
    if (n_cache <= n_per_thread) {
      // Don't exceed the capacity of the l2 cache.
      eigen_internal_assert(n_cache >= static_cast<Index>(nr));
      n = n_cache - (n_cache % nr);
      eigen_internal_assert(n > 0);
    } else {
      n = (numext::mini<Index>)(n, (n_per_thread + nr - 1) - ((n_per_thread + nr - 1) % nr));
    }

    if (l3 > l2) {
      // l3 is shared between all cores, so we'll give each thread its own chunk of l3.
      const Index m_cache = static_cast<Index>((l3 - l2) / (sizeof(LhsScalar) * k * num_threads));
      const Index m_per_thread = numext::div_ceil(m, num_threads);
      if (m_cache < m_per_thread && m_cache >= static_cast<Index>(mr)) {
        m = m_cache - (m_cache % mr);
        eigen_internal_assert(m > 0);
      } else {
        m = (numext::mini<Index>)(m, (m_per_thread + mr - 1) - ((m_per_thread + mr - 1) % mr));
      }
    }
  } else {
    // In unit tests we do not want to use extra large matrices,
    // so we reduce the cache size to check the blocking strategy is not flawed
#ifdef EIGEN_DEBUG_SMALL_PRODUCT_BLOCKS
    l1 = 9 * 1024;
    l2 = 32 * 1024;
    l3 = 512 * 1024;
#endif

    // Early return for small problems because the computation below are time consuming for small problems.
    // Perhaps it would make more sense to consider k*n*m?
    // Note that for very tiny problem, this function should be bypassed anyway
    // because we use the coefficient-based implementation for them.
    if ((numext::maxi)(k, (numext::maxi)(m, n)) < 48) return;

    typedef typename Traits::ResScalar ResScalar;
    enum {
      k_peeling = 8,
      k_div = KcFactor * (Traits::mr * sizeof(LhsScalar) + Traits::nr * sizeof(RhsScalar)),
      k_sub = Traits::mr * (Traits::nr * sizeof(ResScalar))
    };

    // ---- 1st level of blocking on L1, yields kc ----

    // Blocking on the third dimension (i.e., k) is chosen so that an horizontal panel
    // of size mr x kc of the lhs plus a vertical panel of kc x nr of the rhs both fits within L1 cache.
    // We also include a register-level block of the result (mx x nr).
    // (In an ideal world only the lhs panel would stay in L1)
    // Moreover, kc has to be a multiple of 8 to be compatible with loop peeling, leading to a maximum blocking size of:
    const Index max_kc = numext::maxi<Index>(static_cast<Index>(((l1 - k_sub) / k_div) & (~(k_peeling - 1))), 1);
    const Index old_k = k;
    if (k > max_kc) {
      // We are really blocking on the third dimension:
      // -> reduce blocking size to make sure the last block is as large as possible
      //    while keeping the same number of sweeps over the result.
      k = (k % max_kc) == 0 ? max_kc
                            : max_kc - k_peeling * ((max_kc - 1 - (k % max_kc)) / (k_peeling * (k / max_kc + 1)));

      eigen_internal_assert(((old_k / k) == (old_k / max_kc)) && "the number of sweeps has to remain the same");
    }

#ifdef EIGEN_VECTORIZE_AVX512
    // The l1 *= 4 inflation above allows larger kc for better accumulator reuse,
    // but can overfill the physical L1. Recompute max_kc using 85% of actual L1
    // to leave headroom for RHS streaming, prefetch buffers, and stack.
    {
      const Index phys_l1_eff = phys_l1 * 85 / 100;
      const Index max_kc_phys = numext::maxi<Index>(((phys_l1_eff - k_sub) / k_div) & (~(k_peeling - 1)), k_peeling);
      if (max_kc_phys < k) {
        k = (old_k % max_kc_phys) == 0 ? max_kc_phys
                                       : max_kc_phys - k_peeling * ((max_kc_phys - 1 - (old_k % max_kc_phys)) /
                                                                    (k_peeling * (old_k / max_kc_phys + 1)));
      }
    }
#endif

// ---- 2nd level of blocking on max(L2,L3), yields nc ----

// Estimate the effective per-core L2 capacity for 2nd-level blocking.
// Use 1.5x the runtime-detected L2 size. The extra 50% accounts for data
// that spills to L3 but remains accessible with low latency. This matches
// the empirically-tuned constant (1.5MB) previously used when L2 was 1MB.
#ifdef EIGEN_DEBUG_SMALL_PRODUCT_BLOCKS
    const Index actual_l2 = static_cast<Index>(l3);
#else
    const Index actual_l2 = static_cast<Index>(l2 * 3 / 2);
#endif

    // Here, nc is chosen such that a block of kc x nc of the rhs fit within half of L2.
    // The second half is implicitly reserved to access the result and lhs coefficients.
    // When k<max_kc, then nc can grow without bound. In practice, it seems to be fruitful
    // to limit this growth: we bound nc growth to a factor of 1.5x.
    // However, if the entire lhs block fit within L1, then we are not going to block on the rows at all,
    // and it becomes fruitful to keep the packed rhs blocks in L1 if there is enough remaining space.
    Index max_nc;
    const Index lhs_bytes = m * k * sizeof(LhsScalar);
    const Index remaining_l1 = static_cast<Index>(l1 - k_sub - lhs_bytes);
    if (remaining_l1 >= Index(Traits::nr * sizeof(RhsScalar)) * k) {
      // L1 blocking
      max_nc = remaining_l1 / (k * sizeof(RhsScalar));
    } else {
      // L2 blocking: use actual kc (k) rather than max_kc so that nc is not
      // unnecessarily squeezed when k < max_kc (e.g. on CPUs with large L1).
      max_nc = (3 * actual_l2) / (2 * 2 * k * sizeof(RhsScalar));
    }
    // WARNING Below, we assume that Traits::nr is a power of two.
    Index nc = numext::mini<Index>(actual_l2 / (2 * k * sizeof(RhsScalar)), max_nc) & (~(Traits::nr - 1));
    if (n > nc) {
      // We are really blocking over the columns:
      // -> reduce blocking size to make sure the last block is as large as possible
      //    while keeping the same number of sweeps over the packed lhs.
      //    Here we allow one more sweep if this gives us a perfect match, thus the commented "-1"
      n = (n % nc) == 0 ? nc : (nc - Traits::nr * ((nc /*-1*/ - (n % nc)) / (Traits::nr * (n / nc + 1))));
    } else if (old_k == k) {
      // No k- or n-blocking happened yet (kc==depth, nc>=n). gebp already
      // strip-chunks the packed lhs via its own `actual_panel_rows` budget,
      // so cache residency is honored whatever mc we pick here. What this
      // branch actually governs is the size of the `mc * kc` packing buffer
      // (blockA) that the caller allocates — capping mc keeps it bounded for
      // tall-m / small-k shapes, where leaving mc=m would allocate up to
      // `rows * depth * sizeof(LhsScalar)`. A budget-based alternative
      // (e.g. cap blockA at ~L3/4) is no faster in benchmarks and increases
      // heap use, so the original L1/L2-residency tuning is kept.
      Index problem_size = k * n * sizeof(LhsScalar);
      Index actual_lm = actual_l2;
      Index max_mc = m;
      if (problem_size <= 1024) {
        // problem is small enough to keep in L1
        // Let's choose m such that lhs's block fit in 1/3 of L1
        actual_lm = static_cast<Index>(l1);
      } else if (l3 != 0 && problem_size <= l1) {
        // We have both L2 and L3, and the rhs panel still fits in L1. Choose mc so the
        // lhs block fits in 1/3 of L2 and avoid spilling into the L2+50% fallback band.
        // The 32768 byte threshold previously used here was a stand-in for typical x86
        // L1 size; using the runtime-detected l1 generalizes this to current cache sizes.
        actual_lm = static_cast<Index>(l2);
        max_mc = (numext::mini<Index>)(576, max_mc);
      }
      Index mc = (numext::mini<Index>)(actual_lm / (3 * k * sizeof(LhsScalar)), max_mc);
      if (mc > Traits::mr)
        mc -= mc % Traits::mr;
      else if (mc == 0)
        return;
      m = (m % mc) == 0 ? mc : (mc - Traits::mr * ((mc /*-1*/ - (m % mc)) / (Traits::mr * (m / mc + 1))));
    }
  }
}

template <typename Index>
inline bool useSpecificBlockingSizes(Index& k, Index& m, Index& n) {
#ifdef EIGEN_TEST_SPECIFIC_BLOCKING_SIZES
  if (EIGEN_TEST_SPECIFIC_BLOCKING_SIZES) {
    k = numext::mini<Index>(k, EIGEN_TEST_SPECIFIC_BLOCKING_SIZE_K);
    m = numext::mini<Index>(m, EIGEN_TEST_SPECIFIC_BLOCKING_SIZE_M);
    n = numext::mini<Index>(n, EIGEN_TEST_SPECIFIC_BLOCKING_SIZE_N);
    return true;
  }
#else
  EIGEN_UNUSED_VARIABLE(k);
  EIGEN_UNUSED_VARIABLE(m);
  EIGEN_UNUSED_VARIABLE(n);
#endif
  return false;
}

/** \brief Computes the blocking parameters for a m x k times k x n matrix product
 *
 * \param[in,out] k Input: the third dimension of the product. Output: the blocking size along the same dimension.
 * \param[in,out] m Input: the number of rows of the left hand side. Output: the blocking size along the same dimension.
 * \param[in,out] n Input: the number of columns of the right hand side. Output: the blocking size along the same
 *                         dimension.
 * \param[in] num_threads Input: the number of threads used for the computation.
 *
 * Given a m x k times k x n matrix product of scalar types \c LhsScalar and \c RhsScalar,
 * this function computes the blocking size parameters along the respective dimensions
 * for matrix products and related algorithms.
 *
 * The blocking size parameters may be evaluated:
 *   - either by a heuristic based on cache sizes;
 *   - or using fixed prescribed values (for testing purposes).
 *
 * \sa setCpuCacheSizes */

template <typename LhsScalar, typename RhsScalar, int KcFactor, typename Index>
void computeProductBlockingSizes(Index& k, Index& m, Index& n, Index num_threads = 1) {
  if (!useSpecificBlockingSizes(k, m, n)) {
    evaluateProductBlockingSizesHeuristic<LhsScalar, RhsScalar, KcFactor, Index>(k, m, n, num_threads);
  }
}

template <typename LhsScalar, typename RhsScalar, typename Index>
inline void computeProductBlockingSizes(Index& k, Index& m, Index& n, Index num_threads = 1) {
  computeProductBlockingSizes<LhsScalar, RhsScalar, 1, Index>(k, m, n, num_threads);
}

template <typename RhsPacket, typename RhsPacketx4, int registers_taken>
struct RhsPanelHelper {
 private:
  static constexpr int remaining_registers =
      (std::max)(int(EIGEN_ARCH_DEFAULT_NUMBER_OF_REGISTERS) - registers_taken, 0);

 public:
  typedef std::conditional_t<remaining_registers >= 4, RhsPacketx4, RhsPacket> type;
};

template <typename Packet>
struct QuadPacket {
  Packet B_0, B1, B2, B3;
  const Packet& get(const FixedInt<0>&) const { return B_0; }
  const Packet& get(const FixedInt<1>&) const { return B1; }
  const Packet& get(const FixedInt<2>&) const { return B2; }
  const Packet& get(const FixedInt<3>&) const { return B3; }
};

template <int N, typename T1, typename T2, typename T3>
struct packet_conditional {
  typedef T3 type;
};

template <typename T1, typename T2, typename T3>
struct packet_conditional<GEBPPacketFull, T1, T2, T3> {
  typedef T1 type;
};

template <typename T1, typename T2, typename T3>
struct packet_conditional<GEBPPacketHalf, T1, T2, T3> {
  typedef T2 type;
};

#define PACKET_DECL_COND_POSTFIX(postfix, name, packet_size)                                               \
  typedef typename packet_conditional<                                                                     \
      packet_size, typename packet_traits<name##Scalar>::type, typename packet_traits<name##Scalar>::half, \
      typename unpacket_traits<typename packet_traits<name##Scalar>::half>::half>::type name##Packet##postfix

#define PACKET_DECL_COND(name, packet_size)                                                                \
  typedef typename packet_conditional<                                                                     \
      packet_size, typename packet_traits<name##Scalar>::type, typename packet_traits<name##Scalar>::half, \
      typename unpacket_traits<typename packet_traits<name##Scalar>::half>::half>::type name##Packet

#define PACKET_DECL_COND_SCALAR_POSTFIX(postfix, packet_size)                                  \
  typedef typename packet_conditional<                                                         \
      packet_size, typename packet_traits<Scalar>::type, typename packet_traits<Scalar>::half, \
      typename unpacket_traits<typename packet_traits<Scalar>::half>::half>::type ScalarPacket##postfix

#define PACKET_DECL_COND_SCALAR(packet_size)                                                   \
  typedef typename packet_conditional<                                                         \
      packet_size, typename packet_traits<Scalar>::type, typename packet_traits<Scalar>::half, \
      typename unpacket_traits<typename packet_traits<Scalar>::half>::half>::type ScalarPacket

/* Vectorization logic
 *  real*real: unpack rhs to constant packets, ...
 *
 *  cd*cd : unpack rhs to (b_r,b_r), (b_i,b_i), mul to get (a_r b_r,a_i b_r) (a_r b_i,a_i b_i),
 *          storing each res packet into two packets (2x2),
 *          at the end combine them: swap the second and addsub them
 *  cf*cf : same but with 2x4 blocks
 *  cplx*real : unpack rhs to constant packets, ...
 *  real*cplx : load lhs as (a0,a0,a1,a1), and mul as usual
 */
template <typename LhsScalar_, typename RhsScalar_, bool ConjLhs_, bool ConjRhs_, int Arch, int PacketSize_>
class gebp_traits {
 public:
  typedef LhsScalar_ LhsScalar;
  typedef RhsScalar_ RhsScalar;
  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);

  enum {
    ConjLhs = ConjLhs_,
    ConjRhs = ConjRhs_,
    Vectorizable = unpacket_traits<LhsPacket_>::vectorizable && unpacket_traits<RhsPacket_>::vectorizable,
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsPacket_>::size : 1,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1,

    NumberOfRegisters = EIGEN_ARCH_DEFAULT_NUMBER_OF_REGISTERS,

    // register block size along the N direction must be 1 or 4
    nr = 4,

    // register block size along the M direction (currently, this one cannot be modified)
    default_mr = (plain_enum_min(16, NumberOfRegisters) / 2 / nr) * LhsPacketSize,
#if defined(EIGEN_HAS_SINGLE_INSTRUCTION_MADD) && !defined(EIGEN_VECTORIZE_ALTIVEC) && \
    !defined(EIGEN_VECTORIZE_VSX) && ((!EIGEN_COMP_MSVC) || (EIGEN_COMP_MSVC >= 1914))
    // we assume 16 registers or more
    // See bug 992, if the scalar type is not vectorizable but that EIGEN_HAS_SINGLE_INSTRUCTION_MADD is defined,
    // then using 3*LhsPacketSize triggers non-implemented paths in syrk.
    // Bug 1515: MSVC prior to v19.14 yields to register spilling.
    mr = Vectorizable ? 3 * LhsPacketSize : default_mr,
#else
    mr = default_mr,
#endif

    LhsProgress = LhsPacketSize,
    RhsProgress = 1
  };

  typedef std::conditional_t<Vectorizable, LhsPacket_, LhsScalar> LhsPacket;
  typedef std::conditional_t<Vectorizable, RhsPacket_, RhsScalar> RhsPacket;
  typedef std::conditional_t<Vectorizable, ResPacket_, ResScalar> ResPacket;
  typedef LhsPacket LhsPacket4Packing;

  typedef QuadPacket<RhsPacket> RhsPacketx4;
  typedef ResPacket AccPacket;

  EIGEN_STRONG_INLINE void initAcc(AccPacket& p) const { p = pset1<ResPacket>(ResScalar(0)); }

  template <typename RhsPacketType>
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketType& dest) const {
    dest = pset1<RhsPacketType>(*b);
  }

  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  template <typename RhsPacketType>
  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacketType& dest) const {
    loadRhs(b, dest);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const { dest = ploadquad<RhsPacket>(b); }

  template <typename LhsPacketType>
  EIGEN_STRONG_INLINE void loadLhs(const LhsScalar* a, LhsPacketType& dest) const {
    dest = pload<LhsPacketType>(a);
  }

  template <typename LhsPacketType>
  EIGEN_STRONG_INLINE void loadLhsUnaligned(const LhsScalar* a, LhsPacketType& dest) const {
    dest = ploadu<LhsPacketType>(a);
  }

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c, RhsPacketType& tmp,
                                const LaneIdType&) const {
    conj_helper<LhsPacketType, RhsPacketType, ConjLhs, ConjRhs> cj;
    // It would be a lot cleaner to call pmadd all the time. Unfortunately if we
    // let gcc allocate the register in which to store the result of the pmul
    // (in the case where there is no FMA) gcc fails to figure out how to avoid
    // spilling register.
#ifdef EIGEN_HAS_SINGLE_INSTRUCTION_MADD
    EIGEN_UNUSED_VARIABLE(tmp);
    c = cj.pmadd(a, b, c);
#else
    tmp = b;
    tmp = cj.pmul(a, tmp);
    c = padd(c, tmp);
#endif
  }

  template <typename LhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketx4& b, AccPacketType& c, RhsPacket& tmp,
                                const LaneIdType& lane) const {
    madd(a, b.get(lane), c, tmp, lane);
  }

  EIGEN_STRONG_INLINE void acc(const AccPacket& c, const ResPacket& alpha, ResPacket& r) const {
    r = pmadd(c, alpha, r);
  }

  template <typename ResPacketHalf>
  EIGEN_STRONG_INLINE void acc(const ResPacketHalf& c, const ResPacketHalf& alpha, ResPacketHalf& r) const {
    r = pmadd(c, alpha, r);
  }
};

template <typename RealScalar, bool ConjLhs_, int Arch, int PacketSize_>
class gebp_traits<std::complex<RealScalar>, RealScalar, ConjLhs_, false, Arch, PacketSize_> {
 public:
  typedef std::complex<RealScalar> LhsScalar;
  typedef RealScalar RhsScalar;
  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);

  enum {
    ConjLhs = ConjLhs_,
    ConjRhs = false,
    Vectorizable = unpacket_traits<LhsPacket_>::vectorizable && unpacket_traits<RhsPacket_>::vectorizable,
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsPacket_>::size : 1,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1,

    NumberOfRegisters = EIGEN_ARCH_DEFAULT_NUMBER_OF_REGISTERS,
    nr = 4,
#if defined(EIGEN_HAS_SINGLE_INSTRUCTION_MADD) && !defined(EIGEN_VECTORIZE_ALTIVEC) && !defined(EIGEN_VECTORIZE_VSX)
    // we assume 16 registers
    mr = 3 * LhsPacketSize,
#else
    mr = (plain_enum_min(16, NumberOfRegisters) / 2 / nr) * LhsPacketSize,
#endif

    LhsProgress = LhsPacketSize,
    RhsProgress = 1
  };

  typedef std::conditional_t<Vectorizable, LhsPacket_, LhsScalar> LhsPacket;
  typedef std::conditional_t<Vectorizable, RhsPacket_, RhsScalar> RhsPacket;
  typedef std::conditional_t<Vectorizable, ResPacket_, ResScalar> ResPacket;
  typedef LhsPacket LhsPacket4Packing;

  typedef QuadPacket<RhsPacket> RhsPacketx4;

  typedef ResPacket AccPacket;

  EIGEN_STRONG_INLINE void initAcc(AccPacket& p) const { p = pset1<ResPacket>(ResScalar(0)); }

  template <typename RhsPacketType>
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketType& dest) const {
    dest = pset1<RhsPacketType>(*b);
  }

  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  template <typename RhsPacketType>
  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacketType& dest) const {
    loadRhs(b, dest);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const {
    loadRhsQuad_impl(b, dest, std::conditional_t<RhsPacketSize == 16, std::true_type, std::false_type>());
  }

  EIGEN_STRONG_INLINE void loadRhsQuad_impl(const RhsScalar* b, RhsPacket& dest, const std::true_type&) const {
    // FIXME: replace with a dedicated ploadheight operation for more efficient quad loading.
    RhsScalar tmp[4] = {b[0], b[0], b[1], b[1]};
    dest = ploadquad<RhsPacket>(tmp);
  }

  EIGEN_STRONG_INLINE void loadRhsQuad_impl(const RhsScalar* b, RhsPacket& dest, const std::false_type&) const {
    eigen_internal_assert(RhsPacketSize <= 8);
    dest = pset1<RhsPacket>(*b);
  }

  EIGEN_STRONG_INLINE void loadLhs(const LhsScalar* a, LhsPacket& dest) const { dest = pload<LhsPacket>(a); }

  template <typename LhsPacketType>
  EIGEN_STRONG_INLINE void loadLhsUnaligned(const LhsScalar* a, LhsPacketType& dest) const {
    dest = ploadu<LhsPacketType>(a);
  }

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c, RhsPacketType& tmp,
                                const LaneIdType&) const {
    madd_impl(a, b, c, tmp, std::conditional_t<Vectorizable, std::true_type, std::false_type>());
  }

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void madd_impl(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c,
                                     RhsPacketType& tmp, const std::true_type&) const {
#ifdef EIGEN_HAS_SINGLE_INSTRUCTION_MADD
    EIGEN_UNUSED_VARIABLE(tmp);
    c.v = pmadd(a.v, b, c.v);
#else
    tmp = b;
    tmp = pmul(a.v, tmp);
    c.v = padd(c.v, tmp);
#endif
  }

  EIGEN_STRONG_INLINE void madd_impl(const LhsScalar& a, const RhsScalar& b, ResScalar& c, RhsScalar& /*tmp*/,
                                     const std::false_type&) const {
    c += a * b;
  }

  template <typename LhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketx4& b, AccPacketType& c, RhsPacket& tmp,
                                const LaneIdType& lane) const {
    madd(a, b.get(lane), c, tmp, lane);
  }

  template <typename ResPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void acc(const AccPacketType& c, const ResPacketType& alpha, ResPacketType& r) const {
    conj_helper<ResPacketType, ResPacketType, ConjLhs, false> cj;
    r = cj.pmadd(c, alpha, r);
  }

 protected:
};

template <typename Packet>
struct DoublePacket {
  Packet first;
  Packet second;
};

template <typename Packet>
DoublePacket<Packet> padd(const DoublePacket<Packet>& a, const DoublePacket<Packet>& b) {
  DoublePacket<Packet> res;
  res.first = padd(a.first, b.first);
  res.second = padd(a.second, b.second);
  return res;
}

template <typename Packet>
const DoublePacket<Packet>& predux_half(const DoublePacket<Packet>& a,
                                        std::enable_if_t<unpacket_traits<Packet>::size <= 8>* = 0) {
  return a;
}

template <typename Packet>
DoublePacket<typename unpacket_traits<Packet>::half> predux_half(
    const DoublePacket<Packet>& a,
    std::enable_if_t<unpacket_traits<Packet>::size >= 16 &&
                     !NumTraits<typename unpacket_traits<Packet>::type>::IsComplex>* = 0) {
  // Workaround: reduce real packets to half size by reinterpreting as complex.
  DoublePacket<typename unpacket_traits<Packet>::half> res;
  typedef std::complex<typename unpacket_traits<Packet>::type> Cplx;
  typedef typename packet_traits<Cplx>::type CplxPacket;
  res.first = predux_half(CplxPacket(a.first)).v;
  res.second = predux_half(CplxPacket(a.second)).v;
  return res;
}

// same here, "quad" actually means "8" in terms of real coefficients
template <typename Scalar, typename RealPacket>
void loadQuadToDoublePacket(const Scalar* b, DoublePacket<RealPacket>& dest,
                            std::enable_if_t<unpacket_traits<RealPacket>::size <= 8>* = 0) {
  dest.first = pset1<RealPacket>(numext::real(*b));
  dest.second = pset1<RealPacket>(numext::imag(*b));
}

template <typename Scalar, typename RealPacket>
void loadQuadToDoublePacket(const Scalar* b, DoublePacket<RealPacket>& dest,
                            std::enable_if_t<unpacket_traits<RealPacket>::size == 16>* = 0) {
  // Workaround: load quad elements by reinterpreting real packets as complex.
  typedef typename NumTraits<Scalar>::Real RealScalar;
  RealScalar r[4] = {numext::real(b[0]), numext::real(b[0]), numext::real(b[1]), numext::real(b[1])};
  RealScalar i[4] = {numext::imag(b[0]), numext::imag(b[0]), numext::imag(b[1]), numext::imag(b[1])};
  dest.first = ploadquad<RealPacket>(r);
  dest.second = ploadquad<RealPacket>(i);
}

template <typename Packet>
struct unpacket_traits<DoublePacket<Packet>> {
  typedef DoublePacket<typename unpacket_traits<Packet>::half> half;
  enum { size = 2 * unpacket_traits<Packet>::size };
};
// template<typename Packet>
// DoublePacket<Packet> pmadd(const DoublePacket<Packet> &a, const DoublePacket<Packet> &b)
// {
//   DoublePacket<Packet> res;
//   res.first  = padd(a.first, b.first);
//   res.second = padd(a.second,b.second);
//   return res;
// }

template <typename RealScalar, bool ConjLhs_, bool ConjRhs_, int Arch, int PacketSize_>
class gebp_traits<std::complex<RealScalar>, std::complex<RealScalar>, ConjLhs_, ConjRhs_, Arch, PacketSize_> {
 public:
  typedef std::complex<RealScalar> Scalar;
  typedef std::complex<RealScalar> LhsScalar;
  typedef std::complex<RealScalar> RhsScalar;
  typedef std::complex<RealScalar> ResScalar;

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);
  PACKET_DECL_COND(Real, PacketSize_);
  PACKET_DECL_COND_SCALAR(PacketSize_);

  enum {
    ConjLhs = ConjLhs_,
    ConjRhs = ConjRhs_,
    Vectorizable = unpacket_traits<RealPacket>::vectorizable && unpacket_traits<ScalarPacket>::vectorizable,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1,
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsScalar>::size : 1,
    RealPacketSize = Vectorizable ? unpacket_traits<RealPacket>::size : 1,
    NumberOfRegisters = EIGEN_ARCH_DEFAULT_NUMBER_OF_REGISTERS,

    nr = 4,
    mr = (plain_enum_min(16, NumberOfRegisters) / 2 / nr) * ResPacketSize,

    LhsProgress = ResPacketSize,
    RhsProgress = 1
  };

  typedef DoublePacket<RealPacket> DoublePacketType;

  typedef std::conditional_t<Vectorizable, ScalarPacket, Scalar> LhsPacket4Packing;
  typedef std::conditional_t<Vectorizable, RealPacket, Scalar> LhsPacket;
  typedef std::conditional_t<Vectorizable, DoublePacketType, Scalar> RhsPacket;
  typedef std::conditional_t<Vectorizable, ScalarPacket, Scalar> ResPacket;
  typedef std::conditional_t<Vectorizable, DoublePacketType, Scalar> AccPacket;

  // this actually holds 8 packets!
  typedef QuadPacket<RhsPacket> RhsPacketx4;

  EIGEN_STRONG_INLINE void initAcc(Scalar& p) const { p = Scalar(0); }

  EIGEN_STRONG_INLINE void initAcc(DoublePacketType& p) const {
    p.first = pset1<RealPacket>(RealScalar(0));
    p.second = pset1<RealPacket>(RealScalar(0));
  }

  // Scalar path
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, ScalarPacket& dest) const { dest = pset1<ScalarPacket>(*b); }

  // Vectorized path
  template <typename RealPacketType>
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, DoublePacket<RealPacketType>& dest) const {
    dest.first = pset1<RealPacketType>(numext::real(*b));
    dest.second = pset1<RealPacketType>(numext::imag(*b));
  }

  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    loadRhs(b, dest.B_0);
    loadRhs(b + 1, dest.B1);
    loadRhs(b + 2, dest.B2);
    loadRhs(b + 3, dest.B3);
  }

  // Scalar path
  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, ScalarPacket& dest) const { loadRhs(b, dest); }

  // Vectorized path
  template <typename RealPacketType>
  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, DoublePacket<RealPacketType>& dest) const {
    loadRhs(b, dest);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, ResPacket& dest) const { loadRhs(b, dest); }
  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, DoublePacketType& dest) const {
    loadQuadToDoublePacket(b, dest);
  }

  // nothing special here
  EIGEN_STRONG_INLINE void loadLhs(const LhsScalar* a, LhsPacket& dest) const {
    dest = pload<LhsPacket>((const typename unpacket_traits<LhsPacket>::type*)(a));
  }

  template <typename LhsPacketType>
  EIGEN_STRONG_INLINE void loadLhsUnaligned(const LhsScalar* a, LhsPacketType& dest) const {
    dest = ploadu<LhsPacketType>((const typename unpacket_traits<LhsPacketType>::type*)(a));
  }

  template <typename LhsPacketType, typename RhsPacketType, typename ResPacketType, typename TmpType,
            typename LaneIdType>
  EIGEN_STRONG_INLINE std::enable_if_t<!std::is_same<RhsPacketType, RhsPacketx4>::value> madd(
      const LhsPacketType& a, const RhsPacketType& b, DoublePacket<ResPacketType>& c, TmpType& /*tmp*/,
      const LaneIdType&) const {
    c.first = pmadd(a, b.first, c.first);
    c.second = pmadd(a, b.second, c.second);
  }

  template <typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacket& b, ResPacket& c, RhsPacket& /*tmp*/,
                                const LaneIdType&) const {
    c = cj.pmadd(a, b, c);
  }

  template <typename LhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketx4& b, AccPacketType& c, RhsPacket& tmp,
                                const LaneIdType& lane) const {
    madd(a, b.get(lane), c, tmp, lane);
  }

  EIGEN_STRONG_INLINE void acc(const Scalar& c, const Scalar& alpha, Scalar& r) const { r += alpha * c; }

  template <typename RealPacketType, typename ResPacketType>
  EIGEN_STRONG_INLINE void acc(const DoublePacket<RealPacketType>& c, const ResPacketType& alpha,
                               ResPacketType& r) const {
    // assemble c
    ResPacketType tmp;
    if ((!ConjLhs) && (!ConjRhs)) {
      tmp = pcplxflip(pconj(ResPacketType(c.second)));
      tmp = padd(ResPacketType(c.first), tmp);
    } else if ((!ConjLhs) && (ConjRhs)) {
      tmp = pconj(pcplxflip(ResPacketType(c.second)));
      tmp = padd(ResPacketType(c.first), tmp);
    } else if ((ConjLhs) && (!ConjRhs)) {
      tmp = pcplxflip(ResPacketType(c.second));
      tmp = padd(pconj(ResPacketType(c.first)), tmp);
    } else if ((ConjLhs) && (ConjRhs)) {
      tmp = pcplxflip(ResPacketType(c.second));
      tmp = psub(pconj(ResPacketType(c.first)), tmp);
    }

    r = pmadd(tmp, alpha, r);
  }

 protected:
  conj_helper<LhsScalar, RhsScalar, ConjLhs, ConjRhs> cj;
};

template <typename RealScalar, bool ConjRhs_, int Arch, int PacketSize_>
class gebp_traits<RealScalar, std::complex<RealScalar>, false, ConjRhs_, Arch, PacketSize_> {
 public:
  typedef std::complex<RealScalar> Scalar;
  typedef RealScalar LhsScalar;
  typedef Scalar RhsScalar;
  typedef Scalar ResScalar;

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Real, PacketSize_);
  PACKET_DECL_COND_SCALAR_POSTFIX(_, PacketSize_);

#undef PACKET_DECL_COND_SCALAR_POSTFIX
#undef PACKET_DECL_COND_POSTFIX
#undef PACKET_DECL_COND_SCALAR
#undef PACKET_DECL_COND

  enum {
    ConjLhs = false,
    ConjRhs = ConjRhs_,
    Vectorizable = unpacket_traits<RealPacket_>::vectorizable && unpacket_traits<ScalarPacket_>::vectorizable,
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsPacket_>::size : 1,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1,

    NumberOfRegisters = EIGEN_ARCH_DEFAULT_NUMBER_OF_REGISTERS,
    // FIXME: should depend on NumberOfRegisters
    nr = 4,
    mr = (plain_enum_min(16, NumberOfRegisters) / 2 / nr) * ResPacketSize,

    LhsProgress = ResPacketSize,
    RhsProgress = 1
  };

  typedef std::conditional_t<Vectorizable, LhsPacket_, LhsScalar> LhsPacket;
  typedef std::conditional_t<Vectorizable, RhsPacket_, RhsScalar> RhsPacket;
  typedef std::conditional_t<Vectorizable, ResPacket_, ResScalar> ResPacket;
  typedef LhsPacket LhsPacket4Packing;
  typedef QuadPacket<RhsPacket> RhsPacketx4;
  typedef ResPacket AccPacket;

  EIGEN_STRONG_INLINE void initAcc(AccPacket& p) const { p = pset1<ResPacket>(ResScalar(0)); }

  template <typename RhsPacketType>
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketType& dest) const {
    dest = pset1<RhsPacketType>(*b);
  }

  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  template <typename RhsPacketType>
  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacketType& dest) const {
    loadRhs(b, dest);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadLhs(const LhsScalar* a, LhsPacket& dest) const { dest = ploaddup<LhsPacket>(a); }

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const { dest = ploadquad<RhsPacket>(b); }

  template <typename LhsPacketType>
  EIGEN_STRONG_INLINE void loadLhsUnaligned(const LhsScalar* a, LhsPacketType& dest) const {
    dest = ploaddup<LhsPacketType>(a);
  }

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c, RhsPacketType& tmp,
                                const LaneIdType&) const {
    madd_impl(a, b, c, tmp, std::conditional_t<Vectorizable, std::true_type, std::false_type>());
  }

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void madd_impl(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c,
                                     RhsPacketType& tmp, const std::true_type&) const {
#ifdef EIGEN_HAS_SINGLE_INSTRUCTION_MADD
    EIGEN_UNUSED_VARIABLE(tmp);
    c.v = pmadd(a, b.v, c.v);
#else
    tmp = b;
    tmp.v = pmul(a, tmp.v);
    c = padd(c, tmp);
#endif
  }

  EIGEN_STRONG_INLINE void madd_impl(const LhsScalar& a, const RhsScalar& b, ResScalar& c, RhsScalar& /*tmp*/,
                                     const std::false_type&) const {
    c += a * b;
  }

  template <typename LhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketx4& b, AccPacketType& c, RhsPacket& tmp,
                                const LaneIdType& lane) const {
    madd(a, b.get(lane), c, tmp, lane);
  }

  template <typename ResPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void acc(const AccPacketType& c, const ResPacketType& alpha, ResPacketType& r) const {
    conj_helper<ResPacketType, ResPacketType, false, ConjRhs> cj;
    r = cj.pmadd(alpha, c, r);
  }

 protected:
};

/* optimized General packed Block * packed Panel product kernel
 *
 * Mixing type logic: C += A * B
 *  |  A  |  B  | comments
 *  |real |cplx | no vectorization yet, would require to pack A with duplication
 *  |cplx |real | easy vectorization
 */
template <typename LhsScalar, typename RhsScalar, typename Index, typename DataMapper, int mr, int nr,
          bool ConjugateLhs, bool ConjugateRhs>
struct gebp_kernel {
  typedef gebp_traits<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs, Architecture::Target> Traits;
  typedef gebp_traits<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs, Architecture::Target, GEBPPacketHalf>
      HalfTraits;
  typedef gebp_traits<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs, Architecture::Target, GEBPPacketQuarter>
      QuarterTraits;

  typedef typename Traits::ResScalar ResScalar;
  typedef typename Traits::LhsPacket LhsPacket;
  typedef typename Traits::RhsPacket RhsPacket;
  typedef typename Traits::ResPacket ResPacket;
  typedef typename Traits::AccPacket AccPacket;
  typedef typename Traits::RhsPacketx4 RhsPacketx4;

  typedef typename RhsPanelHelper<RhsPacket, RhsPacketx4, 15>::type RhsPanel15;
  typedef typename RhsPanelHelper<RhsPacket, RhsPacketx4, 27>::type RhsPanel27;

  typedef gebp_traits<RhsScalar, LhsScalar, ConjugateRhs, ConjugateLhs, Architecture::Target> SwappedTraits;

  typedef typename SwappedTraits::ResScalar SResScalar;
  typedef typename SwappedTraits::LhsPacket SLhsPacket;
  typedef typename SwappedTraits::RhsPacket SRhsPacket;
  typedef typename SwappedTraits::ResPacket SResPacket;
  typedef typename SwappedTraits::AccPacket SAccPacket;

  typedef typename HalfTraits::LhsPacket LhsPacketHalf;
  typedef typename HalfTraits::RhsPacket RhsPacketHalf;
  typedef typename HalfTraits::ResPacket ResPacketHalf;
  typedef typename HalfTraits::AccPacket AccPacketHalf;

  typedef typename QuarterTraits::LhsPacket LhsPacketQuarter;
  typedef typename QuarterTraits::RhsPacket RhsPacketQuarter;
  typedef typename QuarterTraits::ResPacket ResPacketQuarter;
  typedef typename QuarterTraits::AccPacket AccPacketQuarter;

  typedef typename DataMapper::LinearMapper LinearMapper;

  enum {
    Vectorizable = Traits::Vectorizable,
    LhsProgress = Traits::LhsProgress,
    LhsProgressHalf = HalfTraits::LhsProgress,
    LhsProgressQuarter = QuarterTraits::LhsProgress,
    RhsProgress = Traits::RhsProgress,
    RhsProgressHalf = HalfTraits::RhsProgress,
    RhsProgressQuarter = QuarterTraits::RhsProgress,
    ResPacketSize = Traits::ResPacketSize
  };

  EIGEN_DONT_INLINE void operator()(const DataMapper& res, const LhsScalar* blockA, const RhsScalar* blockB, Index rows,
                                    Index depth, Index cols, ResScalar alpha, Index strideA = -1, Index strideB = -1,
                                    Index offsetA = 0, Index offsetB = 0) const;
};

template <typename LhsScalar, typename RhsScalar, typename Index, typename DataMapper, int mr, int nr,
          bool ConjugateLhs, bool ConjugateRhs,
          int SwappedLhsProgress =
              gebp_traits<RhsScalar, LhsScalar, ConjugateRhs, ConjugateLhs, Architecture::Target>::LhsProgress>
struct last_row_process_16_packets {
  typedef gebp_traits<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs, Architecture::Target> Traits;
  typedef gebp_traits<RhsScalar, LhsScalar, ConjugateRhs, ConjugateLhs, Architecture::Target> SwappedTraits;

  typedef typename Traits::ResScalar ResScalar;
  typedef typename SwappedTraits::LhsPacket SLhsPacket;
  typedef typename SwappedTraits::RhsPacket SRhsPacket;
  typedef typename SwappedTraits::ResPacket SResPacket;
  typedef typename SwappedTraits::AccPacket SAccPacket;

  EIGEN_STRONG_INLINE void operator()(const DataMapper& res, SwappedTraits& straits, const LhsScalar* blA,
                                      const RhsScalar* blB, Index depth, const Index endk, Index i, Index j2,
                                      ResScalar alpha, SAccPacket& C0) const {
    EIGEN_UNUSED_VARIABLE(res);
    EIGEN_UNUSED_VARIABLE(straits);
    EIGEN_UNUSED_VARIABLE(blA);
    EIGEN_UNUSED_VARIABLE(blB);
    EIGEN_UNUSED_VARIABLE(depth);
    EIGEN_UNUSED_VARIABLE(endk);
    EIGEN_UNUSED_VARIABLE(i);
    EIGEN_UNUSED_VARIABLE(j2);
    EIGEN_UNUSED_VARIABLE(alpha);
    EIGEN_UNUSED_VARIABLE(C0);
  }
};

template <typename LhsScalar, typename RhsScalar, typename Index, typename DataMapper, int mr, int nr,
          bool ConjugateLhs, bool ConjugateRhs>
struct last_row_process_16_packets<LhsScalar, RhsScalar, Index, DataMapper, mr, nr, ConjugateLhs, ConjugateRhs, 16> {
  typedef gebp_traits<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs, Architecture::Target> Traits;
  typedef gebp_traits<RhsScalar, LhsScalar, ConjugateRhs, ConjugateLhs, Architecture::Target> SwappedTraits;

  typedef typename Traits::ResScalar ResScalar;
  typedef typename SwappedTraits::LhsPacket SLhsPacket;
  typedef typename SwappedTraits::RhsPacket SRhsPacket;
  typedef typename SwappedTraits::ResPacket SResPacket;
  typedef typename SwappedTraits::AccPacket SAccPacket;

  EIGEN_STRONG_INLINE void operator()(const DataMapper& res, SwappedTraits& straits, const LhsScalar* blA,
                                      const RhsScalar* blB, Index depth, const Index endk, Index i, Index j2,
                                      ResScalar alpha, SAccPacket& C0) const {
    typedef typename unpacket_traits<typename unpacket_traits<SResPacket>::half>::half SResPacketQuarter;
    typedef typename unpacket_traits<typename unpacket_traits<SLhsPacket>::half>::half SLhsPacketQuarter;
    typedef typename unpacket_traits<typename unpacket_traits<SRhsPacket>::half>::half SRhsPacketQuarter;
    typedef typename unpacket_traits<typename unpacket_traits<SAccPacket>::half>::half SAccPacketQuarter;

    SResPacketQuarter R = res.template gatherPacket<SResPacketQuarter>(i, j2);
    SResPacketQuarter alphav = pset1<SResPacketQuarter>(alpha);

    if (depth - endk > 0) {
      // We have to handle the last row(s) of the rhs, which
      // correspond to a half-packet
      SAccPacketQuarter c0 = predux_half(predux_half(C0));

      for (Index kk = endk; kk < depth; kk++) {
        SLhsPacketQuarter a0;
        SRhsPacketQuarter b0;
        straits.loadLhsUnaligned(blB, a0);
        straits.loadRhs(blA, b0);
        straits.madd(a0, b0, c0, b0, fix<0>);
        blB += SwappedTraits::LhsProgress / 4;
        blA += 1;
      }
      straits.acc(c0, alphav, R);
    } else {
      straits.acc(predux_half(predux_half(C0)), alphav, R);
    }
    res.scatterPacket(i, j2, R);
  }
};

// Compile-time recursive helper: processes RHS columns J..NrCols-1 for gebp_micro_onestep.
// For each column, loads/updates the RHS panel and does madd for all MrPackets LHS packets.
// The bool partial specialization terminates the recursion without requiring if constexpr.
template <int J, int MrPackets, int NrCols, bool Continue = (J < NrCols)>
struct gebp_rhs_cols;

// Base case: J >= NrCols, do nothing.
template <int J, int MrPackets, int NrCols>
struct gebp_rhs_cols<J, MrPackets, NrCols, false> {
  template <typename GEBPTraits, typename LhsArray, typename RhsPanelType, typename RhsPacketType, typename AccArray,
            typename RhsScalar>
  static EIGEN_ALWAYS_INLINE void run(GEBPTraits&, const RhsScalar*, Index, LhsArray&, RhsPanelType&, RhsPacketType&,
                                      AccArray&) {}
};

// Active case: J < NrCols.
template <int J, int MrPackets, int NrCols>
struct gebp_rhs_cols<J, MrPackets, NrCols, true> {
  template <typename GEBPTraits, typename LhsArray, typename RhsPanelType, typename RhsPacketType, typename AccArray,
            typename RhsScalar>
  static EIGEN_ALWAYS_INLINE void run(GEBPTraits& traits, const RhsScalar* blB, Index rhs_offset, LhsArray& A,
                                      RhsPanelType& rhs_panel, RhsPacketType& T0, AccArray& C) {
    constexpr int lane = J % 4;
    EIGEN_IF_CONSTEXPR(lane == 0)
    traits.loadRhs(blB + (J + rhs_offset) * GEBPTraits::RhsProgress, rhs_panel);
    else traits.updateRhs(blB + (J + rhs_offset) * GEBPTraits::RhsProgress, rhs_panel);

    EIGEN_IF_CONSTEXPR(MrPackets >= 1) traits.madd(A[0], rhs_panel, C[J + 0 * NrCols], T0, fix<lane>);
    EIGEN_IF_CONSTEXPR(MrPackets >= 2) traits.madd(A[1], rhs_panel, C[J + 1 * NrCols], T0, fix<lane>);
    EIGEN_IF_CONSTEXPR(MrPackets >= 3) traits.madd(A[2], rhs_panel, C[J + 2 * NrCols], T0, fix<lane>);

    gebp_rhs_cols<J + 1, MrPackets, NrCols>::run(traits, blB, rhs_offset, A, rhs_panel, T0, C);
  }
};

// One step of the micro-kernel: loads MrPackets LHS packets at step K,
// then processes NrCols RHS columns via gebp_rhs_cols.
template <int K, int MrPackets, int NrCols>
struct gebp_micro_step {
  template <typename GEBPTraits, typename LhsScalar_, typename RhsScalar_, typename LhsArray, typename RhsPanelType,
            typename RhsPacketType, typename AccArray>
  static EIGEN_ALWAYS_INLINE void run(GEBPTraits& traits, const LhsScalar_* blA, const RhsScalar_* blB, LhsArray& A,
                                      RhsPanelType& rhs_panel, RhsPacketType& T0, AccArray& C) {
    constexpr int LhsProg = GEBPTraits::LhsProgress;

    EIGEN_IF_CONSTEXPR(MrPackets >= 1) traits.loadLhs(&blA[(0 + MrPackets * K) * LhsProg], A[0]);
    EIGEN_IF_CONSTEXPR(MrPackets >= 2) traits.loadLhs(&blA[(1 + MrPackets * K) * LhsProg], A[1]);
    EIGEN_IF_CONSTEXPR(MrPackets >= 3) traits.loadLhs(&blA[(2 + MrPackets * K) * LhsProg], A[2]);

    gebp_rhs_cols<0, MrPackets, NrCols>::run(traits, blB, Index(NrCols * K), A, rhs_panel, T0, C);
  }
};
// Compiler register allocation workarounds for the GEBP micro-kernel.
// GCC can fail to keep array-based SIMD values in vector registers, causing
// excessive spilling. These helpers use inline asm constraints to pin values.
// Only applied when the scalar type is actually vectorizable (not custom types).
// See Eigen bugs 935, 1637, and 3059.

// ARM64 NEON: pin 3 LHS packets in vector registers.
// Old GCC (< 9) misallocates registers for 3-packet paths without this hint.
template <int MrPackets, typename GEBPTraits_, typename FullLhsPacket_, typename LhsArray_>
EIGEN_ALWAYS_INLINE void gebp_neon_3p_workaround(LhsArray_& A) {
#if EIGEN_ARCH_ARM64 && defined(EIGEN_VECTORIZE_NEON) && EIGEN_GNUC_STRICT_LESS_THAN(9, 0, 0)
  using LhsElement = std::remove_all_extents_t<std::remove_reference_t<LhsArray_>>;
  constexpr bool apply = GEBPTraits_::Vectorizable && MrPackets == 3 && std::is_same<LhsElement, FullLhsPacket_>::value;
  EIGEN_IF_CONSTEXPR(apply) { __asm__("" : "+w,m"(A[0]), "+w,m"(A[1]), "+w,m"(A[2])); }
#else
  EIGEN_UNUSED_VARIABLE(A);
#endif
}

// GCC SSE: prevent register spilling for LHS packets and accumulators.
// C++17: pin accumulators with strict "+x" (if constexpr discards dead branches).
// C++14: pin LHS packets with relaxed "+x,m" (memory fallback for non-SSE types).
template <int MrPackets, int NrCols, typename GEBPTraits_, typename FullLhsPacket_, typename LhsArray_,
          typename AccArray_>
EIGEN_ALWAYS_INLINE void gebp_sse_spilling_workaround(LhsArray_& A, AccArray_& ACC) {
  EIGEN_UNUSED_VARIABLE(A);
  EIGEN_UNUSED_VARIABLE(ACC);
#if EIGEN_GNUC_STRICT_AT_LEAST(6, 0, 0) && defined(EIGEN_VECTORIZE_SSE)
  using LhsElement = std::remove_all_extents_t<std::remove_reference_t<LhsArray_>>;
  constexpr bool apply =
      GEBPTraits_::Vectorizable && MrPackets <= 2 && NrCols >= 4 && std::is_same<LhsElement, FullLhsPacket_>::value;
  EIGEN_IF_CONSTEXPR(apply) {
#ifdef EIGEN_HAS_CXX17_IFCONSTEXPR
    using AccElement = std::decay_t<decltype(ACC[0])>;
    constexpr bool pin_acc = std::is_same<AccElement, FullLhsPacket_>::value && MrPackets == 2 && NrCols == 4;
    if constexpr (pin_acc) {
      __asm__(""
              : "+x"(ACC[0]), "+x"(ACC[1]), "+x"(ACC[2]), "+x"(ACC[3]), "+x"(ACC[4]), "+x"(ACC[5]), "+x"(ACC[6]),
                "+x"(ACC[7]));
    }
#else
    EIGEN_IF_CONSTEXPR(MrPackets == 2) { __asm__("" : "+x,m"(A[0]), "+x,m"(A[1])); }
#endif
  }
#endif
}

// Unrolled peeled loop body: calls gebp_micro_step for K=0..7, handling
// double-accumulation for 1pX4, prefetches, and compiler workarounds.
template <int MrPackets, int NrCols>
struct gebp_peeled_loop {
  template <typename GEBPTraits, typename LhsScalar_, typename RhsScalar_, typename LhsArray, typename RhsPanelType,
            typename RhsPacketType, typename AccArray, typename AccArrayD, typename FullLhsPacket>
  static EIGEN_ALWAYS_INLINE void run(GEBPTraits& traits, const LhsScalar_* blA, const RhsScalar_* blB, LhsArray& A,
                                      RhsPanelType& rhs_panel, RhsPacketType& T0, AccArray& C, AccArrayD& D) {
    constexpr bool use_double_accum = (MrPackets == 1 && NrCols == 4);

    // Prefetch for 4-col paths
    EIGEN_IF_CONSTEXPR(NrCols == 4) { internal::prefetch(blB + (48 + 0)); }

    // Helper to do one step with workarounds
#define EIGEN_GEBP_DO_STEP(KVAL, ACC)                                                       \
  do {                                                                                      \
    gebp_micro_step<KVAL, MrPackets, NrCols>::run(traits, blA, blB, A, rhs_panel, T0, ACC); \
    gebp_neon_3p_workaround<MrPackets, GEBPTraits, FullLhsPacket>(A);                       \
    gebp_sse_spilling_workaround<MrPackets, NrCols, GEBPTraits, FullLhsPacket>(A, ACC);     \
    /* LHS prefetch for 2pX4 and 3pX4 */                                                    \
    EIGEN_IF_CONSTEXPR((MrPackets == 2 || MrPackets == 3) && NrCols == 4) {                 \
      internal::prefetch(blA + (MrPackets * KVAL + 16) * GEBPTraits::LhsProgress);          \
      if (EIGEN_ARCH_ARM || EIGEN_ARCH_MIPS) {                                              \
        internal::prefetch(blB + (NrCols * KVAL + 16) * GEBPTraits::RhsProgress);           \
      }                                                                                     \
    }                                                                                       \
  } while (false)

    EIGEN_IF_CONSTEXPR(use_double_accum) {
      EIGEN_GEBP_DO_STEP(0, C);
      EIGEN_GEBP_DO_STEP(1, D);
      EIGEN_GEBP_DO_STEP(2, C);
      EIGEN_GEBP_DO_STEP(3, D);
      EIGEN_IF_CONSTEXPR(NrCols == 4) { internal::prefetch(blB + (48 + 16)); }
      EIGEN_GEBP_DO_STEP(4, C);
      EIGEN_GEBP_DO_STEP(5, D);
      EIGEN_GEBP_DO_STEP(6, C);
      EIGEN_GEBP_DO_STEP(7, D);
    }
    else {
      EIGEN_GEBP_DO_STEP(0, C);
      EIGEN_GEBP_DO_STEP(1, C);
      EIGEN_GEBP_DO_STEP(2, C);
      EIGEN_GEBP_DO_STEP(3, C);
      EIGEN_IF_CONSTEXPR(NrCols == 4 && MrPackets == 2) { internal::prefetch(blB + (48 + 16)); }
      EIGEN_GEBP_DO_STEP(4, C);
      EIGEN_GEBP_DO_STEP(5, C);
      EIGEN_GEBP_DO_STEP(6, C);
      EIGEN_GEBP_DO_STEP(7, C);
    }

#undef EIGEN_GEBP_DO_STEP
  }
};

// Unified micro-panel function: handles a MrPackets x NrCols register block.
// GEBPTraits determines the packet types (supports full/half/quarter sizes).
// Accumulator layout: C[j + p * NrCols] for column j, LHS packet p.
template <int MrPackets, int NrCols, typename GEBPTraits, typename LhsScalar_, typename RhsScalar_, typename ResScalar_,
          typename Index_, typename DataMapper_, typename LinearMapper_, typename FullLhsPacket>
EIGEN_ALWAYS_INLINE void gebp_micro_panel_impl(GEBPTraits& traits, const DataMapper_& res, const LhsScalar_* blockA,
                                               const RhsScalar_* blockB, ResScalar_ alpha, Index_ i, Index_ j2,
                                               Index_ depth, Index_ strideA, Index_ strideB, Index_ offsetA,
                                               Index_ offsetB, int prefetch_res_offset, Index_ peeled_kc, int pk) {
  using LhsPacketLocal = typename GEBPTraits::LhsPacket;
  using RhsPacketLocal = typename GEBPTraits::RhsPacket;
  using ResPacketLocal = typename GEBPTraits::ResPacket;
  using AccPacketLocal = typename GEBPTraits::AccPacket;
  using RhsPacketx4Local = typename GEBPTraits::RhsPacketx4;
  constexpr int LhsProg = GEBPTraits::LhsProgress;
  constexpr int RhsProg = GEBPTraits::RhsProgress;
  constexpr int ResPacketSz = GEBPTraits::ResPacketSize;

  // Determine RhsPanel type based on register pressure
  using RhsPanelType = std::conditional_t<
      NrCols == 1, RhsPacketLocal,
      typename RhsPanelHelper<RhsPacketLocal, RhsPacketx4Local, MrPackets * NrCols + MrPackets>::type>;

  const LhsScalar_* blA = &blockA[i * strideA + offsetA * (MrPackets * LhsProg)];
  prefetch(&blA[0]);

  // Accumulators: C[j + p * NrCols] for column j, LHS packet p.
  // With if constexpr (C++17) we use exact sizes; with plain if (C++14) we pad
  // to 3*NrCols so dead-branch array accesses in gebp_rhs_cols remain valid.
#ifdef EIGEN_HAS_CXX17_IFCONSTEXPR
  constexpr int CSize = MrPackets * NrCols;
#else
  constexpr int CSize = 3 * NrCols > MrPackets * NrCols ? 3 * NrCols : MrPackets * NrCols;
#endif
  alignas(AccPacketLocal) AccPacketLocal C[CSize];
  for (int n = 0; n < MrPackets * NrCols; ++n) traits.initAcc(C[n]);

  // Double-accumulation trick for 1pX4 path to break FMA dependency chains
  constexpr bool use_double_accum = (MrPackets == 1 && NrCols == 4);
#ifdef EIGEN_HAS_CXX17_IFCONSTEXPR
  alignas(AccPacketLocal) AccPacketLocal D[use_double_accum ? NrCols : 1];
#else
  // Without if constexpr, we must allocate a larger array to satisfy the
  // compiler that D[n] is always in bounds for the use_double_accum path.
  alignas(AccPacketLocal) AccPacketLocal D[CSize];
#endif
  EIGEN_IF_CONSTEXPR(use_double_accum) {
    for (int n = 0; n < NrCols; ++n) traits.initAcc(D[n]);
  }

  // Prefetch result memory
  for (int j = 0; j < NrCols; ++j) res.getLinearMapper(i, j2 + j).prefetch(NrCols > 1 ? prefetch_res_offset : 0);

  // RHS pointer
  const RhsScalar_* blB = &blockB[j2 * strideB + offsetB * NrCols];
  prefetch(&blB[0]);

  // LHS packet staging area. With if constexpr (C++17) we use exact sizes.
#ifdef EIGEN_HAS_CXX17_IFCONSTEXPR
  alignas(LhsPacketLocal) LhsPacketLocal A[MrPackets];
#else
  alignas(LhsPacketLocal) LhsPacketLocal A[3];
#endif

  // ---- Peeled k-loop (pk=8 unrolled) ----
  for (Index_ k = 0; k < peeled_kc; k += pk) {
    alignas(RhsPanelType) RhsPanelType rhs_panel;
    alignas(RhsPacketLocal) RhsPacketLocal T0;

    gebp_peeled_loop<MrPackets, NrCols>::template run<GEBPTraits, LhsScalar_, RhsScalar_, decltype(A), RhsPanelType,
                                                      RhsPacketLocal, decltype(C), decltype(D), FullLhsPacket>(
        traits, blA, blB, A, rhs_panel, T0, C, D);

    blB += pk * NrCols * RhsProg;
    blA += pk * MrPackets * LhsProg;
  }

  // Merge double accumulators
  EIGEN_IF_CONSTEXPR(use_double_accum) {
    for (int n = 0; n < NrCols; ++n) C[n] = padd(C[n], D[n]);
  }

  // ---- Remainder k-loop ----
  for (Index_ k = peeled_kc; k < depth; k++) {
    alignas(RhsPanelType) RhsPanelType rhs_panel;
    alignas(RhsPacketLocal) RhsPacketLocal T0;

    gebp_micro_step<0, MrPackets, NrCols>::run(traits, blA, blB, A, rhs_panel, T0, C);

    blB += NrCols * RhsProg;
    blA += MrPackets * LhsProg;
  }

  // ---- Store results: C[j + p * NrCols] -> res(i + p*ResPacketSz, j2 + j) ----
  alignas(ResPacketLocal) ResPacketLocal alphav = pset1<ResPacketLocal>(alpha);
  for (int j = 0; j < NrCols; ++j) {
    LinearMapper_ r = res.getLinearMapper(i, j2 + j);
    for (int p = 0; p < MrPackets; ++p) {
      alignas(ResPacketLocal) ResPacketLocal R = r.template loadPacket<ResPacketLocal>(p * ResPacketSz);
      traits.acc(C[j + p * NrCols], alphav, R);
      r.storePacket(p * ResPacketSz, R);
    }
  }
}

template <typename LhsScalar, typename RhsScalar, typename Index, typename DataMapper, int mr, int nr,
          bool ConjugateLhs, bool ConjugateRhs>
EIGEN_DONT_INLINE void gebp_kernel<LhsScalar, RhsScalar, Index, DataMapper, mr, nr, ConjugateLhs,
                                   ConjugateRhs>::operator()(const DataMapper& res, const LhsScalar* blockA,
                                                             const RhsScalar* blockB, Index rows, Index depth,
                                                             Index cols, ResScalar alpha, Index strideA, Index strideB,
                                                             Index offsetA, Index offsetB) const {
  Traits traits;
  SwappedTraits straits;

  if (strideA == -1) strideA = depth;
  if (strideB == -1) strideB = depth;
  conj_helper<LhsScalar, RhsScalar, ConjugateLhs, ConjugateRhs> cj;
  Index packet_cols4 = nr >= 4 ? (cols / 4) * 4 : 0;
  Index packet_cols8 = nr >= 8 ? (cols / 8) * 8 : 0;
  const Index peeled_mc3 = mr >= 3 * Traits::LhsProgress ? (rows / (3 * LhsProgress)) * (3 * LhsProgress) : 0;
  const Index peeled_mc2 =
      mr >= 2 * Traits::LhsProgress ? peeled_mc3 + ((rows - peeled_mc3) / (2 * LhsProgress)) * (2 * LhsProgress) : 0;
  const Index peeled_mc1 =
      mr >= 1 * Traits::LhsProgress ? peeled_mc2 + ((rows - peeled_mc2) / (1 * LhsProgress)) * (1 * LhsProgress) : 0;
  const Index peeled_mc_half =
      mr >= LhsProgressHalf ? peeled_mc1 + ((rows - peeled_mc1) / (LhsProgressHalf)) * (LhsProgressHalf) : 0;
  const Index peeled_mc_quarter =
      mr >= LhsProgressQuarter
          ? peeled_mc_half + ((rows - peeled_mc_half) / (LhsProgressQuarter)) * (LhsProgressQuarter)
          : 0;
  enum { pk = 8 };  // NOTE Such a large peeling factor is important for large matrices (~ +5% when >1000 on Haswell)
  const Index peeled_kc = depth & ~(pk - 1);
  const int prefetch_res_offset = 32 / sizeof(ResScalar);

  // Helper to invoke gebp_micro_panel_impl with the right types.
  // The always_inline attribute is critical: without it GCC outlines each
  // template instantiation of this generic lambda as a separate function,
  // adding call overhead that causes 10-17 % regressions in LLT/TRSM
  // for small-to-medium matrix sizes.
  auto micro_panel = [&](auto mrp_tag, auto nrc_tag, auto& local_traits, Index i, Index j2) EIGEN_LAMBDA_ALWAYS_INLINE {
    constexpr int MrP = decltype(mrp_tag)::value;
    constexpr int NrC = decltype(nrc_tag)::value;
    using LTraits = std::remove_reference_t<decltype(local_traits)>;
    gebp_micro_panel_impl<MrP, NrC, LTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                          LhsPacket>(local_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB, offsetA,
                                     offsetB, prefetch_res_offset, peeled_kc, pk);
  };

  // Budget (in bytes) for co-residency of the RHS block and a strip of the
  // LHS panel. On most architectures this is L1: the LHS streams sequentially
  // through L1 and we only need room for one micro-panel strip at a time.
  // Sub-blocking trades cache misses for extra passes over the RHS columns,
  // which hurts IPC and loop overhead. On modern x86, L1→L2 traffic is cheap
  // (~5 cycles) and hardware prefetchers absorb the LHS misses, so we use a
  // fraction of L2 instead — effectively disabling sub-blocking when the LHS
  // panel already fits in L2.
  Index lhs_budget;
  {
    std::ptrdiff_t l1, l2, l3;
    manage_caching_sizes(GetAction, &l1, &l2, &l3);
#if EIGEN_ARCH_i386_OR_x86_64
    lhs_budget = static_cast<Index>(l2 / 2);
#else
    lhs_budget = static_cast<Index>(l1);
#endif
  }

  //---------- Process 3 * LhsProgress rows at once ----------
  EIGEN_IF_CONSTEXPR(mr >= 3 * Traits::LhsProgress) {
    const Index rhs_block = sizeof(ResScalar) * mr * nr + depth * nr * sizeof(RhsScalar);
    const Index lhs_strip = depth * sizeof(LhsScalar) * 3 * LhsProgress;
    const Index lhs_avail = (lhs_budget > rhs_block) ? (lhs_budget - rhs_block) : 0;
    const Index actual_panel_rows = (lhs_avail >= peeled_mc3 * depth * static_cast<Index>(sizeof(LhsScalar)))
                                        ? peeled_mc3
                                        : (3 * LhsProgress) * std::max<Index>(1, lhs_avail / lhs_strip);
    for (Index i1 = 0; i1 < peeled_mc3; i1 += actual_panel_rows) {
      const Index actual_panel_end = (std::min)(i1 + actual_panel_rows, peeled_mc3);
      EIGEN_IF_CONSTEXPR(nr >= 8) {
        for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
          for (Index i = i1; i < actual_panel_end; i += 3 * LhsProgress) {
            micro_panel(fix<3>, fix<8>, traits, i, j2);
          }
        }
      }
      for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
        for (Index i = i1; i < actual_panel_end; i += 3 * LhsProgress) {
          micro_panel(fix<3>, fix<4>, traits, i, j2);
        }
      }
      for (Index j2 = packet_cols4; j2 < cols; j2++) {
        for (Index i = i1; i < actual_panel_end; i += 3 * LhsProgress) {
          micro_panel(fix<3>, fix<1>, traits, i, j2);
        }
      }
    }
  }

  //---------- Process 2 * LhsProgress rows at once ----------
  EIGEN_IF_CONSTEXPR(mr >= 2 * Traits::LhsProgress) {
    const Index rhs_block2 = sizeof(ResScalar) * mr * nr + depth * nr * sizeof(RhsScalar);
    const Index lhs_strip2 = depth * sizeof(LhsScalar) * 2 * LhsProgress;
    const Index lhs_avail2 = (lhs_budget > rhs_block2) ? (lhs_budget - rhs_block2) : 0;
    const Index mc2_range = peeled_mc2 - peeled_mc3;
    Index actual_panel_rows = (lhs_avail2 >= mc2_range * depth * static_cast<Index>(sizeof(LhsScalar)))
                                  ? mc2_range
                                  : (2 * LhsProgress) * std::max<Index>(1, lhs_avail2 / lhs_strip2);
    for (Index i1 = peeled_mc3; i1 < peeled_mc2; i1 += actual_panel_rows) {
      Index actual_panel_end = (std::min)(i1 + actual_panel_rows, peeled_mc2);
      EIGEN_IF_CONSTEXPR(nr >= 8) {
        for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
          for (Index i = i1; i < actual_panel_end; i += 2 * LhsProgress) {
            micro_panel(fix<2>, fix<8>, traits, i, j2);
          }
        }
      }
      for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
        for (Index i = i1; i < actual_panel_end; i += 2 * LhsProgress) {
          micro_panel(fix<2>, fix<4>, traits, i, j2);
        }
      }
      for (Index j2 = packet_cols4; j2 < cols; j2++) {
        for (Index i = i1; i < actual_panel_end; i += 2 * LhsProgress) {
          micro_panel(fix<2>, fix<1>, traits, i, j2);
        }
      }
    }
  }

  //---------- Process 1 * LhsProgress rows at once ----------
  EIGEN_IF_CONSTEXPR(mr >= 1 * Traits::LhsProgress) {
    for (Index i = peeled_mc2; i < peeled_mc1; i += LhsProgress) {
      EIGEN_IF_CONSTEXPR(nr >= 8) {
        for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
          micro_panel(fix<1>, fix<8>, traits, i, j2);
        }
      }
      for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
        micro_panel(fix<1>, fix<4>, traits, i, j2);
      }
      for (Index j2 = packet_cols4; j2 < cols; j2++) {
        micro_panel(fix<1>, fix<1>, traits, i, j2);
      }
    }
  }

  //---------- Process LhsProgressHalf rows at once ----------
  EIGEN_IF_CONSTEXPR((LhsProgressHalf < LhsProgress) && mr >= LhsProgressHalf) {
    HalfTraits half_traits;
    for (Index i = peeled_mc1; i < peeled_mc_half; i += LhsProgressHalf) {
      EIGEN_IF_CONSTEXPR(nr >= 8) {
        for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
          gebp_micro_panel_impl<1, 8, HalfTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                                LhsPacket>(half_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB,
                                           offsetA, offsetB, prefetch_res_offset, peeled_kc, pk);
        }
      }
      for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
        gebp_micro_panel_impl<1, 4, HalfTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                              LhsPacket>(half_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB,
                                         offsetA, offsetB, prefetch_res_offset, peeled_kc, pk);
      }
      for (Index j2 = packet_cols4; j2 < cols; j2++) {
        gebp_micro_panel_impl<1, 1, HalfTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                              LhsPacket>(half_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB,
                                         offsetA, offsetB, prefetch_res_offset, peeled_kc, pk);
      }
    }
  }

  //---------- Process LhsProgressQuarter rows at once ----------
  EIGEN_IF_CONSTEXPR((LhsProgressQuarter < LhsProgressHalf) && mr >= LhsProgressQuarter) {
    QuarterTraits quarter_traits;
    for (Index i = peeled_mc_half; i < peeled_mc_quarter; i += LhsProgressQuarter) {
      EIGEN_IF_CONSTEXPR(nr >= 8) {
        for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
          gebp_micro_panel_impl<1, 8, QuarterTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                                LhsPacket>(quarter_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB,
                                           offsetA, offsetB, prefetch_res_offset, peeled_kc, pk);
        }
      }
      for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
        gebp_micro_panel_impl<1, 4, QuarterTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                              LhsPacket>(quarter_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB,
                                         offsetA, offsetB, prefetch_res_offset, peeled_kc, pk);
      }
      for (Index j2 = packet_cols4; j2 < cols; j2++) {
        gebp_micro_panel_impl<1, 1, QuarterTraits, LhsScalar, RhsScalar, ResScalar, Index, DataMapper, LinearMapper,
                              LhsPacket>(quarter_traits, res, blockA, blockB, alpha, i, j2, depth, strideA, strideB,
                                         offsetA, offsetB, prefetch_res_offset, peeled_kc, pk);
      }
    }
  }

  //---------- Process remaining rows, 1 at once ----------
  if (peeled_mc_quarter < rows) {
    EIGEN_IF_CONSTEXPR(nr >= 8) {
      // loop on each panel of the rhs
      for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
        // loop on each row of the lhs (1*LhsProgress x depth)
        for (Index i = peeled_mc_quarter; i < rows; i += 1) {
          const LhsScalar* blA = &blockA[i * strideA + offsetA];
          prefetch(&blA[0]);
          // gets a 1 x 1 res block as registers
          ResScalar C0(0), C1(0), C2(0), C3(0), C4(0), C5(0), C6(0), C7(0);
          const RhsScalar* blB = &blockB[j2 * strideB + offsetB * 8];
          for (Index k = 0; k < depth; k++) {
            LhsScalar A0 = blA[k];
            RhsScalar B_0;

            B_0 = blB[0];
            C0 = cj.pmadd(A0, B_0, C0);

            B_0 = blB[1];
            C1 = cj.pmadd(A0, B_0, C1);

            B_0 = blB[2];
            C2 = cj.pmadd(A0, B_0, C2);

            B_0 = blB[3];
            C3 = cj.pmadd(A0, B_0, C3);

            B_0 = blB[4];
            C4 = cj.pmadd(A0, B_0, C4);

            B_0 = blB[5];
            C5 = cj.pmadd(A0, B_0, C5);

            B_0 = blB[6];
            C6 = cj.pmadd(A0, B_0, C6);

            B_0 = blB[7];
            C7 = cj.pmadd(A0, B_0, C7);

            blB += 8;
          }
          res(i, j2 + 0) += alpha * C0;
          res(i, j2 + 1) += alpha * C1;
          res(i, j2 + 2) += alpha * C2;
          res(i, j2 + 3) += alpha * C3;
          res(i, j2 + 4) += alpha * C4;
          res(i, j2 + 5) += alpha * C5;
          res(i, j2 + 6) += alpha * C6;
          res(i, j2 + 7) += alpha * C7;
        }
      }
    }

    for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
      // loop on each row of the lhs (1*LhsProgress x depth)
      for (Index i = peeled_mc_quarter; i < rows; i += 1) {
        const LhsScalar* blA = &blockA[i * strideA + offsetA];
        prefetch(&blA[0]);
        const RhsScalar* blB = &blockB[j2 * strideB + offsetB * 4];

        // If LhsProgress is 8 or 16, it assumes that there is a
        // half or quarter packet, respectively, of the same size as
        // nr (which is currently 4) for the return type.
        const int SResPacketHalfSize = unpacket_traits<typename unpacket_traits<SResPacket>::half>::size;
        const int SResPacketQuarterSize =
            unpacket_traits<typename unpacket_traits<typename unpacket_traits<SResPacket>::half>::half>::size;
        // The following code assumes we can load SRhsPacket in such a way that
        // it multiplies blocks of 4 elements in SLhsPacket.  This is not the
        // case for some customized kernels (i.e. NEON fp16).  If the assumption
        // fails, drop down to the scalar path.
        constexpr bool kCanLoadSRhsQuad =
            (unpacket_traits<SLhsPacket>::size < 4) ||
            (unpacket_traits<SRhsPacket>::size % ((std::max<int>)(unpacket_traits<SLhsPacket>::size, 4) / 4)) == 0;
        if (kCanLoadSRhsQuad && (SwappedTraits::LhsProgress % 4) == 0 && (SwappedTraits::LhsProgress <= 16) &&
            (SwappedTraits::LhsProgress != 8 || SResPacketHalfSize == nr) &&
            (SwappedTraits::LhsProgress != 16 || SResPacketQuarterSize == nr)) {
          SAccPacket C0, C1, C2, C3;
          straits.initAcc(C0);
          straits.initAcc(C1);
          straits.initAcc(C2);
          straits.initAcc(C3);

          const Index spk = (std::max)(1, SwappedTraits::LhsProgress / 4);
          const Index endk = (depth / spk) * spk;
          const Index endk4 = (depth / (spk * 4)) * (spk * 4);

          Index k = 0;
          for (; k < endk4; k += 4 * spk) {
            SLhsPacket A0, A1;
            SRhsPacket B_0, B_1;

            straits.loadLhsUnaligned(blB + 0 * SwappedTraits::LhsProgress, A0);
            straits.loadLhsUnaligned(blB + 1 * SwappedTraits::LhsProgress, A1);

            straits.loadRhsQuad(blA + 0 * spk, B_0);
            straits.loadRhsQuad(blA + 1 * spk, B_1);
            straits.madd(A0, B_0, C0, B_0, fix<0>);
            straits.madd(A1, B_1, C1, B_1, fix<0>);

            straits.loadLhsUnaligned(blB + 2 * SwappedTraits::LhsProgress, A0);
            straits.loadLhsUnaligned(blB + 3 * SwappedTraits::LhsProgress, A1);
            straits.loadRhsQuad(blA + 2 * spk, B_0);
            straits.loadRhsQuad(blA + 3 * spk, B_1);
            straits.madd(A0, B_0, C2, B_0, fix<0>);
            straits.madd(A1, B_1, C3, B_1, fix<0>);

            blB += 4 * SwappedTraits::LhsProgress;
            blA += 4 * spk;
          }
          C0 = padd(padd(C0, C1), padd(C2, C3));
          for (; k < endk; k += spk) {
            SLhsPacket A0;
            SRhsPacket B_0;

            straits.loadLhsUnaligned(blB, A0);
            straits.loadRhsQuad(blA, B_0);
            straits.madd(A0, B_0, C0, B_0, fix<0>);

            blB += SwappedTraits::LhsProgress;
            blA += spk;
          }
          if (SwappedTraits::LhsProgress == 8) {
            // Special case where we have to first reduce the accumulation register C0
            typedef std::conditional_t<SwappedTraits::LhsProgress >= 8, typename unpacket_traits<SResPacket>::half,
                                       SResPacket>
                SResPacketHalf;
            typedef std::conditional_t<SwappedTraits::LhsProgress >= 8, typename unpacket_traits<SLhsPacket>::half,
                                       SLhsPacket>
                SLhsPacketHalf;
            typedef std::conditional_t<SwappedTraits::LhsProgress >= 8, typename unpacket_traits<SRhsPacket>::half,
                                       SRhsPacket>
                SRhsPacketHalf;
            typedef std::conditional_t<SwappedTraits::LhsProgress >= 8, typename unpacket_traits<SAccPacket>::half,
                                       SAccPacket>
                SAccPacketHalf;

            SResPacketHalf R = res.template gatherPacket<SResPacketHalf>(i, j2);
            SResPacketHalf alphav = pset1<SResPacketHalf>(alpha);

            if (depth - endk > 0) {
              // We have to handle the last row of the rhs which corresponds to a half-packet
              SLhsPacketHalf a0;
              SRhsPacketHalf b0;
              straits.loadLhsUnaligned(blB, a0);
              straits.loadRhs(blA, b0);
              SAccPacketHalf c0 = predux_half(C0);
              straits.madd(a0, b0, c0, b0, fix<0>);
              straits.acc(c0, alphav, R);
            } else {
              straits.acc(predux_half(C0), alphav, R);
            }
            res.scatterPacket(i, j2, R);
          } else if (SwappedTraits::LhsProgress == 16) {
            // Special case where we have to first reduce the
            // accumulation register C0. We specialize the block in
            // template form, so that LhsProgress < 16 paths don't
            // fail to compile
            last_row_process_16_packets<LhsScalar, RhsScalar, Index, DataMapper, mr, nr, ConjugateLhs, ConjugateRhs> p;
            p(res, straits, blA, blB, depth, endk, i, j2, alpha, C0);
          } else {
            SResPacket R = res.template gatherPacket<SResPacket>(i, j2);
            SResPacket alphav = pset1<SResPacket>(alpha);
            straits.acc(C0, alphav, R);
            res.scatterPacket(i, j2, R);
          }
        } else  // scalar path
        {
          // get a 1 x 4 res block as registers
          ResScalar C0(0), C1(0), C2(0), C3(0);

          for (Index k = 0; k < depth; k++) {
            LhsScalar A0;
            RhsScalar B_0, B_1;

            A0 = blA[k];

            B_0 = blB[0];
            B_1 = blB[1];
            C0 = cj.pmadd(A0, B_0, C0);
            C1 = cj.pmadd(A0, B_1, C1);

            B_0 = blB[2];
            B_1 = blB[3];
            C2 = cj.pmadd(A0, B_0, C2);
            C3 = cj.pmadd(A0, B_1, C3);

            blB += 4;
          }
          res(i, j2 + 0) += alpha * C0;
          res(i, j2 + 1) += alpha * C1;
          res(i, j2 + 2) += alpha * C2;
          res(i, j2 + 3) += alpha * C3;
        }
      }
    }
    // remaining columns
    for (Index j2 = packet_cols4; j2 < cols; j2++) {
      // loop on each row of the lhs (1*LhsProgress x depth)
      for (Index i = peeled_mc_quarter; i < rows; i += 1) {
        const LhsScalar* blA = &blockA[i * strideA + offsetA];
        prefetch(&blA[0]);
        // gets a 1 x 1 res block as registers
        ResScalar C0(0);
        const RhsScalar* blB = &blockB[j2 * strideB + offsetB];
        for (Index k = 0; k < depth; k++) {
          LhsScalar A0 = blA[k];
          RhsScalar B_0 = blB[k];
          C0 = cj.pmadd(A0, B_0, C0);
        }
        res(i, j2) += alpha * C0;
      }
    }
  }
}

// pack a block of the lhs
// The traversal is as follow (mr==4):
//   0  4  8 12 ...
//   1  5  9 13 ...
//   2  6 10 14 ...
//   3  7 11 15 ...
//
//  16 20 24 28 ...
//  17 21 25 29 ...
//  18 22 26 30 ...
//  19 23 27 31 ...
//
//  32 33 34 35 ...
//  36 36 38 39 ...
template <typename Scalar, typename Index, typename DataMapper, int Pack1, int Pack2, typename Packet, bool Conjugate,
          bool PanelMode>
struct gemm_pack_lhs<Scalar, Index, DataMapper, Pack1, Pack2, Packet, ColMajor, Conjugate, PanelMode> {
  typedef typename DataMapper::LinearMapper LinearMapper;
  EIGEN_DONT_INLINE void operator()(Scalar* blockA, const DataMapper& lhs, Index depth, Index rows, Index stride = 0,
                                    Index offset = 0) const;
};

template <typename Scalar, typename Index, typename DataMapper, int Pack1, int Pack2, typename Packet, bool Conjugate,
          bool PanelMode>
EIGEN_DONT_INLINE void gemm_pack_lhs<Scalar, Index, DataMapper, Pack1, Pack2, Packet, ColMajor, Conjugate,
                                     PanelMode>::operator()(Scalar* blockA, const DataMapper& lhs, Index depth,
                                                            Index rows, Index stride, Index offset) const {
  typedef typename unpacket_traits<Packet>::half HalfPacket;
  typedef typename unpacket_traits<typename unpacket_traits<Packet>::half>::half QuarterPacket;
  enum {
    PacketSize = unpacket_traits<Packet>::size,
    HalfPacketSize = unpacket_traits<HalfPacket>::size,
    QuarterPacketSize = unpacket_traits<QuarterPacket>::size,
    HasHalf = (int)HalfPacketSize < (int)PacketSize,
    HasQuarter = (int)QuarterPacketSize < (int)HalfPacketSize
  };

  EIGEN_ASM_COMMENT("EIGEN PRODUCT PACK LHS");
  EIGEN_UNUSED_VARIABLE(stride);
  EIGEN_UNUSED_VARIABLE(offset);
  eigen_assert(((!PanelMode) && stride == 0 && offset == 0) || (PanelMode && stride >= depth && offset <= stride));
  eigen_assert(((Pack1 % PacketSize) == 0 && Pack1 <= 4 * PacketSize) || (Pack1 <= 4) || (Pack1 < PacketSize));
  conj_if<NumTraits<Scalar>::IsComplex && Conjugate> cj;
  Index count = 0;

  const Index peeled_mc3 = Pack1 >= 3 * PacketSize ? (rows / (3 * PacketSize)) * (3 * PacketSize) : 0;
  const Index peeled_mc2 =
      Pack1 >= 2 * PacketSize ? peeled_mc3 + ((rows - peeled_mc3) / (2 * PacketSize)) * (2 * PacketSize) : 0;
  const Index peeled_mc1 =
      Pack1 >= 1 * PacketSize ? peeled_mc2 + ((rows - peeled_mc2) / (1 * PacketSize)) * (1 * PacketSize) : 0;
  const Index peeled_mc_half =
      Pack1 >= HalfPacketSize ? peeled_mc1 + ((rows - peeled_mc1) / (HalfPacketSize)) * (HalfPacketSize) : 0;
  const Index peeled_mc_quarter = Pack1 >= QuarterPacketSize ? (rows / (QuarterPacketSize)) * (QuarterPacketSize) : 0;
  const Index last_lhs_progress = rows > peeled_mc_quarter ? (rows - peeled_mc_quarter) & ~1 : 0;
  const Index peeled_mc0 = Pack2 >= PacketSize              ? peeled_mc_quarter
                           : Pack2 > 1 && last_lhs_progress ? (rows / last_lhs_progress) * last_lhs_progress
                                                            : 0;

  Index i = 0;

  // Pack 3 packets
  if (Pack1 >= 3 * PacketSize) {
    for (; i < peeled_mc3; i += 3 * PacketSize) {
      if (PanelMode) count += (3 * PacketSize) * offset;

      for (Index k = 0; k < depth; k++) {
        Packet A, B, C;
        A = lhs.template loadPacket<Packet>(i + 0 * PacketSize, k);
        B = lhs.template loadPacket<Packet>(i + 1 * PacketSize, k);
        C = lhs.template loadPacket<Packet>(i + 2 * PacketSize, k);
        pstore(blockA + count, cj.pconj(A));
        count += PacketSize;
        pstore(blockA + count, cj.pconj(B));
        count += PacketSize;
        pstore(blockA + count, cj.pconj(C));
        count += PacketSize;
      }
      if (PanelMode) count += (3 * PacketSize) * (stride - offset - depth);
    }
  }
  // Pack 2 packets
  if (Pack1 >= 2 * PacketSize) {
    for (; i < peeled_mc2; i += 2 * PacketSize) {
      if (PanelMode) count += (2 * PacketSize) * offset;

      for (Index k = 0; k < depth; k++) {
        Packet A, B;
        A = lhs.template loadPacket<Packet>(i + 0 * PacketSize, k);
        B = lhs.template loadPacket<Packet>(i + 1 * PacketSize, k);
        pstore(blockA + count, cj.pconj(A));
        count += PacketSize;
        pstore(blockA + count, cj.pconj(B));
        count += PacketSize;
      }
      if (PanelMode) count += (2 * PacketSize) * (stride - offset - depth);
    }
  }
  // Pack 1 packets
  if (Pack1 >= 1 * PacketSize) {
    for (; i < peeled_mc1; i += 1 * PacketSize) {
      if (PanelMode) count += (1 * PacketSize) * offset;

      for (Index k = 0; k < depth; k++) {
        Packet A;
        A = lhs.template loadPacket<Packet>(i + 0 * PacketSize, k);
        pstore(blockA + count, cj.pconj(A));
        count += PacketSize;
      }
      if (PanelMode) count += (1 * PacketSize) * (stride - offset - depth);
    }
  }
  // Pack half packets
  if (HasHalf && Pack1 >= HalfPacketSize) {
    for (; i < peeled_mc_half; i += HalfPacketSize) {
      if (PanelMode) count += (HalfPacketSize)*offset;

      for (Index k = 0; k < depth; k++) {
        HalfPacket A;
        A = lhs.template loadPacket<HalfPacket>(i + 0 * (HalfPacketSize), k);
        pstoreu(blockA + count, cj.pconj(A));
        count += HalfPacketSize;
      }
      if (PanelMode) count += (HalfPacketSize) * (stride - offset - depth);
    }
  }
  // Pack quarter packets
  if (HasQuarter && Pack1 >= QuarterPacketSize) {
    for (; i < peeled_mc_quarter; i += QuarterPacketSize) {
      if (PanelMode) count += (QuarterPacketSize)*offset;

      for (Index k = 0; k < depth; k++) {
        QuarterPacket A;
        A = lhs.template loadPacket<QuarterPacket>(i + 0 * (QuarterPacketSize), k);
        pstoreu(blockA + count, cj.pconj(A));
        count += QuarterPacketSize;
      }
      if (PanelMode) count += (QuarterPacketSize) * (stride - offset - depth);
    }
  }
  // Pack2 may be *smaller* than PacketSize—that happens for
  // products like real * complex, where we have to go half the
  // progress on the lhs in order to duplicate those operands to
  // address both real & imaginary parts on the rhs. This portion will
  // pack those half ones until they match the number expected on the
  // last peeling loop at this point (for the rhs).
  //
  // When there are no half/quarter packet types (HasHalf and HasQuarter
  // are both false), last_lhs_progress can exceed Pack2, producing
  // interleaved groups that the GEBP micro-kernel cannot consume.  In
  // that case we use exactly Pack2 rows per group so the kernel's main
  // loop (which reads Pack2 = LhsProgress values via ploaddup) can
  // handle them; remaining rows fall through to the scalar loop below.
  if (Pack2 < PacketSize && Pack2 > 1) {
    const Index pack2_progress = (HasHalf || HasQuarter) ? last_lhs_progress : Pack2;
    const Index peeled = (HasHalf || HasQuarter) ? peeled_mc0 : (rows / Pack2) * Pack2;
    for (; i < peeled; i += pack2_progress) {
      if (PanelMode) count += pack2_progress * offset;

      for (Index k = 0; k < depth; k++)
        for (Index w = 0; w < pack2_progress; w++) blockA[count++] = cj(lhs(i + w, k));

      if (PanelMode) count += pack2_progress * (stride - offset - depth);
    }
  }
  // Pack scalars
  for (; i < rows; i++) {
    if (PanelMode) count += offset;
    for (Index k = 0; k < depth; k++) blockA[count++] = cj(lhs(i, k));
    if (PanelMode) count += (stride - offset - depth);
  }
}

template <typename Scalar, typename Index, typename DataMapper, int Pack1, int Pack2, typename Packet, bool Conjugate,
          bool PanelMode>
struct gemm_pack_lhs<Scalar, Index, DataMapper, Pack1, Pack2, Packet, RowMajor, Conjugate, PanelMode> {
  typedef typename DataMapper::LinearMapper LinearMapper;
  EIGEN_DONT_INLINE void operator()(Scalar* blockA, const DataMapper& lhs, Index depth, Index rows, Index stride = 0,
                                    Index offset = 0) const;
};

template <typename Scalar, typename Index, typename DataMapper, int Pack1, int Pack2, typename Packet, bool Conjugate,
          bool PanelMode>
EIGEN_DONT_INLINE void gemm_pack_lhs<Scalar, Index, DataMapper, Pack1, Pack2, Packet, RowMajor, Conjugate,
                                     PanelMode>::operator()(Scalar* blockA, const DataMapper& lhs, Index depth,
                                                            Index rows, Index stride, Index offset) const {
  typedef typename unpacket_traits<Packet>::half HalfPacket;
  typedef typename unpacket_traits<typename unpacket_traits<Packet>::half>::half QuarterPacket;
  enum {
    PacketSize = unpacket_traits<Packet>::size,
    HalfPacketSize = unpacket_traits<HalfPacket>::size,
    QuarterPacketSize = unpacket_traits<QuarterPacket>::size,
    HasHalf = (int)HalfPacketSize < (int)PacketSize,
    HasQuarter = (int)QuarterPacketSize < (int)HalfPacketSize
  };

  EIGEN_ASM_COMMENT("EIGEN PRODUCT PACK LHS");
  EIGEN_UNUSED_VARIABLE(stride);
  EIGEN_UNUSED_VARIABLE(offset);
  eigen_assert(((!PanelMode) && stride == 0 && offset == 0) || (PanelMode && stride >= depth && offset <= stride));
  conj_if<NumTraits<Scalar>::IsComplex && Conjugate> cj;
  Index count = 0;
  bool gone_half = false, gone_quarter = false, gone_last = false;

  Index i = 0;
  Index pack = Pack1;
  Index psize = PacketSize;
  while (pack > 0) {
    Index remaining_rows = rows - i;
    Index peeled_mc = gone_last ? Pack2 > 1 ? (rows / pack) * pack : 0 : i + (remaining_rows / pack) * pack;
    Index starting_pos = i;
    for (; i < peeled_mc; i += pack) {
      if (PanelMode) count += pack * offset;

      Index k = 0;
      if (pack >= psize && psize >= QuarterPacketSize) {
        const Index peeled_k = (depth / psize) * psize;
        for (; k < peeled_k; k += psize) {
          for (Index m = 0; m < pack; m += psize) {
            if (psize == PacketSize) {
              PacketBlock<Packet> kernel;
              for (Index p = 0; p < psize; ++p) kernel.packet[p] = lhs.template loadPacket<Packet>(i + p + m, k);
              ptranspose(kernel);
              for (Index p = 0; p < psize; ++p) pstore(blockA + count + m + (pack)*p, cj.pconj(kernel.packet[p]));
            } else if (HasHalf && psize == HalfPacketSize) {
              gone_half = true;
              PacketBlock<HalfPacket> kernel_half;
              for (Index p = 0; p < psize; ++p)
                kernel_half.packet[p] = lhs.template loadPacket<HalfPacket>(i + p + m, k);
              ptranspose(kernel_half);
              for (Index p = 0; p < psize; ++p) pstore(blockA + count + m + (pack)*p, cj.pconj(kernel_half.packet[p]));
            } else if (HasQuarter && psize == QuarterPacketSize) {
              gone_quarter = true;
              PacketBlock<QuarterPacket> kernel_quarter;
              for (Index p = 0; p < psize; ++p)
                kernel_quarter.packet[p] = lhs.template loadPacket<QuarterPacket>(i + p + m, k);
              ptranspose(kernel_quarter);
              for (Index p = 0; p < psize; ++p)
                pstore(blockA + count + m + (pack)*p, cj.pconj(kernel_quarter.packet[p]));
            }
          }
          count += psize * pack;
        }
      }

      for (; k < depth; k++) {
        Index w = 0;
        for (; w < pack - 3; w += 4) {
          Scalar a(cj(lhs(i + w + 0, k))), b(cj(lhs(i + w + 1, k))), c(cj(lhs(i + w + 2, k))), d(cj(lhs(i + w + 3, k)));
          blockA[count++] = a;
          blockA[count++] = b;
          blockA[count++] = c;
          blockA[count++] = d;
        }
        if (pack % 4)
          for (; w < pack; ++w) blockA[count++] = cj(lhs(i + w, k));
      }

      if (PanelMode) count += pack * (stride - offset - depth);
    }

    pack -= psize;
    Index left = rows - i;
    if (pack <= 0) {
      if (!gone_last && (starting_pos == i || left >= psize / 2 || left >= psize / 4) &&
          ((psize / 2 == HalfPacketSize && HasHalf && !gone_half) ||
           (psize / 2 == QuarterPacketSize && HasQuarter && !gone_quarter))) {
        psize /= 2;
        pack = psize;
        continue;
      }
      // Pack2 may be *smaller* than PacketSize—that happens for
      // products like real * complex, where we have to go half the
      // progress on the lhs in order to duplicate those operands to
      // address both real & imaginary parts on the rhs. This portion will
      // pack those half ones until they match the number expected on the
      // last peeling loop at this point (for the rhs).
      //
      // When there are no half/quarter packet types (HasHalf and HasQuarter
      // are both false), last_lhs_progress can exceed Pack2, producing
      // interleaved groups that the GEBP micro-kernel cannot consume.  In
      // that case we use exactly Pack2 rows per group so the kernel's main
      // loop (which reads Pack2 = LhsProgress values via ploaddup) can
      // handle them; remaining rows fall through to the scalar loop below.
      if (Pack2 < PacketSize && !gone_last) {
        gone_last = true;
        psize = pack = (HasHalf || HasQuarter) ? (left & ~1) : Pack2;
      }
    }
  }

  for (; i < rows; i++) {
    if (PanelMode) count += offset;
    for (Index k = 0; k < depth; k++) blockA[count++] = cj(lhs(i, k));
    if (PanelMode) count += (stride - offset - depth);
  }
}

// copy a complete panel of the rhs
// this version is optimized for column major matrices
// The traversal order is as follow: (nr==4):
//  0  1  2  3   12 13 14 15   24 27
//  4  5  6  7   16 17 18 19   25 28
//  8  9 10 11   20 21 22 23   26 29
//  .  .  .  .    .  .  .  .    .  .
template <typename Scalar, typename Index, typename DataMapper, int nr, bool Conjugate, bool PanelMode>
struct gemm_pack_rhs<Scalar, Index, DataMapper, nr, ColMajor, Conjugate, PanelMode> {
  typedef typename packet_traits<Scalar>::type Packet;
  typedef typename DataMapper::LinearMapper LinearMapper;
  enum { PacketSize = packet_traits<Scalar>::size };
  EIGEN_DONT_INLINE void operator()(Scalar* blockB, const DataMapper& rhs, Index depth, Index cols, Index stride = 0,
                                    Index offset = 0) const;
};

template <typename Scalar, typename Index, typename DataMapper, int nr, bool Conjugate, bool PanelMode>
EIGEN_DONT_INLINE void gemm_pack_rhs<Scalar, Index, DataMapper, nr, ColMajor, Conjugate, PanelMode>::operator()(
    Scalar* blockB, const DataMapper& rhs, Index depth, Index cols, Index stride, Index offset) const {
  EIGEN_ASM_COMMENT("EIGEN PRODUCT PACK RHS COLMAJOR");
  EIGEN_UNUSED_VARIABLE(stride);
  EIGEN_UNUSED_VARIABLE(offset);
  eigen_assert(((!PanelMode) && stride == 0 && offset == 0) || (PanelMode && stride >= depth && offset <= stride));
  conj_if<NumTraits<Scalar>::IsComplex && Conjugate> cj;
  Index packet_cols8 = nr >= 8 ? (cols / 8) * 8 : 0;
  Index packet_cols4 = nr >= 4 ? (cols / 4) * 4 : 0;
  Index count = 0;
  const Index peeled_k = (depth / PacketSize) * PacketSize;

  EIGEN_IF_CONSTEXPR(nr >= 8) {
    for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
      // skip what we have before
      if (PanelMode) count += 8 * offset;
      const LinearMapper dm0 = rhs.getLinearMapper(0, j2 + 0);
      const LinearMapper dm1 = rhs.getLinearMapper(0, j2 + 1);
      const LinearMapper dm2 = rhs.getLinearMapper(0, j2 + 2);
      const LinearMapper dm3 = rhs.getLinearMapper(0, j2 + 3);
      const LinearMapper dm4 = rhs.getLinearMapper(0, j2 + 4);
      const LinearMapper dm5 = rhs.getLinearMapper(0, j2 + 5);
      const LinearMapper dm6 = rhs.getLinearMapper(0, j2 + 6);
      const LinearMapper dm7 = rhs.getLinearMapper(0, j2 + 7);
      Index k = 0;
      if (PacketSize % 2 == 0 && PacketSize <= 8)  // 2 4 8
      {
        for (; k < peeled_k; k += PacketSize) {
          if (PacketSize == 2) {
            PacketBlock<Packet, PacketSize == 2 ? 2 : PacketSize> kernel0, kernel1, kernel2, kernel3;
            kernel0.packet[0 % PacketSize] = dm0.template loadPacket<Packet>(k);
            kernel0.packet[1 % PacketSize] = dm1.template loadPacket<Packet>(k);
            kernel1.packet[0 % PacketSize] = dm2.template loadPacket<Packet>(k);
            kernel1.packet[1 % PacketSize] = dm3.template loadPacket<Packet>(k);
            kernel2.packet[0 % PacketSize] = dm4.template loadPacket<Packet>(k);
            kernel2.packet[1 % PacketSize] = dm5.template loadPacket<Packet>(k);
            kernel3.packet[0 % PacketSize] = dm6.template loadPacket<Packet>(k);
            kernel3.packet[1 % PacketSize] = dm7.template loadPacket<Packet>(k);
            ptranspose(kernel0);
            ptranspose(kernel1);
            ptranspose(kernel2);
            ptranspose(kernel3);

            pstoreu(blockB + count + 0 * PacketSize, cj.pconj(kernel0.packet[0 % PacketSize]));
            pstoreu(blockB + count + 1 * PacketSize, cj.pconj(kernel1.packet[0 % PacketSize]));
            pstoreu(blockB + count + 2 * PacketSize, cj.pconj(kernel2.packet[0 % PacketSize]));
            pstoreu(blockB + count + 3 * PacketSize, cj.pconj(kernel3.packet[0 % PacketSize]));

            pstoreu(blockB + count + 4 * PacketSize, cj.pconj(kernel0.packet[1 % PacketSize]));
            pstoreu(blockB + count + 5 * PacketSize, cj.pconj(kernel1.packet[1 % PacketSize]));
            pstoreu(blockB + count + 6 * PacketSize, cj.pconj(kernel2.packet[1 % PacketSize]));
            pstoreu(blockB + count + 7 * PacketSize, cj.pconj(kernel3.packet[1 % PacketSize]));
            count += 8 * PacketSize;
          } else if (PacketSize == 4) {
            PacketBlock<Packet, PacketSize == 4 ? 4 : PacketSize> kernel0, kernel1;

            kernel0.packet[0 % PacketSize] = dm0.template loadPacket<Packet>(k);
            kernel0.packet[1 % PacketSize] = dm1.template loadPacket<Packet>(k);
            kernel0.packet[2 % PacketSize] = dm2.template loadPacket<Packet>(k);
            kernel0.packet[3 % PacketSize] = dm3.template loadPacket<Packet>(k);
            kernel1.packet[0 % PacketSize] = dm4.template loadPacket<Packet>(k);
            kernel1.packet[1 % PacketSize] = dm5.template loadPacket<Packet>(k);
            kernel1.packet[2 % PacketSize] = dm6.template loadPacket<Packet>(k);
            kernel1.packet[3 % PacketSize] = dm7.template loadPacket<Packet>(k);
            ptranspose(kernel0);
            ptranspose(kernel1);

            pstoreu(blockB + count + 0 * PacketSize, cj.pconj(kernel0.packet[0 % PacketSize]));
            pstoreu(blockB + count + 1 * PacketSize, cj.pconj(kernel1.packet[0 % PacketSize]));
            pstoreu(blockB + count + 2 * PacketSize, cj.pconj(kernel0.packet[1 % PacketSize]));
            pstoreu(blockB + count + 3 * PacketSize, cj.pconj(kernel1.packet[1 % PacketSize]));
            pstoreu(blockB + count + 4 * PacketSize, cj.pconj(kernel0.packet[2 % PacketSize]));
            pstoreu(blockB + count + 5 * PacketSize, cj.pconj(kernel1.packet[2 % PacketSize]));
            pstoreu(blockB + count + 6 * PacketSize, cj.pconj(kernel0.packet[3 % PacketSize]));
            pstoreu(blockB + count + 7 * PacketSize, cj.pconj(kernel1.packet[3 % PacketSize]));
            count += 8 * PacketSize;
          } else if (PacketSize == 8) {
            PacketBlock<Packet, PacketSize == 8 ? 8 : PacketSize> kernel0;

            kernel0.packet[0 % PacketSize] = dm0.template loadPacket<Packet>(k);
            kernel0.packet[1 % PacketSize] = dm1.template loadPacket<Packet>(k);
            kernel0.packet[2 % PacketSize] = dm2.template loadPacket<Packet>(k);
            kernel0.packet[3 % PacketSize] = dm3.template loadPacket<Packet>(k);
            kernel0.packet[4 % PacketSize] = dm4.template loadPacket<Packet>(k);
            kernel0.packet[5 % PacketSize] = dm5.template loadPacket<Packet>(k);
            kernel0.packet[6 % PacketSize] = dm6.template loadPacket<Packet>(k);
            kernel0.packet[7 % PacketSize] = dm7.template loadPacket<Packet>(k);
            ptranspose(kernel0);

            pstoreu(blockB + count + 0 * PacketSize, cj.pconj(kernel0.packet[0 % PacketSize]));
            pstoreu(blockB + count + 1 * PacketSize, cj.pconj(kernel0.packet[1 % PacketSize]));
            pstoreu(blockB + count + 2 * PacketSize, cj.pconj(kernel0.packet[2 % PacketSize]));
            pstoreu(blockB + count + 3 * PacketSize, cj.pconj(kernel0.packet[3 % PacketSize]));
            pstoreu(blockB + count + 4 * PacketSize, cj.pconj(kernel0.packet[4 % PacketSize]));
            pstoreu(blockB + count + 5 * PacketSize, cj.pconj(kernel0.packet[5 % PacketSize]));
            pstoreu(blockB + count + 6 * PacketSize, cj.pconj(kernel0.packet[6 % PacketSize]));
            pstoreu(blockB + count + 7 * PacketSize, cj.pconj(kernel0.packet[7 % PacketSize]));
            count += 8 * PacketSize;
          }
        }
      }

      for (; k < depth; k++) {
        blockB[count + 0] = cj(dm0(k));
        blockB[count + 1] = cj(dm1(k));
        blockB[count + 2] = cj(dm2(k));
        blockB[count + 3] = cj(dm3(k));
        blockB[count + 4] = cj(dm4(k));
        blockB[count + 5] = cj(dm5(k));
        blockB[count + 6] = cj(dm6(k));
        blockB[count + 7] = cj(dm7(k));
        count += 8;
      }
      // skip what we have after
      if (PanelMode) count += 8 * (stride - offset - depth);
    }
  }

  EIGEN_IF_CONSTEXPR(nr >= 4) {
    for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
      // skip what we have before
      if (PanelMode) count += 4 * offset;
      const LinearMapper dm0 = rhs.getLinearMapper(0, j2 + 0);
      const LinearMapper dm1 = rhs.getLinearMapper(0, j2 + 1);
      const LinearMapper dm2 = rhs.getLinearMapper(0, j2 + 2);
      const LinearMapper dm3 = rhs.getLinearMapper(0, j2 + 3);

      Index k = 0;
      EIGEN_IF_CONSTEXPR((PacketSize % 4) == 0 || PacketSize == 2) {
        for (; k < peeled_k; k += PacketSize) {
          PacketBlock<Packet, 4> kernel;
          kernel.packet[0] = dm0.template loadPacket<Packet>(k);
          kernel.packet[1] = dm1.template loadPacket<Packet>(k);
          kernel.packet[2] = dm2.template loadPacket<Packet>(k);
          kernel.packet[3] = dm3.template loadPacket<Packet>(k);
          EIGEN_IF_CONSTEXPR(PacketSize == 2) {
            // For PacketSize==2 we cannot ptranspose 4 packets directly; compose two
            // 2-packet transposes and re-interleave so the 4 stores produce the
            // packed-rhs layout (each store writing one half-row of the panel).
            PacketBlock<Packet, 2> tmp01;
            tmp01.packet[0] = kernel.packet[0];
            tmp01.packet[1] = kernel.packet[1];
            ptranspose(tmp01);
            PacketBlock<Packet, 2> tmp23;
            tmp23.packet[0] = kernel.packet[2];
            tmp23.packet[1] = kernel.packet[3];
            ptranspose(tmp23);
            kernel.packet[0] = tmp01.packet[0];
            kernel.packet[1] = tmp23.packet[0];
            kernel.packet[2] = tmp01.packet[1];
            kernel.packet[3] = tmp23.packet[1];
          }
          else {
            ptranspose(kernel);
          }
          pstoreu(blockB + count + 0 * PacketSize, cj.pconj(kernel.packet[0]));
          pstoreu(blockB + count + 1 * PacketSize, cj.pconj(kernel.packet[1]));
          pstoreu(blockB + count + 2 * PacketSize, cj.pconj(kernel.packet[2]));
          pstoreu(blockB + count + 3 * PacketSize, cj.pconj(kernel.packet[3]));
          count += 4 * PacketSize;
        }
      }
      for (; k < depth; k++) {
        blockB[count + 0] = cj(dm0(k));
        blockB[count + 1] = cj(dm1(k));
        blockB[count + 2] = cj(dm2(k));
        blockB[count + 3] = cj(dm3(k));
        count += 4;
      }
      // skip what we have after
      if (PanelMode) count += 4 * (stride - offset - depth);
    }
  }

  // copy the remaining columns one at a time (nr==1)
  for (Index j2 = packet_cols4; j2 < cols; ++j2) {
    if (PanelMode) count += offset;
    const LinearMapper dm0 = rhs.getLinearMapper(0, j2);
    for (Index k = 0; k < depth; k++) {
      blockB[count] = cj(dm0(k));
      count += 1;
    }
    if (PanelMode) count += (stride - offset - depth);
  }
}

// this version is optimized for row major matrices
template <typename Scalar, typename Index, typename DataMapper, int nr, bool Conjugate, bool PanelMode>
struct gemm_pack_rhs<Scalar, Index, DataMapper, nr, RowMajor, Conjugate, PanelMode> {
  typedef typename packet_traits<Scalar>::type Packet;
  typedef typename unpacket_traits<Packet>::half HalfPacket;
  typedef typename unpacket_traits<typename unpacket_traits<Packet>::half>::half QuarterPacket;
  typedef typename DataMapper::LinearMapper LinearMapper;
  enum {
    PacketSize = packet_traits<Scalar>::size,
    HalfPacketSize = unpacket_traits<HalfPacket>::size,
    QuarterPacketSize = unpacket_traits<QuarterPacket>::size
  };
  EIGEN_DONT_INLINE void operator()(Scalar* blockB, const DataMapper& rhs, Index depth, Index cols, Index stride = 0,
                                    Index offset = 0) const {
    EIGEN_ASM_COMMENT("EIGEN PRODUCT PACK RHS ROWMAJOR");
    EIGEN_UNUSED_VARIABLE(stride);
    EIGEN_UNUSED_VARIABLE(offset);
    eigen_assert(((!PanelMode) && stride == 0 && offset == 0) || (PanelMode && stride >= depth && offset <= stride));
    const bool HasHalf = (int)HalfPacketSize < (int)PacketSize;
    const bool HasQuarter = (int)QuarterPacketSize < (int)HalfPacketSize;
    conj_if<NumTraits<Scalar>::IsComplex && Conjugate> cj;
    Index packet_cols8 = nr >= 8 ? (cols / 8) * 8 : 0;
    Index packet_cols4 = nr >= 4 ? (cols / 4) * 4 : 0;
    Index count = 0;

    EIGEN_IF_CONSTEXPR(nr >= 8) {
      for (Index j2 = 0; j2 < packet_cols8; j2 += 8) {
        // skip what we have before
        if (PanelMode) count += 8 * offset;
        for (Index k = 0; k < depth; k++) {
          if (PacketSize == 8) {
            Packet A = rhs.template loadPacket<Packet>(k, j2);
            pstoreu(blockB + count, cj.pconj(A));
            count += PacketSize;
          } else if (PacketSize == 4) {
            Packet A = rhs.template loadPacket<Packet>(k, j2);
            Packet B = rhs.template loadPacket<Packet>(k, j2 + 4);
            pstoreu(blockB + count, cj.pconj(A));
            pstoreu(blockB + count + PacketSize, cj.pconj(B));
            count += 2 * PacketSize;
          } else {
            const LinearMapper dm0 = rhs.getLinearMapper(k, j2);
            blockB[count + 0] = cj(dm0(0));
            blockB[count + 1] = cj(dm0(1));
            blockB[count + 2] = cj(dm0(2));
            blockB[count + 3] = cj(dm0(3));
            blockB[count + 4] = cj(dm0(4));
            blockB[count + 5] = cj(dm0(5));
            blockB[count + 6] = cj(dm0(6));
            blockB[count + 7] = cj(dm0(7));
            count += 8;
          }
        }
        // skip what we have after
        if (PanelMode) count += 8 * (stride - offset - depth);
      }
    }

    if (nr >= 4) {
      for (Index j2 = packet_cols8; j2 < packet_cols4; j2 += 4) {
        // skip what we have before
        if (PanelMode) count += 4 * offset;
        for (Index k = 0; k < depth; k++) {
          if (PacketSize == 4) {
            Packet A = rhs.template loadPacket<Packet>(k, j2);
            pstoreu(blockB + count, cj.pconj(A));
            count += PacketSize;
          } else if (HasHalf && HalfPacketSize == 4) {
            HalfPacket A = rhs.template loadPacket<HalfPacket>(k, j2);
            pstoreu(blockB + count, cj.pconj(A));
            count += HalfPacketSize;
          } else if (HasQuarter && QuarterPacketSize == 4) {
            QuarterPacket A = rhs.template loadPacket<QuarterPacket>(k, j2);
            pstoreu(blockB + count, cj.pconj(A));
            count += QuarterPacketSize;
          } else {
            const LinearMapper dm0 = rhs.getLinearMapper(k, j2);
            blockB[count + 0] = cj(dm0(0));
            blockB[count + 1] = cj(dm0(1));
            blockB[count + 2] = cj(dm0(2));
            blockB[count + 3] = cj(dm0(3));
            count += 4;
          }
        }
        // skip what we have after
        if (PanelMode) count += 4 * (stride - offset - depth);
      }
    }
    // copy the remaining columns one at a time (nr==1)
    for (Index j2 = packet_cols4; j2 < cols; ++j2) {
      if (PanelMode) count += offset;
      for (Index k = 0; k < depth; k++) {
        blockB[count] = cj(rhs(k, j2));
        count += 1;
      }
      if (PanelMode) count += stride - offset - depth;
    }
  }
};

}  // end namespace internal

/** \returns the currently set level 1 cpu cache size (in bytes) used to estimate the ideal blocking size parameters.
 * \sa setCpuCacheSize */
inline std::ptrdiff_t l1CacheSize() {
  std::ptrdiff_t l1, l2, l3;
  internal::manage_caching_sizes(GetAction, &l1, &l2, &l3);
  return l1;
}

/** \returns the currently set level 2 cpu cache size (in bytes) used to estimate the ideal blocking size parameters.
 * \sa setCpuCacheSize */
inline std::ptrdiff_t l2CacheSize() {
  std::ptrdiff_t l1, l2, l3;
  internal::manage_caching_sizes(GetAction, &l1, &l2, &l3);
  return l2;
}

/** \returns the currently set level 3 cpu cache size (in bytes) used to estimate the ideal blocking size parameters.
 * \sa setCpuCacheSize */
inline std::ptrdiff_t l3CacheSize() {
  std::ptrdiff_t l1, l2, l3;
  internal::manage_caching_sizes(GetAction, &l1, &l2, &l3);
  return l3;
}

/** Set the cpu L1 and L2 cache sizes (in bytes).
 * These values are use to adjust the size of the blocks
 * for the algorithms working per blocks.
 *
 * \sa computeProductBlockingSizes */
inline void setCpuCacheSizes(std::ptrdiff_t l1, std::ptrdiff_t l2, std::ptrdiff_t l3) {
  internal::manage_caching_sizes(SetAction, &l1, &l2, &l3);
}

}  // end namespace Eigen

#if EIGEN_COMP_MSVC
#pragma warning(pop)
#endif

#endif  // EIGEN_GENERAL_BLOCK_PANEL_H
