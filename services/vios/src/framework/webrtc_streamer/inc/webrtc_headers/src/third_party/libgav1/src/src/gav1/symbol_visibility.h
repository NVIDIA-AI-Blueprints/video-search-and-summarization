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

#ifndef LIBGAV1_SRC_GAV1_SYMBOL_VISIBILITY_H_
#define LIBGAV1_SRC_GAV1_SYMBOL_VISIBILITY_H_

// This module defines the LIBGAV1_PUBLIC macro. LIBGAV1_PUBLIC, when combined
// with the flags -fvisibility=hidden and -fvisibility-inlines-hidden, restricts
// symbol availability when users use the shared object form of libgav1. The
// intent is to prevent exposure of libgav1 internals to users of the library,
// and to avoid ABI compatibility problems that changes to libgav1 internals
// would cause for users of the libgav1 shared object.
//
// Examples:
//
// This form makes a class and all of its members part of the public API:
//
// class LIBGAV1_PUBLIC A {
//  public:
//   A();
//   ~A();
//   void Foo();
//   int Bar();
// };
//
// A::A(), A::~A(), A::Foo(), and A::Bar() are all available to code linking to
// the shared object when this form is used.
//
// This form exposes a single class method as part of the public API:
//
// class B {
//  public:
//   B();
//   ~B();
//   LIBGAV1_PUBLIC int Foo();
// };
//
// In this examples only B::Foo() is available to the user of the shared object.
//
// Non-class member functions can also be exposed individually:
//
// LIBGAV1_PUBLIC void Bar();
//
// In this example Bar() would be available to users of the shared object.
//
// Much of the above information and more can be found at
// https://gcc.gnu.org/wiki/Visibility
//
// NOTE: A third-party build system for libgav1 can add -DLIBGAV1_PUBLIC= to the
// compiler command line to override the definition of LIBGAV1_PUBLIC in this
// header. This can be used to create a libgav1 static library that will not
// export any symbols when it is linked into a shared library.

#if !defined(LIBGAV1_PUBLIC)
#if defined(_WIN32)
#if defined(LIBGAV1_BUILDING_DLL) && LIBGAV1_BUILDING_DLL
#if defined(__GNUC__)
#define LIBGAV1_PUBLIC __attribute__((dllexport))
#else
#define LIBGAV1_PUBLIC __declspec(dllexport)
#endif  // defined(__GNUC__)
#elif defined(LIBGAV1_BUILDING_DLL)
#ifdef __GNUC__
#define LIBGAV1_PUBLIC __attribute__((dllimport))
#else
#define LIBGAV1_PUBLIC __declspec(dllimport)
#endif  // defined(__GNUC__)
#else
#define LIBGAV1_PUBLIC
#endif  // defined(LIBGAV1_BUILDING_DLL) && LIBGAV1_BUILDING_DLL
#else   // !defined(_WIN32)
#if defined(__GNUC__) && __GNUC__ >= 4
#define LIBGAV1_PUBLIC __attribute__((visibility("default")))
#else
#define LIBGAV1_PUBLIC
#endif
#endif  // defined(_WIN32)
#endif  // defined(LIBGAV1_PUBLIC)

#endif  // LIBGAV1_SRC_GAV1_SYMBOL_VISIBILITY_H_
