// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Pavel Guzenfeld
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_CONCAT_OP_H
#define EIGEN_CONCAT_OP_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <int Direction, typename LhsType, typename RhsType>
struct traits<Concat<Direction, LhsType, RhsType>> : traits<LhsType> {
  typedef typename LhsType::Scalar Scalar;
  typedef typename traits<LhsType>::StorageKind StorageKind;
  typedef typename traits<LhsType>::XprKind XprKind;
  typedef typename ref_selector<LhsType>::type LhsTypeNested;
  typedef typename ref_selector<RhsType>::type RhsTypeNested;
  typedef std::remove_reference_t<LhsTypeNested> LhsTypeNested_;
  typedef std::remove_reference_t<RhsTypeNested> RhsTypeNested_;
  enum {
    // For vertical concat (stacking rows): rows add up, cols must match
    // For horizontal concat (stacking cols): cols add up, rows must match
    LhsRows = int(LhsType::RowsAtCompileTime),
    RhsRows = int(RhsType::RowsAtCompileTime),
    LhsCols = int(LhsType::ColsAtCompileTime),
    RhsCols = int(RhsType::ColsAtCompileTime),

    RowsAtCompileTime = Direction == Vertical
                            ? (LhsRows == Dynamic || RhsRows == Dynamic ? int(Dynamic) : LhsRows + RhsRows)
                            : size_prefer_fixed(LhsRows, RhsRows),
    ColsAtCompileTime = Direction == Horizontal
                            ? (LhsCols == Dynamic || RhsCols == Dynamic ? int(Dynamic) : LhsCols + RhsCols)
                            : size_prefer_fixed(LhsCols, RhsCols),

    LhsMaxRows = int(LhsType::MaxRowsAtCompileTime),
    RhsMaxRows = int(RhsType::MaxRowsAtCompileTime),
    LhsMaxCols = int(LhsType::MaxColsAtCompileTime),
    RhsMaxCols = int(RhsType::MaxColsAtCompileTime),

    MaxRowsAtCompileTime =
        Direction == Vertical
            ? (LhsMaxRows == Dynamic || RhsMaxRows == Dynamic ? int(Dynamic) : LhsMaxRows + RhsMaxRows)
            : max_size_prefer_dynamic(LhsMaxRows, RhsMaxRows),
    MaxColsAtCompileTime =
        Direction == Horizontal
            ? (LhsMaxCols == Dynamic || RhsMaxCols == Dynamic ? int(Dynamic) : LhsMaxCols + RhsMaxCols)
            : max_size_prefer_dynamic(LhsMaxCols, RhsMaxCols),

    IsRowMajor = MaxRowsAtCompileTime == 1 && MaxColsAtCompileTime != 1   ? 1
                 : MaxColsAtCompileTime == 1 && MaxRowsAtCompileTime != 1 ? 0
                 : (int(LhsType::Flags) & RowMajorBit)                    ? 1
                                                                          : 0,
    Flags = IsRowMajor ? RowMajorBit : 0
  };
};

}  // namespace internal

/**
 * \class Concat
 * \ingroup Core_Module
 *
 * \brief Expression of the concatenation of two dense expressions
 *
 * \tparam Direction either \c Vertical or \c Horizontal
 * \tparam LhsType the type of the left-hand side expression
 * \tparam RhsType the type of the right-hand side expression
 *
 * This class represents an expression of the concatenation of two dense expressions,
 * either vertically (stacking rows) or horizontally (stacking columns).
 *
 * It is the return type of hcat() and vcat() and typically this is the only way it is used.
 *
 * \sa hcat(), vcat()
 */
template <int Direction, typename LhsType, typename RhsType>
class Concat : public internal::dense_xpr_base<Concat<Direction, LhsType, RhsType>>::type {
  typedef typename internal::traits<Concat>::LhsTypeNested LhsTypeNested;
  typedef typename internal::traits<Concat>::RhsTypeNested RhsTypeNested;
  typedef typename internal::traits<Concat>::LhsTypeNested_ LhsTypeNested_;
  typedef typename internal::traits<Concat>::RhsTypeNested_ RhsTypeNested_;

 public:
  typedef typename internal::dense_xpr_base<Concat>::type Base;
  EIGEN_DENSE_PUBLIC_INTERFACE(Concat)
  typedef internal::remove_all_t<LhsType> LhsNestedExpression;
  typedef internal::remove_all_t<RhsType> RhsNestedExpression;

