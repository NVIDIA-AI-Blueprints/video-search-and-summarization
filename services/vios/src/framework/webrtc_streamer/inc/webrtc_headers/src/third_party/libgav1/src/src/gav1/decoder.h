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

#ifndef LIBGAV1_SRC_GAV1_DECODER_H_
#define LIBGAV1_SRC_GAV1_DECODER_H_

#if defined(__cplusplus)
#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>
#else
#include <stddef.h>
#include <stdint.h>
#endif  // defined(__cplusplus)

// IWYU pragma: begin_exports
#include "gav1/decoder_buffer.h"
#include "gav1/decoder_settings.h"
#include "gav1/frame_buffer.h"
#include "gav1/status_code.h"
#include "gav1/symbol_visibility.h"
#include "gav1/version.h"
// IWYU pragma: end_exports

#if defined(__cplusplus)
extern "C" {
#endif

struct Libgav1Decoder;
typedef struct Libgav1Decoder Libgav1Decoder;

LIBGAV1_PUBLIC Libgav1StatusCode Libgav1DecoderCreate(
    const Libgav1DecoderSettings* settings, Libgav1Decoder** decoder_out);

LIBGAV1_PUBLIC void Libgav1DecoderDestroy(Libgav1Decoder* decoder);

LIBGAV1_PUBLIC Libgav1StatusCode Libgav1DecoderEnqueueFrame(
    Libgav1Decoder* decoder, const uint8_t* data, size_t size,
    int64_t user_private_data, void* buffer_private_data);

LIBGAV1_PUBLIC Libgav1StatusCode Libgav1DecoderDequeueFrame(
    Libgav1Decoder* decoder, const Libgav1DecoderBuffer** out_ptr);

LIBGAV1_PUBLIC Libgav1StatusCode
Libgav1DecoderSignalEOS(Libgav1Decoder* decoder);

LIBGAV1_PUBLIC int Libgav1DecoderGetMaxBitdepth(void);

#if defined(__cplusplus)
}  // extern "C"

namespace libgav1 {

// Forward declaration.
class DecoderImpl;

class LIBGAV1_PUBLIC Decoder {
 public:
  Decoder();
  ~Decoder();

  // Init must be called exactly once per instance. Subsequent calls will do
  // nothing. If |settings| is nullptr, the decoder will be initialized with
  // default settings. Returns kStatusOk on success, an error status otherwise.
  StatusCode Init(const DecoderSettings* settings);

  // Enqueues a compressed frame to be decoded.
  //
  // This function returns:
  //   * kStatusOk on success
  //   * kStatusTryAgain if the decoder queue is full
  //   * an error status otherwise.
  //
  // |user_private_data| may be used to associate application specific private
  // data with the compressed frame. It will be copied to the user_private_data
  // field of the DecoderBuffer returned by the corresponding |DequeueFrame()|
  // call.
  //
  // NOTE: |EnqueueFrame()| does not copy the data. Therefore, after a
  // successful |EnqueueFrame()| call, the caller must keep the |data| buffer
  // alive until:
  // 1) If |settings_.release_input_buffer| is not nullptr, then |data| buffer
  // must be kept alive until release_input_buffer is called with the
  // |buffer_private_data| passed into this EnqueueFrame call.
  // 2) If |settings_.release_input_buffer| is nullptr, then |data| buffer must
  // be kept alive until the corresponding DequeueFrame() call is completed.
  //
  // If the call to |EnqueueFrame()| is not successful, then libgav1 will not
  // hold any references to the |data| buffer. |settings_.release_input_buffer|
  // callback will not be called in that case.
  StatusCode EnqueueFrame(const uint8_t* data, size_t size,
                          int64_t user_private_data, void* buffer_private_data);

  // Dequeues a decompressed frame. If there are enqueued compressed frames,
  // decodes one and sets |*out_ptr| to the last displayable frame in the
  // compressed frame. If there are no displayable frames available, sets
  // |*out_ptr| to nullptr.
  //
  // Returns kStatusOk on success. Returns kStatusNothingToDequeue if there are
  // no enqueued frames (in this case out_ptr will always be set to nullptr).
  // Returns one of the other error statuses if there is an error.
  //
  // If |settings_.blocking_dequeue| is false and the decoder is operating in
  // frame parallel mode (|settings_.frame_parallel| is true and the video
  // stream passes the decoder's heuristics for enabling frame parallel mode),
  // then this call will return kStatusTryAgain if an enqueued frame is not yet
  // decoded (it is a non blocking call in this case). In all other cases, this
  // call will block until an enqueued frame has been decoded.
  StatusCode DequeueFrame(const DecoderBuffer** out_ptr);

  // Signals the end of stream.
  //
  // In non-frame-parallel mode, this function will release all the frames held
  // by the decoder. If the frame buffers were allocated by libgav1, then the
  // pointer obtained by the prior DequeueFrame call will no longer be valid. If
  // the frame buffers were allocated by the application, then any references
  // that libgav1 is holding on to will be released.
  //
  // Once this function returns successfully, the decoder state will be reset
  // and the decoder is ready to start decoding a new coded video sequence.
  StatusCode SignalEOS();

  // Returns the maximum bitdepth that is supported by this decoder.
  static int GetMaxBitdepth();

  // Returns a vector with the QP values for all the frames in the last temporal
  // unit in encoding/decoding order (Note: not display order). If no frames are
  // present in the last temporal unit the method returns an empty vector.
  //
  // NOTE: This function is C++ only and is not exposed via the C API.
  //
  // TODO(vardar): return a map that contains a QP per spatial layer for each
  // temporal layer.
  std::vector<int> GetFramesMeanQpInTemporalUnit();

 private:
  DecoderSettings settings_;
  // The object is initialized if and only if impl_ != nullptr.
  std::unique_ptr<DecoderImpl> impl_;
  std::vector<int> frame_mean_qps_;
};

}  // namespace libgav1
#endif  // defined(__cplusplus)

#endif  // LIBGAV1_SRC_GAV1_DECODER_H_
