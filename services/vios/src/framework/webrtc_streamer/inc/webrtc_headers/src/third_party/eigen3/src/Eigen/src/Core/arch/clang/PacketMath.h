// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Rasmus Munk Larsen
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_PACKET_MATH_CLANG_H
#define EIGEN_PACKET_MATH_CLANG_H

// IWYU pragma: private
#include "../../InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

namespace detail {
// namespace detail contains implementation details specific to this
// file, while namespace internal contains internal APIs used elsewhere
// in Eigen.
template <typename ScalarT, int n>
using VectorType = ScalarT __attribute__((ext_vector_type(n), aligned(n * sizeof(ScalarT))));
}  // namespace detail

// --- Naming Convention ---
// This backend uses size-independent type aliases so the same code works
// for EIGEN_GENERIC_VECTOR_SIZE_BYTES in {16, 32, 64}:
//
//   PacketXf  - float vector   (4, 8, or 16 elements)
//   PacketXd  - double vector  (2, 4, or 8 elements)
//   PacketXi  - int32_t vector (4, 8, or 16 elements)
//   PacketXl  - int64_t vector (2, 4, or 8 elements)
//   PacketXcf - complex<float> vector  (2, 4, or 8 elements)  [in Complex.h]
//   PacketXcd - complex<double> vector (1, 2, or 4 elements)  [in Complex.h]
//
// The "X" suffix indicates the element count is determined by the macro
// EIGEN_GENERIC_VECTOR_SIZE_BYTES at compile time. Operations that require
// compile-time constant indices (e.g. __builtin_shufflevector) use
// #if EIGEN_GENERIC_VECTOR_SIZE_BYTES == ... blocks.

static_assert(EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16 || EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32 ||
                  EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64,
              "EIGEN_GENERIC_VECTOR_SIZE_BYTES must be 16, 32, or 64");

constexpr int kFloatPacketSize = EIGEN_GENERIC_VECTOR_SIZE_BYTES / sizeof(float);
constexpr int kDoublePacketSize = EIGEN_GENERIC_VECTOR_SIZE_BYTES / sizeof(double);
using PacketXf = detail::VectorType<float, kFloatPacketSize>;
using PacketXd = detail::VectorType<double, kDoublePacketSize>;
using PacketXi = detail::VectorType<int32_t, kFloatPacketSize>;
using PacketXl = detail::VectorType<int64_t, kDoublePacketSize>;

// --- packet_traits specializations ---
struct generic_float_packet_traits : default_packet_traits {
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 1,
    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasAbs = 1,
    HasRound = 1,
    HasMin = 1,
    HasMax = 1,
    HasCmp = 1,
    HasSet1 = 1,
    HasCast = 1,
    HasBitwise = 1,
    HasRedux = 1,
    HasSign = 1,
    HasArg = 0,
    HasConj = 1,
    // Math functions
    HasReciprocal = 1,
    HasSin = 1,
    HasCos = 1,
    HasTan = 1,
    HasACos = 1,
    HasASin = 1,
    HasATan = 1,
    HasATanh = 1,
    HasLog = 1,
    HasLog1p = 1,
    HasExpm1 = 1,
    HasExp = 1,
    HasPow = 1,
    HasNdtri = 1,
    HasBessel = 1,
    HasSqrt = 1,
    HasRsqrt = 1,
    HasCbrt = 1,
    HasTanh = 1,
    HasErf = 1,
    HasErfc = 1
  };
};

template <>
struct packet_traits<float> : generic_float_packet_traits {
  using type = PacketXf;
  using half = PacketXf;
  enum {
    size = kFloatPacketSize,
  };
};

template <>
struct packet_traits<double> : generic_float_packet_traits {
  using type = PacketXd;
  using half = PacketXd;
  // Generic double-precision acos/asin are not yet implemented in
  // GenericPacketMathFunctions.h (only float versions exist).
  enum { size = kDoublePacketSize, HasACos = 0, HasASin = 0 };
};

struct generic_integer_packet_traits : default_packet_traits {
  enum {
    Vectorizable = 1,
    AlignedOnScalar = 1,
    HasAdd = 1,
    HasSub = 1,
    HasMul = 1,
    HasDiv = 1,
    HasNegate = 1,
    HasAbs = 1,
    HasMin = 1,
    HasMax = 1,
    HasCmp = 1,
    HasSet1 = 1,
    HasCast = 1,
    HasBitwise = 1,
    HasRedux = 1,
    // Set remaining to 0
    HasRound = 1,
    HasSqrt = 0,
    HasRsqrt = 0,
    HasReciprocal = 0,
    HasArg = 0,
    HasConj = 1,
    HasExp = 0,
    HasLog = 0,
    HasSin = 0,
    HasCos = 0,
  };
};

template <>
struct packet_traits<int32_t> : generic_integer_packet_traits {
  using type = PacketXi;
  using half = PacketXi;
  enum {
    size = kFloatPacketSize,
  };
};

template <>
struct packet_traits<int64_t> : generic_integer_packet_traits {
  using type = PacketXl;
  using half = PacketXl;
  enum {
    size = kDoublePacketSize,
  };
};

// --- unpacket_traits specializations ---
struct generic_unpacket_traits : default_unpacket_traits {
  enum {
    alignment = EIGEN_GENERIC_VECTOR_SIZE_BYTES,
    vectorizable = true,
  };
};

template <>
struct unpacket_traits<PacketXf> : generic_unpacket_traits {
  using type = float;
  using half = PacketXf;
  using integer_packet = PacketXi;
  enum {
    size = kFloatPacketSize,
  };
};
template <>
struct unpacket_traits<PacketXd> : generic_unpacket_traits {
  using type = double;
  using half = PacketXd;
  using integer_packet = PacketXl;
  enum {
    size = kDoublePacketSize,
  };
};
template <>
struct unpacket_traits<PacketXi> : generic_unpacket_traits {
  using type = int32_t;
  using half = PacketXi;
  enum {
    size = kFloatPacketSize,
  };
};
template <>
struct unpacket_traits<PacketXl> : generic_unpacket_traits {
  using type = int64_t;
  using half = PacketXl;
  enum {
    size = kDoublePacketSize,
  };
};

