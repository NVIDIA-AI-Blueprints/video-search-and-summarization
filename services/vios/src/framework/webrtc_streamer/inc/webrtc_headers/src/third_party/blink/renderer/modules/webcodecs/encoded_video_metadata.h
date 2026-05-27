// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_WEBCODECS_ENCODED_VIDEO_METADATA_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_WEBCODECS_ENCODED_VIDEO_METADATA_H_

#include "base/optional.h"
#include "base/time/time.h"

namespace blink {

struct EncodedVideoMetadata {
  bool key_frame = false;
  base::TimeDelta timestamp;
  base::Optional<base::TimeDelta> duration;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_WEBCODECS_ENCODED_VIDEO_METADATA_H_
