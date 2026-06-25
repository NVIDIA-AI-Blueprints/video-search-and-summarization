// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// We used the "A Divide-And-Conquer Algorithm for the Bidiagonal SVD"
// research report written by Ming Gu and Stanley C.Eisenstat
// The code variable names correspond to the names they used in their
// report
//
// Copyright (C) 2013 Gauthier Brun <brun.gauthier@gmail.com>
// Copyright (C) 2013 Nicolas Carre <nicolas.carre@ensimag.fr>
// Copyright (C) 2013 Jean Ceccato <jean.ceccato@ensimag.fr>
// Copyright (C) 2013 Pierre Zoppitelli <pierre.zoppitelli@ensimag.fr>
// Copyright (C) 2013 Jitse Niesen <jitse@maths.leeds.ac.uk>
// Copyright (C) 2014-2017 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_BDCSVD_IMPL_H
#define EIGEN_BDCSVD_IMPL_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

/** \internal
 * Implementation of the divide-and-conquer phase of BDCSVD.
 *
 * Templated only on RealScalar so that all BDCSVD instantiations sharing the same
 * RealScalar (e.g. BDCSVD<MatrixXd, ComputeThinU|ComputeThinV> and
 * BDCSVD<MatrixXd, ComputeFullU|ComputeFullV>, or BDCSVD<MatrixXcd> and
 * BDCSVD<MatrixXd>) share a single copy of the ~950 lines of D&C code.
 */
template <typename RealScalar_>
class bdcsvd_impl {
 public:
  typedef RealScalar_ RealScalar;
  typedef typename NumTraits<RealScalar>::Literal Literal;
  typedef Matrix<RealScalar, Dynamic, Dynamic, ColMajor> MatrixXr;
  typedef Matrix<RealScalar, Dynamic, 1> VectorType;
  typedef Array<RealScalar, Dynamic, 1> ArrayXr;
  typedef Array<Index, 1, Dynamic> ArrayXi;
  typedef Ref<ArrayXr> ArrayRef;
  typedef Ref<ArrayXi> IndicesRef;

  bdcsvd_impl() : m_algoswap(16), m_compU(false), m_compV(false), m_numIters(0), m_info(Success) {}

  void allocate(Index diagSize, bool compU, bool compV);

  /** Entry point for the divide-and-conquer phase. */
  void divide(Index firstCol, Index lastCol, Index firstRowW, Index firstColW, Index shift);

  MatrixXr& naiveU() { return m_naiveU; }
  const MatrixXr& naiveU() const { return m_naiveU; }
  MatrixXr& naiveV() { return m_naiveV; }
  const MatrixXr& naiveV() const { return m_naiveV; }
  MatrixXr& computed() { return m_computed; }
  const MatrixXr& computed() const { return m_computed; }
  ComputationInfo info() const { return m_info; }
  int numIters() const { return m_numIters; }
  int algoSwap() const { return m_algoswap; }
  void setAlgoSwap(int s) { m_algoswap = s; }

 private:
  void computeSVDofM(Index firstCol, Index n, MatrixXr& U, VectorType& singVals, MatrixXr& V);
  void computeSingVals(const ArrayRef& col0, const ArrayRef& diag, const IndicesRef& perm, VectorType& singVals,
                       ArrayRef shifts, ArrayRef mus);
  void perturbCol0(const ArrayRef& col0, const ArrayRef& diag, const IndicesRef& perm, const VectorType& singVals,
                   const ArrayRef& shifts, const ArrayRef& mus, ArrayRef zhat);
  void computeSingVecs(const ArrayRef& zhat, const ArrayRef& diag, const IndicesRef& perm, const VectorType& singVals,
                       const ArrayRef& shifts, const ArrayRef& mus, MatrixXr& U, MatrixXr& V);
  void deflation43(Index firstCol, Index shift, Index i, Index size);
  void deflation44(Index firstColu, Index firstColm, Index firstRowW, Index firstColW, Index i, Index j, Index size);
  void deflation(Index firstCol, Index lastCol, Index k, Index firstRowW, Index firstColW, Index shift);
  void structured_update(Block<MatrixXr, Dynamic, Dynamic> A, const MatrixXr& B, Index n1);
  static RealScalar secularEq(RealScalar x, const ArrayRef& col0, const ArrayRef& diag, const IndicesRef& perm,
                              const ArrayRef& diagShifted, RealScalar shift);
  template <typename SVDType>
  void computeBaseCase(SVDType& svd, Index n, Index firstCol, Index firstRowW, Index firstColW, Index shift);

