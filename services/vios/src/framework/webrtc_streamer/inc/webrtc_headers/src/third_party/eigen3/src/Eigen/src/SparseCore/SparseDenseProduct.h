// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008-2015 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_SPARSEDENSEPRODUCT_H
#define EIGEN_SPARSEDENSEPRODUCT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <>
struct product_promote_storage_type<Sparse, Dense, OuterProduct> {
  typedef Sparse ret;
};
template <>
struct product_promote_storage_type<Dense, Sparse, OuterProduct> {
  typedef Sparse ret;
};

// Type trait to detect if a sparse type supports direct compressed storage access
// (i.e., has valuePtr(), innerIndexPtr(), outerIndexPtr(), isCompressed()).
// All types deriving from SparseCompressedBase provide these methods.
template <typename T>
struct has_compressed_storage : std::is_base_of<SparseCompressedBase<T>, T> {};

template <typename SparseLhsType, typename DenseRhsType, typename DenseResType, typename AlphaType,
          int LhsStorageOrder = ((SparseLhsType::Flags & RowMajorBit) == RowMajorBit) ? RowMajor : ColMajor,
          bool ColPerCol = ((DenseRhsType::Flags & RowMajorBit) == 0) || DenseRhsType::ColsAtCompileTime == 1>
struct sparse_time_dense_product_impl;

