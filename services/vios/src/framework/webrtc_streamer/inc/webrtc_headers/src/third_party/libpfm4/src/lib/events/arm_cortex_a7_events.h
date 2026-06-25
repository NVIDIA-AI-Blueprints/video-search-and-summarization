/*
 * Copyright (c) 2014 by Vince Weaver <vincent.weaver@maine.edu>
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
 * Cortex A7 MPCore
 * based on Table 11-5 from the "Cortex-A7 MPCore Technical Reference Manual"
 */
static const arm_entry_t arm_cortex_a7_pe[]={
	{.name = "SW_INCR",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x00,
	 .desc = "Incremented on writes to the Software Increment Register"
	},
	{.name = "L1I_CACHE_REFILL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x01,
	 .desc = "Level 1 instruction cache refill"
	},
	{.name = "L1I_TLB_REFILL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x02,
	 .desc = "Level 1 instruction TLB refill"
	},
	{.name = "L1D_CACHE_REFILL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x03,
	 .desc = "Level 1 data cache refill"
	},
	{.name = "L1D_CACHE_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x04,
	 .desc = "Level 1 data cache access"
	},
	{.name = "L1D_TLB_REFILL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x05,
	 .desc = "Level 1 data TLB refill"
	},
	{.name = "DATA_READS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x06,
	 .desc = "Data reads architecturally executed"
	},
	{.name = "DATA_WRITES",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x07,
	 .desc = "Data writes architecturally executed"
	},
	{.name = "INST_RETIRED",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x08,
	 .desc = "Instruction architecturally executed"
	},
	{.name = "EXCEPTION_TAKEN",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x09,
	 .desc = "Exception taken"
	},
	{.name = "EXCEPTION_RETURN",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x0a,
	 .desc = "Instruction architecturally executed"
	},
	{.name = "CID_WRITE_RETIRED",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x0b,
	 .desc = "Change to ContextID retired"
	},
	{.name = "SW_CHANGE_PC",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x0c,
	 .desc = "Software change of PC"
	},
	{.name = "IMMEDIATE_BRANCHES",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x0d,
	 .desc = "Immediate branch architecturally executed"
	},
	{.name = "PROCEDURE_RETURNS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x0e,
	 .desc = "Procedure returns architecturally executed"
	},
	{.name = "UNALIGNED_LOAD_STORE",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x0f,
	 .desc = "Unaligned load-store"
	},
	{.name = "BRANCH_MISPRED",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x10,
	 .desc = "Branches mispredicted/not predicted"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x11,
	 .desc = "Cycles"
	},
	{.name = "BRANCH_PRED",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x12,
	 .desc = "Predictable branch speculatively executed"
	},
	{.name = "DATA_MEM_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x13,
	 .desc = "Data memory access"
	},
	{.name = "L1I_CACHE_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x14,
	 .desc = "Level 1 instruction cache access"
	},
	{.name = "L1D_CACHE_EVICTION",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x15,
	 .desc = "Level 1 data cache eviction"
	},
	{.name = "L2D_CACHE_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x16,
	 .desc = "Level 2 data cache access"
	},
	{.name = "L2D_CACHE_REFILL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x17,
	 .desc = "Level 2 data cache refill"
	},
	{.name = "L2D_CACHE_WB",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x18,
	 .desc = "Level 2 data cache WriteBack"
	},
	{.name = "BUS_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x19,
	 .desc = "Bus accesses"
	},
	{.name = "BUS_CYCLES",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x1d,
	 .desc = "Bus cycle"
	},
	{.name = "BUS_READ_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x60,
	 .desc = "Bus read access"
	},
	{.name = "BUS_WRITE_ACCESS",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x61,
	 .desc = "Bus write access"
	},
	{.name = "IRQ_EXCEPTION_TAKEN",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x86,
	 .desc = "IRQ Exception Taken"
	},
	{.name = "FIQ_EXCEPTION_TAKEN",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0x87,
	 .desc = "FIQ Exception Taken"
	},
	{.name = "EXTERNAL_MEMORY_REQUEST",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc0,
	 .desc = "External memory request"
	},
	{.name = "NONCACHE_EXTERNAL_MEMORY_REQUEST",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc1,
	 .desc = "Non-cacheable xternal memory request"
	},
	{.name = "PREFETCH_LINEFILL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc2,
	 .desc = "Linefill due to prefetch"
	},
	{.name = "PREFETCH_LINEFILL_DROPPED",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc3,
	 .desc = "Prefetch linefill dropped"
	},
	{.name = "ENTERING_READ_ALLOC",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc4,
	 .desc = "Entering read allocate mode"
	},
	{.name = "READ_ALLOC",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc5,
	 .desc = "Read allocate mode"
	},
	/* 0xc6 is Reserved */
	{.name = "ETM_EXT_OUT_0",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc7,
	 .desc = "ETM Ext Out[0]"
	},
	{.name = "ETM_EXT_OUT_1",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc8,
	 .desc = "ETM Ext Out[1]"
	},
	{.name = "DATA_WRITE_STALL",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xc9,
	 .desc = "Data write operation that stalls pipeline due to full store buffer"
	},
	{.name = "DATA_SNOOPED",
	 .modmsk = ARMV7_A7_ATTRS,
	 .code = 0xca,
	 .desc = "Data snooped from other processor"
	},
};
