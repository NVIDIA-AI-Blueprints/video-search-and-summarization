// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_TEST_MOCK_P2P_QUIC_STREAM_DELEGATE_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_TEST_MOCK_P2P_QUIC_STREAM_DELEGATE_H_

#include "testing/gmock/include/gmock/gmock.h"
#include "third_party/blink/renderer/modules/peerconnection/adapters/p2p_quic_stream.h"

namespace blink {

class MockP2PQuicStreamDelegate
    : public testing::NiceMock<P2PQuicStream::Delegate> {
 public:
  // P2PQuicStream::Delegate overrides.
  MOCK_METHOD1(OnWriteDataConsumed, void(uint32_t));
  MOCK_METHOD0(OnRemoteReset, void());
  MOCK_METHOD2(OnDataReceived, void(Vector<uint8_t>, bool));
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_TEST_MOCK_P2P_QUIC_STREAM_DELEGATE_H_
