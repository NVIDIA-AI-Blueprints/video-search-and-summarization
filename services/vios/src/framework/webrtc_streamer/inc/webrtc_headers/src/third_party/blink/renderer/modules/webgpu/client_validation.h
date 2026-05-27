// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_WEBGPU_CLIENT_VALIDATION_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_WEBGPU_CLIENT_VALIDATION_H_

#include <dawn/webgpu.h>

// This file provides helpers for validating copy operation in WebGPU.

namespace blink {

class ExceptionState;
class GPUTextureCopyView;
class UnsignedLongEnforceRangeSequenceOrGPUExtent3DDict;

bool ValidateCopySize(
    UnsignedLongEnforceRangeSequenceOrGPUExtent3DDict& copy_size,
    ExceptionState& exception_state);
bool ValidateTextureCopyView(GPUTextureCopyView* texture_copy_view,
                             ExceptionState& exception_state);
}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_WEBGPU_CLIENT_VALIDATION_H_
