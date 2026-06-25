/*
 * Copyright (c) 2016 Intel Corp. All rights reserved
 * Contributed by Peinan Zhang <peinan.zhang@intel.com>
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
 * PMU: knl_unc_edc (Intel Knights Landing EDC_UCLK, EDC_ECLK uncore PMUs)
 */

static const intel_x86_umask_t knl_unc_edc_uclk_access_count[]={
  { .uname = "HIT_CLEAN",
    .udesc  = "Hit E",
    .ucode  = 0x0100,
  },
  { .uname = "HIT_DIRTY",
    .udesc  = "Hit M",
    .ucode  = 0x0200,
  },
  { .uname = "MISS_CLEAN",
    .udesc  = "Miss E",
    .ucode  = 0x0400,
  },
  { .uname = "MISS_DIRTY",
    .udesc  = "Miss M",
    .ucode  = 0x0800,
  },
  { .uname = "MISS_INVALID",
    .udesc  = "Miss I",
    .ucode  = 0x1000,
  },
  { .uname = "MISS_GARBAGE",
    .udesc  = "Miss G",
    .ucode  = 0x2000,
  },
};


static const intel_x86_entry_t intel_knl_unc_edc_uclk_pe[]={
  { .name   = "UNC_E_U_CLOCKTICKS",
    .desc   = "EDC UCLK clockticks (generic counters)",
    .code   = 0x00, /*encoding for generic counters */
    .cntmsk = 0xf,
  },
  { .name = "UNC_E_EDC_ACCESS",
    .desc = "Number of EDC Access Hits or Misses.",
    .code = 0x02,
    .cntmsk = 0xf,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(knl_unc_edc_uclk_access_count),
    .umasks  = knl_unc_edc_uclk_access_count
  },
};

static const intel_x86_entry_t intel_knl_unc_edc_eclk_pe[]={
  { .name   = "UNC_E_E_CLOCKTICKS",
    .desc   = "EDC ECLK clockticks (generic counters)",
    .code   = 0x00, /*encoding for generic counters */
    .cntmsk = 0xf,
  },
  { .name = "UNC_E_RPQ_INSERTS",
    .desc = "Counts total number of EDC RPQ insers",
    .code = 0x0101,
    .cntmsk = 0xf,
  },
  { .name = "UNC_E_WPQ_INSERTS",
    .desc = "Counts total number of EDC WPQ insers",
    .code = 0x0102,
    .cntmsk = 0xf,
  },
};
