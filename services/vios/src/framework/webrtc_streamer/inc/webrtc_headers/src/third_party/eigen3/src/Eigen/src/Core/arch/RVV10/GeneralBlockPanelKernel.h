// This file is part of Eigen, a lightweight C template library
// for linear algebra.
//
// Copyright (C) 2024 Kseniya Zaytseva <kseniya.zaytseva@syntacore.com>
// Copyright (C) 2025 Chip Kerchner <ckerchner@tenstorrent.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_RVV10_GENERAL_BLOCK_KERNEL_H
#define EIGEN_RVV10_GENERAL_BLOCK_KERNEL_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/********************************* real ************************************/

template <>
struct gebp_traits<float, float, false, false, Architecture::RVV10, GEBPPacketFull>
    : gebp_traits<float, float, false, false, Architecture::Generic, GEBPPacketFull> {
  typedef float RhsPacket;
  typedef QuadPacket<float> RhsPacketx4;
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacket& dest) const { dest = pset1<RhsPacket>(*b); }
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacket& dest) const { loadRhs(b, dest); }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const { dest = ploadquad<RhsPacket>(b); }

  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacket& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = __riscv_vfmadd_vf_f32m1(a, b, c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
    c = __riscv_vfmadd_vf_f32m2(a, b, c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 4
    c = __riscv_vfmadd_vf_f32m4(a, b, c, unpacket_traits<AccPacket>::size);
#endif
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE void madd(const Packet1Xf& a, const RhsPacket& b, Packet1Xf& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
    c = __riscv_vfmadd_vf_f32m1(a, b, c, unpacket_traits<Packet1Xf>::size);
  }
#endif
#if EIGEN_RISCV64_DEFAULT_LMUL == 4
  EIGEN_STRONG_INLINE void madd(const Packet2Xf& a, const RhsPacket& b, Packet2Xf& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
    c = __riscv_vfmadd_vf_f32m2(a, b, c, unpacket_traits<Packet2Xf>::size);
  }
#endif

  template <typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacketx4& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const LaneIdType& lane) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = __riscv_vfmadd_vf_f32m1(a, b.get(lane), c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
    c = __riscv_vfmadd_vf_f32m2(a, b.get(lane), c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 4
    c = __riscv_vfmadd_vf_f32m4(a, b.get(lane), c, unpacket_traits<AccPacket>::size);
#endif
  }
};

template <>
struct gebp_traits<double, double, false, false, Architecture::RVV10, GEBPPacketFull>
    : gebp_traits<double, double, false, false, Architecture::Generic, GEBPPacketFull> {
  typedef double RhsPacket;
  typedef QuadPacket<double> RhsPacketx4;
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacket& dest) const { dest = pset1<RhsPacket>(*b); }
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacket& dest) const { loadRhs(b, dest); }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const { dest = ploadquad<RhsPacket>(b); }

  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacket& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = __riscv_vfmadd_vf_f64m1(a, b, c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
    c = __riscv_vfmadd_vf_f64m2(a, b, c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 4
    c = __riscv_vfmadd_vf_f64m4(a, b, c, unpacket_traits<AccPacket>::size);
#endif
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE void madd(const Packet1Xd& a, const RhsPacket& b, Packet1Xd& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
    c = __riscv_vfmadd_vf_f64m1(a, b, c, unpacket_traits<Packet1Xd>::size);
  }
#endif
#if EIGEN_RISCV64_DEFAULT_LMUL == 4
  EIGEN_STRONG_INLINE void madd(const Packet2Xd& a, const RhsPacket& b, Packet2Xd& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
    c = __riscv_vfmadd_vf_f64m2(a, b, c, unpacket_traits<Packet2Xd>::size);
  }
#endif

  template <typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacketx4& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const LaneIdType& lane) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = __riscv_vfmadd_vf_f64m1(a, b.get(lane), c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
    c = __riscv_vfmadd_vf_f64m2(a, b.get(lane), c, unpacket_traits<AccPacket>::size);
#elif EIGEN_RISCV64_DEFAULT_LMUL == 4
    c = __riscv_vfmadd_vf_f64m4(a, b.get(lane), c, unpacket_traits<AccPacket>::size);
#endif
  }
};

