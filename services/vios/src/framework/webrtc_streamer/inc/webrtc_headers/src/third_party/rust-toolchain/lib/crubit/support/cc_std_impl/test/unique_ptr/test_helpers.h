// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#ifndef THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_UNIQUE_PTR_TEST_HELPERS_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_UNIQUE_PTR_TEST_HELPERS_H_

#include <memory>

#include "support/annotations.h"

namespace unique_ptr_test {

struct TwoWords {
  void* ptr1;
  void* ptr2;
};

CRUBIT_MUST_BIND inline std::unique_ptr<int> create_unique_ptr() {
  return std::make_unique<int>(1);
}
CRUBIT_MUST_BIND inline void destroy_unique_ptr(std::unique_ptr<int>) {}
CRUBIT_MUST_BIND inline std::unique_ptr<char> create_unique_ptr_char() {
  return std::make_unique<char>('a');
}
CRUBIT_MUST_BIND inline std::unique_ptr<short> create_unique_ptr_short() {
  return std::make_unique<short>(static_cast<short>(1));
}
CRUBIT_MUST_BIND inline std::unique_ptr<void*> create_unique_ptr_void_ptr() {
  return std::make_unique<void*>(nullptr);
}
CRUBIT_MUST_BIND inline std::unique_ptr<TwoWords>
create_unique_ptr_two_words() {
  return std::make_unique<TwoWords>();
}

struct Base {
  virtual ~Base() = default;
  static inline int derived_destructor_count = 0;
  virtual bool is_derived() const { return false; }
};
struct Derived : public Base {
  ~Derived() override { derived_destructor_count++; }
  bool is_derived() const override { return true; }
};

CRUBIT_MUST_BIND inline std::unique_ptr<Base> create_virtual_base() {
  return std::make_unique<Derived>();
}

CRUBIT_MUST_BIND inline int get_derived_destructor_count() {
  return Base::derived_destructor_count;
}

struct CustomDelete {
  static void operator delete(void* p) {
    custom_delete_count++;
    ::operator delete(p);
  }
  static inline int custom_delete_count = 0;
};

CRUBIT_MUST_BIND inline std::unique_ptr<CustomDelete> create_custom_delete() {
  return std::make_unique<CustomDelete>();
}

CRUBIT_MUST_BIND inline int get_custom_delete_count() {
  return CustomDelete::custom_delete_count;
}

}  // namespace unique_ptr_test

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_IMPL_TEST_UNIQUE_PTR_TEST_HELPERS_H_
