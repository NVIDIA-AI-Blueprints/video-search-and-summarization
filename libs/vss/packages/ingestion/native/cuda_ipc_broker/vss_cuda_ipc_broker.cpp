// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "vss_cuda_ipc_broker.h"
#include "cuda_stream_sync.h"
#include "surface_metadata.h"

#include <cuda_runtime_api.h>
#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include <gst/video/video-color.h>
#include <nvbufsurface.h>

#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace {

void set_error(char *output, size_t size, const std::string &message) {
  if (output == nullptr || size == 0) {
    return;
  }
  std::snprintf(output, size, "%s", message.c_str());
}

std::string cuda_error(const char *operation, cudaError_t status) {
  return std::string(operation) + ": " + cudaGetErrorString(status);
}

struct Broker;

struct Slot {
  Broker *broker = nullptr;
  unsigned int index = 0;
  NvBufSurface *surface = nullptr;
};

struct Broker {
  GstElement *pipeline = nullptr;
  GstAppSrc *source = nullptr;
  cudaStream_t copy_stream = nullptr;
  int device_id = 0;
  unsigned int width = 0;
  unsigned int height = 0;
  int color_matrix = 601;
  bool full_range = false;
  bool closing = false;
  bool stopping = false;
  bool stopped = false;
  unsigned int active_publishers = 0;
  unsigned int outstanding_buffers = 0;
  std::mutex mutex;
  std::condition_variable available;
  std::deque<unsigned int> free_slots;
  std::vector<std::unique_ptr<Slot>> slots;
};

void return_slot(gpointer data, GstMiniObject *) {
  auto *slot = static_cast<Slot *>(data);
  Broker *broker = slot->broker;
  {
    std::lock_guard<std::mutex> lock(broker->mutex);
    if (!broker->closing) {
      broker->free_slots.push_back(slot->index);
    }
    broker->outstanding_buffers--;
  }
  broker->available.notify_all();
}

struct PublisherGuard {
  Broker *broker;

  ~PublisherGuard() {
    {
      std::lock_guard<std::mutex> lock(broker->mutex);
      broker->active_publishers--;
    }
    broker->available.notify_all();
  }
};

bool set_bool_if_present(GObject *object, const char *name, gboolean value) {
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(object), name) == nullptr) {
    return false;
  }
  g_object_set(object, name, value, nullptr);
  return true;
}

void stop_broker(Broker *broker) {
  if (broker == nullptr) {
    return;
  }
  {
    std::unique_lock<std::mutex> lock(broker->mutex);
    if (broker->stopped) {
      return;
    }
    if (broker->stopping) {
      broker->available.wait(lock, [broker] { return broker->stopped; });
      return;
    }
    broker->stopping = true;
    broker->closing = true;
  }
  broker->available.notify_all();

  // A publisher can be blocked inside appsrc after it has acquired a slot.
  // Flush the pipeline before waiting so that push-buffer returns.
  if (broker->source != nullptr) {
    g_object_set(broker->source, "block", FALSE, nullptr);
  }
  if (broker->pipeline != nullptr) {
    gst_element_send_event(broker->pipeline, gst_event_new_flush_start());
    gst_element_set_state(broker->pipeline, GST_STATE_NULL);
  }
  {
    std::unique_lock<std::mutex> lock(broker->mutex);
    broker->available.wait(
        lock, [broker] { return broker->active_publishers == 0; });
  }
  if (broker->pipeline != nullptr) {
    gst_object_unref(broker->pipeline);
    broker->pipeline = nullptr;
    broker->source = nullptr;
  }
  {
    std::unique_lock<std::mutex> lock(broker->mutex);
    broker->available.wait(
        lock, [broker] { return broker->outstanding_buffers == 0; });
    broker->stopped = true;
  }
  broker->available.notify_all();
}

void destroy_broker(Broker *broker) {
  if (broker == nullptr) {
    return;
  }
  stop_broker(broker);
  if (broker->copy_stream != nullptr) {
    cudaStreamDestroy(broker->copy_stream);
  }
  for (auto &slot : broker->slots) {
    if (slot->surface != nullptr) {
      NvBufSurfaceDestroy(slot->surface);
    }
  }
  delete broker;
}

}  // namespace

