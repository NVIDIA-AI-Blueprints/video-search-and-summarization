/*
 * Copyright (c) 2017 Google LLC
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
 * PMU: skx_unc_iio
 */

#define FC_MASK(g) \
   { .uname  = "FC_POSTED_REQ",\
     .udesc  = "Posted requests",\
     .ucode  = 0x100ULL << 36,\
     .grpid = g, \
   },\
   { .uname  = "FC_NON_POSTED_REQ",\
     .udesc  = "Non-Posted requests",\
     .ucode  = 0x200ULL << 36,\
     .grpid = g, \
   },\
   { .uname  = "FC_CMPL",\
     .udesc  = "Completion requests",\
     .ucode  = 0x400ULL << 36,\
     .grpid = g, \
   },\
   { .uname  = "FC_ANY", \
     .udesc  = "Any type of requests",\
     .uequiv = "FC_POSTED_REQ:FC_NON_POSTED_REQ:FC_CMPL",\
     .uflags =INTEL_X86_NCOMBO | INTEL_X86_DFL,\
     .ucode  = 0x700ULL << 36,\
     .grpid = g, \
   }

#define CH_PORT_MASK(g) \
   { .uname  = "CH_P_PCIE_PORT0",\
     .udesc  = "PCIe Port 0",\
     .ucode  = 0x100,\
     .grpid = g, \
   },\
   { .uname  = "CH_P_PCIE_PORT1",\
     .udesc  = "PCIe Port 1",\
     .ucode  = 0x200,\
     .grpid = g, \
   },\
   { .uname  = "CH_P_PCIE_PORT2",\
     .udesc  = "PCIe Port 2",\
     .ucode  = 0x400,\
     .grpid = g, \
   },\
   { .uname  = "CH_P_PCIE_PORT3",\
     .udesc  = "PCIe Port 3",\
     .ucode  = 0x800,\
     .grpid = g, \
   },\


#define CH_P_MASK(g) \
   CH_PORT_MASK(g),\
   { .uname  = "CH_P_INTEL_VTD",\
     .udesc  = "Intel VT-d",\
     .ucode  = 0x1000,\
     .grpid = g, \
   },\
   { .uname  = "CH_P_ANY", \
     .udesc  = "Any type of requests",\
     .uequiv = "FC_P_POSTED_REQ:FC_P_NON_POSTED_REQ:FC_P_CMPL",\
     .uflags =INTEL_X86_NCOMBO | INTEL_X86_DFL,\,\
     .ucode  = 0x1f00,\
     .grpid = g, \
   }

/* not yet used */
#define CH_C_MASK(g) \
   { .uname  = "CH_C_CBDMA",\
     .udesc  = "CBDMA",\
     .ucode  = 0x100,\
     .grpid = g, \
   },\
   { .uname  = "CH_C_DMI_VC0",\
     .udesc  = "DMI VC0",\
     .ucode  = 0x200,\
     .grpid = g, \
   },\
   { .uname  = "CH_C_DMI_VC1",\
     .udesc  = "DMI VC1",\
     .ucode  = 0x400,\
     .grpid = g, \
   },\
   { .uname  = "CH_C_DMI_VCN",\
     .udesc  = "DMI VCn",\
     .ucode  = 0x800,\
     .grpid = g, \
   },\
   { .uname  = "CH_C_INTEL_VTD_NO_ISOCH",\
     .udesc  = "Intel VT-d non-isochronous",\
     .ucode  = 0x1000,\
     .grpid = g, \
   },\
   { .uname  = "CH_C_INTEL_VTD_ISOCH",\
     .udesc  = "Intel VT-d non-isochronous",\
     .ucode  = 0x2000,\
     .grpid = g, \
   },\
   { .uname  = "CH_C_ANY", \
     .udesc  = "Any type of requests",\
     .uequiv = "CH_C_CBDMA:CH_C_DMI_VC0:CH_C_DMI_VC1:CH_C_DMI_VCN:CH_C_INTEL_VTD_NO_ISOCH:CH_C_INTEL_VTD_ISOCH",\
     .uflags =INTEL_X86_NCOMBO,\
     .ucode  = 0x3f00,\
     .grpid = g, \
   }

