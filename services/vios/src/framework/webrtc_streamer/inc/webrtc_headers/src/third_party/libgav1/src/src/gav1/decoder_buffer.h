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

#ifndef LIBGAV1_SRC_GAV1_DECODER_BUFFER_H_
#define LIBGAV1_SRC_GAV1_DECODER_BUFFER_H_

#if defined(__cplusplus)
#include <cstdint>
#else
#include <stdint.h>
#endif  // defined(__cplusplus)

#include "gav1/symbol_visibility.h"

// All the declarations in this file are part of the public ABI.

// The documentation for the enum values in this file can be found in Section
// 6.4.2 of the AV1 spec.

typedef enum Libgav1ChromaSamplePosition {
  kLibgav1ChromaSamplePositionUnknown,
  kLibgav1ChromaSamplePositionVertical,
  kLibgav1ChromaSamplePositionColocated,
  kLibgav1ChromaSamplePositionReserved
} Libgav1ChromaSamplePosition;

typedef enum Libgav1ImageFormat {
  kLibgav1ImageFormatYuv420,
  kLibgav1ImageFormatYuv422,
  kLibgav1ImageFormatYuv444,
  kLibgav1ImageFormatMonochrome400
} Libgav1ImageFormat;

typedef enum Libgav1ColorPrimary {
  // 0 is reserved.
  kLibgav1ColorPrimaryBt709 = 1,
  kLibgav1ColorPrimaryUnspecified,
  // 3 is reserved.
  kLibgav1ColorPrimaryBt470M = 4,
  kLibgav1ColorPrimaryBt470Bg,
  kLibgav1ColorPrimaryBt601,
  kLibgav1ColorPrimarySmpte240,
  kLibgav1ColorPrimaryGenericFilm,
  kLibgav1ColorPrimaryBt2020,
  kLibgav1ColorPrimaryXyz,
  kLibgav1ColorPrimarySmpte431,
  kLibgav1ColorPrimarySmpte432,
  // 13-21 are reserved.
  kLibgav1ColorPrimaryEbu3213 = 22,
  // 23-254 are reserved.
  kLibgav1MaxColorPrimaries = 255
} Libgav1ColorPrimary;

typedef enum Libgav1TransferCharacteristics {
  // 0 is reserved.
  kLibgav1TransferCharacteristicsBt709 = 1,
  kLibgav1TransferCharacteristicsUnspecified,
  // 3 is reserved.
  kLibgav1TransferCharacteristicsBt470M = 4,
  kLibgav1TransferCharacteristicsBt470Bg,
  kLibgav1TransferCharacteristicsBt601,
  kLibgav1TransferCharacteristicsSmpte240,
  kLibgav1TransferCharacteristicsLinear,
  kLibgav1TransferCharacteristicsLog100,
  kLibgav1TransferCharacteristicsLog100Sqrt10,
  kLibgav1TransferCharacteristicsIec61966,
  kLibgav1TransferCharacteristicsBt1361,
  kLibgav1TransferCharacteristicsSrgb,
  kLibgav1TransferCharacteristicsBt2020TenBit,
  kLibgav1TransferCharacteristicsBt2020TwelveBit,
  kLibgav1TransferCharacteristicsSmpte2084,
  kLibgav1TransferCharacteristicsSmpte428,
  kLibgav1TransferCharacteristicsHlg,
  // 19-254 are reserved.
  kLibgav1MaxTransferCharacteristics = 255
} Libgav1TransferCharacteristics;

typedef enum Libgav1MatrixCoefficients {
  kLibgav1MatrixCoefficientsIdentity,
  kLibgav1MatrixCoefficientsBt709,
  kLibgav1MatrixCoefficientsUnspecified,
  // 3 is reserved.
  kLibgav1MatrixCoefficientsFcc = 4,
  kLibgav1MatrixCoefficientsBt470BG,
  kLibgav1MatrixCoefficientsBt601,
  kLibgav1MatrixCoefficientsSmpte240,
  kLibgav1MatrixCoefficientsSmpteYcgco,
  kLibgav1MatrixCoefficientsBt2020Ncl,
  kLibgav1MatrixCoefficientsBt2020Cl,
  kLibgav1MatrixCoefficientsSmpte2085,
  kLibgav1MatrixCoefficientsChromatNcl,
  kLibgav1MatrixCoefficientsChromatCl,
  kLibgav1MatrixCoefficientsIctcp,
  // 15-254 are reserved.
  kLibgav1MaxMatrixCoefficients = 255
} Libgav1MatrixCoefficients;

