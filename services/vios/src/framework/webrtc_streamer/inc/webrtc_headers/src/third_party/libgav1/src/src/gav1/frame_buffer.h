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

#ifndef LIBGAV1_SRC_GAV1_FRAME_BUFFER_H_
#define LIBGAV1_SRC_GAV1_FRAME_BUFFER_H_

// All the declarations in this file are part of the public ABI. This file may
// be included by both C and C++ files.

#if defined(__cplusplus)
#include <cstddef>
#include <cstdint>
#else
#include <stddef.h>
#include <stdint.h>
#endif  // defined(__cplusplus)

#include "gav1/decoder_buffer.h"
#include "gav1/status_code.h"
#include "gav1/symbol_visibility.h"

// The callback functions use the C linkage conventions.
#if defined(__cplusplus)
extern "C" {
#endif

// This structure represents an allocated frame buffer.
typedef struct Libgav1FrameBuffer {
  // In the |plane| and |stride| arrays, the elements at indexes 0, 1, and 2
  // are for the Y, U, and V planes, respectively.
  uint8_t* plane[3];   // Pointers to the frame (excluding the borders) in the
                       // data buffers.
  int stride[3];       // Row strides in bytes.
  void* private_data;  // Frame buffer's private data. Available for use by the
                       // release frame buffer callback. Also copied to the
                       // |buffer_private_data| field of DecoderBuffer for use
                       // by the consumer of a DecoderBuffer.
} Libgav1FrameBuffer;

// This callback is invoked by the decoder to provide information on the
// subsequent frames in the video, until the next invocation of this callback
// or the end of the video.
//
// |width| and |height| are the maximum frame width and height in pixels.
// |left_border|, |right_border|, |top_border|, and |bottom_border| are the
// maximum left, right, top, and bottom border sizes in pixels.
// |stride_alignment| specifies the alignment of the row stride in bytes.
//
// Returns kLibgav1StatusOk on success, an error status on failure.
//
// NOTE: This callback may be omitted if the information is not useful to the
// application.
typedef Libgav1StatusCode (*Libgav1FrameBufferSizeChangedCallback)(
    void* callback_private_data, int bitdepth, Libgav1ImageFormat image_format,
    int width, int height, int left_border, int right_border, int top_border,
    int bottom_border, int stride_alignment);

// This callback is invoked by the decoder to allocate a frame buffer, which
// consists of three data buffers, for the Y, U, and V planes, respectively.
//
// The callback must set |frame_buffer->plane[i]| to point to the data buffers
// of the planes, and set |frame_buffer->stride[i]| to the row strides of the
// planes. If |image_format| is kLibgav1ImageFormatMonochrome400, the callback
// should set |frame_buffer->plane[1]| and |frame_buffer->plane[2]| to a null
// pointer and set |frame_buffer->stride[1]| and |frame_buffer->stride[2]| to
// 0. The callback may set |frame_buffer->private_data| to a value that will
// be useful to the release frame buffer callback and the consumer of a
// DecoderBuffer.
//
// Returns kLibgav1StatusOk on success, an error status on failure.

// |width| and |height| are the frame width and height in pixels.
// |left_border|, |right_border|, |top_border|, and |bottom_border| are the
// left, right, top, and bottom border sizes in pixels. |stride_alignment|
// specifies the alignment of the row stride in bytes.
typedef Libgav1StatusCode (*Libgav1GetFrameBufferCallback)(
    void* callback_private_data, int bitdepth, Libgav1ImageFormat image_format,
    int width, int height, int left_border, int right_border, int top_border,
    int bottom_border, int stride_alignment, Libgav1FrameBuffer* frame_buffer);

// After a frame buffer is allocated, the decoder starts to write decoded video
// to the frame buffer. When the frame buffer is ready for consumption, it is
// made available to the application in a Decoder::DequeueFrame() call.
// Afterwards, the decoder may continue to use the frame buffer in read-only
// mode. When the decoder is finished using the frame buffer, it notifies the
// application by calling the Libgav1ReleaseFrameBufferCallback.

// This callback is invoked by the decoder to release a frame buffer.
typedef void (*Libgav1ReleaseFrameBufferCallback)(void* callback_private_data,
                                                  void* buffer_private_data);

// Libgav1ComputeFrameBufferInfo() and Libgav1SetFrameBuffer() are intended to
// help clients implement frame buffer callbacks using memory buffers. First,
// call Libgav1ComputeFrameBufferInfo(). If it succeeds, allocate y_buffer of
// size info.y_buffer_size and allocate u_buffer and v_buffer, both of size
// info.uv_buffer_size. Finally, pass y_buffer, u_buffer, v_buffer, and
// buffer_private_data to Libgav1SetFrameBuffer().

// This structure contains information useful for allocating memory for a frame
// buffer.
typedef struct Libgav1FrameBufferInfo {
  size_t y_buffer_size;   // Size in bytes of the Y buffer.
  size_t uv_buffer_size;  // Size in bytes of the U or V buffer.

  // The following fields are consumed by Libgav1SetFrameBuffer(). Do not use
  // them directly.
  int y_stride;            // Row stride in bytes of the Y buffer.
  int uv_stride;           // Row stride in bytes of the U or V buffer.
  size_t y_plane_offset;   // Offset in bytes of the frame (excluding the
                           // borders) in the Y buffer.
  size_t uv_plane_offset;  // Offset in bytes of the frame (excluding the
                           // borders) in the U or V buffer.
  int stride_alignment;    // The stride_alignment argument passed to
                           // Libgav1ComputeFrameBufferInfo().
} Libgav1FrameBufferInfo;

// Computes the information useful for allocating memory for a frame buffer.
// On success, stores the output in |info|.
LIBGAV1_PUBLIC Libgav1StatusCode Libgav1ComputeFrameBufferInfo(
    int bitdepth, Libgav1ImageFormat image_format, int width, int height,
    int left_border, int right_border, int top_border, int bottom_border,
    int stride_alignment, Libgav1FrameBufferInfo* info);

// Sets the |frame_buffer| struct.
LIBGAV1_PUBLIC Libgav1StatusCode Libgav1SetFrameBuffer(
    const Libgav1FrameBufferInfo* info, uint8_t* y_buffer, uint8_t* u_buffer,
    uint8_t* v_buffer, void* buffer_private_data,
    Libgav1FrameBuffer* frame_buffer);

#if defined(__cplusplus)
}  // extern "C"

