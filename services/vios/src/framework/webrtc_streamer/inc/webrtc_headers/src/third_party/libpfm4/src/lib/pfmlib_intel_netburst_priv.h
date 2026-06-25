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
 * pfmlib_netburst_priv.h
 *
 * Structures and definitions for use in the Pentium4/Xeon/EM64T libpfm code.
 */

#ifndef _PFMLIB_INTEL_NETBURST_PRIV_H_
#define _PFMLIB_INTEL_NETBURST_PRIV_H_

/* ESCR: Event Selection Control Register
 *
 * These registers are used to select which event to count along with options
 * for that event. There are (up to) 45 ESCRs, but each data counter is
 * restricted to a specific set of ESCRs.
 */

/**
 * netburst_escr_value_t
 *
 * Bit-wise breakdown of the ESCR registers.
 *
 *    Bits     Description
 *   -------   -----------
 *   63 - 31   Reserved
 *   30 - 25   Event Select
 *   24 - 9    Event Mask
 *    8 - 5    Tag Value
 *      4      Tag Enable
 *      3      T0 OS - Enable counting in kernel mode (thread 0)
 *      2      T0 USR - Enable counting in user mode (thread 0)
 *      1      T1 OS - Enable counting in kernel mode (thread 1)
 *      0      T1 USR - Enable counting in user mode (thread 1)
 **/

#define EVENT_MASK_BITS 16
#define EVENT_SELECT_BITS 6

typedef union {
	unsigned long long val;
	struct {
		unsigned long t1_usr:1;
		unsigned long t1_os:1;
		unsigned long t0_usr:1;
		unsigned long t0_os:1;
		unsigned long tag_enable:1;
		unsigned long tag_value:4;
		unsigned long event_mask:EVENT_MASK_BITS;
		unsigned long event_select:EVENT_SELECT_BITS;
		unsigned long reserved:1;
	} bits;
} netburst_escr_value_t;

/* CCCR: Counter Configuration Control Register
 *
 * These registers are used to configure the data counters. There are 18
 * CCCRs, one for each data counter.
 */

/**
 * netburst_cccr_value_t
 *
 * Bit-wise breakdown of the CCCR registers.
 *
 *    Bits     Description
 *   -------   -----------
 *   63 - 32   Reserved
 *     31      OVF - The data counter overflowed.
 *     30      Cascade - Enable cascading of data counter when alternate
 *             counter overflows.
 *   29 - 28   Reserved
 *     27      OVF_PMI_T1 - Generate interrupt for LP1 on counter overflow
 *     26      OVF_PMI_T0 - Generate interrupt for LP0 on counter overflow
 *     25      FORCE_OVF - Force interrupt on every counter increment
 *     24      Edge - Enable rising edge detection of the threshold comparison
 *             output for filtering event counts.
 *   23 - 20   Threshold Value - Select the threshold value for comparing to
 *             incoming event counts.
 *     19      Complement - Select how incoming event count is compared with
 *             the threshold value.
 *     18      Compare - Enable filtering of event counts.
 *   17 - 16   Active Thread - Only used with HT enabled.
 *             00 - None: Count when neither LP is active.
 *             01 - Single: Count when only one LP is active.
 *             10 - Both: Count when both LPs are active.
 *             11 - Any: Count when either LP is active.
 *   15 - 13   ESCR Select - Select which ESCR to use for selecting the
 *             event to count.
 *     12      Enable - Turns the data counter on or off.
 *   11 - 0    Reserved
 **/
typedef union {
	unsigned long long val;
	struct {
		unsigned long reserved1:12;
		unsigned long enable:1;
		unsigned long escr_select:3;
		unsigned long active_thread:2;
		unsigned long compare:1;
		unsigned long complement:1;
		unsigned long threshold:4;
		unsigned long edge:1;
		unsigned long force_ovf:1;
		unsigned long ovf_pmi_t0:1;
		unsigned long ovf_pmi_t1:1;
		unsigned long reserved2:2;
		unsigned long cascade:1;
		unsigned long overflow:1;
	} bits;
} netburst_cccr_value_t;

