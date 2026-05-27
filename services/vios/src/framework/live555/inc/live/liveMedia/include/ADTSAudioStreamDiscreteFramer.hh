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
// A filter that reads (discrete) AAC audio frames, and outputs each frame with
// a preceding ADTS header.
// C++ header

#ifndef _ADTS_AUDIO_STREAM_DISCRETE_FRAMER_HH
#define _ADTS_AUDIO_STREAM_DISCRETE_FRAMER_HH

#ifndef _FRAMED_FILTER_HH
#include "FramedFilter.hh"
#endif

#define ADTS_HEADER_SIZE 7 // we don't include a checksum

class ADTSAudioStreamDiscreteFramer: public FramedFilter {
public:
  static ADTSAudioStreamDiscreteFramer*
  createNew(UsageEnvironment& env, FramedSource* inputSource, char const* configStr);
    // "configStr" should be a 4-character hexadecimal string for a 2-byte value

protected:
  ADTSAudioStreamDiscreteFramer(UsageEnvironment& env, FramedSource* inputSource,
				u_int8_t profile, u_int8_t samplingFrequencyIndex, u_int8_t channelConfiguration);
      // called only by createNew()
  virtual ~ADTSAudioStreamDiscreteFramer();

protected:
  // redefined virtual functions:
  virtual void doGetNextFrame();

protected:
  static void afterGettingFrame(void* clientData, unsigned frameSize,
                                unsigned numTruncatedBytes,
                                struct timeval presentationTime,
                                unsigned durationInMicroseconds);
  void afterGettingFrame1(unsigned frameSize,
                          unsigned numTruncatedBytes,
                          struct timeval presentationTime,
                          unsigned durationInMicroseconds);

private:
  u_int8_t fADTSHeader[ADTS_HEADER_SIZE];
};

#endif
