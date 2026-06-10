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
// Parameters used for streaming (transmitting and receiving) raw video frames over RTP
// C++ header

#ifndef _RAW_VIDEO_FRAME_PARAMETERS_HH
#define _RAW_VIDEO_FRAME_PARAMETERS_HH

class RawVideoFrameParameters {
public:
  RawVideoFrameParameters(unsigned width, unsigned height, unsigned depth, char const* sampling);
  virtual ~RawVideoFrameParameters();

public:
  u_int16_t pgroupSize; // in octets
  u_int16_t numPixelsInPgroup;
  u_int32_t scanLineSize; // in octets
  u_int32_t frameSize; // in octets
  u_int16_t scanLineIterationStep; // usually 1, but 2 for sampling=="YCbCr-4:2:0"
};

#endif
