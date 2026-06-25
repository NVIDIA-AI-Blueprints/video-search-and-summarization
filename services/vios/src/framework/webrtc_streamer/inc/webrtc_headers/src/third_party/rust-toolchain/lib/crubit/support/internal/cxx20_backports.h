// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef CRUBIT_RS_BINDINGS_FROM_CC_SUPPORT_CXX20_BACKPORTS_H_
#define CRUBIT_RS_BINDINGS_FROM_CC_SUPPORT_CXX20_BACKPORTS_H_

#if __cplusplus > 201703L
#include <memory>
#include <type_traits>
#else
#include <utility>
#endif

namespace crubit {

#if __cplusplus > 201703L

using std::construct_at;
using std::type_identity_t;

#else

namespace detail {

template <class T>
struct type_identity {
  using type = T;
};

}  // namespace detail

template <class T, class... Args>
constexpr T* construct_at(T* p, Args&&... args) {
  return ::new (const_cast<void*>(static_cast<const volatile void*>(p)))
      T(std::forward<Args>(args)...);
}

template <class T>
using type_identity_t = typename detail::type_identity<T>::type;

#endif

}  // namespace crubit

#endif  // CRUBIT_RS_BINDINGS_FROM_CC_SUPPORT_CXX20_BACKPORTS_H_
