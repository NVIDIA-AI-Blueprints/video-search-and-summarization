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

#ifndef LIBGAV1_TESTS_BLOCK_UTILS_H_
#define LIBGAV1_TESTS_BLOCK_UTILS_H_

#include <cstdint>

namespace libgav1 {
namespace test_utils {

//------------------------------------------------------------------------------
// Prints |block| pixel by pixel with |width| pixels per row if |print_padding|
// is false, |stride| otherwise. If |print_padding| is true padding pixels are
// surrounded in '[]'.
template <typename Pixel>
void PrintBlock(const Pixel* block, int width, int height, int stride,
                bool print_padding = false);

extern template void PrintBlock(const uint8_t* block, int width, int height,
                                int stride, bool print_padding /*= false*/);
extern template void PrintBlock(const uint16_t* block, int width, int height,
                                int stride, bool print_padding /*= false*/);

//------------------------------------------------------------------------------
// Compares |block1| and |block2| pixel by pixel checking |width| pixels per row
// if |check_padding| is false, min(|stride1|, |stride2|) pixels otherwise.
// Prints the blocks with differences marked with a '*' if |print_diff| is
// true (the default).

template <typename Pixel>
bool CompareBlocks(const Pixel* block1, const Pixel* block2, int width,
                   int height, int stride1, int stride2, bool check_padding,
                   bool print_diff = true);

extern template bool CompareBlocks(const uint8_t* block1, const uint8_t* block2,
                                   int width, int height, int stride1,
                                   int stride2, bool check_padding,
                                   bool print_diff /*= true*/);
extern template bool CompareBlocks(const uint16_t* block1,
                                   const uint16_t* block2, int width,
                                   int height, int stride1, int stride2,
                                   bool check_padding,
                                   bool print_diff /*= true*/);

}  // namespace test_utils
}  // namespace libgav1

#endif  // LIBGAV1_TESTS_BLOCK_UTILS_H_
