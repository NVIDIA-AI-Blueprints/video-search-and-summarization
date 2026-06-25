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

#ifndef LIBGAV1_SRC_OBU_PARSER_H_
#define LIBGAV1_SRC_OBU_PARSER_H_

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <type_traits>
#include <utility>

#include "src/buffer_pool.h"
#include "src/decoder_state.h"
#include "src/gav1/decoder_buffer.h"
#include "src/gav1/status_code.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/constants.h"
#include "src/utils/memory.h"
#include "src/utils/raw_bit_reader.h"
#include "src/utils/types.h"
#include "src/utils/vector.h"

namespace libgav1 {

// structs and enums related to Open Bitstream Units (OBU).

enum {
  kMinimumMajorBitstreamLevel = 2,
  kSelectScreenContentTools = 2,
  kSelectIntegerMv = 2,
  kLoopRestorationTileSizeMax = 256,
  kGlobalMotionAlphaBits = 12,
  kGlobalMotionTranslationBits = 12,
  kGlobalMotionTranslationOnlyBits = 9,
  kGlobalMotionAlphaPrecisionBits = 15,
  kGlobalMotionTranslationPrecisionBits = 6,
  kGlobalMotionTranslationOnlyPrecisionBits = 3,
  kMaxTileWidth = 4096,
  kMaxTileArea = 4096 * 2304,
  kPrimaryReferenceNone = 7,
  // A special value of the scalability_mode_idc syntax element that indicates
  // the picture prediction structure is specified in scalability_structure().
  kScalabilitySS = 14
};  // anonymous enum

struct ObuHeader {
  ObuType type;
  bool has_extension;
  bool has_size_field;
  int8_t temporal_id;
  int8_t spatial_id;
};

enum BitstreamProfile : uint8_t {
  kProfile0,
  kProfile1,
  kProfile2,
  kMaxProfiles
};

// In the bitstream the level is encoded in five bits: the first three bits
// encode |major| - 2 and the last two bits encode |minor|.
//
// If the mapped level (major.minor) is in the tables in Annex A.3, there are
// bitstream conformance requirements on the maximum or minimum values of
// several variables. The encoded value of 31 (which corresponds to the mapped
// level 9.3) is the "maximum parameters" level and imposes no level-based
// constraints on the bitstream.
struct BitStreamLevel {
  uint8_t major;  // Range: 2-9.
  uint8_t minor;  // Range: 0-3.
};

struct ColorConfig {
  int8_t bitdepth;
  bool is_monochrome;
  ColorPrimary color_primary;
  TransferCharacteristics transfer_characteristics;
  MatrixCoefficients matrix_coefficients;
  // A binary value (0 or 1) that is associated with the VideoFullRangeFlag
  // variable specified in ISO/IEC 23091-4/ITUT H.273.
  // * 0: the studio swing representation.
  // * 1: the full swing representation.
  ColorRange color_range;
  int8_t subsampling_x;
  int8_t subsampling_y;
  ChromaSamplePosition chroma_sample_position;
  bool separate_uv_delta_q;
};

struct TimingInfo {
  uint32_t num_units_in_tick;
  uint32_t time_scale;
  bool equal_picture_interval;
  uint32_t num_ticks_per_picture;
};

struct DecoderModelInfo {
  uint8_t encoder_decoder_buffer_delay_length;
  uint32_t num_units_in_decoding_tick;
  uint8_t buffer_removal_time_length;
  uint8_t frame_presentation_time_length;
};

struct OperatingParameters {
  uint32_t decoder_buffer_delay[kMaxOperatingPoints];
  uint32_t encoder_buffer_delay[kMaxOperatingPoints];
  bool low_delay_mode_flag[kMaxOperatingPoints];
};

struct ObuSequenceHeader {
  // Section 7.5:
  //   Within a particular coded video sequence, the contents of
  //   sequence_header_obu must be bit-identical each time the sequence header
  //   appears except for the contents of operating_parameters_info. A new
  //   coded video sequence is required if the sequence header parameters
  //   change.
  //
  // IMPORTANT: ParametersChanged() is implemented with a memcmp() call. For
  // this to work, this object and the |old| object must be initialized with
  // an empty brace-enclosed list, which initializes any padding to zero bits.
  // See https://en.cppreference.com/w/cpp/language/zero_initialization.
  bool ParametersChanged(const ObuSequenceHeader& old) const;

