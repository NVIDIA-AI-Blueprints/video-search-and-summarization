/*
 * Copyright (c) 2004-2006 Hewlett-Packard Development Company, L.P.
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
 *
 * This file is part of libpfm, a performance monitoring support library for
 * applications on Linux.
 */
#ifndef __PFMLIB_AMD64_PRIV_H__
#define __PFMLIB_AMD64_PRIV_H__

#define AMD64_MAX_GRP		4 /* must be < 32 (int) */

typedef struct {
	const char		*uname; /* unit mask name */
	const char		*udesc; /* event/umask description */
	unsigned int		ucode;  /* unit mask code */
	unsigned int		uflags; /* unit mask flags */
	unsigned int		grpid;	/* unit mask group id */
} amd64_umask_t;

typedef struct {
	const char		*name;	/* event name */
	const char		*desc;	/* event description */
	const amd64_umask_t	*umasks;/* list of umasks */
	unsigned int		code; 	/* event code */
	unsigned int		numasks;/* number of umasks */
	unsigned int		flags;	/* flags */
	unsigned int		modmsk;	/* modifiers bitmask */
	unsigned int		ngrp;	/* number of unit masks groups */
} amd64_entry_t;

/*
 * we keep an internal revision type to avoid
 * dealing with arbitrarily large pfm_pmu_t
 * which would not fit into the 8 bits reserved
 * in amd64_entry_t.flags or amd64_umask_t.flags
 */
typedef enum {
        AMD64_CPU_UN = 0,
        AMD64_K7,
        AMD64_K8_REV_B,
        AMD64_K8_REV_C,
        AMD64_K8_REV_D,
        AMD64_K8_REV_E,
        AMD64_K8_REV_F,
        AMD64_K8_REV_G,
        AMD64_FAM10H_REV_B,
        AMD64_FAM10H_REV_C,
        AMD64_FAM10H_REV_D,
        AMD64_FAM11H,
        AMD64_FAM12H, /* first with Host/Guest filtering */

        AMD64_FAM14H_REV_B,
        AMD64_FAM15H,
        AMD64_FAM16H,
        AMD64_FAM17H,
        AMD64_FAM19H,
        AMD64_FAM1AH,
} amd64_rev_t;
#define AMD64_FAM10H AMD64_FAM10H_REV_B

typedef struct {
        pfm_pmu_t		revision;
        int             	family; /* 0 means nothing detected yet */
        int             	model;
        int             	stepping;
} pfm_amd64_config_t;

extern pfm_amd64_config_t pfm_amd64_cfg;

/* 
 * flags values (bottom 8 bits only)
 * bits 00-07: flags
 * bits 08-15: from revision
 * bits 16-23: till revision
 */
#define AMD64_FROM_REV(rev)	((rev)<<8)
#define AMD64_TILL_REV(rev)	((rev)<<16)
#define AMD64_NOT_SUPP		0x1ff00

#define AMD64_FL_NCOMBO        		0x01 /* unit mask can be combined */
#define AMD64_FL_IBSFE			0x02 /* IBS fetch */
#define AMD64_FL_IBSOP			0x04 /* IBS op */
#define AMD64_FL_DFL			0x08 /* unit mask is default choice */
#define AMD64_FL_OMIT			0x10 /* umask can be omitted */

#define AMD64_FL_TILL_K8_REV_C		AMD64_TILL_REV(AMD64_K8_REV_C)
#define AMD64_FL_K8_REV_D		AMD64_FROM_REV(AMD64_K8_REV_D)
#define AMD64_FL_K8_REV_E		AMD64_FROM_REV(AMD64_K8_REV_E)
#define AMD64_FL_TILL_K8_REV_E		AMD64_TILL_REV(AMD64_K8_REV_E)
#define AMD64_FL_K8_REV_F		AMD64_FROM_REV(AMD64_K8_REV_F)
#define AMD64_FL_TILL_FAM10H_REV_B	AMD64_TILL_REV(AMD64_FAM10H_REV_B)
#define AMD64_FL_FAM10H_REV_C		AMD64_FROM_REV(AMD64_FAM10H_REV_C)
#define AMD64_FL_TILL_FAM10H_REV_C	AMD64_TILL_REV(AMD64_FAM10H_REV_C)
#define AMD64_FL_FAM10H_REV_D		AMD64_FROM_REV(AMD64_FAM10H_REV_D)

#define AMD64_ATTR_K	0
#define AMD64_ATTR_U	1
#define AMD64_ATTR_E	2
#define AMD64_ATTR_I	3
#define AMD64_ATTR_C	4
#define AMD64_ATTR_H	5
#define AMD64_ATTR_G	6

#define _AMD64_ATTR_U  (1 << AMD64_ATTR_U)
#define _AMD64_ATTR_K  (1 << AMD64_ATTR_K)
#define _AMD64_ATTR_I  (1 << AMD64_ATTR_I)
#define _AMD64_ATTR_E  (1 << AMD64_ATTR_E)
#define _AMD64_ATTR_C  (1 << AMD64_ATTR_C)
#define _AMD64_ATTR_H  (1 << AMD64_ATTR_H)
#define _AMD64_ATTR_G  (1 << AMD64_ATTR_G)

#define AMD64_BASIC_ATTRS \
	(_AMD64_ATTR_I|_AMD64_ATTR_E|_AMD64_ATTR_C|_AMD64_ATTR_U|_AMD64_ATTR_K)