namespace detail {
// --- vector type helpers ---
template <typename VectorT>
struct ScalarTypeOfVector {
  using type = std::remove_all_extents_t<std::remove_reference_t<decltype(VectorT()[0])>>;
};

template <typename VectorT>
using scalar_type_of_vector_t = typename ScalarTypeOfVector<VectorT>::type;

template <typename VectorType>
struct UnsignedVectorHelper {
  static VectorType v;
  static constexpr int n = __builtin_vectorelements(v);
  using UnsignedScalar = std::make_unsigned_t<scalar_type_of_vector_t<VectorType>>;
  using type = UnsignedScalar __attribute__((ext_vector_type(n), aligned(n * sizeof(UnsignedScalar))));
};

template <typename VectorT>
using unsigned_vector_t = typename UnsignedVectorHelper<VectorT>::type;

template <typename VectorT>
using HalfPacket = VectorType<typename unpacket_traits<VectorT>::type, unpacket_traits<VectorT>::size / 2>;

template <typename VectorT>
using QuarterPacket = VectorType<typename unpacket_traits<VectorT>::type, unpacket_traits<VectorT>::size / 4>;

// load and store helpers.
template <typename VectorT>
EIGEN_STRONG_INLINE VectorT load_vector_unaligned(const scalar_type_of_vector_t<VectorT>* from) {
  VectorT to;
  __builtin_memcpy(&to, from, sizeof(VectorT));
  return to;
}

template <typename VectorT>
EIGEN_STRONG_INLINE VectorT load_vector_aligned(const scalar_type_of_vector_t<VectorT>* from) {
  eigen_assert((std::uintptr_t(from) % alignof(VectorT) == 0) && "load_vector_aligned");
  return *reinterpret_cast<const VectorT*>(assume_aligned<alignof(VectorT)>(from));
}

template <typename VectorT>
EIGEN_STRONG_INLINE void store_vector_unaligned(scalar_type_of_vector_t<VectorT>* to, const VectorT& from) {
  __builtin_memcpy(to, &from, sizeof(VectorT));
}

template <typename VectorT>
EIGEN_STRONG_INLINE void store_vector_aligned(scalar_type_of_vector_t<VectorT>* to, const VectorT& from) {
  eigen_assert((std::uintptr_t(to) % alignof(VectorT) == 0) && "store_vector_aligned");
  *reinterpret_cast<VectorT*>(assume_aligned<alignof(VectorT)>(to)) = from;
}

}  // namespace detail

// --- Intrinsic-like specializations ---

// --- Load/Store operations ---
#define EIGEN_CLANG_PACKET_LOAD_STORE_PACKET(PACKET_TYPE)                                                         \
  template <>                                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE ploadu<PACKET_TYPE>(const detail::scalar_type_of_vector_t<PACKET_TYPE>* from) { \
    return detail::load_vector_unaligned<PACKET_TYPE>(from);                                                      \
  }                                                                                                               \
  template <>                                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pload<PACKET_TYPE>(const detail::scalar_type_of_vector_t<PACKET_TYPE>* from) {  \
    return detail::load_vector_aligned<PACKET_TYPE>(from);                                                        \
  }                                                                                                               \
  template <>                                                                                                     \
  EIGEN_STRONG_INLINE void pstoreu<detail::scalar_type_of_vector_t<PACKET_TYPE>, PACKET_TYPE>(                    \
      detail::scalar_type_of_vector_t<PACKET_TYPE> * to, const PACKET_TYPE& from) {                               \
    detail::store_vector_unaligned<PACKET_TYPE>(to, from);                                                        \
  }                                                                                                               \
  template <>                                                                                                     \
  EIGEN_STRONG_INLINE void pstore<detail::scalar_type_of_vector_t<PACKET_TYPE>, PACKET_TYPE>(                     \
      detail::scalar_type_of_vector_t<PACKET_TYPE> * to, const PACKET_TYPE& from) {                               \
    detail::store_vector_aligned<PACKET_TYPE>(to, from);                                                          \
  }

EIGEN_CLANG_PACKET_LOAD_STORE_PACKET(PacketXf)
EIGEN_CLANG_PACKET_LOAD_STORE_PACKET(PacketXd)
EIGEN_CLANG_PACKET_LOAD_STORE_PACKET(PacketXi)
EIGEN_CLANG_PACKET_LOAD_STORE_PACKET(PacketXl)
#undef EIGEN_CLANG_PACKET_LOAD_STORE_PACKET

// --- Broadcast operation ---
template <>
EIGEN_STRONG_INLINE PacketXf pset1frombits<PacketXf>(uint32_t from) {
  return PacketXf(numext::bit_cast<float>(from));
}

template <>
EIGEN_STRONG_INLINE PacketXd pset1frombits<PacketXd>(uint64_t from) {
  return PacketXd(numext::bit_cast<double>(from));
}

#define EIGEN_CLANG_PACKET_SET1(PACKET_TYPE)                                                            \
  template <>                                                                                           \
  EIGEN_STRONG_INLINE PACKET_TYPE pset1<PACKET_TYPE>(const unpacket_traits<PACKET_TYPE>::type& from) {  \
    return PACKET_TYPE(from);                                                                           \
  }                                                                                                     \
  template <>                                                                                           \
  EIGEN_STRONG_INLINE unpacket_traits<PACKET_TYPE>::type pfirst<PACKET_TYPE>(const PACKET_TYPE& from) { \
    return from[0];                                                                                     \
  }

