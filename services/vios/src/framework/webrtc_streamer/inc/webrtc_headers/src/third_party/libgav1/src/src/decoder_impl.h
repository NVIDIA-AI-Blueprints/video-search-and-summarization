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

#ifndef LIBGAV1_SRC_DECODER_IMPL_H_
#define LIBGAV1_SRC_DECODER_IMPL_H_

#include <array>
#include <condition_variable>  // NOLINT (unapproved c++11 header)
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>  // NOLINT (unapproved c++11 header)
#include <vector>

#include "src/buffer_pool.h"
#include "src/decoder_state.h"
#include "src/dsp/constants.h"
#include "src/frame_scratch_buffer.h"
#include "src/gav1/decoder_buffer.h"
#include "src/gav1/decoder_settings.h"
#include "src/gav1/status_code.h"
#include "src/obu_parser.h"
#include "src/quantizer.h"
#include "src/residual_buffer_pool.h"
#include "src/symbol_decoder_context.h"
#include "src/tile.h"
#include "src/utils/array_2d.h"
#include "src/utils/block_parameters_holder.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"
#include "src/utils/queue.h"
#include "src/utils/segmentation_map.h"
#include "src/utils/types.h"

namespace libgav1 {

struct TemporalUnit;

struct EncodedFrame {
  EncodedFrame(ObuParser* const obu, const DecoderState& state,
               const RefCountedBufferPtr& frame, int position_in_temporal_unit)
      : sequence_header(obu->sequence_header()),
        frame_header(obu->frame_header()),
        state(state),
        temporal_unit(nullptr),
        frame(frame),
        position_in_temporal_unit(position_in_temporal_unit) {
    obu->MoveTileBuffers(&tile_buffers);
    frame->MarkFrameAsStarted();
  }

  const ObuSequenceHeader sequence_header;
  const ObuFrameHeader frame_header;
  Vector<TileBuffer> tile_buffers;
  DecoderState state;
  TemporalUnit* temporal_unit;
  RefCountedBufferPtr frame;
  const int position_in_temporal_unit;
};

struct TemporalUnit : public Allocable {
  // The default constructor is invoked by the Queue<TemporalUnit>::Init()
  // method. Queue<> does not use the default-constructed elements, so it is
  // safe for the default constructor to not initialize the members.
  TemporalUnit() = default;
  TemporalUnit(const uint8_t* data, size_t size, int64_t user_private_data,
               void* buffer_private_data)
      : data(data),
        size(size),
        user_private_data(user_private_data),
        buffer_private_data(buffer_private_data),
        decoded(false),
        status(kStatusOk),
        has_displayable_frame(false),
        output_frame_position(-1),
        decoded_count(0),
        output_layer_count(0),
        released_input_buffer(false) {}

  const uint8_t* data;
  size_t size;
  int64_t user_private_data;
  void* buffer_private_data;

  // The following members are used only in frame parallel mode.
  bool decoded;
  StatusCode status;
  bool has_displayable_frame;
  int output_frame_position;

  Vector<EncodedFrame> frames;
  size_t decoded_count;

  // The struct (and the counter) is used to support output of multiple layers
  // within a single temporal unit. The decoding process will store the output
  // frames in |output_layers| in the order they are finished decoding. At the
  // end of the decoding process, this array will be sorted in reverse order of
  // |position_in_temporal_unit|. DequeueFrame() will then return the frames in
  // reverse order (so that the entire process can run with a single counter
  // variable).
  struct OutputLayer {
    // Used by std::sort to sort |output_layers| in reverse order of
    // |position_in_temporal_unit|.
    bool operator<(const OutputLayer& rhs) const {
      return position_in_temporal_unit > rhs.position_in_temporal_unit;
    }

    RefCountedBufferPtr frame;
    int position_in_temporal_unit = 0;
  } output_layers[kMaxLayers];
  // Number of entries in |output_layers|.
  int output_layer_count;
  // Flag to ensure that we release the input buffer only once if there are
  // multiple output layers.
  bool released_input_buffer;
};

class DecoderImpl : public Allocable {
 public:
  // The constructor saves a const reference to |*settings|. Therefore
  // |*settings| must outlive the DecoderImpl object. On success, |*output|
  // contains a pointer to the newly-created DecoderImpl object. On failure,
  // |*output| is not modified.
  static StatusCode Create(const DecoderSettings* settings,
                           std::unique_ptr<DecoderImpl>* output);
  ~DecoderImpl();
  StatusCode EnqueueFrame(const uint8_t* data, size_t size,
                          int64_t user_private_data, void* buffer_private_data);
  StatusCode DequeueFrame(const DecoderBuffer** out_ptr);
  static constexpr int GetMaxBitdepth() {
    static_assert(LIBGAV1_MAX_BITDEPTH == 8 || LIBGAV1_MAX_BITDEPTH == 10 ||
                      LIBGAV1_MAX_BITDEPTH == 12,
                  "LIBGAV1_MAX_BITDEPTH must be 8, 10 or 12.");
    return LIBGAV1_MAX_BITDEPTH;
  }
  std::vector<int> GetFrameQps();