typedef enum Libgav1ColorRange {
  // The color ranges are scaled by value << (bitdepth - 8) for 10 and 12bit
  // streams.
  kLibgav1ColorRangeStudio,  // Y [16..235], UV [16..240]
  kLibgav1ColorRangeFull     // YUV/RGB [0..255]
} Libgav1ColorRange;

// Section 6.7.3.
typedef struct Libgav1ObuMetadataHdrCll {  // NOLINT
  uint16_t max_cll;                        // Maximum content light level.
  uint16_t max_fall;                       // Maximum frame-average light level.
} Libgav1ObuMetadataHdrCll;

// Section 6.7.4.
typedef struct Libgav1ObuMetadataHdrMdcv {  // NOLINT
  // 0.16 fixed-point X/Y chromaticity coordinate as defined by CIE 1931 in
  // R/G/B order.
  uint16_t primary_chromaticity_x[3];
  uint16_t primary_chromaticity_y[3];
  // 0.16 fixed-point X/Y chromaticity coordinate as defined by CIE 1931.
  uint16_t white_point_chromaticity_x;
  uint16_t white_point_chromaticity_y;
  // 24.8 fixed-point maximum luminance, represented in candelas per square
  // meter.
  uint32_t luminance_max;
  // 18.14 fixed-point minimum luminance, represented in candelas per square
  // meter.
  uint32_t luminance_min;
} Libgav1ObuMetadataHdrMdcv;

// Section 6.7.2.
typedef struct Libgav1ObuMetadataItutT35 {  // NOLINT
  uint8_t country_code;
  uint8_t country_code_extension_byte;  // Valid if country_code is 0xFF.
  uint8_t* payload_bytes;
  int payload_size;
} Libgav1ObuMetadataItutT35;

typedef struct Libgav1DecoderBuffer {
#if defined(__cplusplus)
  LIBGAV1_PUBLIC int NumPlanes() const {
    return (image_format == kLibgav1ImageFormatMonochrome400) ? 1 : 3;
  }
#endif  // defined(__cplusplus)

  Libgav1ChromaSamplePosition chroma_sample_position;
  Libgav1ImageFormat image_format;
  Libgav1ColorRange color_range;
  Libgav1ColorPrimary color_primary;
  Libgav1TransferCharacteristics transfer_characteristics;
  Libgav1MatrixCoefficients matrix_coefficients;

  int bitdepth;  // Stored image bitdepth.

  // Image display dimensions in Y/U/V order.
  int displayed_width[3];   // Displayed image width.
  int displayed_height[3];  // Displayed image height.

  // Values are given in Y/U/V order.
  int stride[3];      // The width in bytes of one row of the |plane| buffer.
                      // This may include padding bytes for alignment or
                      // internal use by the decoder.
  uint8_t* plane[3];  // The reconstructed image plane(s).

  // Spatial id of this frame.
  int spatial_id;
  // Temporal id of this frame.
  int temporal_id;

  Libgav1ObuMetadataHdrCll hdr_cll;
  int has_hdr_cll;  // 1 if the values in hdr_cll are valid for this frame. 0
                    // otherwise.

  Libgav1ObuMetadataHdrMdcv hdr_mdcv;
  int has_hdr_mdcv;  // 1 if the values in hdr_mdcv are valid for this frame. 0
                     // otherwise.

  Libgav1ObuMetadataItutT35 itut_t35[8];
  int itut_t35_count;  // Number of entries in the itut_t35 that are valid for
                       // this frame.

  // The |user_private_data| argument passed to Decoder::EnqueueFrame().
  int64_t user_private_data;
  // The |private_data| field of FrameBuffer. Set by the get frame buffer
  // callback when it allocates a frame buffer.
  void* buffer_private_data;
} Libgav1DecoderBuffer;

