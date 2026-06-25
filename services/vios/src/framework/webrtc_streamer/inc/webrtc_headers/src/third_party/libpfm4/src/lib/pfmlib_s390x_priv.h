#ifndef __PFMLIB_S390X_PRIV_H__
#define __PFMLIB_S390X_PRIV_H__

#define CPUMF_COUNTER_MAX	    0xffff
typedef struct {
	uint64_t ctrnum;	    /* counter number */
	unsigned int ctrset;	    /* counter set */
	char *name;		    /* counter ID */
	char *desc;		    /* short description */
} pme_cpumf_ctr_t;

#define min(a, b)	  ((a) < (b) ? (a) : (b))
extern int pfm_s390x_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
extern void pfm_s390x_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);

#endif /* __PFMLIB_S390X_PRIV_H__ */
