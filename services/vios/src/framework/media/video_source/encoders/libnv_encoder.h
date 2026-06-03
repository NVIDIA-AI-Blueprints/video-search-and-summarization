/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <memory>
#include <string>
#include <vector>
#include <queue>
#include <fstream>
#include "logger.h"
#include <syscall.h>
#include <mutex>
#include <linux/videodev2.h>
#include "v4l2_nv_extensions.h"

#ifndef JETSON_PLATFORM
#include <cuda.h>
#include <cuda_runtime_api.h>
#endif
#include "nvbufwrapper.h"

#ifdef JETSON_PLATFORM
inline constexpr const char* ENCODER_DEV = "/dev/nvidia0";
inline constexpr const char* ENCODER_DEV_ALT = "/dev/v4l2-nvenc";
#else
inline constexpr const char* ENCODER_DEV = "/dev/nvidia0";
#endif

#ifndef MAX_PLANES
#define MAX_PLANES 3
#endif

typedef std::pair <int, int> FD_Index_Pair;

class NvVideoEncoder {
 public:
  NvVideoEncoder();
  ~NvVideoEncoder();
  void Init();
  void Deinit();
  bool isReleased() { return m_released; }
  int Release();
  int InitEncode(uint32_t width, uint32_t height, string codecString);
  FD_Index_Pair Encode(std::pair<int, int> fd_index_pair, unsigned char** data, ssize_t* size);
  void SetRates(unsigned int target_bitrate_bps);
  int setQpRange();
  int setIFrameInterval(uint32_t interval);
  int setIDRInterval(uint32_t interval);
  int setHWPreset();
  int forceIDR();
  int setInsertSpsPpsAtIdrEnabled(bool enabled);
  int setMaxPerfMode();
  void setGPUIndex(int index);
  int sendLastBuffer();
  int dqueueLastBuffers(unsigned char** data, ssize_t *size);
  void setTuningInfo ();
  void setCudaPresetID ();
  CodecStats  m_encStats;

 private:
  int GetEncodedPartitions(unsigned char** data, ssize_t *size, bool last_buffers = false);
  void SetVBR ();
    typedef struct
    {
        uint32_t width;             /**< Holds the width of the plane in pixels. */
        uint32_t height;            /**< Holds the height of the plane in pixels. */

        uint32_t bytesperpixel;     /**< Holds the bytes used to represent one
                                      pixel in the plane. */
        uint32_t stride;            /**< Holds the stride of the plane in bytes. */
        uint32_t sizeimage;         /**< Holds the size of the plane in bytes. */
    } NvBufferPlaneFormat;

    typedef struct
    {
        NvBufferPlaneFormat fmt;    /**< Holds the format of the plane. */

        unsigned char *data;        /**< Holds a pointer to the plane memory. */
        uint32_t bytesused;         /**< Holds the number of valid bytes in the plane. */

        int fd;                     /**< Holds the file descriptor (FD) of the plane of the
                                      exported buffer, in the case of V4L2 MMAP buffers. */
        uint32_t mem_offset;        /**< Holds the offset of the first valid byte
                                      from the data pointer. */
        uint32_t length;            /**< Holds the size of the buffer in bytes. */
    } NvBufferPlane;

    typedef struct {
        enum v4l2_buf_type buf_type;  /**< Type of the buffer. */
        enum v4l2_memory memory_type; /**< Type of memory associated
                                               with the buffer. */

        uint32_t index;               /**< Holds the buffer index. */

        uint32_t n_planes;            /**< Holds the number of planes in the buffer. */
        NvBufferPlane planes[MAX_PLANES];     /**< Holds the data pointer, plane file
                                                 descriptor (FD), plane format, etc. */
        uint32_t ref_count;             /**< Holds the reference count of the buffer. */
        pthread_mutex_t ref_lock;       /**< Mutex to synchronize increment/
                                             decrement operations of @c ref_count. */

        bool mapped;                    /**< Indicates if the buffer is mapped to
                                             memory. */
        bool allocated;                 /**< Indicates if the buffer is allocated */
    } NvBuffer;

  struct v4l2Planes_{

    const char *plane_name; /**< A pointer to the name of the plane. Could be "Output Plane" or
                                 "Capture Plane". Used only for debug logs. */
    enum v4l2_buf_type buf_type; /**< Speciifes the type of the stream. */

    bool blocking;  /**< Specifies whether the V4l2 element is opened with
                         blocking mode. */

    uint32_t num_buffers;   /**< Holds the number of buffers returned by \c VIDIOC_REQBUFS
                                 IOCTL. */
    NvBuffer **buffers;     /**< A pointer to an array of NvBuffer object pointers. This array is
                                 allocated and initialized in #reqbufs. */

    uint8_t n_planes;       /**< Specifies the number of planes in the buffers. */
    NvBufferPlaneFormat planefmts[MAX_PLANES];
                            /**< Format of the buffer planes. This must be
                                 initialized before calling #reqbufs since this
                                 is required by the \c %NvBuffer constructor. */

    enum v4l2_memory memory_type; /**< Specifies the V4l2 memory type of the buffers. */

    uint32_t pixfmt; /**< Holds the pixfmt of the plane. */

    bool streamon;  /**< Specifies whether the plane is streaming. */

    int buffer_count;
  };

  uint32_t buffer_number_ = 0;
  int flags = 0;
  int encoder_fd = -1;
  bool m_released = true;
  struct v4l2Planes_ *capturePlane = nullptr;
  struct v4l2Planes_ *outputPlane = nullptr;

#ifndef JETSON_PLATFORM
  CUdevice    cuDevice = 0; // Device on which this instance is associated.
  CUcontext   cuContext = nullptr; // Cuda context for this instance
#else
  bool gpu_enabled = false;
#endif

  //Initialize V4L2 planes
  void InitPlane(struct v4l2Planes_ *currentPlane);
    int setOutputPlaneFormat(uint32_t pixfmt, uint32_t width,
        uint32_t height);

  int setCapturePlaneFormat(uint32_t pixfmt, uint32_t width,
        uint32_t height, uint32_t sizeimage);

  int reqbufs(struct v4l2Planes_ * currentPlane, uint32_t num);

  int queryBuffer(struct v4l2Planes_ * currentPlane, uint32_t i);

  int exportBuffer(struct v4l2Planes_ * currentPlane, uint32_t i);

  int setupPlane(struct v4l2Planes_ * currentPlane, enum v4l2_memory mem_type,
        uint32_t num_buffers, bool map, bool allocate);

  int subscribeEvent(uint32_t type, uint32_t id, uint32_t flags);

  int setStreamStatus(struct v4l2Planes_ * currentPlane, bool status);

  int qBuffer(struct v4l2Planes_ * currentPlane, struct v4l2_buffer &v4l2_buf,
        NvBuffer * shared_buffer);

  int dqBuffer(struct v4l2Planes_ * currentPlane, struct v4l2_buffer &v4l2_buf,
      NvBuffer ** buffer, NvBuffer ** shared_buffer, uint32_t num_retries);

  int fill_buffer_plane_format(uint32_t *num_planes, NvBufferPlaneFormat *planefmts,
      uint32_t width, uint32_t height, uint32_t raw_pixfmt);

  int mapBuffers(NvBuffer *currentBuffer);

  void unmapBuffers(NvBuffer *currentBuffer);

  void InitNvBuffer(NvBuffer *currentBuffer, enum v4l2_buf_type buf_type,
      enum v4l2_memory memory_type, uint32_t n_planes,
      NvBufferPlaneFormat * fmt, uint32_t index);

  uint32_t m_width = 1920;
  uint32_t m_height = 1080;
};

