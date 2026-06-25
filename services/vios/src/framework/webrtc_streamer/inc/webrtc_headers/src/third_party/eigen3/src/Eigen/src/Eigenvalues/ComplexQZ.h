// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2012 Alexey Korepanov
// Copyright (C) 2025 Ludwig Striet <ludwig.striet@mathematik.uni-freiburg.de>
//
// This Source Code Form is subject to the terms of the
// Mozilla Public License v. 2.0. If a copy of the MPL
// was not distributed with this file, You can obtain one at
// https://mozilla.org/MPL/2.0/.
//
// Derived from: Eigen/src/Eigenvalues/RealQZ.h
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_COMPLEX_QZ_H_
#define EIGEN_COMPLEX_QZ_H_

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

/** \eigenvalues_module \ingroup Eigenvalues_Module
 *
 *
 * \class ComplexQZ
 *
 * \brief Performs a QZ decomposition of a pair of matrices A, B
 *
 * \tparam MatrixType_ the type input type of the matrix.
 *
 * Given to complex square matrices A and B, this class computes the QZ decomposition
 * \f$ A = Q S Z \f$, \f$ B = Q T Z\f$ where Q and Z are unitary matrices and
 * S and T a re upper-triangular matrices. More precisely, Q and Z fulfill
 * \f$ Q Q* = Id\f$ and \f$ Z Z* = Id\f$. The generalized Eigenvalues are then
 * obtained as ratios of corresponding diagonal entries, lambda(i) = S(i,i) / T(i, i).
 *
 * The QZ algorithm was introduced in the seminal work "An Algorithm for
 * Generalized Matrix Eigenvalue Problems" by Moler & Stewart in 1973. The matrix
 * pair S = A, T = B is first transformed to Hessenberg-Triangular form where S is an
 * upper Hessenberg matrix and T is an upper Triangular matrix.
 *
 * This pair is subsequently reduced to the desired form using implicit QZ shifts as
 * described in the original paper. The algorithms to find small entries on the
 * diagonals and subdiagonals are based on the variants in the implementation
 * for Real matrices in the RealQZ class.
 *
 * \sa class RealQZ
 */

namespace Eigen {

template <typename MatrixType_>
class ComplexQZ {
 public:
  using MatrixType = MatrixType_;
  using Scalar = typename MatrixType_::Scalar;
  using RealScalar = typename MatrixType_::RealScalar;

  enum {
    RowsAtCompileTime = MatrixType::RowsAtCompileTime,
    ColsAtCompileTime = MatrixType::ColsAtCompileTime,
    Options = internal::traits<MatrixType>::Options,
    MaxRowsAtCompileTime = MatrixType::MaxRowsAtCompileTime,
    MaxColsAtCompileTime = MatrixType::MaxColsAtCompileTime
  };

  using Vec = Matrix<Scalar, Dynamic, 1>;
  using Vec2 = Matrix<Scalar, 2, 1>;
  using Vec3 = Matrix<Scalar, 3, 1>;
  using Row2 = Matrix<Scalar, 1, 2>;
  using Mat2 = Matrix<Scalar, 2, 2>;

  /** \brief Returns matrix Q in the QZ decomposition.
   *
   * \returns A const reference to the matrix Q.
   */
  const MatrixType& matrixQ() const {
    eigen_assert(m_isInitialized && "ComplexQZ is not initialized.");
    eigen_assert(m_computeQZ && "The matrices Q and Z have not been computed during the QZ decomposition.");
    return m_Q;
  }

  /** \brief Returns matrix Z in the QZ decomposition.
   *
   * \returns A const reference to the matrix Z.
   */
  const MatrixType& matrixZ() const {
    eigen_assert(m_isInitialized && "ComplexQZ is not initialized.");
    eigen_assert(m_computeQZ && "The matrices Q and Z have not been computed during the QZ decomposition.");
    return m_Z;
  }

