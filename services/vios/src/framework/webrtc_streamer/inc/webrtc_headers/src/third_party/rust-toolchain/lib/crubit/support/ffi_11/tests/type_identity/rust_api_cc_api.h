// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

// Automatically @generated C++ bindings for the following Rust crate:
// rust_api_golden
// Features: fmt, supported, types

// clang-format off
#ifndef THIRD_PARTY_CRUBIT_SUPPORT_FFI_11_TESTS_TYPE_IDENTITY_RUST_API_GOLDEN
#define THIRD_PARTY_CRUBIT_SUPPORT_FFI_11_TESTS_TYPE_IDENTITY_RUST_API_GOLDEN

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wreturn-type-c-linkage"
#pragma clang diagnostic ignored "-Wunused-private-field"
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <cstdint>

#include "support/ffi_11/ffi_11.h"

namespace rust_api {

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=5
decltype(char(0)) c_char();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=60
decltype(char16_t(0)) c_char16_t();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=64
decltype(char32_t(0)) c_char32_t();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=56
decltype(char8_t(0)) c_char8_t();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=44
double c_double();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=41
float c_float();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=21
::std::int32_t c_int();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=27
::std::int64_t c_long();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=34
long long c_longlong();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=48
decltype(nullptr) c_nullptr_t();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=11
::std::int8_t c_schar();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=15
::std::int16_t c_short();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=8
::std::uint8_t c_uchar();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=24
::std::uint32_t c_uint();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=30
::std::uint64_t c_ulong();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=37
unsigned long long c_ulonglong();

// Generated from:
// support/ffi_11/tests/type_identity/rust_api.rs;l=18
::std::uint16_t c_ushort();

// Error generating bindings for `rust_api_golden::c_wchar_t` defined at
// support/ffi_11/tests/type_identity/rust_api.rs;l=52:
// Error formatting function return type `ffi_11::wchar_type::c_wchar_t`: Failed
// to format type for the definition of `ffi_11::wchar_type::c_wchar_t`: Error
// formatting the fully-qualified C++ name of `c_wchar_t`: `wchar_t` is a C++
// reserved keyword and can't be used as a C++ identifier

namespace __crubit_internal {
extern "C" decltype(char(0)) __crubit_thunk_c_uchar();
}
inline decltype(char(0)) c_char() {
  return __crubit_internal::__crubit_thunk_c_uchar();
}

namespace __crubit_internal {
extern "C" decltype(char16_t(0)) __crubit_thunk_c_uchar16_ut();
}
inline decltype(char16_t(0)) c_char16_t() {
  return __crubit_internal::__crubit_thunk_c_uchar16_ut();
}

namespace __crubit_internal {
extern "C" decltype(char32_t(0)) __crubit_thunk_c_uchar32_ut();
}
inline decltype(char32_t(0)) c_char32_t() {
  return __crubit_internal::__crubit_thunk_c_uchar32_ut();
}

namespace __crubit_internal {
extern "C" decltype(char8_t(0)) __crubit_thunk_c_uchar8_ut();
}
inline decltype(char8_t(0)) c_char8_t() {
  return __crubit_internal::__crubit_thunk_c_uchar8_ut();
}

namespace __crubit_internal {
extern "C" double __crubit_thunk_c_udouble();
}
inline double c_double() {
  return __crubit_internal::__crubit_thunk_c_udouble();
}

namespace __crubit_internal {
extern "C" float __crubit_thunk_c_ufloat();
}
inline float c_float() { return __crubit_internal::__crubit_thunk_c_ufloat(); }

namespace __crubit_internal {
extern "C" ::std::int32_t __crubit_thunk_c_uint();
}
inline ::std::int32_t c_int() {
  return __crubit_internal::__crubit_thunk_c_uint();
}

namespace __crubit_internal {
extern "C" ::std::int64_t __crubit_thunk_c_ulong();
}
inline ::std::int64_t c_long() {
  return __crubit_internal::__crubit_thunk_c_ulong();
}

namespace __crubit_internal {
extern "C" long long __crubit_thunk_c_ulonglong();
}
inline long long c_longlong() {
  return __crubit_internal::__crubit_thunk_c_ulonglong();
}

namespace __crubit_internal {
extern "C" decltype(nullptr) __crubit_thunk_c_unullptr_ut();
}
inline decltype(nullptr) c_nullptr_t() {
  return __crubit_internal::__crubit_thunk_c_unullptr_ut();
}

namespace __crubit_internal {
extern "C" ::std::int8_t __crubit_thunk_c_uschar();
}
inline ::std::int8_t c_schar() {
  return __crubit_internal::__crubit_thunk_c_uschar();
}

namespace __crubit_internal {
extern "C" ::std::int16_t __crubit_thunk_c_ushort();
}
inline ::std::int16_t c_short() {
  return __crubit_internal::__crubit_thunk_c_ushort();
}

namespace __crubit_internal {
extern "C" ::std::uint8_t __crubit_thunk_c_uuchar();
}
inline ::std::uint8_t c_uchar() {
  return __crubit_internal::__crubit_thunk_c_uuchar();
}

namespace __crubit_internal {
extern "C" ::std::uint32_t __crubit_thunk_c_uuint();
}
inline ::std::uint32_t c_uint() {
  return __crubit_internal::__crubit_thunk_c_uuint();
}

namespace __crubit_internal {
extern "C" ::std::uint64_t __crubit_thunk_c_uulong();
}
inline ::std::uint64_t c_ulong() {
  return __crubit_internal::__crubit_thunk_c_uulong();
}

namespace __crubit_internal {
extern "C" unsigned long long __crubit_thunk_c_uulonglong();
}
inline unsigned long long c_ulonglong() {
  return __crubit_internal::__crubit_thunk_c_uulonglong();
}

namespace __crubit_internal {
extern "C" ::std::uint16_t __crubit_thunk_c_uushort();
}
inline ::std::uint16_t c_ushort() {
  return __crubit_internal::__crubit_thunk_c_uushort();
}

}  // namespace rust_api

#pragma clang diagnostic pop
#endif  // THIRD_PARTY_CRUBIT_SUPPORT_FFI_11_TESTS_TYPE_IDENTITY_RUST_API_GOLDEN
