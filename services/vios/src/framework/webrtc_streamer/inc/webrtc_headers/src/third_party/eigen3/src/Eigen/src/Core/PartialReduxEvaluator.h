// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2011-2018 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_PARTIALREDUX_H
#define EIGEN_PARTIALREDUX_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/***************************************************************************
 *
 * This file provides evaluators for partial reductions.
 * There are two modes:
 *
 *  - scalar path: simply calls the respective function on the column or row.
 *    -> nothing special here, all the tricky part is handled by the return
 *       types of VectorwiseOp's members. They embed the functor calling the
 *       respective DenseBase's member function.
 *
 *  - vectorized path: implements a packet-wise reductions followed by
 *    some (optional) processing of the outcome, e.g., division by n for mean.
 *
 * For the vectorized path let's observe that the packet-size and outer-unrolling
 * are both decided by the assignment logic. So all we have to do is to decide
 * on the inner unrolling.
 *
 * For the unrolling, we can reuse "internal::redux_vec_unroller" from Redux.h,
 * but be need to be careful to specify correct increment.
 *
 ***************************************************************************/

/* logic deciding a strategy for unrolling of vectorized paths */
template <typename Func, typename Evaluator>
struct packetwise_redux_traits {
  static constexpr int OuterSize =
      int(Evaluator::IsRowMajor) ? Evaluator::RowsAtCompileTime : Evaluator::ColsAtCompileTime;
  static constexpr int Cost = OuterSize == Dynamic
                                  ? HugeCost
                                  : OuterSize * Evaluator::CoeffReadCost + (OuterSize - 1) * functor_traits<Func>::Cost;
  static constexpr int Unrolling = Cost <= EIGEN_UNROLLING_LIMIT ? CompleteUnrolling : NoUnrolling;
};

/* Value to be returned when size==0 , by default let's return 0 */
template <typename PacketType, typename Func>
EIGEN_DEVICE_FUNC PacketType packetwise_redux_empty_value(const Func&) {
  const typename unpacket_traits<PacketType>::type zero(0);
  return pset1<PacketType>(zero);
}

/* For products the default is 1 */
template <typename PacketType, typename Scalar>
EIGEN_DEVICE_FUNC PacketType packetwise_redux_empty_value(const scalar_product_op<Scalar, Scalar>&) {
  return pset1<PacketType>(Scalar(1));
}

/* Perform the actual reduction */
template <typename Func, typename Evaluator, int Unrolling = packetwise_redux_traits<Func, Evaluator>::Unrolling>
struct packetwise_redux_impl;

/* Perform the actual reduction with unrolling */
template <typename Func, typename Evaluator>
struct packetwise_redux_impl<Func, Evaluator, CompleteUnrolling> {
  using Base = redux_novec_unroller<Func, Evaluator, 0, Evaluator::SizeAtCompileTime>;
  using Scalar = typename Evaluator::Scalar;

  template <typename PacketType>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE PacketType run(const Evaluator& eval, const Func& func, Index /*size*/) {
    return redux_vec_unroller<Func, Evaluator, 0,
                              packetwise_redux_traits<Func, Evaluator>::OuterSize>::template run<PacketType>(eval,
                                                                                                             func);
  }
};

/* Add a specialization of redux_vec_unroller for size==0 at compiletime.
 * This specialization is not required for general reductions, which is
 * why it is defined here.
 */
template <typename Func, typename Evaluator, Index Start>
struct redux_vec_unroller<Func, Evaluator, Start, 0> {
  template <typename PacketType>
  EIGEN_DEVICE_FUNC static EIGEN_STRONG_INLINE PacketType run(const Evaluator&, const Func& f) {
    return packetwise_redux_empty_value<PacketType>(f);
  }
};

/* Perform the actual reduction for dynamic sizes */
template <typename Func, typename Evaluator>
struct packetwise_redux_impl<Func, Evaluator, NoUnrolling> {
  using Scalar = typename Evaluator::Scalar;
  using PacketScalar = typename redux_traits<Func, Evaluator>::PacketType;