  MatrixXr m_naiveU, m_naiveV;
  MatrixXr m_computed;
  ArrayXr m_workspace;
  ArrayXi m_workspaceI;
  // Reused base-case JacobiSVDs (one per option set) so that recursive divide()
  // calls don't reallocate JacobiSVD's internal U/V/sigma buffers each time.
  JacobiSVD<MatrixXr, ComputeFullU> m_baseSvdU;
  JacobiSVD<MatrixXr, ComputeFullU | ComputeFullV> m_baseSvdUV;
  int m_algoswap;
  bool m_compU, m_compV;
  int m_numIters;
  ComputationInfo m_info;
};

template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::allocate(Index diagSize, bool compU, bool compV) {
  m_compU = compU;
  m_compV = compV;
  m_numIters = 0;
  m_info = Success;

  m_computed = MatrixXr::Zero(diagSize + 1, diagSize);

  if (m_compU)
    m_naiveU = MatrixXr::Zero(diagSize + 1, diagSize + 1);
  else
    m_naiveU = MatrixXr::Zero(2, diagSize + 1);

  if (m_compV) m_naiveV = MatrixXr::Zero(diagSize, diagSize);

  m_workspace.resize((diagSize + 1) * (diagSize + 1) * 3);
  m_workspaceI.resize(3 * diagSize);
}

/** \internal
 * Performs A = A * B exploiting the special structure of the matrix A. Splitting A as:
 *  A = [A1]
 *      [A2]
 * such that A1.rows()==n1, then we assume that at least half of the columns of A1 and A2 are zeros.
 * We can thus pack them prior to the matrix product. However, this is only worth the effort if the matrix is large
 * enough.
 */
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::structured_update(Block<MatrixXr, Dynamic, Dynamic> A, const MatrixXr& B, Index n1) {
  Index n = A.rows();
  if (n > 100) {
    // If the matrices are large enough, let's exploit the sparse structure of A by
    // splitting it in half (wrt n1), and packing the non-zero columns.
    Index n2 = n - n1;
    Map<MatrixXr> A1(m_workspace.data(), n1, n);
    Map<MatrixXr> A2(m_workspace.data() + n1 * n, n2, n);
    Map<MatrixXr> B1(m_workspace.data() + n * n, n, n);
    Map<MatrixXr> B2(m_workspace.data() + 2 * n * n, n, n);
    Index k1 = 0, k2 = 0;
    for (Index j = 0; j < n; ++j) {
      if ((A.col(j).head(n1).array() != Literal(0)).any()) {
        A1.col(k1) = A.col(j).head(n1);
        B1.row(k1) = B.row(j);
        ++k1;
      }
      if ((A.col(j).tail(n2).array() != Literal(0)).any()) {
        A2.col(k2) = A.col(j).tail(n2);
        B2.row(k2) = B.row(j);
        ++k2;
      }
    }

    A.topRows(n1).noalias() = A1.leftCols(k1) * B1.topRows(k1);
    A.bottomRows(n2).noalias() = A2.leftCols(k2) * B2.topRows(k2);
  } else {
    Map<MatrixXr, Aligned> tmp(m_workspace.data(), n, n);
    tmp.noalias() = A * B;
    A = tmp;
  }
}

template <typename RealScalar_>
template <typename SVDType>
void bdcsvd_impl<RealScalar_>::computeBaseCase(SVDType& svd, Index n, Index firstCol, Index firstRowW, Index firstColW,
                                               Index shift) {
  svd.compute(m_computed.block(firstCol, firstCol, n + 1, n));
  m_info = svd.info();
  if (m_info != Success && m_info != NoConvergence) return;
  if (m_compU)
    m_naiveU.block(firstCol, firstCol, n + 1, n + 1) = svd.matrixU();
  else {
    m_naiveU.row(0).segment(firstCol, n + 1) = svd.matrixU().row(0);
    m_naiveU.row(1).segment(firstCol, n + 1) = svd.matrixU().row(n);
  }
  if (m_compV) m_naiveV.block(firstRowW, firstColW, n, n) = svd.matrixV();
  m_computed.block(firstCol + shift, firstCol + shift, n + 1, n).setZero();
  m_computed.diagonal().segment(firstCol + shift, n) = svd.singularValues().head(n);
}

// The divide algorithm is done "in place", we are always working on subsets of the same matrix. The divide methods
// takes as argument the place of the submatrix we are currently working on.

