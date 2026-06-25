/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 * 
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 * list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#pragma once

#include <stdint.h>
#include <mutex>

#include <api/task_queue/task_queue_factory.h>
#include <api/video/encoded_image.h>
#include <api/video_codecs/video_codec.h>
#include <api/video_codecs/video_decoder.h>
#include <modules/video_coding/include/video_codec_interface.h>
#include <rtc_base/task_queue.h>
#include <linux/videodev2.h>
#include <queue>
#include "v4l2_nv_extensions.h"
#include "nvbufsurface.h"
#include "nvbufsurftransform.h"
#include "common_video/include/video_frame_buffer_pool.h"
#include <condition_variable>

#define MAX_PLANES 3
#define NUM_OP_BUFFERS 2

namespace webrtc {

typedef int (*v4l2_ioctl_t) (int , unsigned long int , ...);
typedef int (*v4l2_open_t) (const char *, int, ...);
typedef int (*v4l2_close_t) (int);
/*nvbufsurface APIs*/
typedef int (*NvBufSurfaceFromFd_t) (int, void **);

class RTC_EXPORT NvDecoder {
 public:
  static std::unique_ptr<VideoDecoder> Create(const std::string codecName);
};

struct InputFrameInfo
{
  uint32_t timestamp;
  int64_t startLatency;
};

struct InputData
{
  uint8_t* data;
  size_t size;
};

class NvDecLibs
{
public:
  static NvDecLibs* getInstance();

  v4l2_ioctl_t v4l2_ioctl;
  v4l2_open_t v4l2_open;
  v4l2_close_t v4l2_close;
  NvBufSurfaceFromFd_t NvBufSurfaceFromFd;

  bool isError() { return error; }

private:
  static NvDecLibs* _instance;
  bool error;
  void* handle_v4l2;
  void* handle_nvbufsurface;

  NvDecLibs();

  ~NvDecLibs();
};

/**
 * @brief Class representing a buffer.
 *
 * The Buffer class is modeled on the basis of the @c v4l2_buffer
 * structure. The buffer has @c buf_type @c v4l2_buf_type, @c
 * memory_type @c v4l2_memory, and an index. It contains an
 * BufferPlane array similar to the array of @c v4l2_plane
 * structures in @c v4l2_buffer.m.planes. It also contains a
 * corresponding BufferPlaneFormat array that describes the
 * format of each of the planes.
 *
 * In the case of a V4L2 MMAP, this class provides convenience methods
 * for mapping or unmapping the contents of the buffer to or from
 * memory, allocating or deallocating software memory depending on its
 * format.
 */

class Buffer
{
public:
    /**
     * Holds the buffer plane format.
     */
    typedef struct
    {
        uint32_t width;             /**< Holds the width of the plane in pixels. */
        uint32_t height;            /**< Holds the height of the plane in pixels. */

        uint32_t bytesperpixel;     /**< Holds the bytes used to represent one
                                      pixel in the plane. */
        uint32_t stride;            /**< Holds the stride of the plane in bytes. */
        uint32_t sizeimage;         /**< Holds the size of the plane in bytes. */
    } BufferPlaneFormat;

    /**
     * Holds the buffer plane parameters.
     */
    typedef struct
    {
        BufferPlaneFormat fmt;    /**< Holds the format of the plane. */

        unsigned char *data;        /**< Holds a pointer to the plane memory. */
        uint32_t bytesused;         /**< Holds the number of valid bytes in the plane. */

        int fd;                     /**< Holds the file descriptor (FD) of the plane of the
                                      exported buffer, in the case of V4L2 MMAP buffers. */
        uint32_t mem_offset;        /**< Holds the offset of the first valid byte
                                      from the data pointer. */
        uint32_t length;            /**< Holds the size of the buffer in bytes. */
        uint32_t data_offset;       /** < Holds the offset to the buffer in bytes. */
    } BufferPlane;

    /**
     * Constructor for the Buffer class
     *
     * Calling will create an instance for Buffer class and would help calling
     * its member functions.
     */

    Buffer(enum v4l2_buf_type buf_type, enum v4l2_memory memory_type,
           uint32_t n_planes, BufferPlaneFormat *fmt, uint32_t index, bool is_cuvid);


    /**
     * Destructor for the Buffer class
     */

     ~Buffer();

    /**
     * Maps the contents of the buffer to memory.
     *
     * This method maps the file descriptor (FD) of the planes to
     * a data pointer of @c planes. (MMAP buffers only.)
     */
    int map();
    /**
     * Unmaps the contents of the buffer from memory. (MMAP buffers only.)
     *
     */
    void unmap();

    enum v4l2_buf_type buf_type;  /**< Type of the buffer. */
    enum v4l2_memory memory_type; /**< Type of memory associated
                                           with the buffer. */

    uint32_t index;               /**< Holds the buffer index. */

    uint32_t n_planes;            /**< Holds the number of planes in the buffer. */
    BufferPlane planes[MAX_PLANES];     /**< Holds the data pointer, plane file
                                             descriptor (FD), plane format, etc. */