// RowMajor, single column (ColPerCol=true): CSR SpMV
template <typename SparseLhsType, typename DenseRhsType, typename DenseResType>
struct sparse_time_dense_product_impl<SparseLhsType, DenseRhsType, DenseResType, typename DenseResType::Scalar,
                                      RowMajor, true> {
  typedef internal::remove_all_t<SparseLhsType> Lhs;
  typedef internal::remove_all_t<DenseRhsType> Rhs;
  typedef internal::remove_all_t<DenseResType> Res;
  typedef typename evaluator<Lhs>::InnerIterator LhsInnerIterator;
  typedef evaluator<Lhs> LhsEval;
  typedef typename Res::Scalar ResScalar;

  static void run(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                  const typename Res::Scalar& alpha) {
    LhsEval lhsEval(lhs);
    Index n = lhs.outerSize();

    for (Index c = 0; c < rhs.cols(); ++c) {
      runCol(lhsEval, lhs, rhs, res, alpha, n, c, std::integral_constant<bool, has_compressed_storage<Lhs>::value>());
    }
  }

  // Direct pointer path: works for both compressed and non-compressed storage.
  static void runCol(const LhsEval& /*lhsEval*/, const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                     const ResScalar& alpha, Index n, Index c, std::true_type /* has_compressed_storage */) {
    runColImpl(lhs, rhs, res, alpha, n, c, std::integral_constant<bool, bool(DenseRhsType::Flags & DirectAccessBit)>());
  }

  template <typename RhsT>
  static void runColImpl(const SparseLhsType& lhs, const RhsT& rhs, DenseResType& res, const ResScalar& alpha, Index n,
                         Index c, std::true_type) {
    const Lhs& mat = lhs;
    const auto* vals = mat.valuePtr();
    const auto* inds = mat.innerIndexPtr();
    // Sparse vectors don't store outer indices.
    const auto* outer = mat.outerIndexPtr();
    const auto* innerNnz = mat.innerNonZeroPtr();
    // The fast rhs pointer path requires unit inner stride (common case: VectorXd, contiguous matrix column).
    if (rhs.innerStride() == 1) {
      const auto* x = rhs.data() + c * rhs.outerStride();
#ifdef EIGEN_HAS_OPENMP
      Index threads = Eigen::nbThreads();
      if (threads > 1 && mat.nonZeros() > 20000) {
#pragma omp parallel for schedule(dynamic, (n + threads * 4 - 1) / (threads * 4)) num_threads(threads)
        for (Index i = 0; i < n; ++i) {
          Index k = outer ? outer[i] : 0;
          const Index end = innerNnz ? (outer ? outer[i] : 0) + innerNnz[i] : (outer ? outer[i + 1] : mat.nonZeros());
          ResScalar sum0(0), sum1(0);
          for (; k < end; ++k) {
            sum0 += vals[k] * x[inds[k]];
            ++k;
            if (k < end) {
              sum1 += vals[k] * x[inds[k]];
            }
          }
          res.coeffRef(i, c) += alpha * (sum0 + sum1);
        }
      } else
#endif
      {
        for (Index i = 0; i < n; ++i) {
          Index k = outer ? outer[i] : 0;
          const Index end = innerNnz ? (outer ? outer[i] : 0) + innerNnz[i] : (outer ? outer[i + 1] : mat.nonZeros());
          // Two independent accumulators to break the dependency chain
          ResScalar sum0(0), sum1(0);
          for (; k < end; ++k) {
            sum0 += vals[k] * x[inds[k]];
            ++k;
            if (k < end) {
              sum1 += vals[k] * x[inds[k]];
            }
          }
          res.coeffRef(i, c) += alpha * (sum0 + sum1);
        }
      }
    } else {
      runColImpl(lhs, rhs, res, alpha, n, c, std::false_type());
    }
  }

  // Use fall-back path without direct access to rhs.
  template <typename RhsT>
  static void runColImpl(const SparseLhsType& lhs, const RhsT& rhs, DenseResType& res, const ResScalar& alpha, Index n,
                         Index c, std::false_type) {
    const Lhs& mat = lhs;
    const auto* vals = mat.valuePtr();
    const auto* inds = mat.innerIndexPtr();
    const auto* outer = mat.outerIndexPtr();
    const auto* innerNnz = mat.innerNonZeroPtr();
    // Non-unit rhs stride (or no direct access): use direct pointers for sparse side, coeff() for rhs
    for (Index i = 0; i < n; ++i) {
      Index k = outer ? outer[i] : 0;
      const Index end = innerNnz ? (outer ? outer[i] : 0) + innerNnz[i] : (outer ? outer[i + 1] : mat.nonZeros());
      ResScalar sum0(0), sum1(0);
      for (; k < end; ++k) {
        sum0 += vals[k] * rhs.coeff(inds[k], c);
        ++k;
        if (k < end) {
          sum1 += vals[k] * rhs.coeff(inds[k], c);
        }
      }
      res.coeffRef(i, c) += alpha * (sum0 + sum1);
    }
  }

  // Iterator fallback path
  static void runCol(const LhsEval& lhsEval, const SparseLhsType& /*lhs*/, const DenseRhsType& rhs, DenseResType& res,
                     const ResScalar& alpha, Index n, Index c, std::false_type /* has_compressed_storage */) {
#ifdef EIGEN_HAS_OPENMP
    Index threads = Eigen::nbThreads();
    if (threads > 1 && lhsEval.nonZerosEstimate() > 20000) {
#pragma omp parallel for schedule(dynamic, (n + threads * 4 - 1) / (threads * 4)) num_threads(threads)
      for (Index i = 0; i < n; ++i) processRow(lhsEval, rhs, res, alpha, i, c);
    } else
#endif
    {
      for (Index i = 0; i < n; ++i) processRow(lhsEval, rhs, res, alpha, i, c);
    }
  }

  static void processRow(const LhsEval& lhsEval, const DenseRhsType& rhs, DenseResType& res, const ResScalar& alpha,
                         Index i, Index col) {
    ResScalar tmp_a(0);
    ResScalar tmp_b(0);
    for (LhsInnerIterator it(lhsEval, i); it; ++it) {
      tmp_a += it.value() * rhs.coeff(it.index(), col);
      ++it;
      if (it) {
        tmp_b += it.value() * rhs.coeff(it.index(), col);
      }
    }
    res.coeffRef(i, col) += alpha * (tmp_a + tmp_b);
  }
};

// ColMajor, single column (ColPerCol=true): CSC SpMV
template <typename SparseLhsType, typename DenseRhsType, typename DenseResType, typename AlphaType>
struct sparse_time_dense_product_impl<SparseLhsType, DenseRhsType, DenseResType, AlphaType, ColMajor, true> {
  typedef internal::remove_all_t<SparseLhsType> Lhs;
  typedef internal::remove_all_t<DenseRhsType> Rhs;
  typedef internal::remove_all_t<DenseResType> Res;
  typedef evaluator<Lhs> LhsEval;
  typedef typename LhsEval::InnerIterator LhsInnerIterator;

  static void run(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res, const AlphaType& alpha) {
    runImpl(lhs, rhs, res, alpha, std::integral_constant<bool, has_compressed_storage<Lhs>::value>());
  }

