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
 * Cortex A9 r2p2 Event Table
 * based on Table 11-7 from the "Cortex A9 Technical Reference Manual"
 */
static const arm_entry_t arm_cortex_a9_pe []={
	/*
	 * ARMv7 events
	 */
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
	{.name = "UNALIGNED_ACCESS",
	 .code = 0x0f,
	 .desc = "Unaligned accesses architecturally executed"
	},
	{.name = "PC_BRANCH_MIS_PRED",
	 .code = 0x10,
	 .desc = "Branches mispredicted or not predicted"
	},
	{.name = "CLOCK_CYCLES",
	 .code = 0x11,
	 .desc = "Clock cycles"
	},
	{.name = "PC_BRANCH_MIS_USED",
	 .code = 0x12,
	 .desc = "Branches that could have been predicted"
	},
	/*
	 * Cortex A9 specific events
	 */
	{.name = "JAVA_HW_BYTECODE_EXEC",
	 .code = 0x40,
	 .desc = "Java bytecodes decoded, including speculative (approximate)"
	},
	{.name = "JAVA_SW_BYTECODE_EXEC",
	 .code = 0x41,
	 .desc = "Software Java bytecodes decoded, including speculative (approximate)"
	},
	{.name = "JAZELLE_BRANCH_EXEC",
	 .code = 0x42,
	 .desc = "Jazelle backward branches executed. Includes branches that are flushed because of previous load/store which abort late (approximate)"
	},
	{.name = "COHERENT_LINE_MISS",
	 .code = 0x50,
	 .desc = "Coherent linefill misses which also miss on other processors"
	},     
	{.name = "COHERENT_LINE_HIT",
	 .code = 0x51,
	 .desc = "Coherent linefill requests that hit on another processor"
	},
	{.name = "ICACHE_DEP_STALL_CYCLES",
	 .code = 0x60,
	 .desc = "Cycles processor is stalled waiting for instruction cache and the instruction cache is performing at least one linefill (approximate)"
	},
	{.name = "DCACHE_DEP_STALL_CYCLES",
	 .code = 0x61,
	 .desc = "Cycles processor is stalled waiting for data cache"
	},
	{.name = "TLB_MISS_DEP_STALL_CYCLES",
	 .code = 0x62,
	 .desc = "Cycles processor is stalled waiting for completion of TLB walk (approximate)"
	},     
	{.name = "STREX_EXECUTED_PASSED",
	 .code = 0x63,
	 .desc = "Number of STREX instructions executed and passed"
	},
	{.name = "STREX_EXECUTED_FAILED",
	 .code = 0x64,
	 .desc = "Number of STREX instructions executed and failed"
	},
	{.name = "DATA_EVICTION",
	 .code = 0x65,
	 .desc = "Data eviction requests due to linefill in data cache"
	},
	{.name = "ISSUE_STAGE_NO_INST",
	 .code = 0x66,
	 .desc = "Cycles the issue stage does not dispatch any instructions"
	},
	{.name = "ISSUE_STAGE_EMPTY",
	 .code = 0x67,
	 .desc = "Cycles where issue stage is empty"
	},  
	{.name = "INST_OUT_OF_RENAME_STAGE",
	 .code = 0x68,
	 .desc = "Number of instructions going through register renaming stage (approximate)"
	},
	{.name = "PREDICTABLE_FUNCT_RETURNS",
	 .code = 0x6e,
	 .desc = "Number of predictable function returns whose condition codes do not fail (approximate)"
	},
	{.name = "MAIN_UNIT_EXECUTED_INST",
	 .code = 0x70,
	 .desc = "Instructions executed in the main execution, multiply, ALU pipelines (approximate)"
	},
	{.name = "SECOND_UNIT_EXECUTED_INST",
	 .code = 0x71,
	 .desc = "Instructions executed in the second execution pipeline"
	},
	{.name = "LD_ST_UNIT_EXECUTED_INST",
	 .code = 0x72,
	 .desc = "Instructions executed in the Load/Store unit"
	},
	{.name = "FP_EXECUTED_INST",
	 .code = 0x73,
	 .desc = "Floating point instructions going through register renaming stage"
	},
	{.name = "NEON_EXECUTED_INST",
	 .code = 0x74,
	 .desc = "NEON instructions going through register renaming stage (approximate)"
	},
	{.name = "PLD_FULL_DEP_STALL_CYCLES",
	 .code = 0x80,
	 .desc = "Cycles processor is stalled because PLD slots are full (approximate)"
	},
	{.name = "DATA_WR_DEP_STALL_CYCLES",
	 .code = 0x81,
	 .desc = "Cycles processor is stalled due to writes to external memory (approximate)"
	},
	{.name = "ITLB_MISS_DEP_STALL_CYCLES",
	 .code = 0x82,
	 .desc = "Cycles stalled due to main instruction TLB miss (approximate)"
	},
	{.name = "DTLB_MISS_DEP_STALL_CYCLES",
	 .code = 0x83,
	 .desc = "Cycles stalled due to main data TLB miss (approximate)"
	},
	{.name = "MICRO_ITLB_MISS_DEP_STALL_CYCLES",
	 .code = 0x84,
	 .desc = "Cycles stalled due to micro instruction TLB miss (approximate)"
	},  
	{.name = "MICRO_DTLB_MISS_DEP_STALL_CYCLES",
	 .code = 0x85,
	 .desc = "Cycles stalled due to micro data TLB miss (approximate)"
	},     
	{.name = "DMB_DEP_STALL_CYCLES",
	 .code = 0x86,
	 .desc = "Cycles stalled due to DMB memory barrier (approximate)"
	},
	{.name = "INTGR_CLK_ENABLED_CYCLES",
	 .code = 0x8a,
	 .desc = "Cycles during which integer core clock is enabled (approximate)"
	},
	{.name = "DATA_ENGINE_CLK_EN_CYCLES",
	 .code = 0x8b,
	 .desc = "Cycles during which Data Engine clock is enabled (approximate)"
	},     
	{.name = "ISB_INST",
	 .code = 0x90,
	 .desc = "Number of ISB instructions architecturally executed"
	},
	{.name = "DSB_INST",
	 .code = 0x91,
	 .desc = "Number of DSB instructions architecturally executed"
	},
	{.name = "DMB_INST",
	 .code = 0x92,
	 .desc = "Number of DMB instructions architecturally executed (approximate)"
	},
	{.name = "EXT_INTERRUPTS",
	 .code = 0x93,
	 .desc = "Number of External interrupts (approximate)"
	},
	{.name = "PLE_CACHE_LINE_RQST_COMPLETED",
	 .code = 0xa0,
	 .desc = "PLE cache line requests completed"
	},
	{.name = "PLE_CACHE_LINE_RQST_SKIPPED",
	 .code = 0xa1,
	 .desc = "PLE cache line requests skipped"
	},
	{.name = "PLE_FIFO_FLUSH",
	 .code = 0xa2,
	 .desc = "PLE FIFO flushes"
	},
	{.name = "PLE_RQST_COMPLETED",
	 .code = 0xa3,
	 .desc = "PLE requests completed"
	},
	{.name = "PLE_FIFO_OVERFLOW",
	 .code = 0xa4,
	 .desc = "PLE FIFO overflows"
	},
	{.name = "PLE_RQST_PROG",
	 .code = 0xa5,
	 .desc = "PLE requests programmed"
	},
	{.name = "CPU_CYCLES",
	 .code = 0xff,
	 .desc = "CPU cycles"
	},
};

#define ARM_CORTEX_A9_EVENT_COUNT	(sizeof(arm_cortex_a9_pe)/sizeof(arm_entry_t))