static intel_x86_umask_t skx_unc_io_comp_buf_inserts[]={
	{ .uname = "PORT0",
	  .ucode = 0x400ULL | 1ULL << 36,
	  .udesc = "PCIe Completion Buffer Inserts -- Port 0",
	  .grpid = 0,
	},
	{ .uname = "PORT1",
	  .ucode = 0x400ULL | 2ULL << 36,
	  .udesc = "PCIe Completion Buffer Inserts -- Port 1",
	  .grpid = 0,
	},
	{ .uname = "PORT2",
	  .ucode = 0x400ULL | 4ULL << 36,
	  .udesc = "PCIe Completion Buffer Inserts -- Port 2",
	  .grpid = 0,
	},
	{ .uname = "PORT3",
	  .ucode = 0x400ULL | 8ULL << 36,
	  .udesc = "PCIe Completion Buffer Inserts -- Port 3",
	  .grpid = 0,
	},
	{ .uname = "ANY_PORT",
	  .ucode = 0x400ULL | 0xfULL << 36,
	  .udesc = "PCIe Completion Buffer Inserts -- Any port",
	  .uequiv= "PORT0:PORT1:PORT2:PORT3",
	  .uflags= INTEL_X86_DFL | INTEL_X86_NCOMBO,
	  .grpid = 0,
	},
	FC_MASK(1)
};