EIGEN_CLANG_PACKET_SET1(PacketXf)
EIGEN_CLANG_PACKET_SET1(PacketXd)
EIGEN_CLANG_PACKET_SET1(PacketXi)
EIGEN_CLANG_PACKET_SET1(PacketXl)
#undef EIGEN_CLANG_PACKET_SET1

// --- Arithmetic operations ---
#define EIGEN_CLANG_PACKET_ARITHMETIC(PACKET_TYPE)                             \
  template <>                                                                  \
  EIGEN_STRONG_INLINE PACKET_TYPE pisnan<PACKET_TYPE>(const PACKET_TYPE& a) {  \
    return reinterpret_cast<PACKET_TYPE>(a != a);                              \
  }                                                                            \
  template <>                                                                  \
  EIGEN_STRONG_INLINE PACKET_TYPE pnegate<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return -a;                                                                 \
  }

EIGEN_CLANG_PACKET_ARITHMETIC(PacketXf)
EIGEN_CLANG_PACKET_ARITHMETIC(PacketXd)
EIGEN_CLANG_PACKET_ARITHMETIC(PacketXi)
EIGEN_CLANG_PACKET_ARITHMETIC(PacketXl)
#undef EIGEN_CLANG_PACKET_ARITHMETIC

// --- Bitwise operations (via casting) ---

namespace detail {

// Reinterpret-cast helpers, equivalent to preinterpret<> but defined here
// because PacketMath.h is included before TypeCasting.h.
EIGEN_STRONG_INLINE PacketXi preinterpret_float_to_int(const PacketXf& a) { return reinterpret_cast<PacketXi>(a); }
EIGEN_STRONG_INLINE PacketXf preinterpret_int_to_float(const PacketXi& a) { return reinterpret_cast<PacketXf>(a); }
EIGEN_STRONG_INLINE PacketXl preinterpret_double_to_long(const PacketXd& a) { return reinterpret_cast<PacketXl>(a); }
EIGEN_STRONG_INLINE PacketXd preinterpret_long_to_double(const PacketXl& a) { return reinterpret_cast<PacketXd>(a); }

}  // namespace detail