  /** \brief Returns matrix S in the QZ decomposition.
   *
   * \returns A const reference to the matrix S.
   */
  const MatrixType& matrixS() const {
    eigen_assert(m_isInitialized && "ComplexQZ is not initialized.");
    return m_S;
  }

  /** \brief Returns matrix S in the QZ decomposition.
   *
   * \returns A const reference to the matrix S.
   */
  const MatrixType& matrixT() const {
    eigen_assert(m_isInitialized && "ComplexQZ is not initialized.");
    return m_T;
  }

  /** \brief Constructor
   *
   * \param[in] n size of the matrices whose QZ decomposition we compute
   *
   * This constructor is used when we use the compute(...) method later,
   * especially when we aim to compute the decomposition of two sparse
   * matrices.
   */
  ComplexQZ(Index n, bool computeQZ = true, unsigned int maxIters = 400)
      : m_n(n),
        m_S(n, n),
        m_T(n, n),
        m_Q(computeQZ ? n : (MatrixType::RowsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::RowsAtCompileTime),
            computeQZ ? n : (MatrixType::ColsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::ColsAtCompileTime)),
        m_Z(computeQZ ? n : (MatrixType::RowsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::RowsAtCompileTime),
            computeQZ ? n : (MatrixType::ColsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::ColsAtCompileTime)),
        m_ws(2 * n),
        m_computeQZ(computeQZ),
        m_maxIters(maxIters) {}

  /** \brief Constructor. computes the QZ decomposition of given matrices
   * upon creation
   *
   * \param[in] A         input matrix A
   * \param[in] B         input matrix B
   * \param[in] computeQZ If false, the matrices Q and Z are not computed
   *
   * This constructor calls the compute() method to compute the QZ decomposition.
   * If input matrices are sparse, call the constructor that uses only the
   * size as input the computeSparse(...) method.
   */
  ComplexQZ(const MatrixType& A, const MatrixType& B, bool computeQZ = true, unsigned int maxIters = 400)
      : m_n(A.rows()),
        m_maxIters(maxIters),
        m_computeQZ(computeQZ),
        m_S(A.rows(), A.cols()),
        m_T(A.rows(), A.cols()),
        m_Q(computeQZ ? m_n : (MatrixType::RowsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::RowsAtCompileTime),
            computeQZ ? m_n : (MatrixType::ColsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::ColsAtCompileTime)),
        m_Z(computeQZ ? m_n : (MatrixType::RowsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::RowsAtCompileTime),
            computeQZ ? m_n : (MatrixType::ColsAtCompileTime == Eigen::Dynamic ? 0 : MatrixType::ColsAtCompileTime)),
        m_ws(2 * m_n) {
    compute(A, B, computeQZ);
  }

  /** \brief Compute the QZ decomposition of complex input matrices
   *
   * \param[in] A         Matrix A.
   * \param[in] B         Matrix B.
   * \param[in] computeQZ If false, the matrices Q and Z are not computed.
   */
  void compute(const MatrixType& A, const MatrixType& B, bool computeQZ = true);

  /** \brief Compute the decomposition of sparse complex input matrices.
   * Main difference to the compute(...) method is that it computes a
   * SparseQR decomposition of B
   *
   * \param[in] A         Matrix A.
   * \param[in] B         Matrix B.
   * \param[in] computeQZ If false, the matrices Q and Z are not computed.
   */
  template <typename SparseMatrixType_>
  void computeSparse(const SparseMatrixType_& A, const SparseMatrixType_& B, bool computeQZ = true);

  /** \brief Reports whether the last computation was successful.
   *
   * \returns \c Success if computation was successful, \c NoConvergence otherwise.
   */
  ComputationInfo info() const { return m_info; }

  /** \brief number of performed QZ steps
   */
  unsigned int iterations() const {
    eigen_assert(m_isInitialized && "ComplexQZ is not initialized.");
    return m_global_iter;
  }

