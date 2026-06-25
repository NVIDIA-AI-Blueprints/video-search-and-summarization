// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#ifndef THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_OPTIONAL_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_OPTIONAL_H_

namespace rs_std {

template <typename T>
struct Option final {
  static_assert(false,
                "This type should only be used via a generated specialization");
};

}  // namespace rs_std

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_OPTIONAL_H_
