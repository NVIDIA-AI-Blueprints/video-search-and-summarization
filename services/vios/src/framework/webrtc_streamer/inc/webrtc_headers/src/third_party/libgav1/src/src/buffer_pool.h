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

#ifndef LIBGAV1_SRC_BUFFER_POOL_H_
#define LIBGAV1_SRC_BUFFER_POOL_H_

#include <algorithm>
#include <array>
#include <cassert>
#include <climits>
#include <condition_variable>  // NOLINT (unapproved c++11 header)
#include <cstdint>
#include <memory>
#include <mutex>  // NOLINT (unapproved c++11 header)

#include "src/dsp/common.h"
#include "src/gav1/decoder_buffer.h"
#include "src/gav1/frame_buffer.h"
#include "src/internal_frame_buffer_list.h"
#include "src/symbol_decoder_context.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/dynamic_buffer.h"
#include "src/utils/reference_info.h"
#include "src/utils/segmentation.h"
#include "src/utils/segmentation_map.h"
#include "src/utils/types.h"
#include "src/utils/vector.h"
#include "src/yuv_buffer.h"

namespace libgav1 {

class BufferPool;

enum FrameState : uint8_t {
  kFrameStateUnknown,
  kFrameStateStarted,
  kFrameStateParsed,
  kFrameStateDecoded
};

// A reference-counted frame buffer. Clients should access it via
// RefCountedBufferPtr, which manages reference counting transparently.
// The alignment requirement is due to the SymbolDecoderContext member
// frame_context_.
class RefCountedBuffer : public MaxAlignedAllocable {
 public:
  // Not copyable or movable.
  RefCountedBuffer(const RefCountedBuffer&) = delete;
  RefCountedBuffer& operator=(const RefCountedBuffer&) = delete;

  // Allocates the YUV buffer. Returns true on success. Returns false on
  // failure. This function ensures the thread safety of the |get_frame_buffer_|
  // call (i.e.) only one |get_frame_buffer_| call will happen at a given time.
  // TODO(b/142583029): In frame parallel mode, we can require the callbacks to
  // be thread safe so that we can remove the thread safety of this function and
  // applications can have fine grained locks.
  //
  // * |width| and |height| are the image dimensions in pixels.
  // * |subsampling_x| and |subsampling_y| (either 0 or 1) specify the
  //   subsampling of the width and height of the chroma planes, respectively.
  // * |left_border|, |right_border|, |top_border|, and |bottom_border| are
  //   the sizes (in pixels) of the borders on the left, right, top, and
  //   bottom sides, respectively.
  //
  // NOTE: The strides are a multiple of 16. Since the first row in each plane
  // is 16-byte aligned, subsequent rows are also 16-byte aligned.
  bool Realloc(int bitdepth, bool is_monochrome, int width, int height,
               int subsampling_x, int subsampling_y, int left_border,
               int right_border, int top_border, int bottom_border);

  YuvBuffer* buffer() { return &yuv_buffer_; }

  // Returns the buffer private data set by the get frame buffer callback when
  // it allocated the YUV buffer.
  void* buffer_private_data() const {
    assert(buffer_private_data_valid_);
    return buffer_private_data_;
  }

  // NOTE: In the current frame, this is the frame_type syntax element in the
  // frame header. In a reference frame, this implements the RefFrameType array
  // in the spec.
  FrameType frame_type() const { return frame_type_; }
  void set_frame_type(FrameType frame_type) { frame_type_ = frame_type; }

  // The sample position for subsampled streams. This is the
  // chroma_sample_position syntax element in the sequence header.
  //
  // NOTE: The decoder does not use chroma_sample_position, but it needs to be
  // passed on to the client in DecoderBuffer.
  ChromaSamplePosition chroma_sample_position() const {
    return chroma_sample_position_;
  }
  void set_chroma_sample_position(ChromaSamplePosition chroma_sample_position) {
    chroma_sample_position_ = chroma_sample_position;
  }

  // Whether the frame can be used as show existing frame in the future.
  bool showable_frame() const { return showable_frame_; }
  void set_showable_frame(bool value) { showable_frame_ = value; }

  // Sets upscaled_width_, frame_width_, frame_height_, render_width_,
  // render_height_, rows4x4_ and columns4x4_ from the corresponding fields
  // in frame_header. Allocates reference_info_.motion_field_reference_frame,
  // reference_info_.motion_field_mv_, and segmentation_map_. Returns true on
  // success, false on failure.
  bool SetFrameDimensions(const ObuFrameHeader& frame_header);

