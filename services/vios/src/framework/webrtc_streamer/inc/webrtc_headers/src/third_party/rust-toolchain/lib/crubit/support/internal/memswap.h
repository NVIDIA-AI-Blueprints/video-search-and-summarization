// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_INTERNAL_MEMSWAP_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_INTERNAL_MEMSWAP_H_

#include <cstring>

#include "absl/base/optimization.h"

namespace crubit {

// Like `std::swap`, but the implementation is guaranteed to have no
// dependencies on `T`-specific code (e.g. it does *not* call into
// `T::operator=`).
//
// SAFETY REQUIREMENTS: Should only be called on `[[clang::trivial_abi]]` (aka
// `std::is_trivially_relocatable`) types.
// TODO(b/290992400): Enforce these safety requirements via
// `static_assert(absl::is_trivially_relocatable<T>::value);`.
template <typename T>
void MemSwap(T& a, T& b) {
  if (ABSL_PREDICT_FALSE(&a == &b)) {
    return;
  }

  char tmp[sizeof(T)];
  std::memcpy(tmp, &a, sizeof(T));
  std::memcpy(&a, &b, sizeof(T));
  std::memcpy(&b, tmp, sizeof(T));
}

}  // namespace crubit

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_INTERNAL_MEMSWAP_H_
