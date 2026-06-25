/*
 * Copyright (c) 2006 IBM Corp.
 * Contributed by Kevin Corry <kevcorry@us.ibm.com>
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
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 * IN THE SOFTWARE.
 *
 * This header contains arrays to describe the Event-Selection-Control
 * Registers (ESCRs), Counter-Configuration-Control Registers (CCCRs),
 * and countable events on Pentium4/Xeon/EM64T systems.
 *
 * For more details, see:
 * - IA-32 Intel Architecture Software Developer's Manual,
 *   Volume 3B: System Programming Guide, Part 2
 *   (available at: http://www.intel.com/design/Pentium4/manuals/253669.htm)
 *   - Chapter 18.10: Performance Monitoring Overview
 *   - Chapter 18.13: Performance Monitoring - Pentium4 and Xeon Processors
 *   - Chapter 18.14: Performance Monitoring and Hyper-Threading Technology
 *   - Appendix A.1: Pentium4 and Xeon Processor Performance-Monitoring Events
 *
 * This header also contains an array to describe how the Perfmon PMCs map to
 * the ESCRs and CCCRs.
 */

#ifndef _NETBURST_EVENTS_H_
#define _NETBURST_EVENTS_H_
/**
 * netburst_events
 *
 * Array of events that can be counted on Pentium4.
 **/
