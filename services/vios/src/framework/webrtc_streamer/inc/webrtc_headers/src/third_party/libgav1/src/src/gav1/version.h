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

#ifndef LIBGAV1_SRC_GAV1_VERSION_H_
#define LIBGAV1_SRC_GAV1_VERSION_H_

#include "gav1/symbol_visibility.h"

// This library follows the principles described by Semantic Versioning
// (https://semver.org).

#define LIBGAV1_MAJOR_VERSION 0
#define LIBGAV1_MINOR_VERSION 21
#define LIBGAV1_PATCH_VERSION 0

#define LIBGAV1_VERSION                                           \
  ((LIBGAV1_MAJOR_VERSION << 16) | (LIBGAV1_MINOR_VERSION << 8) | \
   LIBGAV1_PATCH_VERSION)

#if defined(__cplusplus)
extern "C" {
#endif

// Returns the library's version number, packed in an int using 8 bits for
// each of major/minor/patch. e.g, 1.2.3 is 0x010203.
LIBGAV1_PUBLIC int Libgav1GetVersion(void);

// Returns the library's version number as a string in the format
// 'MAJOR.MINOR.PATCH'. Always returns a valid (non-NULL) string.
LIBGAV1_PUBLIC const char* Libgav1GetVersionString(void);

// Returns the build configuration used to produce the library. Always returns
// a valid (non-NULL) string.
LIBGAV1_PUBLIC const char* Libgav1GetBuildConfiguration(void);

#if defined(__cplusplus)
}  // extern "C"

namespace libgav1 {

// Returns the library's version number, packed in an int using 8 bits for
// each of major/minor/patch. e.g, 1.2.3 is 0x010203.
inline int GetVersion() { return Libgav1GetVersion(); }

// Returns the library's version number as a string in the format
// 'MAJOR.MINOR.PATCH'. Always returns a valid (non-NULL) string.
inline const char* GetVersionString() { return Libgav1GetVersionString(); }

// Returns the build configuration used to produce the library. Always returns
// a valid (non-NULL) string.
inline const char* GetBuildConfiguration() {
  return Libgav1GetBuildConfiguration();
}

}  // namespace libgav1
#endif  // defined(__cplusplus)

#endif  // LIBGAV1_SRC_GAV1_VERSION_H_
