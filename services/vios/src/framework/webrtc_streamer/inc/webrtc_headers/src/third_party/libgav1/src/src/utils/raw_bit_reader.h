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

#ifndef LIBGAV1_SRC_UTILS_RAW_BIT_READER_H_
#define LIBGAV1_SRC_UTILS_RAW_BIT_READER_H_

#include <cstddef>
#include <cstdint>

#include "src/utils/bit_reader.h"
#include "src/utils/memory.h"

namespace libgav1 {

class RawBitReader final : public BitReader, public Allocable {
 public:
  RawBitReader(const uint8_t* data, size_t size);
  ~RawBitReader() override = default;

  int ReadBit() override;
  int64_t ReadLiteral(int num_bits) override;  // f(n) in the spec.
  bool ReadInverseSignedLiteral(int num_bits,
                                int* value);  // su(1+num_bits) in the spec.
  bool ReadLittleEndian(int num_bytes,
                        size_t* value);    // le(n) in the spec.
  bool ReadUnsignedLeb128(size_t* value);  // leb128() in the spec.
  // Reads a variable length unsigned number and stores it in |*value|. On a
  // successful return, |*value| is in the range of 0 to UINT32_MAX - 1,
  // inclusive.
  bool ReadUvlc(uint32_t* value);  // uvlc() in the spec.
  bool Finished() const;
  size_t bit_offset() const { return bit_offset_; }
  // Return the bytes consumed so far (rounded up).
  size_t byte_offset() const { return (bit_offset() + 7) >> 3; }
  size_t size() const { return size_; }
  // Move to the next byte boundary if not already at one. Return false if any
  // of the bits being skipped over is non-zero. Return true otherwise. If this
  // function returns false, the reader is left in an undefined state and must
  // not be used further. section 5.3.5.
  bool AlignToNextByte();
  // Make sure that the trailing bits structure is as expected and skip over it.
  // section 5.3.4.
  bool VerifyAndSkipTrailingBits(size_t num_bits);
  // Skip |num_bytes| bytes. This only works if the current position is at a
  // byte boundary. The function returns false if the current position is not at
  // a byte boundary or if skipping |num_bytes| causes the reader to run out of
  // buffer. Returns true otherwise.
  bool SkipBytes(size_t num_bytes);
  // Skip |num_bits| bits. The function returns false if skipping |num_bits|
  // causes the reader to run out of buffer. Returns true otherwise.
  bool SkipBits(size_t num_bits);

 private:
  // Returns true if it is safe to read a literal of size |num_bits|.
  bool CanReadLiteral(size_t num_bits) const;
  int ReadBitImpl();

  const uint8_t* const data_;
  size_t bit_offset_;
  const size_t size_;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_RAW_BIT_READER_H_
