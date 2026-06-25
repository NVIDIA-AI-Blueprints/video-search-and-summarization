// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2009 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TRIANGULARMATRIXVECTOR_H
#define EIGEN_TRIANGULARMATRIXVECTOR_H

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <typename Index, int Mode, typename LhsScalar, bool ConjLhs, typename RhsScalar, bool ConjRhs,
          int StorageOrder, int Version = Specialized>
struct triangular_matrix_vector_product;

template <typename Index, int Mode, typename LhsScalar, bool ConjLhs, typename RhsScalar, bool ConjRhs, int Version>
struct triangular_matrix_vector_product<Index, Mode, LhsScalar, ConjLhs, RhsScalar, ConjRhs, ColMajor, Version> {
  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;
  static constexpr bool IsLower = ((Mode & Lower) == Lower);
  static constexpr bool HasUnitDiag = (Mode & UnitDiag) == UnitDiag;
  static constexpr bool HasZeroDiag = (Mode & ZeroDiag) == ZeroDiag;
  static EIGEN_DONT_INLINE void run(Index _rows, Index _cols, const LhsScalar* lhs_, Index lhsStride,
                                    const RhsScalar* rhs_, Index rhsIncr, ResScalar* res_, Index resIncr,
                                    const RhsScalar& alpha);
};

template <typename Index, int Mode, typename LhsScalar, bool ConjLhs, typename RhsScalar, bool ConjRhs, int Version>
EIGEN_DONT_INLINE void triangular_matrix_vector_product<Index, Mode, LhsScalar, ConjLhs, RhsScalar, ConjRhs, ColMajor,
                                                        Version>::run(Index _rows, Index _cols, const LhsScalar* lhs_,
                                                                      Index lhsStride, const RhsScalar* rhs_,
                                                                      Index rhsIncr, ResScalar* res_, Index resIncr,
                                                                      const RhsScalar& alpha) {
  static const Index PanelWidth = EIGEN_TUNE_TRIANGULAR_PANEL_WIDTH;
  Index size = (std::min)(_rows, _cols);
  Index rows = IsLower ? _rows : (std::min)(_rows, _cols);
  Index cols = IsLower ? (std::min)(_rows, _cols) : _cols;

  typedef const_blas_data_mapper<LhsScalar, Index, ColMajor> LhsMapper;
  typedef const_blas_data_mapper<RhsScalar, Index, RowMajor> RhsMapper;

  conj_if<ConjLhs> cjl;
  conj_if<ConjRhs> cjr;

  for (Index pi = 0; pi < size; pi += PanelWidth) {
    Index actualPanelWidth = (std::min)(PanelWidth, size - pi);

    // Process the triangular panel using raw pointer operations with 2-column batching
    // to eliminate expression template overhead and share result loads/stores.
    if (IsLower) {
      Index k = 0;
      for (; k + 1 < actualPanelWidth; k += 2) {
        Index i0 = pi + k;
        Index i1 = i0 + 1;
        ResScalar s0 = alpha * cjr(rhs_[i0 * rhsIncr]);
        ResScalar s1 = alpha * cjr(rhs_[i1 * rhsIncr]);
        const LhsScalar* EIGEN_RESTRICT c0 = lhs_ + i0 * lhsStride;
        const LhsScalar* EIGEN_RESTRICT c1 = lhs_ + i1 * lhsStride;

        // Diagonal of column 0
        if (!(HasUnitDiag || HasZeroDiag)) res_[i0] += s0 * cjl(c0[i0]);
        // Row i1: contribution from column 0 + diagonal of column 1
        {
          ResScalar r1 = s0 * cjl(c0[i1]);
          if (!(HasUnitDiag || HasZeroDiag)) r1 += s1 * cjl(c1[i1]);
          res_[i1] += r1;
        }
        // Shared rows where both columns contribute
        Index panelEnd = pi + actualPanelWidth;
        for (Index j = i1 + 1; j < panelEnd; ++j) res_[j] += s0 * cjl(c0[j]) + s1 * cjl(c1[j]);

        if (HasUnitDiag) {
          res_[i0] += s0;
          res_[i1] += s1;
        }
      }
      if (k < actualPanelWidth) {
        Index i = pi + k;
        ResScalar s = alpha * cjr(rhs_[i * rhsIncr]);
        const LhsScalar* EIGEN_RESTRICT c = lhs_ + i * lhsStride;
        if (!(HasUnitDiag || HasZeroDiag)) res_[i] += s * cjl(c[i]);
        if (HasUnitDiag) res_[i] += s;
      }
    } else {
      // Upper triangular: process 2 columns at a time
      Index k = 0;
      for (; k + 1 < actualPanelWidth; k += 2) {
        Index i0 = pi + k;
        Index i1 = i0 + 1;
        ResScalar s0 = alpha * cjr(rhs_[i0 * rhsIncr]);
        ResScalar s1 = alpha * cjr(rhs_[i1 * rhsIncr]);
        const LhsScalar* EIGEN_RESTRICT c0 = lhs_ + i0 * lhsStride;
        const LhsScalar* EIGEN_RESTRICT c1 = lhs_ + i1 * lhsStride;

        // Shared rows before the diagonal block
        for (Index j = pi; j < i0; ++j) res_[j] += s0 * cjl(c0[j]) + s1 * cjl(c1[j]);

        // Row i0: diagonal of col0 + contribution from col1
        {
          ResScalar r0 = s1 * cjl(c1[i0]);
          if (!(HasUnitDiag || HasZeroDiag)) r0 += s0 * cjl(c0[i0]);
          res_[i0] += r0;
        }
        // Diagonal of column 1
        if (!(HasUnitDiag || HasZeroDiag)) res_[i1] += s1 * cjl(c1[i1]);

        if (HasUnitDiag) {
          res_[i0] += s0;
          res_[i1] += s1;
        }
      }
      if (k < actualPanelWidth) {
        Index i = pi + k;
        ResScalar s = alpha * cjr(rhs_[i * rhsIncr]);
        const LhsScalar* EIGEN_RESTRICT c = lhs_ + i * lhsStride;
        for (Index j = pi; j < i; ++j) res_[j] += s * cjl(c[j]);
        if (!(HasUnitDiag || HasZeroDiag)) res_[i] += s * cjl(c[i]);
        if (HasUnitDiag) res_[i] += s;
      }
    }

    // Rectangular part: delegate to optimized GEMV
    Index r = IsLower ? rows - pi - actualPanelWidth : pi;
    if (r > 0) {
      Index s = IsLower ? pi + actualPanelWidth : 0;
      general_matrix_vector_product<Index, LhsScalar, LhsMapper, ColMajor, ConjLhs, RhsScalar, RhsMapper, ConjRhs,
                                    BuiltIn>::run(r, actualPanelWidth, LhsMapper(&lhs_[pi * lhsStride + s], lhsStride),
                                                  RhsMapper(&rhs_[pi * rhsIncr], rhsIncr), &res_[s], resIncr, alpha);
    }
  }
  if ((!IsLower) && cols > size) {
    general_matrix_vector_product<Index, LhsScalar, LhsMapper, ColMajor, ConjLhs, RhsScalar, RhsMapper, ConjRhs>::run(
        rows, cols - size, LhsMapper(&lhs_[size * lhsStride], lhsStride), RhsMapper(&rhs_[size * rhsIncr], rhsIncr),
        res_, resIncr, alpha);
  }
}

