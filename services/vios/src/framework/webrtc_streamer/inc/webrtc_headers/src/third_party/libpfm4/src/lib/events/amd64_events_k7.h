/*
 * Copyright (c) 2011 Google, Inc
 * Contributed by Stephane Eranian <eranian@gmail.com>
 *
 * Regenerated from previous version by:
 *
 * Copyright (c) 2006, 2007 Advanced Micro Devices, Inc.
 * Contributed by Ray Bryant <raybry@mpdtxmail.amd.com>
 * Contributed by Robert Richter <robert.richter@amd.com>
 * Modified for K7 by Vince Weaver <vince _at_ csl.cornell.edu>
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
 * This file has been automatically generated.
 *
 * PMU: amd64_k7 (AMD64 K7)
 */

/*
 * Definitions taken from "AMD Athlon Processor x86 Code Optimization Guide"
 * Table 11 February 2002
 */

static const amd64_umask_t amd64_k7_data_cache_refills[]={
   { .uname  = "L2_INVALID",
     .udesc  = "Invalid line from L2",
     .ucode = 0x1,
   },
   { .uname  = "L2_SHARED",
     .udesc  = "Shared-state line from L2",
     .ucode = 0x2,
   },
   { .uname  = "L2_EXCLUSIVE",
     .udesc  = "Exclusive-state line from L2",
     .ucode = 0x4,
   },
   { .uname  = "L2_OWNED",
     .udesc  = "Owned-state line from L2",
     .ucode = 0x8,
   },
   { .uname  = "L2_MODIFIED",
     .udesc  = "Modified-state line from L2",
     .ucode = 0x10,
   },
   { .uname  = "ALL",
     .udesc  = "Shared, Exclusive, Owned, Modified State Refills",
     .ucode = 0x1f,
     .uflags= AMD64_FL_NCOMBO | AMD64_FL_DFL,
   },
};

static const amd64_umask_t amd64_k7_data_cache_refills_from_system[]={
   { .uname  = "INVALID",
     .udesc  = "Invalid",
     .ucode = 0x1,
   },
   { .uname  = "SHARED",
     .udesc  = "Shared",
     .ucode = 0x2,
   },
   { .uname  = "EXCLUSIVE",
     .udesc  = "Exclusive",
     .ucode = 0x4,
   },
   { .uname  = "OWNED",
     .udesc  = "Owned",
     .ucode = 0x8,
   },
   { .uname  = "MODIFIED",
     .udesc  = "Modified",
     .ucode = 0x10,
   },
   { .uname  = "ALL",
     .udesc  = "Invalid, Shared, Exclusive, Owned, Modified",
     .ucode = 0x1f,
     .uflags= AMD64_FL_NCOMBO | AMD64_FL_DFL,
   },
};

static const amd64_entry_t amd64_k7_pe[]={
{ .name    = "DATA_CACHE_ACCESSES",
  .desc    = "Data Cache Accesses",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x40,
},
{ .name    = "DATA_CACHE_MISSES",
  .desc    = "Data Cache Misses",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x41,
},
{ .name    = "DATA_CACHE_REFILLS",
  .desc    = "Data Cache Refills from L2",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x42,
  .numasks = LIBPFM_ARRAY_SIZE(amd64_k7_data_cache_refills),
  .ngrp    = 1,
  .umasks  = amd64_k7_data_cache_refills,
},
{ .name    = "DATA_CACHE_REFILLS_FROM_SYSTEM",
  .desc    = "Data Cache Refills from System",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x43,
  .numasks = LIBPFM_ARRAY_SIZE(amd64_k7_data_cache_refills_from_system),
  .ngrp    = 1,
  .umasks  = amd64_k7_data_cache_refills_from_system,
},
{ .name    = "DATA_CACHE_LINES_EVICTED",
  .desc    = "Data Cache Lines Evicted",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x44,
  .numasks = LIBPFM_ARRAY_SIZE(amd64_k7_data_cache_refills_from_system),
  .ngrp    = 1,
  .umasks  = amd64_k7_data_cache_refills_from_system, /* identical to actual umasks list for this event */
},
{ .name    = "L1_DTLB_MISS_AND_L2_DTLB_HIT",
  .desc    = "L1 DTLB Miss and L2 DTLB Hit",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x45,
},
{ .name    = "L1_DTLB_AND_L2_DTLB_MISS",
  .desc    = "L1 DTLB and L2 DTLB Miss",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x46,
},
{ .name    = "MISALIGNED_ACCESSES",
  .desc    = "Misaligned Accesses",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x47,
},
{ .name    = "CPU_CLK_UNHALTED",
  .desc    = "CPU Clocks not Halted",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x76,
},
{ .name    = "INSTRUCTION_CACHE_FETCHES",
  .desc    = "Instruction Cache Fetches",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x80,
},
{ .name    = "INSTRUCTION_CACHE_MISSES",
  .desc    = "Instruction Cache Misses",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x81,
},
{ .name    = "L1_ITLB_MISS_AND_L2_ITLB_HIT",
  .desc    = "L1 ITLB Miss and L2 ITLB Hit",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x84,
},
{ .name    = "L1_ITLB_MISS_AND_L2_ITLB_MISS",
  .desc    = "L1 ITLB Miss and L2 ITLB Miss",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0x85,
},
{ .name    = "RETIRED_INSTRUCTIONS",
  .desc    = "Retired Instructions (includes exceptions, interrupts, resyncs)",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc0,
},
{ .name    = "RETIRED_UOPS",
  .desc    = "Retired uops",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc1,
},
{ .name    = "RETIRED_BRANCH_INSTRUCTIONS",
  .desc    = "Retired Branch Instructions",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc2,
},
{ .name    = "RETIRED_MISPREDICTED_BRANCH_INSTRUCTIONS",
  .desc    = "Retired Mispredicted Branch Instructions",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc3,
},
{ .name    = "RETIRED_TAKEN_BRANCH_INSTRUCTIONS",
  .desc    = "Retired Taken Branch Instructions",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc4,
},
{ .name    = "RETIRED_TAKEN_BRANCH_INSTRUCTIONS_MISPREDICTED",
  .desc    = "Retired Taken Branch Instructions Mispredicted",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc5,
},
{ .name    = "RETIRED_FAR_CONTROL_TRANSFERS",
  .desc    = "Retired Far Control Transfers",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc6,
},
{ .name    = "RETIRED_BRANCH_RESYNCS",
  .desc    = "Retired Branch Resyncs (only non-control transfer branches)",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xc7,
},
{ .name    = "INTERRUPTS_MASKED_CYCLES",
  .desc    = "Interrupts-Masked Cycles",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xcd,
},
{ .name    = "INTERRUPTS_MASKED_CYCLES_WITH_INTERRUPT_PENDING",
  .desc    = "Interrupts-Masked Cycles with Interrupt Pending",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xce,
},
{ .name    = "INTERRUPTS_TAKEN",
  .desc    = "Interrupts Taken",
  .modmsk  = AMD64_BASIC_ATTRS,
  .code    = 0xcf,
},
};
