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

#ifndef EIGEN_BDCSVD_H
#define EIGEN_BDCSVD_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

// Internal D&C implementation, templated only on RealScalar.
#include "BDCSVDImpl.h"

namespace Eigen {

template <typename MatrixType_, int Options>
class BDCSVD;

namespace internal {

template <typename MatrixType_, int Options>
struct traits<BDCSVD<MatrixType_, Options> > : svd_traits<MatrixType_, Options> {
  typedef MatrixType_ MatrixType;
};

}  // end namespace internal

/** \ingroup SVD_Module
 *
 *
 * \class BDCSVD
 *
 * \brief class Bidiagonal Divide and Conquer SVD
 *
 * \tparam MatrixType_ the type of the matrix of which we are computing the SVD decomposition
 *
 * \tparam Options_ this optional parameter allows one to specify options for computing unitaries \a U and \a V.
 *                  Possible values are #ComputeThinU, #ComputeThinV, #ComputeFullU, #ComputeFullV, and
 *                  #DisableQRDecomposition. It is not possible to request both the thin and full version of \a U or
 *                  \a V. By default, unitaries are not computed. BDCSVD uses R-Bidiagonalization to improve
 *                  performance on tall and wide matrices. For backwards compatility, the option
 *                  #DisableQRDecomposition can be used to disable this optimization.
 *
 * This class first reduces the input matrix to bi-diagonal form using class UpperBidiagonalization,
 * and then performs a divide-and-conquer diagonalization. Small blocks are diagonalized using class JacobiSVD.
 * You can control the switching size with the setSwitchSize() method, default is 16.
 * For small matrice (<16), it is thus preferable to directly use JacobiSVD. For larger ones, BDCSVD is highly
 * recommended and can several order of magnitude faster.
 *
 * \warning this algorithm is unlikely to provide accurate result when compiled with unsafe math optimizations.
 * For instance, this concerns Intel's compiler (ICC), which performs such optimization by default unless
 * you compile with the \c -fp-model \c precise option. Likewise, the \c -ffast-math option of GCC or clang will
 * significantly degrade the accuracy.
 *
 * \sa class JacobiSVD
 */
template <typename MatrixType_, int Options_>
class BDCSVD : public SVDBase<BDCSVD<MatrixType_, Options_> > {
  typedef SVDBase<BDCSVD> Base;

 public:
  using Base::cols;
  using Base::computeU;
  using Base::computeV;
  using Base::diagSize;
  using Base::rows;

  typedef MatrixType_ MatrixType;
  typedef typename Base::Scalar Scalar;
  typedef typename Base::RealScalar RealScalar;
  typedef typename NumTraits<RealScalar>::Literal Literal;
  typedef typename Base::Index Index;
  enum {
    Options = Options_,
    QRDecomposition = internal::get_qr_preconditioner(Options),
    ComputationOptions = internal::get_computation_options(Options),
    RowsAtCompileTime = Base::RowsAtCompileTime,
    ColsAtCompileTime = Base::ColsAtCompileTime,
    DiagSizeAtCompileTime = Base::DiagSizeAtCompileTime,
    MaxRowsAtCompileTime = Base::MaxRowsAtCompileTime,
    MaxColsAtCompileTime = Base::MaxColsAtCompileTime,
    MaxDiagSizeAtCompileTime = Base::MaxDiagSizeAtCompileTime,
    MatrixOptions = Base::MatrixOptions
  };

  typedef typename Base::MatrixUType MatrixUType;
  typedef typename Base::MatrixVType MatrixVType;
  typedef typename Base::SingularValuesType SingularValuesType;

  typedef Matrix<Scalar, Dynamic, Dynamic, ColMajor> MatrixX;
  typedef Matrix<RealScalar, Dynamic, Dynamic, ColMajor> MatrixXr;
  typedef Matrix<RealScalar, Dynamic, 1> VectorType;
  typedef Array<RealScalar, Dynamic, 1> ArrayXr;
  typedef Array<Index, 1, Dynamic> ArrayXi;
  typedef Ref<ArrayXr> ArrayRef;
  typedef Ref<ArrayXi> IndicesRef;

  /** \brief Default Constructor.
   *
   * The default constructor is useful in cases in which the user intends to
   * perform decompositions via BDCSVD::compute(const MatrixType&).
   */
  BDCSVD() : m_isTranspose(false), m_numIters(0) {}

