/*
 * Copyright (c) 2018 Cavium, Inc
 * Contributed by Steve Walk <swalk.cavium@gmail.com>
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
 * Cavium ThunderX2
 *
 * ARM Architecture Reference Manual, ARMv8, for ARMv8-A architecture profile,
 * ARM DDI 0487B.a (ID033117)
 *
 * Cavium ThunderX2 C99XX PMU Events (Abridged), July 31, 2018
 * https://cavium.com/resources.html
 */

static const arm_entry_t arm_thunderx2_pe[]={
	{.name = "SW_INCR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x00,
	 .desc = "Instruction architecturally executed (condition check pass) software increment"
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
	{.name = "L1D_CACHE",
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
	 .desc = "Instruction architecturally executed (condition check pass) - Load"
	},
	{.name = "ST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x07,
	 .desc = "Instruction architecturally executed (condition check pass) - Store"
	},
	{.name = "INST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x08,
	 .desc = "Instruction architecturally executed"
	},
	{.name = "EXC_TAKEN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x09,
	 .desc = "Exception taken"
	},
	{.name = "EXC_RETURN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0A,
	 .desc = "Instruction architecturally executed (condition check pass) - Exception return"
	},
	{.name = "CID_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0B,
	 .desc = "Instruction architecturally executed (condition check pass) - Write to CONTEXTIDR"
	},
	{.name = "BR_IMMED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0D,
	 .desc = "Instruction architecturally executed, immediate branch"
	},
	{.name = "BR_RETURN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0E,
	 .desc = "Instruction architecturally executed (condition check pass) - procedure return"
	},
	{.name = "UNALIGNED_LDST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0F,
	 .desc = "Instruction architecturally executed (condition check pass), unaligned load/store"
	},
	{.name = "BR_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x10,
	 .desc = "Mispredicted or not predicted branch speculatively executed"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x11,
	 .desc = "Cycles"
	},
	{.name = "BR_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x12,
	 .desc = "Predictable branch speculatively executed"
	},
	{.name = "MEM_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x13,
	 .desc = "Data memory access"
	},
	{.name = "L1I_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x14,
	 .desc = "Level 1 instruction cache access"
	},
	{.name = "L1D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x15,
	 .desc = "Level 1 data cache write-back"
	},
	{.name = "L2D_CACHE",
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
	 .desc = "Level 2 data cache write-back"
	},
	{.name = "BUS_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x19,
	 .desc = "Bus access"
	},
	{.name = "INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1B,
	 .desc = "Instruction speculatively executed"
	},
	{.name = "TTBR_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1C,
	 .desc = "Instruction architecturally executed (condition check pass)  Write to translation table base"
	},
	{.name = "CHAIN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1E,
	 .desc = "For odd-numbered counters, increments the count by one for each overflow of the proceeding even counter"
	},
	{.name = "L1D_CACHE_ALLOCATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1F,
	 .desc = "Level 1 data cache allocation without refill"
	},
	{.name = "L2D_CACHE_ALLOCATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x20,
	 .desc = "Level 2 data/unified cache allocation without refill"
	},
	{.name = "BR_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21,
	 .desc = "Counts all branches on the architecturally executed path that would incur cost if mispredicted"
	},
	{.name = "BR_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x22,
	 .desc = "Instructions executed, mis-predicted branch. All instructions counted by BR_RETIRED that were not correctly predicted"
	},
	{.name = "STALL_FRONTEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x23,
	 .desc = "Cycle on which no operation issued because there were no operations to issue"
	},
	{.name = "STALL_BACKEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x24,
	 .desc = "Cycle on which no operation issued due to back-end resources being unavailable"
	},
	{.name = "L1D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x25,
	 .desc = "Level 1 data TLB access"
	},
	{.name = "L1I_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x26,
	 .desc = "Instruction TLB access"
	},
	{.name = "L2D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2D,
	 .desc = "Attributable memory-read or attributable memory-write operation that causes a TLB refill"
	},
	{.name = "L2I_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2E,
	 .desc = "Attributable instruction memory access that causes a TLB refill"
	},
	{.name = "L2D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2F,
	 .desc = "Attributable memory read operation or attributable memory write operation that causes a TLB access"
	},
	{.name = "L2I_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x30,
	 .desc = "Attributable memory read operation or attributable memory write operation that causes a TLB access"
	},
	{.name = "L1D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x40,
	 .desc = "Level 1 data cache access, read"
	},
	{.name = "L1D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x41,
	 .desc = "Level 1 data cache access, write"
	},
	{.name = "L1D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x42,
	 .desc = "Level 1 data cache refill, read"
	},
	{.name = "L1D_CACHE_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x43,
	 .desc = "Level 1 data cache refill, write"
	},
	{.name = "L1D_CACHE_REFILL_INNER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x44,
	 .desc = "Level 1 data cache refill, inner"
	},
	{.name = "L1D_CACHE_REFILL_OUTER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x45,
	 .desc = "Level 1 data cache refill, outer"
	},
	{.name = "L1D_CACHE_WB_VICTIM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x46,
	 .desc = "Level 1 data cache write-back, victim"
	},
	{.name = "L1D_CACHE_WB_CLEAN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x47,
	 .desc = "Level 1 data cache write-back, cleaning and coherency"
	},
	{.name = "L1D_CACHE_INVAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x48,
	 .desc = "Level 1 data cache invalidate"
	},
	{.name = "L1D_TLB_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4C,
	 .desc = "Level 1 data TLB read refill"
	},
	{.name = "L1D_TLB_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4D,
	 .desc = "Level 1 data TLB write refill"
	},
	{.name = "L1D_TLB_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4E,
	 .desc = "Level 1 data TLB access, read"
	},
	{.name = "L1D_TLB_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4F,
	 .desc = "Level 1 data TLB access, write"
	},
	{.name = "L2D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x50,
	 .desc = "Level 2 data cache access, read"
	},
	{.name = "L2D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x51,
	 .desc = "Level 2 data cache access, write"
	},
	{.name = "L2D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x52,
	 .desc = "Level 2 data cache refill, read"
	},
	{.name = "L2D_CACHE_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x53,
	 .desc = "Level 2 data cache refill, write"
	},
	{.name = "L2D_CACHE_WB_VICTIM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x56,
	 .desc = "Level 2 data cache write-back, victim"
	},
	{.name = "L2D_CACHE_WB_CLEAN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x57,
	 .desc = "Level 2 data cache write-back, cleaning and coherency"
	},
	{.name = "L2D_CACHE_INVAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x58,
	 .desc = "Level 2 data cache invalidate"
	},
	{.name = "L2D_TLB_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5C,
	 .desc = "Level 2 data/unified TLB refill, read"
	},
	{.name = "L2D_TLB_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5D,
	 .desc = "Level 2 data/unified TLB refill, write"
	},
	{.name = "L2D_TLB_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5E,
	 .desc = "Level 2 data/unified TLB access, read"
	},
	{.name = "L2D_TLB_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5F,
	 .desc = "Level 2 data/unified TLB access, write"
	},
	{.name = "BUS_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x60,
	 .desc = "Bus access, read"
	},
	{.name = "BUS_ACCESS_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x61,
	 .desc = "Bus access, write"
	},
	{.name = "BUS_ACCESS_SHARED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x62,
	 .desc = "Bus access, normal, cacheable, shareable"
	},
	{.name = "BUS_ACCESS_NOT_SHARED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x63,
	 .desc = "Bus not normal access"
	},
	{.name = "BUS_ACCESS_NORMAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x64,
	 .desc = "Bus access, normal"
	},
	{.name = "BUS_ACCESS_PERIPH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x65,
	 .desc = "Bus access, peripheral"
	},
	{.name = "MEM_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x66,
	 .desc = "Data memory access, read"
	},
	{.name = "MEM_ACCESS_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x67,
	 .desc = "Data memory access, write"
	},
	{.name = "UNALIGNED_LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x68,
	 .desc = "Unaligned access, read"
	},
	{.name = "UNALIGNED_ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x69,
	 .desc = "Unaligned access, write"
	},
	{.name = "UNALIGNED_LDST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6A,
	 .desc = "Unaligned access"
	},
	{.name = "LDREX_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6C,
	 .desc = "Exclusive operation speculatively executed - LDREX or LDX"
	},
	{.name = "STREX_PASS_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6D,
	 .desc = "Exclusive operation speculative executed - STREX or STX pass"
	},
	{.name = "STREX_FAIL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6E,
	 .desc = "Exclusive operation speculative executed - STREX or STX fail"
	},
	{.name = "STREX_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6F,
	 .desc = "Exclusive operation speculatively executed - STREX or STX"
	},
	{.name = "LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x70,
	 .desc = "Operation speculatively executed, load"
	},
	{.name = "ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x71,
	 .desc = "Operation speculatively executed, store"
	},
	{.name = "LDST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x72,
	 .desc = "Operation speculatively executed, load or store"
	},
	{.name = "DP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x73,
	 .desc = "Operation speculatively executed, data-processing"
	},
	{.name = "ASE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x74,
	 .desc = "Operation speculatively executed, Advanced SIMD instruction"
	},
	{.name = "VFP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x75,
	 .desc = "Operation speculatively executed, floating point instruction"
	},
	{.name = "CRYPTO_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x77,
	 .desc = "Operation speculatively executed, Cryptographic instruction"
	},
	{.name = "BR_IMMED_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x78,
	 .desc = "Branch speculatively executed, immediate branch"
	},
	{.name = "BR_RETURN_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x79,
	 .desc = "Branch speculatively executed, return"
	},
	{.name = "BR_INDIRECT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7A,
	 .desc = "Branch speculatively executed, indirect branch"
	},
	{.name = "ISB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7C,
	 .desc = "Barrier speculatively executed, ISB"
	},
	{.name = "DSB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7D,
	 .desc = "barrier speculatively executed, DSB"
	},
	{.name = "DMB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7E,
	 .desc = "Barrier speculatively executed, DMB"
	},
	{.name = "EXC_UNDEF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81,
	 .desc = "Exception taken, other synchronous"
	},
	{.name = "EXC_SVC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x82,
	 .desc = "Exception taken, supervisor call"
	},
	{.name = "EXC_PABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x83,
	 .desc = "Exception taken, instruction abort"
	},
	{.name = "EXC_DABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x84,
	 .desc = "Exception taken, data abort or SError"
	},
	{.name = "EXC_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x86,
	 .desc = "Exception taken, irq"
	},
	{.name = "EXC_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x87,
	 .desc = "Exception taken, fiq"
	},
	{.name = "EXC_SMC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x88,
	 .desc = "Exception taken, smc"
	},
	{.name = "EXC_HVC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8A,
	 .desc = "Exception taken, hypervisor call"
	},
	{.name = "EXC_TRAP_PABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8B,
	 .desc = "Exception taken, instruction abort not taken locally"
	},
	{.name = "EXC_TRAP_DABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8C,
	 .desc = "Exception taken, data abort or SError not taken locally"
	},
	{.name = "EXC_TRAP_OTHER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8D,
	 .desc = "Exception taken, other traps not taken locally"
	},
	{.name = "EXC_TRAP_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8E,
	 .desc = "Exception taken, irq not taken locally"
	},
	{.name = "EXC_TRAP_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8F,
	 .desc = "Exception taken, fiq not taken locally"
	},
	{.name = "RC_LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x90,
	 .desc = "Release consistency instruction speculatively executed (load-acquire)"
	},
	{.name = "RC_ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x91,
	 .desc = "Release consistency instruction speculatively executed (store-release)"
	},
	{.name = "L1D_LHS_VANOTP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC1,
	 .desc = "A Load hit store retry"
	},
	{.name = "L1D_LHS_OVRLAP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC2,
	 .desc = "A Load hit store retry, VA match, PA mismatch"
	},
	{.name = "L1D_LHS_VANOSD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC3,
	 .desc = "A Load hit store retry, VA match, store data not issued"
	},
	{.name = "L1D_LHS_FWD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC4,
	 .desc = "A Load hit store forwarding. Load completes"
	},
	{.name = "L1D_BNKCFL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC6,
	 .desc = "Bank conflict load retry"
	},
	{.name = "L1D_LSMQ_FULL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC7,
	 .desc = "LSMQ retry"
	},
	{.name = "L1D_LSMQ_HIT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC8,
	 .desc = "LSMQ hit retry"
	},
	{.name = "L1D_EXPB_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xC9,
	 .desc = "An external probe missed the L1"
	},
	{.name = "L1D_L2EV_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xCA,
	 .desc = "An L2 evict operation missed the L1"
	},
	{.name = "L1D_EXPB_HITM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xCB,
	 .desc = "An external probe hit a modified line in the L1"
	},
	{.name = "L1D_L2EV_HITM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xCC,
	 .desc = "An L2 evict operation hit a modified line in the L1"
	},
	{.name = "L1D_EXPB_HIT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xCD,
	 .desc = "An external probe hit in the L1"
	},
	{.name = "L1D_L2EV_HIT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xCE,
	 .desc = "An L2 evict operation hit in the L1"
	},
	{.name = "L1D_EXPB_RETRY",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xCF,
	 .desc = "An external probe hit was retried"
	},
	{.name = "L1D_L2EV_RETRY",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD0,
	 .desc = "An L2 evict operation was retried"
	},
	{.name = "L1D_ST_RMW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD1,
	 .desc = "A read modify write store was drained and updated the L1"
	},
	{.name = "L1D_LSMQ00_LDREQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD2,
	 .desc = "A load has allocated LSMQ entry 0"
	},
	{.name = "L1D_LSMQ00_LDVLD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD3,
	 .desc = "LSMQ entry 0 was initiated by a load"
	},
	{.name = "L1D_LSMQ15_STREQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD4,
	 .desc = "A store was allocated LSMQ entry 15"
	},
	{.name = "L1D_LSMQ15_STVLD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD5,
	 .desc = "LSMQ entry 15 was initiated by a store"
	},
	{.name = "L1D_PB_FLUSH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xD6,
	 .desc = "LRQ ordering flush"
	},
	{.name = "BR_COND_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xE0,
	 .desc = "Conditional branch instruction executed, but mis-predicted"
	},
	{.name = "BR_IND_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xE1,
	 .desc = "Indirect branch instruction executed, but mis-predicted"
	},
	{.name = "BR_RETURN_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xE2,
	 .desc = "Return branch instruction executed, but mis-predicted"
	},
	{.name = "OP_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xE8,
	 .desc = "Uops executed"
	},
	{.name = "LD_OP_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xE9,
	 .desc = "Load uops executed"
	},
	{.name = "ST_OP_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xEA,
	 .desc = "Store uops executed"
	},
	{.name = "FUSED_OP_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xEB,
	 .desc = "Fused uops executed"
	},
	{.name = "IRQ_MASK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xF8,
	 .desc = "Cumulative duration of a PSTATE.I interrupt mask set to 1"
	},
	{.name = "FIQ_MASK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xF9,
	 .desc = "Cumulative duration of a PSTATE.F interrupt mask set to 1"
	},
	{.name = "SERROR_MASK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xFA,
	 .desc = "Cumulative duration of PSTATE.A interrupt mask set to 1"
	},
	{.name = "WFIWFE_SLEEP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x108,
	 .desc = "Number of cycles in which CPU is in low power mode due to WFI/WFE instruction"
	},
	{.name = "L2TLB_4K_PAGE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x127,
	 .desc = "L2 TLB lookup miss using 4K page size"
	},
	{.name = "L2TLB_64K_PAGE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x128,
	 .desc = "L2 TLB lookup miss using 64K page size"
	},
	{.name = "L2TLB_2M_PAGE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x129,
	 .desc = "L2 TLB lookup miss using 2M page size"
	},
	{.name = "L2TLB_512M_PAGE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x12A,
	 .desc = "L2 TLB lookup miss using 512M page size"
	},
	{.name = "ISB_EMPTY",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x150,
	 .desc = "Number of cycles during which micro-op skid-buffer is empty"
	},
	{.name = "ISB_FULL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x151,
	 .desc = "Number of cycles during which micro-op skid-buffer is back-pressuring decode"
	},
	{.name = "STALL_NOTSELECTED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x152,
	 .desc = "Number of cycles during which thread was available for dispatch but not selected"
	},
	{.name = "ROB_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x153,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to ROB full"
	},
	{.name = "ISSQ_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x154,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to ISSQ full"
	},
	{.name = "GPR_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x155,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to GPR full"
	},
	{.name = "FPR_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x156,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to FPR full"
	},
	{.name = "LRQ_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x158,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to LRQ full"
	},
	{.name = "SRQ_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x159,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to SRQ full"
	},
	{.name = "BSR_RECYCLE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x15B,
	 .desc = "Number of cycles in which one or more valid micro-ops did not dispatch due to BSR full"
	},
	{.name = "UOPSFUSED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x164,
	 .desc = "Number of fused micro-ops dispatched"
	},
	{.name = "L2D_TLBI_INT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x20B,
	 .desc = "Internal mmu tlbi cacheops"
	},
	{.name = "L2D_TLBI_EXT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x20C,
	 .desc = "External mmu tlbi cacheops"
	},
	{.name = "L2D_HWPF_DMD_HIT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x218,
	 .desc = "Scu ld/st requests that hit cache or msg for lines brought in by the hardware prefetcher"
	},
	{.name = "L2D_HWPF_REQ_VAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x219,
	 .desc = "Scu hwpf requests into the pipeline"
	},
	{.name = "L2D_HWPF_REQ_LD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21A,
	 .desc = "Scu hwpf ld requests into the pipeline"
	},
	{.name = "L2D_HWPF_REQ_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21B,
	 .desc = "Scu hwpf ld requests that miss"
	},
	{.name = "L2D_HWPF_NEXT_LINE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21C,
	 .desc = "Scu hwpf next line requests generated"
	},
};
