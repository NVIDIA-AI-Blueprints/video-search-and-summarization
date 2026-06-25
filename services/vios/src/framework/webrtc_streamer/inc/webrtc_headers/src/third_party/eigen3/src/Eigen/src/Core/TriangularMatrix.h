// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008 Benoit Jacob <jacob.benoit.1@gmail.com>
// Copyright (C) 2008-2009 Gael Guennebaud <gael.guennebaud@inria.fr>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TRIANGULARMATRIX_H
#define EIGEN_TRIANGULARMATRIX_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <int Side, typename TriangularType, typename Rhs>
struct triangular_solve_retval;

template <typename MatrixType, unsigned int Mode, bool IsSelfAdjoint = (int(Mode) & int(SelfAdjoint)) != 0>
struct triangular_base_return_types {
  typedef internal::remove_all_t<typename MatrixType::ConjugateReturnType> MatrixConjugateReturnType;
  enum {
    TransposeMode = (int(Mode) & int(Upper) ? Lower : 0) | (int(Mode) & int(Lower) ? Upper : 0) |
                    (int(Mode) & int(UnitDiag)) | (int(Mode) & int(ZeroDiag))
  };
  typedef TriangularView<std::add_const_t<MatrixType>, Mode> ConstView;
  typedef TriangularView<const MatrixConjugateReturnType, Mode> ConjugateReturnType;
  typedef TriangularView<const typename MatrixType::AdjointReturnType, TransposeMode> AdjointReturnType;
  typedef TriangularView<typename MatrixType::TransposeReturnType, TransposeMode> TransposeReturnType;
  typedef TriangularView<const typename MatrixType::ConstTransposeReturnType, TransposeMode> ConstTransposeReturnType;
};

template <typename MatrixType, unsigned int Mode>
struct triangular_base_return_types<MatrixType, Mode, true> {
  typedef internal::remove_all_t<typename MatrixType::ConjugateReturnType> MatrixConjugateReturnType;
  enum {
    TriangularPart = int(Mode) & int(Upper | Lower),
    TransposeMode = (int(Mode) & int(Upper) ? Lower : 0) | (int(Mode) & int(Lower) ? Upper : 0)
  };
  typedef SelfAdjointView<std::add_const_t<MatrixType>, TriangularPart> ConstView;
  typedef SelfAdjointView<const MatrixConjugateReturnType, TriangularPart> ConjugateReturnType;
  typedef SelfAdjointView<const typename MatrixType::AdjointReturnType, TransposeMode> AdjointReturnType;
  typedef SelfAdjointView<typename MatrixType::TransposeReturnType, TransposeMode> TransposeReturnType;
  typedef SelfAdjointView<const typename MatrixType::ConstTransposeReturnType, TransposeMode> ConstTransposeReturnType;
};

template <unsigned int Mode, typename Expression>
EIGEN_DEVICE_FUNC inline typename triangular_base_return_types<Expression, Mode>::ConstView
make_triangular_base_cwise_view(const Expression& expression) {
  typedef typename triangular_base_return_types<Expression, Mode>::ConstView ReturnType;
  return ReturnType(expression);
}

}  // namespace internal

/** \class TriangularBase
 * \ingroup Core_Module
 *
 * \brief Base class for triangular part in a matrix
 */
template <typename Derived>
class TriangularBase : public EigenBase<Derived> {
 public:
  enum {
    Mode = internal::traits<Derived>::Mode,
    TransposeMode = (int(Mode) & int(Upper) ? Lower : 0) | (int(Mode) & int(Lower) ? Upper : 0) |
                    (int(Mode) & int(UnitDiag)) | (int(Mode) & int(ZeroDiag)),
    RowsAtCompileTime = internal::traits<Derived>::RowsAtCompileTime,
    ColsAtCompileTime = internal::traits<Derived>::ColsAtCompileTime,
    MaxRowsAtCompileTime = internal::traits<Derived>::MaxRowsAtCompileTime,
    MaxColsAtCompileTime = internal::traits<Derived>::MaxColsAtCompileTime,

    SizeAtCompileTime = (internal::size_of_xpr_at_compile_time<Derived>::value),
    /**< This is equal to the number of coefficients, i.e. the number of
     * rows times the number of columns, or to \a Dynamic if this is not
     * known at compile-time. \sa RowsAtCompileTime, ColsAtCompileTime */

    MaxSizeAtCompileTime = internal::size_at_compile_time(internal::traits<Derived>::MaxRowsAtCompileTime,
                                                          internal::traits<Derived>::MaxColsAtCompileTime)

  };
  typedef typename internal::traits<Derived>::Scalar Scalar;
  typedef typename internal::traits<Derived>::StorageKind StorageKind;
  typedef typename internal::traits<Derived>::StorageIndex StorageIndex;
  typedef typename internal::traits<Derived>::FullMatrixType DenseMatrixType;
  typedef typename internal::traits<Derived>::ExpressionType ExpressionType;
  typedef DenseMatrixType DenseType;
  typedef Derived const& Nested;
  typedef internal::triangular_base_return_types<ExpressionType, Mode> ReturnTypes;
  typedef typename ReturnTypes::ConstView ConstView;
  typedef typename ReturnTypes::ConjugateReturnType ConjugateReturnType;
  typedef typename ReturnTypes::AdjointReturnType AdjointReturnType;
  typedef typename ReturnTypes::TransposeReturnType TransposeReturnType;
  typedef typename ReturnTypes::ConstTransposeReturnType ConstTransposeReturnType;

  EIGEN_DEVICE_FUNC inline TriangularBase() {
    eigen_assert(!((int(Mode) & int(UnitDiag)) && (int(Mode) & int(ZeroDiag))));
  }

  EIGEN_DEVICE_FUNC constexpr Index rows() const noexcept { return derived().nestedExpression().rows(); }
  EIGEN_DEVICE_FUNC constexpr Index cols() const noexcept { return derived().nestedExpression().cols(); }
  EIGEN_DEVICE_FUNC constexpr Index size() const noexcept { return rows() * cols(); }
  EIGEN_DEVICE_FUNC constexpr Index outerStride() const noexcept { return derived().nestedExpression().outerStride(); }
  EIGEN_DEVICE_FUNC constexpr Index innerStride() const noexcept { return derived().nestedExpression().innerStride(); }

  // dummy resize function
  EIGEN_DEVICE_FUNC void resize(Index rows, Index cols) {
    EIGEN_UNUSED_VARIABLE(rows);
    EIGEN_UNUSED_VARIABLE(cols);
    eigen_assert(rows == this->rows() && cols == this->cols());
  }

  /** \sa MatrixBase::fill() */
  EIGEN_DEVICE_FUNC void fill(const Scalar& value) { setConstant(value); }

