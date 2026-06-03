// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_CANVAS_CANVAS2D_BLINK_IDENTIFIABILITY_DIGEST_HELPERS_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_CANVAS_CANVAS2D_BLINK_IDENTIFIABILITY_DIGEST_HELPERS_H_

#include "third_party/blink/renderer/platform/wtf/forward.h"

// Provide additional overloads of IdentifiabilityDigestHelper() for
// blink-internal types.
//
// *NOTE*: This header extends the functionality of
// third_party/blink/public/common/privacy_budget/identifiability_metrics.h
// -- it must be included before that header.

// TODO(crbug.com/973801): Consider moving to another directory.

namespace blink {

uint64_t IdentifiabilityDigestHelper(const String&);

// For sensitive strings, this function narrows the hash width to 16 bits. This
// 16-bit value can be combined with other values using the parameter-pack
// IdentifiabilityDigestHelper() overload.
uint16_t IdentifiabilitySensitiveString(const String&);

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_CANVAS_CANVAS2D_BLINK_IDENTIFIABILITY_DIGEST_HELPERS_H_
