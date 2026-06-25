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

#ifndef LIBGAV1_EXAMPLES_FILE_READER_CONSTANTS_H_
#define LIBGAV1_EXAMPLES_FILE_READER_CONSTANTS_H_

namespace libgav1 {

enum {
  kIvfHeaderVersion = 0,
  kIvfFrameHeaderSize = 12,
  kIvfFileHeaderSize = 32,
#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
  kMaxTemporalUnitSize = 512 * 1024,
#else
  kMaxTemporalUnitSize = 256 * 1024 * 1024,
#endif
};

extern const char kIvfSignature[4];
extern const char kAv1FourCcUpper[4];
extern const char kAv1FourCcLower[4];

}  // namespace libgav1

#endif  // LIBGAV1_EXAMPLES_FILE_READER_CONSTANTS_H_
