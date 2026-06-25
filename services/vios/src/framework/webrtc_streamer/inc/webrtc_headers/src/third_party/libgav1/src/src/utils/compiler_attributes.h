/*
 * Copyright 2019 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_UTILS_COMPILER_ATTRIBUTES_H_
#define LIBGAV1_SRC_UTILS_COMPILER_ATTRIBUTES_H_

// A collection of compiler attribute checks and defines to control for
// compatibility across toolchains.

//------------------------------------------------------------------------------
// Language version, attribute and feature helpers.

// Detect C++17 support. Visual Studio sets __cplusplus to 199711L by default
// unless compiled with /Zc:__cplusplus, use the value controlled by /std
// instead.
// https://docs.microsoft.com/en-us/cpp/build/reference/zc-cplusplus
#if __cplusplus < 201703L && (!defined(_MSVC_LANG) || _MSVC_LANG < 201703L)
#error "C++ versions less than C++17 are not supported."
#endif

#if defined(__has_attribute)
#define LIBGAV1_HAS_ATTRIBUTE __has_attribute
#else
#define LIBGAV1_HAS_ATTRIBUTE(x) 0
#endif

#if defined(__has_feature)
#define LIBGAV1_HAS_FEATURE __has_feature
#else
#define LIBGAV1_HAS_FEATURE(x) 0
#endif

//------------------------------------------------------------------------------
// Sanitizer attributes.

#if LIBGAV1_HAS_FEATURE(address_sanitizer) || defined(__SANITIZE_ADDRESS__)
#define LIBGAV1_ASAN 1
#else
#define LIBGAV1_ASAN 0
#endif

#if LIBGAV1_HAS_FEATURE(memory_sanitizer)
#define LIBGAV1_MSAN 1
#else
#define LIBGAV1_MSAN 0
#endif

#if LIBGAV1_HAS_FEATURE(thread_sanitizer) || defined(__SANITIZE_THREAD__)
#define LIBGAV1_TSAN 1
#else
#define LIBGAV1_TSAN 0
#endif

//------------------------------------------------------------------------------
// AddressSanitizer support.

// Define the macros for AddressSanitizer manual memory poisoning. See
// https://github.com/google/sanitizers/wiki/AddressSanitizerManualPoisoning.
#if LIBGAV1_ASAN
#include <sanitizer/asan_interface.h>
#else
#define ASAN_POISON_MEMORY_REGION(addr, size) ((void)(addr), (void)(size))
#define ASAN_UNPOISON_MEMORY_REGION(addr, size) ((void)(addr), (void)(size))
#endif

//------------------------------------------------------------------------------
// MemorySanitizer support.

#if LIBGAV1_MSAN
#include <sanitizer/msan_interface.h>
#endif  // LIBGAV1_MSAN

//------------------------------------------------------------------------------
// Function attributes.
// GCC: https://gcc.gnu.org/onlinedocs/gcc/Function-Attributes.html
// Clang: https://clang.llvm.org/docs/AttributeReference.html

#if defined(__GNUC__)
#define LIBGAV1_ALWAYS_INLINE __attribute__((always_inline)) inline
#elif defined(_MSC_VER)
#define LIBGAV1_ALWAYS_INLINE __forceinline
#else
#define LIBGAV1_ALWAYS_INLINE inline
#endif

// LIBGAV1_MUST_USE_RESULT
//
// Tells the compiler to warn about unused results.
//
// When annotating a function, it must appear as the first part of the
// declaration or definition. The compiler will warn if the return value from
// such a function is unused:
//
//   LIBGAV1_MUST_USE_RESULT Sprocket* AllocateSprocket();
//   AllocateSprocket();  // Triggers a warning.
//
// When annotating a class, it is equivalent to annotating every function which
// returns an instance.
//
//   class LIBGAV1_MUST_USE_RESULT Sprocket {};
//   Sprocket();  // Triggers a warning.
//
//   Sprocket MakeSprocket();
//   MakeSprocket();  // Triggers a warning.
//
// Note that references and pointers are not instances:
//
//   Sprocket* SprocketPointer();
//   SprocketPointer();  // Does *not* trigger a warning.
//
// LIBGAV1_MUST_USE_RESULT allows using cast-to-void to suppress the unused
// result warning. For that, warn_unused_result is used only for clang but not
// for gcc. https://gcc.gnu.org/bugzilla/show_bug.cgi?id=66425
#if LIBGAV1_HAS_ATTRIBUTE(nodiscard)
#define LIBGAV1_MUST_USE_RESULT [[nodiscard]]
#elif defined(__clang__) && LIBGAV1_HAS_ATTRIBUTE(warn_unused_result)
#define LIBGAV1_MUST_USE_RESULT __attribute__((warn_unused_result))
#else
#define LIBGAV1_MUST_USE_RESULT
#endif

// LIBGAV1_PRINTF_ATTRIBUTE
//
// Tells the compiler to perform `printf` format string checking if the
// compiler supports it; see the 'format' attribute in
// <https://gcc.gnu.org/onlinedocs/gcc/Common-Function-Attributes.html>.
//
// Note: As the GCC manual states, "[s]ince non-static C++ methods
// have an implicit 'this' argument, the arguments of such methods
// should be counted from two, not one."
#if LIBGAV1_HAS_ATTRIBUTE(format) || (defined(__GNUC__) && !defined(__clang__))
#define LIBGAV1_PRINTF_ATTRIBUTE(string_index, first_to_check) \
  __attribute__((__format__(__printf__, string_index, first_to_check)))
#else
#define LIBGAV1_PRINTF_ATTRIBUTE(string_index, first_to_check)
#endif

//------------------------------------------------------------------------------
// Thread annotations.

// LIBGAV1_GUARDED_BY()
//
// Documents if a shared field or global variable needs to be protected by a
// mutex. LIBGAV1_GUARDED_BY() allows the user to specify a particular mutex
// that should be held when accessing the annotated variable.
//
// Although this annotation cannot be applied to local variables, a local
// variable and its associated mutex can often be combined into a small class
// or struct, thereby allowing the annotation.
//
// Example:
//
//   class Foo {
//     Mutex mu_;
//     int p1_ LIBGAV1_GUARDED_BY(mu_);
//     ...
//   };
// TODO(b/133245043): this can be reenabled after a local MutexLock
// implementation is added with proper thread annotations.
#if 0  // LIBGAV1_HAS_ATTRIBUTE(guarded_by)
#define LIBGAV1_GUARDED_BY(x) __attribute__((guarded_by(x)))
#else
#define LIBGAV1_GUARDED_BY(x)
#endif

//------------------------------------------------------------------------------

#undef LIBGAV1_HAS_ATTRIBUTE
#undef LIBGAV1_HAS_FEATURE

#endif  // LIBGAV1_SRC_UTILS_COMPILER_ATTRIBUTES_H_
