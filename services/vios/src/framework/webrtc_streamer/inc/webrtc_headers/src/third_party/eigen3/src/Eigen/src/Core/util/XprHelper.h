// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008 Gael Guennebaud <gael.guennebaud@inria.fr>
// Copyright (C) 2006-2008 Benoit Jacob <jacob.benoit.1@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_XPRHELPER_H
#define EIGEN_XPRHELPER_H

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

// useful for unsigned / signed integer comparisons when idx is intended to be non-negative
template <typename IndexType>
EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE std::make_unsigned_t<IndexType> returnUnsignedIndexValue(const IndexType& idx) {
  EIGEN_STATIC_ASSERT((NumTraits<IndexType>::IsInteger), THIS FUNCTION IS FOR INTEGER TYPES)
  eigen_internal_assert(idx >= 0 && "Index value is negative and target type is unsigned");
  using UnsignedType = std::make_unsigned_t<IndexType>;
  return static_cast<UnsignedType>(idx);
}

template <typename IndexDest, typename IndexSrc, bool IndexDestIsInteger = NumTraits<IndexDest>::IsInteger,
          bool IndexDestIsSigned = NumTraits<IndexDest>::IsSigned,
          bool IndexSrcIsInteger = NumTraits<IndexSrc>::IsInteger,
          bool IndexSrcIsSigned = NumTraits<IndexSrc>::IsSigned>
struct convert_index_impl {
  static inline EIGEN_DEVICE_FUNC IndexDest run(const IndexSrc& idx) {
    eigen_internal_assert(idx <= NumTraits<IndexDest>::highest() && "Index value is too big for target type");
    return static_cast<IndexDest>(idx);
  }
};
template <typename IndexDest, typename IndexSrc>
struct convert_index_impl<IndexDest, IndexSrc, true, true, true, false> {
  // IndexDest is a signed integer
  // IndexSrc is an unsigned integer
  static inline EIGEN_DEVICE_FUNC IndexDest run(const IndexSrc& idx) {
    eigen_internal_assert(idx <= returnUnsignedIndexValue(NumTraits<IndexDest>::highest()) &&
                          "Index value is too big for target type");
    return static_cast<IndexDest>(idx);
  }
};
template <typename IndexDest, typename IndexSrc>
struct convert_index_impl<IndexDest, IndexSrc, true, false, true, true> {
  // IndexDest is an unsigned integer
  // IndexSrc is a signed integer
  static inline EIGEN_DEVICE_FUNC IndexDest run(const IndexSrc& idx) {
    eigen_internal_assert(returnUnsignedIndexValue(idx) <= NumTraits<IndexDest>::highest() &&
                          "Index value is too big for target type");
    return static_cast<IndexDest>(idx);
  }
};

template <typename IndexDest, typename IndexSrc>
EIGEN_DEVICE_FUNC inline IndexDest convert_index(const IndexSrc& idx) {
  return convert_index_impl<IndexDest, IndexSrc>::run(idx);
}

// true if both types are not valid index types
template <typename RowIndices, typename ColIndices>
struct valid_indexed_view_overload : std::integral_constant<bool, !(internal::is_valid_index_type<RowIndices>::value &&
                                                                    internal::is_valid_index_type<ColIndices>::value)> {
};

// promote_scalar_arg is an helper used in operation between an expression and a scalar, like:
//    expression * scalar
// Its role is to determine how the type T of the scalar operand should be promoted given the scalar type ExprScalar of
// the given expression. The IsSupported template parameter must be provided by the caller as:
// internal::has_ReturnType<ScalarBinaryOpTraits<ExprScalar,T,op> >::value using the proper order for ExprScalar and T.
// Then the logic is as follows:
//  - if the operation is natively supported as defined by IsSupported, then the scalar type is not promoted, and T is
//  returned.
//  - otherwise, NumTraits<ExprScalar>::Literal is returned if T is implicitly convertible to
//  NumTraits<ExprScalar>::Literal AND that this does not imply a float to integer conversion.
//  - otherwise, ExprScalar is returned if T is implicitly convertible to ExprScalar AND that this does not imply a
//  float to integer conversion.
//  - In all other cases, the promoted type is not defined, and the respective operation is thus invalid and not
//  available (SFINAE).
template <typename ExprScalar, typename T, bool IsSupported>
struct promote_scalar_arg;

template <typename S, typename T>
struct promote_scalar_arg<S, T, true> {
  using type = T;
};

// Recursively check safe conversion to PromotedType, and then ExprScalar if they are different.
template <typename ExprScalar, typename T, typename PromotedType,
          bool ConvertibleToLiteral = std::is_convertible<T, PromotedType>::value,
          bool IsSafe = NumTraits<T>::IsInteger || !NumTraits<PromotedType>::IsInteger>
struct promote_scalar_arg_unsupported;

// Start recursion with NumTraits<ExprScalar>::Literal
template <typename S, typename T>
struct promote_scalar_arg<S, T, false> : promote_scalar_arg_unsupported<S, T, typename NumTraits<S>::Literal> {};

// We found a match!
template <typename S, typename T, typename PromotedType>
struct promote_scalar_arg_unsupported<S, T, PromotedType, true, true> {
  using type = PromotedType;
};

// No match, but no real-to-integer issues, and ExprScalar and current PromotedType are different,
// so let's try to promote to ExprScalar
template <typename ExprScalar, typename T, typename PromotedType>
struct promote_scalar_arg_unsupported<ExprScalar, T, PromotedType, false, true>
    : promote_scalar_arg_unsupported<ExprScalar, T, ExprScalar> {};

// Unsafe real-to-integer, let's stop.
template <typename S, typename T, typename PromotedType, bool ConvertibleToLiteral>
struct promote_scalar_arg_unsupported<S, T, PromotedType, ConvertibleToLiteral, false> {};

