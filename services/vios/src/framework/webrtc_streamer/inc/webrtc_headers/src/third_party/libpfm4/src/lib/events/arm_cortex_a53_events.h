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
 * Cortex A53 r0p2
 * based on Table 12.9 from the "Cortex A53 Technical Reference Manual"
 */

static const arm_entry_t arm_cortex_a53_pe[]={
	{.name = "SW_INCR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x00,
	 .desc = "Instruction architecturally executed (condition check pass) Software increment"
	},
	{.name = "L1I_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x01,
	 .desc = "Level 1 instruction cache refill"
	},
	{.name = "L1I_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x02,
	 .desc = "Level 1 instruction TLB refill"
	},
	{.name = "L1D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x03,
	 .desc = "Level 1 data cache refill"
	},
	{.name = "L1D_CACHE_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x04,
	 .desc = "Level 1 data cache access"
	},
	{.name = "L1D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x05,
	 .desc = "Level 1 data TLB refill"
	},
	{.name = "LD_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x06,
	 .desc = "Load Instruction architecturally executed, condition check",
	},
	{.name = "ST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x07,
	 .desc = "Store Instruction architecturally executed, condition check",
	},
	{.name = "INST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x08,
	 .desc = "Instruction architecturally executed"
	},
	{.name = "EXCEPTION_TAKEN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x09,
	 .desc = "Exception taken"
	},
	{.name = "EXCEPTION_RETURN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0a,
	 .desc = "Instruction architecturally executed (condition check pass) Exception return"
	},
	{.name = "CID_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0b,
	 .desc = "Change to Context ID retired",
	},
	{.name = "PC_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0c,
	 .desc = "Write to CONTEXTIDR, instruction architecturally executed, condition check pass"
	},
	{.name = "BR_IMMED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0d,
	 .desc = "Software change of the PC, instruction architecturally executed, condition check pass"
	},
	{.name = "UNALIGNED_LDST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0f,
	 .desc = "Procedure return, instruction architecturally executed, condition check pass"
	},
	{.name = "BRANCH_MISPRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x10,
	 .desc = "Mispredicted or not predicted branch speculatively executed"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x11,
	 .desc = "Cycles"
	},
	{.name = "BRANCH_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x12,
	 .desc = "Predictable branch speculatively executed"
	},
	{.name = "DATA_MEM_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x13,
	 .desc = "Data memory access"
	},
	{.name = "L1I_CACHE_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x14,
	 .desc = "Level 1 instruction cache access"
	},
	{.name = "L1D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x15,
	 .desc = "Level 1 data cache WriteBack"
	},
	{.name = "L2D_CACHE_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x16,
	 .desc = "Level 2 data cache access"
	},
	{.name = "L2D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x17,
	 .desc = "Level 2 data cache refill"
	},
	{.name = "L2D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x18,
	 .desc = "Level 2 data cache WriteBack"
	},
	{.name = "BUS_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x19,
	 .desc = "Bus access"
	},
	{.name = "LOCAL_MEMORY_ERROR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1a,
	 .desc = "Local memory error"
	},
	{.name = "BUS_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1d,
	 .desc = "Bus cycle"
	},

	{.name = "BUS_READ_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x60,
	 .desc = "Bus read access"
	},
	{.name = "BUS_WRITE_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x61,
	 .desc = "Bus write access"
	},

	{.name = "BRANCH_SPEC_EXEC_IND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7a,
	 .desc = "Indirect branch speculatively executed"
	},

	{.name = "EXCEPTION_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x86,
	 .desc = "Exception taken, irq"
	},
	{.name = "EXCEPTION_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x87,
	 .desc = "Exception taken, irq"
	},
};
