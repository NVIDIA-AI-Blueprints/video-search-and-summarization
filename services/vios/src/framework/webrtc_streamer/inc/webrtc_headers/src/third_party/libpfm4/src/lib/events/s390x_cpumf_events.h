#ifndef __S390X_CPUMF_EVENTS_H__
#define __S390X_CPUMF_EVENTS_H__


#define __stringify(x)		#x
#define STRINGIFY(x)		__stringify(x)

/* CPUMF counter sets */
#define CPUMF_CTRSET_NONE               0
#define CPUMF_CTRSET_BASIC              2
#define CPUMF_CTRSET_PROBLEM_STATE      4
#define CPUMF_CTRSET_CRYPTO             8
#define CPUMF_CTRSET_EXTENDED           1
#define CPUMF_CTRSET_MT_DIAG            32

#define CPUMF_SVN6_ECC                  4

static const pme_cpumf_ctr_t cpumcf_fvn1_counters[] = {
	{
		.ctrnum = 0,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "CPU_CYCLES",
		.desc = "Cycle Count",
	},
	{
		.ctrnum = 1,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "INSTRUCTIONS",
		.desc = "Instruction Count",
	},
	{
		.ctrnum = 2,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1I_DIR_WRITES",
		.desc = "Level-1 I-Cache Directory Write Count",
	},
	{
		.ctrnum = 3,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1I_PENALTY_CYCLES",
		.desc = "Level-1 I-Cache Penalty Cycle Count",
	},
	{
		.ctrnum = 4,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1D_DIR_WRITES",
		.desc = "Level-1 D-Cache Directory Write Count",
	},
	{
		.ctrnum = 5,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1D_PENALTY_CYCLES",
		.desc = "Level-1 D-Cache Penalty Cycle Count",
	},
	{
		.ctrnum = 32,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_CPU_CYCLES",
		.desc = "Problem-State Cycle Count",
	},
	{
		.ctrnum = 33,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_INSTRUCTIONS",
		.desc = "Problem-State Instruction Count",
	},
	{
		.ctrnum = 34,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_L1I_DIR_WRITES",
		.desc = "Problem-State Level-1 I-Cache Directory Write Count",
	},
	{
		.ctrnum = 35,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_L1I_PENALTY_CYCLES",
		.desc = "Problem-State Level-1 I-Cache Penalty Cycle Count",
	},
	{
		.ctrnum = 36,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_L1D_DIR_WRITES",
		.desc = "Problem-State Level-1 D-Cache Directory Write Count",
	},
	{
		.ctrnum = 37,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_L1D_PENALTY_CYCLES",
		.desc = "Problem-State Level-1 D-Cache Penalty Cycle Count",
	},
};

static const pme_cpumf_ctr_t cpumcf_fvn3_counters[] = {
	{
		.ctrnum = 0,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "CPU_CYCLES",
		.desc = "Cycle Count",
	},
	{
		.ctrnum = 1,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "INSTRUCTIONS",
		.desc = "Instruction Count",
	},
	{
		.ctrnum = 2,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1I_DIR_WRITES",
		.desc = "Level-1 I-Cache Directory Write Count",
	},
	{
		.ctrnum = 3,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1I_PENALTY_CYCLES",
		.desc = "Level-1 I-Cache Penalty Cycle Count",
	},
	{
		.ctrnum = 4,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1D_DIR_WRITES",
		.desc = "Level-1 D-Cache Directory Write Count",
	},
	{
		.ctrnum = 5,
		.ctrset = CPUMF_CTRSET_BASIC,
		.name = "L1D_PENALTY_CYCLES",
		.desc = "Level-1 D-Cache Penalty Cycle Count",
	},
	{
		.ctrnum = 32,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_CPU_CYCLES",
		.desc = "Problem-State Cycle Count",
	},
	{
		.ctrnum = 33,
		.ctrset = CPUMF_CTRSET_PROBLEM_STATE,
		.name = "PROBLEM_STATE_INSTRUCTIONS",
		.desc = "Problem-State Instruction Count",
	},
};

static const pme_cpumf_ctr_t cpumcf_svn_generic_counters[] = {
	{
		.ctrnum = 64,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "PRNG_FUNCTIONS",
		.desc = "Total number of the PRNG functions issued by the"
			" CPU",
	},
	{
		.ctrnum = 65,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "PRNG_CYCLES",
		.desc = "Total number of CPU cycles when the DEA/AES"
			" coprocessor is busy performing PRNG functions"
			" issued by the CPU",
	},
	{
		.ctrnum = 66,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "PRNG_BLOCKED_FUNCTIONS",
		.desc = "Total number of the PRNG functions that are issued"
			" by the CPU and are blocked because the DEA/AES"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 67,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "PRNG_BLOCKED_CYCLES",
		.desc = "Total number of CPU cycles blocked for the PRNG"
			" functions issued by the CPU because the DEA/AES"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 68,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "SHA_FUNCTIONS",
		.desc = "Total number of SHA functions issued by the CPU",
	},
	{
		.ctrnum = 69,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "SHA_CYCLES",
		.desc = "Total number of CPU cycles when the SHA coprocessor"
			" is busy performing the SHA functions issued by the"
			" CPU",
	},
	{
		.ctrnum = 70,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "SHA_BLOCKED_FUNCTIONS",
		.desc = "Total number of the SHA functions that are issued"
			" by the CPU and are blocked because the SHA"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 71,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "SHA_BLOCKED_CYCLES",
		.desc = "Total number of CPU cycles blocked for the SHA"
			" functions issued by the CPU because the SHA"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 72,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "DEA_FUNCTIONS",
		.desc = "Total number of the DEA functions issued by the CPU",
	},
	{
		.ctrnum = 73,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "DEA_CYCLES",
		.desc = "Total number of CPU cycles when the DEA/AES"
			" coprocessor is busy performing the DEA functions"
			" issued by the CPU",
	},
	{
		.ctrnum = 74,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "DEA_BLOCKED_FUNCTIONS",
		.desc = "Total number of the DEA functions that are issued"
			" by the CPU and are blocked because the DEA/AES"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 75,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "DEA_BLOCKED_CYCLES",
		.desc = "Total number of CPU cycles blocked for the DEA"
			" functions issued by the CPU because the DEA/AES"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 76,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "AES_FUNCTIONS",
		.desc = "Total number of AES functions issued by the CPU",
	},
	{
		.ctrnum = 77,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "AES_CYCLES",
		.desc = "Total number of CPU cycles when the DEA/AES"
			" coprocessor is busy performing the AES functions"
			" issued by the CPU",
	},
	{
		.ctrnum = 78,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "AES_BLOCKED_FUNCTIONS",
		.desc = "Total number of AES functions that are issued by"
			" the CPU and are blocked because the DEA/AES"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 79,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "AES_BLOCKED_CYCLES",
		.desc = "Total number of CPU cycles blocked for the AES"
			" functions issued by the CPU because the DEA/AES"
			" coprocessor is busy performing a function issued by"
			" another CPU",
	},
	{
		.ctrnum = 80,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "ECC_FUNCTION_COUNT",
		.desc = "This counter counts the total number of the"
			" elliptic-curve cryptography (ECC) functions issued"
			" by the CPU.",
	},
	{
		.ctrnum = 81,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "ECC_CYCLES_COUNT",
		.desc = "This counter counts the total number of CPU cycles"
			" when the ECC coprocessor is busy performing the"
			" elliptic-curve cryptography (ECC) functions issued"
			" by the CPU.",
	},
	{
		.ctrnum = 82,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "ECC_BLOCKED_FUNCTION_COUNT",
		.desc = "This counter counts the total number of the"
			" elliptic-curve cryptography (ECC) functions that"
			" are issued by the CPU and are blocked because the"
			" ECC coprocessor is busy performing a function"
			" issued by another CPU.",
	},
	{
		.ctrnum = 83,
		.ctrset = CPUMF_CTRSET_CRYPTO,
		.name = "ECC_BLOCKED_CYCLES_COUNT",
		.desc = "This counter counts the total number of CPU cycles"
			" blocked for the elliptic-curve cryptography (ECC)"
			" functions issued by the CPU because the ECC"
			" coprocessor is busy perform- ing a function issued"
			" by another CPU.",
	},
};

