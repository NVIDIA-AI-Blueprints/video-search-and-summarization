#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2020-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -e

vst_package=""
if [[ -z ${TOP} ]]; then
	SCRIPT_DIR="$(dirname "$(readlink -f "${0}")")"
	TOP="${SCRIPT_DIR%packaging}"
fi
ARCH=$(uname -m)
if [[ ! -z "${CROSS_COMPILE}" ]]; then
	ARCH=aarch64
fi
OUT_REL="out/${ARCH}"
OUT=${TOP}/${OUT_REL}

# $1 - name of package
create_package() {
	local tmp_dir="${OUT}/tmp"
	local package_name="${1}"
	rm -rf "${tmp_dir}"
	mkdir -p "${tmp_dir}"
	pushd "${tmp_dir}"
	for file in "${mappings[@]}"; do
		source_file=$(echo $file | cut -d"=" -f1)
		destination_file=$(echo $file | cut -d"=" -f2)
		if [[ "${destination_file:${#destination_file}-1}" == "/" ]]; then
			install -dv ${destination_file}
		else
			install -dv $(dirname "${destination_file}")
		fi
		cp -rvf ${TOP}/${source_file} \
			"${destination_file}"
	done
	tar -I lbzip2 -cvf "${OUT}/${package_name}" *
	tar tf "${OUT}/${package_name}" > "${OUT}/${package_name}.list.txt"
	popd
	rm -rf "${tmp_dir}"
}

#PROJECT variable
# ------------------------------------#
# MMS        : milestone_vms.so
# ROSIE      : onvif_client.so + onvif_discovery.so
# VST        : onvif_client.so + onvif_discovery.so + rtsp_streams.so
# NVSTREAMER : nvstreamer.so
# TOKKIO     : rtsp_streams.so


