/*
 * NVIDIA Olympus Core PMU Events
 * Contributed by Thomas Makin <tmakin@nvidia.com>
 *
 * Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
 * References:
 *  - ARM Architecture Reference Manual (ARMv9)
 *  - NVIDIA Tegra410 Technical Reference Manual
 */


static const arm_entry_t arm_olympus_pe[] = {
    /*
     * Architectural events
     */
    {.name = "SW_INCR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0000,
     .desc = "Instruction Architecturally Executed, Condition Code Check Pass, Software Increment"
    },
    {.name = "L1I_CACHE_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0001,
     .desc = "L1 I-Cache Refill"
    },
    {.name = "L1I_TLB_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0002,
     .desc = "L1 Instruction TLB Refill"
    },
    {.name = "L1D_CACHE_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0003,
     .desc = "L1 D-Cache Refill"
    },
    {.name = "L1D_CACHE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0004,
     .desc = "L1 D-Cache Access"
    },
    {.name = "L1D_TLB_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0005,
     .desc = "L1 Data TLB Refill"
    },
    {.name = "INST_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0008,
     .desc = "Instruction Architecturally Executed"
    },
    {.name = "EXC_TAKEN",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0009,
     .desc = "Exception Taken"
    },
    {.name = "EXC_RETURN",
     .modmsk = ARMV9_ATTRS,
     .code = 0x000a,
     .desc = "Instruction Architecturally Executed, Condition Code Check Pass, Exception Return"
    },
    {.name = "CID_WRITE_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x000b,
     .desc = "Instruction Architecturally Executed, Condition Code Check Pass, Write to CONTEXTIDR"
    },
    {.name = "BR_IMMED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x000d,
     .desc = "Branch Instruction Architecturally Executed, Immediate"
    },
    {.name = "BR_RETURN_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x000e,
     .desc = "Branch Instruction Architecturally Executed, Procedure Return, Taken"
    },
    {.name = "BR_MIS_PRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0010,
     .desc = "Branch Instruction Speculatively Executed, Mis-predicted or Not Predicted"
    },
    {.name = "CPU_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0011,
     .desc = "Cycle"
    },
    {.name = "BR_PRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0012,
     .desc = "Predictable Branch Instruction Speculatively Executed"
    },
    {.name = "MEM_ACCESS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0013,
     .desc = "Data Memory Access"
    },
    {.name = "L1I_CACHE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0014,
     .desc = "L1 I-Cache Access"
    },
    {.name = "L1D_CACHE_WB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0015,
     .desc = "L1 D-Cache Write-back"
    },
    {.name = "L2D_CACHE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0016,
     .desc = "L2 D-Cache Access"
    },
    {.name = "L2D_CACHE_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0017,
     .desc = "L2 D-Cache Refill"
    },
    {.name = "L2D_CACHE_WB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0018,
     .desc = "L2 D-Cache Write-back"
    },
    {.name = "BUS_ACCESS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0019,
     .desc = "Bus Access"
    },
    {.name = "MEMORY_ERROR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x001a,
     .desc = "Local Memory Error"
    },
    {.name = "INST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x001b,
     .desc = "Operation Speculatively Executed"
    },
    {.name = "TTBR_WRITE_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x001c,
     .desc = "Instruction Architecturally Executed, Condition Code Check Pass, Write to TTBR"
    },
    {.name = "BUS_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x001d,
     .desc = "Bus Cycle"
    },
    {.name = "CHAIN",
     .modmsk = ARMV9_ATTRS,
     .code = 0x001e,
     .desc = "Chain A Pair of Event Counters"
    },
    {.name = "BR_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0021,
     .desc = "Instruction Architecturally Executed, Branch"
    },
    {.name = "BR_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0022,
     .desc = "Branch Instruction Architecturally Executed, Mispredicted"
    },
    {.name = "STALL_FRONTEND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0023,
     .desc = "No Operation Sent for Execution due to the Frontend"
    },
    {.name = "STALL_BACKEND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0024,
     .desc = "No Operation Sent for Execution due to the Backend"
    },
    {.name = "L1D_TLB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0025,
     .desc = "L1 Data TLB Access"
    },
    {.name = "L1I_TLB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0026,
     .desc = "L1 Instruction TLB Access"
    },
    {.name = "L3D_CACHE_ALLOCATE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0029,
     .desc = "L3 D-Cache Allocation without Refill."
    },
    {.name = "L3D_CACHE_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x002a,
     .desc = "L3 D-Cache Refill."
    },
    {.name = "L3D_CACHE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x002b,
     .desc = "L3 D-Cache Access"
    },
    {.name = "L2D_TLB_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x002d,
     .desc = "L2 Data TLB Refill"
    },
    {.name = "L2D_TLB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x002f,
     .desc = "L2 Data TLB Access"
    },
    {.name = "REMOTE_ACCESS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0031,
     .desc = "Access to Another Socket in a Multi-Socket System"
    },
    {.name = "DTLB_WALK",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0034,
     .desc = "Data TLB Access with at Least One Translation Table Walk"
    },
    {.name = "ITLB_WALK",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0035,
     .desc = "Instruction TLB Access with at Least One Translation Table Walk"
    },
    {.name = "LL_CACHE_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0036,
     .desc = "Last Level Cache Access, Read"
    },
    {.name = "LL_CACHE_MISS_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0037,
     .desc = "Last level Cache Miss, Read"
    },
    {.name = "L1D_CACHE_LMISS_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0039,
     .desc = "L1 D-Cache Long-Latency Read Miss"
    },
    {.name = "OP_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x003a,
     .desc = "Micro-Operation Architecturally Executed"
    },
    {.name = "OP_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x003b,
     .desc = "Micro-Operation Speculatively Executed"
    },
    {.name = "STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x003c,
     .desc = "No Operation Sent for Execution"
    },
    {.name = "STALL_SLOT_BACKEND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x003d,
     .desc = "No Operation Sent for Execution on a Slot due to the Backend"
    },
    {.name = "STALL_SLOT_FRONTEND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x003e,
     .desc = "No Operation Sent for Execution on a Slot due to the Frontend"
    },
    {.name = "STALL_SLOT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x003f,
     .desc = "No Operation Sent for Execution on a Slot"
    },
    {.name = "L1D_CACHE_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0040,
     .desc = "L1 D-Cache Access, Read"
    },
    {.name = "L1D_CACHE_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0041,
     .desc = "L1 D-Cache Access, Write"
    },
    {.name = "L1D_CACHE_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0042,
     .desc = "L1 D-Cache Refill, Read"
    },
    {.name = "L1D_CACHE_REFILL_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0043,
     .desc = "L1 D-Cache Refill, Write"
    },
    {.name = "L1D_CACHE_REFILL_INNER",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0044,
     .desc = "L1 D-Cache Refill, Inner"
    },
    {.name = "L1D_CACHE_REFILL_OUTER",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0045,
     .desc = "L1 D-Cache Refill, Outer"
    },
    {.name = "L1D_CACHE_WB_VICTIM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0046,
     .desc = "L1 D-Cache Write-back, Victim"
    },
    {.name = "L1D_CACHE_WB_CLEAN",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0047,
     .desc = "L1 D-Cache Write-back, Cleaning and Coherency"
    },
    {.name = "L1D_CACHE_INVAL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0048,
     .desc = "L1 D-Cache Invalidate"
    },
    {.name = "L1D_TLB_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x004c,
     .desc = "L1 Data TLB Refill, Read"
    },
    {.name = "L1D_TLB_REFILL_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x004d,
     .desc = "L1 Data TLB Refill, Write"
    },
    {.name = "L1D_TLB_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x004e,
     .desc = "L1 Data TLB Access, Read"
    },
    {.name = "L1D_TLB_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x004f,
     .desc = "L1 Data TLB Access, Write"
    },
    {.name = "L2D_CACHE_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0050,
     .desc = "L2 D-Cache Access, Read"
    },
    {.name = "L2D_CACHE_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0051,
     .desc = "L2 D-Cache Access, Write"
    },
    {.name = "L2D_CACHE_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0052,
     .desc = "L2 D-Cache Refill, Read"
    },
    {.name = "L2D_CACHE_REFILL_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0053,
     .desc = "L2 D-Cache Refill, Write"
    },
    {.name = "L2D_CACHE_WB_VICTIM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0056,
     .desc = "L2 D-Cache Write-back, Victim"
    },
    {.name = "L2D_CACHE_WB_CLEAN",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0057,
     .desc = "L2 D-Cache Write-back, Cleaning and Coherency"
    },
    {.name = "L2D_CACHE_INVAL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0058,
     .desc = "L2 D-Cache Invalidate"
    },
    {.name = "L2D_TLB_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x005c,
     .desc = "L2 Data TLB Refill, Read"
    },
    {.name = "L2D_TLB_REFILL_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x005d,
     .desc = "L2 Data TLB Refill, Write"
    },
    {.name = "L2D_TLB_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x005e,
     .desc = "L2 Data TLB Access, Read"
    },
    {.name = "L2D_TLB_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x005f,
     .desc = "L2 Data TLB Access, Write"
    },
    {.name = "BUS_ACCESS_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0060,
     .desc = "Bus Access, Read"
    },
    {.name = "BUS_ACCESS_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0061,
     .desc = "Bus Access, Write"
    },
    {.name = "MEM_ACCESS_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0066,
     .desc = "Data Memory Access, Read"
    },
    {.name = "MEM_ACCESS_WR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0067,
     .desc = "Data Memory Access, Write"
    },
    {.name = "UNALIGNED_LD_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0068,
     .desc = "Unaligned Access, Read"
    },
    {.name = "UNALIGNED_ST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0069,
     .desc = "Unaligned Access, Write"
    },
    {.name = "UNALIGNED_LDST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x006a,
     .desc = "Unaligned Access"
    },
    {.name = "LDREX_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x006c,
     .desc = "Exclusive Operation Speculatively Executed, Load-Exclusive"
    },
    {.name = "STREX_PASS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x006d,
     .desc = "Exclusive Operation Speculatively Executed, Store-Exclusive Pass"
    },
    {.name = "STREX_FAIL_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x006e,
     .desc = "Exclusive Operation Speculatively Executed, Store-Exclusive Fail"
    },
    {.name = "STREX_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x006f,
     .desc = "Exclusive Operation Speculatively Executed, Store-Exclusive"
    },
    {.name = "LD_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0070,
     .desc = "Operation Speculatively Executed, Load"
    },
    {.name = "ST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0071,
     .desc = "Operation Speculatively Executed, Store"
    },
    {.name = "LDST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0072,
     .desc = "Operation Speculatively Executed, Load or Store"
    },
    {.name = "DP_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0073,
     .desc = "Operation Speculatively Executed, Integer Data Processing"
    },
    {.name = "ASE_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0074,
     .desc = "Operation Speculatively Executed, Advanced SIMD"
    },
    {.name = "VFP_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0075,
     .desc = "Operation Speculatively Executed, Scalar Floating-Point"
    },
    {.name = "PC_WRITE_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0076,
     .desc = "Operation Speculatively Executed, Software Change of the PC"
    },
    {.name = "CRYPTO_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0077,
     .desc = "Operation Speculatively Executed, Cryptographic Instruction"
    },
    {.name = "BR_IMMED_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0078,
     .desc = "Branch Speculatively Executed, Immediate Branch"
    },
    {.name = "BR_RETURN_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0079,
     .desc = "Branch Speculatively Executed, Procedure Return"
    },
    {.name = "BR_INDIRECT_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x007a,
     .desc = "Branch Speculatively Executed, Indirect Branch"
    },
    {.name = "ISB_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x007c,
     .desc = "Barrier Speculatively Executed, ISB"
    },
    {.name = "DSB_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x007d,
     .desc = "Barrier Speculatively Executed, DSB"
    },
    {.name = "DMB_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x007e,
     .desc = "Barrier Speculatively Executed, DMB"
    },
    {.name = "CSDB_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x007f,
     .desc = "Barrier Speculatively Executed, CSDB"
    },
    {.name = "EXC_UNDEF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0081,
     .desc = "Exception Taken, Other Synchronous"
    },
    {.name = "EXC_SVC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0082,
     .desc = "Exception Taken, Supervisor Call"
    },
    {.name = "EXC_PABORT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0083,
     .desc = "Exception Taken, Instruction Abort"
    },
    {.name = "EXC_DABORT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0084,
     .desc = "Exception Taken, Data Abort or SError"
    },
    {.name = "EXC_IRQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0086,
     .desc = "Exception Taken, IRQ"
    },
    {.name = "EXC_FIQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0087,
     .desc = "Exception Taken, FIQ"
    },
    {.name = "EXC_SMC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0088,
     .desc = "Exception Taken, Secure Monitor Call"
    },
    {.name = "EXC_HVC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x008a,
     .desc = "Exception Taken, Hypervisor Call"
    },
    {.name = "EXC_TRAP_PABORT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x008b,
     .desc = "Exception Taken, Instruction Abort not Taken Locally"
    },
    {.name = "EXC_TRAP_DABORT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x008c,
     .desc = "Exception Taken, Data Abort or SError not Taken Locally"
    },
    {.name = "EXC_TRAP_OTHER",
     .modmsk = ARMV9_ATTRS,
     .code = 0x008d,
     .desc = "Exception Taken, Other Traps not Taken Locally"
    },
    {.name = "EXC_TRAP_IRQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x008e,
     .desc = "Exception Taken, IRQ not Taken Locally"
    },
    {.name = "EXC_TRAP_FIQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x008f,
     .desc = "Exception Taken, FIQ not Taken Locally"
    },
    {.name = "RC_LD_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0090,
     .desc = "Release Consistency Operation Speculatively Executed, Load-Acquire"
    },
    {.name = "RC_ST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0091,
     .desc = "Release Consistency Operation Speculatively Executed, Store-Release"
    },
    {.name = "L3D_CACHE_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x00a0,
     .desc = "L3 D-Cache Access, Read."
    },
    {.name = "L3D_CACHE_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x00a2,
     .desc = "L3 D-Cache Refill, Read"
    },
    {.name = "SAMPLE_POP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4000,
     .desc = "Statistical Profiling Sample Population"
    },
    {.name = "SAMPLE_FEED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4001,
     .desc = "Statistical Profiling Sample Taken"
    },
    {.name = "SAMPLE_FILTRATE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4002,
     .desc = "Statistical Profiling Sample Taken and not Removed by Filtering"
    },
    {.name = "SAMPLE_COLLISION",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4003,
     .desc = "Statistical Profiling Sample Collided with Previous Sample"
    },
    {.name = "CNT_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4004,
     .desc = "Constant Frequency Cycles"
    },
    {.name = "STALL_BACKEND_MEM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4005,
     .desc = "Memory Stall Cycles"
    },
    {.name = "L1I_CACHE_LMISS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4006,
     .desc = "L1 I-Cache Long-Latency Miss"
    },
    {.name = "L2D_CACHE_LMISS_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4009,
     .desc = "L2 D-Cache Long-Latency Read Miss"
    },
    {.name = "L3D_CACHE_LMISS_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x400b,
     .desc = "L3 D-Cache Long-Latency Read Miss."
    },
    {.name = "TRB_WRAP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x400c,
     .desc = "Trace Buffer Current Write Pointer Wrapped"
    },
    {.name = "TRCEXTOUT0",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4010,
     .desc = "Trace Unit External Output 0."
    },
    {.name = "TRCEXTOUT1",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4011,
     .desc = "Trace Unit External Output 1."
    },
    {.name = "TRCEXTOUT2",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4012,
     .desc = "Trace Unit External Output 2."
    },
    {.name = "TRCEXTOUT3",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4013,
     .desc = "Trace Unit External Output 3."
    },
    {.name = "CTI_TRIGOUT4",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4018,
     .desc = "Cross-Trigger Interface Output Trigger 4."
    },
    {.name = "CTI_TRIGOUT5",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4019,
     .desc = "Cross-Trigger Interface Output Trigger 5."
    },
    {.name = "CTI_TRIGOUT6",
     .modmsk = ARMV9_ATTRS,
     .code = 0x401a,
     .desc = "Cross-Trigger Interface Output Trigger 6."
    },
    {.name = "CTI_TRIGOUT7",
     .modmsk = ARMV9_ATTRS,
     .code = 0x401b,
     .desc = "Cross-Trigger Interface Output Trigger 7."
    },
    {.name = "LDST_ALIGN_LAT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4020,
     .desc = "Access with Additional Latency from Alignment"
    },
    {.name = "LD_ALIGN_LAT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4021,
     .desc = "Load with Additional Latency from Alignment"
    },
    {.name = "ST_ALIGN_LAT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x4022,
     .desc = "Store with Additional Latency from Alignment"
    },
    {.name = "SIMD_INST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8004,
     .desc = "Operation Speculatively Executed, SIMD"
    },
    {.name = "ASE_INST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8005,
     .desc = "Operation Speculatively Executed, Advanced SIMD"
    },
    {.name = "SVE_INST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8006,
     .desc = "Operation Speculatively Executed, SVE, including Load and Store"
    },
    {.name = "FP_HP_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8014,
     .desc = "Floating-Point Operation Speculatively Executed, Half Precision"
    },
    {.name = "FP_SP_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8018,
     .desc = "Floating-Point Operation Speculatively Executed, Single Precision"
    },
    {.name = "FP_DP_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x801c,
     .desc = "Floating-Point Operation Speculatively Executed, Double Precision"
    },
    {.name = "INT_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8040,
     .desc = "Integer Operation Speculatively Executed"
    },
    {.name = "SVE_PRED_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8074,
     .desc = "Operation Speculatively Executed, SVE Predicated"
    },
    {.name = "SVE_PRED_EMPTY_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8075,
     .desc = "Operation Speculatively Executed, SVE Predicated with No Active Predicates"
    },
    {.name = "SVE_PRED_FULL_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8076,
     .desc = "Operation Speculatively Executed, SVE Predicated with All Active Predicates"
    },
    {.name = "SVE_PRED_PARTIAL_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8077,
     .desc = "Operation Speculatively Executed, SVE Predicated with Partially Active Predicates"
    },
    {.name = "SVE_PRED_NOT_FULL_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8079,
     .desc = "SVE Predicated Operations Speculatively Executed with No Active or Partially Active Predicates"
    },
    {.name = "PRF_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8087,
     .desc = "Operation Speculatively Executed, Prefetch"
    },
    {.name = "SVE_LDFF_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80bc,
     .desc = "Operation Speculatively Executed, SVE Frst-Fault Load"
    },
    {.name = "SVE_LDFF_FAULT_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80bd,
     .desc = "Operation Speculatively Executed, SVE First-Fault Load Which Sets FFR Bit to 0b0"
    },
    {.name = "FP_SCALE_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c0,
     .desc = "Scalable Floating-Point Element ALU Operations Speculatively Executed"
    },
    {.name = "FP_FIXED_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c1,
     .desc = "Non-scalable Foating-Point Element ALU Operations Speculatively Executed"
    },
    {.name = "FP_HP_SCALE_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c2,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Largest Type Is Half-Precision Floating-Point."
    },
    {.name = "FP_HP_FIXED_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c3,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Largest Type Is Half-Precision Floating-Point."
    },
    {.name = "FP_SP_SCALE_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c4,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Largest Type Is Single-Precision Floating-Point."
    },
    {.name = "FP_SP_FIXED_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c5,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Largest Type Is Single-Precision Floating-Point"
    },
    {.name = "FP_DP_SCALE_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c6,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Largest Type Is Double-Precision Floating-Point"
    },
    {.name = "FP_DP_FIXED_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80c7,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Largest Type Is Double-Precision Floating-Point"
    },
    {.name = "ASE_SVE_INT8_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80e3,
     .desc = "Integer Operation Speculatively Executed, Advanced SIMD or SVE 8-bit"
    },
    {.name = "ASE_SVE_INT16_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80e7,
     .desc = "Integer Operation Speculatively Executed, Advanced SIMD or SVE 16-bit"
    },
    {.name = "ASE_SVE_INT32_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80eb,
     .desc = "Integer Operation Speculatively Executed, Advanced SIMD or SVE 32-bit"
    },
    {.name = "ASE_SVE_INT64_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x80ef,
     .desc = "Integer Operation Speculatively Executed, Advanced SIMD or SVE 64-bit"
    },
    {.name = "BR_INDNR_TAKEN_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x810c,
     .desc = "Branch Instruction Architecturally Executed, Indirect Excluding Procedure Return, Taken"
    },
    {.name = "BR_IMMED_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8110,
     .desc = "Branch Instruction Architecturally Executed, Predicted Immediate"
    },
    {.name = "BR_IMMED_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8111,
     .desc = "Branch Instruction Architecturally Executed, Mis-predicted Immediate"
    },
    {.name = "BR_IND_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8112,
     .desc = "Branch Instruction Architecturally Executed, Predicted Indirect"
    },
    {.name = "BR_IND_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8113,
     .desc = "Branch Instruction Architecturally Executed, Mis-predicted Indirect"
    },
    {.name = "BR_RETURN_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8114,
     .desc = "Branch Instruction Architecturally Executed, Predicted Procedure Return"
    },
    {.name = "BR_RETURN_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8115,
     .desc = "Branch Instruction Architecturally Executed, Mis-predicted Procedure Return"
    },
    {.name = "BR_INDNR_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8116,
     .desc = "Branch Instruction Architecturally Executed, Predicted Indirect Excluding Procedure Return"
    },
    {.name = "BR_INDNR_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8117,
     .desc = "Branch Instruction Architecturally Executed, Mis-predicted Indirect Excluding Procedure Return"
    },
    {.name = "BR_TAKEN_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8118,
     .desc = "Branch Instruction Architecturally Executed, Predicted Branch, Taken"
    },
    {.name = "BR_TAKEN_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8119,
     .desc = "Branch Instruction Architecturally Executed, Mis-predicted Branch, Taken"
    },
    {.name = "BR_SKIP_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x811a,
     .desc = "Branch Instruction Architecturally Executed, Predicted Branch, Not Taken"
    },
    {.name = "BR_SKIP_MIS_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x811b,
     .desc = "Branch Instruction Architecturally Executed, Mis-predicted Branch, Not taken"
    },
    {.name = "BR_PRED_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x811c,
     .desc = "Branch Instruction Architecturally Executed, Predicted Branch"
    },
    {.name = "BR_IND_RETIRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x811d,
     .desc = "Instruction Architecturally Executed, Indirect Branch"
    },
    {.name = "BRB_FILTRATE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x811f,
     .desc = "Branch Record Captured"
    },
    {.name = "INST_FETCH_PERCYC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8120,
     .desc = "Event in Progress, INST FETCH"
    },
    {.name = "MEM_ACCESS_RD_PERCYC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8121,
     .desc = "Event in Progress, MEM ACCESS RD"
    },
    {.name = "INST_FETCH",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8124,
     .desc = "Instruction Memory Access"
    },
    {.name = "DTLB_WALK_PERCYC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8128,
     .desc = "Event in Progress, DTLB WALK"
    },
    {.name = "ITLB_WALK_PERCYC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8129,
     .desc = "Event in Progress, ITLB WALK"
    },
    {.name = "SAMPLE_FEED_BR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x812a,
     .desc = "Statistical Profiling Sample Taken, Branch"
    },
    {.name = "SAMPLE_FEED_LD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x812b,
     .desc = "Statistical Profiling Sample Taken, Load"
    },
    {.name = "SAMPLE_FEED_ST",
     .modmsk = ARMV9_ATTRS,
     .code = 0x812c,
     .desc = "Statistical Profiling Sample Taken, Store"
    },
    {.name = "SAMPLE_FEED_OP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x812d,
     .desc = "Statistical Profiling Sample Taken, Matching Operation Type"
    },
    {.name = "SAMPLE_FEED_EVENT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x812e,
     .desc = "Statistical Profiling Sample Taken, Matching Events"
    },
    {.name = "SAMPLE_FEED_LAT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x812f,
     .desc = "Statistical Profiling Sample Taken, Exceeding Minimum Latency"
    },
    {.name = "L1D_TLB_RW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8130,
     .desc = "L1 Data TLB Demand Access"
    },
    {.name = "L1I_TLB_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8131,
     .desc = "L1 Instruction TLB Demand Access"
    },
    {.name = "L1D_TLB_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8132,
     .desc = "L1 Data TLB Software Preload"
    },
    {.name = "L1I_TLB_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8133,
     .desc = "L1 Instruction TLB Software Preload"
    },
    {.name = "DTLB_HWUPD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8134,
     .desc = "Data TLB Hardware Update of Translation Table"
    },
    {.name = "ITLB_HWUPD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8135,
     .desc = "Instruction TLB Hardware Update of Translation Table"
    },
    {.name = "DTLB_STEP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8136,
     .desc = "Data TLB Translation Table Walk, Step"
    },
    {.name = "ITLB_STEP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8137,
     .desc = "Instruction TLB Translation Table Walk, Step"
    },
    {.name = "DTLB_WALK_LARGE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8138,
     .desc = "Data TLB Large Page Translation Table Walk"
    },
    {.name = "ITLB_WALK_LARGE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8139,
     .desc = "Instruction TLB Large Page Translation Table Walk"
    },
    {.name = "DTLB_WALK_SMALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x813a,
     .desc = "Data TLB Small Page Translation Table Walk"
    },
    {.name = "ITLB_WALK_SMALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x813b,
     .desc = "Instruction TLB Small Page Translation Table Walk"
    },
    {.name = "DTLB_WALK_RW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x813c,
     .desc = "Data TLB Demand Access with At Least One Translation Table Walk"
    },
    {.name = "ITLB_WALK_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x813d,
     .desc = "Instruction TLB Demand Access with At Least One Translation Table Walk"
    },
    {.name = "DTLB_WALK_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x813e,
     .desc = "Data TLB Software Preload Access with At Least One Translation Table Walk"
    },
    {.name = "ITLB_WALK_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x813f,
     .desc = "Instruction TLB Software Preload Access with At Least One Translation Table Walk"
    },
    {.name = "L1D_CACHE_RW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8140,
     .desc = "L1 D-Cache Demand Access"
    },
    {.name = "L1I_CACHE_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8141,
     .desc = "L1 I-Cache Demand Fetch"
    },
    {.name = "L1D_CACHE_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8142,
     .desc = "L1 D-Cache Software Preload"
    },
    {.name = "L1I_CACHE_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8143,
     .desc = "L1 I-Cache Software Preload"
    },
    {.name = "L1D_CACHE_MISS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8144,
     .desc = "L1 D-Cache Demand Access Miss"
    },
    {.name = "L1I_CACHE_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8145,
     .desc = "L1 I-Cache Hardware Prefetch"
    },
    {.name = "L1D_CACHE_REFILL_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8146,
     .desc = "L1 D-Cache Refill, Software Preload"
    },
    {.name = "L1I_CACHE_REFILL_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8147,
     .desc = "L1 I-Cache Refill, Software Preload"
    },
    {.name = "L2D_CACHE_RW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8148,
     .desc = "L2 D-Cache Demand Access"
    },
    {.name = "L2D_CACHE_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x814a,
     .desc = "L2 D-Cache Software Preload"
    },
    {.name = "L2D_CACHE_MISS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x814c,
     .desc = "L2 D-Cache Demand Access Miss"
    },
    {.name = "L2D_CACHE_REFILL_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x814e,
     .desc = "L2 D-Cache Refill, Software Preload"
    },
    {.name = "L3D_CACHE_RW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8150,
     .desc = "L3 D-Cache Demand Access"
    },
    {.name = "L3D_CACHE_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8151,
     .desc = "L3D_CACHE_PRFM, L3 D-Cache Software Prefetch"
    },
    {.name = "L3D_CACHE_MISS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8152,
     .desc = "L3 D-Cache Demand Access Miss"
    },
    {.name = "L3D_CACHE_REFILL_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8153,
     .desc = "L3 D-Cache Refill, Software Prefetch"
    },
    {.name = "L1D_CACHE_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8154,
     .desc = "L1 D-Cache Hardware Prefetch"
    },
    {.name = "L2D_CACHE_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8155,
     .desc = "L2 D-Cache Hardware Prefetch."
    },
    {.name = "L3D_CACHE_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8156,
     .desc = "L3D_CACHE_PRFM, L3 D-Cache Software Prefetch"
    },
    {.name = "STALL_FRONTEND_MEMBOUND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8158,
     .desc = "Frontend Stall Cycles, Memory Bound"
    },
    {.name = "STALL_FRONTEND_L1I",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8159,
     .desc = "Frontend Stall Cycles, L1 I-Cache"
    },
    {.name = "STALL_FRONTEND_MEM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x815b,
     .desc = "Frontend Stall Cycles, Last Level PE Cache or Memory"
    },
    {.name = "STALL_FRONTEND_TLB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x815c,
     .desc = "Frontend Stall Cycles, TLB"
    },
    {.name = "STALL_FRONTEND_CPUBOUND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8160,
     .desc = "Frontend Stall Cycles, Processor Bound"
    },
    {.name = "STALL_FRONTEND_FLOW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8161,
     .desc = "Frontend Stall Cycles, Flow Control"
    },
    {.name = "STALL_FRONTEND_FLUSH",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8162,
     .desc = "Frontend Stall Cycles, Flush Recovery"
    },
    {.name = "STALL_BACKEND_MEMBOUND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8164,
     .desc = "Backend Stall Cycles, Memory Bound"
    },
    {.name = "STALL_BACKEND_L1D",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8165,
     .desc = "Backend Stall Cycles, L1 D-Cache"
    },
    {.name = "STALL_BACKEND_TLB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8167,
     .desc = "Backend Stall Cycles, TLB"
    },
    {.name = "STALL_BACKEND_ST",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8168,
     .desc = "Backend Stall Cycles, Store"
    },
    {.name = "STALL_BACKEND_CPUBOUND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x816a,
     .desc = "Backend Stall Cycles, Processor Bound"
    },
    {.name = "STALL_BACKEND_BUSY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x816b,
     .desc = "Backend Stall Cycles, Backend Busy"
    },
    {.name = "STALL_BACKEND_ILOCK",
     .modmsk = ARMV9_ATTRS,
     .code = 0x816c,
     .desc = "Backend Stall Cycles, Input Dependency"
    },
    {.name = "STALL_BACKEND_RENAME",
     .modmsk = ARMV9_ATTRS,
     .code = 0x816d,
     .desc = "Backend Stall Cycles, Rename Full"
    },
    {.name = "L1I_CACHE_REFILL_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81b8,
     .desc = "L1 I-Cache Refill, Hardware Prefetch"
    },
    {.name = "L1D_CACHE_REFILL_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81bc,
     .desc = "L1 D-Cache Refill, Hardware Prefetch"
    },
    {.name = "L2D_CACHE_REFILL_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81bd,
     .desc = "L2 D-Cache Refill, Hardware Prefetch"
    },
    {.name = "L3D_CACHE_REFILL_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81be,
     .desc = "L3 D-Cache Refill, Hardware Prefetch"
    },
    {.name = "L1I_CACHE_HIT_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81c0,
     .desc = "L1 I-Cache Demand Fetch Hit"
    },
    {.name = "L1D_CACHE_HIT_RW_FPRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81dc,
     .desc = "L1 D-Cache Demand Access First Hit, Fetched by Software Prefetch"
    },
    {.name = "L1D_CACHE_HIT_RW_FHWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81ec,
     .desc = "L1 D-Cache Demand Access First Hit, Fetched by Hardware Prefetcher"
    },
    {.name = "L1I_CACHE_HIT_RD_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81f0,
     .desc = "L1 I-Cache Demand Fetch First Hit, Fetched by Prefetch"
    },
    {.name = "L1D_CACHE_HIT_RW_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x81fc,
     .desc = "L1 D-Cache Demand Access First Hit, Fetched by Prefetch"
    },
    {.name = "L1I_CACHE_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8200,
     .desc = "L1 I-Cache Hit"
    },
    {.name = "L1I_CACHE_HIT_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8208,
     .desc = "L1 I-Cache Software Preload Hit"
    },
    {.name = "L1I_LFB_HIT_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8240,
     .desc = "L1 I-Cache Demand Fetch Line-Fill Buffer Hit"
    },
    {.name = "L1D_LFB_HIT_RW_FPRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x825c,
     .desc = "L1 D-Cache Demand Access Line-Fill Buffer First Hit, Recently Fetched by Software Prefetch"
    },
    {.name = "L1D_LFB_HIT_RW_FHWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x826c,
     .desc = "L1 D-Cache Demand Access Line-Fill Buffer First Hit, Recently Fetched by Hardware Prefetcher"
    },
    {.name = "L1D_LFB_HIT_RW_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x827c,
     .desc = "L1 D-Cache Demand Access Line-Fill Buffer First Hit, Recently Fetched by Prefetch"
    },
    {.name = "L2D_CACHE_REFILL_PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x828d,
     .desc = "L2 D-Cache Refill, Prefetch"
    },
    {.name = "L3D_CACHE_REFILL_PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x828e,
     .desc = "L3 D-Cache Refill, Prefetch"
    },
    {.name = "FP_SP_FIXED_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8480,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is Single-Precision Floating-Point"
    },
    {.name = "FP_HP_FIXED_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8482,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is Half-Precision Floating-Point"
    },
    {.name = "FP_BF16_FIXED_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8483,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is BFloat16 Floating-Point"
    },
    {.name = "FP_FP8_FIXED_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8484,
     .desc = "Non-Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is 8-bit Floating-Point"
    },
    {.name = "FP_SP_SCALE_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x8488,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is Single-Precision Floating-Point."
    },
    {.name = "FP_HP_SCALE_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x848a,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is Half-Precision Floating-Point"
    },
    {.name = "FP_BF16_SCALE_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x848b,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is BFloat16 Floating-Point"
    },
    {.name = "FP_FP8_SCALE_MIN_OPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x848c,
     .desc = "Scalable Element Arithmetic Operations Speculatively Executed, Smallest Type Is 8-bit Floating-Point"
    },

    /*
     * Implementation-defined events
     */
    {.name = "L1I_PRFM_REQ_DROP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x00e1,
     .desc = "L1 I-cache software prefetch dropped."
    },
    {.name = "L1_PF_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0100,
     .desc = "L1 prefetch requests, refilled to L1 cache."
    },
    {.name = "L2D_CACHE_IF_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0108,
     .desc = "L2 D-cache refill, Instruction fetch. The Event counts demand Instruction fetch that causes a refill of the L2 cache or L1 cache of this PE, from outside of those caches."
    },
    {.name = "L2D_CACHE_TBW_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0109,
     .desc = "L2 D-cache refill, Page table walk. The Event counts demand translation table walk that causes a refill of the L2 cache or L1 cache of this PE, from outside of those caches."
    },
    {.name = "L2D_CACHE_PF_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x010a,
     .desc = "L2 D-cache refill, prefetch. The Event counts L1 or L2 hardware or software prefetch accesses that causes a refill of the L2 cache or L1 cache of this PE, from outside of those caches."
    },
    {.name = "L2D_LFB_HIT_RWL1PRF_FHWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x010b,
     .desc = "L2 line fill buffer demand Read, demand Write or L1 prefetch first hit, fetched by hardware prefetch. The Event counts each of the following access that hit the line-fill buffer when the same cache line is already being fetched due to an L2 hardware prefetcher. * Demand Read or Write * L1I-HWPRF * L1D-HWPRF * L1I PRFM * L1D PRFM These accesses hit a cache line that is currently being loaded into the L2 cache as a result of a hardware prefetcher to the same line. Consequently, this access does not initiate a new refill but waits for the completion of the previous refill. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "L1D_TLB_REFILL_RD_PF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x010e,
     .desc = "L1 Data TLB refill, Read, prefetch."
    },
    {.name = "L2TLB_PF_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x010f,
     .desc = "L2 Data TLB refill, Read, prefetch. The Event counts MMU refills due to internal PFStream requests."
    },
    {.name = "SPEC_RET_STACK_FULL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x011d,
     .desc = "The Event counts predict pipe stalls due to speculative return address predictor full."
    },
    {.name = "MOPS_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x011f,
     .desc = "Macro-ops speculatively decoded."
    },
    {.name = "FLUSH",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0120,
     .desc = "The Event counts both the CT flush and BX flush. The BR_MIS_PRED counts the BX flushes. So the FLUSH-BR_MIS_PRED gives the CT flushes."
    },
    {.name = "FLUSH_MEM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0121,
     .desc = "Flushes due to memory hazards. This only includes CT flushes."
    },
    {.name = "FLUSH_BAD_BRANCH",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0122,
     .desc = "Flushes due to bad predicted Branch. This only includes CT flushes."
    },
    {.name = "FLUSH_STDBYPASS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0123,
     .desc = "Flushes due to bad predecode. This only includes CT flushes."
    },
    {.name = "FLUSH_ISB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0124,
     .desc = "Flushes due to ISB or similar side-effects. This only includes CT flushes."
    },
    {.name = "FLUSH_OTHER",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0125,
     .desc = "Flushes due to other hazards. This only includes CT flushes."
    },
    {.name = "STORE_STREAM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0126,
     .desc = "Stored lines in streaming no-Write-allocate mode."
    },
    {.name = "NUKE_RAR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0127,
     .desc = "Load/Store nuke due to Read-after-Read ordering hazard."
    },
    {.name = "NUKE_RAW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0128,
     .desc = "Load/Store nuke due to Read-after-Write ordering hazard."
    },
    {.name = "L1_PF_GEN_PAGE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0129,
     .desc = "Load/Store prefetch to L1 generated, Page mode."
    },
    {.name = "L1_PF_GEN_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x012a,
     .desc = "Load/Store prefetch to L1 generated, stride mode."
    },
    {.name = "L2_PF_GEN_LD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x012b,
     .desc = "Load prefetch to L2 generated."
    },
    {.name = "LS_PF_TRAIN_TABLE_ALLOC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x012d,
     .desc = "LS prefetch train table entry allocated."
    },
    {.name = "LS_PF_GEN_TABLE_ALLOC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0130,
     .desc = "The Event counts the number of cycles with at least one table allocation, for L2 hardware prefetches (including the SW PRFM that are converted into hardware prefetches due to D-TLB miss). LS prefetch gen table allocation (for L2 prefetches)."
    },
    {.name = "LS_PF_GEN_TABLE_ALLOC_PF_PEND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0131,
     .desc = "The Event counts the number of cycles in which at least one hardware prefetch is dropped due to the inability to identify a victim when the generation table is full. The hardware prefetch considered here includes the software PRFM that is converted into hardware prefetches due to D-TLB miss."
    },
    {.name = "TBW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0132,
     .desc = "Tablewalks."
    },
    {.name = "S1L2_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0134,
     .desc = "Translation cache hit on S1L2 walk cache entry."
    },
    {.name = "S1L1_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0135,
     .desc = "Translation cache hit on S1L1 walk cache entry."
    },
    {.name = "S1L0_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0136,
     .desc = "Translation cache hit on S1L0 walk cache entry."
    },
    {.name = "S2L2_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0137,
     .desc = "Translation cache hit for S2L2 IPA walk cache entry."
    },
    {.name = "IPA_REQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0138,
     .desc = "Translation cache lookups for IPA to PA entries."
    },
    {.name = "IPA_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0139,
     .desc = "Translation cache refills for IPA to PA entries."
    },
    {.name = "S1_FLT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x013a,
     .desc = "Stage1 tablewalk fault."
    },
    {.name = "S2_FLT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x013b,
     .desc = "Stage2 tablewalk fault."
    },
    {.name = "COLT_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x013c,
     .desc = "Aggregated page refill."
    },
    {.name = "L1_PF_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0145,
     .desc = "L1 prefetch requests, hitting in L1 cache."
    },
    {.name = "L1_PF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0146,
     .desc = "L1 prefetch requests."
    },
    {.name = "CACHE_LS_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0147,
     .desc = "L2 D-cache refill, Load/Store."
    },
    {.name = "CACHE_PF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0148,
     .desc = "L2 prefetch requests."
    },
    {.name = "CACHE_PF_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0149,
     .desc = "L2 prefetch requests, hitting in L2 cache."
    },
    {.name = "UNUSED_PF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0150,
     .desc = "L2 unused prefetch."
    },
    {.name = "PFT_SENT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0151,
     .desc = "L2 prefetch TGT sent. Note that PFT_SENT != PFT_USEFUL + PFT_DROP. There may be PFT_SENT for which the accesses resulted in a SLC hit."
    },
    {.name = "PFT_USEFUL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0152,
     .desc = "L2 prefetch TGT useful."
    },
    {.name = "PFT_DROP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0153,
     .desc = "L2 prefetch TGT dropped."
    },
    {.name = "BUS_REQUEST_REQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0154,
     .desc = "Bus request, request."
    },
    {.name = "BUS_REQUEST_RETRY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0155,
     .desc = "Bus request, retry."
    },
    {.name = "FLAG_DISP_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0158,
     .desc = "Rename stalled due to FRF(Flag register file) full."
    },
    {.name = "GEN_DISP_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0159,
     .desc = "Rename stalled due to GRF (General-purpose register file) full."
    },
    {.name = "VEC_DISP_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x015a,
     .desc = "Rename stalled due to VRF (Vector register file) full."
    },
    {.name = "SX_IQ_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x015c,
     .desc = "Dispatch stalled due to IQ full, SX."
    },
    {.name = "MX_IQ_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x015d,
     .desc = "Dispatch stalled due to IQ full, MX."
    },
    {.name = "LS_IQ_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x015e,
     .desc = "Dispatch stalled due to IQ full, LS."
    },
    {.name = "VX_IQ_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x015f,
     .desc = "Dispatch stalled due to IQ full, VX."
    },
    {.name = "MCQ_FULL_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0160,
     .desc = "Dispatch stalled due to MCQ full."
    },
    {.name = "LRQ_FULL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0162,
     .desc = "The Event counts the number of cycles the LRQ is full."
    },
    {.name = "FETCH_FQ_EMPTY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0163,
     .desc = "Fetch Queue empty cycles."
    },
    {.name = "FPG2",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0164,
     .desc = "Forward progress guarantee. Medium range livelock triggered."
    },
    {.name = "FPG",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0165,
     .desc = "Forward progress guarantee. Tofu global livelock buster is triggered."
    },
    {.name = "DEADBLOCK",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0172,
     .desc = "Write-back evictions converted to Dataless EVICT. The victim line is deemed deadblock if the likeliness of a reuse is low. The Core uses Dataless evict to evict a deadblock; And it uses a evict with Data to evict an L2 line that is not a deadblock."
    },
    {.name = "PF_PRQ_ALLOC_PF_PEND",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0173,
     .desc = "L1 prefetch prq allocation (replacing pending)."
    },
    {.name = "L1I_HWPRF_REQ_DROP",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0174,
     .desc = "L1 I-cache hardware prefetch dropped."
    },
    {.name = "FETCH_ICACHE_INSTR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0178,
     .desc = "Instructions fetched from I-cache."
    },
    {.name = "L2D_CACHE_HIT_RWL1PRF_FHWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0179,
     .desc = "L2 D-cache demand Read, demand Write and L1 prefetch hit, fetched by hardware prefetch.. The Event counts each demand Read, demand Write and L1 hardware or software prefetch request that hit an L2 D-cache line that was refilled into L2 D-cache in response to an L2 hardware prefetch. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "NEAR_CAS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x017b,
     .desc = "Near atomics: compare and swap."
    },
    {.name = "NEAR_CAS_PASS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x017c,
     .desc = "Near atomics: compare and swap pass."
    },
    {.name = "FAR_CAS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x017d,
     .desc = "Far atomics: compare and swap."
    },
    {.name = "BR_PRED_BTB_CTX_UPDATE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x017e,
     .desc = "Branch context table update."
    },
    {.name = "BR_SPEC_PRED_TAKEN",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0180,
     .desc = "Number of Predicted Taken from Branch Predictor."
    },
    {.name = "BR_SPEC_PRED_TAKEN_FROM_L2BTB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0181,
     .desc = "Number of Predicted Taken Branch from L2 BTB."
    },
    {.name = "BR_SPEC_PRED_TAKEN_MULTI",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0182,
     .desc = "Number of Predicted Taken for Polymorphic Branch."
    },
    {.name = "BR_SPEC_PRED_STATIC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0185,
     .desc = "Number of post fetch prediction."
    },
    {.name = "L2_BTB_RELOAD_MAIN_BTB",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0186,
     .desc = "Number of completed L1 BTB update initiated by L2 BTB hit which swap Branch information between L1 BTB and L2 BTB."
    },
    {.name = "BR_MIS_PRED_DIR_RESOLVED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0188,
     .desc = "Number of Branch misprediction due to direction misprediction."
    },
    {.name = "BR_MIS_PRED_DIR_UNCOND_RESOLVED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0189,
     .desc = "Number of Branch misprediction due to direction misprediction for unconditional Branches."
    },
    {.name = "BR_MIS_PRED_DIR_UNCOND_DIRECT_RESOLVED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x018a,
     .desc = "Number of Branch misprediction due to direction misprediction for unconditional direct Branches."
    },
    {.name = "BR_PRED_MULTI_RESOLVED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x018b,
     .desc = "Number of resolved branch which made prediction by polymorphic indirect predictor."
    },
    {.name = "BR_MIS_PRED_MULTI_RESOLVED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x018c,
     .desc = "Number of branch misprediction which made prediction by polymorphic indirect predictor."
    },
    {.name = "L1_PF_GEN_MCMC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x018f,
     .desc = "Load/Store prefetch to L1 generated, MCMC."
    },
    {.name = "PF_MODE_0_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0190,
     .desc = "Number of cycles in which the hardware prefetcher is in the most aggressive mode."
    },
    {.name = "PF_MODE_1_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0191,
     .desc = "Number of cycles in which the hardware prefetcher is in the more aggressive mode."
    },
    {.name = "PF_MODE_2_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0192,
     .desc = "Number of cycles in which the hardware prefetcher is in the less aggressive mode."
    },
    {.name = "PF_MODE_3_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0193,
     .desc = "Number of cycles in which the hardware prefetcher is in the most conservative mode."
    },
    {.name = "TXREQ_LIMIT_MAX_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0194,
     .desc = "Number of cycles in which the dynamic TXREQ limit is the L2_TQ_SIZE."
    },
    {.name = "TXREQ_LIMIT_3QUARTER_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0195,
     .desc = "Number of cycles in which the dynamic TXREQ limit is between 3/4 of the L2_TQ_SIZE and the L2_TQ_SIZE-1."
    },
    {.name = "TXREQ_LIMIT_HALF_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0196,
     .desc = "Number of cycles in which the dynamic TXREQ limit is between 1/2 of the L2_TQ_SIZE and 3/4 of the L2_TQ_SIZE."
    },
    {.name = "TXREQ_LIMIT_1QUARTER_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0197,
     .desc = "Number of cycles in which the dynamic TXREQ limit is between 1/4 of the L2_TQ_SIZE and 1/2 of the L2_TQ_SIZE."
    },
    {.name = "L2_CHI_CBUSY0",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0198,
     .desc = "Number of RXDAT or RXRSP response received width CBusy of 0."
    },
    {.name = "L2_CHI_CBUSY1",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0199,
     .desc = "Number of RXDAT or RXRSP response received width CBusy of 1."
    },
    {.name = "L2_CHI_CBUSY2",
     .modmsk = ARMV9_ATTRS,
     .code = 0x019a,
     .desc = "Number of RXDAT or RXRSP response received width CBusy of 2."
    },
    {.name = "L2_CHI_CBUSY3",
     .modmsk = ARMV9_ATTRS,
     .code = 0x019b,
     .desc = "Number of RXDAT or RXRSP response received width CBusy of 3."
    },
    {.name = "PREFETCH_LATE_CMC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x019d,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by CMC prefetch request."
    },
    {.name = "PREFETCH_LATE_BO",
     .modmsk = ARMV9_ATTRS,
     .code = 0x019e,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by BO prefetch request."
    },
    {.name = "PREFETCH_LATE_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x019f,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by STRIDE prefetch request."
    },
    {.name = "PREFETCH_LATE_SPATIAL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a0,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by SPATIAL prefetch request."
    },
    {.name = "PREFETCH_LATE_TBW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a2,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by TBW prefetch request."
    },
    {.name = "PREFETCH_LATE_PAGE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a3,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by PAGE prefetch request."
    },
    {.name = "PREFETCH_LATE_GSMS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a4,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by GSMS prefetch request."
    },
    {.name = "PREFETCH_LATE_SIP_CONS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a5,
     .desc = "LS/readclean or LS/readunique lookup hit on TQ entry allocated by SIP_CONS prefetch request."
    },
    {.name = "PREFETCH_REFILL_CMC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a6,
     .desc = "PF/prefetch or PF/readclean request from CMC pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_BO",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a7,
     .desc = "PF/prefetch or PF/readclean request from BO pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a8,
     .desc = "PF/prefetch or PF/readclean request from STRIDE pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_SPATIAL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01a9,
     .desc = "PF/prefetch or PF/readclean request from SPATIAL pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_TBW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ab,
     .desc = "PF/prefetch or PF/readclean request from TBW pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_PAGE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ac,
     .desc = "PF/prefetch or PF/readclean request from PAGE pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_GSMS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ad,
     .desc = "PF/prefetch or PF/readclean request from GSMS pf engine filled the L2 cache."
    },
    {.name = "PREFETCH_REFILL_SIP_CONS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ae,
     .desc = "PF/prefetch or PF/readclean request from SIP_CONS pf engine filled the L2 cache."
    },
    {.name = "CACHE_HIT_LINE_PF_CMC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01af,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by CMC prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_BO",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b0,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by BO prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b1,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by STRIDE prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_SPATIAL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b2,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by SPATIAL prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_TBW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b4,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by TBW prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_PAGE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b5,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by PAGE prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_GSMS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b6,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by GSMS prefetch request."
    },
    {.name = "CACHE_HIT_LINE_PF_SIP_CONS",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b7,
     .desc = "LS/readclean or LS/readunique lookup hit in L2 cache on line filled by SIP_CONS prefetch request."
    },
    {.name = "L2D_CACHE_L1PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b8,
     .desc = "L2 D-cache access, L1 hardware or software prefetch. The Event counts L1 Hardware or software prefetch access to L2 D-cache."
    },
    {.name = "L2D_CACHE_REFILL_L1PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01b9,
     .desc = "L2 D-cache refill, L1 hardware or software prefetch. The Event counts each access counted by L2D_CACHE_L1PRF that causes a refill of the L2 cache or any L1 cache of this PE, from outside of those caches."
    },
    {.name = "PREFETCH_LATE_STORE_ISSUE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ba,
     .desc = "The Event counts the number of demand requests that matches a Store-issue prefetcher's pending refill request. These are called late prefetch requests and are still counted as useful prefetcher requests for the sake of accuracy and coverage measurements."
    },
    {.name = "PREFETCH_LATE_STORE_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01bb,
     .desc = "The Event counts the number of demand requests that matches a Store-stride prefetcher's pending refill request. These are called late prefetch requests and are still counted as useful prefetcher requests for the sake of accuracy and coverage measurements."
    },
    {.name = "PREFETCH_LATE_PC_OFFSET",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01bc,
     .desc = "The Event counts the number of demand requests that matches a PC-offset prefetcher's pending refill request. These are called late prefetch requests and are still counted as useful prefetcher requests for the sake of accuracy and coverage measurements."
    },
    {.name = "PREFETCH_LATE_IFUPF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01bd,
     .desc = "The Event counts the number of demand requests that matches a IFU prefetcher's pending refill request. These are called late prefetch requests and are still counted as useful prefetcher requests for the sake of accuracy and coverage measurements."
    },
    {.name = "PREFETCH_REFILL_STORE_ISSUE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01be,
     .desc = "The Event counts the number of cache refills due to Store-Issue prefetcher."
    },
    {.name = "PREFETCH_REFILL_STORE_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01bf,
     .desc = "The Event counts the number of cache refills due to Store-stride prefetcher."
    },
    {.name = "PREFETCH_REFILL_PC_OFFSET",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c0,
     .desc = "The Event counts the number of cache refills due to PC-offset prefetcher."
    },
    {.name = "PREFETCH_REFILL_IFUPF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c1,
     .desc = "The Event counts the number of cache refills due to IFU prefetcher."
    },
    {.name = "CACHE_HIT_LINE_PF_STORE_ISSUE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c2,
     .desc = "The Event counts the number of first hit to a cache line filled by Store-issue prefetcher."
    },
    {.name = "CACHE_HIT_LINE_PF_STORE_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c3,
     .desc = "The Event counts the number of first hit to a cache line filled by Store-stride prefetcher."
    },
    {.name = "CACHE_HIT_LINE_PF_PC_OFFSET",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c4,
     .desc = "The Event counts the number of first hit to a cache line filled by PC-offset prefetcher."
    },
    {.name = "CACHE_HIT_LINE_PF_IFUPF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c5,
     .desc = "The Event counts the number of first hit to a cache line filled by IFU prefetcher."
    },
    {.name = "L2_PF_GEN_ST_ISSUE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c6,
     .desc = "Store-issue prefetch to L2 generated."
    },
    {.name = "L2_PF_GEN_ST_STRIDE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01c7,
     .desc = "Store-stride prefetch to L2 generated"
    },
    {.name = "L2_TQ_OUTSTANDING",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01cb,
     .desc = "Outstanding tracker count, per cycle. The Event increments by the number of valid entries pertaining to this thread in the L2TQ, in each cycle. The Event can be used to calculate the occupancy of L2TQ by dividing this by the CPU_CYCLES Event. The L2TQ queue tracks the outstanding Read, Write ,and Snoop transactions. The Read transaction and the Write transaction entries are attributable to PE, whereas the Snoop transactions are not always attributable to PE."
    },
    {.name = "TXREQ_LIMIT_COUNT_CYCLES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01cc,
     .desc = "The Event increments by the dynamic TXREQ value, in each cycle. This is a companion Event of TXREQ_LIMIT_MAX_CYCLES, TXREQ_LIMIT_3QUARTER_CYCLES, TXREQ_LIMIT_HALF_CYCLES, and TXREQ_LIMIT_1QUARTER_CYCLES."
    },
    {.name = "L3DPRFM_TO_L2PRQ_CONVERTED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ce,
     .desc = "The Event counts the number of Converted-L3D-PRFMs. These are indeed L3D PRFM and activities around these PRFM are counted by the L3D_CACHE_PRFM, L3D_CACHE_REFILL_PRFM and L3D_CACHE_REFILL Events."
    },
    {.name = "PRD_DISP_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01cf,
     .desc = "Rename stalled due to predicate registers (physical) are full."
    },
    {.name = "TLBI_LOCAL_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01d0,
     .desc = "A non-broadcast TLBI Instruction executed (Speculatively or otherwise) on *this* PE."
    },
    {.name = "TLBI_BROADCAST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01d1,
     .desc = "A broadcast TLBI Instruction executed (Speculatively or otherwise) on *this* PE."
    },
    {.name = "DVM_TLBI_RCVD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01d2,
     .desc = "The Event counts the number of TLBI DVM message received over CHI interface, for *this* Core."
    },
    {.name = "DSB_COMMITING_LOCAL_TLBI",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01d6,
     .desc = "The Event counts the number of DSB that are retired and committed at least one local TLBI Instruction. This Event increments no more than once (in a cycle) even if the DSB commits multiple local TLBI Instruction."
    },
    {.name = "DSB_COMMITING_BROADCAST_TLBI",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01d7,
     .desc = "The Event counts the number of DSB that are retired and committed at least one broadcast TLBI Instruction. This Event increments no more than once (in a cycle) even if the DSB commits multiple broadcast TLBI Instruction."
    },
    {.name = "CSDB_STALL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e0,
     .desc = "Rename stalled due to CSDB."
    },
    {.name = "CPU_SLOT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e1,
     .desc = "Entitled CPU slots. The Event counts the number of slots. When in ST mode, this Event shall increment by PMMIR_EL1.SLOTS quantities, and when in SMT partitioned resource mode (regardless of in WFI state or otherwise), this Event is incremented by PMMIR_EL1.SLOTS/2 quantities."
    },
    {.name = "STALL_SLOT_FRONTEND_WITHOUT_MISPRED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e2,
     .desc = "Stall slot frontend during non-mispredicted branch. The Event counts the STALL_STOT_FRONTEND Events, except for the 4 cycles following a mispredicted branch Event or 4 cycles following a commit flush&restart Event."
    },
    {.name = "L1I_CACHE_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e3,
     .desc = "L1 I-cache refill, Read. The Event counts demand Instruction fetch that causes a refill of the L1 I-cache of this PE, from outside of this cache."
    },
    {.name = "BR_RGN_RECLAIM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e4,
     .desc = "The Event counts the Indirect predictor entries flushed by region reclamation."
    },
    {.name = "BR_SPEC_PRED_ALN_REDIR",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e7,
     .desc = "BPU predict pipe align redirect (either AL-APQ hit/miss)."
    },
    {.name = "L3D_CACHE_RWL1PRFL2PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e8,
     .desc = "L3 cache access, demand Read, demand Write, L1 hardware or software prefetch or L2 hardware or software prefetch. The Event counts each access to L3 D-cache due to the following: * Demand Read or Write. * L1 Hardware or software prefetch. * L2 Hardware or software prefetch."
    },
    {.name = "L3D_CACHE_REFILL_RWL1PRFL2PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01e9,
     .desc = "L3 cache refill, demand Read, demand Write, L1 hardware or software prefetch or L2 hardware or software prefetch. The Event counts each access counted by L3D_CACHE_RWL1PRFL2PRF that causes a refill of the L3 cache, or any L1 or L2 cache of this PE, from outside of those caches."
    },
    {.name = "L1I_CFC_ENTRIES",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ea,
     .desc = "The Event counts the CFC (Cache Fill Control) entries. The CFC is the fill buffer for I-cache."
    },
    {.name = "L1DPRFM_L2DPRFM_TO_L2PRQ_CONVERTED",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01eb,
     .desc = "The Event counts the number of Converted-L1D-PRFMs and Converted-L2D-PRFM. Activities involving the Converted-L1D-PRFM are counted by the L1D_CACHE_PRFM. However they are *not* counted by the L1D_CACHE_REFILL_PRFM, and L1D_CACHE_REFILL, as these Converted-L1D-PRFM are treated as L2 D hardware prefetches. Activities around the Converted-L1D-PRFMs and Converted-L2D-PRFMs are counted by the L2D_CACHE_PRFM, L2D_CACHE_REFILL_PRFM and L2D_CACHE_REFILL Events."
    },
    {.name = "PREFETCH_LATE_CONVERTED_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ec,
     .desc = "The Event counts the number of demand requests that matches a Converted-L1D-PRFM or Converted-L2D-PRFM pending refill request at L2 D-cache. These are called late prefetch requests and are still counted as useful prefetcher requests for the sake of accuracy and coverage measurements. Note that this Event is not counted by the L2D_CACHE_HIT_RWL1PRF_LATE_HWPRF, though the Converted-L1D-PRFM or Converted-L2D-PRFM are replayed by the L2PRQ."
    },
    {.name = "PREFETCH_REFILL_CONVERTED_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ed,
     .desc = "The Event counts the number of L2 D-cache refills due to Converted-L1D-PRFM or Converted-L2D-PRFM. Note : L2D_CACHE_REFILL_PRFM is inclusive of PREFETCH_REFILL_PRFM_CONVERTED, where both the PREFETCH_REFILL_PRFM_CONVERTED and the L2D_CACHE_REFILL_PRFM increment when L2 D-cache refills due to Converted-L1D-PRFM or Converted-L2D-PRFM."
    },
    {.name = "CACHE_HIT_LINE_PF_CONVERTED_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ee,
     .desc = "The Event counts the number of first hit to a cache line filled by Converted-L1D-PRFM or Converted-L2D-PRFM. Note that L2D_CACHE_HIT_RWL1PRF_FPRFM is inclusive of CACHE_HIT_LINE_PF_CONVERTED_PRFM, where both the CACHE_HIT_LINE_PF_CONVERTED_PRFM and the L2D_CACHE_HIT_RWL1PRF_FPRFM increment on a first hit to L2 D-cache filled by Converted-L1D-PRFM or Converted-L2D-PRFM."
    },
    {.name = "L1I_CACHE_INVAL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01ef,
     .desc = "L1 I-cache invalidate. The Event counts each explicit invalidation of a cache line in the L1 I-cache caused by: * Broadcast cache coherency operations from another CPU in the system. * Invalidation dues to capacity eviction in L2 D-cache. This Event does not count for the following conditions: * A cache refill invalidates a cache line. * A CMO which is executed on that CPU Core and invalidates a cache line specified by Set/Way. * Cache Maintenance Operations (CMO) that operate by a virtual address. Note that * CMOs that operate by Set/Way cannot be broadcast from one CPU Core to another. * The CMO is treated as No-op for the purposes of L1 I-cache line invalidation, as this Core implements fully coherent I-cache."
    },
    {.name = "TMS_ST_TO_SMT_LATENCY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f0,
     .desc = "The Event counts the number of CPU cycles spent on TMS for ST-to-SMT switch. This Event is counted by both the threads - The Event in both threads increment during TMS for ST-to-SMT switch."
    },
    {.name = "TMS_SMT_TO_ST_LATENCY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f1,
     .desc = "The Event counts the number of CPU cycles spent on TMS for SMT-to-ST switch. The count also includes the CPU cycles spend due to an aborted SMT-to-ST TMS attempt. This Event is counted only by the thread that is not in WFI."
    },
    {.name = "TMS_ST_TO_SMT_COUNT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f2,
     .desc = "The Event counts the number of completed TMS from ST-to-SMT. This Event is counted only by the active thread (the one that is not in WFI). Note: When an active thread enters the Debug state in ST-Full resource mode, it is switched to SMT mode. This is because the inactive thread cannot wake up while the other thread remains in the Debug state. To prEvent this issue, threads operating in ST-Full resource mode are transitioned to SMT mode upon entering Debug state. The Event count will also reflect such switches from ST to SMT mode. (Also see the (NV_CPUACTLR14_EL1.chka_prEvent_st_tx_to_smt_when_tx_in_debug_state bit to disable this behavior.)"
    },
    {.name = "TMS_SMT_TO_ST_COUNT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f3,
     .desc = "The Event counts the number of completed TMS from SMT-to-ST. This Event is counted only by the thread that is not in WFI."
    },
    {.name = "TMS_SMT_TO_ST_COUNT_ABRT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f4,
     .desc = "The Event counts the number of aborted TMS from SMT-to-ST. This Event is counted only by the thread that is not in WFI."
    },
    {.name = "L1D_CACHE_REFILL_RW",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f5,
     .desc = "L1 D-cache refill, demand Read and Write. The Event counts demand Read and Write accesses that causes a refill of the L1 D-cache of this PE, from outside of this cache."
    },
    {.name = "L3D_CACHE_REFILL_L2PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f6,
     .desc = "The Event counts each access counted by L3D_CACHE_L2PRF that causes a refill of the L3 cache, or any L1 or L2 cache of this PE, from outside of those caches."
    },
    {.name = "L3D_CACHE_HIT_RWL1PRFL2PRF_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x01f7,
     .desc = "L3 cache demand Read, demand Write , L1 prefetch L2 prefetch first hit, fetched by software or hardware prefetch. The Event counts each demand Read, demand Write , L1 hardware or software prefetch request and L2 hardware or software prefetch that hit an L3 D-cache line that was refilled into L3 D-cache in response to an L3 hardware prefetch or software prefetch. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "SIMD_CRYPTO_INST_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0200,
     .desc = "SIMD, SVE, and CRYPTO Instructions speculatively decoded."
    },
    {.name = "L2D_CACHE_BACKSNOOP_L1D_VIRT_ALIASING",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0201,
     .desc = "The Event counts when the L2 D-cache sends an invalidating back-snoop to the L1 D for an access initiated by the L1 D, where the corresponding line is already present in the L1 D-cache. The L2 D-cache line tags the PE that refilled the line. It also retains specific bits of the VA to identify virtually aliased addresses. The L1 D request requiring a back-snoop can originate either from the same PE that refilled the L2 D line or from a different PE. In either case, this Event only counts those back snoop where the requested VA mismatch the VA stored in the L2 D tag. This Event is counted only by PE that initiated the original request necessitating a back-snoop. Note : The L1 D is VIPT, it identifies this access as a miss. Conversely, as L2 is PIPT, it identifies this as a hit. L2 D utilizes the back-snoop mechanism to refill L1 D with the snooped Data."
    },
    {.name = "L0I_CACHE_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0202,
     .desc = "The Event counts the number of predict blocks serviced out of L0 I-cache. Note: The L0 I-cache performs at most 4 L0 I look-up in a cycle. Two of which are to service PB from L0 I. And the other two to refill L0 I-cache from L1 I. This Event count only the L0 I-cache lookup pertaining to servicing the PB from L0 I."
    },
    {.name = "L0I_CACHE_REFILL",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0203,
     .desc = "The Event counts the number of L0I cache refill from L1 I-cache."
    },
    {.name = "L1D_CACHE_REFILL_OUTER_LLC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0204,
     .desc = "The Event counts L1D_CACHE_REFILL from L3 D-cache."
    },
    {.name = "L1D_CACHE_REFILL_OUTER_DRAM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0205,
     .desc = "The Event counts L1D_CACHE_REFILL from local memory."
    },
    {.name = "L1D_CACHE_REFILL_OUTER_REMOTE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0206,
     .desc = "The Event counts L1D_CACHE_REFILL from a remote memory."
    },
    {.name = "INTR_LATENCY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0207,
     .desc = "The Event counts the number of cycles elapsed between when an Interrupt is recognized (after masking) to when a uop associated with the first Instruction in the destination exception level is allocated. If there is some other flush condition that pre-empts the Interrupt, then the cycles counted terminates early at the first Instruction executed after that flush. In the Event of dropped Interrupts (when an Interrupt is deasserted before it is taken), this counter measures the number of cycles that elapse from the moment an Interrupt is recognized (post-masking) until the Interrupt is dropped or deasserted."
    },
    {.name = "L2D_CACHE_RWL1PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0208,
     .desc = "L2 D-cache access, demand Read, demand Write or L1 hardware or software prefetch. The Event counts each access to L2 D-cache due to the following: * Demand Read or Write. * L1 Hardware or software prefetch."
    },
    {.name = "L2D_CACHE_REFILL_RWL1PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x020a,
     .desc = "L2 D-cache refill, demand Read, demand Write or L1 hardware or software prefetch. The Event counts each access counted by L2D_CACHE_RWL1PRF that causes a refill of the L2 cache, or any L1 cache of this PE, from outside of those caches."
    },
    {.name = "L2D_CACHE_HIT_RWL1PRF_FPRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x020c,
     .desc = "L2 D-cache demand Read, demand Write and L1 prefetch hit, fetched by software prefetch. The Event counts each demand Read, demand Write and L1 hardware or software prefetch request that hit an L2 D-cache line that was refilled into L2 D-cache in response to an L2 software prefetch. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "L2D_CACHE_HIT_RWL1PRF_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x020e,
     .desc = "L2 D-cache demand Read, demand Write and L1 prefetch hit, fetched by software or hardware prefetch. The Event counts each demand Read, demand Write and L1 hardware or software prefetch request that hit an L2 D-cache line that was refilled into L2 D-cache in response to an L2 hardware prefetch or software prefetch. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "L1I_CACHE_HIT_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0212,
     .desc = "The Event counts each hardware prefetch access that hits an L1 I-cache."
    },
    {.name = "L1I_LFB_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0215,
     .desc = "L1 Line fill buffer hit. The Event counts each Demand or software preload or hardware prefetch induced Instruction fetch that hits an L1 I-cache line that is in the process of being loaded into the L1 Instruction, and so does not generate a new refill, but has to wait for the previous refill to complete."
    },
    {.name = "L1I_LFB_HIT_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0216,
     .desc = "The Event counts each software prefetch access that hits a cache line that is in the process of being loaded into the L1 Instruction, and so does not generate a new refill, but has to wait for the previous refill to complete."
    },
    {.name = "L1I_LFB_HIT_HWPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0219,
     .desc = "The Event counts each hardware prefetch access that hits a cache line that is in the process of being loaded into the L1 Instruction, and so does not generate a new refill, but has to wait for the previous refill to complete."
    },
    {.name = "CWT_ALLOC_ENTRY",
     .modmsk = ARMV9_ATTRS,
     .code = 0x021c,
     .desc = "Cache Way Tracker Allocate entry."
    },
    {.name = "CWT_ALLOC_LINE",
     .modmsk = ARMV9_ATTRS,
     .code = 0x021d,
     .desc = "Cache Way Tracker Allocate line."
    },
    {.name = "CWT_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x021e,
     .desc = "Cache Way Tracker hit."
    },
    {.name = "CWT_HIT_TAG",
     .modmsk = ARMV9_ATTRS,
     .code = 0x021f,
     .desc = "Cache Way Tracker hit when ITAG lookup suppressed."
    },
    {.name = "CWT_REPLAY_TAG",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0220,
     .desc = "Cache Way Tracker causes ITAG replay due to miss when ITAG lookup suppressed."
    },
    {.name = "L1I_PRFM_REQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0221,
     .desc = "L1 I-cache software prefetch requests."
    },
    {.name = "L1I_HWPRF_REQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0222,
     .desc = "L1 I-cache hardware prefetch requests."
    },
    {.name = "L1I_TLB_REFILL_RD",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0223,
     .desc = "L1 Instruction TLB refills due to Demand miss."
    },
    {.name = "L1I_TLB_REFILL_PRFM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0224,
     .desc = "L1 Instruction TLB refills due to Software prefetch miss."
    },
    {.name = "L3D_CACHE_REFILL_IF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0225,
     .desc = "L3 cache refill, Instruction fetch. The Event counts demand Instruction fetch that causes a refill of the L3 cache, or any L1 or L2 cache of this PE, from outside of those caches."
    },
    {.name = "L3D_CACHE_REFILL_MM",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0226,
     .desc = "L3 cache refill, translation table walk access. The Event counts demand translation table access that causes a refill of the L3 cache, or any L1 or L2 cache of this PE, from outside of those caches."
    },
    {.name = "L3D_CACHE_REFILL_L1PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0227,
     .desc = "The Event counts each access counted by L3D_CACHE_L1PRF that causes a refill of the L3 cache, or any L1 or L2 cache of this PE, from outside of those caches."
    },
    {.name = "L1I_CACHE_HIT_PRFM_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0228,
     .desc = "L1 I-cache software prefetch access first hit, fetched by hardware or software prefetch. The Event counts each software preload access first hit where the cache line was fetched in response to a hadware prefetcher or software preload Instruction. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "L1I_CACHE_HIT_HWPRF_FPRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x022a,
     .desc = "L1 I-cache hardware prefetch access first hit, fetched by hardware or software prefetch. The Event counts each hardware prefetch access first hit where the cache line was fetched in response to a hardware or prefetch Instruction. Only the first hit is counted. After this Event is generated for a cache line, the Event is not generated again for the same cache line while it remains in the cache."
    },
    {.name = "L3D_CACHE_L1PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x022c,
     .desc = "The Event counts the L3 D-cache access due to L1 hardware prefetch of software prefetch request. The L1 hardware prefetch or software prefetch request that miss the L1I, L1D and L2 D-cache are counted by this counter"
    },
    {.name = "L3D_CACHE_L2PRF",
     .modmsk = ARMV9_ATTRS,
     .code = 0x022d,
     .desc = "The Event counts the L3 D-cache access due to L2 hardware prefetch of software prefetch request. The L2 hardware prefetch or software prefetch request that miss the L2 D-cache are counted by this counter"
    },
    {.name = "VPRED_LD_SPEC",
     .modmsk = ARMV9_ATTRS,
     .code = 0x022e,
     .desc = "The Event counts the number of Speculatively-executed-Load operations with addresses produced by the value-prediction mechanism. The loaded Data might be discarded if the predicted address differs from the actual address."
    },
    {.name = "VPRED_LD_SPEC_MISMATCH",
     .modmsk = ARMV9_ATTRS,
     .code = 0x022f,
     .desc = "The Event counts a subset of VPRED_LD_SPEC where the predicted Load address and the actual address mismatched."
    },
    {.name = "GPT_REQ",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0250,
     .desc = "GPT lookup."
    },
    {.name = "GPT_WC_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0251,
     .desc = "GPT lookup hit in Walk cache."
    },
    {.name = "GPT_PG_HIT",
     .modmsk = ARMV9_ATTRS,
     .code = 0x0252,
     .desc = "GPT lookup hit in TLB."
    }
};
