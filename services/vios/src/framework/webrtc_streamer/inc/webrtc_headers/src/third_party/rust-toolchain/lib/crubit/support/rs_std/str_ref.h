// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_STR_REF_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_STR_REF_H_

#include <stdbool.h>

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <optional>

#include "absl/base/attributes.h"
#include "absl/base/nullability.h"
#include "absl/strings/string_view.h"
#include "support/annotations_internal.h"
#include "support/internal/check_no_mutable_aliasing.h"
#include "support/rs_std/internal/is_utf8.h"
#include "support/rs_std/slice_ref.h"

namespace rs_std {
namespace internal {

// A call to this function is used to trigger a compiler error when a `StrRef`
// is constructed from non-UTF8 data.
//
// It is intentionally not-`constexpr` so that calls to it from a constexpr
// context will result in a compiler error.
inline void StrRefArgumentMustBeUtf8() {}

}  // namespace internal

// `rs_std::StrRef` is a C++ representation of a pointer or reference to a
// Rust `str`. `StrRef` is like a `&str` or `*const str`. `StrRef`
// is trivially destructible, copyable, and moveable.
// `rust_builtin_type_abi_assumptions.md` documents the ABI compatibility of
// these types.
//
// It is easily convertible to and from `absl::string_view`, but otherwise
// offers no interesting functionality. Note that, unlike `absl::string_view`,
// `StrRef` can only contain valid UTF8.
//
// See <internal link> for history on the design and rationale of this API.
class CRUBIT_INTERNAL_RUST_TYPE("&str") ABSL_ATTRIBUTE_TRIVIAL_ABI
    StrRef final {
 public:
  // Returns a `StrRef` containing the given `string_view`, or nullopt if the
  // `string_view` is not valid UTF8.
  constexpr static std::optional<StrRef> FromUtf8(
      absl::string_view string_view) noexcept {
    if (!internal::IsUtf8(string_view)) {
      return std::nullopt;
    }
    return StrRef(UnsafePromiseUtf8(), string_view);
  }

  // Returns a `StrRef` containing the given `string_view` without performing
  // UTF8 validation.
  //
  // NOTE: other code relies on `StrRef` being valid UTF8, so calls to this
  // function may result in undefined behavior if `string_view` is not UTF8.
  constexpr static StrRef FromUtf8Unchecked(
      absl::string_view string_view) noexcept {
    return StrRef(UnsafePromiseUtf8(), string_view);
  }

  // consteval implicit conversion from `const char*` so that string
  // literals can be used as `StrRef` arguments while still requiring runtime
  // UTF8 validation to be explicit.
  consteval StrRef(  // NOLINT(google-explicit-constructor)
                     // Style waiver for implicit conversions granted in
                     // cl/825200658.
      const char* absl_nonnull char_ptr) noexcept
      : StrRef(absl::string_view(char_ptr)) {}

  // consteval implicit conversion from `absl::string_view`.
  consteval StrRef(  // NOLINT(google-explicit-constructor)
                     // Style waiver for implicit conversions granted in
                     // cl/825200658.
      absl::string_view string_view) noexcept
      : slice_() {
    if (!string_view.empty()) {
      // We cannot use `static_assert` because C++ does not treat arguments
      // to `consteval` functions as constants.
      // Note that this check is still guaranteed to be evaluated at compile
      // time because this function is `consteval`.
      if (!internal::IsUtf8(string_view)) {
        internal::StrRefArgumentMustBeUtf8();
      }
      slice_ = SliceRef<const char>(string_view);
    }
  }

  constexpr
  operator absl::string_view()  // NOLINT(google-explicit-constructor)
                                // Style waiver for implicit
                                // conversions granted in cl/825200658.
      const noexcept {
    return absl::string_view(slice_.data(), slice_.size());
  }

  // Creates a default `StrRef` - one that represents an empty slice.
  // To mirror slices in Rust, the data pointer is not null.
  constexpr StrRef() noexcept = default;
  constexpr StrRef(const StrRef&) = default;
  constexpr StrRef& operator=(const StrRef&) = default;

  // A mirror of Rust's `len` method.
  constexpr size_t len() const noexcept { return slice_.size(); }
  constexpr bool empty() const noexcept { return slice_.size() == 0; }

  constexpr const char* data() const noexcept { return slice_.data(); }
  constexpr size_t size() const noexcept { return slice_.size(); }

  constexpr absl::string_view to_string_view() const noexcept {
    return absl::string_view(data(), size());
  }

  // Support automatic stringification with absl::StrCat and absl::StrFormat.
  template <typename Sink>
  friend void AbslStringify(Sink& sink, const StrRef& str) {
    sink.Append(str.to_string_view());
  }

 private:
  // Private token used to select the `StrRef` constructor which does not
  // perform UTF8 validation.
  struct UnsafePromiseUtf8 {};

  // Private constructor which does not perform UTF8 validation.
  constexpr StrRef(UnsafePromiseUtf8, absl::string_view string_view) noexcept
      : slice_(string_view) {}

  SliceRef<const char> slice_;
};

// Note: Operators are defined twice multiple times in order to support
// comparison to potentially non-UTF8 `absl::string_view`s.

constexpr bool operator==(StrRef lhs, StrRef rhs) noexcept {
  return lhs.to_string_view() == rhs.to_string_view();
}

constexpr bool operator==(StrRef lhs, absl::string_view rhs) noexcept {
  return lhs.to_string_view() == rhs;
}

constexpr bool operator==(absl::string_view lhs, StrRef rhs) noexcept {
  return rhs == lhs;
}

constexpr bool operator==(StrRef lhs, const char* rhs) noexcept {
  return lhs.to_string_view() == absl::string_view(rhs);
}

constexpr bool operator==(const char* lhs, StrRef rhs) noexcept {
  return rhs == lhs;
}

constexpr auto operator<=>(StrRef lhs, StrRef rhs) noexcept {
  return lhs.to_string_view() <=> rhs.to_string_view();
}

constexpr auto operator<=>(StrRef lhs, absl::string_view rhs) noexcept {
  return lhs.to_string_view() <=> rhs;
}

constexpr auto operator<=>(absl::string_view lhs, StrRef rhs) noexcept {
  return lhs <=> rhs.to_string_view();
}

constexpr auto operator<=>(StrRef lhs, const char* rhs) noexcept {
  return lhs.to_string_view() <=> absl::string_view(rhs);
}

constexpr auto operator<=>(const char* lhs, StrRef rhs) noexcept {
  return absl::string_view(lhs) <=> rhs.to_string_view();
}

}  // namespace rs_std

namespace crubit::internal {

template <>
struct PtrLike<rs_std::StrRef> {
  static constexpr bool kIsConst = true;
  static PtrData AsPtrData(rs_std::StrRef t) {
    uintptr_t start = reinterpret_cast<uintptr_t>(t.data());
    return {
        .start = start,
        .end = start + t.size(),
    };
  }
};

}  // namespace crubit::internal

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_STR_REF_H_
