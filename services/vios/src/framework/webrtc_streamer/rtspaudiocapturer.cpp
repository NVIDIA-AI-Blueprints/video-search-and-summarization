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
** rtspaudiocapturer.cpp
**
** -------------------------------------------------------------------------*/


#ifdef HAVE_LIVE555


#include "rtc_base/logging.h"

#include "rtspaudiocapturer.h"


RTSPAudioSource::RTSPAudioSource(rtc::scoped_refptr<webrtc::AudioDecoderFactory> audioDecoderFactory, const std::string & uri, const std::map<std::string,std::string, std::less<>> & opts) 
				: LiveAudioSource(audioDecoderFactory, uri, opts, false) {
}

RTSPAudioSource::~RTSPAudioSource()  { 
}


#endif
