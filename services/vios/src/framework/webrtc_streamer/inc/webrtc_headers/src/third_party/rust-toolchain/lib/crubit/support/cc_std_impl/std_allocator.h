// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_STD_ALLOCATOR_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_STD_ALLOCATOR_H_

#include <stdio.h>

#include <cstddef>
#include <new>

namespace crubit_cc_std_internal::std_allocator {

// Performs `new x` without running the constructor. Instead, this directly
// calls the correct `operator new` overload.
inline void* cpp_new(size_t n, size_t align) {
  if (align <= __STDCPP_DEFAULT_NEW_ALIGNMENT__) {
    return operator new(n);
  } else {
    return operator new(n, static_cast<std::align_val_t>(align));
  }
}

// Performs `delete x` without running the destructor. Instead, this directly
// calls the correct `operator delete` overload.
inline void cpp_delete(void* ptr, size_t n, size_t align) {
#ifdef __cpp_sized_deallocation
  if (align <= __STDCPP_DEFAULT_NEW_ALIGNMENT__) {
    operator delete(ptr, n);
  } else {
    operator delete(ptr, n, static_cast<std::align_val_t>(align));
  }
#else
  if (align <= __STDCPP_DEFAULT_NEW_ALIGNMENT__) {
    operator delete(ptr);
  } else {
    operator delete(ptr, static_cast<std::align_val_t>(align));
  }
#endif
}

}  // namespace crubit_cc_std_internal::std_allocator

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_CC_STD_STD_ALLOCATOR_H_