//@param firstCol : The Index of the first column of the submatrix of m_computed and for m_naiveU;
//@param lastCol : The Index of the last column of the submatrix of m_computed and for m_naiveU;
// lastCol + 1 - firstCol is the size of the submatrix.
//@param firstRowW : The Index of the first row of the matrix W that we are to change. (see the reference paper section
// 1 for more information on W)
//@param firstColW : Same as firstRowW with the column.
//@param shift : Each time one takes the left submatrix, one must add 1 to the shift. Why? Because! We actually want the
// last column of the U submatrix
// to become the first column (*coeff) and to shift all the other columns to the right. There are more details on the
// reference paper.
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::divide(Index firstCol, Index lastCol, Index firstRowW, Index firstColW, Index shift) {
  // requires rows = cols + 1;
  using std::abs;
  using std::sqrt;
  const Index n = lastCol - firstCol + 1;
  const Index k = n / 2;
  const RealScalar considerZero = (std::numeric_limits<RealScalar>::min)();
  RealScalar alphaK;
  RealScalar betaK;
  RealScalar r0;
  RealScalar lambda, phi, c0, s0;
  VectorType l, f;
  // We use the other algorithm which is more efficient for small
  // matrices.
  if (n < m_algoswap) {
    if (m_compV) {
      computeBaseCase(m_baseSvdUV, n, firstCol, firstRowW, firstColW, shift);
    } else {
      computeBaseCase(m_baseSvdU, n, firstCol, firstRowW, firstColW, shift);
    }
    return;
  }
  // We use the divide and conquer algorithm
  alphaK = m_computed(firstCol + k, firstCol + k);
  betaK = m_computed(firstCol + k + 1, firstCol + k);
  // The divide must be done in that order in order to have good results. Divide change the data inside the submatrices
  // and the divide of the right submatrice reads one column of the left submatrice. That's why we need to treat the
  // right submatrix before the left one.
  divide(k + 1 + firstCol, lastCol, k + 1 + firstRowW, k + 1 + firstColW, shift);
  if (m_info != Success && m_info != NoConvergence) return;
  divide(firstCol, k - 1 + firstCol, firstRowW, firstColW + 1, shift + 1);
  if (m_info != Success && m_info != NoConvergence) return;

  if (m_compU) {
    lambda = m_naiveU(firstCol + k, firstCol + k);
    phi = m_naiveU(firstCol + k + 1, lastCol + 1);
  } else {
    lambda = m_naiveU(1, firstCol + k);
    phi = m_naiveU(0, lastCol + 1);
  }
  r0 = sqrt((abs(alphaK * lambda) * abs(alphaK * lambda)) + abs(betaK * phi) * abs(betaK * phi));
  if (m_compU) {
    l = m_naiveU.row(firstCol + k).segment(firstCol, k);
    f = m_naiveU.row(firstCol + k + 1).segment(firstCol + k + 1, n - k - 1);
  } else {
    l = m_naiveU.row(1).segment(firstCol, k);
    f = m_naiveU.row(0).segment(firstCol + k + 1, n - k - 1);
  }
  if (m_compV) m_naiveV(firstRowW + k, firstColW) = Literal(1);
  if (r0 < considerZero) {
    c0 = Literal(1);
    s0 = Literal(0);
  } else {
    c0 = alphaK * lambda / r0;
    s0 = betaK * phi / r0;
  }

  if (m_compU) {
    MatrixXr q1(m_naiveU.col(firstCol + k).segment(firstCol, k + 1));
    // we shiftW Q1 to the right
    for (Index i = firstCol + k - 1; i >= firstCol; i--)
      m_naiveU.col(i + 1).segment(firstCol, k + 1) = m_naiveU.col(i).segment(firstCol, k + 1);
    // we shift q1 at the left with a factor c0
    m_naiveU.col(firstCol).segment(firstCol, k + 1) = (q1 * c0);
    // last column = q1 * - s0
    m_naiveU.col(lastCol + 1).segment(firstCol, k + 1) = (q1 * (-s0));
    // first column = q2 * s0
    m_naiveU.col(firstCol).segment(firstCol + k + 1, n - k) =
        m_naiveU.col(lastCol + 1).segment(firstCol + k + 1, n - k) * s0;
    // q2 *= c0
    m_naiveU.col(lastCol + 1).segment(firstCol + k + 1, n - k) *= c0;
  } else {
    RealScalar q1 = m_naiveU(0, firstCol + k);
    // we shift Q1 to the right
    for (Index i = firstCol + k - 1; i >= firstCol; i--) m_naiveU(0, i + 1) = m_naiveU(0, i);
    // we shift q1 at the left with a factor c0
    m_naiveU(0, firstCol) = (q1 * c0);
    // last column = q1 * - s0
    m_naiveU(0, lastCol + 1) = (q1 * (-s0));
    // first column = q2 * s0
    m_naiveU(1, firstCol) = m_naiveU(1, lastCol + 1) * s0;
    // q2 *= c0
    m_naiveU(1, lastCol + 1) *= c0;
    m_naiveU.row(1).segment(firstCol + 1, k).setZero();
    m_naiveU.row(0).segment(firstCol + k + 1, n - k - 1).setZero();
  }

  m_computed(firstCol + shift, firstCol + shift) = r0;
  m_computed.col(firstCol + shift).segment(firstCol + shift + 1, k) = alphaK * l.transpose();
  m_computed.col(firstCol + shift).segment(firstCol + shift + k + 1, n - k - 1) = betaK * f.transpose();

  // Second part: try to deflate singular values in combined matrix
  deflation(firstCol, lastCol, k, firstRowW, firstColW, shift);

  // Third part: compute SVD of combined matrix
  MatrixXr UofSVD, VofSVD;
  VectorType singVals;
  computeSVDofM(firstCol + shift, n, UofSVD, singVals, VofSVD);

  if (m_compU)
    structured_update(m_naiveU.block(firstCol, firstCol, n + 1, n + 1), UofSVD, (n + 2) / 2);
  else {
    Map<Matrix<RealScalar, 2, Dynamic>, Aligned> tmp(m_workspace.data(), 2, n + 1);
    tmp.noalias() = m_naiveU.middleCols(firstCol, n + 1) * UofSVD;
    m_naiveU.middleCols(firstCol, n + 1) = tmp;
  }

  if (m_compV) structured_update(m_naiveV.block(firstRowW, firstColW, n, n), VofSVD, (n + 1) / 2);

  m_computed.block(firstCol + shift, firstCol + shift, n, n).setZero();
  m_computed.block(firstCol + shift, firstCol + shift, n, n).diagonal() = singVals;
}  // end divide

