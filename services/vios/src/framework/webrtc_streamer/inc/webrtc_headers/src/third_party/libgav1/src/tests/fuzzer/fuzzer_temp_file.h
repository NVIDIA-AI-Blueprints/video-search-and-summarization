/*
 * Copyright 2020 Google Inc.
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

#ifndef LIBGAV1_TESTS_FUZZER_FUZZER_TEMP_FILE_H_
#define LIBGAV1_TESTS_FUZZER_FUZZER_TEMP_FILE_H_

// Adapter utility from fuzzer input to a temporary file, for fuzzing APIs that
// require a file instead of an input buffer.

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <io.h>
#include <windows.h>

#define strdup _strdup
#define unlink _unlink
#else
#include <unistd.h>
#endif  // _WIN32

// Pure-C interface for creating and cleaning up temporary files.

static char* fuzzer_get_tmpfile_with_suffix(const uint8_t* data, size_t size,
                                            const char* suffix) {
#ifdef _WIN32
  // GetTempPathA generates '<path>\<pre><uuuu>.TMP'.
  (void)suffix;  // NOLINT (this could be a C compilation unit)
  char temp_path[MAX_PATH];
  const DWORD ret = GetTempPathA(MAX_PATH, temp_path);
  if (ret == 0 || ret > MAX_PATH) {
    fprintf(stderr, "Error getting temporary directory name: %lu\n",
            GetLastError());
    abort();
  }
  char* filename_buffer =
      (char*)malloc(MAX_PATH);  // NOLINT (this could be a C compilation unit)
  if (!filename_buffer) {
    perror("Failed to allocate file name buffer.");
    abort();
  }
  if (GetTempFileNameA(temp_path, "ftf", /*uUnique=*/0, filename_buffer) == 0) {
    fprintf(stderr, "Error getting temporary file name: %lu\n", GetLastError());
    abort();
  }
#if defined(_MSC_VER) || defined(MINGW_HAS_SECURE_API)
  FILE* file;
  const errno_t err = fopen_s(&file, filename_buffer, "wb");
  if (err != 0) file = NULL;  // NOLINT (this could be a C compilation unit)
#else
  FILE* file = fopen(filename_buffer, "wb");
#endif
  if (!file) {
    perror("Failed to open file.");
    abort();
  }
#else  // !_WIN32
  if (suffix == NULL) {  // NOLINT (this could be a C compilation unit)
    suffix = "";
  }
  const size_t suffix_len = strlen(suffix);
  if (suffix_len > INT_MAX) {  // mkstemps takes int for suffixlen param
    perror("Suffix too long");
    abort();
  }

#ifdef __ANDROID__
  const char* leading_temp_path =
      "/data/local/tmp/generate_temporary_file.XXXXXX";
#else
  const char* leading_temp_path = "/tmp/generate_temporary_file.XXXXXX";
#endif
  const size_t buffer_sz = strlen(leading_temp_path) + suffix_len + 1;
  char* filename_buffer =
      (char*)malloc(buffer_sz);  // NOLINT (this could be a C compilation unit)
  if (!filename_buffer) {
    perror("Failed to allocate file name buffer.");
    abort();
  }

  if (snprintf(filename_buffer, buffer_sz, "%s%s", leading_temp_path, suffix) >=
      (int)buffer_sz) {  // NOLINT (this could be a C compilation unit)
    perror("File name buffer too short.");
    abort();
  }

  const int file_descriptor = mkstemps(filename_buffer, suffix_len);
  if (file_descriptor < 0) {
    perror("Failed to make temporary file.");
    abort();
  }
  FILE* file = fdopen(file_descriptor, "wb");
  if (!file) {
    perror("Failed to open file descriptor.");
    close(file_descriptor);
    abort();
  }
#endif  // _WIN32
  const size_t bytes_written = fwrite(data, sizeof(uint8_t), size, file);
  if (bytes_written < size) {
    fclose(file);
    fprintf(stderr, "Failed to write all bytes to file (%zu out of %zu)",
            bytes_written, size);
    abort();
  }
  fclose(file);
  return filename_buffer;
}

static char* fuzzer_get_tmpfile(
    const uint8_t* data,
    size_t size) {  // NOLINT (people include this .inc file directly)
  return fuzzer_get_tmpfile_with_suffix(data, size, NULL);  // NOLINT
}

static void fuzzer_release_tmpfile(char* filename) {
  if (unlink(filename) != 0) {
    perror("WARNING: Failed to delete temporary file.");
  }
  free(filename);
}

// C++ RAII object for creating temporary files.

#ifdef __cplusplus
class FuzzerTemporaryFile {
 public:
  FuzzerTemporaryFile(const uint8_t* data, size_t size)
      : original_filename_(fuzzer_get_tmpfile(data, size)) {
    filename_ = strdup(original_filename_);
    if (!filename_) {
      perror("Failed to allocate file name copy.");
      abort();
    }
  }

  FuzzerTemporaryFile(const uint8_t* data, size_t size, const char* suffix)
      : original_filename_(fuzzer_get_tmpfile_with_suffix(data, size, suffix)) {
    filename_ = strdup(original_filename_);
    if (!filename_) {
      perror("Failed to allocate file name copy.");
      abort();
    }
  }

  ~FuzzerTemporaryFile() {
    free(filename_);
    fuzzer_release_tmpfile(original_filename_);
  }

  FuzzerTemporaryFile(const FuzzerTemporaryFile& other) = delete;
  FuzzerTemporaryFile operator=(const FuzzerTemporaryFile& other) = delete;

  FuzzerTemporaryFile(const FuzzerTemporaryFile&& other) = delete;
  FuzzerTemporaryFile operator=(const FuzzerTemporaryFile&& other) = delete;

  const char* filename() const { return filename_; }

  // Returns a mutable pointer to the file name. Should be used sparingly, only
  // in case the fuzzed API demands it or when making a mutable copy is
  // inconvenient (e.g., in auto-generated code).
  char* mutable_filename() const { return filename_; }

 private:
  char* original_filename_;

  // A mutable copy of the original filename, returned by the accessor. This
  // guarantees that the original filename can always be used to release the
  // temporary path.
  char* filename_;
};
#endif  // __cplusplus
#endif  // LIBGAV1_TESTS_FUZZER_FUZZER_TEMP_FILE_H_
