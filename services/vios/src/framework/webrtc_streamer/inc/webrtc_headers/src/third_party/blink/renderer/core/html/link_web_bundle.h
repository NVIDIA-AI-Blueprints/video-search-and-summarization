// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_HTML_LINK_WEB_BUNDLE_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_HTML_LINK_WEB_BUNDLE_H_

#include "third_party/blink/renderer/core/html/link_resource.h"

namespace blink {

// LinkWebBundle is used in the Subresource loading with Web Bundles feature.
// See crbug.com/1082020 for details.
// A <link rel="webbundle" ...> element creates LinkWebBundle.
class LinkWebBundle final : public LinkResource {
 public:
  explicit LinkWebBundle(HTMLLinkElement* owner);
  ~LinkWebBundle() override = default;

  void Process() override;
  LinkResourceType GetType() const override;
  bool HasLoaded() const override;
  void OwnerRemoved() override;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_HTML_LINK_WEB_BUNDLE_H_
