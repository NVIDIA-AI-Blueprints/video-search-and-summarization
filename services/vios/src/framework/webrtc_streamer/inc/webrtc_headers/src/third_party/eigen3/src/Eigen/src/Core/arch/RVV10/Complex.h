// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Kseniya Zaytseva <kseniya.zaytseva@syntacore.com>
// Copyright (C) 2026 Chip Kerchner <ckerchner@tenstorrent.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_COMPLEX_RVV10_H
#define EIGEN_COMPLEX_RVV10_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/********************************* float32 ************************************/

#if 0
#if EIGEN_RISCV64_DEFAULT_LMUL == 4
#define USE_LMUL4_ONLY
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
#define USE_LMUL2_ONLY
#else
#define USE_LMUL1_ONLY
#endif
#endif

template <typename RealPacketT, int N>
struct complex_packet_wrapper {
  complex_packet_wrapper() = default;
  EIGEN_STRONG_INLINE explicit complex_packet_wrapper(const RealPacketT& a) : v(a) {}

  RealPacketT v;
};

typedef complex_packet_wrapper<Packet2Xf, 29> Packet2Xcf;
typedef complex_packet_wrapper<Packet4Xf, 30> Packet4Xcf;
typedef complex_packet_wrapper<Packet1Xf, 31> Packet1Xcf;

#if EIGEN_RISCV64_DEFAULT_LMUL == 1
typedef Packet1Xcf PacketXcf;

template <>
struct packet_traits<std::complex<float>> : default_packet_traits {
  typedef Packet1Xcf type;
  typedef Packet1Xcf half;
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 0,
    size = rvv_packet_size_selector<std::complex<float>, EIGEN_RISCV64_RVV_VL, 1>::size,

    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasSqrt = 1,
    HasLog = 1,
    HasExp = 1,
    HasSign = 0,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasConj = 1,
    HasArg = 0,
    HasSetLinear = 0
  };
};
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
typedef Packet2Xcf PacketXcf;

template <>
struct packet_traits<std::complex<float>> : default_packet_traits {
  typedef Packet2Xcf type;
#ifndef USE_LMUL2_ONLY
  typedef Packet1Xcf half;
#else
  typedef Packet2Xcf half;
#endif
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 0,
    size = rvv_packet_size_selector<std::complex<float>, EIGEN_RISCV64_RVV_VL, 2>::size,

    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasSqrt = 1,
    HasLog = 1,
    HasExp = 1,
    HasSign = 0,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasConj = 1,
    HasArg = 0,
    HasSetLinear = 0
  };
};
#elif EIGEN_RISCV64_DEFAULT_LMUL == 4
typedef Packet4Xcf PacketXcf;

template <>
struct packet_traits<std::complex<float>> : default_packet_traits {
  typedef Packet4Xcf type;
#ifndef USE_LMUL4_ONLY
  typedef Packet2Xcf half;
#else
  typedef Packet4Xcf half;
#endif
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 0,
    size = rvv_packet_size_selector<std::complex<float>, EIGEN_RISCV64_RVV_VL, 4>::size,

    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasSqrt = 1,
    HasLog = 1,
    HasExp = 1,
    HasSign = 0,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasConj = 1,
    HasArg = 0,
    HasSetLinear = 0
  };
};
#endif

template <>
struct unpacket_traits<Packet2Xcf> : default_unpacket_traits {
  typedef std::complex<float> type;
#ifndef USE_LMUL2_ONLY
  typedef Packet1Xcf half;
#else
  typedef Packet2Xcf half;
#endif
  typedef Packet2Xf as_real;
  enum {
    size = rvv_packet_size_selector<std::complex<float>, EIGEN_RISCV64_RVV_VL, 2>::size,
    alignment = rvv_packet_alignment_selector<EIGEN_RISCV64_RVV_VL, 2>::alignment,
    vectorizable = true,
    masked_load_available = false,
    masked_store_available = false
  };
};

