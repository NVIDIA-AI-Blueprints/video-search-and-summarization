/*
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
 * ARM Neoverse N3
 * Based on ARM Neoverse N3 Technical Reference Manual rev 0
 * Section 19.1 Performance Monitors events
 */
static const arm_entry_t arm_n3_pe[]={
	{.name = "SW_INCR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x00,
	 .desc = "Instruction architecturally executed, Condition code check pass, software increment This event counts any instruction architecturally executed (condition code check pass)"
	},
	{.name = "L1I_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x01,
	 .desc = "Level 1 instruction cache refill This event counts any instruction fetch which misses in the cache"
	},
	{.name = "L1I_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x02,
	 .desc = "Level 1 instruction TLB refill This event counts any refill of the L1 instruction TLB from the MMU Translation Cache (MMUTC)"
	},
	{.name = "L1D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x03,
	 .desc = "Level 1 data cache refill This event counts any load or store operation or translation table walk that causes data to be read from outside the L1 cache, including accesses which do not allocate into L1"
	},
	{.name = "L1D_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x04,
	 .desc = "Level 1 data cache access This event counts any load or store operation or translation table walk that looks up in the L1 data cache"
	},
	{.name = "L1D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x05,
	 .desc = "Level 1 data TLB refill This event counts any refill of the data L1 TLB from the L2 TLB"
	},
	{.name = "INST_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x08,
	 .desc = "Instruction architecturally executed This event counts all retired instructions, including ones that fail their condition check"
	},
	{.name = "EXC_TAKEN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x09,
	 .desc = "Exception taken The counter counts each exception taken"
	},
	{.name = "EXC_RETURN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0a,
	 .desc = "Instruction architecturally executed, Condition code check pass, exception return"
	},
	{.name = "CID_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0b,
	 .desc = "Instruction architecturally executed, Condition code check pass, write to CONTEXTIDR This event only counts writes using the CONTEXTIDR_EL1 mnemonic"
	},
	{.name = "PC_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0c,
	 .desc = "Instruction architecturally executed, Condition code check pass, software change of the PC This event counts all branches taken and popped from the branch monitor"
	},
	{.name = "BR_IMMED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0d,
	 .desc = "Instruction architecturally executed, immediate branch This event counts all branches decoded as immediate branches, taken or not, and popped from the branch monitor"
	},
	{.name = "BR_RETURN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x0e,
	 .desc = "Branch instruction architecturally executed, procedure return, taken Instruction architecturally executed, Condition code check pass, procedure return"
	},
	{.name = "BR_MIS_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x10,
	 .desc = "Mispredicted or not predicted branch speculatively executed This event counts any predictable branch instruction which is mispredicted either due to dynamic misprediction or because the MMU is off and the branches are statically predicted not taken"
	},
	{.name = "CPU_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x11,
	 .desc = "Cycle"
	},
	{.name = "BR_PRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x12,
	 .desc = "Predictable branch instruction speculatively executed This event counts all predictable branches"
	},
	{.name = "MEM_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x13,
	 .desc = "Data memory access This event counts memory accesses due to load or store instructions"
	},
	{.name = "L1I_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x14,
	 .desc = "Level 1 instruction cache access This event counts any instruction fetch which accesses the L1 instruction cache"
	},
	{.name = "L1D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x15,
	 .desc = "Level 1 data cache write-back This event counts any write-back of data from the L1 data cache to lower level of caches"
	},
	{.name = "L2D_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x16,
	 .desc = "Level 2 data cache access - If the core is configured with a per-core L2 cache, this event counts any transaction from L1 which looks up in the L2 cache, and any writeback from the L1 to the L2"
	},
	{.name = "L2D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x17,
	 .desc = "Level 2 data cache refill - If the core is configured with a per-core L2 cache, this event counts any Cacheable transaction from L1 which causes data to be read from outside the core"
	},
	{.name = "L2D_CACHE_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x18,
	 .desc = "Level 2 data cache write-back If the core is configured with a per-core L2 cache, this event counts any write-back of data from the L2 cache to a location outside the core"
	},
	{.name = "BUS_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x19,
	 .desc = "Bus access This event counts for every beat of data that is transferred over the data channels between the core and the SCU"
	},
	{.name = "INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1b,
	 .desc = "Operation speculatively executed This event duplicates INST_RETIRED"
	},
	{.name = "TTBR_WRITE_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1c,
	 .desc = "Instruction architecturally executed, condition code check pass, write to TTBR This event only counts writes to TTBR0/TTBR1 in AArch32 and TTBR0_EL1/TTBR1_EL1 in AArch64"
	},
	{.name = "BUS_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x1d,
	 .desc = "Bus cycle This event duplicates CPU_CYCLES"
	},
	{.name = "L2D_CACHE_ALLOCATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x20,
	 .desc = "Level 2 data cache allocation without refill This event counts any full cache line write into the L2 cache which does not cause a linefill, including write-backs from L1 to L2 and full-line writes which do not allocate into L1"
	},
	{.name = "BR_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x21,
	 .desc = "Branch instruction architecturally executed This event counts all branches, taken or not, popped from the branch monitor"
	},
	{.name = "BR_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x22,
	 .desc = "Branch instruction architecturally executed, mispredicted This event counts any branch that is counted by BR_RETIRED which is not correctly predicted and causes pipeline clears"
	},
	{.name = "STALL_FRONTEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x23,
	 .desc = "No operation has been sent for execution, due to the frontend No operation has been issued, because of the frontend The counter counts on any cycle when no operations are issued due to the instruction queue being empty"
	},
	{.name = "STALL_BACKEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x24,
	 .desc = "No operation has been sent for execution due to the backend"
	},
	{.name = "L1D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x25,
	 .desc = "Level 1 data TLB access This event counts any load or store operation which accesses the data L1 TLB"
	},
	{.name = "L1I_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x26,
	 .desc = "Level 1 instruction TLB access This event counts any instruction fetch which accesses the instruction L1 TLB"
	},
	{.name = "L2I_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x27,
	 .desc = "Level 2 instruction cache access The counter counts each instruction memory access to at least the L2 instruction or unified cache"
	},
	{.name = "L2I_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x28,
	 .desc = "Level 2 instruction cache refill The counter counts each access counted by L2I_CACHE that causes a refill of the L2 instruction or unified cache, or any L1 data, instruction, or unified cache of this PE, from outside of those caches"
	},
	{.name = "L3D_CACHE_ALLOCATE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x29,
	 .desc = "Level 3 data cache allocation without refill This event counts any full cache line write into the L3 cache which does not cause a linefill, including write-backs from L2 to L3 cache and full-line writes which do not allocate into L2"
	},
	{.name = "L3D_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2a,
	 .desc = "Level 3 data cache refill This event counts for any cacheable read transaction returning data from the SCU for which the data source was outside the cluster"
	},
	{.name = "L3D_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2b,
	 .desc = "Level 3 data cache access This event counts for any cacheable read, write or write-back transaction sent to the SCU"
	},
	{.name = "L2D_TLB_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2d,
	 .desc = "Level 2 data TLB refill This event counts on any refill of the L2 TLB, caused by either an instruction or data access"
	},
	{.name = "L2D_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x2f,
	 .desc = "Level 2 data TLB access Attributable level 2 unified TLB access"
	},
	{.name = "REMOTE_ACCESS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x31,
	 .desc = "Access to another socket in a multi-socket system This event counts any transactions returning data from another socket in a multi-socket system"
	},
	{.name = "LL_CACHE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x32,
	 .desc = "Last level cache access The counter counts each memory-read operation or memory-write operation that causes a cache access to at least the last level cache"
	},
	{.name = "LL_CACHE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x33,
	 .desc = "Last level cache miss The counter counts each access counted by LL_CACHE that is not completed by the last level cache"
	},
	{.name = "DTLB_WALK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x34,
	 .desc = "Data TLB access with at least one translation table walk Access to data TLB that caused a translation table walk"
	},
	{.name = "ITLB_WALK",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x35,
	 .desc = "Instruction TLB access with at least one translation table walk Access to instruction TLB that caused a translation table walk"
	},
	{.name = "LL_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x36,
	 .desc = "Last level cache access, read This event counts any cacheable read transaction which returns a data source of 'interconnect cache', 'DRAM', 'remote' or 'inter-cluster peer'"
	},
	{.name = "LL_CACHE_MISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x37,
	 .desc = "Last Level cache miss read This event counts any cacheable read transaction which returns a data source of 'DRAM', 'remote' or 'inter-cluster peer'"
	},
	{.name = "L1D_CACHE_LMISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x39,
	 .desc = "Level 1 data cache long-latency read miss Level 1 data cache access, read"
	},
	{.name = "OP_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3a,
	 .desc = "This event counts each operation counted by OP_SPEC that would be executed in a Simple sequential execution of the program"
	},
	{.name = "OP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3b,
	 .desc = "Micro-operation speculatively executed This event counts the number of operations executed by the core, including those that are executed speculatively and would not be executed in a simple sequential execution of the program"
	},
	{.name = "STALL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3c,
	 .desc = "No operation sent for execution This event counts every Attributable cycle on which no Attributable instruction or operation was sent for execution on this core"
	},
	{.name = "STALL_SLOT_BACKEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3d,
	 .desc = "No operation sent for execution on a Slot due to the backend Counts each Slot counted by STALL_SLOT where no Attributable instruction or operation was for execution because the backend is unable to accept one of: - The instruction operation available for the PE on the slot"
	},
	{.name = "STALL_SLOT_FRONTEND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3e,
	 .desc = "No operation sent for execution due to the frontend"
	},
	{.name = "STALL_SLOT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x3f,
	 .desc = "No operation sent for execution on a Slot The counter counts on each Attributable cycle the number of instruction or operation Slots that were not occupied by an instruction or operation Attributable to the PE"
	},
	{.name = "L1D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x40,
	 .desc = "Level 1 data cache access, read Counts any load operation or translation table walk access which looks up in the L1 data cache"
	},
	{.name = "L1D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x41,
	 .desc = "Level 1 data cache access, write Counts any store operation which looks up in the L1 data cache"
	},
	{.name = "L1D_CACHE_REFILL_INNER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x44,
	 .desc = "Level 1 data cache refill, inner This event counts any L1 data cache linefill (as counted by L1D_CACHE_REFILL) which hits in lower level of caches, or another core in the cluster"
	},
	{.name = "L1D_CACHE_REFILL_OUTER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x45,
	 .desc = "Level 1 data cache refill, outer This event counts any L1 data cache linefill (as counted by L1D_CACHE_REFILL) which does not hit in lower level of caches, or another core in the cluster, and instead obtains data from outside the cluster"
	},
	{.name = "L1D_CACHE_INVAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x48,
	 .desc = "Level 1 data cache invalidate The counter counts each invalidation of a cache line in the Level 1 data or unified cache"
	},
	{.name = "L2D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x50,
	 .desc = "Level 2 data cache access, read This event counts any transaction issued from L1 caches which looks up in the L2 cache, including requests for instructions fetches and MMU table walks"
	},
	{.name = "L2D_CACHE_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x51,
	 .desc = "Level 2 data cache access, write This event counts any full cache line write into the L2 cache which does not cause a linefill, including write-backs from L1 to L2, full-line writes which do not allocate into L1 and MMU descriptor hardware updates performed in L2"
	},
	{.name = "L2D_CACHE_REFILL_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x52,
	 .desc = "Level 2 data cache refill, read This event counts any Cacheable transaction generated by a read operation which causes data to be read from outside the L2"
	},
	{.name = "L2D_CACHE_REFILL_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x53,
	 .desc = "Level 2 data cache refill, write This event counts any Cacheable transaction generated by a store operation which causes data to be read from outside the L2"
	},
	{.name = "L2D_CACHE_WB_VICTIM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x56,
	 .desc = "Level 2 data cache write-back, victim This event counts any datafull write-back operation caused by allocations"
	},
	{.name = "L2D_CACHE_WB_CLEAN",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x57,
	 .desc = "Level 2 data cache write-back, cleaning, and coherency This event counts any datafull write-back operation caused by cache maintenance operations or external coherency requests"
	},
	{.name = "L2D_CACHE_INVAL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x58,
	 .desc = "Level 2 data cache invalidate This event counts any cache maintenance operation which causes the invalidation of a line present in the L2 cache"
	},
	{.name = "BUS_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x60,
	 .desc = "Bus access, read This event counts for every beat of data that is transferred over the read data channel between the core and the SCU"
	},
	{.name = "BUS_ACCESS_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x61,
	 .desc = "Bus access, write This event counts for every beat of data that is transferred over the write data channel between the core and the SCU"
	},
	{.name = "MEM_ACCESS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x66,
	 .desc = "Data memory access, read This event counts memory accesses due to load instructions"
	},
	{.name = "MEM_ACCESS_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x67,
	 .desc = "Data memory access, write This event counts memory accesses due to store instructions"
	},
	{.name = "STREX_FAIL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6e,
	 .desc = "Exclusive operation speculatively executed, Store-Exclusive fail"
	},
	{.name = "STREX_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x6f,
	 .desc = "Exclusive operation speculatively executed, Store-Exclusive"
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
	{.name = "DP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x73,
	 .desc = "Operation speculatively executed, integer data processing This event counts retired integer data-processing instructions"
	},
	{.name = "ASE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x74,
	 .desc = "Operation speculatively executed, Advanced SIMD This event counts retired Advanced SIMD instructions"
	},
	{.name = "VFP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x75,
	 .desc = "Operation speculatively executed, floating-point This event counts retired floating-point instructions"
	},
	{.name = "PC_WRITE_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x76,
	 .desc = "Operation speculatively executed, software change of the PC This event counts at Decoder step each instruction changing the PC: all branches, some exceptions (HVC/SVC/SMC/ISB and exception return)"
	},
	{.name = "CRYPTO_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x77,
	 .desc = "Operation speculatively executed, Cryptographic instruction This event counts retired Cryptographic instructions"
	},
	{.name = "ISB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7c,
	 .desc = "Barrier speculatively executed, ISB"
	},
	{.name = "DSB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7d,
	 .desc = "Barrier speculatively executed, DSB"
	},
	{.name = "DMB_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x7e,
	 .desc = "Barrier speculatively executed, DMB"
	},
	{.name = "EXC_UNDEF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81,
	 .desc = "Exception taken, other synchronous Counts the number of undefined exceptions taken locally"
	},
	{.name = "EXC_SVC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x82,
	 .desc = "Exception taken, Supervisor Call Exception taken locally, Supervisor Call"
	},
	{.name = "EXC_PABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x83,
	 .desc = "Exception taken, Instruction Abort Exception taken locally, Instruction Abort"
	},
	{.name = "EXC_DABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x84,
	 .desc = "Exception taken, Data Abort or SError Exception taken locally, Data Abort and SError"
	},
	{.name = "EXC_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x86,
	 .desc = "Exception taken, IRQ Exception taken locally, IRQ"
	},
	{.name = "EXC_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x87,
	 .desc = "Exception taken, FIQ Exception taken locally, FIQ"
	},
	{.name = "EXC_SMC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x88,
	 .desc = "Exception taken, Secure Monitor Call Exception taken locally, Secure Monitor Call"
	},
	{.name = "EXC_HVC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8a,
	 .desc = "Exception taken, Hypervisor Call Exception taken locally, Hypervisor Call"
	},
	{.name = "EXC_TRAP_PABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8b,
	 .desc = "Exception taken, Instruction Abort not Taken locally"
	},
	{.name = "EXC_TRAP_DABORT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8c,
	 .desc = "Exception taken, Data Abort or SError not Taken locally"
	},
	{.name = "EXC_TRAP_OTHER",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8d,
	 .desc = "Exception taken, other traps not Taken locally"
	},
	{.name = "EXC_TRAP_IRQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8e,
	 .desc = "Exception taken, IRQ not Taken locally"
	},
	{.name = "EXC_TRAP_FIQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8f,
	 .desc = "Exception taken, FIQ not Taken locally"
	},
	{.name = "RC_LD_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x90,
	 .desc = "Release consistency operation speculatively executed, Load-Acquire"
	},
	{.name = "RC_ST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x91,
	 .desc = "Release consistency operation speculatively executed, Store-Release"
	},
	{.name = "L3D_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0xa0,
	 .desc = "Level 3 data cache access, read This event counts for any cacheable read transaction sent to the SCU"
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
	 .desc = "Constant frequency cycles The counter increments at a constant frequency equal to the rate of increment of the System Counter, CNTPCT_EL0"
	},
	{.name = "STALL_BACKEND_MEM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4005,
	 .desc = "Memory stall cycles The counter counts each cycle counted by STALL_BACKEND_MEMBOUND where there is a demand data miss in the last level of data or unified cache within the PE clock domain or a non-cacheable data access in progress"
	},
	{.name = "L1I_CACHE_LMISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4006,
	 .desc = "Level 1 instruction cache long-latency miss The counter counts each access counted by L1I_CACHE that incurs additional latency because it returns instructions from outside the L1 instruction cache"
	},
	{.name = "L2D_CACHE_LMISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4009,
	 .desc = "Level 2 data cache long-latency read miss The counter counts each memory read access counted by L2D_CACHE that incurs additional latency because it returns data from outside the L2 data or unified cache of this PE"
	},
	{.name = "L2I_CACHE_LMISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x400a,
	 .desc = "Level 2 instruction cache long-latency miss The counter counts each memory read access counted by L2I_CACHE that incurs additional latency because it returns data from outside the L2 instruction or unified cache of this PE"
	},
	{.name = "L3D_CACHE_LMISS_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x400b,
	 .desc = "Level 3 data cache long-latency read miss The counter counts each memory read access counted by L3D_CACHE that incurs additional latency because it returns data from outside the L3 data or unified cache of this PE"
	},
	{.name = "TRB_WRAP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x400c,
	 .desc = "Trace buffer current write pointer wrapped"
	},
	{.name = "PMU_OVFS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x400d,
	 .desc = "PMU overflow, counters accessible to EL1 and EL0 Note: This event is exported to the trace unit, but cannot be counted in the PMU"
	},
	{.name = "TRB_TRIG",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x400e,
	 .desc = "Trace buffer Trigger Event Note: This event is only exported to the trace unit and is not visible to the PMU"
	},
	{.name = "PMU_HOVFS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x400f,
	 .desc = "PMU overflow, counters reserved for use by EL2 Note: This event is only exported to the trace unit and is not visible to the PMU"
	},
	{.name = "TRCEXTOUT0",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4010,
	 .desc = "Trace unit external output 0 PE Trace Unit external output 0 Note: This event is not exported to the trace unit"
	},
	{.name = "TRCEXTOUT1",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4011,
	 .desc = "Trace unit external output 1 PE Trace Unit external output 1 Note: This event is not exported to the trace unit"
	},
	{.name = "TRCEXTOUT2",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4012,
	 .desc = "Trace unit external output 2 PE Trace Unit external output 2 Note: This event is not exported to the trace unit"
	},
	{.name = "TRCEXTOUT3",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4013,
	 .desc = "Trace unit external output 3 PE Trace Unit external output 3 Note: This event is not exported to the trace unit"
	},
	{.name = "CTI_TRIGOUT4",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4018,
	 .desc = "Cross Trigger Interface output trigger 4"
	},
	{.name = "CTI_TRIGOUT5",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4019,
	 .desc = "Cross Trigger Interface output trigger 5"
	},
	{.name = "CTI_TRIGOUT6",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x401a,
	 .desc = "Cross Trigger Interface output trigger 6"
	},
	{.name = "CTI_TRIGOUT7",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x401b,
	 .desc = "Cross Trigger Interface output trigger 7"
	},
	{.name = "LDST_ALIGN_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4020,
	 .desc = "Access with additional latency from alignment The counter counts each access counted by MEM_ACCESS that, due to the alignment of the address and size of data being accessed, incurred additional latency"
	},
	{.name = "LD_ALIGN_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4021,
	 .desc = "Load with additional latency from alignment The counter counts each memory-read access counted by LDST_ALIGN_LAT"
	},
	{.name = "ST_ALIGN_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4022,
	 .desc = "Store with additional latency from alignment The counter counts each memory-write access counted by LDST_ALIGN_LAT"
	},
	{.name = "MEM_ACCESS_CHECKED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4024,
	 .desc = "Checked data memory access"
	},
	{.name = "MEM_ACCESS_CHECKED_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4025,
	 .desc = "Checked data memory access, read"
	},
	{.name = "MEM_ACCESS_CHECKED_WR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x4026,
	 .desc = "Checked data memory access, write"
	},
	{.name = "ASE_INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8005,
	 .desc = "Advanced SIMD operations speculatively executed"
	},
	{.name = "SVE_INST_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8006,
	 .desc = "SVE operation, including load/store The counter counts speculatively executed operations due to SVE instructions"
	},
	{.name = "FP_HP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8014,
	 .desc = "Half-precision floating-point operation speculatively executed"
	},
	{.name = "FP_SP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8018,
	 .desc = "Single-precision floating-point operation speculatively executed"
	},
	{.name = "FP_DP_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x801c,
	 .desc = "Double-precision floating-point operation speculatively executed"
	},
	{.name = "SVE_PRED_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8074,
	 .desc = "SVE predicated operations speculatively executed"
	},
	{.name = "SVE_PRED_EMPTY_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8075,
	 .desc = "SVE predicated operations with no active predicates speculatively executed"
	},
	{.name = "SVE_PRED_FULL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8076,
	 .desc = "SVE predicated operations with all active predicates speculatively executed"
	},
	{.name = "SVE_PRED_PARTIAL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8077,
	 .desc = "SVE predicated operations with partially active predicates speculatively executed"
	},
	{.name = "SVE_PRED_NOT_FULL_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8079,
	 .desc = "SVE predicated operations with no or partially active predicates speculatively executed"
	},
	{.name = "SVE_LDFF_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80bc,
	 .desc = "SVE First-fault load operations speculatively executed"
	},
	{.name = "SVE_LDFF_FAULT_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80bd,
	 .desc = "SVE First-fault load operations speculatively executed which set FFR bit to 0"
	},
	{.name = "FP_SCALE_OPS_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80c0,
	 .desc = "Scalable floating-point element operations speculatively executed"
	},
	{.name = "FP_FIXED_OPS_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80c1,
	 .desc = "Non-scalable floating-point element operations speculatively executed"
	},
	{.name = "ASE_SVE_INT8_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80e3,
	 .desc = "Advanced SIMD and SVE 8-bit integer operation speculatively executed"
	},
	{.name = "ASE_SVE_INT16_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80e7,
	 .desc = "Advanced SIMD and SVE 16-bit integer operation speculatively executed"
	},
	{.name = "ASE_SVE_INT32_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80eb,
	 .desc = "Advanced SIMD and SVE 32-bit integer operation speculatively executed"
	},
	{.name = "ASE_SVE_INT64_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x80ef,
	 .desc = "Advanced SIMD and SVE 64-bit integer operation speculatively executed"
	},
	{.name = "BR_IMMED_TAKEN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8108,
	 .desc = "Instruction architecturally executed, immediate branch taken"
	},
	{.name = "BR_INDNR_TAKEN_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x810c,
	 .desc = "Instruction architecturally executed, indirect branch excluding procedure return retired"
	},
	{.name = "BR_IMMED_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8110,
	 .desc = "Branch instruction architecturally executed, predicted immediate The counter counts the instructions on the architecturally executed path counted by both BR_IMMED_RETIRED and BR_PRED_RETIRED"
	},
	{.name = "BR_IMMED_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8111,
	 .desc = "Branch instruction architecturally executed, mispredicted immediate The counter counts the instructions on the architecturally executed path, counted by both BR_IMMED_RETIRED and BR_MIS_PRED_RETIRED"
	},
	{.name = "BR_IND_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8112,
	 .desc = "Branch instruction architecturally executed, predicted indirect The counter counts the instructions on the architecturally executed path counted by both BR_IND_RETIRED and BR_PRED_RETIRED"
	},
	{.name = "BR_IND_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8113,
	 .desc = "Branch instruction architecturally executed, mispredicted indirect The counter counts the instructions on the architecturally executed path counted by both BR_IND_RETIRED and BR_MIS_PRED_RETIRED"
	},
	{.name = "BR_RETURN_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8114,
	 .desc = "Branch instruction architecturally executed, predicted procedure return The counter counts the instructions on the architecturally executed path counted by BR_IND_PRED_RETIRED where, if taken, the branch would be counted by BR_RETURN_RETIRED"
	},
	{.name = "BR_RETURN_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8115,
	 .desc = "Branch instruction architecturally executed, mispredicted procedure return The counter counts the instructions on the architecturally executed path counted by BR_IND_MIS_PRED_RETIRED where, if taken, the branch would also be counted by BR_RETURN_RETIRED"
	},
	{.name = "BR_INDNR_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8116,
	 .desc = "Branch instruction architecturally executed, predicted indirect excluding procedure return The counter counts the instructions on the architecturally executed path counted by BR_IND_PRED_RETIRED where, if taken, the branch would not be counted by BR_RETURN_RETIRED"
	},
	{.name = "BR_INDNR_MIS_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8117,
	 .desc = "Branch instruction architecturally executed, mispredicted indirect excluding procedure return The counter counts the instructions on the architecturally executed path counted by BR_IND_MIS_PRED_RETIRED where, if taken, the branch would not be counted by BR_RETURN_RETIRED"
	},
	{.name = "BR_PRED_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x811c,
	 .desc = "Branch instruction architecturally executed, predicted branch The counter counts the instructions on the architecturally executed path counted by BR_RETIRED that are not counted by BR_MIS_PRED_RETIRED"
	},
	{.name = "BR_IND_RETIRED",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x811d,
	 .desc = "Instruction architecturally executed, indirect branch"
	},
	{.name = "INST_FETCH_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8120,
	 .desc = "Event in progress, INST_FETCH The counter counts by the number of INST_FETCH events in progress on each Processor cycle"
	},
	{.name = "MEM_ACCESS_RD_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8121,
	 .desc = "Event in progress, MEM_ACCESS_RD The counter counts by the number of MEM_ACCESS_RD events in progress on each Processor Cycle"
	},
	{.name = "INST_FETCH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8124,
	 .desc = "Instruction memory access The counter counts each Instruction memory access that the PE makes"
	},
	{.name = "DTLB_WALK_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8128,
	 .desc = "Total cycles, DTLB_WALK The counter counts by the number of data TLB walk events in progress on each processor cycle"
	},
	{.name = "ITLB_WALK_PERCYC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8129,
	 .desc = "Total cycles, ITLB_WALK The counter counts by the number of instruction TLB walk events in progress on each processor cycle"
	},
	{.name = "SAMPLE_FEED_BR",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812a,
	 .desc = "Statistical Profiling sample taken, branch The counter counts each sample counted by SAMPLE_FEED that are branch operations"
	},
	{.name = "SAMPLE_FEED_LD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812b,
	 .desc = "Statistical Profiling sample taken, load The counter counts each sample counted by SAMPLE_FEED that are load or load atomic operations"
	},
	{.name = "SAMPLE_FEED_ST",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812c,
	 .desc = "Statistical Profiling sample taken, store The counter counts each sample counted by SAMPLE_FEED that are store or atomic operations, including load atomic operations"
	},
	{.name = "SAMPLE_FEED_OP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812d,
	 .desc = "Statistical Profiling sample taken, matching operation type The counter counts each sample counted by SAMPLE_FEED that meets the operation type filter constraints"
	},
	{.name = "SAMPLE_FEED_EVENT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812e,
	 .desc = "Statistical Profiling sample taken, matching events The counter counts each sample counted by SAMPLE_FEED that meets the Events packet filter constraints"
	},
	{.name = "SAMPLE_FEED_LAT",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x812f,
	 .desc = "Statistical Profiling sample taken, exceeding minimum latency The counter counts each sample counted by SAMPLE_FEED that meets the operation latency filter constraints"
	},
	{.name = "DTLB_HWUPD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8134,
	 .desc = "Data TLB hardware update of translation table The counter counts each access counted by L1D_TLB that causes a hardware update of a translation table entry"
	},
	{.name = "ITLB_HWUPD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8135,
	 .desc = "Instruction TLB hardware update of translation table The counter counts each access counted by L1I_TLB that causes a hardware update of a translation table entry"
	},
	{.name = "DTLB_STEP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8136,
	 .desc = "Data TLB translation table walk, step The counter counts each translation table walk access made by a refill of the data or unified TLB"
	},
	{.name = "ITLB_STEP",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8137,
	 .desc = "Instruction TLB translation table walk, step The counter counts each translation table walk access made by a refill of the instruction TLB"
	},
	{.name = "DTLB_WALK_LARGE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8138,
	 .desc = "Data TLB large page translation table walk The counter counts each translation table walk counted by DTLB_WALK where the result of the walk yields a large page size"
	},
	{.name = "ITLB_WALK_LARGE",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8139,
	 .desc = "Instruction TLB large page translation table walk The counter counts each translation table walk counted by ITLB_WALK where the result of the walk yields a large page size"
	},
	{.name = "DTLB_WALK_SMALL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813a,
	 .desc = "Data TLB small page translation table walk The counter counts each translation table walk counted by DTLB_WALK where the result of the walk yields a small page size"
	},
	{.name = "ITLB_WALK_SMALL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x813b,
	 .desc = "Instruction TLB small page translation table walk The counter counts each translation table walk counted by ITLB_WALK where the result of the walk yields a small page size"
	},
	{.name = "L1D_CACHE_RW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8140,
	 .desc = "Level 1 data cache demand access The counter counts each access counted by L1D_CACHE that is due to a demand read or demand write access"
	},
	{.name = "L2D_CACHE_RW",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8148,
	 .desc = "Level 2 data cache demand access The counter counts each access counted by L2D_CACHE that is due to a demand Memoryread operation or demand Memory-write operation"
	},
	{.name = "L2I_CACHE_RD",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8149,
	 .desc = "Level 2 instruction cache demand fetch The counter counts each access counted by L2I_CACHE that is due to a demand instruction memory access"
	},
	{.name = "L3D_CACHE_MISS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8152,
	 .desc = "Level 3 data cache demand access miss The counter counts each access counted by L3D_CACHE_RW that misses in the L1 to L3 data or unified caches, causing an access to outside of the L1 to L3 caches of this PE"
	},
	{.name = "L1D_CACHE_HWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8154,
	 .desc = "Level 1 data cache hardware prefetch The counter counts each fetch triggered by L1 prefetchers"
	},
	{.name = "STALL_FRONTEND_MEMBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8158,
	 .desc = "Frontend stall cycles, memory bound The counter counts each cycle counted by STALL_FRONTEND when no instructions are delivered from the memory system"
	},
	{.name = "STALL_FRONTEND_L1I",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8159,
	 .desc = "Frontend stall cycles, level 1 instruction cache The counter counts each cycle counted by STALL_FRONTEND_MEMBOUND when there is a demand miss in the first level instruction cache"
	},
	{.name = "STALL_FRONTEND_MEM",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x815b,
	 .desc = "Frontend stall cycles, last level PE cache or memory The counter counts each cycle counted by STALL_FRONTEND_MEMBOUND when there is a demand instruction miss in the last level of instruction or unified cache within the PE clock domain or a non-cacheable instruction fetch in progress"
	},
	{.name = "STALL_FRONTEND_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x815c,
	 .desc = "Frontend stall cycles, TLB The counter counts each cycle counted by STALL_FRONTEND_MEMBOUND when there is an instruction or unified TLB demand miss"
	},
	{.name = "STALL_FRONTEND_CPUBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8160,
	 .desc = "Frontend stall cycles, processor bound The counter counts each cycle counted by STALL_FRONTEND when the frontend is stalled on a frontend processor resource, not including memory"
	},
	{.name = "STALL_FRONTEND_FLUSH",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8162,
	 .desc = "Frontend stall cycles, flush recovery"
	},
	{.name = "STALL_BACKEND_MEMBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8164,
	 .desc = "Backend stall cycles, memory bound The counter counts each cycle counted by STALL_BACKEND when the backend is waiting for a memory access to complete"
	},
	{.name = "STALL_BACKEND_L1D",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8165,
	 .desc = "Backend stall cycles, level 1 data cache The counter counts each cycle counted by STALL_BACKEND_MEMBOUND where there is a demand data miss in the L1 of data or unified cache"
	},
	{.name = "STALL_BACKEND_TLB",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8167,
	 .desc = "Backend stall cycles, TLB The counter counts each cycle counted by STALL_BACKEND_MEMBOUND where there is a demand data miss in the data or unified TLB"
	},
	{.name = "STALL_BACKEND_ST",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8168,
	 .desc = "Back stall cycles store The counter counts each cycle counted by STALL_BACKEND_MEMBOUND when the backend is stalled waiting for a store"
	},
	{.name = "STALL_BACKEND_CPUBOUND",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816a,
	 .desc = "Backend stall cycles, processor bound The counter counts each cycle counted by STALL_BACKEND when the backend is stalled on a processor resource, not including memory"
	},
	{.name = "STALL_BACKEND_BUSY",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816b,
	 .desc = "Backend stall cycles, backend busy The counter counts each cycle by STALL_BACKEND when operations are available from the frontend but the backend is not able to accept an operation because an execution unit is busy"
	},
	{.name = "STALL_BACKEND_RENAME",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x816d,
	 .desc = "Backend stall cycles, rename full The counter counts each cycle counted by STALL_BACKEND_CPUBOUND when operation are available from the frontend but at least one is not ready to be sent to the backend because no rename register is available"
	},
	{.name = "CAS_NEAR_PASS",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8171,
	 .desc = "Atomic memory Operation speculatively executed, Compare and Swap pass The counter counts each Compare and Swap operation counted by CAS_NEAR_SPEC that updates the location accessed"
	},
	{.name = "CAS_NEAR_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8172,
	 .desc = "Atomic memory Operation speculatively executed, Compare and Swap near The counter counts each Compare and Swap operation that executes locally to the PE"
	},
	{.name = "CAS_FAR_SPEC",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8173,
	 .desc = "Atomic memory Operation speculatively executed, Compare and Swap far The counter counts each Compare and Swap operation that does not execute locally to the PE"
	},
	{.name = "L1D_CACHE_REFILL_HWPRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x81bc,
	 .desc = "Level 1 data cache refill, hardware prefetch The counter counts each refill triggered by L1 prefetchers"
	},
	{.name = "L2D_CACHE_PRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x8285,
	 .desc = "Level 2 data cache, preload or prefetch hit The counter counts each fetch counted by either L2D_CACHE_HWPRF or L2D_CACHE_PRFM"
	},
	{.name = "L2D_CACHE_REFILL_PRF",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x828d,
	 .desc = "Level 2 data cache refill, preload or prefetch hit The counter counts each refill counted by either L2D_CACHE_REFILL_HWPRF or L2D_CACHE_REFILL_PRFM"
	},
	{.name = "LL_CACHE_REFILL",
	 .modmsk = ARMV8_ATTRS,
	 .code = 0x829a,
	 .desc = "Last level cache refill The counter counts each access counted by LL_CACHE that causes a refill of the Last level cache, or any other data, instruction, or unified cache of this PE, from outside of those caches"
	},
	/* END Neoverse N3 specific events */
};
