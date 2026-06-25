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

#ifndef LIBGAV1_SRC_UTILS_CONSTANTS_H_
#define LIBGAV1_SRC_UTILS_CONSTANTS_H_

#include <cstdint>
#include <cstdlib>

#include "src/utils/bit_mask_set.h"

namespace libgav1 {

// Returns the number of elements between begin (inclusive) and end (inclusive).
constexpr int EnumRangeLength(int begin, int end) { return end - begin + 1; }

enum {
// Maximum number of threads that the library will ever create.
#if defined(LIBGAV1_MAX_THREADS) && LIBGAV1_MAX_THREADS > 0
  kMaxThreads = LIBGAV1_MAX_THREADS
#else
  kMaxThreads = 128
#endif
};  // anonymous enum

enum {
  // Documentation variables.
  kBitdepth8 = 8,
  kBitdepth10 = 10,
  kBitdepth12 = 12,
  kInvalidMvValue = -32768,
  kCdfMaxProbability = 32768,
  kBlockWidthCount = 5,
  kMaxSegments = 8,
  kMinQuantizer = 0,
  kMinLossyQuantizer = 1,
  kMaxQuantizer = 255,
  // Quantizer matrix is used only when level < 15.
  kNumQuantizerLevelsForQuantizerMatrix = 15,
  kFrameLfCount = 4,
  kMaxLoopFilterValue = 63,
  kNum4x4In64x64 = 256,
  kMaxAngleDelta = 3,
  kDirectionalIntraModes = 8,
  kMaxSuperBlockSizeLog2 = 7,
  kMinSuperBlockSizeLog2 = 6,
  kGlobalMotionReadControl = 3,
  kSuperResScaleNumerator = 8,
  kBooleanSymbolCount = 2,
  kRestorationTypeSymbolCount = 3,
  kSgrProjParamsBits = 4,
  kSgrProjPrecisionBits = 7,
  // Precision of a division table (mtable)
  kSgrProjScaleBits = 20,
  kSgrProjReciprocalBits = 12,
  // Core self-guided restoration precision bits.
  kSgrProjSgrBits = 8,
  // Precision bits of generated values higher than source before projection.
  kSgrProjRestoreBits = 4,
  // Padding on left and right side of a restoration block.
  // 3 is enough, but padding to 4 is more efficient, and makes the temporary
  // source buffer 8-pixel aligned.
  kRestorationHorizontalBorder = 4,
  // Padding on top and bottom side of a restoration block.
  kRestorationVerticalBorder = 2,
  kCdefBorder = 2,             // Padding on each side of a cdef block.
  kConvolveBorderLeftTop = 3,  // Left/top padding of a convolve block.
  // Right/bottom padding of a convolve block. This needs to be 4 at minimum,
  // but was increased to simplify the SIMD loads in
  // ConvolveCompoundScale2D_NEON() and ConvolveScale2D_NEON().
  kConvolveBorderRight = 8,
  kConvolveScaleBorderRight = 15,
  kConvolveBorderBottom = 4,
  kSubPixelTaps = 8,
  kWienerFilterBits = 7,
  kWienerFilterTaps = 7,
  kMaxPaletteSize = 8,
  kMinPaletteSize = 2,
  kMaxPaletteSquare = 64,
  kBorderPixels = 64,
  // The final blending process for film grain needs room to overwrite and read
  // with SIMD instructions. The maximum overwrite is 7 pixels, but the border
  // is required to be a multiple of 32 by YuvBuffer::Realloc, so that
  // subsampled chroma borders are 16-aligned.
  kBorderPixelsFilmGrain = 32,
  // These constants are the minimum left, right, top, and bottom border sizes
  // in pixels as an extension of the frame boundary. The minimum border sizes
  // are derived from the following requirements:
  // - Warp_C() may read up to 13 pixels before or after a row.
  // - Warp_NEON() may read up to 13 pixels before a row. It may read up to 14
  //   pixels after a row, but the value of the last read pixel is not used.
  // - Warp_C() and Warp_NEON() may read up to 13 pixels above the top row and
  //   13 pixels below the bottom row.
  kMinLeftBorderPixels = 13,
  kMinRightBorderPixels = 13,
  kMinTopBorderPixels = 13,
  kMinBottomBorderPixels = 13,
  kWarpedModelPrecisionBits = 16,
  kMaxRefMvStackSize = 8,
  kMaxLeastSquaresSamples = 8,
  kMaxTemporalMvCandidates = 19,
  // The SIMD implementations of motion vection projection functions always
  // process 2 or 4 elements together, so we pad the corresponding buffers to
  // size 20.
  kMaxTemporalMvCandidatesWithPadding = 20,
  kMaxSuperBlockSizeInPixels = 128,
  kMaxScaledSuperBlockSizeInPixels = 128 * 2,
  kMaxSuperBlockSizeSquareInPixels = 128 * 128,
  kNum4x4InLoopFilterUnit = 16,
  kNum4x4InLoopRestorationUnit = 16,
  kProjectionMvClamp = (1 << 14) - 1,  // == 16383
  kProjectionMvMaxHorizontalOffset = 8,
  kCdefUnitSize = 64,
  kCdefUnitSizeWithBorders = kCdefUnitSize + 2 * kCdefBorder,
  kRestorationUnitOffset = 8,
  // Loop restoration's processing unit size is fixed as 64x64.
  kRestorationUnitHeight = 64,
  kRestorationUnitWidth = 256,
  kRestorationUnitHeightWithBorders =
      kRestorationUnitHeight + 2 * kRestorationVerticalBorder,
  kRestorationUnitWidthWithBorders =
      kRestorationUnitWidth + 2 * kRestorationHorizontalBorder,
  kSuperResFilterBits = 6,
  kSuperResFilterShifts = 1 << kSuperResFilterBits,
  kSuperResFilterTaps = 8,
  kSuperResScaleBits = 14,
  kSuperResExtraBits = kSuperResScaleBits - kSuperResFilterBits,
  kSuperResScaleMask = (1 << 14) - 1,
  kSuperResHorizontalBorder = 4,
  kSuperResVerticalBorder = 1,
  // The SIMD implementations of superres calculate up to 15 extra upscaled
  // pixels which will over-read up to 15 downscaled pixels in the end of each
  // row. Set the padding to 16 for alignment purposes.
  kSuperResHorizontalPadding = 16,
  // TODO(chengchen): consider merging these constants:
  // kFilterBits, kWienerFilterBits, and kSgrProjPrecisionBits, which are all 7,
  // They are designed to match AV1 convolution, which increases coeff
  // values up to 7 bits. We could consider to combine them and use kFilterBits
  // only.
  kFilterBits = 7,
  // Sub pixel is used in AV1 to represent a pixel location that is not at
  // integer position. Sub pixel is in 1/16 (1 << kSubPixelBits) unit of
  // integer pixel. Sub pixel values are interpolated using adjacent integer
  // pixel values. The interpolation is a filtering process.
  kSubPixelBits = 4,
  kSubPixelMask = (1 << kSubPixelBits) - 1,
  // Precision bits when computing inter prediction locations.
  kScaleSubPixelBits = 10,
  kWarpParamRoundingBits = 6,
  // Number of fractional bits of lookup in divisor lookup table.
  kDivisorLookupBits = 8,
  // Number of fractional bits of entries in divisor lookup table.
  kDivisorLookupPrecisionBits = 14,
  // Number of phases used in warped filtering.
  kWarpedPixelPrecisionShifts = 1 << 6,
  kResidualPaddingVertical = 4,
  kWedgeMaskMasterSize = 64,
  kMaxFrameDistance = 31,
  kReferenceFrameScalePrecision = 14,
  kNumWienerCoefficients = 3,
  kLoopFilterMaxModeDeltas = 2,
  kMaxCdefStrengths = 8,
  kCdefLargeValue = 0x4000,  // Used to indicate where CDEF is not available.
  kMaxTileColumns = 64,
  kMaxTileRows = 64,
  kMaxOperatingPoints = 32,
  // There can be a maximum of 4 spatial layers and 8 temporal layers.
  kMaxLayers = 32,
  // The cache line size should ideally be queried at run time. 64 is a common
  // cache line size of x86 CPUs. Web searches showed the cache line size of ARM
  // CPUs is 32 or 64 bytes. So aligning to 64-byte boundary will work for all
  // CPUs that we care about, even though it is excessive for some ARM
  // CPUs.
  //
  // On Linux, the cache line size can be looked up with the command:
  //   getconf LEVEL1_DCACHE_LINESIZE
  kCacheLineSize = 64,
  // InterRound0, Section 7.11.3.2.
  kInterRoundBitsHorizontal = 3,  // 8 & 10-bit.
  kInterRoundBitsHorizontal12bpp = 5,
  kInterRoundBitsCompoundVertical = 7,  // 8, 10 & 12-bit compound prediction.
  kInterRoundBitsVertical = 11,         // 8 & 10-bit, single prediction.
  kInterRoundBitsVertical12bpp = 9,
  // Offset applied to 10bpp and 12bpp predictors to allow storing them in
  // uint16_t. Removed before blending.
  kCompoundOffset = (1 << 14) + (1 << 13),
};  // anonymous enum

enum FrameType : uint8_t {
  kFrameKey,
  kFrameInter,
  kFrameIntraOnly,
  kFrameSwitch
};

enum Plane : uint8_t { kPlaneY, kPlaneU, kPlaneV };
enum : uint8_t { kMaxPlanesMonochrome = kPlaneY + 1, kMaxPlanes = kPlaneV + 1 };

// The plane types, called luma and chroma in the spec.
enum PlaneType : uint8_t { kPlaneTypeY, kPlaneTypeUV, kNumPlaneTypes };

enum ReferenceFrameType : int8_t {
  kReferenceFrameNone = -1,
  kReferenceFrameIntra,
  kReferenceFrameLast,
  kReferenceFrameLast2,
  kReferenceFrameLast3,
  kReferenceFrameGolden,
  kReferenceFrameBackward,
  kReferenceFrameAlternate2,
  kReferenceFrameAlternate,
  kNumReferenceFrameTypes,
  kNumInterReferenceFrameTypes =
      EnumRangeLength(kReferenceFrameLast, kReferenceFrameAlternate),
  kNumForwardReferenceTypes =
      EnumRangeLength(kReferenceFrameLast, kReferenceFrameGolden),
  kNumBackwardReferenceTypes =
      EnumRangeLength(kReferenceFrameBackward, kReferenceFrameAlternate)
};

enum {
  // Unidirectional compound reference pairs that are signaled explicitly:
  // {kReferenceFrameLast, kReferenceFrameLast2},
  // {kReferenceFrameLast, kReferenceFrameLast3},
  // {kReferenceFrameLast, kReferenceFrameGolden},
  // {kReferenceFrameBackward, kReferenceFrameAlternate}
  kExplicitUnidirectionalCompoundReferences = 4,
  // Other unidirectional compound reference pairs:
  // {kReferenceFrameLast2, kReferenceFrameLast3},
  // {kReferenceFrameLast2, kReferenceFrameGolden},
  // {kReferenceFrameLast3, kReferenceFrameGolden},
  // {kReferenceFrameBackward, kReferenceFrameAlternate2},
  // {kReferenceFrameAlternate2, kReferenceFrameAlternate}
  kUnidirectionalCompoundReferences =
      kExplicitUnidirectionalCompoundReferences + 5,
};  // anonymous enum

enum BlockSize : uint8_t {
  kBlock4x4,
  kBlock4x8,
  kBlock4x16,
  kBlock8x4,
  kBlock8x8,
  kBlock8x16,
  kBlock8x32,
  kBlock16x4,
  kBlock16x8,
  kBlock16x16,
  kBlock16x32,
  kBlock16x64,
  kBlock32x8,
  kBlock32x16,
  kBlock32x32,
  kBlock32x64,
  kBlock64x16,
  kBlock64x32,
  kBlock64x64,
  kBlock64x128,
  kBlock128x64,
  kBlock128x128,
  kMaxBlockSizes,
  kBlockInvalid
};

//  Partition types.  R: Recursive
//
//  None          Horizontal    Vertical      Split
//  +-------+     +-------+     +---+---+     +---+---+
//  |       |     |       |     |   |   |     | R | R |
//  |       |     +-------+     |   |   |     +---+---+
//  |       |     |       |     |   |   |     | R | R |
//  +-------+     +-------+     +---+---+     +---+---+
//
//  Horizontal    Horizontal    Vertical      Vertical
//  with top      with bottom   with left     with right
//  split         split         split         split
//  +---+---+     +-------+     +---+---+     +---+---+
//  |   |   |     |       |     |   |   |     |   |   |
//  +---+---+     +---+---+     +---+   |     |   +---+
//  |       |     |   |   |     |   |   |     |   |   |
//  +-------+     +---+---+     +---+---+     +---+---+
//
//  Horizontal4   Vertical4
//  +-----+       +-+-+-+
//  +-----+       | | | |
//  +-----+       | | | |
//  +-----+       +-+-+-+
enum Partition : uint8_t {
  kPartitionNone,
  kPartitionHorizontal,
  kPartitionVertical,
  kPartitionSplit,
  kPartitionHorizontalWithTopSplit,
  kPartitionHorizontalWithBottomSplit,
  kPartitionVerticalWithLeftSplit,
  kPartitionVerticalWithRightSplit,
  kPartitionHorizontal4,
  kPartitionVertical4
};
enum : uint8_t { kMaxPartitionTypes = kPartitionVertical4 + 1 };

enum PredictionMode : uint8_t {
  // Intra prediction modes.
  kPredictionModeDc,
  kPredictionModeVertical,
  kPredictionModeHorizontal,
  kPredictionModeD45,
  kPredictionModeD135,
  kPredictionModeD113,
  kPredictionModeD157,
  kPredictionModeD203,
  kPredictionModeD67,
  kPredictionModeSmooth,
  kPredictionModeSmoothVertical,
  kPredictionModeSmoothHorizontal,
  kPredictionModePaeth,
  kPredictionModeChromaFromLuma,
  // Single inter prediction modes.
  kPredictionModeNearestMv,
  kPredictionModeNearMv,
  kPredictionModeGlobalMv,
  kPredictionModeNewMv,
  // Compound inter prediction modes.
  kPredictionModeNearestNearestMv,
  kPredictionModeNearNearMv,
  kPredictionModeNearestNewMv,
  kPredictionModeNewNearestMv,
  kPredictionModeNearNewMv,
  kPredictionModeNewNearMv,
  kPredictionModeGlobalGlobalMv,
  kPredictionModeNewNewMv,
  kNumPredictionModes,
  kNumCompoundInterPredictionModes =
      EnumRangeLength(kPredictionModeNearestNearestMv, kPredictionModeNewNewMv),
  kIntraPredictionModesY =
      EnumRangeLength(kPredictionModeDc, kPredictionModePaeth),
  kIntraPredictionModesUV =
      EnumRangeLength(kPredictionModeDc, kPredictionModeChromaFromLuma),
  kPredictionModeInvalid = 255
};

enum InterIntraMode : uint8_t {
  kInterIntraModeDc,
  kInterIntraModeVertical,
  kInterIntraModeHorizontal,
  kInterIntraModeSmooth,
  kNumInterIntraModes
};

enum MotionMode : uint8_t {
  kMotionModeSimple,
  kMotionModeObmc,  // Overlapped block motion compensation.
  kMotionModeLocalWarp,
  kNumMotionModes
};

enum TxMode : uint8_t {
  kTxModeOnly4x4,
  kTxModeLargest,
  kTxModeSelect,
  kNumTxModes
};

// These enums are named as kType1Type2 where Type1 is the transform type for
// the rows and Type2 is the transform type for the columns.
enum TransformType : uint8_t {
  kTransformTypeDctDct,
  kTransformTypeAdstDct,
  kTransformTypeDctAdst,
  kTransformTypeAdstAdst,
  kTransformTypeFlipadstDct,
  kTransformTypeDctFlipadst,
  kTransformTypeFlipadstFlipadst,
  kTransformTypeAdstFlipadst,
  kTransformTypeFlipadstAdst,
  kTransformTypeIdentityIdentity,
  kTransformTypeIdentityDct,
  kTransformTypeDctIdentity,
  kTransformTypeIdentityAdst,
  kTransformTypeAdstIdentity,
  kTransformTypeIdentityFlipadst,
  kTransformTypeFlipadstIdentity,
  kNumTransformTypes
};

constexpr BitMaskSet kTransformFlipColumnsMask(kTransformTypeFlipadstDct,
                                               kTransformTypeFlipadstAdst,
                                               kTransformTypeFlipadstIdentity,
                                               kTransformTypeFlipadstFlipadst);
constexpr BitMaskSet kTransformFlipRowsMask(kTransformTypeDctFlipadst,
                                            kTransformTypeAdstFlipadst,
                                            kTransformTypeIdentityFlipadst,
                                            kTransformTypeFlipadstFlipadst);

enum TransformSize : uint8_t {
  kTransformSize4x4,
  kTransformSize4x8,
  kTransformSize4x16,
  kTransformSize8x4,
  kTransformSize8x8,
  kTransformSize8x16,
  kTransformSize8x32,
  kTransformSize16x4,
  kTransformSize16x8,
  kTransformSize16x16,
  kTransformSize16x32,
  kTransformSize16x64,
  kTransformSize32x8,
  kTransformSize32x16,
  kTransformSize32x32,
  kTransformSize32x64,
  kTransformSize64x16,
  kTransformSize64x32,
  kTransformSize64x64,
  kNumTransformSizes
};

enum TransformSet : uint8_t {
  // DCT Only (1).
  kTransformSetDctOnly,
  // 2D-DCT and 2D-ADST without flip (4) + Identity (1) + 1D Horizontal/Vertical
  // DCT (2) = Total (7).
  kTransformSetIntra1,
  // 2D-DCT and 2D-ADST without flip (4) + Identity (1) = Total (5).
  kTransformSetIntra2,
  // All transforms = Total (16).
  kTransformSetInter1,
  // 2D-DCT and 2D-ADST with flip (9) + Identity (1) + 1D Horizontal/Vertical
  // DCT (2) = Total (12).
  kTransformSetInter2,
  // DCT (1) + Identity (1) = Total (2).
  kTransformSetInter3,
  kNumTransformSets
};

enum TransformClass : uint8_t {
  kTransformClass2D,
  kTransformClassHorizontal,
  kTransformClassVertical,
  kNumTransformClasses
};

enum FilterIntraPredictor : uint8_t {
  kFilterIntraPredictorDc,
  kFilterIntraPredictorVertical,
  kFilterIntraPredictorHorizontal,
  kFilterIntraPredictorD157,
  kFilterIntraPredictorPaeth,
  kNumFilterIntraPredictors
};

enum ObmcDirection : uint8_t {
  kObmcDirectionVertical,
  kObmcDirectionHorizontal,
  kNumObmcDirections
};

// In AV1 the name of the filter refers to the direction of filter application.
// Horizontal refers to the column edge and vertical the row edge.
enum LoopFilterType : uint8_t {
  kLoopFilterTypeVertical,
  kLoopFilterTypeHorizontal,
  kNumLoopFilterTypes
};

enum LoopFilterTransformSizeId : uint8_t {
  kLoopFilterTransformSizeId4x4,
  kLoopFilterTransformSizeId8x8,
  kLoopFilterTransformSizeId16x16,
  kNumLoopFilterTransformSizeIds
};

enum LoopRestorationType : uint8_t {
  kLoopRestorationTypeNone,
  kLoopRestorationTypeSwitchable,
  kLoopRestorationTypeWiener,
  kLoopRestorationTypeSgrProj,  // self guided projection filter.
  kNumLoopRestorationTypes
};

enum CompoundReferenceType : uint8_t {
  kCompoundReferenceUnidirectional,
  kCompoundReferenceBidirectional,
  kNumCompoundReferenceTypes
};

enum CompoundPredictionType : uint8_t {
  kCompoundPredictionTypeWedge,
  kCompoundPredictionTypeDiffWeighted,
  kCompoundPredictionTypeAverage,
  kCompoundPredictionTypeIntra,
  kCompoundPredictionTypeDistance,
  kNumCompoundPredictionTypes,
  // Number of compound prediction types that are explicitly signaled in the
  // bitstream (in the compound_type syntax element).
  kNumExplicitCompoundPredictionTypes = 2
};

enum InterpolationFilter : uint8_t {
  kInterpolationFilterEightTap,
  kInterpolationFilterEightTapSmooth,
  kInterpolationFilterEightTapSharp,
  kInterpolationFilterBilinear,
  kInterpolationFilterSwitchable,
  kNumInterpolationFilters,
  // Number of interpolation filters that can be explicitly signaled in the
  // compressed headers (when the uncompressed headers allow switchable
  // interpolation filters) of the bitstream.
  kNumExplicitInterpolationFilters = EnumRangeLength(
      kInterpolationFilterEightTap, kInterpolationFilterEightTapSharp)
};

enum MvJointType : uint8_t {
  kMvJointTypeZero,
  kMvJointTypeHorizontalNonZeroVerticalZero,
  kMvJointTypeHorizontalZeroVerticalNonZero,
  kMvJointTypeNonZero,
  kNumMvJointTypes
};

enum ObuType : int8_t {
  kObuInvalid = -1,
  kObuSequenceHeader = 1,
  kObuTemporalDelimiter = 2,
  kObuFrameHeader = 3,
  kObuTileGroup = 4,
  kObuMetadata = 5,
  kObuFrame = 6,
  kObuRedundantFrameHeader = 7,
  kObuTileList = 8,
  kObuPadding = 15,
};

constexpr BitMaskSet kPredictionModeSmoothMask(kPredictionModeSmooth,
                                               kPredictionModeSmoothHorizontal,
                                               kPredictionModeSmoothVertical);

//------------------------------------------------------------------------------
// ToString()
//
// These functions are meant to be used only in debug logging and within tests.
// They are defined inline to avoid including the strings in the release
// library when logging is disabled; unreferenced functions will not be added to
// any object file in that case.

inline const char* ToString(const BlockSize size) {
  switch (size) {
    case kBlock4x4:
      return "kBlock4x4";
    case kBlock4x8:
      return "kBlock4x8";
    case kBlock4x16:
      return "kBlock4x16";
    case kBlock8x4:
      return "kBlock8x4";
    case kBlock8x8:
      return "kBlock8x8";
    case kBlock8x16:
      return "kBlock8x16";
    case kBlock8x32:
      return "kBlock8x32";
    case kBlock16x4:
      return "kBlock16x4";
    case kBlock16x8:
      return "kBlock16x8";
    case kBlock16x16:
      return "kBlock16x16";
    case kBlock16x32:
      return "kBlock16x32";
    case kBlock16x64:
      return "kBlock16x64";
    case kBlock32x8:
      return "kBlock32x8";
    case kBlock32x16:
      return "kBlock32x16";
    case kBlock32x32:
      return "kBlock32x32";
    case kBlock32x64:
      return "kBlock32x64";
    case kBlock64x16:
      return "kBlock64x16";
    case kBlock64x32:
      return "kBlock64x32";
    case kBlock64x64:
      return "kBlock64x64";
    case kBlock64x128:
      return "kBlock64x128";
    case kBlock128x64:
      return "kBlock128x64";
    case kBlock128x128:
      return "kBlock128x128";
    case kMaxBlockSizes:
      return "kMaxBlockSizes";
    case kBlockInvalid:
      return "kBlockInvalid";
  }
  abort();
}

inline const char* ToString(const InterIntraMode mode) {
  switch (mode) {
    case kInterIntraModeDc:
      return "kInterIntraModeDc";
    case kInterIntraModeVertical:
      return "kInterIntraModeVertical";
    case kInterIntraModeHorizontal:
      return "kInterIntraModeHorizontal";
    case kInterIntraModeSmooth:
      return "kInterIntraModeSmooth";
    case kNumInterIntraModes:
      return "kNumInterIntraModes";
  }
  abort();
}

inline const char* ToString(const ObmcDirection direction) {
  switch (direction) {
    case kObmcDirectionVertical:
      return "kObmcDirectionVertical";
    case kObmcDirectionHorizontal:
      return "kObmcDirectionHorizontal";
    case kNumObmcDirections:
      return "kNumObmcDirections";
  }
  abort();
}

inline const char* ToString(const LoopRestorationType type) {
  switch (type) {
    case kLoopRestorationTypeNone:
      return "kLoopRestorationTypeNone";
    case kLoopRestorationTypeSwitchable:
      return "kLoopRestorationTypeSwitchable";
    case kLoopRestorationTypeWiener:
      return "kLoopRestorationTypeWiener";
    case kLoopRestorationTypeSgrProj:
      return "kLoopRestorationTypeSgrProj";
    case kNumLoopRestorationTypes:
      return "kNumLoopRestorationTypes";
  }
  abort();
}

inline const char* ToString(const TransformSize size) {
  switch (size) {
    case kTransformSize4x4:
      return "kTransformSize4x4";
    case kTransformSize4x8:
      return "kTransformSize4x8";
    case kTransformSize4x16:
      return "kTransformSize4x16";
    case kTransformSize8x4:
      return "kTransformSize8x4";
    case kTransformSize8x8:
      return "kTransformSize8x8";
    case kTransformSize8x16:
      return "kTransformSize8x16";
    case kTransformSize8x32:
      return "kTransformSize8x32";
    case kTransformSize16x4:
      return "kTransformSize16x4";
    case kTransformSize16x8:
      return "kTransformSize16x8";
    case kTransformSize16x16:
      return "kTransformSize16x16";
    case kTransformSize16x32:
      return "kTransformSize16x32";
    case kTransformSize16x64:
      return "kTransformSize16x64";
    case kTransformSize32x8:
      return "kTransformSize32x8";
    case kTransformSize32x16:
      return "kTransformSize32x16";
    case kTransformSize32x32:
      return "kTransformSize32x32";
    case kTransformSize32x64:
      return "kTransformSize32x64";
    case kTransformSize64x16:
      return "kTransformSize64x16";
    case kTransformSize64x32:
      return "kTransformSize64x32";
    case kTransformSize64x64:
      return "kTransformSize64x64";
    case kNumTransformSizes:
      return "kNumTransformSizes";
  }
  abort();
}

inline const char* ToString(const TransformType type) {
  switch (type) {
    case kTransformTypeDctDct:
      return "kTransformTypeDctDct";
    case kTransformTypeAdstDct:
      return "kTransformTypeAdstDct";
    case kTransformTypeDctAdst:
      return "kTransformTypeDctAdst";
    case kTransformTypeAdstAdst:
      return "kTransformTypeAdstAdst";
    case kTransformTypeFlipadstDct:
      return "kTransformTypeFlipadstDct";
    case kTransformTypeDctFlipadst:
      return "kTransformTypeDctFlipadst";
    case kTransformTypeFlipadstFlipadst:
      return "kTransformTypeFlipadstFlipadst";
    case kTransformTypeAdstFlipadst:
      return "kTransformTypeAdstFlipadst";
    case kTransformTypeFlipadstAdst:
      return "kTransformTypeFlipadstAdst";
    case kTransformTypeIdentityIdentity:
      return "kTransformTypeIdentityIdentity";
    case kTransformTypeIdentityDct:
      return "kTransformTypeIdentityDct";
    case kTransformTypeDctIdentity:
      return "kTransformTypeDctIdentity";
    case kTransformTypeIdentityAdst:
      return "kTransformTypeIdentityAdst";
    case kTransformTypeAdstIdentity:
      return "kTransformTypeAdstIdentity";
    case kTransformTypeIdentityFlipadst:
      return "kTransformTypeIdentityFlipadst";
    case kTransformTypeFlipadstIdentity:
      return "kTransformTypeFlipadstIdentity";
    // case to quiet compiler
    case kNumTransformTypes:
      return "kNumTransformTypes";
  }
  abort();
}

//------------------------------------------------------------------------------

extern const uint8_t k4x4WidthLog2[kMaxBlockSizes];

extern const uint8_t k4x4HeightLog2[kMaxBlockSizes];

extern const uint8_t kNum4x4BlocksWide[kMaxBlockSizes];

extern const uint8_t kNum4x4BlocksHigh[kMaxBlockSizes];

extern const uint8_t kBlockWidthPixels[kMaxBlockSizes];

extern const uint8_t kBlockHeightPixels[kMaxBlockSizes];

extern const BlockSize kSubSize[kMaxPartitionTypes][kMaxBlockSizes];

extern const BlockSize kPlaneResidualSize[kMaxBlockSizes][2][2];

extern const int16_t kProjectionMvDivisionLookup[kMaxFrameDistance + 1];

extern const uint8_t kTransformWidth[kNumTransformSizes];

extern const uint8_t kTransformHeight[kNumTransformSizes];

extern const uint8_t kTransformWidth4x4[kNumTransformSizes];

extern const uint8_t kTransformHeight4x4[kNumTransformSizes];

extern const uint8_t kTransformWidthLog2[kNumTransformSizes];

extern const uint8_t kTransformHeightLog2[kNumTransformSizes];

extern const TransformSize kSplitTransformSize[kNumTransformSizes];

// Square transform of size min(w,h).
extern const TransformSize kTransformSizeSquareMin[kNumTransformSizes];

// Square transform of size max(w,h).
extern const TransformSize kTransformSizeSquareMax[kNumTransformSizes];

extern const uint8_t kNumTransformTypesInSet[kNumTransformSets];

extern const uint8_t kSgrProjParams[1 << kSgrProjParamsBits][4];

extern const int8_t kSgrProjMultiplierMin[2];

extern const int8_t kSgrProjMultiplierMax[2];

extern const int8_t kWienerTapsMin[3];

extern const int8_t kWienerTapsMax[3];

extern const uint8_t kUpscaleFilterUnsigned[kSuperResFilterShifts]
                                           [kSuperResFilterTaps];

// An int8_t version of the kWarpedFilters array.
// Note: The array could be removed with a performance penalty.
extern const int8_t kWarpedFilters8[3 * kWarpedPixelPrecisionShifts + 1][8];

extern const int16_t kWarpedFilters[3 * kWarpedPixelPrecisionShifts + 1][8];

extern const int8_t kHalfSubPixelFilters[6][16][8];

extern const uint8_t kAbsHalfSubPixelFilters[6][16][8];

extern const int16_t kDirectionalIntraPredictorDerivative[44];

extern const uint8_t kDeblockFilterLevelIndex[kMaxPlanes][kNumLoopFilterTypes];

extern const uint16_t kBlockWeight[kMaxBlockSizes];

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_CONSTANTS_H_