template <typename Index, int Mode, typename LhsScalar, bool ConjLhs, typename RhsScalar, bool ConjRhs, int Version>
struct triangular_matrix_vector_product<Index, Mode, LhsScalar, ConjLhs, RhsScalar, ConjRhs, RowMajor, Version> {
  typedef typename ScalarBinaryOpTraits<LhsScalar, RhsScalar>::ReturnType ResScalar;
  static constexpr bool IsLower = ((Mode & Lower) == Lower);
  static constexpr bool HasUnitDiag = (Mode & UnitDiag) == UnitDiag;
  static constexpr bool HasZeroDiag = (Mode & ZeroDiag) == ZeroDiag;
  static EIGEN_DONT_INLINE void run(Index _rows, Index _cols, const LhsScalar* lhs_, Index lhsStride,
                                    const RhsScalar* rhs_, Index rhsIncr, ResScalar* res_, Index resIncr,
                                    const ResScalar& alpha);
};

template <typename Index, int Mode, typename LhsScalar, bool ConjLhs, typename RhsScalar, bool ConjRhs, int Version>
EIGEN_DONT_INLINE void triangular_matrix_vector_product<Index, Mode, LhsScalar, ConjLhs, RhsScalar, ConjRhs, RowMajor,
                                                        Version>::run(Index _rows, Index _cols, const LhsScalar* lhs_,
                                                                      Index lhsStride, const RhsScalar* rhs_,
                                                                      Index rhsIncr, ResScalar* res_, Index resIncr,
                                                                      const ResScalar& alpha) {
  static const Index PanelWidth = EIGEN_TUNE_TRIANGULAR_PANEL_WIDTH;
  Index diagSize = (std::min)(_rows, _cols);
  Index rows = IsLower ? _rows : diagSize;
  Index cols = IsLower ? diagSize : _cols;

  typedef const_blas_data_mapper<LhsScalar, Index, RowMajor> LhsMapper;
  typedef const_blas_data_mapper<RhsScalar, Index, RowMajor> RhsMapper;

  conj_if<ConjLhs> cjl;
  conj_if<ConjRhs> cjr;

  for (Index pi = 0; pi < diagSize; pi += PanelWidth) {
    Index actualPanelWidth = (std::min)(PanelWidth, diagSize - pi);

    // Process the triangular panel using raw dot products to eliminate
    // the cwiseProduct().sum() expression template overhead.
    for (Index k = 0; k < actualPanelWidth; ++k) {
      Index i = pi + k;
      const LhsScalar* EIGEN_RESTRICT row_i = lhs_ + i * lhsStride;
      ResScalar dot = ResScalar(0);

      if (IsLower) {
        Index s = pi;
        Index len = (HasUnitDiag || HasZeroDiag) ? k : k + 1;
        for (Index j = 0; j < len; ++j) dot += cjl(row_i[s + j]) * cjr(rhs_[s + j]);
      } else {
        Index s = (HasUnitDiag || HasZeroDiag) ? i + 1 : i;
        Index len = pi + actualPanelWidth - s;
        for (Index j = 0; j < len; ++j) dot += cjl(row_i[s + j]) * cjr(rhs_[s + j]);
      }
      res_[i * resIncr] += alpha * dot;
      if (HasUnitDiag) res_[i * resIncr] += alpha * cjr(rhs_[i]);
    }

    // Rectangular part: delegate to optimized GEMV
    Index r = IsLower ? pi : cols - pi - actualPanelWidth;
    if (r > 0) {
      Index s = IsLower ? 0 : pi + actualPanelWidth;
      general_matrix_vector_product<Index, LhsScalar, LhsMapper, RowMajor, ConjLhs, RhsScalar, RhsMapper, ConjRhs,
                                    BuiltIn>::run(actualPanelWidth, r, LhsMapper(&lhs_[pi * lhsStride + s], lhsStride),
                                                  RhsMapper(&rhs_[s], rhsIncr), &res_[pi * resIncr], resIncr, alpha);
    }
  }
  if (IsLower && rows > diagSize) {
    general_matrix_vector_product<Index, LhsScalar, LhsMapper, RowMajor, ConjLhs, RhsScalar, RhsMapper, ConjRhs>::run(
        rows - diagSize, cols, LhsMapper(&lhs_[diagSize * lhsStride], lhsStride), RhsMapper(rhs_, rhsIncr),
        &res_[diagSize * resIncr], resIncr, alpha);
  }
}