// T is not even convertible to ExprScalar, let's stop.
template <typename S, typename T>
struct promote_scalar_arg_unsupported<S, T, S, false, true> {};

// classes inheriting no_assignment_operator don't generate a default operator=.
class no_assignment_operator {
  no_assignment_operator& operator=(const no_assignment_operator&) = delete;

 protected:
  EIGEN_DEFAULT_COPY_CONSTRUCTOR(no_assignment_operator)
  EIGEN_DEFAULT_EMPTY_CONSTRUCTOR_AND_DESTRUCTOR(no_assignment_operator)
};

/** \internal return the index type with the largest number of bits */
template <typename I1, typename I2>
struct promote_index_type {
  using type = std::conditional_t<(sizeof(I1) < sizeof(I2)), I2, I1>;
};

/** \internal If the template parameter Value is Dynamic, this class is just a wrapper around a T variable that
 * can be accessed using value() and setValue().
 * Otherwise, this class is an empty structure and value() just returns the template parameter Value.
 */
template <typename T, int Value>
class variable_if_dynamic {
 public:
  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE explicit variable_if_dynamic(T v) {
    EIGEN_ONLY_USED_FOR_DEBUG(v);
    eigen_assert(v == T(Value));
  }
  EIGEN_DEVICE_FUNC static constexpr T value() { return T(Value); }
  EIGEN_DEVICE_FUNC constexpr operator T() const { return T(Value); }
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void setValue(T v) const {
    EIGEN_ONLY_USED_FOR_DEBUG(v);
    eigen_assert(v == T(Value));
  }
};

template <typename T>
class variable_if_dynamic<T, Dynamic> {
  T m_value;

 public:
  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE explicit variable_if_dynamic(T value = 0) noexcept : m_value(value) {}
  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE T value() const { return m_value; }
  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE operator T() const { return m_value; }
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void setValue(T value) { m_value = value; }
};

/** \internal like variable_if_dynamic but for DynamicIndex
 */
template <typename T, int Value>
class variable_if_dynamicindex {
 public:
  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE explicit variable_if_dynamicindex(T v) {
    EIGEN_ONLY_USED_FOR_DEBUG(v);
    eigen_assert(v == T(Value));
  }
  EIGEN_DEVICE_FUNC static constexpr T value() { return T(Value); }
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE constexpr void setValue(T) {}
};

template <typename T>
class variable_if_dynamicindex<T, DynamicIndex> {
  T m_value;
  EIGEN_DEVICE_FUNC variable_if_dynamicindex() { eigen_assert(false); }

 public:
  EIGEN_DEVICE_FUNC constexpr EIGEN_STRONG_INLINE explicit variable_if_dynamicindex(T value) : m_value(value) {}
  EIGEN_DEVICE_FUNC constexpr T EIGEN_STRONG_INLINE value() const { return m_value; }
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void setValue(T value) { m_value = value; }
};

template <typename T>
struct functor_traits {
  enum { Cost = 10, PacketAccess = false, IsRepeatable = false };
};

// estimates the cost of lazily evaluating a generic functor by unwinding the expression
template <typename Xpr>
struct nested_functor_cost {
  static constexpr Index Cost = static_cast<Index>(functor_traits<Xpr>::Cost);
};

template <typename Scalar, int Rows, int Cols, int Options, int MaxRows, int MaxCols>
struct nested_functor_cost<Matrix<Scalar, Rows, Cols, Options, MaxRows, MaxCols>> {
  static constexpr Index Cost = 1;
};

template <typename Scalar, int Rows, int Cols, int Options, int MaxRows, int MaxCols>
struct nested_functor_cost<Array<Scalar, Rows, Cols, Options, MaxRows, MaxCols>> {
  static constexpr Index Cost = 1;
};

// TODO: assign a cost to the stride type?
template <typename PlainObjectType, int MapOptions, typename StrideType>
struct nested_functor_cost<Map<PlainObjectType, MapOptions, StrideType>> : nested_functor_cost<PlainObjectType> {};

template <typename Func, typename Xpr>
struct nested_functor_cost<CwiseUnaryOp<Func, Xpr>> {
  using XprCleaned = remove_all_t<Xpr>;
  using FuncCleaned = remove_all_t<Func>;
  static constexpr Index Cost = nested_functor_cost<FuncCleaned>::Cost + nested_functor_cost<XprCleaned>::Cost;
};

template <typename Func, typename Xpr>
struct nested_functor_cost<CwiseNullaryOp<Func, Xpr>> {
  using XprCleaned = remove_all_t<Xpr>;
  using FuncCleaned = remove_all_t<Func>;
  static constexpr Index Cost = nested_functor_cost<FuncCleaned>::Cost + nested_functor_cost<XprCleaned>::Cost;
};

template <typename Func, typename LhsXpr, typename RhsXpr>
struct nested_functor_cost<CwiseBinaryOp<Func, LhsXpr, RhsXpr>> {
  using LhsXprCleaned = remove_all_t<LhsXpr>;
  using RhsXprCleaned = remove_all_t<RhsXpr>;
  using FuncCleaned = remove_all_t<Func>;
  static constexpr Index Cost = nested_functor_cost<FuncCleaned>::Cost + nested_functor_cost<LhsXprCleaned>::Cost +
                                nested_functor_cost<RhsXprCleaned>::Cost;
};

template <typename Func, typename LhsXpr, typename MidXpr, typename RhsXpr>
struct nested_functor_cost<CwiseTernaryOp<Func, LhsXpr, MidXpr, RhsXpr>> {
  using LhsXprCleaned = remove_all_t<LhsXpr>;
  using MidXprCleaned = remove_all_t<MidXpr>;
  using RhsXprCleaned = remove_all_t<RhsXpr>;
  using FuncCleaned = remove_all_t<Func>;
  static constexpr Index Cost = nested_functor_cost<FuncCleaned>::Cost + nested_functor_cost<LhsXprCleaned>::Cost +
                                nested_functor_cost<MidXprCleaned>::Cost + nested_functor_cost<RhsXprCleaned>::Cost;
};

