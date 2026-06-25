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

#ifndef LIBGAV1_SRC_UTILS_ENTROPY_DECODER_H_
#define LIBGAV1_SRC_UTILS_ENTROPY_DECODER_H_

#include <cstddef>
#include <cstdint>

#include "src/utils/bit_reader.h"
#include "src/utils/compiler_attributes.h"

namespace libgav1 {

class EntropyDecoder final : public BitReader {
 public:
  // WindowSize must be an unsigned integer type with at least 32 bits. Use the
  // largest type with fast arithmetic. size_t should meet these requirements.
  using WindowSize = size_t;

  EntropyDecoder(const uint8_t* data, size_t size, bool allow_update_cdf);
  ~EntropyDecoder() override = default;

  // Move only.
  EntropyDecoder(EntropyDecoder&& rhs) noexcept;
  EntropyDecoder& operator=(EntropyDecoder&& rhs) noexcept;

  int ReadBit() override;
  int64_t ReadLiteral(int num_bits) override;
  // ReadSymbol() calls for which the |symbol_count| is only known at runtime
  // will use this variant.
  int ReadSymbol(uint16_t* cdf, int symbol_count);
  // ReadSymbol() calls for which the |symbol_count| is equal to 2 (boolean
  // symbols) will use this variant.
  bool ReadSymbol(uint16_t* cdf);
  bool ReadSymbolWithoutCdfUpdate(uint16_t cdf);
  // Use either linear search or binary search for decoding the symbol depending
  // on |symbol_count|. ReadSymbol calls for which the |symbol_count| is known
  // at compile time will use this variant.
  template <int symbol_count>
  int ReadSymbol(uint16_t* cdf);

 private:
  static constexpr int kWindowSize = static_cast<int>(sizeof(WindowSize)) * 8;
  static_assert(kWindowSize >= 32, "");

  // Reads a symbol using the |cdf| table which contains the probabilities of
  // each symbol. On a high level, this function does the following:
  //   1) Scale the |cdf| values.
  //   2) Find the index in the |cdf| array where the scaled CDF value crosses
  //   the modified |window_diff_| threshold.
  //   3) That index is the symbol that has been decoded.
  //   4) Update |window_diff_| and |values_in_range_| based on the symbol that
  //   has been decoded.
  inline int ReadSymbolImpl(const uint16_t* cdf, int symbol_count);
  // Similar to ReadSymbolImpl but it uses binary search to perform step 2 in
  // the comment above. As of now, this function is called when |symbol_count|
  // is greater than or equal to 14.
  inline int ReadSymbolImplBinarySearch(const uint16_t* cdf, int symbol_count);
  // Specialized implementation of ReadSymbolImpl based on the fact that
  // symbol_count == 2.
  inline int ReadSymbolImpl(uint16_t cdf);
  // ReadSymbolN is a specialization of ReadSymbol for symbol_count == N.
  LIBGAV1_ALWAYS_INLINE int ReadSymbol3Or4(uint16_t* cdf, int symbol_count);
  // ReadSymbolImplN is a specialization of ReadSymbolImpl for
  // symbol_count == N.
  LIBGAV1_ALWAYS_INLINE int ReadSymbolImpl8(const uint16_t* cdf);
  inline void PopulateBits();
  // Normalizes the range so that 32768 <= |values_in_range_| < 65536. Also
  // calls PopulateBits() if necessary.
  inline void NormalizeRange();

  const uint8_t* data_;
  const uint8_t* const data_end_;
  // If |data_| < |data_memcpy_end_|, then we can read sizeof(WindowSize) bytes
  // from |data_|. Note with sizeof(WindowSize) == 4 this is only used in the
  // constructor, not PopulateBits().
  const uint8_t* const data_memcpy_end_;
  const bool allow_update_cdf_;
  // Number of cached bits of data in the current value.
  int bits_;
  // Number of values in the current range. Declared as uint32_t for better
  // performance but only the lower 16 bits are used.
  uint32_t values_in_range_;
  // The difference between the high end of the current range and the coded
  // value minus 1. The 16 bits above |bits_| of this variable are used to
  // decode the next symbol. It is filled in whenever |bits_| is less than 0.
  // Note this implementation differs from the spec as it trades the need to
  // shift in 1s in NormalizeRange() with an extra shift in PopulateBits(),
  // which occurs less frequently.
  WindowSize window_diff_;
};

extern template int EntropyDecoder::ReadSymbol<3>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<4>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<5>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<6>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<7>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<8>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<9>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<10>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<11>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<12>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<13>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<14>(uint16_t* cdf);
extern template int EntropyDecoder::ReadSymbol<16>(uint16_t* cdf);

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_ENTROPY_DECODER_H_