  // Direct pointer path: works for both compressed and non-compressed storage.
  static void runImpl(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res, const AlphaType& alpha,
                      std::true_type /* has_compressed_storage */) {
    typedef typename Lhs::Scalar LhsScalar;
    typedef typename Lhs::StorageIndex StorageIndex;
    const Lhs& mat = lhs;
    const LhsScalar* vals = mat.valuePtr();
    const StorageIndex* inds = mat.innerIndexPtr();
    // Sparse vectors don't store outer indices.
    const auto* outer = mat.outerIndexPtr();
    const auto* innerNnz = mat.innerNonZeroPtr();
    // The fast result pointer path requires contiguous ColMajor result layout.
    // Transpose<ColMajor> reports innerStride()==1 but is actually RowMajor, so check both.
    if (!(Res::Flags & RowMajorBit) && res.innerStride() == 1) {
      for (Index c = 0; c < rhs.cols(); ++c) {
        typename Res::Scalar* y = res.data() + c * res.outerStride();
        for (Index j = 0; j < lhs.outerSize(); ++j) {
          typename ScalarBinaryOpTraits<AlphaType, typename Rhs::Scalar>::ReturnType rhs_j(alpha * rhs.coeff(j, c));
          const Index start = outer ? outer[j] : 0;
          const Index end = innerNnz ? start + innerNnz[j] : (outer ? outer[j + 1] : mat.nonZeros());
          Index k = start;
          // 4-way unrolled scatter-add (no SIMD: writes are scattered)
          for (; k + 3 < end; k += 4) {
            y[inds[k]] += vals[k] * rhs_j;
            y[inds[k + 1]] += vals[k + 1] * rhs_j;
            y[inds[k + 2]] += vals[k + 2] * rhs_j;
            y[inds[k + 3]] += vals[k + 3] * rhs_j;
          }
          for (; k < end; ++k) y[inds[k]] += vals[k] * rhs_j;
        }
      }
    } else {
      // Non-unit result stride: use coeffRef() for result access
      for (Index c = 0; c < rhs.cols(); ++c) {
        for (Index j = 0; j < lhs.outerSize(); ++j) {
          typename ScalarBinaryOpTraits<AlphaType, typename Rhs::Scalar>::ReturnType rhs_j(alpha * rhs.coeff(j, c));
          const Index start = outer ? outer[j] : 0;
          const Index end = innerNnz ? start + innerNnz[j] : (outer ? outer[j + 1] : mat.nonZeros());
          for (Index k = start; k < end; ++k) res.coeffRef(inds[k], c) += vals[k] * rhs_j;
        }
      }
    }
  }

  // Iterator-based fallback
  static void runImpl(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res, const AlphaType& alpha,
                      std::false_type /* has_compressed_storage */) {
    LhsEval lhsEval(lhs);
    for (Index c = 0; c < rhs.cols(); ++c) {
      for (Index j = 0; j < lhs.outerSize(); ++j) {
        typename ScalarBinaryOpTraits<AlphaType, typename Rhs::Scalar>::ReturnType rhs_j(alpha * rhs.coeff(j, c));
        for (LhsInnerIterator it(lhsEval, j); it; ++it) res.coeffRef(it.index(), c) += it.value() * rhs_j;
      }
    }
  }
};

