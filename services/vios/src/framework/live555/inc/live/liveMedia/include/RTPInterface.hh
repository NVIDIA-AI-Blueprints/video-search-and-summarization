/**********
This library is free software; you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the
Free Software Foundation; either version 3 of the License, or (at your
option) any later version. (See <http://www.gnu.org/copyleft/lesser.html>.)

This library is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for
more details.

You should have received a copy of the GNU Lesser General Public License
along with this library; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
**********/
// "liveMedia"
// Copyright (c) 1996-2023 Live Networks, Inc.  All rights reserved.
// An abstraction of a network interface used for RTP (or RTCP).
// (This allows the RTP-over-TCP hack (RFC 2326, section 10.12) to
// be implemented transparently.)
// C++ header

#ifndef _RTP_INTERFACE_HH
#define _RTP_INTERFACE_HH

#ifndef _MEDIA_HH
#include "Media.hh"
#endif
#ifndef _TLS_STATE_HH
#include "TLSState.hh"
#endif
#ifndef _GROUPSOCK_HH
#include <Groupsock.hh>
#endif
#include <string>
#include <chrono>
#include <queue>
#include <vector>
#include <array>
#include <memory>

#define POOL_SIZE    500
#define MAX_PACKET_SIZE 1500

// Typedef for an optional auxilliary handler function, to be called
// when each new packet is read:
typedef void AuxHandlerFunc(void* clientData, unsigned char* packet,
			    unsigned& packetSize);

typedef void ServerRequestAlternativeByteHandler(void* instance, u_int8_t requestByte);
// A hack that allows a handler for RTP/RTCP packets received over TCP to process RTSP commands that may also appear within
// the same TCP connection.  A RTSP server implementation would supply a function like this - as a parameter to
// "ServerMediaSubsession::startStream()".

// Packet batching
struct PacketQueueEntry {
    std::shared_ptr<class PacketBuffer> buffer;
    unsigned packetSize;
    bool markerBit;
    PacketQueueEntry(std::shared_ptr<class PacketBuffer> buf, unsigned size, bool marker)
        : buffer(buf), packetSize(size), markerBit(marker) {}
};

// Packet Pacer objects
class PacketBuffer {
  private:
    std::shared_ptr<unsigned char[]> buffer;
    bool inUse;
  public:
    PacketBuffer() : buffer(new unsigned char[MAX_PACKET_SIZE]), inUse(false) {}

    unsigned char* getData() { return buffer.get(); }
    unsigned const char* getData() const { return buffer.get(); }

    PacketBuffer(const PacketBuffer&) = delete;
    PacketBuffer& operator=(const PacketBuffer&) = delete;
};

class RTPInterface {
public:
  RTPInterface(Medium* owner, Groupsock* gs);
  virtual ~RTPInterface();

  Groupsock* gs() const { return fGS; }

  void setStreamSocket(int sockNum, unsigned char streamChannelId, TLSState* tlsState);
  void addStreamSocket(int sockNum, unsigned char streamChannelId, TLSState* tlsState);
  void removeStreamSocket(int sockNum, unsigned char streamChannelId);
  static void setServerRequestAlternativeByteHandler(UsageEnvironment& env, int socketNum,
						     ServerRequestAlternativeByteHandler* handler, void* clientData);
  static void clearServerRequestAlternativeByteHandler(UsageEnvironment& env, int socketNum);

  Boolean sendPacket(unsigned char* packet, unsigned packetSize);
  void startNetworkReading(TaskScheduler::BackgroundHandlerProc*
                           handlerProc);
  Boolean handleRead(unsigned char* buffer, unsigned bufferMaxSize,
		     // out parameters:
		     unsigned& bytesRead, struct sockaddr_storage& fromAddress,
		     int& tcpSocketNum, unsigned char& tcpStreamChannelId,
		     Boolean& packetReadWasIncomplete);
  // Note: If "tcpSocketNum" < 0, then the packet was received over UDP, and "tcpStreamChannelId"
  //   is undefined (and irrelevant).