  /** \brief Default Constructor with memory preallocation
   *
   * Like the default constructor but with preallocation of the internal data
   * according to the specified problem size and \a Options template parameter.
   * \sa BDCSVD()
   */
  BDCSVD(Index rows, Index cols) : m_numIters(0) { allocate(rows, cols, internal::get_computation_options(Options)); }

  /** \brief Default Constructor with memory preallocation
   *
   * Like the default constructor but with preallocation of the internal data
   * according to the specified problem size and the \a computationOptions.
   *
   * One \b cannot request unitaries using both the \a Options template parameter
   * and the constructor. If possible, prefer using the \a Options template parameter.
   *
   * \param rows number of rows for the input matrix
   * \param cols number of columns for the input matrix
   * \param computationOptions specification for computing Thin/Full unitaries U/V
   * \sa BDCSVD()
   *
   * \deprecated Will be removed in the next major Eigen version. Options should
   * be specified in the \a Options template parameter.
   */
  EIGEN_DEPRECATED_WITH_REASON("Options should be specified using the class template parameter.")
  BDCSVD(Index rows, Index cols, unsigned int computationOptions) : m_numIters(0) {
    internal::check_svd_options_assertions<MatrixType, Options>(computationOptions, rows, cols);
    allocate(rows, cols, computationOptions);
  }

  /** \brief Constructor performing the decomposition of given matrix, using the custom options specified
   *         with the \a Options template parameter.
   *
   * \param matrix the matrix to decompose
   */
  template <typename Derived>
  BDCSVD(const MatrixBase<Derived>& matrix) : m_numIters(0) {
    compute_impl(matrix, internal::get_computation_options(Options));
  }

  /** \brief Constructor performing the SVD of an upper bidiagonal matrix given its diagonal and superdiagonal.
   *
   * This skips the bidiagonalization step and directly runs the divide-and-conquer algorithm.
   * The input vectors must be real-valued. For an n x n bidiagonal matrix, \a diagonal has n entries
   * and \a superdiagonal has n-1 entries.
   *
   * \param diagonal the diagonal entries of the bidiagonal matrix
   * \param superdiagonal the superdiagonal entries of the bidiagonal matrix
   */
  template <typename DerivedD, typename DerivedE>
  BDCSVD(const MatrixBase<DerivedD>& diagonal, const MatrixBase<DerivedE>& superdiagonal) : m_numIters(0) {
    compute_bidiagonal_impl(diagonal, superdiagonal, internal::get_computation_options(Options));
  }

  /** \brief Constructor performing the decomposition of given matrix using specified options
   *         for computing unitaries.
   *
   *  One \b cannot request unitaries using both the \a Options template parameter
   *  and the constructor. If possible, prefer using the \a Options template parameter.
   *
   * \param matrix the matrix to decompose
   * \param computationOptions specification for computing Thin/Full unitaries U/V
   *
   * \deprecated Will be removed in the next major Eigen version. Options should
   * be specified in the \a Options template parameter.
   */
  template <typename Derived>
  EIGEN_DEPRECATED_WITH_REASON("Options should be specified using the class template parameter.")
  BDCSVD(const MatrixBase<Derived>& matrix, unsigned int computationOptions) : m_numIters(0) {
    internal::check_svd_options_assertions<MatrixType, Options>(computationOptions, matrix.rows(), matrix.cols());
    compute_impl(matrix, computationOptions);
  }

  ~BDCSVD() {}

  /** \brief Method performing the decomposition of given matrix. Computes Thin/Full unitaries U/V if specified
   *         using the \a Options template parameter or the class constructor.
   *
   * \param matrix the matrix to decompose
   */
  template <typename Derived>
  BDCSVD& compute(const MatrixBase<Derived>& matrix) {
    return compute_impl(matrix, m_computationOptions);
  }

  /** \brief Method performing the decomposition of given matrix, as specified by
   *         the `computationOptions` parameter.
   *
   * \param matrix the matrix to decompose
   * \param computationOptions specify whether to compute Thin/Full unitaries U/V
   *
   * \deprecated Will be removed in the next major Eigen version. Options should
   * be specified in the \a Options template parameter.
   */
  template <typename Derived>
  EIGEN_DEPRECATED_WITH_REASON("Options should be specified using the class template parameter.")
  BDCSVD& compute(const MatrixBase<Derived>& matrix, unsigned int computationOptions) {
    internal::check_svd_options_assertions<MatrixType, Options>(computationOptions, matrix.rows(), matrix.cols());
    return compute_impl(matrix, computationOptions);
  }