template <>
struct unpacket_traits<Packet4Xcf> : default_unpacket_traits {
  typedef std::complex<float> type;
#ifndef USE_LMUL4_ONLY
  typedef Packet2Xcf half;
#else
  typedef Packet4Xcf half;
#endif
  typedef Packet4Xf as_real;
  enum {
    size = rvv_packet_size_selector<std::complex<float>, EIGEN_RISCV64_RVV_VL, 4>::size,
    alignment = rvv_packet_alignment_selector<EIGEN_RISCV64_RVV_VL, 4>::alignment,
    vectorizable = true,
    masked_load_available = false,
    masked_store_available = false
  };
};

template <>
struct unpacket_traits<Packet1Xcf> : default_unpacket_traits {
  typedef std::complex<float> type;
  typedef Packet1Xcf half;
  typedef Packet1Xf as_real;
  enum {
    size = rvv_packet_size_selector<std::complex<float>, EIGEN_RISCV64_RVV_VL, 1>::size,
    alignment = rvv_packet_alignment_selector<EIGEN_RISCV64_RVV_VL, 1>::alignment,
    vectorizable = true,
    masked_load_available = false,
    masked_store_available = false
  };
};

template <>
EIGEN_STRONG_INLINE Packet2Xcf pcast<Packet2Xf, Packet2Xcf>(const Packet2Xf& a) {
  return Packet2Xcf(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xf pcast<Packet2Xcf, Packet2Xf>(const Packet2Xcf& a) {
  return a.v;
}

EIGEN_STRONG_INLINE Packet2Xul __riscv_vreinterpret_v_f32m2_u64m2(const Packet2Xf& a) {
  return __riscv_vreinterpret_v_u32m2_u64m2(__riscv_vreinterpret_v_f32m2_u32m2(a));
}

EIGEN_STRONG_INLINE Packet2Xl __riscv_vreinterpret_v_f32m2_i64m2(const Packet2Xf& a) {
  return __riscv_vreinterpret_v_u64m2_i64m2(__riscv_vreinterpret_v_u32m2_u64m2(__riscv_vreinterpret_v_f32m2_u32m2(a)));
}

EIGEN_STRONG_INLINE Packet2Xf __riscv_vreinterpret_v_i64m2_f32m2(const Packet2Xl& a) {
  return __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vreinterpret_v_u64m2_u32m2(__riscv_vreinterpret_v_i64m2_u64m2(a)));
}

EIGEN_STRONG_INLINE void prealimag2(const Packet2Xcf& a, Packet2Xf& real, Packet2Xf& imag) {
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet2Xu res = __riscv_vreinterpret_v_f32m2_u32m2(a.v);
  real = __riscv_vreinterpret_v_u32m2_f32m2(
      __riscv_vslide1up_vx_u32m2_tumu(mask, res, res, 0, unpacket_traits<Packet2Xi>::size));
  imag = __riscv_vreinterpret_v_u32m2_f32m2(__riscv_vslide1down_vx_u32m2_tumu(
      __riscv_vmnot_m_b16(mask, unpacket_traits<Packet1Xs>::size), res, res, 0, unpacket_traits<Packet2Xi>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pset1<Packet2Xcf>(const std::complex<float>& from) {
  const numext::int64_t from2 = *reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(&from));
  Packet2Xf res = __riscv_vreinterpret_v_i64m2_f32m2(pset1<Packet2Xl>(from2));
  return Packet2Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf padd<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return Packet2Xcf(padd<Packet2Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf psub<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return Packet2Xcf(psub<Packet2Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pnegate(const Packet2Xcf& a) {
  return Packet2Xcf(pnegate<Packet2Xf>(a.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pconj(const Packet2Xcf& a) {
  return Packet2Xcf(__riscv_vreinterpret_v_u64m2_f32m2(__riscv_vxor_vx_u64m2(
      __riscv_vreinterpret_v_f32m2_u64m2(a.v), 0x8000000000000000ull, unpacket_traits<Packet2Xl>::size)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pcplxflip<Packet2Xcf>(const Packet2Xcf& a) {
#ifndef __riscv_zvbb
  Packet2Xu res = __riscv_vreinterpret_v_f32m2_u32m2(a.v);
  const PacketMask16 mask =
      __riscv_vreinterpret_v_i8m1_b16(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet2Xu data = __riscv_vslide1down_vx_u32m2(res, 0, unpacket_traits<Packet2Xi>::size);
  Packet2Xf res2 = __riscv_vreinterpret_v_u32m2_f32m2(
      __riscv_vslide1up_vx_u32m2_tumu(mask, data, res, 0, unpacket_traits<Packet2Xf>::size));
  return Packet2Xcf(res2);
#else
  Packet2Xf res = __riscv_vreinterpret_v_u64m2_f32m2(
      __riscv_vror_vx_u64m2(__riscv_vreinterpret_v_f32m2_u64m2(a.v), 32, unpacket_traits<Packet2Xl>::size));
  return Packet2Xcf(res);
#endif
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pmul<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  Packet2Xf real, imag;
  prealimag2(a, real, imag);
  return Packet2Xcf(pmadd<Packet2Xf>(imag, pcplxflip<Packet2Xcf>(pconj<Packet2Xcf>(b)).v, pmul<Packet2Xf>(real, b.v)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pmadd<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b, const Packet2Xcf& c) {
  Packet2Xf real, imag;
  prealimag2(a, real, imag);
  return Packet2Xcf(
      pmadd<Packet2Xf>(imag, pcplxflip<Packet2Xcf>(pconj<Packet2Xcf>(b)).v, pmadd<Packet2Xf>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pmsub<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b, const Packet2Xcf& c) {
  Packet2Xf real, imag;
  prealimag2(a, real, imag);
  return Packet2Xcf(
      pmadd<Packet2Xf>(imag, pcplxflip<Packet2Xcf>(pconj<Packet2Xcf>(b)).v, pmsub<Packet2Xf>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pcmp_eq(const Packet2Xcf& a, const Packet2Xcf& b) {
  Packet2Xi c = __riscv_vundefined_i32m2();
  PacketMask16 mask = __riscv_vmfeq_vv_f32m2_b16(a.v, b.v, unpacket_traits<Packet2Xf>::size);
  Packet2Xl res = __riscv_vreinterpret_v_i32m2_i64m2(
      __riscv_vmerge_vvm_i32m2(pzero<Packet2Xi>(c), ptrue<Packet2Xi>(c), mask, unpacket_traits<Packet2Xi>::size));
  Packet2Xf res2 = __riscv_vreinterpret_v_i64m2_f32m2(
      __riscv_vsra_vx_i64m2(__riscv_vand_vv_i64m2(__riscv_vsll_vx_i64m2(res, 32, unpacket_traits<Packet2Xl>::size), res,
                                                  unpacket_traits<Packet2Xl>::size),
                            32, unpacket_traits<Packet2Xl>::size));
  return Packet2Xcf(res2);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pand<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return Packet2Xcf(pand<Packet2Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf por<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return Packet2Xcf(por<Packet2Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pxor<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return Packet2Xcf(pxor<Packet2Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pandnot<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return Packet2Xcf(pandnot<Packet2Xf>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pload<Packet2Xcf>(const std::complex<float>* from) {
  Packet2Xf res = pload<Packet2Xf>(reinterpret_cast<const float*>(from));
  EIGEN_DEBUG_ALIGNED_LOAD return Packet2Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf ploadu<Packet2Xcf>(const std::complex<float>* from) {
  Packet2Xf res = ploadu<Packet2Xf>(reinterpret_cast<const float*>(from));
  EIGEN_DEBUG_UNALIGNED_LOAD return Packet2Xcf(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf ploaddup<Packet2Xcf>(const std::complex<float>* from) {
  Packet2Xl res = ploaddup<Packet2Xl>(reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)));
  return Packet2Xcf(__riscv_vreinterpret_v_i64m2_f32m2(res));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf ploadquad<Packet2Xcf>(const std::complex<float>* from) {
  Packet2Xl res = ploadquad<Packet2Xl>(reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)));
  return Packet2Xcf(__riscv_vreinterpret_v_i64m2_f32m2(res));
}

template <>
EIGEN_STRONG_INLINE void pstore<std::complex<float>>(std::complex<float>* to, const Packet2Xcf& from) {
  EIGEN_DEBUG_ALIGNED_STORE pstore<float>(reinterpret_cast<float*>(to), from.v);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<std::complex<float>>(std::complex<float>* to, const Packet2Xcf& from) {
  EIGEN_DEBUG_UNALIGNED_STORE pstoreu<float>(reinterpret_cast<float*>(to), from.v);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE Packet2Xcf
pgather<std::complex<float>, Packet2Xcf>(const std::complex<float>* from, Index stride) {
  return Packet2Xcf(__riscv_vreinterpret_v_i64m2_f32m2(pgather<int64_t, Packet2Xl>(
      reinterpret_cast<const numext::int64_t*>(reinterpret_cast<const void*>(from)), stride)));
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void pscatter<std::complex<float>, Packet2Xcf>(std::complex<float>* to,
                                                                                     const Packet2Xcf& from,
                                                                                     Index stride) {
  pscatter<int64_t, Packet2Xl>(reinterpret_cast<numext::int64_t*>(reinterpret_cast<void*>(to)),
                               __riscv_vreinterpret_v_f32m2_i64m2(from.v), stride);
}

template <>
EIGEN_STRONG_INLINE std::complex<float> pfirst<Packet2Xcf>(const Packet2Xcf& a) {
  numext::int64_t res = pfirst<Packet2Xl>(__riscv_vreinterpret_v_f32m2_i64m2(a.v));
  return numext::bit_cast<std::complex<float>>(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf preverse(const Packet2Xcf& a) {
  return Packet2Xcf(__riscv_vreinterpret_v_i64m2_f32m2(preverse<Packet2Xl>(__riscv_vreinterpret_v_f32m2_i64m2(a.v))));
}

template <>
EIGEN_STRONG_INLINE std::complex<float> predux<Packet2Xcf>(const Packet2Xcf& a) {
  Packet2Xl res = __riscv_vreinterpret_v_f32m2_i64m2(a.v);
  Packet2Xf real = __riscv_vreinterpret_v_i64m2_f32m2(
      __riscv_vand_vx_i64m2(res, 0x00000000ffffffffull, unpacket_traits<Packet2Xl>::size));
  Packet2Xf imag = __riscv_vreinterpret_v_i64m2_f32m2(
      __riscv_vand_vx_i64m2(res, 0xffffffff00000000ull, unpacket_traits<Packet2Xl>::size));
  return std::complex<float>(predux<Packet2Xf>(real), predux<Packet2Xf>(imag));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pdiv<Packet2Xcf>(const Packet2Xcf& a, const Packet2Xcf& b) {
  return pdiv_complex(a, b);
}

template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void ptranspose(PacketBlock<Packet2Xcf, N>& kernel) {
  numext::int64_t buffer[unpacket_traits<Packet2Xl>::size * N] = {0};
  int i = 0;

  for (i = 0; i < N; i++) {
    __riscv_vsse64(&buffer[i], N * sizeof(numext::int64_t), __riscv_vreinterpret_v_f32m2_i64m2(kernel.packet[i].v),
                   unpacket_traits<Packet2Xl>::size);
  }
  for (i = 0; i < N; i++) {
    kernel.packet[i] = Packet2Xcf(__riscv_vreinterpret_v_i64m2_f32m2(
        __riscv_vle64_v_i64m2(&buffer[i * unpacket_traits<Packet2Xl>::size], unpacket_traits<Packet2Xl>::size)));
  }
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf psqrt<Packet2Xcf>(const Packet2Xcf& a) {
  return psqrt_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf plog<Packet2Xcf>(const Packet2Xcf& a) {
  return plog_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcf pexp<Packet2Xcf>(const Packet2Xcf& a) {
  return pexp_complex(a);
}

template <typename Packet = Packet2Xcf>
EIGEN_STRONG_INLINE Packet1Xcf predux_half(const Packet2Xcf& a) {
  return Packet1Xcf(__riscv_vfadd_vv_f32m1(__riscv_vget_v_f32m2_f32m1(a.v, 0), __riscv_vget_v_f32m2_f32m1(a.v, 1),
                                           unpacket_traits<Packet1Xf>::size));
}

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(Packet2Xcf, Packet2Xf)

/********************************* double ************************************/

typedef complex_packet_wrapper<Packet2Xd, 32> Packet2Xcd;
typedef complex_packet_wrapper<Packet4Xd, 33> Packet4Xcd;
typedef complex_packet_wrapper<Packet1Xd, 34> Packet1Xcd;

#if EIGEN_RISCV64_DEFAULT_LMUL == 1
typedef Packet1Xcd PacketXcd;

template <>
struct packet_traits<std::complex<double>> : default_packet_traits {
  typedef Packet1Xcd type;
  typedef Packet1Xcd half;
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 0,
    size = rvv_packet_size_selector<std::complex<double>, EIGEN_RISCV64_RVV_VL, 1>::size,

    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasSqrt = 1,
    HasLog = 1,
    HasExp = 1,
    HasSign = 0,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasConj = 1,
    HasArg = 0,
    HasSetLinear = 0
  };
};
#elif EIGEN_RISCV64_DEFAULT_LMUL == 2
typedef Packet2Xcd PacketXcd;

template <>
struct packet_traits<std::complex<double>> : default_packet_traits {
  typedef Packet2Xcd type;
#ifndef USE_LMUL2_ONLY
  typedef Packet1Xcd half;
#else
  typedef Packet2Xcd half;
#endif
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 0,
    size = rvv_packet_size_selector<std::complex<double>, EIGEN_RISCV64_RVV_VL, 2>::size,

    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasSqrt = 1,
    HasLog = 1,
    HasExp = 1,
    HasSign = 0,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasConj = 1,
    HasArg = 0,
    HasSetLinear = 0
  };
};
#elif EIGEN_RISCV64_DEFAULT_LMUL == 4
typedef Packet4Xcd PacketXcd;

template <>
struct packet_traits<std::complex<double>> : default_packet_traits {
  typedef Packet4Xcd type;
#ifndef USE_LMUL4_ONLY
  typedef Packet2Xcd half;
#else
  typedef Packet4Xcd half;
#endif
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 0,
    size = rvv_packet_size_selector<std::complex<double>, EIGEN_RISCV64_RVV_VL, 4>::size,

    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasSqrt = 1,
    HasLog = 1,
    HasExp = 1,
    HasSign = 0,
    HasAbs = 0,
    HasAbs2 = 0,
    HasMin = 0,
    HasMax = 0,
    HasConj = 1,
    HasArg = 0,
    HasSetLinear = 0
  };
};
#endif

template <>
struct unpacket_traits<Packet2Xcd> : default_unpacket_traits {
  typedef std::complex<double> type;
#ifndef USE_LMUL2_ONLY
  typedef Packet1Xcd half;
#else
  typedef Packet2Xcd half;
#endif
  typedef Packet2Xd as_real;
  enum {
    size = rvv_packet_size_selector<std::complex<double>, EIGEN_RISCV64_RVV_VL, 2>::size,
    alignment = rvv_packet_alignment_selector<EIGEN_RISCV64_RVV_VL, 2>::alignment,
    vectorizable = true,
    masked_load_available = false,
    masked_store_available = false
  };
};

template <>
struct unpacket_traits<Packet4Xcd> : default_unpacket_traits {
  typedef std::complex<double> type;
#ifndef USE_LMUL4_ONLY
  typedef Packet2Xcd half;
#else
  typedef Packet4Xcd half;
#endif
  typedef Packet4Xd as_real;
  enum {
    size = rvv_packet_size_selector<std::complex<double>, EIGEN_RISCV64_RVV_VL, 4>::size,
    alignment = rvv_packet_alignment_selector<EIGEN_RISCV64_RVV_VL, 4>::alignment,
    vectorizable = true,
    masked_load_available = false,
    masked_store_available = false
  };
};

template <>
struct unpacket_traits<Packet1Xcd> : default_unpacket_traits {
  typedef std::complex<double> type;
  typedef Packet1Xcd half;
  typedef Packet1Xd as_real;
  enum {
    size = rvv_packet_size_selector<std::complex<double>, EIGEN_RISCV64_RVV_VL, 1>::size,
    alignment = rvv_packet_alignment_selector<EIGEN_RISCV64_RVV_VL, 1>::alignment,
    vectorizable = true,
    masked_load_available = false,
    masked_store_available = false
  };
};

template <>
EIGEN_STRONG_INLINE Packet2Xcd pcast<Packet2Xd, Packet2Xcd>(const Packet2Xd& a) {
  return Packet2Xcd(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xd pcast<Packet2Xcd, Packet2Xd>(const Packet2Xcd& a) {
  return a.v;
}

EIGEN_STRONG_INLINE void prealimag2(const Packet2Xcd& a, Packet2Xd& real, Packet2Xd& imag) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  real = __riscv_vfslide1up_vf_f64m2_tumu(mask, a.v, a.v, 0.0, unpacket_traits<Packet2Xd>::size);
  imag = __riscv_vfslide1down_vf_f64m2_tumu(__riscv_vmnot_m_b32(mask, unpacket_traits<Packet1Xi>::size), a.v, a.v, 0.0,
                                            unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pset1<Packet2Xcd>(const std::complex<double>& from) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet2Xd res = __riscv_vmerge_vvm_f64m2(pset1<Packet2Xd>(from.real()), pset1<Packet2Xd>(from.imag()), mask,
                                           unpacket_traits<Packet2Xd>::size);
  return Packet2Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd padd<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return Packet2Xcd(padd<Packet2Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd psub<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return Packet2Xcd(psub<Packet2Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pnegate(const Packet2Xcd& a) {
  return Packet2Xcd(pnegate<Packet2Xd>(a.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pconj(const Packet2Xcd& a) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  return Packet2Xcd(__riscv_vfsgnjn_vv_f64m2_tumu(mask, a.v, a.v, a.v, unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pcplxflip<Packet2Xcd>(const Packet2Xcd& a) {
  Packet2Xul res = __riscv_vreinterpret_v_f64m2_u64m2(a.v);
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet2Xul data = __riscv_vslide1down_vx_u64m2(res, 0, unpacket_traits<Packet2Xl>::size);
  Packet2Xd res2 = __riscv_vreinterpret_v_u64m2_f64m2(
      __riscv_vslide1up_vx_u64m2_tumu(mask, data, res, 0, unpacket_traits<Packet2Xl>::size));
  return Packet2Xcd(res2);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pmul<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  Packet2Xd real, imag;
  prealimag2(a, real, imag);
  return Packet2Xcd(pmadd<Packet2Xd>(imag, pcplxflip<Packet2Xcd>(pconj<Packet2Xcd>(b)).v, pmul<Packet2Xd>(real, b.v)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pmadd<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b, const Packet2Xcd& c) {
  Packet2Xd real, imag;
  prealimag2(a, real, imag);
  return Packet2Xcd(
      pmadd<Packet2Xd>(imag, pcplxflip<Packet2Xcd>(pconj<Packet2Xcd>(b)).v, pmadd<Packet2Xd>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pmsub<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b, const Packet2Xcd& c) {
  Packet2Xd real, imag;
  prealimag2(a, real, imag);
  return Packet2Xcd(
      pmadd<Packet2Xd>(imag, pcplxflip<Packet2Xcd>(pconj<Packet2Xcd>(b)).v, pmsub<Packet2Xd>(real, b.v, c.v)));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pcmp_eq(const Packet2Xcd& a, const Packet2Xcd& b) {
  Packet2Xl c = __riscv_vundefined_i64m2();
  Packet1Xu mask =
      __riscv_vreinterpret_v_b32_u32m1(__riscv_vmfeq_vv_f64m2_b32(a.v, b.v, unpacket_traits<Packet2Xd>::size));
  Packet1Xu mask_r = __riscv_vsrl_vx_u32m1(__riscv_vand_vx_u32m1(mask, 0xaaaaaaaa, unpacket_traits<Packet1Xi>::size), 1,
                                           unpacket_traits<Packet1Xi>::size);
  mask = __riscv_vand_vv_u32m1(mask, mask_r, unpacket_traits<Packet1Xi>::size);
  mask = __riscv_vor_vv_u32m1(__riscv_vsll_vx_u32m1(mask, 1, unpacket_traits<Packet1Xi>::size), mask,
                              unpacket_traits<Packet1Xi>::size);
  Packet2Xd res = __riscv_vreinterpret_v_i64m2_f64m2(__riscv_vmerge_vvm_i64m2(pzero<Packet2Xl>(c), ptrue<Packet2Xl>(c),
                                                                              __riscv_vreinterpret_v_u32m1_b32(mask),
                                                                              unpacket_traits<Packet2Xl>::size));
  return Packet2Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pand<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return Packet2Xcd(pand<Packet2Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd por<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return Packet2Xcd(por<Packet2Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pxor<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return Packet2Xcd(pxor<Packet2Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pandnot<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return Packet2Xcd(pandnot<Packet2Xd>(a.v, b.v));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pload<Packet2Xcd>(const std::complex<double>* from) {
  Packet2Xd res = pload<Packet2Xd>(reinterpret_cast<const double*>(from));
  EIGEN_DEBUG_ALIGNED_LOAD return Packet2Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd ploadu<Packet2Xcd>(const std::complex<double>* from) {
  Packet2Xd res = ploadu<Packet2Xd>(reinterpret_cast<const double*>(from));
  EIGEN_DEBUG_UNALIGNED_LOAD return Packet2Xcd(res);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd ploaddup<Packet2Xcd>(const std::complex<double>* from) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0x66), unpacket_traits<Packet1Xc>::size));
  Packet2Xul idx1 =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 1, unpacket_traits<Packet2Xd>::size);
  Packet2Xul idx2 = __riscv_vxor_vx_u64m2_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet2Xl>::size);
  return Packet2Xcd(__riscv_vrgather_vv_f64m2(
      __riscv_vlmul_ext_v_f64m1_f64m2(pload<Packet1Xd>(reinterpret_cast<const double*>(from))), idx2,
      unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd ploadquad<Packet2Xcd>(const std::complex<double>* from) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0x5a), unpacket_traits<Packet1Xc>::size));
  Packet2Xul idx1 =
      __riscv_vsrl_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xd>::size), 2, unpacket_traits<Packet2Xd>::size);
  Packet2Xul idx2 = __riscv_vxor_vx_u64m2_tumu(mask, idx1, idx1, 1, unpacket_traits<Packet2Xl>::size);
  return Packet2Xcd(__riscv_vrgather_vv_f64m2(
      __riscv_vlmul_ext_v_f64m1_f64m2(pload<Packet1Xd>(reinterpret_cast<const double*>(from))), idx2,
      unpacket_traits<Packet2Xd>::size));
}

template <>
EIGEN_STRONG_INLINE void pstore<std::complex<double>>(std::complex<double>* to, const Packet2Xcd& from) {
  EIGEN_DEBUG_ALIGNED_STORE pstore<double>(reinterpret_cast<double*>(to), from.v);
}

template <>
EIGEN_STRONG_INLINE void pstoreu<std::complex<double>>(std::complex<double>* to, const Packet2Xcd& from) {
  EIGEN_DEBUG_UNALIGNED_STORE pstoreu<double>(reinterpret_cast<double*>(to), from.v);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE Packet2Xcd
pgather<std::complex<double>, Packet2Xcd>(const std::complex<double>* from, Index stride) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));
  const double* from2 = reinterpret_cast<const double*>(from);
  Packet2Xd res = __riscv_vundefined_f64m2();
  res = __riscv_vlse64_v_f64m2_tumu(mask, res, &from2[0 - (0 * stride)], stride * sizeof(double),
                                    unpacket_traits<Packet2Xd>::size);
  res =
      __riscv_vlse64_v_f64m2_tumu(__riscv_vmnot_m_b32(mask, unpacket_traits<Packet1Xi>::size), res,
                                  &from2[1 - (1 * stride)], stride * sizeof(double), unpacket_traits<Packet2Xd>::size);
  return Packet2Xcd(res);
}

template <>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void pscatter<std::complex<double>, Packet2Xcd>(std::complex<double>* to,
                                                                                      const Packet2Xcd& from,
                                                                                      Index stride) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));
  double* to2 = reinterpret_cast<double*>(to);
  __riscv_vsse64_v_f64m2_m(mask, &to2[0 - (0 * stride)], stride * sizeof(double), from.v,
                           unpacket_traits<Packet2Xd>::size);
  __riscv_vsse64_v_f64m2_m(__riscv_vmnot_m_b32(mask, unpacket_traits<Packet1Xi>::size), &to2[1 - (1 * stride)],
                           stride * sizeof(double), from.v, unpacket_traits<Packet2Xd>::size);
}

template <>
EIGEN_STRONG_INLINE std::complex<double> pfirst<Packet2Xcd>(const Packet2Xcd& a) {
  double real = pfirst<Packet2Xd>(a.v);
  double imag = pfirst<Packet2Xd>(__riscv_vfslide1down_vf_f64m2(a.v, 0.0, unpacket_traits<Packet2Xd>::size));
  return std::complex<double>(real, imag);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd preverse(const Packet2Xcd& a) {
  Packet2Xul idx = __riscv_vxor_vx_u64m2(__riscv_vid_v_u64m2(unpacket_traits<Packet2Xl>::size),
                                         unpacket_traits<Packet2Xl>::size - 2, unpacket_traits<Packet2Xl>::size);
  Packet2Xd res = __riscv_vrgather_vv_f64m2(a.v, idx, unpacket_traits<Packet2Xd>::size);
  return Packet2Xcd(res);
}

template <>
EIGEN_STRONG_INLINE std::complex<double> predux<Packet2Xcd>(const Packet2Xcd& a) {
  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0xaa), unpacket_traits<Packet1Xc>::size));
  Packet2Xl res = __riscv_vreinterpret_v_f64m2_i64m2(a.v);
  Packet2Xd real = __riscv_vreinterpret_v_i64m2_f64m2(
      __riscv_vand_vx_i64m2_tumu(mask, res, res, 0, unpacket_traits<Packet2Xl>::size));
  Packet2Xd imag = __riscv_vreinterpret_v_i64m2_f64m2(__riscv_vand_vx_i64m2_tumu(
      __riscv_vmnot_m_b32(mask, unpacket_traits<Packet1Xi>::size), res, res, 0, unpacket_traits<Packet2Xl>::size));
  return std::complex<double>(predux<Packet2Xd>(real), predux<Packet2Xd>(imag));
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pdiv<Packet2Xcd>(const Packet2Xcd& a, const Packet2Xcd& b) {
  return pdiv_complex(a, b);
}

template <int N>
EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE void ptranspose(PacketBlock<Packet2Xcd, N>& kernel) {
  double buffer[unpacket_traits<Packet2Xd>::size * N];
  int i = 0;

  const PacketMask32 mask =
      __riscv_vreinterpret_v_i8m1_b32(__riscv_vmv_v_x_i8m1(static_cast<char>(0x55), unpacket_traits<Packet1Xc>::size));

  for (i = 0; i < N; i++) {
    __riscv_vsse64_v_f64m2_m(mask, &buffer[(i * 2) - (0 * N) + 0], N * sizeof(double), kernel.packet[i].v,
                             unpacket_traits<Packet2Xd>::size);
    __riscv_vsse64_v_f64m2_m(__riscv_vmnot_m_b32(mask, unpacket_traits<Packet1Xi>::size),
                             &buffer[(i * 2) - (1 * N) + 1], N * sizeof(double), kernel.packet[i].v,
                             unpacket_traits<Packet2Xd>::size);
  }

  for (i = 0; i < N; i++) {
    kernel.packet[i] = Packet2Xcd(
        __riscv_vle64_v_f64m2(&buffer[i * unpacket_traits<Packet2Xd>::size], unpacket_traits<Packet2Xd>::size));
  }
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd psqrt<Packet2Xcd>(const Packet2Xcd& a) {
  return psqrt_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd plog<Packet2Xcd>(const Packet2Xcd& a) {
  return plog_complex(a);
}

template <>
EIGEN_STRONG_INLINE Packet2Xcd pexp<Packet2Xcd>(const Packet2Xcd& a) {
  return pexp_complex(a);
}

template <typename Packet = Packet2Xcd>
EIGEN_STRONG_INLINE Packet1Xcd predux_half(const Packet2Xcd& a) {
  return Packet1Xcd(__riscv_vfadd_vv_f64m1(__riscv_vget_v_f64m2_f64m1(a.v, 0), __riscv_vget_v_f64m2_f64m1(a.v, 1),
                                           unpacket_traits<Packet1Xd>::size));
}

EIGEN_MAKE_CONJ_HELPER_CPLX_REAL(Packet2Xcd, Packet2Xd)

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_COMPLEX_RVV10_H