// Compute SVD of m_computed.block(firstCol, firstCol, n + 1, n); this block only has non-zeros in
// the first column and on the diagonal and has undergone deflation, so diagonal is in increasing
// order except for possibly the (0,0) entry. The computed SVD is stored U, singVals and V, except
// that if m_compV is false, then V is not computed. Singular values are sorted in decreasing order.
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::computeSVDofM(Index firstCol, Index n, MatrixXr& U, VectorType& singVals, MatrixXr& V) {
  const RealScalar considerZero = (std::numeric_limits<RealScalar>::min)();
  using std::abs;
  ArrayRef col0 = m_computed.col(firstCol).segment(firstCol, n);
  m_workspace.head(n) = m_computed.block(firstCol, firstCol, n, n).diagonal();
  ArrayRef diag = m_workspace.head(n);
  diag(0) = Literal(0);

  // Allocate space for singular values and vectors
  singVals.resize(n);
  U.resize(n + 1, n + 1);
  if (m_compV) V.resize(n, n);

  // Many singular values might have been deflated, the zero ones have been moved to the end,
  // but others are interleaved and we must ignore them at this stage.
  // To this end, let's compute a permutation skipping them:
  Index actual_n = n;
  while (actual_n > 1 && numext::is_exactly_zero(diag(actual_n - 1))) {
    --actual_n;
    eigen_internal_assert(numext::is_exactly_zero(col0(actual_n)));
  }
  Index m = 0;  // size of the deflated problem
  for (Index k = 0; k < actual_n; ++k)
    if (abs(col0(k)) > considerZero) m_workspaceI(m++) = k;
  Map<ArrayXi> perm(m_workspaceI.data(), m);

  Map<ArrayXr> shifts(m_workspace.data() + 1 * n, n);
  Map<ArrayXr> mus(m_workspace.data() + 2 * n, n);
  Map<ArrayXr> zhat(m_workspace.data() + 3 * n, n);

  // Compute singVals, shifts, and mus
  computeSingVals(col0, diag, perm, singVals, shifts, mus);

  // Compute zhat
  perturbCol0(col0, diag, perm, singVals, shifts, mus, zhat);

  computeSingVecs(zhat, diag, perm, singVals, shifts, mus, U, V);

  // Because of deflation, the singular values might not be completely sorted.
  // Fortunately, reordering them is a O(n) problem
  for (Index i = 0; i < actual_n - 1; ++i) {
    if (singVals(i) > singVals(i + 1)) {
      using std::swap;
      swap(singVals(i), singVals(i + 1));
      U.col(i).swap(U.col(i + 1));
      if (m_compV) V.col(i).swap(V.col(i + 1));
    }
  }

  // Reverse order so that singular values in increased order
  // Because of deflation, the zeros singular-values are already at the end
  singVals.head(actual_n).reverseInPlace();
  U.leftCols(actual_n).rowwise().reverseInPlace();
  if (m_compV) V.leftCols(actual_n).rowwise().reverseInPlace();
}

