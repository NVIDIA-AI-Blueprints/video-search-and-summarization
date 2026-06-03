/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include "FileServerMediaSubsession.hh"
#include "VideoRTPSink.hh"
#include "FramedFilter.hh"
#include "H264ByteStreamSource.hh"
#include "NvMediaSource.hh"

#include <memory>
#include <string>
#include <vector>

class AvLoopSyncCoordinator;

typedef enum { InvalidState = -1, InitialState, DescribeState, PlayState, PauseState, DestroyState } eStreamState;

class NvFileServerMediaSubsession: public FileServerMediaSubsession
{
public:
  static NvFileServerMediaSubsession*
  createNew(UsageEnvironment& env, std::string streamName,
          eMediaType mediaType, eSourceType sourceType,
          Boolean reuseFirstSource, string url_params);

  // Used to implement "getAuxSDPLine()":
  void checkForAuxSDPLine1();
  void afterPlayingDummy1();
  void frameSourceEventChange(eFrameSourceEvent sourceEvent);
  void seekStreamByGlobalFrameId(H264ByteStreamSource* source);
  H264ByteStreamSource* getVideoStreamSource() { return m_videoStreamSource; }

  /* Inject the AV-loop-sync coordinator. Called from
   * DynamicRTSPServer::createNewSMS() only when this subsession is
   * part of an audio+video pair (URL contains includeAudio=true,
   * sourceType is file). Forwards the pointer to the underlying
   * NvMediaSource so the ByteStreamSource (created later by
   * createNewStreamSource) can pick it up. Safe to pass nullptr to
   * detach, though that's never used today. */
  void setAvLoopSync(const std::shared_ptr<AvLoopSyncCoordinator>& coord);

protected:
  NvFileServerMediaSubsession(UsageEnvironment& env,
				      std::string streamName, eMediaType mediaType, eSourceType sourceType,
              Boolean reuseFirstSource, portNumBits initialPortNum, string url_params);
      // called only by createNew();
  virtual ~NvFileServerMediaSubsession();

  void setDoneFlag() { fDoneFlag = ~0; }

protected: // redefined virtual functions
  virtual char const* getAuxSDPLine(RTPSink* rtpSink,
				    FramedSource* inputSource);
  virtual void testScaleFactor(float& scale);
  virtual void setStreamSourceScale(FramedSource* inputSource, float scale);
  virtual void seekStreamSource(FramedSource* inputSource, double& seekNPT, double streamDuration, u_int64_t& numBytes);
    // This routine is used to seek by relative (i.e., NPT) time.
    // "streamDuration", if >0.0, specifies how much data to stream, past "seekNPT".  (If <=0.0, all remaining data is streamed.)
    // "numBytes" returns the size (in bytes) of the data to be streamed, or 0 if unknown or unlimited.
  virtual void seekStreamSource(FramedSource* inputSource, char*& absStart, char*& absEnd);
    // This routine is used to seek by 'absolute' time.
    // "absStart" should be a string of the form "YYYYMMDDTHHMMSSZ" or "YYYYMMDDTHHMMSS.<frac>Z".
    // "absEnd" should be either NULL (for no end time), or a string of the same form as "absStart".
    // These strings may be modified in-place, or can be reassigned to a newly-allocated value (after delete[]ing the original).
  virtual FramedSource* createNewStreamSource(unsigned clientSessionId,
					      unsigned& estBitrate);
  virtual RTPSink* createNewRTPSink(Groupsock* rtpGroupsock,
                                    unsigned char rtpPayloadTypeIfDynamic,
				    FramedSource* inputSource);
  virtual void startStream(unsigned clientSessionId, void* streamToken,
                    TaskFunc* rtcpRRHandler,
                    void* rtcpRRHandlerClientData,
                    unsigned short& rtpSeqNum,
                    unsigned& rtpTimestamp,
                    ServerRequestAlternativeByteHandler* serverRequestAlternativeByteHandler,
                    void* serverRequestAlternativeByteHandlerClientData);
  void pauseStream(unsigned clientSessionId, void* streamToken);
  virtual void deleteStream(unsigned clientSessionId, void*& streamToken);
  void createMediaSource(eMediaType mediaType, eSourceType sourceType);
  void destroyMediaSource();
  static void checkIfSourceInPlayMode(void* clientData);
  shared_ptr<NvMediaSource> getMediaSource() { return m_mediaSource; }
  void setRtcpBaseTime();
  std::pair<uint64_t, uint64_t> getRangeFromUrlParams(const string& url_params);
  std::string getSessionId() { return m_sessionId; }
private:
  void setDoneFlagAndResetSource();
  char* fAuxSDPLine;
  char fDoneFlag; // used when setting up "fAuxSDPLine"
  RTPSink* fDummyRTPSink; // ditto
  std::string m_streamName;
  std::string m_sessionId;
  shared_ptr<NvMediaSource> m_mediaSource;
  string m_url_params;
  TaskToken m_PlayModeCheckTask;
  eStreamState m_stream_state;
  void *m_streamToken;
  eMediaType m_mediaType;
  eSourceType m_sourceType;
  H264ByteStreamSource* m_videoStreamSource = nullptr;
  int64_t m_syncSourceToGlobalFrameId = -1;
  uint64_t m_startTime;
  uint64_t m_endTime;
  bool m_isSeekStreamDone = false;
  bool m_vodEnableOverlay = false;
  std::shared_ptr<AvLoopSyncCoordinator> m_avLoopSync;
};
