// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_VECTOR_CC_MOVABLE_TYPES_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_VECTOR_CC_MOVABLE_TYPES_H_

namespace crubit_test {

class SimpleRustMovableType {
 public:
  explicit SimpleRustMovableType(int x) : x_(x) {}
  int x() const { return x_; }

 private:
  int x_;
};
}  // namespace crubit_test

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_VECTOR_CC_MOVABLE_TYPES_H_