  int32_t upscaled_width() const { return upscaled_width_; }
  int32_t frame_width() const { return frame_width_; }
  int32_t frame_height() const { return frame_height_; }
  // RenderWidth() and RenderHeight() return the render size, which is a hint
  // to the application about the desired display size.
  int32_t render_width() const { return render_width_; }
  int32_t render_height() const { return render_height_; }
  int32_t rows4x4() const { return rows4x4_; }
  int32_t columns4x4() const { return columns4x4_; }

  int spatial_id() const { return spatial_id_; }
  void set_spatial_id(int value) { spatial_id_ = value; }
  int temporal_id() const { return temporal_id_; }
  void set_temporal_id(int value) { temporal_id_ = value; }

  ObuMetadataHdrCll hdr_cll() const { return hdr_cll_; }
  void set_hdr_cll(const ObuMetadataHdrCll& hdr_cll) {
    hdr_cll_set_ = true;
    hdr_cll_ = hdr_cll;
  }
  bool hdr_cll_set() const { return hdr_cll_set_; }

  ObuMetadataHdrMdcv hdr_mdcv() const { return hdr_mdcv_; }
  void set_hdr_mdcv(const ObuMetadataHdrMdcv& hdr_mdcv) {
    hdr_mdcv_set_ = true;
    hdr_mdcv_ = hdr_mdcv;
  }
  bool hdr_mdcv_set() const { return hdr_mdcv_set_; }

  const ObuMetadataItutT35* itut_t35() const { return itut_t35_; }
  bool AddItutT35Metadata(const ObuMetadataItutT35& itut_t35,
                          const uint8_t* const payload) {
    if (itut_t35_count_ >= 8) {
      // Only store the first 8 itut_t35 payloads.
      return true;
    }
    itut_t35_[itut_t35_count_] = itut_t35;
    if (itut_t35.payload_size > 0) {
      if (!itut_t35_payload_[itut_t35_count_].Resize(itut_t35.payload_size)) {
        return false;
      }
      memcpy(itut_t35_payload_[itut_t35_count_].get(), payload,
             itut_t35.payload_size);
      itut_t35_[itut_t35_count_].payload_bytes =
          itut_t35_payload_[itut_t35_count_].get();
    } else {
      itut_t35_[itut_t35_count_].payload_bytes = nullptr;
    }
    ++itut_t35_count_;
    return true;
  }
  int itut_t35_count() const { return itut_t35_count_; }

  SegmentationMap* segmentation_map() { return &segmentation_map_; }
  const SegmentationMap* segmentation_map() const { return &segmentation_map_; }

  // Only the |params| field of each GlobalMotion struct should be used.
  const std::array<GlobalMotion, kNumReferenceFrameTypes>& GlobalMotions()
      const {
    return global_motion_;
  }
  // Saves the GlobalMotion array. Only the |params| field of each GlobalMotion
  // struct is saved.
  void SetGlobalMotions(
      const std::array<GlobalMotion, kNumReferenceFrameTypes>& global_motions);

  // Returns the saved CDF tables.
  const SymbolDecoderContext& FrameContext() const { return frame_context_; }
  // Saves the CDF tables. The intra_frame_y_mode_cdf table is reset to the
  // default. The last entry in each table, representing the symbol count for
  // that context, is set to 0.
  void SetFrameContext(const SymbolDecoderContext& context);

  const std::array<int8_t, kNumReferenceFrameTypes>& loop_filter_ref_deltas()
      const {
    return loop_filter_ref_deltas_;
  }
  const std::array<int8_t, kLoopFilterMaxModeDeltas>& loop_filter_mode_deltas()
      const {
    return loop_filter_mode_deltas_;
  }
  // Saves the ref_deltas and mode_deltas arrays in loop_filter.
  void SetLoopFilterDeltas(const LoopFilter& loop_filter) {
    loop_filter_ref_deltas_ = loop_filter.ref_deltas;
    loop_filter_mode_deltas_ = loop_filter.mode_deltas;
  }

  // Copies the saved values of the following fields to the Segmentation
  // struct: feature_enabled, feature_data, segment_id_pre_skip, and
  // last_active_segment_id. The other fields are left unchanged.
  void GetSegmentationParameters(Segmentation* segmentation) const;
  // Saves the feature_enabled, feature_data, segment_id_pre_skip, and
  // last_active_segment_id fields of the Segmentation struct.
  void SetSegmentationParameters(const Segmentation& segmentation);

  const FilmGrainParams& film_grain_params() const {
    return film_grain_params_;
  }
  void set_film_grain_params(const FilmGrainParams& params) {
    film_grain_params_ = params;
  }