static const pme_cpumf_ctr_t cpumcf_z10_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L2_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from the"
			" Level-2 (L1.5) cache",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the installed cache line was sourced from the"
			" Level-2 (L1.5) cache",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L3_LOCAL_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the installed cache line was sourced from the"
			" Level-3 cache that is on the same book as the"
			" Instruction cache (Local L2 cache)",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L3_LOCAL_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the installtion cache line was source from"
			" the Level-3 cache that is on the same book as the"
			" Data cache (Local L2 cache)",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L3_REMOTE_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the installed cache line was sourced from a"
			" Level-3 cache that is not on the same book as the"
			" Instruction cache (Remote L2 cache)",
	},
	{
		.ctrnum = 133,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L3_REMOTE_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the installed cache line was sourced from a"
			" Level-3 cache that is not on the same book as the"
			" Data cache (Remote L2 cache)",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_LMEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the installed cache line was sourced from"
			" memory that is attached to the same book as the"
			" Data cache (Local Memory)",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_LMEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache where the"
			" installed cache line was sourced from memory that"
			" is attached to the s ame book as the Instruction"
			" cache (Local Memory)",
	},
	{
		.ctrnum = 136,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 D-Cache where the"
			" line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_CACHELINE_INVALIDATES",
		.desc = "A cache line in the Level-1 I-Cache has been"
			" invalidated by a store on the same CPU as the Level-"
			"1 I-Cache",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_WRITES",
		.desc = "A translation entry has been written into the Level-"
			"1 Instruction Translation Lookaside Buffer",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Page Table Entry arrays",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Common Region Segment Table Entry arrays",
	},
	{
		.ctrnum = 142,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Common Region Segment Table Entry arrays for a"
			" one-megabyte large page translation",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_MISSES",
		.desc = "Level-1 Instruction TLB miss in progress."
			" Incremented by one for every cycle an ITLB1 miss is"
			" in progress",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_MISSES",
		.desc = "Level-1 Data TLB miss in progress. Incremented by"
			" one for every cycle an DTLB1 miss is in progress",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L2C_STORES_SENT",
		.desc = "Incremented by one for every store sent to Level-2"
			" (L1.5) cache",
	},
};

static const pme_cpumf_ctr_t cpumcf_z196_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the returned cache line was sourced from the"
			" Level-2 cache",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L2_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from the"
			" Level-2 cache",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_MISSES",
		.desc = "Level-1 Data TLB miss in progress. Incremented by"
			" one for every cycle a DTLB1 miss is in progress.",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_MISSES",
		.desc = "Level-1 Instruction TLB miss in progress."
			" Incremented by one for every cycle a ITLB1 miss is"
			" in progress.",
	},
	{
		.ctrnum = 133,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L2C_STORES_SENT",
		.desc = "Incremented by one for every store sent to Level-2"
			" cache",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFBOOK_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the returned cache line was sourced from an"
			" Off Book Level-3 cache",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the returned cache line was sourced from an"
			" On Book Level-4 cache",
	},
	{
		.ctrnum = 136,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from an"
			" On Book Level-4 cache",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 D-Cache where the"
			" line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the returned cache line was sourced from an"
			" Off Book Level-4 cache",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from an"
			" Off Book Level-4 cache",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer for a one-"
			"megabyte page",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_LMEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache where the"
			" installed cache line was sourced from memory that"
			" is attached to the same book as the Data cache"
			" (Local Memory)",
	},
	{
		.ctrnum = 142,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_LMEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache where the"
			" installed cache line was sourced from memory that"
			" is attached to the same book as the Instruction"
			" cache (Local Memory)",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFBOOK_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from an"
			" Off Book Level-3 cache",
	},
	{
		.ctrnum = 144,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Instruction Translation Lookaside Buffer",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Page Table Entry arrays",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Common Region Segment Table Entry arrays for a"
			" one-megabyte large page translation",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Common Region Segment Table Entry arrays",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the returned cache line was sourced from an"
			" On Chip Level-3 cache",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 D-Cache directory"
			" where the returned cache line was sourced from an"
			" Off Chip/On Book Level-3 cache",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from an"
			" On Chip Level-3 cache",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 I-Cache directory"
			" where the returned cache line was sourced from an"
			" Off Chip/On Book Level-3 cache",
	},
};

