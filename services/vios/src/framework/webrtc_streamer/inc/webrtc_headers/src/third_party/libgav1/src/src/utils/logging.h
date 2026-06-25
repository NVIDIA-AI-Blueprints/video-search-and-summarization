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

#ifndef LIBGAV1_SRC_UTILS_LOGGING_H_
#define LIBGAV1_SRC_UTILS_LOGGING_H_

#include <cstddef>

#include "src/utils/compiler_attributes.h"

#if !defined(LIBGAV1_ENABLE_LOGGING)
#if defined(NDEBUG) || defined(FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION)
#define LIBGAV1_ENABLE_LOGGING 0
#else
#define LIBGAV1_ENABLE_LOGGING 1
#endif
#endif

#if LIBGAV1_ENABLE_LOGGING
// LIBGAV1_DLOG(severity, printf-format-string)
// Debug logging that can optionally be enabled in release builds by explicitly
// setting LIBGAV1_ENABLE_LOGGING.
// Severity is given as an all-caps version of enum LogSeverity with the
// leading 'k' removed: LIBGAV1_DLOG(INFO, "...");
#define LIBGAV1_DLOG(severity, ...)                                     \
  do {                                                                  \
    constexpr const char* libgav1_logging_internal_basename =           \
        libgav1::internal::Basename(__FILE__, sizeof(__FILE__) - 1);    \
    libgav1::internal::Log(LIBGAV1_LOGGING_INTERNAL_##severity,         \
                           libgav1_logging_internal_basename, __LINE__, \
                           __VA_ARGS__);                                \
  } while (0)
#else
#define LIBGAV1_DLOG(severity, ...) \
  do {                              \
  } while (0)
#endif  // LIBGAV1_ENABLE_LOGGING

#define LIBGAV1_LOGGING_INTERNAL_ERROR libgav1::internal::LogSeverity::kError
#define LIBGAV1_LOGGING_INTERNAL_WARNING \
  libgav1::internal::LogSeverity::kWarning
#define LIBGAV1_LOGGING_INTERNAL_INFO libgav1::internal::LogSeverity::kInfo

namespace libgav1 {
namespace internal {

enum class LogSeverity : int {
  kError,
  kWarning,
  kInfo,
};

// Helper function to implement LIBGAV1_DLOG
// Logs |format, ...| at |severity| level, reporting it as called from
// |file|:|line|.
void Log(libgav1::internal::LogSeverity severity, const char* file, int line,
         const char* format, ...) LIBGAV1_PRINTF_ATTRIBUTE(4, 5);

// Compile-time function to get the 'base' file_name, that is, the part of
// a file_name after the last '/' or '\' path separator. The search starts at
// the end of the string; the second parameter is the length of the string.
constexpr const char* Basename(const char* file_name, size_t offset) {
  return (offset == 0 || file_name[offset - 1] == '/' ||
          file_name[offset - 1] == '\\')
             ? file_name + offset
             : Basename(file_name, offset - 1);
}

}  // namespace internal
}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_LOGGING_H_
