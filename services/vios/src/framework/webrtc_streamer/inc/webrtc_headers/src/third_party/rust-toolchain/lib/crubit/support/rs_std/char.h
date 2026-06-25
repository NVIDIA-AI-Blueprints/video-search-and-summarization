// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef CRUBIT_SUPPORT_RUST_STD_CHAR_H_
#define CRUBIT_SUPPORT_RUST_STD_CHAR_H_

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <type_traits>

#include "absl/base/optimization.h"
#include "absl/types/span.h"
#include "support/annotations.h"
#include "support/rs_std/str_ref.h"

namespace rs_std {
namespace internal {

// A call to this function is used to trigger a compiler error when a `char_`
// is constructed from a `uint32_t` that is not a valid Unicode code point.
//
// It is intentionally not-`constexpr` so that calls to it from a constexpr
// context will result in a compiler error.
inline void CharArgumentMustBeUnicodeCodePoint() {}

}  // namespace internal

// The names like `from_u32` and `from_u32_unchecked` mimic Rust names and
// therefore don't need to be named the same as ordinary C++ names. See:
// https://google.github.io/styleguide/cppguide.html#Exceptions_to_Naming_Rules

// `rs_std::char_` is a C++ representation of the `char` type from Rust.
// `rust_builtin_type_abi_assumptions.md` documents the ABI compatibility of
// these types.
class CRUBIT_INTERNAL_RUST_TYPE("char") CRUBIT_INTERNAL_SAME_ABI char_ final {
 public:
  // Creates a default `char_` - one that represents ASCII NUL character.
  //
  // Providing the default constructor helps to ensure that the `value_` always
  // effectively stores a C++ equivalent of a well-defined Rust's `u32` value
  // (and never has a `MaybeUninit<u32>` value).  See also the P2723R1 proposal
  // for C++ which argues that zero-initialization may mitigate 10% of exploits.
  constexpr char_() noexcept = default;

  // Constant-time implicit constructor which converts a character
  // literal into an `rs_std::char_`. This function performs compile-time
  // validation that the argument is a valid Unicode scalar value.
  //
  // Note: this constructor is templated in order to ensure that implicit
  // conversions from `int` values are not applied.
  template <typename Char, typename CharNoCv = std::remove_cvref_t<Char>,
            typename = std::enable_if_t<std::is_same_v<CharNoCv, char> ||
                                        std::is_same_v<CharNoCv, char8_t> ||
                                        std::is_same_v<CharNoCv, char16_t> ||
                                        std::is_same_v<CharNoCv, char32_t>>>
  consteval char_(  // NOLINT(google-explicit-constructor)
                    // Style waiver for implicit conversions granted in
                    // cl/825200658.
      Char c) noexcept
      : value_(c) {
    if (!IsValidCodePoint(c)) {
      internal::CharArgumentMustBeUnicodeCodePoint();
    }
  }

  // Converts a `uint32_t` into a `rs_std::char_`.
  //
  // Note that not all valid `uint32_t`s are valid `rs_std::char_`s.
  // `from_u32` will return `std::nullopt` if the input is not a valid value for
  // a `rs_std::char_`.
  //
  // See also
  // https://doc.rust-lang.org/reference/behavior-considered-undefined.html
  // which documents that undefined behavior may result in presence of "A value
  // in a char which is a surrogate or above char::MAX."
  //
  // This function mimics Rust's `char::from_u32`:
  // https://doc.rust-lang.org/std/primitive.char.html#method.from_u32
  static constexpr std::optional<char_> from_u32(char32_t c) noexcept {
    if (!IsValidCodePoint(c)) {
      return std::nullopt;
    }

    return from_u32_unchecked(c);
  }

  constexpr char_(const char_&) = default;
  constexpr char_& operator=(const char_&) = default;

  explicit constexpr operator std::uint32_t() const noexcept { return value_; }

  friend constexpr bool operator==(char_ lhs, char_ rhs) noexcept;
  friend constexpr auto operator<=>(char_ lhs, char_ rhs) noexcept;