static const pme_cpumf_ctr_t cpumcf_zec12_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_MISSES",
		.desc = "Level-1 Data TLB miss in progress. Incremented by"
			" one for every cycle a DTLB1 miss is in progress.",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_MISSES",
		.desc = "Level-1 Instruction TLB miss in progress."
			" Incremented by one for every cycle a ITLB1 miss is"
			" in progress.",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2I_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Instruction cache",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L2I_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Instruction cache",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2D_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Data cache",
	},
	{
		.ctrnum = 133,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_LMEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache where"
			" the installed cache line was sourced from memory"
			" that is attached to the same book as the Data cache"
			" (Local Memory)",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_LMEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" where the installed cache line was sourced from"
			" memory that is attached to the same book as the"
			" Instruction cache (Local Memory)",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 D-Cache where the"
			" line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer for a one-"
			"megabyte page",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Instruction Translation Lookaside Buffer",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Page Table Entry arrays",
	},
	{
		.ctrnum = 142,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Common Region Segment Table Entry arrays for a"
			" one-megabyte large page translation",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Common Region Segment Table Entry arrays",
	},
	{
		.ctrnum = 144,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off Chip/On Book Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFBOOK_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off Book Level-3 cache without intervention",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On Book Level-4 cache",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off Book Level-4 cache",
	},
	{
		.ctrnum = 149,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TEND",
		.desc = "A TEND instruction has completed in a"
			" nonconstrained transactional-execution mode",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from a On Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 151,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off Chip/On Book Level-3 cache with"
			" intervention",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFBOOK_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off Book Level-3 cache with intervention",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 154,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off Chip/On Book Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFBOOK_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off Book Level-3 cache without intervention",
	},
	{
		.ctrnum = 156,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On Book Level-4 cache",
	},
	{
		.ctrnum = 157,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFBOOK_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off Book Level-4 cache",
	},
	{
		.ctrnum = 158,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TEND",
		.desc = "A TEND instruction has completed in a constrained"
			" transactional-execution mode",
	},
	{
		.ctrnum = 159,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 160,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off Chip/On Book Level-3 cache with"
			" intervention",
	},
	{
		.ctrnum = 161,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFBOOK_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off Book Level-3 cache with intervention",
	},
	{
		.ctrnum = 177,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TABORT",
		.desc = "A transaction abort has occurred in a"
			" nonconstrained transactional-execution mode",
	},
	{
		.ctrnum = 178,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_NO_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is not"
			" using any special logic to allow the transaction to"
			" complete",
	},
	{
		.ctrnum = 179,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is using"
			" special logic to allow the transaction to complete",
	},
};

static const pme_cpumf_ctr_t cpumcf_z13_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 Data cache where"
			" the line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line.",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_MISSES",
		.desc = "Level-1 Data TLB miss in progress. Incremented by"
			" one for every cycle a DTLB1 miss is in progress.",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer for a one-"
			"megabyte page",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB1_GPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Data Translation Lookaside Buffer for a two-"
			"gigabyte page.",
	},
	{
		.ctrnum = 133,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2D_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Data cache",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_WRITES",
		.desc = "A translation entry has been written to the Level-1"
			" Instruction Translation Lookaside Buffer",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB1_MISSES",
		.desc = "Level-1 Instruction TLB miss in progress."
			" Incremented by one for every cycle an ITLB1 miss is"
			" in progress",
	},
	{
		.ctrnum = 136,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L2I_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Instruction cache",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Page Table Entry arrays",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_HPAGE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Combined Region Segment Table Entry arrays for"
			" a one-megabyte large page translation",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "A translation entry has been written to the Level-2"
			" TLB Combined Region Segment Table Entry arrays",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TEND",
		.desc = "A TEND instruction has completed in a constrained"
			" transactional-execution mode",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TEND",
		.desc = "A TEND instruction has completed in a non-"
			"constrained transactional-execution mode",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1C_TLB1_MISSES",
		.desc = "Increments by one for any cycle where a Level-1"
			" cache or Level-1 TLB miss is in progress.",
	},
	{
		.ctrnum = 144,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONNODE_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Node Level-4 cache",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONNODE_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Node Level-3 cache with intervention",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONNODE_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Node Level-3 cache without intervention",
	},
	{
		.ctrnum = 149,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-4 cache",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONDRAWER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-3 cache with intervention",
	},
	{
		.ctrnum = 151,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONDRAWER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_SCOL_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Same-Column Level-4 cache",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_SCOL_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Same-Column Level-3 cache with"
			" intervention",
	},
	{
		.ctrnum = 154,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_SCOL_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Same-Column Level-3 cache"
			" without intervention",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_FCOL_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Far-Column Level-4 cache",
	},
	{
		.ctrnum = 156,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_FCOL_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Far-Column Level-3 cache with"
			" intervention",
	},
	{
		.ctrnum = 157,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_FCOL_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Far-Column Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 158,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONNODE_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Node memory",
	},
	{
		.ctrnum = 159,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONDRAWER_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer memory",
	},
	{
		.ctrnum = 160,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer memory",
	},
	{
		.ctrnum = 161,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Chip memory",
	},
	{
		.ctrnum = 162,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 163,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 164,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONNODE_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Node Level-4 cache",
	},
	{
		.ctrnum = 165,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONNODE_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Node Level-3 cache with intervention",
	},
	{
		.ctrnum = 166,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONNODE_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Node Level-3 cache without intervention",
	},
	{
		.ctrnum = 167,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-4 cache",
	},
	{
		.ctrnum = 168,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONDRAWER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-3 cache with intervention",
	},
	{
		.ctrnum = 169,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONDRAWER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 170,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_SCOL_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Same-Column Level-4 cache",
	},
	{
		.ctrnum = 171,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_SCOL_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Same-Column Level-3 cache with"
			" intervention",
	},
	{
		.ctrnum = 172,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_SCOL_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Same-Column Level-3 cache"
			" without intervention",
	},
	{
		.ctrnum = 173,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_FCOL_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Far-Column Level-4 cache",
	},
	{
		.ctrnum = 174,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_FCOL_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Far-Column Level-3 cache with"
			" intervention",
	},
	{
		.ctrnum = 175,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_FCOL_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Far-Column Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 176,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONNODE_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Node memory",
	},
	{
		.ctrnum = 177,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONDRAWER_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer memory",
	},
	{
		.ctrnum = 178,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer memory",
	},
	{
		.ctrnum = 179,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_MEM_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Chip memory",
	},
	{
		.ctrnum = 218,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TABORT",
		.desc = "A transaction abort has occurred in a non-"
			"constrained transactional-execution mode",
	},
	{
		.ctrnum = 219,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_NO_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is not"
			" using any special logic to allow the transaction to"
			" complete",
	},
	{
		.ctrnum = 220,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is using"
			" special logic to allow the transaction to complete",
	},
	{
		.ctrnum = 448,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_ONE_THR_ACTIVE",
		.desc = "Cycle count with one thread active",
	},
	{
		.ctrnum = 449,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_TWO_THR_ACTIVE",
		.desc = "Cycle count with two threads active",
	},
};

