/*
 *  Copyright 2017 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef SDK_ANDROID_SRC_JNI_PC_SDP_OBSERVER_H_
#define SDK_ANDROID_SRC_JNI_PC_SDP_OBSERVER_H_

#include <jni.h>

#include <memory>

#include "api/jsep.h"
#include "api/rtc_error.h"
#include "api/set_local_description_observer_interface.h"
#include "api/set_remote_description_observer_interface.h"
#include "sdk/android/native_api/jni/scoped_java_ref.h"
#include "sdk/media_constraints.h"

namespace webrtc {
namespace jni {

class CreateSdpObserverJni : public CreateSessionDescriptionObserver {
 public:
  CreateSdpObserverJni(JNIEnv* env,
                       const JavaRef<jobject>& j_observer,
                       std::unique_ptr<MediaConstraints> constraints);
  ~CreateSdpObserverJni() override;

  MediaConstraints* constraints() { return constraints_.get(); }

  void OnSuccess(SessionDescriptionInterface* desc) override;
  void OnFailure(RTCError error) override;

 private:
  const ScopedJavaGlobalRef<jobject> j_observer_global_;
  std::unique_ptr<MediaConstraints> constraints_;
};

class SetLocalSdpObserverJni : public SetLocalDescriptionObserverInterface {
 public:
  SetLocalSdpObserverJni(JNIEnv* env, const JavaRef<jobject>& j_observer);

  ~SetLocalSdpObserverJni() override = default;

  virtual void OnSetLocalDescriptionComplete(RTCError error) override;

 private:
  const ScopedJavaGlobalRef<jobject> j_observer_global_;
};

class SetRemoteSdpObserverJni : public SetRemoteDescriptionObserverInterface {
 public:
  SetRemoteSdpObserverJni(JNIEnv* env, const JavaRef<jobject>& j_observer);

  ~SetRemoteSdpObserverJni() override = default;

  virtual void OnSetRemoteDescriptionComplete(RTCError error) override;

 private:
  const ScopedJavaGlobalRef<jobject> j_observer_global_;
};

}  // namespace jni
}  // namespace webrtc

#endif  // SDK_ANDROID_SRC_JNI_PC_SDP_OBSERVER_H_