  // The highest valid code point a char can have, '\u{10FFFF}'.
  //
  // This constant mimics Rust's `char::MAX`:
  // https://doc.rust-lang.org/std/primitive.char.html#associatedconstant.MAX
  static const char_ MAX;

  // Encodes the UTF-8 representation of this `char_` into the given
  // `output_buffer`.
  //
  // The `output_buffer` must be large enough to hold the UTF-8 representation
  // of this `char_`, which may be 1, 2, 3, or 4 bytes.
  //
  // This function mimics Rust's `char::encode_utf8`:
  // https://doc.rust-lang.org/std/primitive.char.html#method.encode_utf8
  StrRef encode_utf8(absl::Span<uint8_t> output_buffer) const;

  // Returns the number of bytes required to UTF-8-encode this `char_`.
  //
  // This function mimics Rust's `char::len_utf8`:
  // https://doc.rust-lang.org/std/primitive.char.html#method.len_utf8
  constexpr size_t len_utf8() const noexcept {
    if (value_ < 0x80) {
      return 1;
    } else if (value_ < 0x800) {
      return 2;
    } else if (value_ < 0x10000) {
      return 3;
    } else {
      return 4;
    }
  }

 private:
  struct UnsafePromiseUnicode {};

  // This function mimics Rust's `char::from_u32_unchecked`:
  // https://doc.rust-lang.org/std/primitive.char.html#method.from_u32_unchecked
  static constexpr char_ from_u32_unchecked(uint32_t value) noexcept {
    return char_(value, UnsafePromiseUnicode{});
  }

  static inline constexpr bool IsValidCodePoint(uint32_t c) noexcept {
    // TODO(lukasza): Consider using slightly more efficient checks similarly
    // to how `char_try_from_u32` is implemented in Rust standard library.
    if (ABSL_PREDICT_FALSE(c > 0x10ffff)) {
      // Value greater than Rust's `char::MAX`:
      // https://doc.rust-lang.org/std/primitive.char.html#associatedconstant.MAX
      return false;
    }
    if (ABSL_PREDICT_FALSE(c >= 0xd800 && c <= 0xdfff)) {
      // Surrogate characters.
      return false;
    }
    return true;
  }

  explicit constexpr char_(std::uint32_t value, UnsafePromiseUnicode)
      : value_(value) {}

  // See "layout tests" comments in `char_test.cc` for explanation why
  // `char32_t` is not used.
  std::uint32_t value_ = '\0';
};

// Definition of `char_::MAX` - it can't be defined and declared within the
// `class` definition, because before `char_` is fully defined the compiler
// complains that `constexpr` variable cannot have non-literal type
// 'const char_'.
constexpr char_ char_::MAX = char_::from_u32_unchecked(0x10ffff);

CRUBIT_DO_NOT_BIND constexpr bool operator==(char_ lhs, char_ rhs) noexcept {
  return lhs.value_ == rhs.value_;
}

CRUBIT_DO_NOT_BIND constexpr auto operator<=>(char_ lhs, char_ rhs) noexcept {
  return lhs.value_ <=> rhs.value_;
}

// Support automatic stringification with absl::StrCat and absl::StrFormat.
// This will append a UTF-8-encoded representation of `char_` to the `sink`.
//
// Note: this interface does not actually depend on absl. However, we may
// have difficulty if we ever want to upstream this interface to Rust itself.
// If that happens, we can consider adding this type as an optional
// dependency of absl, or otherwise extend the absl stringification mechanism
// in order to understand this type.
template <typename Sink>
void AbslStringify(Sink& sink, const char_& c) {
  std::array<uint8_t, 4> buffer;
  StrRef str = c.encode_utf8(absl::MakeSpan(buffer));
  sink.Append(str.to_string_view());
}

}  // namespace rs_std

#endif  // CRUBIT_SUPPORT_RS_STD_CHAR_H_