// RowMajor, multiple columns (ColPerCol=false): sparse * dense_matrix
template <typename SparseLhsType, typename DenseRhsType, typename DenseResType>
struct sparse_time_dense_product_impl<SparseLhsType, DenseRhsType, DenseResType, typename DenseResType::Scalar,
                                      RowMajor, false> {
  typedef internal::remove_all_t<SparseLhsType> Lhs;
  typedef internal::remove_all_t<DenseRhsType> Rhs;
  typedef internal::remove_all_t<DenseResType> Res;
  typedef evaluator<Lhs> LhsEval;
  typedef typename LhsEval::InnerIterator LhsInnerIterator;

  static constexpr bool IsCompressedLhs = has_compressed_storage<Lhs>::value;

  static void run(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                  const typename Res::Scalar& alpha) {
    Index n = lhs.rows();
    LhsEval lhsEval(lhs);

#ifdef EIGEN_HAS_OPENMP
    Index threads = Eigen::nbThreads();
    // This 20000 threshold has been found experimentally on 2D and 3D Poisson problems.
    // It basically represents the minimal amount of work to be done to be worth it.
    if (threads > 1 && lhsEval.nonZerosEstimate() * rhs.cols() > 20000) {
#pragma omp parallel for schedule(dynamic, (n + threads * 4 - 1) / (threads * 4)) num_threads(threads)
      for (Index i = 0; i < n; ++i)
        processRow(lhsEval, lhs, rhs, res, alpha, i, std::integral_constant<bool, IsCompressedLhs>());
    } else
#endif
    {
      for (Index i = 0; i < n; ++i)
        processRow(lhsEval, lhs, rhs, res, alpha, i, std::integral_constant<bool, IsCompressedLhs>());
    }
  }

  // Direct pointer path: works for both compressed and non-compressed storage.
  static void processRow(const LhsEval& /*lhsEval*/, const SparseLhsType& lhs, const DenseRhsType& rhs, Res& res,
                         const typename Res::Scalar& alpha, Index i, std::true_type /* has_compressed_storage */) {
    typedef typename Lhs::Scalar LhsScalar;
    typedef typename Lhs::StorageIndex StorageIndex;
    const Lhs& mat = lhs;
    const LhsScalar* vals = mat.valuePtr();
    const StorageIndex* inds = mat.innerIndexPtr();
    // Sparse vectors don't store outer indices.
    const Index start = mat.outerIndexPtr() ? mat.outerIndexPtr()[i] : 0;
    const auto* innerNnz = mat.innerNonZeroPtr();
    const Index end =
        innerNnz ? start + innerNnz[i] : (mat.outerIndexPtr() ? mat.outerIndexPtr()[i + 1] : mat.nonZeros());
    typename Res::RowXpr res_i(res.row(i));
    for (Index k = start; k < end; ++k) res_i += (alpha * vals[k]) * rhs.row(inds[k]);
  }

  static void processRow(const LhsEval& lhsEval, const SparseLhsType& /*lhs*/, const DenseRhsType& rhs, Res& res,
                         const typename Res::Scalar& alpha, Index i, std::false_type /* has_compressed_storage */) {
    typename Res::RowXpr res_i(res.row(i));
    for (LhsInnerIterator it(lhsEval, i); it; ++it) res_i += (alpha * it.value()) * rhs.row(it.index());
  }
};

// ColMajor, multiple columns (ColPerCol=false): sparse * dense_matrix
template <typename SparseLhsType, typename DenseRhsType, typename DenseResType>
struct sparse_time_dense_product_impl<SparseLhsType, DenseRhsType, DenseResType, typename DenseResType::Scalar,
                                      ColMajor, false> {
  typedef internal::remove_all_t<SparseLhsType> Lhs;
  typedef internal::remove_all_t<DenseRhsType> Rhs;
  typedef internal::remove_all_t<DenseResType> Res;
  typedef typename evaluator<Lhs>::InnerIterator LhsInnerIterator;

  static void run(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                  const typename Res::Scalar& alpha) {
    runImpl(lhs, rhs, res, alpha, std::integral_constant<bool, has_compressed_storage<Lhs>::value>());
  }

  // Direct pointer path: works for both compressed and non-compressed storage.
  static void runImpl(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                      const typename Res::Scalar& alpha, std::true_type /* has_compressed_storage */) {
    typedef typename Lhs::Scalar LhsScalar;
    typedef typename Lhs::StorageIndex StorageIndex;
    const Lhs& mat = lhs;
    const LhsScalar* vals = mat.valuePtr();
    const StorageIndex* inds = mat.innerIndexPtr();
    // Sparse vectors don't store outer indices.
    const auto* outer = mat.outerIndexPtr();
    const auto* innerNnz = mat.innerNonZeroPtr();
    for (Index j = 0; j < lhs.outerSize(); ++j) {
      typename Rhs::ConstRowXpr rhs_j(rhs.row(j));
      const Index start = outer ? outer[j] : 0;
      const Index end = innerNnz ? start + innerNnz[j] : (outer ? outer[j + 1] : mat.nonZeros());
      for (Index k = start; k < end; ++k) res.row(inds[k]) += (alpha * vals[k]) * rhs_j;
    }
  }

  static void runImpl(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                      const typename Res::Scalar& alpha, std::false_type /* has_compressed_storage */) {
    evaluator<Lhs> lhsEval(lhs);
    for (Index j = 0; j < lhs.outerSize(); ++j) {
      typename Rhs::ConstRowXpr rhs_j(rhs.row(j));
      for (LhsInnerIterator it(lhsEval, j); it; ++it) res.row(it.index()) += (alpha * it.value()) * rhs_j;
    }
  }
};

