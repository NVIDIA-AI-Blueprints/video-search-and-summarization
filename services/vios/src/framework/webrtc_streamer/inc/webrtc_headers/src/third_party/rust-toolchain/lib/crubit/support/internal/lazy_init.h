// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_INTERNAL_LAZY_INIT_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_INTERNAL_LAZY_INIT_H_
#include <memory>

namespace crubit {

template <typename T>
union LazyInit {
  constexpr LazyInit() {}
  ~LazyInit() { std::destroy_at(&this->val); }
  T val;
};

}  // namespace crubit

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_INTERNAL_LAZY_INIT_H_
