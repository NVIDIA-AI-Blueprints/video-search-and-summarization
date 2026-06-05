// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_SERIAL_SERIAL_CONNECTION_EVENT_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_SERIAL_SERIAL_CONNECTION_EVENT_H_

#include "third_party/blink/renderer/modules/event_modules.h"
#include "third_party/blink/renderer/platform/heap/handle.h"

namespace blink {

class SerialConnectionEventInit;
class SerialPort;

class SerialConnectionEvent final : public Event {
  DEFINE_WRAPPERTYPEINFO();

 public:
  static SerialConnectionEvent* Create(const AtomicString& type,
                                       const SerialConnectionEventInit*);
  static SerialConnectionEvent* Create(const AtomicString& type, SerialPort*);

  SerialConnectionEvent(const AtomicString& type,
                        const SerialConnectionEventInit*);
  SerialConnectionEvent(const AtomicString& type, SerialPort*);

  SerialPort* port() const { return port_; }

  void Trace(Visitor*) const override;

 private:
  Member<SerialPort> port_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_SERIAL_SERIAL_CONNECTION_EVENT_H_
