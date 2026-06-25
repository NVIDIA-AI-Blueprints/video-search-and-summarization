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
 * PMU: bdx_unc_cbo
 */

#define CBO_FILT_MESIF(a, b, c, d) \
   { .uname  = "STATE_"#a,\
     .udesc  = #b" cacheline state",\
     .ufilters[0] = 1ULL << (17 + (c)),\
     .grpid = d, \
   }

#define CBO_FILT_MESIFS(d) \
   CBO_FILT_MESIF(I, Invalid, 0, d), \
   CBO_FILT_MESIF(S, Shared, 1, d), \
   CBO_FILT_MESIF(E, Exclusive, 2, d), \
   CBO_FILT_MESIF(M, Modified, 3, d), \
   CBO_FILT_MESIF(F, Forward, 4, d), \
   CBO_FILT_MESIF(D, Debug, 5, d), \
   { .uname  = "STATE_MP",\
     .udesc  = "Cacheline is modified but never written, was forwarded in modified state",\
     .ufilters[0] = 0x1ULL << (17+6),\
     .grpid = d, \
     .uflags = INTEL_X86_NCOMBO, \
   }, \
   { .uname  = "STATE_MESIFD",\
     .udesc  = "Any cache line state",\
     .ufilters[0] = 0x7fULL << 17,\
     .grpid = d, \
     .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL, \
   }

#define CBO_FILT_OPC(d) \
   { .uname  = "OPC_RFO",\
     .udesc  = "Demand data RFO (combine with any OPCODE umask)",\
     .ufilters[1] = 0x180ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_CRD",\
     .udesc  = "Demand code read (combine with any OPCODE umask)",\
     .ufilters[1] = 0x181ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_DRD",\
     .udesc  = "Demand data read (combine with any OPCODE umask)",\
     .ufilters[1] = 0x182ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PRD",\
     .udesc  = "Partial reads (UC) (combine with any OPCODE umask)",\
     .ufilters[1] = 0x187ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_WCILF",\
     .udesc  = "Full Stream store (combine with any OPCODE umask)", \
     .ufilters[1] = 0x18cULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_WCIL",\
     .udesc  = "Partial Stream store (combine with any OPCODE umask)", \
     .ufilters[1] = 0x18dULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_WIL",\
     .udesc  = "Write Invalidate Line (Partial) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x18fULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PF_RFO",\
     .udesc  = "Prefetch RFO into LLC but do not pass to L2 (includes hints) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x190ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PF_CODE",\
     .udesc  = "Prefetch code into LLC but do not pass to L2 (includes hints) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x191ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PF_DATA",\
     .udesc  = "Prefetch data into LLC but do not pass to L2 (includes hints) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x192ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCIWIL",\
     .udesc  = "PCIe write (partial, non-allocating) - partial line MMIO write transactions from IIO (P2P). Not used for coherent transacions. Uncacheable. (combine with any OPCODE umask)", \
     .ufilters[1] = 0x193ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCIWIF",\
     .udesc  = "PCIe write (full, non-allocating) - full line MMIO write transactions from IIO (P2P). Not used for coherent transacions. Uncacheable. (combine with any OPCODE umask)", \
     .ufilters[1] = 0x194ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCIITOM",\
     .udesc  = "PCIe write (allocating) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x19cULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCIRDCUR",\
     .udesc  = "PCIe read current (combine with any OPCODE umask)", \
     .ufilters[1] = 0x19eULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_WBMTOI",\
     .udesc  = "Request writeback modified invalidate line (combine with any OPCODE umask)", \
     .ufilters[1] = 0x1c4ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_WBMTOE",\
     .udesc  = "Request writeback modified set to exclusive (combine with any OPCODE umask)", \
     .ufilters[1] = 0x1c5ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_ITOM",\
     .udesc  = "Request invalidate line. Request exclusive ownership of the line  (combine with any OPCODE umask)", \
     .ufilters[1] = 0x1c8ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCINSRD",\
     .udesc  = "PCIe non-snoop read (combine with any OPCODE umask)", \
     .ufilters[1] = 0x1e4ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCINSWR",\
     .udesc  = "PCIe non-snoop write (partial) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x1e5ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }, \
   { .uname  = "OPC_PCINSWRF",\
     .udesc  = "PCIe non-snoop write (full) (combine with any OPCODE umask)", \
     .ufilters[1] = 0x1e6ULL << 20, \
     .uflags = INTEL_X86_NCOMBO, \
     .grpid = d, \
   }


