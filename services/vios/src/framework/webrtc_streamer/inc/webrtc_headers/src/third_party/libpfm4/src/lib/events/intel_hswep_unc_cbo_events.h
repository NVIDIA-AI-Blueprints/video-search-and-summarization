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
 * PMU: hswep_unc_cbo (Intel Haswell-EP C-Box uncore PMU)
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

static const intel_x86_umask_t hswep_unc_c_llc_lookup[]={
   { .uname  = "DATA_READ",
     .udesc  = "Data read requests",
     .grpid  = 0,
     .ucode = 0x300,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "WRITE",
     .udesc  = "Write requests. Includes all write transactions (cached, uncached)",
     .grpid  = 0,
     .ucode = 0x500,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "REMOTE_SNOOP",
     .udesc  = "External snoop request",
     .grpid  = 0,
     .ucode = 0x900,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "ANY",
     .udesc  = "Any request",
     .grpid  = 0,
     .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
     .ucode = 0x1100,
   },
   { .uname  = "NID",
     .udesc  = "Match a given RTID destination NID (must provide nf=X modifier)",
     .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
     .grpid  = 1,
     .ucode = 0x4100,
     .uflags = INTEL_X86_GRP_DFL_NONE
   },
   CBO_FILT_MESIFS(2),
};

static const intel_x86_umask_t hswep_unc_c_llc_victims[]={
   { .uname  = "STATE_M",
     .udesc  = "Lines in M state",
     .ucode = 0x100,
     .grpid  = 0,
   },
   { .uname  = "STATE_E",
     .udesc  = "Lines in E state",
     .ucode = 0x200,
     .grpid  = 0,
   },
   { .uname  = "STATE_S",
     .udesc  = "Lines in S state",
     .ucode = 0x400,
     .grpid  = 0,
   },
   { .uname  = "STATE_F",
     .udesc  = "Lines in F state",
     .ucode = 0x800,
     .grpid  = 0,
   },
   { .uname  = "MISS",
     .udesc  = "TBD",
     .ucode = 0x1000,
     .grpid  = 0,
   },
   { .uname  = "NID",
     .udesc  = "Victimized Lines matching the NID filter (must provide nf=X modifier)",
     .ucode = 0x4000,
     .uflags = INTEL_X86_GRP_DFL_NONE,
     .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
     .grpid  = 1,
   },
};

static const intel_x86_umask_t hswep_unc_c_ring_ad_used[]={
   { .uname  = "UP_EVEN",
     .udesc  = "Up and Even ring polarity filter",
     .ucode = 0x100,
   },
   { .uname  = "UP_ODD",
     .udesc  = "Up and odd ring polarity filter",
     .ucode = 0x200,
   },
   { .uname  = "DOWN_EVEN",
     .udesc  = "Down and even ring polarity filter",
     .ucode = 0x400,
   },
   { .uname  = "DOWN_ODD",
     .udesc  = "Down and odd ring polarity filter",
     .ucode = 0x800,
   },
   { .uname  = "UP",
     .udesc  = "Up ring polarity filter",
     .ucode = 0x3300,
   },
   { .uname  = "DOWN",
     .udesc  = "Down ring polarity filter",
     .ucode = 0xcc00,
   },
   { .uname  = "ALL",
     .udesc  = "up or down ring polarity filter",
     .ucode = 0xcc00,
   },
};

static const intel_x86_umask_t hswep_unc_c_ring_bounces[]={
   { .uname  = "AD_IRQ",
     .udesc  = "TBD",
     .ucode = 0x200,
   },
   { .uname  = "AK",
     .udesc  = "Acknowledgments to core",
     .ucode = 0x400,
   },

   { .uname  = "BL",
     .udesc  = "Data responses to core",
     .ucode = 0x800,
   },
   { .uname  = "IV",
     .udesc  = "Snoops of processor cache",
     .ucode = 0x1000,
   },
};

