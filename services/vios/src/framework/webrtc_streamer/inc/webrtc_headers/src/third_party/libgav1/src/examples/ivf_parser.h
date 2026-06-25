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

#ifndef LIBGAV1_EXAMPLES_IVF_PARSER_H_
#define LIBGAV1_EXAMPLES_IVF_PARSER_H_

#include <cstddef>
#include <cstdint>

namespace libgav1 {

struct IvfFileHeader {
  IvfFileHeader() = default;
  IvfFileHeader(const IvfFileHeader& rhs) = default;
  IvfFileHeader& operator=(const IvfFileHeader& rhs) = default;
  IvfFileHeader(IvfFileHeader&& rhs) = default;
  IvfFileHeader& operator=(IvfFileHeader&& rhs) = default;

  size_t width = 0;
  size_t height = 0;
  size_t frame_rate_numerator = 0;
  size_t frame_rate_denominator = 0;
};

struct IvfFrameHeader {
  IvfFrameHeader() = default;
  IvfFrameHeader(const IvfFrameHeader& rhs) = default;
  IvfFrameHeader& operator=(const IvfFrameHeader& rhs) = default;
  IvfFrameHeader(IvfFrameHeader&& rhs) = default;
  IvfFrameHeader& operator=(IvfFrameHeader&& rhs) = default;

  size_t frame_size = 0;
  int64_t timestamp = 0;
};

bool ParseIvfFileHeader(const uint8_t* header_buffer,
                        IvfFileHeader* ivf_file_header);

bool ParseIvfFrameHeader(const uint8_t* header_buffer,
                         IvfFrameHeader* ivf_frame_header);

}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_IVF_PARSER_H_