static intel_x86_umask_t bdx_unc_c_llc_lookup[]={
	{ .uname = "ANY",
	  .ucode = 0x1100,
	  .udesc = "Cache Lookups -- Any Request",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
	  .grpid  = 0,
	},
	{ .uname = "DATA_READ",
	  .ucode = 0x300,
	  .udesc = "Cache Lookups -- Data Read Request",
	  .grpid  = 0,
	},
	{ .uname = "NID",
	  .ucode = 0x4100,
	  .udesc = "Cache Lookups -- Lookups that Match NID",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .grpid  = 1,
	  .uflags = INTEL_X86_GRP_DFL_NONE
	},
	{ .uname = "READ",
	  .ucode = 0x2100,
	  .udesc = "Cache Lookups -- Any Read Request",
	  .grpid  = 0,
	},
	{ .uname = "REMOTE_SNOOP",
	  .ucode = 0x900,
	  .udesc = "Cache Lookups -- External Snoop Request",
	  .grpid  = 0,
	},
	{ .uname = "WRITE",
	  .ucode = 0x500,
	  .udesc = "Cache Lookups -- Write Requests",
	  .grpid  = 0,
	},
	CBO_FILT_MESIFS(2),
};

static intel_x86_umask_t bdx_unc_c_llc_victims[]={
	{ .uname = "F_STATE",
	  .ucode = 0x800,
	  .udesc = "Lines in Forward state",
	  .grpid = 0,
	},
	{ .uname = "I_STATE",
	  .ucode = 0x400,
	  .udesc = "Lines in S State",
	  .grpid = 0,
	},
	{ .uname = "S_STATE",
	  .ucode = 0x400,
	  .udesc = "Lines in S state",
	  .grpid = 0,
	},
	{ .uname = "E_STATE",
	  .ucode = 0x200,
	  .udesc = "Lines in E state",
	  .grpid = 0,
	},
	{ .uname = "M_STATE",
	  .ucode = 0x100,
	  .udesc = "Lines in M state",
	  .grpid = 0,
	},
	{ .uname = "MISS",
	  .ucode = 0x1000,
	  .udesc = "Lines Victimized",
	  .grpid = 0,
	},
	{ .uname = "NID",
	  .ucode = 0x4000,
	  .udesc = "Lines Victimized -- Victimized Lines that Match NID",
	  .uflags = INTEL_X86_GRP_DFL_NONE,
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .grpid = 1,
	},
};

static intel_x86_umask_t bdx_unc_c_misc[]={
	{ .uname = "CVZERO_PREFETCH_MISS",
	  .ucode = 0x2000,
	  .udesc = "Cbo Misc -- DRd hitting non-M with raw CV=0",
	},
	{ .uname = "CVZERO_PREFETCH_VICTIM",
	  .ucode = 0x1000,
	  .udesc = "Cbo Misc -- Clean Victim with raw CV=0",
	},
	{ .uname = "RFO_HIT_S",
	  .ucode = 0x800,
	  .udesc = "Cbo Misc -- RFO HitS",
	},
	{ .uname = "RSPI_WAS_FSE",
	  .ucode = 0x100,
	  .udesc = "Cbo Misc -- Silent Snoop Eviction",
	},
	{ .uname = "STARTED",
	  .ucode = 0x400,
	  .udesc = "Cbo Misc -- ",
	},
	{ .uname = "WC_ALIASING",
	  .ucode = 0x200,
	  .udesc = "Cbo Misc -- Write Combining Aliasing",
	},
};

static intel_x86_umask_t bdx_unc_c_ring_ad_used[]={
	{ .uname = "ALL",
	  .ucode = 0xf00,
	  .udesc = "AD Ring In Use -- All",
	},
	{ .uname = "CCW",
	  .ucode = 0xc00,
	  .udesc = "AD Ring In Use -- Down",
	},
	{ .uname = "CW",
	  .ucode = 0x300,
	  .udesc = "AD Ring In Use -- Up",
	},
	{ .uname = "DOWN_EVEN",
	  .ucode = 0x400,
	  .udesc = "AD Ring In Use -- Down and Even",
	},
	{ .uname = "DOWN_ODD",
	  .ucode = 0x800,
	  .udesc = "AD Ring In Use -- Down and Odd",
	},
	{ .uname = "UP_EVEN",
	  .ucode = 0x100,
	  .udesc = "AD Ring In Use -- Up and Even",
	},
	{ .uname = "UP_ODD",
	  .ucode = 0x200,
	  .udesc = "AD Ring In Use -- Up and Odd",
	},
};

static intel_x86_umask_t bdx_unc_c_ring_ak_used[]={
	{ .uname = "ALL",
	  .ucode = 0xf00,
	  .udesc = "AK Ring In Use -- All",
	},
	{ .uname = "CCW",
	  .ucode = 0xc00,
	  .udesc = "AK Ring In Use -- Down",
	},
	{ .uname = "CW",
	  .ucode = 0x300,
	  .udesc = "AK Ring In Use -- Up",
	},
	{ .uname = "DOWN_EVEN",
	  .ucode = 0x400,
	  .udesc = "AK Ring In Use -- Down and Even",
	},
	{ .uname = "DOWN_ODD",
	  .ucode = 0x800,
	  .udesc = "AK Ring In Use -- Down and Odd",
	},
	{ .uname = "UP_EVEN",
	  .ucode = 0x100,
	  .udesc = "AK Ring In Use -- Up and Even",
	},
	{ .uname = "UP_ODD",
	  .ucode = 0x200,
	  .udesc = "AK Ring In Use -- Up and Odd",
	},
};

