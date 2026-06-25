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

#ifndef LIBGAV1_EXAMPLES_FILE_READER_H_
#define LIBGAV1_EXAMPLES_FILE_READER_H_

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

#include "examples/file_reader_interface.h"

namespace libgav1 {

// Temporal Unit based file reader class. Currently supports only IVF files.
class FileReader : public FileReaderInterface {
 public:
  enum FileType {
    kFileTypeUnknown,
    kFileTypeIvf,
  };

  // Creates and returns a FileReader that reads from |file_name|.
  // If |error_tolerant| is true format and read errors are ignored,
  // ReadTemporalUnit() may return truncated data.
  // Returns nullptr when the file does not exist, cannot be read, or is not an
  // IVF file.
  static std::unique_ptr<FileReaderInterface> Open(const std::string& file_name,
                                                   bool error_tolerant = false);

  FileReader() = delete;
  FileReader(const FileReader&) = delete;
  FileReader& operator=(const FileReader&) = delete;

  // Closes |file_|.
  ~FileReader() override;

  // Reads a temporal unit from |file_| and writes the data to |tu_data|.
  // Returns true when:
  // - A temporal unit is read successfully, or
  // - At end of file.
  // When ReadTemporalUnit() is called at the end of the file, it will return
  // true without writing any data to |tu_data|.
  //
  // The |timestamp| pointer is optional: callers not interested in timestamps
  // can pass nullptr. When |timestamp| is not a nullptr, this function returns
  // the presentation timestamp from the IVF frame header.
  /*LIBGAV1_MUST_USE_RESULT*/ bool ReadTemporalUnit(
      std::vector<uint8_t>* tu_data, int64_t* timestamp) override;

  /*LIBGAV1_MUST_USE_RESULT*/ bool IsEndOfFile() const override {
    return feof(file_) != 0;
  }

  // The values returned by these accessors are strictly informative. No
  // validation is performed when they are read from the IVF file header.
  size_t width() const override { return width_; }
  size_t height() const override { return height_; }
  size_t frame_rate() const override { return frame_rate_; }
  size_t time_scale() const override { return time_scale_; }

 private:
  FileReader(FILE* file, bool owns_file, bool error_tolerant)
      : file_(file), owns_file_(owns_file), error_tolerant_(error_tolerant) {}

  bool ReadIvfFileHeader();

  FILE* file_ = nullptr;
  size_t width_ = 0;
  size_t height_ = 0;
  size_t frame_rate_ = 0;
  size_t time_scale_ = 0;
  FileType type_ = kFileTypeUnknown;
  // True if this object owns file_ and is responsible for closing it when
  // done.
  const bool owns_file_;
  const bool error_tolerant_;

  static bool registered_in_factory_;
};

}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_FILE_READER_H_
