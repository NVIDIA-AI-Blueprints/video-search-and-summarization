/*
 * Copyright 2020 The libgav1 Authors
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

#ifndef LIBGAV1_SRC_DECODER_STATE_H_
#define LIBGAV1_SRC_DECODER_STATE_H_

#include <array>
#include <cstdint>

#include "src/buffer_pool.h"
#include "src/utils/constants.h"

namespace libgav1 {

struct DecoderState {
  // Section 7.20. Updates frames in the reference_frame array with
  // |current_frame|, based on the |refresh_frame_flags| bitmask.
  void UpdateReferenceFrames(const RefCountedBufferPtr& current_frame,
                             int refresh_frame_flags) {
    for (int ref_index = 0, mask = refresh_frame_flags; mask != 0;
         ++ref_index, mask >>= 1) {
      if ((mask & 1) != 0) {
        reference_frame_id[ref_index] = current_frame_id;
        reference_frame[ref_index] = current_frame;
        reference_order_hint[ref_index] = order_hint;
      }
    }
  }

  // Clears all the reference frames.
  void ClearReferenceFrames() {
    reference_frame_id = {};
    reference_order_hint = {};
    for (int ref_index = 0; ref_index < kNumReferenceFrameTypes; ++ref_index) {
      reference_frame[ref_index] = nullptr;
    }
  }

  // reference_frame_id and current_frame_id have meaningful values and are used
  // in checks only if sequence_header_.frame_id_numbers_present is true. If
  // sequence_header_.frame_id_numbers_present is false, reference_frame_id and
  // current_frame_id are assigned the default value 0 and are not used in
  // checks.
  std::array<uint16_t, kNumReferenceFrameTypes> reference_frame_id = {};
  // A valid value of current_frame_id is an unsigned integer of at most 16
  // bits. -1 indicates current_frame_id is not initialized.
  int current_frame_id = -1;
  // The RefOrderHint array variable in the spec.
  std::array<uint8_t, kNumReferenceFrameTypes> reference_order_hint = {};
  // The OrderHint variable in the spec. Its value comes from either the
  // order_hint syntax element in the uncompressed header (if
  // show_existing_frame is false) or RefOrderHint[ frame_to_show_map_idx ]
  // (if show_existing_frame is true and frame_type is KEY_FRAME). See Section
  // 5.9.2 and Section 7.4.
  //
  // NOTE: When show_existing_frame is false, it is often more convenient to
  // just use the order_hint field of the frame header as OrderHint. So this
  // field is mainly used to update the reference_order_hint array in
  // UpdateReferenceFrames().
  uint8_t order_hint = 0;
  // reference_frame_sign_bias[i] (a boolean) specifies the intended direction
  // of the motion vector in time for each reference frame.
  // * |false| indicates that the reference frame is a forwards reference (i.e.
  //   the reference frame is expected to be output before the current frame);
  // * |true| indicates that the reference frame is a backwards reference.
  // Note: reference_frame_sign_bias[0] (for kReferenceFrameIntra) is not used.
  std::array<bool, kNumReferenceFrameTypes> reference_frame_sign_bias = {};
  // The RefValid[i] variable in the spec does not need to be stored explicitly.
  // If the RefValid[i] variable in the spec is 0, then reference_frame[i] is a
  // null pointer. (Whenever the spec sets the RefValid[i] variable to 0, we set
  // reference_frame[i] to a null pointer.) If the RefValid[i] variable in the
  // spec is 1, then reference_frame[i] contains a frame buffer pointer.
  std::array<RefCountedBufferPtr, kNumReferenceFrameTypes> reference_frame;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_DECODER_STATE_H_
