// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2025 Charlie Schlosser <cs.schlosser@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_REALVIEW_H
#define EIGEN_REALVIEW_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

// Write access and vectorization requires array-oriented access to the real and imaginary components.
// From https://en.cppreference.com/w/cpp/numeric/complex.html:
// For any pointer to an element of an array of std::complex<T> named p and any valid array index i,
// reinterpret_cast<T*>(p)[2 * i] is the real part of the complex number p[i], and
// reinterpret_cast<T*>(p)[2 * i + 1] is the imaginary part of the complex number p[i].

template <typename T>
struct complex_array_access : std::false_type {};
template <typename T>
struct complex_array_access<std::complex<T>> : std::true_type {};

template <typename Xpr>
struct traits<RealView<Xpr>> : public traits<Xpr> {
  template <typename T>
  static constexpr int double_size(T size, bool times_two) {
    int size_as_int = int(size);
    if (size_as_int == Dynamic) return Dynamic;
    return times_two ? (2 * size_as_int) : size_as_int;
  }

  using Base = traits<Xpr>;
  using ComplexScalar = typename Base::Scalar;
  using Scalar = typename NumTraits<ComplexScalar>::Real;

  static constexpr bool ArrayAccess = complex_array_access<ComplexScalar>::value;
  static constexpr int ActualDirectAccessBit = ArrayAccess ? DirectAccessBit : 0;
  static constexpr int ActualLvaluebit = !std::is_const<Xpr>::value && ArrayAccess ? LvalueBit : 0;
  static constexpr int ActualPacketAccessBit = packet_traits<Scalar>::Vectorizable ? PacketAccessBit : 0;
  static constexpr int FlagMask =
      ActualDirectAccessBit | ActualLvaluebit | ActualPacketAccessBit | HereditaryBits | LinearAccessBit;
  static constexpr int BaseFlags = int(evaluator<Xpr>::Flags) | int(Base::Flags);
  static constexpr int Flags = BaseFlags & FlagMask;
  static constexpr bool IsRowMajor = Flags & RowMajorBit;
  static constexpr int RowsAtCompileTime = double_size(Base::RowsAtCompileTime, !IsRowMajor);
  static constexpr int ColsAtCompileTime = double_size(Base::ColsAtCompileTime, IsRowMajor);
  static constexpr int SizeAtCompileTime = size_at_compile_time(RowsAtCompileTime, ColsAtCompileTime);
  static constexpr int MaxRowsAtCompileTime = double_size(Base::MaxRowsAtCompileTime, !IsRowMajor);
  static constexpr int MaxColsAtCompileTime = double_size(Base::MaxColsAtCompileTime, IsRowMajor);
  static constexpr int MaxSizeAtCompileTime = size_at_compile_time(MaxRowsAtCompileTime, MaxColsAtCompileTime);
  static constexpr int OuterStrideAtCompileTime = double_size(outer_stride_at_compile_time<Xpr>::value, true);
  static constexpr int InnerStrideAtCompileTime = inner_stride_at_compile_time<Xpr>::value;
};

template <typename Xpr>
struct evaluator<RealView<Xpr>> : private evaluator<Xpr> {
  using BaseEvaluator = evaluator<Xpr>;
  using XprType = RealView<Xpr>;
  using ExpressionTraits = traits<XprType>;
  using ComplexScalar = typename ExpressionTraits::ComplexScalar;
  using Scalar = typename ExpressionTraits::Scalar;

  static constexpr int Flags = ExpressionTraits::Flags;
  static constexpr int CoeffReadCost = BaseEvaluator::CoeffReadCost;
  static constexpr int Alignment = BaseEvaluator::Alignment;
  static constexpr bool IsRowMajor = ExpressionTraits::IsRowMajor;
  static constexpr bool DirectAccess = (Flags & DirectAccessBit) != 0;

  using ComplexCoeffReturnType = std::conditional_t<DirectAccess, const ComplexScalar&, ComplexScalar>;
  using CoeffReturnType = std::conditional_t<DirectAccess, const Scalar&, Scalar>;

  EIGEN_DEVICE_FUNC explicit evaluator(XprType realView) : BaseEvaluator(realView.m_xpr) {}