  template <typename PacketType>
  EIGEN_DEVICE_FUNC static PacketType run(const Evaluator& eval, const Func& func, Index size) {
    if (size == 0) return packetwise_redux_empty_value<PacketType>(func);

    const Index size4 = 1 + numext::round_down(size - 1, 4);
    PacketType p = eval.template packetByOuterInner<Unaligned, PacketType>(0, 0);
    // This loop is optimized for instruction pipelining:
    // - each iteration generates two independent instructions
    // - thanks to branch prediction and out-of-order execution we have independent instructions across loops
    for (Index i = 1; i < size4; i += 4)
      p = func.packetOp(
          p, func.packetOp(func.packetOp(eval.template packetByOuterInner<Unaligned, PacketType>(i + 0, 0),
                                         eval.template packetByOuterInner<Unaligned, PacketType>(i + 1, 0)),
                           func.packetOp(eval.template packetByOuterInner<Unaligned, PacketType>(i + 2, 0),
                                         eval.template packetByOuterInner<Unaligned, PacketType>(i + 3, 0))));
    for (Index i = size4; i < size; ++i)
      p = func.packetOp(p, eval.template packetByOuterInner<Unaligned, PacketType>(i, 0));
    return p;
  }
};

template <typename Func, typename Evaluator>
struct packetwise_segment_redux_impl {
  using Scalar = typename Evaluator::Scalar;
  using PacketScalar = typename redux_traits<Func, Evaluator>::PacketType;

  template <typename PacketType>
  EIGEN_DEVICE_FUNC static PacketType run(const Evaluator& eval, const Func& func, Index size, Index begin,
                                          Index count) {
    if (size == 0) return packetwise_redux_empty_value<PacketType>(func);

    PacketType p = eval.template packetSegmentByOuterInner<Unaligned, PacketType>(0, 0, begin, count);
    for (Index i = 1; i < size; ++i)
      p = func.packetOp(p, eval.template packetSegmentByOuterInner<Unaligned, PacketType>(i, 0, begin, count));
    return p;
  }
};