 private:
  Index m_n;
  const unsigned int m_maxIters;
  unsigned int m_global_iter;
  bool m_isInitialized;
  bool m_computeQZ;
  ComputationInfo m_info;
  MatrixType m_S, m_T, m_Q, m_Z;
  RealScalar m_normOfT, m_normOfS;
  Vec m_ws;

  // Test if a Scalar is 0 up to a certain tolerance
  static bool is_negligible(const Scalar x, const RealScalar tol = NumTraits<RealScalar>::epsilon()) {
    return numext::abs(x) <= tol;
  }

  void do_QZ_step(Index p, Index q);

  inline Mat2 computeZk2(const Row2& b);

  // This is basically taken from Eigen3::RealQZ
  void hessenbergTriangular(const MatrixType& A, const MatrixType& B);

  // This function can be called when m_Q and m_Z are initialized and m_S, m_T
  // are in hessenberg-triangular form
  void reduceHessenbergTriangular();

  // Sparse variant of the above method.
  template <typename SparseMatrixType_>
  void hessenbergTriangularSparse(const SparseMatrixType_& A, const SparseMatrixType_& B);

  void computeNorms();

  Index findSmallSubdiagEntry(Index l);
  Index findSmallDiagEntry(Index f, Index l);

  void push_down_zero_ST(Index k, Index l);