  BitstreamProfile profile;
  bool still_picture;
  bool reduced_still_picture_header;
  int operating_points;
  int operating_point_idc[kMaxOperatingPoints];
  BitStreamLevel level[kMaxOperatingPoints];
  int8_t tier[kMaxOperatingPoints];
  int8_t frame_width_bits;
  int8_t frame_height_bits;
  int32_t max_frame_width;
  int32_t max_frame_height;
  bool frame_id_numbers_present;
  int8_t frame_id_length_bits;
  int8_t delta_frame_id_length_bits;
  bool use_128x128_superblock;
  bool enable_filter_intra;
  bool enable_intra_edge_filter;
  bool enable_interintra_compound;
  bool enable_masked_compound;
  bool enable_warped_motion;
  bool enable_dual_filter;
  bool enable_order_hint;
  // If enable_order_hint is true, order_hint_bits is in the range [1, 8].
  // If enable_order_hint is false, order_hint_bits is 0.
  int8_t order_hint_bits;
  // order_hint_shift_bits equals (32 - order_hint_bits) % 32.
  // This is used frequently in GetRelativeDistance().
  uint8_t order_hint_shift_bits;
  bool enable_jnt_comp;
  bool enable_ref_frame_mvs;
  bool choose_screen_content_tools;
  int8_t force_screen_content_tools;
  bool choose_integer_mv;
  int8_t force_integer_mv;
  bool enable_superres;
  bool enable_cdef;
  bool enable_restoration;
  ColorConfig color_config;
  bool timing_info_present_flag;
  TimingInfo timing_info;
  bool decoder_model_info_present_flag;
  DecoderModelInfo decoder_model_info;
  bool decoder_model_present_for_operating_point[kMaxOperatingPoints];
  bool initial_display_delay_present_flag;
  uint8_t initial_display_delay[kMaxOperatingPoints];
  bool film_grain_params_present;

  // IMPORTANT: the operating_parameters member must be at the end of the
  // struct so that ParametersChanged() can be implemented with a memcmp()
  // call.
  OperatingParameters operating_parameters;
};
// Verify it is safe to use offsetof with ObuSequenceHeader and to use memcmp
// to compare two ObuSequenceHeader objects.
static_assert(std::is_standard_layout<ObuSequenceHeader>::value, "");
// Verify operating_parameters is the last member of ObuSequenceHeader. The
// second assertion assumes that ObuSequenceHeader has no padding after the
// operating_parameters field. The first assertion is a sufficient condition
// for ObuSequenceHeader to have no padding after the operating_parameters
// field.
static_assert(alignof(ObuSequenceHeader) == alignof(OperatingParameters), "");
static_assert(sizeof(ObuSequenceHeader) ==
                  offsetof(ObuSequenceHeader, operating_parameters) +
                      sizeof(OperatingParameters),
              "");

struct TileBuffer {
  const uint8_t* data;
  size_t size;
};

enum MetadataType : uint8_t {
  // 0 is reserved for AOM use.
  kMetadataTypeHdrContentLightLevel = 1,
  kMetadataTypeHdrMasteringDisplayColorVolume = 2,
  kMetadataTypeScalability = 3,
  kMetadataTypeItutT35 = 4,
  kMetadataTypeTimecode = 5,
  // 6-31 are unregistered user private.
  // 32 and greater are reserved for AOM use.
};

class ObuParser : public Allocable {
 public:
  ObuParser(const uint8_t* const data, size_t size, int operating_point,
            BufferPool* const buffer_pool, DecoderState* const decoder_state)
      : data_(data),
        size_(size),
        operating_point_(operating_point),
        buffer_pool_(buffer_pool),
        decoder_state_(*decoder_state) {}

  // Not copyable or movable.
  ObuParser(const ObuParser& rhs) = delete;
  ObuParser& operator=(const ObuParser& rhs) = delete;

  // Returns true if there is more data that needs to be parsed.
  bool HasData() const;

  // Parses a sequence of Open Bitstream Units until a decodable frame is found
  // (or until the end of stream is reached). A decodable frame is considered to
  // be found when one of the following happens:
  //   * A kObuFrame is seen.
  //   * The kObuTileGroup containing the last tile is seen.
  //   * A kFrameHeader with show_existing_frame = true is seen.
  //
  // If the parsing is successful, relevant fields will be populated. The fields
  // are valid only if the return value is kStatusOk. Returns kStatusOk on
  // success, an error status otherwise. On success, |current_frame| will be
  // populated with a valid frame buffer.
  StatusCode ParseOneFrame(RefCountedBufferPtr* current_frame);

