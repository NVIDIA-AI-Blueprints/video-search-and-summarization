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

#ifndef LIBGAV1_EXAMPLES_LOGGING_H_
#define LIBGAV1_EXAMPLES_LOGGING_H_

#include <cstddef>
#include <cstdio>

namespace libgav1 {
namespace examples {

#if !defined(LIBGAV1_EXAMPLES_ENABLE_LOGGING)
#if defined(FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION)
#define LIBGAV1_EXAMPLES_ENABLE_LOGGING 0
#else
#define LIBGAV1_EXAMPLES_ENABLE_LOGGING 1
#endif
#endif

#if LIBGAV1_EXAMPLES_ENABLE_LOGGING

// Compile-time function to get the 'base' file_name, that is, the part of
// a file_name after the last '/' or '\' path separator. The search starts at
// the end of the string; the second parameter is the length of the string.
constexpr const char* Basename(const char* file_name, size_t offset) {
  return (offset == 0 || file_name[offset - 1] == '/' ||
          file_name[offset - 1] == '\\')
             ? file_name + offset
             : Basename(file_name, offset - 1);
}

#define LIBGAV1_EXAMPLES_LOG_ERROR(error_string)                              \
  do {                                                                        \
    constexpr const char* libgav1_examples_basename =                         \
        libgav1::examples::Basename(__FILE__, sizeof(__FILE__) - 1);          \
    fprintf(stderr, "%s:%d (%s): %s.\n", libgav1_examples_basename, __LINE__, \
            __func__, error_string);                                          \
  } while (false)

#else  // !LIBGAV1_EXAMPLES_ENABLE_LOGGING

#define LIBGAV1_EXAMPLES_LOG_ERROR(error_string) \
  do {                                           \
  } while (false)

#endif  // LIBGAV1_EXAMPLES_ENABLE_LOGGING

}  // namespace examples
}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_LOGGING_H_
