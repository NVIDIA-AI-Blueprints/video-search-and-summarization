/*
 * Copyright (c) 2019 Marvell Technology Group Ltd
 * Contributed by Shay Gal-On <sgalon@marvell.com>
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
 * Marvell ThunderX2
 *
 * ARM Architecture Reference Manual, ARMv8, for ARMv8-A architecture profile,
 * ARM DDI 0487B.a (ID033117)
 *
 * Marvell ThunderX2 C99XX Core and Uncore PMU Events (Abridged) can be found at
 * https://www.marvell.com/documents/hrur6mybdvk5uki1w0z7/
 *
 */


/* L3C event IDs */
#define L3_EVENT_READ_REQ               0xD
#define L3_EVENT_WRITEBACK_REQ          0xE
#define L3_EVENT_EVICT_REQ              0x13
#define L3_EVENT_READ_HIT               0x17
#define L3_EVENT_MAX                    0x18

/* DMC event IDs */
#define DMC_EVENT_COUNT_CYCLES          0x1
#define DMC_EVENT_WRITE_TXNS            0xB
#define DMC_EVENT_DATA_TRANSFERS        0xD
#define DMC_EVENT_READ_TXNS             0xF
#define DMC_EVENT_MAX                   0x10

/* CCPI event IDs */
#define CCPI2_EVENT_REQ_PKT_SENT	0x3D
#define CCPI2_EVENT_SNOOP_PKT_SENT	0x65
#define CCPI2_EVENT_DATA_PKT_SENT	0x105
#define CCPI2_EVENT_GIC_PKT_SENT	0x12D


static const arm_entry_t arm_thunderx2_unc_dmc_pe[]={
	{.name = "UNC_DMC_READS",
	 .modmsk = ARMV8_ATTRS,
	 .code = DMC_EVENT_READ_TXNS,
	 .desc = "Memory read transactions"
	},
	{.name = "UNC_DMC_WRITES",
	 .modmsk = ARMV8_ATTRS,
	 .code = DMC_EVENT_WRITE_TXNS,
	 .desc = "Memory write transactions"
	},
	{.name = "UNC_DMC_DATA_TRANSFERS",
	 .modmsk = ARMV8_ATTRS,
	 .code = DMC_EVENT_DATA_TRANSFERS,
	 .desc = "Memory data transfers"
	},
	{.name = "UNC_DMC_CYCLES",
	 .modmsk = ARMV8_ATTRS,
	 .code = DMC_EVENT_COUNT_CYCLES,
	 .desc = "Clocks at the DMC clock rate"
	}
};

#define ARM_TX2_CORE_DMC_COUNT	(sizeof(arm_thunderx2_unc_dmc_pe)/sizeof(arm_entry_t))

static const arm_entry_t arm_thunderx2_unc_ccpi_pe[]={
	{.name = "UNC_CCPI_REQ",
	 .modmsk = ARMV8_ATTRS,
	 .code = CCPI2_EVENT_REQ_PKT_SENT,
	 .desc = "Request packets sent from this node"
	},
	{.name = "UNC_CCPI_SNOOP",
	 .modmsk = ARMV8_ATTRS,
	 .code = CCPI2_EVENT_SNOOP_PKT_SENT,
	 .desc = "Snoop packets sent from this node"
	},
	{.name = "UNC_CCPI_DATA",
	 .modmsk = ARMV8_ATTRS,
	 .code = CCPI2_EVENT_DATA_PKT_SENT ,
	 .desc = "Data packets sent from this node"
	},
	{.name = "UNC_CCPI_GIC",
	 .modmsk = ARMV8_ATTRS,
	 .code = CCPI2_EVENT_GIC_PKT_SENT,
	 .desc = "Interrupt related packets sent from this node"
	}
};

#define ARM_TX2_CORE_CCPI_COUNT	(sizeof(arm_thunderx2_unc_ccpi_pe)/sizeof(arm_entry_t))

static const arm_entry_t arm_thunderx2_unc_llc_pe[]={
	{.name = "UNC_LLC_READ",
	 .modmsk = ARMV8_ATTRS,
	 .code = L3_EVENT_READ_REQ,
	 .desc = "Read requests to LLC"
	},
	{.name = "UNC_LLC_EVICT",
	 .modmsk = ARMV8_ATTRS,
	 .code = L3_EVENT_EVICT_REQ,
	 .desc = "Evict requests to LLC"
	},
	{.name = "UNC_LLC_READ_HIT",
	 .modmsk = ARMV8_ATTRS,
	 .code = L3_EVENT_READ_HIT,
	 .desc = "Read requests to LLC which hit"
	},
	{.name = "UNC_LLC_WB",
	 .modmsk = ARMV8_ATTRS,
	 .code = L3_EVENT_WRITEBACK_REQ,
	 .desc = "Writeback requests to LLC"
	}
};

#define ARM_TX2_CORE_LLC_COUNT	(sizeof(arm_thunderx2_unc_llc_pe)/sizeof(arm_entry_t))