template <typename SparseLhsType, typename DenseRhsType, typename DenseResType, typename AlphaType>
inline void sparse_time_dense_product(const SparseLhsType& lhs, const DenseRhsType& rhs, DenseResType& res,
                                      const AlphaType& alpha) {
  sparse_time_dense_product_impl<SparseLhsType, DenseRhsType, DenseResType, AlphaType>::run(lhs, rhs, res, alpha);
}

}  // end namespace internal

namespace internal {

template <typename Lhs, typename Rhs, int ProductType>
struct generic_product_impl<Lhs, Rhs, SparseShape, DenseShape, ProductType>
    : generic_product_impl_base<Lhs, Rhs, generic_product_impl<Lhs, Rhs, SparseShape, DenseShape, ProductType> > {
  typedef typename Product<Lhs, Rhs>::Scalar Scalar;

  template <typename Dest>
  static void scaleAndAddTo(Dest& dst, const Lhs& lhs, const Rhs& rhs, const Scalar& alpha) {
    typedef typename nested_eval<Lhs, ((Rhs::Flags & RowMajorBit) == 0) ? 1 : Rhs::ColsAtCompileTime>::type LhsNested;
    typedef typename nested_eval<Rhs, ((Lhs::Flags & RowMajorBit) == 0) ? 1 : Dynamic>::type RhsNested;
    LhsNested lhsNested(lhs);
    RhsNested rhsNested(rhs);
    internal::sparse_time_dense_product(lhsNested, rhsNested, dst, alpha);
  }
};

template <typename Lhs, typename Rhs, int ProductType>
struct generic_product_impl<Lhs, Rhs, SparseTriangularShape, DenseShape, ProductType>
    : generic_product_impl<Lhs, Rhs, SparseShape, DenseShape, ProductType> {};

template <typename Lhs, typename Rhs, int ProductType>
struct generic_product_impl<Lhs, Rhs, DenseShape, SparseShape, ProductType>
    : generic_product_impl_base<Lhs, Rhs, generic_product_impl<Lhs, Rhs, DenseShape, SparseShape, ProductType> > {
  typedef typename Product<Lhs, Rhs>::Scalar Scalar;

  template <typename Dst>
  static void scaleAndAddTo(Dst& dst, const Lhs& lhs, const Rhs& rhs, const Scalar& alpha) {
    typedef typename nested_eval<Lhs, ((Rhs::Flags & RowMajorBit) == 0) ? Dynamic : 1>::type LhsNested;
    typedef typename nested_eval<Rhs, ((Lhs::Flags & RowMajorBit) == RowMajorBit) ? 1 : Lhs::RowsAtCompileTime>::type
        RhsNested;
    LhsNested lhsNested(lhs);
    RhsNested rhsNested(rhs);

    // transpose everything
    Transpose<Dst> dstT(dst);
    internal::sparse_time_dense_product(rhsNested.transpose(), lhsNested.transpose(), dstT, alpha);
  }
};

template <typename Lhs, typename Rhs, int ProductType>
struct generic_product_impl<Lhs, Rhs, DenseShape, SparseTriangularShape, ProductType>
    : generic_product_impl<Lhs, Rhs, DenseShape, SparseShape, ProductType> {};

template <typename LhsT, typename RhsT, bool NeedToTranspose>
struct sparse_dense_outer_product_evaluator {
 protected:
  typedef std::conditional_t<NeedToTranspose, RhsT, LhsT> Lhs1;
  typedef std::conditional_t<NeedToTranspose, LhsT, RhsT> ActualRhs;
  typedef Product<LhsT, RhsT, DefaultProduct> ProdXprType;

