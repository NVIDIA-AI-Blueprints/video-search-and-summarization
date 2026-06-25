/*
 * Copyright (c) 2024 Google, Inc
 * Contributed by Stephane Eranian <eranian@gmail.com>
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 *
 * ARM Cortex A55
 * References:
 *  - Arm Cortex A55 TRM: https://developer.arm.com/documentation/100442/0100/debug-descriptions/pmu/pmu-events
 *  - https://github.com/ARM-software/data/blob/master/pmu/cortex-a55.json
 */
static const arm_entry_t arm_cortex_a55_pe[]={
	{.name = "SW_INCR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x00,
	 .desc = "Instruction architecturally executed, condition code check pass, software increment"
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
	 .desc = "Instruction architecturally executed, condition code check pass, load"
	},
	{.name = "ST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x07,
	 .desc = "Instruction architecturally executed, condition code check pass, store"
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
	 .code = 0x0a,
	 .desc = "Instruction architecturally executed, condition code check pass, exception return"
	},
	{.name = "CID_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0b,
	 .desc = "Instruction architecturally executed, condition code check pass, write to CONTEXTIDR"
	},
	{.name = "PC_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0c,
	 .desc = "Instruction architecturally executed, condition code check pass, software change of the PC"
	},
	{.name = "BR_IMMED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0d,
	 .desc = "Instruction architecturally executed, immediate branch"
	},
	{.name = "BR_RETURN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0e,
	 .desc = "Instruction architecturally executed, condition code check pass, procedure return"
	},
	{.name = "UNALIGNED_LDST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0f,
	 .desc = "Instruction architecturally executed, condition code check pass, unaligned load or store"
	},
	{.name = "BR_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x10,
	 .desc = "Mispredicted or not predicted branch speculatively executed"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x11,
	 .desc = "Cycle"
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
	 .desc = "Level 1 data cache Write-Back"
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
	 .desc = "Level 2 data cache Write-Back"
	},
	{.name = "BUS_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x19,
	 .desc = "Bus access"
	},
	{.name = "MEMORY_ERROR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1a,
	 .desc = "Local memory error"
	},
	{.name = "INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1b,
	 .desc = "Operation speculatively executed"
	},
	{.name = "INT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .equiv = "INST_SPEC",
	 .code = 0x1b,
	 .desc = "Operation speculatively executed"
	},
	{.name = "TTBR_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1c,
	 .desc = "Instruction architecturally executed, condition code check pass, write to TTBR"
	},
	{.name = "BUS_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1d,
	 .desc = "Bus cycles"
	},
	{.name = "L2D_CACHE_ALLOCATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x20,
	 .desc = "Level 2 data cache allocation without refill"
	},
	{.name = "BR_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21,
	 .desc = "Instruction architecturally executed, branch"
	},
	{.name = "BR_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x22,
	 .desc = "Instruction architecturally executed, mispredicted branch"
	},
	{.name = "BR__MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .equiv = "BR_MIS_PRED_RETIRED",
	 .code = 0x22,
	 .desc = "Instruction architecturally executed, mispredicted branch"
	},
	{.name = "STALL_FRONTEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x23,
	 .desc = "No operation issued because of the frontend"
	},
	{.name = "STALL_BACKEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x24,
	 .desc = "No operation issued because of the backend"
	},
	{.name = "L1D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x25,
	 .desc = "Level 1 data TLB access"
	},
	{.name = "L1I_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x26,
	 .desc = "Level 1 instruction TLB access"
	},
	{.name = "L3D_CACHE_ALLOCATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x29,
	 .desc = "Attributable Level 3 unified cache allocation without refill"
	},
	{.name = "L3D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2a,
	 .desc = "Attributable Level 3 unified cache refill"
	},
	{.name = "L3D_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2b,
	 .desc = "Attributable Level 3 unified cache access"
	},
	{.name = "L2D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2d,
	 .desc = "Attributable Level 2 unified TLB refill"
	},
	{.name = "L2D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2f,
	 .desc = "Attributable Level 2 unified TLB access"
	},
	{.name = "DTLB_WALK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x34,
	 .desc = "Access to data TLB that caused a page table walk"
	},
	{.name = "ITLB_WALK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x35,
	 .desc = "Access to instruction TLB that caused a page table walk"
	},
	{.name = "LL_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x36,
	 .desc = "Last level cache access, read"
	},
	{.name = "LL_CACHE_MISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x37,
	 .desc = "Last level cache miss, read"
	},
	{.name = "REMOTE_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x38,
	 .desc = "Access to another socket in a multi-socket system, read"
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
	{.name = "L2D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x50,
	 .desc = "Level 2 cache access, read"
	},
	{.name = "L2D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x51,
	 .desc = "Level 2 cache access, write"
	},
	{.name = "L2D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x52,
	 .desc = "Level 2 cache refill, read"
	},
	{.name = "L2D_CACHE_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x53,
	 .desc = "Level 2 cache refill, write"
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
	 .desc = "Operation speculatively executed, integer data processing"
	},
	{.name = "ASE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x74,
	 .desc = "Operation speculatively executed, Advanced SIMD instruction"
	},
	{.name = "VFP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x75,
	 .desc = "Operation speculatively executed, floating-point instruction"
	},
	{.name = "PC_WRITE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x76,
	 .desc = "Operation speculatively executed, software change of the PC"
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
	 .desc = "Branch speculatively executed, procedure return"
	},
	{.name = "BR_INDIRECT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7a,
	 .desc = "Branch speculatively executed, indirect branch"
	},
	{.name = "EXC_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x86,
	 .desc = "Exception taken, IRQ"
	},
	{.name = "EXC_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x87,
	 .desc = "Exception taken, FIQ"
	},
	{.name = "L3D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xa0,
	 .desc = "Attributable Level 3 unified cache access, read"
	},
	{.name = "L3D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xa2,
	 .desc = "Attributable Level 3 unified cache refill, read"
	},
	{.name = "L3D_CACHE_REFILL_PREFETCH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc0,
	 .desc = "Level 3 cache refill due to prefetch"
	},
	{.name = "L2D_CACHE_REFILL_PREFETCH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc1,
	 .desc = "Level 2 cache refill due to prefetch"
	},
	{.name = "L1D_CACHE_REFILL_PREFETCH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc2,
	 .desc = "Level 1 data cache refill due to prefetch"
	},
	{.name = "L2D_WS_MODE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc3,
	 .desc = "Level 2 cache write streaming mode"
	},
	{.name = "L1D_WS_MODE_ENTRY",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc4,
	 .desc = "Level 1 data cache entering write streaming mode"
	},
	{.name = "L1D_WS_MODE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc5,
	 .desc = "Level 1 data cache write streaming mode"
	},
	{.name = "PREDECODE_ERROR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc6,
	 .desc = "Predecode error"
	},
	{.name = "L3D_WS_MODE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc7,
	 .desc = "Level 3 cache write streaming mode"
	},
	{.name = "BR_COND_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xc9,
	 .desc = "Predicted conditional branch executed"
	},
	{.name = "BR_INDIRECT_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xca,
	 .desc = "Indirect branch mis-predicted"
	},
	{.name = "BR_INDIRECT_ADDR_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xcb,
	 .desc = "Indirect branch mis-predicted due to address mis-compare"
	},
	{.name = "BR_COND_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xcc,
	 .desc = "Conditional branch mis-predicted"
	},
	{.name = "BR_INDIRECT_ADDR_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xcd,
	 .desc = "Indirect branch with predicted address executed"
	},
	{.name = "BR_RETURN_ADDR_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xce,
	 .desc = "Procedure return with predicted address executed"
	},
	{.name = "BR_RETURN_ADDR_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xcf,
	 .desc = "Procedure return mis-predicted due to address mis-compare"
	},
	{.name = "L2D_LLWALK_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd0,
	 .desc = "Level 2 TLB last-level walk cache access"
	},
	{.name = "L2D_LLWALK_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd1,
	 .desc = "Level 2 TLB last-level walk cache refill"
	},
	{.name = "L2D_L2WALK_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd2,
	 .desc = "Level 2 TLB level-2 walk cache access"
	},
	{.name = "L2D_L2WALK_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd3,
	 .desc = "Level 2 TLB level-2 walk cache refill"
	},
	{.name = "L2D_S2_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd4,
	 .desc = "Level 2 TLB IPA cache access"
	},
	{.name = "L2D_S2_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd5,
	 .desc = "Level 2 TLB IPA cache refill"
	},
	{.name = "L2D_CACHE_STASH_DROPPED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xd6,
	 .desc = "Level 2 cache stash dropped"
	},
	{.name = "STALL_FRONTEND_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe1,
	 .desc = "No operation issued due to the frontend, cache miss"
	},
	{.name = "STALL_FRONTEND_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe2,
	 .desc = "No operation issued due to the frontend, TLB miss"
	},
	{.name = "STALL_FRONTEND_PDERR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe3,
	 .desc = "No operation issued due to the frontend, pre-decode error"
	},
	{.name = "STALL_BACKEND_ILOCK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe4,
	 .desc = "No operation issued due to the backend interlock"
	},
	{.name = "STALL_BACKEND_ILOCK_AGU",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe5,
	 .desc = "No operation issued due to the backend, interlock, AGU"
	},
	{.name = "STALL_BACKEND_ILOCK_FPU",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe6,
	 .desc = "No operation issued due to the backend, interlock, FPU"
	},
	{.name = "STALL_BACKEND_LD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe7,
	 .desc = "No operation issued due to the backend, load"
	},
	{.name = "STALL_BACKEND_ST",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe8,
	 .desc = "No operation issued due to the backend, store"
	},
	{.name = "STALL_BACKEND_LD_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xe9,
	 .desc = "No operation issued due to the backend, load, cache miss"
	},
	{.name = "STALL_BACKEND_LD_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xea,
	 .desc = "No operation issued due to the backend, load, TLB miss"
	},
	{.name = "STALL_BACKEND_ST_STB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xeb,
	 .desc = "No operation issued due to the backend, store, STB full"
	},
	{.name = "STALL_BACKEND_ST_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xec,
	 .desc = "No operation issued due to the backend, store, TLB miss"
	},
};