template <typename Xpr>
struct functor_cost {
  static constexpr Index Cost = plain_enum_max(nested_functor_cost<Xpr>::Cost, 1);
};

template <typename T>
struct packet_traits;

template <typename T>
struct unpacket_traits;

template <int Size, typename PacketType,
          bool Stop = Size == Dynamic || (Size % unpacket_traits<PacketType>::size) == 0 ||
                      std::is_same<PacketType, typename unpacket_traits<PacketType>::half>::value>
struct find_best_packet_helper;

template <int Size, typename PacketType>
struct find_best_packet_helper<Size, PacketType, true> {
  using type = PacketType;
};

template <int Size, typename PacketType>
struct find_best_packet_helper<Size, PacketType, false> {
  using type = typename find_best_packet_helper<Size, typename unpacket_traits<PacketType>::half>::type;
};

template <typename T, int Size>
struct find_best_packet {
  using type = typename find_best_packet_helper<Size, typename packet_traits<T>::type>::type;
};

template <int Size, typename PacketType,
          bool Stop = (Size == unpacket_traits<PacketType>::size) ||
                      std::is_same<PacketType, typename unpacket_traits<PacketType>::half>::value>
struct find_packet_by_size_helper;
template <int Size, typename PacketType>
struct find_packet_by_size_helper<Size, PacketType, true> {
  using type = PacketType;
};
template <int Size, typename PacketType>
struct find_packet_by_size_helper<Size, PacketType, false> {
  using type = typename find_packet_by_size_helper<Size, typename unpacket_traits<PacketType>::half>::type;
};

template <typename T, int Size>
struct find_packet_by_size {
  using type = typename find_packet_by_size_helper<Size, typename packet_traits<T>::type>::type;
  static constexpr bool value = (Size == unpacket_traits<type>::size);
};
template <typename T>
struct find_packet_by_size<T, 1> {
  using type = typename unpacket_traits<T>::type;
  static constexpr bool value = (unpacket_traits<type>::size == 1);
};

#if EIGEN_MAX_STATIC_ALIGN_BYTES > 0
constexpr int compute_default_alignment_helper(int ArrayBytes, int AlignmentBytes) {
  if ((ArrayBytes % AlignmentBytes) == 0) {
    return AlignmentBytes;
  } else if (EIGEN_MIN_ALIGN_BYTES < AlignmentBytes) {
    return compute_default_alignment_helper(ArrayBytes, AlignmentBytes / 2);
  } else {
    return 0;
  }
}
#else
// If static alignment is disabled, no need to bother.
// This also avoids a division by zero
constexpr int compute_default_alignment_helper(int ArrayBytes, int AlignmentBytes) {
  EIGEN_UNUSED_VARIABLE(ArrayBytes);
  EIGEN_UNUSED_VARIABLE(AlignmentBytes);
  return 0;
}
#endif

template <typename T, int Size>
struct compute_default_alignment
    : std::integral_constant<int, compute_default_alignment_helper(Size * sizeof(T), EIGEN_MAX_STATIC_ALIGN_BYTES)> {};

template <typename T>
struct compute_default_alignment<T, Dynamic> : std::integral_constant<int, EIGEN_MAX_ALIGN_BYTES> {};

template <typename Scalar_, int Rows_, int Cols_,
          int Options_ = AutoAlign | ((Rows_ == 1 && Cols_ != 1)   ? RowMajor
                                      : (Cols_ == 1 && Rows_ != 1) ? ColMajor
                                                                   : EIGEN_DEFAULT_MATRIX_STORAGE_ORDER_OPTION),
          int MaxRows_ = Rows_, int MaxCols_ = Cols_>
struct make_proper_matrix_type {
 private:
  static constexpr bool IsColVector = Cols_ == 1 && Rows_ != 1;
  static constexpr bool IsRowVector = Rows_ == 1 && Cols_ != 1;
  static constexpr int Options = IsColVector   ? (Options_ | ColMajor) & ~RowMajor
                                 : IsRowVector ? (Options_ | RowMajor) & ~ColMajor
                                               : Options_;

 public:
  using type = Matrix<Scalar_, Rows_, Cols_, Options, MaxRows_, MaxCols_>;
};

constexpr unsigned compute_matrix_flags(int Options) {
  unsigned row_major_bit = Options & RowMajor ? RowMajorBit : 0;
  // FIXME currently we still have to handle DirectAccessBit at the expression level to handle DenseCoeffsBase<>
  // and then propagate this information to the evaluator's flags.
  // However, I (Gael) think that DirectAccessBit should only matter at the evaluation stage.
  return DirectAccessBit | LvalueBit | NestByRefBit | row_major_bit;
}

constexpr int size_at_compile_time(int rows, int cols) {
  if (rows == 0 || cols == 0) return 0;
  if (rows == Dynamic || cols == Dynamic) return Dynamic;
  return rows * cols;
}

template <typename XprType>
struct size_of_xpr_at_compile_time
    : std::integral_constant<int, size_at_compile_time(traits<XprType>::RowsAtCompileTime,
                                                       traits<XprType>::ColsAtCompileTime)> {};

/* plain_matrix_type : the difference from eval is that plain_matrix_type is always a plain matrix type,
 * whereas eval is a const reference in the case of a matrix
 */

template <typename T, typename StorageKind = typename traits<T>::StorageKind>
struct plain_matrix_type;
template <typename T, typename BaseClassType, int Flags>
struct plain_matrix_type_dense;
template <typename T>
struct plain_matrix_type<T, Dense> {
  using type = typename plain_matrix_type_dense<T, typename traits<T>::XprKind, traits<T>::Flags>::type;
};
template <typename T>
struct plain_matrix_type<T, DiagonalShape> {
  using type = typename T::PlainObject;
};

