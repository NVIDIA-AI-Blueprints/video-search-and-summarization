// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-FileCopyrightText: The Eigen Authors
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_SPARSITY_PATTERN_REF_H
#define EIGEN_SPARSITY_PATTERN_REF_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {
namespace internal {

/** \internal
 * \ingroup SparseCore_Module
 *
 * Non-owning view of a column-major sparsity pattern. The pattern is encoded
 * as CSC index arrays:
 *  - \c outer (size \c outerSize+1 when compressed) gives the start of each
 *    column's row-index range in \c inner.
 *  - \c inner holds the row indices.
 *  - \c innerNonZero is non-null only for an uncompressed source: in that
 *    case column \c j's row indices live in
 *    [outer[j], outer[j] + innerNonZero[j]) instead of [outer[j], outer[j+1]).
 *
 * Use the helper \ref nonZeros to iterate uniformly over both layouts:
 * \code
 *   const Index nz = pattern.nonZeros(j);
 *   for (Index k = pattern.outer[j], end = k + nz; k < end; ++k) {
 *     Index row = pattern.inner[k];
 *   }
 * \endcode
 *
 * Construct via \ref make_col_major_pattern_ref, which shares the source
 * storage when possible (already column-major SparseMatrix) and otherwise
 * materializes the pattern into caller-supplied scratch buffers without ever
 * reading the source's scalar values.
 */
template <typename StorageIndex>
struct SparsityPatternRef {
  const StorageIndex* outer = nullptr;
  const StorageIndex* inner = nullptr;
  // null ⇒ source is compressed: column j is [outer[j], outer[j+1]).
  // otherwise: column j is [outer[j], outer[j] + innerNonZero[j]).
  const StorageIndex* innerNonZero = nullptr;
  Index outerSize = 0;  // number of columns
  Index innerSize = 0;  // number of rows

  EIGEN_DEVICE_FUNC inline bool isCompressed() const { return innerNonZero == nullptr; }