  template <typename OriginalLhsType, typename OriginalRhsType>
  EIGEN_DEVICE_FUNC constexpr inline Concat(const OriginalLhsType& lhs, const OriginalRhsType& rhs)
      : m_lhs(lhs), m_rhs(rhs) {
    EIGEN_STATIC_ASSERT((std::is_same<std::remove_const_t<LhsType>, OriginalLhsType>::value),
                        THE_MATRIX_OR_EXPRESSION_THAT_YOU_PASSED_DOES_NOT_HAVE_THE_EXPECTED_TYPE)
    EIGEN_STATIC_ASSERT((std::is_same<std::remove_const_t<RhsType>, OriginalRhsType>::value),
                        THE_MATRIX_OR_EXPRESSION_THAT_YOU_PASSED_DOES_NOT_HAVE_THE_EXPECTED_TYPE)
    EIGEN_STATIC_ASSERT(
        (std::is_same<typename LhsType::Scalar, typename RhsType::Scalar>::value),
        YOU_MIXED_DIFFERENT_NUMERIC_TYPES__YOU_NEED_TO_USE_THE_CAST_METHOD_OF_MATRIXBASE_TO_CAST_NUMERIC_TYPES_EXPLICITLY)
    EIGEN_STATIC_ASSERT_SAME_XPR_KIND(LhsType, RhsType)
    EIGEN_STATIC_ASSERT(Direction != Horizontal || int(LhsType::RowsAtCompileTime) == Dynamic ||
                            int(RhsType::RowsAtCompileTime) == Dynamic ||
                            int(LhsType::RowsAtCompileTime) == int(RhsType::RowsAtCompileTime),
                        YOU_MIXED_MATRICES_OF_DIFFERENT_SIZES)
    EIGEN_STATIC_ASSERT(Direction != Vertical || int(LhsType::ColsAtCompileTime) == Dynamic ||
                            int(RhsType::ColsAtCompileTime) == Dynamic ||
                            int(LhsType::ColsAtCompileTime) == int(RhsType::ColsAtCompileTime),
                        YOU_MIXED_MATRICES_OF_DIFFERENT_SIZES)
    if (Direction == Vertical) {
      eigen_assert(lhs.cols() == rhs.cols() && "vcat: number of columns must match");
    } else {
      eigen_assert(lhs.rows() == rhs.rows() && "hcat: number of rows must match");
    }
  }

  EIGEN_DEVICE_FUNC constexpr Index rows() const {
    return Direction == Vertical ? m_lhs.rows() + m_rhs.rows() : m_lhs.rows();
  }
  EIGEN_DEVICE_FUNC constexpr Index cols() const {
    return Direction == Horizontal ? m_lhs.cols() + m_rhs.cols() : m_lhs.cols();
  }

  EIGEN_DEVICE_FUNC constexpr const LhsTypeNested_& lhs() const { return m_lhs; }
  EIGEN_DEVICE_FUNC constexpr const RhsTypeNested_& rhs() const { return m_rhs; }

 protected:
  LhsTypeNested m_lhs;
  RhsTypeNested m_rhs;
};

// Evaluator for Concat
namespace internal {

template <int Direction, typename LhsType, typename RhsType>
struct evaluator<Concat<Direction, LhsType, RhsType>> : evaluator_base<Concat<Direction, LhsType, RhsType>> {
  typedef Concat<Direction, LhsType, RhsType> XprType;
  typedef typename XprType::CoeffReturnType CoeffReturnType;

  typedef typename nested_eval<LhsType, 1>::type LhsNested;
  typedef typename nested_eval<RhsType, 1>::type RhsNested;
  typedef remove_all_t<LhsNested> LhsNestedCleaned;
  typedef remove_all_t<RhsNested> RhsNestedCleaned;