static intel_x86_umask_t bdx_unc_c_ring_bl_used[]={
	{ .uname = "ALL",
	  .ucode = 0xf00,
	  .udesc = "BL Ring in Use -- Down",
	},
	{ .uname = "CCW",
	  .ucode = 0xc00,
	  .udesc = "BL Ring in Use -- Down",
	},
	{ .uname = "CW",
	  .ucode = 0x300,
	  .udesc = "BL Ring in Use -- Up",
	},
	{ .uname = "DOWN_EVEN",
	  .ucode = 0x400,
	  .udesc = "BL Ring in Use -- Down and Even",
	},
	{ .uname = "DOWN_ODD",
	  .ucode = 0x800,
	  .udesc = "BL Ring in Use -- Down and Odd",
	},
	{ .uname = "UP_EVEN",
	  .ucode = 0x100,
	  .udesc = "BL Ring in Use -- Up and Even",
	},
	{ .uname = "UP_ODD",
	  .ucode = 0x200,
	  .udesc = "BL Ring in Use -- Up and Odd",
	},
};

static intel_x86_umask_t bdx_unc_c_ring_bounces[]={
	{ .uname = "AD",
	  .ucode = 0x100,
	  .udesc = "Number of LLC responses that bounced on the Ring. -- AD",
	},
	{ .uname = "AK",
	  .ucode = 0x200,
	  .udesc = "Number of LLC responses that bounced on the Ring. -- AK",
	},
	{ .uname = "BL",
	  .ucode = 0x400,
	  .udesc = "Number of LLC responses that bounced on the Ring. -- BL",
	},
	{ .uname = "IV",
	  .ucode = 0x1000,
	  .udesc = "Number of LLC responses that bounced on the Ring. -- Snoops of processors cachee.",
	},
};

