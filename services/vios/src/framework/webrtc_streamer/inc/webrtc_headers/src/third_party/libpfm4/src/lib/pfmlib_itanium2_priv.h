/*
 * Copyright (c) 2002-2006 Hewlett-Packard Development Company, L.P.
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
 * applications on Linux/ia64.
 */
#ifndef __PFMLIB_ITANIUM2_PRIV_H__
#define __PFMLIB_ITANIUM2_PRIV_H__

/*
 * Event type definitions
 *
 * The virtual events are not really defined in the specs but are an artifact used
 * to quickly and easily setup EAR and/or BTB. The event type encodes the exact feature
 * which must be configured in combination with a counting monitor.
 * For instance, DATA_EAR_CACHE_LAT4 is a virtual D-EAR cache event. If the user
 * requests this event, this will configure a counting monitor to count DATA_EAR_EVENTS
 * and PMC11 will be configured for cache mode. The latency is encoded in the umask, here
 * it would correspond to 4 cycles.
 *
 */
#define PFMLIB_ITA2_EVENT_NORMAL	0x0 /* standard counter */
#define PFMLIB_ITA2_EVENT_BTB		0x1 /* virtual event used with BTB configuration */
#define PFMLIB_ITA2_EVENT_IEAR_TLB	0x2 /* virtual event used for I-EAR TLB configuration */	
#define PFMLIB_ITA2_EVENT_IEAR_CACHE	0x3 /* virtual event used for I-EAR cache configuration */	
#define PFMLIB_ITA2_EVENT_DEAR_TLB	0x4 /* virtual event used for D-EAR TLB configuration */	
#define PFMLIB_ITA2_EVENT_DEAR_CACHE	0x5 /* virtual event used for D-EAR cache configuration */	
#define PFMLIB_ITA2_EVENT_DEAR_ALAT	0x6 /* virtual event used for D-EAR ALAT configuration */

#define event_is_ear(e)	       ((e)->pme_type >= PFMLIB_ITA2_EVENT_IEAR_TLB &&(e)->pme_type <= PFMLIB_ITA2_EVENT_DEAR_ALAT)
#define event_is_iear(e)       ((e)->pme_type == PFMLIB_ITA2_EVENT_IEAR_TLB || (e)->pme_type == PFMLIB_ITA2_EVENT_IEAR_CACHE)
#define event_is_dear(e)       ((e)->pme_type >= PFMLIB_ITA2_EVENT_DEAR_TLB && (e)->pme_type <= PFMLIB_ITA2_EVENT_DEAR_ALAT)
#define event_is_ear_cache(e)  ((e)->pme_type == PFMLIB_ITA2_EVENT_DEAR_CACHE || (e)->pme_type == PFMLIB_ITA2_EVENT_IEAR_CACHE)
#define event_is_ear_tlb(e)    ((e)->pme_type == PFMLIB_ITA2_EVENT_IEAR_TLB || (e)->pme_type == PFMLIB_ITA2_EVENT_DEAR_TLB)
#define event_is_ear_alat(e)   ((e)->pme_type == PFMLIB_ITA2_EVENT_DEAR_ALAT)
#define event_is_btb(e)	       ((e)->pme_type == PFMLIB_ITA2_EVENT_BTB)


/*
 * Itanium encoding structure
 * (code must be first 8 bits)
 */
typedef struct {
	unsigned long pme_code:8;	/* major event code */
	unsigned long pme_type:3;	/* see definitions above */
	unsigned long pme_ig1:5;	/* ignored */
	unsigned long pme_umask:16;	/* unit mask*/
	unsigned long pme_ig:32;	/* ignored */
} pme_ita2_entry_code_t;		

typedef union {
	unsigned long  	     pme_vcode;
	pme_ita2_entry_code_t pme_ita2_code;	/* must not be larger than vcode */
} pme_ita2_code_t;

typedef union {
	unsigned long qual;		/* generic qualifier */
	struct {
		unsigned long pme_iar:1;	/* instruction address range supported */
		unsigned long pme_opm:1;	/* opcode match supported */
		unsigned long pme_dar:1;	/* data address range supported */
		unsigned long pme_res1:13;	/* reserved */
		unsigned long pme_group:4;	/* event group */
		unsigned long pme_set:4;	/* event feature set*/
		unsigned long pme_res2:40;	/* reserved */
	} pme_qual;
} pme_ita2_qualifiers_t;

typedef struct {
	char			*pme_name;
	pme_ita2_code_t		pme_entry_code;
	unsigned long	 	pme_counters;		/* supported counters */
	unsigned int		pme_maxincr;
	pme_ita2_qualifiers_t	pme_qualifiers;
	char			*pme_desc;	/* text description of the event */
} pme_ita2_entry_t;


/*
 * We embed the umask value into the event code. Because it really is
 * like a subevent.
 * pme_code:
 * 	- lower 16 bits: major event code
 * 	- upper 16 bits: unit mask
 */
#define pme_code	pme_entry_code.pme_ita2_code.pme_code
#define pme_umask	pme_entry_code.pme_ita2_code.pme_umask
#define pme_used	pme_qualifiers.pme_qual_struct.pme_used
#define pme_type	pme_entry_code.pme_ita2_code.pme_type

#define event_opcm_ok(e) ((e)->pme_qualifiers.pme_qual.pme_opm==1)
#define event_iarr_ok(e) ((e)->pme_qualifiers.pme_qual.pme_iar==1)
#define event_darr_ok(e) ((e)->pme_qualifiers.pme_qual.pme_dar==1)

#endif /* __PFMLIB_ITANIUM2_PRIV_H__ */
