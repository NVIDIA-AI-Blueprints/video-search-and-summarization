// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_CONTACTS_PICKER_NAVIGATOR_CONTACTS_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_CONTACTS_PICKER_NAVIGATOR_CONTACTS_H_

#include "third_party/blink/renderer/modules/contacts_picker/contacts_manager.h"
#include "third_party/blink/renderer/platform/heap/garbage_collected.h"
#include "third_party/blink/renderer/platform/supplementable.h"

namespace blink {

class Navigator;

// Represents a supplement to Navigator, implementing the Contacts Selector.
class NavigatorContacts final : public GarbageCollected<NavigatorContacts>,
                                public Supplement<Navigator> {
  USING_GARBAGE_COLLECTED_MIXIN(NavigatorContacts);

 public:
  static const char kSupplementName[];

  static NavigatorContacts& From(Navigator& navigator);

  // Web Exposed attribute defined in the IDL file.
  static ContactsManager* contacts(Navigator& navigator);
  ContactsManager* contacts();

  explicit NavigatorContacts(Navigator& navigator);

  void Trace(Visitor* visitor) const override;

 private:
  Member<ContactsManager> contacts_manager_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_CONTACTS_PICKER_NAVIGATOR_CONTACTS_H_