static intel_x86_umask_t bdx_unc_c_ring_iv_used[]={
	{ .uname = "ANY",
	  .ucode = 0xf00,
	  .udesc = "BL Ring in Use -- Any",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
	},
	{ .uname = "DN",
	  .ucode = 0xc00,
	  .udesc = "BL Ring in Use -- Any",
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "DOWN",
	  .ucode = 0xcc00,
	  .udesc = "BL Ring in Use -- Down",
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "UP",
	  .ucode = 0x300,
	  .udesc = "BL Ring in Use -- Any",
	  .uflags = INTEL_X86_NCOMBO,
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_ext_starved[]={
	{ .uname = "IPQ",
	  .ucode = 0x200,
	  .udesc = "Ingress Arbiter Blocking Cycles -- IRQ",
	},
	{ .uname = "IRQ",
	  .ucode = 0x100,
	  .udesc = "Ingress Arbiter Blocking Cycles -- IPQ",
	},
	{ .uname = "ISMQ_BIDS",
	  .ucode = 0x800,
	  .udesc = "Ingress Arbiter Blocking Cycles -- ISMQ_BID",
	},
	{ .uname = "PRQ",
	  .ucode = 0x400,
	  .udesc = "Ingress Arbiter Blocking Cycles -- PRQ",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_inserts[]={
	{ .uname = "IPQ",
	  .ucode = 0x400,
	  .udesc = "Ingress Allocations -- IPQ",
	},
	{ .uname = "IRQ",
	  .ucode = 0x100,
	  .udesc = "Ingress Allocations -- IRQ",
	},
	{ .uname = "IRQ_REJ",
	  .ucode = 0x200,
	  .udesc = "Ingress Allocations -- IRQ Rejected",
	},
	{ .uname = "PRQ",
	  .ucode = 0x1000,
	  .udesc = "Ingress Allocations -- PRQ",
	},
	{ .uname = "PRQ_REJ",
	  .ucode = 0x2000,
	  .udesc = "Ingress Allocations -- PRQ",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_ipq_retry[]={
	{ .uname = "ADDR_CONFLICT",
	  .ucode = 0x400,
	  .udesc = "Probe Queue Retries -- Address Conflict",
	},
	{ .uname = "ANY",
	  .ucode = 0x100,
	  .udesc = "Probe Queue Retries -- Any Reject",
	  .uflags = INTEL_X86_DFL,
	},
	{ .uname = "FULL",
	  .ucode = 0x200,
	  .udesc = "Probe Queue Retries -- No Egress Credits",
	},
	{ .uname = "QPI_CREDITS",
	  .ucode = 0x1000,
	  .udesc = "Probe Queue Retries -- No QPI Credits",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_ipq_retry2[]={
	{ .uname = "AD_SBO",
	  .ucode = 0x100,
	  .udesc = "Probe Queue Retries -- No AD Sbo Credits",
	},
	{ .uname = "TARGET",
	  .ucode = 0x4000,
	  .udesc = "Probe Queue Retries -- Target Node Filter",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_irq_retry[]={
	{ .uname = "ADDR_CONFLICT",
	  .ucode = 0x400,
	  .udesc = "Ingress Request Queue Rejects -- Address Conflict",
	},
	{ .uname = "ANY",
	  .ucode = 0x100,
	  .udesc = "Ingress Request Queue Rejects -- Any Reject",
	  .uflags = INTEL_X86_DFL,
	},
	{ .uname = "FULL",
	  .ucode = 0x200,
	  .udesc = "Ingress Request Queue Rejects -- No Egress Credits",
	},
	{ .uname = "IIO_CREDITS",
	  .ucode = 0x2000,
	  .udesc = "Ingress Request Queue Rejects -- No IIO Credits",
	},
	{ .uname = "NID",
	  .ucode = 0x4000,
	  .udesc = "Ingress Request Queue Rejects -- ",
	},
	{ .uname = "QPI_CREDITS",
	  .ucode = 0x1000,
	  .udesc = "Ingress Request Queue Rejects -- No QPI Credits",
	},
	{ .uname = "RTID",
	  .ucode = 0x800,
	  .udesc = "Ingress Request Queue Rejects -- No RTIDs",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_irq_retry2[]={
	{ .uname = "AD_SBO",
	  .ucode = 0x100,
	  .udesc = "Ingress Request Queue Rejects -- No AD Sbo Credits",
	},
	{ .uname = "BL_SBO",
	  .ucode = 0x200,
	  .udesc = "Ingress Request Queue Rejects -- No BL Sbo Credits",
	},
	{ .uname = "TARGET",
	  .ucode = 0x4000,
	  .udesc = "Ingress Request Queue Rejects -- Target Node Filter",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_ismq_retry[]={
	{ .uname = "ANY",
	  .ucode = 0x100,
	  .udesc = "ISMQ Retries -- Any Reject",
	  .uflags = INTEL_X86_DFL,
	},
	{ .uname = "FULL",
	  .ucode = 0x200,
	  .udesc = "ISMQ Retries -- No Egress Credits",
	},
	{ .uname = "IIO_CREDITS",
	  .ucode = 0x2000,
	  .udesc = "ISMQ Retries -- No IIO Credits",
	},
	{ .uname = "NID",
	  .ucode = 0x4000,
	  .udesc = "ISMQ Retries -- ",
	},
	{ .uname = "QPI_CREDITS",
	  .ucode = 0x1000,
	  .udesc = "ISMQ Retries -- No QPI Credits",
	},
	{ .uname = "RTID",
	  .ucode = 0x800,
	  .udesc = "ISMQ Retries -- No RTIDs",
	},
	{ .uname = "WB_CREDITS",
	  .ucode = 0x8000,
	  .udesc = "ISMQ Retries -- ",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_ismq_retry2[]={
	{ .uname = "AD_SBO",
	  .ucode = 0x100,
	  .udesc = "ISMQ Request Queue Rejects -- No AD Sbo Credits",
	},
	{ .uname = "BL_SBO",
	  .ucode = 0x200,
	  .udesc = "ISMQ Request Queue Rejects -- No BL Sbo Credits",
	},
	{ .uname = "TARGET",
	  .ucode = 0x4000,
	  .udesc = "ISMQ Request Queue Rejects -- Target Node Filter",
	},
};

static intel_x86_umask_t bdx_unc_c_rxr_occupancy[]={
	{ .uname = "IPQ",
	  .ucode = 0x400,
	  .udesc = "Ingress Occupancy -- IPQ",
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "IRQ",
	  .ucode = 0x100,
	  .udesc = "Ingress Occupancy -- IRQ",
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "IRQ_REJ",
	  .ucode = 0x200,
	  .udesc = "Ingress Occupancy -- IRQ Rejected",
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "PRQ_REJ",
	  .ucode = 0x2000,
	  .udesc = "Ingress Occupancy -- PRQ Rejects",
	  .uflags = INTEL_X86_NCOMBO,
	},
};

static intel_x86_umask_t bdx_unc_c_sbo_credits_acquired[]={
	{ .uname = "AD",
	  .ucode = 0x100,
	  .udesc = "SBo Credits Acquired -- For AD Ring",
	},
	{ .uname = "BL",
	  .ucode = 0x200,
	  .udesc = "SBo Credits Acquired -- For BL Ring",
	},
};

static intel_x86_umask_t bdx_unc_c_sbo_credit_occupancy[]={
	{ .uname = "AD",
	  .ucode = 0x100,
	  .udesc = "SBo Credits Occupancy -- For AD Ring",
	},
	{ .uname = "BL",
	  .ucode = 0x200,
	  .udesc = "SBo Credits Occupancy -- For BL Ring",
	},
};

static intel_x86_umask_t bdx_unc_c_tor_inserts[]={
	{ .uname = "ALL",
	  .ucode = 0x800,
	  .udesc = "All",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "EVICTION",
	  .ucode = 0x400,
	  .udesc = "Evictions",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "LOCAL",
	  .ucode = 0x2800,
	  .udesc = "Local Memory",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "LOCAL_OPCODE",
	  .ucode = 0x2100,
	  .udesc = "Local Memory - Opcode Matched",
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "MISS_LOCAL",
	  .ucode = 0x2a00,
	  .udesc = "Misses to Local Memory",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "MISS_LOCAL_OPCODE",
	  .ucode = 0x2300,
	  .udesc = "Misses to Local Memory - Opcode Matched",
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "MISS_OPCODE",
	  .ucode = 0x300,
	  .udesc = "Miss Opcode Match",
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "MISS_REMOTE",
	  .ucode = 0x8a00,
	  .udesc = "Misses to Remote Memory",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "MISS_REMOTE_OPCODE",
	  .ucode = 0x8300,
	  .udesc = "Misses to Remote Memory - Opcode Matched",
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "NID_ALL",
	  .ucode = 0x4800,
	  .udesc = "NID Matched",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "NID_EVICTION",
	  .ucode = 0x4400,
	  .udesc = "NID Matched Evictions",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "NID_MISS_ALL",
	  .ucode = 0x4a00,
	  .udesc = "NID Matched Miss All",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "NID_MISS_OPCODE",
	  .ucode = 0x4300,
	  .udesc = "NID and Opcode Matched Miss",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "NID_OPCODE",
	  .ucode = 0x4100,
	  .udesc = "NID and Opcode Matched",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "NID_WB",
	  .ucode = 0x5000,
	  .udesc = "NID Matched Writebacks",
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "OPCODE",
	  .ucode = 0x100,
	  .udesc = "Opcode Match",
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "REMOTE",
	  .ucode = 0x8800,
	  .udesc = "Remote Memory",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "REMOTE_OPCODE",
	  .ucode = 0x8100,
	  .udesc = "Remote Memory - Opcode Matched",
	  .uflags = INTEL_X86_NCOMBO,
	  .grpid  = 0,
	},
	{ .uname = "WB",
	  .ucode = 0x1000,
	  .udesc = "Writebacks",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
 	CBO_FILT_OPC(1)
};

static intel_x86_umask_t bdx_unc_c_tor_occupancy[]={
	{ .uname = "ALL",
	  .ucode = 0x800,
	  .udesc = "Any",
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL | INTEL_X86_EXCL_GRP_GT,
	  .grpid  = 0,
	},
	{ .uname = "EVICTION",
	  .ucode = 0x400,
	  .udesc = "Evictions",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "LOCAL",
	  .ucode = 0x2800,
	  .udesc  = "Number of transactions in the TOR that are satisfied by locally homed memory",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "LOCAL_OPCODE",
	  .ucode = 0x2100,
	  .udesc = "Local Memory - Opcode Matched",
	  .grpid  = 0,
	},
	{ .uname = "MISS_ALL",
	  .ucode = 0xa00,
	  .udesc = "Miss All",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "MISS_LOCAL",
	  .ucode = 0x2a00,
	  .udesc  = "Number of miss transactions in the TOR that are satisfied by locally homed memory",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "MISS_LOCAL_OPCODE",
	  .ucode = 0x2300,
	  .udesc  = "Number of miss opcode-matched transactions inserted into the TOR that are satisfied by locally homed memory",
	  .grpid  = 0,
	},
	{ .uname = "MISS_OPCODE",
	  .ucode = 0x300,
	  .udesc  = "Number of miss transactions inserted into the TOR that match an opcode (must provide opc_* umask)",
	  .grpid  = 0,
	},
	{ .uname = "MISS_REMOTE_OPCODE",
	  .ucode = 0x8300,
	  .udesc  = "Number of miss opcode-matched transactions inserted into the TOR that are satisfied by remote caches or memory",
	  .grpid  = 0,
	},
	{ .uname = "NID_ALL",
	  .ucode = 0x4800,
	  .udesc  = "Number of NID-matched transactions inserted into the TOR (must provide nf=X modifier)",
	  .grpid  = 0,
	},
	{ .uname = "NID_EVICTION",
	  .ucode = 0x4400,
	  .udesc  = "Number of NID-matched eviction transactions inserted into the TOR (must provide nf=X modifier)",
	  .grpid  = 0,
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "NID_MISS_ALL",
	  .ucode = 0x4a00,
	  .udesc  = "Number of NID-matched miss transactions that were inserted into the TOR (must provide nf=X modifier)",
	  .grpid  = 0,
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "NID_MISS_OPCODE",
	  .ucode = 0x4300,
	  .udesc  = "Number of NID and opcode matched miss transactions inserted into the TOR (must provide opc_* umask and nf=X modifier)",
	  .grpid  = 0,
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "NID_OPCODE",
	  .ucode = 0x4100,
	  .udesc  = "Number of transactions inserted into the TOR that match a NID and opcode (must provide opc_* umask and nf=X modifier)",
	  .grpid  = 0,
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "NID_WB",
	  .ucode = 0x5000,
	  .udesc  = "Number of NID-matched write back transactions inserted into the TOR (must provide nf=X modifier)",
	  .grpid  = 0,
	  .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "OPCODE",
	  .ucode = 0x100,
	  .udesc  = "Number of transactions inserted into the TOR that match an opcode (must provide opc_* umask)",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "REMOTE",
	  .ucode = 0x8800,
	  .udesc  = "Number of transactions inserted into the TOR that are satisfied by remote caches or memory",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "REMOTE_OPCODE",
	  .ucode = 0x8100,
	  .udesc  = "Number of opcode-matched transactions inserted into the TOR that are satisfied by remote caches or memory",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO,
	},
	{ .uname = "WB",
	  .ucode = 0x1000,
	  .udesc  = "Number of write transactions inserted into the TOR",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	{ .uname = "MISS_REMOTE",
	  .ucode = 0x8a00,
	  .udesc  = "Number of miss transactions inserted into the TOR that are satisfied by remote caches or memory",
	  .grpid  = 0,
	  .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
	},
	CBO_FILT_OPC(1)
};

static intel_x86_umask_t bdx_unc_c_txr_ads_used[]={
	{ .uname = "AD",
	  .ucode = 0x100,
	  .udesc = "Onto AD Ring",
	},
	{ .uname = "AK",
	  .ucode = 0x200,
	  .udesc = "Onto AK Ring",
	},
	{ .uname = "BL",
	  .ucode = 0x400,
	  .udesc = "Onto BL Ring",
	},
};

static intel_x86_umask_t bdx_unc_c_txr_inserts[]={
	{ .uname = "AD_CACHE",
	  .ucode = 0x100,
	  .udesc = "Egress Allocations -- AD - Cachebo",
	},
	{ .uname = "AD_CORE",
	  .ucode = 0x1000,
	  .udesc = "Egress Allocations -- AD - Corebo",
	},
	{ .uname = "AK_CACHE",
	  .ucode = 0x200,
	  .udesc = "Egress Allocations -- AK - Cachebo",
	},
	{ .uname = "AK_CORE",
	  .ucode = 0x2000,
	  .udesc = "Egress Allocations -- AK - Corebo",
	},
	{ .uname = "BL_CACHE",
	  .ucode = 0x400,
	  .udesc = "Egress Allocations -- BL - Cacheno",
	},
	{ .uname = "BL_CORE",
	  .ucode = 0x4000,
	  .udesc = "Egress Allocations -- BL - Corebo",
	},
	{ .uname = "IV_CACHE",
	  .ucode = 0x800,
	  .udesc = "Egress Allocations -- IV - Cachebo",
	},
};


static intel_x86_entry_t intel_bdx_unc_c_pe[]={
  { .name   = "UNC_C_BOUNCE_CONTROL",
    .code   = 0xa,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_C_CLOCKTICKS",
    .code   = 0x0,
    .desc   = "Clock ticks",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_C_COUNTER0_OCCUPANCY",
    .code   = 0x1f,
    .desc   = "Since occupancy counts can only be captured in the Cbos 0 counter, this event allows a user to capture occupancy related information by filtering the Cb0 occupancy count captured in Counter 0. The filtering available is found in the control register - threshold, invert and edge detect.  E.g. setting threshold to 1 can effectively monitor how many cycles the monitored queue has an entry.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_C_FAST_ASSERTED",
    .code   = 0x9,
    .desc   = "Counts the number of cycles either the local distress or incoming distress signals are asserted.  Incoming distress includes both up and dn.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0x3,
  },
  { .name   = "UNC_C_LLC_LOOKUP",
    .code   = 0x34,
    .desc   = "Counts the number of times the LLC was accessed - this includes code, data, prefetches and hints coming from L2.  This has numerous filters available.  Note the non-standard filtering equation.  This event will count requests that lookup the cache multiple times with multiple increments.  One must ALWAYS set umask bit 0 and select a state or states to match.  Otherwise, the event will count nothing.  CBoGlCtrl[22:18] bits correspond to [FMESI] state.",
    .modmsk = BDX_UNC_CBO_NID_ATTRS,
    .flags  = INTEL_X86_NO_AUTOENCODE,
    .cntmsk = 0xf,
    .ngrp   = 3,
    .umasks = bdx_unc_c_llc_lookup,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_llc_lookup),
  },
  { .name   = "UNC_C_LLC_VICTIMS",
    .code   = 0x37,
    .desc   = "Counts the number of lines that were victimized on a fill.  This can be filtered by the state that the line was in.",
    .modmsk = BDX_UNC_CBO_NID_ATTRS,
    .flags  = INTEL_X86_NO_AUTOENCODE,
    .cntmsk = 0xf,
    .ngrp   = 2,
    .umasks = bdx_unc_c_llc_victims,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_llc_victims),
  },
  { .name   = "UNC_C_MISC",
    .code   = 0x39,
    .desc   = "Miscellaneous events in the Cbo.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_misc,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_misc),
  },
  { .name   = "UNC_C_RING_AD_USED",
    .code   = 0x1b,
    .desc   = "Counts the number of cycles that the AD ring is being used at this ring stop.  This includes when packets are passing by and when packets are being sunk, but does not include when packets are being sent from the ring stop.  We really have two rings in BDX -- a clockwise ring and a counter-clockwise ring.  On the left side of the ring, the UP direction is on the clockwise ring and DN is on the counter-clockwise ring.  On the right side of the ring, this is reversed.  The first half of the CBos are on the left side of the ring, and the 2nd half are on the right side of the ring.  In other words (for example), in a 4c part, Cbo 0 UP AD is NOT the same ring as CBo 2 UP AD because they are on opposite sides of the rhe ring.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_ring_ad_used,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_ring_ad_used),
  },
  { .name   = "UNC_C_RING_AK_USED",
    .code   = 0x1c,
    .desc   = "Counts the number of cycles that the AK ring is being used at this ring stop.  This includes when packets are passing by and when packets are being sunk, but does not include when packets are being sent from the ring stop.We really have two rings in BDX -- a clockwise ring and a counter-clockwise ring.  On the left side of the ring, the UP direction is on the clockwise ring and DN is on the counter-clockwise ring.  On the right side of the ring, this is reversed.  The first half of the CBos are on the left side of the ring, and the 2nd half are on the right side of the ring.  In other words (for example), in a 4c part, Cbo 0 UP AD is NOT the same ring as CBo 2 UP AD because they are on opposite sides of the rhe ring.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_ring_ak_used,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_ring_ak_used),
  },
  { .name   = "UNC_C_RING_BL_USED",
    .code   = 0x1d,
    .desc   = "Counts the number of cycles that the BL ring is being used at this ring stop.  This includes when packets are passing by and when packets are being sunk, but does not include when packets are being sent from the ring stop.We really have two rings in BDX -- a clockwise ring and a counter-clockwise ring.  On the left side of the ring, the UP direction is on the clockwise ring and DN is on the counter-clockwise ring.  On the right side of the ring, this is reversed.  The first half of the CBos are on the left side of the ring, and the 2nd half are on the right side of the ring.  In other words (for example), in a 4c part, Cbo 0 UP AD is NOT the same ring as CBo 2 UP AD because they are on opposite sides of the rhe ring.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_ring_bl_used,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_ring_bl_used),
  },
  { .name   = "UNC_C_RING_BOUNCES",
    .code   = 0x5,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_ring_bounces,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_ring_bounces),
  },
  { .name   = "UNC_C_RING_IV_USED",
    .code   = 0x1e,
    .desc   = "Counts the number of cycles that the IV ring is being used at this ring stop.  This includes when packets are passing by and when packets are being sunk, but does not include when packets are being sent from the ring stop.  There is only 1 IV ring in BDX  Therefore, if one wants to monitor the Even ring, they should select both UP_EVEN and DN_EVEN.  To monitor the Odd ring, they should select both UP_ODD and DN_ DN_ODD.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_ring_iv_used,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_ring_iv_used),
  },
  { .name   = "UNC_C_RING_SRC_THRTL",
    .code   = 0x7,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_C_RXR_EXT_STARVED",
    .code   = 0x12,
    .desc   = "Counts cycles in external starvation.  This occurs when one of the ingress queues is being starved by the other queues.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_ext_starved,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_ext_starved),
  },
  { .name   = "UNC_C_RXR_INSERTS",
    .code   = 0x13,
    .desc   = "Counts number of allocations per cycle into the specified Ingress queue.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_inserts,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_inserts),
  },
  { .name   = "UNC_C_RXR_IPQ_RETRY",
    .code   = 0x31,
    .desc   = "Number of times a snoop (probe) request had to retry.  Filters exist to cover some of the common cases retries.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_ipq_retry,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_ipq_retry),
  },
  { .name   = "UNC_C_RXR_IPQ_RETRY2",
    .code   = 0x28,
    .desc   = "Number of times a snoop (probe) request had to retry.  Filters exist to cover some of the common cases retries.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_ipq_retry2,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_ipq_retry2),
  },
  { .name   = "UNC_C_RXR_IRQ_RETRY",
    .code   = 0x32,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_irq_retry,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_irq_retry),
  },
  { .name   = "UNC_C_RXR_IRQ_RETRY2",
    .code   = 0x29,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_irq_retry2,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_irq_retry2),
  },
  { .name   = "UNC_C_RXR_ISMQ_RETRY",
    .code   = 0x33,
    .desc   = "Number of times a transaction flowing through the ISMQ had to retry.  Transaction pass through the ISMQ as responses for requests that already exist in the Cbo.  Some examples include: when data is returned or when snoop responses come back from the cores.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_ismq_retry,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_ismq_retry),
  },
  { .name   = "UNC_C_RXR_ISMQ_RETRY2",
    .code   = 0x2a,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_ismq_retry2,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_ismq_retry2),
  },
  { .name   = "UNC_C_RXR_OCCUPANCY",
    .code   = 0x11,
    .desc   = "Counts number of entries in the specified Ingress queue in each cycle.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0x1,
    .ngrp   = 1,
    .umasks = bdx_unc_c_rxr_occupancy,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_rxr_occupancy),
  },
  { .name   = "UNC_C_SBO_CREDITS_ACQUIRED",
    .code   = 0x3d,
    .desc   = "Number of Sbo credits acquired in a given cycle, per ring.  Each Cbo is assigned an Sbo it can communicate with.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_sbo_credits_acquired,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_sbo_credits_acquired),
  },
  { .name   = "UNC_C_SBO_CREDIT_OCCUPANCY",
    .code   = 0x3e,
    .desc   = "Number of Sbo credits in use in a given cycle, per ring.  Each Cbo is assigned an Sbo it can communicate with.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0x1,
    .ngrp   = 1,
    .umasks = bdx_unc_c_sbo_credit_occupancy,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_sbo_credit_occupancy),
  },
  { .name   = "UNC_C_TOR_INSERTS",
    .code   = 0x35,
    .desc   = "Counts the number of entries successfully inserted into the TOR that match qualifications specified by the subevent.  There are a number of subevent filters but only a subset of the subevent combinations are valid.  Subevents that require an opcode or NID match require the Cn_MSR_PMON_BOX_FILTER.{opc, nid} field to be set.  If, for example, one wanted to count DRD Local Misses, one should select MISS_OPC_MATCH and set Cn_MSR_PMON_BOX_FILTER.opc to DRD (0x182).",
    .modmsk = BDX_UNC_CBO_NID_ATTRS | _SNBEP_UNC_ATTR_ISOC | _SNBEP_UNC_ATTR_NC,
    .flags  = INTEL_X86_NO_AUTOENCODE,
    .cntmsk = 0xf,
    .ngrp   = 2,
    .umasks = bdx_unc_c_tor_inserts,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_tor_inserts),
  },
  { .name   = "UNC_C_TOR_OCCUPANCY",
    .code   = 0x36,
    .desc   = "For each cycle, this event accumulates the number of valid entries in the TOR that match qualifications specified by the subevent.  There are a number of subevent filters but only a subset of the subevent combinations are valid.  Subevents that require an opcode or NID match require the Cn_MSR_PMON_BOX_FILTER.{opc, nid} field to be set.  If, for example, one wanted to count DRD Local Misses, one should select MISS_OPC_MATCH and set Cn_MSR_PMON_BOX_FILTER.opc to DRD (0x182).",
    .modmsk = BDX_UNC_CBO_NID_ATTRS | _SNBEP_UNC_ATTR_ISOC | _SNBEP_UNC_ATTR_NC,
    .flags  = INTEL_X86_NO_AUTOENCODE,
    .cntmsk = 0x1,
    .ngrp   = 2,
    .umasks = bdx_unc_c_tor_occupancy,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_tor_occupancy),
  },
  { .name   = "UNC_C_TXR_ADS_USED",
    .code   = 0x4,
    .desc   = "TBD",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_txr_ads_used,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_txr_ads_used),
  },
  { .name   = "UNC_C_TXR_INSERTS",
    .code   = 0x2,
    .desc   = "Number of allocations into the Cbo Egress.  The Egress is used to queue up requests destined for the ring.",
    .modmsk = BDX_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = bdx_unc_c_txr_inserts,
    .numasks= LIBPFM_ARRAY_SIZE(bdx_unc_c_txr_inserts),
  },
};

