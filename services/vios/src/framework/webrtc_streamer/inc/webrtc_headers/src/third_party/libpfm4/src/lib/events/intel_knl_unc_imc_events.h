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
 * PMU: knl_unc_imc (Intel Knights Landing IMC uncore PMU)
 */

static const intel_x86_umask_t knl_unc_m_cas_count[]={
  { .uname = "ALL",
    .udesc  = "Counts total number of DRAM CAS commands issued on this channel",
    .ucode  = 0x0300,
  },
  { .uname = "RD",
    .udesc  = "Counts all DRAM reads on this channel, incl. underfills",
    .ucode  = 0x0100,
  },
  { .uname = "WR",
    .udesc  = "Counts number of DRAM write CAS commands on this channel",
    .ucode  = 0x0200,
  },
};


static const intel_x86_entry_t intel_knl_unc_imc_pe[]={
  { .name   = "UNC_M_D_CLOCKTICKS",
    .desc   = "IMC Uncore DCLK counts",
    .code   = 0x00, /*encoding for generic counters */
    .cntmsk = 0xf,
  },
  { .name = "UNC_M_CAS_COUNT",
    .desc = "DRAM RD_CAS and WR_CAS Commands.",
    .code = 0x03,
    .cntmsk = 0xf,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(knl_unc_m_cas_count),
    .umasks  = knl_unc_m_cas_count,
  },
};

static const intel_x86_entry_t intel_knl_unc_imc_uclk_pe[]={
  { .name   = "UNC_M_U_CLOCKTICKS",
    .desc   = "IMC UCLK counts",
    .code   = 0x00, /*encoding for generic counters */
    .cntmsk = 0xf,
  },
};


