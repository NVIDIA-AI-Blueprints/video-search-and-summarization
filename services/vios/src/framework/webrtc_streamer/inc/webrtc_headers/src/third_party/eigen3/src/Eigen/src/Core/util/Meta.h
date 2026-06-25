// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2008-2015 Gael Guennebaud <gael.guennebaud@inria.fr>
// Copyright (C) 2006-2008 Benoit Jacob <jacob.benoit.1@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_META_H
#define EIGEN_META_H

// IWYU pragma: private
#include "../InternalHeaderCheck.h"

#if defined(EIGEN_GPU_COMPILE_PHASE)

#include <cfloat>

#if defined(EIGEN_CUDA_ARCH)
#include <math_constants.h>
#endif

#if defined(EIGEN_HIP_DEVICE_COMPILE)
#include "Eigen/src/Core/arch/HIP/hcc/math_constants.h"
#endif

#endif

// Define portable (u)int{32,64} types
#include <cstdint>

namespace Eigen {
namespace numext {
typedef std::uint8_t uint8_t;
typedef std::int8_t int8_t;
typedef std::uint16_t uint16_t;
typedef std::int16_t int16_t;
typedef std::uint32_t uint32_t;
typedef std::int32_t int32_t;
typedef std::uint64_t uint64_t;
typedef std::int64_t int64_t;

template <size_t Size>
struct get_integer_by_size {
  typedef void signed_type;
  typedef void unsigned_type;
};
template <>
struct get_integer_by_size<1> {
  typedef int8_t signed_type;
  typedef uint8_t unsigned_type;
};
template <>
struct get_integer_by_size<2> {
  typedef int16_t signed_type;
  typedef uint16_t unsigned_type;
};
template <>
struct get_integer_by_size<4> {
  typedef int32_t signed_type;
  typedef uint32_t unsigned_type;
};
template <>
struct get_integer_by_size<8> {
  typedef int64_t signed_type;
  typedef uint64_t unsigned_type;
};
}  // namespace numext
}  // namespace Eigen