  // if the actual left-hand side is a dense vector,
  // then build a sparse-view so that we can seamlessly iterate over it.
  typedef std::conditional_t<std::is_same<typename internal::traits<Lhs1>::StorageKind, Sparse>::value, Lhs1,
                             SparseView<Lhs1> >
      ActualLhs;
  typedef std::conditional_t<std::is_same<typename internal::traits<Lhs1>::StorageKind, Sparse>::value, Lhs1 const&,
                             SparseView<Lhs1> >
      LhsArg;

  typedef evaluator<ActualLhs> LhsEval;
  typedef evaluator<ActualRhs> RhsEval;
  typedef typename evaluator<ActualLhs>::InnerIterator LhsIterator;
  typedef typename ProdXprType::Scalar Scalar;

 public:
  enum { Flags = NeedToTranspose ? RowMajorBit : 0, CoeffReadCost = HugeCost };

  class InnerIterator : public LhsIterator {
   public:
    InnerIterator(const sparse_dense_outer_product_evaluator& xprEval, Index outer)
        : LhsIterator(xprEval.m_lhsXprImpl, 0),
          m_outer(outer),
          m_empty(false),
          m_factor(get(xprEval.m_rhsXprImpl, outer, typename internal::traits<ActualRhs>::StorageKind())) {}

    EIGEN_STRONG_INLINE Index outer() const { return m_outer; }
    EIGEN_STRONG_INLINE Index row() const { return NeedToTranspose ? m_outer : LhsIterator::index(); }
    EIGEN_STRONG_INLINE Index col() const { return NeedToTranspose ? LhsIterator::index() : m_outer; }

    EIGEN_STRONG_INLINE Scalar value() const { return LhsIterator::value() * m_factor; }
    EIGEN_STRONG_INLINE operator bool() const { return LhsIterator::operator bool() && (!m_empty); }

   protected:
    Scalar get(const RhsEval& rhs, Index outer, Dense = Dense()) const { return rhs.coeff(outer); }

    Scalar get(const RhsEval& rhs, Index outer, Sparse = Sparse()) {
      typename RhsEval::InnerIterator it(rhs, outer);
      if (it && it.index() == 0 && it.value() != Scalar(0)) return it.value();
      m_empty = true;
      return Scalar(0);
    }

    Index m_outer;
    bool m_empty;
    Scalar m_factor;
  };

  sparse_dense_outer_product_evaluator(const Lhs1& lhs, const ActualRhs& rhs)
      : m_lhs(lhs), m_lhsXprImpl(m_lhs), m_rhsXprImpl(rhs) {
    EIGEN_INTERNAL_CHECK_COST_VALUE(CoeffReadCost);
  }

  // transpose case
  sparse_dense_outer_product_evaluator(const ActualRhs& rhs, const Lhs1& lhs)
      : m_lhs(lhs), m_lhsXprImpl(m_lhs), m_rhsXprImpl(rhs) {
    EIGEN_INTERNAL_CHECK_COST_VALUE(CoeffReadCost);
  }

 protected:
  const LhsArg m_lhs;
  evaluator<ActualLhs> m_lhsXprImpl;
  evaluator<ActualRhs> m_rhsXprImpl;
};

// sparse * dense outer product
template <typename Lhs, typename Rhs>
struct product_evaluator<Product<Lhs, Rhs, DefaultProduct>, OuterProduct, SparseShape, DenseShape>
    : sparse_dense_outer_product_evaluator<Lhs, Rhs, Lhs::IsRowMajor> {
  typedef sparse_dense_outer_product_evaluator<Lhs, Rhs, Lhs::IsRowMajor> Base;

  typedef Product<Lhs, Rhs> XprType;
  typedef typename XprType::PlainObject PlainObject;

  explicit product_evaluator(const XprType& xpr) : Base(xpr.lhs(), xpr.rhs()) {}
};

template <typename Lhs, typename Rhs>
struct product_evaluator<Product<Lhs, Rhs, DefaultProduct>, OuterProduct, DenseShape, SparseShape>
    : sparse_dense_outer_product_evaluator<Lhs, Rhs, Rhs::IsRowMajor> {
  typedef sparse_dense_outer_product_evaluator<Lhs, Rhs, Rhs::IsRowMajor> Base;

  typedef Product<Lhs, Rhs> XprType;
  typedef typename XprType::PlainObject PlainObject;

  explicit product_evaluator(const XprType& xpr) : Base(xpr.lhs(), xpr.rhs()) {}
};

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_SPARSEDENSEPRODUCT_H