static intel_x86_umask_t skx_unc_io_data_req_by_cpu[]={
	{ .uname = "CFG_READ_PART0",
	  .ucode = 0x4000ULL | 0x1ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards PCICFG spacce",
	},
	{ .uname = "CFG_READ_PART1",
	  .ucode = 0x4000ULL | 0x2ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards PCICFG spacce",
	},
	{ .uname = "CFG_READ_PART2",
	  .ucode = 0x4000ULL | 0x4ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards PCICFG spacce",
	},
	{ .uname = "CFG_READ_PART3",
	  .ucode = 0x4000ULL | 0x8ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards PCICFG spacce",
	},
	{ .uname = "CFG_READ_VTD0",
	  .ucode = 0x4000ULL | 0x10ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards PCICFG spacce",
	},
	{ .uname = "CFG_READ_VTD1",
	  .ucode = 0x4000ULL | 0x20ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards PCICFG spacce",
	},
	{ .uname = "CFG_WRITE_PART0",
	  .ucode = 0x1000ULL | 0x1ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards PCICFG spacce",
	},
	{ .uname = "CFG_WRITE_PART1",
	  .ucode = 0x1000ULL | 0x2ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards PCICFG spacce",
	},
	{ .uname = "CFG_WRITE_PART2",
	  .ucode = 0x1000ULL | 0x4ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards PCICFG spacce",
	},
	{ .uname = "CFG_WRITE_PART3",
	  .ucode = 0x1000ULL | 0x8ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards PCICFG spacce",
	},
	{ .uname = "CFG_WRITE_VTD0",
	  .ucode = 0x1000ULL | 0x10ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards PCICFG spacce",
	},
	{ .uname = "CFG_WRITE_VTD1",
	  .ucode = 0x1000ULL | 0x20ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards PCICFG spacce",
	},
	{ .uname = "IO_READ_PART0",
	  .ucode = 0x8000ULL | 0x1ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards IO spacce",
	},
	{ .uname = "IO_READ_PART1",
	  .ucode = 0x8000ULL | 0x2ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards IO spacce",
	},
	{ .uname = "IO_READ_PART2",
	  .ucode = 0x8000ULL | 0x4ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards IO spacce",
	},
	{ .uname = "IO_READ_PART3",
	  .ucode = 0x8000ULL | 0x8ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards IO spacce",
	},
	{ .uname = "IO_READ_VTD0",
	  .ucode = 0x8000ULL | 0x10ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards IO spacce",
	},
	{ .uname = "IO_READ_VTD1",
	  .ucode = 0x8000ULL | 0x20ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards IO spacce",
	},
	{ .uname = "IO_WRITE_PART0",
	  .ucode = 0x2000ULL | 0x1ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards IO spacce",
	},
	{ .uname = "IO_WRITE_PART1",
	  .ucode = 0x2000ULL | 0x2ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards IO spacce",
	},
	{ .uname = "IO_WRITE_PART2",
	  .ucode = 0x2000ULL | 0x4ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards IO spacce",
	},
	{ .uname = "IO_WRITE_PART3",
	  .ucode = 0x2000ULL | 0x8ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards IO spacce",
	},
	{ .uname = "IO_WRITE_VTD0",
	  .ucode = 0x2000ULL | 0x10ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards IO spacce",
	},
	{ .uname = "IO_WRITE_VTD1",
	  .ucode = 0x2000ULL | 0x20ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards IO spacce",
	},
	{ .uname = "MEM_READ_PART0",
	  .ucode = 0x400ULL | 0x1ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards MMIO spacce",
	},
	{ .uname = "MEM_READ_PART1",
	  .ucode = 0x400ULL | 0x2ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards MMIO spacce",
	},
	{ .uname = "MEM_READ_PART2",
	  .ucode = 0x400ULL | 0x4ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards MMIO spacce",
	},
	{ .uname = "MEM_READ_PART3",
	  .ucode = 0x400ULL | 0x8ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards MMIO spacce",
	},
	{ .uname = "MEM_READ_VTD0",
	  .ucode = 0x400ULL | 0x10ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards MMIO spacce",
	},
	{ .uname = "MEM_READ_VTD1",
	  .ucode = 0x400ULL | 0x20ULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from Cards MMIO spacce",
	},
	{ .uname = "MEM_READ_ANY",
	  .ucode = 0x400ULL | 0x3fULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from any source",
	  .uflags= INTEL_X86_DFL,
	  .uequiv= "MEM_READ_PART0:MEM_READ_PART1:MEM_READ_PART2:MEM_READ_PART3:MEM_READ_VTD0:MEM_READ_VTD1",
	},
	{ .uname = "MEM_WRITE_PART0",
	  .ucode = 0x100ULL | 1ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards MMIO spacce",
	},
	{ .uname = "MEM_WRITE_PART1",
	  .ucode = 0x100ULL | 2ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards MMIO spacce",
	},
	{ .uname = "MEM_WRITE_PART2",
	  .ucode = 0x100ULL | 4ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards MMIO spacce",
	},
	{ .uname = "MEM_WRITE_PART3",
	  .ucode = 0x100ULL | 8ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards MMIO spacce",
	},
	{ .uname = "MEM_WRITE_VTD0",
	  .ucode = 0x100ULL | 0x10ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards MMIO spacce",
	},
	{ .uname = "MEM_WRITE_VTD1",
	  .ucode = 0x100ULL | 0x20ULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing to Cards MMIO spacce",
	},
	{ .uname = "MEM_WRITE_ANY",
	  .ucode = 0x100ULL | 0x3fULL << 36,
	  .udesc = "Data requested by the CPU -- Core writing",
	  .uequiv= "MEM_WRITE_PART0:MEM_WRITE_PART1:MEM_WRITE_PART2:MEM_WRITE_PART3:MEM_WRITE_VTD0:MEM_WRITE_VTD1",
	},
	{ .uname = "PEER_READ_PART0",
	  .ucode = 0x800ULL | 0x1ULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	},
	{ .uname = "PEER_READ_PART1",
	  .ucode = 0x800ULL | 0x2ULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	},
	{ .uname = "PEER_READ_PART2",
	  .ucode = 0x800ULL | 0x4ULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	},
	{ .uname = "PEER_READ_PART3",
	  .ucode = 0x800ULL | 0x8ULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	},
	{ .uname = "PEER_READ_VTD0",
	  .ucode = 0x800ULL | 0x10ULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	},
	{ .uname = "PEER_READ_VTD1",
	  .ucode = 0x800ULL | 0x20ULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	},
	{ .uname = "PEER_READ_ANY",
	  .ucode = 0x800ULL | 0x3fULL << 36,
	  .udesc = "Another card (different IIO stack) reading from this card.",
	  .uequiv= "PEER_READ_PART0:PEER_READ_PART1:PEER_READ_PART2:PEER_READ_PART3:PEER_READ_VTD0:PEER_READ_VTD1",
	},
	{ .uname = "PEER_WRITE_PART0",
	  .ucode = 0x200ULL | 0x1ULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	},
	{ .uname = "PEER_WRITE_PART1",
	  .ucode = 0x200ULL | 0x2ULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	},
	{ .uname = "PEER_WRITE_PART2",
	  .ucode = 0x200ULL | 0x4ULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	},
	{ .uname = "PEER_WRITE_PART3",
	  .ucode = 0x200ULL | 0x8ULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	},
	{ .uname = "PEER_WRITE_VTD0",
	  .ucode = 0x200ULL | 0x10ULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	},
	{ .uname = "PEER_WRITE_VTD1",
	  .ucode = 0x200ULL | 0x20ULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	},
	{ .uname = "PEER_WRITE_ANY",
	  .ucode = 0x200ULL | 0x3fULL << 36,
	  .udesc = "Another card (different IIO stack) writing to this card.",
	  .uequiv= "PEER_WRITE_PART0:PEER_WRITE_PART1:PEER_WRITE_PART2:PEER_WRITE_PART3:PEER_WRITE_VTD0:PEER_WRITE_VTD1",
	},
	FC_MASK(1)
};