static const pme_cpumf_ctr_t cpumcf_z14_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 Data cache where"
			" the line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_WRITES",
		.desc = "A translation has been written into The Translation"
			" Lookaside Buffer 2 (TLB2) and the request was made"
			" by the data cache",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the data cache. Incremented by one for every TLB2"
			" miss in progress for the Level-1 Data cache on this"
			" cycle",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_HPAGE_WRITES",
		.desc = "A translation entry was written into the Combined"
			" Region and Segment Table Entry array in the Level-2"
			" TLB for a one-megabyte page or a Last Host"
			" Translation was done",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_GPAGE_WRITES",
		.desc = "A translation entry for a two-gigabyte page was"
			" written into the Level-2 TLB",
	},
	{
		.ctrnum = 133,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2D_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Data cache",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_WRITES",
		.desc = "A translation entry has been written into the"
			" Translation Lookaside Buffer 2 (TLB2) and the"
			" request was made by the instruction cache",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the instruction cache. Incremented by one for every"
			" TLB2 miss in progress for the Level-1 Instruction"
			" cache in a cycle",
	},
	{
		.ctrnum = 136,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L2I_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Instruction cache",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry was written into the Page Table"
			" Entry array in the Level-2 TLB",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "Translation entries were written into the Combined"
			" Region and Segment Table Entry array and the Page"
			" Table Entry array in the Level-2 TLB",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_ENGINES_BUSY",
		.desc = "The number of Level-2 TLB translation engines busy"
			" in a cycle",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TEND",
		.desc = "A TEND instruction has completed in a constrained"
			" transactional-execution mode",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TEND",
		.desc = "A TEND instruction has completed in a non-"
			"constrained transactional-execution mode",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1C_TLB2_MISSES",
		.desc = "Increments by one for any cycle where a level-1"
			" cache or level-2 TLB miss is in progress",
	},
	{
		.ctrnum = 144,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Chip memory",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Cluster Level-3 cache withountervention",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster memory",
	},
	{
		.ctrnum = 149,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 151,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Cluster memory",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 154,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer memory",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache with intervention",
	},
	{
		.ctrnum = 156,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer Level-4 cache",
	},
	{
		.ctrnum = 157,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer Level-4 cache",
	},
	{
		.ctrnum = 158,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES_RO",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Chip L3 but a read-only invalidate was done"
			" to remove other copies of the cache line",
	},
	{
		.ctrnum = 162,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache ine was sourced"
			" from an On-Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 163,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache ine was sourced"
			" from On-Chip memory",
	},
	{
		.ctrnum = 164,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache ine was sourced"
			" from an On-Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 165,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 166,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster memory",
	},
	{
		.ctrnum = 167,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 168,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 169,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Cluster memory",
	},
	{
		.ctrnum = 170,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 171,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 172,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer memory",
	},
	{
		.ctrnum = 173,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache with intervention",
	},
	{
		.ctrnum = 174,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer Level-4 cache",
	},
	{
		.ctrnum = 175,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer Level-4 cache",
	},
	{
		.ctrnum = 224,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "BCD_DFP_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished Binary Coded Decimal to Decimal Floating"
			" Point conversions. Instructions: CDZT, CXZT, CZDT,"
			" CZXT",
	},
	{
		.ctrnum = 225,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "VX_BCD_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished vector arithmetic Binary Coded Decimal"
			" instructions. Instructions: VAP, VSP, VMPVMSP, VDP,"
			" VSDP, VRP, VLIP, VSRP, VPSOPVCP, VTP, VPKZ, VUPKZ,"
			" VCVB, VCVBG, VCVDVCVDG",
	},
	{
		.ctrnum = 226,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DECIMAL_INSTRUCTIONS",
		.desc = "Decimal instructions dispatched. Instructions: CVB,"
			" CVD, AP, CP, DP, ED, EDMK, MP, SRP, SP, ZAP",
	},
	{
		.ctrnum = 232,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "LAST_HOST_TRANSLATIONS",
		.desc = "Last Host Translation done",
	},
	{
		.ctrnum = 243,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TABORT",
		.desc = "A transaction abort has occurred in a non-"
			"constrained transactional-execution mode",
	},
	{
		.ctrnum = 244,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_NO_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is not"
			" using any special logic to allow the transaction to"
			" complete",
	},
	{
		.ctrnum = 245,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is using"
			" special logic to allow the transaction to complete",
	},
	{
		.ctrnum = 448,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_ONE_THR_ACTIVE",
		.desc = "Cycle count with one thread active",
	},
	{
		.ctrnum = 449,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_TWO_THR_ACTIVE",
		.desc = "Cycle count with two threads active",
	},
};