  void reduceDiagonal2x2block(Index i);
};

template <typename MatrixType_>
void ComplexQZ<MatrixType_>::compute(const MatrixType& A, const MatrixType& B, bool computeQZ) {
  m_computeQZ = computeQZ;
  m_n = A.rows();

  eigen_assert(m_n == A.cols() && "A is not a square matrix");
  eigen_assert(m_n == B.rows() && m_n == B.cols() && "B is not a square matrix or B is not of the same size as A");

  m_isInitialized = true;
  m_global_iter = 0;

  // This will initialize m_Q and m_Z and bring m_S, m_T to hessenberg-triangular form
  hessenbergTriangular(A, B);

  // We assume that we already have that S is upper-Hessenberg and T is
  // upper-triangular. This is what the hessenbergTriangular(...) method does
  reduceHessenbergTriangular();
}

// This is basically taken from Eigen3::RealQZ
template <typename MatrixType_>
void ComplexQZ<MatrixType_>::hessenbergTriangular(const MatrixType& A, const MatrixType& B) {
  // Copy A and B, these will be the matrices on which we operate later
  m_S = A;
  m_T = B;

  // Perform QR decomposition of the matrix Q
  HouseholderQR<MatrixType> qr(m_T);
  m_T = qr.matrixQR();
  m_T.template triangularView<StrictlyLower>().setZero();

  if (m_computeQZ) m_Q = qr.householderQ();

  // overwrite S with Q* x S
  m_S.applyOnTheLeft(qr.householderQ().adjoint());

  if (m_computeQZ) m_Z = MatrixType::Identity(m_n, m_n);

  // reduce S to upper Hessenberg with Givens rotations
  for (Index j = 0; j <= m_n - 3; j++) {
    for (Index i = m_n - 1; i >= j + 2; i--) {
      JacobiRotation<Scalar> G;
      // delete S(i,j)
      if (!numext::is_exactly_zero(m_S.coeff(i, j))) {
        G.makeGivens(m_S.coeff(i - 1, j), m_S.coeff(i, j), &m_S.coeffRef(i - 1, j));
        m_S.coeffRef(i, j) = Scalar(0);
        m_T.rightCols(m_n - i + 1).applyOnTheLeft(i - 1, i, G.adjoint());
        m_S.rightCols(m_n - j - 1).applyOnTheLeft(i - 1, i, G.adjoint());
        // This is what we want to achieve
        if (!is_negligible(m_S(i, j)))
          m_info = ComputationInfo::NumericalIssue;
        else
          m_S(i, j) = Scalar(0);
        // update Q
        if (m_computeQZ) m_Q.applyOnTheRight(i - 1, i, G);
      }

      if (!numext::is_exactly_zero(m_T.coeff(i, i - 1))) {
        // Compute rotation and update matrix T
        G.makeGivens(m_T.coeff(i, i), m_T.coeff(i, i - 1), &m_T.coeffRef(i, i));
        m_T.topRows(i).applyOnTheRight(i - 1, i, G.adjoint());
        m_T.coeffRef(i, i - 1) = Scalar(0);
        // Update matrix S
        m_S.applyOnTheRight(i - 1, i, G.adjoint());
        // update Z
        if (m_computeQZ) m_Z.applyOnTheLeft(i - 1, i, G);
      }
    }
  }
}

template <typename MatrixType>
template <typename SparseMatrixType_>
void ComplexQZ<MatrixType>::hessenbergTriangularSparse(const SparseMatrixType_& A, const SparseMatrixType_& B) {
  m_S = A.toDense();

  SparseQR<SparseMatrix<Scalar, ColMajor>, NaturalOrdering<Index>> sparseQR;

  eigen_assert(B.isCompressed() &&
               "SparseQR requires a sparse matrix in compressed mode."
               "Call .makeCompressed() before passing it to SparseQR");

  // Computing QR decomposition of T...
  sparseQR.setPivotThreshold(RealScalar(0));  // This prevends algorithm from doing pivoting
  sparseQR.compute(B);
  // perform QR decomposition of T, overwrite T with R, save Q
  // HouseholderQR<Mat> qrT(m_T);
  m_T = sparseQR.matrixR();
  m_T.template triangularView<StrictlyLower>().setZero();

  if (m_computeQZ) m_Q = sparseQR.matrixQ();

  // overwrite S with Q* S
  m_S = sparseQR.matrixQ().adjoint() * m_S;

  if (m_computeQZ) m_Z = MatrixType::Identity(m_n, m_n);

  // reduce S to upper Hessenberg with Givens rotations
  for (Index j = 0; j <= m_n - 3; j++) {
    for (Index i = m_n - 1; i >= j + 2; i--) {
      JacobiRotation<Scalar> G;
      // kill S(i,j)
      // if(!numext::is_exactly_zero(_S.coeff(i, j)))
      if (m_S.coeff(i, j) != Scalar(0)) {
        // This is the adapted code
        G.makeGivens(m_S.coeff(i - 1, j), m_S.coeff(i, j), &m_S.coeffRef(i - 1, j));
        m_S.coeffRef(i, j) = Scalar(0);
        m_T.rightCols(m_n - i + 1).applyOnTheLeft(i - 1, i, G.adjoint());
        m_S.rightCols(m_n - j - 1).applyOnTheLeft(i - 1, i, G.adjoint());
        // This is what we want to achieve
        if (!is_negligible(m_S(i, j))) {
          m_info = ComputationInfo::NumericalIssue;
        }
        m_S(i, j) = Scalar(0);
        // update Q
        if (m_computeQZ) m_Q.applyOnTheRight(i - 1, i, G);
      }

      if (!numext::is_exactly_zero(m_T.coeff(i, i - 1))) {
        // Compute rotation and update matrix T
        G.makeGivens(m_T.coeff(i, i), m_T.coeff(i, i - 1), &m_T.coeffRef(i, i));
        m_T.topRows(i).applyOnTheRight(i - 1, i, G.adjoint());
        m_T.coeffRef(i, i - 1) = Scalar(0);
        // Update matrix S
        m_S.applyOnTheRight(i - 1, i, G.adjoint());
        // update Z
        if (m_computeQZ) m_Z.applyOnTheLeft(i - 1, i, G);
      }
    }
  }
}

template <typename MatrixType>
template <typename SparseMatrixType_>
void ComplexQZ<MatrixType>::computeSparse(const SparseMatrixType_& A, const SparseMatrixType_& B, bool computeQZ) {
  m_computeQZ = computeQZ;
  m_n = A.rows();
  eigen_assert(m_n == A.cols() && "A is not a square matrix");
  eigen_assert(m_n == B.rows() && m_n == B.cols() && "B is not a square matrix or B is not of the same size as A");
  m_isInitialized = true;
  m_global_iter = 0;
  hessenbergTriangularSparse(A, B);

  // We assume that we already have that A is upper-Hessenberg and B is
  // upper-triangular. This is what the hessenbergTriangular(...) method does
  reduceHessenbergTriangular();
}

template <typename MatrixType_>
void ComplexQZ<MatrixType_>::reduceHessenbergTriangular() {
  Index l = m_n - 1, f;
  unsigned int local_iter = 0;
  computeNorms();

  while (l > 0 && local_iter < m_maxIters) {
    f = findSmallSubdiagEntry(l);

    // Subdiag entry is small -> can be safely set to 0
    if (f > 0) {
      m_S.coeffRef(f, f - 1) = Scalar(0);
    }
    if (f == l) {  // One root found
      l--;
      local_iter = 0;
    } else if (f == l - 1) {  // Two roots found
      // We found an undesired non-zero at (f+1,f) in S and eliminate it immediately
      reduceDiagonal2x2block(f);
      l -= 2;
      local_iter = 0;
    } else {
      Index z = findSmallDiagEntry(f, l);
      if (z >= f) {
        push_down_zero_ST(z, l);
      } else {
        do_QZ_step(f, m_n - l - 1);
        local_iter++;
        m_global_iter++;
      }
    }
  }

  m_info = (local_iter < m_maxIters) ? Success : NoConvergence;
}

template <typename MatrixType_>
inline typename ComplexQZ<MatrixType_>::Mat2 ComplexQZ<MatrixType_>::computeZk2(const Row2& b) {
  Mat2 S;
  S << Scalar(0), Scalar(1), Scalar(1), Scalar(0);
  Vec2 bprime = S * b.adjoint();
  JacobiRotation<Scalar> J;
  J.makeGivens(bprime(0), bprime(1));
  Mat2 Z = S;
  Z.applyOnTheLeft(0, 1, J);
  Z = S * Z;
  return Z;
}

template <typename MatrixType_>
void ComplexQZ<MatrixType_>::do_QZ_step(Index p, Index q) {
  // This is certainly not the most efficient way of doing this,
  // but a readable one.
  const auto a = [p, this](Index i, Index j) { return m_S(p + i - 1, p + j - 1); };
  const auto b = [p, this](Index i, Index j) { return m_T(p + i - 1, p + j - 1); };
  const Index m = m_n - p - q;  // Size of the inner block
  Scalar x, y, z;
  // We could introduce doing exceptional shifts from time to time.
  Scalar W1 = a(m - 1, m - 1) / b(m - 1, m - 1) - a(1, 1) / b(1, 1), W2 = a(m, m) / b(m, m) - a(1, 1) / b(1, 1),
         W3 = a(m, m - 1) / b(m - 1, m - 1);

  x = (W1 * W2 - a(m - 1, m) / b(m, m) * W3 + W3 * b(m - 1, m) / b(m, m) * a(1, 1) / b(1, 1)) * b(1, 1) / a(2, 1) +
      a(1, 2) / b(2, 2) - a(1, 1) / b(1, 1) * b(1, 2) / b(2, 2);
  y = (a(2, 2) / b(2, 2) - a(1, 1) / b(1, 1)) - a(2, 1) / b(1, 1) * b(1, 2) / b(2, 2) - W1 - W2 +
      W3 * (b(m - 1, m) / b(m, m));
  z = a(3, 2) / b(2, 2);
  Vec3 X;
  const PermutationMatrix<3, 3, int> S3(Vector3i(2, 0, 1));
  for (Index k = p; k < p + m - 2; k++) {
    X << x, y, z;
    Vec2 ess;
    Scalar tau;
    RealScalar beta;
    X.makeHouseholder(ess, tau, beta);
    // The permutations are needed because the makeHouseHolder-method computes
    // the householder transformation in a way that the vector is reflected to
    // (1 0 ... 0) instead of (0 ... 0 1)
    m_S.template middleRows<3>(k)
        .rightCols((std::min)(m_n, m_n - k + 1))
        .applyHouseholderOnTheLeft(ess, tau, m_ws.data());
    m_T.template middleRows<3>(k).rightCols(m_n - k).applyHouseholderOnTheLeft(ess, tau, m_ws.data());
    if (m_computeQZ) m_Q.template middleCols<3>(k).applyHouseholderOnTheRight(ess, std::conj(tau), m_ws.data());

    // Compute Matrix Zk1 s.t. (b(k+2,k) ... b(k+2, k+2)) Zk1 = (0,0,*)
    Vec3 bprime = (m_T.template block<1, 3>(k + 2, k) * S3).adjoint();
    bprime.makeHouseholder(ess, tau, beta);
    m_S.template middleCols<3>(k).topRows((std::min)(k + 4, m_n)).applyOnTheRight(S3);
    m_S.template middleCols<3>(k)
        .topRows((std::min)(k + 4, m_n))
        .applyHouseholderOnTheRight(ess, std::conj(tau), m_ws.data());
    m_S.template middleCols<3>(k).topRows((std::min)(k + 4, m_n)).applyOnTheRight(S3.transpose());
    m_T.template middleCols<3>(k).topRows((std::min)(k + 3, m_n)).applyOnTheRight(S3);
    m_T.template middleCols<3>(k)
        .topRows((std::min)(k + 3, m_n))
        .applyHouseholderOnTheRight(ess, std::conj(tau), m_ws.data());
    m_T.template middleCols<3>(k).topRows((std::min)(k + 3, m_n)).applyOnTheRight(S3.transpose());
    if (m_computeQZ) {
      m_Z.template middleRows<3>(k).applyOnTheLeft(S3.transpose());
      m_Z.template middleRows<3>(k).applyHouseholderOnTheLeft(ess, tau, m_ws.data());
      m_Z.template middleRows<3>(k).applyOnTheLeft(S3);
    }
    Mat2 Zk2 = computeZk2(m_T.template block<1, 2>(k + 1, k));
    m_S.template middleCols<2>(k).topRows((std::min)(k + 4, m_n)).applyOnTheRight(Zk2);
    m_T.template middleCols<2>(k).topRows((std::min)(k + 3, m_n)).applyOnTheRight(Zk2);

    if (m_computeQZ) m_Z.template middleRows<2>(k).applyOnTheLeft(Zk2.adjoint());

    x = m_S(k + 1, k);
    y = m_S(k + 2, k);
    if (k < p + m - 3) {
      z = m_S(k + 3, k);
    }
  };

  // Find a Householdermartirx Qn1 s.t. Qn1 (x y)^T = (* 0)
  JacobiRotation<Scalar> J;
  J.makeGivens(x, y);
  m_S.template middleRows<2>(p + m - 2).applyOnTheLeft(0, 1, J.adjoint());
  m_T.template middleRows<2>(p + m - 2).applyOnTheLeft(0, 1, J.adjoint());

  if (m_computeQZ) m_Q.template middleCols<2>(p + m - 2).applyOnTheRight(0, 1, J);

  // Find a Householdermatrix Zn1 s.t. (b(n,n-1) b(n,n)) * Zn1 = (0 *)
  Mat2 Zn1 = computeZk2(m_T.template block<1, 2>(p + m - 1, p + m - 2));
  m_S.template middleCols<2>(p + m - 2).applyOnTheRight(Zn1);
  m_T.template middleCols<2>(p + m - 2).applyOnTheRight(Zn1);

  if (m_computeQZ) m_Z.template middleRows<2>(p + m - 2).applyOnTheLeft(Zn1.adjoint());
}

/** \internal we found an undesired non-zero at (i+1,i) on the subdiagonal of S and reduce the block */
template <typename MatrixType_>
void ComplexQZ<MatrixType_>::reduceDiagonal2x2block(Index i) {
  // We have found a non-zero on the subdiagonal and want to eliminate it
  Mat2 Si = m_S.template block<2, 2>(i, i), Ti = m_T.template block<2, 2>(i, i);
  if (is_negligible(Ti(0, 0)) && !is_negligible(Ti(1, 1))) {
    Eigen::JacobiRotation<Scalar> G;
    G.makeGivens(m_S(i, i), m_S(i + 1, i));
    m_S.applyOnTheLeft(i, i + 1, G.adjoint());
    m_T.applyOnTheLeft(i, i + 1, G.adjoint());

    if (m_computeQZ) m_Q.applyOnTheRight(i, i + 1, G);

  } else if (!is_negligible(Ti(0, 0)) && is_negligible(Ti(1, 1))) {
    Eigen::JacobiRotation<Scalar> G;
    G.makeGivens(m_S(i + 1, i + 1), m_S(i + 1, i));
    m_S.applyOnTheRight(i, i + 1, G.adjoint());
    m_T.applyOnTheRight(i, i + 1, G.adjoint());
    if (m_computeQZ) m_Z.applyOnTheLeft(i, i + 1, G);
  } else if (!is_negligible(Ti(0, 0)) && !is_negligible((Ti(1, 1)))) {
    Scalar mu = Si(0, 0) / Ti(0, 0);
    Scalar a12_bar = Si(0, 1) - mu * Ti(0, 1);
    Scalar a22_bar = Si(1, 1) - mu * Ti(1, 1);
    Scalar p = Scalar(0.5) * (a22_bar / Ti(1, 1) - Ti(0, 1) * Si(1, 0) / (Ti(0, 0) * Ti(1, 1)));
    RealScalar sgn_p = p.real() >= RealScalar(0) ? RealScalar(1) : RealScalar(-1);
    Scalar q = Si(1, 0) * a12_bar / (Ti(0, 0) * Ti(1, 1));
    Scalar r = p * p + q;
    Scalar lambda = mu + p + sgn_p * numext::sqrt(r);
    Mat2 E = Si - lambda * Ti;
    Index l;
    E.rowwise().norm().maxCoeff(&l);
    JacobiRotation<Scalar> G;
    G.makeGivens(E(l, 1), E(l, 0));
    m_S.applyOnTheRight(i, i + 1, G.adjoint());
    m_T.applyOnTheRight(i, i + 1, G.adjoint());

    if (m_computeQZ) m_Z.applyOnTheLeft(i, i + 1, G);

    Mat2 tildeSi = m_S.template block<2, 2>(i, i), tildeTi = m_T.template block<2, 2>(i, i);
    Mat2 C = tildeSi.norm() < (lambda * tildeTi).norm() ? tildeSi : lambda * tildeTi;
    G.makeGivens(C(0, 0), C(1, 0));
    m_S.applyOnTheLeft(i, i + 1, G.adjoint());
    m_T.applyOnTheLeft(i, i + 1, G.adjoint());

    if (m_computeQZ) m_Q.applyOnTheRight(i, i + 1, G);
  }

  if (!is_negligible(m_S(i + 1, i), m_normOfS * NumTraits<RealScalar>::epsilon())) {
    m_info = ComputationInfo::NumericalIssue;
  } else {
    m_S(i + 1, i) = Scalar(0);
  }
}

/** \internal We found a zero at T(k,k) and want to "push it down" to T(l,l) */
template <typename MatrixType_>
void ComplexQZ<MatrixType_>::push_down_zero_ST(Index k, Index l) {
  // Test Preconditions

  JacobiRotation<Scalar> J;
  for (Index j = k + 1; j <= l; j++) {
    // Create a 0 at _T(j, j)
    J.makeGivens(m_T(j - 1, j), m_T(j, j), &m_T.coeffRef(j - 1, j));
    if (m_n - j - 1 > 0) {
      m_T.rightCols(m_n - j - 1).applyOnTheLeft(j - 1, j, J.adjoint());
    }
    m_T.coeffRef(j, j) = Scalar(0);

    m_S.applyOnTheLeft(j - 1, j, J.adjoint());

    if (m_computeQZ) m_Q.applyOnTheRight(j - 1, j, J);

    // Delete the non-desired non-zero at _S(j, j-2)
    if (j > 1) {
      J.makeGivens(std::conj(m_S(j, j - 1)), std::conj(m_S(j, j - 2)));
      m_S.applyOnTheRight(j - 1, j - 2, J);
      m_S(j, j - 2) = Scalar(0);
      m_T.applyOnTheRight(j - 1, j - 2, J);
      if (m_computeQZ) m_Z.applyOnTheLeft(j - 1, j - 2, J.adjoint());
    }
  }

  // Assume we have the desired structure now, up to the non-zero entry at
  // _S(l, l-1) which we will delete through a last right-jacobi-rotation
  J.makeGivens(std::conj(m_S(l, l)), std::conj(m_S(l, l - 1)));
  m_S.topRows(l + 1).applyOnTheRight(l, l - 1, J);

  if (!is_negligible(m_S(l, l - 1), m_normOfS * NumTraits<Scalar>::epsilon())) {
    m_info = ComputationInfo::NumericalIssue;
  } else {
    m_S(l, l - 1) = Scalar(0);
  }
  m_T.topRows(l + 1).applyOnTheRight(l, l - 1, J);

  if (m_computeQZ) m_Z.applyOnTheLeft(l, l - 1, J.adjoint());

  // Ensure postconditions
  if (!is_negligible(m_T(l, l)) || !is_negligible(m_S(l, l - 1))) {
    m_info = ComputationInfo::NumericalIssue;
  } else {
    m_T(l, l) = Scalar(0);
    m_S(l, l - 1) = Scalar(0);
  }
}

/** \internal Computes vector L1 norms of S and T when in Hessenberg-Triangular form already */
template <typename MatrixType_>
void ComplexQZ<MatrixType_>::computeNorms() {
  const Index size = m_S.cols();
  m_normOfS = RealScalar(0);
  m_normOfT = RealScalar(0);
  for (Index j = 0; j < size; ++j) {
    m_normOfS += m_S.col(j).segment(0, (std::min)(size, j + 2)).cwiseAbs().sum();
    m_normOfT += m_T.row(j).segment(j, size - j).cwiseAbs().sum();
  }
}

/** \internal Look for single small sub-diagonal element S(res, res-1) and return res (or 0). Copied from Eigen3 RealQZ
 * implementation */
template <typename MatrixType_>
inline Index ComplexQZ<MatrixType_>::findSmallSubdiagEntry(Index iu) {
  Index res = iu;
  while (res > 0) {
    RealScalar s = numext::abs(m_S.coeff(res - 1, res - 1)) + numext::abs(m_S.coeff(res, res));
    if (s == Scalar(0)) s = m_normOfS;
    if (numext::abs(m_S.coeff(res, res - 1)) < NumTraits<RealScalar>::epsilon() * s) break;
    res--;
  }
  return res;
}

//
/** \internal Look for single small diagonal element T(res, res) for res between f and l, and return res (or f-1).
 * Copied from Eigen3 RealQZ implementation. */
template <typename MatrixType_>
inline Index ComplexQZ<MatrixType_>::findSmallDiagEntry(Index f, Index l) {
  Index res = l;
  while (res >= f) {
    if (numext::abs(m_T.coeff(res, res)) <= NumTraits<RealScalar>::epsilon() * m_normOfT) break;
    res--;
  }
  return res;
}

}  // namespace Eigen

#endif  // EIGEN_COMPLEX_QZ_H_
