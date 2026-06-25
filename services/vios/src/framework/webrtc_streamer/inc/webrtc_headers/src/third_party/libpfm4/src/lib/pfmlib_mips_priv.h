/*
 * Copyright (c) 2011 Samara Technology Group, Inc
 * Contributed by Philip Mucci <phil.mucci@@samaratechnologygroup.com>
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
#ifndef __PFMLIB_MIPS_PRIV_H__
#define __PFMLIB_MIPS_PRIV_H__

/*
 * This file contains the definitions used for MIPS processors
 */


/*
 * event description
 */
typedef struct {
	const char			*name;	/* event name */
	const char			*desc;	/* event description */
	unsigned int			mask;   /* which counters event lives on */
	unsigned int			code; 	/* event code */
} mips_entry_t;

#if __BYTE_ORDER == __LITTLE_ENDIAN

typedef union {
	uint64_t	val;				/* complete register value */
	struct {
	        unsigned long sel_exl:1;		/* int level */
		unsigned long sel_os:1;			/* system level */
		unsigned long sel_sup:1;		/* supervisor level */
		unsigned long sel_usr:1;		/* user level */
	        unsigned long sel_int:1;		/* enable intr */
		unsigned long sel_event_mask:7;         /* event mask */
		unsigned long sel_res1:20;              /* reserved */
		unsigned long sel_res2:32;		/* reserved */
	} perfsel64;
} pfm_mips_sel_reg_t;

#elif __BYTE_ORDER == __BIG_ENDIAN

typedef union {
	uint64_t	val;				/* complete register value */
	struct {
		unsigned long sel_res2:32;		/* reserved */
		unsigned long sel_res1:20;              /* reserved */
		unsigned long sel_event_mask:7;         /* event mask */
	        unsigned long sel_int:1;		/* enable intr */
		unsigned long sel_usr:1;		/* user level */
		unsigned long sel_sup:1;		/* supervisor level */
		unsigned long sel_os:1;			/* system level */
	        unsigned long sel_exl:1;		/* int level */
	} perfsel64;
} pfm_mips_sel_reg_t;

#else
#error "cannot determine endianess"
#endif

typedef struct {
  char model[1024];
  int implementer;
  int architecture;
  int part;
} pfm_mips_config_t;

extern pfm_mips_config_t pfm_mips_cfg;

#define MIPS_ATTR_K	0 /* system level */
#define MIPS_ATTR_U	1 /* user level */
#define MIPS_ATTR_S	2 /* supervisor level */
#define MIPS_ATTR_E	3 /* exception level */
#define MIPS_NUM_ATTRS	4

#define _MIPS_ATTR_K  (1 << MIPS_ATTR_K)
#define _MIPS_ATTR_U  (1 << MIPS_ATTR_U)
#define _MIPS_ATTR_S  (1 << MIPS_ATTR_S)
#define _MIPS_ATTR_E  (1 << MIPS_ATTR_E)

#define MIPS_PLM_ALL (	_MIPS_ATTR_K |\
			_MIPS_ATTR_U |\
			_MIPS_ATTR_S |\
			_MIPS_ATTR_E)

extern int pfm_mips_detect(void *this);
extern int pfm_mips_get_encoding(void *this, pfmlib_event_desc_t *e);
extern int pfm_mips_get_event_first(void *this);
extern int pfm_mips_get_event_next(void *this, int idx);
extern int pfm_mips_event_is_valid(void *this, int pidx);
extern int pfm_mips_validate_table(void *this, FILE *fp);
extern int pfm_mips_get_event_attr_info(void *this, int pidx, int attr_idx, pfmlib_event_attr_info_t *info);
extern int pfm_mips_get_event_info(void *this, int idx, pfm_event_info_t *info);
extern unsigned int pfm_mips_get_event_nattrs(void *this, int pidx);

extern void pfm_mips_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);
extern int pfm_mips_get_perf_encoding(void *this, pfmlib_event_desc_t *e);

#endif /* __PFMLIB_MIPS_PRIV_H__ */