  const ReferenceInfo* reference_info() const { return &reference_info_; }
  ReferenceInfo* reference_info() { return &reference_info_; }

  // This will wake up the WaitUntil*() functions and make them return false.
  void Abort() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      abort_ = true;
    }
    parsed_condvar_.notify_all();
    decoded_condvar_.notify_all();
    progress_row_condvar_.notify_all();
  }

  void SetFrameState(FrameState frame_state) {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      frame_state_ = frame_state;
    }
    if (frame_state == kFrameStateParsed) {
      parsed_condvar_.notify_all();
    } else if (frame_state == kFrameStateDecoded) {
      decoded_condvar_.notify_all();
      progress_row_condvar_.notify_all();
    }
  }

  // Sets the progress of this frame to |progress_row| and notifies any threads
  // that may be waiting on rows <= |progress_row|.
  void SetProgress(int progress_row) {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (progress_row_ >= progress_row) return;
      progress_row_ = progress_row;
    }
    progress_row_condvar_.notify_all();
  }

  void MarkFrameAsStarted() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (frame_state_ != kFrameStateUnknown) return;
    frame_state_ = kFrameStateStarted;
  }

  // All the WaitUntil* functions will return true if the desired wait state was
  // reached successfully. If the return value is false, then the caller must
  // assume that the wait was not successful and try to stop whatever they are
  // doing as early as possible.

  // Waits until the frame has been parsed.
  bool WaitUntilParsed() {
    std::unique_lock<std::mutex> lock(mutex_);
    while (frame_state_ < kFrameStateParsed && !abort_) {
      parsed_condvar_.wait(lock);
    }
    return !abort_;
  }

  // Waits until the |progress_row| has been decoded (as indicated either by
  // |progress_row_| or |frame_state_|). |progress_row_cache| must not be
  // nullptr and will be populated with the value of |progress_row_| after the
  // wait.
  //
  // Typical usage of |progress_row_cache| is as follows:
  //  * Initialize |*progress_row_cache| to INT_MIN.
  //  * Call WaitUntil only if |*progress_row_cache| < |progress_row|.
  bool WaitUntil(int progress_row, int* progress_row_cache) {
    // If |progress_row| is negative, it means that the wait is on the top
    // border to be available. The top border will be available when row 0 has
    // been decoded. So we can simply wait on row 0 instead.
    progress_row = std::max(progress_row, 0);
    std::unique_lock<std::mutex> lock(mutex_);
    while (progress_row_ < progress_row && frame_state_ != kFrameStateDecoded &&
           !abort_) {
      progress_row_condvar_.wait(lock);
    }
    // Once |frame_state_| reaches kFrameStateDecoded, |progress_row_| may no
    // longer be updated. So we set |*progress_row_cache| to INT_MAX in that
    // case.
    *progress_row_cache =
        (frame_state_ != kFrameStateDecoded) ? progress_row_ : INT_MAX;
    return !abort_;
  }

  // Waits until the entire frame has been decoded.
  bool WaitUntilDecoded() {
    std::unique_lock<std::mutex> lock(mutex_);
    while (frame_state_ != kFrameStateDecoded && !abort_) {
      decoded_condvar_.wait(lock);
    }
    return !abort_;
  }

 private:
  friend class BufferPool;

  // Methods for BufferPool:
  RefCountedBuffer();
  ~RefCountedBuffer();
  void SetBufferPool(BufferPool* pool);
  static void ReturnToBufferPool(RefCountedBuffer* ptr);

  BufferPool* pool_ = nullptr;
  bool buffer_private_data_valid_ = false;
  void* buffer_private_data_ = nullptr;
  YuvBuffer yuv_buffer_;
  bool in_use_ = false;  // Only used by BufferPool.

  std::mutex mutex_;
  FrameState frame_state_ = kFrameStateUnknown LIBGAV1_GUARDED_BY(mutex_);
  int progress_row_ = -1 LIBGAV1_GUARDED_BY(mutex_);
  // Signaled when progress_row_ is updated or when frame_state_ is set to
  // kFrameStateDecoded.
  std::condition_variable progress_row_condvar_;
  // Signaled when the frame state is set to kFrameStateParsed.
  std::condition_variable parsed_condvar_;
  // Signaled when the frame state is set to kFrameStateDecoded.
  std::condition_variable decoded_condvar_;
  bool abort_ = false LIBGAV1_GUARDED_BY(mutex_);

  FrameType frame_type_ = kFrameKey;
  ChromaSamplePosition chroma_sample_position_ = kChromaSamplePositionUnknown;
  bool showable_frame_ = false;

  int32_t upscaled_width_ = 0;
  int32_t frame_width_ = 0;
  int32_t frame_height_ = 0;
  int32_t render_width_ = 0;
  int32_t render_height_ = 0;
  int32_t columns4x4_ = 0;
  int32_t rows4x4_ = 0;
  int spatial_id_ = 0;
  int temporal_id_ = 0;

  ObuMetadataHdrCll hdr_cll_ = {};
  bool hdr_cll_set_ = false;  // Set to true when set_hdr_cll() is called.
  ObuMetadataHdrMdcv hdr_mdcv_ = {};
  bool hdr_mdcv_set_ = false;  // Set to true when set_hdr_mdcv() is called.
  ObuMetadataItutT35 itut_t35_[8] = {};
  DynamicBuffer<uint8_t> itut_t35_payload_[8];
  int itut_t35_count_ = 0;  // Set when add_itut_t35() is called.

  // segmentation_map_ contains a rows4x4_ by columns4x4_ 2D array.
  SegmentationMap segmentation_map_;

  // Only the |params| field of each GlobalMotion struct is used.
  // global_motion_[0] (for kReferenceFrameIntra) is not used.
  std::array<GlobalMotion, kNumReferenceFrameTypes> global_motion_ = {};
  SymbolDecoderContext frame_context_;
  std::array<int8_t, kNumReferenceFrameTypes> loop_filter_ref_deltas_;
  std::array<int8_t, kLoopFilterMaxModeDeltas> loop_filter_mode_deltas_;
  // Only the feature_enabled, feature_data, segment_id_pre_skip, and
  // last_active_segment_id fields of the Segmentation struct are used.
  //
  // Note: The spec only requires that we save feature_enabled and
  // feature_data. Since segment_id_pre_skip and last_active_segment_id depend
  // on feature_enabled only, we also save their values as an optimization.
  Segmentation segmentation_ = {};
  FilmGrainParams film_grain_params_ = {};
  ReferenceInfo reference_info_;
};

