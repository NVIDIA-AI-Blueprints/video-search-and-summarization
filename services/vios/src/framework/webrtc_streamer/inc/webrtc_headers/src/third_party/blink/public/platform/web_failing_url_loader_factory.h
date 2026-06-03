// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_FAILING_URL_LOADER_FACTORY_H_
#define THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_FAILING_URL_LOADER_FACTORY_H_

#include "third_party/blink/public/platform/web_common.h"
#include "third_party/blink/public/platform/web_url_loader_factory.h"

namespace blink {

// A WebURLLoaderFactory implementation that creates WebURLLoaders that
// always fail loading.
class BLINK_PLATFORM_EXPORT WebFailingURLLoaderFactory final
    : public WebURLLoaderFactory {
 public:
  WebFailingURLLoaderFactory() = default;
  ~WebFailingURLLoaderFactory() override = default;

  std::unique_ptr<WebURLLoader> CreateURLLoader(
      const WebURLRequest&,
      std::unique_ptr<scheduler::WebResourceLoadingTaskRunnerHandle>) override;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_PLATFORM_WEB_FAILING_URL_LOADER_FACTORY_H_
