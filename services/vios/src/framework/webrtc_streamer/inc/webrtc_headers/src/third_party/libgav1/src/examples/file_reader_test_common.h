/*
 * Copyright 2021 The libgav1 Authors
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

#ifndef LIBGAV1_EXAMPLES_FILE_READER_TEST_COMMON_H_
#define LIBGAV1_EXAMPLES_FILE_READER_TEST_COMMON_H_

#include <cstdint>
#include <memory>
#include <ostream>
#include <string>
#include <vector>

#include "examples/file_reader.h"
#include "examples/file_reader_factory.h"
#include "examples/file_reader_interface.h"
#include "gtest/gtest.h"
#include "tests/utils.h"

namespace libgav1 {

struct FileReaderTestParameters {
  FileReaderTestParameters() = default;
  FileReaderTestParameters(FileReaderFactory::OpenFunction open_function,
                           const char* file_name)
      : open_function(open_function), file_name(file_name) {}
  FileReaderTestParameters(const FileReaderTestParameters&) = default;
  FileReaderTestParameters& operator=(const FileReaderTestParameters&) = delete;
  FileReaderTestParameters(FileReaderTestParameters&&) = default;
  FileReaderTestParameters& operator=(FileReaderTestParameters&&) = default;
  ~FileReaderTestParameters() = default;

  FileReaderFactory::OpenFunction open_function = nullptr;
  const char* file_name = nullptr;
};

class FileReaderTestBase {
 public:
  FileReaderTestBase() = default;
  FileReaderTestBase(const FileReaderTestBase&) = delete;
  FileReaderTestBase& operator=(const FileReaderTestBase&) = delete;
  FileReaderTestBase(FileReaderTestBase&&) = default;
  FileReaderTestBase& operator=(FileReaderTestBase&&) = default;
  ~FileReaderTestBase() = default;

 protected:
  void OpenReader(const char* file_name,
                  FileReaderFactory::OpenFunction open_function) {
    file_name_ = test_utils::GetTestInputFilePath(file_name);
    reader_ = open_function(file_name_, /*error_tolerant=*/false);
    ASSERT_NE(reader_, nullptr);
  }

  std::string file_name_;
  std::unique_ptr<FileReaderInterface> reader_;
  std::vector<uint8_t> tu_data_;
};

class FileReaderFailTest
    : public FileReaderTestBase,
      public testing::TestWithParam<FileReaderTestParameters> {
 public:
  FileReaderFailTest() = default;
  FileReaderFailTest(const FileReaderTestBase&) = delete;
  FileReaderFailTest& operator=(const FileReaderTestBase&) = delete;
  ~FileReaderFailTest() override = default;

 protected:
  void SetUp() override {
    OpenReader(GetParam().file_name, GetParam().open_function);
  }
};

class FileReaderTestNoTimeStamps
    : public FileReaderTestBase,
      public testing::TestWithParam<FileReaderTestParameters> {
 public:
  FileReaderTestNoTimeStamps() = default;
  FileReaderTestNoTimeStamps(const FileReaderTestNoTimeStamps&) = delete;
  FileReaderTestNoTimeStamps& operator=(const FileReaderTestNoTimeStamps&) =
      delete;
  ~FileReaderTestNoTimeStamps() override = default;

 protected:
  void SetUp() override {
    OpenReader(GetParam().file_name, GetParam().open_function);
  }
};

class FileReaderErrorTolerant
    : public FileReaderTestBase,
      public testing::TestWithParam<FileReaderTestParameters> {
 public:
  FileReaderErrorTolerant() = default;
  FileReaderErrorTolerant(const FileReaderErrorTolerant&) = delete;
  FileReaderErrorTolerant& operator=(const FileReaderErrorTolerant&) = delete;
  ~FileReaderErrorTolerant() override = default;

 protected:
  void SetUp() override {
    file_name_ = test_utils::GetTestInputFilePath(GetParam().file_name);
    reader_ = GetParam().open_function(file_name_, /*error_tolerant=*/true);
    ASSERT_NE(reader_, nullptr);
  }
};

struct FileReaderTestWithTimeStampsParameters {
  FileReaderTestWithTimeStampsParameters() = default;
  FileReaderTestWithTimeStampsParameters(
      FileReaderFactory::OpenFunction open_function, const char* file_name,
      int64_t expected_last_timestamp)
      : open_function(open_function),
        file_name(file_name),
        expected_last_timestamp(expected_last_timestamp) {}
  FileReaderTestWithTimeStampsParameters(
      const FileReaderTestWithTimeStampsParameters&) = default;
  FileReaderTestWithTimeStampsParameters& operator=(
      const FileReaderTestWithTimeStampsParameters&) = delete;
  FileReaderTestWithTimeStampsParameters(
      FileReaderTestWithTimeStampsParameters&&) = default;
  FileReaderTestWithTimeStampsParameters& operator=(
      FileReaderTestWithTimeStampsParameters&&) = default;
  ~FileReaderTestWithTimeStampsParameters() = default;

  FileReaderFactory::OpenFunction open_function = nullptr;
  const char* file_name = nullptr;
  int64_t expected_last_timestamp = 0;
};

std::ostream& operator<<(std::ostream& stream,
                         const FileReaderTestParameters& parameters);

std::ostream& operator<<(
    std::ostream& stream,
    const FileReaderTestWithTimeStampsParameters& parameters);

class FileReaderTestWithTimeStamps
    : public FileReaderTestBase,
      public testing::TestWithParam<FileReaderTestWithTimeStampsParameters> {
 public:
  FileReaderTestWithTimeStamps() = default;
  FileReaderTestWithTimeStamps(const FileReaderTestWithTimeStamps&) = delete;
  FileReaderTestWithTimeStamps& operator=(const FileReaderTestWithTimeStamps&) =
      delete;
  ~FileReaderTestWithTimeStamps() override = default;

 protected:
  void SetUp() override {
    FileReaderTestWithTimeStampsParameters parameters = GetParam();
    OpenReader(parameters.file_name, parameters.open_function);
    expected_last_timestamp_ = parameters.expected_last_timestamp;
  }

  int64_t last_timestamp_ = 0;
  int64_t expected_last_timestamp_ = 0;
};

}  // namespace libgav1
#endif  // LIBGAV1_EXAMPLES_FILE_READER_TEST_COMMON_H_