template <typename T>
struct plain_matrix_type<T, SkewSymmetricShape> {
  using type = typename T::PlainObject;
};

template <typename T, int Flags>
struct plain_matrix_type_dense<T, MatrixXpr, Flags> {
  using type = Matrix<typename traits<T>::Scalar, traits<T>::RowsAtCompileTime, traits<T>::ColsAtCompileTime,
                      AutoAlign | (Flags & RowMajorBit ? RowMajor : ColMajor), traits<T>::MaxRowsAtCompileTime,
                      traits<T>::MaxColsAtCompileTime>;
};

template <typename T, int Flags>
struct plain_matrix_type_dense<T, ArrayXpr, Flags> {
  using type = Array<typename traits<T>::Scalar, traits<T>::RowsAtCompileTime, traits<T>::ColsAtCompileTime,
                     AutoAlign | (Flags & RowMajorBit ? RowMajor : ColMajor), traits<T>::MaxRowsAtCompileTime,
                     traits<T>::MaxColsAtCompileTime>;
};

/* eval : the return type of eval(). For matrices, this is just a const reference
 * in order to avoid a useless copy
 */

template <typename T, typename StorageKind = typename traits<T>::StorageKind>
struct eval;

template <typename T>
struct eval<T, Dense> {
  using type = typename plain_matrix_type<T>::type;
};

template <typename T>
struct eval<T, DiagonalShape> {
  using type = typename plain_matrix_type<T>::type;
};

template <typename T>
struct eval<T, SkewSymmetricShape> {
  using type = typename plain_matrix_type<T>::type;
};

// for matrices, no need to evaluate, just use a const reference to avoid a useless copy
template <typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
struct eval<Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>, Dense> {
  using type = const Matrix<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>&;
};

template <typename Scalar_, int Rows_, int Cols_, int Options_, int MaxRows_, int MaxCols_>
struct eval<Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>, Dense> {
  using type = const Array<Scalar_, Rows_, Cols_, Options_, MaxRows_, MaxCols_>&;
};

/* similar to plain_matrix_type, but using the evaluator's Flags */
template <typename T, typename StorageKind = typename traits<T>::StorageKind>
struct plain_object_eval;

template <typename T>
struct plain_object_eval<T, Dense> {
  using type = typename plain_matrix_type_dense<T, typename traits<T>::XprKind, evaluator<T>::Flags>::type;
};

/* plain_matrix_type_column_major : same as plain_matrix_type but guaranteed to be column-major
 */
template <typename T>
struct plain_matrix_type_column_major {
  static constexpr int Rows = traits<T>::RowsAtCompileTime;
  static constexpr int Cols = traits<T>::ColsAtCompileTime;
  static constexpr int MaxRows = traits<T>::MaxRowsAtCompileTime;
  static constexpr int MaxCols = traits<T>::MaxColsAtCompileTime;
  using type = Matrix<typename traits<T>::Scalar, Rows, Cols, (MaxRows == 1 && MaxCols != 1) ? RowMajor : ColMajor,
                      MaxRows, MaxCols>;
};

/* plain_matrix_type_row_major : same as plain_matrix_type but guaranteed to be row-major
 */
template <typename T>
struct plain_matrix_type_row_major {
  static constexpr int Rows = traits<T>::RowsAtCompileTime;
  static constexpr int Cols = traits<T>::ColsAtCompileTime;
  static constexpr int MaxRows = traits<T>::MaxRowsAtCompileTime;
  static constexpr int MaxCols = traits<T>::MaxColsAtCompileTime;
  using type = Matrix<typename traits<T>::Scalar, Rows, Cols, (MaxCols == 1 && MaxRows != 1) ? ColMajor : RowMajor,
                      MaxRows, MaxCols>;
};

/** \internal The reference selector for template expressions. The idea is that we don't
 * need to use references for expressions since they are light weight proxy
 * objects which should generate no copying overhead. */
template <typename T>
struct ref_selector {
  using type = std::conditional_t<bool(traits<T>::Flags& NestByRefBit), T const&, const T>;

  using non_const_type = std::conditional_t<bool(traits<T>::Flags& NestByRefBit), T&, T>;
};

// However, we still need a mechanism to detect whether an expression which is evaluated multiple time
// has to be evaluated into a temporary.
// That's the purpose of this new nested_eval helper:
/** \internal Determines how a given expression should be nested when evaluated multiple times.
 * For example, when you do a * (b+c), Eigen will determine how the expression b+c should be
 * evaluated into the bigger product expression. The choice is between nesting the expression b+c as-is, or
 * evaluating that expression b+c into a temporary variable d, and nest d so that the resulting expression is
 * a*d. Evaluating can be beneficial for example if every coefficient access in the resulting expression causes
 * many coefficient accesses in the nested expressions -- as is the case with matrix product for example.
 *
 * \tparam T the type of the expression being nested.
 * \tparam n the number of coefficient accesses in the nested expression for each coefficient access in the bigger
 * expression. \tparam PlainObject the type of the temporary if needed.
 */
template <typename T, int n, typename PlainObject = typename plain_object_eval<T>::type>
struct nested_eval {
  enum {
    ScalarReadCost = NumTraits<typename traits<T>::Scalar>::ReadCost,
    CoeffReadCost =
        evaluator<T>::CoeffReadCost,  // NOTE What if an evaluator evaluate itself into a temporary?
                                      //      Then CoeffReadCost will be small (e.g., 1) but we still have to evaluate,
                                      //      especially if n>1. This situation is already taken care by the
                                      //      EvalBeforeNestingBit flag, which is turned ON for all evaluator creating a
                                      //      temporary. This flag is then propagated by the parent evaluators. Another
                                      //      solution could be to count the number of temps?
    NAsInteger = n == Dynamic ? HugeCost : n,
    CostEval = (NAsInteger + 1) * ScalarReadCost + CoeffReadCost,
    CostNoEval = int(NAsInteger) * int(CoeffReadCost),
    Evaluate = (int(evaluator<T>::Flags) & EvalBeforeNestingBit) || (int(CostEval) < int(CostNoEval))
  };