  /** \sa MatrixBase::setConstant() */
  EIGEN_DEVICE_FUNC Derived& setConstant(const Scalar& value) {
    EIGEN_STATIC_ASSERT_LVALUE(Derived);
    return derived() = DenseMatrixType::Constant(rows(), cols(), value);
  }

  /** \sa MatrixBase::setZero() */
  EIGEN_DEVICE_FUNC Derived& setZero() { return setConstant(Scalar(0)); }

  /** \sa MatrixBase::setOnes() */
  EIGEN_DEVICE_FUNC Derived& setOnes() { return setConstant(Scalar(1)); }

  /** \sa MatrixBase::setRandom() */
  EIGEN_DEVICE_FUNC Derived& setRandom() {
    EIGEN_STATIC_ASSERT_LVALUE(Derived);
    return derived() = DenseMatrixType::Random(rows(), cols());
  }

  /** \sa MatrixBase::setIdentity() */
  EIGEN_DEVICE_FUNC Derived& setIdentity() {
    EIGEN_STATIC_ASSERT_LVALUE(Derived);
    return derived() = DenseMatrixType::Identity(rows(), cols());
  }

  EIGEN_DEVICE_FUNC inline Scalar coeff(Index row, Index col) const {
    check_coordinates_internal(row, col);
    return derived().nestedExpression().coeff(row, col);
  }
  EIGEN_DEVICE_FUNC inline Scalar& coeffRef(Index row, Index col) {
    EIGEN_STATIC_ASSERT_LVALUE(Derived);
    check_coordinates_internal(row, col);
    return derived().nestedExpression().coeffRef(row, col);
  }

  /** \see MatrixBase::copyCoeff(row,col)
   */
  template <typename Other>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void copyCoeff(Index row, Index col, Other& other) {
    coeffRef(row, col) = other.coeff(row, col);
  }

  EIGEN_DEVICE_FUNC inline Scalar operator()(Index row, Index col) const {
    check_coordinates(row, col);
    return coeff(row, col);
  }
  EIGEN_DEVICE_FUNC inline Scalar& operator()(Index row, Index col) {
    check_coordinates(row, col);
    return coeffRef(row, col);
  }

#ifdef EIGEN_MULTIDIMENSIONAL_SUBSCRIPT
  EIGEN_DEVICE_FUNC inline Scalar operator[](Index row, Index col) const { return operator()(row, col); }
  EIGEN_DEVICE_FUNC inline Scalar& operator[](Index row, Index col) { return operator()(row, col); }
#endif

#ifndef EIGEN_PARSED_BY_DOXYGEN
  EIGEN_DEVICE_FUNC constexpr inline const Derived& derived() const noexcept {
    return *static_cast<const Derived*>(this);
  }
  EIGEN_DEVICE_FUNC constexpr inline Derived& derived() noexcept { return *static_cast<Derived*>(this); }
#endif  // not EIGEN_PARSED_BY_DOXYGEN

  template <typename DenseDerived>
  EIGEN_DEVICE_FUNC void evalTo(MatrixBase<DenseDerived>& other) const;
  template <typename DenseDerived>
  EIGEN_DEVICE_FUNC void evalToLazy(MatrixBase<DenseDerived>& other) const;

  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC const Product<Derived, OtherDerived> operator*(const MatrixBase<OtherDerived>& rhs) const {
    return Product<Derived, OtherDerived>(derived(), rhs.derived());
  }

  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC const Product<Derived, OtherDerived> operator*(const DiagonalBase<OtherDerived>& rhs) const {
    return Product<Derived, OtherDerived>(derived(), rhs.derived());
  }

  template <typename OtherDerived,
            std::enable_if_t<int(Mode) == int(OtherDerived::Mode) && (int(Mode) & int(UnitDiag)) == 0, int> = 0>
  EIGEN_DEVICE_FUNC inline auto operator+(const TriangularBase<OtherDerived>& other) const
      -> decltype(internal::make_triangular_base_cwise_view<Mode>(
          std::declval<const ExpressionType&>() +
          std::declval<const typename internal::traits<OtherDerived>::ExpressionType&>())) {
    return internal::make_triangular_base_cwise_view<Mode>(derived().nestedExpression() +
                                                           other.derived().nestedExpression());
  }

  template <typename OtherDerived,
            std::enable_if_t<int(Mode) == int(OtherDerived::Mode) && (int(Mode) & int(UnitDiag)) == 0, int> = 0>
  EIGEN_DEVICE_FUNC inline auto operator-(const TriangularBase<OtherDerived>& other) const
      -> decltype(internal::make_triangular_base_cwise_view<Mode>(
          std::declval<const ExpressionType&>() -
          std::declval<const typename internal::traits<OtherDerived>::ExpressionType&>())) {
    return internal::make_triangular_base_cwise_view<Mode>(derived().nestedExpression() -
                                                           other.derived().nestedExpression());
  }

  template <typename OtherDerived>
  friend EIGEN_DEVICE_FUNC const Product<OtherDerived, Derived> operator*(const MatrixBase<OtherDerived>& lhs,
                                                                          const Derived& rhs) {
    return Product<OtherDerived, Derived>(lhs.derived(), rhs);
  }

  template <typename OtherDerived>
  friend EIGEN_DEVICE_FUNC const Product<OtherDerived, Derived> operator*(const DiagonalBase<OtherDerived>& lhs,
                                                                          const Derived& rhs) {
    return Product<OtherDerived, Derived>(lhs.derived(), rhs);
  }

  /** \sa MatrixBase::conjugate() const */
  EIGEN_DEVICE_FUNC inline const ConjugateReturnType conjugate() const {
    return ConjugateReturnType(derived().nestedExpression().conjugate());
  }

  /** \returns an expression of the complex conjugate of \c *this if Cond==true,
   *           returns \c *this otherwise.
   */
  template <bool Cond>
  EIGEN_DEVICE_FUNC inline std::conditional_t<Cond, ConjugateReturnType, ConstView> conjugateIf() const {
    typedef std::conditional_t<Cond, ConjugateReturnType, ConstView> ReturnType;
    return ReturnType(derived().nestedExpression().template conjugateIf<Cond>());
  }

  /** \sa MatrixBase::adjoint() const */
  EIGEN_DEVICE_FUNC inline const AdjointReturnType adjoint() const {
    return AdjointReturnType(derived().nestedExpression().adjoint());
  }

  /** \sa MatrixBase::transpose() */
  template <class Dummy = int>
  EIGEN_DEVICE_FUNC inline TransposeReturnType transpose(
      std::enable_if_t<Eigen::internal::is_lvalue<ExpressionType>::value, Dummy*> = nullptr) {
    typename ExpressionType::TransposeReturnType tmp(derived().nestedExpression());
    return TransposeReturnType(tmp);
  }