  /** \brief Compute the SVD of an upper bidiagonal matrix given its diagonal and superdiagonal.
   *
   * This skips the bidiagonalization step and directly runs the divide-and-conquer algorithm.
   * The input vectors must be real-valued. For an n x n bidiagonal matrix, \a diagonal has n entries
   * and \a superdiagonal has n-1 entries.
   *
   * \param diagonal the diagonal entries of the bidiagonal matrix
   * \param superdiagonal the superdiagonal entries of the bidiagonal matrix
   */
  template <typename DerivedD, typename DerivedE>
  BDCSVD& compute(const MatrixBase<DerivedD>& diagonal, const MatrixBase<DerivedE>& superdiagonal) {
    return compute_bidiagonal_impl(diagonal, superdiagonal, m_computationOptions);
  }

  void setSwitchSize(int s) {
    eigen_assert(s >= 3 && "BDCSVD the size of the algo switch has to be at least 3.");
    m_impl.setAlgoSwap(s);
  }

 private:
  template <typename Derived>
  BDCSVD& compute_impl(const MatrixBase<Derived>& matrix, unsigned int computationOptions);
  template <typename DerivedD, typename DerivedE>
  BDCSVD& compute_bidiagonal_impl(const MatrixBase<DerivedD>& diagonal, const MatrixBase<DerivedE>& superdiagonal,
                                  unsigned int computationOptions);
  template <typename HouseholderU, typename HouseholderV, typename NaiveU, typename NaiveV>
  void copyUV(const HouseholderU& householderU, const HouseholderV& householderV, const NaiveU& naiveU,
              const NaiveV& naivev);

 protected:
  void allocate(Index rows, Index cols, unsigned int computationOptions);
  internal::bdcsvd_impl<RealScalar> m_impl;
  bool m_isTranspose, m_useQrDecomp;
  JacobiSVD<MatrixX> smallSvd;
  HouseholderQR<MatrixX> qrDecomp;
  internal::UpperBidiagonalization<MatrixX> bid;
  MatrixX copyWorkspace;
  MatrixX reducedTriangle;
  // Reused workspace for HouseholderSequence::applyThisOnTheLeft in copyUV().
  // Without this, each apply allocates a fresh row vector.
  Matrix<Scalar, 1, Dynamic, RowMajor> m_householderWorkspace;

  using Base::m_computationOptions;
  using Base::m_computeThinU;
  using Base::m_computeThinV;
  using Base::m_info;
  using Base::m_isInitialized;
  using Base::m_matrixU;
  using Base::m_matrixV;
  using Base::m_nonzeroSingularValues;
  using Base::m_singularValues;