    /**
     * Fills the Buffer::BufferPlaneFormat array.
     */
    static int fill_buffer_plane_format(uint32_t *num_planes,
            Buffer::BufferPlaneFormat *planefmts,
            uint32_t width, uint32_t height, uint32_t raw_pixfmt);

private:

    bool is_cuvid;
    bool mapped;
};

class WebrtcSyncObject
{
    public:
        bool wait(unsigned int duration)
        {
            bool ret = true;
            std::unique_lock<std::mutex> lock(m_Lock);
            auto until = std::chrono::system_clock::now() + std::chrono::milliseconds(duration);
            if(m_flag == false)
            {
                ret = m_cond.wait_until(lock, until , [this]{ return m_flag.load(); });
            }
            return ret;
        }

        void signal()
        {
            std::unique_lock<std::mutex> lock(m_Lock);
            m_flag = true;
            m_cond.notify_all();
        }
    private:
        std::mutex               m_Lock;
        std::condition_variable  m_cond;
        std::atomic<bool>        m_flag {false};
};

class NvVideoDecoder : public webrtc::VideoDecoder
{
 public:
  NvVideoDecoder();
  NvVideoDecoder(std::string format);
  explicit NvVideoDecoder(webrtc::TaskQueueFactory* taskQueueFactory);
  ~NvVideoDecoder() override;

  bool Configure(const Settings& settings) override;

  int32_t Decode(const webrtc::EncodedImage& input,
                 bool missingFrames,
                 int64_t renderTimeMs) override;

  int32_t RegisterDecodeCompleteCallback(
      webrtc::DecodedImageCallback* callback) override;

  int32_t Release() override;

  void Reset();

  DecoderInfo GetDecoderInfo() const override;
  const char* ImplementationName() const override;

  static const char* kImplementationName;

  void SetDelayedDecoding(int decodeDelayMs);

  int flags = 0;
  int decoder_fd = -1;
  struct v4l2_buffer v4l2_buf_op = {0};
  struct v4l2_plane v4l2_plane_op[MAX_PLANES] = {{0}};
  v4l2_capability caps = {{0}};
  struct v4l2_format op_format = {0};
  int frameCount = 0;

  struct v4l2_requestbuffers op_reqbuf = {0};
  struct v4l2_requestbuffers cp_reqbuf = {0};
  struct v4l2_exportbuffer op_expbuf = {0};
  struct v4l2_event_subscription sub = {0};
  uint32_t decode_pixfmt = 0;
  uint32_t out_pixfmt = 0;

  struct v4l2_ext_control control = {0};
  struct v4l2_ext_controls ctrls = {{0}};
  Buffer::BufferPlaneFormat op_planefmts[MAX_PLANES] = {{0}};
  Buffer::BufferPlaneFormat cp_planefmts[MAX_PLANES] = {{0}};
  Buffer **op_buffers = NULL;
  Buffer **cp_buffers = NULL;
  uint32_t op_index = 0;
  bool is_drc = false;
  bool is_cuvid = false;
  int gpu_index = 0;
  std::atomic<bool> init_done {false};
  bool key_frame_required_ = false;

  bool op_streamon = false;
  bool cp_streamon = false;
  bool eos = false;
  bool in_error = false;
  bool got_eos = false;
  bool add_sar = false;

  enum v4l2_memory op_mem_type;
  enum v4l2_memory cp_mem_type;
  enum v4l2_buf_type op_buf_type;
  enum v4l2_buf_type cp_buf_type;
  struct v4l2_event event = {0};

 private:
  webrtc::DecodedImageCallback* m_callback = NULL;
  int m_width = 0;
  int m_height = 0;
  std::string m_codec = "H264";
  std::unique_ptr<rtc::TaskQueue> m_taskQueue = NULL;
  webrtc::TaskQueueFactory* m_taskQueueFactory = NULL;
  webrtc::TimeDelta m_decodeDelayMs;
  pthread_mutex_t queue_lock;
  pthread_cond_t queue_cond;
  uint32_t cp_num_planes = 0;
  uint32_t op_num_planes = 0;
  uint32_t cp_num_buffers = 0;
  uint32_t op_num_buffers = 0;
  std::atomic<int> num_queued_op_buffers {0};
  std::atomic<int> num_queued_cp_buffers {0};
  uint32_t crop_width = 0;
  uint32_t crop_height = 0;
  uint32_t sar_width = 0;
  uint32_t sar_height = 0;
  Settings init_settings;
  std::mutex m_releaseLock;
  std::mutex m_inputFrameInfoQueueLock;
  std::queue<struct InputFrameInfo> m_inputFrameInfoQueue;
  std::mutex m_inputQueueLock;
  std::queue<struct InputData> m_inputQueue;
  WebrtcSyncObject syncObj = {};

  int dqBuffer (struct v4l2_buffer &v4l2_buf, Buffer ** buffer,
    enum v4l2_buf_type buf_type, enum v4l2_memory memory_type, uint32_t num_retries);
  int qBuffer (struct v4l2_buffer &v4l2_buf, Buffer * buffer,
    enum v4l2_buf_type buf_type, enum v4l2_memory memory_type, int num_planes);
  void check_sar();
  int query_and_set_capture();

};

}
