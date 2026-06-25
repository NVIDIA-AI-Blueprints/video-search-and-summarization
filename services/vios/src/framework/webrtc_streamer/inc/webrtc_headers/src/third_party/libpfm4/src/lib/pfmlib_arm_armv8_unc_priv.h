#ifndef PFMLIB_ARM_ARMV8_UNC_PRIV_H
#define PFMLIB_ARM_ARMV8_UNC_PRIV_H

#include <sys/types.h>

typedef union {
	uint64_t val;
	struct {
		unsigned long unc_res1:32;	/* reserved */
	} com; /* reserved space for future extensions */
} tx2_unc_data_t;

typedef struct {
	uint64_t val;
} kunpeng_unc_data_t;

extern int pfm_tx2_unc_get_perf_encoding(void *this, pfmlib_event_desc_t *e);

//extern int pfm_kunpeng_get_perf_encoding(void *this, pfmlib_event_desc_t *e);

extern int pfm_kunpeng_unc_get_event_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_kunpeng_unc_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
#endif /* PFMLIB_ARM_ARMV8_UNC_PRIV_H */