/**
 * netburst_event_mask_t
 *
 * Defines one bit of the event-mask for one Pentium4 event.
 *
 * @name: Event mask name
 * @desc: Event mask description
 * @bit: The bit position within the event_mask field.
 **/
typedef struct {
	const char *name;
	const char *desc;
	unsigned int bit;
	unsigned int flags;
} netburst_event_mask_t;
/*
 * netburst_event_mask_t->flags
 */
#define NETBURST_FL_DFL	0x1 /* event mask is default */

#define MAX_ESCRS_PER_EVENT 2

/*
 * These are the unique event codes used by perf_events.
 * The need to be encoded in the ESCR.event_select field when
 * programming for perf_events
 */
enum netburst_events {
	P4_EVENT_TC_DELIVER_MODE,
	P4_EVENT_BPU_FETCH_REQUEST,
	P4_EVENT_ITLB_REFERENCE,
	P4_EVENT_MEMORY_CANCEL,
	P4_EVENT_MEMORY_COMPLETE,
	P4_EVENT_LOAD_PORT_REPLAY,
	P4_EVENT_STORE_PORT_REPLAY,
	P4_EVENT_MOB_LOAD_REPLAY,
	P4_EVENT_PAGE_WALK_TYPE,
	P4_EVENT_BSQ_CACHE_REFERENCE,
	P4_EVENT_IOQ_ALLOCATION,
	P4_EVENT_IOQ_ACTIVE_ENTRIES,
	P4_EVENT_FSB_DATA_ACTIVITY,
	P4_EVENT_BSQ_ALLOCATION,
	P4_EVENT_BSQ_ACTIVE_ENTRIES,
	P4_EVENT_SSE_INPUT_ASSIST,
	P4_EVENT_PACKED_SP_UOP,
	P4_EVENT_PACKED_DP_UOP,
	P4_EVENT_SCALAR_SP_UOP,
	P4_EVENT_SCALAR_DP_UOP,
	P4_EVENT_64BIT_MMX_UOP,
	P4_EVENT_128BIT_MMX_UOP,
	P4_EVENT_X87_FP_UOP,
	P4_EVENT_TC_MISC,
	P4_EVENT_GLOBAL_POWER_EVENTS,
	P4_EVENT_TC_MS_XFER,
	P4_EVENT_UOP_QUEUE_WRITES,
	P4_EVENT_RETIRED_MISPRED_BRANCH_TYPE,
	P4_EVENT_RETIRED_BRANCH_TYPE,
	P4_EVENT_RESOURCE_STALL,
	P4_EVENT_WC_BUFFER,
	P4_EVENT_B2B_CYCLES,
	P4_EVENT_BNR,
	P4_EVENT_SNOOP,
	P4_EVENT_RESPONSE,
	P4_EVENT_FRONT_END_EVENT,
	P4_EVENT_EXECUTION_EVENT,
	P4_EVENT_REPLAY_EVENT,
	P4_EVENT_INSTR_RETIRED,
	P4_EVENT_UOPS_RETIRED,
	P4_EVENT_UOP_TYPE,
	P4_EVENT_BRANCH_RETIRED,
	P4_EVENT_MISPRED_BRANCH_RETIRED,
	P4_EVENT_X87_ASSIST,
	P4_EVENT_MACHINE_CLEAR,
	P4_EVENT_INSTR_COMPLETED,
};

typedef struct {
	const char *name;
	const char *desc;
	unsigned int event_select;
	unsigned int escr_select;
	enum netburst_events perf_code;	/* perf_event event code, enum P4_EVENTS */
	int allowed_escrs[MAX_ESCRS_PER_EVENT];
	netburst_event_mask_t event_masks[EVENT_MASK_BITS];
} netburst_entry_t;

#define NETBURST_ATTR_U	0
#define NETBURST_ATTR_K	1
#define NETBURST_ATTR_C	2
#define NETBURST_ATTR_E	3
#define NETBURST_ATTR_T	4

#define _NETBURST_ATTR_U (1 << NETBURST_ATTR_U)
#define _NETBURST_ATTR_K (1 << NETBURST_ATTR_K)

#define P4_REPLAY_REAL_MASK 0x00000003

extern int pfm_netburst_get_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_netburst_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
extern void pfm_netburst_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);

#endif