  using type = std::conditional_t<Evaluate, PlainObject, typename ref_selector<T>::type>;
};

template <typename Derived, typename XprKind = typename traits<Derived>::XprKind>
struct dense_xpr_base {
  /* dense_xpr_base should only ever be used on dense expressions, thus falling either into the MatrixXpr or into the
   * ArrayXpr cases */
};

template <typename Derived>
struct dense_xpr_base<Derived, MatrixXpr> {
  using type = MatrixBase<Derived>;
};

template <typename Derived>
struct dense_xpr_base<Derived, ArrayXpr> {
  using type = ArrayBase<Derived>;
};

template <typename Derived, typename XprKind = typename traits<Derived>::XprKind,
          typename StorageKind = typename traits<Derived>::StorageKind>
struct generic_xpr_base;

template <typename Derived, typename XprKind>
struct generic_xpr_base<Derived, XprKind, Dense> {
  using type = typename dense_xpr_base<Derived, XprKind>::type;
};

template <typename XprType, typename CastType>
struct cast_return_type {
  using CurrentScalarType = typename XprType::Scalar;
  using CastType_ = remove_all_t<CastType>;
  using NewScalarType = typename CastType_::Scalar;
  using type = std::conditional_t<std::is_same<CurrentScalarType, NewScalarType>::value, const XprType&, CastType>;
};

template <typename A, typename B>
struct promote_storage_type;

template <typename A>
struct promote_storage_type<A, A> {
  using ret = A;
};
template <typename A>
struct promote_storage_type<A, const A> {
  using ret = A;
};
template <typename A>
struct promote_storage_type<const A, A> {
  using ret = A;
};

/** \internal Specify the "storage kind" of applying a coefficient-wise
 * binary operations between two expressions of kinds A and B respectively.
 * The template parameter Functor permits to specialize the resulting storage kind wrt to
 * the functor.
 * The default rules are as follows:
 * \code
 * A      op A      -> A
 * A      op dense  -> dense
 * dense  op B      -> dense
 * sparse op dense  -> sparse
 * dense  op sparse -> sparse
 * \endcode
 */
template <typename A, typename B, typename Functor>
struct cwise_promote_storage_type;

template <typename A, typename Functor>
struct cwise_promote_storage_type<A, A, Functor> {
  using ret = A;
};
template <typename Functor>
struct cwise_promote_storage_type<Dense, Dense, Functor> {
  using ret = Dense;
};
template <typename A, typename Functor>
struct cwise_promote_storage_type<A, Dense, Functor> {
  using ret = Dense;
};
template <typename B, typename Functor>
struct cwise_promote_storage_type<Dense, B, Functor> {
  using ret = Dense;
};
template <typename Functor>
struct cwise_promote_storage_type<Sparse, Dense, Functor> {
  using ret = Sparse;
};
template <typename Functor>
struct cwise_promote_storage_type<Dense, Sparse, Functor> {
  using ret = Sparse;
};

template <typename LhsKind, typename RhsKind, int LhsOrder, int RhsOrder>
struct cwise_promote_storage_order : std::integral_constant<int, LhsOrder> {};

template <typename LhsKind, int LhsOrder, int RhsOrder>
struct cwise_promote_storage_order<LhsKind, Sparse, LhsOrder, RhsOrder> : std::integral_constant<int, RhsOrder> {};
template <typename RhsKind, int LhsOrder, int RhsOrder>
struct cwise_promote_storage_order<Sparse, RhsKind, LhsOrder, RhsOrder> : std::integral_constant<int, LhsOrder> {};
template <int Order>
struct cwise_promote_storage_order<Sparse, Sparse, Order, Order> : std::integral_constant<int, Order> {};

/** \internal Specify the "storage kind" of multiplying an expression of kind A with kind B.
 * The template parameter ProductTag permits to specialize the resulting storage kind wrt to
 * some compile-time properties of the product: GemmProduct, GemvProduct, OuterProduct, InnerProduct.
 * The default rules are as follows:
 * \code
 *  K * K            -> K
 *  dense * K        -> dense
 *  K * dense        -> dense
 *  diag * K         -> K
 *  K * diag         -> K
 *  Perm * K         -> K
 * K * Perm          -> K
 * \endcode
 */
template <typename A, typename B, int ProductTag>
struct product_promote_storage_type;

template <typename A, int ProductTag>
struct product_promote_storage_type<A, A, ProductTag> {
  using ret = A;
};
template <int ProductTag>
struct product_promote_storage_type<Dense, Dense, ProductTag> {
  using ret = Dense;
};
template <typename A, int ProductTag>
struct product_promote_storage_type<A, Dense, ProductTag> {
  using ret = Dense;
};
template <typename B, int ProductTag>
struct product_promote_storage_type<Dense, B, ProductTag> {
  using ret = Dense;
};

template <typename A, int ProductTag>
struct product_promote_storage_type<A, DiagonalShape, ProductTag> {
  using ret = A;
};
template <typename B, int ProductTag>
struct product_promote_storage_type<DiagonalShape, B, ProductTag> {
  using ret = B;
};
template <int ProductTag>
struct product_promote_storage_type<Dense, DiagonalShape, ProductTag> {
  using ret = Dense;
};
template <int ProductTag>
struct product_promote_storage_type<DiagonalShape, Dense, ProductTag> {
  using ret = Dense;
};