static intel_x86_umask_t skx_unc_io_data_req_of_cpu[]={
	{ .uname = "ATOMIC_PART0",
	  .ucode = 0x1000ULL | 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Atomic requests targeting DRAM",
	},
	{ .uname = "ATOMIC_PART1",
	  .ucode = 0x1000ULL | 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Atomic requests targeting DRAM",
	},
	{ .uname = "ATOMIC_PART2",
	  .ucode = 0x1000ULL | 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Atomic requests targeting DRAM",
	},
	{ .uname = "ATOMIC_PART3",
	  .ucode = 0x1000ULL | 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Atomic requests targeting DRAM",
	},
	{ .uname = "ATOMIC_VTD0",
	  .ucode = 0x1000ULL | 0x10ULL << 36,
	  .udesc = "Data requested of the CPU -- Atomic requests targeting DRAM",
	},
	{ .uname = "ATOMIC_VTD1",
	  .ucode = 0x1000ULL | 0x20ULL << 36,
	  .udesc = "Data requested of the CPU -- Atomic requests targeting DRAM",
	},
	{ .uname = "ATOMICCMP_PART0",
	  .ucode = 0x2000ULL | 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Completion of atomic requests targeting DRAM",
	},
	{ .uname = "ATOMICCMP_PART1",
	  .ucode = 0x2000ULL | 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Completion of atomic requests targeting DRAM",
	},
	{ .uname = "ATOMICCMP_PART2",
	  .ucode = 0x2000ULL | 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Completion of atomic requests targeting DRAM",
	},
	{ .uname = "ATOMICCMP_PART3",
	  .ucode = 0x2000ULL | 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Completion of atomic requests targeting DRAM",
	},
	{ .uname = "MEM_READ_PART0",
	  .ucode = 0x400ULL| 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from DRAM",
	},
	{ .uname = "MEM_READ_PART1",
	  .ucode = 0x400ULL| 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from DRAM",
	},
	{ .uname = "MEM_READ_PART2",
	  .ucode = 0x400ULL| 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from DRAM",
	},
	{ .uname = "MEM_READ_PART3",
	  .ucode = 0x400ULL| 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from DRAM",
	},
	{ .uname = "MEM_READ_VTD0",
	  .ucode = 0x400ULL| 0x10ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from DRAM",
	},
	{ .uname = "MEM_READ_VTD1",
	  .ucode = 0x400ULL| 0x20ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from DRAM",
	},
	{ .uname = "MEM_READ_ANY",
	  .ucode = 0x400ULL | 0x3fULL << 36,
	  .udesc = "Data requested by the CPU -- Core reading from any DRAM source",
	  .uflags= INTEL_X86_DFL,
	  .uequiv= "MEM_READ_PART0:MEM_READ_PART1:MEM_READ_PART2:MEM_READ_PART3:MEM_READ_VTD0:MEM_READ_VTD1",
	},
	{ .uname = "MEM_WRITE_PART0",
	  .ucode = 0x100ULL | 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to DRAM",
	},
	{ .uname = "MEM_WRITE_PART1",
	  .ucode = 0x100ULL | 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to DRAM",
	},
	{ .uname = "MEM_WRITE_PART2",
	  .ucode = 0x100ULL | 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to DRAM",
	},
	{ .uname = "MEM_WRITE_PART3",
	  .ucode = 0x100ULL | 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to DRAM",
	},
	{ .uname = "MEM_WRITE_VTD0",
	  .ucode = 0x100ULL | 0x10ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to DRAM",
	},
	{ .uname = "MEM_WRITE_VTD1",
	  .ucode = 0x100ULL | 0x20ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to DRAM",
	},
	{ .uname = "MSG_PART0",
	  .ucode = 0x4000ULL | 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Messages",
	},
	{ .uname = "MSG_PART1",
	  .ucode = 0x4000ULL | 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Messages",
	},
	{ .uname = "MSG_PART2",
	  .ucode = 0x4000ULL | 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Messages",
	},
	{ .uname = "MSG_PART3",
	  .ucode = 0x4000ULL | 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Messages",
	},
	{ .uname = "MSG_VTD0",
	  .ucode = 0x4000ULL | 0x10ULL << 36,
	  .udesc = "Data requested of the CPU -- Messages",
	},
	{ .uname = "MSG_VTD1",
	  .ucode = 0x4000ULL | 0x20ULL << 36,
	  .udesc = "Data requested of the CPU -- Messages",
	},
	{ .uname = "PEER_READ_PART0",
	  .ucode = 0x800ULL | 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from another Card (same or different stack)",
	},
	{ .uname = "PEER_READ_PART1",
	  .ucode = 0x800ULL | 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from another Card (same or different stack)",
	},
	{ .uname = "PEER_READ_PART2",
	  .ucode = 0x800ULL | 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from another Card (same or different stack)",
	},
	{ .uname = "PEER_READ_PART3",
	  .ucode = 0x800ULL | 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from another Card (same or different stack)",
	},
	{ .uname = "PEER_READ_VTD0",
	  .ucode = 0x800ULL | 0x10ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from another Card (same or different stack)",
	},
	{ .uname = "PEER_READ_VTD1",
	  .ucode = 0x800ULL | 0x20ULL << 36,
	  .udesc = "Data requested of the CPU -- Card reading from another Card (same or different stack)",
	},
	{ .uname = "PEER_WRITE_PART0",
	  .ucode = 0x200ULL | 0x1ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to another Card (same or different stack)",
	},
	{ .uname = "PEER_WRITE_PART1",
	  .ucode = 0x200ULL | 0x2ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to another Card (same or different stack)",
	},
	{ .uname = "PEER_WRITE_PART2",
	  .ucode = 0x200ULL | 0x4ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to another Card (same or different stack)",
	},
	{ .uname = "PEER_WRITE_PART3",
	  .ucode =  0x200ULL | 0x8ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to another Card (same or different stack)",
	},
	{ .uname = "PEER_WRITE_VTD0",
	  .ucode = 0x200ULL | 0x10ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to another Card (same or different stack)",
	},
	{ .uname = "PEER_WRITE_VTD1",
	  .ucode = 0x200ULL | 0x20ULL << 36,
	  .udesc = "Data requested of the CPU -- Card writing to another Card (same or different stack)",
	},
	FC_MASK(1)
};