// RefCountedBufferPtr contains a reference to a RefCountedBuffer.
//
// Note: For simplicity, RefCountedBufferPtr is implemented as a
// std::shared_ptr<RefCountedBuffer>. This requires a heap allocation of the
// control block for std::shared_ptr. To avoid that heap allocation, we can
// add a |ref_count_| field to RefCountedBuffer and implement a custom
// RefCountedBufferPtr class.
using RefCountedBufferPtr = std::shared_ptr<RefCountedBuffer>;

// BufferPool maintains a pool of RefCountedBuffers.
class BufferPool {
 public:
  BufferPool(FrameBufferSizeChangedCallback on_frame_buffer_size_changed,
             GetFrameBufferCallback get_frame_buffer,
             ReleaseFrameBufferCallback release_frame_buffer,
             void* callback_private_data);

  // Not copyable or movable.
  BufferPool(const BufferPool&) = delete;
  BufferPool& operator=(const BufferPool&) = delete;

  ~BufferPool();

  LIBGAV1_MUST_USE_RESULT bool OnFrameBufferSizeChanged(
      int bitdepth, Libgav1ImageFormat image_format, int width, int height,
      int left_border, int right_border, int top_border, int bottom_border);

  // Finds a free buffer in the buffer pool and returns a reference to the free
  // buffer. If there is no free buffer, returns a null pointer. This function
  // is thread safe.
  RefCountedBufferPtr GetFreeBuffer();

  // Aborts all the buffers that are in use.
  void Abort();

 private:
  friend class RefCountedBuffer;

  // Returns an unused buffer to the buffer pool. Called by RefCountedBuffer
  // only. This function is thread safe.
  void ReturnUnusedBuffer(RefCountedBuffer* buffer);

  // Used to make the following functions thread safe: GetFreeBuffer(),
  // ReturnUnusedBuffer(), RefCountedBuffer::Realloc().
  std::mutex mutex_;

  // Storing a RefCountedBuffer object in a Vector is complicated because of the
  // copy/move semantics. So the simplest way around that is to store a list of
  // pointers in the vector.
  Vector<RefCountedBuffer*> buffers_ LIBGAV1_GUARDED_BY(mutex_);
  InternalFrameBufferList internal_frame_buffers_;

  // Frame buffer callbacks.
  FrameBufferSizeChangedCallback on_frame_buffer_size_changed_;
  GetFrameBufferCallback get_frame_buffer_;
  ReleaseFrameBufferCallback release_frame_buffer_;
  // Private data associated with the frame buffer callbacks.
  void* callback_private_data_;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_BUFFER_POOL_H_