extern "C" void *vss_cuda_ipc_broker_create(
    const char *socket_path, int device_id, unsigned int width,
    unsigned int height, unsigned int slot_count, int color_matrix,
    int full_range, char *error, size_t error_size) {
  if (socket_path == nullptr || socket_path[0] != '/' || width == 0 ||
      height == 0 || (height % 2) != 0 || slot_count < 2 ||
      (color_matrix != 601 && color_matrix != 709) ||
      (full_range != 0 && full_range != 1)) {
    set_error(error, error_size, "invalid CUDA IPC broker arguments");
    return nullptr;
  }
  gst_init(nullptr, nullptr);
  auto *broker = new Broker();
  broker->device_id = device_id;
  broker->width = width;
  broker->height = height;
  broker->color_matrix = color_matrix;
  broker->full_range = full_range != 0;
  if (cudaSetDevice(device_id) != cudaSuccess) {
    set_error(error, error_size, "could not select CUDA device");
    destroy_broker(broker);
    return nullptr;
  }
  cudaError_t cuda_status =
      cudaStreamCreateWithFlags(&broker->copy_stream, cudaStreamNonBlocking);
  if (cuda_status != cudaSuccess) {
    set_error(error, error_size, cuda_error("cudaStreamCreate", cuda_status));
    destroy_broker(broker);
    return nullptr;
  }

  broker->pipeline = gst_pipeline_new("vss-cuda-ipc-broker");
  GstElement *source = gst_element_factory_make("appsrc", "source");
  GstElement *sink = gst_element_factory_make("nvunixfdsink", "ipc-sink");
  if (broker->pipeline == nullptr || source == nullptr || sink == nullptr) {
    set_error(error, error_size,
              "DeepStream appsrc or nvunixfdsink element is unavailable");
    if (source != nullptr) gst_object_unref(source);
    if (sink != nullptr) gst_object_unref(sink);
    destroy_broker(broker);
    return nullptr;
  }
  broker->source = GST_APP_SRC(source);
  GstVideoColorimetry colorimetry{};
  gst_video_colorimetry_from_string(
      &colorimetry, color_matrix == 709 ? GST_VIDEO_COLORIMETRY_BT709
                                        : GST_VIDEO_COLORIMETRY_BT601);
  if (broker->full_range) {
    colorimetry.range = GST_VIDEO_COLOR_RANGE_0_255;
  }
  gchar *colorimetry_text = gst_video_colorimetry_to_string(&colorimetry);
  GstCaps *caps = gst_caps_from_string(
      ("video/x-raw(memory:NVMM),format=NV12,width=" +
       std::to_string(width) + ",height=" + std::to_string(height) +
       ",framerate=0/1,colorimetry=" + colorimetry_text)
          .c_str());
  g_free(colorimetry_text);
  g_object_set(source, "caps", caps, "format", GST_FORMAT_TIME, "block", TRUE,
               "is-live", FALSE, nullptr);
  gst_caps_unref(caps);
  g_object_set(sink, "socket-path", socket_path, nullptr);
  set_bool_if_present(G_OBJECT(sink), "buffer-copy", FALSE);
  set_bool_if_present(G_OBJECT(sink), "buffer-timestamp-copy", TRUE);
  gst_bin_add_many(GST_BIN(broker->pipeline), source, sink, nullptr);
  if (!gst_element_link(source, sink)) {
    set_error(error, error_size, "could not link appsrc to nvunixfdsink");
    destroy_broker(broker);
    return nullptr;
  }

  NvBufSurfaceCreateParams params{};
  params.gpuId = device_id;
  params.width = width;
  params.height = height;
  params.size = 0;
  params.colorFormat = vss_nv12_color_format(color_matrix, broker->full_range);
  params.layout = NVBUF_LAYOUT_PITCH;
  params.memType = NVBUF_MEM_CUDA_DEVICE;
  for (unsigned int index = 0; index < slot_count; ++index) {
    auto slot = std::make_unique<Slot>();
    slot->broker = broker;
    slot->index = index;
    if (NvBufSurfaceCreate(&slot->surface, 1, &params) != 0) {
      set_error(error, error_size, "could not allocate NV12 NvBufSurface");
      destroy_broker(broker);
      return nullptr;
    }
    vss_mark_surface_filled(slot->surface);
    broker->free_slots.push_back(index);
    broker->slots.push_back(std::move(slot));
  }
  GstStateChangeReturn state =
      gst_element_set_state(broker->pipeline, GST_STATE_PLAYING);
  if (state == GST_STATE_CHANGE_FAILURE) {
    set_error(error, error_size, "CUDA IPC publisher failed to enter PLAYING");
    destroy_broker(broker);
    return nullptr;
  }
  return broker;
}