#if defined(__cplusplus)
namespace libgav1 {

using ChromaSamplePosition = Libgav1ChromaSamplePosition;
constexpr ChromaSamplePosition kChromaSamplePositionUnknown =
    kLibgav1ChromaSamplePositionUnknown;
constexpr ChromaSamplePosition kChromaSamplePositionVertical =
    kLibgav1ChromaSamplePositionVertical;
constexpr ChromaSamplePosition kChromaSamplePositionColocated =
    kLibgav1ChromaSamplePositionColocated;
constexpr ChromaSamplePosition kChromaSamplePositionReserved =
    kLibgav1ChromaSamplePositionReserved;

using ImageFormat = Libgav1ImageFormat;
constexpr ImageFormat kImageFormatYuv420 = kLibgav1ImageFormatYuv420;
constexpr ImageFormat kImageFormatYuv422 = kLibgav1ImageFormatYuv422;
constexpr ImageFormat kImageFormatYuv444 = kLibgav1ImageFormatYuv444;
constexpr ImageFormat kImageFormatMonochrome400 =
    kLibgav1ImageFormatMonochrome400;

using ColorPrimary = Libgav1ColorPrimary;
constexpr ColorPrimary kColorPrimaryBt709 = kLibgav1ColorPrimaryBt709;
constexpr ColorPrimary kColorPrimaryUnspecified =
    kLibgav1ColorPrimaryUnspecified;
constexpr ColorPrimary kColorPrimaryBt470M = kLibgav1ColorPrimaryBt470M;
constexpr ColorPrimary kColorPrimaryBt470Bg = kLibgav1ColorPrimaryBt470Bg;
constexpr ColorPrimary kColorPrimaryBt601 = kLibgav1ColorPrimaryBt601;
constexpr ColorPrimary kColorPrimarySmpte240 = kLibgav1ColorPrimarySmpte240;
constexpr ColorPrimary kColorPrimaryGenericFilm =
    kLibgav1ColorPrimaryGenericFilm;
constexpr ColorPrimary kColorPrimaryBt2020 = kLibgav1ColorPrimaryBt2020;
constexpr ColorPrimary kColorPrimaryXyz = kLibgav1ColorPrimaryXyz;
constexpr ColorPrimary kColorPrimarySmpte431 = kLibgav1ColorPrimarySmpte431;
constexpr ColorPrimary kColorPrimarySmpte432 = kLibgav1ColorPrimarySmpte432;
constexpr ColorPrimary kColorPrimaryEbu3213 = kLibgav1ColorPrimaryEbu3213;
constexpr ColorPrimary kMaxColorPrimaries = kLibgav1MaxColorPrimaries;

using TransferCharacteristics = Libgav1TransferCharacteristics;
constexpr TransferCharacteristics kTransferCharacteristicsBt709 =
    kLibgav1TransferCharacteristicsBt709;
constexpr TransferCharacteristics kTransferCharacteristicsUnspecified =
    kLibgav1TransferCharacteristicsUnspecified;
constexpr TransferCharacteristics kTransferCharacteristicsBt470M =
    kLibgav1TransferCharacteristicsBt470M;
constexpr TransferCharacteristics kTransferCharacteristicsBt470Bg =
    kLibgav1TransferCharacteristicsBt470Bg;
constexpr TransferCharacteristics kTransferCharacteristicsBt601 =
    kLibgav1TransferCharacteristicsBt601;
constexpr TransferCharacteristics kTransferCharacteristicsSmpte240 =
    kLibgav1TransferCharacteristicsSmpte240;
constexpr TransferCharacteristics kTransferCharacteristicsLinear =
    kLibgav1TransferCharacteristicsLinear;
constexpr TransferCharacteristics kTransferCharacteristicsLog100 =
    kLibgav1TransferCharacteristicsLog100;
constexpr TransferCharacteristics kTransferCharacteristicsLog100Sqrt10 =
    kLibgav1TransferCharacteristicsLog100Sqrt10;
constexpr TransferCharacteristics kTransferCharacteristicsIec61966 =
    kLibgav1TransferCharacteristicsIec61966;
constexpr TransferCharacteristics kTransferCharacteristicsBt1361 =
    kLibgav1TransferCharacteristicsBt1361;
constexpr TransferCharacteristics kTransferCharacteristicsSrgb =
    kLibgav1TransferCharacteristicsSrgb;
constexpr TransferCharacteristics kTransferCharacteristicsBt2020TenBit =
    kLibgav1TransferCharacteristicsBt2020TenBit;
constexpr TransferCharacteristics kTransferCharacteristicsBt2020TwelveBit =
    kLibgav1TransferCharacteristicsBt2020TwelveBit;
constexpr TransferCharacteristics kTransferCharacteristicsSmpte2084 =
    kLibgav1TransferCharacteristicsSmpte2084;
constexpr TransferCharacteristics kTransferCharacteristicsSmpte428 =
    kLibgav1TransferCharacteristicsSmpte428;
constexpr TransferCharacteristics kTransferCharacteristicsHlg =
    kLibgav1TransferCharacteristicsHlg;
constexpr TransferCharacteristics kMaxTransferCharacteristics =
    kLibgav1MaxTransferCharacteristics;

using MatrixCoefficients = Libgav1MatrixCoefficients;
constexpr MatrixCoefficients kMatrixCoefficientsIdentity =
    kLibgav1MatrixCoefficientsIdentity;
constexpr MatrixCoefficients kMatrixCoefficientsBt709 =
    kLibgav1MatrixCoefficientsBt709;
constexpr MatrixCoefficients kMatrixCoefficientsUnspecified =
    kLibgav1MatrixCoefficientsUnspecified;
constexpr MatrixCoefficients kMatrixCoefficientsFcc =
    kLibgav1MatrixCoefficientsFcc;
constexpr MatrixCoefficients kMatrixCoefficientsBt470BG =
    kLibgav1MatrixCoefficientsBt470BG;
constexpr MatrixCoefficients kMatrixCoefficientsBt601 =
    kLibgav1MatrixCoefficientsBt601;
constexpr MatrixCoefficients kMatrixCoefficientsSmpte240 =
    kLibgav1MatrixCoefficientsSmpte240;
constexpr MatrixCoefficients kMatrixCoefficientsSmpteYcgco =
    kLibgav1MatrixCoefficientsSmpteYcgco;
constexpr MatrixCoefficients kMatrixCoefficientsBt2020Ncl =
    kLibgav1MatrixCoefficientsBt2020Ncl;
constexpr MatrixCoefficients kMatrixCoefficientsBt2020Cl =
    kLibgav1MatrixCoefficientsBt2020Cl;
constexpr MatrixCoefficients kMatrixCoefficientsSmpte2085 =
    kLibgav1MatrixCoefficientsSmpte2085;
constexpr MatrixCoefficients kMatrixCoefficientsChromatNcl =
    kLibgav1MatrixCoefficientsChromatNcl;
constexpr MatrixCoefficients kMatrixCoefficientsChromatCl =
    kLibgav1MatrixCoefficientsChromatCl;
constexpr MatrixCoefficients kMatrixCoefficientsIctcp =
    kLibgav1MatrixCoefficientsIctcp;
constexpr MatrixCoefficients kMaxMatrixCoefficients =
    kLibgav1MaxMatrixCoefficients;

using ColorRange = Libgav1ColorRange;
constexpr ColorRange kColorRangeStudio = kLibgav1ColorRangeStudio;
constexpr ColorRange kColorRangeFull = kLibgav1ColorRangeFull;

using ObuMetadataHdrCll = Libgav1ObuMetadataHdrCll;
using ObuMetadataHdrMdcv = Libgav1ObuMetadataHdrMdcv;
using ObuMetadataItutT35 = Libgav1ObuMetadataItutT35;

using DecoderBuffer = Libgav1DecoderBuffer;

}  // namespace libgav1
#endif  // defined(__cplusplus)

#endif  // LIBGAV1_SRC_GAV1_DECODER_BUFFER_H_