static intel_x86_umask_t skx_unc_io_mask_match_and[]={
	{ .uname = "BUS0",
	  .ucode = 0x100,
	  .udesc = "AND Mask/match for debug bus -- Non-PCIE bus",
	},
	{ .uname = "BUS0_BUS1",
	  .ucode = 0x800,
	  .udesc = "AND Mask/match for debug bus -- Non-PCIE bus and PCIE bus",
	},
	{ .uname = "BUS0_NOT_BUS1",
	  .ucode = 0x400,
	  .udesc = "AND Mask/match for debug bus -- Non-PCIE bus and !(PCIE bus)",
	},
	{ .uname = "BUS1",
	  .ucode = 0x200,
	  .udesc = "AND Mask/match for debug bus -- PCIE bus",
	},
	{ .uname = "NOT_BUS0_BUS1",
	  .ucode = 0x1000,
	  .udesc = "AND Mask/match for debug bus -- !(Non-PCIE bus) and PCIE bus",
	},
	{ .uname = "NOT_BUS0_NOT_BUS1",
	  .ucode = 0x2000,
	  .udesc = "AND Mask/match for debug bus -- ",
	},
};

static intel_x86_umask_t skx_unc_io_mask_match_or[]={
	{ .uname = "BUS0",
	  .ucode = 0x100,
	  .udesc = "OR Mask/match for debug bus -- Non-PCIE bus",
	},
	{ .uname = "BUS0_BUS1",
	  .ucode = 0x800,
	  .udesc = "OR Mask/match for debug bus -- Non-PCIE bus and PCIE bus",
	},
	{ .uname = "BUS0_NOT_BUS1",
	  .ucode = 0x400,
	  .udesc = "OR Mask/match for debug bus -- Non-PCIE bus and !(PCIE bus)",
	},
	{ .uname = "BUS1",
	  .ucode = 0x200,
	  .udesc = "OR Mask/match for debug bus -- PCIE bus",
	},
	{ .uname = "NOT_BUS0_BUS1",
	  .ucode = 0x1000,
	  .udesc = "OR Mask/match for debug bus -- !(Non-PCIE bus) and PCIE bus",
	},
	{ .uname = "NOT_BUS0_NOT_BUS1",
	  .ucode = 0x2000,
	  .udesc = "OR Mask/match for debug bus -- !(Non-PCIE bus) and !(PCIE bus)",
	},
};