// Bitwise ops for integer packets
#define EIGEN_CLANG_PACKET_BITWISE_INT(PACKET_TYPE)                                                  \
  template <>                                                                                        \
  constexpr EIGEN_STRONG_INLINE PACKET_TYPE pzero<PACKET_TYPE>(const PACKET_TYPE& /*unused*/) {      \
    return PACKET_TYPE(0);                                                                           \
  }                                                                                                  \
  template <>                                                                                        \
  constexpr EIGEN_STRONG_INLINE PACKET_TYPE ptrue<PACKET_TYPE>(const PACKET_TYPE& /*unused*/) {      \
    return numext::bit_cast<PACKET_TYPE>(PACKET_TYPE(0) == PACKET_TYPE(0));                          \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pand<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {    \
    return a & b;                                                                                    \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE por<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {     \
    return a | b;                                                                                    \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pxor<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {    \
    return a ^ b;                                                                                    \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pandnot<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return a & ~b;                                                                                   \
  }                                                                                                  \
  template <int N>                                                                                   \
  EIGEN_STRONG_INLINE PACKET_TYPE parithmetic_shift_right(const PACKET_TYPE& a) {                    \
    return a >> N;                                                                                   \
  }                                                                                                  \
  template <int N>                                                                                   \
  EIGEN_STRONG_INLINE PACKET_TYPE plogical_shift_right(const PACKET_TYPE& a) {                       \
    using UnsignedT = detail::unsigned_vector_t<PACKET_TYPE>;                                        \
    return reinterpret_cast<PACKET_TYPE>(reinterpret_cast<UnsignedT>(a) >> N);                       \
  }                                                                                                  \
  template <int N>                                                                                   \
  EIGEN_STRONG_INLINE PACKET_TYPE plogical_shift_left(const PACKET_TYPE& a) {                        \
    return a << N;                                                                                   \
  }

EIGEN_CLANG_PACKET_BITWISE_INT(PacketXi)
EIGEN_CLANG_PACKET_BITWISE_INT(PacketXl)
#undef EIGEN_CLANG_PACKET_BITWISE_INT

// Bitwise ops for floating point packets
#define EIGEN_CLANG_PACKET_BITWISE_FLOAT(PACKET_TYPE, CAST_TO_INT, CAST_FROM_INT)                    \
  template <>                                                                                        \
  constexpr EIGEN_STRONG_INLINE PACKET_TYPE pzero<PACKET_TYPE>(const PACKET_TYPE& /*unused*/) {      \
    using Scalar = detail::scalar_type_of_vector_t<PACKET_TYPE>;                                     \
    return PACKET_TYPE(Scalar(0));                                                                   \
  }                                                                                                  \
  template <>                                                                                        \
  constexpr EIGEN_STRONG_INLINE PACKET_TYPE ptrue<PACKET_TYPE>(const PACKET_TYPE& /* unused */) {    \
    using Scalar = detail::scalar_type_of_vector_t<PACKET_TYPE>;                                     \
    return numext::bit_cast<PACKET_TYPE>(PACKET_TYPE(Scalar(0)) == PACKET_TYPE(Scalar(0)));          \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pand<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {    \
    return CAST_FROM_INT(CAST_TO_INT(a) & CAST_TO_INT(b));                                           \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE por<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {     \
    return CAST_FROM_INT(CAST_TO_INT(a) | CAST_TO_INT(b));                                           \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pxor<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {    \
    return CAST_FROM_INT(CAST_TO_INT(a) ^ CAST_TO_INT(b));                                           \
  }                                                                                                  \
  template <>                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pandnot<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return CAST_FROM_INT(CAST_TO_INT(a) & ~CAST_TO_INT(b));                                          \
  }

EIGEN_CLANG_PACKET_BITWISE_FLOAT(PacketXf, detail::preinterpret_float_to_int, detail::preinterpret_int_to_float)
EIGEN_CLANG_PACKET_BITWISE_FLOAT(PacketXd, detail::preinterpret_double_to_long, detail::preinterpret_long_to_double)
#undef EIGEN_CLANG_PACKET_BITWISE_FLOAT

// --- Comparison operations ---
// Clang vector extensions perform comparisons in the original type (float/double),
// returning an int vector with all-ones (-1) for true and all-zeros for false.
// The bit_cast reinterprets those int bitmasks as float packets, which is the
// format expected by pselect and other Eigen packet operations.
#define EIGEN_CLANG_PACKET_CMP(PACKET_TYPE, INT_PACKET_TYPE)                                                \
  template <>                                                                                               \
  EIGEN_STRONG_INLINE PACKET_TYPE pcmp_eq<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {        \
    return numext::bit_cast<PACKET_TYPE>(INT_PACKET_TYPE(a == b));                                          \
  }                                                                                                         \
  template <>                                                                                               \
  EIGEN_STRONG_INLINE PACKET_TYPE pcmp_lt<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {        \
    return numext::bit_cast<PACKET_TYPE>(INT_PACKET_TYPE(a < b));                                           \
  }                                                                                                         \
  template <>                                                                                               \
  EIGEN_STRONG_INLINE PACKET_TYPE pcmp_le<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {        \
    return numext::bit_cast<PACKET_TYPE>(INT_PACKET_TYPE(a <= b));                                          \
  }                                                                                                         \
  template <>                                                                                               \
  EIGEN_STRONG_INLINE PACKET_TYPE pcmp_lt_or_nan<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return numext::bit_cast<PACKET_TYPE>(INT_PACKET_TYPE(!(a >= b)));                                       \
  }

EIGEN_CLANG_PACKET_CMP(PacketXf, PacketXi)
EIGEN_CLANG_PACKET_CMP(PacketXd, PacketXl)
#undef EIGEN_CLANG_PACKET_CMP

// --- Min/Max operations ---
#if EIGEN_HAS_BUILTIN(__builtin_elementwise_min) && EIGEN_HAS_BUILTIN(__builtin_elementwise_max) && \
    EIGEN_HAS_BUILTIN(__builtin_elementwise_abs)
#define EIGEN_CLANG_PACKET_ELEMENTWISE(PACKET_TYPE)                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pmin<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {                   \
    /* Match NaN propagation of std::min. */                                                                        \
    return a == a ? __builtin_elementwise_min(a, b) : a;                                                            \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pmax<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {                   \
    /* Match NaN propagation of std::max. */                                                                        \
    return a == a ? __builtin_elementwise_max(a, b) : a;                                                            \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pmin<PropagateNumbers, PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return __builtin_elementwise_min(a, b);                                                                         \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pmax<PropagateNumbers, PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) { \
    return __builtin_elementwise_max(a, b);                                                                         \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pmin<PropagateNaN, PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {     \
    return a != a ? a : (b != b ? b : __builtin_elementwise_min(a, b));                                             \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pmax<PropagateNaN, PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b) {     \
    return a != a ? a : (b != b ? b : __builtin_elementwise_max(a, b));                                             \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pabs<PACKET_TYPE>(const PACKET_TYPE& a) {                                         \
    return __builtin_elementwise_abs(a);                                                                            \
  }                                                                                                                 \
  template <>                                                                                                       \
  EIGEN_STRONG_INLINE PACKET_TYPE pselect<PACKET_TYPE>(const PACKET_TYPE& mask, const PACKET_TYPE& a,               \
                                                       const PACKET_TYPE& b) {                                      \
    return mask != 0 ? a : b;                                                                                       \
  }

EIGEN_CLANG_PACKET_ELEMENTWISE(PacketXf)
EIGEN_CLANG_PACKET_ELEMENTWISE(PacketXd)
EIGEN_CLANG_PACKET_ELEMENTWISE(PacketXi)
EIGEN_CLANG_PACKET_ELEMENTWISE(PacketXl)
#undef EIGEN_CLANG_PACKET_ELEMENTWISE
#endif

// --- Math functions (float/double only) ---

#if EIGEN_HAS_BUILTIN(__builtin_elementwise_floor) && EIGEN_HAS_BUILTIN(__builtin_elementwise_ceil) &&      \
    EIGEN_HAS_BUILTIN(__builtin_elementwise_round) && EIGEN_HAS_BUILTIN(__builtin_elementwise_roundeven) && \
    EIGEN_HAS_BUILTIN(__builtin_elementwise_trunc) && EIGEN_HAS_BUILTIN(__builtin_elementwise_sqrt)
#define EIGEN_CLANG_PACKET_MATH_FLOAT(PACKET_TYPE)                            \
  template <>                                                                 \
  EIGEN_STRONG_INLINE PACKET_TYPE pfloor<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return __builtin_elementwise_floor(a);                                    \
  }                                                                           \
  template <>                                                                 \
  EIGEN_STRONG_INLINE PACKET_TYPE pceil<PACKET_TYPE>(const PACKET_TYPE& a) {  \
    return __builtin_elementwise_ceil(a);                                     \
  }                                                                           \
  template <>                                                                 \
  EIGEN_STRONG_INLINE PACKET_TYPE pround<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return __builtin_elementwise_round(a);                                    \
  }                                                                           \
  template <>                                                                 \
  EIGEN_STRONG_INLINE PACKET_TYPE print<PACKET_TYPE>(const PACKET_TYPE& a) {  \
    return __builtin_elementwise_roundeven(a);                                \
  }                                                                           \
  template <>                                                                 \
  EIGEN_STRONG_INLINE PACKET_TYPE ptrunc<PACKET_TYPE>(const PACKET_TYPE& a) { \
    return __builtin_elementwise_trunc(a);                                    \
  }                                                                           \
  template <>                                                                 \
  EIGEN_STRONG_INLINE PACKET_TYPE psqrt<PACKET_TYPE>(const PACKET_TYPE& a) {  \
    return __builtin_elementwise_sqrt(a);                                     \
  }

EIGEN_CLANG_PACKET_MATH_FLOAT(PacketXf)
EIGEN_CLANG_PACKET_MATH_FLOAT(PacketXd)
#undef EIGEN_CLANG_PACKET_MATH_FLOAT
#endif

// --- Fused Multiply-Add (MADD) ---
#if defined(__FMA__) && EIGEN_HAS_BUILTIN(__builtin_elementwise_fma)
#define EIGEN_CLANG_PACKET_MADD(PACKET_TYPE)                                                      \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pmadd<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b,  \
                                                     const PACKET_TYPE& c) {                      \
    return __builtin_elementwise_fma(a, b, c);                                                    \
  }                                                                                               \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pmsub<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b,  \
                                                     const PACKET_TYPE& c) {                      \
    return __builtin_elementwise_fma(a, b, -c);                                                   \
  }                                                                                               \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pnmadd<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b, \
                                                      const PACKET_TYPE& c) {                     \
    return __builtin_elementwise_fma(-a, b, c);                                                   \
  }                                                                                               \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pnmsub<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b, \
                                                      const PACKET_TYPE& c) {                     \
    return -(__builtin_elementwise_fma(a, b, c));                                                 \
  }