static const netburst_entry_t netburst_events[] = {

	/* 0 */
	{.name = "TC_deliver_mode",
	 .desc = "The duration (in clock cycles) of the operating modes of "
		 "the trace cache and decode engine in the processor package",
	 .event_select = 0x1,
	 .escr_select = 0x1,
	 .allowed_escrs = { 9, 32 },
	 .perf_code = P4_EVENT_TC_DELIVER_MODE,
	 .event_masks = {
		{.name = "DD",
		 .desc = "Both logical CPUs in deliver mode",
		 .bit = 0,
		},
		{.name = "DB",
		 .desc = "Logical CPU 0 in deliver mode and "
			 "logical CPU 1 in build mode",
		 .bit = 1,
		},
		{.name = "DI",
		 .desc = "Logical CPU 0 in deliver mode and logical CPU 1 "
			 "either halted, under machine clear condition, or "
			 "transitioning to a long microcode flow",
		 .bit = 2,
		},
		{.name = "BD",
		 .desc = "Logical CPU 0 in build mode and "
			 "logical CPU 1 is in deliver mode",
		 .bit = 3,
		},
		{.name = "BB",
		 .desc = "Both logical CPUs in build mode",
		 .bit = 4,
		},
		{.name = "BI",
		 .desc = "Logical CPU 0 in build mode and logical CPU 1 "
			 "either halted, under machine clear condition, or "
			 "transitioning to a long microcode flow",
		 .bit = 5,
		},
		{.name = "ID",
		 .desc = "Logical CPU 0 either halted, under machine clear "
			 "condition, or transitioning to a long microcode "
			 "flow, and logical CPU 1 in deliver mode",
		 .bit = 6,
		},
		{.name = "IB",
		 .desc = "Logical CPU 0 either halted, under machine clear "
			 "condition, or transitioning to a long microcode "
			 "flow, and logical CPU 1 in build mode",
		 .bit = 7,
		},
	 },
	},

	/* 1 */
	{.name = "BPU_fetch_request",
	 .desc = "Instruction fetch requests by the Branch Prediction Unit",
	 .event_select = 0x3,
	 .escr_select = 0x0,
	 .allowed_escrs = { 0, 23 },
	 .perf_code = P4_EVENT_BPU_FETCH_REQUEST,
	 .event_masks = {
		{.name = "TCMISS",
		 .desc = "Trace cache lookup miss",
		 .bit = 0,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 2 */
	{.name = "ITLB_reference",
	 .desc = "Translations using the Instruction "
		 "Translation Look-Aside Buffer",
	 .event_select = 0x18,
	 .escr_select = 0x3,
	 .allowed_escrs = { 3, 26 },
	 .perf_code = P4_EVENT_ITLB_REFERENCE,
	 .event_masks = {
		{.name = "HIT",
		 .desc = "ITLB hit",
		 .bit = 0,
		},
		{.name = "MISS",
		 .desc = "ITLB miss",
		 .bit = 1,
		},
		{.name = "HIT_UC",
		 .desc = "Uncacheable ITLB hit",
		 .bit = 2,
		},
	 },
	},

	/* 3 */
	{.name = "memory_cancel",
	 .desc = "Canceling of various types of requests in the "
		 "Data cache Address Control unit (DAC)",
	 .event_select = 0x2,
	 .escr_select = 0x5,
	 .allowed_escrs = { 15, 38 },
	 .perf_code = P4_EVENT_MEMORY_CANCEL,
	 .event_masks = {
		{.name = "ST_RB_FULL",
		 .desc = "Replayed because no store request "
			 "buffer is available",
		 .bit = 2,
		},
		{.name = "64K_CONF",
		 .desc = "Conflicts due to 64K aliasing",
		 .bit = 3,
		},
	 },
	},

	/* 4 */
	{.name = "memory_complete",
	 .desc = "Completions of a load split, store split, "
		 "uncacheable (UC) split, or UC load",
	 .event_select = 0x8,
	 .escr_select = 0x2,
	 .allowed_escrs = { 13, 36 },
	 .perf_code = P4_EVENT_MEMORY_COMPLETE,
	 .event_masks = {
		{.name = "LSC",
		 .desc = "Load split completed, excluding UC/WC loads",
		 .bit = 0,
		},
		{.name = "SSC",
		 .desc = "Any split stores completed",
		 .bit = 1,
		},
	 },
	},

	/* 5 */
	{.name = "load_port_replay",
	 .desc = "Replayed events at the load port",
	 .event_select = 0x4,
	 .escr_select = 0x2,
	 .allowed_escrs = { 13, 36 },
	 .perf_code = P4_EVENT_LOAD_PORT_REPLAY,
	 .event_masks = {
		{.name = "SPLIT_LD",
		 .desc = "Split load",
		 .bit = 1,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 6 */
	{.name = "store_port_replay",
	 .desc = "Replayed events at the store port",
	 .event_select = 0x5,
	 .escr_select = 0x2,
	 .allowed_escrs = { 13, 36 },
	 .perf_code = P4_EVENT_STORE_PORT_REPLAY,
	 .event_masks = {
		{.name = "SPLIT_ST",
		 .desc = "Split store",
		 .bit = 1,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 7 */
	{.name = "MOB_load_replay",
	 .desc = "Count of times the memory order buffer (MOB) "
		 "caused a load operation to be replayed",
	 .event_select = 0x3,
	 .escr_select = 0x2,
	 .allowed_escrs = { 2, 25 },
	 .perf_code = P4_EVENT_MOB_LOAD_REPLAY,
	 .event_masks = {
		{.name = "NO_STA",
		 .desc = "Replayed because of unknown store address",
		 .bit = 1,
		},
		{.name = "NO_STD",
		 .desc = "Replayed because of unknown store data",
		 .bit = 3,
		},
		{.name = "PARTIAL_DATA",
		 .desc = "Replayed because of partially overlapped data "
			 "access between the load and store operations",
		 .bit = 4,
		},
		{.name = "UNALGN_ADDR",
		 .desc = "Replayed because the lower 4 bits of the "
			 "linear address do not match between the "
			 "load and store operations",
		 .bit = 5,
		},
	 },
	},

	/* 8 */
	{.name = "page_walk_type",
	 .desc = "Page walks that the page miss handler (PMH) performs",
	 .event_select = 0x1,
	 .escr_select = 0x4,
	 .allowed_escrs = { 4, 27 },
	 .perf_code = P4_EVENT_PAGE_WALK_TYPE,
	 .event_masks = {
		{.name = "DTMISS",
		 .desc = "Page walk for a data TLB miss (load or store)",
		 .bit = 0,
		},
		{.name = "ITMISS",
		 .desc = "Page walk for an instruction TLB miss",
		 .bit = 1,
		},
	 },
	},

	/* 9 */
	{.name = "BSQ_cache_reference",
	 .desc = "Cache references (2nd or 3rd level caches) as seen by the "
		 "bus unit. Read types include both load and RFO, and write "
		 "types include writebacks and evictions",
	 .event_select = 0xC,
	 .escr_select = 0x7,
	 .allowed_escrs = { 7, 30 },
	 .perf_code = P4_EVENT_BSQ_CACHE_REFERENCE,
	 .event_masks = {
		{.name = "RD_2ndL_HITS",
		 .desc = "Read 2nd level cache hit Shared",
		 .bit = 0,
		},
		{.name = "RD_2ndL_HITE",
		 .desc = "Read 2nd level cache hit Exclusive",
		 .bit = 1,
		},
		{.name = "RD_2ndL_HITM",
		 .desc = "Read 2nd level cache hit Modified",
		 .bit = 2,
		},
		{.name = "RD_3rdL_HITS",
		 .desc = "Read 3rd level cache hit Shared",
		 .bit = 3,
		},
		{.name = "RD_3rdL_HITE",
		 .desc = "Read 3rd level cache hit Exclusive",
		 .bit = 4,
		},
		{.name = "RD_3rdL_HITM",
		 .desc = "Read 3rd level cache hit Modified",
		 .bit = 5,
		},
		{.name = "RD_2ndL_MISS",
		 .desc = "Read 2nd level cache miss",
		 .bit = 8,
		},
		{.name = "RD_3rdL_MISS",
		 .desc = "Read 3rd level cache miss",
		 .bit = 9,
		},
		{.name = "WR_2ndL_MISS",
		 .desc = "A writeback lookup from DAC misses the 2nd "
			 "level cache (unlikely to happen)",
		 .bit = 10,
		},
	 },
	},

	/* 10 */
	{.name = "IOQ_allocation",
	 .desc = "Count of various types of transactions on the bus. A count "
		 "is generated each time a transaction is allocated into the "
		 "IOQ that matches the specified mask bits. An allocated entry "
		 "can be a sector (64 bytes) or a chunk of 8 bytes. Requests "
		 "are counted once per retry. All 'TYPE_BIT*' event-masks "
		 "together are treated as a single 5-bit value",
	 .event_select = 0x3,
	 .escr_select = 0x6,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_IOQ_ALLOCATION,
	 .event_masks = {
		{.name = "TYPE_BIT0",
		 .desc = "Bus request type (bit 0)",
		 .bit = 0,
		},
		{.name = "TYPE_BIT1",
		 .desc = "Bus request type (bit 1)",
		 .bit = 1,
		},
		{.name = "TYPE_BIT2",
		 .desc = "Bus request type (bit 2)",
		 .bit = 2,
		},
		{.name = "TYPE_BIT3",
		 .desc = "Bus request type (bit 3)",
		 .bit = 3,
		},
		{.name = "TYPE_BIT4",
		 .desc = "Bus request type (bit 4)",
		 .bit = 4,
		},
		{.name = "ALL_READ",
		 .desc = "Count read entries",
		 .bit = 5,
		},
		{.name = "ALL_WRITE",
		 .desc = "Count write entries",
		 .bit = 6,
		},
		{.name = "MEM_UC",
		 .desc = "Count UC memory access entries",
		 .bit = 7,
		},
		{.name = "MEM_WC",
		 .desc = "Count WC memory access entries",
		 .bit = 8,
		},
		{.name = "MEM_WT",
		 .desc = "Count write-through (WT) memory access entries",
		 .bit = 9,
		},
		{.name = "MEM_WP",
		 .desc = "Count write-protected (WP) memory access entries",
		 .bit = 10,
		},
		{.name = "MEM_WB",
		 .desc = "Count WB memory access entries",
		 .bit = 11,
		},
		{.name = "OWN",
		 .desc = "Count all store requests driven by processor, as "
			 "opposed to other processor or DMA",
		 .bit = 13,
		},
		{.name = "OTHER",
		 .desc = "Count all requests driven by other "
			 "processors or DMA",
		 .bit = 14,
		},
		{.name = "PREFETCH",
		 .desc = "Include HW and SW prefetch requests in the count",
		 .bit = 15,
		},
	 },
	},

	/* 11 */
	{.name = "IOQ_active_entries",
	 .desc = "Number of entries (clipped at 15) in the IOQ that are "
		 "active. An allocated entry can be a sector (64 bytes) "
		 "or a chunk of 8 bytes. This event must be programmed in "
		 "conjunction with IOQ_allocation. All 'TYPE_BIT*' event-masks "
		 "together are treated as a single 5-bit value",
	 .event_select = 0x1A,
	 .escr_select = 0x6,
	 .allowed_escrs = { 29, -1 },
	 .perf_code = P4_EVENT_IOQ_ACTIVE_ENTRIES,
	 .event_masks = {
		{.name = "TYPE_BIT0",
		 .desc = "Bus request type (bit 0)",
		 .bit = 0,
		},
		{.name = "TYPE_BIT1",
		 .desc = "Bus request type (bit 1)",
		 .bit = 1,
		},
		{.name = "TYPE_BIT2",
		 .desc = "Bus request type (bit 2)",
		 .bit = 2,
		},
		{.name = "TYPE_BIT3",
		 .desc = "Bus request type (bit 3)",
		 .bit = 3,
		},
		{.name = "TYPE_BIT4",
		 .desc = "Bus request type (bit 4)",
		 .bit = 4,
		},
		{.name = "ALL_READ",
		 .desc = "Count read entries",
		 .bit = 5,
		},
		{.name = "ALL_WRITE",
		 .desc = "Count write entries",
		 .bit = 6,
		},
		{.name = "MEM_UC",
		 .desc = "Count UC memory access entries",
		 .bit = 7,
		},
		{.name = "MEM_WC",
		 .desc = "Count WC memory access entries",
		 .bit = 8,
		},
		{.name = "MEM_WT",
		 .desc = "Count write-through (WT) memory access entries",
		 .bit = 9,
		},
		{.name = "MEM_WP",
		 .desc = "Count write-protected (WP) memory access entries",
		 .bit = 10,
		},
		{.name = "MEM_WB",
		 .desc = "Count WB memory access entries",
		 .bit = 11,
		},
		{.name = "OWN",
		 .desc = "Count all store requests driven by processor, as "
			 "opposed to other processor or DMA",
		 .bit = 13,
		},
		{.name = "OTHER",
		 .desc = "Count all requests driven by other "
			 "processors or DMA",
		 .bit = 14,
		},
		{.name = "PREFETCH",
		 .desc = "Include HW and SW prefetch requests in the count",
		 .bit = 15,
		},
	 },
	},

	/* 12 */
	{.name = "FSB_data_activity",
	 .desc = "Count of DRDY or DBSY events that "
		 "occur on the front side bus",
	 .event_select = 0x17,
	 .escr_select = 0x6,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_FSB_DATA_ACTIVITY,
	 .event_masks = {
		{.name = "DRDY_DRV",
		 .desc = "Count when this processor drives data onto the bus. "
			 "Includes writes and implicit writebacks",
		 .bit = 0,
		},
		{.name = "DRDY_OWN",
		 .desc = "Count when this processor reads data from the bus. "
			 "Includes loads and some PIC transactions. Count "
			 "DRDY events that we drive. Count DRDY events sampled "
			 "that we own",
		 .bit = 1,
		},
		{.name = "DRDY_OTHER",
		 .desc = "Count when data is on the bus but not being sampled "
			 "by the processor. It may or may not be driven by "
			 "this processor",
		 .bit = 2,
		},
		{.name = "DBSY_DRV",
		 .desc = "Count when this processor reserves the bus for use "
			 "in the next bus cycle in order to drive data",
		 .bit = 3,
		},
		{.name = "DBSY_OWN",
		 .desc = "Count when some agent reserves the bus for use in "
			 "the next bus cycle to drive data that this processor "
			 "will sample",
		 .bit = 4,
		},
		{.name = "DBSY_OTHER",
		 .desc = "Count when some agent reserves the bus for use in "
			 "the next bus cycle to drive data that this processor "
			 "will NOT sample. It may or may not be being driven "
			 "by this processor",
		 .bit = 5,
		},
	 },
	},

	/* 13 */
	{.name = "BSQ_allocation",
	 .desc = "Allocations in the Bus Sequence Unit (BSQ). The event mask "
		 "bits consist of four sub-groups: request type, request "
		 "length, memory type, and a sub-group consisting mostly of "
		 "independent bits (5 through 10). Must specify a mask for "
		 "each sub-group",
	 .event_select = 0x5,
	 .escr_select = 0x7,
	 .allowed_escrs = { 7, -1 },
	 .perf_code = P4_EVENT_BSQ_ALLOCATION,
	 .event_masks = {
		{.name = "REQ_TYPE0",
		 .desc = "Along with REQ_TYPE1, request type encodings are: "
			 "0 - Read (excludes read invalidate), 1 - Read "
			 "invalidate, 2 - Write (other than writebacks), 3 - "
			 "Writeback (evicted from cache)",
		 .bit = 0,
		},
		{.name = "REQ_TYPE1",
		 .desc = "Along with REQ_TYPE0, request type encodings are: "
			 "0 - Read (excludes read invalidate), 1 - Read "
			 "invalidate, 2 - Write (other than writebacks), 3 - "
			 "Writeback (evicted from cache)",
		 .bit = 1,
		},
		{.name = "REQ_LEN0",
		 .desc = "Along with REQ_LEN1, request length encodings are: "
			 "0 - zero chunks, 1 - one chunk, 3 - eight chunks",
		 .bit = 2,
		},
		{.name = "REQ_LEN1",
		 .desc = "Along with REQ_LEN0, request length encodings are: "
			 "0 - zero chunks, 1 - one chunk, 3 - eight chunks",
		 .bit = 3,
		},
		{.name = "REQ_IO_TYPE",
		 .desc = "Request type is input or output",
		 .bit = 5,
		},
		{.name = "REQ_LOCK_TYPE",
		 .desc = "Request type is bus lock",
		 .bit = 6,
		},
		{.name = "REQ_CACHE_TYPE",
		 .desc = "Request type is cacheable",
		 .bit = 7,
		},
		{.name = "REQ_SPLIT_TYPE",
		 .desc = "Request type is a bus 8-byte chunk split across "
			 "an 8-byte boundary",
		 .bit = 8,
		},
		{.name = "REQ_DEM_TYPE",
		 .desc = "0: Request type is HW.SW prefetch. "
			 "1: Request type is a demand",
		 .bit = 9,
		},
		{.name = "REQ_ORD_TYPE",
		 .desc = "Request is an ordered type",
		 .bit = 10,
		},
		{.name = "MEM_TYPE0",
		 .desc = "Along with MEM_TYPE1 and MEM_TYPE2, "
			 "memory type encodings are: 0 - UC, "
			 "1 - USWC, 4- WT, 5 - WP, 6 - WB",
		 .bit = 11,
		},
		{.name = "MEM_TYPE1",
		 .desc = "Along with MEM_TYPE0 and MEM_TYPE2, "
			 "memory type encodings are: 0 - UC, "
			 "1 - USWC, 4- WT, 5 - WP, 6 - WB",
		 .bit = 12,
		},
		{.name = "MEM_TYPE2",
		 .desc = "Along with MEM_TYPE0 and MEM_TYPE1, "
			 "memory type encodings are: 0 - UC, "
			 "1 - USWC, 4- WT, 5 - WP, 6 - WB",
		 .bit = 13,
		},
	 },
	},

	/* 14 */
	{.name = "BSQ_active_entries",
	 .desc = "Number of BSQ entries (clipped at 15) currently active "
		 "(valid) which meet the subevent mask criteria during "
		 "allocation in the BSQ. Active request entries are allocated "
		 "on the BSQ until de-allocated. De-allocation of an entry "
		 "does not necessarily imply the request is filled. This "
		 "event must be programmed in conjunction with BSQ_allocation",
	 .event_select = 0x6,
	 .escr_select = 0x7,
	 .allowed_escrs = { 30, -1 },
	 .perf_code = P4_EVENT_BSQ_ACTIVE_ENTRIES,
	 .event_masks = {
		{.name = "REQ_TYPE0",
		 .desc = "Along with REQ_TYPE1, request type encodings are: "
			 "0 - Read (excludes read invalidate), 1 - Read "
			 "invalidate, 2 - Write (other than writebacks), 3 - "
			 "Writeback (evicted from cache)",
		 .bit = 0,
		},
		{.name = "REQ_TYPE1",
		 .desc = "Along with REQ_TYPE0, request type encodings are: "
			 "0 - Read (excludes read invalidate), 1 - Read "
			 "invalidate, 2 - Write (other than writebacks), 3 - "
			 "Writeback (evicted from cache)",
		 .bit = 1,
		},
		{.name = "REQ_LEN0",
		 .desc = "Along with REQ_LEN1, request length encodings are: "
			 "0 - zero chunks, 1 - one chunk, 3 - eight chunks",
		 .bit = 2,
		},
		{.name = "REQ_LEN1",
		 .desc = "Along with REQ_LEN0, request length encodings are: "
			 "0 - zero chunks, 1 - one chunk, 3 - eight chunks",
		 .bit = 3,
		},
		{.name = "REQ_IO_TYPE",
		 .desc = "Request type is input or output",
		 .bit = 5,
		},
		{.name = "REQ_LOCK_TYPE",
		 .desc = "Request type is bus lock",
		 .bit = 6,
		},
		{.name = "REQ_CACHE_TYPE",
		 .desc = "Request type is cacheable",
		 .bit = 7,
		},
		{.name = "REQ_SPLIT_TYPE",
		 .desc = "Request type is a bus 8-byte chunk split across "
			 "an 8-byte boundary",
		 .bit = 8,
		},
		{.name = "REQ_DEM_TYPE",
		 .desc = "0: Request type is HW.SW prefetch. "
			 "1: Request type is a demand",
		 .bit = 9,
		},
		{.name = "REQ_ORD_TYPE",
		 .desc = "Request is an ordered type",
		 .bit = 10,
		},
		{.name = "MEM_TYPE0",
		 .desc = "Along with MEM_TYPE1 and MEM_TYPE2, "
			 "memory type encodings are: 0 - UC, "
			 "1 - USWC, 4- WT, 5 - WP, 6 - WB",
		 .bit = 11,
		},
		{.name = "MEM_TYPE1",
		 .desc = "Along with MEM_TYPE0 and MEM_TYPE2, "
			 "memory type encodings are: 0 - UC, "
			 "1 - USWC, 4- WT, 5 - WP, 6 - WB",
		 .bit = 12,
		},
		{.name = "MEM_TYPE2",
		 .desc = "Along with MEM_TYPE0 and MEM_TYPE1, "
			 "memory type encodings are: 0 - UC, "
			 "1 - USWC, 4- WT, 5 - WP, 6 - WB",
		 .bit = 13,
		},
	 },
	},

	/* 15 */
	{.name = "SSE_input_assist",
	 .desc = "Number of times an assist is requested to handle problems "
		 "with input operands for SSE/SSE2/SSE3 operations; most "
		 "notably denormal source operands when the DAZ bit isn't set",
	 .event_select = 0x34,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_SSE_INPUT_ASSIST,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count assists for SSE/SSE2/SSE3 uops",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 16 */
	{.name = "packed_SP_uop",
	 .desc = "Number of packed single-precision uops",
	 .event_select = 0x8,
	 .escr_select = 0x1,
	 .perf_code = P4_EVENT_PACKED_SP_UOP,
	 .allowed_escrs = { 12, 35 },
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all uops operating on packed "
			 "single-precisions operands",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 17 */
	{.name = "packed_DP_uop",
	 .desc = "Number of packed double-precision uops",
	 .event_select = 0xC,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_PACKED_DP_UOP,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all uops operating on packed "
			 "double-precisions operands",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 18 */
	{.name = "scalar_SP_uop",
	 .desc = "Number of scalar single-precision uops",
	 .event_select = 0xA,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_SCALAR_SP_UOP,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all uops operating on scalar "
			 "single-precisions operands",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 19 */
	{.name = "scalar_DP_uop",
	 .desc = "Number of scalar double-precision uops",
	 .event_select = 0xE,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_SCALAR_DP_UOP,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all uops operating on scalar "
			 "double-precisions operands",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 20 */
	{.name = "64bit_MMX_uop",
	 .desc = "Number of MMX instructions which "
		 "operate on 64-bit SIMD operands",
	 .event_select = 0x2,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_64BIT_MMX_UOP,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all uops operating on 64-bit SIMD integer "
			 "operands in memory or MMX registers",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 21 */
	{.name = "128bit_MMX_uop",
	 .desc = "Number of MMX instructions which "
		 "operate on 128-bit SIMD operands",
	 .event_select = 0x1A,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_128BIT_MMX_UOP,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all uops operating on 128-bit SIMD integer "
			 "operands in memory or MMX registers",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 22 */
	{.name = "x87_FP_uop",
	 .desc = "Number of x87 floating-point uops",
	 .event_select = 0x4,
	 .escr_select = 0x1,
	 .allowed_escrs = { 12, 35 },
	 .perf_code = P4_EVENT_X87_FP_UOP,
	 .event_masks = {
		{.name = "ALL",
		 .desc = "Count all x87 FP uops",
		 .bit = 15,
		 .flags = NETBURST_FL_DFL,
		},
		{.name = "TAG0",
		 .desc = "Tag this event with tag bit 0 "
			 "for retirement counting with execution_event",
		 .bit = 16,
		},
		{.name = "TAG1",
		 .desc = "Tag this event with tag bit 1 "
			 "for retirement counting with execution_event",
		 .bit = 17,
		},
		{.name = "TAG2",
		 .desc = "Tag this event with tag bit 2 "
			 "for retirement counting with execution_event",
		 .bit = 18,
		},
		{.name = "TAG3",
		 .desc = "Tag this event with tag bit 3 "
			 "for retirement counting with execution_event",
		 .bit = 19,
		},
	 },
	},

	/* 23 */
	{.name = "TC_misc",
	 .desc = "Miscellaneous events detected by the TC. The counter will "
		 "count twice for each occurrence",
	 .event_select = 0x6,
	 .escr_select = 0x1,
	 .allowed_escrs = { 9, 32 },
	 .perf_code = P4_EVENT_TC_MISC,
	 .event_masks = {
		{.name = "FLUSH",
		 .desc = "Number of flushes",
		 .bit = 4,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 24 */
	{.name = "global_power_events",
	 .desc = "Counts the time during which a processor is not stopped",
	 .event_select = 0x13,
	 .escr_select = 0x6,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_GLOBAL_POWER_EVENTS,
	 .event_masks = {
		{.name = "RUNNING",
		 .desc = "The processor is active (includes the "
			 "handling of HLT STPCLK and throttling",
		 .bit = 0,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 25 */
	{.name = "tc_ms_xfer",
	 .desc = "Number of times that uop delivery changed from TC to MS ROM",
	 .event_select = 0x5,
	 .escr_select = 0x0,
	 .allowed_escrs = { 8, 31 },
	 .perf_code = P4_EVENT_TC_MS_XFER,
	 .event_masks = {
		{.name = "CISC",
		 .desc = "A TC to MS transfer occurred",
		 .bit = 0,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 26 */
	{.name = "uop_queue_writes",
	 .desc = "Number of valid uops written to the uop queue",
	 .event_select = 0x9,
	 .escr_select = 0x0,
	 .allowed_escrs = { 8, 31 },
	 .perf_code = P4_EVENT_UOP_QUEUE_WRITES,
	 .event_masks = {
		{.name = "FROM_TC_BUILD",
		 .desc = "The uops being written are from TC build mode",
		 .bit = 0,
		},
		{.name = "FROM_TC_DELIVER",
		 .desc = "The uops being written are from TC deliver mode",
		 .bit = 1,
		},
		{.name = "FROM_ROM",
		 .desc = "The uops being written are from microcode ROM",
		 .bit = 2,
		},
	 },
	},

	/* 27 */
	{.name = "retired_mispred_branch_type",
	 .desc = "Number of retiring mispredicted branches by type",
	 .event_select = 0x5,
	 .escr_select = 0x2,
	 .allowed_escrs = { 10, 33 },
	 .perf_code = P4_EVENT_RETIRED_MISPRED_BRANCH_TYPE,
	 .event_masks = {
		{.name = "CONDITIONAL",
		 .desc = "Conditional jumps",
		 .bit = 1,
		},
		{.name = "CALL",
		 .desc = "Indirect call branches",
		 .bit = 2,
		},
		{.name = "RETURN",
		 .desc = "Return branches",
		 .bit = 3,
		},
		{.name = "INDIRECT",
		 .desc = "Returns, indirect calls, or indirect jumps",
		 .bit = 4,
		},
	 },
	},

	/* 28 */
	{.name = "retired_branch_type",
	 .desc = "Number of retiring branches by type",
	 .event_select = 0x4,
	 .escr_select = 0x2,
	 .allowed_escrs = { 10, 33 },
	 .perf_code = P4_EVENT_RETIRED_BRANCH_TYPE,
	 .event_masks = {
		{.name = "CONDITIONAL",
		 .desc = "Conditional jumps",
		 .bit = 1,
		},
		{.name = "CALL",
		 .desc = "Indirect call branches",
		 .bit = 2,
		},
		{.name = "RETURN",
		 .desc = "Return branches",
		 .bit = 3,
		},
		{.name = "INDIRECT",
		 .desc = "Returns, indirect calls, or indirect jumps",
		 .bit = 4,
		},
	 },
	},

	/* 29 */
	{.name = "resource_stall",
	 .desc = "Occurrences of latency or stalls in the Allocator",
	 .event_select = 0x1,
	 .escr_select = 0x1,
	 .allowed_escrs = { 17, 40 },
	 .perf_code = P4_EVENT_RESOURCE_STALL,
	 .event_masks = {
		{.name = "SBFULL",
		 .desc = "A stall due to lack of store buffers",
		 .bit = 5,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 30 */
	{.name = "WC_Buffer",
	 .desc = "Number of Write Combining Buffer operations",
	 .event_select = 0x5,
	 .escr_select = 0x5,
	 .allowed_escrs = { 15, 38 },
	 .perf_code = P4_EVENT_WC_BUFFER,
	 .event_masks = {
		{.name = "WCB_EVICTS",
		 .desc = "WC Buffer evictions of all causes",
		 .bit = 0,
		},
		{.name = "WCB_FULL_EVICT",
		 .desc = "WC Buffer eviction; no WC buffer is available",
		 .bit = 1,
		},
	 },
	},

	/* 31 */
	{.name = "b2b_cycles",
	 .desc = "Number of back-to-back bus cycles",
	 .event_select = 0x16,
	 .escr_select = 0x3,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_B2B_CYCLES,
	 .event_masks = {
		{.name = "BIT1",
		 .desc = "bit 1",
		 .bit = 1,
		},
		{.name = "BIT2",
		 .desc = "bit 2",
		 .bit = 2,
		},
		{.name = "BIT3",
		 .desc = "bit 3",
		 .bit = 3,
		},
		{.name = "BIT4",
		 .desc = "bit 4",
		 .bit = 4,
		},
		{.name = "BIT5",
		 .desc = "bit 5",
		 .bit = 4,
		},
		{.name = "BIT6",
		 .desc = "bit 6",
		 .bit = 4,
		},
	 },
	},
	/* 32 */
	{.name = "bnr",
	 .desc = "Number of bus-not-ready conditions",
	 .event_select = 0x8,
	 .escr_select = 0x3,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_BNR,
	 .event_masks = {
		{.name = "BIT0",
		 .desc = "bit 0",
		 .bit = 0,
		},
		{.name = "BIT1",
		 .desc = "bit 1",
		 .bit = 1,
		},
		{.name = "BIT2",
		 .desc = "bit 2",
		 .bit = 2,
		},
	 },
	},

	/* 33 */
	{.name = "snoop",
	 .desc = "Number of snoop hit modified bus traffic",
	 .event_select = 0x6,
	 .escr_select = 0x3,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_SNOOP,
	 .event_masks = {
		{.name = "BIT2",
		 .desc = "bit 2",
		 .bit = 2,
		},
		{.name = "BIT6",
		 .desc = "bit 6",
		 .bit = 6,
		},
		{.name = "BIT7",
		 .desc = "bit 7",
		 .bit = 7,
		},
	 },
	},

	/* 34 */
	{.name = "response",
	 .desc = "Count of different types of responses",
	 .event_select = 0x4,
	 .escr_select = 0x3,
	 .allowed_escrs = { 6, 29 },
	 .perf_code = P4_EVENT_RESPONSE,
	 .event_masks = {
		{.name = "BIT1",
		 .desc = "bit 1",
		 .bit = 1,
		},
		{.name = "BIT2",
		 .desc = "bit 2",
		 .bit = 2,
		},
		{.name = "BIT8",
		 .desc = "bit 8",
		 .bit = 8,
		},
		{.name = "BIT9",
		 .desc = "bit 9",
		 .bit = 9,
		},
	 },
	},

	/* 35 */
	{.name = "front_end_event",
	 .desc = "Number of retirements of tagged uops which are specified "
		 "through the front-end tagging mechanism",
	 .event_select = 0x8,
	 .escr_select = 0x5,
	 .allowed_escrs = { 21, 43 },
	 .perf_code = P4_EVENT_FRONT_END_EVENT,
	 .event_masks = {
		{.name = "NBOGUS",
		 .desc = "The marked uops are not bogus",
		 .bit = 0,
		},
		{.name = "BOGUS",
		 .desc = "The marked uops are bogus",
		 .bit = 1,
		},
	 },
	},

	/* 36 */
	{.name = "execution_event",
	 .desc = "Number of retirements of tagged uops which are specified "
		 "through the execution tagging mechanism. The event-mask "
		 "allows from one to four types of uops to be tagged",
	 .event_select = 0xC,
	 .escr_select = 0x5,
	 .allowed_escrs = { 21, 43 },
	 .perf_code = P4_EVENT_EXECUTION_EVENT,
	 .event_masks = {
		{.name = "NBOGUS0",
		 .desc = "The marked uops are not bogus",
		 .bit = 0,
		},
		{.name = "NBOGUS1",
		 .desc = "The marked uops are not bogus",
		 .bit = 1,
		},
		{.name = "NBOGUS2",
		 .desc = "The marked uops are not bogus",
		 .bit = 2,
		},
		{.name = "NBOGUS3",
		 .desc = "The marked uops are not bogus",
		 .bit = 3,
		},
		{.name = "BOGUS0",
		 .desc = "The marked uops are bogus",
		 .bit = 4,
		},
		{.name = "BOGUS1",
		 .desc = "The marked uops are bogus",
		 .bit = 5,
		},
		{.name = "BOGUS2",
		 .desc = "The marked uops are bogus",
		 .bit = 6,
		},
		{.name = "BOGUS3",
		 .desc = "The marked uops are bogus",
		 .bit = 7,
		},
	 },
	},

	/* 37 */
	{.name = "replay_event",
	 .desc = "Number of retirements of tagged uops which are specified "
		 "through the replay tagging mechanism",
	 .event_select = 0x9,
	 .escr_select = 0x5,
	 .allowed_escrs = { 21, 43 },
	 .perf_code = P4_EVENT_REPLAY_EVENT,
	 .event_masks = {
		{.name = "NBOGUS",
		 .desc = "The marked uops are not bogus",
		 .bit = 0,
		},
		{.name = "BOGUS",
		 .desc = "The marked uops are bogus",
		 .bit = 1,
		},
		{.name = "L1_LD_MISS",
		 .desc = "Virtual mask for L1 cache load miss replays",
		 .bit = 2,
		},
		{.name = "L2_LD_MISS",
		 .desc = "Virtual mask for L2 cache load miss replays",
		 .bit = 3,
		},
		{.name = "DTLB_LD_MISS",
		 .desc = "Virtual mask for DTLB load miss replays",
		 .bit = 4,
		},
		{.name = "DTLB_ST_MISS",
		 .desc = "Virtual mask for DTLB store miss replays",
		 .bit = 5,
		},
		{.name = "DTLB_ALL_MISS",
		 .desc = "Virtual mask for all DTLB miss replays",
		 .bit = 6,
		},
		{.name = "BR_MSP",
		 .desc = "Virtual mask for tagged mispredicted branch replays",
		 .bit = 7,
		},
		{.name = "MOB_LD_REPLAY",
		 .desc = "Virtual mask for MOB load replays",
		 .bit = 8,
		},
		{.name = "SP_LD_RET",
		 .desc = "Virtual mask for split load replays. Use with load_port_replay event",
		 .bit = 9,
		},
		{.name = "SP_ST_RET",
		 .desc = "Virtual mask for split store replays. Use with store_port_replay event",
		 .bit = 10,
		},
	 },
	},

	/* 38 */
	{.name = "instr_retired",
	 .desc = "Number of instructions retired during a clock cycle",
	 .event_select = 0x2,
	 .escr_select = 0x4,
	 .allowed_escrs = { 20, 42 },
	 .perf_code = P4_EVENT_INSTR_RETIRED,
	 .event_masks = {
		{.name = "NBOGUSNTAG",
		 .desc = "Non-bogus instructions that are not tagged",
		 .bit = 0,
		},
		{.name = "NBOGUSTAG",
		 .desc = "Non-bogus instructions that are tagged",
		 .bit = 1,
		},
		{.name = "BOGUSNTAG",
		 .desc = "Bogus instructions that are not tagged",
		 .bit = 2,
		},
		{.name = "BOGUSTAG",
		 .desc = "Bogus instructions that are tagged",
		 .bit = 3,
		},
	 },
	},

	/* 39 */
	{.name = "uops_retired",
	 .desc = "Number of uops retired during a clock cycle",
	 .event_select = 0x1,
	 .escr_select = 0x4,
	 .allowed_escrs = { 20, 42 },
	 .perf_code = P4_EVENT_UOPS_RETIRED,
	 .event_masks = {
		{.name = "NBOGUS",
		 .desc = "The marked uops are not bogus",
		 .bit = 0,
		},
		{.name = "BOGUS",
		 .desc = "The marked uops are bogus",
		 .bit = 1,
		},
	 },
	},

	/* 40 */
	{.name = "uops_type",
	 .desc = "This event is used in conjunction with with the front-end "
		 "mechanism to tag load and store uops",
	 .event_select = 0x2,
	 .escr_select = 0x2,
	 .allowed_escrs = { 18, 41 },
	 .perf_code = P4_EVENT_UOP_TYPE,
	 .event_masks = {
		{.name = "TAGLOADS",
		 .desc = "The uop is a load operation",
		 .bit = 1,
		},
		{.name = "TAGSTORES",
		 .desc = "The uop is a store operation",
		 .bit = 2,
		},
	 },
	},

	/* 41 */
	{.name = "branch_retired",
	 .desc = "Number of retirements of a branch",
	 .event_select = 0x6,
	 .escr_select = 0x5,
	 .allowed_escrs = { 21, 43 },
	 .perf_code = P4_EVENT_BRANCH_RETIRED,
	 .event_masks = {
		{.name = "MMNP",
		 .desc = "Branch not-taken predicted",
		 .bit = 0,
		},
		{.name = "MMNM",
		 .desc = "Branch not-taken mispredicted",
		 .bit = 1,
		},
		{.name = "MMTP",
		 .desc = "Branch taken predicted",
		 .bit = 2,
		},
		{.name = "MMTM",
		 .desc = "Branch taken mispredicted",
		 .bit = 3,
		},
	 },
	},

	/* 42 */
	{.name = "mispred_branch_retired",
	 .desc = "Number of retirements of mispredicted "
		 "IA-32 branch instructions",
	 .event_select = 0x3,
	 .escr_select = 0x4,
	 .allowed_escrs = { 20, 42 },
	 .perf_code = P4_EVENT_MISPRED_BRANCH_RETIRED,
	 .event_masks = {
		{.name = "BOGUS",
		 .desc = "The retired instruction is not bogus",
		 .bit = 0,
		 .flags = NETBURST_FL_DFL,
		},
	 },
	},

	/* 43 */
	{.name = "x87_assist",
	 .desc = "Number of retirements of x87 instructions that required "
		 "special handling",
	 .event_select = 0x3,
	 .escr_select = 0x5,
	 .allowed_escrs = { 21, 43 },
	 .perf_code = P4_EVENT_X87_ASSIST,
	 .event_masks = {
		{.name = "FPSU",
		 .desc = "Handle FP stack underflow",
		 .bit = 0,
		},
		{.name = "FPSO",
		 .desc = "Handle FP stack overflow",
		 .bit = 1,
		},
		{.name = "POAO",
		 .desc = "Handle x87 output overflow",
		 .bit = 2,
		},
		{.name = "POAU",
		 .desc = "Handle x87 output underflow",
		 .bit = 3,
		},
		{.name = "PREA",
		 .desc = "Handle x87 input assist",
		 .bit = 4,
		},
	 },
	},

	/* 44 */
	{.name = "machine_clear",
	 .desc = "Number of occurrences when the entire "
		 "pipeline of the machine is cleared",
	 .event_select = 0x2,
	 .escr_select = 0x5,
	 .allowed_escrs = { 21, 43 },
	 .perf_code = P4_EVENT_MACHINE_CLEAR,
	 .event_masks = {
		{.name = "CLEAR",
		 .desc = "Counts for a portion of the many cycles while the "
			 "machine is cleared for any cause. Use edge-"
			 "triggering for this bit only to get a count of "
			 "occurrences versus a duration",
		 .bit = 0,
		},
		{.name = "MOCLEAR",
		 .desc = "Increments each time the machine is cleared due to "
			 "memory ordering issues",
		 .bit = 2,
		},
		{.name = "SMCLEAR",
		 .desc = "Increments each time the machine is cleared due to "
			 "self-modifying code issues",
		 .bit = 6,
		},
	 },
	},

	/* 45 */
	{.name = "instr_completed",
	 .desc = "Instructions that have completed and "
		 "retired during a clock cycle (models 3, 4, 6 only)",
	 .event_select = 0x7,
	 .escr_select = 0x4,
	 .allowed_escrs = { 21, 42 },
	 .perf_code = P4_EVENT_INSTR_COMPLETED,
	 .event_masks = {
		{.name = "NBOGUS",
		 .desc = "Non-bogus instructions",
		 .bit = 0,
		},
		{.name = "BOGUS",
		 .desc = "Bogus instructions",
		 .bit = 1,
		},
	 },
	},
};
#define PME_REPLAY_EVENT    37
#define NETBURST_EVENT_COUNT (sizeof(netburst_events)/sizeof(netburst_entry_t))

#endif