template <typename RealScalar_>
typename bdcsvd_impl<RealScalar_>::RealScalar bdcsvd_impl<RealScalar_>::secularEq(RealScalar mu, const ArrayRef& col0,
                                                                                  const ArrayRef& diag,
                                                                                  const IndicesRef& perm,
                                                                                  const ArrayRef& diagShifted,
                                                                                  RealScalar shift) {
  Index m = perm.size();
  RealScalar res = Literal(1);
  for (Index i = 0; i < m; ++i) {
    Index j = perm(i);
    // The following expression could be rewritten to involve only a single division,
    // but this would make the expression more sensitive to overflow.
    res += (col0(j) / (diagShifted(j) - mu)) * (col0(j) / (diag(j) + shift + mu));
  }
  return res;
}

template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::computeSingVals(const ArrayRef& col0, const ArrayRef& diag, const IndicesRef& perm,
                                               VectorType& singVals, ArrayRef shifts, ArrayRef mus) {
  using std::abs;
  using std::sqrt;
  using std::swap;

  Index n = col0.size();
  Index actual_n = n;
  // Note that here actual_n is computed based on col0(i)==0 instead of diag(i)==0 as above
  // because 1) we have diag(i)==0 => col0(i)==0 and 2) if col0(i)==0, then diag(i) is already a singular value.
  while (actual_n > 1 && numext::is_exactly_zero(col0(actual_n - 1))) --actual_n;

  for (Index k = 0; k < n; ++k) {
    if (numext::is_exactly_zero(col0(k)) || actual_n == 1) {
      // if col0(k) == 0, then entry is deflated, so singular value is on diagonal
      // if actual_n==1, then the deflated problem is already diagonalized
      singVals(k) = k == 0 ? col0(0) : diag(k);
      mus(k) = Literal(0);
      shifts(k) = k == 0 ? col0(0) : diag(k);
      continue;
    }

    // otherwise, use secular equation to find singular value
    RealScalar left = diag(k);
    RealScalar right;  // was: = (k != actual_n-1) ? diag(k+1) : (diag(actual_n-1) + col0.matrix().norm());
    if (k == actual_n - 1)
      right = (diag(actual_n - 1) + col0.matrix().stableNorm());
    else {
      // Skip deflated singular values,
      // recall that at this stage we assume that z[j]!=0 and all entries for which z[j]==0 have been put aside.
      // This should be equivalent to using perm[]
      Index l = k + 1;
      while (numext::is_exactly_zero(col0(l))) {
        ++l;
        eigen_internal_assert(l < actual_n);
      }
      right = diag(l);
    }

    // first decide whether it's closer to the left end or the right end
    RealScalar mid = left + (right - left) / Literal(2);
    RealScalar fMid = secularEq(mid, col0, diag, perm, diag, Literal(0));
    RealScalar shift = (k == actual_n - 1 || fMid > Literal(0)) ? left : right;

    // measure everything relative to shift
    Map<ArrayXr> diagShifted(m_workspace.data() + 4 * n, n);
    diagShifted = diag - shift;

    if (k != actual_n - 1) {
      // check that after the shift, f(mid) is still negative:
      RealScalar midShifted = (right - left) / RealScalar(2);
      // we can test exact equality here, because shift comes from `... ? left : right`
      if (numext::equal_strict(shift, right)) midShifted = -midShifted;
      RealScalar fMidShifted = secularEq(midShifted, col0, diag, perm, diagShifted, shift);
      if (fMidShifted > 0) {
        // fMid was erroneous, fix it:
        shift = fMidShifted > Literal(0) ? left : right;
        diagShifted = diag - shift;
      }
    }

    // initial guess
    RealScalar muPrev, muCur;
    // we can test exact equality here, because shift comes from `... ? left : right`
    if (numext::equal_strict(shift, left)) {
      muPrev = (right - left) * RealScalar(0.1);
      if (k == actual_n - 1)
        muCur = right - left;
      else
        muCur = (right - left) * RealScalar(0.5);
    } else {
      muPrev = -(right - left) * RealScalar(0.1);
      muCur = -(right - left) * RealScalar(0.5);
    }

    RealScalar fPrev = secularEq(muPrev, col0, diag, perm, diagShifted, shift);
    RealScalar fCur = secularEq(muCur, col0, diag, perm, diagShifted, shift);
    if (abs(fPrev) < abs(fCur)) {
      swap(fPrev, fCur);
      swap(muPrev, muCur);
    }

    // rational interpolation: fit a function of the form a / mu + b through the two previous
    // iterates and use its zero to compute the next iterate
    bool useBisection = fPrev * fCur > Literal(0);
    while (!numext::is_exactly_zero(fCur) &&
           abs(muCur - muPrev) >
               Literal(8) * NumTraits<RealScalar>::epsilon() * numext::maxi<RealScalar>(abs(muCur), abs(muPrev)) &&
           abs(fCur - fPrev) > NumTraits<RealScalar>::epsilon() && !useBisection) {
      ++m_numIters;

      // Find a and b such that the function f(mu) = a / mu + b matches the current and previous samples.
      RealScalar a = (fCur - fPrev) / (Literal(1) / muCur - Literal(1) / muPrev);
      RealScalar b = fCur - a / muCur;
      // And find mu such that f(mu)==0:
      RealScalar muZero = -a / b;
      RealScalar fZero = secularEq(muZero, col0, diag, perm, diagShifted, shift);

      muPrev = muCur;
      fPrev = fCur;
      muCur = muZero;
      fCur = fZero;

      // we can test exact equality here, because shift comes from `... ? left : right`
      if (numext::equal_strict(shift, left) && (muCur < Literal(0) || muCur > right - left)) useBisection = true;
      if (numext::equal_strict(shift, right) && (muCur < -(right - left) || muCur > Literal(0))) useBisection = true;
      if (abs(fCur) > abs(fPrev)) useBisection = true;
    }

    // fall back on bisection method if rational interpolation did not work
    if (useBisection) {
      RealScalar leftShifted, rightShifted;
      // we can test exact equality here, because shift comes from `... ? left : right`
      if (numext::equal_strict(shift, left)) {
        // to avoid overflow, we must have mu > max(real_min, |z(k)|/sqrt(real_max)),
        // the factor 2 is to be more conservative
        leftShifted =
            numext::maxi<RealScalar>((std::numeric_limits<RealScalar>::min)(),
                                     Literal(2) * abs(col0(k)) / sqrt((std::numeric_limits<RealScalar>::max)()));

        // check that we did it right:
        eigen_internal_assert(
            (numext::isfinite)((col0(k) / leftShifted) * (col0(k) / (diag(k) + shift + leftShifted))));
        rightShifted = (k == actual_n - 1)
                           ? right
                           : ((right - left) * RealScalar(0.51));  // theoretically we can take 0.5, but let's be safe
      } else {
        leftShifted = -(right - left) * RealScalar(0.51);
        if (k + 1 < n)
          rightShifted = -numext::maxi<RealScalar>((std::numeric_limits<RealScalar>::min)(),
                                                   abs(col0(k + 1)) / sqrt((std::numeric_limits<RealScalar>::max)()));
        else
          rightShifted = -(std::numeric_limits<RealScalar>::min)();
      }
      RealScalar fLeft = secularEq(leftShifted, col0, diag, perm, diagShifted, shift);
      eigen_internal_assert(fLeft < Literal(0));

      if (fLeft < Literal(0)) {
        while (rightShifted - leftShifted > Literal(2) * NumTraits<RealScalar>::epsilon() *
                                                numext::maxi<RealScalar>(abs(leftShifted), abs(rightShifted))) {
          RealScalar midShifted = (leftShifted + rightShifted) / Literal(2);
          fMid = secularEq(midShifted, col0, diag, perm, diagShifted, shift);
          eigen_internal_assert((numext::isfinite)(fMid));

          if (fLeft * fMid < Literal(0)) {
            rightShifted = midShifted;
          } else {
            leftShifted = midShifted;
            fLeft = fMid;
          }
        }
        muCur = (leftShifted + rightShifted) / Literal(2);
      } else {
        // We have a problem as shifting on the left or right give either a positive or negative value
        // at the middle of [left,right]...
        // Instead of abbording or entering an infinite loop,
        // let's just use the middle as the estimated zero-crossing:
        muCur = (right - left) * RealScalar(0.5);
        // we can test exact equality here, because shift comes from `... ? left : right`
        if (numext::equal_strict(shift, right)) muCur = -muCur;
      }
    }

    singVals[k] = shift + muCur;
    shifts[k] = shift;
    mus[k] = muCur;
  }
}

