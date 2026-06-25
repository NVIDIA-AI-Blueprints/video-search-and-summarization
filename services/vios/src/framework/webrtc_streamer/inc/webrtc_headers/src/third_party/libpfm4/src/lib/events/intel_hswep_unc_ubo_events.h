/*
 * Copyright (c) 2014 Google Inc. All rights reserved
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
 * PMU: hswep_unc_ubo (Intel Haswell-EP U-Box uncore PMU)
 */

static const intel_x86_umask_t hswep_unc_u_event_msg[]={
  { .uname = "DOORBELL_RCVD",
    .udesc  = "TBD",
    .ucode  = 0x800,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
};

static const intel_x86_umask_t hswep_unc_u_phold_cycles[]={
  { .uname = "ASSERT_TO_ACK",
    .udesc  = "Number of cycles asserted to ACK",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
};

static const intel_x86_entry_t intel_hswep_unc_u_pe[]={
  { .name = "UNC_U_EVENT_MSG",
    .desc = "VLW Received",
    .code = 0x42,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_UBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_u_event_msg),
    .umasks  = hswep_unc_u_event_msg
  },
  { .name = "UNC_U_PHOLD_CYCLES",
    .desc = "Cycles PHOLD asserts to Ack",
    .code = 0x45,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_UBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_u_phold_cycles),
    .umasks  = hswep_unc_u_phold_cycles
  },
  { .name = "UNC_U_RACU_REQUESTS",
    .desc = "RACU requests",
    .code = 0x46,
    .cntmsk = 0x3,
    .modmsk = HSWEP_UNC_UBO_ATTRS,
  },
};