  /** \sa MatrixBase::transpose() const */
  EIGEN_DEVICE_FUNC inline const ConstTransposeReturnType transpose() const {
    return ConstTransposeReturnType(derived().nestedExpression().transpose());
  }

  EIGEN_DEVICE_FUNC DenseMatrixType toDenseMatrix() const {
    DenseMatrixType res(rows(), cols());
    evalToLazy(res);
    return res;
  }

 protected:
  void check_coordinates(Index row, Index col) const {
    EIGEN_ONLY_USED_FOR_DEBUG(row);
    EIGEN_ONLY_USED_FOR_DEBUG(col);
    eigen_assert(col >= 0 && col < cols() && row >= 0 && row < rows());
    const int mode = int(Mode) & ~SelfAdjoint;
    EIGEN_ONLY_USED_FOR_DEBUG(mode);
    eigen_assert((mode == Upper && col >= row) || (mode == Lower && col <= row) ||
                 ((mode == StrictlyUpper || mode == UnitUpper) && col > row) ||
                 ((mode == StrictlyLower || mode == UnitLower) && col < row));
  }

#ifdef EIGEN_INTERNAL_DEBUGGING
  void check_coordinates_internal(Index row, Index col) const { check_coordinates(row, col); }
#else
  void check_coordinates_internal(Index, Index) const {}
#endif
};

/** \class TriangularView
 * \ingroup Core_Module
 *
 * \brief Expression of a triangular part in a matrix
 *
 * \tparam MatrixType the type of the object in which we are taking the triangular part
 * \tparam Mode the kind of triangular matrix expression to construct. Can be #Upper,
 *             #Lower, #UnitUpper, #UnitLower, #StrictlyUpper, or #StrictlyLower.
 *             This is in fact a bit field; it must have either #Upper or #Lower,
 *             and additionally it may have #UnitDiag or #ZeroDiag or neither.
 *
 * This class represents a triangular part of a matrix, not necessarily square. Strictly speaking, for rectangular
 * matrices one should speak of "trapezoid" parts. This class is the return type
 * of MatrixBase::triangularView() and SparseMatrixBase::triangularView(), and most of the time this is the only way it
 * is used.
 *
 * \sa MatrixBase::triangularView()
 */
namespace internal {
template <typename MatrixType, unsigned int Mode_>
struct traits<TriangularView<MatrixType, Mode_>> : traits<MatrixType> {
  typedef typename ref_selector<MatrixType>::non_const_type MatrixTypeNested;
  typedef std::remove_reference_t<MatrixTypeNested> MatrixTypeNestedNonRef;
  typedef remove_all_t<MatrixTypeNested> MatrixTypeNestedCleaned;
  typedef typename MatrixType::PlainObject FullMatrixType;
  typedef MatrixType ExpressionType;
  enum {
    Mode = Mode_,
    FlagsLvalueBit = is_lvalue<MatrixType>::value ? LvalueBit : 0,
    Flags = (MatrixTypeNestedCleaned::Flags & (HereditaryBits | FlagsLvalueBit) &
             (~(PacketAccessBit | DirectAccessBit | LinearAccessBit)))
  };
};
}  // namespace internal

template <typename MatrixType_, unsigned int Mode_, typename StorageKind>
class TriangularViewImpl;

template <typename MatrixType_, unsigned int Mode_>
class TriangularView
    : public TriangularViewImpl<MatrixType_, Mode_, typename internal::traits<MatrixType_>::StorageKind> {
 public:
  typedef TriangularViewImpl<MatrixType_, Mode_, typename internal::traits<MatrixType_>::StorageKind> Base;
  typedef typename internal::traits<TriangularView>::Scalar Scalar;
  typedef MatrixType_ MatrixType;

 protected:
  typedef typename internal::traits<TriangularView>::MatrixTypeNested MatrixTypeNested;
  typedef typename internal::traits<TriangularView>::MatrixTypeNestedNonRef MatrixTypeNestedNonRef;

 public:
  typedef typename internal::traits<TriangularView>::StorageKind StorageKind;
  typedef typename internal::traits<TriangularView>::MatrixTypeNestedCleaned NestedExpression;

  enum {
    Mode = Mode_,
    Flags = internal::traits<TriangularView>::Flags,
    TransposeMode = (int(Mode) & int(Upper) ? Lower : 0) | (int(Mode) & int(Lower) ? Upper : 0) |
                    (int(Mode) & int(UnitDiag)) | (int(Mode) & int(ZeroDiag)),
    IsVectorAtCompileTime = false
  };

  EIGEN_DEVICE_FUNC explicit inline TriangularView(MatrixType& matrix) : m_matrix(matrix) {}

  EIGEN_INHERIT_ASSIGNMENT_OPERATORS(TriangularView)

  /** \returns a const reference to the nested expression */
  EIGEN_DEVICE_FUNC constexpr const NestedExpression& nestedExpression() const noexcept { return m_matrix; }

  /** \returns a reference to the nested expression */
  EIGEN_DEVICE_FUNC constexpr NestedExpression& nestedExpression() noexcept { return m_matrix; }

  template <typename Other>
  EIGEN_DEVICE_FUNC inline Solve<TriangularView, Other> solve(const MatrixBase<Other>& other) const {
    return Solve<TriangularView, Other>(*this, other.derived());
  }

// workaround MSVC ICE
#if EIGEN_COMP_MSVC
  template <int Side, typename Other>
  EIGEN_DEVICE_FUNC inline const internal::triangular_solve_retval<Side, TriangularView, Other> solve(
      const MatrixBase<Other>& other) const {
    return Base::template solve<Side>(other);
  }
#else
  using Base::solve;
#endif

  /** \returns a selfadjoint view of the referenced triangular part which must be either \c #Upper or \c #Lower.
   *
   * This is a shortcut for \code this->nestedExpression().selfadjointView<(*this)::Mode>() \endcode
   * \sa MatrixBase::selfadjointView() */
  EIGEN_DEVICE_FUNC SelfAdjointView<MatrixTypeNestedNonRef, Mode> selfadjointView() {
    EIGEN_STATIC_ASSERT((Mode & (UnitDiag | ZeroDiag)) == 0, PROGRAMMING_ERROR);
    return SelfAdjointView<MatrixTypeNestedNonRef, Mode>(m_matrix);
  }

  /** This is the const version of selfadjointView() */
  EIGEN_DEVICE_FUNC const SelfAdjointView<MatrixTypeNestedNonRef, Mode> selfadjointView() const {
    EIGEN_STATIC_ASSERT((Mode & (UnitDiag | ZeroDiag)) == 0, PROGRAMMING_ERROR);
    return SelfAdjointView<MatrixTypeNestedNonRef, Mode>(m_matrix);
  }

  /** \returns the determinant of the triangular matrix
   * \sa MatrixBase::determinant() */
  EIGEN_DEVICE_FUNC Scalar determinant() const {
    if (Mode & UnitDiag)
      return 1;
    else if (Mode & ZeroDiag)
      return 0;
    else
      return m_matrix.diagonal().prod();
  }

 protected:
  MatrixTypeNested m_matrix;
};