#if defined(EIGEN_VECTORIZE_RVV10FP16)

template <>
struct gebp_traits<half, half, false, false, Architecture::RVV10>
    : gebp_traits<half, half, false, false, Architecture::Generic> {
  typedef half RhsPacket;
  typedef PacketXh LhsPacket;
  typedef PacketXh AccPacket;
  typedef QuadPacket<half> RhsPacketx4;

  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacket& dest) const { dest = pset1<RhsPacket>(*b); }
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacket& dest) const { loadRhs(b, dest); }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const { dest = pload<RhsPacket>(b); }

  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacket& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = __riscv_vfmadd_vf_f16m1(a, numext::bit_cast<_Float16>(b), c, unpacket_traits<AccPacket>::size);
#else
    c = __riscv_vfmadd_vf_f16m2(a, numext::bit_cast<_Float16>(b), c, unpacket_traits<AccPacket>::size);
#endif
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE void madd(const Packet1Xh& a, const RhsPacket& b, Packet1Xh& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
    c = __riscv_vfmadd_vf_f16m1(a, numext::bit_cast<_Float16>(b), c, unpacket_traits<Packet1Xh>::size);
  }
#endif

  template <typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacketx4& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const LaneIdType& lane) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = __riscv_vfmadd_vf_f16m1(a, numext::bit_cast<_Float16>(b.get(lane)), c, unpacket_traits<AccPacket>::size);
#else
    c = __riscv_vfmadd_vf_f16m2(a, numext::bit_cast<_Float16>(b.get(lane)), c, unpacket_traits<AccPacket>::size);
#endif
  }
};

#endif

#if defined(EIGEN_VECTORIZE_RVV10BF16)

template <>
struct gebp_traits<bfloat16, bfloat16, false, false, Architecture::RVV10>
    : gebp_traits<bfloat16, bfloat16, false, false, Architecture::Generic> {
  typedef bfloat16 RhsPacket;
  typedef PacketXbf LhsPacket;
  typedef PacketXbf AccPacket;
  typedef QuadPacket<bfloat16> RhsPacketx4;

  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacket& dest) const { dest = pset1<RhsPacket>(*b); }
  EIGEN_STRONG_INLINE void loadRhs(const RhsScalar* b, RhsPacketx4& dest) const {
    pbroadcast4(b, dest.B_0, dest.B1, dest.B2, dest.B3);
  }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar* b, RhsPacket& dest) const { loadRhs(b, dest); }

  EIGEN_STRONG_INLINE void updateRhs(const RhsScalar*, RhsPacketx4&) const {}

  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, RhsPacket& dest) const { dest = pload<RhsPacket>(b); }

  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacket& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = F32ToBf16(
        __riscv_vfwmaccbf16_vf_f32m2(Bf16ToF32(c), numext::bit_cast<__bf16>(b), a, unpacket_traits<AccPacket>::size));
#else
    c = F32ToBf16(
        __riscv_vfwmaccbf16_vf_f32m4(Bf16ToF32(c), numext::bit_cast<__bf16>(b), a, unpacket_traits<AccPacket>::size));
#endif
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE void madd(const Packet1Xbf& a, const RhsPacket& b, Packet1Xbf& c, RhsPacket& /*tmp*/,
                                const FixedInt<0>&) const {
    c = F32ToBf16(
        __riscv_vfwmaccbf16_vf_f32m2(Bf16ToF32(c), numext::bit_cast<__bf16>(b), a, unpacket_traits<Packet1Xbf>::size));
  }
#endif

  template <typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacketx4& b, AccPacket& c, RhsPacket& /*tmp*/,
                                const LaneIdType& lane) const {
#if EIGEN_RISCV64_DEFAULT_LMUL == 1
    c = F32ToBf16(__riscv_vfwmaccbf16_vf_f32m2(Bf16ToF32(c), numext::bit_cast<__bf16>(b.get(lane)), a,
                                               unpacket_traits<AccPacket>::size));
#else
    c = F32ToBf16(__riscv_vfwmaccbf16_vf_f32m4(Bf16ToF32(c), numext::bit_cast<__bf16>(b.get(lane)), a,
                                               unpacket_traits<AccPacket>::size));
#endif
  }
};

