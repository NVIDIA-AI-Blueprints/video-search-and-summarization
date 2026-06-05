#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_INDEXEDDB_IDB_NAME_AND_VERSION_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_INDEXEDDB_IDB_NAME_AND_VERSION_H_

#include "third_party/blink/renderer/platform/wtf/text/wtf_string.h"

namespace blink {

struct IDBNameAndVersion {
  enum { kNoVersion = -1 };
  String name;
  int64_t version;

  IDBNameAndVersion() : version(kNoVersion) {}
  IDBNameAndVersion(String name, int64_t version)
      : name(name), version(version) {}
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_INDEXEDDB_IDB_NAME_AND_VERSION_H_
