// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_STRING_VIEW_STRING_VIEW_CC_APIS_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_STRING_VIEW_STRING_VIEW_CC_APIS_H_

#include <string_view>
namespace crubit_string_view {

inline std::string_view GetHelloWorld() { return "Hello, world!"; }

inline std::string_view GetDefault() { return std::string_view(); }

inline std::string_view GetInvalidUTF() { return "\x80\x81"; }

inline std::string_view Identity(std::string_view sv) { return sv; }

}  // namespace crubit_string_view

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_STRING_VIEW_STRING_VIEW_CC_APIS_H_