#endif

#if EIGEN_RISCV64_DEFAULT_LMUL == 1
/********************************* complex ************************************/

#define PACKET_DECL_COND_POSTFIX(postfix, name, packet_size)                                               \
  typedef typename packet_conditional<                                                                     \
      packet_size, typename packet_traits<name##Scalar>::type, typename packet_traits<name##Scalar>::half, \
      typename unpacket_traits<typename packet_traits<name##Scalar>::half>::half>::type name##Packet##postfix

#define RISCV_COMPLEX_PACKET_DECL_COND_SCALAR(packet_size)                                     \
  typedef typename packet_conditional<                                                         \
      packet_size, typename packet_traits<Scalar>::type, typename packet_traits<Scalar>::half, \
      typename unpacket_traits<typename packet_traits<Scalar>::half>::half>::type ScalarPacket

template <typename RealScalar, bool ConjLhs_, bool ConjRhs_, int PacketSize_>
struct gebp_traits<std::complex<RealScalar>, std::complex<RealScalar>, ConjLhs_, ConjRhs_, Architecture::RVV10,
                   PacketSize_> : gebp_traits<std::complex<RealScalar>, std::complex<RealScalar>, ConjLhs_, ConjRhs_,
                                              Architecture::Generic, PacketSize_> {
  typedef std::complex<RealScalar> Scalar;
  typedef std::complex<RealScalar> LhsScalar;
  typedef std::complex<RealScalar> RhsScalar;
  typedef std::complex<RealScalar> ResScalar;
  typedef typename packet_traits<std::complex<RealScalar>>::type RealPacket;

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);
  RISCV_COMPLEX_PACKET_DECL_COND_SCALAR(PacketSize_);
