// Copyright 2022 The Centipede Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef THIRD_PARTY_CENTIPEDE_RUNNER_UTILS_H_
#define THIRD_PARTY_CENTIPEDE_RUNNER_UTILS_H_

#include <sys/stat.h>

#include <cstdint>
#include <cstdio>
#include <new>
#include <vector>

#include "absl/base/nullability.h"

// Use this attribute for functions that must not be instrumented even if
// the library is built with sanitizers (asan, etc).
#define FUZZTEST_NO_SANITIZE __attribute__((no_sanitize("all")))

namespace fuzztest::internal {

// If `condition` prints `error` and calls exit(1).
// TODO(kcc): change all uses of PrintErrorAndExitIf() to RunnerCheck()
// as it is a more common pattern.
void PrintErrorAndExitIf(bool condition, const char* absl_nonnull error);

// A rough equivalent of "FUZZTEST_CHECK(condition) << error;".
inline void RunnerCheck(bool condition, const char* absl_nonnull error) {
  PrintErrorAndExitIf(!condition, error);
}

// Returns the lower bound of the stack region for the current thread. 0 will be
// returned on failures.
uintptr_t GetCurrentThreadStackRegionLow();

template <typename Type>
std::vector<Type> ReadBytesFromFilePath(const char* input_path) {
  FILE* input_file = fopen(input_path, "rb");
  RunnerCheck(input_file != nullptr, "can't open the input file");
  struct stat statbuf = {};
  RunnerCheck(fstat(fileno(input_file), &statbuf) == 0, "fstat failed");
  size_t size_in_bytes = statbuf.st_size;
  RunnerCheck(size_in_bytes != 0, "empty file");
  RunnerCheck((size_in_bytes % sizeof(Type)) == 0,
              "file size is not multiple of the type size");
  std::vector<Type> data(size_in_bytes / sizeof(Type));
  auto num_bytes_read = fread(data.data(), 1, size_in_bytes, input_file);
  RunnerCheck(num_bytes_read == size_in_bytes, "read failed");
  RunnerCheck(fclose(input_file) == 0, "fclose failed");
  return data;
}

// Reads `size` bytes to `data` from `fd` with retires (assuming `fd` is
// blocking so there is no busy-spinning). Returns true if all bytes are
// written, false otherwise due to errors.
bool ReadAll(int fd, char* data, size_t size);

// Writes `size` bytes from `data` to `fd` with retires (assuming `fd` is
// blocking so there is no busy-spinning). Returns true if all bytes are
// written, false otherwise due to errors.
bool WriteAll(int fd, const char* data, size_t size);

extern "C" void __lsan_register_root_region(const void* p, size_t size)
    __attribute__((weak));
extern "C" void __lsan_unregister_root_region(const void* p, size_t size)
    __attribute__((weak));

// Wraps an object of `T` stored as a plain byte array with explicit
// construction/destruction. Needed for runner/dispatcher related global states
// that need extended lifetime. (Alternatively we could using dynamic pointers
// for them, but that would introduce extra pointer check/dereference on every
// use.)
//
// The lifetime manager of the actual object should be cautious to follow the
// calling requirements of the methods below.
//
// The implementation is modified/simplified from `absl::NoDestructor`.
template <typename T>
class ExplicitLifetime {
 public:
  ExplicitLifetime() = default;

  // No copying.
  ExplicitLifetime(const ExplicitLifetime&) = delete;
  ExplicitLifetime& operator=(const ExplicitLifetime&) = delete;

  T& operator*() { return *get(); }
  T* absl_nonnull operator->() { return get(); }

  // Constructs the actual object with forwarded `args`.
  //
  // Must be called exactly once after creation of this ExplicitLifetime<>
  // instance or any recent `Destruct()` and before accessing the actual object.
  template <typename... Args>
  void Construct(Args&&... args) {
    new (&space_) T(std::forward<Args>(args)...);
    // Needed otherwise lsan may lose track of the pointers inside the object as
    // it is in-place constructed from the byte array.
    if (__lsan_register_root_region) {
      __lsan_register_root_region(&space_, sizeof(space_));
    }
  }

  // Destructs the actual object (without reclaiming the space). It can only be
  // called at most once after recent `Construct()`.
  void Destruct() {
    get()->~T();
    if (__lsan_unregister_root_region) {
      __lsan_unregister_root_region(&space_, sizeof(space_));
    }
  }

  // Gets the pointer to the actual object backed by a plain byte array. Using
  // the pointer before `Construct()` or after `Destruct()` may result in
  // accessing uninitialized data.
  T* absl_nonnull get() { return std::launder(reinterpret_cast<T*>(&space_)); }

 private:
  alignas(T) unsigned char space_[sizeof(T)];
};

}  // namespace fuzztest::internal

#endif  // THIRD_PARTY_CENTIPEDE_RUNNER_UTILS_H_
