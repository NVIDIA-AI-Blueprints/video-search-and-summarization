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

#ifndef LIBGAV1_EXAMPLES_FILE_WRITER_H_
#define LIBGAV1_EXAMPLES_FILE_WRITER_H_

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>

#include "gav1/decoder_buffer.h"

namespace libgav1 {

// Frame based file writer class. Supports only Y4M (YUV4MPEG2) and RAW output.
class FileWriter {
 public:
  enum FileType : uint8_t {
    kFileTypeRaw,
    kFileTypeY4m,
  };

  struct Y4mParameters {
    Y4mParameters() = default;
    Y4mParameters(size_t width, size_t height, size_t frame_rate_numerator,
                  size_t frame_rate_denominator,
                  ChromaSamplePosition chroma_sample_position,
                  ImageFormat image_format, size_t bitdepth)
        : width(width),
          height(height),
          frame_rate_numerator(frame_rate_numerator),
          frame_rate_denominator(frame_rate_denominator),
          chroma_sample_position(chroma_sample_position),
          image_format(image_format),
          bitdepth(bitdepth) {}

    Y4mParameters(const Y4mParameters& rhs) = default;
    Y4mParameters& operator=(const Y4mParameters& rhs) = default;
    Y4mParameters(Y4mParameters&& rhs) = default;
    Y4mParameters& operator=(Y4mParameters&& rhs) = default;

    size_t width = 0;
    size_t height = 0;
    size_t frame_rate_numerator = 30;
    size_t frame_rate_denominator = 1;
    ChromaSamplePosition chroma_sample_position = kChromaSamplePositionUnknown;
    ImageFormat image_format = kImageFormatYuv420;
    size_t bitdepth = 8;
  };

  // Opens |file_name|. When |file_type| is kFileTypeY4m the Y4M file header is
  // written out to |file_| before this method returns.
  //
  // Returns a FileWriter instance after the file is opened successfully for
  // kFileTypeRaw files, and after the Y4M file header bytes are written for
  // kFileTypeY4m files. Returns nullptr upon failure.
  static std::unique_ptr<FileWriter> Open(const std::string& file_name,
                                          FileType type,
                                          const Y4mParameters* y4m_parameters);

  FileWriter() = delete;
  FileWriter(const FileWriter&) = delete;
  FileWriter& operator=(const FileWriter&) = delete;

  FileWriter(FileWriter&&) = default;
  FileWriter& operator=(FileWriter&&) = default;

  // Closes |file_|.
  ~FileWriter();

  // Writes the frame data in |frame_buffer| to |file_|. Returns true after
  // successful write of |frame_buffer| data.
  /*LIBGAV1_MUST_USE_RESULT*/ bool WriteFrame(
      const DecoderBuffer& frame_buffer);

 private:
  explicit FileWriter(FILE* file) : file_(file) {}

  bool WriteY4mFileHeader(const Y4mParameters& y4m_parameters);

  FILE* file_ = nullptr;
  FileType file_type_ = kFileTypeRaw;
};

}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_FILE_WRITER_H_
