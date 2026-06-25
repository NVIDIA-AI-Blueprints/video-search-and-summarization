/*
 * Copyright (c) 2002-2006 Hewlett-Packard Development Company, L.P.
 * Contributed by Stephane Eranian <eranian@hpl.hp.com>
 *
 * Copyright (c) 2022-2026, NVIDIA CORPORATION & AFFILIATES.
 * Contributed by John Linford <jlinford@nvidia.com>
 * Contributed by Thomas Makin <tmakin@nvidia.com>
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
#ifndef __PFMLIB_PRIV_H__
#define __PFMLIB_PRIV_H__
#include <perfmon/pfmlib.h>
#include <string.h>

#define PFM_PLM_ALL (PFM_PLM0|PFM_PLM1|PFM_PLM2|PFM_PLM3|PFM_PLMH)

#define PFMLIB_ATTR_DELIM	":."	/* event attribute delimiter possible */
#define PFMLIB_PMU_DELIM	"::"	/* pmu to event delimiter */
#define PFMLIB_EVENT_DELIM	", \t\n"/* event to event delimiters */

#define PFM_ATTR_I(y, d) { .name = (y), .type = PFM_ATTR_MOD_INTEGER, .desc = (d) }
#define PFM_ATTR_B(y, d) { .name = (y), .type = PFM_ATTR_MOD_BOOL, .desc = (d) }
#define PFM_ATTR_SKIP	 { .name = "" } /* entry not populated (skipped) */
#define PFM_ATTR_NULL	{ .name = NULL }

#define PFMLIB_EVT_MAX_NAME_LEN	256

/*
 * event identifier encoding:
 * bit 00-20 : event table specific index (2097152 possibilities)
 * bit 21-30 : PMU identifier (1024 possibilities)
 * bit 31    : reserved (to distinguish from a negative error code)
 */
#define PFMLIB_PMU_SHIFT	21
#define PFMLIB_PMU_MASK		0x3ff /* must fit PFM_PMU_MAX */
#define PFMLIB_PMU_PIDX_MASK	((1<< PFMLIB_PMU_SHIFT)-1)

typedef struct {
	const char	*name;	/* name */
	const char	*desc;	/* description */
	pfm_attr_t	type;	/* used to validate value (if any) */
} pfmlib_attr_desc_t;

typedef struct {
	const char		*name;	/* attribute symbolic name */
	const char		*desc;	/* attribute description */
	const char		*equiv;	/* attribute is equivalent to */
	size_t			size;	/* struct sizeof */
	uint64_t		code;	/* attribute code */
	pfm_attr_t		type;	/* attribute type */
	pfm_attr_ctrl_t		ctrl;	/* what is providing attr */

	uint64_t		idx;	/* attribute opaque index */

	struct {
		unsigned int    is_dfl:1;	 /* is default umask */
		unsigned int    is_precise:1;	 /* Intel X86: supports PEBS */
		unsigned int	is_speculative:2;/* count correct and wrong path occurrences */
		unsigned int	support_hw_smpl:1;/* can be recorded by hw buffer (Intel X86=EXTPEBS) */
		unsigned int	support_no_mods:1;/* attribute does not support modifiers (umask only) */
		unsigned int	reserved_bits:26;
	};
	union {
		uint64_t	dfl_val64;	/* default 64-bit value */
		const char	*dfl_str;	/* default string value */
		int		dfl_bool;	/* default boolean value */
		int		dfl_int;	/* default integer value */
	};
} pfmlib_event_attr_info_t;

/*
 * attribute description passed to model-specific layer
 */
typedef struct {
	int	id;			/* attribute index */
	union {
		uint64_t	ival;	/* integer value (incl. bool) */
		char		*sval;	/* string */
	};
} pfmlib_attr_t;

/*
 * must be big enough to hold all possible priv level attributes
 */
#define PFMLIB_MAX_ATTRS	64 /* max attributes per event desc */
#define PFMLIB_MAX_ENCODING	4  /* max encoding length */

/*
 * we add one entry to hold any raw umask users may specify
 * the last entry in pattrs[] hold that raw umask info
 */
#define PFMLIB_MAX_PATTRS	(PFMLIB_MAX_ATTRS+1)

struct pfmlib_pmu;
typedef struct {
	struct pfmlib_pmu	*pmu;				/* pmu */
	int			dfl_plm;			/* default priv level mask */
	int			event;				/* pidx */
	int			npattrs;			/* number of attrs in pattrs[] */
	int			nattrs;				/* number of attrs in attrs[] */
	pfm_os_t		osid;				/* OS API requested */
	int			count;				/* number of entries in codes[] */
	pfmlib_attr_t		attrs[PFMLIB_MAX_ATTRS];	/* list of requested attributes */

	pfmlib_event_attr_info_t *pattrs;			/* list of possible attributes */
	char			fstr[PFMLIB_EVT_MAX_NAME_LEN];	/* fully qualified event string */
	uint64_t		codes[PFMLIB_MAX_ENCODING];	/* event encoding */
	void			*os_data;
} pfmlib_event_desc_t;
#define modx(atdesc, a, z)	(atdesc[(a)].z)
#define attr(e, k)		((e)->pattrs + (e)->attrs[k].id)

typedef struct pfmlib_node {
	struct pfmlib_node *next;
	struct pfmlib_node *prev;
} pfmlib_node_t;

typedef struct pfmlib_pmu {
	const char 	*desc;			/* PMU description */
	const char 	*name;			/* pmu short name */
	const char	*perf_name;		/* (Linux optional): comma separated list of possible perf_events PMU names */
	pfmlib_node_t   node;			/* active list node */
	struct pfmlib_pmu *next_active;		/* active PMU link list */
	struct pfmlib_pmu *prev_active;		/* active PMU link list */
	pfm_pmu_t	pmu;			/* PMU model */
	int		pme_count;		/* number of events */
	int		max_encoding;		/* max number of uint64_t to encode an event */
	int		flags;			/* 16 LSB: common, 16 MSB: arch spec*/
	int		pmu_rev;		/* PMU model specific revision */
	int		num_cntrs;		/* number of generic counters */
	int		num_fixed_cntrs;	/* number of fixed counters */
	int		supported_plm;		/* supported priv levels */

	pfm_pmu_type_t	type;			/* PMU type */
	const void	*pe;			/* pointer to event table */

	const pfmlib_attr_desc_t *atdesc;	/* pointer to attrs table */

	const int	cpu_family;		/* cpu family number for detection */
	const int	*cpu_models;		/* cpu model numbers for detection (zero terminated) */

	int 		 (*pmu_detect)(void *this);
	int 		 (*pmu_init)(void *this);	/* optional */
	void		 (*pmu_terminate)(void *this); /* optional */
	int		 (*get_event_first)(void *this);
	int		 (*get_event_next)(void *this, int pidx);
	int		 (*get_event_info)(void *this, int pidx, pfm_event_info_t *info);
	unsigned int	 (*get_event_nattrs)(void *this, int pidx);
	int		 (*event_is_valid)(void *this, int pidx);
	int		 (*can_auto_encode)(void *this, int pidx, int uidx);

	int		 (*get_event_attr_info)(void *this, int pidx, int umask_idx, pfmlib_event_attr_info_t *info);
	int		 (*get_event_encoding[PFM_OS_MAX])(void *this, pfmlib_event_desc_t *e);

	void		 (*validate_pattrs[PFM_OS_MAX])(void *this, pfmlib_event_desc_t *e);
	int		 (*os_detect[PFM_OS_MAX])(void *this);
	int		 (*validate_table)(void *this, FILE *fp);
	/*
	 * optional callbacks
	 */
	int 		 (*get_num_events)(void *this);
	void		 (*display_reg)(void *this, pfmlib_event_desc_t *e, void *val);
	int 		 (*match_event)(void *this, pfmlib_event_desc_t *d, const char *e, const char *s);
} pfmlib_pmu_t;