/** \ingroup Core_Module
 *
 * \brief Base class for a triangular part in a \b dense matrix
 *
 * This class is an abstract base class of class TriangularView, and objects of type TriangularViewImpl cannot be
 * instantiated. It extends class TriangularView with additional methods which available for dense expressions only.
 *
 * \sa class TriangularView, MatrixBase::triangularView()
 */
template <typename MatrixType_, unsigned int Mode_>
class TriangularViewImpl<MatrixType_, Mode_, Dense> : public TriangularBase<TriangularView<MatrixType_, Mode_>> {
 public:
  typedef TriangularView<MatrixType_, Mode_> TriangularViewType;

  typedef TriangularBase<TriangularViewType> Base;
  typedef typename internal::traits<TriangularViewType>::Scalar Scalar;

  typedef MatrixType_ MatrixType;
  typedef typename MatrixType::PlainObject DenseMatrixType;
  typedef DenseMatrixType PlainObject;

 public:
  using Base::derived;
  using Base::evalToLazy;
  using Base::operator*;

  typedef typename internal::traits<TriangularViewType>::StorageKind StorageKind;

  enum { Mode = Mode_, Flags = internal::traits<TriangularViewType>::Flags };

  /** \sa MatrixBase::operator+=() */
  template <typename Other>
  EIGEN_DEVICE_FUNC TriangularViewType& operator+=(const DenseBase<Other>& other) {
    internal::call_assignment_no_alias(derived(), other.derived(),
                                       internal::add_assign_op<Scalar, typename Other::Scalar>());
    return derived();
  }
  /** \sa MatrixBase::operator-=() */
  template <typename Other>
  EIGEN_DEVICE_FUNC TriangularViewType& operator-=(const DenseBase<Other>& other) {
    internal::call_assignment_no_alias(derived(), other.derived(),
                                       internal::sub_assign_op<Scalar, typename Other::Scalar>());
    return derived();
  }

  /** \sa MatrixBase::operator*=() */
  EIGEN_DEVICE_FUNC TriangularViewType& operator*=(const typename internal::traits<MatrixType>::Scalar& other) {
    return *this = derived().nestedExpression() * other;
  }
  /** \sa DenseBase::operator/=() */
  EIGEN_DEVICE_FUNC TriangularViewType& operator/=(const typename internal::traits<MatrixType>::Scalar& other) {
    return *this = derived().nestedExpression() / other;
  }

  /** Assigns a triangular matrix to a triangular part of a dense matrix */
  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC TriangularViewType& operator=(const TriangularBase<OtherDerived>& other);

  /** Shortcut for\code *this = other.other.triangularView<(*this)::Mode>() \endcode */
  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC TriangularViewType& operator=(const MatrixBase<OtherDerived>& other);

#ifndef EIGEN_PARSED_BY_DOXYGEN
  EIGEN_DEVICE_FUNC TriangularViewType& operator=(const TriangularViewImpl& other) {
    return *this = other.derived().nestedExpression();
  }

  template <typename OtherDerived>
  /** \deprecated */
  EIGEN_DEPRECATED EIGEN_DEVICE_FUNC void lazyAssign(const TriangularBase<OtherDerived>& other);

  template <typename OtherDerived>
  /** \deprecated */
  EIGEN_DEPRECATED EIGEN_DEVICE_FUNC void lazyAssign(const MatrixBase<OtherDerived>& other);
#endif

  // Scaling a unit triangular view would break its implicit unit diagonal, so only non-unit modes participate.
  template <unsigned int M = Mode, std::enable_if_t<(M & UnitDiag) == 0, int> = 0>
  EIGEN_DEVICE_FUNC const
      TriangularView<const EIGEN_EXPR_BINARYOP_SCALAR_RETURN_TYPE(MatrixType, Scalar, product), Mode>
      operator*(const Scalar& s) const {
    return (derived().nestedExpression() * s).template triangularView<Mode>();
  }

  template <unsigned int M = Mode, std::enable_if_t<(M & UnitDiag) == 0, int> = 0>
  friend EIGEN_DEVICE_FUNC const
      TriangularView<const EIGEN_SCALAR_BINARYOP_EXPR_RETURN_TYPE(Scalar, MatrixType, product), Mode>
      operator*(const Scalar& s, const TriangularViewImpl& mat) {
    return (s * mat.derived().nestedExpression()).template triangularView<Mode>();
  }

  /** \returns the product of the inverse of \c *this with \a other, \a *this being triangular.
   *
   * This function computes the inverse-matrix matrix product inverse(\c *this) * \a other if
   * \a Side==OnTheLeft (the default), or the right-inverse-multiply  \a other * inverse(\c *this) if
   * \a Side==OnTheRight.
   *
   * Note that the template parameter \c Side can be omitted, in which case \c Side==OnTheLeft
   *
   * The matrix \c *this must be triangular and invertible (i.e., all the coefficients of the
   * diagonal must be non zero). It works as a forward (resp. backward) substitution if \c *this
   * is an upper (resp. lower) triangular matrix.
   *
   * Example: \include Triangular_solve.cpp
   * Output: \verbinclude Triangular_solve.out
   *
   * This function returns an expression of the inverse-multiply and can works in-place if it is assigned
   * to the same matrix or vector \a other.
   *
   * For users coming from BLAS, this function (and more specifically solveInPlace()) offer
   * all the operations supported by the \c *TRSV and \c *TRSM BLAS routines.
   *
   * \sa TriangularView::solveInPlace()
   */
  template <int Side, typename Other>
  inline const internal::triangular_solve_retval<Side, TriangularViewType, Other> solve(
      const MatrixBase<Other>& other) const;

  /** "in-place" version of TriangularView::solve() where the result is written in \a other
   *
   * \warning The parameter is only marked 'const' to make the C++ compiler accept a temporary expression here.
   * This function will const_cast it, so constness isn't honored here.
   *
   * Note that the template parameter \c Side can be omitted, in which case \c Side==OnTheLeft
   *
   * See TriangularView:solve() for the details.
   */
  template <int Side, typename OtherDerived>
  EIGEN_DEVICE_FUNC void solveInPlace(const MatrixBase<OtherDerived>& other) const;

  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC void solveInPlace(const MatrixBase<OtherDerived>& other) const {
    return solveInPlace<OnTheLeft>(other);
  }