static const pme_cpumf_ctr_t cpumcf_z15_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 Data cache where"
			" the line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_WRITES",
		.desc = "A translation has been written into The Translation"
			" Lookaside Buffer 2 (TLB2) and the request was made"
			" by the data cache",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the data cache. Incremented by one for every TLB2"
			" miss in progress for the Level-1 Data cache on this"
			" cycle",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_HPAGE_WRITES",
		.desc = "A translation entry was written into the Combined"
			" Region and Segment Table Entry array in the Level-2"
			" TLB for a one-megabyte page",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_GPAGE_WRITES",
		.desc = "A translation entry for a two-gigabyte page was"
			" written into the Level-2 TLB",
	},
	{
		.ctrnum = 133,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_L2D_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Data cache",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_WRITES",
		.desc = "A translation entry has been written into the"
			" Translation Lookaside Buffer 2 (TLB2) and the"
			" request was made by the instruction cache",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the instruction cache. Incremented by one for every"
			" TLB2 miss in progress for the Level-1 Instruction"
			" cache in a cycle",
	},
	{
		.ctrnum = 136,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_L2I_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the Level-2 Instruction cache",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry was written into the Page Table"
			" Entry array in the Level-2 TLB",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "Translation entries were written into the Combined"
			" Region and Segment Table Entry array and the Page"
			" Table Entry array in the Level-2 TLB",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_ENGINES_BUSY",
		.desc = "The number of Level-2 TLB translation engines busy"
			" in a cycle",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TEND",
		.desc = "A TEND instruction has completed in a constrained"
			" transactional-execution mode",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TEND",
		.desc = "A TEND instruction has completed in a non-"
			"constrained transactional-execution mode",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1C_TLB2_MISSES",
		.desc = "Increments by one for any cycle where a level-1"
			" cache or level-2 TLB miss is in progress",
	},
	{
		.ctrnum = 144,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Chip memory",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Cluster Level-3 cache withountervention",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster memory",
	},
	{
		.ctrnum = 149,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 151,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Cluster memory",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 154,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer memory",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache with intervention",
	},
	{
		.ctrnum = 156,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer Level-4 cache",
	},
	{
		.ctrnum = 157,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_OFFDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer Level-4 cache",
	},
	{
		.ctrnum = 158,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_ONCHIP_L3_SOURCED_WRITES_RO",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Chip L3 but a read-only invalidate was done"
			" to remove other copies of the cache line",
	},
	{
		.ctrnum = 162,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache ine was sourced"
			" from an On-Chip Level-3 cache without intervention",
	},
	{
		.ctrnum = 163,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache ine was sourced"
			" from On-Chip memory",
	},
	{
		.ctrnum = 164,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCHIP_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache ine was sourced"
			" from an On-Chip Level-3 cache with intervention",
	},
	{
		.ctrnum = 165,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 166,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Cluster memory",
	},
	{
		.ctrnum = 167,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 168,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCLUSTER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 169,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCLUSTER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Cluster memory",
	},
	{
		.ctrnum = 170,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFCLUSTER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Cluster Level-3 cache with intervention",
	},
	{
		.ctrnum = 171,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_L3_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache without"
			" intervention",
	},
	{
		.ctrnum = 172,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_MEMORY_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer memory",
	},
	{
		.ctrnum = 173,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_L3_SOURCED_WRITES_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-3 cache with intervention",
	},
	{
		.ctrnum = 174,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_ONDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer Level-4 cache",
	},
	{
		.ctrnum = 175,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1I_OFFDRAWER_L4_SOURCED_WRITES",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer Level-4 cache",
	},
	{
		.ctrnum = 224,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "BCD_DFP_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished Binary Coded Decimal to Decimal Floating"
			" Point conversions. Instructions: CDZT, CXZT, CZDT,"
			" CZXT",
	},
	{
		.ctrnum = 225,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "VX_BCD_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished vector arithmetic Binary Coded Decimal"
			" instructions. Instructions: VAP, VSP, VMPVMSP, VDP,"
			" VSDP, VRP, VLIP, VSRP, VPSOPVCP, VTP, VPKZ, VUPKZ,"
			" VCVB, VCVBG, VCVDVCVDG",
	},
	{
		.ctrnum = 226,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DECIMAL_INSTRUCTIONS",
		.desc = "Decimal instructions dispatched. Instructions: CVB,"
			" CVD, AP, CP, DP, ED, EDMK, MP, SRP, SP, ZAP",
	},
	{
		.ctrnum = 232,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "LAST_HOST_TRANSLATIONS",
		.desc = "Last Host Translation done",
	},
	{
		.ctrnum = 243,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TABORT",
		.desc = "A transaction abort has occurred in a non-"
			"constrained transactional-execution mode",
	},
	{
		.ctrnum = 244,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_NO_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is not"
			" using any special logic to allow the transaction to"
			" complete",
	},
	{
		.ctrnum = 245,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is using"
			" special logic to allow the transaction to complete",
	},
	{
		.ctrnum = 247,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_ACCESS",
		.desc = "Cycles CPU spent obtaining access to Deflate unit",
	},
	{
		.ctrnum = 252,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CYCLES",
		.desc = "Cycles CPU is using Deflate unit",
	},
	{
		.ctrnum = 264,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CC",
		.desc = "Increments by one for every DEFLATE CONVERSION CALL"
			" instruction executed",
	},
	{
		.ctrnum = 265,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CCFINISH",
		.desc = "Increments by one for every DEFLATE CONVERSION CALL"
			" instruction executed that ended in Condition Codes"
			" 0, 1 or 2",
	},
	{
		.ctrnum = 448,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_ONE_THR_ACTIVE",
		.desc = "Cycle count with one thread active",
	},
	{
		.ctrnum = 449,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_TWO_THR_ACTIVE",
		.desc = "Cycle count with two threads active",
	},
};