static const intel_x86_umask_t hswep_unc_c_ring_iv_used[]={
   { .uname  = "ANY",
     .udesc  = "Any filter",
     .ucode = 0x0f00,
     .uflags = INTEL_X86_DFL,
   },
   { .uname  = "UP",
     .udesc  = "Filter on any up polarity",
     .ucode = 0x0300,
   },
   { .uname  = "DN",
     .udesc  = "Filter on any up polarity",
     .ucode = 0x0c00,
   },
   { .uname  = "DOWN",
     .udesc  = "Filter on any down polarity",
     .ucode = 0xcc00,
   },
};

static const intel_x86_umask_t hswep_unc_c_rxr_ext_starved[]={
   { .uname  = "IRQ",
     .udesc  = "Irq externally starved, therefore blocking the IPQ",
     .ucode = 0x100,
   },
   { .uname  = "IPQ",
     .udesc  = "IPQ externally starved, therefore blocking the IRQ",
     .ucode = 0x200,
   },
   { .uname  = "PRQ",
     .udesc  = "IRQ is blocking the ingress queue and causing starvation",
     .ucode = 0x400,
   },
   { .uname  = "ISMQ_BIDS",
     .udesc  = "Number of time the ISMQ bids",
     .ucode = 0x800,
   },
};

static const intel_x86_umask_t hswep_unc_c_rxr_inserts[]={
  { .uname = "IRQ",
    .udesc  = "IRQ",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "IRQ_REJECTED",
    .udesc  = "IRQ rejected",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "IPQ",
    .udesc  = "IPQ",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "PRQ",
    .udesc  = "PRQ",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "PRQ_REJECTED",
    .udesc  = "PRQ rejected",
    .ucode  = 0x2000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_rxr_ipq_retry[]={
  { .uname = "ADDR_CONFLICT",
    .udesc  = "Address conflict",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "ANY",
    .udesc  = "Any Reject",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
  { .uname = "FULL",
    .udesc  = "No Egress credits",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "QPI_CREDITS",
    .udesc  = "No QPI credits",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_rxr_ipq_retry2[]={
  { .uname = "AD_SBO",
    .udesc  = "Count number of time that a request from the IPQ was retried because it lacked credits to send an AD packet to SBO",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "TARGET",
    .udesc  = "Count number of times that a request from the IPQ was retried filtered by the target NodeId",
    .ucode  = 0x100,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_rxr_irq_retry[]={
  { .uname = "ADDR_CONFLICT",
    .udesc  = "Address conflict",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "ANY",
    .udesc  = "Any reject",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
  { .uname = "FULL",
    .udesc  = "No Egress credits",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "QPI_CREDITS",
    .udesc  = "No QPI credits",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "RTID",
    .udesc  = "No RTIDs",
    .ucode  = 0x800,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "IIO_CREDITS",
    .udesc  = "No IIO Credits",
    .ucode  = 0x2000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_rxr_irq_retry2[]={
  { .uname = "AD_SBO",
    .udesc  = "Count number of time that a request from the IRQ was retried because it lacked credits to send an AD packet to SBO",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL_SBO",
    .udesc  = "Count number of time that a request from the IRQ was retried because it lacked credits to send an BL packet to SBO",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "TARGET",
    .udesc  = "Count number of times that a request from the IRQ was retried filtered by the target NodeId",
    .ucode  = 0x100,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_rxr_ismq_retry[]={
  { .uname = "ANY",
    .udesc  = "Any reject",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
  { .uname = "FULL",
    .udesc  = "No Egress credits",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "IIO_CREDITS",
    .udesc  = "No IIO credits",
    .ucode  = 0x2000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "QPI_CREDITS",
    .udesc  = "NO QPI credits",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "RTID",
    .udesc  = "No RTIDs",
    .ucode  = 0x800,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "WB_CREDITS",
    .udesc  = "No WB credits",
    .ucode  = 0x8000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_rxr_ismq_retry2[]={
  { .uname = "AD_SBO",
    .udesc  = "Count number of time that a request from the ISMQ was retried because it lacked credits to send an AD packet to SBO",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL_SBO",
    .udesc  = "Count number of time that a request from the ISMQ was retried because it lacked credits to send an BL packet to SBO",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "TARGET",
    .udesc  = "Count number of times that a request from the ISMQ was retried filtered by the target NodeId",
    .ucode  = 0x100,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_tor_inserts[]={
  { .uname = "OPCODE",
    .udesc  = "Number of transactions inserted into the TOR that match an opcode (must provide opc_* umask)",
    .ucode  = 0x100,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "MISS_OPCODE",
    .udesc  = "Number of miss transactions inserted into the TOR that match an opcode (must provide opc_* umask)",
    .ucode  = 0x300,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "EVICTION",
    .udesc  = "Number of Evictions transactions inserted into TOR",
    .ucode  = 0x400,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "ALL",
    .udesc  = "Number of transactions inserted in TOR",
    .ucode  = 0x800,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "WB",
    .udesc  = "Number of write transactions inserted into the TOR",
    .ucode  = 0x1000,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "LOCAL_OPCODE",
    .udesc  = "Number of opcode-matched transactions inserted into the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2100,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "MISS_LOCAL_OPCODE",
    .udesc  = "Number of miss opcode-matched transactions inserted into the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2300,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "LOCAL",
    .udesc  = "Number of transactions inserted into the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2800,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "MISS_LOCAL",
    .udesc  = "Number of miss transactions inserted into the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2a00,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_OPCODE",
    .udesc  = "Number of transactions inserted into the TOR that match a NID and opcode (must provide opc_* umask and nf=X modifier)",
    .ucode  = 0x4100,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "NID_MISS_OPCODE",
    .udesc  = "Number of NID and opcode matched miss transactions inserted into the TOR (must provide opc_* umask and nf=X modifier)",
    .ucode  = 0x4300,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "NID_EVICTION",
    .udesc  = "Number of NID-matched eviction transactions inserted into the TOR (must provide nf=X modifier)",
    .ucode  = 0x4400,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_ALL",
    .udesc  = "Number of NID-matched transactions inserted into the TOR (must provide nf=X modifier)",
    .ucode  = 0x4800,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_MISS_ALL",
    .udesc  = "Number of NID-matched miss transactions that were inserted into the TOR (must provide nf=X modifier)",
    .ucode  = 0x4a00,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_WB",
    .udesc  = "Number of NID-matched write back transactions inserted into the TOR (must provide nf=X modifier)",
    .ucode  = 0x5000,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "REMOTE_OPCODE",
    .udesc  = "Number of opcode-matched transactions inserted into the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8100,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "MISS_REMOTE_OPCODE",
    .udesc  = "Number of miss opcode-matched transactions inserted into the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8300,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "REMOTE",
    .udesc  = "Number of transactions inserted into the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8800,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "MISS_REMOTE",
    .udesc  = "Number of miss transactions inserted into the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8a00,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  CBO_FILT_OPC(1)
};

static const intel_x86_umask_t hswep_unc_c_tor_occupancy[]={
  { .uname = "OPCODE",
    .udesc  = "Number of TOR entries that match an opcode (must provide opc_* umask)",
    .ucode  = 0x100,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "MISS_OPCODE",
    .udesc  = "Number of TOR entries that match a NID and an opcode (must provide opc_* umask)",
    .ucode  = 0x300,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "EVICTION",
    .udesc  = "Number of outstanding eviction transactions in the TOR",
    .ucode  = 0x400,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "ALL",
    .udesc  = "All valid TOR entries",
    .ucode  = 0x800,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "MISS_ALL",
    .udesc  = "Number of outstanding miss requests in the TOR",
    .ucode  = 0xa00,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "WB",
    .udesc  = "Number of write transactions in the TOR. Does not include RFO, but actual operations that contain data being sent from the core",
    .ucode  = 0x1000,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "LOCAL_OPCODE",
    .udesc  = "Number of opcode-matched transactions in the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2100,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "MISS_LOCAL_OPCODE",
    .udesc  = "Number of miss opcode-matched transactions in the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2300,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "LOCAL",
    .udesc  = "Number of transactions in the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2800,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "MISS_LOCAL",
    .udesc  = "Number of miss transactions in the TOR that are satisfied by locally homed memory",
    .ucode  = 0x2a00,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_OPCODE",
    .udesc  = "Number of NID-matched TOR entries that an opcode (must provide nf=X modifier and opc_* umask)",
    .ucode  = 0x4100,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "NID_MISS_OPCODE",
    .udesc  = "Number of NID-matched outstanding miss requests in the TOR that an opcode (must provide nf=X modifier and opc_* umask)",
    .ucode  = 0x4300,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "NID_EVICTION",
    .udesc  = "Number of NID-matched outstanding requests in the TOR (must provide a nf=X modifier)",
    .ucode  = 0x4400,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_ALL",
    .udesc  = "Number of NID-matched outstanding requests in the TOR (must provide nf=X modifier)",
    .ucode  = 0x4800,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_MISS_ALL",
    .udesc  = "Number of NID-matched outstanding miss requests in the TOR (must provide a nf=X modifier)",
    .ucode  = 0x4a00,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "NID_WB",
    .udesc  = "Number of NID-matched write transactions in the TOR (must provide a nf=X modifier)",
    .ucode  = 0x5000,
    .grpid  = 0,
    .umodmsk_req = _SNBEP_UNC_ATTR_NF1,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "REMOTE_OPCODE",
    .udesc  = "Number of opcode-matched transactions in the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8100,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "MISS_REMOTE_OPCODE",
    .udesc  = "Number of miss opcode-matched transactions in the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8300,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "REMOTE",
    .udesc  = "Number of transactions in the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8800,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  { .uname = "MISS_REMOTE",
    .udesc  = "Number of miss transactions inserted into the TOR that are satisfied by remote caches or memory",
    .ucode  = 0x8a00,
    .grpid  = 0,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_EXCL_GRP_GT,
  },
  CBO_FILT_OPC(1)
};

static const intel_x86_umask_t hswep_unc_c_txr_inserts[]={
  { .uname = "AD_CACHE",
    .udesc  = "Counts the number of ring transactions from Cachebo to AD ring",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "AK_CACHE",
    .udesc  = "Counts the number of ring transactions from Cachebo to AK ring",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL_CACHE",
    .udesc  = "Counts the number of ring transactions from Cachebo to BL ring",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "IV_CACHE",
    .udesc  = "Counts the number of ring transactions from Cachebo ton IV ring",
    .ucode  = 0x800,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "AD_CORE",
    .udesc  = "Counts the number of ring transactions from Corebo to AD ring",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "AK_CORE",
    .udesc  = "Counts the number of ring transactions from Corebo to AK ring",
    .ucode  = 0x2000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL_CORE",
    .udesc  = "Counts the number of ring transactions from Corebo to BL ring",
    .ucode  = 0x4000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_txr_ads_used[]={
  { .uname = "AD",
    .udesc  = "onto AD ring",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "AK",
    .udesc  = "Onto AK ring",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL",
    .udesc  = "Onto BL ring",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  }
};

static const intel_x86_umask_t hswep_unc_c_misc[]={
  { .uname = "RSPI_WAS_FSE",
    .udesc  = "Counts the number of times when a SNoop hit in FSE states and triggered a silent eviction. This is useful because this information is lost in the PRE encodings",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "WC_ALIASING",
    .udesc  = "Counts the number of times a USWC write (WCIL(F)) transaction hits in the LLC in M state, triggering a WBMTOI followed by the USWC write. This occurs when there is WC aliasing",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "STARTED",
    .udesc  = "TBD",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "RFO_HIT_S",
    .udesc  = "Counts the number of times that an RFO hits in S state. This is useful for determining if it might be good for a workload to use RSPIWB instead of RSPSWB",
    .ucode  = 0x800,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "CVZERO_PREFETCH_VICTIM",
    .udesc  = "Counts the number of clean victims with raw CV=0 (core valid)",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "CVZERO_PREFETCH_MISS",
    .udesc  = "Counts the number of Demand Data Read requests hitting non-modified state lines with raw CV=0 (core valid)",
    .ucode  = 0x2000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_c_sbo_credits_acquired[]={
  { .uname = "AD",
    .udesc  = "for AD ring",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL",
    .udesc  = "for BL ring",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  }
};

static const intel_x86_entry_t intel_hswep_unc_c_pe[]={
  { .name   = "UNC_C_CLOCKTICKS",
    .desc   = "C-box Uncore clockticks",
    .modmsk = 0x0,
    .cntmsk = 0xf,
    .code = 0x00,
    .flags = INTEL_X86_FIXED,
  },
  { .name   = "UNC_C_COUNTER0_OCCUPANCY",
    .desc   = "Counter 0 occupancy. Counts the occupancy related information by filtering CB0 occupancy count captured in counter 0.",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xe,
    .code = 0x1f,
  },
  { .name   = "UNC_C_LLC_LOOKUP",
    .desc   = "Cache lookups",
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS,
    .cntmsk = 0xf,
    .code = 0x34,
    .ngrp = 3,
    .flags = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_llc_lookup),
    .umasks = hswep_unc_c_llc_lookup,
  },
  { .name   = "UNC_C_LLC_VICTIMS",
    .desc   = "Lines victimized",
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS,
    .cntmsk = 0xf,
    .code = 0x37,
    .flags = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_llc_victims),
    .ngrp = 2,
    .umasks = hswep_unc_c_llc_victims,
  },
  { .name   = "UNC_C_MISC",
    .desc   = "Miscellaneous C-Box events",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x39,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_misc),
    .ngrp = 1,
    .umasks = hswep_unc_c_misc,
  },
  { .name   = "UNC_C_RING_AD_USED",
    .desc   = "Address ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1b,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_ring_ad_used),
    .ngrp = 1,
    .umasks = hswep_unc_c_ring_ad_used,
  },
  { .name   = "UNC_C_RING_AK_USED",
    .desc   = "Acknowledgement ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1c,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_ring_ad_used), /* identical to RING_AD_USED */
    .ngrp = 1,
    .umasks = hswep_unc_c_ring_ad_used,
  },
  { .name   = "UNC_C_RING_BL_USED",
    .desc   = "Bus or Data ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1d,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_ring_ad_used), /* identical to RING_AD_USED */
    .ngrp = 1,
    .umasks = hswep_unc_c_ring_ad_used,
  },
  { .name   = "UNC_C_RING_BOUNCES",
    .desc   = "Number of LLC responses that bounced in the ring",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x05,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_ring_bounces),
    .ngrp = 1,
    .umasks = hswep_unc_c_ring_bounces,
  },
  { .name   = "UNC_C_FAST_ASSERTED",
    .desc   = "Number of cycles in which the local distress or incoming distress signals are asserted (FaST). Incoming distress includes both up and down",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0x3,
    .code = 0x09,
  },
  { .name   = "UNC_C_BOUNCE_CONTROL",
    .desc   = "Bounce control",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x0a,
  },
  { .name   = "UNC_C_RING_IV_USED",
    .desc   = "Invalidate ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1e,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_ring_iv_used),
    .ngrp = 1,
    .umasks = hswep_unc_c_ring_iv_used,
  },
  { .name   = "UNC_C_RING_SRC_THRTL",
    .desc   = "TDB",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x07,
  },
  { .name   = "UNC_C_RXR_EXT_STARVED",
    .desc   = "Ingress arbiter blocking cycles",
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x12,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_ext_starved),
    .ngrp = 1,
    .umasks = hswep_unc_c_rxr_ext_starved,
  },
  { .name = "UNC_C_RXR_INSERTS",
    .desc = "Ingress Allocations",
    .code = 0x13,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_inserts),
    .umasks  = hswep_unc_c_rxr_inserts
  },
  { .name = "UNC_C_RXR_IPQ_RETRY",
    .desc = "Probe Queue Retries",
    .code = 0x31,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_ipq_retry),
    .umasks  = hswep_unc_c_rxr_ipq_retry
  },
  { .name = "UNC_C_RXR_IPQ_RETRY2",
    .desc = "Probe Queue Retries",
    .code = 0x28,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS,
    .flags = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_ipq_retry2),
    .umasks  = hswep_unc_c_rxr_ipq_retry2
  },
  { .name = "UNC_C_RXR_IRQ_RETRY",
    .desc = "Ingress Request Queue Rejects",
    .code = 0x32,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_irq_retry),
    .umasks  = hswep_unc_c_rxr_irq_retry
  },
  { .name = "UNC_C_RXR_IRQ_RETRY2",
    .desc = "Ingress Request Queue Rejects",
    .code = 0x29,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS,
    .flags = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_irq_retry2),
    .umasks  = hswep_unc_c_rxr_irq_retry2
  },
  { .name = "UNC_C_RXR_ISMQ_RETRY",
    .desc = "ISMQ Retries",
    .code = 0x33,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_ismq_retry),
    .umasks  = hswep_unc_c_rxr_ismq_retry
  },
  { .name = "UNC_C_RXR_ISMQ_RETRY2",
    .desc = "ISMQ Retries",
    .code = 0x2a,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS,
    .flags = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_ismq_retry2),
    .umasks  = hswep_unc_c_rxr_ismq_retry2
  },
  { .name = "UNC_C_RXR_OCCUPANCY",
    .desc = "Ingress Occupancy",
    .code = 0x11,
    .cntmsk = 0x1,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_rxr_inserts),
    .umasks  = hswep_unc_c_rxr_inserts, /* identical to hswep_unc_c_rxr_inserts */
  },
  { .name = "UNC_C_TOR_INSERTS",
    .desc = "TOR Inserts",
    .code = 0x35,
    .cntmsk = 0xf,
    .ngrp = 2,
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS | _SNBEP_UNC_ATTR_ISOC | _SNBEP_UNC_ATTR_NC,
    .flags  = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_tor_inserts),
    .umasks  = hswep_unc_c_tor_inserts
  },
  { .name = "UNC_C_TOR_OCCUPANCY",
    .desc = "TOR Occupancy",
    .code = 0x36,
    .cntmsk = 0x1,
    .ngrp = 2,
    .modmsk = HSWEP_UNC_CBO_NID_ATTRS | _SNBEP_UNC_ATTR_ISOC | _SNBEP_UNC_ATTR_NC,
    .flags  = INTEL_X86_NO_AUTOENCODE,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_tor_occupancy),
    .umasks  = hswep_unc_c_tor_occupancy
  },
  { .name = "UNC_C_TXR_ADS_USED",
    .desc = "Egress events",
    .code = 0x04,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_txr_ads_used),
    .umasks  = hswep_unc_c_txr_ads_used
  },
  { .name = "UNC_C_TXR_INSERTS",
    .desc = "Egress allocations",
    .code = 0x02,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_txr_inserts),
    .umasks  = hswep_unc_c_txr_inserts
  },
  { .name = "UNC_C_SBO_CREDITS_ACQUIRED",
    .desc = "SBO credits acquired",
    .code = 0x3d,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_sbo_credits_acquired),
    .umasks  = hswep_unc_c_sbo_credits_acquired
  },
  { .name = "UNC_C_SBO_CREDITS_OCCUPANCY",
    .desc = "SBO credits occupancy",
    .code = 0x3e,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_CBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_c_sbo_credits_acquired), /* shared */
    .umasks  = hswep_unc_c_sbo_credits_acquired
  },
};
