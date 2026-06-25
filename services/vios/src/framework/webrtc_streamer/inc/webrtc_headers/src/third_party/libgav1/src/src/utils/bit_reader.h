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

#ifndef LIBGAV1_SRC_UTILS_BIT_READER_H_
#define LIBGAV1_SRC_UTILS_BIT_READER_H_

#include <cstdint>

namespace libgav1 {

class BitReader {
 public:
  virtual ~BitReader() = default;

  virtual int ReadBit() = 0;
  // |num_bits| has to be <= 32. The function returns a value in the range [0,
  // 2^num_bits - 1] (inclusive) on success and -1 on failure.
  virtual int64_t ReadLiteral(int num_bits) = 0;

  bool DecodeSignedSubexpWithReference(int low, int high, int reference,
                                       int control, int* value);  // 5.9.26.
  // Decodes a nonnegative integer with maximum number of values |n| (i.e.,
  // output in range 0..n-1) by following the process specified in Section
  // 4.10.7 ns(n) and Section 4.10.10 NS(n) of the spec.
  bool DecodeUniform(int n, int* value);

 private:
  // Helper functions for DecodeSignedSubexpWithReference.
  bool DecodeUnsignedSubexpWithReference(int mx, int reference, int control,
                                         int* value);           // 5.9.27.
  bool DecodeSubexp(int num_symbols, int control, int* value);  // 5.9.28.
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_BIT_READER_H_
