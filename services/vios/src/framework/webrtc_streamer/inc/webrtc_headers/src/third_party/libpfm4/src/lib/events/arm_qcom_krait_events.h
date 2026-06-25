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
 * Qualcomm Krait Chips
 * based on info in the thread on linux-kernel:
 *   [PATCH 0/7] Support Krait CPU PMUs
 */
static const arm_entry_t arm_qcom_krait_pe[]={
	{.name = "L1D_CACHE_REFILL",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x03,
	 .desc = "Level 1 data cache refill"
	},
	{.name = "L1D_CACHE_ACCESS",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x04,
	 .desc = "Level 1 data cache access"
	},
	{.name = "INSTR_EXECUTED",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x08,
	 .desc = "Instructions architecturally executed"
	},
	{.name = "PC_WRITE",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x0c,
	 .desc = "Software change of PC.  Equivalent to branches"
	},
	{.name = "PC_BRANCH_MIS_PRED",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x10,
	 .desc = "Branches mispredicted or not predicted"
	},
	{.name = "CLOCK_CYCLES",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x11,
	 .desc = "Cycles"
	},
	{.name = "BRANCH_PRED",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0x12,
	 .desc = "Predictable branch speculatively executed"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV7_A15_ATTRS,
	 .code = 0xff,
	 .desc = "Cycles"
	},
};
