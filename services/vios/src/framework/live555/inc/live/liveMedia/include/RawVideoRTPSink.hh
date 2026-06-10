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
// RTP sink for Raw video
// C++ header

#ifndef _RAW_VIDEO_RTP_SINK_HH
#define _RAW_VIDEO_RTP_SINK_HH

#ifndef _VIDEO_RTP_SINK_HH
#include "VideoRTPSink.hh"
#endif

#ifndef _RAW_VIDEO_FRAME_PARAMETERS_HH
#include "RawVideoFrameParameters.hh"
#endif

class RawVideoRTPSink: public VideoRTPSink {
public:
  static RawVideoRTPSink*
  createNew(UsageEnvironment& env, Groupsock* RTPgs, u_int8_t rtpPayloadFormat,
	    unsigned width, unsigned height, unsigned depth, // as defined by RFC 4175, sec 6.1
	    char const* sampling, char const* colorimetry);

protected:
  RawVideoRTPSink(UsageEnvironment& env, Groupsock* RTPgs,
                  u_int8_t rtpPayloadFormat,
                  unsigned width, unsigned height, unsigned depth,
                  char const* sampling, char const* colorimetry);
  // called only by createNew()
  
  virtual ~RawVideoRTPSink();
  
private: // redefined virtual functions:
  virtual char const* auxSDPLine(); // for the "a=fmtp:" SDP line
  
  virtual void doSpecialFrameHandling(unsigned fragmentationOffset,
                      unsigned char* frameStart,
                      unsigned numBytesInFrame,
                      struct timeval framePresentationTime,
                      unsigned numRemainingBytes);
  virtual Boolean frameCanAppearAfterPacketStart(unsigned char const* frameStart,
                         unsigned numBytesInFrame) const;
  virtual unsigned specialHeaderSize() const;
  virtual unsigned computeOverflowForNewFrame(unsigned newFrameSize) const;
  
private:
  char* fFmtpSDPLine;
  unsigned fLineIndex;
  RawVideoFrameParameters fP;

  unsigned getNumLinesInPacket(unsigned fragOffset, u_int16_t*& lengths, u_int16_t*& offsets) const;
  //  return the number of lines, their lengths and offsets from the fragmentation offset of the whole frame.
  // call delete[] on lengths and offsets after use of the function
};

#endif