template <typename A, int ProductTag>
struct product_promote_storage_type<A, SkewSymmetricShape, ProductTag> {
  using ret = A;
};
template <typename B, int ProductTag>
struct product_promote_storage_type<SkewSymmetricShape, B, ProductTag> {
  using ret = B;
};
template <int ProductTag>
struct product_promote_storage_type<Dense, SkewSymmetricShape, ProductTag> {
  using ret = Dense;
};
template <int ProductTag>
struct product_promote_storage_type<SkewSymmetricShape, Dense, ProductTag> {
  using ret = Dense;
};
template <int ProductTag>
struct product_promote_storage_type<SkewSymmetricShape, SkewSymmetricShape, ProductTag> {
  using ret = Dense;
};

template <typename A, int ProductTag>
struct product_promote_storage_type<A, PermutationStorage, ProductTag> {
  using ret = A;
};
template <typename B, int ProductTag>
struct product_promote_storage_type<PermutationStorage, B, ProductTag> {
  using ret = B;
};
template <int ProductTag>
struct product_promote_storage_type<Dense, PermutationStorage, ProductTag> {
  using ret = Dense;
};
template <int ProductTag>
struct product_promote_storage_type<PermutationStorage, Dense, ProductTag> {
  using ret = Dense;
};

/** \internal gives the plain matrix or array type to store a row/column/diagonal of a matrix type.
 * \tparam Scalar optional parameter allowing to pass a different scalar type than the one of the MatrixType.
 */
template <typename ExpressionType, typename Scalar = typename ExpressionType::Scalar>
struct plain_row_type {
  using MatrixRowType =
      Matrix<Scalar, 1, ExpressionType::ColsAtCompileTime, int(ExpressionType::PlainObject::Options) | int(RowMajor), 1,
             ExpressionType::MaxColsAtCompileTime>;
  using ArrayRowType =
      Array<Scalar, 1, ExpressionType::ColsAtCompileTime, int(ExpressionType::PlainObject::Options) | int(RowMajor), 1,
            ExpressionType::MaxColsAtCompileTime>;

  using type = std::conditional_t<std::is_same<typename traits<ExpressionType>::XprKind, MatrixXpr>::value,
                                  MatrixRowType, ArrayRowType>;
};

template <typename ExpressionType, typename Scalar = typename ExpressionType::Scalar>
struct plain_col_type {
  using MatrixColType =
      Matrix<Scalar, ExpressionType::RowsAtCompileTime, 1, ExpressionType::PlainObject::Options & ~RowMajor,
             ExpressionType::MaxRowsAtCompileTime, 1>;
  using ArrayColType = Array<Scalar, ExpressionType::RowsAtCompileTime, 1,
                             ExpressionType::PlainObject::Options & ~RowMajor, ExpressionType::MaxRowsAtCompileTime, 1>;

  using type = std::conditional_t<std::is_same<typename traits<ExpressionType>::XprKind, MatrixXpr>::value,
                                  MatrixColType, ArrayColType>;
};

template <typename ExpressionType, typename Scalar = typename ExpressionType::Scalar>
struct plain_diag_type {
  static constexpr int diag_size =
      internal::min_size_prefer_dynamic(ExpressionType::RowsAtCompileTime, ExpressionType::ColsAtCompileTime);
  static constexpr int max_diag_size =
      min_size_prefer_fixed(ExpressionType::MaxRowsAtCompileTime, ExpressionType::MaxColsAtCompileTime);
  using MatrixDiagType =
      Matrix<Scalar, diag_size, 1, ExpressionType::PlainObject::Options & ~RowMajor, max_diag_size, 1>;
  using ArrayDiagType = Array<Scalar, diag_size, 1, ExpressionType::PlainObject::Options & ~RowMajor, max_diag_size, 1>;

  using type = std::conditional_t<std::is_same<typename traits<ExpressionType>::XprKind, MatrixXpr>::value,
                                  MatrixDiagType, ArrayDiagType>;
};

template <typename Expr, typename Scalar = typename Expr::Scalar>
struct plain_constant_type {
  static constexpr int Options = (traits<Expr>::Flags & RowMajorBit) ? RowMajor : 0;

  using array_type = Array<Scalar, traits<Expr>::RowsAtCompileTime, traits<Expr>::ColsAtCompileTime, Options,
                           traits<Expr>::MaxRowsAtCompileTime, traits<Expr>::MaxColsAtCompileTime>;

  using matrix_type = Matrix<Scalar, traits<Expr>::RowsAtCompileTime, traits<Expr>::ColsAtCompileTime, Options,
                             traits<Expr>::MaxRowsAtCompileTime, traits<Expr>::MaxColsAtCompileTime>;

  using type = CwiseNullaryOp<scalar_constant_op<Scalar>,
                              const std::conditional_t<std::is_same<typename traits<Expr>::XprKind, MatrixXpr>::value,
                                                       matrix_type, array_type>>;
};

template <typename ExpressionType>
struct is_lvalue : std::integral_constant<bool, (!bool(std::is_const<ExpressionType>::value)) &&
                                                    bool((traits<ExpressionType>::Flags & LvalueBit))> {};

template <typename T>
struct is_diagonal : std::false_type {};

template <typename T>
struct is_diagonal<DiagonalBase<T>> : std::true_type {};

template <typename T>
struct is_diagonal<DiagonalWrapper<T>> : std::true_type {};

template <typename T, int S>
struct is_diagonal<DiagonalMatrix<T, S>> : std::true_type {};

template <typename T>
struct is_identity : std::false_type {};

template <typename T>
struct is_identity<CwiseNullaryOp<internal::scalar_identity_op<typename T::Scalar>, T>> : std::true_type {};

