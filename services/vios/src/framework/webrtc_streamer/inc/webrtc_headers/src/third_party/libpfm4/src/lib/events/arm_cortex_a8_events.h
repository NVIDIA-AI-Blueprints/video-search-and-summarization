/*
 * Copyright (c) 2010 University of Tennessee
 * Contributed by Vince Weaver <vweaver1@utk.edu>
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
 */

/*
 * the various event names are the same as those given in the
 * file linux-2.6/arch/arm/kernel/perf_event.c
 */

/*
 * Cortex A8 Event Table
 */
static const arm_entry_t arm_cortex_a8_pe []={
	{.name = "PMNC_SW_INCR",
	 .code = 0x00,
	 .desc = "Incremented by writes to the Software Increment Register"
	},
	{.name = "IFETCH_MISS",
	 .code = 0x01,
	 .desc = "Instruction fetches that cause lowest-level cache miss"
	},
	{.name = "ITLB_MISS",
	 .code = 0x02,
	 .desc = "Instruction fetches that cause lowest-level TLB miss"
	},
	{.name = "DCACHE_REFILL",
	 .code = 0x03,
	 .desc = "Data read or writes that cause lowest-level cache miss"
	},
	{.name = "DCACHE_ACCESS",
	 .code = 0x04,
	 .desc = "Data read or writes that cause lowest-level cache access"
	},
	{.name = "DTLB_REFILL",
	 .code = 0x05,
	 .desc = "Data read or writes that cause lowest-level TLB refill"
	},
	{.name = "DREAD",
	 .code = 0x06,
	 .desc = "Data read architecturally executed"
	},
	{.name = "DWRITE",
	 .code = 0x07,
	 .desc = "Data write architecturally executed"
	},
	{.name = "INSTR_EXECUTED",
	 .code = 0x08,
	 .desc = "Instructions architecturally executed"
	},
	{.name = "EXC_TAKEN",
	 .code = 0x09,
	 .desc = "Counts each exception taken"
	},
	{.name = "EXC_EXECUTED",
	 .code = 0x0a,
	 .desc = "Exception returns architecturally executed"
	},
	{.name = "CID_WRITE",
	 .code = 0x0b,
	 .desc = "Instruction writes to Context ID Register, architecturally executed"
	},
	{.name = "PC_WRITE",
	 .code = 0x0c,
	 .desc = "Software change of PC.  Equivalent to branches"
	},
	{.name = "PC_IMM_BRANCH",
	 .code = 0x0d,
	 .desc = "Immediate branches architecturally executed"
	},
	{.name = "PC_PROC_RETURN",
	 .code = 0x0e,
	 .desc = "Procedure returns architecturally executed"
	},
	{.name = "UNALIGNED_ACCESS",
	 .code = 0x0f,
	 .desc = "Unaligned accesses architecturally executed"
	},
	{.name = "PC_BRANCH_MIS_PRED",
	 .code = 0x10,
	 .desc = "Branches mispredicted or not predicted"
	},
	{.name = "CLOCK_CYCLES",  /* this isn't in the Cortex-A8 tech doc */
	 .code = 0x11,            /* but is in linux kernel */
	 .desc = "Clock cycles"
	},
	{.name = "PC_BRANCH_MIS_USED",
	 .code = 0x12,
	 .desc = "Branches that could have been predicted"
	},
	{.name = "WRITE_BUFFER_FULL",
	 .code = 0x40,
	 .desc = "Cycles Write buffer full"
	},  
	{.name = "L2_STORE_MERGED",
	 .code = 0x41,
	 .desc = "Stores merged in L2"
	},
	{.name = "L2_STORE_BUFF",
	 .code = 0x42,
	 .desc = "Bufferable store transactions to L2"
	},
	{.name = "L2_ACCESS",
	 .code = 0x43,
	 .desc = "Accesses to L2 cache"
	},
	{.name = "L2_CACHE_MISS",
	 .code = 0x44,
	 .desc = "L2 cache misses"
	},
	{.name = "AXI_READ_CYCLES",
	 .code = 0x45,
	 .desc = "Cycles with active AXI read channel transactions"
	},
	{.name = "AXI_WRITE_CYCLES",
	 .code = 0x46,
	 .desc = "Cycles with Active AXI write channel transactions"
	},
	{.name = "MEMORY_REPLAY",
	 .code = 0x47,
	 .desc = "Memory replay events"
	},
	{.name = "UNALIGNED_ACCESS_REPLAY",
	 .code = 0x48,
	 .desc = "Unaligned accesses causing replays"
	},
	{.name = "L1_DATA_MISS",
	 .code = 0x49,
	 .desc = "L1 data misses due to hashing algorithm"
	},
	{.name = "L1_INST_MISS",
	 .code = 0x4a,
	 .desc = "L1 instruction misses due to hashing algorithm"
	},
	{.name = "L1_DATA_COLORING",
	 .code = 0x4b,
	 .desc = "L1 data access where page color alias occurs"
	},
	{.name = "L1_NEON_DATA",
	 .code = 0x4c,
	 .desc = "NEON accesses that hit in L1 cache"
	},
	{.name = "L1_NEON_CACH_DATA",
	 .code = 0x4d,
	 .desc = "NEON cache accesses for L1 cache"
	},
	{.name = "L2_NEON",
	 .code = 0x4e,
	 .desc = "L2 accesses caused by NEON"
	},
	{.name = "L2_NEON_HIT",
	 .code = 0x4f,
	 .desc = "L2 hits caused by NEON"
	},
	{.name = "L1_INST",
	 .code = 0x50,
	 .desc = "L1 instruction cache accesses"
	},
	{.name = "PC_RETURN_MIS_PRED",
	 .code = 0x51,
	 .desc = "Return stack mispredictions"
	},  
	{.name = "PC_BRANCH_FAILED",
	 .code = 0x52,
	 .desc = "Branch prediction failures"
	},
	{.name = "PC_BRANCH_TAKEN",
	 .code = 0x53,
	 .desc = "Branches predicted taken"
	},
	{.name = "PC_BRANCH_EXECUTED",
	 .code = 0x54,
	 .desc = "Taken branches executed"
	},  
	{.name = "OP_EXECUTED",
	 .code = 0x55,
	 .desc = "Operations executed (includes sub-ops in multi-cycle instructions)"
	},
	{.name = "CYCLES_INST_STALL",
	 .code = 0x56,
	 .desc = "Cycles no instruction is available for issue"
	},
	{.name = "CYCLES_INST",
	 .code = 0x57,
	 .desc = "Number of instructions issued in cycle"
	},
	{.name = "CYCLES_NEON_DATA_STALL",
	 .code = 0x58,
	 .desc = "Cycles stalled waiting on NEON MRC data"
	},  
	{.name = "CYCLES_NEON_INST_STALL",
	 .code = 0x59,
	 .desc = "Cycles stalled due to full NEON queues"
	},
	{.name = "NEON_CYCLES",
	 .code = 0x5a,
	 .desc = "Cycles NEON and integer processors both not idle"
	},  
	{.name = "PMU0_EVENTS",
	 .code = 0x70,
	 .desc = "External PMUEXTIN[0] event"
	},     
	{.name = "PMU1_EVENTS",
	 .code = 0x71,
	 .desc = "External PMUEXTIN[1] event"
	},        
	{.name = "PMU_EVENTS",
	 .code = 0x72,
	 .desc = "External PMUEXTIN[0] or PMUEXTIN[1] event"
	},              
	{.name = "CPU_CYCLES",
	 .code = 0xff,
	 .desc = "CPU cycles"
	},
};

#define ARM_CORTEX_A8_EVENT_COUNT	(sizeof(arm_cortex_a8_pe)/sizeof(arm_entry_t))