add_common_files() {
	mappings+=("include/*.h=${PACKAGE_DIR}/include/")
	mappings+=("configs/vst_config.json=${PACKAGE_DIR}/configs/vst_config.json")
	mappings+=("configs/vst_storage.json=${PACKAGE_DIR}/configs/vst_storage.json")
	mappings+=("configs/adaptor_config.json=${PACKAGE_DIR}/configs/adaptor_config.json")

	# PROJECT = MMS
	if [[ -z ${PROJECT} ]] || [[ ${PROJECT} = mms ]] || [[ ${PROJECT} = all ]]; then
		if [[ -z ${MODULE} ]] || [[ ${MODULE} = sensor ]]; then
			mappings+=("prebuilts/${ARCH}/milestone_vms.so=${PACKAGE_DIR}/prebuilts/${ARCH}/milestone_vms.so")
		fi
		if [[ -z ${MODULE} ]] || [[ ${MODULE} = storage ]] || [[ ${MODULE} = streamprocessing ]]; then
			mappings+=("prebuilts/${ARCH}/vms_media.so=${PACKAGE_DIR}/prebuilts/${ARCH}/vms_media.so")
		fi
	fi
	# PROJECT = rosie || vst
	if [[ -z ${PROJECT} ]] || [[ ${PROJECT} = rosie ]] ||  [[ ${PROJECT} = vst ]] || [[ ${PROJECT} = all ]]; then
		if [[ -z ${MODULE} ]] || [[ ${MODULE} = sensor ]]; then
			mappings+=("prebuilts/${ARCH}/onvif_client.so=${PACKAGE_DIR}/prebuilts/${ARCH}/onvif_client.so")
			mappings+=("prebuilts/${ARCH}/onvif_discovery.so=${PACKAGE_DIR}/prebuilts/${ARCH}/onvif_discovery.so")
			mappings+=("prebuilts/${ARCH}/libremotedevice.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libremotedevice.so")
			mappings+=("prebuilts/${ARCH}/libnativesensors_discovery.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnativesensors_discovery.so")
			mappings+=("prebuilts/${ARCH}/libnativesensors_control.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnativesensors_control.so")
		fi
	fi
	# PROJECT = nvstreamer
	if [[ -z ${PROJECT} ]] || [[ ${PROJECT} = nvstreamer ]] || [[ ${PROJECT} = all ]]; then
		if [[ -z ${MODULE} ]] || [[ ${MODULE} = sensor ]]; then
			mappings+=("prebuilts/${ARCH}/liblocalstreams.so=${PACKAGE_DIR}/prebuilts/${ARCH}/liblocalstreams.so")
			mappings+=("prebuilts/${ARCH}/libsensordata.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libsensordata.so")
		fi
	fi
	# PROJECT = vst || tokkio
	if [[ -z ${PROJECT} ]] || [[ ${PROJECT} = vst ]] ||  [[ ${PROJECT} = tokkio ]] || [[ ${PROJECT} = all ]]; then
		if [[ -z ${MODULE} ]] || [[ ${MODULE} = sensor ]]; then
			mappings+=("prebuilts/${ARCH}/rtsp_streams.so=${PACKAGE_DIR}/prebuilts/${ARCH}/rtsp_streams.so")
			mappings+=("configs/rtsp_streams.json=${PACKAGE_DIR}/configs/rtsp_streams.json")
			mappings+=("prebuilts/${ARCH}/liblocalstreams.so=${PACKAGE_DIR}/prebuilts/${ARCH}/liblocalstreams.so")
			mappings+=("prebuilts/${ARCH}/libsensordata.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libsensordata.so")
		fi
	fi
	mappings+=("prebuilts/${ARCH}/libnvsoap.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvsoap.so")
	mappings+=("prebuilts/${ARCH}/libstream_monitor.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libstream_monitor.so")
	mappings+=("prebuilts/${ARCH}/libnvds_schema_2d.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_schema_2d.so")
	mappings+=("prebuilts/${ARCH}/libnvds_schema_3d.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_schema_3d.so")

	mappings+=("prebuilts/${ARCH}/liblivemedia.so=${PACKAGE_DIR}/prebuilts/${ARCH}/liblivemedia.so")
	mappings+=("prebuilts/${ARCH}/libasync++.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libasync++.so")
	mappings+=("prebuilts/${ARCH}/prom_so/libprometheus-cpp-core.so=${PACKAGE_DIR}/prebuilts/${ARCH}/prom_so/libprometheus-cpp-core.so")
	mappings+=("prebuilts/${ARCH}/prom_so/libprometheus-cpp-core.so.0.12=${PACKAGE_DIR}/prebuilts/${ARCH}/prom_so/libprometheus-cpp-core.so.0.12")
	mappings+=("prebuilts/${ARCH}/prom_so/libprometheus-cpp-core.so.0.12.3=${PACKAGE_DIR}/prebuilts/${ARCH}/prom_so/libprometheus-cpp-core.so.0.12.3")
	mappings+=("prebuilts/${ARCH}/prom_so/libprometheus-cpp-pull.so=${PACKAGE_DIR}/prebuilts/${ARCH}/prom_so/libprometheus-cpp-pull.so")
	mappings+=("prebuilts/${ARCH}/prom_so/libprometheus-cpp-pull.so.0.12=${PACKAGE_DIR}/prebuilts/${ARCH}/prom_so/libprometheus-cpp-pull.so.0.12")
	mappings+=("prebuilts/${ARCH}/prom_so/libprometheus-cpp-pull.so.0.12.3=${PACKAGE_DIR}/prebuilts/${ARCH}/prom_so/libprometheus-cpp-pull.so.0.12.3")
	mappings+=("prebuilts/${ARCH}/libnvds_redis_proto.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_redis_proto.so")
	mappings+=("prebuilts/${ARCH}/libnvds_logger.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_logger.so")
	mappings+=("prebuilts/${ARCH}/gst-plugins/libgstcuosd.so=${PACKAGE_DIR}/prebuilts/${ARCH}/gst-plugins/libgstcuosd.so")
	mappings+=("prebuilts/${ARCH}/libcuosd.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libcuosd.so")
	mappings+=("prebuilts/${ARCH}/libllosd.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libllosd.so")
	mappings+=("prebuilts/${ARCH}/libgstcuosdmeta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libgstcuosdmeta.so")

	mappings+=("prebuilts/${ARCH}/libmmutils.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libmmutils.so")
	mappings+=("prebuilts/${ARCH}/libnvcmdlineparser.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvcmdlineparser.so")
	mappings+=("prebuilts/${ARCH}/libnvconfigmanager.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvconfigmanager.so")
	mappings+=("prebuilts/${ARCH}/libnveventloop.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnveventloop.so")
	mappings+=("prebuilts/${ARCH}/libnvfpsdisplay.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvfpsdisplay.so")
	mappings+=("prebuilts/${ARCH}/libnvfsutils.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvfsutils.so")
	mappings+=("prebuilts/${ARCH}/libnvlogger.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvlogger.so")
	mappings+=("prebuilts/${ARCH}/libnvnetworkutils.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvnetworkutils.so")
	mappings+=("prebuilts/${ARCH}/libnvstatistics.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstatistics.so")
	mappings+=("prebuilts/${ARCH}/libnvutils.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvutils.so")
	mappings+=("prebuilts/${ARCH}/libnvdatabase.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvdatabase.so")
	mappings+=("packaging/user_additional_install.sh=${PACKAGE_DIR}/tools/user_additional_install.sh")
	mappings+=("prebuilts/${ARCH}/libnvsystemmonitoring.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvsystemmonitoring.so")
	mappings+=("prebuilts/${ARCH}/libnvvideo_source.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvvideo_source.so")
	mappings+=("prebuilts/${ARCH}/libnvoverlays.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvoverlays.so")
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = sensor ]] || [[ ${MODULE} = streambridge ]]; then
		mappings+=("prebuilts/${ARCH}/libnvsensormanagement.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvsensormanagement.so")
	fi
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = rtspserver ]] || [[ ${MODULE} = streambridge ]] || [[ ${MODULE} = streamprocessing ]]; then
		mappings+=("prebuilts/${ARCH}/libnvrtspserver.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvrtspserver.so")
		mappings+=("prebuilts/${ARCH}/libnvwebrtc_streamer.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvwebrtc_streamer.so")
	fi
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = recorder ]] || [[ ${MODULE} = streambridge ]] || [[ ${MODULE} = streamprocessing ]]; then
		mappings+=("prebuilts/${ARCH}/libnvstreamrecorder.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstreamrecorder.so")
		mappings+=("prebuilts/${ARCH}/libnvstoragewriter.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstoragewriter.so")
	fi
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = streamprocessing ]] || [[ ${MODULE} = storage ]] || [[ ${MODULE} = replaystream ]] || [[ ${MODULE} = livestream ]] || [[ ${MODULE} = rtspserver ]]; then
		mappings+=("prebuilts/${ARCH}/libclip_producer.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libclip_producer.so")
		mappings+=("prebuilts/${ARCH}/libs3stream_producer.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libs3stream_producer.so")
	fi

	if [[ -z ${MODULE} ]] || [[ ${MODULE} = streamprocessing ]] || [[ ${MODULE} = storage ]] || [[ ${MODULE} = streambridge ]] || [[ ${MODULE} = livestream ]] || [[ ${MODULE} = replaystream ]] || [[ ${MODULE} = rtspserver ]]; then
		# AWS SDK Core and CRT libraries
		mappings+=("prebuilts/${ARCH}/aws/libaws-cpp-sdk-core.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-cpp-sdk-core.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-crt-cpp.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-crt-cpp.so")
		# AWS C libraries for complete S3 functionality
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-auth.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-auth.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-auth.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-auth.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-common.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-common.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-common.so.1=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-common.so.1")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-common.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-common.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-event-stream.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-event-stream.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-event-stream.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-event-stream.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-mqtt.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-mqtt.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-mqtt.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-mqtt.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-io.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-io.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-io.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-io.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-s3.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-s3.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-s3.so.0unstable=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-s3.so.0unstable")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-s3.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-s3.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-http.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-http.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-http.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-http.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-cal.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-cal.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-cal.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-cal.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-compression.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-compression.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-compression.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-compression.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-sdkutils.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-sdkutils.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-c-sdkutils.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-c-sdkutils.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libaws-checksums.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-checksums.so")
		mappings+=("prebuilts/${ARCH}/aws/libaws-checksums.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libaws-checksums.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/aws/libs2n.so=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libs2n.so")
		mappings+=("prebuilts/${ARCH}/aws/libs2n.so.1=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libs2n.so.1")
		mappings+=("prebuilts/${ARCH}/aws/libs2n.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/aws/libs2n.so.1.0.0")
		
		# Unified Storage libraries
		mappings+=("prebuilts/${ARCH}/libnvstoragereader.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstoragereader.so")
		mappings+=("prebuilts/${ARCH}/libnvstoragemanager.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstoragemanager.so")
	fi

	if [[ -z ${MODULE} ]] || [[ ${MODULE} = streamprocessing ]] || [[ ${MODULE} = storage ]] || [[ ${MODULE} = streambridge ]]; then
		mappings+=("prebuilts/${ARCH}/libnvstoragemanagement.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstoragemanagement.so")
		mappings+=("prebuilts/${ARCH}/libvideosegmentextractor.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libvideosegmentextractor.so")
		mappings+=("prebuilts/${ARCH}/libnvstoragemanager.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstoragemanager.so")
	fi
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = streamprocessing ]] || [[ ${MODULE} = livestream ]]; then
		mappings+=("prebuilts/${ARCH}/libnvwebrtc_streamer.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvwebrtc_streamer.so")
		mappings+=("prebuilts/${ARCH}/libnvpeerconnection_live.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvpeerconnection_live.so")
	fi
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = streamprocessing ]] || [[ ${MODULE} = replaystream ]]; then
		mappings+=("prebuilts/${ARCH}/libnvwebrtc_streamer.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvwebrtc_streamer.so")
		mappings+=("prebuilts/${ARCH}/libnvpeerconnection_replay.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvpeerconnection_replay.so")
	fi
	if [[ -z ${MODULE} ]] || [[ ${MODULE} = streambridge ]]; then
		mappings+=("prebuilts/${ARCH}/libnvwebrtc_streamer.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvwebrtc_streamer.so")
		mappings+=("prebuilts/${ARCH}/libnvstreambridge.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvstreambridge.so")
	fi
	if [[ ${ARCH} = x86_64 ]]; then
		mappings+=("prebuilts/${ARCH}/deepstream/libnvbufsurface.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvbufsurface.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvbuf_fdmap.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvbuf_fdmap.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libcuvidv4l2.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libcuvidv4l2.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libcuvidv4l2_plugin.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libcuvidv4l2_plugin.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvbufsurftransform.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvbufsurftransform.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libgstnvdsseimeta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libgstnvdsseimeta.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvdsbufferpool.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvdsbufferpool.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvdsgst_helper.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvdsgst_helper.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvdsgst_meta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvdsgst_meta.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvds_meta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvds_meta.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvds_lljpeg.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvds_lljpeg.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libgstnvcustomhelper.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libgstnvcustomhelper.so")
		mappings+=("prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideoconvert.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideoconvert.so")
		mappings+=("prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideo4linux2.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideo4linux2.so")
		mappings+=("prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvidconv.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvidconv.so")
		mappings+=("prebuilts/${ARCH}/gst-plugins/libgstapp-1.0.so.0.1603.0=${PACKAGE_DIR}/prebuilts/${ARCH}/gst-plugins/libgstapp-1.0.so.0.1603.0")
		mappings+=("prebuilts/${ARCH}/libnvds_kafka_proto.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_kafka_proto.so")
		mappings+=("prebuilts/${ARCH}/libnvds_schema_3d.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_schema_3d.so")
		mappings+=("prebuilts/${ARCH}/libnvds_schema_2d.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_schema_2d.so")
		mappings+=("tools/x86_64/testRTSPClient=${PACKAGE_DIR}/tools/testRTSPClient")
		mappings+=("tools/x86_64/testWebrtcTool=${PACKAGE_DIR}/tools/testWebrtcTool")
		mappings+=("tools/x86_64/tokkioTestClient=${PACKAGE_DIR}/tools/tokkioTestClient")
		#mappings+=("prebuilts/${ARCH}/libpq.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libpq.so")
		#mappings+=("prebuilts/${ARCH}/libpq.so.5=${PACKAGE_DIR}/prebuilts/${ARCH}/libpq.so.5")
		#mappings+=("prebuilts/${ARCH}/libpq.so.5.14=${PACKAGE_DIR}/prebuilts/${ARCH}/libpq.so.5.14")
		#mappings+=("prebuilts/${ARCH}/libpqxx-7.8.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libpqxx-7.8.so")
		#mappings+=("prebuilts/${ARCH}/libpqxx.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libpqxx.so")
		mappings+=("tools/prerecorded_data.sh=${PACKAGE_DIR}/tools/prerecorded_data.sh")
		if [[ -z ${MODULE} ]] || [[ ${MODULE} = streamprocessing ]] || [[ ${MODULE} = streambridge ]]; then
			mappings+=("prebuilts/${ARCH}/libgrpc.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libgrpc.so")
		fi
	fi

	if [[ ${ARCH} = aarch64 ]]; then
		mappings+=("tools/aarch64/testRTSPClient=${PACKAGE_DIR}/tools/testRTSPClient")
		mappings+=("tools/aarch64/testWebrtcTool=${PACKAGE_DIR}/tools/testWebrtcTool")
		mappings+=("prebuilts/${ARCH}/gst-plugins/libgstapp-1.0.so.0.1603.0.so=${PACKAGE_DIR}/prebuilts/${ARCH}/gst-plugins/libgstapp-1.0.so.0.1603.0.so")
		mappings+=("prebuilts/${ARCH}/gst-plugins/libgstnvvideoconvert.so=${PACKAGE_DIR}/prebuilts/${ARCH}/gst-plugins/libgstnvvideoconvert.so")
		mappings+=("prebuilts/${ARCH}/libnvdsgst_ipcmeta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvdsgst_ipcmeta.so")
		mappings+=("prebuilts/${ARCH}/libnvcuvidv4l2.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvcuvidv4l2.so")
		mappings+=("prebuilts/${ARCH}/libnvds_meta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvds_meta.so")
		mappings+=("prebuilts/${ARCH}/libnvdsgst_helper.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvdsgst_helper.so")
		mappings+=("prebuilts/${ARCH}/libnvdsgst_meta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/libnvdsgst_meta.so")
	fi

	if [[ ${ARCH} = aarch64 ]]; then
		mappings+=("prebuilts/${ARCH}/sbsa/libnvbuf_fdmap.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvbuf_fdmap.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/sbsa/libnvv4l2.so=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvv4l2.so")
		mappings+=("prebuilts/${ARCH}/sbsa/libv4l2_nvcuvidvideocodec.so=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libv4l2_nvcuvidvideocodec.so")
		mappings+=("prebuilts/${ARCH}/sbsa/libnvbufsurface.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvbufsurface.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/sbsa/libnvdsbufferpool.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvdsbufferpool.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/sbsa/libnvbufsurftransform.so.1.0.0=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvbufsurftransform.so.1.0.0")
		mappings+=("prebuilts/${ARCH}/sbsa/libgstnvexifmeta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libgstnvexifmeta.so")
		mappings+=("prebuilts/${ARCH}/sbsa/libgstnvjpeg.so=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libgstnvjpeg.so")
		mappings+=("prebuilts/${ARCH}/sbsa/libnvjpeg.so.13=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvjpeg.so.13")
		mappings+=("prebuilts/${ARCH}/sbsa/libnvmm_jpeg.so=${PACKAGE_DIR}/prebuilts/${ARCH}/sbsa/libnvmm_jpeg.so")
		mappings+=("prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideo4linux2.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideo4linux2.so")
	fi

	if [[ ${ARCH} = x86_64 ]]; then
		mappings+=("prebuilts/${ARCH}/deepstream/libnvv4l2.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libv4l2.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvdsbufferpool.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvdsbufferpool.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvdsgst_helper.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvdsgst_helper.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvdsgst_meta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvdsgst_meta.so")
		mappings+=("prebuilts/${ARCH}/deepstream/libnvds_meta.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/libnvds_meta.so")
		mappings+=("prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideoconvert.so=${PACKAGE_DIR}/prebuilts/${ARCH}/deepstream/gst-plugins/libgstnvvideoconvert.so")
	fi

	if [[ -z ${PROJECT} ]]; then
		mappings+=("configs/rtsp_streams.json=${PACKAGE_DIR}/configs/rtsp_streams.json")
	fi
	mappings+=("webroot/*=${PACKAGE_DIR}/webroot/")
}