template <typename ArgType, typename MemberOp, int Direction>
struct evaluator<PartialReduxExpr<ArgType, MemberOp, Direction> >
    : evaluator_base<PartialReduxExpr<ArgType, MemberOp, Direction> > {
  using XprType = PartialReduxExpr<ArgType, MemberOp, Direction>;
  using ArgTypeNested = typename internal::nested_eval<ArgType, 1>::type;
  using ConstArgTypeNested = add_const_on_value_type_t<ArgTypeNested>;
  using ArgTypeNestedCleaned = internal::remove_all_t<ArgTypeNested>;
  using InputScalar = typename ArgType::Scalar;
  using Scalar = typename XprType::Scalar;
  enum {
    TraversalSize = Direction == int(Vertical) ? int(ArgType::RowsAtCompileTime) : int(ArgType::ColsAtCompileTime)
  };
  using CostOpType = typename MemberOp::template Cost<int(TraversalSize)>;
  enum {
    CoeffReadCost = TraversalSize == Dynamic ? HugeCost
                    : TraversalSize == 0
                        ? 1
                        : int(TraversalSize) * int(evaluator<ArgType>::CoeffReadCost) + int(CostOpType::value),

    ArgFlags_ = evaluator<ArgType>::Flags,

    Vectorizable_ = bool(int(ArgFlags_) & PacketAccessBit) && bool(MemberOp::Vectorizable) &&
                    (Direction == int(Vertical) ? bool(ArgFlags_ & RowMajorBit) : (ArgFlags_ & RowMajorBit) == 0) &&
                    (TraversalSize != 0),

    Flags = (traits<XprType>::Flags & RowMajorBit) | (evaluator<ArgType>::Flags & (HereditaryBits & (~RowMajorBit))) |
            (Vectorizable_ ? PacketAccessBit : 0) | LinearAccessBit,

    Alignment = 0  // FIXME this will need to be improved once PartialReduxExpr is vectorized
  };

  EIGEN_DEVICE_FUNC explicit evaluator(const XprType& xpr) : m_arg(xpr.nestedExpression()), m_functor(xpr.functor()) {
    EIGEN_INTERNAL_CHECK_COST_VALUE(TraversalSize == Dynamic ? HugeCost
                                                             : (TraversalSize == 0 ? 1 : int(CostOpType::value)));
    EIGEN_INTERNAL_CHECK_COST_VALUE(CoeffReadCost);
  }

  using CoeffReturnType = typename XprType::CoeffReturnType;

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const Scalar coeff(Index i, Index j) const {
    return coeff(Direction == Vertical ? j : i);
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const Scalar coeff(Index index) const {
    return m_functor(m_arg.template subVector<DirectionType(Direction)>(index));
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packet(Index i, Index j) const {
    return packet<LoadMode, PacketType>(Direction == Vertical ? j : i);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC PacketType packet(Index idx) const {
    static constexpr int PacketSize = internal::unpacket_traits<PacketType>::size;
    static constexpr int PanelRows = Direction == Vertical ? ArgType::RowsAtCompileTime : PacketSize;
    static constexpr int PanelCols = Direction == Vertical ? PacketSize : ArgType::ColsAtCompileTime;
    using PanelType = Block<const ArgTypeNestedCleaned, PanelRows, PanelCols, true /* InnerPanel */>;
    using PanelEvaluator = typename internal::redux_evaluator<PanelType>;
    using BinaryOp = typename MemberOp::BinaryOp;
    using Impl = internal::packetwise_redux_impl<BinaryOp, PanelEvaluator>;

    // Workaround for issue 1612 (closed): when PacketSize==1 (i.e. complex<double> with 128bits registers) the
    // storage-order of panel gets reversed and methods like packetByOuterInner do not make sense in this context, so
    // bypass "vectorization":
    EIGEN_IF_CONSTEXPR(PacketSize == 1) return internal::pset1<PacketType>(coeff(idx));

    Index startRow = Direction == Vertical ? 0 : idx;
    Index startCol = Direction == Vertical ? idx : 0;
    Index numRows = Direction == Vertical ? m_arg.rows() : PacketSize;
    Index numCols = Direction == Vertical ? PacketSize : m_arg.cols();

    PanelType panel(m_arg, startRow, startCol, numRows, numCols);
    PanelEvaluator panel_eval(panel);
    PacketType p = Impl::template run<PacketType>(panel_eval, m_functor.binaryFunc(), m_arg.outerSize());
    return p;
  }

  template <int LoadMode, typename PacketType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketType packetSegment(Index i, Index j, Index begin, Index count) const {
    return packetSegment<LoadMode, PacketType>(Direction == Vertical ? j : i, begin, count);
  }

  template <int LoadMode, typename PacketType>
  EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC PacketType packetSegment(Index idx, Index begin, Index count) const {
    static constexpr int PanelRows = Direction == Vertical ? ArgType::RowsAtCompileTime : Dynamic;
    static constexpr int PanelCols = Direction == Vertical ? Dynamic : ArgType::ColsAtCompileTime;
    using PanelType = Block<const ArgTypeNestedCleaned, PanelRows, PanelCols, true /* InnerPanel */>;
    using PanelEvaluator = typename internal::redux_evaluator<PanelType>;
    using BinaryOp = typename MemberOp::BinaryOp;
    using Impl = internal::packetwise_segment_redux_impl<BinaryOp, PanelEvaluator>;

    Index startRow = Direction == Vertical ? 0 : idx;
    Index startCol = Direction == Vertical ? idx : 0;
    Index numRows = Direction == Vertical ? m_arg.rows() : begin + count;
    Index numCols = Direction == Vertical ? begin + count : m_arg.cols();

    PanelType panel(m_arg, startRow, startCol, numRows, numCols);
    PanelEvaluator panel_eval(panel);
    PacketType p = Impl::template run<PacketType>(panel_eval, m_functor.binaryFunc(), m_arg.outerSize(), begin, count);
    return p;
  }

 protected:
  ConstArgTypeNested m_arg;
  const MemberOp m_functor;
};

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_PARTIALREDUX_H