static intel_x86_umask_t skx_unc_io_vtd_access[]={
	{ .uname = "CTXT_MISS",
	  .ucode = 0x200,
	  .udesc = "VTd Access -- context cache miss",
	},
	{ .uname = "L1_MISS",
	  .ucode = 0x400,
	  .udesc = "VTd Access -- L1 miss",
	},
	{ .uname = "L2_MISS",
	  .ucode = 0x800,
	  .udesc = "VTd Access -- L2 miss",
	},
	{ .uname = "L3_MISS",
	  .ucode = 0x1000,
	  .udesc = "VTd Access -- L3 miss",
	},
	{ .uname = "L4_PAGE_HIT",
	  .ucode = 0x100,
	  .udesc = "VTd Access -- Vtd hit",
	},
	{ .uname = "TLB1_MISS",
	  .ucode = 0x8000,
	  .udesc = "VTd Access -- TLB miss",
	},
	{ .uname = "TLB_FULL",
	  .ucode = 0x4000,
	  .udesc = "VTd Access -- TLB is full",
	},
	{ .uname = "TLB_MISS",
	  .ucode = 0x2000,
	  .udesc = "VTd Access -- TLB miss",
	},
};


static intel_x86_entry_t intel_skx_unc_iio_pe[]={
  { .name   = "UNC_IO_CLOCKTICKS",
    .code   = 0x1,
    .desc   = "IIO clockticks",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_COMP_BUF_INSERTS",
    .code   = 0xc2,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 2,
    .umasks = skx_unc_io_comp_buf_inserts,
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_comp_buf_inserts),
  },
  { .name   = "UNC_IO_COMP_BUF_OCCUPANCY",
    .code   = 0xd5,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_DATA_REQ_BY_CPU",
    .code   = 0xc0,
    .desc   = "Number of double word (4 bytes) requests initiated by the main die to the attached device.",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xc,
    .ngrp   = 2,
    .umasks = skx_unc_io_data_req_by_cpu,
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_data_req_by_cpu),
  },
  { .name   = "UNC_IO_DATA_REQ_OF_CPU",
    .code   = 0x83,
    .desc   = "Number of double word (4 bytes) requests the attached device made of the main die.",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0x3,
    .ngrp   = 2,
    .umasks = skx_unc_io_data_req_of_cpu,
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_data_req_of_cpu),
  },
  { .name   = "UNC_IO_LINK_NUM_CORR_ERR",
    .code   = 0xf,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_LINK_NUM_RETRIES",
    .code   = 0xe,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_MASK_MATCH",
    .code   = 0x21,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_MASK_MATCH_AND",
    .code   = 0x2,
    .desc   = "Asserted if all bits specified by mask match",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = skx_unc_io_mask_match_and,
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_mask_match_and),
  },
  { .name   = "UNC_IO_MASK_MATCH_OR",
    .code   = 0x3,
    .desc   = "Asserted if any bits specified by mask match",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = skx_unc_io_mask_match_or,
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_mask_match_or),
  },
  { .name   = "UNC_IO_NOTHING",
    .code   = 0x0,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_SYMBOL_TIMES",
    .code   = 0x82,
    .desc   = "Gen1 - increment once every 4nS, Gen2 - increment once every 2nS, Gen3 - increment once every 1nS",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
  { .name   = "UNC_IO_TXN_REQ_BY_CPU",
    .code   = 0xc1,
    .desc   = "Also known as Outbound.  Number of requests, to the attached device, initiated by the main die.",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 2,
    .umasks = skx_unc_io_data_req_by_cpu, /* shared */
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_data_req_by_cpu),
  },
  { .name   = "UNC_IO_TXN_REQ_OF_CPU",
    .code   = 0x84,
    .desc   = "Also known as Inbound.  Number of 64 byte cache line requests initiated by the attached device.",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 2,
    .umasks = skx_unc_io_data_req_of_cpu, /* shared */
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_data_req_of_cpu),
  },
  { .name   = "UNC_IO_VTD_ACCESS",
    .code   = 0x41,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
    .ngrp   = 1,
    .umasks = skx_unc_io_vtd_access,
    .numasks= LIBPFM_ARRAY_SIZE(skx_unc_io_vtd_access),
  },
  { .name   = "UNC_IO_VTD_OCCUPANCY",
    .code   = 0x40,
    .desc   = "TBD",
    .modmsk = SKX_UNC_IIO_ATTRS,
    .cntmsk = 0xf,
  },
};

