/*
 *  Copyright (c) 2018 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef SDK_ANDROID_NATIVE_API_AUDIO_DEVICE_MODULE_AUDIO_DEVICE_ANDROID_H_
#define SDK_ANDROID_NATIVE_API_AUDIO_DEVICE_MODULE_AUDIO_DEVICE_ANDROID_H_

#include <jni.h>

#include "api/audio/audio_device.h"
#include "api/environment/environment.h"
#include "api/scoped_refptr.h"

namespace webrtc {

scoped_refptr<AudioDeviceModule> CreateJavaAudioDeviceModule(
    JNIEnv* env,
    const Environment& webrtc_env,
    jobject application_context);

scoped_refptr<AudioDeviceModule> CreateAndroidAudioDeviceModule(
    const Environment& webrtc_env,
    AudioDeviceModule::AudioLayer audio_layer);

}  // namespace webrtc

#endif  // SDK_ANDROID_NATIVE_API_AUDIO_DEVICE_MODULE_AUDIO_DEVICE_ANDROID_H_