// zhat is perturbation of col0 for which singular vectors can be computed stably (see Section 3.1)
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::perturbCol0(const ArrayRef& col0, const ArrayRef& diag, const IndicesRef& perm,
                                           const VectorType& singVals, const ArrayRef& shifts, const ArrayRef& mus,
                                           ArrayRef zhat) {
  using std::sqrt;
  Index n = col0.size();
  Index m = perm.size();
  if (m == 0) {
    zhat.setZero();
    return;
  }
  Index lastIdx = perm(m - 1);
  // The offset permits to skip deflated entries while computing zhat
  for (Index k = 0; k < n; ++k) {
    if (numext::is_exactly_zero(col0(k)))  // deflated
      zhat(k) = Literal(0);
    else {
      // see equation (3.6)
      RealScalar dk = diag(k);
      RealScalar prod = (singVals(lastIdx) + dk) * (mus(lastIdx) + (shifts(lastIdx) - dk));

      for (Index l = 0; l < m; ++l) {
        Index i = perm(l);
        if (i != k) {
          // There is no valid predecessor when the first active index is already on the
          // right of k. Treat this as a numerical issue and zero the product.
          if (i >= k && l == 0) {
            m_info = NumericalIssue;
            prod = Literal(0);
            break;
          }
          Index j = i < k ? i : perm(l - 1);
          prod *= ((singVals(j) + dk) / ((diag(i) + dk))) * ((mus(j) + (shifts(j) - dk)) / ((diag(i) - dk)));
        }
      }
      RealScalar tmp = sqrt(prod);
      zhat(k) = col0(k) > Literal(0) ? RealScalar(tmp) : RealScalar(-tmp);
    }
  }
}