 private:
  explicit DecoderImpl(const DecoderSettings* settings);
  StatusCode Init();
  // Called when the first frame is enqueued. It does the OBU parsing for one
  // temporal unit to retrieve the tile configuration and sets up the frame
  // threading if frame parallel mode is allowed. It also initializes the
  // |temporal_units_| queue based on the number of frame threads.
  //
  // The following are the limitations of the current implementation:
  //  * It assumes that all frames in the video have the same tile
  //    configuration. The frame parallel threading model will not be updated
  //    based on tile configuration changes mid-stream.
  //  * The above assumption holds true even when there is a new coded video
  //    sequence (i.e.) a new sequence header.
  StatusCode InitializeFrameThreadPoolAndTemporalUnitQueue(const uint8_t* data,
                                                           size_t size);
  // Used only in frame parallel mode. Signals failure and waits until the
  // worker threads are aborted if |status| is a failure status. If |status| is
  // equal to kStatusOk or kStatusTryAgain, this function does not do anything.
  // Always returns the input parameter |status| as the return value.
  //
  // This function is called only from the application thread (from
  // EnqueueFrame() and DequeueFrame()).
  StatusCode SignalFailure(StatusCode status);

  void ReleaseOutputFrame();

  // Decodes all the frames contained in the given temporal unit. Used only in
  // non frame parallel mode.
  StatusCode DecodeTemporalUnit(const TemporalUnit& temporal_unit,
                                const DecoderBuffer** out_ptr);
  // Used only in frame parallel mode. Does the OBU parsing for |data| and
  // schedules the individual frames for decoding in the |frame_thread_pool_|.
  StatusCode ParseAndSchedule(const uint8_t* data, size_t size,
                              int64_t user_private_data,
                              void* buffer_private_data);
  // Decodes the |encoded_frame| and updates the
  // |encoded_frame->temporal_unit|'s parameters if the decoded frame is a
  // displayable frame. Used only in frame parallel mode.
  StatusCode DecodeFrame(EncodedFrame* encoded_frame);

  // Populates |buffer_| with values from |frame|. Adds a reference to |frame|
  // in |output_frame_|.
  StatusCode CopyFrameToOutputBuffer(const RefCountedBufferPtr& frame);
  StatusCode DecodeTiles(const ObuSequenceHeader& sequence_header,
                         const ObuFrameHeader& frame_header,
                         const Vector<TileBuffer>& tile_buffers,
                         const DecoderState& state,
                         FrameScratchBuffer* frame_scratch_buffer,
                         RefCountedBuffer* current_frame);
  // Applies film grain synthesis to the |displayable_frame| and stores the film
  // grain applied frame into |film_grain_frame|. Returns kStatusOk on success.
  StatusCode ApplyFilmGrain(const ObuSequenceHeader& sequence_header,
                            const ObuFrameHeader& frame_header,
                            const RefCountedBufferPtr& displayable_frame,
                            RefCountedBufferPtr* film_grain_frame,
                            ThreadPool* thread_pool);

  bool IsNewSequenceHeader(const ObuParser& obu);

  bool HasFailure() {
    std::lock_guard<std::mutex> lock(mutex_);
    return failure_status_ != kStatusOk;
  }

  // Initializes the |quantizer_matrix_| if necessary and sets
  // |quantizer_matrix_initialized_| to true.
  bool MaybeInitializeQuantizerMatrix(const ObuFrameHeader& frame_header);

  // Allocates and generates the |wedge_masks_| if necessary and sets
  // |wedge_masks_initialized_| to true.
  bool MaybeInitializeWedgeMasks(FrameType frame_type);

  // Elements in this queue cannot be moved with std::move since the
  // |EncodedFrame.temporal_unit| stores a pointer to elements in this queue.
  Queue<TemporalUnit> temporal_units_;
  DecoderState state_;

  DecoderBuffer buffer_ = {};
  // |output_frame_| holds a reference to the output frame on behalf of
  // |buffer_|.
  RefCountedBufferPtr output_frame_;

  // Queue of output frames that are to be returned in the DequeueFrame() calls.
  // If |settings_.output_all_layers| is false, this queue will never contain
  // more than 1 element. This queue is used only when |is_frame_parallel_| is
  // false.
  Queue<RefCountedBufferPtr> output_frame_queue_;

  BufferPool buffer_pool_;
  WedgeMaskArray wedge_masks_;
  bool wedge_masks_initialized_ = false;
  QuantizerMatrix quantizer_matrix_;
  bool quantizer_matrix_initialized_ = false;
  FrameScratchBufferPool frame_scratch_buffer_pool_;

  // Used to synchronize the accesses into |temporal_units_| in order to update
  // the "decoded" state of a temporal unit.
  std::mutex mutex_;
  std::condition_variable decoded_condvar_;
  bool is_frame_parallel_;
  std::unique_ptr<ThreadPool> frame_thread_pool_;

  // In frame parallel mode, there are two primary points of failure:
  //  1) ParseAndSchedule()
  //  2) DecodeTiles()
  // Both of these functions have to respond to the other one failing by
  // aborting whatever they are doing. This variable is used to accomplish that.
  // If |failure_status_| is not kStatusOk, then the two functions will try to
  // abort as early as they can.
  StatusCode failure_status_ = kStatusOk LIBGAV1_GUARDED_BY(mutex_);

  ObuSequenceHeader sequence_header_ = {};
  // If true, sequence_header is valid.
  bool has_sequence_header_ = false;

  const DecoderSettings& settings_;
  bool seen_first_frame_ = false;

  std::vector<int> frame_mean_qps_;
  int frame_mean_qp_ = 0;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_DECODER_IMPL_H_
