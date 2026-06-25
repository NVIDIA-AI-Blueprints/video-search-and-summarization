// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef CRUBIT_SUPPORT_INTERNAL_SIZEOF_H_
#define CRUBIT_SUPPORT_INTERNAL_SIZEOF_H_

// like sizeof, but rounds up to alignment, in case the type has a strange
// sizeof.
//
// In particular, this is true of type aliases which override alignment but
// not size, as in e.g. `typedef __attribute__((aligned(N)) struct {} MyAlias;`.
//
// Note that this must be a macro, not a template, because it must not desugar
// the type alias. So this cannot be
// `template <typename T> constexpr size_t size = ...` -- it would incorrectly
// report the size of the underlying type, not the type alias.
#define CRUBIT_SIZEOF(...)                                                   \
  ((sizeof(__VA_ARGS__) + alignof(__VA_ARGS__) - 1) / alignof(__VA_ARGS__) * \
   alignof(__VA_ARGS__))

#endif  // CRUBIT_SUPPORT_INTERNAL_SIZEOF_H_
