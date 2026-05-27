// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_WEB_WEB_EXTERNAL_WIDGET_H_
#define THIRD_PARTY_BLINK_PUBLIC_WEB_WEB_EXTERNAL_WIDGET_H_

#include "cc/layers/layer.h"
#include "third_party/blink/public/mojom/page/widget.mojom-shared.h"
#include "third_party/blink/public/platform/cross_variant_mojo_util.h"
#include "third_party/blink/public/web/web_external_widget_client.h"
#include "third_party/blink/public/web/web_widget.h"

namespace blink {

// This is a type of Widget which is partially implemented outside of blink,
// such as fullscreen pepper widgets. This interface provides methods for the
// external implementation to access common Widget behaviour implemented inside
// blink, in addition to the WebWidget methods. The blink Widget uses
// WebExternalWidgetClient to communicate back to the external implementation.
class WebExternalWidget : public WebWidget {
 public:
  // Create a new concrete instance of this class.
  // |client| should be non-null.
  // |debug_url| provides the return value for WebWidget::GetURLForDebugTrace.
  BLINK_EXPORT static std::unique_ptr<WebExternalWidget> Create(
      WebExternalWidgetClient* client,
      const WebURL& debug_url,
      CrossVariantMojoAssociatedRemote<mojom::WidgetHostInterfaceBase>
          widget_host,
      CrossVariantMojoAssociatedReceiver<mojom::WidgetInterfaceBase> widget);

  virtual ~WebExternalWidget() = default;

  // Provides an externally-created Layer to display as the widget's content, or
  // a null pointer to remove any existing Layer which will cause the widget to
  // display nothing.
  virtual void SetRootLayer(scoped_refptr<cc::Layer>) = 0;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_WEB_WEB_EXTERNAL_WIDGET_H_