template <typename S1, typename S2>
struct glue_shapes;
template <>
struct glue_shapes<DenseShape, TriangularShape> {
  using type = TriangularShape;
};

template <typename T1, typename T2>
struct possibly_same_dense
    : std::integral_constant<bool, has_direct_access<T1>::value && has_direct_access<T2>::value &&
                                       std::is_same<typename T1::Scalar, typename T2::Scalar>::value> {};

template <typename T1, typename T2>
EIGEN_DEVICE_FUNC bool is_same_dense(const T1& mat1, const T2& mat2,
                                     std::enable_if_t<possibly_same_dense<T1, T2>::value>* = 0) {
  return (mat1.data() == mat2.data()) && (mat1.innerStride() == mat2.innerStride()) &&
         (mat1.outerStride() == mat2.outerStride());
}

template <typename T1, typename T2>
EIGEN_DEVICE_FUNC bool is_same_dense(const T1&, const T2&, std::enable_if_t<!possibly_same_dense<T1, T2>::value>* = 0) {
  return false;
}

// Internal helper defining the cost of a scalar division for the type T.
// The default heuristic can be specialized for each scalar type and architecture.
template <typename T, bool Vectorized = false, typename EnableIf = void>
struct scalar_div_cost : std::integral_constant<int, 8 * NumTraits<T>::MulCost> {};

template <typename T, bool Vectorized>
struct scalar_div_cost<T, Vectorized, std::enable_if_t<NumTraits<T>::IsComplex>>
    : std::integral_constant<int, 2 * scalar_div_cost<typename NumTraits<T>::Real>::value +
                                      6 * NumTraits<typename NumTraits<T>::Real>::MulCost +
                                      3 * NumTraits<typename NumTraits<T>::Real>::AddCost> {};

template <bool Vectorized>
struct scalar_div_cost<signed long, Vectorized, std::conditional_t<sizeof(long) == 8, void, std::false_type>>
    : std::integral_constant<int, 24> {};
template <bool Vectorized>
struct scalar_div_cost<unsigned long, Vectorized, std::conditional_t<sizeof(long) == 8, void, std::false_type>>
    : std::integral_constant<int, 21> {};

#ifdef EIGEN_DEBUG_ASSIGN
std::string demangle_traversal(int t) {
  if (t == DefaultTraversal) return "DefaultTraversal";
  if (t == LinearTraversal) return "LinearTraversal";
  if (t == InnerVectorizedTraversal) return "InnerVectorizedTraversal";
  if (t == LinearVectorizedTraversal) return "LinearVectorizedTraversal";
  if (t == SliceVectorizedTraversal) return "SliceVectorizedTraversal";
  return "?";
}
std::string demangle_unrolling(int t) {
  if (t == NoUnrolling) return "NoUnrolling";
  if (t == InnerUnrolling) return "InnerUnrolling";
  if (t == CompleteUnrolling) return "CompleteUnrolling";
  return "?";
}
std::string demangle_flags(int f) {
  std::string res;
  if (f & RowMajorBit) res += " | RowMajor";
  if (f & PacketAccessBit) res += " | Packet";
  if (f & LinearAccessBit) res += " | Linear";
  if (f & LvalueBit) res += " | Lvalue";
  if (f & DirectAccessBit) res += " | Direct";
  if (f & NestByRefBit) res += " | NestByRef";
  if (f & NoPreferredStorageOrderBit) res += " | NoPreferredStorageOrderBit";

  return res;
}
#endif

template <typename XprType>
struct is_block_xpr : std::false_type {};

template <typename XprType, int BlockRows, int BlockCols, bool InnerPanel>
struct is_block_xpr<Block<XprType, BlockRows, BlockCols, InnerPanel>> : std::true_type {};

template <typename XprType, int BlockRows, int BlockCols, bool InnerPanel>
struct is_block_xpr<const Block<XprType, BlockRows, BlockCols, InnerPanel>> : std::true_type {};

// Helper utility for constructing non-recursive block expressions.
template <typename XprType>
struct block_xpr_helper {
  using BaseType = XprType;

  // For regular block expressions, simply forward along the InnerPanel argument,
  // which is set when calling row/column expressions.
  static constexpr bool is_inner_panel(bool inner_panel) { return inner_panel; }

  // Only enable non-const base function if XprType is not const (otherwise we get a duplicate definition).
  template <typename T = XprType, typename EnableIf = std::enable_if_t<!std::is_const<T>::value>>
  static EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE BaseType& base(XprType& xpr) {
    return xpr;
  }
  static EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE const BaseType& base(const XprType& xpr) { return xpr; }
  static constexpr EIGEN_ALWAYS_INLINE Index row(const XprType& /*xpr*/, Index r) { return r; }
  static constexpr EIGEN_ALWAYS_INLINE Index col(const XprType& /*xpr*/, Index c) { return c; }
};

template <typename XprType, int BlockRows, int BlockCols, bool InnerPanel>
struct block_xpr_helper<Block<XprType, BlockRows, BlockCols, InnerPanel>> {
  using BlockXprType = Block<XprType, BlockRows, BlockCols, InnerPanel>;
  // Recursive helper in case of explicit block-of-block expression.
  using NestedXprHelper = block_xpr_helper<XprType>;
  using BaseType = typename NestedXprHelper::BaseType;

  // For block-of-block expressions, we need to combine the InnerPannel trait
  // with that of the block subexpression.
  static constexpr bool is_inner_panel(bool inner_panel) { return InnerPanel && inner_panel; }

