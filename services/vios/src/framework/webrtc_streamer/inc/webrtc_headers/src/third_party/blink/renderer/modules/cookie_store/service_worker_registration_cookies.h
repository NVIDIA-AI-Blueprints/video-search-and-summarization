// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_COOKIE_STORE_SERVICE_WORKER_REGISTRATION_COOKIES_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_COOKIE_STORE_SERVICE_WORKER_REGISTRATION_COOKIES_H_

#include "third_party/blink/renderer/platform/wtf/allocator/allocator.h"

namespace blink {

class CookieStoreManager;
class ServiceWorkerRegistration;

class ServiceWorkerRegistrationCookies final {
  STATIC_ONLY(ServiceWorkerRegistrationCookies);

 public:
  static CookieStoreManager* cookies(ServiceWorkerRegistration& registration);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_COOKIE_STORE_SERVICE_WORKER_REGISTRATION_COOKIES_H_
