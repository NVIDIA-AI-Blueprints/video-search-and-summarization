/* Power Torrent PMU event codes */

#ifndef __POWER_TORRENT_EVENTS_H__
#define __POWER_TORRENT_EVENTS_H__

/* PRELIMINARY EVENT ENCODING
 * 0x0000_0000 - 0x00FF_FFFF = PowerPC core events
 * 0x0100_0000 - 0x01FF_FFFF = Torrent events
 * 0x0200_0000 - 0xFFFF_FFFF = reserved
 * For Torrent events:
 * Reserve encodings 0x0..0x00FF_FFFF for core PowerPC events.
 * For Torrent events
 *    0x00F0_0000 = Torrent PMU id
 *    0x000F_0000 = PMU unit number (e.g. 0 for MCD0, 1 for MCD1)
 *    0x0000_FF00 = virtual counter number (unused on MCD)
 *    0x0000_00FF = PMC mux value (unused on Util, MMU, CAU)
 * (Note that some of these fields are wider than necessary)
 *
 * The upper bits 0xFFFF_FFFF_0000_0000 are reserved for attribute
 * fields.
 */

#define PMU_SPACE_MASK		0xFF000000
#define POWERPC_CORE_SPACE	0x00000000
#define TORRENT_SPACE		0x01000000
#define IS_CORE_EVENT(x)	((x & PMU_SPACE_MASK) == POWERPC_CORE_SPACE)
#define IS_TORRENT_EVENT(x)	((x & PMU_SPACE_MASK) == TORRENT_SPACE)
#define TORRENT_PMU_SHIFT	20
#define TORRENT_PMU_MASK	(0xF << TORRENT_PMU_SHIFT)
#define TORRENT_PMU_GET(x)	((x & TORRENT_PMU_MASK) >> TORRENT_PMU_SHIFT)
#define TORRENT_UNIT_SHIFT	16
#define TORRENT_UNIT_MASK	(0xF << TORRENT_UNIT_SHIFT)
#define TORRENT_UNIT_GET(x)	((x & TORRENT_UNIT_MASK) >> TORRENT_UNIT_SHIFT)
#define TORRENT_VIRT_CTR_SHIFT	8
#define TORRENT_VIRT_CTR_MASK	(0xFF << TORRENT_VIRT_CTR_SHIFT)
#define TORRENT_VIRT_CTR_GET(x)	((x & TORRENT_VIRT_CTR_MASK) >> TORRENT_VIRT_CTR_SHIFT)
#define TORRENT_MUX_SHIFT	0
#define TORRENT_MUX_MASK	0xFF
#define TORRENT_MUX_GET(x)	((x & TORRENT_MUX_MASK) >> TORRENT_MUX_SHIFT)

#define TORRENT_PBUS_WXYZ_ID	0x0
#define TORRENT_PBUS_LL_ID	0x1
#define TORRENT_PBUS_MCD_ID	0x2
#define TORRENT_PBUS_UTIL_ID	0x3
#define TORRENT_MMU_ID		0x4
#define TORRENT_CAU_ID		0x5
#define TORRENT_LAST_ID		(TORRENT_CAU_ID)
#define TORRENT_NUM_PMU_TYPES	(TORRENT_LAST_ID + 1)

/* TORRENT_DEVEL_NUM_PMU_TYPES is so that we don't try to call functions in
 * PMUs which are not currently supported.  When all Torrent PMUs are
 * supported, we NEED to remove this definition and replace the usages of it
 * with TORRENT_NUM_PMU_TYPES.
 */
#define TORRENT_DEVEL_NUM_PMU_TYPES	(TORRENT_PBUS_WXYZ_ID + 1)

#define TORRENT_PMU(pmu)	(TORRENT_SPACE | \
				TORRENT_##pmu##_ID << TORRENT_PMU_SHIFT)

#define TORRENT_PBUS_WXYZ	TORRENT_PMU(PBUS_WXYZ)
#define TORRENT_PBUS_LL		TORRENT_PMU(PBUS_LL)
#define TORRENT_PBUS_MCD	TORRENT_PMU(PBUS_MCD)
#define TORRENT_PBUS_UTIL	TORRENT_PMU(PBUS_UTIL)
#define TORRENT_MMU		TORRENT_PMU(MMU)
#define TORRENT_CAU		TORRENT_PMU(CAU)