#else
// Fallback if FMA builtin is not available
#define EIGEN_CLANG_PACKET_MADD(PACKET_TYPE)                                                      \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pmadd<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b,  \
                                                     const PACKET_TYPE& c) {                      \
    return (a * b) + c;                                                                           \
  }                                                                                               \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pmsub<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b,  \
                                                     const PACKET_TYPE& c) {                      \
    return (a * b) - c;                                                                           \
  }                                                                                               \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pnmadd<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b, \
                                                      const PACKET_TYPE& c) {                     \
    return c - (a * b);                                                                           \
  }                                                                                               \
  template <>                                                                                     \
  EIGEN_STRONG_INLINE PACKET_TYPE pnmsub<PACKET_TYPE>(const PACKET_TYPE& a, const PACKET_TYPE& b, \
                                                      const PACKET_TYPE& c) {                     \
    return -((a * b) + c);                                                                        \
  }
#endif

EIGEN_CLANG_PACKET_MADD(PacketXf)
EIGEN_CLANG_PACKET_MADD(PacketXd)
#undef EIGEN_CLANG_PACKET_MADD

#define EIGEN_CLANG_PACKET_SCATTER_GATHER(PACKET_TYPE)                                                               \
  template <>                                                                                                        \
  EIGEN_STRONG_INLINE void pscatter(unpacket_traits<PACKET_TYPE>::type* to, const PACKET_TYPE& from, Index stride) { \
    constexpr int size = unpacket_traits<PACKET_TYPE>::size;                                                         \
    for (int i = 0; i < size; ++i) {                                                                                 \
      to[i * stride] = from[i];                                                                                      \
    }                                                                                                                \
  }                                                                                                                  \
  template <>                                                                                                        \
  EIGEN_STRONG_INLINE PACKET_TYPE pgather<typename unpacket_traits<PACKET_TYPE>::type, PACKET_TYPE>(                 \
      const unpacket_traits<PACKET_TYPE>::type* from, Index stride) {                                                \
    constexpr int size = unpacket_traits<PACKET_TYPE>::size;                                                         \
    PACKET_TYPE result;                                                                                              \
    for (int i = 0; i < size; ++i) {                                                                                 \
      result[i] = from[i * stride];                                                                                  \
    }                                                                                                                \
    return result;                                                                                                   \
  }

EIGEN_CLANG_PACKET_SCATTER_GATHER(PacketXf)
EIGEN_CLANG_PACKET_SCATTER_GATHER(PacketXd)
EIGEN_CLANG_PACKET_SCATTER_GATHER(PacketXi)
EIGEN_CLANG_PACKET_SCATTER_GATHER(PacketXl)

#undef EIGEN_CLANG_PACKET_SCATTER_GATHER