  /** Swaps the coefficients of the common triangular parts of two matrices */
  template <typename OtherDerived>
  EIGEN_DEVICE_FUNC
#ifdef EIGEN_PARSED_BY_DOXYGEN
      void
      swap(TriangularBase<OtherDerived>& other)
#else
      void
      swap(TriangularBase<OtherDerived> const& other)
#endif
  {
    EIGEN_STATIC_ASSERT_LVALUE(OtherDerived);
    call_assignment(derived(), other.const_cast_derived(), internal::swap_assign_op<Scalar>());
  }

  /** Shortcut for \code (*this).swap(other.triangularView<(*this)::Mode>()) \endcode */
  template <typename OtherDerived>
  /** \deprecated */
  EIGEN_DEPRECATED EIGEN_DEVICE_FUNC void swap(MatrixBase<OtherDerived> const& other) {
    EIGEN_STATIC_ASSERT_LVALUE(OtherDerived);
    call_assignment(derived(), other.const_cast_derived(), internal::swap_assign_op<Scalar>());
  }

  template <typename RhsType, typename DstType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void _solve_impl(const RhsType& rhs, DstType& dst) const {
    if (!internal::is_same_dense(dst, rhs)) dst = rhs;
    this->solveInPlace(dst);
  }

  template <typename ProductType>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TriangularViewType& _assignProduct(const ProductType& prod, const Scalar& alpha,
                                                                           bool beta);

 protected:
  EIGEN_DEFAULT_COPY_CONSTRUCTOR(TriangularViewImpl)
  EIGEN_DEFAULT_EMPTY_CONSTRUCTOR_AND_DESTRUCTOR(TriangularViewImpl)
};

/***************************************************************************
 * Implementation of triangular evaluation/assignment
 ***************************************************************************/

#ifndef EIGEN_PARSED_BY_DOXYGEN
// FIXME should we keep that possibility
template <typename MatrixType, unsigned int Mode>
template <typename OtherDerived>
EIGEN_DEVICE_FUNC inline TriangularView<MatrixType, Mode>& TriangularViewImpl<MatrixType, Mode, Dense>::operator=(
    const MatrixBase<OtherDerived>& other) {
  internal::call_assignment_no_alias(derived(), other.derived(),
                                     internal::assign_op<Scalar, typename OtherDerived::Scalar>());
  return derived();
}

// FIXME should we keep that possibility
template <typename MatrixType, unsigned int Mode>
template <typename OtherDerived>
EIGEN_DEVICE_FUNC void TriangularViewImpl<MatrixType, Mode, Dense>::lazyAssign(const MatrixBase<OtherDerived>& other) {
  internal::call_assignment_no_alias(derived(), other.template triangularView<Mode>());
}

template <typename MatrixType, unsigned int Mode>
template <typename OtherDerived>
EIGEN_DEVICE_FUNC inline TriangularView<MatrixType, Mode>& TriangularViewImpl<MatrixType, Mode, Dense>::operator=(
    const TriangularBase<OtherDerived>& other) {
  eigen_assert(Mode == int(OtherDerived::Mode));
  internal::call_assignment(derived(), other.derived());
  return derived();
}

template <typename MatrixType, unsigned int Mode>
template <typename OtherDerived>
EIGEN_DEVICE_FUNC void TriangularViewImpl<MatrixType, Mode, Dense>::lazyAssign(
    const TriangularBase<OtherDerived>& other) {
  eigen_assert(Mode == int(OtherDerived::Mode));
  internal::call_assignment_no_alias(derived(), other.derived());
}
#endif

/***************************************************************************
 * Implementation of TriangularBase methods
 ***************************************************************************/

/** Assigns a triangular or selfadjoint matrix to a dense matrix.
 * If the matrix is triangular, the opposite part is set to zero. */
template <typename Derived>
template <typename DenseDerived>
EIGEN_DEVICE_FUNC void TriangularBase<Derived>::evalTo(MatrixBase<DenseDerived>& other) const {
  evalToLazy(other.derived());
}

/***************************************************************************
 * Implementation of TriangularView methods
 ***************************************************************************/

/***************************************************************************
 * Implementation of MatrixBase methods
 ***************************************************************************/

/**
 * \returns an expression of a triangular view extracted from the current matrix
 *
 * The parameter \a Mode can have the following values: \c #Upper, \c #StrictlyUpper, \c #UnitUpper,
 * \c #Lower, \c #StrictlyLower, \c #UnitLower.
 *
 * Example: \include MatrixBase_triangularView.cpp
 * Output: \verbinclude MatrixBase_triangularView.out
 *
 * \sa class TriangularView
 */
template <typename Derived>
template <unsigned int Mode>
EIGEN_DEVICE_FUNC constexpr typename MatrixBase<Derived>::template TriangularViewReturnType<Mode>::Type
MatrixBase<Derived>::triangularView() {
  return typename TriangularViewReturnType<Mode>::Type(derived());
}

/** This is the const version of MatrixBase::triangularView() */
template <typename Derived>
template <unsigned int Mode>
EIGEN_DEVICE_FUNC constexpr typename MatrixBase<Derived>::template ConstTriangularViewReturnType<Mode>::Type
MatrixBase<Derived>::triangularView() const {
  return typename ConstTriangularViewReturnType<Mode>::Type(derived());
}

/** \returns true if *this is approximately equal to an upper triangular matrix,
 *          within the precision given by \a prec.
 *
 * \sa isLowerTriangular()
 */
template <typename Derived>
bool MatrixBase<Derived>::isUpperTriangular(const RealScalar& prec) const {
  RealScalar maxAbsOnUpperPart = static_cast<RealScalar>(-1);
  for (Index j = 0; j < cols(); ++j) {
    Index maxi = numext::mini(j, rows() - 1);
    for (Index i = 0; i <= maxi; ++i) {
      RealScalar absValue = numext::abs(coeff(i, j));
      if (absValue > maxAbsOnUpperPart) maxAbsOnUpperPart = absValue;
    }
  }
  RealScalar threshold = maxAbsOnUpperPart * prec;
  for (Index j = 0; j < cols(); ++j)
    for (Index i = j + 1; i < rows(); ++i)
      if (numext::abs(coeff(i, j)) > threshold) return false;
  return true;
}

/** \returns true if *this is approximately equal to a lower triangular matrix,
 *          within the precision given by \a prec.
 *
 * \sa isUpperTriangular()
 */
