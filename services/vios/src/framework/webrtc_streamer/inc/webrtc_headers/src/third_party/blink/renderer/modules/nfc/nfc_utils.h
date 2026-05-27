// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_NFC_NFC_UTILS_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_NFC_NFC_UTILS_H_

#include "services/device/public/mojom/nfc.mojom-blink-forward.h"
#include "third_party/blink/renderer/platform/wtf/text/wtf_string.h"

namespace blink {

class DOMException;

DOMException* NDEFErrorTypeToDOMException(
    device::mojom::blink::NDEFErrorType error_type,
    const String& error_message);

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_NFC_NFC_UTILS_H_
