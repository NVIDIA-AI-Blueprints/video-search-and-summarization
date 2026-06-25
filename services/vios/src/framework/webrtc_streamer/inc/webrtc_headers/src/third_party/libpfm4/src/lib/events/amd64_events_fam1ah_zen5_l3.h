#include "../pfmlib_amd64_priv.h"
#include "../pfmlib_priv.h"
/*
 * Copyright (C) 2024 Advanced Micro Devices, Inc. All rights reserved.
 *               Contributed by Swarup Sahoo
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
 * PMU: amd64_fam1ah_zen5_l3 (AMD64 Fam1Ah Zen3 L3)
 */

static const amd64_umask_t amd64_fam1ah_zen5_l3_requests[]={
  { .uname  = "L3_MISS",
    .udesc  = "L3 miss",
    .ucode  = 0x1,
  },
  { .uname  = "L3_HIT",
    .udesc  = "L3 hit",
    .ucode  = 0xfe,
  },
  { .uname  = "ALL",
    .udesc  = "All types of requests",
    .ucode  = 0xff,
    .uflags = AMD64_FL_NCOMBO | AMD64_FL_DFL,
  },
};

static const amd64_entry_t amd64_fam1ah_zen5_l3_pe[]={
  { .name   = "UNC_L3_REQUESTS",
    .desc   = "Number of requests to L3 cache",
    .code    = 0x04,
    .ngrp    = 1,
    .numasks = LIBPFM_ARRAY_SIZE(amd64_fam1ah_zen5_l3_requests),
    .umasks = amd64_fam1ah_zen5_l3_requests,
  },
};
