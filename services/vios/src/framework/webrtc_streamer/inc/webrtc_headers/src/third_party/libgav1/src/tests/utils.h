/*
 * Copyright 2020 The libgav1 Authors
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

#ifndef LIBGAV1_TESTS_UTILS_H_
#define LIBGAV1_TESTS_UTILS_H_

#include <cstddef>
#include <new>
#include <string>

#include "absl/base/config.h"
#include "absl/strings/string_view.h"
#include "absl/time/time.h"
#include "src/gav1/decoder_buffer.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/memory.h"
#include "tests/third_party/libvpx/acm_random.h"

#ifdef ABSL_HAVE_EXCEPTIONS
#include <exception>
#endif

namespace libgav1 {
namespace test_utils {

enum { kAlternateDeterministicSeed = 0x9571 };
static_assert(kAlternateDeterministicSeed !=
                  libvpx_test::ACMRandom::DeterministicSeed(),
              "");

// Similar to libgav1::MaxAlignedAllocable, but retains the throwing versions
// of new to support googletest allocations.
// Note when building the source as C++17 or greater, gcc 11.2.0 may issue a
// warning of the form:
//   warning: 'void operator delete [](void*, std::align_val_t)' called on
//     pointer returned from a mismatched allocation function
//   note: returned from 'static void*
//     libgav1::test_utils::MaxAlignedAllocable::operator new [](size_t)'
// This is a false positive as this function calls
// libgav1::MaxAlignedAllocable::operator new[](size, std::nothrow) which in
// turn calls
// void* operator new[](std::size_t, std::align_val_t, const std::nothrow_t&).
// This is due to unbalanced inlining of the functions, so we force them to be
// inlined.
// See: https://gcc.gnu.org/bugzilla/show_bug.cgi?id=103993
struct MaxAlignedAllocable {
  // Class-specific allocation functions.
  static LIBGAV1_ALWAYS_INLINE void* operator new(size_t size) {
    void* const p =
        libgav1::MaxAlignedAllocable::operator new(size, std::nothrow);
#ifdef ABSL_HAVE_EXCEPTIONS
    if (p == nullptr) throw std::bad_alloc();
#endif
    return p;
  }
  static LIBGAV1_ALWAYS_INLINE void* operator new[](size_t size) {
    void* const p =
        libgav1::MaxAlignedAllocable::operator new[](size, std::nothrow);
#ifdef ABSL_HAVE_EXCEPTIONS
    if (p == nullptr) throw std::bad_alloc();
#endif
    return p;
  }

  // Class-specific non-throwing allocation functions
  static LIBGAV1_ALWAYS_INLINE void* operator new(
      size_t size, const std::nothrow_t& tag) noexcept {
    return libgav1::MaxAlignedAllocable::operator new(size, tag);
  }
  static LIBGAV1_ALWAYS_INLINE void* operator new[](
      size_t size, const std::nothrow_t& tag) noexcept {
    return libgav1::MaxAlignedAllocable::operator new[](size, tag);
  }

  // Class-specific deallocation functions.
  static LIBGAV1_ALWAYS_INLINE void operator delete(void* ptr) noexcept {
    libgav1::MaxAlignedAllocable::operator delete(ptr);
  }
  static LIBGAV1_ALWAYS_INLINE void operator delete[](void* ptr) noexcept {
    libgav1::MaxAlignedAllocable::operator delete[](ptr);
  }

  // Only called if new (std::nothrow) is used and the constructor throws an
  // exception.
  static LIBGAV1_ALWAYS_INLINE void operator delete(
      void* ptr, const std::nothrow_t& tag) noexcept {
    libgav1::MaxAlignedAllocable::operator delete(ptr, tag);
  }
  // Only called if new[] (std::nothrow) is used and the constructor throws an
  // exception.
  static LIBGAV1_ALWAYS_INLINE void operator delete[](
      void* ptr, const std::nothrow_t& tag) noexcept {
    libgav1::MaxAlignedAllocable::operator delete[](ptr, tag);
  }
};

// Clears dsp table entries for |bitdepth|. This function is not thread safe.
void ResetDspTable(int bitdepth);

//------------------------------------------------------------------------------
// Gets human readable hexadecimal encoded MD5 sum from given data, block, or
// frame buffer.

std::string GetMd5Sum(const void* bytes, size_t size);
template <typename Pixel>
std::string GetMd5Sum(const Pixel* block, int width, int height, int stride);
std::string GetMd5Sum(const DecoderBuffer& buffer);

//------------------------------------------------------------------------------
// Compares the md5 digest of |size| bytes of |data| with |expected_digest|.
// Prints a log message with |name|, |function_name|, md5 digest and
// |elapsed_time|. |name| and |function_name| are merely tags used for logging
// and can be any meaningful string depending on the caller's context.

void CheckMd5Digest(const char name[], const char function_name[],
                    const char expected_digest[], const void* data, size_t size,
                    absl::Duration elapsed_time);

//------------------------------------------------------------------------------
// Compares the md5 digest of |block| with |expected_digest|. The width, height,
// and stride of |block| are |width|, |height|, and |stride|, respectively.
// Prints a log message with |name|, |function_name|, md5 digest and
// |elapsed_time|. |name| and |function_name| are merely tags used for logging
// and can be any meaningful string depending on the caller's context.

template <typename Pixel>
void CheckMd5Digest(const char name[], const char function_name[],
                    const char expected_digest[], const Pixel* block, int width,
                    int height, int stride, absl::Duration elapsed_time);

//------------------------------------------------------------------------------
// Compares |actual_digest| with |expected_digest|. Prints a log message with
// |name|, |function_name|, md5 digest and |elapsed_time|. |name| and
// |function_name| are merely tags used for logging and can be any meaningful
// string depending on the caller's context.

void CheckMd5Digest(const char name[], const char function_name[],
                    const char expected_digest[], const char actual_digest[],
                    absl::Duration elapsed_time);

//------------------------------------------------------------------------------
// Reads the test data from |file_name| as a string into |output|. The
// |is_output_file| argument controls the expansion of |file_name| to its full
// path. When |is_output_file| is true GetTestData() reads from
// utils.cc::GetTempDir(), and when it is false the file is read from
// utils.cc::GetSourceDir().
void GetTestData(absl::string_view file_name, bool is_output_file,
                 std::string* output);

//------------------------------------------------------------------------------
// Returns the full path to |file_name| from libgav1/tests/data.
std::string GetTestInputFilePath(absl::string_view file_name);

//------------------------------------------------------------------------------
// Returns the full path to |file_name| in a location where the file can be
// opened for writing.
std::string GetTestOutputFilePath(absl::string_view file_name);

}  // namespace test_utils
}  // namespace libgav1

#endif  // LIBGAV1_TESTS_UTILS_H_