static const pme_cpumf_ctr_t cpumcf_z16_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 Data cache where"
			" the line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line.",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_WRITES",
		.desc = "A translation has been written into The Translation"
			" Lookaside Buffer 2 (TLB2) and the request was made"
			" by the Level-1 Data cache. This is a replacement"
			" for what was provided for the DTLB on z13 and prior"
			" machines.",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the Level-1 Data cache. Incremented by one for"
			" every TLB2 miss in progress for the Level-1 Data"
			" cache on this cycle. This is a replacement for what"
			" was provided for the DTLB on z13 and prior"
			" machines.",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "CRSTE_1MB_WRITES",
		.desc = "A translation entry was written into the Combined"
			" Region and Segment Table Entry array in the Level-2"
			" TLB for a one-megabyte page.",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_GPAGE_WRITES",
		.desc = "A translation entry for a two-gigabyte page was"
			" written into the Level-2 TLB.",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_WRITES",
		.desc = "A translation entry has been written into the"
			" Translation Lookaside Buffer 2 (TLB2) and the"
			" request was made by the instruction cache. This is"
			" a replacement for what was provided for the ITLB on"
			" z13 and prior machines.",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the Level-1 Instruction cache. Incremented by one"
			" for every TLB2 miss in progress for the Level-1"
			" Instruction cache in a cycle. This is a replacement"
			" for what was provided for the ITLB on z13 and prior"
			" machines.",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry was written into the Page Table"
			" Entry array in the Level-2 TLB.",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "Translation entries were written into the Combined"
			" Region and Segment Table Entry array and the Page"
			" Table Entry array in the Level-2 TLB.",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_ENGINES_BUSY",
		.desc = "The number of Level-2 TLB translation engines busy"
			" in a cycle.",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TEND",
		.desc = "A TEND instruction has completed in a constrained"
			" transactional-execution mode.",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TEND",
		.desc = "A TEND instruction has completed in a non-"
			" constrained transactional-execution mode.",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1C_TLB2_MISSES",
		.desc = "Increments by one for any cycle where a level-1"
			" cache or level-2 TLB miss is in progress.",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestor's Level-2 cache.",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestor's Level-2 cache with"
			" intervention.",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestor's Level-2 cache after using"
			" chip level horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestor's Level-2 cache after using"
			" drawer level horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 149,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache.",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache with intervention.",
	},
	{
		.ctrnum = 151,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache after using chip"
			" level horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache using drawer level"
			" horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_MODULE",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Module Level-2 cache.",
	},
	{
		.ctrnum = 154,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_DRAWER",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_OFF_DRAWER",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 156,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_MEMORY",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Chip memory.",
	},
	{
		.ctrnum = 157,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_MODULE_MEMORY",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Module memory.",
	},
	{
		.ctrnum = 158,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_DRAWER_MEMORY",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer memory.",
	},
	{
		.ctrnum = 159,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_OFF_DRAWER_MEMORY",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer memory.",
	},
	{
		.ctrnum = 160,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_MODULE_IV",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Module Level-2"
			" cache with intervention.",
	},
	{
		.ctrnum = 161,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_MODULE_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Module Level-2"
			" cache using chip horizontal persistence, Chip-HP"
			" hit.",
	},
	{
		.ctrnum = 162,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_MODULE_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Module Level-2"
			" cache using drawer level horizontal persistence,"
			" Drawer-HP hit.",
	},
	{
		.ctrnum = 163,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_DRAWER_IV",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Drawer Level-2"
			" cache with intervention.",
	},
	{
		.ctrnum = 164,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_DRAWER_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an On-Drawer Level-2"
			" cache using chip level horizontal persistence, Chip-"
			" HP hit.",
	},
	{
		.ctrnum = 165,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_DRAWER_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an On-Drawer Level-2"
			" cache using drawer level horizontal persistence,"
			" Drawer-HP hit.",
	},
	{
		.ctrnum = 166,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_OFF_DRAWER_IV",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an Off-Drawer Level-2"
			" cache with intervention.",
	},
	{
		.ctrnum = 167,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_OFF_DRAWER_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an Off-Drawer Level-2"
			" cache using chip level horizontal persistence, Chip-"
			" HP hit.",
	},
	{
		.ctrnum = 168,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_OFF_DRAWER_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an Off-Drawer Level-2"
			" cache using drawer level horizontal persistence,"
			" Drawer-HP hit.",
	},
	{
		.ctrnum = 169,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" the requestors Level-2 cache.",
	},
	{
		.ctrnum = 170,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache with"
			" intervention.",
	},
	{
		.ctrnum = 171,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ_CHIP_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache using chip level"
			" horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 172,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the requestor's Level-2 cache using drawer"
			" level horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 173,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache.",
	},
	{
		.ctrnum = 174,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" an On-Chip Level-2 cache with intervention.",
	},
	{
		.ctrnum = 175,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_CHIP_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache using chip level"
			" horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 176,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip level 2 cache using drawer level"
			" horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 177,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_MODULE",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Module Level-2 cache.",
	},
	{
		.ctrnum = 178,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_DRAWER",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" an On-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 179,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_OFF_DRAWER",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" an Off-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 180,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_MEMORY",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Chip memory.",
	},
	{
		.ctrnum = 181,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_MODULE_MEMORY",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Module memory.",
	},
	{
		.ctrnum = 182,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_DRAWER_MEMORY",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from On-Drawer memory.",
	},
	{
		.ctrnum = 183,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_OFF_DRAWER_MEMORY",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from Off-Drawer memory.",
	},
	{
		.ctrnum = 224,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "BCD_DFP_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished Binary Coded Decimal to Decimal Floating"
			" Point conversions. Instructions: CDZT, CXZT, CZDT,"
			" CZXT.",
	},
	{
		.ctrnum = 225,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "VX_BCD_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished vector arithmetic Binary Coded Decimal"
			" instructions. Instructions: VAP, VSP, VMP, VMSP,"
			" VDP, VSDP, VRP, VLIP, VSRP, VPSOP, VCP, VTP, VPKZ,"
			" VUPKZ, VCVB, VCVBG, VCVD, VCVDG.",
	},
	{
		.ctrnum = 226,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DECIMAL_INSTRUCTIONS",
		.desc = "Decimal instruction dispatched. Instructions: CVB,"
			" CVD, AP, CP, DP, ED, EDMK, MP, SRP, SP, ZAP.",
	},
	{
		.ctrnum = 232,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "LAST_HOST_TRANSLATIONS",
		.desc = "Last Host Translation done",
	},
	{
		.ctrnum = 244,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TABORT",
		.desc = "A transaction abort has occurred in a non-"
			" constrained transactional-execution mode.",
	},
	{
		.ctrnum = 245,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_NO_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is not"
			" using any special logic to allow the transaction to"
			" complete.",
	},
	{
		.ctrnum = 246,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is using"
			" special logic to allow the transaction to complete.",
	},
	{
		.ctrnum = 248,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_ACCESS",
		.desc = "Cycles CPU spent obtaining access to Deflate unit",
	},
	{
		.ctrnum = 253,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CYCLES",
		.desc = "Cycles CPU is using Deflate unit",
	},
	{
		.ctrnum = 256,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "SORTL",
		.desc = "Increments by one for every SORT LISTS instruction"
			" executed.",
	},
	{
		.ctrnum = 265,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CC",
		.desc = "Increments by one for every DEFLATE CONVERSION CALL"
			" instruction executed.",
	},
	{
		.ctrnum = 266,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CCFINISH",
		.desc = "Increments by one for every DEFLATE CONVERSION CALL"
			" instruction executed that ended in Condition Codes"
			" 0, 1 or 2.",
	},
	{
		.ctrnum = 267,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_INVOCATIONS",
		.desc = "Increments by one for every Neural Network"
			" Processing Assist instruction executed.",
	},
	{
		.ctrnum = 268,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_COMPLETIONS",
		.desc = "Increments by one for every Neural Network"
			" Processing Assist instruction executed that ended"
			" in Condition Codes 0, 1 or 2.",
	},
	{
		.ctrnum = 269,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_WAIT_LOCK",
		.desc = "Cycles CPU spent obtaining access to IBM Z"
			" Integrated Accelerator for AI.",
	},
	{
		.ctrnum = 270,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_HOLD_LOCK",
		.desc = "Cycles CPU is using IBM Z Integrated Accelerator"
			" for AI.",
	},
	{
		.ctrnum = 448,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_ONE_THR_ACTIVE",
		.desc = "Cycle count with one thread active",
	},
	{
		.ctrnum = 449,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_TWO_THR_ACTIVE",
		.desc = "Cycle count with two threads active",
	},
};

