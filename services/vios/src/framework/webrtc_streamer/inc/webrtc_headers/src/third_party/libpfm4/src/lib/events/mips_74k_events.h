/*
 * Copyright (c) 2011 Samara Technology Group, Inc
 * Contributed by Philip Mucci <phil.mucci@@samaratechnologygroup.com>
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
 * Based on:
 * MIPS32 74KTM Processor Core Family Software Users' Manual
 * Document Number: MD00519 Revision 01.05 March 30, 2011
 */

static const mips_entry_t mips_74k_pe []={
  {
    .name = "CYCLES", /* BOTH */
    .code = 0x0,
    .desc = "Cycles",
  },
  {
    .name = "INSTRUCTIONS", /* BOTH */
    .code = 0x1,
    .desc = "Instructions graduated",
  },
  {
    .name = "PREDICTED_JR_31",
    .code = 0x2,
    .desc = "jr $31 (return) instructions whose target is predicted",
  },
  {
    .name = "JR_31_MISPREDICTIONS",
    .code = 0x82,
    .desc = "jr $31 (return) predicted but guessed wrong",
  },
  {
    .name = "REDIRECT_STALLS",
    .code = 0x3,
    .desc = "Cycles where no instruction is fetched because it has no next address candidate. This includes stalls due to register indirect jumps such as jr, stalls following a wait or eret and stalls dues to exceptions from instruction fetch",
  },
  {
    .name = "JR_31_NO_PREDICTIONS",
    .code = 0x83,
    .desc = "jr $31 (return) instructions fetched and not predicted using RPS",
  },
  {
    .name = "ITLB_ACCESSES",
    .code = 0x4,
    .desc = "ITLB accesses",
  },
  {
    .name = "ITLB_MISSES",
    .code = 0x84,
    .desc = "ITLB misses, which result in a JTLB access",
  },
  {
    .name = "JTLB_INSN_MISSES",
    .code = 0x85,
    .desc = "JTLB instruction access misses (will lead to an exception)",
  },
  {
    .name = "ICACHE_ACCESSES",
    .code = 0x6,
    .desc = "Instruction cache accesses. 74K cores have a 128-bit connection to the I-cache and fetch 4 instructions every access. This counts every such access, including accesses for instructions which are eventually discarded. For example, following a branch which is incorrectly predicted, the 74K core will continue to fetch instructions, which will eventually get thrown away",
  },
  {
    .name = "ICACHE_MISSES",
    .code = 0x86,
    .desc = "I-cache misses. Includes misses resulting from fetch-ahead and speculation",
  },
  {
    .name = "ICACHE_MISS_STALLS",
    .code = 0x7,
    .desc = "Cycles where no instruction is fetched because we missed in the I-cache",
  },
  {
    .name = "UNCACHED_IFETCH_STALLS",
    .code = 0x8,
    .desc = "Cycles where no instruction is fetched because we're waiting for an I-fetch from uncached memory",
  },
  {
    .name = "PDTRACE_BACK_STALLS",
    .code = 0x88,
    .desc = "PDTrace back stalls",
  },
  {
    .name = "IFU_REPLAYS",
    .code = 0x9,
    .desc = "Number of times the instruction fetch pipeline is flushed and replayed because the IFU buffers are full and unable to accept any instructions",
  },
  {
    .name = "KILLED_FETCH_SLOTS",
    .code = 0x89,
    .desc = "Valid fetch slots killed due to taken branches/jumps or stalling instructions",
  },
  {
    .name = "DDQ0_FULL_DR_STALLS",
    .code = 0xd,
    .desc = "Cycles where no instructions are brought into the IDU because the ALU instruction candidate pool is full",
  },
  {
    .name = "DDQ1_FULL_DR_STALLS",
    .code = 0x8d,
    .desc = "Cycles where no instructions are brought into the IDU because the AGEN instruction candidate pool is full",
  },
  {
    .name = "ALCB_FULL_DR_STALLS",
    .code = 0xe,
    .desc = "Cycles where no instructions can be added to the issue pool, because we have run out of ALU completion buffers (CBs)",
  },
  {
    .name = "AGCB_FULL_DR_STALLS",
    .code = 0x8e,
    .desc = "Cycles where no instructions can be added to the issue pool, because we have run out of AGEN completion buffers (CBs)",
  },
  {
    .name = "CLDQ_FULL_DR_STALLS",
    .code = 0xf,
    .desc = "Cycles where no instructions can be added to the issue pool, because we've used all the FIFO entries in the CLDQ which keep track of data coming back from the FPU",
  },
  {
    .name = "IODQ_FULL_DR_STALLS",
    .code = 0x8f,
    .desc = "Cycles where no instructions can be added to the issue pool, because we've filled the in order FIFO used for coprocessor 1 instructions (IOIQ)",
  },
  {
    .name = "ALU_EMPTY_CYCLES",
    .code = 0x10,
    .desc = "Cycles with no ALU-pipe issue; no instructions available",
  },
  {
    .name = "AGEN_EMPTY_CYCLES",
    .code = 0x90,
    .desc = "Cycles with no AGEN-pipe issue; no instructions available",
  },
  {
    .name = "ALU_OPERANDS_NOT_READY_CYCLES",
    .code = 0x11,
    .desc = "Cycles with no ALU-pipe issue; we have instructions, but operands not ready",
  },
  {
    .name = "AGEN_OPERANDS_NOT_READY_CYCLES",
    .code = 0x91,
    .desc = "Cycles with no AGEN-pipe issue; we have instructions, but operands not ready",
  },
  {
    .name = "ALU_NO_ISSUE_CYCLES",
    .code = 0x12,
    .desc = "Cycles with no ALU-pipe issue; we have instructions, but some resource is unavailable. This includes, operands are not ready (same as event 17), div in progress inhibits MDU instructions, CorExtend resource limitation",
  },
  {
    .name = "AGEN_NO_ISSUE_CYCLES",
    .code = 0x92,
    .desc = "Cycles with no AGEN-pipe issue; we have instructions, but some resource is unavailable. This includes, operands are not ready (same as event 17), Non-issued stores blocking ready to issue loads, issued cacheops blocking ready to issue loads",
  },
  {
    .name = "ALU_BUBBLE_CYCLES",
    .code = 0x13,
    .desc = "ALU-pipe bubble issued. The resulting empty pipe stage guarantees that some resource will be unused for a cycle, sometime soon. Used, for example, to guarantee an opportunity to write mfc1 data into a CB",
  },
  {
    .name = "AGEN_BUBBLE_CYCLES",
    .code = 0x93,
    .desc = "AGEN-pipe bubble issued. The resulting empty pipe stage guarantees that some resource will be unused for a cycle, sometime soon. Used, for example, to allow access to the data cache for refill or eviction",

  },
  {
    .name = "SINGLE_ISSUE_CYCLES",
    .code = 0x14,
    .desc = "Cycles when one instruction is issued",
  },
  {
    .name = "DUAL_ISSUE_CYCLES",
    .code = 0x94,
    .desc = "Cycles when two instructions are issued (one ALU, one AGEN)",
  },
  {
    .name = "OOO_ALU_ISSUE_CYCLES",
    .code = 0x15,
    .desc = "Cycles when instructions are issued out of order into the ALU pipe. i.e. instruction issued is not the oldest in the pool",
  },
  {
    .name = "OOO_AGEN_ISSUE_CYCLES",
    .code = 0x95,
    .desc = "Cycles when instructions are issued out of order into the AGEN pipe. i.e. instruction issued is not the oldest in the pool",
  },
  {
    .name = "JALR_JALR_HB_INSNS",
    .code = 0x16,
    .desc = "Graduated JAR/JALR.HB",
  },
  {
    .name = "DCACHE_LINE_REFILL_REQUESTS",
    .code = 0x96,
    .desc = "D-Cache line refill (not LD/ST misses)",
  },
  {
    .name = "DCACHE_LOAD_ACCESSES",
    .code = 0x17,
    .desc = "Cacheable loads - Counts all accesses to the D-cache caused by load instructions. This count includes instructions that do not graduate",
  },
  {
    .name = "DCACHE_ACCESSES",
    .code = 0x97,
    .desc = "All D-cache accesses (loads, stores, prefetch, cacheop etc). This count includes instructions that do not graduate",
  },
  {
    .name = "DCACHE_WRITEBACKS",
    .code = 0x18,
    .desc = "D-Cache writebacks",
  },
  {
    .name = "DCACHE_MISSES",
    .code = 0x98,
    .desc = "D-cache misses. This count is per instruction at graduation and includes load, store, prefetch, synci and address based cacheops",
  },
  {
    .name = "JTLB_DATA_ACCESSES",
    .code = 0x19,
    .desc = "JTLB d-side (data side as opposed to instruction side) accesses",
  },
  {
    .name = "JTLB_DATA_MISSES",
    .code = 0x99,
    .desc = "JTLB translation fails on d-side (data side as opposed to instruction side) accesses. This count includes instructions that do not graduate",
  },
  {
    .name = "LOAD_STORE_REPLAYS",
    .code = 0x1a,
    .desc = "Load/store instruction redirects, which happen when the load/store follows too closely on a possibly matching cacheop",
  },
  {
    .name = "DCACHE_VTAG_MISMATCH",
    .code = 0x9a,
    .desc = "The 74K core's D-cache has an auxiliary virtual tag, used to pick the right line early. When (occasionally) the physical tag match and virtual tag match do not line up, it is treated as a cache miss - in processing the miss the virtual tag is corrected for future accesses. This event counts those bogus misses",
  },
  {
    .name = "L2_CACHE_WRITEBACKS",
    .code = 0x1c,
    .desc = "L2 cache writebacks",
  },
  {
    .name = "L2_CACHE_ACCESSES",
    .code = 0x9c,
    .desc = "L2 cache accesses",
  },
  {
    .name = "L2_CACHE_MISSES",
    .code = 0x1d,
    .desc = "L2 cache misses",
  },
  {
    .name = "L2_CACHE_MISS_CYCLES",
    .code = 0x9d,
    .desc = "L2 cache miss cycles",
  },
  {
    .name = "FSB_FULL_STALLS",
    .code = 0x1e,
    .desc = "Cycles Fill Store Buffer(FSB) are full and cause a pipe stall",
  },
  {
    .name = "FSB_OVER_50_FULL",
    .code = 0x9e,
    .desc = "Cycles Fill Store Buffer(FSB) > 1/2 full",
  },
  {
    .name = "LDQ_FULL_STALLS",
    .code = 0x1f,
    .desc = "Cycles Load Data Queue (LDQ) are full and cause a pipe stall",
  },
  {
    .name = "LDQ_OVER_50_FULL",
    .code = 0x9f,
    .desc = "Cycles Load Data Queue(LDQ) > 1/2 full",
  },
  {
    .name = "WBB_FULL_STALLS",
    .code = 0x20,
    .desc = "Cycles Writeback Buffer(WBB) are full and cause a pipe stall",
  },
  {
    .name = "WBB_OVER_50_FULL",
    .code = 0xa0,
    .desc = "Cycles Writeback Buffer(WBB) > 1/2 full",
  },
  {
    .name = "LOAD_MISS_CONSUMER_REPLAYS",
    .code = 0x23,
    .desc = "Replays following optimistic issue of instruction dependent on load which missed. Counted only when the dependent instruction graduates",
  },
  {
    .name = "FPU_LOAD_INSNS",
    .code = 0xa3,
    .desc = "Floating Point Load instructions graduated",
  },
  {
    .name = "JR_NON_31_INSNS",
    .code = 0x24,
    .desc = "jr (not $31) instructions graduated",
  },
  {
    .name = "MISPREDICTED_JR_31_INSNS",
    .code = 0xa4,
    .desc = "jr $31 mispredicted at graduation",
  },
  {
    .name = "INT_BRANCH_INSNS",
    .code = 0x25,
    .desc = "Integer branch instructions graduated",
  },
  {
    .name = "FPU_BRANCH_INSNS",
    .code = 0xa5,
    .desc = "Floating point branch instructions graduated",
  },
  {
    .name = "BRANCH_LIKELY_INSNS",
    .code = 0x26,
    .desc = "Branch-likely instructions graduated",
  },
  {
    .name = "MISPREDICTED_BRANCH_LIKELY_INSNS",
    .code = 0xa6,
    .desc = "Mispredicted branch-likely instructions graduated",
  },
  {
    .name = "COND_BRANCH_INSNS",
    .code = 0x27,
    .desc = "Conditional branches graduated",
  },
  {
    .name = "MISPREDICTED_BRANCH_INSNS",
    .code = 0xa7,
    .desc = "Mispredicted conditional branches graduated",
  },
  {
    .name = "INTEGER_INSNS",
    .code = 0x28,
    .desc = "Integer instructions graduated (includes nop, ssnop, ehb as well as all arithmetic, logical, shift and extract type operations)",
  },
  {
    .name = "FPU_INSNS",
    .code = 0xa8,
    .desc = "Floating point instructions graduated (but not counting floating point load/store)",
  },
  {
    .name = "LOAD_INSNS",
    .code = 0x29,
    .desc = "Loads graduated (includes floating point)",
  },
  {
    .name = "STORE_INSNS",
    .code = 0xa9,
    .desc = "Stores graduated (includes floating point). Of sc instructions, only successful ones are counted",
  },
  {
    .name = "J_JAL_INSNS",
    .code = 0x2a,
    .desc = "j/jal graduated",
  },
  {
    .name = "MIPS16_INSNS",
    .code = 0xaa,
    .desc = "MIPS16e instructions graduated",
  },
  {
    .name = "NOP_INSNS",
    .code = 0x2b,
    .desc = "no-ops graduated - included (sll, nop, ssnop, ehb)",
  },
  {
    .name = "NT_MUL_DIV_INSNS",
    .code = 0xab,
    .desc = "integer multiply/divides graduated",
  },
  {
    .name = "DSP_INSNS",
    .code = 0x2c,
    .desc = "DSP instructions graduated",
  },
  {
    .name = "ALU_DSP_SATURATION_INSNS",
    .code = 0xac,
    .desc = "ALU-DSP instructions graduated, result was saturated",
  },
  {
    .name = "DSP_BRANCH_INSNS",
    .code = 0x2d,
    .desc = "DSP branch instructions graduated",
  },
  {
    .name = "MDU_DSP_SATURATION_INSNS",
    .code = 0xad,
    .desc = "MDU-DSP instructions graduated, result was saturated",
  },
  {
    .name = "UNCACHED_LOAD_INSNS",
    .code = 0x2e,
    .desc = "Uncached loads graduated",
  },
  {
    .name = "UNCACHED_STORE_INSNS",
    .code = 0xae,
    .desc = "Uncached stores graduated",
  },
  {
    .name = "EJTAG_INSN_TRIGGERS",
    .code = 0x31,
    .desc = "EJTAG instruction triggers",
  },
  {
    .name = "EJTAG_DATA_TRIGGERS",
    .code = 0xb1,
    .desc = "EJTAG data triggers",
  },
  {
    .name = "CP1_BRANCH_MISPREDICTIONS",
    .code = 0x32,
    .desc = "CP1 branches mispredicted",
  },
  {
    .name = "SC_INSNS",
    .code = 0x33,
    .desc = "sc instructions graduated",
  },
  {
    .name = "FAILED_SC_INSNS",
    .code = 0xb3,
    .desc = "sc instructions failed",
  },
  {
    .name = "PREFETCH_INSNS",
    .code = 0x34,
    .desc = "prefetch instructions graduated at the top of LSGB",
  },
  {
    .name = "CACHE_HIT_PREFETCH_INSNS",
    .code = 0xb4,
    .desc = "prefetch instructions which did nothing, because they hit in the cache",
  },
  {
    .name = "NO_INSN_CYCLES",
    .code = 0x35,
    .desc = "Cycles where no instructions graduated",
  },
  {
    .name = "LOAD_MISS_INSNS",
    .code = 0xb5,
    .desc = "Load misses graduated. Includes floating point loads",
  },
  {
    .name = "ONE_INSN_CYCLES",
    .code = 0x36,
    .desc = "Cycles where one instruction graduated",
  },
  {
    .name = "TWO_INSNS_CYCLES",
    .code = 0xb6,
    .desc = "Cycles where two instructions graduated",
  },
  {
    .name = "GFIFO_BLOCKED_CYCLES",
    .code = 0x37,
    .desc = "GFifo blocked cycles",
  },
  {
    .name = "FPU_STORE_INSNS",
    .code = 0xb7,
    .desc = "Floating point stores graduated",
  },
  {
    .name = "GFIFO_BLOCKED_TLB_CACHE",
    .code = 0x38,
    .desc = "GFifo blocked due to TLB or Cacheop",
  },
  {
    .name = "NO_INSTRUCTIONS_FROM_REPLAY_CYCLES",
    .code = 0xb8,
    .desc = "Number of cycles no instructions graduated from the time the pipe was flushed because of a replay until the first new instruction graduates. This is an indicator of the graduation bandwidth loss due to replay. Often times this replay is a result of event 25 and therefore an indicator of bandwidth lost due to cache misses",
  },
  {
    .name = "MISPREDICTION_BRANCH_NODELAY_CYCLES",
    .code = 0x39, /* even counters event 57 (raw 57) */
    .desc = "Slot 0 misprediction branch instruction graduation cycles without the delay slot"
  },
  {
    .name = "MISPREDICTION_BRANCH_DELAY_WAIT_CYCLES",
    .code = 0xb9, /* even counters event 57 (raw 57) */
    .desc = "Cycles waiting for delay slot to graduate on a mispredicted branch",
  },
  {
    .name = "EXCEPTIONS_TAKEN",
    .code = 0x3a,
    .desc = "Exceptions taken",
  },
  {
    .name = "GRADUATION_REPLAYS",
    .code = 0xba,
    .desc = "Replays initiated from graduation",
  },
  {
    .name = "COREEXTEND_EVENTS",
    .code = 0x3b,
    .desc = "Implementation specific CorExtend event. The integrator of this core may connect the core pin UDI_perfcnt_event to an event to be counted. This is intended for use with the CorExtend interface",
  },
  {
    .name = "DSPRAM_EVENTS",
    .code = 0xbe,
    .desc = "Implementation-specific DSPRAM event. The integrator of this core may connect the core pin SP_prf_c13_e62_xx to the event to be counted",
  },
  {
    .name = "L2_CACHE_SINGLE_BIT_ERRORS",
    .code = 0x3f,
    .desc = "L2 single-bit errors which were detected",
  },
  {
    .name = "SYSTEM_EVENT_0",
    .code = 0x40,
    .desc = "SI_Event[0] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[0] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_1",
    .code = 0xc0,
    .desc = "SI_Event[1] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[1] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_2",
    .code = 0x41,
    .desc = "SI_Event[2] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[2] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_3",
    .code = 0xc1,
    .desc = "SI_Event[3] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[3] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_4",
    .code = 0x42,
    .desc = "SI_Event[4] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[4] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_5",
    .code = 0xc2,
    .desc = "SI_Event[5] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[5] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_6",
    .code = 0x43,
    .desc = "SI_Event[6] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[6] to an event to be counted",
  },
  {
    .name = "SYSTEM_EVENT_7",
    .code = 0xc3,
    .desc = "SI_Event[7] - Implementation-specific system event. The integrator of this core may connect the core pin SI_PCEvent[7] to an event to be counted",
  },
  {
    .name = "OCP_ALL_REQUESTS",
    .code = 0x44,
    .desc = "All OCP requests accepted",
  },
  {
    .name = "OCP_ALL_CACHEABLE_REQUESTS",
    .code = 0xc4,
    .desc = "All OCP cacheable requests accepted",
  },
  {
    .name = "OCP_READ_REQUESTS",
    .code = 0x45,
    .desc = "OCP read requests accepted",
  },
  {
    .name = "OCP_READ_CACHEABLE_REQUESTS",
    .code = 0xc5,
    .desc = "OCP cacheable read requests accepted",
  },
  {
    .name = "OCP_WRITE_REQUESTS",
    .code = 0x46,
    .desc = "OCP write requests accepted",
  },
  {
    .name = "OCP_WRITE_CACHEABLE_REQUESTS",
    .code = 0xc6,
    .desc = "OCP cacheable write requests accepted",
  },
  {
    .name = "OCP_WRITE_DATA_SENT",
    .code = 0xc7,
    .desc = "OCP write data sent",
  },
  {
    .name = "OCP_READ_DATA_RECEIVED",
    .code = 0xc8,
    .desc = "OCP read data received",
  },
  {
    .name = "FSB_LESS_25_FULL",
    .code = 0x4a,
    .desc = "Cycles fill store buffer (FSB) < 1/4 full",
  },
  {
    .name = "FSB_25_50_FULL",
    .code = 0xca,
    .desc = "Cycles fill store buffer (FSB) 1/4 to 1/2 full",
  },
  {
    .name = "LDQ_LESS_25_FULL",
    .code = 0x4b,
    .desc = "Cycles load data queue (LDQ) < 1/4 full",
  },
  {
    .name = "LDQ_25_50_FULL",
    .code = 0xcb,
    .desc = "Cycles load data queue (LDQ) 1/4 to 1/2 full",
  },
  {
    .name = "WBB_LESS_25_FULL",
    .code = 0x4c,
    .desc = "Cycles writeback buffer (WBB) < 1/4 full",
  },
  {
    .name = "WBB_25_50_FULL",
    .code = 0xcc,
    .desc = "Cycles writeback buffer (WBB) 1/4 to 1/2 full",
  },
};