#define AMD64_K8_ATTRS			(AMD64_BASIC_ATTRS)
#define AMD64_FAM10H_ATTRS		(AMD64_BASIC_ATTRS|_AMD64_ATTR_H|_AMD64_ATTR_G)
#define AMD64_FAM12H_ATTRS		AMD64_FAM10H_ATTRS
#define AMD64_FAM14H_ATTRS		AMD64_FAM10H_ATTRS
#define AMD64_FAM15H_ATTRS		AMD64_FAM10H_ATTRS
#define AMD64_FAM17H_ATTRS		AMD64_FAM10H_ATTRS
#define AMD64_FAM19H_ATTRS		AMD64_FAM10H_ATTRS
#define AMD64_FAM1AH_ATTRS		AMD64_FAM10H_ATTRS

#define AMD64_FAM10H_PLM	(PFM_PLM0|PFM_PLM3|PFM_PLMH)
#define AMD64_K7_PLM		(PFM_PLM0|PFM_PLM3)

/*
 * AMD64 MSR definitions
 */
typedef union {
	uint64_t val;				/* complete register value */
	struct {
		uint64_t sel_event_mask:8;	/* event mask */
		uint64_t sel_unit_mask:8;	/* unit mask */
		uint64_t sel_usr:1;		/* user level */
		uint64_t sel_os:1;		/* system level */
		uint64_t sel_edge:1;		/* edge detec */
		uint64_t sel_pc:1;		/* pin control */
		uint64_t sel_int:1;		/* enable APIC intr */
		uint64_t sel_res1:1;		/* reserved */
		uint64_t sel_en:1;		/* enable */
		uint64_t sel_inv:1;		/* invert counter mask */
		uint64_t sel_cnt_mask:8;	/* counter mask */
		uint64_t sel_event_mask2:4;	/* 10h only: event mask [11:8] */
		uint64_t sel_res2:4;		/* reserved */
		uint64_t sel_guest:1;		/* 10h only: guest only counter */
		uint64_t sel_host:1;		/* 10h only: host only counter */
		uint64_t sel_res3:22;		/* reserved */
	} perfsel;

	struct {
		uint64_t maxcnt:16;
		uint64_t cnt:16;
		uint64_t lat:16;
		uint64_t en:1;
		uint64_t val:1;
		uint64_t comp:1;
		uint64_t icmiss:1;
		uint64_t phyaddrvalid:1;
		uint64_t l1tlbpgsz:2;
		uint64_t l1tlbmiss:1;
		uint64_t l2tlbmiss:1;
		uint64_t randen:1;
		uint64_t reserved:6;
	} ibsfetch;
	struct {
		uint64_t maxcnt:16;
		uint64_t reserved1:1;
		uint64_t en:1;
		uint64_t val:1;
		uint64_t reserved2:45;
	} ibsop;
	struct { /* Zen3 L3 */
		uint64_t event:8;		/* event mask */
		uint64_t umask:8;		/* unit mask */
		uint64_t reserved1:6;		/* reserved */
		uint64_t en:1;			/* enable */
		uint64_t reserved2:19;		/* reserved */
		uint64_t core_id:3;		/* Core ID */
		uint64_t reserved3:1;		/* reserved */
		uint64_t en_all_slices:1;	/* enable all slices */
		uint64_t en_all_cores:1;	/* enable all cores */
		uint64_t slice_id:3;		/* Slice ID */
		uint64_t reserved4:5;		/* reserved */
		uint64_t thread_id:4;		/* reserved */
		uint64_t reserved5:4;		/* reserved */
	} l3;
} pfm_amd64_reg_t; /* MSR 0xc001000-0xc001003 */

/* let's define some handy shortcuts! */
#define sel_event_mask	perfsel.sel_event_mask
#define sel_unit_mask	perfsel.sel_unit_mask
#define sel_usr		perfsel.sel_usr
#define sel_os		perfsel.sel_os
#define sel_edge	perfsel.sel_edge
#define sel_pc		perfsel.sel_pc
#define sel_int		perfsel.sel_int
#define sel_en		perfsel.sel_en
#define sel_inv		perfsel.sel_inv
#define sel_cnt_mask	perfsel.sel_cnt_mask
#define sel_event_mask2 perfsel.sel_event_mask2
#define sel_guest	perfsel.sel_guest
#define sel_host	perfsel.sel_host

extern int pfm_amd64_get_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_amd64_get_event_first(void *this);
extern int pfm_amd64_get_event_next(void *this, int idx);
extern int pfm_amd64_event_is_valid(void *this, int idx);
extern int pfm_amd64_get_event_attr_info(void *this, int idx, int attr_idx, pfmlib_event_attr_info_t *info);
extern int pfm_amd64_get_event_info(void *this, int idx, pfm_event_info_t *info);
extern int pfm_amd64_validate_table(void *this, FILE *fp);
extern int pfm_amd64_detect(void *this);
extern const pfmlib_attr_desc_t amd64_mods[];
extern unsigned int pfm_amd64_get_event_nattrs(void *this, int pidx);
extern int pfm_amd64_get_num_events(void *this);

extern int pfm_amd64_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
extern void pfm_amd64_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);
extern void pfm_amd64_nb_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);
extern int pfm_amd64_family_detect(void *this);

static inline int pfm_amd64_supports_virt(pfmlib_pmu_t *pmu)
{
	return pmu->pmu_rev >= AMD64_FAM10H && pmu->pmu_rev != AMD64_FAM11H;
}

#endif /* __PFMLIB_AMD64_PRIV_H__ */