template <typename Derived>
bool MatrixBase<Derived>::isLowerTriangular(const RealScalar& prec) const {
  RealScalar maxAbsOnLowerPart = static_cast<RealScalar>(-1);
  for (Index j = 0; j < cols(); ++j)
    for (Index i = j; i < rows(); ++i) {
      RealScalar absValue = numext::abs(coeff(i, j));
      if (absValue > maxAbsOnLowerPart) maxAbsOnLowerPart = absValue;
    }
  RealScalar threshold = maxAbsOnLowerPart * prec;
  for (Index j = 1; j < cols(); ++j) {
    Index maxi = numext::mini(j, rows() - 1);
    for (Index i = 0; i < maxi; ++i)
      if (numext::abs(coeff(i, j)) > threshold) return false;
  }
  return true;
}

/***************************************************************************
****************************************************************************
* Evaluators and Assignment of triangular expressions
***************************************************************************
***************************************************************************/

namespace internal {

// TODO: currently a triangular expression has the form TriangularView<.,.>
//      in the future triangular-ness should be defined by the expression traits
//      such that Transpose<TriangularView<.,.> > is valid. (currently TriangularBase::transpose() is overloaded to make
//      it work)
template <typename MatrixType, unsigned int Mode>
struct evaluator_traits<TriangularView<MatrixType, Mode>> {
  typedef typename storage_kind_to_evaluator_kind<typename MatrixType::StorageKind>::Kind Kind;
  typedef typename glue_shapes<typename evaluator_traits<MatrixType>::Shape, TriangularShape>::type Shape;
};

template <typename MatrixType, unsigned int Mode>
struct unary_evaluator<TriangularView<MatrixType, Mode>, IndexBased> : evaluator<internal::remove_all_t<MatrixType>> {
  typedef TriangularView<MatrixType, Mode> XprType;
  typedef evaluator<internal::remove_all_t<MatrixType>> Base;
  EIGEN_DEVICE_FUNC unary_evaluator(const XprType& xpr) : Base(xpr.nestedExpression()) {}
};

// Additional assignment kinds:
struct Triangular2Triangular {};
struct Triangular2Dense {};
struct Dense2Triangular {};

template <typename Kernel, unsigned int Mode, int UnrollCount, bool ClearOpposite>
struct triangular_assignment_loop;

/** \internal Specialization of the dense assignment kernel for triangular matrices.
 * The main difference is that the triangular, diagonal, and opposite parts are processed through three different
 * functions. \tparam UpLo must be either Lower or Upper \tparam Mode must be either 0, UnitDiag, ZeroDiag, or
 * SelfAdjoint
 */
template <int UpLo, int Mode, int SetOpposite, typename DstEvaluatorTypeT, typename SrcEvaluatorTypeT, typename Functor,
          int Version = Specialized>
class triangular_dense_assignment_kernel
    : public generic_dense_assignment_kernel<DstEvaluatorTypeT, SrcEvaluatorTypeT, Functor, Version> {
 protected:
  typedef generic_dense_assignment_kernel<DstEvaluatorTypeT, SrcEvaluatorTypeT, Functor, Version> Base;
  typedef typename Base::DstXprType DstXprType;
  typedef typename Base::SrcXprType SrcXprType;
  using Base::m_dst;
  using Base::m_functor;
  using Base::m_src;

 public:
  typedef typename Base::DstEvaluatorType DstEvaluatorType;
  typedef typename Base::SrcEvaluatorType SrcEvaluatorType;
  typedef typename Base::Scalar Scalar;
  typedef typename Base::AssignmentTraits AssignmentTraits;

  EIGEN_DEVICE_FUNC triangular_dense_assignment_kernel(DstEvaluatorType& dst, const SrcEvaluatorType& src,
                                                       const Functor& func, DstXprType& dstExpr)
      : Base(dst, src, func, dstExpr) {}

#ifdef EIGEN_INTERNAL_DEBUGGING
  EIGEN_DEVICE_FUNC void assignCoeff(Index row, Index col) {
    eigen_internal_assert(row != col);
    Base::assignCoeff(row, col);
  }
#else
  using Base::assignCoeff;
#endif

  EIGEN_DEVICE_FUNC void assignDiagonalCoeff(Index id) {
    if (Mode == UnitDiag && SetOpposite)
      m_functor.assignCoeff(m_dst.coeffRef(id, id), Scalar(1));
    else if (Mode == ZeroDiag && SetOpposite)
      m_functor.assignCoeff(m_dst.coeffRef(id, id), Scalar(0));
    else if (Mode == 0)
      Base::assignCoeff(id, id);
  }

  EIGEN_DEVICE_FUNC void assignOppositeCoeff(Index row, Index col) {
    eigen_internal_assert(row != col);
    if (SetOpposite) m_functor.assignCoeff(m_dst.coeffRef(row, col), Scalar(0));
  }
};

template <int Mode, bool SetOpposite, typename DstXprType, typename SrcXprType, typename Functor>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void call_triangular_assignment_loop(DstXprType& dst, const SrcXprType& src,
                                                                           const Functor& func) {
  typedef evaluator<DstXprType> DstEvaluatorType;
  typedef evaluator<SrcXprType> SrcEvaluatorType;

  SrcEvaluatorType srcEvaluator(src);

  Index dstRows = src.rows();
  Index dstCols = src.cols();
  if ((dst.rows() != dstRows) || (dst.cols() != dstCols)) dst.resize(dstRows, dstCols);
  DstEvaluatorType dstEvaluator(dst);

  typedef triangular_dense_assignment_kernel<Mode&(Lower | Upper), Mode&(UnitDiag | ZeroDiag | SelfAdjoint),
                                             SetOpposite, DstEvaluatorType, SrcEvaluatorType, Functor>
      Kernel;
  Kernel kernel(dstEvaluator, srcEvaluator, func, dst.const_cast_derived());

  enum {
    unroll = DstXprType::SizeAtCompileTime != Dynamic && SrcEvaluatorType::CoeffReadCost < HugeCost &&
             DstXprType::SizeAtCompileTime *
                     (int(DstEvaluatorType::CoeffReadCost) + int(SrcEvaluatorType::CoeffReadCost)) / 2 <=
                 EIGEN_UNROLLING_LIMIT
  };

  triangular_assignment_loop<Kernel, Mode, unroll ? int(DstXprType::SizeAtCompileTime) : Dynamic, SetOpposite>::run(
      kernel);
}

template <int Mode, bool SetOpposite, typename DstXprType, typename SrcXprType>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void call_triangular_assignment_loop(DstXprType& dst, const SrcXprType& src) {
  call_triangular_assignment_loop<Mode, SetOpposite>(
      dst, src, internal::assign_op<typename DstXprType::Scalar, typename SrcXprType::Scalar>());
}

template <>
struct AssignmentKind<TriangularShape, TriangularShape> {
  typedef Triangular2Triangular Kind;
};
template <>
struct AssignmentKind<DenseShape, TriangularShape> {
  typedef Triangular2Dense Kind;
};
template <>
struct AssignmentKind<TriangularShape, DenseShape> {
  typedef Dense2Triangular Kind;
};

template <typename Shape>
struct is_dense_structured_shape : std::integral_constant<bool, std::is_same<Shape, TriangularShape>::value ||
                                                                    std::is_same<Shape, SelfAdjointShape>::value> {};

template <typename Lhs, typename Rhs>
struct is_dense_structured_diagonal_product
    : std::integral_constant<bool, (is_dense_structured_shape<typename evaluator_traits<Lhs>::Shape>::value &&
                                    std::is_same<typename evaluator_traits<Rhs>::Shape, DiagonalShape>::value) ||
                                       (std::is_same<typename evaluator_traits<Lhs>::Shape, DiagonalShape>::value &&
                                        is_dense_structured_shape<typename evaluator_traits<Rhs>::Shape>::value)> {};

template <typename DstXprType, typename SrcXprType, typename Functor>
struct Assignment<DstXprType, SrcXprType, Functor, Triangular2Triangular> {
  EIGEN_DEVICE_FUNC static void run(DstXprType& dst, const SrcXprType& src, const Functor& func) {
    eigen_assert(int(DstXprType::Mode) == int(SrcXprType::Mode));

    call_triangular_assignment_loop<DstXprType::Mode, false>(dst, src, func);
  }
};

template <typename DstXprType, typename SrcXprType, typename Functor>
struct Assignment<DstXprType, SrcXprType, Functor, Triangular2Dense> {
  EIGEN_DEVICE_FUNC static void run(DstXprType& dst, const SrcXprType& src, const Functor& func) {
    call_triangular_assignment_loop<SrcXprType::Mode, (int(SrcXprType::Mode) & int(SelfAdjoint)) == 0>(dst, src, func);
  }
};

template <typename DstXprType, typename SrcXprType, typename Functor>
struct Assignment<DstXprType, SrcXprType, Functor, Dense2Triangular> {
  EIGEN_DEVICE_FUNC static void run(DstXprType& dst, const SrcXprType& src, const Functor& func) {
    call_triangular_assignment_loop<DstXprType::Mode, false>(dst, src, func);
  }
};

template <typename Kernel, unsigned int Mode, int UnrollCount, bool SetOpposite>
struct triangular_assignment_loop {
  // FIXME: this is not very clean, perhaps this information should be provided by the kernel?
  typedef typename Kernel::DstEvaluatorType DstEvaluatorType;
  typedef typename DstEvaluatorType::XprType DstXprType;