/***************************************************************************
 * Wrapper to product_triangular_vector
 ***************************************************************************/

template <int Mode, int StorageOrder>
struct trmv_selector;

}  // end namespace internal

namespace internal {

template <int Mode, typename Lhs, typename Rhs>
struct triangular_product_impl<Mode, true, Lhs, false, Rhs, true> {
  template <typename Dest>
  static void run(Dest& dst, const Lhs& lhs, const Rhs& rhs, const typename Dest::Scalar& alpha) {
    eigen_assert(dst.rows() == lhs.rows() && dst.cols() == rhs.cols());

    internal::trmv_selector<Mode, (int(internal::traits<Lhs>::Flags) & RowMajorBit) ? RowMajor : ColMajor>::run(
        lhs, rhs, dst, alpha);
  }
};

template <int Mode, typename Lhs, typename Rhs>
struct triangular_product_impl<Mode, false, Lhs, true, Rhs, false> {
  template <typename Dest>
  static void run(Dest& dst, const Lhs& lhs, const Rhs& rhs, const typename Dest::Scalar& alpha) {
    eigen_assert(dst.rows() == lhs.rows() && dst.cols() == rhs.cols());

    Transpose<Dest> dstT(dst);
    internal::trmv_selector<(Mode & (UnitDiag | ZeroDiag)) | ((Mode & Lower) ? Upper : Lower),
                            (int(internal::traits<Rhs>::Flags) & RowMajorBit) ? ColMajor
                                                                              : RowMajor>::run(rhs.transpose(),
                                                                                               lhs.transpose(), dstT,
                                                                                               alpha);
  }
};

}  // end namespace internal