static const pme_cpumf_ctr_t cpumcf_z17_counters[] = {
	{
		.ctrnum = 128,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1D_RO_EXCL_WRITES",
		.desc = "A directory write to the Level-1 Data cache where"
			" the line was originally in a Read-Only state in the"
			" cache but has been updated to be in the Exclusive"
			" state that allows stores to the cache line.",
	},
	{
		.ctrnum = 129,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_WRITES",
		.desc = "A translation has been written into The Translation"
			" Lookaside Buffer 2 (TLB2) and the request was made"
			" by the Level-1 Data cache. This is a replacement"
			" for what was provided for the DTLB on z13 and prior"
			" machines.",
	},
	{
		.ctrnum = 130,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the Level-1 Data cache. Incremented by one for"
			" every TLB2 miss in progress for the Level-1 Data"
			" cache on this cycle. This is a replacement for what"
			" was provided for the DTLB on z13 and prior"
			" machines.",
	},
	{
		.ctrnum = 131,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "CRSTE_1MB_WRITES",
		.desc = "A translation entry was written into the Combined"
			" Region and Segment Table Entry array in the Level-2"
			" TLB for a one-megabyte page.",
	},
	{
		.ctrnum = 132,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DTLB2_GPAGE_WRITES",
		.desc = "A translation entry for a two-gigabyte page was"
			" written into the Level-2 TLB.",
	},
	{
		.ctrnum = 134,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_WRITES",
		.desc = "A translation entry has been written into the"
			" Translation Lookaside Buffer 2 (TLB2) and the"
			" request was made by the Level-1 Instruction cache."
			" This is a replacement for what was provided for the"
			" ITLB on z13 and prior machines.",
	},
	{
		.ctrnum = 135,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ITLB2_MISSES",
		.desc = "A TLB2 miss is in progress for a request made by"
			" the Level-1 Instruction cache. Incremented by one"
			" for every TLB2 miss in progress for the Level-1"
			" Instruction cache in a cycle. This is a replacement"
			" for what was provided for the ITLB on z13 and prior"
			" machines.",
	},
	{
		.ctrnum = 137,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_PTE_WRITES",
		.desc = "A translation entry was written into the Page Table"
			" Entry array in the Level-2 TLB.",
	},
	{
		.ctrnum = 138,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_CRSTE_WRITES",
		.desc = "Translation entries were written into the Combined"
			" Region and Segment Table Entry array and the Page"
			" Table Entry array in the Level-2 TLB.",
	},
	{
		.ctrnum = 139,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TLB2_ENGINES_BUSY",
		.desc = "The number of Level-2 TLB translation engines busy"
			" in a cycle.",
	},
	{
		.ctrnum = 140,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TEND",
		.desc = "A TEND instruction has completed in a constrained"
			" transactional-execution mode.",
	},
	{
		.ctrnum = 141,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TEND",
		.desc = "A TEND instruction has completed in a non-"
			" constrained transactional-execution mode.",
	},
	{
		.ctrnum = 143,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "L1C_TLB2_MISSES",
		.desc = "Increments by one for any cycle where a Level-1"
			" cache or Level-2 TLB miss is in progress.",
	},
	{
		.ctrnum = 145,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache.",
	},
	{
		.ctrnum = 146,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache with"
			" intervention.",
	},
	{
		.ctrnum = 147,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache after using"
			" chip level horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 148,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_REQ_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache after using"
			" drawer level horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 149,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache.",
	},
	{
		.ctrnum = 150,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_IV",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache with intervention.",
	},
	{
		.ctrnum = 151,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache after using chip"
			" level horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 152,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache after using drawer"
			" level horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 153,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_MODULE",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Module Level-2 cache.",
	},
	{
		.ctrnum = 154,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_DRAWER",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 155,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_OFF_DRAWER",
		.desc = "A directory write to the Level-1 Data cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 156,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_CHIP_MEMORY",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from On-Chip memory.",
	},
	{
		.ctrnum = 157,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_MODULE_MEMORY",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from On-Module memory.",
	},
	{
		.ctrnum = 158,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_ON_DRAWER_MEMORY",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from On-Drawer memory.",
	},
	{
		.ctrnum = 159,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DCW_OFF_DRAWER_MEMORY",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from Off-Drawer memory.",
	},
	{
		.ctrnum = 160,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_MODULE_IV",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Module Level-2"
			" cache with intervention.",
	},
	{
		.ctrnum = 161,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_MODULE_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Module Level-2"
			" cache after using chip level horizontal"
			" persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 162,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_MODULE_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Module Level-2"
			" cache after using drawer level horizontal"
			" persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 163,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_DRAWER_IV",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an On-Drawer Level-2"
			" cache with intervention.",
	},
	{
		.ctrnum = 164,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_DRAWER_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an On-Drawer Level-2"
			" cache after using chip level horizontal"
			" persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 165,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_ON_DRAWER_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an On-Drawer Level-2"
			" cache after using drawer level horizontal"
			" persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 166,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_OFF_DRAWER_IV",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an Off-Drawer Level-2"
			" cache with intervention.",
	},
	{
		.ctrnum = 167,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_OFF_DRAWER_CHIP_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" instruction cache directory where the returned"
			" cache line was sourced from an Off-Drawer Level-2"
			" cache after using chip level horizontal"
			" persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 168,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "IDCW_OFF_DRAWER_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Data or Level-1"
			" Instruction cache directory where the returned"
			" cache line was sourced from an Off-Drawer Level-2"
			" cache after using drawer level horizontal"
			" persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 169,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" the requestors Level-2 cache.",
	},
	{
		.ctrnum = 170,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache with"
			" intervention.",
	},
	{
		.ctrnum = 171,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ_CHIP_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache after using"
			" chip level horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 172,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_REQ_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from the requestors Level-2 cache after using"
			" drawer level horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 173,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache.",
	},
	{
		.ctrnum = 174,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_IV",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache with intervention.",
	},
	{
		.ctrnum = 175,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_CHIP_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip Level-2 cache after using chip"
			" level horizontal persistence, Chip-HP hit.",
	},
	{
		.ctrnum = 176,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_CHIP_DRAWER_HIT",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Chip level 2 cache after using drawer"
			" level horizontal persistence, Drawer-HP hit.",
	},
	{
		.ctrnum = 177,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_MODULE",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Module Level-2 cache.",
	},
	{
		.ctrnum = 178,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_ON_DRAWER",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an On-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 179,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "ICW_OFF_DRAWER",
		.desc = "A directory write to the Level-1 Instruction cache"
			" directory where the returned cache line was sourced"
			" from an Off-Drawer Level-2 cache.",
	},
	{
		.ctrnum = 202,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "CYCLES_SAMETHRD",
		.desc = "The number of cycles the CPU is not in wait state"
			" and the CPU is running by itself on the Core.",
	},
	{
		.ctrnum = 203,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "CYCLES_DIFFTHRD",
		.desc = "The number of cycles the CPU is not in wait state"
			" and the CPU is running with another thread on the"
			" Core.",
	},
	{
		.ctrnum = 204,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "INST_SAMETHRD",
		.desc = "The number of instructions executed on the CPU and"
			" the CPU is running by itself on the Core.",
	},
	{
		.ctrnum = 205,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "INST_DIFFTHRD",
		.desc = "The number of instructions executed on the CPU and"
			" the CPU is running with another thread on the Core.",
	},
	{
		.ctrnum = 206,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "WRONG_BRANCH_PREDICTION",
		.desc = "A count of the number of branches that were"
			" predicted incorrectly by the branch prediction"
			" logic in the Core. This includes incorrectly"
			" predicted branches that are executed in Firmware."
			" Examples of instructions implemented in Firmware"
			" are complicated instructions like MVCL (Move"
			" Character Long) and PC (Program Call).",
	},
	{
		.ctrnum = 225,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "VX_BCD_EXECUTION_SLOTS",
		.desc = "Count of floating point execution slots used for"
			" finished vector arithmetic Binary Coded Decimal"
			" instructions. Instructions: VAP, VSP, VMP, VMSP,"
			" VDP, VSDP, VRP, VLIP, VSRP, VPSOP, VCP, VTP, VPKZ,"
			" VUPKZ, VCVB, VCVBG, VCVD, VCVDG, VSCHP, VSCSHP,"
			" VCSPH, VCLZDP, VPKZR, VSRPR, VUPKZH, VUPKZL, VTZ,"
			" VUPH, VUPL, VCVBX, VCVDX.",
	},
	{
		.ctrnum = 226,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DECIMAL_INSTRUCTIONS",
		.desc = "Decimal instruction dispatched. Instructions: CVB,"
			" CVD, AP, CP, DP, ED, EDMK, MP, SRP, SP, ZAP, TP.",
	},
	{
		.ctrnum = 232,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "LAST_HOST_TRANSLATIONS",
		.desc = "Last Host Translation done.",
	},
	{
		.ctrnum = 244,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_NC_TABORT",
		.desc = "A transaction abort has occurred in a non-"
			" constrained transactional-execution mode.",
	},
	{
		.ctrnum = 245,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_NO_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is not"
			" using any special logic to allow the transaction to"
			" complete.",
	},
	{
		.ctrnum = 246,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "TX_C_TABORT_SPECIAL",
		.desc = "A transaction abort has occurred in a constrained"
			" transactional-execution mode and the CPU is using"
			" special logic to allow the transaction to complete.",
	},
	{
		.ctrnum = 248,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_ACCESS",
		.desc = "Cycles CPU spent obtaining access to Deflate unit.",
	},
	{
		.ctrnum = 253,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CYCLES",
		.desc = "Cycles CPU is using Deflate unit.",
	},
	{
		.ctrnum = 256,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "SORTL",
		.desc = "Increments by one for every SORT LISTS (SORTL)"
			" instruction executed.",
	},
	{
		.ctrnum = 265,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CC",
		.desc = "Increments by one for every DEFLATE CONVERSION CALL"
			" (DFLTCC) instruction executed.",
	},
	{
		.ctrnum = 266,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "DFLT_CCFINISH",
		.desc = "Increments by one for every DEFLATE CONVERSION CALL"
			" (DFLTCC) instruction executed that ended in"
			" Condition Codes 0, 1 or 2.",
	},
	{
		.ctrnum = 267,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_INVOCATIONS",
		.desc = "Increments by one for every NEURAL NETWORK"
			" PROCESSING ASSIST (NNPA) instruction executed.",
	},
	{
		.ctrnum = 268,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_COMPLETIONS",
		.desc = "Increments by one for every NEURAL NETWORK"
			" PROCESSING ASSIST (NNPA) instruction executed that"
			" ended in Condition Code 0.",
	},
	{
		.ctrnum = 269,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_WAIT_LOCK",
		.desc = "Cycles CPU spent obtaining access to IBM Z"
			" Integrated Accelerator for AI.",
	},
	{
		.ctrnum = 270,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_HOLD_LOCK",
		.desc = "Cycles CPU is using IBM Z Integrated Accelerator"
			" for AI.",
	},
	{
		.ctrnum = 272,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_INST_ONCHIP",
		.desc = "A NEURAL NETWORK PROCESSING ASSIST (NNPA)"
			" instruction has used the Local On-Chip IBM Z"
			" Integrated Accelerator for AI during its execution",
	},
	{
		.ctrnum = 273,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_INST_OFFCHIP",
		.desc = "A NEURAL NETWORK PROCESSING ASSIST (NNPA)"
			" instruction has used an Off-Chip IBM Z Integrated"
			" Accelerator for AI during its execution.",
	},
	{
		.ctrnum = 274,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_INST_DIFF",
		.desc = "A NEURAL NETWORK PROCESSING ASSIST (NNPA)"
			" instruction has used a different IBM Z Integrated"
			" Accelerator for AI since it was last executed.",
	},
	{
		.ctrnum = 276,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_4K_PREFETCH",
		.desc = "Number of 4K prefetches done for a remote IBM Z"
			" Integated Accelerator for AI.",
	},
	{
		.ctrnum = 277,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_COMPL_LOCK",
		.desc = "A PERFORM LOCKED OPERATION (PLO) has completed.",
	},
	{
		.ctrnum = 278,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_RETRY_LOCK",
		.desc = "A PERFORM LOCKED OPERATION (PLO) has been retried and"
			" the CPU did not use any special logic to allow the"
			" PLO to complete.",
	},
	{
		.ctrnum = 279,
		.ctrset = CPUMF_CTRSET_EXTENDED,
		.name = "NNPA_RETRY_LOCK_WITH_PLO",
		.desc = "A PERFORM LOCKED OPERATION (PLO) has been retried and"
			" the CPU is using special logic to allow PLO to"
			" complete.",
	},
	{
		.ctrnum = 448,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_ONE_THR_ACTIVE",
		.desc = "Cycle count with one thread active",
	},
	{
		.ctrnum = 449,
		.ctrset = CPUMF_CTRSET_MT_DIAG,
		.name = "MT_DIAG_CYCLES_TWO_THR_ACTIVE",
		.desc = "Cycle count with two threads active",
	},
};

static const pme_cpumf_ctr_t cpumsf_counters[] = {
	{
		.ctrnum = 720896,
		.ctrset = CPUMF_CTRSET_NONE,
		.name = "SF_CYCLES_BASIC",
		.desc = "Sample CPU cycles using basic-sampling mode",
	},
	{
		.ctrnum = 774144,
		.ctrset = CPUMF_CTRSET_NONE,
		.name = "SF_CYCLES_BASIC_DIAG",
		.desc = "Sample CPU cycle using diagnostic-sampling mode"
			" (not for ordinary use)",
	},
};

#endif /* __S390X_CPUMF_EVENTS_H__ */
