/*
 * Copyright (c) 2010 University of Tennessee
 * Contributed by Vince Weaver <vweaver1@utk.edu>
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
#ifndef __PFMLIB_ARM_PRIV_H__
#define __PFMLIB_ARM_PRIV_H__

/*
 * This file contains the definitions used for ARM processors
 */


/*
 * event description
 */
typedef struct {
	const char			*name;	/* event name */
	const char			*desc;	/* event description */
	const char			*equiv;	/* aliased to that event */
	unsigned int			code; 	/* event code */
	unsigned int			modmsk; /* modifiers bitmask */
} arm_entry_t;

typedef union pfm_arm_reg {
	unsigned int val;			/* complete register value */
	struct {
		unsigned int sel:8;
		unsigned int reserved1:19;
		unsigned int excl_hyp:1;
		unsigned int reserved2:2;
		unsigned int excl_pl1:1;
		unsigned int excl_usr:1;
	} evtsel;
} pfm_arm_reg_t;

typedef struct {
	int init_cpuinfo_done;
} pfm_arm_config_t;

extern pfm_arm_config_t pfm_arm_cfg;

typedef struct {
	int impl;
	int arch;
	int part;
	/* if number of fields altered, update ARM_NUM_ATTR_FIELDS */
} arm_cpuid_t;
#define ARM_NUM_ATTR_FIELDS 3 /* number of fields on arm_cpuid_t */

extern int pfm_arm_detect(arm_cpuid_t *attr, arm_cpuid_t *match_attr);
extern int pfm_arm_get_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_arm_get_event_first(void *this);
extern int pfm_arm_get_event_next(void *this, int idx);
extern int pfm_arm_event_is_valid(void *this, int pidx);
extern int pfm_arm_validate_table(void *this, FILE *fp);
extern int pfm_arm_get_event_attr_info(void *this, int pidx, int attr_idx, pfmlib_event_attr_info_t *info);
extern int pfm_arm_get_event_info(void *this, int idx, pfm_event_info_t *info);
extern unsigned int pfm_arm_get_event_nattrs(void *this, int pidx);

extern void pfm_arm_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);
extern int pfm_arm_get_perf_encoding(void *this, pfmlib_event_desc_t *e);

#define ARM_ATTR_K	0 /* pl1 priv level */
#define ARM_ATTR_U	1 /* user priv level */
#define ARM_ATTR_HV	2 /* hypervisor priv level */

#define _ARM_ATTR_K	(1 << ARM_ATTR_K)
#define _ARM_ATTR_U	(1 << ARM_ATTR_U)
#define _ARM_ATTR_HV	(1 << ARM_ATTR_HV)

#define ARM_ATTR_PLM_ALL (_ARM_ATTR_K|_ARM_ATTR_U|_ARM_ATTR_HV)

#define ARMV7_A15_ATTRS	(_ARM_ATTR_K|_ARM_ATTR_U|_ARM_ATTR_HV)
#define ARMV7_A15_PLM	(PFM_PLM0|PFM_PLM3|PFM_PLMH)

#define ARMV7_A7_ATTRS	(_ARM_ATTR_K|_ARM_ATTR_U|_ARM_ATTR_HV)
#define ARMV7_A7_PLM	(PFM_PLM0|PFM_PLM3|PFM_PLMH)

#define ARMV8_ATTRS	(_ARM_ATTR_K|_ARM_ATTR_U|_ARM_ATTR_HV)
#define ARMV8_PLM	(PFM_PLM0|PFM_PLM3|PFM_PLMH)

#define ARMV9_ATTRS	(_ARM_ATTR_K|_ARM_ATTR_U|_ARM_ATTR_HV)
#define ARMV9_PLM	(PFM_PLM0|PFM_PLM3|PFM_PLMH)

static inline int
arm_has_plm(void *this, pfmlib_event_desc_t *e)
{
	const arm_entry_t *pe = this_pe(this);

	return pe[e->event].modmsk & ARM_ATTR_PLM_ALL;
}

#endif /* __PFMLIB_ARM_PRIV_H__ */