  enum {
    col = (UnrollCount - 1) / DstXprType::RowsAtCompileTime,
    row = (UnrollCount - 1) % DstXprType::RowsAtCompileTime
  };

  typedef typename Kernel::Scalar Scalar;

  EIGEN_DEVICE_FUNC static inline void run(Kernel& kernel) {
    triangular_assignment_loop<Kernel, Mode, UnrollCount - 1, SetOpposite>::run(kernel);

    if (row == col)
      kernel.assignDiagonalCoeff(row);
    else if (((Mode & Lower) && row > col) || ((Mode & Upper) && row < col))
      kernel.assignCoeff(row, col);
    else if (SetOpposite)
      kernel.assignOppositeCoeff(row, col);
  }
};

// prevent buggy user code from causing an infinite recursion
template <typename Kernel, unsigned int Mode, bool SetOpposite>
struct triangular_assignment_loop<Kernel, Mode, 0, SetOpposite> {
  EIGEN_DEVICE_FUNC static inline void run(Kernel&) {}
};

// TODO: experiment with a recursive assignment procedure splitting the current
//       triangular part into one rectangular and two triangular parts.

template <typename Kernel, unsigned int Mode, bool SetOpposite>
struct triangular_assignment_loop<Kernel, Mode, Dynamic, SetOpposite> {
  typedef typename Kernel::Scalar Scalar;
  typedef typename Kernel::DstEvaluatorType DstEvaluatorType;
  typedef typename Kernel::AssignmentTraits AssignmentTraits;

  enum {
    IsRowMajor = (int(DstEvaluatorType::Flags) & RowMajorBit) != 0,
    // In col-major: inner=row, outer=col. Upper means row<col i.e. inner<outer -> active before diagonal.
    // In row-major: inner=col, outer=row. Upper means row<col i.e. inner>outer -> active after diagonal.
    // So ActiveBeforeDiag = (Upper XOR IsRowMajor).
    ActiveBeforeDiag = (bool(Mode & Upper) != bool(IsRowMajor))
  };

  // Compile-time outer/inner to row/col mapping. These constant-fold away entirely:
  // ColMajor: row(outer,i) -> i, col(outer,i) -> outer
  // RowMajor: row(outer,i) -> outer, col(outer,i) -> i
  static constexpr Index row(Index outer, Index inner) { return IsRowMajor ? outer : inner; }
  static constexpr Index col(Index outer, Index inner) { return IsRowMajor ? inner : outer; }

  // Iterates in outer/inner order matching the storage layout for cache friendliness.
  // Unlike the old code (which always iterated outer=col, inner=row), this gives
  // contiguous memory access for both ColMajor and RowMajor storage.
  // Simple scalar loops allow GCC to recognize memcpy/memset idioms and Clang to auto-vectorize.
  // Uses a single running index 'i' per column (not separate loop variables) so the compiler
  // can track the continuous progression and optimize register allocation.
  EIGEN_DEVICE_FUNC static inline void run(Kernel& kernel) {
    const Index outerSize = IsRowMajor ? kernel.rows() : kernel.cols();
    const Index innerSize = IsRowMajor ? kernel.cols() : kernel.rows();

    for (Index outer = 0; outer < outerSize; ++outer) {
      const Index maxi = numext::mini(outer, innerSize);
      Index i = 0;

      if (ActiveBeforeDiag) {
        for (; i < maxi; ++i) kernel.assignCoeff(row(outer, i), col(outer, i));
      } else if (SetOpposite) {
        for (; i < maxi; ++i) kernel.assignOppositeCoeff(row(outer, i), col(outer, i));
      } else {
        i = maxi;
      }

      if (i < innerSize) kernel.assignDiagonalCoeff(i++);

      if (!ActiveBeforeDiag) {
        for (; i < innerSize; ++i) kernel.assignCoeff(row(outer, i), col(outer, i));
      } else if (SetOpposite) {
        for (; i < innerSize; ++i) kernel.assignOppositeCoeff(row(outer, i), col(outer, i));
      }
    }
  }
};

}  // end namespace internal