  // Otherwise (if "tcpSocketNum" >= 0), the packet was received (interleaved) over TCP, and
  //   "tcpStreamChannelId" will return the channel id.

  void stopNetworkReading();

  Boolean hasAtleastOneTcpDestination() const { return fTCPStreams != NULL; }

  UsageEnvironment& envir() const { return fOwner->envir(); }

  void setAuxilliaryReadHandler(AuxHandlerFunc* handlerFunc,
				void* handlerClientData) {
    fAuxReadHandlerFunc = handlerFunc;
    fAuxReadHandlerClientData = handlerClientData;
  }

  void forgetOurGroupsock() { fGS = NULL; }
    // This may be called - *only immediately prior* to deleting this - to prevent our destructor
    // from turning off background reading on the 'groupsock'.  (This is in case the 'groupsock'
    // is also being read from elsewhere.)

  void enablePacketPacing();
  void setUrl(const std::string& url) { fDebugURL = url; }
  std::string getUrl() { return fDebugURL; }

private:
  // Helper functions for sending a RTP or RTCP packet over a TCP connection:
  Boolean sendRTPorRTCPPacketOverTCP(unsigned char* packet, unsigned packetSize,
				     int socketNum, unsigned char streamChannelId,
				     TLSState* tlsState);
  Boolean sendDataOverTCP(int socketNum, TLSState* tlsState,
			  u_int8_t const* data, unsigned dataSize, Boolean forceSendToSucceed);

private:
  friend class SocketDescriptor;
  Medium* fOwner;
  Groupsock* fGS;
  class tcpStreamRecord* fTCPStreams; // optional, for RTP-over-TCP streaming/receiving

  unsigned short fNextTCPReadSize;
    // how much data (if any) is available to be read from the TCP stream
  int fNextTCPReadStreamSocketNum;
  unsigned char fNextTCPReadStreamChannelId;
  TLSState* fNextTCPReadTLSState;
  TaskScheduler::BackgroundHandlerProc* fReadHandlerProc; // if any

  AuxHandlerFunc* fAuxReadHandlerFunc;
  void* fAuxReadHandlerClientData;

  Boolean fPacketPacingEnabled;
  unsigned fPacketPaceTimeUs;
  unsigned fPacketConfiguredBatchSize;
  unsigned fcurrentBatchSize;

  Boolean fEnableOutputPacketDebug = False;
  std::string fDebugURL;
  std::chrono::steady_clock::time_point startTs;

  // For batching and pacing
  Boolean sendPacketInternal(unsigned char* packet, unsigned packetSize);
  std::queue<PacketQueueEntry> fPacketQueue;
  void processPacedPackets();
  static void pacingTimerHandler(void* clientData);
  TaskToken fPacingTaskToken = nullptr;

  // Buffer pool for packet copies
  class BufferPool {
    private:
      std::vector<std::shared_ptr<PacketBuffer> > pool;
      std::queue<std::shared_ptr<PacketBuffer> > freeList;
      bool initialized = false;
    public:
      void initialize() {
        if (initialized) return;
        pool.reserve(POOL_SIZE);
        for (size_t i = 0; i < POOL_SIZE; ++i) {
          auto pktBuf = std::make_shared<PacketBuffer>();
          pool.push_back(pktBuf);
          freeList.push(pktBuf);
        }
        initialized = true;
      }

      std::shared_ptr<PacketBuffer> acquireBuffer(size_t size) {
        if (!initialized || size > MAX_PACKET_SIZE || freeList.empty()) {
          return nullptr;
        }
        auto buf = freeList.front();
        freeList.pop();
        return buf;
      }

      void releaseBuffer(std::shared_ptr<PacketBuffer> buf) {
        if (!initialized || !buf) return;
        freeList.push(buf);
      }

      void destroy() {
        while (!freeList.empty()) freeList.pop();
        pool.clear();
        initialized = false;
      }
  } fBufferPool;
};

#endif