// ---- Various operations that depend on __builtin_shufflevector.
#if EIGEN_HAS_BUILTIN(__builtin_shufflevector)
namespace detail {
template <typename Packet>
EIGEN_STRONG_INLINE Packet preverse_impl_2(const Packet& a) {
  return __builtin_shufflevector(a, a, 1, 0);
}
template <typename Packet>
EIGEN_STRONG_INLINE Packet preverse_impl_4(const Packet& a) {
  return __builtin_shufflevector(a, a, 3, 2, 1, 0);
}
template <typename Packet>
EIGEN_STRONG_INLINE Packet preverse_impl_8(const Packet& a) {
  return __builtin_shufflevector(a, a, 7, 6, 5, 4, 3, 2, 1, 0);
}
template <typename Packet>
EIGEN_STRONG_INLINE Packet preverse_impl_16(const Packet& a) {
  return __builtin_shufflevector(a, a, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0);
}
}  // namespace detail

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXf preverse<PacketXf>(const PacketXf& a) {
  return detail::preverse_impl_4(a);
}
template <>
EIGEN_STRONG_INLINE PacketXd preverse<PacketXd>(const PacketXd& a) {
  return detail::preverse_impl_2(a);
}
template <>
EIGEN_STRONG_INLINE PacketXi preverse<PacketXi>(const PacketXi& a) {
  return detail::preverse_impl_4(a);
}
template <>
EIGEN_STRONG_INLINE PacketXl preverse<PacketXl>(const PacketXl& a) {
  return detail::preverse_impl_2(a);
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXf preverse<PacketXf>(const PacketXf& a) {
  return detail::preverse_impl_8(a);
}
template <>
EIGEN_STRONG_INLINE PacketXd preverse<PacketXd>(const PacketXd& a) {
  return detail::preverse_impl_4(a);
}
template <>
EIGEN_STRONG_INLINE PacketXi preverse<PacketXi>(const PacketXi& a) {
  return detail::preverse_impl_8(a);
}
template <>
EIGEN_STRONG_INLINE PacketXl preverse<PacketXl>(const PacketXl& a) {
  return detail::preverse_impl_4(a);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXf preverse<PacketXf>(const PacketXf& a) {
  return detail::preverse_impl_16(a);
}
template <>
EIGEN_STRONG_INLINE PacketXd preverse<PacketXd>(const PacketXd& a) {
  return detail::preverse_impl_8(a);
}
template <>
EIGEN_STRONG_INLINE PacketXi preverse<PacketXi>(const PacketXi& a) {
  return detail::preverse_impl_16(a);
}
template <>
EIGEN_STRONG_INLINE PacketXl preverse<PacketXl>(const PacketXl& a) {
  return detail::preverse_impl_8(a);
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

namespace detail {

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploaddup2(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 2 == 0, "Packet size must be a multiple of 2");
  using HalfPacket = HalfPacket<Packet>;
  HalfPacket a = load_vector_unaligned<HalfPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0);
}

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploaddup4(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 2 == 0, "Packet size must be a multiple of 2");
  using HalfPacket = HalfPacket<Packet>;
  HalfPacket a = load_vector_unaligned<HalfPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0, 1, 1);
}

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploaddup8(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 2 == 0, "Packet size must be a multiple of 2");
  using HalfPacket = HalfPacket<Packet>;
  HalfPacket a = load_vector_unaligned<HalfPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0, 1, 1, 2, 2, 3, 3);
}

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploaddup16(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 2 == 0, "Packet size must be a multiple of 2");
  using HalfPacket = HalfPacket<Packet>;
  HalfPacket a = load_vector_unaligned<HalfPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7);
}

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploadquad4(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 4 == 0, "Packet size must be a multiple of 4");
  using QuarterPacket = QuarterPacket<Packet>;
  QuarterPacket a = load_vector_unaligned<QuarterPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0, 0, 0);
}

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploadquad8(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 4 == 0, "Packet size must be a multiple of 4");
  using QuarterPacket = QuarterPacket<Packet>;
  QuarterPacket a = load_vector_unaligned<QuarterPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0, 0, 0, 1, 1, 1, 1);
}