namespace internal {

// TODO: find a way to factorize this piece of code with gemv_selector since the logic is exactly the same.

template <int Mode>
struct trmv_selector<Mode, ColMajor> {
  template <typename Lhs, typename Rhs, typename Dest>
  static void run(const Lhs& lhs, const Rhs& rhs, Dest& dest, const typename Dest::Scalar& alpha) {
    typedef typename Lhs::Scalar LhsScalar;
    typedef typename Rhs::Scalar RhsScalar;
    typedef typename Dest::Scalar ResScalar;

    typedef internal::blas_traits<Lhs> LhsBlasTraits;
    typedef typename LhsBlasTraits::DirectLinearAccessType ActualLhsType;
    typedef internal::blas_traits<Rhs> RhsBlasTraits;
    typedef typename RhsBlasTraits::DirectLinearAccessType ActualRhsType;
    constexpr int Alignment = (std::min)(int(AlignedMax), int(internal::packet_traits<ResScalar>::size));

    typedef Map<Matrix<ResScalar, Dynamic, 1>, Alignment> MappedDest;

    add_const_on_value_type_t<ActualLhsType> actualLhs = LhsBlasTraits::extract(lhs);
    add_const_on_value_type_t<ActualRhsType> actualRhs = RhsBlasTraits::extract(rhs);

    LhsScalar lhs_alpha = LhsBlasTraits::extractScalarFactor(lhs);
    RhsScalar rhs_alpha = RhsBlasTraits::extractScalarFactor(rhs);
    ResScalar actualAlpha = alpha * lhs_alpha * rhs_alpha;

    // FIXME find a way to allow an inner stride on the result if packet_traits<Scalar>::size==1
    // On the other hand, it is good for the cache to pack the vector anyways...
    constexpr bool EvalToDestAtCompileTime = Dest::InnerStrideAtCompileTime == 1;
    constexpr bool ComplexByReal = (NumTraits<LhsScalar>::IsComplex) && (!NumTraits<RhsScalar>::IsComplex);
    constexpr bool MightCannotUseDest = (Dest::InnerStrideAtCompileTime != 1) || ComplexByReal;

    gemv_static_vector_if<ResScalar, Dest::SizeAtCompileTime, Dest::MaxSizeAtCompileTime, MightCannotUseDest>
        static_dest;

    bool alphaIsCompatible = (!ComplexByReal) || numext::is_exactly_zero(numext::imag(actualAlpha));
    bool evalToDest = EvalToDestAtCompileTime && alphaIsCompatible;

    RhsScalar compatibleAlpha = get_factor<ResScalar, RhsScalar>::run(actualAlpha);

    ei_declare_aligned_stack_constructed_variable(ResScalar, actualDestPtr, dest.size(),
                                                  evalToDest ? dest.data() : static_dest.data());

    if (!evalToDest) {
#ifdef EIGEN_DENSE_STORAGE_CTOR_PLUGIN
      constexpr int Size = Dest::SizeAtCompileTime;
      Index size = dest.size();
      EIGEN_DENSE_STORAGE_CTOR_PLUGIN
#endif
      if (!alphaIsCompatible) {
        MappedDest(actualDestPtr, dest.size()).setZero();
        compatibleAlpha = RhsScalar(1);
      } else
        MappedDest(actualDestPtr, dest.size()) = dest;
    }

    internal::triangular_matrix_vector_product<Index, Mode, LhsScalar, LhsBlasTraits::NeedToConjugate, RhsScalar,
                                               RhsBlasTraits::NeedToConjugate, ColMajor>::run(actualLhs.rows(),
                                                                                              actualLhs.cols(),
                                                                                              actualLhs.data(),
                                                                                              actualLhs.outerStride(),
                                                                                              actualRhs.data(),
                                                                                              actualRhs.innerStride(),
                                                                                              actualDestPtr, 1,
                                                                                              compatibleAlpha);

    if (!evalToDest) {
      if (!alphaIsCompatible)
        dest += actualAlpha * MappedDest(actualDestPtr, dest.size());
      else
        dest = MappedDest(actualDestPtr, dest.size());
    }

    if (((Mode & UnitDiag) == UnitDiag) && !numext::is_exactly_one(lhs_alpha)) {
      Index diagSize = (std::min)(lhs.rows(), lhs.cols());
      dest.head(diagSize) -= (lhs_alpha - LhsScalar(1)) * rhs.head(diagSize);
    }
  }
};

template <int Mode>
struct trmv_selector<Mode, RowMajor> {
  template <typename Lhs, typename Rhs, typename Dest>
  static void run(const Lhs& lhs, const Rhs& rhs, Dest& dest, const typename Dest::Scalar& alpha) {
    typedef typename Lhs::Scalar LhsScalar;
    typedef typename Rhs::Scalar RhsScalar;
    typedef typename Dest::Scalar ResScalar;

    typedef internal::blas_traits<Lhs> LhsBlasTraits;
    typedef typename LhsBlasTraits::DirectLinearAccessType ActualLhsType;
    typedef internal::blas_traits<Rhs> RhsBlasTraits;
    typedef typename RhsBlasTraits::DirectLinearAccessType ActualRhsType;
    typedef internal::remove_all_t<ActualRhsType> ActualRhsTypeCleaned;

    std::add_const_t<ActualLhsType> actualLhs = LhsBlasTraits::extract(lhs);
    std::add_const_t<ActualRhsType> actualRhs = RhsBlasTraits::extract(rhs);

    LhsScalar lhs_alpha = LhsBlasTraits::extractScalarFactor(lhs);
    RhsScalar rhs_alpha = RhsBlasTraits::extractScalarFactor(rhs);
    ResScalar actualAlpha = alpha * lhs_alpha * rhs_alpha;

    constexpr bool DirectlyUseRhs = ActualRhsTypeCleaned::InnerStrideAtCompileTime == 1;

    const RhsScalar* actualRhsPtr = actualRhs.data();

    // Potentially create a temporary buffer to copy RHS to contiguous memory.
    gemv_static_vector_if<RhsScalar, ActualRhsTypeCleaned::SizeAtCompileTime,
                          ActualRhsTypeCleaned::MaxSizeAtCompileTime, !DirectlyUseRhs>
        static_rhs;  // Fixed-sized array.
    RhsScalar* buffer = nullptr;
    if (!DirectlyUseRhs) {
      // Maybe used fixed-sized buffer, otherwise allocate.
      if (static_rhs.data() != nullptr) {
        buffer = static_rhs.data();
      } else {
        // Allocate either with alloca or malloc.
        Eigen::internal::check_size_for_overflow<RhsScalar>(actualRhs.size());
#ifdef EIGEN_ALLOCA
        buffer = static_cast<RhsScalar*>((sizeof(RhsScalar) * actualRhs.size() <= EIGEN_STACK_ALLOCATION_LIMIT)
                                             ? EIGEN_ALIGNED_ALLOCA(sizeof(RhsScalar) * actualRhs.size())
                                             : Eigen::internal::aligned_malloc(sizeof(RhsScalar) * actualRhs.size()));
#else
        buffer = static_cast<RhsScalar*>(Eigen::internal::aligned_malloc(sizeof(RhsScalar) * actualRhs.size()));
#endif
      }
#ifdef EIGEN_DENSE_STORAGE_CTOR_PLUGIN
      constexpr int Size = ActualRhsTypeCleaned::SizeAtCompileTime;
      Index size = actualRhs.size();
      EIGEN_DENSE_STORAGE_CTOR_PLUGIN
#endif
      Map<typename ActualRhsTypeCleaned::PlainObject, Eigen::AlignedMax>(buffer, actualRhs.size()) = actualRhs;
      actualRhsPtr = buffer;
    }
    // Deallocate only if malloced.
    Eigen::internal::aligned_stack_memory_handler<RhsScalar> buffer_stack_memory_destructor(
        buffer, actualRhs.size(),
        !DirectlyUseRhs && static_rhs.data() == nullptr && actualRhs.size() > EIGEN_STACK_ALLOCATION_LIMIT);

    internal::triangular_matrix_vector_product<Index, Mode, LhsScalar, LhsBlasTraits::NeedToConjugate, RhsScalar,
                                               RhsBlasTraits::NeedToConjugate, RowMajor>::run(actualLhs.rows(),
                                                                                              actualLhs.cols(),
                                                                                              actualLhs.data(),
                                                                                              actualLhs.outerStride(),
                                                                                              actualRhsPtr, 1,
                                                                                              dest.data(),
                                                                                              dest.innerStride(),
                                                                                              actualAlpha);

    if (((Mode & UnitDiag) == UnitDiag) && !numext::is_exactly_one(lhs_alpha)) {
      Index diagSize = (std::min)(lhs.rows(), lhs.cols());
      dest.head(diagSize) -= (lhs_alpha - LhsScalar(1)) * rhs.head(diagSize);
    }
  }
};

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_TRIANGULARMATRIXVECTOR_H