/** Assigns a triangular or selfadjoint matrix to a dense matrix.
 * If the matrix is triangular, the opposite part is set to zero. */
template <typename Derived>
template <typename DenseDerived>
EIGEN_DEVICE_FUNC void TriangularBase<Derived>::evalToLazy(MatrixBase<DenseDerived>& other) const {
  other.derived().resize(this->rows(), this->cols());
  internal::call_triangular_assignment_loop<Derived::Mode,
                                            (int(Derived::Mode) & int(SelfAdjoint)) == 0 /* SetOpposite */>(
      other.derived(), derived().nestedExpression());
}

namespace internal {

template <bool UseTriangularAssignmentLoop>
struct triangular_product_assignment_dispatcher {
  template <typename DstXprType, typename SrcXprType, typename Functor, typename Scalar>
  static void run(DstXprType& dst, const SrcXprType& src, const Functor&, const Scalar& alpha, bool beta) {
    if (!beta) {
      Index dstRows = src.rows();
      Index dstCols = src.cols();
      if ((dst.rows() != dstRows) || (dst.cols() != dstCols)) dst.resize(dstRows, dstCols);
    }

    dst._assignProduct(src, alpha, beta);
  }
};

// Underlying-storage data pointer for the diagonal operand of a structured x diagonal
// product, or nullptr for non-diagonal operands. The structured (triangular/selfadjoint)
// operand can safely share storage with dst because the kernel reads each (row, col) cell
// before writing it; only diagonal/dst overlap can corrupt later reads via the diagonal
// entries that have already been written.
template <typename Op>
EIGEN_DEVICE_FUNC inline const void* diagonal_operand_data(const Op& op, DiagonalShape) {
  return extract_data(op.diagonal());
}
template <typename Op, typename Shape>
EIGEN_DEVICE_FUNC inline const void* diagonal_operand_data(const Op& /*op*/, Shape) {
  return nullptr;
}

template <typename DstXprType, typename SrcXprType>
EIGEN_DEVICE_FUNC inline bool structured_diagonal_product_aliases(const DstXprType& dst, const SrcXprType& src) {
  const void* dst_data = dst.nestedExpression().data();
  if (dst_data == nullptr) return false;
  const void* lhs_diag_data =
      diagonal_operand_data(src.lhs(), typename evaluator_traits<typename SrcXprType::Lhs>::Shape{});
  const void* rhs_diag_data =
      diagonal_operand_data(src.rhs(), typename evaluator_traits<typename SrcXprType::Rhs>::Shape{});
  return (lhs_diag_data != nullptr && lhs_diag_data == dst_data) ||
         (rhs_diag_data != nullptr && rhs_diag_data == dst_data);
}

template <>
struct triangular_product_assignment_dispatcher<true> {
  template <typename DstXprType, typename SrcXprType, typename Functor, typename Scalar>
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void run(DstXprType& dst, const SrcXprType& src, const Functor& func,
                                                        const Scalar& alpha, bool beta) {
    EIGEN_UNUSED_VARIABLE(alpha);
    EIGEN_UNUSED_VARIABLE(beta);
    EIGEN_STATIC_ASSERT((int(DstXprType::Mode) & int(UnitDiag)) == 0,
                        WRITING_TO_TRIANGULAR_PART_WITH_UNIT_DIAGONAL_IS_NOT_SUPPORTED);
    // The triangular assignment loop reads src.coeff(row, col) lazily while writing
    // dst.coeffRef(row, col). When the diagonal operand of the product shares storage with
    // dst (e.g.
    //   A.triangularView<Upper>() = A.diagonal().asDiagonal() * A.triangularView<Upper>())
    // the diagonal entries already written in earlier columns would feed back as modified
    // values, corrupting later reads. Materialize the source into a temporary first when
    // overlap is detected at run time. The structured (triangular/selfadjoint) operand may
    // safely alias dst because the kernel reads each cell before writing it.
    if (structured_diagonal_product_aliases(dst, src)) {
      typename SrcXprType::PlainObject tmp(src);
      call_triangular_assignment_loop<DstXprType::Mode, false>(dst, tmp, func);
    } else {
      call_triangular_assignment_loop<DstXprType::Mode, false>(dst, src, func);
    }
  }
};

// Triangular = Product
template <typename DstXprType, typename Lhs, typename Rhs, typename Scalar>
struct Assignment<DstXprType, Product<Lhs, Rhs, DefaultProduct>,
                  internal::assign_op<Scalar, typename Product<Lhs, Rhs, DefaultProduct>::Scalar>, Dense2Triangular> {
  typedef Product<Lhs, Rhs, DefaultProduct> SrcXprType;
  static void run(DstXprType& dst, const SrcXprType& src,
                  const internal::assign_op<Scalar, typename SrcXprType::Scalar>& func) {
    enum { UseTriangularAssignmentLoop = is_dense_structured_diagonal_product<Lhs, Rhs>::value };
    triangular_product_assignment_dispatcher<UseTriangularAssignmentLoop>::run(dst, src, func, Scalar(1), false);
  }
};

// Triangular += Product
template <typename DstXprType, typename Lhs, typename Rhs, typename Scalar>
struct Assignment<DstXprType, Product<Lhs, Rhs, DefaultProduct>,
                  internal::add_assign_op<Scalar, typename Product<Lhs, Rhs, DefaultProduct>::Scalar>,
                  Dense2Triangular> {
  typedef Product<Lhs, Rhs, DefaultProduct> SrcXprType;
  static void run(DstXprType& dst, const SrcXprType& src,
                  const internal::add_assign_op<Scalar, typename SrcXprType::Scalar>& func) {
    enum { UseTriangularAssignmentLoop = is_dense_structured_diagonal_product<Lhs, Rhs>::value };
    triangular_product_assignment_dispatcher<UseTriangularAssignmentLoop>::run(dst, src, func, Scalar(1), true);
  }
};

// Triangular -= Product
template <typename DstXprType, typename Lhs, typename Rhs, typename Scalar>
struct Assignment<DstXprType, Product<Lhs, Rhs, DefaultProduct>,
                  internal::sub_assign_op<Scalar, typename Product<Lhs, Rhs, DefaultProduct>::Scalar>,
                  Dense2Triangular> {
  typedef Product<Lhs, Rhs, DefaultProduct> SrcXprType;
  static void run(DstXprType& dst, const SrcXprType& src,
                  const internal::sub_assign_op<Scalar, typename SrcXprType::Scalar>& func) {
    enum { UseTriangularAssignmentLoop = is_dense_structured_diagonal_product<Lhs, Rhs>::value };
    triangular_product_assignment_dispatcher<UseTriangularAssignmentLoop>::run(dst, src, func, Scalar(-1), true);
  }
};

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_TRIANGULARMATRIX_H