  template <bool Enable = DirectAccess, std::enable_if_t<!Enable, bool> = true>
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar coeff(Index row, Index col) const {
    Index r = IsRowMajor ? row : row / 2;
    Index c = IsRowMajor ? col / 2 : col;
    bool p = (IsRowMajor ? col : row) & 1;
    ComplexScalar ccoeff = BaseEvaluator::coeff(r, c);
    return p ? numext::imag(ccoeff) : numext::real(ccoeff);
  }
  template <bool Enable = DirectAccess, std::enable_if_t<Enable, bool> = true>
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE CoeffReturnType coeff(Index row, Index col) const {
    Index r = IsRowMajor ? row : row / 2;
    Index c = IsRowMajor ? col / 2 : col;
    Index p = (IsRowMajor ? col : row) & 1;
    ComplexCoeffReturnType ccoeff = BaseEvaluator::coeff(r, c);
    return reinterpret_cast<const Scalar(&)[2]>(ccoeff)[p];
  }
  template <bool Enable = DirectAccess, std::enable_if_t<!Enable, bool> = true>
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar coeff(Index index) const {
    ComplexScalar ccoeff = BaseEvaluator::coeff(index / 2);
    bool p = index & 1;
    return p ? numext::imag(ccoeff) : numext::real(ccoeff);
  }
  template <bool Enable = DirectAccess, std::enable_if_t<Enable, bool> = true>
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE CoeffReturnType coeff(Index index) const {
    ComplexCoeffReturnType ccoeff = BaseEvaluator::coeff(index / 2);
    Index p = index & 1;
    return reinterpret_cast<const Scalar(&)[2]>(ccoeff)[p];
  }
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar& coeffRef(Index row, Index col) {
    Index r = IsRowMajor ? row : row / 2;
    Index c = IsRowMajor ? col / 2 : col;
    Index p = (IsRowMajor ? col : row) & 1;
    ComplexScalar& ccoeffRef = BaseEvaluator::coeffRef(r, c);
    return reinterpret_cast<Scalar(&)[2]>(ccoeffRef)[p];
  }
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE Scalar& coeffRef(Index index) {
    ComplexScalar& ccoeffRef = BaseEvaluator::coeffRef(index / 2);
    Index p = index & 1;
    return reinterpret_cast<Scalar(&)[2]>(ccoeffRef)[p];
  }

  // If the first index is odd (imaginary), discard the first scalar
  // in 'result' and assign the missing scalar.
  // This operation is safe as the real component of the first scalar must exist.

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packet(Index row, Index col) const {
    constexpr int RealPacketSize = unpacket_traits<PacketType>::size;
    using ComplexPacket = typename find_packet_by_size<ComplexScalar, RealPacketSize / 2>::type;
    EIGEN_STATIC_ASSERT((find_packet_by_size<ComplexScalar, RealPacketSize / 2>::value),
                        MISSING COMPATIBLE COMPLEX PACKET TYPE)
    Index r = IsRowMajor ? row : row / 2;
    Index c = IsRowMajor ? col / 2 : col;
    bool p = (IsRowMajor ? col : row) & 1;
    ComplexPacket cresult = BaseEvaluator::template packet<LoadMode, ComplexPacket>(r, c);
    PacketType result = preinterpret<PacketType>(cresult);
    if (p) {
      Scalar aux[RealPacketSize + 1];
      pstoreu(aux, result);
      Index lastr = IsRowMajor ? row : row + RealPacketSize - 1;
      Index lastc = IsRowMajor ? col + RealPacketSize - 1 : col;
      aux[RealPacketSize] = coeff(lastr, lastc);
      result = ploadu<PacketType>(aux + 1);
    }
    return result;
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packet(Index index) const {
    constexpr int RealPacketSize = unpacket_traits<PacketType>::size;
    using ComplexPacket = typename find_packet_by_size<ComplexScalar, RealPacketSize / 2>::type;
    EIGEN_STATIC_ASSERT((find_packet_by_size<ComplexScalar, RealPacketSize / 2>::value),
                        MISSING COMPATIBLE COMPLEX PACKET TYPE)
    ComplexPacket cresult = BaseEvaluator::template packet<LoadMode, ComplexPacket>(index / 2);
    PacketType result = preinterpret<PacketType>(cresult);
    bool p = index & 1;
    if (p) {
      Scalar aux[RealPacketSize + 1];
      pstoreu(aux, result);
      aux[RealPacketSize] = coeff(index + RealPacketSize - 1);
      result = ploadu<PacketType>(aux + 1);
    }
    return result;
  }

  // The requested real packet segment forms the half-open interval [begin, end), where 'end' = 'begin' + 'count'.
  // In order to access the underlying complex array, even indices must be aligned with the real components
  // of the complex scalars. 'begin' and 'count' must be modified as follows:
  // a) 'begin' must be rounded down to the nearest even number; and
  // b) 'end' must be rounded up to the nearest even number.

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegment(Index row, Index col, Index begin, Index count) const {
    constexpr int RealPacketSize = unpacket_traits<PacketType>::size;
    using ComplexPacket = typename find_packet_by_size<ComplexScalar, RealPacketSize / 2>::type;
    EIGEN_STATIC_ASSERT((find_packet_by_size<ComplexScalar, RealPacketSize / 2>::value),
                        MISSING COMPATIBLE COMPLEX PACKET TYPE)
    Index actualBegin = numext::round_down(begin, 2);
    Index actualEnd = numext::round_down(begin + count + 1, 2);
    Index actualCount = actualEnd - actualBegin;
    Index r = IsRowMajor ? row : row / 2;
    Index c = IsRowMajor ? col / 2 : col;
    ComplexPacket cresult =
        BaseEvaluator::template packetSegment<LoadMode, ComplexPacket>(r, c, actualBegin / 2, actualCount / 2);
    PacketType result = preinterpret<PacketType>(cresult);
    bool p = (IsRowMajor ? col : row) & 1;
    if (p) {
      Scalar aux[RealPacketSize + 1] = {};
      pstoreu(aux, result);
      Index lastr = IsRowMajor ? row : row + actualEnd - 1;
      Index lastc = IsRowMajor ? col + actualEnd - 1 : col;
      aux[actualEnd] = coeff(lastr, lastc);
      result = ploadu<PacketType>(aux + 1);
    }
    return result;
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegment(Index index, Index begin, Index count) const {
    constexpr int RealPacketSize = unpacket_traits<PacketType>::size;
    using ComplexPacket = typename find_packet_by_size<ComplexScalar, RealPacketSize / 2>::type;
    EIGEN_STATIC_ASSERT((find_packet_by_size<ComplexScalar, RealPacketSize / 2>::value),
                        MISSING COMPATIBLE COMPLEX PACKET TYPE)
    Index actualBegin = numext::round_down(begin, 2);
    Index actualEnd = numext::round_down(begin + count + 1, 2);
    Index actualCount = actualEnd - actualBegin;
    ComplexPacket cresult =
        BaseEvaluator::template packetSegment<LoadMode, ComplexPacket>(index / 2, actualBegin / 2, actualCount / 2);
    PacketType result = preinterpret<PacketType>(cresult);
    bool p = index & 1;
    if (p) {
      Scalar aux[RealPacketSize + 1] = {};
      pstoreu(aux, result);
      aux[actualEnd] = coeff(index + actualEnd - 1);
      result = ploadu<PacketType>(aux + 1);
    }
    return result;
  }
};

}  // namespace internal

template <typename Xpr>
class RealView : public internal::dense_xpr_base<RealView<Xpr>>::type {
  using ExpressionTraits = internal::traits<RealView>;
  EIGEN_STATIC_ASSERT(NumTraits<typename Xpr::Scalar>::IsComplex, SCALAR MUST BE COMPLEX)
 public:
  using Scalar = typename ExpressionTraits::Scalar;
  using Nested = RealView;

