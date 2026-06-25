static const sparc_entry_t niagara2_pe[] = {
	/* PIC0 Niagara-2 events */
	{	.name = "All_strands_idle",
		.desc = "Cycles when no strand can be picked for the physical core on which the monitoring strand resides.",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x0,
	},
	{	.name = "Instr_cnt",
		.desc = "Number of instructions completed",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x2,
		.umasks = {
			{
				.uname = "branches",
				.udesc = "Completed branches",
				.ubit = 0,
			},
			{
				.uname = "taken_branches",
				.udesc = "Taken branches, which are always mispredicted",
				.ubit = 1,
			},
			{
				.uname = "FGU_arith",
				.udesc = "All FADD, FSUB, FCMP, convert, FMUL, FDIV, FNEG, FABS, FSQRT, FMOV, FPADD, FPSUB, FPACK, FEXPAND, FPMERGE, FMUL8, FMULD8, FALIGNDATA, BSHUFFLE, FZERO, FONE, FSRC, FNOT1, FNOT2, FOR, FNOR, FAND, FNAND, FXOR, FXNOR, FORNOT1, FORNOT2, FANDNOT1, FANDNOT2, PDIST, SIAM",
				.ubit = 2,

			},
			{
				.uname = "Loads",
				.udesc = "Load instructions",
				.ubit = 3,
			},
			{
				.uname = "Stores",
				.udesc = "Stores instructions",
				.ubit = 3,
			},
			{
				.uname = "SW_count",
				.udesc = "Software count 'sethi %hi(fc00), %g0' instructions",
				.ubit = 5,
			},
			{
				.uname = "other",
				.udesc = "Instructions not covered by other mask bits",
				.ubit = 6,
			},
			{
				.uname = "atomics",
				.udesc = "Atomics are LDSTUB/A, CASA/XA, SWAP/A",
				.ubit = 7,
			},
		},
		.numasks = 8,
	},
	{
		.name = "cache",
		.desc = "Cache events",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x3,
		.umasks = {
			{
				.uname = "IC_miss",
				.udesc = "I-cache misses. This counts only primary instruction cache misses, and does not count duplicate instruction cache misses.4 Also, only 'true' misses are counted. If a thread encounters an I$ miss, but the thread is redirected (due to a branch misprediction or trap, for example) before the line returns from L2 and is loaded into the I$, then the miss is not counted.",
				.ubit = 0,
			},
			{
				.uname = "DC_miss",
				.udesc = "D-cache misses.  This counts both primary and duplicate data cache misses.",
				.ubit = 1,
			},
			{
				.uname = "L2IC_miss",
				.udesc = "L2 cache instruction misses",
				.ubit = 4,
			},
			{
				.uname = "L2LD_miss",
				.udesc = "L2 cache load misses.  Block loads are treated as one L2 miss event. In reality, each individual load can hit or miss in the L2 since the block load is not atomic.",
				.ubit = 5,
			},
		},
		.numasks = 4,
	},
	{
		.name = "TLB",
		.desc = "TLB events",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x4,
		.umasks = {
			{
				.uname = "ITLB_L2ref",
				.udesc = "ITLB references to L2. For each ITLB miss with hardware tablewalk enabled, count each access the ITLB hardware tablewalk makes to L2.",
				.ubit = 2,
			},
			{
				.uname = "DTLB_L2ref",
				.udesc = "DTLB references to L2. For each DTLB miss with hardware tablewalk enabled, count each access the DTLB hardware tablewalk makes to L2.",
				.ubit = 3,
			},
			{
				.uname = "ITLB_L2miss",
				.udesc = "For each ITLB miss with hardware tablewalk enabled, count each access the ITLB hardware tablewalk makes to L2 which misses in L2.  Note: Depending upon the hardware table walk configuration, each ITLB miss may issue from 1 to 4 requests to L2 to search TSBs.",
				.ubit = 4,
			},
			{
				.uname = "DTLB_L2miss",
				.udesc = "For each DTLB miss with hardware tablewalk enabled, count each access the DTLB hardware tablewalk makes to L2 which misses in L2.  Note: Depending upon the hardware table walk configuration, each DTLB miss may issue from 1 to 4 requests to L2 to search TSBs.",
				.ubit = 5,

			},
		},
		.numasks = 4,
	},
	{
		.name = "mem",
		.desc = "Memory operations",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x5,
		.umasks = {
			{
				.uname = "stream_load",
				.udesc = "Stream Unit load operations to L2",
				.ubit = 0,
			},
			{
				.uname = "stream_store",
				.udesc = "Stream Unit store operations to L2",
				.ubit = 1,
			},
			{
				.uname = "cpu_load",
				.udesc = "CPU loads to L2",
				.ubit = 2,
			},
			{
				.uname = "cpu_ifetch",
				.udesc = "CPU instruction fetches to L2",
				.ubit = 3,
			},
			{
				.uname = "cpu_store",
				.udesc = "CPU stores to L2",
				.ubit = 6,
			},
			{
				.uname = "mmu_load",
				.udesc = "MMU loads to L2",
				.ubit = 7,
			},
		},
		.numasks = 6,
	},
	{
		.name = "spu_ops",
		.desc = "Stream Unit operations.  User, supervisor, and hypervisor counting must all be enabled to properly count these events.",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x6,
		.umasks = {
			{
				.uname = "DES",
				.udesc = "Increment for each CWQ or ASI operation that uses DES/3DES unit",
				.ubit = 0,
			},
			{
				.uname = "AES",
				.udesc = "Increment for each CWQ or ASI operation that uses AES unit",
				.ubit = 1,
			},
			{
				.uname = "RC4",
				.udesc = "Increment for each CWQ or ASI operation that uses RC4 unit",
				.ubit = 2,
			},
			{
				.uname = "HASH",
				.udesc = "Increment for each CWQ or ASI operation that uses MD5/SHA-1/SHA-256 unit",
				.ubit = 3,
			},
			{
				.uname = "MA",
				.udesc = "Increment for each CWQ or ASI modular arithmetic operation",
				.ubit = 4,
			},
			{
				.uname = "CSUM",
				.udesc = "Increment for each iSCSI CRC or TCP/IP checksum operation",
				.ubit = 5,
			},
		},
		.numasks = 6,
	},
	{
		.name = "spu_busy",
		.desc = "Stream Unit busy cycles.  User, supervisor, and hypervisor counting must all be enabled to properly count these events.",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x07,
		.umasks = {
			{
				.uname = "DES",
				.udesc = "Cycles the DES/3DES unit is busy",
				.ubit = 0,
			},
			{
				.uname = "AES",
				.udesc = "Cycles the AES unit is busy",
				.ubit = 1,
			},
			{
				.uname = "RC4",
				.udesc = "Cycles the RC4 unit is busy",
				.ubit = 2,
			},
			{
				.uname = "HASH",
				.udesc = "Cycles the MD5/SHA-1/SHA-256 unit is busy",
				.ubit = 3,
			},
			{
				.uname = "MA",
				.udesc = "Cycles the modular arithmetic unit is busy",
				.ubit = 4,
			},
			{
				.uname = "CSUM",
				.udesc = "Cycles the CRC/MPA/checksum unit is busy",
				.ubit = 5,
			},
		},
		.numasks = 6,
	},
	{
		.name = "tlb_miss",
		.desc = "TLB misses",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0xb,
		.umasks = {
			{
				.uname = "ITLB",
				.udesc = "I-TLB misses",
				.ubit = 2,
			},
			{
				.uname = "DTLB",
				.udesc = "D-TLB misses",
				.ubit = 3,
			},
		},
		.numasks = 2,
	},
};
#define PME_SPARC_NIAGARA2_EVENT_COUNT	   (sizeof(niagara2_pe)/sizeof(sparc_entry_t))
