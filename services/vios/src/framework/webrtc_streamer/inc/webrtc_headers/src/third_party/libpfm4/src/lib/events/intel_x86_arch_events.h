/*
 * Copyright (c) 2006-2007 Hewlett-Packard Development Company, L.P.
 * Contributed by Stephane Eranian <eranian@hpl.hp.com>
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
 * architected events for architectural perfmon v1 and v2 as defined by the IA-32 developer's manual
 * Vol 3B, table 18-6 (May 2007)
 */
static intel_x86_entry_t intel_x86_arch_pe[]={
	{.name = "UNHALTED_CORE_CYCLES",
	 .code = 0x003c,
	 .cntmsk = 0x200000000ull, /* temporary */
	 .desc =  "count core clock cycles whenever the clock signal on the specific core is running (not halted)"
	},
	{.name = "INSTRUCTION_RETIRED",
	 .code = 0x00c0,
	 .cntmsk = 0x100000000ull, /* temporary */
	 .desc =  "count the number of instructions at retirement. For instructions that consists of multiple micro-ops, this event counts the retirement of the last micro-op of the instruction",
	},
	{.name = "UNHALTED_REFERENCE_CYCLES",
	 .code = 0x013c,
	 .cntmsk = 0x400000000ull, /* temporary */
	 .desc =  "count reference clock cycles while the clock signal on the specific core is running. The reference clock operates at a fixed frequency, irrespective of core frequency changes due to performance state transitions",
	},
	{.name = "LLC_REFERENCES",
	 .code = 0x4f2e,
	 .desc =  "count each request originating from the core to reference a cache line in the last level cache. The count may include speculation, but excludes cache line fills due to hardware prefetch",
	},
	{.name = "LLC_MISSES",
	 .code = 0x412e,
	 .desc =  "count each cache miss condition for references to the last level cache. The event count may include speculation, but excludes cache line fills due to hardware prefetch",
	},
	{.name = "BRANCH_INSTRUCTIONS_RETIRED",
	 .code = 0x00c4,
	 .desc =  "count branch instructions at retirement. Specifically, this event counts the retirement of the last micro-op of a branch instruction",
	},
	{.name = "MISPREDICTED_BRANCH_RETIRED",
	 .code = 0x00c5,
	 .desc =  "count mispredicted branch instructions at retirement. Specifically, this event counts at retirement of the last micro-op of a branch instruction in the architectural path of the execution and experienced misprediction in the branch prediction hardware",
	}
};
