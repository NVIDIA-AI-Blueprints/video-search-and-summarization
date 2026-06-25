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
 * ARM Neoverse V3
 * References:
 *  - Arm Neoverse V3 Core TRM: https://developer.arm.com/documentation/107734/
 *  - https://github.com/ARM-software/data/blob/master/pmu/neoverse-v3.json
 */
static const arm_entry_t arm_neoverse_v3_pe[]={
	{.name = "SW_INCR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x00,
	 .desc = "Instruction architecturally executed, Condition code check pass, software increment Counts software writes to the PMSWINC_EL0 (software PMU increment) register"
	},
	{.name = "L1I_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x01,
	 .desc = "Level 1 instruction cache refill Counts cache line refills in the level 1 instruction cache caused by a missed instruction fetch"
	},
	{.name = "L1I_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x02,
	 .desc = "Level 1 instruction TLB refill Counts level 1 instruction TLB refills from any Instruction fetch"
	},
	{.name = "L1D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x03,
	 .desc = "Level 1 data cache refill Counts level 1 data cache refills caused by speculatively executed load or store operations that missed in the level 1 data cache"
	},
	{.name = "L1D_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x04,
	 .desc = "Level 1 data cache access Counts level 1 data cache accesses from any load/store operations"
	},
	{.name = "L1D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x05,
	 .desc = "Level 1 data TLB refill Counts level 1 data TLB accesses that resulted in TLB refills"
	},
	{.name = "INST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x08,
	 .desc = "Instruction architecturally executed Counts instructions that have been architecturally executed"
	},
	{.name = "EXC_TAKEN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x09,
	 .desc = "Exception taken Counts any taken architecturally visible exceptions such as IRQ, FIQ, SError, and other synchronous exceptions"
	},
	{.name = "EXC_RETURN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0a,
	 .desc = "Instruction architecturally executed, Condition code check pass, exception return Counts any architecturally executed exception return instructions"
	},
	{.name = "CID_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0b,
	 .desc = "Instruction architecturally executed, Condition code check pass, write to CONTEXTIDR Counts architecturally executed writes to the CONTEXTIDR_EL1 register, which usually contain the kernel PID and can be output with hardware trace"
	},
	{.name = "PC_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0c,
	 .desc = "Instruction architecturally executed, Condition code check pass, Software change of the PC Counts branch instructions that caused a change of Program Counter, which effectively causes a change in the control flow of the program"
	},
	{.name = "BR_IMMED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0d,
	 .desc = "Branch instruction architecturally executed, immediate Counts architecturally executed direct branches"
	},
	{.name = "BR_RETURN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0e,
	 .desc = "Branch instruction architecturally executed, procedure return, taken Counts architecturally executed procedure returns"
	},
	{.name = "BR_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x10,
	 .desc = "Branch instruction speculatively executed, mispredicted or not predicted Counts branches which are speculatively executed and mispredicted"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x11,
	 .desc = "Cycle Counts CPU clock cycles (not timer cycles)"
	},
	{.name = "BR_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x12,
	 .desc = "Predictable branch instruction speculatively executed Counts all speculatively executed branches"
	},
	{.name = "MEM_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x13,
	 .desc = "Data memory access Counts memory accesses issued by the CPU load store unit, where those accesses are issued due to load or store operations"
	},
	{.name = "L1I_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x14,
	 .desc = "Level 1 instruction cache access Counts instruction fetches which access the level 1 instruction cache"
	},
	{.name = "L1D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x15,
	 .desc = "Level 1 data cache write-back Counts write-backs of dirty data from the L1 data cache to the L2 cache"
	},
	{.name = "L2D_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x16,
	 .desc = "Level 2 data cache access Counts accesses to the level 2 cache due to data accesses"
	},
	{.name = "L2D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x17,
	 .desc = "Level 2 data cache refill Counts cache line refills into the level 2 cache"
	},
	{.name = "L2D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x18,
	 .desc = "Level 2 data cache write-back Counts write-backs of data from the L2 cache to outside the CPU"
	},
	{.name = "BUS_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x19,
	 .desc = "Bus access Counts memory transactions issued by the CPU to the external bus, including snoop requests and snoop responses"
	},
	{.name = "MEMORY_ERROR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1a,
	 .desc = "Local memory error Counts any detected correctable or uncorrectable physical memory errors (ECC or parity) in protected CPUs RAMs"
	},
	{.name = "INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1b,
	 .desc = "Operation speculatively executed Counts operations that have been speculatively executed"
	},
	{.name = "TTBR_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1c,
	 .desc = "Instruction architecturally executed, Condition code check pass, write to TTBR Counts architectural writes to TTBR0/1_EL1"
	},
	{.name = "BUS_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1d,
	 .desc = "Bus cycle Counts bus cycles in the CPU"
	},
	{.name = "BR_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21,
	 .desc = "Instruction architecturally executed, branch Counts architecturally executed branches, whether the branch is taken or not"
	},
	{.name = "BR_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x22,
	 .desc = "Branch instruction architecturally executed, mispredicted Counts branches counted by BR_RETIRED which were mispredicted and caused a pipeline flush"
	},
	{.name = "STALL_FRONTEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x23,
	 .desc = "No operation sent for execution due to the frontend Counts cycles when frontend could not send any micro-operations to the rename stage because of frontend resource stalls caused by fetch memory latency or branch prediction flow stalls"
	},
	{.name = "STALL_BACKEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x24,
	 .desc = "No operation sent for execution due to the backend Counts cycles whenever the rename unit is unable to send any micro-operations to the backend of the pipeline because of backend resource constraints"
	},
	{.name = "L1D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x25,
	 .desc = "Level 1 data TLB access Counts level 1 data TLB accesses caused by any memory load or store operation"
	},
	{.name = "L1I_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x26,
	 .desc = "Level 1 instruction TLB access Counts level 1 instruction TLB accesses, whether the access hits or misses in the TLB"
	},
	{.name = "L2D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2d,
	 .desc = "Level 2 data TLB refill Counts level 2 TLB refills caused by memory operations from both data and instruction fetch, except for those caused by TLB maintenance operations and hardware prefetches"
	},
	{.name = "L2D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2f,
	 .desc = "Level 2 data TLB access Counts level 2 TLB accesses except those caused by TLB maintenance operations"
	},
	{.name = "REMOTE_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x31,
	 .desc = "Access to another socket in a multi-socket system Counts accesses to another chip, which is implemented as a different CMN mesh in the system"
	},
	{.name = "DTLB_WALK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x34,
	 .desc = "Data TLB access with at least one translation table walk Counts number of demand data translation table walks caused by a miss in the L2 TLB and performing at least one memory access"
	},
	{.name = "ITLB_WALK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x35,
	 .desc = "Instruction TLB access with at least one translation table walk Counts number of instruction translation table walks caused by a miss in the L2 TLB and performing at least one memory access"
	},
	{.name = "LL_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x36,
	 .desc = "Last level cache access, read Counts read transactions that were returned from outside the core cluster"
	},
	{.name = "LL_CACHE_MISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x37,
	 .desc = "Last level cache miss, read Counts read transactions that were returned from outside the core cluster but missed in the system level cache"
	},
	{.name = "L1D_CACHE_LMISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x39,
	 .desc = "Level 1 data cache long-latency read miss Counts cache line refills into the level 1 data cache from any memory read operations, that incurred additional latency"
	},
	{.name = "OP_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3a,
	 .desc = "Micro-operation architecturally executed Counts micro-operations that are architecturally executed"
	},
	{.name = "OP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3b,
	 .desc = "Micro-operation speculatively executed Counts micro-operations speculatively executed"
	},
	{.name = "STALL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3c,
	 .desc = "No operation sent for execution Counts cycles when no operations are sent to the rename unit from the frontend or from the rename unit to the backend for any reason (either frontend or backend stall)"
	},
	{.name = "STALL_SLOT_BACKEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3d,
	 .desc = "No operation sent for execution on a Slot due to the backend Counts slots per cycle in which no operations are sent from the rename unit to the backend due to backend resource constraints"
	},
	{.name = "STALL_SLOT_FRONTEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3e,
	 .desc = "No operation sent for execution on a Slot due to the frontend Counts slots per cycle in which no operations are sent to the rename unit from the frontend due to frontend resource constraints"
	},
	{.name = "STALL_SLOT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3f,
	 .desc = "No operation sent for execution on a Slot Counts slots per cycle in which no operations are sent to the rename unit from the frontend or from the rename unit to the backend for any reason (either frontend or backend stall)"
	},
	{.name = "L1D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x40,
	 .desc = "Level 1 data cache access, read Counts level 1 data cache accesses from any load operation"
	},
	{.name = "L1D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x41,
	 .desc = "Level 1 data cache access, write Counts level 1 data cache accesses generated by store operations"
	},
	{.name = "L1D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x42,
	 .desc = "Level 1 data cache refill, read Counts level 1 data cache refills caused by speculatively executed load instructions where the memory read operation misses in the level 1 data cache"
	},
	{.name = "L1D_CACHE_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x43,
	 .desc = "Level 1 data cache refill, write Counts level 1 data cache refills caused by speculatively executed store instructions where the memory write operation misses in the level 1 data cache"
	},
	{.name = "L1D_CACHE_REFILL_INNER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x44,
	 .desc = "Level 1 data cache refill, inner Counts level 1 data cache refills where the cache line data came from caches inside the immediate cluster of the core"
	},
	{.name = "L1D_CACHE_REFILL_OUTER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x45,
	 .desc = "Level 1 data cache refill, outer Counts level 1 data cache refills for which the cache line data came from outside the immediate cluster of the core, like an SLC in the system interconnect or DRAM"
	},
	{.name = "L1D_CACHE_WB_VICTIM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x46,
	 .desc = "Level 1 data cache write-back, victim Counts dirty cache line evictions from the level 1 data cache caused by a new cache line allocation"
	},
	{.name = "L1D_CACHE_WB_CLEAN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x47,
	 .desc = "Level 1 data cache write-back, cleaning and coherency Counts write-backs from the level 1 data cache that are a result of a coherency operation made by another CPU"
	},
	{.name = "L1D_CACHE_INVAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x48,
	 .desc = "Level 1 data cache invalidate Counts each explicit invalidation of a cache line in the level 1 data cache caused by: - Cache Maintenance Operations (CMO) that operate by a virtual address"
	},
	{.name = "L1D_TLB_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4c,
	 .desc = "Level 1 data TLB refill, read Counts level 1 data TLB refills caused by memory read operations"
	},
	{.name = "L1D_TLB_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4d,
	 .desc = "Level 1 data TLB refill, write Counts level 1 data TLB refills caused by data side memory write operations"
	},
	{.name = "L1D_TLB_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4e,
	 .desc = "Level 1 data TLB access, read Counts level 1 data TLB accesses caused by memory read operations"
	},
	{.name = "L1D_TLB_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4f,
	 .desc = "Level 1 data TLB access, write Counts any L1 data side TLB accesses caused by memory write operations"
	},
	{.name = "L2D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x50,
	 .desc = "Level 2 data cache access, read Counts level 2 data cache accesses due to memory read operations"
	},
	{.name = "L2D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x51,
	 .desc = "Level 2 data cache access, write Counts level 2 cache accesses due to memory write operations"
	},
	{.name = "L2D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x52,
	 .desc = "Level 2 data cache refill, read Counts refills for memory accesses due to memory read operation counted by L2D_CACHE_RD"
	},
	{.name = "L2D_CACHE_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x53,
	 .desc = "Level 2 data cache refill, write Counts refills for memory accesses due to memory write operation counted by L2D_CACHE_WR"
	},
	{.name = "L2D_CACHE_WB_VICTIM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x56,
	 .desc = "Level 2 data cache write-back, victim Counts evictions from the level 2 cache because of a line being allocated into the L2 cache"
	},
	{.name = "L2D_CACHE_WB_CLEAN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x57,
	 .desc = "Level 2 data cache write-back, cleaning and coherency Counts write-backs from the level 2 cache that are a result of either: 1"
	},
	{.name = "L2D_CACHE_INVAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x58,
	 .desc = "Level 2 data cache invalidate Counts each explicit invalidation of a cache line in the level 2 cache by cache maintenance operations that operate by a virtual address, or by external coherency operations"
	},
	{.name = "L2D_TLB_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5c,
	 .desc = "Level 2 data TLB refill, read Counts level 2 TLB refills caused by memory read operations from both data and instruction fetch except for those caused by TLB maintenance operations or hardware prefetches"
	},
	{.name = "L2D_TLB_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5d,
	 .desc = "Level 2 data TLB refill, write Counts level 2 TLB refills caused by memory write operations from both data and instruction fetch except for those caused by TLB maintenance operations"
	},
	{.name = "L2D_TLB_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5e,
	 .desc = "Level 2 data TLB access, read Counts level 2 TLB accesses caused by memory read operations from both data and instruction fetch except for those caused by TLB maintenance operations"
	},
	{.name = "L2D_TLB_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x5f,
	 .desc = "Level 2 data TLB access, write Counts level 2 TLB accesses caused by memory write operations from both data and instruction fetch except for those caused by TLB maintenance operations"
	},
	{.name = "BUS_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x60,
	 .desc = "Bus access, read Counts memory read transactions seen on the external bus"
	},
	{.name = "BUS_ACCESS_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x61,
	 .desc = "Bus access, write Counts memory write transactions seen on the external bus"
	},
	{.name = "MEM_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x66,
	 .desc = "Data memory access, read Counts memory accesses issued by the CPU due to load operations"
	},
	{.name = "MEM_ACCESS_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x67,
	 .desc = "Data memory access, write Counts memory accesses issued by the CPU due to store operations"
	},
	{.name = "UNALIGNED_LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x68,
	 .desc = "Unaligned access, read Counts unaligned memory read operations issued by the CPU"
	},
	{.name = "UNALIGNED_ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x69,
	 .desc = "Unaligned access, write Counts unaligned memory write operations issued by the CPU"
	},
	{.name = "UNALIGNED_LDST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6a,
	 .desc = "Unaligned access Counts unaligned memory operations issued by the CPU"
	},
	{.name = "LDREX_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6c,
	 .desc = "Exclusive operation speculatively executed, Load-Exclusive Counts Load-Exclusive operations that have been speculatively executed"
	},
	{.name = "STREX_PASS_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6d,
	 .desc = "Exclusive operation speculatively executed, Store-Exclusive pass Counts store-exclusive operations that have been speculatively executed and have successfully completed the store operation"
	},
	{.name = "STREX_FAIL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6e,
	 .desc = "Exclusive operation speculatively executed, Store-Exclusive fail Counts store-exclusive operations that have been speculatively executed and have not successfully completed the store operation"
	},
	{.name = "STREX_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6f,
	 .desc = "Exclusive operation speculatively executed, Store-Exclusive Counts store-exclusive operations that have been speculatively executed"
	},
	{.name = "LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x70,
	 .desc = "Operation speculatively executed, load Counts speculatively executed load operations including Single Instruction Multiple Data (SIMD) load operations"
	},
	{.name = "ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x71,
	 .desc = "Operation speculatively executed, store Counts speculatively executed store operations including Single Instruction Multiple Data (SIMD) store operations"
	},
	{.name = "LDST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x72,
	 .desc = "Operation speculatively executed, load or store Counts load and store operations that have been speculatively executed"
	},
	{.name = "DP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x73,
	 .desc = "Operation speculatively executed, integer data processing Counts speculatively executed logical or arithmetic instructions such as MOV/MVN operations"
	},
	{.name = "ASE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x74,
	 .desc = "Operation speculatively executed, Advanced SIMD Counts speculatively executed Advanced SIMD operations excluding load, store and move micro-operations that move data to or from SIMD (vector) registers"
	},
	{.name = "VFP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x75,
	 .desc = "Operation speculatively executed, scalar floating-point Counts speculatively executed floating point operations"
	},
	{.name = "PC_WRITE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x76,
	 .desc = "Operation speculatively executed, Software change of the PC Counts speculatively executed operations which cause software changes of the PC"
	},
	{.name = "CRYPTO_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x77,
	 .desc = "Operation speculatively executed, Cryptographic instruction Counts speculatively executed cryptographic operations except for PMULL and VMULL operations"
	},
	{.name = "BR_IMMED_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x78,
	 .desc = "Branch speculatively executed, immediate branch Counts direct branch operations which are speculatively executed"
	},
	{.name = "BR_RETURN_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x79,
	 .desc = "Branch speculatively executed, procedure return Counts procedure return operations (RET, RETAA and RETAB) which are speculatively executed"
	},
	{.name = "BR_INDIRECT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7a,
	 .desc = "Branch speculatively executed, indirect branch Counts indirect branch operations including procedure returns, which are speculatively executed"
	},
	{.name = "ISB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7c,
	 .desc = "Barrier speculatively executed, ISB Counts ISB operations that are executed"
	},
	{.name = "DSB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7d,
	 .desc = "Barrier speculatively executed, DSB Counts DSB operations that are speculatively issued to Load/Store unit in the CPU"
	},
	{.name = "DMB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7e,
	 .desc = "Barrier speculatively executed, DMB Counts DMB operations that are speculatively issued to the Load/Store unit in the CPU"
	},
	{.name = "CSDB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7f,
	 .desc = "Barrier speculatively executed, CSDB Counts CDSB operations that are speculatively issued to the Load/Store unit in the CPU"
	},
	{.name = "EXC_UNDEF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81,
	 .desc = "Exception taken, other synchronous Counts the number of synchronous exceptions which are taken locally that are due to attempting to execute an instruction that is UNDEFINED"
	},
	{.name = "EXC_SVC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x82,
	 .desc = "Exception taken, Supervisor Call Counts SVC exceptions taken locally"
	},
	{.name = "EXC_PABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x83,
	 .desc = "Exception taken, Instruction Abort Counts synchronous exceptions that are taken locally and caused by Instruction Aborts"
	},
	{.name = "EXC_DABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x84,
	 .desc = "Exception taken, Data Abort or SError Counts exceptions that are taken locally and are caused by data aborts or SErrors"
	},
	{.name = "EXC_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x86,
	 .desc = "Exception taken, IRQ Counts IRQ exceptions including the virtual IRQs that are taken locally"
	},
	{.name = "EXC_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x87,
	 .desc = "Exception taken, FIQ Counts FIQ exceptions including the virtual FIQs that are taken locally"
	},
	{.name = "EXC_SMC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x88,
	 .desc = "Exception taken, Secure Monitor Call Counts SMC exceptions take to EL3"
	},
	{.name = "EXC_HVC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8a,
	 .desc = "Exception taken, Hypervisor Call Counts HVC exceptions taken to EL2"
	},
	{.name = "EXC_TRAP_PABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8b,
	 .desc = "Exception taken, Instruction Abort not Taken locally Counts exceptions which are traps not taken locally and are caused by Instruction Aborts"
	},
	{.name = "EXC_TRAP_DABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8c,
	 .desc = "Exception taken, Data Abort or SError not Taken locally Counts exceptions which are traps not taken locally and are caused by Data Aborts or SError interrupts"
	},
	{.name = "EXC_TRAP_OTHER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8d,
	 .desc = "Exception taken, other traps not Taken locally Counts the number of synchronous trap exceptions which are not taken locally and are not SVC, SMC, HVC, data aborts, Instruction Aborts, or interrupts"
	},
	{.name = "EXC_TRAP_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8e,
	 .desc = "Exception taken, IRQ not Taken locally Counts IRQ exceptions including the virtual IRQs that are not taken locally"
	},
	{.name = "EXC_TRAP_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8f,
	 .desc = "Exception taken, FIQ not Taken locally Counts FIQs which are not taken locally but taken from EL0, EL1, or EL2 to EL3 (which would be the normal behavior for FIQs when not executing in EL3)"
	},
	{.name = "RC_LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x90,
	 .desc = "Release consistency operation speculatively executed, Load-Acquire Counts any load acquire operations that are speculatively executed"
	},
	{.name = "RC_ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x91,
	 .desc = "Release consistency operation speculatively executed, Store-Release Counts any store release operations that are speculatively executed"
	},
	{.name = "SAMPLE_POP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4000,
	 .desc = "Sample Population"
	},
	{.name = "SAMPLE_FEED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4001,
	 .desc = "Sample Taken"
	},
	{.name = "SAMPLE_FILTRATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4002,
	 .desc = "Sample Taken and not removed by filtering"
	},
	{.name = "SAMPLE_COLLISION",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4003,
	 .desc = "Sample collided with previous sample"
	},
	{.name = "CNT_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4004,
	 .desc = "Constant frequency cycles Increments at a constant frequency equal to the rate of increment of the System Counter, CNTPCT_EL0"
	},
	{.name = "STALL_BACKEND_MEM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4005,
	 .desc = "Memory stall cycles Counts cycles when the backend is stalled because there is a pending demand load request in progress in the last level core cache"
	},
	{.name = "L1I_CACHE_LMISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4006,
	 .desc = "Level 1 instruction cache long-latency miss Counts cache line refills into the level 1 instruction cache, that incurred additional latency"
	},
	{.name = "L2D_CACHE_LMISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4009,
	 .desc = "Level 2 data cache long-latency read miss Counts cache line refills into the level 2 unified cache from any memory read operations that incurred additional latency"
	},
	{.name = "LDST_ALIGN_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4020,
	 .desc = "Access with additional latency from alignment Counts the number of memory read and write accesses in a cycle that incurred additional latency, due to the alignment of the address and the size of data being accessed, which results in store crossing a single cache line"
	},
	{.name = "LD_ALIGN_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4021,
	 .desc = "Load with additional latency from alignment Counts the number of memory read accesses in a cycle that incurred additional latency, due to the alignment of the address and size of data being accessed, which results in load crossing a single cache line"
	},
	{.name = "ST_ALIGN_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4022,
	 .desc = "Store with additional latency from alignment Counts the number of memory write access in a cycle that incurred additional latency, due to the alignment of the address and size of data being accessed incurred additional latency"
	},
	{.name = "MEM_ACCESS_CHECKED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4024,
	 .desc = "Checked data memory access Counts the number of memory read and write accesses counted by MEM_ACCESS that are tag checked by the Memory Tagging Extension (MTE)"
	},
	{.name = "MEM_ACCESS_CHECKED_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4025,
	 .desc = "Checked data memory access, read Counts the number of memory read accesses in a cycle that are tag checked by the Memory Tagging Extension (MTE)"
	},
	{.name = "MEM_ACCESS_CHECKED_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4026,
	 .desc = "Checked data memory access, write Counts the number of memory write accesses in a cycle that is tag checked by the Memory Tagging Extension (MTE)"
	},
	{.name = "SIMD_INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8004,
	 .desc = "Operation speculatively executed, SIMD Counts speculatively executed operations that are SIMD or SVE vector operations or Advanced SIMD non-scalar operations"
	},
	{.name = "ASE_INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8005,
	 .desc = "Operation speculatively executed, Advanced SIMD Counts speculatively executed Advanced SIMD operations"
	},
	{.name = "SVE_INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8006,
	 .desc = "Operation speculatively executed, SVE, including load and store Counts speculatively executed operations that are SVE operations"
	},
	{.name = "FP_HP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8014,
	 .desc = "Floating-point operation speculatively executed, half precision Counts speculatively executed half precision floating point operations"
	},
	{.name = "FP_SP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8018,
	 .desc = "Floating-point operation speculatively executed, single precision Counts speculatively executed single precision floating point operations"
	},
	{.name = "FP_DP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x801c,
	 .desc = "Floating-point operation speculatively executed, double precision Counts speculatively executed double precision floating point operations"
	},
	{.name = "INT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8040,
	 .desc = "Integer operation speculatively executed Counts speculatively executed integer arithmetic operations"
	},
	{.name = "SVE_PRED_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8074,
	 .desc = "Operation speculatively executed, SVE predicated Counts speculatively executed predicated SVE operations"
	},
	{.name = "SVE_PRED_EMPTY_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8075,
	 .desc = "Operation speculatively executed, SVE predicated with no active predicates Counts speculatively executed predicated SVE operations with no active predicate elements"
	},
	{.name = "SVE_PRED_FULL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8076,
	 .desc = "Operation speculatively executed, SVE predicated with all active predicates Counts speculatively executed predicated SVE operations with all predicate elements active"
	},
	{.name = "SVE_PRED_PARTIAL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8077,
	 .desc = "Operation speculatively executed, SVE predicated with partially active predicates Counts speculatively executed predicated SVE operations with at least one but not all active predicate elements"
	},
	{.name = "SVE_PRED_NOT_FULL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8079,
	 .desc = "SVE predicated operations speculatively executed with no active or partially active predicates Counts speculatively executed predicated SVE operations with at least one non active predicate elements"
	},
	{.name = "PRF_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8087,
	 .desc = "Operation speculatively executed, Prefetch Counts speculatively executed operations that prefetch memory"
	},
	{.name = "SVE_LDFF_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80bc,
	 .desc = "Operation speculatively executed, SVE first-fault load Counts speculatively executed SVE first fault or non-fault load operations"
	},
	{.name = "SVE_LDFF_FAULT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80bd,
	 .desc = "Operation speculatively executed, SVE first-fault load which set FFR bit to 0b0 Counts speculatively executed SVE first fault or non-fault load operations that clear at least one bit in the FFR"
	},
	{.name = "FP_SCALE_OPS_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80c0,
	 .desc = "Scalable floating-point element ALU operations speculatively executed Counts speculatively executed scalable single precision floating point operations"
	},
	{.name = "FP_FIXED_OPS_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80c1,
	 .desc = "Non-scalable floating-point element ALU operations speculatively executed Counts speculatively executed non-scalable single precision floating point operations"
	},
	{.name = "ASE_SVE_INT8_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80e3,
	 .desc = "Integer operation speculatively executed, Advanced SIMD or SVE 8-bit Counts speculatively executed Advanced SIMD or SVE integer operations with the largest data type an 8-bit integer"
	},
	{.name = "ASE_SVE_INT16_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80e7,
	 .desc = "Integer operation speculatively executed, Advanced SIMD or SVE 16-bit Counts speculatively executed Advanced SIMD or SVE integer operations with the largest data type a 16-bit integer"
	},
	{.name = "ASE_SVE_INT32_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80eb,
	 .desc = "Integer operation speculatively executed, Advanced SIMD or SVE 32-bit Counts speculatively executed Advanced SIMD or SVE integer operations with the largest data type a 32-bit integer"
	},
	{.name = "ASE_SVE_INT64_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80ef,
	 .desc = "Integer operation speculatively executed, Advanced SIMD or SVE 64-bit Counts speculatively executed Advanced SIMD or SVE integer operations with the largest data type a 64-bit integer"
	},
	{.name = "BR_IMMED_TAKEN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8108,
	 .desc = "Branch instruction architecturally executed, immediate, taken Counts architecturally executed direct branches that were taken"
	},
	{.name = "BR_INDNR_TAKEN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x810c,
	 .desc = "Branch instruction architecturally executed, indirect excluding procedure return, taken Counts architecturally executed indirect branches excluding procedure returns that were taken"
	},
	{.name = "BR_IMMED_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8110,
	 .desc = "Branch instruction architecturally executed, predicted immediate Counts architecturally executed direct branches that were correctly predicted"
	},
	{.name = "BR_IMMED_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8111,
	 .desc = "Branch instruction architecturally executed, mispredicted immediate Counts architecturally executed direct branches that were mispredicted and caused a pipeline flush"
	},
	{.name = "BR_IND_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8112,
	 .desc = "Branch instruction architecturally executed, predicted indirect Counts architecturally executed indirect branches including procedure returns that were correctly predicted"
	},
	{.name = "BR_IND_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8113,
	 .desc = "Branch instruction architecturally executed, mispredicted indirect Counts architecturally executed indirect branches including procedure returns that were mispredicted and caused a pipeline flush"
	},
	{.name = "BR_RETURN_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8114,
	 .desc = "Branch instruction architecturally executed, predicted procedure return Counts architecturally executed procedure returns that were correctly predicted"
	},
	{.name = "BR_RETURN_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8115,
	 .desc = "Branch instruction architecturally executed, mispredicted procedure return Counts architecturally executed procedure returns that were mispredicted and caused a pipeline flush"
	},
	{.name = "BR_INDNR_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8116,
	 .desc = "Branch instruction architecturally executed, predicted indirect excluding procedure return Counts architecturally executed indirect branches excluding procedure returns that were correctly predicted"
	},
	{.name = "BR_INDNR_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8117,
	 .desc = "Branch instruction architecturally executed, mispredicted indirect excluding procedure return Counts architecturally executed indirect branches excluding procedure returns that were mispredicted and caused a pipeline flush"
	},
	{.name = "BR_TAKEN_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8118,
	 .desc = "Branch instruction architecturally executed, predicted branch, taken Counts architecturally executed branches that were taken and were correctly predicted"
	},
	{.name = "BR_TAKEN_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8119,
	 .desc = "Branch instruction architecturally executed, mispredicted branch, taken Counts architecturally executed branches that were taken and were mispredicted causing a pipeline flush"
	},
	{.name = "BR_SKIP_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x811a,
	 .desc = "Branch instruction architecturally executed, predicted branch, not taken Counts architecturally executed branches that were not taken and were correctly predicted"
	},
	{.name = "BR_SKIP_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x811b,
	 .desc = "Branch instruction architecturally executed, mispredicted branch, not taken Counts architecturally executed branches that were not taken and were mispredicted causing a pipeline flush"
	},
	{.name = "BR_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x811c,
	 .desc = "Branch instruction architecturally executed, predicted branch Counts branch instructions counted by BR_RETIRED which were correctly predicted"
	},
	{.name = "BR_IND_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x811d,
	 .desc = "Instruction architecturally executed, indirect branch Counts architecturally executed indirect branches including procedure returns"
	},
	{.name = "INST_FETCH_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8120,
	 .desc = "Event in progress, INST FETCH Counts number of instruction fetches outstanding per cycle, which will provide an average latency of instruction fetch"
	},
	{.name = "MEM_ACCESS_RD_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8121,
	 .desc = "Event in progress, MEM ACCESS RD Counts the number of outstanding loads or memory read accesses per cycle"
	},
	{.name = "INST_FETCH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8124,
	 .desc = "Instruction memory access Counts Instruction memory accesses that the PE makes"
	},
	{.name = "DTLB_WALK_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8128,
	 .desc = "Event in progress, DTLB WALK Counts the number of data translation table walks in progress per cycle"
	},
	{.name = "ITLB_WALK_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8129,
	 .desc = "Event in progress, ITLB WALK Counts the number of instruction translation table walks in progress per cycle"
	},
	{.name = "SAMPLE_FEED_BR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812a,
	 .desc = "Statisical Profiling sample taken, branch Counts statistical profiling samples taken which are branches"
	},
	{.name = "SAMPLE_FEED_LD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812b,
	 .desc = "Statisical Profiling sample taken, load Counts statistical profiling samples taken which are loads or load atomic operations"
	},
	{.name = "SAMPLE_FEED_ST",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812c,
	 .desc = "Statisical Profiling sample taken, store Counts statistical profiling samples taken which are stores or store atomic operations"
	},
	{.name = "SAMPLE_FEED_OP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812d,
	 .desc = "Statisical Profiling sample taken, matching operation type Counts statistical profiling samples taken which are matching any operation type filters supported"
	},
	{.name = "SAMPLE_FEED_EVENT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812e,
	 .desc = "Statisical Profiling sample taken, matching events Counts statistical profiling samples taken which are matching event packet filter constraints"
	},
	{.name = "SAMPLE_FEED_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812f,
	 .desc = "Statisical Profiling sample taken, exceeding minimum latency Counts statistical profiling samples taken which are exceeding minimum latency set by operation latency filter constraints"
	},
	{.name = "L1D_TLB_RW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8130,
	 .desc = "Level 1 data TLB demand access Counts level 1 data TLB demand accesses caused by memory read or write operations"
	},
	{.name = "L1I_TLB_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8131,
	 .desc = "Level 1 instruction TLB demand access Counts level 1 instruction TLB demand accesses whether the access hits or misses in the TLB"
	},
	{.name = "L1D_TLB_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8132,
	 .desc = "Level 1 data TLB software preload Counts level 1 data TLB accesses generated by software prefetch or preload memory accesses"
	},
	{.name = "L1I_TLB_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8133,
	 .desc = "Level 1 instruction TLB software preload Counts level 1 instruction TLB accesses generated by software preload or prefetch instructions"
	},
	{.name = "DTLB_HWUPD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8134,
	 .desc = "Data TLB hardware update of translation table Counts number of memory accesses triggered by a data translation table walk and performing an update of a translation table entry"
	},
	{.name = "ITLB_HWUPD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8135,
	 .desc = "Instruction TLB hardware update of translation table Counts number of memory accesses triggered by an instruction translation table walk and performing an update of a translation table entry"
	},
	{.name = "DTLB_STEP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8136,
	 .desc = "Data TLB translation table walk, step Counts number of memory accesses triggered by a demand data translation table walk and performing a read of a translation table entry"
	},
	{.name = "ITLB_STEP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8137,
	 .desc = "Instruction TLB translation table walk, step Counts number of memory accesses triggered by an instruction translation table walk and performing a read of a translation table entry"
	},
	{.name = "DTLB_WALK_LARGE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8138,
	 .desc = "Data TLB large page translation table walk Counts number of demand data translation table walks caused by a miss in the L2 TLB and yielding a large page"
	},
	{.name = "ITLB_WALK_LARGE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8139,
	 .desc = "Instruction TLB large page translation table walk Counts number of instruction translation table walks caused by a miss in the L2 TLB and yielding a large page"
	},
	{.name = "DTLB_WALK_SMALL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813a,
	 .desc = "Data TLB small page translation table walk Counts number of data translation table walks caused by a miss in the L2 TLB and yielding a small page"
	},
	{.name = "ITLB_WALK_SMALL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813b,
	 .desc = "Instruction TLB small page translation table walk Counts number of instruction translation table walks caused by a miss in the L2 TLB and yielding a small page"
	},
	{.name = "DTLB_WALK_RW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813c,
	 .desc = "Data TLB demand access with at least one translation table walk Counts number of demand data translation table walks caused by a miss in the L2 TLB and performing at least one memory access"
	},
	{.name = "ITLB_WALK_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813d,
	 .desc = "Instruction TLB demand access with at least one translation table walk Counts number of demand instruction translation table walks caused by a miss in the L2 TLB and performing at least one memory access"
	},
	{.name = "DTLB_WALK_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813e,
	 .desc = "Data TLB software preload access with at least one translation table walk Counts number of software prefetches or preloads generated data translation table walks caused by a miss in the L2 TLB and performing at least one memory access"
	},
	{.name = "ITLB_WALK_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813f,
	 .desc = "Instruction TLB software preload access with at least one translation table walk Counts number of software prefetches or preloads generated instruction translation table walks caused by a miss in the L2 TLB and performing at least one memory access"
	},
	{.name = "L1D_CACHE_RW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8140,
	 .desc = "Level 1 data cache demand access Counts level 1 data demand cache accesses from any load or store operation"
	},
	{.name = "L1I_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8141,
	 .desc = "Level 1 instruction cache demand fetch Counts demand instruction fetches which access the level 1 instruction cache"
	},
	{.name = "L1D_CACHE_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8142,
	 .desc = "Level 1 data cache software preload Counts level 1 data cache accesses from software preload or prefetch instructions"
	},
	{.name = "L1I_CACHE_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8143,
	 .desc = "Level 1 instruction cache software preload Counts instruction fetches generated by software preload or prefetch instructions which access the level 1 instruction cache"
	},
	{.name = "L1D_CACHE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8144,
	 .desc = "Level 1 data cache demand access miss Counts cache line misses in the level 1 data cache"
	},
	{.name = "L1I_CACHE_HWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8145,
	 .desc = "Level 1 instruction cache hardware prefetch Counts instruction fetches which access the level 1 instruction cache generated by the hardware prefetcher"
	},
	{.name = "L1D_CACHE_REFILL_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8146,
	 .desc = "Level 1 data cache refill, software preload Counts level 1 data cache refills where the cache line access was generated by software preload or prefetch instructions"
	},
	{.name = "L1I_CACHE_REFILL_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8147,
	 .desc = "Level 1 instruction cache refill, software preload Counts cache line refills in the level 1 instruction cache caused by a missed instruction fetch generated by software preload or prefetch instructions"
	},
	{.name = "L2D_CACHE_RW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8148,
	 .desc = "Level 2 data cache demand access Counts level 2 cache demand accesses from any load/store operations"
	},
	{.name = "L2D_CACHE_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x814a,
	 .desc = "Level 2 data cache software preload Counts level 2 data cache accesses generated by software preload or prefetch instructions"
	},
	{.name = "L2D_CACHE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x814c,
	 .desc = "Level 2 data cache demand access miss Counts cache line misses in the level 2 cache"
	},
	{.name = "L2D_CACHE_REFILL_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x814e,
	 .desc = "Level 2 data cache refill, software preload Counts refills due to accesses generated as a result of software preload or prefetch instructions as counted by L2D_CACHE_PRFM"
	},
	{.name = "L1D_CACHE_HWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8154,
	 .desc = "Level 1 data cache hardware prefetch Counts level 1 data cache accesses from any load/store operations generated by the hardware prefetcher"
	},
	{.name = "L2D_CACHE_HWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8155,
	 .desc = "Level 2 data cache hardware prefetch Counts level 2 data cache accesses generated by L2D hardware prefetchers"
	},
	{.name = "STALL_FRONTEND_MEMBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8158,
	 .desc = "Frontend stall cycles, memory bound Counts cycles when the frontend could not send any micro-operations to the rename stage due to resource constraints in the memory resources"
	},
	{.name = "STALL_FRONTEND_L1I",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8159,
	 .desc = "Frontend stall cycles, level 1 instruction cache Counts cycles when the frontend is stalled because there is an instruction fetch request pending in the level 1 instruction cache"
	},
	{.name = "STALL_FRONTEND_MEM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x815b,
	 .desc = "Frontend stall cycles, last level PE cache or memory Counts cycles when the frontend is stalled because there is an instruction fetch request pending in the last level core cache"
	},
	{.name = "STALL_FRONTEND_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x815c,
	 .desc = "Frontend stall cycles, TLB Counts when the frontend is stalled on any TLB misses being handled"
	},
	{.name = "STALL_FRONTEND_CPUBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8160,
	 .desc = "Frontend stall cycles, processor bound Counts cycles when the frontend could not send any micro-operations to the rename stage due to resource constraints in the CPU resources excluding memory resources"
	},
	{.name = "STALL_FRONTEND_FLOW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8161,
	 .desc = "Frontend stall cycles, flow control Counts cycles when the frontend could not send any micro-operations to the rename stage due to resource constraints in the branch prediction unit"
	},
	{.name = "STALL_FRONTEND_FLUSH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8162,
	 .desc = "Frontend stall cycles, flush recovery Counts cycles when the frontend could not send any micro-operations to the rename stage as the frontend is recovering from a machine flush or resteer"
	},
	{.name = "STALL_BACKEND_MEMBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8164,
	 .desc = "Backend stall cycles, memory bound Counts cycles when the backend could not accept any micro-operations due to resource constraints in the memory resources"
	},
	{.name = "STALL_BACKEND_L1D",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8165,
	 .desc = "Backend stall cycles, level 1 data cache Counts cycles when the backend is stalled because there is a pending demand load request in progress in the level 1 data cache"
	},
	{.name = "STALL_BACKEND_L2D",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8166,
	 .desc = "Backend stall cycles, level 2 data cache Counts cycles when the backend is stalled because there is a pending demand load request in progress in the level 2 data cache"
	},
	{.name = "STALL_BACKEND_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8167,
	 .desc = "Backend stall cycles, TLB Counts cycles when the backend is stalled on any demand TLB misses being handled"
	},
	{.name = "STALL_BACKEND_ST",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8168,
	 .desc = "Backend stall cycles, store Counts cycles when the backend is stalled and there is a store that has not reached the pre-commit stage"
	},
	{.name = "STALL_BACKEND_CPUBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816a,
	 .desc = "Backend stall cycles, processor bound Counts cycles when the backend could not accept any micro-operations due to any resource constraints in the CPU excluding memory resources"
	},
	{.name = "STALL_BACKEND_BUSY",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816b,
	 .desc = "Backend stall cycles, backend busy Counts cycles when the backend could not accept any micro-operations because the issue queues are full to take any operations for execution"
	},
	{.name = "STALL_BACKEND_ILOCK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816c,
	 .desc = "Backend stall cycles, input dependency Counts cycles when the backend could not accept any micro-operations due to resource constraints imposed by input dependency"
	},
	{.name = "STALL_BACKEND_RENAME",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816d,
	 .desc = "Backend stall cycles, rename full Counts cycles when backend is stalled even when operations are available from the frontend but at least one is not ready to be sent to the backend because no rename register is available"
	},
	{.name = "L1I_CACHE_HIT_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81c0,
	 .desc = "Level 1 instruction cache demand fetch hit Counts demand instruction fetches that access the level 1 instruction cache and hit in the L1 instruction cache"
	},
	{.name = "L1I_CACHE_HIT_RD_FPRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81d0,
	 .desc = "Level 1 instruction cache demand fetch first hit, fetched by software preload Counts demand instruction fetches that access the level 1 instruction cache that hit in the L1 instruction cache and the line was requested by a software prefetch"
	},
	{.name = "L1I_CACHE_HIT_RD_FHWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81e0,
	 .desc = "Level 1 instruction cache demand fetch first hit, fetched by hardware prefetcher Counts demand instruction fetches generated by hardware prefetch that access the level 1 instruction cache and hit in the L1 instruction cache"
	},
	{.name = "L1I_CACHE_HIT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8200,
	 .desc = "Level 1 instruction cache hit Counts instruction fetches that access the level 1 instruction cache and hit in the level 1 instruction cache"
	},
	{.name = "L1I_CACHE_HIT_PRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8208,
	 .desc = "Level 1 instruction cache software preload hit Counts instruction fetches generated by software preload or prefetch instructions that access the level 1 instruction cache and hit in the level 1 instruction cache"
	},
	{.name = "L1I_LFB_HIT_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8240,
	 .desc = "Level 1 instruction cache demand fetch line-fill buffer hit Counts demand instruction fetches that access the level 1 instruction cache and hit in a line that is in the process of being loaded into the level 1 instruction cache"
	},
	{.name = "L1I_LFB_HIT_RD_FPRFM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8250,
	 .desc = "Level 1 instruction cache demand fetch line-fill buffer first hit, recently fetched by software preload Counts demand instruction fetches generated by software prefetch instructions that access the level 1 instruction cache and hit in a line that is in the process of being loaded into the level 1 instruction cache"
	},
	{.name = "L1I_LFB_HIT_RD_FHWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8260,
	 .desc = "Level 1 instruction cache demand fetch line-fill buffer first hit, recently fetched by hardware prefetcher Counts demand instruction fetches generated by hardware prefetch that access the level 1 instruction cache and hit in a line that is in the process of being loaded into the level 1 instruction cache"
	},
	/* END Neoverse V3 specific events */
};