// Declare type aliases for C++.
namespace libgav1 {

using FrameBuffer = Libgav1FrameBuffer;
using FrameBufferSizeChangedCallback = Libgav1FrameBufferSizeChangedCallback;
using GetFrameBufferCallback = Libgav1GetFrameBufferCallback;
using ReleaseFrameBufferCallback = Libgav1ReleaseFrameBufferCallback;
using FrameBufferInfo = Libgav1FrameBufferInfo;

inline StatusCode ComputeFrameBufferInfo(int bitdepth, ImageFormat image_format,
                                         int width, int height, int left_border,
                                         int right_border, int top_border,
                                         int bottom_border,
                                         int stride_alignment,
                                         FrameBufferInfo* info) {
  return Libgav1ComputeFrameBufferInfo(bitdepth, image_format, width, height,
                                       left_border, right_border, top_border,
                                       bottom_border, stride_alignment, info);
}

inline StatusCode SetFrameBuffer(const FrameBufferInfo* info, uint8_t* y_buffer,
                                 uint8_t* u_buffer, uint8_t* v_buffer,
                                 void* buffer_private_data,
                                 FrameBuffer* frame_buffer) {
  return Libgav1SetFrameBuffer(info, y_buffer, u_buffer, v_buffer,
                               buffer_private_data, frame_buffer);
}

}  // namespace libgav1
#endif  // defined(__cplusplus)

#endif  // LIBGAV1_SRC_GAV1_FRAME_BUFFER_H_
