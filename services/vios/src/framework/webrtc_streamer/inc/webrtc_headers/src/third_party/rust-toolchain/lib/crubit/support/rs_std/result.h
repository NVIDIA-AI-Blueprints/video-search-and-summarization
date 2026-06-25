// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#ifndef THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_RESULT_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_RESULT_H_

#include <initializer_list>
#include <memory>
#include <utility>
#if __cplusplus >= 202302L
#include <expected>
#endif

namespace rs_std {

template <typename T, typename E>
struct Result final {
  static_assert(false,
                "This type should only be used via a generated specialization");
};

#if __cplusplus >= 202302L
using std::unexpect;
using std::unexpect_t;
using std::unexpected;
#else
struct unexpect_t {
  explicit unexpect_t() = default;
};
inline constexpr unexpect_t unexpect{};

template <typename E>
class unexpected final {
 public:
  constexpr unexpected(const unexpected& err) = default;
  constexpr unexpected(unexpected&& err) = default;

  constexpr unexpected& operator=(const unexpected& err) = default;
  constexpr unexpected& operator=(unexpected&& err) = default;

  constexpr ~unexpected() = default;

  constexpr explicit unexpected(E&& err) : err_(std::move(err)) {};

  template <typename... Args>
    requires(std::is_constructible_v<E, Args...>)
  constexpr explicit unexpected(std::in_place_t, Args&&... args) {
    std::construct_at(&err_, std::forward<Args>(args)...);
  };

  template <class U, class... Args>
    requires(std::is_constructible_v<E, std::initializer_list<U>&, Args...>)
  constexpr explicit unexpected(std::in_place_t, std::initializer_list<U> il,
                                Args&&... args) {
    std::construct_at(&err_, il, std::forward<Args>(args)...);
  };

  constexpr const E& error() const& noexcept { return err_; }
  constexpr E& error() & noexcept { return err_; }
  constexpr const E&& error() const&& noexcept { return std::move(err_); }
  constexpr E&& error() && noexcept { return std::move(err_); }

 private:
  E err_;
};
#endif

}  // namespace rs_std

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_RESULT_H_
