/****************************/
/* THIS IS OPEN SOURCE CODE */
/****************************/

#ifndef __PFMLIB_POWER_PRIV_H__
#define __PFMLIB_POWER_PRIV_H__

/*
* File:    pfmlib_power_priv.h
* CVS:
* Author:  Corey Ashford
*          cjashfor@us.ibm.com
* Mods:    <your name here>
*          <your email address>
*
* (C) Copyright IBM Corporation, 2009.  All Rights Reserved.
* Contributed by Corey Ashford <cjashfor.ibm.com>
*
*/
typedef struct {
   uint64_t pme_code;
   const char *pme_name;
   const char *pme_short_desc;
   const char *pme_long_desc;
} pme_power_entry_t;

typedef struct {
   const char *pme_name;
   const char *pme_desc;
   unsigned pme_code;
   uint64_t pme_modmsk;
} pme_torrent_entry_t;

/* Attribute "type "for PowerBus MCD events */
#define TORRENT_ATTR_MCD_TYPE		0
/* Attribute "sel" for PowerBus bus utilization events */
#define TORRENT_ATTR_UTIL_SEL		1
/* Attribute "lo_cmp" for PowerBus utilization events */
#define TORRENT_ATTR_UTIL_LO_CMP	2
/* Attribute "hi_cmp" for PowerBus utilization events */
#define TORRENT_ATTR_UTIL_HI_CMP	3

#define _TORRENT_ATTR_MCD_TYPE		(1 << TORRENT_ATTR_MCD_TYPE)
#define _TORRENT_ATTR_MCD		(_TORRENT_ATTR_MCD_TYPE)
#define _TORRENT_ATTR_UTIL_SEL		(1 << TORRENT_ATTR_UTIL_SEL)
#define _TORRENT_ATTR_UTIL_LO_CMP	(1 << TORRENT_ATTR_UTIL_LO_CMP)
#define _TORRENT_ATTR_UTIL_HI_CMP	(1 << TORRENT_ATTR_UTIL_HI_CMP)
#define _TORRENT_ATTR_UTIL_LO		(_TORRENT_ATTR_UTIL_SEL | \
					_TORRENT_ATTR_UTIL_LO_CMP)
#define _TORRENT_ATTR_UTIL_HI		(_TORRENT_ATTR_UTIL_SEL | \
					_TORRENT_ATTR_UTIL_HI_CMP)

/*
 * These definitions were taken from the reg.h file which, until Linux
 * 2.6.18, resided in /usr/include/asm-ppc64.  Most of the unneeded
 * definitions have been removed, but there are still a few in this file
 * that are currently unused by libpfm.
 */

#ifndef _POWER_REG_H
#define _POWER_REG_H

#define __stringify_1(x)	#x
#define __stringify(x)		__stringify_1(x)

#ifdef __powerpc__
#define mfspr(rn)	({unsigned long rval; \
			asm volatile("mfspr %0," __stringify(rn) \
				: "=r" (rval)); rval;})
#else
#define mfspr(rn)	(0)
#endif

/* Special Purpose Registers (SPRNs)*/
#define SPRN_PVR	0x11F	/* Processor Version Register */

/* Processor Version Register (PVR) field extraction */

#define PVR_VER(pvr)	(((pvr) >>  16) & 0xFFFF)	/* Version field */
#define PVR_REV(pvr)	(((pvr) >>   0) & 0xFFFF)	/* Revision field */

#define __is_processor(pv)	(PVR_VER(mfspr(SPRN_PVR)) == (pv))

/* 64-bit processors */
#define PV_POWER4	0x0035
#define PV_POWER4p	0x0038
#define PV_970		0x0039
#define PV_POWER5	0x003A
#define PV_POWER5p	0x003B
#define PV_970FX	0x003C
#define PV_POWER6	0x003E
#define PV_POWER7	0x003F
#define PV_POWER7p	0x004a
#define PV_970MP	0x0044
#define PV_970GX	0x0045
#define PV_POWER8E	0x004b
#define PV_POWER8NVL	0x004c
#define PV_POWER8	0x004d
#define PV_POWER9	0x004e
#define PV_POWER10	0x0080

#define POWER_PLM (PFM_PLM0|PFM_PLM3)
#define POWER8_PLM (POWER_PLM|PFM_PLMH)
#define POWER9_PLM (POWER_PLM|PFM_PLMH)
#define POWER10_PLM (POWER_PLM|PFM_PLMH)

extern int pfm_gen_powerpc_get_event_info(void *this, int pidx, pfm_event_info_t *info);
extern int pfm_gen_powerpc_get_event_attr_info(void *this, int pidx, int umask_idx, pfmlib_event_attr_info_t *info);
extern int pfm_gen_powerpc_get_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_gen_powerpc_get_event_first(void *this);
extern int pfm_gen_powerpc_get_event_next(void *this, int idx);
extern int pfm_gen_powerpc_event_is_valid(void *this, int pidx);
extern int pfm_gen_powerpc_validate_table(void *this, FILE *fp);
extern void pfm_gen_powerpc_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);

extern int pfm_gen_powerpc_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_gen_powerpc_get_nest_perf_encoding(void *this, pfmlib_event_desc_t *e);
#endif /* _POWER_REG_H */
#endif