  // Only enable non-const base function if XprType is not const (otherwise we get a duplicates definition).
  template <typename T = XprType, typename EnableIf = std::enable_if_t<!std::is_const<T>::value>>
  static EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE BaseType& base(BlockXprType& xpr) {
    return NestedXprHelper::base(xpr.nestedExpression());
  }
  static EIGEN_DEVICE_FUNC EIGEN_ALWAYS_INLINE const BaseType& base(const BlockXprType& xpr) {
    return NestedXprHelper::base(xpr.nestedExpression());
  }
  static constexpr EIGEN_ALWAYS_INLINE Index row(const BlockXprType& xpr, Index r) {
    return xpr.startRow() + NestedXprHelper::row(xpr.nestedExpression(), r);
  }
  static constexpr EIGEN_ALWAYS_INLINE Index col(const BlockXprType& xpr, Index c) {
    return xpr.startCol() + NestedXprHelper::col(xpr.nestedExpression(), c);
  }
};

template <typename XprType, int BlockRows, int BlockCols, bool InnerPanel>
struct block_xpr_helper<const Block<XprType, BlockRows, BlockCols, InnerPanel>>
    : block_xpr_helper<Block<XprType, BlockRows, BlockCols, InnerPanel>> {};

template <typename XprType>
struct is_matrix_base_xpr : std::is_base_of<MatrixBase<remove_all_t<XprType>>, remove_all_t<XprType>> {};

template <typename XprType>
struct is_permutation_base_xpr : std::is_base_of<PermutationBase<remove_all_t<XprType>>, remove_all_t<XprType>> {};

}  // end namespace internal

/** \class ScalarBinaryOpTraits
  * \ingroup Core_Module
  *
  * \brief Determines whether the given binary operation of two numeric types is allowed and what the scalar return type
  is.
  *
  * This class permits to control the scalar return type of any binary operation performed on two different scalar types
  through (partial) template specializations.
  *
  * For instance, let \c U1, \c U2 and \c U3 be three user defined scalar types for which most operations between
  instances of \c U1 and \c U2 returns an \c U3.
  * You can let %Eigen knows that by defining:
    \code
    template<typename BinaryOp>
    struct ScalarBinaryOpTraits<U1,U2,BinaryOp> { typedef U3 ReturnType;  };
    template<typename BinaryOp>
    struct ScalarBinaryOpTraits<U2,U1,BinaryOp> { typedef U3 ReturnType;  };
    \endcode
  * You can then explicitly disable some particular operations to get more explicit error messages:
    \code
    template<>
    struct ScalarBinaryOpTraits<U1,U2,internal::scalar_max_op<U1,U2> > {};
    \endcode
  * Or customize the return type for individual operation:
    \code
    template<>
    struct ScalarBinaryOpTraits<U1,U2,internal::scalar_sum_op<U1,U2> > { typedef U1 ReturnType; };
    \endcode
  *
  * By default, the following generic combinations are supported:
  <table class="manual">
  <tr><th>ScalarA</th><th>ScalarB</th><th>BinaryOp</th><th>ReturnType</th><th>Note</th></tr>
  <tr            ><td>\c T </td><td>\c T </td><td>\c * </td><td>\c T </td><td></td></tr>
  <tr class="alt"><td>\c NumTraits<T>::Real </td><td>\c T </td><td>\c * </td><td>\c T </td><td>Only if \c
  NumTraits<T>::IsComplex </td></tr> <tr            ><td>\c T </td><td>\c NumTraits<T>::Real </td><td>\c * </td><td>\c T
  </td><td>Only if \c NumTraits<T>::IsComplex </td></tr>
  </table>
  *
  * \sa CwiseBinaryOp
  */
template <typename ScalarA, typename ScalarB, typename BinaryOp = internal::scalar_product_op<ScalarA, ScalarB>>
struct ScalarBinaryOpTraits
#ifndef EIGEN_PARSED_BY_DOXYGEN
    // for backward compatibility, use the hints given by the (deprecated) internal::scalar_product_traits class.
    : internal::scalar_product_traits<ScalarA, ScalarB>
#endif  // EIGEN_PARSED_BY_DOXYGEN
{
};

template <typename T, typename BinaryOp>
struct ScalarBinaryOpTraits<T, T, BinaryOp> {
  using ReturnType = T;
};

template <typename T, typename BinaryOp>
struct ScalarBinaryOpTraits<T, typename NumTraits<std::enable_if_t<NumTraits<T>::IsComplex, T>>::Real, BinaryOp> {
  using ReturnType = T;
};
template <typename T, typename BinaryOp>
struct ScalarBinaryOpTraits<typename NumTraits<std::enable_if_t<NumTraits<T>::IsComplex, T>>::Real, T, BinaryOp> {
  using ReturnType = T;
};

// For Matrix * Permutation
template <typename T, typename BinaryOp>
struct ScalarBinaryOpTraits<T, void, BinaryOp> {
  using ReturnType = T;
};

// For Permutation * Matrix
template <typename T, typename BinaryOp>
struct ScalarBinaryOpTraits<void, T, BinaryOp> {
  using ReturnType = T;
};

// for Permutation*Permutation
template <typename BinaryOp>
struct ScalarBinaryOpTraits<void, void, BinaryOp> {
  using ReturnType = void;
};

// We require Lhs and Rhs to have "compatible" scalar types.
// It is tempting to always allow mixing different types but remember that this is often impossible in the vectorized
// paths. So allowing mixing different types gives very unexpected errors when enabling vectorization, when the user
// tries to add together a float matrix and a double matrix.
#define EIGEN_CHECK_BINARY_COMPATIBILIY(BINOP, LHS, RHS)                               \
  EIGEN_STATIC_ASSERT(                                                                 \
      (Eigen::internal::has_ReturnType<ScalarBinaryOpTraits<LHS, RHS, BINOP>>::value), \
      YOU_MIXED_DIFFERENT_NUMERIC_TYPES__YOU_NEED_TO_USE_THE_CAST_METHOD_OF_MATRIXBASE_TO_CAST_NUMERIC_TYPES_EXPLICITLY)

}  // end namespace Eigen

#endif  // EIGEN_XPRHELPER_H
