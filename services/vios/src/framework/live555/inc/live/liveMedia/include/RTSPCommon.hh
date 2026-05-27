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
// Common routines used by both RTSP clients and servers
// C++ header

#ifndef _RTSP_COMMON_HH
#define _RTSP_COMMON_HH

#ifndef _BOOLEAN_HH
#include "Boolean.hh"
#endif

#ifndef _MEDIA_HH
#include <Media.hh> // includes some definitions perhaps needed for Borland compilers?
#endif
#include <string>

#if defined(__WIN32__) || defined(_WIN32) || defined(_QNX4)
#define _strncasecmp _strnicmp
#define snprintf _snprintf
#else
#define _strncasecmp strncasecmp
#endif

#define RTSP_PARAM_STRING_MAX 200

Boolean parseRTSPRequestString(char const *reqStr, unsigned reqStrSize, // in
			       char *resultCmdName, // out
			       unsigned resultCmdNameMaxSize, // in
			       char* resultURLPreSuffix, // out
			       unsigned resultURLPreSuffixMaxSize, // in
			       char* resultURLSuffix, // out
			       unsigned resultURLSuffixMaxSize, // in
			       char* resultCSeq, // out
			       unsigned resultCSeqMaxSize, // in
			       char* resultSessionId, // out
			       unsigned resultSessionIdMaxSize, // in
			       unsigned& contentLength, Boolean& urlIsRTSPS); // out

Boolean parseRangeParam(char const* paramStr, double& rangeStart, double& rangeEnd, char*& absStartTime, char*& absEndTime, Boolean& startTimeIsNow);
Boolean parseRangeHeader(char const* buf, double& rangeStart, double& rangeEnd, char*& absStartTime, char*& absEndTime, Boolean& startTimeIsNow);

Boolean parseScaleHeader(char const* buf, float& scale);

Boolean RTSPOptionIsSupported(char const* commandName, char const* optionsResponseString);
    // Returns True iff the RTSP command "commandName" is mentioned as one of the commands supported in "optionsResponseString"
    // (which should be the 'resultString' from a previous RTSP "OPTIONS" request).

char const* dateHeader(); // A "Date:" header that can be used in a RTSP (or HTTP) response 

const std::string getCurrentIsoTimeMS();
const std::string convertEpocTimeToIsoTime (int64_t epoch64);
std::string convertHexBytesToString(const uint8_t* hexBytes, size_t length);

// True iff the NAL/RTP-payload at "data" begins a new picture.
// Returns False for header NALs (SPS/PPS/SEI/AUD/VPS), continuation
// slices (first_mb_in_slice > 0 / first_slice_segment_in_pic_flag == 0),
// and undersized buffers. Codec must be "H264" or "H265". Handles both
// reassembled NAL units and raw RTP packets containing FU-A/FU-B (H.264)
// or FU (H.265) fragmentation.
Boolean isFirstSliceOfPicture(char const* codec,
                              unsigned char const* data,
                              unsigned dataSize);

// PTS monotonic-fix helper. If "pts" (converted to ms-since-epoch)
// equals "lastPtsMs", adjusts "pts" by +1 ms to keep the timestamp
// strictly monotonic; otherwise leaves "pts" unchanged. Always updates
// "lastPtsMs" to the (possibly adjusted) new value. Returns True iff
// an adjustment was applied. See ProxyServerMediaSession.cpp /
// MultiFramedRTPSource.cpp for the broader rationale (h264parse
// duplicate-PTS bump avoidance).
Boolean adjustIfDuplicateMs(struct timeval& pts, int64_t& lastPtsMs);

#endif
