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

#ifndef LIBGAV1_EXAMPLES_FILE_READER_FACTORY_H_
#define LIBGAV1_EXAMPLES_FILE_READER_FACTORY_H_

#include <memory>
#include <string>

#include "examples/file_reader_interface.h"

namespace libgav1 {

class FileReaderFactory {
 public:
  using OpenFunction = std::unique_ptr<FileReaderInterface> (*)(
      const std::string& file_name, bool error_tolerant);

  FileReaderFactory() = delete;
  FileReaderFactory(const FileReaderFactory&) = delete;
  FileReaderFactory& operator=(const FileReaderFactory&) = delete;
  ~FileReaderFactory() = default;

  // Registers the OpenFunction for a FileReaderInterface and returns true when
  // registration succeeds.
  static bool RegisterReader(OpenFunction open_function);

  // Passes |file_name| to each OpenFunction until one succeeds. Returns nullptr
  // when no reader is found for |file_name|. Otherwise a FileReaderInterface is
  // returned. If |error_tolerant| is true and the reader supports it, some
  // format and read errors may be ignored and partial data returned.
  static std::unique_ptr<FileReaderInterface> OpenReader(
      const std::string& file_name, bool error_tolerant = false);
};

}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_FILE_READER_FACTORY_H_
