/*
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
 *
 * This file is part of libpfm, a performance monitoring support library for
 * applications on Linux/ia64.
 */
#ifndef __PFMLIB_ITANIUM_PRIV_H__
#define __PFMLIB_ITANIUM_PRIV_H__

/*
 * Itanium encoding structure
 * (code must be first 8 bits)
 */
typedef struct {
	unsigned long pme_code:8;	/* major event code */
	unsigned long pme_ear:1;	/* is EAR event */
	unsigned long pme_dear:1;	/* 1=Data 0=Instr */
	unsigned long pme_tlb:1;	/* 1=TLB 0=Cache */
	unsigned long pme_btb:1;	/* 1=BTB */
	unsigned long pme_ig1:4;	/* ignored */
	unsigned long pme_umask:16;	/* unit mask*/
	unsigned long pme_ig:32;	/* ignored */
} pme_ita_entry_code_t;		

#define PME_UMASK_NONE	0x0

typedef union {
	unsigned long  	     pme_vcode;
	pme_ita_entry_code_t pme_ita_code;	/* must not be larger than vcode */
} pme_ita_code_t;

typedef union {
	unsigned long qual;		/* generic qualifier */
	struct {
		unsigned long pme_iar:1;	/* instruction address range supported */
		unsigned long pme_opm:1;	/* opcode match supported */
		unsigned long pme_dar:1;	/* data address range supported */
		unsigned long pme_reserved:61;	/* not used */
	} pme_qual;
} pme_ita_qualifiers_t;

typedef struct {
	char			*pme_name;
	pme_ita_code_t		pme_entry_code;
	unsigned long	 	pme_counters;		/* supported counters */
	unsigned int		pme_maxincr;
	pme_ita_qualifiers_t	pme_qualifiers;
	char			*pme_desc;
} pme_ita_entry_t;

/*
 * We embed the umask value into the event code. Because it really is
 * like a subevent.
 * pme_code:
 * 	- lower 16 bits: major event code
 * 	- upper 16 bits: unit mask
 */
#define pme_code	pme_entry_code.pme_ita_code.pme_code
#define pme_ear		pme_entry_code.pme_ita_code.pme_ear
#define pme_dear	pme_entry_code.pme_ita_code.pme_dear
#define pme_tlb		pme_entry_code.pme_ita_code.pme_tlb
#define pme_btb		pme_entry_code.pme_ita_code.pme_btb
#define pme_umask	pme_entry_code.pme_ita_code.pme_umask
#define pme_used	pme_qualifiers.pme_qual_struct.pme_used

#define event_is_ear(e)	    ((e)->pme_ear == 1)
#define event_is_iear(e)    ((e)->pme_ear == 1 && (e)->pme_dear==0)
#define event_is_dear(e)    ((e)->pme_ear == 1 && (e)->pme_dear==1)
#define event_is_tlb_ear(e) ((e)->pme_ear == 1 && (e)->pme_tlb==1)
#define event_is_btb(e)	    ((e)->pme_btb)

#define event_opcm_ok(e) ((e)->pme_qualifiers.pme_qual.pme_opm==1)
#define event_iarr_ok(e) ((e)->pme_qualifiers.pme_qual.pme_iar==1)
#define event_darr_ok(e) ((e)->pme_qualifiers.pme_qual.pme_dar==1)

#endif /* __PFMLIB_ITANIUM_PRIV_H__ */