 public:
  int m_numIters;
};  // end class BDCSVD

// Method to allocate and initialize matrix and attributes
template <typename MatrixType, int Options>
void BDCSVD<MatrixType, Options>::allocate(Index rows, Index cols, unsigned int computationOptions) {
  if (Base::allocate(rows, cols, computationOptions)) return;

  if (cols < m_impl.algoSwap())
    smallSvd.allocate(rows, cols, Options == 0 ? computationOptions : internal::get_computation_options(Options));

  m_isTranspose = (cols > rows);

  bool compU = computeV();
  bool compV = computeU();
  if (m_isTranspose) std::swap(compU, compV);

  m_impl.allocate(diagSize(), compU, compV);

  // kMinAspectRatio is the crossover point that determines if we perform R-Bidiagonalization
  // or bidiagonalize the input matrix directly.
  // It is based off of LAPACK's dgesdd routine, which uses 11.0/6.0
  // we use a larger scalar to prevent a regression for relatively square matrices.
  constexpr Index kMinAspectRatio = 4;
  constexpr bool disableQrDecomp = static_cast<int>(QRDecomposition) == static_cast<int>(DisableQRDecomposition);
  m_useQrDecomp = !disableQrDecomp && ((rows / kMinAspectRatio > cols) || (cols / kMinAspectRatio > rows));
  if (m_useQrDecomp) {
    qrDecomp = HouseholderQR<MatrixX>((std::max)(rows, cols), (std::min)(rows, cols));
    reducedTriangle = MatrixX(diagSize(), diagSize());
  }

  copyWorkspace = MatrixX(m_isTranspose ? cols : rows, m_isTranspose ? rows : cols);
  bid = internal::UpperBidiagonalization<MatrixX>(m_useQrDecomp ? diagSize() : copyWorkspace.rows(),
                                                  m_useQrDecomp ? diagSize() : copyWorkspace.cols());
}  // end allocate

template <typename MatrixType, int Options>
template <typename Derived>
EIGEN_DONT_INLINE BDCSVD<MatrixType, Options>& BDCSVD<MatrixType, Options>::compute_impl(
    const MatrixBase<Derived>& matrix, unsigned int computationOptions) {
  EIGEN_STATIC_ASSERT_SAME_MATRIX_SIZE(Derived, MatrixType);
  EIGEN_STATIC_ASSERT((std::is_same<typename Derived::Scalar, typename MatrixType::Scalar>::value),
                      Input matrix must have the same Scalar type as the BDCSVD object.);

  using std::abs;

  allocate(matrix.rows(), matrix.cols(), computationOptions);

  const RealScalar considerZero = (std::numeric_limits<RealScalar>::min)();

  //**** step -1 - If the problem is too small, directly falls back to JacobiSVD and return
  if (matrix.cols() < m_impl.algoSwap()) {
    smallSvd.compute(matrix);
    m_isInitialized = true;
    m_info = smallSvd.info();
    if (m_info == Success || m_info == NoConvergence) {
      if (computeU()) m_matrixU = smallSvd.matrixU();
      if (computeV()) m_matrixV = smallSvd.matrixV();
      m_singularValues = smallSvd.singularValues();
      m_nonzeroSingularValues = smallSvd.nonzeroSingularValues();
    }
    return *this;
  }

  //**** step 0 - Copy the input matrix and apply scaling to reduce over/under-flows
  RealScalar scale = matrix.cwiseAbs().template maxCoeff<PropagateNaN>();
  if (!(numext::isfinite)(scale)) {
    m_isInitialized = true;
    m_info = InvalidInput;
    return *this;
  }

  if (numext::is_exactly_zero(scale)) scale = Literal(1);

  if (m_isTranspose)
    copyWorkspace = matrix.adjoint() / scale;
  else
    copyWorkspace = matrix / scale;

  //**** step 1 - Bidiagonalization.
  // If the problem is sufficiently rectangular, we perform R-Bidiagonalization: compute A = Q(R/0)
  // and then bidiagonalize R. Otherwise, if the problem is relatively square, we
  // bidiagonalize the input matrix directly.
  if (m_useQrDecomp) {
    qrDecomp.compute(copyWorkspace);
    reducedTriangle = qrDecomp.matrixQR().topRows(diagSize());
    reducedTriangle.template triangularView<StrictlyLower>().setZero();
    bid.compute(reducedTriangle);
  } else {
    bid.compute(copyWorkspace);
  }

  //**** step 2 - Divide & Conquer
  m_impl.naiveU().setZero();
  m_impl.naiveV().setZero();
  // The transposed bidiagonal has only the main diagonal and one sub-diagonal;
  // fill those directly instead of materializing a dense temporary.
  // Note: BandMatrix::diagonal<N>() const has a latent type bug (returns
  // Block<CoefficientsType, ...> instead of Block<const CoefficientsType, ...>),
  // so use the index-based overload which is correctly const-qualified.
  m_impl.computed().setZero();
  m_impl.computed().topRows(diagSize()).diagonal() = bid.bidiagonal().diagonal();
  m_impl.computed().topRows(diagSize()).template diagonal<-1>() = bid.bidiagonal().diagonal(1);
  m_impl.divide(0, diagSize() - 1, 0, 0, 0);
  m_info = m_impl.info();
  m_numIters = m_impl.numIters();
  if (m_info != Success && m_info != NoConvergence) {
    m_isInitialized = true;
    return *this;
  }

  //**** step 3 - Copy singular values and vectors
  for (int i = 0; i < diagSize(); i++) {
    RealScalar a = abs(m_impl.computed().coeff(i, i));
    m_singularValues.coeffRef(i) = a * scale;
    if (a < considerZero) {
      m_nonzeroSingularValues = i;
      m_singularValues.tail(diagSize() - i - 1).setZero();
      break;
    } else if (i == diagSize() - 1) {
      m_nonzeroSingularValues = i + 1;
      break;
    }
  }

  //**** step 4 - Finalize unitaries U and V
  if (m_isTranspose)
    copyUV(bid.householderV(), bid.householderU(), m_impl.naiveV(), m_impl.naiveU());
  else
    copyUV(bid.householderU(), bid.householderV(), m_impl.naiveU(), m_impl.naiveV());

  if (m_useQrDecomp) {
    if (m_isTranspose && computeV())
      m_matrixV.applyOnTheLeft(qrDecomp.householderQ());
    else if (!m_isTranspose && computeU())
      m_matrixU.applyOnTheLeft(qrDecomp.householderQ());
  }

  m_isInitialized = true;
  return *this;
}  // end compute

template <typename MatrixType, int Options>
template <typename HouseholderU, typename HouseholderV, typename NaiveU, typename NaiveV>
EIGEN_DONT_INLINE void BDCSVD<MatrixType, Options>::copyUV(const HouseholderU& householderU,
                                                           const HouseholderV& householderV, const NaiveU& naiveU,
                                                           const NaiveV& naiveV) {
  // Note exchange of U and V: m_matrixU is set from m_naiveV and vice versa.
  // Cast the diagSize x diagSize block (rather than the full naive matrix) to avoid materializing
  // a full-size temporary when Scalar != RealScalar; reuse m_householderWorkspace across the two
  // applyThisOnTheLeft calls so each does not allocate a fresh row vector.
  if (computeU()) {
    Index Ucols = m_computeThinU ? diagSize() : rows();
    m_matrixU = MatrixX::Identity(rows(), Ucols);
    m_matrixU.topLeftCorner(diagSize(), diagSize()) =
        naiveV.topLeftCorner(diagSize(), diagSize()).template cast<Scalar>();
    if (m_useQrDecomp) {
      auto sub = m_matrixU.topLeftCorner(householderU.cols(), diagSize());
      householderU.applyThisOnTheLeft(sub, m_householderWorkspace);
    } else {
      householderU.applyThisOnTheLeft(m_matrixU, m_householderWorkspace);
    }
  }
  if (computeV()) {
    Index Vcols = m_computeThinV ? diagSize() : cols();
    m_matrixV = MatrixX::Identity(cols(), Vcols);
    m_matrixV.topLeftCorner(diagSize(), diagSize()) =
        naiveU.topLeftCorner(diagSize(), diagSize()).template cast<Scalar>();
    if (m_useQrDecomp) {
      auto sub = m_matrixV.topLeftCorner(householderV.cols(), diagSize());
      householderV.applyThisOnTheLeft(sub, m_householderWorkspace);
    } else {
      householderV.applyThisOnTheLeft(m_matrixV, m_householderWorkspace);
    }
  }
}

template <typename MatrixType, int Options>
template <typename DerivedD, typename DerivedE>
EIGEN_DONT_INLINE BDCSVD<MatrixType, Options>& BDCSVD<MatrixType, Options>::compute_bidiagonal_impl(
    const MatrixBase<DerivedD>& diagonal, const MatrixBase<DerivedE>& superdiagonal, unsigned int computationOptions) {
  EIGEN_STATIC_ASSERT(DerivedD::IsVectorAtCompileTime, THIS_METHOD_IS_ONLY_FOR_VECTORS);
  EIGEN_STATIC_ASSERT(DerivedE::IsVectorAtCompileTime, THIS_METHOD_IS_ONLY_FOR_VECTORS);
  EIGEN_STATIC_ASSERT((NumTraits<typename DerivedD::Scalar>::IsComplex == 0),
                      THIS_FUNCTION_IS_NOT_FOR_COMPLEX_VALUED_MATRICES);
  EIGEN_STATIC_ASSERT((NumTraits<typename DerivedE::Scalar>::IsComplex == 0),
                      THIS_FUNCTION_IS_NOT_FOR_COMPLEX_VALUED_MATRICES);

  using std::abs;
  const Index n = diagonal.size();
  eigen_assert((n == 0 || superdiagonal.size() == n - 1) && "superdiagonal must have size diagonal.size() - 1");

  // For a bidiagonal matrix, rows == cols == n.
  allocate(n, n, computationOptions);

  if (n == 0) {
    m_isInitialized = true;
    m_info = Success;
    m_nonzeroSingularValues = 0;
    return *this;
  }

  // Check for non-finite inputs.
  const RealScalar diagScale = diagonal.cwiseAbs().template maxCoeff<PropagateNaN>();
  const RealScalar superdiagScale = n > 1 ? superdiagonal.cwiseAbs().template maxCoeff<PropagateNaN>() : RealScalar(0);
  RealScalar scale = numext::maxi(diagScale, superdiagScale);
  if (!(numext::isfinite)(scale)) {
    m_isInitialized = true;
    m_info = InvalidInput;
    return *this;
  }

  const RealScalar considerZero = (std::numeric_limits<RealScalar>::min)();
  if (numext::is_exactly_zero(scale)) scale = Literal(1);

  //**** Small problem: build dense bidiagonal and delegate to JacobiSVD.
  if (n < m_impl.algoSwap()) {
    // Build the dense upper bidiagonal matrix.
    MatrixX B = MatrixX::Zero(n, n);
    B.diagonal() = diagonal.template cast<Scalar>() / Scalar(scale);
    if (n > 1) B.diagonal(1) = superdiagonal.template cast<Scalar>() / Scalar(scale);
    smallSvd.compute(B);
    m_isInitialized = true;
    m_info = smallSvd.info();
    if (m_info == Success || m_info == NoConvergence) {
      m_singularValues = smallSvd.singularValues() * scale;
      m_nonzeroSingularValues = smallSvd.nonzeroSingularValues();
      if (computeU()) m_matrixU = smallSvd.matrixU();
      if (computeV()) m_matrixV = smallSvd.matrixV();
    }
    return *this;
  }

  //**** Fill m_computed with transposed bidiagonal format.
  // D&C operates on B^T: m_computed(i,i) = d_i, m_computed(i+1,i) = e_i.
  m_impl.naiveU().setZero();
  m_impl.naiveV().setZero();
  m_impl.computed().setZero();
  for (Index i = 0; i < n; ++i) {
    m_impl.computed()(i, i) = RealScalar(diagonal.coeff(i)) / scale;
  }
  for (Index i = 0; i < n - 1; ++i) {
    m_impl.computed()(i + 1, i) = RealScalar(superdiagonal.coeff(i)) / scale;
  }

  m_isTranspose = false;

  //**** Run D&C.
  m_impl.divide(0, n - 1, 0, 0, 0);
  m_info = m_impl.info();
  m_numIters = m_impl.numIters();
  if (m_info != Success && m_info != NoConvergence) {
    m_isInitialized = true;
    return *this;
  }

  //**** Extract singular values.
  for (int i = 0; i < diagSize(); i++) {
    RealScalar a = abs(m_impl.computed().coeff(i, i));
    m_singularValues.coeffRef(i) = a * scale;
    if (a < considerZero) {
      m_nonzeroSingularValues = i;
      m_singularValues.tail(diagSize() - i - 1).setZero();
      break;
    } else if (i == diagSize() - 1) {
      m_nonzeroSingularValues = i + 1;
      break;
    }
  }

  //**** Copy U and V directly (no Householder to apply).
  // D&C computes B^T = naiveU * S * naiveV^T, so B = naiveV * S * naiveU^T.
  // Thus U_of_B = naiveV, V_of_B = naiveU.
  if (computeU()) {
    Index Ucols = m_computeThinU ? diagSize() : rows();
    m_matrixU = MatrixX::Identity(rows(), Ucols);
    m_matrixU.topLeftCorner(diagSize(), diagSize()) =
        m_impl.naiveV().template cast<Scalar>().topLeftCorner(diagSize(), diagSize());
  }
  if (computeV()) {
    Index Vcols = m_computeThinV ? diagSize() : cols();
    m_matrixV = MatrixX::Identity(cols(), Vcols);
    m_matrixV.topLeftCorner(diagSize(), diagSize()) =
        m_impl.naiveU().template cast<Scalar>().topLeftCorner(diagSize(), diagSize());
  }

  m_isInitialized = true;
  return *this;
}

/** \svd_module
 *
 * \return the singular value decomposition of \c *this computed by Divide & Conquer algorithm
 *
 * \sa class BDCSVD
 */
template <typename Derived>
template <int Options>
BDCSVD<typename MatrixBase<Derived>::PlainObject, Options> MatrixBase<Derived>::bdcSvd() const {
  return BDCSVD<PlainObject, Options>(*this);
}

/** \svd_module
 *
 * \return the singular value decomposition of \c *this computed by Divide & Conquer algorithm
 *
 * \sa class BDCSVD
 */
template <typename Derived>
template <int Options>
BDCSVD<typename MatrixBase<Derived>::PlainObject, Options> MatrixBase<Derived>::bdcSvd(
    unsigned int computationOptions) const {
  return BDCSVD<PlainObject, Options>(*this, computationOptions);
}

}  // end namespace Eigen

#endif