  // Get the AV1CodecConfigurationBox as described in
  // https://aomediacodec.github.io/av1-isobmff/#av1codecconfigurationbox. This
  // does minimal bitstream parsing to obtain the necessary information to
  // generate the av1c box. Returns a std::unique_ptr that contains the av1c
  // data on success, nullptr otherwise. |av1c_size| must not be nullptr and
  // will contain the size of the buffer pointed to by the std::unique_ptr.
  static std::unique_ptr<uint8_t[]> GetAV1CodecConfigurationBox(
      const uint8_t* data, size_t size, size_t* av1c_size);

  // Getters. Only valid if ParseOneFrame() completes successfully.
  const Vector<ObuHeader>& obu_headers() const { return obu_headers_; }
  const ObuSequenceHeader& sequence_header() const { return sequence_header_; }
  const ObuFrameHeader& frame_header() const { return frame_header_; }
  const Vector<TileBuffer>& tile_buffers() const { return tile_buffers_; }
  // Returns true if the last call to ParseOneFrame() encountered a sequence
  // header change.
  bool sequence_header_changed() const { return sequence_header_changed_; }

  // Setters.
  void set_sequence_header(const ObuSequenceHeader& sequence_header) {
    sequence_header_ = sequence_header;
    has_sequence_header_ = true;
  }

  // Moves |tile_buffers_| into |tile_buffers|.
  void MoveTileBuffers(Vector<TileBuffer>* tile_buffers) {
    *tile_buffers = std::move(tile_buffers_);
  }

 private:
  // Initializes the bit reader. This is a function of its own to make unit
  // testing of private functions simpler.
  LIBGAV1_MUST_USE_RESULT bool InitBitReader(const uint8_t* data, size_t size);

  // Parse helper functions.
  bool ParseHeader();  // 5.3.2 and 5.3.3.
  bool ParseColorConfig(ObuSequenceHeader* sequence_header);       // 5.5.2.
  bool ParseTimingInfo(ObuSequenceHeader* sequence_header);        // 5.5.3.
  bool ParseDecoderModelInfo(ObuSequenceHeader* sequence_header);  // 5.5.4.
  bool ParseOperatingParameters(ObuSequenceHeader* sequence_header,
                                int index);          // 5.5.5.
  bool ParseSequenceHeader(bool seen_frame_header);  // 5.5.1.
  bool ParseFrameParameters();                       // 5.9.2, 5.9.7 and 5.9.10.
  void MarkInvalidReferenceFrames();                 // 5.9.4.
  bool ParseFrameSizeAndRenderSize();                // 5.9.5 and 5.9.6.
  bool ParseSuperResParametersAndComputeImageSize();  // 5.9.8 and 5.9.9.
  // Checks the bitstream conformance requirement in Section 6.8.6.
  bool ValidateInterFrameSize() const;
  bool ParseReferenceOrderHint();
  static int FindLatestBackwardReference(
      const int current_frame_hint,
      const std::array<int, kNumReferenceFrameTypes>& shifted_order_hints,
      const std::array<bool, kNumReferenceFrameTypes>& used_frame);
  static int FindEarliestBackwardReference(
      const int current_frame_hint,
      const std::array<int, kNumReferenceFrameTypes>& shifted_order_hints,
      const std::array<bool, kNumReferenceFrameTypes>& used_frame);
  static int FindLatestForwardReference(
      const int current_frame_hint,
      const std::array<int, kNumReferenceFrameTypes>& shifted_order_hints,
      const std::array<bool, kNumReferenceFrameTypes>& used_frame);
  static int FindReferenceWithSmallestOutputOrder(
      const std::array<int, kNumReferenceFrameTypes>& shifted_order_hints);
  bool SetFrameReferences(int8_t last_frame_idx,
                          int8_t gold_frame_idx);  // 7.8.
  bool ParseLoopFilterParameters();                // 5.9.11.
  bool ParseDeltaQuantizer(int8_t* delta);         // 5.9.13.
  bool ParseQuantizerParameters();                 // 5.9.12.
  bool ParseSegmentationParameters();              // 5.9.14.
  bool ParseQuantizerIndexDeltaParameters();       // 5.9.17.
  bool ParseLoopFilterDeltaParameters();           // 5.9.18.
  void ComputeSegmentLosslessAndQIndex();
  bool ParseCdefParameters();             // 5.9.19.
  bool ParseLoopRestorationParameters();  // 5.9.20.
  bool ParseTxModeSyntax();               // 5.9.21.
  bool ParseFrameReferenceModeSyntax();   // 5.9.23.
  // Returns whether skip mode is allowed. When it returns true, it also sets
  // the frame_header_.skip_mode_frame array.
  bool IsSkipModeAllowed();
  bool ParseSkipModeParameters();  // 5.9.22.
  bool ReadAllowWarpedMotion();
  bool ParseGlobalParamSyntax(
      int ref, int index,
      const std::array<GlobalMotion, kNumReferenceFrameTypes>&
          prev_global_motions);        // 5.9.25.
  bool ParseGlobalMotionParameters();  // 5.9.24.
  bool ParseFilmGrainParameters();     // 5.9.30.
  bool ParseTileInfoSyntax();          // 5.9.15.
  bool ParseFrameHeader();             // 5.9.
  // |data| and |size| specify the payload data of the padding OBU.
  // NOTE: Although the payload data is available in the bit_reader_ member,
  // it is also passed to ParsePadding() as function parameters so that
  // ParsePadding() can find the trailing bit of the OBU and skip over the
  // payload data as an opaque chunk of data.
  bool ParsePadding(const uint8_t* data, size_t size);  // 5.7.
  bool ParseMetadataScalability();                      // 5.8.5 and 5.8.6.
  bool ParseMetadataTimecode();                         // 5.8.7.
  // |data| and |size| specify the payload data of the metadata OBU.
  // NOTE: Although the payload data is available in the bit_reader_ member,
  // it is also passed to ParseMetadata() as function parameters so that
  // ParseMetadata() can find the trailing bit of the OBU and either extract
  // or skip over the payload data as an opaque chunk of data.
  bool ParseMetadata(const uint8_t* data, size_t size);  // 5.8.
  // Adds and populates the TileBuffer for each tile in the tile group and
  // updates |next_tile_group_start_|
  bool AddTileBuffers(int start, int end, size_t total_size,
                      size_t tg_header_size, size_t bytes_consumed_so_far);
  bool ParseTileGroup(size_t size, size_t bytes_consumed_so_far);  // 5.11.1.

