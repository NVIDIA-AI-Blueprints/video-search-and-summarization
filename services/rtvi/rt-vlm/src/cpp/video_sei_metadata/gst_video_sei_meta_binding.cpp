/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/*
 * Compilation:
 *
 * g++ -O3 -Wall -shared -std=c++11 -I/opt/nvidia/deepstream/deepstream-9.1/sources/includes/ -fPIC $(python3 -m pybind11 --includes) gst_video_sei_meta_binding.cpp -o gst_video_sei_meta$(python3-config --extension-suffix)  $(pkg-config --cflags --libs gstreamer-1.0) -L/opt/nvidia/deepstream/deepstream-9.1/lib/ -lgstnvdsseimeta
 *
 * Usage in a probe callback:
 *
 * import gst_video_sei_meta
 * buffer_address = hash(gst_buffer)
 * video_sei_meta = gst_video_sei_meta.gst_buffer_add_video_sei_meta(buffer_address)
 * video_sei_meta.sei_metadata_type = gst_video_sei_meta.GST_USER_SEI_META()
 * video_sei_meta.sei_metadata_ptr  = b"Some metadata SOME SOME SOME META"
 * video_sei_meta.sei_metadata_size = len (video_sei_meta.sei_metadata_ptr)
 *
 * */

#include <pybind11/pybind11.h>
#include <gst/gst.h>

// Include the original C header file
#include "gstnvdsseimeta.h"

namespace py = pybind11;

PYBIND11_MODULE(gst_video_sei_meta, m) {
    m.doc() = "Python bindings for GstVideoSEIMeta";

    py::class_<GstVideoSEIMeta>(m, "GstVideoSEIMeta")
        .def(py::init<>())
        .def_readwrite("sei_metadata_type", &GstVideoSEIMeta::sei_metadata_type)
        .def_readwrite("sei_metadata_size", &GstVideoSEIMeta::sei_metadata_size)
        .def_property("sei_metadata_ptr",
            [](GstVideoSEIMeta& self) {
                return py::bytes(static_cast<char*>(self.sei_metadata_ptr), self.sei_metadata_size);
            },
            [](GstVideoSEIMeta& self, py::bytes data) {
                // Free existing buffer if present to avoid memory leak
                if (self.sei_metadata_ptr != nullptr) {
                    g_free(self.sei_metadata_ptr);
                    self.sei_metadata_ptr = nullptr;
                    self.sei_metadata_size = 0;
                }

                size_t data_len = py::len(data);
                if (data_len > 0) {
                    // Use GLib allocator (g_malloc) instead of malloc
                    self.sei_metadata_ptr = g_malloc(data_len);
                    if (self.sei_metadata_ptr == nullptr) {
                        throw py::error_already_set();
                    }
                    memcpy(self.sei_metadata_ptr, PyBytes_AsString(data.ptr()), data_len);
                    self.sei_metadata_size = data_len;
                }
            }
        );

    m.def("gst_video_sei_meta_api_get_type", &gst_video_sei_meta_api_get_type, "Get the GType for the GstVideoSEIMeta API");

    m.def("gst_video_sei_meta_get_info", []() {
        return gst_video_sei_meta_get_info();
    }, "Get the GstMetaInfo for GstVideoSEIMeta");

    m.def("gst_buffer_add_video_sei_meta",
        [](py::object buffer) {
            uintptr_t buffer_ptr = py::cast<uintptr_t>(buffer);
            GstBuffer* gst_buffer = reinterpret_cast<GstBuffer*>(buffer_ptr);
            GstVideoSEIMeta* meta = gst_buffer_add_video_sei_meta(gst_buffer);

            // Check for NULL return (can happen if buffer is invalid or read-only)
            if (meta == nullptr) {
                throw std::runtime_error("Failed to add GstVideoSEIMeta to buffer (buffer may be invalid or read-only)");
            }

            return py::cast(meta);
        },"Add a GstVideoSEIMeta to a GstBuffer");

    m.def("gst_buffer_get_video_sei_meta",
        [](py::object buffer) -> py::object {
            uintptr_t buffer_ptr = py::cast<uintptr_t>(buffer);
            GstBuffer* gst_buffer = reinterpret_cast<GstBuffer*>(buffer_ptr);
            GstVideoSEIMeta* meta = gst_buffer_get_video_sei_meta(gst_buffer);

            // Return None if metadata not found (instead of crashing on NULL)
            if (meta == nullptr) {
                return py::none();
            }

            return py::cast(meta);
        },"Get the GstVideoSEIMeta from a GstBuffer");

    m.attr("GST_VIDEO_SEI_META_API_TYPE") = py::cpp_function([]() {
        return gst_video_sei_meta_api_get_type();
    });

    m.attr("GST_VIDEO_SEI_META_INFO") = py::cpp_function([]() {
        return gst_video_sei_meta_get_info();
    });

    m.attr("GST_USER_SEI_META") = py::cpp_function([]() {
        return g_quark_from_static_string("GST.USER.SEI.META");
    });
}