  enum {
    CoeffReadCost = plain_enum_max(evaluator<LhsNestedCleaned>::CoeffReadCost,
                                   evaluator<RhsNestedCleaned>::CoeffReadCost) +
                    NumTraits<typename XprType::Scalar>::AddCost,  // cost of the branch
    LhsFlags = evaluator<LhsNestedCleaned>::Flags,
    RhsFlags = evaluator<RhsNestedCleaned>::Flags,
    BothHavePacketAccess = (int(LhsFlags) & PacketAccessBit) && (int(RhsFlags) & PacketAccessBit),
    BothHaveLinearAccess = (int(LhsFlags) & LinearAccessBit) && (int(RhsFlags) & LinearAccessBit),
    IsRowMajor = int(traits<XprType>::Flags) & RowMajorBit,
    IsVectorAtCompileTime = XprType::IsVectorAtCompileTime,
    Flags = (traits<XprType>::Flags & RowMajorBit) | (BothHavePacketAccess ? PacketAccessBit : 0) |
            (IsVectorAtCompileTime && BothHaveLinearAccess ? LinearAccessBit : 0),
    Alignment = 0  // conservative: no alignment guarantees across boundary
  };

  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE explicit evaluator(const XprType& xpr)
      : m_lhs(xpr.lhs()),
        m_rhs(xpr.rhs()),
        m_lhsImpl(m_lhs),
        m_rhsImpl(m_rhs),
        m_lhsRows(xpr.lhs().rows()),
        m_lhsCols(xpr.lhs().cols()) {}

  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE CoeffReturnType coeff(Index row, Index col) const {
    if (Direction == Vertical) {
      if (row < m_lhsRows.value())
        return m_lhsImpl.coeff(row, col);
      else
        return m_rhsImpl.coeff(row - m_lhsRows.value(), col);
    } else {
      if (col < m_lhsCols.value())
        return m_lhsImpl.coeff(row, col);
      else
        return m_rhsImpl.coeff(row, col - m_lhsCols.value());
    }
  }

  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE CoeffReturnType coeff(Index index) const {
    const Index boundary = Direction == Vertical ? m_lhsRows.value() : m_lhsCols.value();
    if (index < boundary)
      return m_lhsImpl.coeff(index);
    else
      return m_rhsImpl.coeff(index - boundary);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packet(Index row, Index col) const {
    constexpr int packetSize = unpacket_traits<PacketType>::size;
    if (Direction == Vertical) {
      const Index boundary = m_lhsRows.value();
      if (row >= boundary) return m_rhsImpl.template packet<LoadMode, PacketType>(row - boundary, col);
      // Column-major: inner=rows, packet extends along rows and may straddle the row boundary.
      // Row-major: inner=cols, packet extends along cols — never crosses the row boundary.
      if (!IsRowMajor && row + packetSize > boundary) return packetBoundary<LoadMode, PacketType>(row, col);
      return m_lhsImpl.template packet<LoadMode, PacketType>(row, col);
    } else {
      const Index boundary = m_lhsCols.value();
      if (col >= boundary) return m_rhsImpl.template packet<LoadMode, PacketType>(row, col - boundary);
      // Row-major: inner=cols, packet extends along cols and may straddle the col boundary.
      // Column-major: inner=rows, packet extends along rows — never crosses the col boundary.
      if (IsRowMajor && col + packetSize > boundary) return packetBoundary<LoadMode, PacketType>(row, col);
      return m_lhsImpl.template packet<LoadMode, PacketType>(row, col);
    }
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packet(Index index) const {
    constexpr int packetSize = unpacket_traits<PacketType>::size;
    const Index boundary = Direction == Vertical ? m_lhsRows.value() : m_lhsCols.value();
    if (index >= boundary) return m_rhsImpl.template packet<LoadMode, PacketType>(index - boundary);
    if (index + packetSize > boundary) return packetBoundaryLinear<LoadMode, PacketType>(index);
    return m_lhsImpl.template packet<LoadMode, PacketType>(index);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegment(Index row, Index col, Index begin, Index count) const {
    if (Direction == Vertical) {
      const Index boundary = m_lhsRows.value();
      if (row >= boundary)
        return m_rhsImpl.template packetSegment<LoadMode, PacketType>(row - boundary, col, begin, count);
      if (!IsRowMajor && row + begin + count > boundary)
        return packetSegmentBoundary<LoadMode, PacketType>(row, col, begin, count);
      return m_lhsImpl.template packetSegment<LoadMode, PacketType>(row, col, begin, count);
    } else {
      const Index boundary = m_lhsCols.value();
      if (col >= boundary)
        return m_rhsImpl.template packetSegment<LoadMode, PacketType>(row, col - boundary, begin, count);
      if (IsRowMajor && col + begin + count > boundary)
        return packetSegmentBoundary<LoadMode, PacketType>(row, col, begin, count);
      return m_lhsImpl.template packetSegment<LoadMode, PacketType>(row, col, begin, count);
    }
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegment(Index index, Index begin, Index count) const {
    const Index boundary = Direction == Vertical ? m_lhsRows.value() : m_lhsCols.value();
    if (index >= boundary)
      return m_rhsImpl.template packetSegment<LoadMode, PacketType>(index - boundary, begin, count);
    if (index + begin + count > boundary) return packetSegmentBoundaryLinear<LoadMode, PacketType>(index, begin, count);
    return m_lhsImpl.template packetSegment<LoadMode, PacketType>(index, begin, count);
  }

 protected:
  typedef typename XprType::Scalar Scalar;

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetBoundary(Index row, Index col) const {
    constexpr int packetSize = unpacket_traits<PacketType>::size;
    EIGEN_ALIGN_MAX Scalar tmp[packetSize];
    for (int i = 0; i < packetSize; ++i)
      tmp[i] = coeff(row + (Direction == Vertical ? i : 0), col + (Direction == Horizontal ? i : 0));
    return pload<PacketType>(tmp);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetBoundaryLinear(Index index) const {
    constexpr int packetSize = unpacket_traits<PacketType>::size;
    EIGEN_ALIGN_MAX Scalar tmp[packetSize];
    for (int i = 0; i < packetSize; ++i) tmp[i] = coeff(index + i);
    return pload<PacketType>(tmp);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegmentBoundary(Index row, Index col, Index begin,
                                                                         Index count) const {
    constexpr int packetSize = unpacket_traits<PacketType>::size;
    EIGEN_ALIGN_MAX Scalar tmp[packetSize];
    for (Index i = begin; i < begin + count; ++i)
      tmp[i] = coeff(row + (Direction == Vertical ? i : 0), col + (Direction == Horizontal ? i : 0));
    return ploadSegment<PacketType>(tmp, begin, count);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegmentBoundaryLinear(Index index, Index begin,
                                                                               Index count) const {
    constexpr int packetSize = unpacket_traits<PacketType>::size;
    EIGEN_ALIGN_MAX Scalar tmp[packetSize];
    for (Index i = begin; i < begin + count; ++i) tmp[i] = coeff(index + i);
    return ploadSegment<PacketType>(tmp, begin, count);
  }

  LhsNested m_lhs;
  RhsNested m_rhs;
  evaluator<LhsNestedCleaned> m_lhsImpl;
  evaluator<RhsNestedCleaned> m_rhsImpl;
  const variable_if_dynamic<Index, LhsType::RowsAtCompileTime> m_lhsRows;
  const variable_if_dynamic<Index, LhsType::ColsAtCompileTime> m_lhsCols;
};

}  // namespace internal

/**
 * \relates Concat
 * \returns an expression of \a lhs and \a rhs concatenated horizontally (side by side).
 *
 * Both arguments must have the same number of rows.
 * To concatenate more than two expressions, chain calls: \c hcat(hcat(a, b), c).
 *
 * Example: \code
 * Matrix2d A, B;
 * auto C = hcat(A, B);  // C is 2x4
 * \endcode
 *
 * \sa vcat(), Concat
 */
template <typename Lhs, typename Rhs>
EIGEN_DEVICE_FUNC inline const Concat<Horizontal, Lhs, Rhs> hcat(const DenseBase<Lhs>& lhs, const DenseBase<Rhs>& rhs) {
  return Concat<Horizontal, Lhs, Rhs>(lhs.derived(), rhs.derived());
}

/**
 * \relates Concat
 * \returns an expression of \a lhs and \a rhs concatenated vertically (stacked on top of each other).
 *
 * Both arguments must have the same number of columns.
 * To concatenate more than two expressions, chain calls: \c vcat(vcat(a, b), c).
 *
 * Example: \code
 * Matrix2d A, B;
 * auto C = vcat(A, B);  // C is 4x2
 * \endcode
 *
 * \sa hcat(), Concat
 */
template <typename Lhs, typename Rhs>
EIGEN_DEVICE_FUNC inline const Concat<Vertical, Lhs, Rhs> vcat(const DenseBase<Lhs>& lhs, const DenseBase<Rhs>& rhs) {
  return Concat<Vertical, Lhs, Rhs>(lhs.derived(), rhs.derived());
}

}  // end namespace Eigen

#endif  // EIGEN_CONCAT_OP_H