extern "C" int vss_cuda_ipc_broker_publish(
    void *opaque, uint64_t device_pointer, size_t source_pitch,
    uint64_t timestamp_ns, uint64_t producer_stream, char *error,
    size_t error_size) {
  auto *broker = static_cast<Broker *>(opaque);
  if (broker == nullptr || device_pointer == 0 ||
      source_pitch < broker->width) {
    set_error(error, error_size, "invalid CUDA IPC frame");
    return 1;
  }
  {
    std::lock_guard<std::mutex> lock(broker->mutex);
    if (broker->closing) {
      set_error(error, error_size, "CUDA IPC broker is closing");
      return 1;
    }
    broker->active_publishers++;
  }
  PublisherGuard publisher{broker};
  cudaError_t select_status = cudaSetDevice(broker->device_id);
  if (select_status != cudaSuccess) {
    set_error(error, error_size, cuda_error("cudaSetDevice", select_status));
    return 1;
  }
  unsigned int index = 0;
  {
    std::unique_lock<std::mutex> lock(broker->mutex);
    broker->available.wait(
        lock, [broker] { return broker->closing || !broker->free_slots.empty(); });
    if (broker->closing) {
      set_error(error, error_size, "CUDA IPC broker is closing");
      return 1;
    }
    index = broker->free_slots.front();
    broker->free_slots.pop_front();
  }
  Slot *slot = broker->slots[index].get();
  NvBufSurfaceParams &destination = slot->surface->surfaceList[0];
  const auto *source = reinterpret_cast<const unsigned char *>(device_pointer);
  auto *target = static_cast<unsigned char *>(destination.dataPtr);
  const NvBufSurfacePlaneParams &planes = destination.planeParams;
  cudaEvent_t producer_ready = nullptr;
  cudaError_t status = vss_wait_for_producer(
      broker->copy_stream, producer_stream, &producer_ready);
  if (status == cudaSuccess) {
    status = cudaMemcpy2DAsync(
        target + planes.offset[0], planes.pitch[0], source, source_pitch,
        broker->width, broker->height, cudaMemcpyDeviceToDevice,
        broker->copy_stream);
  }
  if (status == cudaSuccess) {
    status = cudaMemcpy2DAsync(
        target + planes.offset[1], planes.pitch[1],
        source + source_pitch * broker->height, source_pitch, broker->width,
        broker->height / 2, cudaMemcpyDeviceToDevice, broker->copy_stream);
  }
  if (status == cudaSuccess) {
    status = cudaStreamSynchronize(broker->copy_stream);
  }
  if (producer_ready != nullptr) {
    cudaError_t destroy_status = cudaEventDestroy(producer_ready);
    if (status == cudaSuccess) {
      status = destroy_status;
    }
  }
  if (status != cudaSuccess) {
    {
      std::lock_guard<std::mutex> lock(broker->mutex);
      broker->free_slots.push_back(index);
    }
    broker->available.notify_one();
    set_error(error, error_size, cuda_error("NV12 device copy", status));
    return 1;
  }

  GstBuffer *buffer =
      gst_buffer_new_allocate(nullptr, sizeof(NvBufSurface), nullptr);
  GstMapInfo map{};
  if (buffer == nullptr ||
      !gst_buffer_map(buffer, &map, GST_MAP_WRITE)) {
    if (buffer != nullptr) gst_buffer_unref(buffer);
    {
      std::lock_guard<std::mutex> lock(broker->mutex);
      broker->free_slots.push_back(index);
    }
    broker->available.notify_one();
    set_error(error, error_size, "could not allocate NvBufSurface GstBuffer");
    return 1;
  }
  vss_mark_surface_filled(slot->surface);
  std::memcpy(map.data, slot->surface, sizeof(NvBufSurface));
  gst_buffer_unmap(buffer, &map);
  GST_BUFFER_PTS(buffer) = timestamp_ns;
  GST_BUFFER_DTS(buffer) = timestamp_ns;
  {
    std::lock_guard<std::mutex> lock(broker->mutex);
    broker->outstanding_buffers++;
  }
  gst_mini_object_weak_ref(GST_MINI_OBJECT(buffer), return_slot, slot);
  GstFlowReturn flow = gst_app_src_push_buffer(broker->source, buffer);
  if (flow != GST_FLOW_OK) {
    set_error(error, error_size,
              "nvunixfdsink rejected a decoded frame");
    return 1;
  }
  return 0;
}

extern "C" int vss_cuda_ipc_broker_eos(void *opaque, char *error,
                                         size_t error_size) {
  auto *broker = static_cast<Broker *>(opaque);
  if (broker == nullptr) {
    set_error(error, error_size, "CUDA IPC broker is not initialized");
    return 1;
  }
  {
    std::lock_guard<std::mutex> lock(broker->mutex);
    if (broker->closing) {
      set_error(error, error_size, "CUDA IPC broker is closing");
      return 1;
    }
    broker->active_publishers++;
  }
  PublisherGuard publisher{broker};
  GstFlowReturn flow = gst_app_src_end_of_stream(broker->source);
  if (flow != GST_FLOW_OK) {
    set_error(error, error_size, "nvunixfdsink rejected end-of-stream");
    return 1;
  }
  return 0;
}

extern "C" void vss_cuda_ipc_broker_stop(void *opaque) {
  stop_broker(static_cast<Broker *>(opaque));
}

extern "C" void vss_cuda_ipc_broker_destroy(void *opaque) {
  destroy_broker(static_cast<Broker *>(opaque));
}
