// Copyright 2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef FUZZTEST_FUZZTEST_INTERNAL_FUZZING_MOCK_STREAM_H_
#define FUZZTEST_FUZZTEST_INTERNAL_FUZZING_MOCK_STREAM_H_

#include <cstddef>
#include <cstdint>
#include <limits>

#include "absl/functional/function_ref.h"
#include "absl/numeric/bits.h"
#include "absl/types/span.h"

namespace fuzztest::internal {

class FuzzingMockStream {
 public:
  // DataStreamFn copies up to n bytes from the data stream to the buffer
  // pointer.
  using DataStreamFn = absl::FunctionRef<void(void*, size_t)>;

  // Instruction is used to select an implementation-specific random number
  // variate when invoking the fuzztest mock.
  enum class Instruction : uint8_t {
    // Use the data stream to generate the random number.
    kDataStreamVariate = 0,
    // Use the LCG algorithm to generate the random number.
    kLCGVariate = 1,
    kMin = 2,   // Return the minimum value of the distribution.
    kMax = 3,   // Return the maximum value of the distribution.
    kMean = 4,  // Return the mean of the distribution.

    // Return an alternate value of the distribution. This is typically an edge
    // case value that is unlikely to be returned by the normal distribution.
    kAlternateVariate = 5,
  };

  FuzzingMockStream(DataStreamFn data_stream_fn, Instruction instruction)
      : data_stream_fn_(data_stream_fn), instruction_(instruction) {}

  static Instruction GetNextInstruction(
      absl::Span<const uint8_t>& control_stream) {
    if (control_stream.empty()) {
      return Instruction::kDataStreamVariate;
    }
    uint8_t v = control_stream[0];
    control_stream.remove_prefix(1);
    return static_cast<Instruction>(v % 6);
  }

  Instruction instruction() { return instruction_; }

  void get_bytes(void* result, size_t result_size) {
    data_stream_fn_(result, result_size);
  }

  template <typename T>
  T get_int_value() {
    T x = 0;
    data_stream_fn_(&x, sizeof(x));
    return x;
  }

  // Consumes bytes from the data stream to generate a random integer in the
  // range [0, range].
  template <typename T>
  T get_int_value_in_range(uint64_t range) {
    if (range == 0) {
      return 0;
    }
    uint64_t x = 0;
    if (range <= (std::numeric_limits<uint8_t>::max)()) {
      x = get_int_value<uint8_t>();
    } else if (range <= (std::numeric_limits<uint16_t>::max)()) {
      x = get_int_value<uint16_t>();
    } else if (range <= (std::numeric_limits<uint32_t>::max)()) {
      x = get_int_value<uint32_t>();
    } else {
      x = get_int_value<uint64_t>();
    }
    if (range == std::numeric_limits<uint64_t>::max() ||
        absl::has_single_bit(range + 1)) {
      return static_cast<T>(x & range);  // range is a mask of 2^N-1
    } else {
      return static_cast<T>(x % (range + 1));
    }
  }

  // URBG interface.
  using result_type = uint64_t;

  static constexpr result_type(min)() {
    return (std::numeric_limits<result_type>::min)();
  }
  static constexpr result_type(max)() {
    return (std::numeric_limits<result_type>::max)();
  }

  void reset() {}

  uint64_t operator()() { return get_int_value<uint64_t>(); }

 private:
  DataStreamFn data_stream_fn_;
  Instruction instruction_;
};

// TypeErasedFuzzFunctionT(fuzzing_mock_stream, args_tuple, result)
// is a type erased function pointer for use with absl::MockingBitGen and
// fuzztest mocking.
using TypeErasedFuzzFunctionT = void (*)(FuzzingMockStream, void*, void*);

}  // namespace fuzztest::internal

#endif  // FUZZTEST_FUZZTEST_INTERNAL_FUZZING_MOCK_STREAM_H_
