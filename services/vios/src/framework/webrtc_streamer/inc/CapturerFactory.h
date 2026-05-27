/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/* ---------------------------------------------------------------------------
** This software is in the public domain, furnished "as is", without technical
** support, and with no warranty, express or implied, as to its usefulness for
** any purpose.
**
** CapturerFactory.h
**
** -------------------------------------------------------------------------*/

#pragma once

#include <regex>

#include "VcmCapturer.h"
#include "logger.h"
#include "stats.h"

#ifdef HAVE_LIVE555
#include "rtspvideocapturer.h"
#include "nvgstvideocapturer.h"
#include "nvgstaudiocapturer.h"
#include "rtspaudiocapturer.h"
#include "fileaudiocapturer.h"
#endif

#include "nvgstudpvideocapturer.h"
#include "nvgstudpaudiocapturer.h"
#include "rtc_base/ref_counted_object.h"

#ifdef USE_X11
#include "screencapturer.h"
#endif

#include "pc/video_track_source.h"

template<class T>
class TrackSource : public webrtc::VideoTrackSource {
public:
	static rtc::scoped_refptr<TrackSource> Create(const std::string & videourl, 
		const std::map<std::string, std::string, std::less<>> & opts) {
		std::unique_ptr<T> capturer = absl::WrapUnique(T::Create(videourl, opts));
		if (!capturer) {
			return nullptr;
		}
		return rtc::make_ref_counted<TrackSource>(std::move(capturer));
	}

	void getDecoderStatsTrackSource(LatencyStats& stats) {
		capturer_->getDecoderStats(stats);
		return;
	}

	VmsErrorCode controlStreamTrackSource(const std::string& action, std::string seek_value="")	{
		return capturer_->controlStreamCapturer(action, seek_value);
	}
	void switchStreamTrackSource(std::string url, const std::map<std::string, std::string, std::less<>> &opts)	{
		capturer_->switchStreamCapturer(url, opts);
		return;
	}

	void streamSettingTrackSource(const std::unordered_map<std::string, std::string> &opts)	{
		capturer_->streamSettingCapturer(opts);
		return;
	}

	void startPlayback()	
	{
		capturer_->startPlayback();
		return;
	}

	gint64 getPositionTrackSource()	{
		return capturer_->getPositionCapturer();
	}
	string getStreamState()
	{
		return capturer_->getStreamState();
	}
	Json::Value getOverlayStatus()
	{
#if 0
		return capturer_->getOverlayStatus();
#endif
		return Json::nullValue;
	}
	bool isStreamError()
	{
		return capturer_->isStreamError();
	}
	uint64_t getLastTS()	{
		return capturer_->getLastTS();
	}
	int64_t getFileStartTime()	{
		return capturer_->getFileStartTime();
	}
	uint32_t getDurationStream()	{
		return capturer_->getDurationStream();
	}
	string getSensorId()	{
		return capturer_->getSensorId();
	}
	string getSensorName()	{
		return capturer_->getSensorName();
	}

protected:
	explicit TrackSource(std::unique_ptr<T> capturer)
		: webrtc::VideoTrackSource(/*remote=*/false), capturer_(std::move(capturer)) {}

private:
	rtc::VideoSourceInterface<webrtc::VideoFrame>* source() override {
		return capturer_.get();
	}
	std::unique_ptr<T> capturer_;
};

class CapturerFactory {
	public:

	static const std::list<std::string> GetVideoCaptureDeviceList(const std::regex & publishFilter)
	{
		std::list<std::string> videoDeviceList;

		if (std::regex_match("videocap://",publishFilter)) {
			std::unique_ptr<webrtc::VideoCaptureModule::DeviceInfo> info(webrtc::VideoCaptureFactory::CreateDeviceInfo());
			if (info)
			{
				int num_videoDevices = info->NumberOfDevices();
				LOG(verbose) << "nb video devices:" << num_videoDevices;
				for (int i = 0; i < num_videoDevices; ++i)
				{
					const uint32_t kSize = 256;
					char name[kSize] = {0};
					char id[kSize] = {0};
					if (info->GetDeviceName(i, name, kSize, id, kSize) != -1)
					{
						LOG(verbose) << "video device name:" << name << " id:" << id;
						std::string devname;
						auto it = std::find(videoDeviceList.begin(), videoDeviceList.end(), name);
						if (it == videoDeviceList.end()) {
							devname = name;
						} else {
							devname = "videocap://";
							devname += std::to_string(i);
						}
						videoDeviceList.push_back(devname);
					}
				}
			}
		}

		return videoDeviceList;
	}
	