typedef struct {
	const char			*name;
	const pfmlib_attr_desc_t	*atdesc;
	pfm_os_t			id;
	int				flags;
	int				(*detect)(void *this);
	int				(*get_os_attr_info)(void *this, pfmlib_event_desc_t *e);
	int				(*get_os_nattrs)(void *this, pfmlib_event_desc_t *e);
	int				(*encode)(void *this, const char *str, int dfl_plm, void *args);
} pfmlib_os_t;

#define PFMLIB_OS_FL_ACTIVATED	0x1	/* OS layer detected */

/*
 * pfmlib_pmu_t common flags (LSB 16 bits)
 */
#define PFMLIB_PMU_FL_INIT	0x1	/* PMU initialized correctly */
#define PFMLIB_PMU_FL_ACTIVE	0x2	/* PMU is initialized + detected on host */
#define PFMLIB_PMU_FL_RAW_UMASK	0x4	/* PMU supports PFM_ATTR_RAW_UMASKS */
#define PFMLIB_PMU_FL_ARCH_DFL	0x8	/* PMU is arch default */
#define PFMLIB_PMU_FL_NO_SMPL	0x10	/* PMU does not support sampling */
#define PFMLIB_PMU_FL_SPEC	0x20	/* PMU provides event speculation info */
#define PFMLIB_PMU_FL_DEPR	0x40	/* PMU model is deprecated */

typedef struct {
	int	initdone;
	int	initret; /* initial return value from pfm_initialize() */
	int	verbose;
	int	debug;
	int	inactive;
	char	*forced_pmu;
	char	*blacklist_pmus;
	char	*proc_cpuinfo; /* override /proc/cpuinfo with this file */
	FILE 	*fp;	/* verbose and debug file descriptor, default stderr or PFMLIB_DEBUG_STDOUT */
} pfmlib_config_t;	

#define PFMLIB_INITIALIZED()	(pfm_cfg.initdone && pfm_cfg.initret == PFM_SUCCESS)

extern pfmlib_config_t pfm_cfg;

extern void __pfm_vbprintf(const char *fmt,...);
extern void __pfm_dbprintf(const char *fmt,...);
extern void pfmlib_strconcat(char *str, size_t max, const char *fmt, ...);
extern int pfmlib_getl(char **buffer, size_t *len, FILE *fp);
extern void pfmlib_compact_pattrs(pfmlib_event_desc_t *e, int i);
#define evt_strcat(str, fmt, a...) pfmlib_strconcat(str, PFMLIB_EVT_MAX_NAME_LEN, fmt, a)

extern int pfmlib_parse_event(const char *event, pfmlib_event_desc_t *d);
extern int pfmlib_build_fstr(pfmlib_event_desc_t *e, char **fstr);
extern void pfmlib_sort_attr(pfmlib_event_desc_t *e);
extern pfmlib_pmu_t * pfmlib_get_pmu_by_type(pfm_pmu_type_t t);
extern void pfmlib_release_event(pfmlib_event_desc_t *e);

extern size_t pfmlib_check_struct(void *st, size_t usz, size_t refsz, size_t sz);

