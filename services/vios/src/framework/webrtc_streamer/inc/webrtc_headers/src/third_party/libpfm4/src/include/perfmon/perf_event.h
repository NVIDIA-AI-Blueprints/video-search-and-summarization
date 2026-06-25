/*
 * Copyright (c) 2011 Google, Inc
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
 */
#ifndef __PERFMON_PERF_EVENT_H__
#define __PERFMON_PERF_EVENT_H__

#pragma GCC visibility push(default)

#include <sys/types.h>
#include <unistd.h>		/* for syscall numbers */
#include <inttypes.h>
#include <sys/syscall.h>	/* for syscall stub macros */
#include <sys/ioctl.h>		/* for _IO */
#include <sys/prctl.h>		/* for prctl() comamnds */

#ifdef __cplusplus
extern "C" {
#endif
/*
 * avoid clashes with actual kernel header file
 */
#if !(defined(_LINUX_PERF_EVENT_H) || defined(_UAPI_LINUX_PERF_EVENT_H))

/*
 * attr->type field values
 */
enum perf_type_id {
	PERF_TYPE_HARDWARE	= 0,
	PERF_TYPE_SOFTWARE	= 1,
	PERF_TYPE_TRACEPOINT	= 2,
	PERF_TYPE_HW_CACHE	= 3,
	PERF_TYPE_RAW		= 4,
	PERF_TYPE_BREAKPOINT	= 5,
	PERF_TYPE_MAX
};

/*
 * attr->config values for generic HW PMU events
 *
 * they get mapped onto actual events by the kernel
 */
enum perf_hw_id {
	PERF_COUNT_HW_CPU_CYCLES		= 0,
	PERF_COUNT_HW_INSTRUCTIONS		= 1,
	PERF_COUNT_HW_CACHE_REFERENCES		= 2,
	PERF_COUNT_HW_CACHE_MISSES		= 3,
	PERF_COUNT_HW_BRANCH_INSTRUCTIONS	= 4,
	PERF_COUNT_HW_BRANCH_MISSES		= 5,
	PERF_COUNT_HW_BUS_CYCLES		= 6,
	PERF_COUNT_HW_STALLED_CYCLES_FRONTEND	= 7,
	PERF_COUNT_HW_STALLED_CYCLES_BACKEND	= 8,
	PERF_COUNT_HW_REF_CPU_CYCLES		= 9,
	PERF_COUNT_HW_MAX
};

/*
 * attr->config values for generic HW cache events
 *
 * they get mapped onto actual events by the kernel
 */
enum perf_hw_cache_id {
	PERF_COUNT_HW_CACHE_L1D		= 0,
	PERF_COUNT_HW_CACHE_L1I		= 1,
	PERF_COUNT_HW_CACHE_LL		= 2,
	PERF_COUNT_HW_CACHE_DTLB	= 3,
	PERF_COUNT_HW_CACHE_ITLB	= 4,
	PERF_COUNT_HW_CACHE_BPU		= 5,
	PERF_COUNT_HW_CACHE_NODE	= 6,
	PERF_COUNT_HW_CACHE_MAX
};

enum perf_hw_cache_op_id {
	PERF_COUNT_HW_CACHE_OP_READ		= 0,
	PERF_COUNT_HW_CACHE_OP_WRITE		= 1,
	PERF_COUNT_HW_CACHE_OP_PREFETCH		= 2,
	PERF_COUNT_HW_CACHE_OP_MAX
};

enum perf_hw_cache_op_result_id {
	PERF_COUNT_HW_CACHE_RESULT_ACCESS	= 0,
	PERF_COUNT_HW_CACHE_RESULT_MISS		= 1,
	PERF_COUNT_HW_CACHE_RESULT_MAX
};

/*
 * attr->config values for SW events
 */
enum perf_sw_ids {
	PERF_COUNT_SW_CPU_CLOCK			= 0,
	PERF_COUNT_SW_TASK_CLOCK		= 1,
	PERF_COUNT_SW_PAGE_FAULTS		= 2,
	PERF_COUNT_SW_CONTEXT_SWITCHES		= 3,
	PERF_COUNT_SW_CPU_MIGRATIONS		= 4,
	PERF_COUNT_SW_PAGE_FAULTS_MIN		= 5,
	PERF_COUNT_SW_PAGE_FAULTS_MAJ		= 6,
	PERF_COUNT_SW_ALIGNMENT_FAULTS		= 7,
	PERF_COUNT_SW_EMULATION_FAULTS		= 8,
	PERF_COUNT_SW_DUMMY			= 9,
	PERF_COUNT_SW_BPF_OUTPUT		= 10,
	PERF_COUNT_SW_CGROUP_SWITCHES           = 11,
	PERF_COUNT_SW_MAX
};

/*
 * attr->sample_type values
 */
enum perf_event_sample_format {
	PERF_SAMPLE_IP			= 1U << 0,
	PERF_SAMPLE_TID			= 1U << 1,
	PERF_SAMPLE_TIME		= 1U << 2,
	PERF_SAMPLE_ADDR		= 1U << 3,
	PERF_SAMPLE_READ		= 1U << 4,
	PERF_SAMPLE_CALLCHAIN		= 1U << 5,
	PERF_SAMPLE_ID			= 1U << 6,
	PERF_SAMPLE_CPU			= 1U << 7,
	PERF_SAMPLE_PERIOD		= 1U << 8,
	PERF_SAMPLE_STREAM_ID		= 1U << 9,
	PERF_SAMPLE_RAW			= 1U << 10,
	PERF_SAMPLE_BRANCH_STACK	= 1U << 11,
	PERF_SAMPLE_REGS_USER		= 1U << 12,
	PERF_SAMPLE_STACK_USER		= 1U << 13,
	PERF_SAMPLE_WEIGHT		= 1U << 14,
	PERF_SAMPLE_DATA_SRC		= 1U << 15,
	PERF_SAMPLE_IDENTIFIER		= 1U << 16,
	PERF_SAMPLE_TRANSACTION		= 1U << 17,
	PERF_SAMPLE_REGS_INTR		= 1U << 18,
	PERF_SAMPLE_PHYS_ADDR		= 1U << 19,
	PERF_SAMPLE_AUX			= 1U << 20,
	PERF_SAMPLE_CGROUP		= 1U << 21,
	PERF_SAMPLE_DATA_PAGE_SIZE	= 1U << 22,
	PERF_SAMPLE_CODE_PAGE_SIZE	= 1U << 23,
	PERF_SAMPLE_WEIGHT_STRUCT	= 1U << 24,
	PERF_SAMPLE_MAX			= 1U << 25,
};

enum perf_txn_qualifier {
	PERF_TXN_ELISION        = (1 << 0),
	PERF_TXN_TRANSACTION    = (1 << 1),
	PERF_TXN_SYNC           = (1 << 2),
	PERF_TXN_ASYNC          = (1 << 3),
	PERF_TXN_RETRY          = (1 << 4),
	PERF_TXN_CONFLICT       = (1 << 5),
	PERF_TXN_CAPACITY_WRITE = (1 << 6),
	PERF_TXN_CAPACITY_READ  = (1 << 7),
	PERF_TXN_MAX	        = (1 << 8),
	PERF_TXN_ABORT_MASK     = (0xffffffffULL << 32),
	PERF_TXN_ABORT_SHIFT    = 32,
};

/*
 * branch_sample_type values
 */
enum perf_branch_sample_type_shift {
	PERF_SAMPLE_BRANCH_USER_SHIFT		= 0,
	PERF_SAMPLE_BRANCH_KERNEL_SHIFT		= 1,
	PERF_SAMPLE_BRANCH_HV_SHIFT		= 2,
	PERF_SAMPLE_BRANCH_ANY_SHIFT		= 3,
	PERF_SAMPLE_BRANCH_ANY_CALL_SHIFT	= 4,
	PERF_SAMPLE_BRANCH_ANY_RETURN_SHIFT	= 5,
	PERF_SAMPLE_BRANCH_IND_CALL_SHIFT	= 6,
	PERF_SAMPLE_BRANCH_ABORT_TX_SHIFT	= 7,
	PERF_SAMPLE_BRANCH_IN_TX_SHIFT		= 8,
	PERF_SAMPLE_BRANCH_NO_TX_SHIFT		= 9,
	PERF_SAMPLE_BRANCH_COND_SHIFT		= 10,
	PERF_SAMPLE_BRANCH_CALL_STACK_SHIFT	= 11,
	PERF_SAMPLE_BRANCH_IND_JUMP_SHIFT	= 12,
	PERF_SAMPLE_BRANCH_CALL_SHIFT		= 13,
	PERF_SAMPLE_BRANCH_NO_FLAGS_SHIFT	= 14,
	PERF_SAMPLE_BRANCH_NO_CYCLES_SHIFT	= 15,
	PERF_SAMPLE_BRANCH_TYPE_SAVE_SHIFT	= 16,
	PERF_SAMPLE_BRANCH_HW_INDEX_SHIFT	= 17,
	PERF_SAMPLE_BRANCH_PRIV_SAVE_SHIFT	= 18,
	PERF_SAMPLE_BRANCH_COUNTERS_SHIFT	= 19,

	PERF_SAMPLE_BRANCH_MAX_SHIFT		/* non-ABI */
};

enum perf_branch_sample_type {
	PERF_SAMPLE_BRANCH_USER		= 1U << PERF_SAMPLE_BRANCH_USER_SHIFT,
	PERF_SAMPLE_BRANCH_KERNEL	= 1U << PERF_SAMPLE_BRANCH_KERNEL_SHIFT,
	PERF_SAMPLE_BRANCH_HV		= 1U << PERF_SAMPLE_BRANCH_HV_SHIFT,

	PERF_SAMPLE_BRANCH_ANY		= 1U << PERF_SAMPLE_BRANCH_ANY_SHIFT,
	PERF_SAMPLE_BRANCH_ANY_CALL	= 1U << PERF_SAMPLE_BRANCH_ANY_CALL_SHIFT,
	PERF_SAMPLE_BRANCH_ANY_RETURN	= 1U << PERF_SAMPLE_BRANCH_ANY_RETURN_SHIFT,
	PERF_SAMPLE_BRANCH_IND_CALL	= 1U << PERF_SAMPLE_BRANCH_IND_CALL_SHIFT,
	PERF_SAMPLE_BRANCH_ABORT_TX	= 1U << PERF_SAMPLE_BRANCH_ABORT_TX_SHIFT,
	PERF_SAMPLE_BRANCH_IN_TX	= 1U << PERF_SAMPLE_BRANCH_IN_TX_SHIFT,
	PERF_SAMPLE_BRANCH_NO_TX	= 1U << PERF_SAMPLE_BRANCH_NO_TX_SHIFT,
	PERF_SAMPLE_BRANCH_COND		= 1U << PERF_SAMPLE_BRANCH_COND_SHIFT,

	PERF_SAMPLE_BRANCH_CALL_STACK	= 1U << PERF_SAMPLE_BRANCH_CALL_STACK_SHIFT,
	PERF_SAMPLE_BRANCH_IND_JUMP	= 1U << PERF_SAMPLE_BRANCH_IND_JUMP_SHIFT,
	PERF_SAMPLE_BRANCH_CALL		= 1U << PERF_SAMPLE_BRANCH_IND_CALL_SHIFT,
	PERF_SAMPLE_BRANCH_NO_FLAGS	= 1U << PERF_SAMPLE_BRANCH_NO_FLAGS_SHIFT,
	PERF_SAMPLE_BRANCH_NO_CYCLES	= 1U << PERF_SAMPLE_BRANCH_NO_CYCLES_SHIFT,
	PERF_SAMPLE_BRANCH_TYPE_SAVE	= 1U << PERF_SAMPLE_BRANCH_TYPE_SAVE_SHIFT,
	PERF_SAMPLE_BRANCH_HW_INDEX	= 1U << PERF_SAMPLE_BRANCH_HW_INDEX_SHIFT,
	PERF_SAMPLE_BRANCH_PRIV_SAVE	= 1U << PERF_SAMPLE_BRANCH_PRIV_SAVE_SHIFT,
	PERF_SAMPLE_BRANCH_COUNTERS	= 1U << PERF_SAMPLE_BRANCH_COUNTERS_SHIFT,

	PERF_SAMPLE_BRANCH_MAX		= 1U << PERF_SAMPLE_BRANCH_MAX_SHIFT,
};

enum perf_branch_type {
	PERF_BR_UNKNOWN		= 0,
	PERF_BR_COND		= 1,
	PERF_BR_UNCOND		= 2,
	PERF_BR_IND		= 3,
	PERF_BR_CALL		= 4,
	PERF_BR_IND_CALL	= 5,
	PERF_BR_RET		= 6,
	PERF_BR_SYSCALL		= 7,
	PERF_BR_SYSRET		= 8,
	PERF_BR_COND_CALL	= 9,
	PERF_BR_COND_RET	= 10,
	PERF_BR_ERET		= 11,
	PERF_BR_IRQ		= 12,
	PERF_BR_SERROR		= 13,
	PERF_BR_NO_TX		= 14,
	PERF_BR_EXTEND_ABI	= 15,
	PERF_BR_MAX,
};

enum perf_branch_spec {
	PERF_BR_SPEC_NA			= 0,
	PERF_BR_SPEC_WRONG_PATH		= 1,
	PERF_BR_NON_SPEC_CORRECT_PATH	= 2,
	PERF_BR_SPEC_CORRECT_PATH	= 3,
	PERF_BR_SPEC_MAX,
};

enum perf_branch_fault {
	PERF_BR_NEW_FAULT_ALGN	= 0,
	PERF_BR_NEW_FAULT_DATA	= 1,
	PERF_BR_NEW_FAULT_INST	= 2,
	PERF_BR_NEW_ARCH_1	= 3,
	PERF_BR_NEW_ARCH_2	= 4,
	PERF_BR_NEW_ARCH_3	= 5,
	PERF_BR_NEW_ARCH_4	= 6,
	PERF_BR_NEW_ARCH_5	= 7,
	PERF_BR_NEW_MAX,
};

enum perf_branch_priv {
	PERF_BR_PRIV_UNKNOWN	= 0,
	PERF_BR_PRIV_USER	= 1,
	PERF_BR_PRIV_KERNEL	= 2,
	PERF_BR_PRIV_HV		= 3,
};


enum perf_sample_regs_abi {
	PERF_SAMPLE_REGS_ABI_NONE	= 0,
	PERF_SAMPLE_REGS_ABI_32		= 1,
	PERF_SAMPLE_REGS_ABI_64		= 2,
};

/*
 * attr->read_format values
 */
enum perf_event_read_format {
	PERF_FORMAT_TOTAL_TIME_ENABLED	= 1U << 0,
	PERF_FORMAT_TOTAL_TIME_RUNNING	= 1U << 1,
	PERF_FORMAT_ID			= 1U << 2,
	PERF_FORMAT_GROUP		= 1U << 3,
	PERF_FORMAT_LOST		= 1U << 4,
	PERF_FORMAT_MAX			= 1U << 5,
};

#define PERF_ATTR_SIZE_VER0	64	/* sizeof first published struct */
#define PERF_ATTR_SIZE_VER1	72	/* add: config2 */
#define PERF_ATTR_SIZE_VER2	80	/* add: branch_sample_type */
#define PERF_ATTR_SIZE_VER3	96	/* add: sample_regs_user */
					/* add: sample_stack_user */
#define PERF_ATTR_SIZE_VER4	104	/* add: sample_regs_intr */
#define PERF_ATTR_SIZE_VER5	112	/* add: aux_watermark */
#define PERF_ATTR_SIZE_VER6	120	/* add: aux_sample_size */
#define PERF_ATTR_SIZE_VER7	128	/* add: sig_data */
#define PERF_ATTR_SIZE_VER8	136	/* add: config3 */


/*
 * SWIG doesn't deal well with anonymous nested structures
 * so we add names for the nested structure only when swig
 * is used.
 */
#ifdef SWIG
#define SWIG_NAME(x) x
#else
#define SWIG_NAME(x)
#endif /* SWIG */

/*
 * perf_event_attr struct passed to perf_event_open()
 */
typedef struct perf_event_attr {
	uint32_t	type;
	uint32_t	size;
	uint64_t	config;

	union {
		uint64_t	sample_period;
		uint64_t	sample_freq;
	} SWIG_NAME(sample);

	uint64_t	sample_type;
	uint64_t	read_format;

	uint64_t	disabled       :  1,
			inherit	       :  1,
			pinned	       :  1,
			exclusive      :  1,
			exclude_user   :  1,
			exclude_kernel :  1,
			exclude_hv     :  1,
			exclude_idle   :  1,
			mmap           :  1,
			comm	       :  1,
			freq           :  1,
			inherit_stat   :  1,
			enable_on_exec :  1,
			task           :  1,
			watermark      :  1,
			precise_ip     :  2,
			mmap_data      :  1,
			sample_id_all  :  1,
			exclude_host   :  1,
			exclude_guest  :  1,
			exclude_callchain_kernel : 1,
			exclude_callchain_user   : 1,
			mmap2          :  1,
			comm_exec      :  1,
			use_clockid    :  1,
			context_switch :  1,
			write_backward :  1,
			namespaces     :  1,
			ksymbol        :  1,
			bpf_event      :  1,
			aux_output     :  1,
			cgroup         :  1,
			text_poke      :  1,
			build_id       :  1,
			inherit_thread :  1,
			remove_on_exec :  1,
			sigtrap        :  1,
			__reserved_1   : 26;

	union {
		uint32_t	wakeup_events;
		uint32_t	wakeup_watermark;
	} SWIG_NAME(wakeup);

	uint32_t        bp_type;
	union {
		uint64_t        bp_addr;
		uint64_t        kprobe_func;
		uint64_t        uprobe_path;
		uint64_t	config1; /* extend config */
	} SWIG_NAME(bpa);
	union {
		uint64_t        bp_len;
		uint64_t        kprobe_addr;
		uint64_t        probe_offset;
		uint64_t	config2; /* extend config1 */
	} SWIG_NAME(bpb);
	uint64_t branch_sample_type;
	uint64_t sample_regs_user;
	uint32_t sample_stack_user;
	int32_t  clockid;
	uint64_t sample_regs_intr;
	uint32_t aux_watermark;
	uint16_t sample_max_stack;
	uint16_t __reserved_2;
	uint32_t aux_sample_size;
	uint32_t __reserved_3;
	uint64_t sig_data;
	uint64_t config3;
} perf_event_attr_t;

struct perf_branch_entry {
	uint64_t	from;
	uint64_t	to;
	uint64_t	mispred:1,  /* target mispredicted */
			predicted:1,/* target predicted */
			in_tx:1,    /* in transaction */
			abort:1,    /* transaction abort */
			cycles:16,  /* cycle count to last branch */
			type:4,     /* branch type */
			spec:2,     /* branch speculation info */
			new_type:4, /* additional branch type */
			priv:3,     /* privilege level */
			reserved:31;
};

/*
 * branch stack layout:
 *  nr: number of taken branches stored in entries[]
 *
 * Note that nr can vary from sample to sample
 * branches (to, from) are stored from most recent
 * to least recent, i.e., entries[0] contains the most
 * recent branch.
 */
struct perf_branch_stack {
	uint64_t			nr;
	struct perf_branch_entry        entries[0];
};

/*
 * Structure used by below PERF_EVENT_IOC_QUERY_BPF command
 * to query bpf programs attached to the same perf tracepoint
 * as the given perf event.
 */
struct perf_event_query_bpf {
	uint32_t	ids_len;
	uint32_t	prog_cnt;
	uint32_t	ids[0];
};

union perf_sample_weight {
	uint64_t full;
#if __BYTE_ORDER == __LITTLE_ENDIAN
	struct {
		uint32_t var1_dw;
		uint16_t var2_w;
		uint16_t var3_w;
	};
#elif __BYTE_ORDER == __BIG_ENDIAN
	struct {
		uint16_t var3_w;
		uint16_t var2_w;
		uint32_t var1_dw;
	};
#else
#error "Unsupported endianness"
#endif
};

/*
 * perf_events ioctl commands, use with event fd
 */
#define PERF_EVENT_IOC_ENABLE			_IO ('$', 0)
#define PERF_EVENT_IOC_DISABLE			_IO ('$', 1)
#define PERF_EVENT_IOC_REFRESH			_IO ('$', 2)
#define PERF_EVENT_IOC_RESET			_IO ('$', 3)
#define PERF_EVENT_IOC_PERIOD			_IOW('$', 4, uint64_t)
#define PERF_EVENT_IOC_SET_OUTPUT		_IO ('$', 5)
#define PERF_EVENT_IOC_SET_FILTER		_IOW('$', 6, char *)
#define PERF_EVENT_IOC_ID			_IOR('$', 7, uint64_t *)
#define PERF_EVENT_IOC_SET_BPF			_IOW('$', 8, uint32_t)
#define PERF_EVENT_IOC_PAUSE_OUTPUT		_IOW('$', 9, __u32)
#define PERF_EVENT_IOC_QUERY_BPF		_IOWR('$', 10, struct perf_event_query_bpf *)
#define PERF_EVENT_IOC_MODIFY_ATTRIBUTES	_IOW('$', 11, struct perf_event_attr *)

/*
 * ioctl() 3rd argument
 */
enum perf_event_ioc_flags {
	PERF_IOC_FLAG_GROUP	= 1U << 0,
};

/*
 * mmapped sampling buffer layout
 * occupies a 4kb page
 */
struct perf_event_mmap_page {
	uint32_t	version;
	uint32_t	compat_version;
	uint32_t	lock;
	uint32_t	index;
	int64_t		offset;
	uint64_t	time_enabled;
	uint64_t	time_running;
	union {
		uint64_t capabilities;
		struct {
			uint64_t cap_bit0:1,
				 cap_bit0_is_deprecated:1,
				 cap_usr_rdpmc:1,
				 cap_user_time:1,
				 cap_user_time_zero:1,
				 cap_user_time_short:1,
				 cap_____res:58;
		} SWIG_NAME(rdmap_cap_s);
	} SWIG_NAME(rdmap_cap_u);
	uint16_t	pmc_width;
	uint16_t	time_shift;
	uint32_t	time_mult;
	uint64_t	time_offset;

	uint64_t	time_zero;
	uint32_t	size;
	uint32_t	__reserved_1;
	uint64_t	time_cycles;
	uint64_t	time_mask;
	uint8_t		__reserved[116*8];

	uint64_t  	data_head;
	uint64_t	data_tail;
	uint64_t  	data_offset;
	uint64_t  	data_size;
	uint64_t	aux_head;
	uint64_t	aux_tail;
	uint64_t	aux_offset;
	uint64_t	aux_size;
};

/*
 * sampling buffer event header
 */
struct perf_event_header {
	uint32_t	type;
	uint16_t	misc;
	uint16_t	size;
};

/*
 * event header misc field values
 */
#define PERF_EVENT_MISC_CPUMODE_MASK	(3 << 0)
#define PERF_EVENT_MISC_CPUMODE_UNKNOWN	(0 << 0)
#define PERF_EVENT_MISC_KERNEL		(1 << 0)
#define PERF_EVENT_MISC_USER		(2 << 0)
#define PERF_EVENT_MISC_HYPERVISOR	(3 << 0)
#define PERF_RECORD_MISC_GUEST_KERNEL	(4 << 0)
#define PERF_RECORD_MISC_GUEST_USER	(5 << 0)

#define PERF_RECORD_MISC_PROC_MAP_PARSE_TIMEOUT	(1 << 12)
#define PERF_RECORD_MISC_MMAP_DATA		(1 << 13)
#define PERF_RECORD_MISC_COMM_EXEC		(1 << 13)
#define PERF_RECORD_MISC_FORK_EXEC		(1 << 13)
#define PERF_RECORD_MISC_SWITCH_OUT		(1 << 13)
#define PERF_RECORD_MISC_EXACT			(1 << 14)
#define PERF_RECORD_MISC_EXACT_IP               (1 << 14)
#define PERF_RECORD_MISC_SWITCH_OUT_PREEMPT	(1 << 14)
#define PERF_RECORD_MISC_MMAP_BUILD_ID		(1 << 14)
#define PERF_RECORD_MISC_EXT_RESERVED		(1 << 15)

/*
 * header->type values
 */
enum perf_event_type {
	PERF_RECORD_MMAP		= 1,
	PERF_RECORD_LOST		= 2,
	PERF_RECORD_COMM		= 3,
	PERF_RECORD_EXIT		= 4,
	PERF_RECORD_THROTTLE		= 5,
	PERF_RECORD_UNTHROTTLE		= 6,
	PERF_RECORD_FORK		= 7,
	PERF_RECORD_READ		= 8,
	PERF_RECORD_SAMPLE		= 9,
	PERF_RECORD_MMAP2		= 10,
	PERF_RECORD_AUX			= 11,
	PERF_RECORD_ITRACE_START	= 12,
	PERF_RECORD_LOST_SAMPLES	= 13,
	PERF_RECORD_SWITCH		= 14,
	PERF_RECORD_SWITCH_CPU_WIDE	= 15,
	PERF_RECORD_NAMESPACES		= 16,
	PERF_RECORD_KSYMBOL		= 17,
	PERF_RECORD_BPF_EVENT		= 18,
	PERF_RECORD_CGROUP		= 19,
	PERF_RECORD_TEXT_POKE		= 20,
	PERF_RECORD_AUX_OUTPUT_HW_ID	= 21,
	PERF_RECORD_MAX
};

struct perf_ns_link_info {
	uint64_t dev;
	uint64_t ino;
};

enum perf_ns_type {
	NET_NS_INDEX	= 0,
	UTS_NS_INDEX	= 1,
	IPC_NS_INDEX	= 2,
	PID_NS_INDEX	= 3,
	USER_NS_INDEX	= 4,
	MNT_NS_INDEX	= 5,
	CGROUP_NS_INDEX	= 6,
	NR_NAMESPACES
};

enum perf_record_ksymbol_type {
	PERF_RECORD_KSYMBOL_TYPE_UNKNOWN	= 0,
	PERF_RECORD_KSYMBOL_TYPE_BPF		= 1,
	/*
	 * Out of line code such as kprobe-replaced instructions or optimized
	 * kprobes or ftrace trampolines.
	 */
	PERF_RECORD_KSYMBOL_TYPE_OOL		= 2,
	PERF_RECORD_KSYMBOL_TYPE_MAX		/* non-ABI */
};

#define PERF_RECORD_KSYMBOL_FLAGS_UNREGISTER	(1 << 0)

enum perf_bpf_event_type {
	PERF_BPF_EVENT_UNKNOWN		= 0,
	PERF_BPF_EVENT_PROG_LOAD	= 1,
	PERF_BPF_EVENT_PROG_UNLOAD	= 2,
	PERF_BPF_EVENT_MAX,		/* non-ABI */
};

#define PERF_MAX_STACK_DEPTH		127
#define PERF_MAX_CONTEXTS_PER_STACK	  8


enum perf_callchain_context {
	PERF_CONTEXT_HV			= (uint64_t)-32,
	PERF_CONTEXT_KERNEL		= (uint64_t)-128,
	PERF_CONTEXT_USER		= (uint64_t)-512,

	PERF_CONTEXT_GUEST		= (uint64_t)-2048,
	PERF_CONTEXT_GUEST_KERNEL	= (uint64_t)-2176,
	PERF_CONTEXT_GUEST_USER		= (uint64_t)-2560,

	PERF_CONTEXT_MAX		= (uint64_t)-4095,
};

#define PERF_AUX_FLAG_TRUNCATED			0x01
#define PERF_AUX_FLAG_OVERWRITE			0x02
#define PERF_AUX_FLAG_PARTIAL			0x04
#define PERF_AUX_FLAG_COLLISION			0x08
#define PERF_AUX_FLAG_PMU_FORMAT_TYPE_MASK	0xff00

/* CoreSight PMU AUX buffer formats */
#define PERF_AUX_FLAG_CORESIGHT_FORMAT_CORESIGHT	0x0000
#define PERF_AUX_FLAG_CORESIGHT_FORMAT_RAW		0x0100


/*
 * flags for perf_event_open()
 */
#define PERF_FLAG_FD_NO_GROUP	(1U << 0)
#define PERF_FLAG_FD_OUTPUT	(1U << 1)
#define PERF_FLAG_PID_CGROUP	(1U << 2)
#define PERF_FLAG_FD_CLOEXEC		(1UL << 3)

#endif /* _LINUX_PERF_EVENT_H */

#ifndef __NR_perf_event_open
#ifdef __x86_64__
# define __NR_perf_event_open	298
#endif

#ifdef __i386__
# define __NR_perf_event_open 336
#endif

#ifdef __powerpc__
# define __NR_perf_event_open 319
#endif

#ifdef __s390__
# define __NR_perf_event_open 331
#endif

#ifdef __arm__
#if defined(__ARM_EABI__) || defined(__thumb__)
# define __NR_perf_event_open 364
#else
# define __NR_perf_event_open (0x900000+364)
#endif
#endif

#ifdef __mips__
#if _MIPS_SIM == _MIPS_SIM_ABI32
# define __NR_perf_event_open __NR_Linux + 333
#elif _MIPS_SIM == _MIPS_SIM_ABI64
# define __NR_perf_event_open __NR_Linux + 292
#else /* if _MIPS_SIM == MIPS_SIM_NABI32 */
# define __NR_perf_event_open __NR_Linux + 296
#endif
#endif
#endif /* __NR_perf_event_open */

/*
 * perf_event_open() syscall stub
 */
static inline int
perf_event_open(
	struct perf_event_attr		*hw_event_uptr,
	pid_t				pid,
	int				cpu,
	int				group_fd,
	unsigned long			flags)
{
	return syscall(
		__NR_perf_event_open, hw_event_uptr, pid, cpu, group_fd, flags);
}

/*
 * compensate for some distros which do not
 * have recent enough linux/prctl.h
 */
#ifndef PR_TASK_PERF_EVENTS_DISABLE
#define PR_TASK_PERF_EVENTS_ENABLE	32
#define PR_TASK_PERF_EVENTS_DISABLE	31
#endif

/* handle case of older system perf_event.h included before this file */
#ifndef PERF_MEM_OP_NA

union perf_mem_data_src {
	uint64_t val;
	struct {
		uint64_t   mem_op:5,		/* type of opcode */
			   mem_lvl:14,		/* memory hierarchy level */
			   mem_snoop:5,		/* snoop mode */
			   mem_lock:2,		/* lock instr */
			   mem_dtlb:7,		/* tlb access */
			   mem_lvl_num:4,	/* memory hierarchy level number */
			   mem_remote:1,	/* remote */
			   mem_snoopx:2,	/* snoop mode, ext */
			   mem_blk:3,		/* access blocked */
			   mem_hops:3,		/* hop level */
			   mem_rsvd:18;
	};
};

/* type of opcode (load/store/prefetch,code) */
#define PERF_MEM_OP_NA		0x01 /* not available */
#define PERF_MEM_OP_LOAD	0x02 /* load instruction */
#define PERF_MEM_OP_STORE	0x04 /* store instruction */
#define PERF_MEM_OP_PFETCH	0x08 /* prefetch */
#define PERF_MEM_OP_EXEC	0x10 /* code (execution) */
#define PERF_MEM_OP_SHIFT	0

/* memory hierarchy (memory level, hit or miss) */
#define PERF_MEM_LVL_NA		0x01  /* not available */
#define PERF_MEM_LVL_HIT	0x02  /* hit level */
#define PERF_MEM_LVL_MISS	0x04  /* miss level  */
#define PERF_MEM_LVL_L1		0x08  /* L1 */
#define PERF_MEM_LVL_LFB	0x10  /* Line Fill Buffer */
#define PERF_MEM_LVL_L2		0x20  /* L2 */
#define PERF_MEM_LVL_L3		0x40  /* L3 */
#define PERF_MEM_LVL_LOC_RAM	0x80  /* Local DRAM */
#define PERF_MEM_LVL_REM_RAM1	0x100 /* Remote DRAM (1 hop) */
#define PERF_MEM_LVL_REM_RAM2	0x200 /* Remote DRAM (2 hops) */
#define PERF_MEM_LVL_REM_CCE1	0x400 /* Remote Cache (1 hop) */
#define PERF_MEM_LVL_REM_CCE2	0x800 /* Remote Cache (2 hops) */
#define PERF_MEM_LVL_IO		0x1000 /* I/O memory */
#define PERF_MEM_LVL_UNC	0x2000 /* Uncached memory */
#define PERF_MEM_LVL_SHIFT	5

#define PERF_MEM_REMOTE_REMOTE	0x01  /* Remote */
#define PERF_MEM_REMOTE_SHIFT	37

/* snoop mode */
#define PERF_MEM_SNOOP_NA	0x01 /* not available */
#define PERF_MEM_SNOOP_NONE	0x02 /* no snoop */
#define PERF_MEM_SNOOP_HIT	0x04 /* snoop hit */
#define PERF_MEM_SNOOP_MISS	0x08 /* snoop miss */
#define PERF_MEM_SNOOP_HITM	0x10 /* snoop hit modified */
#define PERF_MEM_SNOOP_SHIFT	19

#define PERF_MEM_SNOOPX_FWD	0x01 /* forward */
/* 1 free */
#define PERF_MEM_SNOOPX_SHIFT	38

/* locked instruction */
#define PERF_MEM_LOCK_NA	0x01 /* not available */
#define PERF_MEM_LOCK_LOCKED	0x02 /* locked transaction */
#define PERF_MEM_LOCK_SHIFT	24

/* TLB access */
#define PERF_MEM_TLB_NA		0x01 /* not available */
#define PERF_MEM_TLB_HIT	0x02 /* hit level */
#define PERF_MEM_TLB_MISS	0x04 /* miss level */
#define PERF_MEM_TLB_L1		0x08 /* L1 */
#define PERF_MEM_TLB_L2		0x10 /* L2 */
#define PERF_MEM_TLB_WK		0x20 /* Hardware Walker*/
#define PERF_MEM_TLB_OS		0x40 /* OS fault handler */
#define PERF_MEM_TLB_SHIFT	26

/* Access blocked */
#define PERF_MEM_BLK_NA		0x01 /* not available */
#define PERF_MEM_BLK_DATA	0x02 /* data could not be forwarded */
#define PERF_MEM_BLK_ADDR	0x04 /* address conflict */
#define PERF_MEM_BLK_SHIFT	40

/* hop level */
#define PERF_MEM_HOPS_0		0x01 /* remote core, same node */
#define PERF_MEM_HOPS_1		0x02 /* remote node, same socket */
#define PERF_MEM_HOPS_2		0x03 /* remote socket, same board */
#define PERF_MEM_HOPS_3		0x04 /* remote board */
/* 5-7 available */
#define PERF_MEM_HOPS_SHIFT	43

#define PERF_MEM_S(a, s) \
	(((u64)PERF_MEM_##a##_##s) << PERF_MEM_##a##_SHIFT)

#endif /* PERF_MEM_OP_NA */

#ifdef __cplusplus /* extern C */
}
#endif

#pragma GCC visibility pop

#endif /* __PERFMON_PERF_EVENT_H__ */
