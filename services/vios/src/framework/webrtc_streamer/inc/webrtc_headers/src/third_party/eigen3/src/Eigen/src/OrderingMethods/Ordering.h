// SPDX-License-Identifier: MPL-2.0

// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2012  Désiré Nuentsa-Wakam <desire.nuentsa_wakam@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.

#ifndef EIGEN_ORDERING_H
#define EIGEN_ORDERING_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"
#include "Eigen_Colamd.h"

namespace Eigen {

/** \ingroup OrderingMethods_Module
 * \class AMDOrdering
 *
 * Functor computing the \em approximate \em minimum \em degree ordering
 * If the matrix is not structurally symmetric, an ordering of A^T+A is computed.
 * Only the sparsity pattern of the input is read — scalar values are not.
 * \tparam  StorageIndex The type of indices of the matrix
 * \sa COLAMDOrdering
 */
template <typename StorageIndex>
class AMDOrdering {
 public:
  typedef PermutationMatrix<Dynamic, Dynamic, StorageIndex> PermutationType;

  /** Compute the permutation vector from a sparse matrix.
   * Only the sparsity pattern of \a mat is read; scalar values are not.
   * This routine is much faster if the input matrix is column-major.
   */
  template <typename MatrixType>
  void operator()(const MatrixType& mat, PermutationType& perm) const {
    // AMD only reads the sparsity pattern. Build a column-major view of mat,
    // then materialize \c pattern(mat + mat^T) directly into a
    // SparseMatrix<signed char> (1-byte placeholder values), bypassing
    // Eigen's generic transpose + sparse-sum evaluators.
    Matrix<StorageIndex, Dynamic, 1> outer_buf;
    Matrix<StorageIndex, Dynamic, 1> inner_buf;
    internal::SparsityPatternRef<StorageIndex> pat = internal::make_col_major_pattern_ref(mat, outer_buf, inner_buf);
    SparseMatrix<signed char, ColMajor, StorageIndex> symm;
    internal::materialize_at_plus_a_pattern(pat, symm);
    internal::minimum_degree_ordering(symm, perm);
  }

  /** Compute the permutation with a selfadjoint matrix.
   * Only the sparsity pattern is used; scalar values are not.
   */
  template <typename SrcType, unsigned int SrcUpLo>
  void operator()(const SparseSelfAdjointView<SrcType, SrcUpLo>& mat, PermutationType& perm) const {
    // Build a column-major pattern view of the underlying matrix and expand
    // its UpLo triangle to the full symmetric pattern in one pass, bypassing
    // Eigen's generic selfadjointView assignment evaluator.
    Matrix<StorageIndex, Dynamic, 1> outer_buf;
    Matrix<StorageIndex, Dynamic, 1> inner_buf;
    internal::SparsityPatternRef<StorageIndex> pat =
        internal::make_col_major_pattern_ref(mat.matrix(), outer_buf, inner_buf);
    SparseMatrix<signed char, ColMajor, StorageIndex> symm;
    internal::materialize_selfadjoint_pattern<SrcUpLo>(pat, symm);
    internal::minimum_degree_ordering(symm, perm);
  }
};

/** \ingroup OrderingMethods_Module
 * \class NaturalOrdering
 *
 * Functor computing the natural ordering (identity)
 *
 * \note Returns an empty permutation matrix
 * \tparam  StorageIndex The type of indices of the matrix
 */
template <typename StorageIndex>
class NaturalOrdering {
 public:
  typedef PermutationMatrix<Dynamic, Dynamic, StorageIndex> PermutationType;

  /** Compute the permutation vector from a column-major sparse matrix */
  template <typename MatrixType>
  void operator()(const MatrixType& /*mat*/, PermutationType& perm) const {
    perm.resize(0);
  }
};

/** \ingroup OrderingMethods_Module
 * \class COLAMDOrdering
 *
 * \tparam  StorageIndex The type of indices of the matrix
 *
 * Functor computing the \em column \em approximate \em minimum \em degree ordering
 * The matrix should be in column-major and \b compressed format (see SparseMatrix::makeCompressed()).
 */
template <typename StorageIndex>
class COLAMDOrdering {
 public:
  typedef PermutationMatrix<Dynamic, Dynamic, StorageIndex> PermutationType;
  typedef Matrix<StorageIndex, Dynamic, 1> IndexVector;

  /** Compute the permutation vector \a perm form the sparse matrix \a mat
   * \warning The input sparse matrix \a mat must be in compressed mode (see SparseMatrix::makeCompressed()).
   */
  template <typename MatrixType>
  void operator()(const MatrixType& mat, PermutationType& perm) const {
    eigen_assert(mat.isCompressed() &&
                 "COLAMDOrdering requires a sparse matrix in compressed mode. Call .makeCompressed() before passing it "
                 "to COLAMDOrdering");

    StorageIndex m = StorageIndex(mat.rows());
    StorageIndex n = StorageIndex(mat.cols());
    StorageIndex nnz = StorageIndex(mat.nonZeros());
    // Get the recommended value of Alen to be used by colamd
    StorageIndex Alen = internal::Colamd::recommended(nnz, m, n);
    // Set the default parameters
    double knobs[internal::Colamd::NKnobs];
    StorageIndex stats[internal::Colamd::NStats];
    internal::Colamd::set_defaults(knobs);

    IndexVector p(n + 1), A(Alen);
    for (StorageIndex i = 0; i <= n; i++) p(i) = mat.outerIndexPtr()[i];
    for (StorageIndex i = 0; i < nnz; i++) A(i) = mat.innerIndexPtr()[i];
    // Call Colamd routine to compute the ordering
    StorageIndex info = internal::Colamd::compute_ordering(m, n, Alen, A.data(), p.data(), knobs, stats);
    EIGEN_UNUSED_VARIABLE(info);
    eigen_assert(info && "COLAMD failed ");

    perm.resize(n);
    for (StorageIndex i = 0; i < n; i++) perm.indices()(p(i)) = i;
  }
};

}  // end namespace Eigen

#endif
