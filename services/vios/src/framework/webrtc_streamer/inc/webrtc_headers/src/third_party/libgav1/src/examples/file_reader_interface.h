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

#ifndef LIBGAV1_EXAMPLES_FILE_READER_INTERFACE_H_
#define LIBGAV1_EXAMPLES_FILE_READER_INTERFACE_H_

#include <cstddef>
#include <cstdint>
#include <vector>

namespace libgav1 {

class FileReaderInterface {
 public:
  FileReaderInterface() = default;
  FileReaderInterface(const FileReaderInterface&) = delete;
  FileReaderInterface& operator=(const FileReaderInterface&) = delete;

  FileReaderInterface(FileReaderInterface&&) = default;
  FileReaderInterface& operator=(FileReaderInterface&&) = default;

  // Closes the file.
  virtual ~FileReaderInterface() = default;

  // Reads a temporal unit from the file and writes the data to |tu_data|.
  // Returns true when:
  // - A temporal unit is read successfully, or
  // - At end of file.
  // When ReadTemporalUnit() is called at the end of the file, it will return
  // true without writing any data to |tu_data|.
  //
  // The |timestamp| pointer is optional: callers not interested in timestamps
  // can pass nullptr. When |timestamp| is not a nullptr, this function returns
  // the presentation timestamp of the temporal unit.
  /*LIBGAV1_MUST_USE_RESULT*/ virtual bool ReadTemporalUnit(
      std::vector<uint8_t>* tu_data, int64_t* timestamp) = 0;

  /*LIBGAV1_MUST_USE_RESULT*/ virtual bool IsEndOfFile() const = 0;

  // The values returned by these accessors are strictly informative. No
  // validation is performed when they are read from file.
  virtual size_t width() const = 0;
  virtual size_t height() const = 0;
  virtual size_t frame_rate() const = 0;
  virtual size_t time_scale() const = 0;
};

}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_FILE_READER_INTERFACE_H_
