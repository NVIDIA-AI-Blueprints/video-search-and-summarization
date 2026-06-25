/*
 * Copyright (c) 2013 by Vince Weaver <vincent.weaver@maine.edu>
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
 * file linux-2.6/arch/arm/kernel/perf_event_v6.c
 */

/*
 * ARM1176 Event Table
 */
static const arm_entry_t arm_1176_pe []={
	{.name = "ICACHE_MISS",
	 .code = 0x00,
	 .desc = "Instruction cache miss (includes speculative accesses)"
	},
	{.name = "IBUF_STALL",
	 .code = 0x01,
	 .desc = "Stall because instruction buffer cannot deliver an instruction"
	},
	{.name = "DDEP_STALL",
	 .code = 0x02,
	 .desc = "Stall because of data dependency"
	},
	{.name = "ITLB_MISS",
	 .code = 0x03,
	 .desc = "Instruction MicroTLB miss"
	},
	{.name = "DTLB_MISS",
	 .code = 0x04,
	 .desc = "Data MicroTLB miss"
	},
	{.name = "BR_EXEC",
	 .code = 0x05,
	 .desc = "Branch instruction executed"
	},
	{.name = "BR_MISPREDICT",
	 .code = 0x06,
	 .desc = "Branch mispredicted"
	},
	{.name = "INSTR_EXEC",
	 .code = 0x07,
	 .desc = "Instruction executed"
	},
	{.name = "DCACHE_HIT",
	 .code = 0x09,
	 .desc = "Data cache hit"
	},
	{.name = "DCACHE_ACCESS",
	 .code = 0x0a,
	 .desc = "Data cache access"
	},
	{.name = "DCACHE_MISS",
	 .code = 0x0b,
	 .desc = "Data cache miss"
	},
	{.name = "DCACHE_WBACK",
	 .code = 0x0c,
	 .desc = "Data cache writeback"
	},
	{.name = "SW_PC_CHANGE",
	 .code = 0x0d,
	 .desc = "Software changed the PC."
	},
	{.name = "MAIN_TLB_MISS",
	 .code = 0x0f,
	 .desc = "Main TLB miss"
	},
	{.name = "EXPL_D_ACCESS",
	 .code = 0x10,
	 .desc = "Explicit external data cache access "
	},
	{.name = "LSU_FULL_STALL",
	 .code = 0x11,
	 .desc = "Stall because of a full Load Store Unit request queue."
	},
	{.name = "WBUF_DRAINED",
	 .code = 0x12,
	 .desc = "Write buffer drained due to data synchronization barrier or strongly ordered operation"
	},
	{.name = "ETMEXTOUT_0",
	 .code = 0x20,
	 .desc = "ETMEXTOUT[0] was asserted"
	},
	{.name = "ETMEXTOUT_1",
	 .code = 0x21,
	 .desc = "ETMEXTOUT[1] was asserted"
	},
	{.name = "ETMEXTOUT",
	 .code = 0x22,
	 .desc = "Increment once for each of ETMEXTOUT[0] or ETMEXTOUT[1]"
	},
	{.name = "PROC_CALL_EXEC",
	 .code = 0x23,
	 .desc = "Procedure call instruction executed"
	},
	{.name = "PROC_RET_EXEC",
	 .code = 0x24,
	 .desc = "Procedure return instruction executed"
	},
	{.name = "PROC_RET_EXEC_PRED",
	 .code = 0x25,
	 .desc = "Procedure return instruction executed and address predicted"
	},
	{.name = "PROC_RET_EXEC_PRED_INCORRECT",
	 .code = 0x26,
	 .desc = "Procedure return instruction executed and address predicted incorrectly"
	},
	{.name = "CPU_CYCLES",
	 .code = 0xff,
	 .desc = "CPU cycles"
	},
};

#define ARM_1176_EVENT_COUNT	(sizeof(arm_1176_pe)/sizeof(arm_entry_t))
