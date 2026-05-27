/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef MODULES_VIDEO_CODING_CODECS_LIBNV_PASSTHROUGH_ENCODER_H_
#define MODULES_VIDEO_CODING_CODECS_LIBNV_PASSTHROUGH_ENCODER_H_

#include <memory>
#include <string>
#include <vector>
#include <fstream>

#include "api/video/encoded_image.h"
#include "api/video/video_frame.h"
#include "api/video_codecs/video_encoder.h"
#include "api/video_codecs/vp8_frame_buffer_controller.h"
#include "api/video_codecs/vp8_frame_config.h"
#include "modules/video_coding/include/video_codec_interface.h"
#include "common_video/framerate_controller.h"
#include "rtc_base/experiments/cpu_speed_experiment.h"
#include "rtc_base/experiments/rate_control_settings.h"
#include "api/video/i420_buffer.h"
#include "common_video/h264/h264_bitstream_parser.h"
#include "modules/video_coding/codecs/h264/include/h264.h"
#include "modules/video_coding/utility/quality_scaler.h"
#include "modules/video_coding/codecs/nvidia/NvVideoFrameBuffer.h"
#include "api/video_codecs/webrtcstats.h"
#include <syscall.h>

#define MAX_PLANES 3

namespace webrtc {

class RTC_EXPORT NvPassthroughEncoder {
 public:
  static std::unique_ptr<VideoEncoder> Create(const std::string codecName);
};

class NvPassthroughLibs
{
public:
  static NvPassthroughLibs* getInstance();

  bool isError() { return error; }

private:
  static NvPassthroughLibs* _instance;
  bool error;

  NvPassthroughLibs();

  ~NvPassthroughLibs();
};

class NvPassthroughVideoEncoder : public VideoEncoder {
 public:
  NvPassthroughVideoEncoder();
  explicit NvPassthroughVideoEncoder(std::string codecName);
  ~NvPassthroughVideoEncoder() override;

  int Release() override;

  int InitEncode(const VideoCodec* codec_settings,
                 int number_of_cores,
                 size_t max_payload_size) override;

  int Encode(const VideoFrame& input_image,
             const std::vector<VideoFrameType>* frame_types) override;

  int RegisterEncodeCompleteCallback(EncodedImageCallback* callback) override;

  int SetRateAllocation(const VideoBitrateAllocation& bitrate,
                        uint32_t new_framerate);

  void SetRates(const RateControlParameters& parameters) override;

  EncoderInfo GetEncoderInfo() const override;
  WebrtcCodecStats m_rtspToWebrtcLatency;
  int64_t lastStatsPrintTime = 0;

 private:
  std::vector<int> m_naluSizes;
  // Get the cpu_speed setting for encoder based on resolution and/or platform.
  int GetCpuSpeed(int width, int height);

  // Determine number of encoder threads to use.
  int NumberOfThreads(int width, int height, int number_of_cores);

  void PopulateCodecSpecific(CodecSpecificInfo* codec_specific,
                             int stream_idx,
                             int encoder_idx,
                             uint32_t timestamp);

  int GetEncodedPartitions(const VideoFrame& input_image, bool mode = false, void* frame_buffer = nullptr);

  const absl::optional<std::vector<CpuSpeedExperiment::Config>>
      experimental_cpu_speed_config_arm_;
  webrtc::H264BitstreamParser h264_bitstream_parser_;
  H264PacketizationMode packetization_mode_;

  EncodedImageCallback* encoded_complete_callback_;
  std::string codecString;
  size_t pics_since_key_;
  bool first_frame_in_picture_;
  bool ss_info_needed_;
  uint8_t num_temporal_layers_;
  uint8_t num_spatial_layers_;
  uint8_t num_active_spatial_layers_;  // Number of actively encoded SLs
  InterLayerPredMode inter_layer_pred_;
  struct RefFrameBuffer {
    RefFrameBuffer(size_t pic_num,
                   size_t spatial_layer_id,
                   size_t temporal_layer_id)
        : pic_num(pic_num),
          spatial_layer_id(spatial_layer_id),
          temporal_layer_id(temporal_layer_id) {}
    RefFrameBuffer() {}

    bool operator==(const RefFrameBuffer& o) {
      return pic_num == o.pic_num && spatial_layer_id == o.spatial_layer_id &&
             temporal_layer_id == o.temporal_layer_id;
    }

    size_t pic_num = 0;
    size_t spatial_layer_id = 0;
    size_t temporal_layer_id = 0;
  };

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

  std::map<size_t, RefFrameBuffer> ref_buf_;
  VideoCodec codec_;
  bool inited_;
  int64_t timestamp_;
  uint32_t buffer_number_;
  bool released_;
  int qp_max_;
  int cpu_speed_default_;
  int number_of_cores_;
  int flags;
  int encoder_fd;
  uint32_t rc_max_intra_target_;
  std::vector<bool> key_frame_request_;
  std::vector<bool> send_stream_;
  std::vector<int> cpu_speed_;
  std::vector<EncodedImage> encoded_images_;
  std::map<int , rtc::scoped_refptr<NvVideoFrameBuffer>> m_fdBufferMap;
  std::atomic<unsigned int> m_targetBps;
  std::atomic<double> m_frameRate;

  // Variable frame-rate screencast related fields and methods.
  const struct VariableFramerateExperiment {
    bool enabled = false;
    // Framerate is limited to this value in steady state.
    float framerate_limit = 5.0;
    // This qp or below is considered a steady state.
    int steady_state_qp = 15;
    // Frames of at least this percentage below ideal for configured bitrate are
    // considered in a steady state.
    int steady_state_undershoot_percentage = 30;
  } variable_framerate_experiment_;
  static VariableFramerateExperiment ParseVariableFramerateConfig(
      std::string group_name);
  int num_steady_state_frames_;
};

}  // namespace webrtc

#endif  // MODULES_VIDEO_CODING_CODECS_LIBNV_PASSTHROUGH_ENCODER_H_