namespace Eigen {

typedef EIGEN_DEFAULT_DENSE_INDEX_TYPE DenseIndex;

/**
 * \brief The Index type as used for the API.
 * \details To change this, \c \#define the preprocessor symbol \c EIGEN_DEFAULT_DENSE_INDEX_TYPE.
 * \sa \blank \ref TopicPreprocessorDirectives, StorageIndex.
 */
typedef EIGEN_DEFAULT_DENSE_INDEX_TYPE Index;

namespace internal {

/** \internal
 * \file Meta.h
 * This file contains generic metaprogramming classes which are not specifically related to Eigen.
 * \note In case you wonder, yes we're aware that Boost already provides all these features,
 * we however don't want to add a dependency to Boost.
 */

template <bool Condition>
using bool_constant = std::integral_constant<bool, Condition>;

// Deprecated compatibility aliases. Third-party libraries rely on these, but new code should use std:: directly.
using std::conditional;
using std::false_type;
using std::is_const;
using std::is_convertible;
using std::is_integral;
using std::is_same;
using std::is_void;
using std::make_unsigned;
using std::remove_const;
using std::remove_pointer;
using std::remove_reference;
using std::true_type;

template <typename T>
struct remove_all {
  typedef T type;
};
template <typename T>
struct remove_all<const T> {
  typedef typename remove_all<T>::type type;
};
template <typename T>
struct remove_all<T const&> {
  typedef typename remove_all<T>::type type;
};
template <typename T>
struct remove_all<T&> {
  typedef typename remove_all<T>::type type;
};
template <typename T>
struct remove_all<T const*> {
  typedef typename remove_all<T>::type type;
};
template <typename T>
struct remove_all<T*> {
  typedef typename remove_all<T>::type type;
};

template <typename T>
using remove_all_t = typename remove_all<T>::type;

// Eigen's is_arithmetic is similar to std::is_arithmetic but can be specialized
// for SIMD packet types and other Eigen-specific types. The primary template
// delegates to std::is_arithmetic for fundamental types.
template <typename T>
struct is_arithmetic : std::is_arithmetic<T> {};
// GPU devices treat `long double` as `double`.
#ifdef EIGEN_GPU_COMPILE_PHASE
template <>
struct is_arithmetic<long double> : std::false_type {};
#endif

/** \internal
 * Implementation of std::void_t for SFINAE.
 *
 * Pre C++17:
 * Custom implementation.
 *
 * Post C++17: Uses std::void_t
 */
#if EIGEN_COMP_CXXVER >= 17 && defined(__cpp_lib_void_t) && __cpp_lib_void_t >= 201411L
using std::void_t;
#else
template <typename...>
using void_t = void;
#endif

template <typename T>
struct add_const_on_value_type {
  typedef const T type;
};
template <typename T>
struct add_const_on_value_type<T&> {
  typedef T const& type;
};
template <typename T>
struct add_const_on_value_type<T*> {
  typedef T const* type;
};
template <typename T>
struct add_const_on_value_type<T* const> {
  typedef T const* const type;
};
template <typename T>
struct add_const_on_value_type<T const* const> {
  typedef T const* const type;
};

template <typename T>
using add_const_on_value_type_t = typename add_const_on_value_type<T>::type;

/** \internal
 * Provides access to the number of elements in the object of as a compile-time constant expression.
 * It "returns" Eigen::Dynamic if the size cannot be resolved at compile-time (default).
 *
 * Similar to std::tuple_size, but more general.
 *
 * It currently supports:
 *  - any types T defining T::SizeAtCompileTime
 *  - plain C arrays as T[N]
 *  - std::array
 *  - some internal types such as SingleRange and AllRange
 *
 * The second template parameter eases SFINAE-based specializations.
 */
template <typename T, typename EnableIf = void>
struct array_size {
  static constexpr Index value = Dynamic;
};

template <typename T>
struct array_size<T, std::enable_if_t<((T::SizeAtCompileTime & 0) == 0)>> {
  static constexpr Index value = T::SizeAtCompileTime;
};

template <typename T, int N>
struct array_size<const T (&)[N]> {
  static constexpr Index value = N;
};
template <typename T, int N>
struct array_size<T (&)[N]> {
  static constexpr Index value = N;
};

template <typename T, std::size_t N>
struct array_size<const std::array<T, N>> {
  static constexpr Index value = N;
};
template <typename T, std::size_t N>
struct array_size<std::array<T, N>> {
  static constexpr Index value = N;
};

/** \internal
 * Analogue of the std::ssize free function.
 * It returns the signed size of the container or view \a x of type \c T
 *
 * It currently supports:
 *  - any types T defining a member T::size() const
 *  - plain C arrays as T[N]
 *
 * For C++20, this function just forwards to `std::ssize`, or any ADL discoverable `ssize` function.
 */
#if EIGEN_COMP_CXXVER >= 20 && defined(__cpp_lib_ssize) && __cpp_lib_ssize >= 201902L

template <typename T>
constexpr auto index_list_size(T&& x) {
  using std::ssize;
  return ssize(std::forward<T>(x));
}

#else

template <typename T>
constexpr auto index_list_size(const T& x) {
  using R = std::common_type_t<std::ptrdiff_t, std::make_signed_t<decltype(x.size())>>;
  return static_cast<R>(x.size());
}

template <typename T, std::ptrdiff_t N>
constexpr std::ptrdiff_t index_list_size(const T (&)[N]) {
  return N;
}
#endif

/** \internal
 * Convenient struct to get the result type of a nullary, unary, binary, or
 * ternary functor.
 *
 * Pre C++17:
 * This uses std::result_of. However, note the `type` member removes
 * const and converts references/pointers to their corresponding value type.
 *
 * Post C++17: Uses std::invoke_result
 */
#if EIGEN_HAS_STD_INVOKE_RESULT
template <typename T>
struct result_of;

template <typename F, typename... ArgTypes>
struct result_of<F(ArgTypes...)> {
  typedef std::invoke_result_t<F, ArgTypes...> type1;
  typedef remove_all_t<type1> type;
};
#else
template <typename T>
struct result_of {
  typedef std::result_of_t<T> type1;
  typedef remove_all_t<type1> type;
};
#endif

// Reduces a sequence of bools to true if all are true, false otherwise.
template <bool... values>
using reduce_all =
    std::is_same<std::integer_sequence<bool, values..., true>, std::integer_sequence<bool, true, values...>>;

// Check whether T::ReturnType does exist
template <typename T, typename EnableIf = void>
struct has_ReturnType : std::false_type {};

template <typename T>
struct has_ReturnType<T, void_t<typename T::ReturnType>> : std::true_type {};

template <typename T, typename IndexType = Index, typename EnableIf = void>
struct has_nullary_operator : std::false_type {};

template <typename T, typename IndexType>
struct has_nullary_operator<T, IndexType, std::enable_if_t<(sizeof(decltype(std::declval<const T&>()())) > 0)>>
    : std::true_type {};

template <typename T, typename IndexType = Index, typename EnableIf = void>
struct has_unary_operator : std::false_type {};

template <typename T, typename IndexType>
struct has_unary_operator<T, IndexType,
                          std::enable_if_t<(sizeof(decltype(std::declval<const T&>()(IndexType(0)))) > 0)>>
    : std::true_type {};

template <typename T, typename IndexType = Index, typename EnableIf = void>
struct has_binary_operator : std::false_type {};

template <typename T, typename IndexType>
struct has_binary_operator<
    T, IndexType, std::enable_if_t<(sizeof(decltype(std::declval<const T&>()(IndexType(0), IndexType(0)))) > 0)>>
    : std::true_type {};

/** \internal Computes the least common multiple of two positive integer A and B
 * at compile-time.
 */
template <int A, int B, int K = 1, bool Done = ((A * K) % B) == 0, bool Big = (A >= B)>
struct meta_least_common_multiple : std::integral_constant<int, meta_least_common_multiple<A, B, K + 1>::value> {};
template <int A, int B, int K, bool Done>
struct meta_least_common_multiple<A, B, K, Done, false>
    : std::integral_constant<int, meta_least_common_multiple<B, A, K>::value> {};
template <int A, int B, int K>
struct meta_least_common_multiple<A, B, K, true, true> : std::integral_constant<int, A * K> {};

/** \internal determines whether the product of two numeric types is allowed and what the return type is */
template <typename T, typename U>
struct scalar_product_traits {
  enum { Defined = 0 };
};

/** \internal Obtains a POD type suitable to use as storage for an object of a size
 * of at most Len bytes, aligned as specified by \c Align.
 */
template <unsigned Len, unsigned Align>
struct aligned_storage {
  struct type {
    EIGEN_ALIGN_TO_BOUNDARY(Align) unsigned char data[Len];
  };
};

}  // end namespace internal

template <typename T>
struct NumTraits;

namespace numext {

#if defined(EIGEN_GPU_COMPILE_PHASE)
template <typename T>
EIGEN_DEVICE_FUNC constexpr void swap(T& a, T& b) {
  T tmp = b;
  b = a;
  a = tmp;
}
#else
template <typename T>
constexpr EIGEN_STRONG_INLINE void swap(T& a, T& b) {
  std::swap(a, b);
}
#endif

using std::numeric_limits;

// Handle integer comparisons of different signedness.
template <typename X, typename Y, bool XIsInteger = NumTraits<X>::IsInteger, bool XIsSigned = NumTraits<X>::IsSigned,
          bool YIsInteger = NumTraits<Y>::IsInteger, bool YIsSigned = NumTraits<Y>::IsSigned>
struct equal_strict_impl {
  static constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool run(const X& x, const Y& y) { return x == y; }
};
template <typename X, typename Y>
struct equal_strict_impl<X, Y, true, false, true, true> {
  // X is an unsigned integer
  // Y is a signed integer
  // if Y is non-negative, it may be represented exactly as its unsigned counterpart.
  using UnsignedY = std::make_unsigned_t<Y>;
  static constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool run(const X& x, const Y& y) {
    return y < Y(0) ? false : (x == static_cast<UnsignedY>(y));
  }
};
template <typename X, typename Y>
struct equal_strict_impl<X, Y, true, true, true, false> {
  // X is a signed integer
  // Y is an unsigned integer
  static constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool run(const X& x, const Y& y) {
    return equal_strict_impl<Y, X>::run(y, x);
  }
};

// The aim of the following functions is to bypass -Wfloat-equal warnings
// when we really want a strict equality comparison on floating points.
template <typename X, typename Y>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool equal_strict(const X& x, const Y& y) {
  return equal_strict_impl<X, Y>::run(x, y);
}

#if !defined(EIGEN_GPU_COMPILE_PHASE) || (!defined(EIGEN_CUDA_ARCH) && defined(EIGEN_CONSTEXPR_ARE_DEVICE_FUNC))
template <>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool equal_strict(const float& x, const float& y) {
  return std::equal_to<float>()(x, y);
}

template <>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool equal_strict(const double& x, const double& y) {
  return std::equal_to<double>()(x, y);
}
#endif

/**
 * \internal Performs an exact comparison of x to zero, e.g. to decide whether a term can be ignored.
 * Use this to to bypass -Wfloat-equal warnings when exact zero is what needs to be tested.
 */
template <typename X>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool is_exactly_zero(const X& x) {
  return equal_strict(x, typename NumTraits<X>::Literal{0});
}

/**
 * \internal Performs an exact comparison of x to one, e.g. to decide whether a factor needs to be multiplied.
 * Use this to to bypass -Wfloat-equal warnings when exact one is what needs to be tested.
 */
template <typename X>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool is_exactly_one(const X& x) {
  return equal_strict(x, typename NumTraits<X>::Literal{1});
}

template <typename X, typename Y>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool not_equal_strict(const X& x, const Y& y) {
  return !equal_strict_impl<X, Y>::run(x, y);
}

#if !defined(EIGEN_GPU_COMPILE_PHASE) || (!defined(EIGEN_CUDA_ARCH) && defined(EIGEN_CONSTEXPR_ARE_DEVICE_FUNC))
template <>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool not_equal_strict(const float& x, const float& y) {
  return std::not_equal_to<float>()(x, y);
}

template <>
constexpr EIGEN_STRONG_INLINE EIGEN_DEVICE_FUNC bool not_equal_strict(const double& x, const double& y) {
  return std::not_equal_to<double>()(x, y);
}
#endif

}  // end namespace numext

namespace internal {

template <typename Scalar>
struct is_identically_zero_impl {
  static constexpr bool run(const Scalar& s) { return numext::is_exactly_zero(s); }
};

template <typename Scalar>
constexpr EIGEN_STRONG_INLINE bool is_identically_zero(const Scalar& s) {
  return is_identically_zero_impl<Scalar>::run(s);
}

// true if T can be considered as an integral index (i.e., an integral type or enum)
template <typename T>
struct is_valid_index_type : std::integral_constant<bool, std::is_integral<T>::value || std::is_enum<T>::value> {};

template <typename A, typename B>
constexpr void plain_enum_asserts(A, B) {
  static_assert(is_valid_index_type<A>::value, "Argument a must be an integer or enum");
  static_assert(is_valid_index_type<B>::value, "Argument b must be an integer or enum");
}

/// \internal Gets the minimum of two values which may be integers or enums
template <typename A, typename B>
constexpr int plain_enum_min(A a, B b) {
  plain_enum_asserts(a, b);
  return ((int)a <= (int)b) ? (int)a : (int)b;
}

/// \internal Gets the maximum of two values which may be integers or enums
template <typename A, typename B>
constexpr int plain_enum_max(A a, B b) {
  plain_enum_asserts(a, b);
  return ((int)a >= (int)b) ? (int)a : (int)b;
}

/**
 * \internal
 *  `min_size_prefer_dynamic` gives the min between compile-time sizes. 0 has absolute priority, followed by 1,
 *  followed by Dynamic, followed by other finite values. The reason for giving Dynamic the priority over
 *  finite values is that min(3, Dynamic) should be Dynamic, since that could be anything between 0 and 3.
 */
template <typename A, typename B>
constexpr int min_size_prefer_dynamic(A a, B b) {
  plain_enum_asserts(a, b);
  if ((int)a == 0 || (int)b == 0) return 0;
  if ((int)a == 1 || (int)b == 1) return 1;
  if ((int)a == Dynamic || (int)b == Dynamic) return Dynamic;
  return plain_enum_min(a, b);
}

/**
 * \internal
 *  min_size_prefer_fixed is a variant of `min_size_prefer_dynamic` comparing MaxSizes. The difference is that finite
 * values now have priority over Dynamic, so that min(3, Dynamic) gives 3. Indeed, whatever the actual value is (between
 * 0 and 3), it is not more than 3.
 */
template <typename A, typename B>
constexpr int min_size_prefer_fixed(A a, B b) {
  plain_enum_asserts(a, b);
  if ((int)a == 0 || (int)b == 0) return 0;
  if ((int)a == 1 || (int)b == 1) return 1;
  if ((int)a == Dynamic && (int)b == Dynamic) return Dynamic;
  if ((int)a == Dynamic) return (int)b;
  if ((int)b == Dynamic) return (int)a;
  return plain_enum_min(a, b);
}

/// \internal see `min_size_prefer_fixed`. No need for a separate variant for MaxSizes here.
template <typename A, typename B>
constexpr int max_size_prefer_dynamic(A a, B b) {
  plain_enum_asserts(a, b);
  if ((int)a == Dynamic || (int)b == Dynamic) return Dynamic;
  return plain_enum_max(a, b);
}

template <typename A, typename B>
inline constexpr int size_prefer_fixed(A a, B b) {
  plain_enum_asserts(a, b);
  return int(a) == Dynamic ? int(b) : int(a);
}

template <typename A, typename B>
inline constexpr bool enum_eq_not_dynamic(A a, B b) {
  plain_enum_asserts(a, b);
  if ((int)a == Dynamic || (int)b == Dynamic) return false;
  return (int)a == (int)b;
}

template <typename A, typename B>
constexpr bool enum_lt_not_dynamic(A a, B b) {
  plain_enum_asserts(a, b);
  if ((int)a == Dynamic || (int)b == Dynamic) return false;
  return (int)a < (int)b;
}

template <typename A, typename B>
constexpr bool enum_ge_not_dynamic(A a, B b) {
  plain_enum_asserts(a, b);
  if ((int)a == Dynamic || (int)b == Dynamic) return false;
  return (int)a >= (int)b;
}

/// \internal Calculate logical XOR at compile time
constexpr bool logical_xor(bool a, bool b) { return a != b; }

/// \internal Calculate logical IMPLIES at compile time
constexpr bool check_implication(bool a, bool b) { return !a || b; }

/// \internal Provide fallback for std::is_constant_evaluated for pre-C++20.
#if EIGEN_COMP_CXXVER >= 20 && defined(__cpp_lib_is_constant_evaluated) && __cpp_lib_is_constant_evaluated >= 201811L
using std::is_constant_evaluated;
#else
constexpr bool is_constant_evaluated() { return false; }
#endif

template <typename Scalar>
using make_complex_t = std::conditional_t<NumTraits<Scalar>::IsComplex, Scalar, std::complex<Scalar>>;

}  // end namespace internal

}  // end namespace Eigen

#endif  // EIGEN_META_H