#define COUNTER_W		(0 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_X		(1 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_Y		(2 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_Z		(3 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL0		(0 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL1		(1 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL2		(2 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL3		(3 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL4		(4 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL5		(5 << TORRENT_VIRT_CTR_SHIFT)
#define COUNTER_LL6		(6 << TORRENT_VIRT_CTR_SHIFT)


/* Attributes */

#define TORRENT_ATTR_MCD_TYPE_SHIFT	32
#define TORRENT_ATTR_MCD_TYPE_MASK	(0x3ULL << TORRENT_ATTR_MCD_TYPE_SHIFT)
#define TORRENT_ATTR_UTIL_SEL_SHIFT	32
#define TORRENT_ATTR_UTIL_SEL_MASK	(0x3ULL << TORRENT_ATTR_UTIL_SEL_SHIFT)
#define TORRENT_ATTR_UTIL_CMP_SHIFT	34
#define TORRENT_ATTR_UTIL_CMP_MASK	(0x1FULL << TORRENT_ATTR_UTIL_CMP_SHIFT)

static const pme_torrent_entry_t torrent_pe[] = {
	{
		.pme_name = "PM_PBUS_W_DISABLED",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x0,
		.pme_desc = "The W Link event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_W_IN_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x1,
		.pme_desc = "Bus cycles that the W Link \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_W_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the W Link \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_W_IN_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x3,
		.pme_desc = "Bus cycles that the W Link \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_W_OUT_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x5,
		.pme_desc = "Bus cycles that the W Link \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_W_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the W Link \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_W_OUT_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_W | 0x7,
		.pme_desc = "Bus cycles that the W Link \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_X_DISABLED",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x0,
		.pme_desc = "The X Link event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_X_IN_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x1,
		.pme_desc = "Bus cycles that the X Link \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_X_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the X Link \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_X_IN_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x3,
		.pme_desc = "Bus cycles that the X Link \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_X_OUT_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x5,
		.pme_desc = "Bus cycles that the X Link \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_X_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the X Link \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_X_OUT_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_X | 0x7,
		.pme_desc = "Bus cycles that the X Link \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_Y_DISABLED",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x0,
		.pme_desc = "The Y Link event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_Y_IN_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x1,
		.pme_desc = "Bus cycles that the Y Link \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_Y_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Y Link \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_Y_IN_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x3,
		.pme_desc = "Bus cycles that the Y Link \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_Y_OUT_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x5,
		.pme_desc = "Bus cycles that the Y Link \"out\" channel is idle",
	},
	{
		.pme_name = "PM_PBUS_Y_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Y Link \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_Y_OUT_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Y | 0x7,
		.pme_desc = "Bus cycles that the W Link \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_Z_DISABLED",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x0,
		.pme_desc = "The Z Link event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_Z_IN_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x1,
		.pme_desc = "Bus cycles that the Z Link \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_Z_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Z Link \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_Z_IN_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x3,
		.pme_desc = "Bus cycles that the Z Link \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_Z_OUT_IDLE",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x5,
		.pme_desc = "Bus cycles that the Z Link \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_Z_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Z Link \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_Z_OUT_DATA",
		.pme_code = TORRENT_PBUS_WXYZ | COUNTER_Z | 0x7,
		.pme_desc = "Bus cycles that the Z Link \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL0_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x0,
		.pme_desc = "The Local Link 0 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL0_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 0 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL0_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 0 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL0_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 0 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL0_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 0 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL0_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 0 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL0_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 0 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL0_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 0 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL0_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL0 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 0 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL1_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x0,
		.pme_desc = "The Local Link 1 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL1_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 1 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL1_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 1 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL1_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 1 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL1_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 1 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL1_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 1 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL1_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 1 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL1_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 1 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL1_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL1 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 1 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL2_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x0,
		.pme_desc = "The Local Link 2 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL2_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 2 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL2_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 2 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL2_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 2 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL2_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 2 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL2_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 2 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL2_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 2 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL2_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 2 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL2_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL2 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 2 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL3_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x0,
		.pme_desc = "The Local Link 3 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL3_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 3 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL3_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 3 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL3_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 3 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL3_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 3 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL3_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 3 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL3_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 3 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL3_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 3 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL3_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL3 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 3 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL4_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x0,
		.pme_desc = "The Local Link 4 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL4_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 4 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL4_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 4 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL4_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 4 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL4_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 4 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL4_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 4 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL4_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 4 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL4_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 4 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL4_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL4 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 4 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL5_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x0,
		.pme_desc = "The Local Link 5 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL5_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 5 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL5_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 5 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL5_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 5 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL5_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 5 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL5_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 5 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL5_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 5 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL5_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 5 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL5_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL5 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 5 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL6_DISABLED",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x0,
		.pme_desc = "The Local Link 6 event counter is disabled"
	},
	{
		.pme_name = "PM_PBUS_LL6_IN_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x1,
		.pme_desc = "Bus cycles that the Local Link 6 \"in\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL6_IN_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x2,
		.pme_desc = "Number of commands, partial responses, and combined responses received on the Local Link 6 \"in\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL6_IN_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x3,
		.pme_desc = "Bus cycles that the Local Link 6 \"in\" channel is receiving data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL6_OUT_IDLE",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x5,
		.pme_desc = "Bus cycles that the Local Link 6 \"out\" channel is idle"
	},
	{
		.pme_name = "PM_PBUS_LL6_OUT_CMDRSP",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x6,
		.pme_desc = "Number of commands, partial responses, and combined responses sent on the Local Link 6 \"out\" channel (Note: multiple events can occur in one cycle)"
	},
	{
		.pme_name = "PM_PBUS_LL6_OUT_DATA",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x7,
		.pme_desc = "Bus cycles that the Local Link 6 \"out\" channel is sending data or a data header"
	},
	{
		.pme_name = "PM_PBUS_LL6_IN_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0x9,
		.pme_desc = "Bus cycles that the Local Link 6 \"in\" channel is receiving ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_LL6_OUT_ISR",
		.pme_code = TORRENT_PBUS_LL | COUNTER_LL6 | 0xd,
		.pme_desc = "Bus cycles that the Local Link 6 \"out\" channel is sending ISR data or an ISR data header"
	},
	{
		.pme_name = "PM_PBUS_MCD0_PROBE_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x00,
		.pme_desc = "cl_probe command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_PROBE_CRESP_GOOD",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x01,
		.pme_desc = "cResp for a cl_probe was addr_ack_done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_PROBE_CRESP_RETRY",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x02,
		.pme_desc = "cResp for a cl_probe was rty_sp or addr_error or unexpected cResp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_FLUSH1_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x03,
		.pme_desc = "dcbfk command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_FLUSH0_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x04,
		.pme_desc = "dcbf command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_BKILL_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x05,
		.pme_desc = "bkill command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_FLUSH1_GOOD_COMP",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x06,
		.pme_desc = "cResp for a dcbfk was addr_ack_done and no collision",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_FLUSH1_COLLISION",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x07,
		.pme_desc = "dcbfk had a collision",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_FLUSH1_BAD_CRESP",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x08,
		.pme_desc = "cResp for a dcbfk was rty_sp or fl_addr_ack_bk_sp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_FLUSH0_CRESP_RETRY",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x09,
		.pme_desc = "cResp for a dcbf was rty_sp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_BKILL_CRESP_RETRY",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x0A,
		.pme_desc = "cResp for a bkill was rty_sp or fl_addr_ack_bk_sp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_HIT",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x0B,
		.pme_desc = "a reflected command got a hit",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_MISS",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x0C,
		.pme_desc = "a reflected command got a miss",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_HIT_MD",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x0D,
		.pme_desc = "a reflected command got a hit in the main directory",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_HIT_NE",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x0E,
		.pme_desc = "a reflected command got a hit in the new entry buffer",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_HIT_CO",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x0F,
		.pme_desc = "a reflected command got a hit in the castout buffer",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_MISS_CREATE",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x10,
		.pme_desc = "a reflected command with a miss should create an entry",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RCMD_MISS_CREATED",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x11,
		.pme_desc = "a new entry was created",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RTY_DINC",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x12,
		.pme_desc = "MCD responded rty_dinc",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_RTY_FULL",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x13,
		.pme_desc = "MCD responded rty_lpc",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_BK_RTY",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x14,
		.pme_desc = "MCD responded with a master retry (rty_other or rty_lost_claim)",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_NE_FULL",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x15,
		.pme_desc = "The new entry buffer is full",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_DEMAND_CASTOUT",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x16,
		.pme_desc = "A demand castout was done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_OTHER_CASTOUT",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x17,
		.pme_desc = "A non-demand castout was done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_CASTOUT",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x18,
		.pme_desc = "A castout was done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_CO_MOVE",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x19,
		.pme_desc = "A castout entry was moved to the main directory",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_NE_MOVE",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x1A,
		.pme_desc = "A new entry movement was processed",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_PAGE_CREATE",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x1B,
		.pme_desc = "A new entry movement created a page (got a miss)",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_NE_MOVE_MERGE",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x1C,
		.pme_desc = "A new entry movement merged with an existing page (got a hit)",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_NE_MOVE_ABORT_FLUSH",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x1D,
		.pme_desc = "A new entry movement was aborted due to flush in progress",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_NE_MOVE_ABORT_COQ",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x1E,
		.pme_desc = "A new entry movement was aborted due to castout buffer full",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_EM_HOLDOFF",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x1F,
		.pme_desc = "An entry movement was held off",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD0_EMQ_NOT_MT",
		.pme_code = TORRENT_PBUS_MCD | 0 << TORRENT_UNIT_SHIFT | 0x21,
		.pme_desc = "The entry movement queue is not empty",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_PROBE_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x00,
		.pme_desc = "cl_probe command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_PROBE_CRESP_GOOD",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x01,
		.pme_desc = "cResp for a cl_probe was addr_ack_done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_PROBE_CRESP_RETRY",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x02,
		.pme_desc = "cResp for a cl_probe was rty_sp or addr_error or unexpected cResp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_FLUSH1_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x03,
		.pme_desc = "dcbfk command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_FLUSH0_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x04,
		.pme_desc = "dcbf command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_BKILL_ISSUED",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x05,
		.pme_desc = "bkill command issued",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_FLUSH1_GOOD_COMP",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x06,
		.pme_desc = "cResp for a dcbfk was addr_ack_done and no collision",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_FLUSH1_COLLISION",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x07,
		.pme_desc = "dcbfk had a collision",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_FLUSH1_BAD_CRESP",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x08,
		.pme_desc = "cResp for a dcbfk was rty_sp or fl_addr_ack_bk_sp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_FLUSH0_CRESP_RETRY",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x09,
		.pme_desc = "cResp for a dcbf was rty_sp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_BKILL_CRESP_RETRY",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x0A,
		.pme_desc = "cResp for a bkill was rty_sp or fl_addr_ack_bk_sp",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_HIT",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x0B,
		.pme_desc = "a reflected command got a hit",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_MISS",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x0C,
		.pme_desc = "a reflected command got a miss",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_HIT_MD",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x0D,
		.pme_desc = "a reflected command got a hit in the main directory",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_HIT_NE",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x0E,
		.pme_desc = "a reflected command got a hit in the new entry buffer",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_HIT_CO",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x0F,
		.pme_desc = "a reflected command got a hit in the castout buffer",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_MISS_CREATE",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x10,
		.pme_desc = "a reflected command with a miss should create an entry",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RCMD_MISS_CREATED",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x11,
		.pme_desc = "a new entry was created",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RTY_DINC",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x12,
		.pme_desc = "MCD responded rty_dinc",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_RTY_FULL",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x13,
		.pme_desc = "MCD responded rty_lpc",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_BK_RTY",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x14,
		.pme_desc = "MCD responded with a master retry (rty_other or rty_lost_claim)",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_NE_FULL",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x15,
		.pme_desc = "The new entry buffer is full",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_DEMAND_CASTOUT",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x16,
		.pme_desc = "A demand castout was done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_OTHER_CASTOUT",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x17,
		.pme_desc = "A non-demand castout was done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_CASTOUT",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x18,
		.pme_desc = "A castout was done",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_CO_MOVE",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x19,
		.pme_desc = "A castout entry was moved to the main directory",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_NE_MOVE",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x1A,
		.pme_desc = "A new entry movement was processed",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_PAGE_CREATE",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x1B,
		.pme_desc = "A new entry movement created a page (got a miss)",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_NE_MOVE_MERGE",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x1C,
		.pme_desc = "A new entry movement merged with an existing page (got a hit)",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_NE_MOVE_ABORT_FLUSH",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x1D,
		.pme_desc = "A new entry movement was aborted due to flush in progress",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_NE_MOVE_ABORT_COQ",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x1E,
		.pme_desc = "A new entry movement was aborted due to castout buffer full",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_EM_HOLDOFF",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x1F,
		.pme_desc = "An entry movement was held off",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_MCD1_EMQ_NOT_MT",
		.pme_code = TORRENT_PBUS_MCD | 1 << TORRENT_UNIT_SHIFT | 0x21,
		.pme_desc = "The entry movement queue is not empty",
		.pme_modmsk = _TORRENT_ATTR_MCD
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_NM_HI_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x0 << TORRENT_VIRT_CTR_SHIFT | 0x0,
		.pme_desc = "Node Master High Threshold Counter",
		.pme_modmsk = _TORRENT_ATTR_UTIL_HI
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_NM_LO_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x1 << TORRENT_VIRT_CTR_SHIFT | 0x0,
		.pme_desc = "Node Master Low Threshold Counter",
		.pme_modmsk = _TORRENT_ATTR_UTIL_LO
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_LM_HI_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x2 << TORRENT_VIRT_CTR_SHIFT | 0x0,
		.pme_desc = "Local Master High Threshold Counter",
		.pme_modmsk = _TORRENT_ATTR_UTIL_HI
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_LM_LO_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x3 << TORRENT_VIRT_CTR_SHIFT | 0x0,
		.pme_desc = "Local Master Low Threshold Counter",
		.pme_modmsk = _TORRENT_ATTR_UTIL_LO
	},
	{
		.pme_name = "PM_PBUS_UTIL_NODE_MASTER_PUMPS",
		.pme_code = TORRENT_PBUS_UTIL | 0x0 << TORRENT_VIRT_CTR_SHIFT | 0x1,
		.pme_desc = "Node Master Pumps"
	},
	{
		.pme_name = "PM_PBUS_UTIL_LOCAL_MASTER_PUMPS",
		.pme_code = TORRENT_PBUS_UTIL | 0x1 << TORRENT_VIRT_CTR_SHIFT | 0x1,
		.pme_desc = "Local Master Pumps"
	},
	{
		.pme_name = "PM_PBUS_UTIL_RETRY_NODE_MASTER_PUMPS",
		.pme_code = TORRENT_PBUS_UTIL | 0x2 << TORRENT_VIRT_CTR_SHIFT | 0x1,
		.pme_desc = "Retry Node Master Pumps"
	},
	{
		.pme_name = "PM_PBUS_UTIL_RETRY_LOCAL_MASTER_PUMPS",
		.pme_code = TORRENT_PBUS_UTIL | 0x3 << TORRENT_VIRT_CTR_SHIFT | 0x1,
		.pme_desc = "Retry Local Master Pumps"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_RCMD_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x4 << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "rCmd Activity Counter"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_INTDATA_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x5 << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "Internal Data Counter"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_EXTDATSND_W_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x6 << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "External Data Send Activity Counter for WXYZ links"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_EXTDATRCV_W_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x7 << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "External Data Receive Activity Counter for WXYZ links"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_EXTDATSND_LL_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x8 << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "External Data Send Activity Counter for LL links"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_EXTDATRCV_LL_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0x9 << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "External Data Receive Activity Counter for LL links"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_EXTDAT_W_LL_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0xA << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "External Data Activity Counter from WXYZ to LL links"
	},
	{
		.pme_name = "PM_PBUS_UTIL_PB_APM_EXTDAT_LL_W_CNT",
		.pme_code = TORRENT_PBUS_UTIL | 0xB << TORRENT_VIRT_CTR_SHIFT,
		.pme_desc = "External Data Activity Counter from LL to WXYZ links"
	},
	{
		.pme_name = "PM_MMU_G_MMCHIT",
		.pme_code = TORRENT_MMU | (0 << TORRENT_VIRT_CTR_SHIFT),
		.pme_desc = "Memory Management Cache Hit Counter Register"
	},
	{
		.pme_name = "PM_MMU_G_MMCMIS",
		.pme_code = TORRENT_MMU | (1 << TORRENT_VIRT_CTR_SHIFT),
		.pme_desc = "Memory Management Cache Miss Counter Register"
	},
	{
		.pme_name = "PM_MMU_G_MMATHIT",
		.pme_code = TORRENT_MMU | (2 << TORRENT_VIRT_CTR_SHIFT),
		.pme_desc = "Memory Management AT Cache Hit Counter Register"
	},
	{
		.pme_name = "PM_MMU_G_MMATMIS",
		.pme_code = TORRENT_MMU | (3 << TORRENT_VIRT_CTR_SHIFT),
		.pme_desc = "Memory Management AT Cache Miss Counter Register"
	},
	{
		.pme_name = "PM_CAU_CYCLES_WAITING_ON_A_CREDIT",
		.pme_code = TORRENT_CAU | 0,
		.pme_desc = "Count of cycles spent waiting on a credit.  Increments whenever any index has a packet to send, but nothing (from any index) can be sent."
	},
};
#define PME_TORRENT_EVENT_COUNT (sizeof(torrent_pe) / sizeof(pme_torrent_entry_t))
#endif
