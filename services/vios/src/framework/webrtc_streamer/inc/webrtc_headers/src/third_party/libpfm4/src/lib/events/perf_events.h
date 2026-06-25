/*
 * Copyright (c) 2009 Google, Inc
 * Contributed by Stephane Eranian <eranian@google.com>
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

#define CACHE_ST_ACCESS(n, d, e) \
       {\
	.name = #n"-STORES",\
	.desc = d" store accesses",\
	.id   = PERF_COUNT_HW_CACHE_##e,\
	.type = PERF_TYPE_HW_CACHE,\
	.modmsk = PERF_ATTR_HW,\
	.umask_ovfl_idx = ~0UL,\
	.equiv = "PERF_COUNT_HW_CACHE_"#e":WRITE:ACCESS"\
       },\
       {\
	.name = #n"-STORE-MISSES",\
	.desc = d" store misses",\
	.id   = PERF_COUNT_HW_CACHE_##e,\
	.type = PERF_TYPE_HW_CACHE,\
	.modmsk = PERF_ATTR_HW,\
	.umask_ovfl_idx = ~0UL,\
	.equiv = "PERF_COUNT_HW_CACHE_"#e":WRITE:MISS"\
       }

#define CACHE_PF_ACCESS(n, d, e) \
       {\
	.name = #n"-PREFETCHES",\
	.desc = d" prefetch accesses",\
	.id   = PERF_COUNT_HW_CACHE_##e,\
	.type = PERF_TYPE_HW_CACHE,\
	.modmsk = PERF_ATTR_HW,\
	.umask_ovfl_idx = ~0UL,\
	.equiv = "PERF_COUNT_HW_CACHE_"#e":PREFETCH:ACCESS"\
       },\
       {\
	.name = #n"-PREFETCH-MISSES",\
	.desc = d" prefetch misses",\
	.id   = PERF_COUNT_HW_CACHE_##e,\
	.type = PERF_TYPE_HW_CACHE,\
	.modmsk = PERF_ATTR_HW,\
	.umask_ovfl_idx = ~0UL,\
	.equiv = "PERF_COUNT_HW_CACHE_"#e":PREFETCH:MISS"\
       }


#define CACHE_LD_ACCESS(n, d, e) \
       {\
	.name = #n"-LOADS",\
	.desc = d" load accesses",\
	.id   = PERF_COUNT_HW_CACHE_##e,\
	.type = PERF_TYPE_HW_CACHE,\
	.modmsk = PERF_ATTR_HW,\
	.umask_ovfl_idx = ~0UL,\
	.equiv = "PERF_COUNT_HW_CACHE_"#e":READ:ACCESS"\
       },\
       {\
	.name = #n"-LOAD-MISSES",\
	.desc = d" load misses",\
	.id   = PERF_COUNT_HW_CACHE_##e,\
	.type = PERF_TYPE_HW_CACHE,\
	.modmsk = PERF_ATTR_HW,\
	.umask_ovfl_idx = ~0UL,\
	.equiv = "PERF_COUNT_HW_CACHE_"#e":READ:MISS"\
       }

#define CACHE_ACCESS(n, d, e) \
	CACHE_LD_ACCESS(n, d, e), \
	CACHE_ST_ACCESS(n, d, e), \
	CACHE_PF_ACCESS(n, d, e)

#define ICACHE_ACCESS(n, d, e) \
	CACHE_LD_ACCESS(n, d, e), \
	CACHE_PF_ACCESS(n, d, e)

static perf_event_t perf_static_events[]={
	PCL_EVT_HW_FL(CPU_CYCLES, PERF_FL_PRECISE),
	PCL_EVT_AHW(CYCLES, CPU_CYCLES),
	PCL_EVT_AHW(CPU-CYCLES, CPU_CYCLES),

	PCL_EVT_HW(INSTRUCTIONS),
	PCL_EVT_AHW(INSTRUCTIONS, INSTRUCTIONS),

	PCL_EVT_HW(CACHE_REFERENCES),
	PCL_EVT_AHW(CACHE-REFERENCES, CACHE_REFERENCES),

	PCL_EVT_HW(CACHE_MISSES),
	PCL_EVT_AHW(CACHE-MISSES,CACHE_MISSES),

	PCL_EVT_HW(BRANCH_INSTRUCTIONS),
	PCL_EVT_AHW(BRANCH-INSTRUCTIONS, BRANCH_INSTRUCTIONS),
	PCL_EVT_AHW(BRANCHES, BRANCH_INSTRUCTIONS),

	PCL_EVT_HW(BRANCH_MISSES),
	PCL_EVT_AHW(BRANCH-MISSES, BRANCH_MISSES),

	PCL_EVT_HW(BUS_CYCLES),
	PCL_EVT_AHW(BUS-CYCLES, BUS_CYCLES),

	PCL_EVT_HW(STALLED_CYCLES_FRONTEND),
	PCL_EVT_AHW(STALLED-CYCLES-FRONTEND, STALLED_CYCLES_FRONTEND),
	PCL_EVT_AHW(IDLE-CYCLES-FRONTEND, STALLED_CYCLES_FRONTEND),

	PCL_EVT_HW(STALLED_CYCLES_BACKEND),
	PCL_EVT_AHW(STALLED-CYCLES-BACKEND, STALLED_CYCLES_BACKEND),
	PCL_EVT_AHW(IDLE-CYCLES-BACKEND, STALLED_CYCLES_BACKEND),

	PCL_EVT_HW(REF_CPU_CYCLES),
	PCL_EVT_AHW(REF-CYCLES,REF_CPU_CYCLES),

        PCL_EVT_SW(CPU_CLOCK),
        PCL_EVT_ASW(CPU-CLOCK, CPU_CLOCK),

        PCL_EVT_SW(TASK_CLOCK),
        PCL_EVT_ASW(TASK-CLOCK, TASK_CLOCK),

        PCL_EVT_SW(PAGE_FAULTS),
        PCL_EVT_ASW(PAGE-FAULTS, PAGE_FAULTS),
        PCL_EVT_ASW(FAULTS, PAGE_FAULTS),

        PCL_EVT_SW(CONTEXT_SWITCHES),
        PCL_EVT_ASW(CONTEXT-SWITCHES, CONTEXT_SWITCHES),
        PCL_EVT_ASW(CS, CONTEXT_SWITCHES),

        PCL_EVT_SW(CPU_MIGRATIONS),
        PCL_EVT_ASW(CPU-MIGRATIONS, CPU_MIGRATIONS),
        PCL_EVT_ASW(MIGRATIONS, CPU_MIGRATIONS),

        PCL_EVT_SW(PAGE_FAULTS_MIN),
        PCL_EVT_ASW(MINOR-FAULTS, PAGE_FAULTS_MIN),

        PCL_EVT_SW(PAGE_FAULTS_MAJ),
        PCL_EVT_ASW(MAJOR-FAULTS, PAGE_FAULTS_MAJ),

	PCL_EVT_SW(CGROUP_SWITCHES),
	PCL_EVT_ASW(CGROUP-SWITCHES, CGROUP_SWITCHES),

	{
	.name = "PERF_COUNT_HW_CACHE_L1D",
	.desc = "L1 data cache",
	.id   = PERF_COUNT_HW_CACHE_L1D,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 5,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "WRITE",
		  .udesc = "write access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_WRITE << 8,
		  .grpid = 0,
		},
		{ .uname = "PREFETCH",
		  .udesc = "prefetch access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_PREFETCH << 8,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	}
       },
       CACHE_ACCESS(L1-DCACHE, "L1 cache", L1D),
       {
	.name = "PERF_COUNT_HW_CACHE_L1I",
	.desc = "L1 instruction cache",
	.id   = PERF_COUNT_HW_CACHE_L1I,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 4,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "PREFETCH",
		  .udesc = "prefetch access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_PREFETCH << 8,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	}
       },
       ICACHE_ACCESS(L1-ICACHE, "L1I cache", L1I),
       {
	.name = "PERF_COUNT_HW_CACHE_LL",
	.desc = "Last level cache",
	.id   = PERF_COUNT_HW_CACHE_LL,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 5,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "WRITE",
		  .udesc = "write access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_WRITE << 8,
		  .grpid = 0,
		},
		{ .uname = "PREFETCH",
		  .udesc = "prefetch access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_PREFETCH << 8,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	}
       },
       CACHE_ACCESS(LLC, "Last level cache", LL),
       {
	.name = "PERF_COUNT_HW_CACHE_DTLB",
	.desc = "Data Translation Lookaside Buffer",
	.id   = PERF_COUNT_HW_CACHE_DTLB,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 5,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "WRITE",
		  .udesc = "write access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_WRITE << 8,
		  .grpid = 0,
		},
		{ .uname = "PREFETCH",
		  .udesc = "prefetch access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_PREFETCH << 8,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	}
       },
       CACHE_ACCESS(DTLB, "Data TLB", DTLB),
       {
	.name = "PERF_COUNT_HW_CACHE_ITLB",
	.desc = "Instruction Translation Lookaside Buffer",
	.id   = PERF_COUNT_HW_CACHE_ITLB,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 3,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	}
       },
       CACHE_LD_ACCESS(ITLB, "Instruction TLB", ITLB),
       {
	.name = "PERF_COUNT_HW_CACHE_BPU",
	.desc = "Branch Prediction Unit",
	.id   = PERF_COUNT_HW_CACHE_BPU,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 3,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	}
       },
       CACHE_LD_ACCESS(BRANCH, "Branch ", BPU),
       {
	.name = "PERF_COUNT_HW_CACHE_NODE",
	.desc = "Node memory access",
	.id   = PERF_COUNT_HW_CACHE_NODE,
	.type = PERF_TYPE_HW_CACHE,
	.numasks = 5,
	.modmsk = PERF_ATTR_HW,
	.umask_ovfl_idx = ~0UL,
	.ngrp = 2,
	.umasks = {
		{ .uname = "READ",
		  .udesc = "read access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_READ << 8,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 0,
		},
		{ .uname = "WRITE",
		  .udesc = "write access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_WRITE << 8,
		  .grpid = 0,
		},
		{ .uname = "PREFETCH",
		  .udesc = "prefetch access",
		  .uid   = PERF_COUNT_HW_CACHE_OP_PREFETCH << 8,
		  .grpid = 0,
		},
		{ .uname = "ACCESS",
		  .udesc = "hit access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16,
		  .grpid = 1,
		},
		{ .uname = "MISS",
		  .udesc = "miss access",
		  .uid   = PERF_COUNT_HW_CACHE_RESULT_MISS << 16,
		  .uflags= PERF_FL_DEFAULT,
		  .grpid = 1,
		}
	},
       },
       CACHE_ACCESS(NODE, "Node ", NODE)
};
#define PME_PERF_EVENT_COUNT (sizeof(perf_static_events)/sizeof(perf_event_t))

/*
 * the following events depend on the kernel exporting them. They may be dependent on hardware features
 */
static perf_event_t perf_optional_events[]={
       PCL_EVT_RAW(slots, 0x00, 0x04, "issue slots per logical CPU (used for topdown toplevel computation, must be first event in the group)"),
       PCL_EVT_RAW(topdown-retiring, 0x00, 0x80, "topdown useful slots retiring uops (must be used in a group with the other topdown- events with slots as leader)"),
       PCL_EVT_RAW(topdown-bad-spec, 0x00, 0x81, "topdown wasted slots due to bad speculation (must be used in a group with the other topdown- events with slots as leader)"),
       PCL_EVT_RAW(topdown-fe-bound, 0x00, 0x82, "topdown wasted slots due to frontend (must be used in a group with the other topdown- events with slots as leader)"),
       PCL_EVT_RAW(topdown-be-bound, 0x00, 0x83, "topdown wasted slots due to backend (must be used in a group with the other topdown- events with slots as leader)"),
};
#define PME_PERF_EVENT_OPT_COUNT (sizeof(perf_optional_events)/sizeof(perf_event_t))