  EIGEN_DEVICE_FUNC explicit RealView(Xpr& xpr) : m_xpr(xpr) {}
  EIGEN_DEVICE_FUNC constexpr Index rows() const noexcept { return Xpr::IsRowMajor ? m_xpr.rows() : 2 * m_xpr.rows(); }
  EIGEN_DEVICE_FUNC constexpr Index cols() const noexcept { return Xpr::IsRowMajor ? 2 * m_xpr.cols() : m_xpr.cols(); }
  EIGEN_DEVICE_FUNC constexpr Index size() const noexcept { return 2 * m_xpr.size(); }
  EIGEN_DEVICE_FUNC constexpr Index innerStride() const noexcept { return m_xpr.innerStride(); }
  EIGEN_DEVICE_FUNC constexpr Index outerStride() const noexcept { return 2 * m_xpr.outerStride(); }
  EIGEN_DEVICE_FUNC void resize(Index rows, Index cols) {
    m_xpr.resize(Xpr::IsRowMajor ? rows : rows / 2, Xpr::IsRowMajor ? cols / 2 : cols);
  }
  EIGEN_DEVICE_FUNC void resize(Index size) { m_xpr.resize(size / 2); }
  EIGEN_DEVICE_FUNC Scalar* data() { return reinterpret_cast<Scalar*>(m_xpr.data()); }
  EIGEN_DEVICE_FUNC const Scalar* data() const { return reinterpret_cast<const Scalar*>(m_xpr.data()); }

  EIGEN_DEVICE_FUNC RealView(const RealView&) = default;

  EIGEN_DEVICE_FUNC RealView& operator=(const RealView& other);

  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC RealView& operator=(const RealView<OtherDerived>& other);

  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC RealView& operator=(const DenseBase<OtherDerived>& other);

 protected:
  friend struct internal::evaluator<RealView>;
  Xpr& m_xpr;
};

template <typename Xpr>
EIGEN_DEVICE_FUNC RealView<Xpr>& RealView<Xpr>::operator=(const RealView& other) {
  internal::call_assignment(*this, other);
  return *this;
}

template <typename Xpr>
template <typename OtherDerived>
EIGEN_DEVICE_FUNC RealView<Xpr>& RealView<Xpr>::operator=(const RealView<OtherDerived>& other) {
  internal::call_assignment(*this, other);
  return *this;
}

template <typename Xpr>
template <typename OtherDerived>
EIGEN_DEVICE_FUNC RealView<Xpr>& RealView<Xpr>::operator=(const DenseBase<OtherDerived>& other) {
  internal::call_assignment(*this, other.derived());
  return *this;
}

template <typename Derived>
EIGEN_DEVICE_FUNC typename DenseBase<Derived>::RealViewReturnType DenseBase<Derived>::realView() {
  return RealViewReturnType(derived());
}

template <typename Derived>
EIGEN_DEVICE_FUNC typename DenseBase<Derived>::ConstRealViewReturnType DenseBase<Derived>::realView() const {
  return ConstRealViewReturnType(derived());
}

}  // namespace Eigen

#endif  // EIGEN_REALVIEW_H
