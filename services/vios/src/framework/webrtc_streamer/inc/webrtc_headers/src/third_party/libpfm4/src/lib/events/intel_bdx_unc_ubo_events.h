/*
 * Copyright (c) 2017 Google Inc. All rights reserved
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
 * PMU: bdx_unc_ubo
 */

static intel_x86_umask_t bdx_unc_u_event_msg[]={
	{ .uname  = "DOORBELL_RCVD",
	  .ucode  = 0x800,
	  .udesc  = "VLW Received",
	  .uflags = INTEL_X86_DFL,
	},
};

static intel_x86_umask_t bdx_unc_u_phold_cycles[]={
	{ .uname  = "ASSERT_TO_ACK",
	  .ucode  = 0x100,
	  .udesc  = "Cycles PHOLD Assert to Ack. Assert to ACK",
	  .uflags = INTEL_X86_DFL,
	},
};

static intel_x86_entry_t intel_bdx_unc_u_pe[]={
  { .name   = "UNC_U_EVENT_MSG",
    .code   = 0x42,
    .desc   = "Virtual Logical Wire (legacy) message were received from uncore",
    .modmsk = BDX_UNC_UBO_ATTRS,
    .cntmsk = 0x3,
    .ngrp   = 1,
    .umasks = bdx_unc_u_event_msg,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_u_event_msg),
  },
  { .name   = "UNC_U_PHOLD_CYCLES",
    .code   = 0x45,
    .desc   = "PHOLD cycles.  Filter from source CoreID.",
    .modmsk = BDX_UNC_UBO_ATTRS,
    .cntmsk = 0x3,
    .ngrp   = 1,
    .umasks = bdx_unc_u_phold_cycles,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_u_phold_cycles),
  },
  { .name   = "UNC_U_RACU_REQUESTS",
    .code   = 0x46,
    .desc   = "Number outstanding register requests within message channel tracker",
    .modmsk = BDX_UNC_UBO_ATTRS,
    .cntmsk = 0x3,
  },
};

