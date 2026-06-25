#ifndef __PFMLIB_SPARC_PRIV_H__
#define __PFMLIB_SPARC_PRIV_H__

typedef struct {
	char			*uname;	/* mask name */
	char			*udesc;	/* mask description */
	int			ubit;	/* umask bit position */
} sparc_mask_t;

#define EVENT_MASK_BITS		8
typedef struct {
	char			*name;	/* event name */
	char			*desc;	/* event description */
	char			ctrl;	/* S0 or S1 */
	char			__pad;
	int			code;	/* S0/S1 encoding */
	int			numasks;	/* number of entries in masks */
	sparc_mask_t		umasks[EVENT_MASK_BITS];
} sparc_entry_t;

typedef union {
	unsigned int val;
	struct {
		unsigned int	ctrl_s0   : 1;
		unsigned int	ctrl_s1   : 1;
		unsigned int	reserved1 : 14;
		unsigned int	code	  : 8;
		unsigned int	umask	  : 8;
	} config;
} pfm_sparc_reg_t;

#define PME_CTRL_S0		1
#define PME_CTRL_S1		2

#define SPARC_ATTR_K	0
#define SPARC_ATTR_U	1
#define SPARC_ATTR_H	2

#define SPARC_PLM (PFM_PLM0|PFM_PLM3)
#define NIAGARA2_PLM (SPARC_PLM|PFM_PLMH)

extern int pfm_sparc_detect(void *this);
extern int pfm_sparc_get_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_sparc_get_event_first(void *this);
extern int pfm_sparc_get_event_next(void *this, int idx);
extern int pfm_sparc_event_is_valid(void *this, int pidx);
extern int pfm_sparc_validate_table(void *this, FILE *fp);
extern int pfm_sparc_get_event_attr_info(void *this, int pidx, int attr_idx, pfmlib_event_attr_info_t *info);
extern int pfm_sparc_get_event_info(void *this, int idx, pfm_event_info_t *info);
extern unsigned int pfm_sparc_get_event_nattrs(void *this, int pidx);

extern void pfm_sparc_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);
extern int pfm_sparc_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
#endif /* __PFMLIB_SPARC_PRIV_H__ */