#ifdef CONFIG_PFMLIB_DEBUG
#define DPRINT(fmt, a...) \
	do { \
		__pfm_dbprintf("%s (%s.%d): " fmt, __FILE__, __func__, __LINE__, ## a); \
	} while (0)
#else
#define DPRINT(fmt, a...)
#endif

extern pfmlib_pmu_t montecito_support;
extern pfmlib_pmu_t itanium2_support;
extern pfmlib_pmu_t itanium_support;
extern pfmlib_pmu_t generic_ia64_support;
extern pfmlib_pmu_t amd64_k7_support;
extern pfmlib_pmu_t amd64_k8_revb_support;
extern pfmlib_pmu_t amd64_k8_revc_support;
extern pfmlib_pmu_t amd64_k8_revd_support;
extern pfmlib_pmu_t amd64_k8_reve_support;
extern pfmlib_pmu_t amd64_k8_revf_support;
extern pfmlib_pmu_t amd64_k8_revg_support;
extern pfmlib_pmu_t amd64_fam10h_barcelona_support;
extern pfmlib_pmu_t amd64_fam10h_shanghai_support;
extern pfmlib_pmu_t amd64_fam10h_istanbul_support;
extern pfmlib_pmu_t amd64_fam11h_turion_support;
extern pfmlib_pmu_t amd64_fam12h_llano_support;
extern pfmlib_pmu_t amd64_fam14h_bobcat_support;
extern pfmlib_pmu_t amd64_fam15h_interlagos_support;
extern pfmlib_pmu_t amd64_fam15h_nb_support;
extern pfmlib_pmu_t amd64_fam16h_support;
extern pfmlib_pmu_t amd64_fam17h_deprecated_support;
extern pfmlib_pmu_t amd64_fam17h_zen1_support;
extern pfmlib_pmu_t amd64_fam17h_zen2_support;
extern pfmlib_pmu_t amd64_fam19h_zen3_support;
extern pfmlib_pmu_t amd64_fam19h_zen4_support;
extern pfmlib_pmu_t amd64_fam19h_zen3_l3_support;
extern pfmlib_pmu_t amd64_fam1ah_zen5_support;
extern pfmlib_pmu_t amd64_fam1ah_zen5_l3_support;
extern pfmlib_pmu_t amd64_rapl_support;
extern pfmlib_pmu_t intel_p6_support;
extern pfmlib_pmu_t intel_ppro_support;
extern pfmlib_pmu_t intel_pii_support;
extern pfmlib_pmu_t intel_pm_support;
extern pfmlib_pmu_t sicortex_support;
extern pfmlib_pmu_t netburst_support;
extern pfmlib_pmu_t netburst_p_support;
extern pfmlib_pmu_t intel_coreduo_support;
extern pfmlib_pmu_t intel_core_support;
extern pfmlib_pmu_t intel_x86_arch_support;
extern pfmlib_pmu_t intel_atom_support;
extern pfmlib_pmu_t intel_nhm_support;
extern pfmlib_pmu_t intel_nhm_ex_support;
extern pfmlib_pmu_t intel_nhm_unc_support;
extern pfmlib_pmu_t intel_snb_support;
extern pfmlib_pmu_t intel_snb_unc_cbo0_support;
extern pfmlib_pmu_t intel_snb_unc_cbo1_support;
extern pfmlib_pmu_t intel_snb_unc_cbo2_support;
extern pfmlib_pmu_t intel_snb_unc_cbo3_support;
extern pfmlib_pmu_t intel_snb_ep_support;
extern pfmlib_pmu_t intel_ivb_support;
extern pfmlib_pmu_t intel_ivb_unc_cbo0_support;
extern pfmlib_pmu_t intel_ivb_unc_cbo1_support;
extern pfmlib_pmu_t intel_ivb_unc_cbo2_support;
extern pfmlib_pmu_t intel_ivb_unc_cbo3_support;
extern pfmlib_pmu_t intel_ivb_ep_support;
extern pfmlib_pmu_t intel_hsw_support;
extern pfmlib_pmu_t intel_hsw_ep_support;
extern pfmlib_pmu_t intel_bdw_support;
extern pfmlib_pmu_t intel_bdw_ep_support;
extern pfmlib_pmu_t intel_skl_support;
extern pfmlib_pmu_t intel_skx_support;
extern pfmlib_pmu_t intel_clx_support;
extern pfmlib_pmu_t intel_icl_support;
extern pfmlib_pmu_t intel_icx_support;
extern pfmlib_pmu_t intel_icx_unc_cha0_support;
extern pfmlib_pmu_t intel_icx_unc_cha1_support;
extern pfmlib_pmu_t intel_icx_unc_cha2_support;
extern pfmlib_pmu_t intel_icx_unc_cha3_support;
extern pfmlib_pmu_t intel_icx_unc_cha4_support;
extern pfmlib_pmu_t intel_icx_unc_cha5_support;
extern pfmlib_pmu_t intel_icx_unc_cha6_support;
extern pfmlib_pmu_t intel_icx_unc_cha7_support;
extern pfmlib_pmu_t intel_icx_unc_cha8_support;
extern pfmlib_pmu_t intel_icx_unc_cha9_support;
extern pfmlib_pmu_t intel_icx_unc_cha10_support;
extern pfmlib_pmu_t intel_icx_unc_cha11_support;
extern pfmlib_pmu_t intel_icx_unc_cha12_support;
extern pfmlib_pmu_t intel_icx_unc_cha13_support;
extern pfmlib_pmu_t intel_icx_unc_cha14_support;
extern pfmlib_pmu_t intel_icx_unc_cha15_support;
extern pfmlib_pmu_t intel_icx_unc_cha16_support;
extern pfmlib_pmu_t intel_icx_unc_cha17_support;
extern pfmlib_pmu_t intel_icx_unc_cha18_support;
extern pfmlib_pmu_t intel_icx_unc_cha19_support;
extern pfmlib_pmu_t intel_icx_unc_cha20_support;
extern pfmlib_pmu_t intel_icx_unc_cha21_support;
extern pfmlib_pmu_t intel_icx_unc_cha22_support;
extern pfmlib_pmu_t intel_icx_unc_cha23_support;
extern pfmlib_pmu_t intel_icx_unc_cha24_support;
extern pfmlib_pmu_t intel_icx_unc_cha25_support;
extern pfmlib_pmu_t intel_icx_unc_cha26_support;
extern pfmlib_pmu_t intel_icx_unc_cha27_support;
extern pfmlib_pmu_t intel_icx_unc_cha28_support;
extern pfmlib_pmu_t intel_icx_unc_cha29_support;
extern pfmlib_pmu_t intel_icx_unc_cha30_support;
extern pfmlib_pmu_t intel_icx_unc_cha31_support;
extern pfmlib_pmu_t intel_icx_unc_cha32_support;
extern pfmlib_pmu_t intel_icx_unc_cha33_support;
extern pfmlib_pmu_t intel_icx_unc_cha34_support;
extern pfmlib_pmu_t intel_icx_unc_cha35_support;
extern pfmlib_pmu_t intel_icx_unc_cha36_support;
extern pfmlib_pmu_t intel_icx_unc_cha37_support;
extern pfmlib_pmu_t intel_icx_unc_cha38_support;
extern pfmlib_pmu_t intel_icx_unc_cha39_support;
extern pfmlib_pmu_t intel_icx_unc_imc0_support;
extern pfmlib_pmu_t intel_icx_unc_imc1_support;
extern pfmlib_pmu_t intel_icx_unc_imc2_support;
extern pfmlib_pmu_t intel_icx_unc_imc3_support;
extern pfmlib_pmu_t intel_icx_unc_imc4_support;
extern pfmlib_pmu_t intel_icx_unc_imc5_support;
extern pfmlib_pmu_t intel_icx_unc_imc6_support;
extern pfmlib_pmu_t intel_icx_unc_imc7_support;
extern pfmlib_pmu_t intel_icx_unc_imc8_support;
extern pfmlib_pmu_t intel_icx_unc_imc9_support;
extern pfmlib_pmu_t intel_icx_unc_imc10_support;
extern pfmlib_pmu_t intel_icx_unc_imc11_support;
extern pfmlib_pmu_t intel_icx_unc_m2m0_support;
extern pfmlib_pmu_t intel_icx_unc_m2m1_support;
extern pfmlib_pmu_t intel_icx_unc_iio0_support;
extern pfmlib_pmu_t intel_icx_unc_iio1_support;
extern pfmlib_pmu_t intel_icx_unc_iio2_support;
extern pfmlib_pmu_t intel_icx_unc_iio3_support;
extern pfmlib_pmu_t intel_icx_unc_iio4_support;
extern pfmlib_pmu_t intel_icx_unc_iio5_support;
extern pfmlib_pmu_t intel_icx_unc_irp0_support;
extern pfmlib_pmu_t intel_icx_unc_irp1_support;
extern pfmlib_pmu_t intel_icx_unc_irp2_support;
extern pfmlib_pmu_t intel_icx_unc_irp3_support;
extern pfmlib_pmu_t intel_icx_unc_irp4_support;
extern pfmlib_pmu_t intel_icx_unc_irp5_support;
extern pfmlib_pmu_t intel_icx_unc_pcu_support;
extern pfmlib_pmu_t intel_icx_unc_upi0_support;
extern pfmlib_pmu_t intel_icx_unc_upi1_support;
extern pfmlib_pmu_t intel_icx_unc_upi2_support;
extern pfmlib_pmu_t intel_icx_unc_upi3_support;
extern pfmlib_pmu_t intel_icx_unc_m3upi0_support;
extern pfmlib_pmu_t intel_icx_unc_m3upi1_support;
extern pfmlib_pmu_t intel_icx_unc_m3upi2_support;
extern pfmlib_pmu_t intel_icx_unc_m3upi3_support;
extern pfmlib_pmu_t intel_icx_unc_ubox_support;
extern pfmlib_pmu_t intel_icx_unc_m2pcie0_support;
extern pfmlib_pmu_t intel_icx_unc_m2pcie1_support;
extern pfmlib_pmu_t intel_icx_unc_m2pcie2_support;
extern pfmlib_pmu_t intel_spr_support;
extern pfmlib_pmu_t intel_spr_unc_imc0_support;
extern pfmlib_pmu_t intel_spr_unc_imc1_support;
extern pfmlib_pmu_t intel_spr_unc_imc2_support;
extern pfmlib_pmu_t intel_spr_unc_imc3_support;
extern pfmlib_pmu_t intel_spr_unc_imc4_support;
extern pfmlib_pmu_t intel_spr_unc_imc5_support;
extern pfmlib_pmu_t intel_spr_unc_imc6_support;
extern pfmlib_pmu_t intel_spr_unc_imc7_support;
extern pfmlib_pmu_t intel_spr_unc_imc8_support;
extern pfmlib_pmu_t intel_spr_unc_imc9_support;
extern pfmlib_pmu_t intel_spr_unc_imc10_support;
extern pfmlib_pmu_t intel_spr_unc_imc11_support;
extern pfmlib_pmu_t intel_spr_unc_upi0_support;
extern pfmlib_pmu_t intel_spr_unc_upi1_support;
extern pfmlib_pmu_t intel_spr_unc_upi2_support;
extern pfmlib_pmu_t intel_spr_unc_upi3_support;
extern pfmlib_pmu_t intel_spr_unc_cha0_support;
extern pfmlib_pmu_t intel_spr_unc_cha1_support;
extern pfmlib_pmu_t intel_spr_unc_cha2_support;
extern pfmlib_pmu_t intel_spr_unc_cha3_support;
extern pfmlib_pmu_t intel_spr_unc_cha4_support;
extern pfmlib_pmu_t intel_spr_unc_cha5_support;
extern pfmlib_pmu_t intel_spr_unc_cha6_support;
extern pfmlib_pmu_t intel_spr_unc_cha7_support;
extern pfmlib_pmu_t intel_spr_unc_cha8_support;
extern pfmlib_pmu_t intel_spr_unc_cha9_support;
extern pfmlib_pmu_t intel_spr_unc_cha10_support;
extern pfmlib_pmu_t intel_spr_unc_cha11_support;
extern pfmlib_pmu_t intel_spr_unc_cha12_support;
extern pfmlib_pmu_t intel_spr_unc_cha13_support;
extern pfmlib_pmu_t intel_spr_unc_cha14_support;
extern pfmlib_pmu_t intel_spr_unc_cha15_support;
extern pfmlib_pmu_t intel_spr_unc_cha16_support;
extern pfmlib_pmu_t intel_spr_unc_cha17_support;
extern pfmlib_pmu_t intel_spr_unc_cha18_support;
extern pfmlib_pmu_t intel_spr_unc_cha19_support;
extern pfmlib_pmu_t intel_spr_unc_cha20_support;
extern pfmlib_pmu_t intel_spr_unc_cha21_support;
extern pfmlib_pmu_t intel_spr_unc_cha22_support;
extern pfmlib_pmu_t intel_spr_unc_cha23_support;
extern pfmlib_pmu_t intel_spr_unc_cha24_support;
extern pfmlib_pmu_t intel_spr_unc_cha25_support;
extern pfmlib_pmu_t intel_spr_unc_cha26_support;
extern pfmlib_pmu_t intel_spr_unc_cha27_support;
extern pfmlib_pmu_t intel_spr_unc_cha28_support;
extern pfmlib_pmu_t intel_spr_unc_cha29_support;
extern pfmlib_pmu_t intel_spr_unc_cha30_support;
extern pfmlib_pmu_t intel_spr_unc_cha31_support;
extern pfmlib_pmu_t intel_spr_unc_cha32_support;
extern pfmlib_pmu_t intel_spr_unc_cha33_support;
extern pfmlib_pmu_t intel_spr_unc_cha34_support;
extern pfmlib_pmu_t intel_spr_unc_cha35_support;
extern pfmlib_pmu_t intel_spr_unc_cha36_support;
extern pfmlib_pmu_t intel_spr_unc_cha37_support;
extern pfmlib_pmu_t intel_spr_unc_cha38_support;
extern pfmlib_pmu_t intel_spr_unc_cha39_support;
extern pfmlib_pmu_t intel_spr_unc_cha40_support;
extern pfmlib_pmu_t intel_spr_unc_cha40_support;
extern pfmlib_pmu_t intel_spr_unc_cha40_support;
extern pfmlib_pmu_t intel_spr_unc_cha41_support;
extern pfmlib_pmu_t intel_spr_unc_cha42_support;
extern pfmlib_pmu_t intel_spr_unc_cha43_support;
extern pfmlib_pmu_t intel_spr_unc_cha44_support;
extern pfmlib_pmu_t intel_spr_unc_cha45_support;
extern pfmlib_pmu_t intel_spr_unc_cha46_support;
extern pfmlib_pmu_t intel_spr_unc_cha47_support;
extern pfmlib_pmu_t intel_spr_unc_cha48_support;
extern pfmlib_pmu_t intel_spr_unc_cha49_support;
extern pfmlib_pmu_t intel_spr_unc_cha50_support;
extern pfmlib_pmu_t intel_spr_unc_cha51_support;
extern pfmlib_pmu_t intel_spr_unc_cha52_support;
extern pfmlib_pmu_t intel_spr_unc_cha53_support;
extern pfmlib_pmu_t intel_spr_unc_cha54_support;
extern pfmlib_pmu_t intel_spr_unc_cha55_support;
extern pfmlib_pmu_t intel_spr_unc_cha56_support;
extern pfmlib_pmu_t intel_spr_unc_cha57_support;
extern pfmlib_pmu_t intel_spr_unc_cha58_support;
extern pfmlib_pmu_t intel_spr_unc_cha59_support;
extern pfmlib_pmu_t intel_emr_support;
extern pfmlib_pmu_t intel_gnr_support;
extern pfmlib_pmu_t intel_gnr_unc_imc0_support;
extern pfmlib_pmu_t intel_gnr_unc_imc1_support;
extern pfmlib_pmu_t intel_gnr_unc_imc2_support;
extern pfmlib_pmu_t intel_gnr_unc_imc3_support;
extern pfmlib_pmu_t intel_gnr_unc_imc4_support;
extern pfmlib_pmu_t intel_gnr_unc_imc5_support;
extern pfmlib_pmu_t intel_gnr_unc_imc6_support;
extern pfmlib_pmu_t intel_gnr_unc_imc7_support;
extern pfmlib_pmu_t intel_gnr_unc_imc8_support;
extern pfmlib_pmu_t intel_gnr_unc_imc9_support;
extern pfmlib_pmu_t intel_gnr_unc_imc10_support;
extern pfmlib_pmu_t intel_gnr_unc_imc11_support;
extern pfmlib_pmu_t intel_adl_glc_support;
extern pfmlib_pmu_t intel_adl_grt_support;
extern pfmlib_pmu_t intel_rapl_support;
extern pfmlib_pmu_t intel_snbep_unc_cb0_support;
extern pfmlib_pmu_t intel_snbep_unc_cb1_support;
extern pfmlib_pmu_t intel_snbep_unc_cb2_support;
extern pfmlib_pmu_t intel_snbep_unc_cb3_support;
extern pfmlib_pmu_t intel_snbep_unc_cb4_support;
extern pfmlib_pmu_t intel_snbep_unc_cb5_support;
extern pfmlib_pmu_t intel_snbep_unc_cb6_support;
extern pfmlib_pmu_t intel_snbep_unc_cb7_support;
extern pfmlib_pmu_t intel_snbep_unc_ha_support;
extern pfmlib_pmu_t intel_snbep_unc_imc0_support;
extern pfmlib_pmu_t intel_snbep_unc_imc1_support;
extern pfmlib_pmu_t intel_snbep_unc_imc2_support;
extern pfmlib_pmu_t intel_snbep_unc_imc3_support;
extern pfmlib_pmu_t intel_snbep_unc_pcu_support;
extern pfmlib_pmu_t intel_snbep_unc_qpi0_support;
extern pfmlib_pmu_t intel_snbep_unc_qpi1_support;
extern pfmlib_pmu_t intel_snbep_unc_ubo_support;
extern pfmlib_pmu_t intel_snbep_unc_r2pcie_support;
extern pfmlib_pmu_t intel_snbep_unc_r3qpi0_support;
extern pfmlib_pmu_t intel_snbep_unc_r3qpi1_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb0_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb1_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb2_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb3_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb4_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb5_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb6_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb7_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb8_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb9_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb10_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb11_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb12_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb13_support;
extern pfmlib_pmu_t intel_ivbep_unc_cb14_support;
extern pfmlib_pmu_t intel_ivbep_unc_ha0_support;
extern pfmlib_pmu_t intel_ivbep_unc_ha1_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc0_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc1_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc2_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc3_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc4_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc5_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc6_support;
extern pfmlib_pmu_t intel_ivbep_unc_imc7_support;
extern pfmlib_pmu_t intel_ivbep_unc_pcu_support;
extern pfmlib_pmu_t intel_ivbep_unc_qpi0_support;
extern pfmlib_pmu_t intel_ivbep_unc_qpi1_support;
extern pfmlib_pmu_t intel_ivbep_unc_qpi2_support;
extern pfmlib_pmu_t intel_ivbep_unc_ubo_support;
extern pfmlib_pmu_t intel_ivbep_unc_r2pcie_support;
extern pfmlib_pmu_t intel_ivbep_unc_r3qpi0_support;
extern pfmlib_pmu_t intel_ivbep_unc_r3qpi1_support;
extern pfmlib_pmu_t intel_ivbep_unc_r3qpi2_support;
extern pfmlib_pmu_t intel_ivbep_unc_irp_support;
extern pfmlib_pmu_t intel_hswep_unc_cb0_support;
extern pfmlib_pmu_t intel_hswep_unc_cb1_support;
extern pfmlib_pmu_t intel_hswep_unc_cb2_support;
extern pfmlib_pmu_t intel_hswep_unc_cb3_support;
extern pfmlib_pmu_t intel_hswep_unc_cb4_support;
extern pfmlib_pmu_t intel_hswep_unc_cb5_support;
extern pfmlib_pmu_t intel_hswep_unc_cb6_support;
extern pfmlib_pmu_t intel_hswep_unc_cb7_support;
extern pfmlib_pmu_t intel_hswep_unc_cb8_support;
extern pfmlib_pmu_t intel_hswep_unc_cb9_support;
extern pfmlib_pmu_t intel_hswep_unc_cb10_support;
extern pfmlib_pmu_t intel_hswep_unc_cb11_support;
extern pfmlib_pmu_t intel_hswep_unc_cb12_support;
extern pfmlib_pmu_t intel_hswep_unc_cb13_support;
extern pfmlib_pmu_t intel_hswep_unc_cb14_support;
extern pfmlib_pmu_t intel_hswep_unc_cb15_support;
extern pfmlib_pmu_t intel_hswep_unc_cb16_support;
extern pfmlib_pmu_t intel_hswep_unc_cb17_support;
extern pfmlib_pmu_t intel_hswep_unc_ha0_support;
extern pfmlib_pmu_t intel_hswep_unc_ha1_support;
extern pfmlib_pmu_t intel_hswep_unc_imc0_support;
extern pfmlib_pmu_t intel_hswep_unc_imc1_support;
extern pfmlib_pmu_t intel_hswep_unc_imc2_support;
extern pfmlib_pmu_t intel_hswep_unc_imc3_support;
extern pfmlib_pmu_t intel_hswep_unc_imc4_support;
extern pfmlib_pmu_t intel_hswep_unc_imc5_support;
extern pfmlib_pmu_t intel_hswep_unc_imc6_support;
extern pfmlib_pmu_t intel_hswep_unc_imc7_support;
extern pfmlib_pmu_t intel_hswep_unc_pcu_support;
extern pfmlib_pmu_t intel_hswep_unc_qpi0_support;
extern pfmlib_pmu_t intel_hswep_unc_qpi1_support;
extern pfmlib_pmu_t intel_hswep_unc_sb0_support;
extern pfmlib_pmu_t intel_hswep_unc_sb1_support;
extern pfmlib_pmu_t intel_hswep_unc_sb2_support;
extern pfmlib_pmu_t intel_hswep_unc_sb3_support;
extern pfmlib_pmu_t intel_hswep_unc_ubo_support;
extern pfmlib_pmu_t intel_hswep_unc_r2pcie_support;
extern pfmlib_pmu_t intel_hswep_unc_r3qpi0_support;
extern pfmlib_pmu_t intel_hswep_unc_r3qpi1_support;
extern pfmlib_pmu_t intel_hswep_unc_r3qpi2_support;
extern pfmlib_pmu_t intel_hswep_unc_irp_support;
extern pfmlib_pmu_t intel_knc_support;
extern pfmlib_pmu_t intel_slm_support;
extern pfmlib_pmu_t intel_tmt_support;
extern pfmlib_pmu_t intel_knl_support;
extern pfmlib_pmu_t intel_knl_unc_imc0_support;
extern pfmlib_pmu_t intel_knl_unc_imc1_support;
extern pfmlib_pmu_t intel_knl_unc_imc2_support;
extern pfmlib_pmu_t intel_knl_unc_imc3_support;
extern pfmlib_pmu_t intel_knl_unc_imc4_support;
extern pfmlib_pmu_t intel_knl_unc_imc5_support;
extern pfmlib_pmu_t intel_knl_unc_imc_uclk0_support;
extern pfmlib_pmu_t intel_knl_unc_imc_uclk1_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk0_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk1_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk2_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk3_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk4_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk5_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk6_support;
extern pfmlib_pmu_t intel_knl_unc_edc_uclk7_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk0_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk1_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk2_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk3_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk4_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk5_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk6_support;
extern pfmlib_pmu_t intel_knl_unc_edc_eclk7_support;
extern pfmlib_pmu_t intel_knl_unc_cha0_support;
extern pfmlib_pmu_t intel_knl_unc_cha1_support;
extern pfmlib_pmu_t intel_knl_unc_cha2_support;
extern pfmlib_pmu_t intel_knl_unc_cha3_support;
extern pfmlib_pmu_t intel_knl_unc_cha4_support;
extern pfmlib_pmu_t intel_knl_unc_cha5_support;
extern pfmlib_pmu_t intel_knl_unc_cha6_support;
extern pfmlib_pmu_t intel_knl_unc_cha7_support;
extern pfmlib_pmu_t intel_knl_unc_cha8_support;
extern pfmlib_pmu_t intel_knl_unc_cha9_support;
extern pfmlib_pmu_t intel_knl_unc_cha10_support;
extern pfmlib_pmu_t intel_knl_unc_cha11_support;
extern pfmlib_pmu_t intel_knl_unc_cha12_support;
extern pfmlib_pmu_t intel_knl_unc_cha13_support;
extern pfmlib_pmu_t intel_knl_unc_cha14_support;
extern pfmlib_pmu_t intel_knl_unc_cha15_support;
extern pfmlib_pmu_t intel_knl_unc_cha16_support;
extern pfmlib_pmu_t intel_knl_unc_cha17_support;
extern pfmlib_pmu_t intel_knl_unc_cha18_support;
extern pfmlib_pmu_t intel_knl_unc_cha19_support;
extern pfmlib_pmu_t intel_knl_unc_cha20_support;
extern pfmlib_pmu_t intel_knl_unc_cha21_support;
extern pfmlib_pmu_t intel_knl_unc_cha22_support;
extern pfmlib_pmu_t intel_knl_unc_cha23_support;
extern pfmlib_pmu_t intel_knl_unc_cha24_support;
extern pfmlib_pmu_t intel_knl_unc_cha25_support;
extern pfmlib_pmu_t intel_knl_unc_cha26_support;
extern pfmlib_pmu_t intel_knl_unc_cha27_support;
extern pfmlib_pmu_t intel_knl_unc_cha28_support;
extern pfmlib_pmu_t intel_knl_unc_cha29_support;
extern pfmlib_pmu_t intel_knl_unc_cha30_support;
extern pfmlib_pmu_t intel_knl_unc_cha31_support;
extern pfmlib_pmu_t intel_knl_unc_cha32_support;
extern pfmlib_pmu_t intel_knl_unc_cha33_support;
extern pfmlib_pmu_t intel_knl_unc_cha34_support;
extern pfmlib_pmu_t intel_knl_unc_cha35_support;
extern pfmlib_pmu_t intel_knl_unc_cha36_support;
extern pfmlib_pmu_t intel_knl_unc_cha37_support;
extern pfmlib_pmu_t intel_knl_unc_m2pcie_support;
extern pfmlib_pmu_t intel_bdx_unc_cb0_support;
extern pfmlib_pmu_t intel_bdx_unc_cb1_support;
extern pfmlib_pmu_t intel_bdx_unc_cb2_support;
extern pfmlib_pmu_t intel_bdx_unc_cb3_support;
extern pfmlib_pmu_t intel_bdx_unc_cb4_support;
extern pfmlib_pmu_t intel_bdx_unc_cb5_support;
extern pfmlib_pmu_t intel_bdx_unc_cb6_support;
extern pfmlib_pmu_t intel_bdx_unc_cb7_support;
extern pfmlib_pmu_t intel_bdx_unc_cb8_support;
extern pfmlib_pmu_t intel_bdx_unc_cb9_support;
extern pfmlib_pmu_t intel_bdx_unc_cb10_support;
extern pfmlib_pmu_t intel_bdx_unc_cb11_support;
extern pfmlib_pmu_t intel_bdx_unc_cb12_support;
extern pfmlib_pmu_t intel_bdx_unc_cb13_support;
extern pfmlib_pmu_t intel_bdx_unc_cb14_support;
extern pfmlib_pmu_t intel_bdx_unc_cb15_support;
extern pfmlib_pmu_t intel_bdx_unc_cb16_support;
extern pfmlib_pmu_t intel_bdx_unc_cb17_support;
extern pfmlib_pmu_t intel_bdx_unc_cb18_support;
extern pfmlib_pmu_t intel_bdx_unc_cb19_support;
extern pfmlib_pmu_t intel_bdx_unc_cb20_support;
extern pfmlib_pmu_t intel_bdx_unc_cb21_support;
extern pfmlib_pmu_t intel_bdx_unc_cb22_support;
extern pfmlib_pmu_t intel_bdx_unc_cb23_support;
extern pfmlib_pmu_t intel_bdx_unc_ha0_support;
extern pfmlib_pmu_t intel_bdx_unc_ha1_support;
extern pfmlib_pmu_t intel_bdx_unc_imc0_support;
extern pfmlib_pmu_t intel_bdx_unc_imc1_support;
extern pfmlib_pmu_t intel_bdx_unc_imc2_support;
extern pfmlib_pmu_t intel_bdx_unc_imc3_support;
extern pfmlib_pmu_t intel_bdx_unc_imc4_support;
extern pfmlib_pmu_t intel_bdx_unc_imc5_support;
extern pfmlib_pmu_t intel_bdx_unc_imc6_support;
extern pfmlib_pmu_t intel_bdx_unc_imc7_support;
extern pfmlib_pmu_t intel_bdx_unc_pcu_support;
extern pfmlib_pmu_t intel_bdx_unc_qpi0_support;
extern pfmlib_pmu_t intel_bdx_unc_qpi1_support;
extern pfmlib_pmu_t intel_bdx_unc_qpi2_support;
extern pfmlib_pmu_t intel_bdx_unc_sbo0_support;
extern pfmlib_pmu_t intel_bdx_unc_sbo1_support;
extern pfmlib_pmu_t intel_bdx_unc_sbo2_support;
extern pfmlib_pmu_t intel_bdx_unc_sbo3_support;
extern pfmlib_pmu_t intel_bdx_unc_ubo_support;
extern pfmlib_pmu_t intel_bdx_unc_r2pcie_support;
extern pfmlib_pmu_t intel_bdx_unc_r3qpi0_support;
extern pfmlib_pmu_t intel_bdx_unc_r3qpi1_support;
extern pfmlib_pmu_t intel_bdx_unc_r3qpi2_support;
extern pfmlib_pmu_t intel_bdx_unc_irp_support;
extern pfmlib_pmu_t intel_skx_unc_cha0_support;
extern pfmlib_pmu_t intel_skx_unc_cha1_support;
extern pfmlib_pmu_t intel_skx_unc_cha2_support;
extern pfmlib_pmu_t intel_skx_unc_cha3_support;
extern pfmlib_pmu_t intel_skx_unc_cha4_support;
extern pfmlib_pmu_t intel_skx_unc_cha5_support;
extern pfmlib_pmu_t intel_skx_unc_cha6_support;
extern pfmlib_pmu_t intel_skx_unc_cha7_support;
extern pfmlib_pmu_t intel_skx_unc_cha8_support;
extern pfmlib_pmu_t intel_skx_unc_cha9_support;
extern pfmlib_pmu_t intel_skx_unc_cha10_support;
extern pfmlib_pmu_t intel_skx_unc_cha11_support;
extern pfmlib_pmu_t intel_skx_unc_cha12_support;
extern pfmlib_pmu_t intel_skx_unc_cha13_support;
extern pfmlib_pmu_t intel_skx_unc_cha14_support;
extern pfmlib_pmu_t intel_skx_unc_cha15_support;
extern pfmlib_pmu_t intel_skx_unc_cha16_support;
extern pfmlib_pmu_t intel_skx_unc_cha17_support;
extern pfmlib_pmu_t intel_skx_unc_cha18_support;
extern pfmlib_pmu_t intel_skx_unc_cha19_support;
extern pfmlib_pmu_t intel_skx_unc_cha20_support;
extern pfmlib_pmu_t intel_skx_unc_cha21_support;
extern pfmlib_pmu_t intel_skx_unc_cha22_support;
extern pfmlib_pmu_t intel_skx_unc_cha23_support;
extern pfmlib_pmu_t intel_skx_unc_cha24_support;
extern pfmlib_pmu_t intel_skx_unc_cha25_support;
extern pfmlib_pmu_t intel_skx_unc_cha26_support;
extern pfmlib_pmu_t intel_skx_unc_cha27_support;
extern pfmlib_pmu_t intel_skx_unc_iio0_support;
extern pfmlib_pmu_t intel_skx_unc_iio1_support;
extern pfmlib_pmu_t intel_skx_unc_iio2_support;
extern pfmlib_pmu_t intel_skx_unc_iio3_support;
extern pfmlib_pmu_t intel_skx_unc_iio4_support;
extern pfmlib_pmu_t intel_skx_unc_iio5_support;
extern pfmlib_pmu_t intel_skx_unc_imc0_support;
extern pfmlib_pmu_t intel_skx_unc_imc1_support;
extern pfmlib_pmu_t intel_skx_unc_imc2_support;
extern pfmlib_pmu_t intel_skx_unc_imc3_support;
extern pfmlib_pmu_t intel_skx_unc_imc4_support;
extern pfmlib_pmu_t intel_skx_unc_imc5_support;
extern pfmlib_pmu_t intel_skx_unc_pcu_support;
extern pfmlib_pmu_t intel_skx_unc_upi0_support;
extern pfmlib_pmu_t intel_skx_unc_upi1_support;
extern pfmlib_pmu_t intel_skx_unc_upi2_support;
extern pfmlib_pmu_t intel_skx_unc_m2m0_support;
extern pfmlib_pmu_t intel_skx_unc_m2m1_support;
extern pfmlib_pmu_t intel_skx_unc_ubo_support;
extern pfmlib_pmu_t intel_skx_unc_m3upi0_support;
extern pfmlib_pmu_t intel_skx_unc_m3upi1_support;
extern pfmlib_pmu_t intel_skx_unc_m3upi2_support;
extern pfmlib_pmu_t intel_skx_unc_irp_support;
extern pfmlib_pmu_t intel_knm_support;
extern pfmlib_pmu_t intel_knm_unc_imc0_support;
extern pfmlib_pmu_t intel_knm_unc_imc1_support;
extern pfmlib_pmu_t intel_knm_unc_imc2_support;
extern pfmlib_pmu_t intel_knm_unc_imc3_support;
extern pfmlib_pmu_t intel_knm_unc_imc4_support;
extern pfmlib_pmu_t intel_knm_unc_imc5_support;
extern pfmlib_pmu_t intel_knm_unc_imc_uclk0_support;
extern pfmlib_pmu_t intel_knm_unc_imc_uclk1_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk0_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk1_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk2_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk3_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk4_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk5_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk6_support;
extern pfmlib_pmu_t intel_knm_unc_edc_uclk7_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk0_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk1_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk2_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk3_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk4_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk5_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk6_support;
extern pfmlib_pmu_t intel_knm_unc_edc_eclk7_support;
extern pfmlib_pmu_t intel_knm_unc_cha0_support;
extern pfmlib_pmu_t intel_knm_unc_cha1_support;
extern pfmlib_pmu_t intel_knm_unc_cha2_support;
extern pfmlib_pmu_t intel_knm_unc_cha3_support;
extern pfmlib_pmu_t intel_knm_unc_cha4_support;
extern pfmlib_pmu_t intel_knm_unc_cha5_support;
extern pfmlib_pmu_t intel_knm_unc_cha6_support;
extern pfmlib_pmu_t intel_knm_unc_cha7_support;
extern pfmlib_pmu_t intel_knm_unc_cha8_support;
extern pfmlib_pmu_t intel_knm_unc_cha9_support;
extern pfmlib_pmu_t intel_knm_unc_cha10_support;
extern pfmlib_pmu_t intel_knm_unc_cha11_support;
extern pfmlib_pmu_t intel_knm_unc_cha12_support;
extern pfmlib_pmu_t intel_knm_unc_cha13_support;
extern pfmlib_pmu_t intel_knm_unc_cha14_support;
extern pfmlib_pmu_t intel_knm_unc_cha15_support;
extern pfmlib_pmu_t intel_knm_unc_cha16_support;
extern pfmlib_pmu_t intel_knm_unc_cha17_support;
extern pfmlib_pmu_t intel_knm_unc_cha18_support;
extern pfmlib_pmu_t intel_knm_unc_cha19_support;
extern pfmlib_pmu_t intel_knm_unc_cha20_support;
extern pfmlib_pmu_t intel_knm_unc_cha21_support;
extern pfmlib_pmu_t intel_knm_unc_cha22_support;
extern pfmlib_pmu_t intel_knm_unc_cha23_support;
extern pfmlib_pmu_t intel_knm_unc_cha24_support;
extern pfmlib_pmu_t intel_knm_unc_cha25_support;
extern pfmlib_pmu_t intel_knm_unc_cha26_support;
extern pfmlib_pmu_t intel_knm_unc_cha27_support;
extern pfmlib_pmu_t intel_knm_unc_cha28_support;
extern pfmlib_pmu_t intel_knm_unc_cha29_support;
extern pfmlib_pmu_t intel_knm_unc_cha30_support;
extern pfmlib_pmu_t intel_knm_unc_cha31_support;
extern pfmlib_pmu_t intel_knm_unc_cha32_support;
extern pfmlib_pmu_t intel_knm_unc_cha33_support;
extern pfmlib_pmu_t intel_knm_unc_cha34_support;
extern pfmlib_pmu_t intel_knm_unc_cha35_support;
extern pfmlib_pmu_t intel_knm_unc_cha36_support;
extern pfmlib_pmu_t intel_knm_unc_cha37_support;
extern pfmlib_pmu_t intel_knm_unc_m2pcie_support;

extern pfmlib_pmu_t intel_glm_support;
extern pfmlib_pmu_t power4_support;
extern pfmlib_pmu_t ppc970_support;
extern pfmlib_pmu_t ppc970mp_support;
extern pfmlib_pmu_t power5_support;
extern pfmlib_pmu_t power5p_support;
extern pfmlib_pmu_t power6_support;
extern pfmlib_pmu_t power7_support;
extern pfmlib_pmu_t power8_support;
extern pfmlib_pmu_t power9_support;
extern pfmlib_pmu_t power10_support;
extern pfmlib_pmu_t torrent_support;
extern pfmlib_pmu_t powerpc_nest_mcs_read_support;
extern pfmlib_pmu_t powerpc_nest_mcs_write_support;
extern pfmlib_pmu_t sparc_support;
extern pfmlib_pmu_t sparc_ultra12_support;
extern pfmlib_pmu_t sparc_ultra3_support;
extern pfmlib_pmu_t sparc_ultra3i_support;
extern pfmlib_pmu_t sparc_ultra3plus_support;
extern pfmlib_pmu_t sparc_ultra4plus_support;
extern pfmlib_pmu_t sparc_niagara1_support;
extern pfmlib_pmu_t sparc_niagara2_support;
extern pfmlib_pmu_t cell_support;
extern pfmlib_pmu_t perf_event_support;
extern pfmlib_pmu_t perf_event_raw_support;
extern pfmlib_pmu_t intel_wsm_sp_support;
extern pfmlib_pmu_t intel_wsm_dp_support;
extern pfmlib_pmu_t intel_wsm_unc_support;
extern pfmlib_pmu_t arm_cortex_a7_support;
extern pfmlib_pmu_t arm_cortex_a8_support;
extern pfmlib_pmu_t arm_cortex_a9_support;
extern pfmlib_pmu_t arm_cortex_a15_support;
extern pfmlib_pmu_t arm_1176_support;
extern pfmlib_pmu_t arm_qcom_krait_support;
extern pfmlib_pmu_t arm_cortex_a57_support;
extern pfmlib_pmu_t arm_cortex_a53_support;
extern pfmlib_pmu_t arm_cortex_a55_support;
extern pfmlib_pmu_t arm_cortex_a72_support;
extern pfmlib_pmu_t arm_cortex_a76_support;
extern pfmlib_pmu_t arm_cortex_x4_support;
extern pfmlib_pmu_t arm_xgene_support;
extern pfmlib_pmu_t arm_n1_support;
extern pfmlib_pmu_t arm_n2_support;
extern pfmlib_pmu_t arm_n3_support;
extern pfmlib_pmu_t arm_v1_support;
extern pfmlib_pmu_t arm_v2_support;
extern pfmlib_pmu_t arm_v3_support;

extern pfmlib_pmu_t arm_nvidia_olympus_support;

extern pfmlib_pmu_t arm_thunderx2_support;
extern pfmlib_pmu_t arm_thunderx2_dmc0_support;
extern pfmlib_pmu_t arm_thunderx2_dmc1_support;
extern pfmlib_pmu_t arm_thunderx2_llc0_support;
extern pfmlib_pmu_t arm_thunderx2_llc1_support;
extern pfmlib_pmu_t arm_thunderx2_ccpi0_support;
extern pfmlib_pmu_t arm_thunderx2_ccpi1_support;

extern pfmlib_pmu_t arm_fujitsu_a64fx_support;
extern pfmlib_pmu_t arm_fujitsu_monaka_support;

extern pfmlib_pmu_t arm_hisilicon_kunpeng_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl1_ddrc0_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl1_ddrc1_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl1_ddrc2_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl1_ddrc3_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl3_ddrc0_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl3_ddrc1_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl3_ddrc2_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl3_ddrc3_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl5_ddrc0_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl5_ddrc1_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl5_ddrc2_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl5_ddrc3_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl7_ddrc0_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl7_ddrc1_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl7_ddrc2_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl7_ddrc3_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl1_hha2_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl1_hha3_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl3_hha0_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl3_hha1_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl5_hha6_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl5_hha7_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl7_hha4_support;
extern pfmlib_pmu_t arm_hisilicon_kunpeng_sccl7_hha5_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c10_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c11_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c12_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c13_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c14_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c15_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c8_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl1_l3c9_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c0_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c1_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c2_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c3_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c4_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c5_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c6_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl3_l3c7_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c24_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c25_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c26_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c27_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c28_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c29_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c30_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl5_l3c31_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c16_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c17_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c18_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c19_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c20_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c21_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c22_support;
extern pfmlib_pmu_t	arm_hisilicon_kunpeng_sccl7_l3c23_support;

extern pfmlib_pmu_t mips_74k_support;
extern pfmlib_pmu_t s390x_cpum_cf_support;
extern pfmlib_pmu_t s390x_cpum_sf_support;

extern pfmlib_os_t *pfmlib_os;
extern pfmlib_os_t pfmlib_os_perf;
extern pfmlib_os_t pfmlib_os_perf_ext;

extern char *pfmlib_forced_pmu;

#define this_pe(t)		(((pfmlib_pmu_t *)t)->pe)
#define this_atdesc(t)		(((pfmlib_pmu_t *)t)->atdesc)

#define LIBPFM_ARRAY_SIZE(a) (sizeof(a) / sizeof(typeof(*(a))))
/*
 * population count (number of bits set)
 */
static inline int
pfmlib_popcnt(unsigned long v)
{
	int sum = 0;

	for(; v ; v >>=1) {
		if (v & 0x1) sum++;
	}
	return sum;
}

/*
 * find next bit set
 */
static inline size_t
pfmlib_fnb(unsigned long value, size_t nbits, int p)
{
	unsigned long m;
	size_t i;

	for(i=p; i < nbits; i++) {
		m = 1 << i;
		if (value & m)
			return i;
	}
	return i;
}

/*
 * PMU + internal idx -> external opaque idx
 */
static inline int
pfmlib_pidx2idx(pfmlib_pmu_t *pmu, int pidx)
{
	int idx;

	idx = pmu->pmu << PFMLIB_PMU_SHIFT;
	idx |= pidx;

	return  idx;
}

#define pfmlib_for_each_bit(x, m) \
	for((x) = pfmlib_fnb((m), (sizeof(m)<<3), 0); (x) < (sizeof(m)<<3); (x) = pfmlib_fnb((m), (sizeof(m)<<3), (x)+1))

#ifdef __linux__

#define PFMLIB_VALID_PERF_PATTRS(f)  \
	.validate_pattrs[PFM_OS_PERF_EVENT] = f, \
	.validate_pattrs[PFM_OS_PERF_EVENT_EXT]	= f

#define PFMLIB_ENCODE_PERF(f)  \
	.get_event_encoding[PFM_OS_PERF_EVENT] = f, \
	.get_event_encoding[PFM_OS_PERF_EVENT_EXT] = f

#define PFMLIB_OS_DETECT(f)  \
	.os_detect[PFM_OS_PERF_EVENT] = f, \
	.os_detect[PFM_OS_PERF_EVENT_EXT] = f
#else
#define PFMLIB_VALID_PERF_PATTRS(f) \
	.validate_pattrs[PFM_OS_PERF_EVENT] = NULL, \
	.validate_pattrs[PFM_OS_PERF_EVENT_EXT]	= NULL

#define PFMLIB_ENCODE_PERF(f)  \
	.get_event_encoding[PFM_OS_PERF_EVENT] = NULL, \
	.get_event_encoding[PFM_OS_PERF_EVENT_EXT] = NULL

#define PFMLIB_OS_DETECT(f)  \
	.os_detect[PFM_OS_PERF_EVENT] = NULL, \
	.os_detect[PFM_OS_PERF_EVENT_EXT] = NULL
#endif

static inline int
is_empty_attr(const pfmlib_attr_desc_t *a)
{
	return !a || !a->name || strlen(a->name) == 0 ? 1 : 0;
}

#endif /* __PFMLIB_PRIV_H__ */