// compute singular vectors
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::computeSingVecs(const ArrayRef& zhat, const ArrayRef& diag, const IndicesRef& perm,
                                               const VectorType& singVals, const ArrayRef& shifts, const ArrayRef& mus,
                                               MatrixXr& U, MatrixXr& V) {
  Index n = zhat.size();
  Index m = perm.size();

  for (Index k = 0; k < n; ++k) {
    if (numext::is_exactly_zero(zhat(k))) {
      U.col(k) = VectorType::Unit(n + 1, k);
      if (m_compV) V.col(k) = VectorType::Unit(n, k);
    } else {
      U.col(k).setZero();
      for (Index l = 0; l < m; ++l) {
        Index i = perm(l);
        U(i, k) = zhat(i) / (((diag(i) - shifts(k)) - mus(k))) / ((diag(i) + singVals[k]));
      }
      U(n, k) = Literal(0);
      U.col(k).normalize();

      if (m_compV) {
        V.col(k).setZero();
        for (Index l = 1; l < m; ++l) {
          Index i = perm(l);
          V(i, k) = diag(i) * zhat(i) / (((diag(i) - shifts(k)) - mus(k))) / ((diag(i) + singVals[k]));
        }
        V(0, k) = Literal(-1);
        V.col(k).normalize();
      }
    }
  }
  U.col(n) = VectorType::Unit(n + 1, n);
}

// page 12_13
// i >= 1, di almost null and zi non null.
// We use a rotation to zero out zi applied to the left of M, and set di = 0.
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::deflation43(Index firstCol, Index shift, Index i, Index size) {
  using std::abs;
  using std::sqrt;
  Index start = firstCol + shift;
  RealScalar c = m_computed(start, start);
  RealScalar s = m_computed(start + i, start);
  RealScalar r = numext::hypot(c, s);
  if (numext::is_exactly_zero(r)) {
    m_computed(start + i, start + i) = Literal(0);
    return;
  }
  m_computed(start, start) = r;
  m_computed(start + i, start) = Literal(0);
  m_computed(start + i, start + i) = Literal(0);

  JacobiRotation<RealScalar> J(c / r, -s / r);
  if (m_compU)
    m_naiveU.middleRows(firstCol, size + 1).applyOnTheRight(firstCol, firstCol + i, J);
  else
    m_naiveU.applyOnTheRight(firstCol, firstCol + i, J);
}  // end deflation 43

// page 13
// i,j >= 1, i > j, and |di - dj| < epsilon * norm2(M)
// We apply two rotations to have zi = 0, and dj = di.
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::deflation44(Index firstColu, Index firstColm, Index firstRowW, Index firstColW, Index i,
                                           Index j, Index size) {
  using std::abs;
  using std::sqrt;

  RealScalar s = m_computed(firstColm + i, firstColm);
  RealScalar c = m_computed(firstColm + j, firstColm);
  RealScalar r = numext::hypot(c, s);
  if (numext::is_exactly_zero(r)) {
    m_computed(firstColm + j, firstColm + j) = m_computed(firstColm + i, firstColm + i);
    return;
  }
  c /= r;
  s /= r;
  m_computed(firstColm + j, firstColm) = r;
  m_computed(firstColm + j, firstColm + j) = m_computed(firstColm + i, firstColm + i);
  m_computed(firstColm + i, firstColm) = Literal(0);

  JacobiRotation<RealScalar> J(c, -s);
  if (m_compU)
    m_naiveU.middleRows(firstColu, size + 1).applyOnTheRight(firstColu + j, firstColu + i, J);
  else
    m_naiveU.applyOnTheRight(firstColu + j, firstColu + i, J);
  if (m_compV) m_naiveV.middleRows(firstRowW, size).applyOnTheRight(firstColW + j, firstColW + i, J);
}  // end deflation 44