  EIGEN_DEVICE_FUNC inline Index nonZeros(Index j) const {
    return isCompressed() ? Index(outer[j + 1] - outer[j]) : Index(innerNonZero[j]);
  }
};

/** \internal
 * Build a column-major sparsity-pattern view of \c amat without copying any
 * scalar values. The returned view aliases \c amat's index storage when
 * \c amat is already a column-major \c SparseMatrix; otherwise the pattern is
 * materialized into the caller-supplied \c outer_buf / \c inner_buf and the
 * returned view points into them.
 *
 * The buffers must outlive every use of the returned view.
 *
 * Specialization for column-major \c SparseMatrix (the no-copy fast path).
 */
template <typename Scalar, typename StorageIndex>
SparsityPatternRef<StorageIndex> make_col_major_pattern_ref(const SparseMatrix<Scalar, ColMajor, StorageIndex>& amat,
                                                            Matrix<StorageIndex, Dynamic, 1>& /*outer_buf*/,
                                                            Matrix<StorageIndex, Dynamic, 1>& /*inner_buf*/) {
  SparsityPatternRef<StorageIndex> p;
  p.outer = amat.outerIndexPtr();
  p.inner = amat.innerIndexPtr();
  p.innerNonZero = amat.isCompressed() ? nullptr : amat.innerNonZeroPtr();
  p.outerSize = amat.cols();
  p.innerSize = amat.rows();
  return p;
}

/** \internal
 * Generic fallback for any other sparse expression (row-major, products,
 * permutations, etc.). The pattern is materialized into \c outer_buf and
 * \c inner_buf via a value-free counting transpose driven by the expression
 * evaluator. Some evaluators may materialize internally.
 */
template <typename Derived>
SparsityPatternRef<typename Derived::StorageIndex> make_col_major_pattern_ref(
    const SparseMatrixBase<Derived>& amat_base, Matrix<typename Derived::StorageIndex, Dynamic, 1>& outer_buf,
    Matrix<typename Derived::StorageIndex, Dynamic, 1>& inner_buf) {
  typedef typename Derived::StorageIndex StorageIndex;
  const Derived& amat = amat_base.derived();
  internal::evaluator<Derived> amat_eval(amat);
  const Index n_cols = amat.cols();
  const Index n_outer = amat.outerSize();

  outer_buf.setZero(n_cols + 1);
  for (Index i = 0; i < n_outer; ++i)
    for (typename internal::evaluator<Derived>::InnerIterator it(amat_eval, i); it; ++it) ++outer_buf(it.col() + 1);
  for (Index j = 0; j < n_cols; ++j) outer_buf(j + 1) += outer_buf(j);
  inner_buf.resize(outer_buf(n_cols));
  Matrix<StorageIndex, Dynamic, 1> head = outer_buf.head(n_cols);
  for (Index i = 0; i < n_outer; ++i)
    for (typename internal::evaluator<Derived>::InnerIterator it(amat_eval, i); it; ++it)
      inner_buf(head(it.col())++) = convert_index<StorageIndex>(it.row());

  SparsityPatternRef<StorageIndex> p;
  p.outer = outer_buf.data();
  p.inner = inner_buf.data();
  p.innerNonZero = nullptr;
  p.outerSize = n_cols;
  p.innerSize = amat.rows();
  return p;
}

/** \internal
 * Materialize a column-major sparsity pattern view as a compressed
 * \c SparseMatrix with a 1-byte placeholder \c Scalar. Values are filled with
 * a fixed nonzero sentinel — downstream pattern consumers (\c transpose(),
 * sparse \c operator+, \c selfadjointView expansion) read coefficients even
 * when the algorithm only cares about the pattern, so leaving them
 * uninitialized would be UB.
 *
 * Intended for fill-reducing ordering algorithms (AMD, COLAMD) that read only
 * the pattern. \b Precondition: \c out's storage must not alias \c pat —
 * \c resize / \c resizeNonZeros may reallocate and invalidate the view.
 *
 * If \c row_perm is non-null, each inner row index is remapped through it
 * (entry \c (i, j) of \c pat becomes \c (row_perm[i], j) in the result),
 * implementing a left multiplication by a row permutation without copying any
 * scalar values from the source.
 *
 * The result preserves SparseMatrix's invariant that inner indices are sorted
 * within each column.
 */
template <typename StorageIndex>
void materialize_col_major_pattern(const SparsityPatternRef<StorageIndex>& pat, const StorageIndex* row_perm,
                                   SparseMatrix<signed char, ColMajor, StorageIndex>& out) {
  const Index n_outer = pat.outerSize;
  const Index n_inner = pat.innerSize;
  out.resize(n_inner, n_outer);

  Index total = 0;
  if (pat.isCompressed()) {
    total = pat.outer[n_outer];
  } else {
    for (Index j = 0; j < n_outer; ++j) total += pat.nonZeros(j);
  }
  out.resizeNonZeros(total);

  StorageIndex* o_outer = out.outerIndexPtr();
  StorageIndex* o_inner = out.innerIndexPtr();
  if (pat.isCompressed()) {
    // The source's outer array is already in CSC form, and SparseMatrix's
    // outerIndexPtr() always starts at zero (as does the buffer-built fallback
    // in make_col_major_pattern_ref), so the result's outer indices are a
    // verbatim copy. For the no-permutation case the inner indices are also a
    // straight bulk copy. Both reduce to single std::copy / memcpy calls.
    std::copy(pat.outer, pat.outer + n_outer + 1, o_outer);
    if (row_perm == nullptr) {
      std::copy(pat.inner, pat.inner + total, o_inner);
    } else {
      // Remap-and-sort per column, reading from the source's inner array
      // directly to avoid the write-amplification of bulk-copying then
      // overwriting in place.
      for (Index j = 0; j < n_outer; ++j) {
        const StorageIndex* src = pat.inner + o_outer[j];
        StorageIndex* dst = o_inner + o_outer[j];
        const Index nz = o_outer[j + 1] - o_outer[j];
        for (Index k = 0; k < nz; ++k) dst[k] = row_perm[src[k]];
        std::sort(dst, dst + nz);
      }
    }
  } else {
    o_outer[0] = 0;
    for (Index j = 0; j < n_outer; ++j) {
      const Index nz = pat.nonZeros(j);
      o_outer[j + 1] = convert_index<StorageIndex>(o_outer[j] + nz);
      const StorageIndex* src = pat.inner + pat.outer[j];
      StorageIndex* dst = o_inner + o_outer[j];
      if (row_perm == nullptr) {
        std::copy(src, src + nz, dst);
      } else {
        for (Index k = 0; k < nz; ++k) dst[k] = row_perm[src[k]];
        std::sort(dst, dst + nz);
      }
    }
  }
  // Fill values with a fixed nonzero sentinel so downstream consumers that
  // read coefficients (transpose, sparse +, selfadjointView expansion) do not
  // observe uninitialized memory.
  std::fill_n(out.valuePtr(), total, static_cast<signed char>(1));
}

/** \internal Convenience overload — identity row permutation. */
template <typename StorageIndex>
void materialize_col_major_pattern(const SparsityPatternRef<StorageIndex>& pat,
                                   SparseMatrix<signed char, ColMajor, StorageIndex>& out) {
  materialize_col_major_pattern(pat, static_cast<const StorageIndex*>(nullptr), out);
}

// The two materializers below — materialize_at_plus_a_pattern and
// materialize_selfadjoint_pattern<UpLo> — share a common skeleton (counting
// sort to build A^T, then two passes of linear two-way merge to count and
// write column sizes). The two-way merge bodies are duplicated rather than
// extracted into shared helpers because every attempt to factor them out
// regressed performance by 2–5% on medium matrices: lambda-predicate
// unification (because the lambda call wasn't elided across the inner loop)
// and EIGEN_STRONG_INLINE function-template helpers (the compiler inlined
// them but the function-template boundary still produced measurably worse
// codegen than the explicit inline loops, presumably from differences in
// register allocation or vectorization heuristics around the merge's
// local-variable life-ranges). The merge logic is short enough that the
// duplication is a small maintenance burden in exchange for stable codegen
// on the AMD hot path. If a future compiler change makes the abstraction
// free, this can be revisited.

/** \internal
 * Materialize the symmetric pattern \c pattern(A + A^T) of a square column-major
 * sparsity pattern \a A as a compressed \c SparseMatrix with a 1-byte placeholder
 * \c Scalar. Values are filled with a fixed nonzero sentinel.
 *
 * Avoids the \c C = A.transpose(); symmat = C + A; chain previously performed
 * by Eigen's general-purpose evaluators. Both A's columns and A^T's columns
 * (built by counting sort) are sorted, so per-column union is a linear two-way
 * merge.
 *
 * Intended for fill-reducing ordering algorithms (AMD) that read only the
 * pattern. \b Precondition: \a A is square (\c outerSize == innerSize) and
 * \a out's storage must not alias \a A.
 */
template <typename StorageIndex>
void materialize_at_plus_a_pattern(const SparsityPatternRef<StorageIndex>& A,
                                   SparseMatrix<signed char, ColMajor, StorageIndex>& out) {
  const Index n = A.outerSize;
  eigen_assert(n == A.innerSize && "materialize_at_plus_a_pattern: A must be square");

  // 1. Build A^T's column starts (= A's row counts) via counting sort.
  Matrix<StorageIndex, Dynamic, 1> AT_p(n + 1);
  AT_p.setZero();
  for (Index j = 0; j < n; ++j) {
    const Index nz = A.nonZeros(j);
    const StorageIndex* col = A.inner + A.outer[j];
    for (Index k = 0; k < nz; ++k) ++AT_p(col[k] + 1);
  }
  for (Index i = 0; i < n; ++i) AT_p(i + 1) += AT_p(i);

  // 2. Place A^T's inner indices. Visiting A column-by-column in increasing j
  //    means each A^T column accumulates its inner indices in increasing order,
  //    so AT_i is sorted within each column. \a head is a deliberate deep copy
  //    of \c AT_p.head(n) — it is incremented as a per-column write cursor while
  //    \c AT_p remains the immutable column-start array used in passes 3 and 4.
  const StorageIndex a_nnz = AT_p(n);
  Matrix<StorageIndex, Dynamic, 1> AT_i(a_nnz);
  Matrix<StorageIndex, Dynamic, 1> head = AT_p.head(n);
  for (Index j = 0; j < n; ++j) {
    const Index nz = A.nonZeros(j);
    const StorageIndex* col = A.inner + A.outer[j];
    for (Index k = 0; k < nz; ++k) AT_i(head(col[k])++) = convert_index<StorageIndex>(j);
  }

  // 3. First merge pass: count column sizes of pattern(A + A^T) by two-way
  //    merge of A's and A^T's sorted columns.
  out.resize(n, n);
  StorageIndex* out_p = out.outerIndexPtr();
  out_p[0] = 0;
  for (Index j = 0; j < n; ++j) {
    const StorageIndex* a_col = A.inner + A.outer[j];
    const Index a_nz = A.nonZeros(j);
    const StorageIndex* at_col = AT_i.data() + AT_p(j);
    const Index at_nz = AT_p(j + 1) - AT_p(j);
    Index ia = 0, it = 0, count = 0;
    while (ia < a_nz && it < at_nz) {
      const StorageIndex va = a_col[ia], vt = at_col[it];
      if (va < vt) {
        ++ia;
      } else if (va > vt) {
        ++it;
      } else {
        ++ia;
        ++it;
      }
      ++count;
    }
    count += (a_nz - ia) + (at_nz - it);
    out_p[j + 1] = out_p[j] + convert_index<StorageIndex>(count);
  }

  const StorageIndex total = out_p[n];
  out.resizeNonZeros(total);

  // 4. Second merge pass: write merged inner indices.
  StorageIndex* out_i = out.innerIndexPtr();
  for (Index j = 0; j < n; ++j) {
    const StorageIndex* a_col = A.inner + A.outer[j];
    const Index a_nz = A.nonZeros(j);
    const StorageIndex* at_col = AT_i.data() + AT_p(j);
    const Index at_nz = AT_p(j + 1) - AT_p(j);
    StorageIndex* dst = out_i + out_p[j];
    Index ia = 0, it = 0;
    while (ia < a_nz && it < at_nz) {
      const StorageIndex va = a_col[ia], vt = at_col[it];
      if (va < vt) {
        *dst++ = va;
        ++ia;
      } else if (va > vt) {
        *dst++ = vt;
        ++it;
      } else {
        *dst++ = va;
        ++ia;
        ++it;
      }
    }
    while (ia < a_nz) *dst++ = a_col[ia++];
    while (it < at_nz) *dst++ = at_col[it++];
  }
  std::fill_n(out.valuePtr(), total, static_cast<signed char>(1));
}

/** \internal
 * Materialize the full symmetric pattern of a \c SparseSelfAdjointView<UpLo>
 * given the underlying matrix's pattern view \a A. \c UpLo must be \c Lower or
 * \c Upper; entries of \a A outside that triangle (and outside the diagonal)
 * are ignored, matching \c selfadjointView semantics.
 *
 * Output is a compressed column-major \c SparseMatrix<signed char> with
 * placeholder values, suitable for the AMD ordering pipeline. \b Precondition:
 * \a A is square; \a out's storage must not alias \a A.
 */
template <unsigned int UpLo, typename StorageIndex>
void materialize_selfadjoint_pattern(const SparsityPatternRef<StorageIndex>& A,
                                     SparseMatrix<signed char, ColMajor, StorageIndex>& out) {
  static_assert(UpLo == static_cast<unsigned>(Lower) || UpLo == static_cast<unsigned>(Upper),
                "UpLo must be Lower or Upper");
  const Index n = A.outerSize;
  eigen_assert(n == A.innerSize && "materialize_selfadjoint_pattern: A must be square");
  constexpr bool IsLower = (UpLo == static_cast<unsigned>(Lower));

  // 1. Count filtered row occurrences (A^T's column lengths). For UpLo == Lower
  //    we keep entries with row >= col; for Upper, row <= col. The diagonal is
  //    kept in both cases.
  Matrix<StorageIndex, Dynamic, 1> AT_p(n + 1);
  AT_p.setZero();
  for (Index j = 0; j < n; ++j) {
    const Index nz = A.nonZeros(j);
    const StorageIndex* col = A.inner + A.outer[j];
    for (Index k = 0; k < nz; ++k) {
      const StorageIndex r = col[k];
      const bool keep = IsLower ? (Index(r) >= j) : (Index(r) <= j);
      if (keep) ++AT_p(r + 1);
    }
  }
  for (Index i = 0; i < n; ++i) AT_p(i + 1) += AT_p(i);

  // 2. Place A^T's inner indices. The kept entries of column j are visited in
  //    sorted row-order, so each A^T column ends up sorted. \a head is a deep
  //    copy used as a per-column write cursor while \c AT_p stays untouched.
  const StorageIndex a_nnz = AT_p(n);
  Matrix<StorageIndex, Dynamic, 1> AT_i(a_nnz);
  Matrix<StorageIndex, Dynamic, 1> head = AT_p.head(n);
  for (Index j = 0; j < n; ++j) {
    const Index nz = A.nonZeros(j);
    const StorageIndex* col = A.inner + A.outer[j];
    for (Index k = 0; k < nz; ++k) {
      const StorageIndex r = col[k];
      const bool keep = IsLower ? (Index(r) >= j) : (Index(r) <= j);
      if (keep) AT_i(head(r)++) = convert_index<StorageIndex>(j);
    }
  }

  // Cache the kept-range bound for each column so passes 3 and 4 don't both
  // pay the binary search. For UpLo == Lower the kept range starts at
  // \c lower_bound(j) and runs to the end of the column; for Upper it starts
  // at 0 and ends at \c upper_bound(j). Storing only the side that requires a
  // search keeps scratch to one Index per column.
  Matrix<Index, Dynamic, 1> a_split(n);
  for (Index j = 0; j < n; ++j) {
    const StorageIndex* a_col = A.inner + A.outer[j];
    const Index a_nz = A.nonZeros(j);
    if (IsLower) {
      a_split(j) = std::lower_bound(a_col, a_col + a_nz, StorageIndex(j)) - a_col;
    } else {
      a_split(j) = std::upper_bound(a_col, a_col + a_nz, StorageIndex(j)) - a_col;
    }
  }

  // 3. First merge pass: count column sizes of pattern(A + A^T) over the
  //    filtered subrange of each column.
  out.resize(n, n);
  StorageIndex* out_p = out.outerIndexPtr();
  out_p[0] = 0;
  for (Index j = 0; j < n; ++j) {
    const StorageIndex* a_col = A.inner + A.outer[j];
    const Index a_nz = A.nonZeros(j);
    const StorageIndex* a_kept = IsLower ? a_col + a_split(j) : a_col;
    const Index a_kept_nz = IsLower ? (a_nz - a_split(j)) : a_split(j);
    const StorageIndex* at_col = AT_i.data() + AT_p(j);
    const Index at_nz = AT_p(j + 1) - AT_p(j);
    Index ia = 0, it = 0, count = 0;
    while (ia < a_kept_nz && it < at_nz) {
      const StorageIndex va = a_kept[ia], vt = at_col[it];
      if (va < vt) {
        ++ia;
      } else if (va > vt) {
        ++it;
      } else {
        ++ia;
        ++it;
      }
      ++count;
    }
    count += (a_kept_nz - ia) + (at_nz - it);
    out_p[j + 1] = out_p[j] + convert_index<StorageIndex>(count);
  }

  const StorageIndex total = out_p[n];
  out.resizeNonZeros(total);

  // 4. Second merge pass: write merged inner indices.
  StorageIndex* out_i = out.innerIndexPtr();
  for (Index j = 0; j < n; ++j) {
    const StorageIndex* a_col = A.inner + A.outer[j];
    const Index a_nz = A.nonZeros(j);
    const StorageIndex* a_kept = IsLower ? a_col + a_split(j) : a_col;
    const Index a_kept_nz = IsLower ? (a_nz - a_split(j)) : a_split(j);
    const StorageIndex* at_col = AT_i.data() + AT_p(j);
    const Index at_nz = AT_p(j + 1) - AT_p(j);
    StorageIndex* dst = out_i + out_p[j];
    Index ia = 0, it = 0;
    while (ia < a_kept_nz && it < at_nz) {
      const StorageIndex va = a_kept[ia], vt = at_col[it];
      if (va < vt) {
        *dst++ = va;
        ++ia;
      } else if (va > vt) {
        *dst++ = vt;
        ++it;
      } else {
        *dst++ = va;
        ++ia;
        ++it;
      }
    }
    while (ia < a_kept_nz) *dst++ = a_kept[ia++];
    while (it < at_nz) *dst++ = at_col[it++];
  }
  std::fill_n(out.valuePtr(), total, static_cast<signed char>(1));
}

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_SPARSITY_PATTERN_REF_H