template <typename Packet>
EIGEN_STRONG_INLINE Packet ploadquad16(const typename unpacket_traits<Packet>::type* from) {
  static_assert((unpacket_traits<Packet>::size) % 4 == 0, "Packet size must be a multiple of 4");
  using QuarterPacket = QuarterPacket<Packet>;
  QuarterPacket a = load_vector_unaligned<QuarterPacket>(from);
  return __builtin_shufflevector(a, a, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3);
}

}  // namespace detail

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXf ploaddup<PacketXf>(const float* from) {
  return detail::ploaddup4<PacketXf>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXd ploaddup<PacketXd>(const double* from) {
  return detail::ploaddup2<PacketXd>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXi ploaddup<PacketXi>(const int32_t* from) {
  return detail::ploaddup4<PacketXi>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXl ploaddup<PacketXl>(const int64_t* from) {
  return detail::ploaddup2<PacketXl>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXf ploadquad<PacketXf>(const float* from) {
  return detail::ploadquad4<PacketXf>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXi ploadquad<PacketXi>(const int32_t* from) {
  return detail::ploadquad4<PacketXi>(from);
}
// No ploadquad for 2-element packets (PacketXd, PacketXl) at 16 bytes.

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXf ploaddup<PacketXf>(const float* from) {
  return detail::ploaddup8<PacketXf>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXd ploaddup<PacketXd>(const double* from) {
  return detail::ploaddup4<PacketXd>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXi ploaddup<PacketXi>(const int32_t* from) {
  return detail::ploaddup8<PacketXi>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXl ploaddup<PacketXl>(const int64_t* from) {
  return detail::ploaddup4<PacketXl>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXf ploadquad<PacketXf>(const float* from) {
  return detail::ploadquad8<PacketXf>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXd ploadquad<PacketXd>(const double* from) {
  return detail::ploadquad4<PacketXd>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXi ploadquad<PacketXi>(const int32_t* from) {
  return detail::ploadquad8<PacketXi>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXl ploadquad<PacketXl>(const int64_t* from) {
  return detail::ploadquad4<PacketXl>(from);
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXf ploaddup<PacketXf>(const float* from) {
  return detail::ploaddup16<PacketXf>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXd ploaddup<PacketXd>(const double* from) {
  return detail::ploaddup8<PacketXd>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXi ploaddup<PacketXi>(const int32_t* from) {
  return detail::ploaddup16<PacketXi>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXl ploaddup<PacketXl>(const int64_t* from) {
  return detail::ploaddup8<PacketXl>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXf ploadquad<PacketXf>(const float* from) {
  return detail::ploadquad16<PacketXf>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXd ploadquad<PacketXd>(const double* from) {
  return detail::ploadquad8<PacketXd>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXi ploadquad<PacketXi>(const int32_t* from) {
  return detail::ploadquad16<PacketXi>(from);
}
template <>
EIGEN_STRONG_INLINE PacketXl ploadquad<PacketXl>(const int64_t* from) {
  return detail::ploadquad8<PacketXl>(from);
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- plset ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXf plset<PacketXf>(const float& a) {
  return PacketXf{a + 0.0f, a + 1.0f, a + 2.0f, a + 3.0f};
}
template <>
EIGEN_STRONG_INLINE PacketXd plset<PacketXd>(const double& a) {
  return PacketXd{a + 0.0, a + 1.0};
}
template <>
EIGEN_STRONG_INLINE PacketXi plset<PacketXi>(const int32_t& a) {
  return PacketXi{a + 0, a + 1, a + 2, a + 3};
}
template <>
EIGEN_STRONG_INLINE PacketXl plset<PacketXl>(const int64_t& a) {
  return PacketXl{a + 0, a + 1};
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXf plset<PacketXf>(const float& a) {
  return PacketXf{a + 0.0f, a + 1.0f, a + 2.0f, a + 3.0f, a + 4.0f, a + 5.0f, a + 6.0f, a + 7.0f};
}
template <>
EIGEN_STRONG_INLINE PacketXd plset<PacketXd>(const double& a) {
  return PacketXd{a + 0.0, a + 1.0, a + 2.0, a + 3.0};
}
template <>
EIGEN_STRONG_INLINE PacketXi plset<PacketXi>(const int32_t& a) {
  return PacketXi{a + 0, a + 1, a + 2, a + 3, a + 4, a + 5, a + 6, a + 7};
}
template <>
EIGEN_STRONG_INLINE PacketXl plset<PacketXl>(const int64_t& a) {
  return PacketXl{a + 0, a + 1, a + 2, a + 3};
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXf plset<PacketXf>(const float& a) {
  return PacketXf{a + 0.0f, a + 1.0f, a + 2.0f,  a + 3.0f,  a + 4.0f,  a + 5.0f,  a + 6.0f,  a + 7.0f,
                  a + 8.0f, a + 9.0f, a + 10.0f, a + 11.0f, a + 12.0f, a + 13.0f, a + 14.0f, a + 15.0f};
}
template <>
EIGEN_STRONG_INLINE PacketXd plset<PacketXd>(const double& a) {
  return PacketXd{a + 0.0, a + 1.0, a + 2.0, a + 3.0, a + 4.0, a + 5.0, a + 6.0, a + 7.0};
}
template <>
EIGEN_STRONG_INLINE PacketXi plset<PacketXi>(const int32_t& a) {
  return PacketXi{a + 0, a + 1, a + 2,  a + 3,  a + 4,  a + 5,  a + 6,  a + 7,
                  a + 8, a + 9, a + 10, a + 11, a + 12, a + 13, a + 14, a + 15};
}
template <>
EIGEN_STRONG_INLINE PacketXl plset<PacketXl>(const int64_t& a) {
  return PacketXl{a + 0, a + 1, a + 2, a + 3, a + 4, a + 5, a + 6, a + 7};
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// --- peven_mask ---
#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16

template <>
EIGEN_STRONG_INLINE PacketXf peven_mask(const PacketXf& /* unused */) {
  float kTrue = numext::bit_cast<float>(int32_t(-1));
  float kFalse = 0.0f;
  return PacketXf{kTrue, kFalse, kTrue, kFalse};
}
template <>
EIGEN_STRONG_INLINE PacketXd peven_mask(const PacketXd& /* unused */) {
  double kTrue = numext::bit_cast<double>(int64_t(-1l));
  double kFalse = 0.0;
  return PacketXd{kTrue, kFalse};
}

#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32

template <>
EIGEN_STRONG_INLINE PacketXf peven_mask(const PacketXf& /* unused */) {
  float kTrue = numext::bit_cast<float>(int32_t(-1));
  float kFalse = 0.0f;
  return PacketXf{kTrue, kFalse, kTrue, kFalse, kTrue, kFalse, kTrue, kFalse};
}
template <>
EIGEN_STRONG_INLINE PacketXd peven_mask(const PacketXd& /* unused */) {
  double kTrue = numext::bit_cast<double>(int64_t(-1l));
  double kFalse = 0.0;
  return PacketXd{kTrue, kFalse, kTrue, kFalse};
}

#else  // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64

template <>
EIGEN_STRONG_INLINE PacketXf peven_mask(const PacketXf& /* unused */) {
  float kTrue = numext::bit_cast<float>(int32_t(-1));
  float kFalse = 0.0f;
  return PacketXf{kTrue, kFalse, kTrue, kFalse, kTrue, kFalse, kTrue, kFalse,
                  kTrue, kFalse, kTrue, kFalse, kTrue, kFalse, kTrue, kFalse};
}
template <>
EIGEN_STRONG_INLINE PacketXd peven_mask(const PacketXd& /* unused */) {
  double kTrue = numext::bit_cast<double>(int64_t(-1l));
  double kFalse = 0.0;
  return PacketXd{kTrue, kFalse, kTrue, kFalse, kTrue, kFalse, kTrue, kFalse};
}

#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

// Helpers for ptranspose.
namespace detail {

template <typename Packet>
EIGEN_ALWAYS_INLINE void zip_in_place2(Packet& p1, Packet& p2) {
  Packet tmp = __builtin_shufflevector(p1, p2, 0, 2);
  p2 = __builtin_shufflevector(p1, p2, 1, 3);
  p1 = tmp;
}

template <typename Packet>
EIGEN_ALWAYS_INLINE void zip_in_place4(Packet& p1, Packet& p2) {
  Packet tmp = __builtin_shufflevector(p1, p2, 0, 4, 1, 5);
  p2 = __builtin_shufflevector(p1, p2, 2, 6, 3, 7);
  p1 = tmp;
}

template <typename Packet>
EIGEN_ALWAYS_INLINE void zip_in_place8(Packet& p1, Packet& p2) {
  Packet tmp = __builtin_shufflevector(p1, p2, 0, 8, 1, 9, 2, 10, 3, 11);
  p2 = __builtin_shufflevector(p1, p2, 4, 12, 5, 13, 6, 14, 7, 15);
  p1 = tmp;
}

template <typename Packet>
EIGEN_ALWAYS_INLINE void zip_in_place16(Packet& p1, Packet& p2) {
  Packet tmp = __builtin_shufflevector(p1, p2, 0, 16, 1, 17, 2, 18, 3, 19, 4, 20, 5, 21, 6, 22, 7, 23);
  p2 = __builtin_shufflevector(p1, p2, 8, 24, 9, 25, 10, 26, 11, 27, 12, 28, 13, 29, 14, 30, 15, 31);
  p1 = tmp;
}

template <typename Packet>
void zip_in_place(Packet& p1, Packet& p2);

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES == 16
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXf>(PacketXf& p1, PacketXf& p2) {
  zip_in_place4(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXd>(PacketXd& p1, PacketXd& p2) {
  zip_in_place2(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXi>(PacketXi& p1, PacketXi& p2) {
  zip_in_place4(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXl>(PacketXl& p1, PacketXl& p2) {
  zip_in_place2(p1, p2);
}
#elif EIGEN_GENERIC_VECTOR_SIZE_BYTES == 32
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXf>(PacketXf& p1, PacketXf& p2) {
  zip_in_place8(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXd>(PacketXd& p1, PacketXd& p2) {
  zip_in_place4(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXi>(PacketXi& p1, PacketXi& p2) {
  zip_in_place8(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXl>(PacketXl& p1, PacketXl& p2) {
  zip_in_place4(p1, p2);
}
#else   // EIGEN_GENERIC_VECTOR_SIZE_BYTES == 64
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXf>(PacketXf& p1, PacketXf& p2) {
  zip_in_place16(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXd>(PacketXd& p1, PacketXd& p2) {
  zip_in_place8(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXi>(PacketXi& p1, PacketXi& p2) {
  zip_in_place16(p1, p2);
}
template <>
EIGEN_ALWAYS_INLINE void zip_in_place<PacketXl>(PacketXl& p1, PacketXl& p2) {
  zip_in_place8(p1, p2);
}
#endif  // EIGEN_GENERIC_VECTOR_SIZE_BYTES

template <typename Packet>
EIGEN_ALWAYS_INLINE void ptranspose_impl(PacketBlock<Packet, 2>& kernel) {
  zip_in_place(kernel.packet[0], kernel.packet[1]);
}

template <typename Packet>
EIGEN_ALWAYS_INLINE void ptranspose_impl(PacketBlock<Packet, 4>& kernel) {
  zip_in_place(kernel.packet[0], kernel.packet[2]);
  zip_in_place(kernel.packet[1], kernel.packet[3]);
  zip_in_place(kernel.packet[0], kernel.packet[1]);
  zip_in_place(kernel.packet[2], kernel.packet[3]);
}

template <typename Packet>
EIGEN_ALWAYS_INLINE void ptranspose_impl(PacketBlock<Packet, 8>& kernel) {
  zip_in_place(kernel.packet[0], kernel.packet[4]);
  zip_in_place(kernel.packet[1], kernel.packet[5]);
  zip_in_place(kernel.packet[2], kernel.packet[6]);
  zip_in_place(kernel.packet[3], kernel.packet[7]);

  zip_in_place(kernel.packet[0], kernel.packet[2]);
  zip_in_place(kernel.packet[1], kernel.packet[3]);
  zip_in_place(kernel.packet[4], kernel.packet[6]);
  zip_in_place(kernel.packet[5], kernel.packet[7]);

  zip_in_place(kernel.packet[0], kernel.packet[1]);
  zip_in_place(kernel.packet[2], kernel.packet[3]);
  zip_in_place(kernel.packet[4], kernel.packet[5]);
  zip_in_place(kernel.packet[6], kernel.packet[7]);
}

template <typename Packet>
EIGEN_ALWAYS_INLINE void ptranspose_impl(PacketBlock<Packet, 16>& kernel) {
  EIGEN_UNROLL_LOOP
  for (int i = 0; i < 4; ++i) {
    const int m = (1 << i);
    EIGEN_UNROLL_LOOP
    for (int j = 0; j < m; ++j) {
      const int n = (1 << (3 - i));
      EIGEN_UNROLL_LOOP
      for (int k = 0; k < n; ++k) {
        const int idx = 2 * j * n + k;
        zip_in_place(kernel.packet[idx], kernel.packet[idx + n]);
      }
    }
  }
}

}  // namespace detail

// ptranspose overloads: only emit valid block sizes per vector size.
// At 16 bytes: float has 4 elems, double has 2 elems.
// At 32 bytes: float has 8 elems, double has 4 elems.
// At 64 bytes: float has 16 elems, double has 8 elems.

// All sizes support PacketBlock<PacketXf, 2> and PacketBlock<PacketXf, 4>.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXf, 4>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXf, 2>& kernel) {
  detail::ptranspose_impl(kernel);
}

// All sizes support PacketBlock<PacketXd, 2>.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXd, 2>& kernel) {
  detail::ptranspose_impl(kernel);
}

// All sizes support PacketBlock<PacketXi, 2> and PacketBlock<PacketXi, 4>.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXi, 4>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXi, 2>& kernel) {
  detail::ptranspose_impl(kernel);
}

// All sizes support PacketBlock<PacketXl, 2>.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXl, 2>& kernel) {
  detail::ptranspose_impl(kernel);
}

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 32
// 32+ bytes: float has 8+ elems, double has 4+ elems.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXf, 8>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXd, 4>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXi, 8>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXl, 4>& kernel) {
  detail::ptranspose_impl(kernel);
}
#endif

#if EIGEN_GENERIC_VECTOR_SIZE_BYTES >= 64
// 64 bytes: float has 16 elems, double has 8 elems.
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXf, 16>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXd, 8>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXi, 16>& kernel) {
  detail::ptranspose_impl(kernel);
}
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void ptranspose(PacketBlock<PacketXl, 8>& kernel) {
  detail::ptranspose_impl(kernel);
}
#endif
#endif

}  // end namespace internal
}  // end namespace Eigen

#endif  // EIGEN_PACKET_MATH_CLANG_H