	static const std::list<std::string> GetVideoSourceList(const std::regex & publishFilter) {
	
		std::list<std::string> videoList;
		
#ifdef USE_X11
		if (std::regex_match("window://",publishFilter)) {
			std::unique_ptr<webrtc::DesktopCapturer> capturer = webrtc::DesktopCapturer::CreateWindowCapturer(webrtc::DesktopCaptureOptions::CreateDefault());	
			if (capturer) {
				webrtc::DesktopCapturer::SourceList sourceList;
				if (capturer->GetSourceList(&sourceList)) {
					for (auto source : sourceList) {
						std::ostringstream os;
						os << "window://" << source.title;
						videoList.push_back(os.str());
					}
				}
			}
		}
		if (std::regex_match("screen://",publishFilter)) {
			std::unique_ptr<webrtc::DesktopCapturer> capturer = webrtc::DesktopCapturer::CreateScreenCapturer(webrtc::DesktopCaptureOptions::CreateDefault());		
			if (capturer) {
				webrtc::DesktopCapturer::SourceList sourceList;
				if (capturer->GetSourceList(&sourceList)) {
					for (auto source : sourceList) {
						std::ostringstream os;
						os << "screen://" << source.id;
						videoList.push_back(os.str());
					}
				}
			}
		}
#endif		
		return videoList;
	}

	static rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> CreateVideoSource(const std::string & videourl, 
				const std::map<std::string,std::string, std::less<>> & opts, const std::regex & publishFilter, 
				rtc::scoped_refptr<webrtc::PeerConnectionFactoryInterface> peer_connection_factory)
	{
		rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> videoSource;
		if ( ((videourl.find("rtsp://") == 0) && (std::regex_match("rtsp://", publishFilter)))
				||   ((videourl.find("file://") == 0) && (std::regex_match("file://", publishFilter)))
				||   ((videourl.find("s3://") == 0) && (std::regex_match("file://", publishFilter))) )
		{
			if (opts.at("capture_type") == "rtsp")
			{
				videoSource = TrackSource<RTSPVideoCapturer>::Create(videourl, opts);
			}
			else
			{
				videoSource = TrackSource<NvGstVideoCapturer>::Create(videourl, opts);
			}
		}
		else if (videourl.find("udp:") == 0)
		{
			if (opts.at("capture_type") == "udp")
			{
				videoSource = TrackSource<NvGstUDPVideoCapturer>::Create(videourl, opts);
			}
		}
#if 0
		else if ((videourl.find("screen://") == 0) && (std::regex_match("screen://", publishFilter)))
		{
#ifdef USE_X11
			videoSource = TrackSource<ScreenCapturer>::Create(videourl, opts);
#endif	
		}
		else if ((videourl.find("window://") == 0) && (std::regex_match("window://", publishFilter)))
		{
#ifdef USE_X11
			videoSource = TrackSource<WindowCapturer>::Create(videourl, opts);
#endif	
		}
		else if (std::regex_match("videocap://",publishFilter)) {
			videoSource = TrackSource<VcmCapturer>::Create(videourl, opts);
		}
#endif
		return videoSource;
	}

	static rtc::scoped_refptr<webrtc::AudioSourceInterface> CreateAudioSource(const std::string & audiourl, 
							const std::map<std::string,std::string, std::less<>> & opts, 
							const std::regex & publishFilter, 
							rtc::scoped_refptr<webrtc::PeerConnectionFactoryInterface> peer_connection_factory,
							rtc::scoped_refptr<webrtc::AudioDecoderFactory> audioDecoderfactory,
							rtc::scoped_refptr<webrtc::AudioDeviceModule>   audioDeviceModule) {
		rtc::scoped_refptr<webrtc::AudioSourceInterface> audioSource;

		if ( (audiourl.find("rtsp://") == 0) && (std::regex_match("rtsp://",publishFilter)) )
		{
	#ifdef HAVE_LIVE555
			audioDeviceModule->Terminate();
			audioSource = NvGstAudioCapturer::Create(audiourl, opts);
	#endif
		}
		else if (audiourl.find("udp:") == 0)
		{
			if (opts.at("capture_type") == "udp")
			{
				audioSource = NvGstUDPAudioCapturer::Create(audiourl, opts);
			}
		}
		else if ( ((audiourl.find("file://") == 0) || (audiourl.find("s3://") == 0)) && (std::regex_match("file://",publishFilter)) )
		{
	#ifdef HAVE_LIVE555_
			audioDeviceModule->Terminate();
			audioSource = FileAudioSource::Create(audioDecoderfactory, audiourl, opts);
	#endif
		}
		else if (std::regex_match("audiocap://",publishFilter)) 
		{
			audioDeviceModule->Init();
			int16_t num_audioDevices = audioDeviceModule->RecordingDevices();
			int16_t idx_audioDevice = -1;
			for (int16_t i = 0; i < num_audioDevices; ++i)
			{
				char name[webrtc::kAdmMaxDeviceNameSize] = {0};
				char id[webrtc::kAdmMaxGuidSize] = {0};
				if (audioDeviceModule->RecordingDeviceName(i, name, id) != -1)
				{
					if (audiourl == name)
					{
						idx_audioDevice = i;
						break;
					}
				}
			}
			LOG(error) << "audiourl:" << audiourl << " idx_audioDevice:" << idx_audioDevice;
			if ( (idx_audioDevice >= 0) && (idx_audioDevice < num_audioDevices) )
			{
				audioDeviceModule->SetRecordingDevice(idx_audioDevice);
				cricket::AudioOptions opt;
				audioSource = peer_connection_factory->CreateAudioSource(opt);
			}
		}
		return audioSource;
	}
};