#undef RISCV_COMPLEX_PACKET_DECL_COND_SCALAR

  enum {
    ConjLhs = ConjLhs_,
    ConjRhs = ConjRhs_,
    Vectorizable = unpacket_traits<RealPacket>::vectorizable && unpacket_traits<ScalarPacket>::vectorizable,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1,
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsScalar>::size : 1,
    RealPacketSize = Vectorizable ? unpacket_traits<RealPacket>::size : 1,

    nr = 4,
    mr = ResPacketSize,

    LhsProgress = ResPacketSize,
    RhsProgress = 1
  };

  typedef DoublePacket<RealPacket> DoublePacketType;

  typedef std::conditional_t<Vectorizable, ScalarPacket, Scalar> LhsPacket4Packing;
  typedef std::conditional_t<Vectorizable, RealPacket, Scalar> LhsPacket;
  typedef std::conditional_t<Vectorizable, DoublePacket<RealScalar>, Scalar> RhsPacket;
  typedef std::conditional_t<Vectorizable, ScalarPacket, Scalar> ResPacket;
  typedef std::conditional_t<Vectorizable, DoublePacketType, Scalar> AccPacket;

  typedef QuadPacket<RhsPacket> RhsPacketx4;

  EIGEN_STRONG_INLINE void initAcc(Scalar& p) { p = Scalar(0); }

  EIGEN_STRONG_INLINE void initAcc(DoublePacketType& p) {
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
  EIGEN_STRONG_INLINE void loadRhsQuad(const RhsScalar* b, DoublePacket<RealScalar>& dest) const {
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

  EIGEN_STRONG_INLINE Packet1Xcf pmadd_scalar(const Packet1Xcf& a, float b, const Packet1Xcf& c) const {
    return Packet1Xcf(__riscv_vfmadd_vf_f32m1(a.v, b, c.v, unpacket_traits<Packet1Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet1Xcd pmadd_scalar(const Packet1Xcd& a, double b, const Packet1Xcd& c) const {
    return Packet1Xcd(__riscv_vfmadd_vf_f64m1(a.v, b, c.v, unpacket_traits<Packet1Xd>::size));
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE Packet2Xcf pmadd_scalar(const Packet2Xcf& a, float b, const Packet2Xcf& c) const {
    return Packet2Xcf(__riscv_vfmadd_vf_f32m2(a.v, b, c.v, unpacket_traits<Packet2Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet2Xcd pmadd_scalar(const Packet2Xcd& a, double b, const Packet2Xcd& c) const {
    return Packet2Xcd(__riscv_vfmadd_vf_f64m2(a.v, b, c.v, unpacket_traits<Packet2Xd>::size));
  }
#endif

#if EIGEN_RISCV64_DEFAULT_LMUL == 4
  EIGEN_STRONG_INLINE Packet4Xcf pmadd_scalar(const Packet4Xcf& a, float b, const Packet4Xcf& c) const {
    return Packet4Xcf(__riscv_vfmadd_vf_f32m4(a.v, b, c.v, unpacket_traits<Packet4Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet4Xcd pmadd_scalar(const Packet4Xcd& a, double b, const Packet4Xcd& c) const {
    return Packet4Xcd(__riscv_vfmadd_vf_f64m4(a.v, b, c.v, unpacket_traits<Packet4Xd>::size));
  }
#endif

  template <typename LhsPacketType, typename RhsPacketType, typename ResPacketType, typename TmpType,
            typename LaneIdType>
  EIGEN_STRONG_INLINE std::enable_if_t<!is_same<RhsPacketType, RhsPacketx4>::value> madd(const LhsPacketType& a,
                                                                                         const RhsPacketType& b,
                                                                                         DoublePacket<ResPacketType>& c,
                                                                                         TmpType& /*tmp*/,
                                                                                         const LaneIdType&) const {
    c.first = pmadd_scalar(a, b.first, c.first);
    c.second = pmadd_scalar(a, b.second, c.second);
  }

  template <typename LhsPacketType, typename AccPacketType, typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacketType& a, const RhsPacketx4& b, AccPacketType& c, RhsPacket& tmp,
                                const LaneIdType& lane) const {
    madd(a, b.get(lane), c, tmp, lane);
  }

  template <typename LaneIdType>
  EIGEN_STRONG_INLINE void madd(const LhsPacket& a, const RhsPacket& b, ResPacket& c, RhsPacket& /*tmp*/,
                                const LaneIdType&) const {
    c = cj.pmadd(a, b, c);
  }

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

  template <typename ResPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void acc(const AccPacketType& c, const ResPacketType& alpha, ResPacketType& r) const {
    r = cj.pmadd(c, alpha, r);
  }

  EIGEN_STRONG_INLINE void acc(const Packet1Xf& c, const Packet1Xcf& alpha, Packet1Xcf& r) const {
    r = cj.pmadd(c, alpha, r);
  }

  EIGEN_STRONG_INLINE void acc(const Packet1Xd& c, const Packet1Xcd& alpha, Packet1Xcd& r) const {
    r = cj.pmadd(c, alpha, r);
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE void acc(const Packet2Xf& c, const Packet2Xcf& alpha, Packet2Xcf& r) const {
    r = cj.pmadd(c, alpha, r);
  }

  EIGEN_STRONG_INLINE void acc(const Packet2Xd& c, const Packet2Xcd& alpha, Packet2Xcd& r) const {
    r = cj.pmadd(c, alpha, r);
  }
#endif

#if EIGEN_RISCV64_DEFAULT_LMUL == 4
  EIGEN_STRONG_INLINE void acc(const Packet4Xf& c, const Packet4Xcf& alpha, Packet4Xcf& r) const {
    r = cj.pmadd(c, alpha, r);
  }

  EIGEN_STRONG_INLINE void acc(const Packet4Xd& c, const Packet4Xcd& alpha, Packet4Xcd& r) const {
    r = cj.pmadd(c, alpha, r);
  }
#endif

 protected:
  conj_helper<LhsScalar, RhsScalar, ConjLhs, ConjRhs> cj;
};

#define PACKET_DECL_COND_SCALAR_POSTFIX(postfix, packet_size)                                  \
  typedef typename packet_conditional<                                                         \
      packet_size, typename packet_traits<Scalar>::type, typename packet_traits<Scalar>::half, \
      typename unpacket_traits<typename packet_traits<Scalar>::half>::half>::type ScalarPacket##postfix

template <typename RealScalar, bool ConjRhs_, int PacketSize_>
class gebp_traits<RealScalar, std::complex<RealScalar>, false, ConjRhs_, Architecture::RVV10, PacketSize_>
    : public gebp_traits<RealScalar, std::complex<RealScalar>, false, ConjRhs_, Architecture::Generic, PacketSize_> {
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
  typedef RhsScalar RhsPacket;
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
    madd_impl(a, b, c, tmp, std::conditional_t<Vectorizable, true_type, false_type>());
  }

  EIGEN_STRONG_INLINE Packet1Xcf pmadd_scalar(const Packet1Xf& a, std::complex<float> b, const Packet1Xcf& c) const {
    Packet1Xcf bp = pset1<Packet1Xcf>(b);
    return Packet1Xcf(__riscv_vfmadd_vv_f32m1(a, bp.v, c.v, unpacket_traits<Packet1Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet1Xcd pmadd_scalar(const Packet1Xd& a, std::complex<double> b, const Packet1Xcd& c) const {
    Packet1Xcd bp = pset1<Packet1Xcd>(b);
    return Packet1Xcd(__riscv_vfmadd_vv_f64m1(a, bp.v, c.v, unpacket_traits<Packet1Xd>::size));
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE Packet2Xcf pmadd_scalar(const Packet2Xf& a, std::complex<float> b, const Packet2Xcf& c) const {
    Packet2Xcf bp = pset1<Packet2Xcf>(b);
    return Packet2Xcf(__riscv_vfmadd_vv_f32m2(a, bp.v, c.v, unpacket_traits<Packet2Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet2Xcd pmadd_scalar(const Packet2Xd& a, std::complex<double> b, const Packet2Xcd& c) const {
    Packet2Xcd bp = pset1<Packet2Xcd>(b);
    return Packet2Xcd(__riscv_vfmadd_vv_f64m2(a, bp.v, c.v, unpacket_traits<Packet2Xd>::size));
  }
#endif

#if EIGEN_RISCV64_DEFAULT_LMUL == 4
  EIGEN_STRONG_INLINE Packet4Xcf pmadd_scalar(const Packet4Xf& a, std::complex<float> b, const Packet4Xcf& c) const {
    Packet4Xcf bp = pset1<Packet4Xcf>(b);
    return Packet4Xcf(__riscv_vfmadd_vv_f32m4(a, bp.v, c.v, unpacket_traits<Packet4Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet4Xcd pmadd_scalar(const Packet4Xd& a, std::complex<double> b, const Packet4Xcd& c) const {
    Packet4Xcd bp = pset1<Packet4Xcd>(b);
    return Packet4Xcd(__riscv_vfmadd_vv_f64m4(a, bp.v, c.v, unpacket_traits<Packet4Xd>::size));
  }
#endif

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void madd_impl(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c,
                                     RhsPacketType& tmp, const true_type&) const {
    EIGEN_UNUSED_VARIABLE(tmp);
    c = pmadd_scalar(a, b, c);
  }

  EIGEN_STRONG_INLINE void madd_impl(const LhsScalar& a, const RhsScalar& b, ResScalar& c, RhsScalar& /*tmp*/,
                                     const false_type&) const {
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
};

template <typename RealScalar, bool ConjLhs_, int PacketSize_>
class gebp_traits<std::complex<RealScalar>, RealScalar, ConjLhs_, false, Architecture::RVV10, PacketSize_>
    : public gebp_traits<RealScalar, std::complex<RealScalar>, ConjLhs_, false, Architecture::Generic, PacketSize_> {
 public:
  typedef std::complex<RealScalar> LhsScalar;
  typedef RealScalar RhsScalar;
  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;

  PACKET_DECL_COND_POSTFIX(_, Lhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Rhs, PacketSize_);
  PACKET_DECL_COND_POSTFIX(_, Res, PacketSize_);
#undef PACKET_DECL_COND_POSTFIX

  enum {
    ConjLhs = ConjLhs_,
    ConjRhs = false,
    Vectorizable = unpacket_traits<LhsPacket_>::vectorizable && unpacket_traits<RhsPacket_>::vectorizable,
    LhsPacketSize = Vectorizable ? unpacket_traits<LhsPacket_>::size : 1,
    RhsPacketSize = Vectorizable ? unpacket_traits<RhsPacket_>::size : 1,
    ResPacketSize = Vectorizable ? unpacket_traits<ResPacket_>::size : 1,

    nr = 4,
    mr = 3 * LhsPacketSize,

    LhsProgress = LhsPacketSize,
    RhsProgress = 1
  };

  typedef std::conditional_t<Vectorizable, LhsPacket_, LhsScalar> LhsPacket;
  typedef RhsScalar RhsPacket;
  typedef std::conditional_t<Vectorizable, ResPacket_, ResScalar> ResPacket;
  typedef LhsPacket LhsPacket4Packing;

  typedef QuadPacket<RhsPacket> RhsPacketx4;

  typedef ResPacket AccPacket;

  EIGEN_STRONG_INLINE void initAcc(AccPacket& p) { p = pset1<ResPacket>(ResScalar(0)); }

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
    loadRhsQuad_impl(b, dest, std::conditional_t<RhsPacketSize == 16, true_type, false_type>());
  }

  EIGEN_STRONG_INLINE void loadRhsQuad_impl(const RhsScalar* b, RhsPacket& dest, const true_type&) const {
    // FIXME we can do better!
    // what we want here is a ploadheight
    RhsScalar tmp[4] = {b[0], b[0], b[1], b[1]};
    dest = ploadquad<RhsPacket>(tmp);
  }

  EIGEN_STRONG_INLINE void loadRhsQuad_impl(const RhsScalar* b, RhsPacket& dest, const false_type&) const {
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
    madd_impl(a, b, c, tmp, std::conditional_t<Vectorizable, true_type, false_type>());
  }

  EIGEN_STRONG_INLINE Packet1Xcf pmadd_scalar(const Packet1Xcf& a, float b, const Packet1Xcf& c) const {
    return Packet1Xcf(__riscv_vfmadd_vf_f32m1(a.v, b, c.v, unpacket_traits<Packet1Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet1Xcd pmadd_scalar(const Packet1Xcd& a, double b, const Packet1Xcd& c) const {
    return Packet1Xcd(__riscv_vfmadd_vf_f64m1(a.v, b, c.v, unpacket_traits<Packet1Xd>::size));
  }

#if EIGEN_RISCV64_DEFAULT_LMUL >= 2
  EIGEN_STRONG_INLINE Packet2Xcf pmadd_scalar(const Packet2Xcf& a, float b, const Packet2Xcf& c) const {
    return Packet2Xcf(__riscv_vfmadd_vf_f32m2(a.v, b, c.v, unpacket_traits<Packet2Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet2Xcd pmadd_scalar(const Packet2Xcd& a, double b, const Packet2Xcd& c) const {
    return Packet2Xcd(__riscv_vfmadd_vf_f64m2(a.v, b, c.v, unpacket_traits<Packet2Xd>::size));
  }
#endif

#if EIGEN_RISCV64_DEFAULT_LMUL == 4
  EIGEN_STRONG_INLINE Packet4Xcf pmadd_scalar(const Packet4Xcf& a, float b, const Packet4Xcf& c) const {
    return Packet4Xcf(__riscv_vfmadd_vf_f32m4(a.v, b, c.v, unpacket_traits<Packet4Xf>::size));
  }

  EIGEN_STRONG_INLINE Packet4Xcd pmadd_scalar(const Packet4Xcd& a, double b, const Packet4Xcd& c) const {
    return Packet4Xcd(__riscv_vfmadd_vf_f64m4(a.v, b, c.v, unpacket_traits<Packet4Xd>::size));
  }
#endif

  template <typename LhsPacketType, typename RhsPacketType, typename AccPacketType>
  EIGEN_STRONG_INLINE void madd_impl(const LhsPacketType& a, const RhsPacketType& b, AccPacketType& c,
                                     RhsPacketType& tmp, const true_type&) const {
    EIGEN_UNUSED_VARIABLE(tmp);
    c = pmadd_scalar(a, b, c);
  }

  EIGEN_STRONG_INLINE void madd_impl(const LhsScalar& a, const RhsScalar& b, ResScalar& c, RhsScalar& /*tmp*/,
                                     const false_type&) const {
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
};
#endif

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_RVV10_GENERAL_BLOCK_KERNEL_H