  // Populates |current_frame_| from the |buffer_pool_| if |current_frame_| is
  // nullptr. Does not do anything otherwise. Returns true on success, false
  // otherwise.
  bool EnsureCurrentFrameIsNotNull();

  // Parses the basic bitstream information from the given AV1 stream in |data|.
  // This is used for generating the AV1CodecConfigurationBox.
  static StatusCode ParseBasicStreamInfo(const uint8_t* data, size_t size,
                                         ObuSequenceHeader* sequence_header,
                                         size_t* sequence_header_offset,
                                         size_t* sequence_header_size);

  // Parser elements.
  std::unique_ptr<RawBitReader> bit_reader_;
  const uint8_t* data_;
  size_t size_;
  const int operating_point_;

  // OBU elements. Only valid if ParseOneFrame() completes successfully.
  Vector<ObuHeader> obu_headers_;
  ObuSequenceHeader sequence_header_ = {};
  ObuFrameHeader frame_header_ = {};
  Vector<TileBuffer> tile_buffers_;
  // The expected starting tile number of the next Tile Group.
  int next_tile_group_start_ = 0;
  // If true, the sequence_header_ field is valid.
  bool has_sequence_header_ = false;
  // If true, it means that the last call to ParseOneFrame() encountered a
  // sequence header change.
  bool sequence_header_changed_ = false;
  // If true, the obu_extension_flag syntax element in the OBU header must be
  // 0. Set to true when parsing a sequence header if OperatingPointIdc is 0.
  bool extension_disallowed_ = false;

  BufferPool* const buffer_pool_;
  DecoderState& decoder_state_;
  // Used by ParseOneFrame() to populate the current frame that is being
  // decoded. The invariant maintained is that this variable will be nullptr at
  // the beginning and at the end of each call to ParseOneFrame(). This ensures
  // that the ObuParser is not holding on to any references to the current
  // frame once the ParseOneFrame() call is complete.
  RefCountedBufferPtr current_frame_;

  // For unit testing private functions.
  friend class ObuParserTest;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_OBU_PARSER_H_
