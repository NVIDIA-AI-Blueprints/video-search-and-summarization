/*
 *  Copyright 2012 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef PC_DATA_CHANNEL_H_
#define PC_DATA_CHANNEL_H_

#include <deque>
#include <memory>
#include <set>
#include <string>

#include "api/data_channel_interface.h"
#include "api/priority.h"
#include "api/proxy.h"
#include "api/scoped_refptr.h"
#include "media/base/media_channel.h"
#include "pc/channel.h"
#include "rtc_base/async_invoker.h"
#include "rtc_base/third_party/sigslot/sigslot.h"

namespace webrtc {

class DataChannel;

// TODO(deadbeef): Once RTP data channels go away, get rid of this and have
// DataChannel depend on SctpTransportInternal (pure virtual SctpTransport
// interface) instead.
class DataChannelProviderInterface {
 public:
  // Sends the data to the transport.
  virtual bool SendData(const cricket::SendDataParams& params,
                        const rtc::CopyOnWriteBuffer& payload,
                        cricket::SendDataResult* result) = 0;
  // Connects to the transport signals.
  virtual bool ConnectDataChannel(DataChannel* data_channel) = 0;
  // Disconnects from the transport signals.
  virtual void DisconnectDataChannel(DataChannel* data_channel) = 0;
  // Adds the data channel SID to the transport for SCTP.
  virtual void AddSctpDataStream(int sid) = 0;
  // Begins the closing procedure by sending an outgoing stream reset. Still
  // need to wait for callbacks to tell when this completes.
  virtual void RemoveSctpDataStream(int sid) = 0;
  // Returns true if the transport channel is ready to send data.
  virtual bool ReadyToSendData() const = 0;

 protected:
  virtual ~DataChannelProviderInterface() {}
};

// TODO(tommi): Change to not inherit from DataChannelInit but to have it as
// a const member. Block access to the 'id' member since it cannot be const.
struct InternalDataChannelInit : public DataChannelInit {
  enum OpenHandshakeRole { kOpener, kAcker, kNone };
  // The default role is kOpener because the default |negotiated| is false.
  InternalDataChannelInit() : open_handshake_role(kOpener) {}
  explicit InternalDataChannelInit(const DataChannelInit& base);
  OpenHandshakeRole open_handshake_role;
};

// Helper class to allocate unique IDs for SCTP DataChannels
class SctpSidAllocator {
 public:
  // Gets the first unused odd/even id based on the DTLS role. If |role| is
  // SSL_CLIENT, the allocated id starts from 0 and takes even numbers;
  // otherwise, the id starts from 1 and takes odd numbers.
  // Returns false if no ID can be allocated.
  bool AllocateSid(rtc::SSLRole role, int* sid);

  // Attempts to reserve a specific sid. Returns false if it's unavailable.
  bool ReserveSid(int sid);

  // Indicates that |sid| isn't in use any more, and is thus available again.
  void ReleaseSid(int sid);

 private:
  // Checks if |sid| is available to be assigned to a new SCTP data channel.
  bool IsSidAvailable(int sid) const;

  std::set<int> used_sids_;
};

// DataChannel is an implementation of the DataChannelInterface based on
// libjingle's data engine. It provides an implementation of unreliable or
// reliabledata channels. Currently this class is specifically designed to use
// both RtpDataChannel and SctpTransport.

// DataChannel states:
// kConnecting: The channel has been created the transport might not yet be
//              ready.
// kOpen: The channel have a local SSRC set by a call to UpdateSendSsrc
//        and a remote SSRC set by call to UpdateReceiveSsrc and the transport
//        has been writable once.
// kClosing: DataChannelInterface::Close has been called or UpdateReceiveSsrc
//           has been called with SSRC==0
// kClosed: Both UpdateReceiveSsrc and UpdateSendSsrc has been called with
//          SSRC==0.
//
// How the closing procedure works for SCTP:
// 1. Alice calls Close(), state changes to kClosing.
// 2. Alice finishes sending any queued data.
// 3. Alice calls RemoveSctpDataStream, sends outgoing stream reset.
// 4. Bob receives incoming stream reset; OnClosingProcedureStartedRemotely
//    called.
// 5. Bob sends outgoing stream reset. 6. Alice receives incoming reset,
//    Bob receives acknowledgement. Both receive OnClosingProcedureComplete
//    callback and transition to kClosed.
class DataChannel : public DataChannelInterface, public sigslot::has_slots<> {
 public:
  struct Stats {
    int internal_id;
    int id;
    std::string label;
    std::string protocol;
    DataState state;
    uint32_t messages_sent;
    uint32_t messages_received;
    uint64_t bytes_sent;
    uint64_t bytes_received;
  };

  static rtc::scoped_refptr<DataChannel> Create(
      DataChannelProviderInterface* provider,
      cricket::DataChannelType dct,
      const std::string& label,
      const InternalDataChannelInit& config,
      rtc::Thread* signaling_thread,
      rtc::Thread* network_thread);

  static bool IsSctpLike(cricket::DataChannelType type);

  void RegisterObserver(DataChannelObserver* observer) override;
  void UnregisterObserver() override;

  std::string label() const override { return label_; }
  bool reliable() const override;
  bool ordered() const override { return config_.ordered; }
  // Backwards compatible accessors
  uint16_t maxRetransmitTime() const override {
    return config_.maxRetransmitTime ? *config_.maxRetransmitTime
                                     : static_cast<uint16_t>(-1);
  }
  uint16_t maxRetransmits() const override {
    return config_.maxRetransmits ? *config_.maxRetransmits
                                  : static_cast<uint16_t>(-1);
  }
  absl::optional<int> maxPacketLifeTime() const override {
    return config_.maxRetransmitTime;
  }
  absl::optional<int> maxRetransmitsOpt() const override {
    return config_.maxRetransmits;
  }
  std::string protocol() const override { return config_.protocol; }
  bool negotiated() const override { return config_.negotiated; }
  int id() const override { return config_.id; }
  Priority priority() const override {
    return config_.priority ? *config_.priority : Priority::kLow;
  }

  virtual int internal_id() const { return internal_id_; }

  uint64_t buffered_amount() const override;
  void Close() override;
  DataState state() const override;
  RTCError error() const override;
  uint32_t messages_sent() const override;
  uint64_t bytes_sent() const override;
  uint32_t messages_received() const override;
  uint64_t bytes_received() const override;
  bool Send(const DataBuffer& buffer) override;

  // Close immediately, ignoring any queued data or closing procedure.
  // This is called for RTP data channels when SDP indicates a channel should
  // be removed, or SCTP data channels when the underlying SctpTransport is
  // being destroyed.
  // It is also called by the PeerConnection if SCTP ID assignment fails.
  void CloseAbruptlyWithError(RTCError error);
  // Specializations of CloseAbruptlyWithError
  void CloseAbruptlyWithDataChannelFailure(const std::string& message);
  void CloseAbruptlyWithSctpCauseCode(const std::string& message,
                                      uint16_t cause_code);

  // Called when the channel's ready to use.  That can happen when the
  // underlying DataMediaChannel becomes ready, or when this channel is a new
  // stream on an existing DataMediaChannel, and we've finished negotiation.
  void OnChannelReady(bool writable);

  // Slots for provider to connect signals to.
  void OnDataReceived(const cricket::ReceiveDataParams& params,
                      const rtc::CopyOnWriteBuffer& payload);

  /********************************************
   * The following methods are for SCTP only. *
   ********************************************/

  // Sets the SCTP sid and adds to transport layer if not set yet. Should only
  // be called once.
  void SetSctpSid(int sid);
  // The remote side started the closing procedure by resetting its outgoing
  // stream (our incoming stream). Sets state to kClosing.
  void OnClosingProcedureStartedRemotely(int sid);
  // The closing procedure is complete; both incoming and outgoing stream
  // resets are done and the channel can transition to kClosed. Called
  // asynchronously after RemoveSctpDataStream.
  void OnClosingProcedureComplete(int sid);
  // Called when the transport channel is created.
  // Only needs to be called for SCTP data channels.
  void OnTransportChannelCreated();
  // Called when the transport channel is unusable.
  // This method makes sure the DataChannel is disconnected and changes state
  // to kClosed.
  void OnTransportChannelClosed();

  Stats GetStats() const;

  /*******************************************
   * The following methods are for RTP only. *
   *******************************************/

  // The remote peer requested that this channel should be closed.
  void RemotePeerRequestClose();
  // Set the SSRC this channel should use to send data on the
  // underlying data engine. |send_ssrc| == 0 means that the channel is no
  // longer part of the session negotiation.
  void SetSendSsrc(uint32_t send_ssrc);
  // Set the SSRC this channel should use to receive data from the
  // underlying data engine.
  void SetReceiveSsrc(uint32_t receive_ssrc);

  cricket::DataChannelType data_channel_type() const {
    return data_channel_type_;
  }

  // Emitted when state transitions to kOpen.
  sigslot::signal1<DataChannel*> SignalOpened;
  // Emitted when state transitions to kClosed.
  // In the case of SCTP channels, this signal can be used to tell when the
  // channel's sid is free.
  sigslot::signal1<DataChannel*> SignalClosed;

  // Reset the allocator for internal ID values for testing, so that
  // the internal IDs generated are predictable. Test only.
  static void ResetInternalIdAllocatorForTesting(int new_value);

 protected:
  DataChannel(const InternalDataChannelInit& config,
              DataChannelProviderInterface* client,
              cricket::DataChannelType dct,
              const std::string& label,
              rtc::Thread* signaling_thread,
              rtc::Thread* network_thread);
  ~DataChannel() override;

 private:
  // A packet queue which tracks the total queued bytes. Queued packets are
  // owned by this class.
  class PacketQueue final {
   public:
    size_t byte_count() const { return byte_count_; }

    bool Empty() const;

    std::unique_ptr<DataBuffer> PopFront();

    void PushFront(std::unique_ptr<DataBuffer> packet);
    void PushBack(std::unique_ptr<DataBuffer> packet);

    void Clear();

    void Swap(PacketQueue* other);

   private:
    std::deque<std::unique_ptr<DataBuffer>> packets_;
    size_t byte_count_ = 0;
  };

  // The OPEN(_ACK) signaling state.
  enum HandshakeState {
    kHandshakeInit,
    kHandshakeShouldSendOpen,
    kHandshakeShouldSendAck,
    kHandshakeWaitingForAck,
    kHandshakeReady
  };

  bool Init();
  void UpdateState();
  void SetState(DataState state);
  void DisconnectFromProvider();

  void DeliverQueuedReceivedData();

  void SendQueuedDataMessages();
  bool SendDataMessage(const DataBuffer& buffer, bool queue_if_blocked);
  bool QueueSendDataMessage(const DataBuffer& buffer);

  void SendQueuedControlMessages();
  void QueueControlMessage(const rtc::CopyOnWriteBuffer& buffer);
  bool SendControlMessage(const rtc::CopyOnWriteBuffer& buffer);

  rtc::Thread* const signaling_thread_;
  rtc::Thread* const network_thread_;
  const int internal_id_;
  const std::string label_;
  const InternalDataChannelInit config_;
  DataChannelObserver* observer_ RTC_GUARDED_BY(signaling_thread_);
  DataState state_ RTC_GUARDED_BY(signaling_thread_);
  RTCError error_ RTC_GUARDED_BY(signaling_thread_);
  uint32_t messages_sent_ RTC_GUARDED_BY(signaling_thread_);
  uint64_t bytes_sent_ RTC_GUARDED_BY(signaling_thread_);
  uint32_t messages_received_ RTC_GUARDED_BY(signaling_thread_);
  uint64_t bytes_received_ RTC_GUARDED_BY(signaling_thread_);
  // Number of bytes of data that have been queued using Send(). Increased
  // before each transport send and decreased after each successful send.
  uint64_t buffered_amount_ RTC_GUARDED_BY(signaling_thread_);
  const cricket::DataChannelType data_channel_type_;
  DataChannelProviderInterface* const provider_;
  HandshakeState handshake_state_ RTC_GUARDED_BY(signaling_thread_);
  bool connected_to_provider_ RTC_GUARDED_BY(signaling_thread_);
  bool send_ssrc_set_ RTC_GUARDED_BY(signaling_thread_);
  bool receive_ssrc_set_ RTC_GUARDED_BY(signaling_thread_);
  bool writable_ RTC_GUARDED_BY(signaling_thread_);
  // Did we already start the graceful SCTP closing procedure?
  bool started_closing_procedure_ RTC_GUARDED_BY(signaling_thread_) = false;
  uint32_t send_ssrc_ RTC_GUARDED_BY(signaling_thread_);
  uint32_t receive_ssrc_ RTC_GUARDED_BY(signaling_thread_);
  // Control messages that always have to get sent out before any queued
  // data.
  PacketQueue queued_control_data_ RTC_GUARDED_BY(signaling_thread_);
  PacketQueue queued_received_data_ RTC_GUARDED_BY(signaling_thread_);
  PacketQueue queued_send_data_ RTC_GUARDED_BY(signaling_thread_);
  rtc::AsyncInvoker invoker_ RTC_GUARDED_BY(signaling_thread_);
};

