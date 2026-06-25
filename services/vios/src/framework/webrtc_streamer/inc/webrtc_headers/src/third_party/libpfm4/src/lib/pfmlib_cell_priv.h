/*
 * Copyright (c) 2007 TOSHIBA CORPORATION based on code from
 * Copyright (c) 2001-2006 Hewlett-Packard Development Company, L.P.
 * Contributed by Stephane Eranian <eranian@hpl.hp.com>
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
#ifndef __PFMLIB_CELL_PRIV_H__
#define __PFMLIB_CELL_PRIV_H__

#define PFM_CELL_PME_FREQ_PPU_MFC	0
#define PFM_CELL_PME_FREQ_SPU		1
#define PFM_CELL_PME_FREQ_HALF		2

typedef struct {
	char			*pme_name;	/* event name */
	char			*pme_desc;	/* event description */
	unsigned long long	pme_code;	/* event code */
	unsigned int		pme_type;	/* count type */
	unsigned int		pme_freq;	/* debug_bus_control's frequency value */
	unsigned int		pme_enable_word;
} pme_cell_entry_t;

/* PMC register */
#define REG_PM0_CONTROL		0x0000
#define REG_PM1_CONTROL		0x0001
#define REG_PM2_CONTROL		0x0002
#define REG_PM3_CONTROL		0x0003
#define REG_PM4_CONTROL		0x0004
#define REG_PM5_CONTROL		0x0005
#define REG_PM6_CONTROL		0x0006
#define REG_PM7_CONTROL		0x0007

#define REG_PM0_EVENT		0x0008
#define REG_PM1_EVENT		0x0009
#define REG_PM2_EVENT		0x000A
#define REG_PM3_EVENT		0x000B
#define REG_PM4_EVENT		0x000C
#define REG_PM5_EVENT		0x000D
#define REG_PM6_EVENT		0x000E
#define REG_PM7_EVENT		0x000F

#define REG_GROUP_CONTROL	0x0010
#define REG_DEBUG_BUS_CONTROL	0x0011
#define REG_TRACE_ADDRESS	0x0012
#define REG_EXT_TRACE_TIMER	0x0013
#define REG_PM_STATUS		0x0014
#define REG_PM_CONTROL		0x0015
#define REG_PM_INTERVAL		0x0016
#define REG_PM_START_STOP	0x0017

#define NONE_SIGNAL		0x0000
#define SIGNAL_SPU		41
#define SIGNAL_SPU_TRIGGER	42
#define SIGNAL_SPU_EVENT	43

#define COUNT_TYPE_BOTH_TYPE		1
#define COUNT_TYPE_CUMULATIVE_LEN	2
#define COUNT_TYPE_OCCURRENCE		3
#define COUNT_TYPE_MULTI_CYCLE		4
#define COUNT_TYPE_SINGLE_CYCLE		5

#define WORD_0_ONLY	1	/* 0001 */
#define WORD_2_ONLY	4	/* 0100 */
#define WORD_0_AND_1	3	/* 0011 */
#define WORD_0_AND_2	5	/* 0101 */
#define WORD_NONE	0

#endif /* __PFMLIB_CELL_PRIV_H__ */
