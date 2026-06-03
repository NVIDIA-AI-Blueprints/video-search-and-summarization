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

#ifndef _DYNAMIC_RTSP_SERVER_HH
#define _DYNAMIC_RTSP_SERVER_HH

#include<vector>
#include<atomic>
#ifndef _RTSP_SERVER_HH
#include "RTSPServer.hh"
#endif

class DynamicRTSPServer: public RTSPServer {
public:
  static DynamicRTSPServer* createNew(UsageEnvironment& env, Port ourPort,
				      UserAuthenticationDatabase* authDatabase,
				      unsigned reclamationTestSeconds = 65);
  void cleanup();
  ServerMediaSession* getServerMediaSessionForStream(char const* streamName);
  void deleteServerMediaSessionForStream(char const* streamName);
  std::vector<std::string> getActiveStreams();
  void setVodServer(bool isVodServer) { m_isVodServer = isVodServer; }
  bool isVodServer() { return m_isVodServer; }

protected:
  DynamicRTSPServer(UsageEnvironment& env, int ourSocketIPv4, int ourSocketIPv6, Port ourPort,
		    UserAuthenticationDatabase* authDatabase, unsigned reclamationTestSeconds);
  // called only by createNew();
  virtual ~DynamicRTSPServer();

protected: // redefined virtual functions
  virtual void lookupServerMediaSession(char const* streamName,
                                        lookupServerMediaSessionCompletionFunc* completionFunc,
                                        void* completionClientData,
                                        Boolean isFirstLookupInSession);
private:
  std::atomic<bool> m_isVodServer{false};
};

#endif
