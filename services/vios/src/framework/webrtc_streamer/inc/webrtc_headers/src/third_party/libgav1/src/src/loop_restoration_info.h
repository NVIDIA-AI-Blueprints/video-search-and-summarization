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

#ifndef LIBGAV1_SRC_LOOP_RESTORATION_INFO_H_
#define LIBGAV1_SRC_LOOP_RESTORATION_INFO_H_

#include <array>
#include <cstdint>

#include "src/dsp/common.h"
#include "src/symbol_decoder_context.h"
#include "src/utils/constants.h"
#include "src/utils/dynamic_buffer.h"
#include "src/utils/entropy_decoder.h"
#include "src/utils/types.h"

namespace libgav1 {

struct LoopRestorationUnitInfo {
  int row_start;
  int row_end;
  int column_start;
  int column_end;
};

class LoopRestorationInfo {
 public:
  LoopRestorationInfo() = default;

  // Non copyable/movable.
  LoopRestorationInfo(const LoopRestorationInfo&) = delete;
  LoopRestorationInfo& operator=(const LoopRestorationInfo&) = delete;
  LoopRestorationInfo(LoopRestorationInfo&&) = delete;
  LoopRestorationInfo& operator=(LoopRestorationInfo&&) = delete;

  bool Reset(const LoopRestoration* loop_restoration, uint32_t width,
             uint32_t height, int8_t subsampling_x, int8_t subsampling_y,
             bool is_monochrome);
  // Populates the |unit_info| for the super block at |row4x4|, |column4x4|.
  // Returns true on success, false otherwise.
  bool PopulateUnitInfoForSuperBlock(Plane plane, BlockSize block_size,
                                     bool is_superres_scaled,
                                     uint8_t superres_scale_denominator,
                                     int row4x4, int column4x4,
                                     LoopRestorationUnitInfo* unit_info) const;
  void ReadUnitCoefficients(EntropyDecoder* reader,
                            SymbolDecoderContext* symbol_decoder_context,
                            Plane plane, int unit_id,
                            std::array<RestorationUnitInfo, kMaxPlanes>*
                                reference_unit_info);  // 5.11.58.
  void ReadWienerInfo(
      EntropyDecoder* reader, Plane plane, int unit_id,
      std::array<RestorationUnitInfo, kMaxPlanes>* reference_unit_info);
  void ReadSgrProjInfo(
      EntropyDecoder* reader, Plane plane, int unit_id,
      std::array<RestorationUnitInfo, kMaxPlanes>* reference_unit_info);

  // Getters.
  const RestorationUnitInfo* loop_restoration_info(Plane plane,
                                                   int unit_id) const {
    return &loop_restoration_info_[plane][unit_id];
  }

  int num_horizontal_units(Plane plane) const {
    return num_horizontal_units_[plane];
  }
  int num_vertical_units(Plane plane) const {
    return num_vertical_units_[plane];
  }
  int num_units(Plane plane) const { return num_units_[plane]; }

 private:
  // If plane_needs_filtering_[plane] is true, loop_restoration_info_[plane]
  // points to an array of num_units_[plane] elements.
  RestorationUnitInfo* loop_restoration_info_[kMaxPlanes];
  // Owns the memory that loop_restoration_info_[plane] points to.
  DynamicBuffer<RestorationUnitInfo> loop_restoration_info_buffer_;
  bool plane_needs_filtering_[kMaxPlanes];
  const LoopRestoration* loop_restoration_;
  int8_t subsampling_x_;
  int8_t subsampling_y_;
  int num_horizontal_units_[kMaxPlanes];
  int num_vertical_units_[kMaxPlanes];
  int num_units_[kMaxPlanes];
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_LOOP_RESTORATION_INFO_H_