create_rel_package() {
	mappings=()
	PACKAGE_DIR="vst_${RELEASE_VERSION}_${ARCH}"
	mappings+=("tools/deploy_vst.sh=${PACKAGE_DIR}/deploy_vst.sh")
	mappings+=("LICENSE.3rdparty=${PACKAGE_DIR}/LICENSE.3rdparty")
	mappings+=("LICENSE_libnvjpeg=${PACKAGE_DIR}/LICENSE_libnvjpeg")
	echo "vst_package: ${vst_package}"
	mappings+=("${OUT_REL}/${vst_package}"=${PACKAGE_DIR}/${vst_package})
	create_package "${PACKAGE_DIR}.tbz2"
}

create_dev_package() {
	mappings=()
	PACKAGE_DIR="vst_release"
	mappings+=("launch_vst=${PACKAGE_DIR}/launch_vst")
	mappings+=("LICENSE.3rdparty=${PACKAGE_DIR}/LICENSE.3rdparty")
	vst_package="${PACKAGE_DIR}.tbz2"
	add_common_files
	create_package "${vst_package}"
}

create_test_package() {
	mappings=()
	PACKAGE_DIR="vst_tests"
	mappings+=("vst_test=${PACKAGE_DIR}/vst_test")
	mappings+=("prebuilts/${ARCH}/test_control.so=${PACKAGE_DIR}/prebuilts/${ARCH}/test_control.so")
	mappings+=("prebuilts/${ARCH}/test_discovery.so=${PACKAGE_DIR}/prebuilts/${ARCH}/test_discovery.so")
	add_common_files
	create_package "${PACKAGE_DIR}.tbz2"
}

if [[ -n "${PACKAGE_TESTS}" ]]; then
	echo "Creating test package.."
	create_test_package
else
	echo "Creating release package.. RELEASE_VERSION: ${RELEASE_VERSION}"
	create_dev_package
	if [[ ! -z "${RELEASE_VERSION}" ]]; then
		echo "Create public release package..."
		create_rel_package
	fi
fi