// Define proxy for DataChannelInterface.
BEGIN_SIGNALING_PROXY_MAP(DataChannel)
PROXY_SIGNALING_THREAD_DESTRUCTOR()
PROXY_METHOD1(void, RegisterObserver, DataChannelObserver*)
PROXY_METHOD0(void, UnregisterObserver)
BYPASS_PROXY_CONSTMETHOD0(std::string, label)
BYPASS_PROXY_CONSTMETHOD0(bool, reliable)
BYPASS_PROXY_CONSTMETHOD0(bool, ordered)
BYPASS_PROXY_CONSTMETHOD0(uint16_t, maxRetransmitTime)
BYPASS_PROXY_CONSTMETHOD0(uint16_t, maxRetransmits)
BYPASS_PROXY_CONSTMETHOD0(absl::optional<int>, maxRetransmitsOpt)
BYPASS_PROXY_CONSTMETHOD0(absl::optional<int>, maxPacketLifeTime)
BYPASS_PROXY_CONSTMETHOD0(std::string, protocol)
BYPASS_PROXY_CONSTMETHOD0(bool, negotiated)
// Can't bypass the proxy since the id may change.
PROXY_CONSTMETHOD0(int, id)
BYPASS_PROXY_CONSTMETHOD0(Priority, priority)
PROXY_CONSTMETHOD0(DataState, state)
PROXY_CONSTMETHOD0(RTCError, error)
PROXY_CONSTMETHOD0(uint32_t, messages_sent)
PROXY_CONSTMETHOD0(uint64_t, bytes_sent)
PROXY_CONSTMETHOD0(uint32_t, messages_received)
PROXY_CONSTMETHOD0(uint64_t, bytes_received)
PROXY_CONSTMETHOD0(uint64_t, buffered_amount)
PROXY_METHOD0(void, Close)
// TODO(bugs.webrtc.org/11547): Change to run on the network thread.
PROXY_METHOD1(bool, Send, const DataBuffer&)
END_PROXY_MAP()

}  // namespace webrtc

#endif  // PC_DATA_CHANNEL_H_
