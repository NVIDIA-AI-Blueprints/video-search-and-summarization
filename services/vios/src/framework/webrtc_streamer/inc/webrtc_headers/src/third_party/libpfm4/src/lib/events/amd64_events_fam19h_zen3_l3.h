/*
 * Copyright 2021 Google LLC
 * Contributed by Stephane Eranian <eranian@gmail.com>
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
 * INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
 * PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
 * HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
 * OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * This file is part of libpfm, a performance monitoring support library for
 * applications on Linux.
 *
 * PMU: amd64_fam19h_zen3_l3 (AMD64 Fam19h Zen3 L3)
 */

static const amd64_umask_t amd64_fam19h_zen3_l3_requests[]={
  { .uname  = "ALL",
    .udesc  = "All types of requests",
    .ucode  = 0xff,
    .uflags = AMD64_FL_DFL,
  },
};

static const amd64_entry_t amd64_fam19h_zen3_l3_pe[]={
  { .name   = "UNC_L3_REQUESTS",
    .desc   = "Number of requests to L3 cache",
    .code    = 0x04,
    .ngrp    = 1,
    .numasks = LIBPFM_ARRAY_SIZE(amd64_fam19h_zen3_l3_requests),
    .umasks = amd64_fam19h_zen3_l3_requests,
  },
  { .name   = "UNC_L3_MISS_LATENCY",
    .desc   = "Each cycle, this event increments by the total number of read requests outstanding from the CCX divided by XiSysFillLatencyDivider. The user can calculate the average system fill latency in cycles by multiplying by XiSysFillLatencyDivider and dividing by the total number of fill requests over the same period (counted by event 0x9A UserMask 0x1F). XiSysFillLatencyDivider is 16 for this product, but may change for future products",
    .code    = 0x90,
  },
  { .name   = "UNC_L3_MISSES",
    .desc   = "Number of L3 cache misses",
    .code    = 0x9a,
    .ngrp    = 1,
    .numasks = LIBPFM_ARRAY_SIZE(amd64_fam19h_zen3_l3_requests),
    .umasks = amd64_fam19h_zen3_l3_requests, /* shared */
  },
};
