static const sparc_entry_t ultra12_pe[] = {
	/* These two must always be first.  */
	{	.name = "Cycle_cnt",
		.desc = "Accumulated cycles",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x0,
	},
	{	.name = "Instr_cnt",
		.desc = "Number of instructions completed",
		.ctrl = PME_CTRL_S0 | PME_CTRL_S1,
		.code = 0x1,
	},
	{
		.name = "Dispatch0_IC_miss",
		.desc = "I-buffer is empty from I-Cache miss",
		.ctrl = PME_CTRL_S0,
		.code = 0x2,
	},

	/* PIC0 events for UltraSPARC-I/II/IIi/IIe */
	{
		.name = "Dispatch0_storeBuf",
		.desc = "Store buffer can not hold additional stores",
		.ctrl = PME_CTRL_S0,
		.code = 0x3,
	},
	{
		.name = "IC_ref",
		.desc = "I-cache references",
		.ctrl = PME_CTRL_S0,
		.code = 0x8,
	},
	{
		.name = "DC_rd",
		.desc = "D-cache read references (including accesses that subsequently trap)",
		.ctrl = PME_CTRL_S0,
		.code = 0x9,
	},
	{
		.name = "DC_wr",
		.desc = "D-cache write references (including accesses that subsequently trap)",
		.ctrl = PME_CTRL_S0,
		.code = 0xa,
	},
	{
		.name = "Load_use",
		.desc = "An instruction in the execute stage depends on an earlier load result that is not yet available",
		.ctrl = PME_CTRL_S0,
		.code = 0xb,
	},
	{
		.name = "EC_ref",
		.desc = "Total E-cache references",
		.ctrl = PME_CTRL_S0,
		.code = 0xc,
	},
	{
		.name = "EC_write_hit_RDO",
		.desc = "E-cache hits that do a read for ownership UPA transaction",
		.ctrl = PME_CTRL_S0,
		.code = 0xd,
	},
	{
		.name = "EC_snoop_inv",
		.desc = "E-cache invalidates from the following UPA transactions: S_INV_REQ, S_CPI_REQ",
		.ctrl = PME_CTRL_S0,
		.code = 0xe,
	},
	{
		.name = "EC_rd_hit",
		.desc = "E-cache read hits from D-cache misses",
		.ctrl = PME_CTRL_S0,
		.code = 0xf,
	},

	/* PIC1 events for UltraSPARC-I/II/IIi/IIe */
	{
		.name = "Dispatch0_mispred",
		.desc = "I-buffer is empty from Branch misprediction",
		.ctrl = PME_CTRL_S1,
		.code = 0x2,
	},
	{
		.name = "Dispatch0_FP_use",
		.desc = "First instruction in the group depends on an earlier floating point result that is not yet available",
		.ctrl = PME_CTRL_S1,
		.code = 0x3,
	},
	{
		.name = "IC_hit",
		.desc = "I-cache hits",
		.ctrl = PME_CTRL_S1,
		.code = 0x8,
	},
	{
		.name = "DC_rd_hit",
		.desc = "D-cache read hits",
		.ctrl = PME_CTRL_S1,
		.code = 0x9,
	},
	{
		.name = "DC_wr_hit",
		.desc = "D-cache write hits",
		.ctrl = PME_CTRL_S1,
		.code = 0xa,
	},
	{
		.name = "Load_use_RAW",
		.desc = "There is a load use in the execute stage and there is a read-after-write hazard on the oldest outstanding load",
		.ctrl = PME_CTRL_S1,
		.code = 0xb,
	},
	{
		.name = "EC_hit",
		.desc = "Total E-cache hits",
		.ctrl = PME_CTRL_S1,
		.code = 0xc,
	},
	{
		.name = "EC_wb",
		.desc = "E-cache misses that do writebacks",
		.ctrl = PME_CTRL_S1,
		.code = 0xd,
	},
	{
		.name = "EC_snoop_cb",
		.desc = "E-cache snoop copy-backs from the following UPA transactions: S_CPB_REQ, S_CPI_REQ, S_CPD_REQ, S_CPB_MIS_REQ",
		.ctrl = PME_CTRL_S1,
		.code = 0xe,
	},
	{
		.name = "EC_ic_hit",
		.desc = "E-cache read hits from I-cache misses",
		.ctrl = PME_CTRL_S1,
		.code = 0xf,
	},
};
#define PME_SPARC_ULTRA12_EVENT_COUNT	   (sizeof(ultra12_pe)/sizeof(sparc_entry_t))