// acts on block from (firstCol+shift, firstCol+shift) to (lastCol+shift, lastCol+shift) [inclusive]
template <typename RealScalar_>
void bdcsvd_impl<RealScalar_>::deflation(Index firstCol, Index lastCol, Index k, Index firstRowW, Index firstColW,
                                         Index shift) {
  using std::abs;
  using std::sqrt;
  const Index length = lastCol + 1 - firstCol;

  Block<MatrixXr, Dynamic, 1> col0(m_computed, firstCol + shift, firstCol + shift, length, 1);
  Diagonal<MatrixXr> fulldiag(m_computed);
  VectorBlock<Diagonal<MatrixXr>, Dynamic> diag(fulldiag, firstCol + shift, length);

  const RealScalar considerZero = (std::numeric_limits<RealScalar>::min)();
  RealScalar maxDiag = diag.tail((std::max)(Index(1), length - 1)).cwiseAbs().maxCoeff();
  RealScalar epsilon_strict = numext::maxi<RealScalar>(considerZero, NumTraits<RealScalar>::epsilon() * maxDiag);
  RealScalar epsilon_coarse =
      Literal(8) * NumTraits<RealScalar>::epsilon() * numext::maxi<RealScalar>(col0.cwiseAbs().maxCoeff(), maxDiag);

  // condition 4.1
  if (diag(0) < epsilon_coarse) {
    diag(0) = epsilon_coarse;
  }

  // condition 4.2
  for (Index i = 1; i < length; ++i)
    if (abs(col0(i)) < epsilon_strict) {
      col0(i) = Literal(0);
    }

  // condition 4.3
  for (Index i = 1; i < length; i++)
    if (diag(i) < epsilon_coarse) {
      deflation43(firstCol, shift, i, length);
    }

  {
    // Check for total deflation:
    // If we have a total deflation, then we have to consider col0(0)==diag(0) as a singular value during sorting.
    const bool total_deflation = (col0.tail(length - 1).array().abs() < considerZero).all();

    // Sort the diagonal entries, since diag(1:k-1) and diag(k:length) are already sorted, let's do a sorted merge.
    // First, compute the respective permutation.
    Index* permutation = m_workspaceI.data();
    {
      permutation[0] = 0;
      Index p = 1;

      // Move deflated diagonal entries at the end.
      for (Index i = 1; i < length; ++i)
        if (diag(i) < considerZero) permutation[p++] = i;

      Index i = 1, j = k + 1;
      for (; p < length; ++p) {
        if (i > k)
          permutation[p] = j++;
        else if (j >= length)
          permutation[p] = i++;
        else if (diag(i) < diag(j))
          permutation[p] = j++;
        else
          permutation[p] = i++;
      }
    }

    // If we have a total deflation, then we have to insert diag(0) at the right place
    if (total_deflation) {
      for (Index i = 1; i < length; ++i) {
        Index pi = permutation[i];
        if (diag(pi) < considerZero || diag(0) < diag(pi))
          permutation[i - 1] = permutation[i];
        else {
          permutation[i - 1] = 0;
          break;
        }
      }
    }

    // Current index of each col, and current column of each index
    Index* realInd = m_workspaceI.data() + length;
    Index* realCol = m_workspaceI.data() + 2 * length;

    for (int pos = 0; pos < length; pos++) {
      realCol[pos] = pos;
      realInd[pos] = pos;
    }

    for (Index i = total_deflation ? 0 : 1; i < length; i++) {
      const Index pi = permutation[length - (total_deflation ? i + 1 : i)];
      const Index J = realCol[pi];

      using std::swap;
      // swap diagonal and first column entries:
      swap(diag(i), diag(J));
      if (i != 0 && J != 0) swap(col0(i), col0(J));

      // change columns
      if (m_compU)
        m_naiveU.col(firstCol + i)
            .segment(firstCol, length + 1)
            .swap(m_naiveU.col(firstCol + J).segment(firstCol, length + 1));
      else
        m_naiveU.col(firstCol + i).segment(0, 2).swap(m_naiveU.col(firstCol + J).segment(0, 2));
      if (m_compV)
        m_naiveV.col(firstColW + i)
            .segment(firstRowW, length)
            .swap(m_naiveV.col(firstColW + J).segment(firstRowW, length));

      // update real pos
      const Index realI = realInd[i];
      realCol[realI] = J;
      realCol[pi] = i;
      realInd[J] = realI;
      realInd[i] = pi;
    }
  }

  // condition 4.4
  {
    Index i = length - 1;
    // Find last non-deflated entry.
    while (i > 0 && (diag(i) < considerZero || abs(col0(i)) < considerZero)) --i;

    for (; i > 1; --i)
      if ((diag(i) - diag(i - 1)) < epsilon_coarse) {
        deflation44(firstCol, firstCol + shift, firstRowW, firstColW, i, i - 1, length);
      }
  }

}  // end deflation

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_BDCSVD_IMPL_H
