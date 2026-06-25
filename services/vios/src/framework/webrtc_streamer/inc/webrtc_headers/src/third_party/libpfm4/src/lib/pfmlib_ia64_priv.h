/*
 * Copyright (c) 2003-2006 Hewlett-Packard Development Company, L.P.
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
#ifndef __PFMLIB_PRIV_IA64_H__
#define __PFMLIB_PRIV_IA64_H__

/*
 * architected PMC register structure
 */
typedef union {
	unsigned long pmc_val;			/* generic PMC register */

	struct {
		unsigned long pmc_plm:4;	/* privilege level mask */
		unsigned long pmc_ev:1;		/* external visibility */
		unsigned long pmc_oi:1;		/* overflow interrupt */
		unsigned long pmc_pm:1;		/* privileged monitor */
		unsigned long pmc_ig1:1;	/* reserved */
		unsigned long pmc_es:8;		/* event select */
		unsigned long pmc_ig2:48;	/* reserved */
	} pmc_gen_count_reg;

	/* This is the Itanium-specific PMC layout for counter config */
	struct {
		unsigned long pmc_plm:4;	/* privilege level mask */
		unsigned long pmc_ev:1;		/* external visibility */
		unsigned long pmc_oi:1;		/* overflow interrupt */
		unsigned long pmc_pm:1;		/* privileged monitor */
		unsigned long pmc_ig1:1;	/* reserved */
		unsigned long pmc_es:7;		/* event select */
		unsigned long pmc_ig2:1;	/* reserved */
		unsigned long pmc_umask:4;	/* unit mask */
		unsigned long pmc_thres:3;	/* threshold */
		unsigned long pmc_ig3:1;	/* reserved (missing from table on p6-17) */
		unsigned long pmc_ism:2;	/* instruction set mask */
		unsigned long pmc_ig4:38;	/* reserved */
	} pmc_ita_count_reg;

	/* Opcode matcher */
	struct {
		unsigned long ignored1:3;
		unsigned long mask:27;		/* mask encoding bits {40:27}{12:0} */
		unsigned long ignored2:3;	
		unsigned long match:27;		/* match encoding bits {40:27}{12:0} */
		unsigned long b:1;		/* B-syllable */
		unsigned long f:1;		/* F-syllable */
		unsigned long i:1;		/* I-syllable */
		unsigned long m:1;		/* M-syllable */
	} pmc8_9_ita_reg;

	/* Instruction Event Address Registers */
	struct {
		unsigned long iear_plm:4;	/* privilege level mask */
		unsigned long iear_ig1:2;	/* reserved */
		unsigned long iear_pm:1;	/* privileged monitor */
		unsigned long iear_tlb:1;	/* cache/tlb mode */
		unsigned long iear_ig2:8;	/* reserved */
		unsigned long iear_umask:4;	/* unit mask */
		unsigned long iear_ig3:4;	/* reserved */
		unsigned long iear_ism:2;	/* instruction set */
		unsigned long iear_ig4:38;	/* reserved */
	} pmc10_ita_reg;

	/* Data Event Address Registers */
	struct {
		unsigned long dear_plm:4;	/* privilege level mask */
		unsigned long dear_ig1:2;	/* reserved */
		unsigned long dear_pm:1;	/* privileged monitor */
		unsigned long dear_tlb:1;	/* cache/tlb mode */
		unsigned long dear_ig2:8;	/* reserved */
		unsigned long dear_umask:4;	/* unit mask */
		unsigned long dear_ig3:4;	/* reserved */
		unsigned long dear_ism:2;	/* instruction set */
		unsigned long dear_ig4:2;	/* reserved */
		unsigned long dear_pt:1;	/* pass tags */
		unsigned long dear_ig5:35;	/* reserved */
	} pmc11_ita_reg;

	/* Branch Trace Buffer registers */
	struct {
		unsigned long btbc_plm:4;	/* privilege level */
		unsigned long btbc_ig1:2;
		unsigned long btbc_pm:1;	/* privileged monitor */
		unsigned long btbc_tar:1;	/* target address register */
		unsigned long btbc_tm:2;	/* taken mask */
		unsigned long btbc_ptm:2;	/* predicted taken address mask */
		unsigned long btbc_ppm:2;	/* predicted predicate mask */
		unsigned long btbc_bpt:1;	/* branch prediction table */
		unsigned long btbc_bac:1;	/* branch address calculator */
		unsigned long btbc_ig2:48;
	} pmc12_ita_reg;

	struct {
		unsigned long irange_ta:1;	/* tag all bit */
		unsigned long irange_ig:63;
	} pmc13_ita_reg;

	/* This is the Itanium2-specific PMC layout for counter config */
	struct {
		unsigned long pmc_plm:4;	/* privilege level mask */
		unsigned long pmc_ev:1;		/* external visibility */
		unsigned long pmc_oi:1;		/* overflow interrupt */
		unsigned long pmc_pm:1;		/* privileged monitor */
		unsigned long pmc_ig1:1;	/* reserved */
		unsigned long pmc_es:8;		/* event select */
		unsigned long pmc_umask:4;	/* unit mask */
		unsigned long pmc_thres:3;	/* threshold */
		unsigned long pmc_enable:1;	/* pmc4 only: power enable bit */
		unsigned long pmc_ism:2;	/* instruction set mask */
		unsigned long pmc_ig2:38;	/* reserved */
	} pmc_ita2_counter_reg;

	/* opcode matchers */
	struct {
		unsigned long opcm_ig_ad:1;	/* ignore instruction address range checking */
		unsigned long opcm_inv:1;	/* invert range check */
		unsigned long opcm_bit2:1;	/* must be 1 */
		unsigned long opcm_mask:27;	/* mask encoding bits {41:27}{12:0} */
		unsigned long opcm_ig1:3;	/* reserved */
		unsigned long opcm_match:27;	/* match encoding bits {41:27}{12:0} */
		unsigned long opcm_b:1;		/* B-syllable */
		unsigned long opcm_f:1;		/* F-syllable */
		unsigned long opcm_i:1;		/* I-syllable */
		unsigned long opcm_m:1;		/* M-syllable */
	} pmc8_9_ita2_reg;

	/*
	 * instruction event address register configuration
	 *
	 * The register has two layout depending on the value of the ct field.
	 * In cache mode(ct=1x):
	 * 	- ct is 1 bit, umask is 8 bits
	 * In TLB mode (ct=00):
	 * 	- ct is 2 bits, umask is 7 bits
	 * ct=11 <=> cache mode and use a latency with eighth bit set
	 * ct=01 => nothing monitored
	 *
	 * The ct=01 value is the only reason why we cannot fix the layout
	 * to ct 1 bit and umask 8 bits. Even though in TLB mode, only 6 bits
	 * are effectively used for the umask, if the user inadvertently use
	 * a umask with the most significant bit set, it would be equivalent
	 * to no monitoring.
	 */
	struct {
		unsigned long iear_plm:4;	/* privilege level mask */
		unsigned long iear_pm:1;	/* privileged monitor */
		unsigned long iear_umask:8;	/* event unit mask: 7 bits in TLB mode, 8 bits in cache mode */
		unsigned long iear_ct:1;	/* cache tlb bit13: 0 for TLB mode, 1 for cache mode  */
		unsigned long iear_ism:2;	/* instruction set */
		unsigned long iear_ig4:48;	/* reserved */
	} pmc10_ita2_cache_reg;

	struct {
		unsigned long iear_plm:4;	/* privilege level mask */
		unsigned long iear_pm:1;	/* privileged monitor */
		unsigned long iear_umask:7;	/* event unit mask: 7 bits in TLB mode, 8 bits in cache mode */
		unsigned long iear_ct:2;	/* cache tlb bit13: 0 for TLB mode, 1 for cache mode  */
		unsigned long iear_ism:2;	/* instruction set */
		unsigned long iear_ig4:48;	/* reserved */
	} pmc10_ita2_tlb_reg;

	/* data event address register configuration */
	struct {
		unsigned long dear_plm:4;	/* privilege level mask */
		unsigned long dear_ig1:2;	/* reserved */
		unsigned long dear_pm:1;	/* privileged monitor */
		unsigned long dear_mode:2;	/* mode */
		unsigned long dear_ig2:7;	/* reserved */
		unsigned long dear_umask:4;	/* unit mask */
		unsigned long dear_ig3:4;	/* reserved */
		unsigned long dear_ism:2;	/* instruction set */
		unsigned long dear_ig4:38;	/* reserved */
	} pmc11_ita2_reg;

	/* branch trace buffer configuration register */
	struct {
		unsigned long btbc_plm:4;	/* privilege level */
		unsigned long btbc_ig1:2;
		unsigned long btbc_pm:1;	/* privileged monitor */
		unsigned long btbc_ds:1;	/* data selector */
		unsigned long btbc_tm:2;	/* taken mask */
		unsigned long btbc_ptm:2;	/* predicted taken address mask */
		unsigned long btbc_ppm:2;	/* predicted predicate mask */
		unsigned long btbc_brt:2;	/* branch type mask */
		unsigned long btbc_ig2:48;
	} pmc12_ita2_reg;

	/* data address range configuration register */
	struct {
		unsigned long darc_ig1:3;
		unsigned long darc_cfg_dbrp0:2;	/* constraint on dbr0 */
		unsigned long darc_ig2:6;
		unsigned long darc_cfg_dbrp1:2;	/* constraint on dbr1 */
		unsigned long darc_ig3:6;
		unsigned long darc_cfg_dbrp2:2;	/* constraint on dbr2 */
		unsigned long darc_ig4:6;
		unsigned long darc_cfg_dbrp3:2;	/* constraint on dbr3 */
		unsigned long darc_ig5:16;
		unsigned long darc_ena_dbrp0:1;	/* enable constraint dbr0 */
		unsigned long darc_ena_dbrp1:1;	/* enable constraint dbr1 */
		unsigned long darc_ena_dbrp2:1;	/* enable constraint dbr2 */
		unsigned long darc_ena_dbrp3:1; 	/* enable constraint dbr3 */
		unsigned long darc_ig6:15;
	} pmc13_ita2_reg;

	/* instruction address range configuration register */
	struct {
		unsigned long iarc_ig1:1;
		unsigned long iarc_ibrp0:1;	/* constrained by ibr0 */
		unsigned long iarc_ig2:2;
		unsigned long iarc_ibrp1:1;	/* constrained by ibr1 */
		unsigned long iarc_ig3:2;
		unsigned long iarc_ibrp2:1;	/* constrained by ibr2 */
		unsigned long iarc_ig4:2;
		unsigned long iarc_ibrp3:1;	/* constrained by ibr3 */
		unsigned long iarc_ig5:2;
		unsigned long iarc_fine:1;	/* fine mode */
		unsigned long iarc_ig6:50;
	} pmc14_ita2_reg;

	/* opcode matcher configuration register */
	struct {
		unsigned long	opcmc_ibrp0_pmc8:1;
		unsigned long	opcmc_ibrp1_pmc9:1;
		unsigned long	opcmc_ibrp2_pmc8:1;
		unsigned long	opcmc_ibrp3_pmc9:1;
		unsigned long 	opcmc_ig1:60;
	} pmc15_ita2_reg;


	/* This is the Montecito-specific PMC layout for counters PMC4-PMC15 */
	struct {
		unsigned long pmc_plm:4;	/* privilege level mask */
		unsigned long pmc_ev:1;		/* external visibility */
		unsigned long pmc_oi:1;		/* overflow interrupt */
		unsigned long pmc_pm:1;		/* privileged monitor */
		unsigned long pmc_ig1:1;	/* ignored */
		unsigned long pmc_es:8;		/* event select */
		unsigned long pmc_umask:4;	/* unit mask */
		unsigned long pmc_thres:3;	/* threshold */
		unsigned long pmc_ig2:1;	/* ignored */
		unsigned long pmc_ism:2;	/* instruction set: must be 2  */
		unsigned long pmc_all:1;	/* 0=only self, 1=both threads */
		unsigned long pmc_i:1;		/* Invalidate */
		unsigned long pmc_s:1;		/* Shared */
		unsigned long pmc_e:1;		/* Exclusive */
		unsigned long pmc_m:1;		/* Modified */
		unsigned long pmc_res3:33;	/* reserved */
	} pmc_mont_counter_reg;

	/* opcode matchers mask registers */
	struct {
		unsigned long opcm_mask:41;	/* opcode mask */
		unsigned long opcm_ig1:7;	/* ignored */
		unsigned long opcm_b:1;		/* B-syllable  */
		unsigned long opcm_f:1;		/* F-syllable  */
		unsigned long opcm_i:1;		/* I-syllable  */
		unsigned long opcm_m:1;		/* M-syllable  */
		unsigned long opcm_ig2:4;	/* ignored */
		unsigned long opcm_inv:1;	/* inverse range for ibrp0 */
		unsigned long opcm_ig_ad:1;	/* ignore address range restrictions */
		unsigned long opcm_ig3:6;	/* ignored */
	} pmc32_34_mont_reg;

	/* opcode matchers match registers */
	struct {
		unsigned long opcm_match:41;	/* opcode match */
		unsigned long opcm_ig1:23;	/* ignored */
	} pmc33_35_mont_reg;

	/* opcode matcher config register */
	struct {
		unsigned long opcm_ch0_ig_opcm:1;	/* chan0 opcode constraint */
		unsigned long opcm_ch1_ig_opcm:1;	/* chan1 opcode constraint */
		unsigned long opcm_ch2_ig_opcm:1;	/* chan2 opcode constraint */
		unsigned long opcm_ch3_ig_opcm:1;	/* chan3 opcode constraint */
		unsigned long opcm_res:28;		/* reserved */
		unsigned long opcm_ig:32;		/* ignored */
	} pmc36_mont_reg;

	/*
	 * instruction event address register configuration (I-EAR)
	 *
	 * The register has two layouts depending on the value of the ct field.
	 * In cache mode(ct=1x):
	 * 	- ct is 1 bit, umask is 8 bits
	 * In TLB mode (ct=0x):
	 * 	- ct is 2 bits, umask is 7 bits
	 * ct=11 => cache mode using a latency filter with eighth bit set
	 * ct=01 => nothing monitored
	 *
	 * The ct=01 value is the only reason why we cannot fix the layout
	 * to ct 1 bit and umask 8 bits. Even though in TLB mode, only 6 bits
	 * are effectively used for the umask, if the user inadvertently sets
	 * a umask with the most significant bit set, it would be equivalent
	 * to no monitoring.
	 */
	struct {
		unsigned long iear_plm:4;	/* privilege level mask */
		unsigned long iear_pm:1;	/* privileged monitor */
		unsigned long iear_umask:8;	/* event unit mask */
		unsigned long iear_ct:1;	/* =1 for i-cache */
		unsigned long iear_res:2;	/* reserved */
		unsigned long iear_ig:48;	/* ignored */
	} pmc37_mont_cache_reg;

	struct {
		unsigned long iear_plm:4;	/* privilege level mask */
		unsigned long iear_pm:1;	/* privileged monitor */
		unsigned long iear_umask:7;	/* event unit mask */
		unsigned long iear_ct:2;	/* 00=i-tlb, 01=nothing 1x=illegal */
		unsigned long iear_res:50;	/* reserved */
	} pmc37_mont_tlb_reg;

	/* data event address register configuration (D-EAR) */
	struct {
		unsigned long dear_plm:4;	/* privilege level mask */
		unsigned long dear_ig1:2;	/* ignored */
		unsigned long dear_pm:1;	/* privileged monitor */
		unsigned long dear_mode:2;	/* mode */
		unsigned long dear_ig2:7;	/* ignored */
		unsigned long dear_umask:4;	/* unit mask */
		unsigned long dear_ig3:4;	/* ignored */
		unsigned long dear_ism:2;	/* instruction set: must be 2 */
		unsigned long dear_ig4:38;	/* ignored */
	} pmc40_mont_reg;

	/* IP event address register (IP-EAR) */
	struct {
		unsigned long ipear_plm:4;	/* privilege level mask */
		unsigned long ipear_ig1:2;	/* ignored */
		unsigned long ipear_pm:1;	/* privileged monitor */
		unsigned long ipear_ig2:1;	/* ignored */
		unsigned long ipear_mode:3;	/* mode */
		unsigned long ipear_delay:8;	/* delay */
		unsigned long ipear_ig3:45;	/* reserved */
	} pmc42_mont_reg;
			
	/* execution trace buffer configuration register (ETB) */
	struct {
		unsigned long etbc_plm:4;	/* privilege level */
		unsigned long etbc_res1:2;	/* reserved */
		unsigned long etbc_pm:1;	/* privileged monitor */
		unsigned long etbc_ds:1;	/* data selector */
		unsigned long etbc_tm:2;	/* taken mask */
		unsigned long etbc_ptm:2;	/* predicted taken address mask */
		unsigned long etbc_ppm:2;	/* predicted predicate mask */
		unsigned long etbc_brt:2;	/* branch type mask */
		unsigned long etbc_ig:48;	/* ignored */
	} pmc39_mont_reg;

	/* data address range configuration register */
	struct {
		unsigned long darc_res1:3;	/* reserved */
		unsigned long darc_cfg_dtag0:2;	/* constraints on dbrp0 */
		unsigned long darc_res2:6;	/* reserved */
		unsigned long darc_cfg_dtag1:2;	/* constraints on dbrp1 */
		unsigned long darc_res3:6;	/* reserved */
		unsigned long darc_cfg_dtag2:2;	/* constraints on dbrp2 */
		unsigned long darc_res4:6;	/* reserved */
		unsigned long darc_cfg_dtag3:2;	/* constraints on dbrp3 */
		unsigned long darc_res5:16;	/* reserved */
		unsigned long darc_ena_dbrp0:1;	/* enable constraints dbrp0 */
		unsigned long darc_ena_dbrp1:1;	/* enable constraints dbrp1 */
		unsigned long darc_ena_dbrp2:1;	/* enable constraints dbrp2 */
		unsigned long darc_ena_dbrp3:1; /* enable constraint dbr3 */
		unsigned long darc_res6:15;
	} pmc41_mont_reg;

	/* instruction address range configuration register */
	struct {
		unsigned long iarc_res1:1;	/* reserved */
		unsigned long iarc_ig_ibrp0:1;	/* constrained by ibrp0 */
		unsigned long iarc_res2:2;	/* reserved */
		unsigned long iarc_ig_ibrp1:1;	/* constrained by ibrp1 */
		unsigned long iarc_res3:2;	/* reserved */
		unsigned long iarc_ig_ibrp2:1;	/* constrained by ibrp2 */
		unsigned long iarc_res4:2;	/* reserved */
		unsigned long iarc_ig_ibrp3:1;	/* constrained by ibrp3 */
		unsigned long iarc_res5:2;	/* reserved */
		unsigned long iarc_fine:1;	/* fine mode */
		unsigned long iarc_ig6:50;	/* reserved */
	} pmc38_mont_reg;

} pfm_gen_ia64_pmc_reg_t;

typedef struct {
	unsigned long	pmd_val;	/* generic counter value */

	/* counting pmd register */
	struct {
		unsigned long pmd_count:32;	/* 32-bit hardware counter  */
		unsigned long pmd_sxt32:32;	/* sign extension of bit 32 */
	} pmd_ita_counter_reg;

	struct {
		unsigned long iear_v:1;		/* valid bit */
		unsigned long iear_tlb:1;	/* tlb miss bit */
		unsigned long iear_ig1:3;	/* reserved */
		unsigned long iear_icla:59;	/* instruction cache line address {60:51} sxt {50}*/
	} pmd0_ita_reg;

	struct {
		unsigned long iear_lat:12;	/* latency */
		unsigned long iear_ig1:52;	/* reserved */
	} pmd1_ita_reg;

	struct {
		unsigned long dear_daddr;	/* data address */
	} pmd2_ita_reg;

	struct {
		unsigned long dear_latency:12;	/* latency */
		unsigned long dear_ig1:50;	/* reserved */
		unsigned long dear_level:2;	/* level */
	} pmd3_ita_reg;

	struct {
		unsigned long btb_b:1;		/* branch bit */
		unsigned long btb_mp:1;		/* mispredict bit */
		unsigned long btb_slot:2;	/* which slot, 3=not taken branch */
		unsigned long btb_addr:60;	/* b=1, bundle address, b=0 target address */
	} pmd8_15_ita_reg;

	struct {
		unsigned long btbi_bbi:3;	/* branch buffer index */
		unsigned long btbi_full:1;	/* full bit (sticky) */
		unsigned long btbi_ignored:60;
	} pmd16_ita_reg;

	struct {
		unsigned long dear_vl:1;	/* valid bit */
		unsigned long dear_ig1:1;	/* reserved */
		unsigned long dear_slot:2;	/* slot number */
		unsigned long dear_iaddr:60;	/* instruction address */
	} pmd17_ita_reg;

	/* counting pmd register */
	struct {
		unsigned long pmd_count:47;	/* 47-bit hardware counter  */
		unsigned long pmd_sxt47:17;	/* sign extension of bit 46 */
	} pmd_ita2_counter_reg;

	/* instruction event address register: data address register */
	struct {
		unsigned long iear_stat:2;	/* status bit */
		unsigned long iear_ig1:3;
		unsigned long iear_iaddr:59;	/* instruction cache line address {60:51} sxt {50}*/
	} pmd0_ita2_reg;

	/* instruction event address register: data address register */
	struct {
		unsigned long iear_latency:12;	/* latency */
		unsigned long iear_overflow:1;	/* latency overflow */
		unsigned long iear_ig1:51;	/* reserved */
	} pmd1_ita2_reg;

	/* data event address register: data address register */
	struct {
		unsigned long dear_daddr;	/* data address */
	} pmd2_ita2_reg;

	/* data event address register: data address register */
	struct {
		unsigned long dear_latency:13;	/* latency  */
		unsigned long dear_overflow:1;	/* overflow */
		unsigned long dear_stat:2;	/* status   */
		unsigned long dear_ig1:48;	/* ignored  */
	} pmd3_ita2_reg;

	/* branch trace buffer data register when pmc12.ds == 0 */
	struct {
		unsigned long btb_b:1;		/* branch bit */
		unsigned long btb_mp:1;		/* mispredict bit */
		unsigned long btb_slot:2;	/* which slot, 3=not taken branch */
		unsigned long btb_addr:60;	/* bundle address(b=1), target address(b=0) */
	} pmd8_15_ita2_reg;

	/* branch trace buffer data register when pmc12.ds == 1 */
	struct {
		unsigned long btb_b:1;		/* branch bit */
		unsigned long btb_mp:1;		/* mispredict bit */
		unsigned long btb_slot:2;	/* which slot, 3=not taken branch */
		unsigned long btb_loaddr:37;	/* b=1, bundle address, b=0 target address */
		unsigned long btb_pred:20;	/* low 20bits of L1IBR */
		unsigned long btb_hiaddr:3;	/* hi 3bits of bundle address(b=1) or target address (b=0)*/
	} pmd8_15_ds_ita2_reg;

	/* branch trace buffer index register */
	struct {
		unsigned long btbi_bbi:3;		/* next entry index  */
		unsigned long btbi_full:1;		/* full bit (sticky) */
		unsigned long btbi_pmd8ext_b1:1;	/* pmd8 ext  */
		unsigned long btbi_pmd8ext_bruflush:1;	/* pmd8 ext  */
		unsigned long btbi_pmd8ext_ig:2;	/* pmd8 ext  */
		unsigned long btbi_pmd9ext_b1:1;	/* pmd9 ext  */
		unsigned long btbi_pmd9ext_bruflush:1;	/* pmd9 ext  */
		unsigned long btbi_pmd9ext_ig:2;	/* pmd9 ext  */
		unsigned long btbi_pmd10ext_b1:1;	/* pmd10 ext */
		unsigned long btbi_pmd10ext_bruflush:1;	/* pmd10 ext */
		unsigned long btbi_pmd10ext_ig:2;	/* pmd10 ext */
		unsigned long btbi_pmd11ext_b1:1;	/* pmd11 ext */
		unsigned long btbi_pmd11ext_bruflush:1;	/* pmd11 ext */
		unsigned long btbi_pmd11ext_ig:2;	/* pmd11 ext */
		unsigned long btbi_pmd12ext_b1:1;	/* pmd12 ext */
		unsigned long btbi_pmd12ext_bruflush:1;	/* pmd12 ext */
		unsigned long btbi_pmd12ext_ig:2;	/* pmd12 ext */
		unsigned long btbi_pmd13ext_b1:1;	/* pmd13 ext */
		unsigned long btbi_pmd13ext_bruflush:1;	/* pmd13 ext */
		unsigned long btbi_pmd13ext_ig:2;	/* pmd13 ext */
		unsigned long btbi_pmd14ext_b1:1;	/* pmd14 ext */
		unsigned long btbi_pmd14ext_bruflush:1;	/* pmd14 ext */
		unsigned long btbi_pmd14ext_ig:2;	/* pmd14 ext */
		unsigned long btbi_pmd15ext_b1:1;	/* pmd15 ext */
		unsigned long btbi_pmd15ext_bruflush:1;	/* pmd15 ext */
		unsigned long btbi_pmd15ext_ig:2;	/* pmd15 ext */
		unsigned long btbi_ignored:28;
	} pmd16_ita2_reg;

	/* data event address register: data address register */
	struct {
		unsigned long dear_slot:2;	/* slot   */
		unsigned long dear_bn:1;	/* bundle bit (if 1 add 16 to address) */
		unsigned long dear_vl:1;	/* valid  */
		unsigned long dear_iaddr:60;	/* instruction address (2-bundle window)*/
	} pmd17_ita2_reg;

	struct {
		unsigned long pmd_count:47;	/* 47-bit hardware counter  */
		unsigned long pmd_sxt47:17;	/* sign extension of bit 46 */
	} pmd_mont_counter_reg;

	/* data event address register */
	struct {
		unsigned long dear_daddr;	/* data address */
	} pmd32_mont_reg;

	/* data event address register (D-EAR) */
	struct {
		unsigned long dear_latency:13;	/* latency  */
		unsigned long dear_ov:1;	/* latency overflow */
		unsigned long dear_stat:2;	/* status   */
		unsigned long dear_ig:48;	/* ignored */
	} pmd33_mont_reg;

	/* instruction event address register (I-EAR) */
	struct {
		unsigned long iear_stat:2;	/* status bit */
		unsigned long iear_ig:3;	/* ignored */
		unsigned long iear_iaddr:59;	/* instruction cache line address {60:51} sxt {50}*/
	} pmd34_mont_reg;

	/* instruction event address register (I-EAR) */
	struct {
		unsigned long iear_latency:12;	/* latency */
		unsigned long iear_ov:1;	/* latency overflow */
		unsigned long iear_ig:51;	/* ignored */
	} pmd35_mont_reg;

	/* data event address register (D-EAR) */
	struct {
		unsigned long dear_slot:2;	/* slot */
		unsigned long dear_bn:1;	/* bundle bit (if 1 add 16 to iaddr) */
		unsigned long dear_vl:1;	/* valid */
		unsigned long dear_iaddr:60;	/* instruction address (2-bundle window)*/
	} pmd36_mont_reg;

	/* execution trace buffer index register (ETB) */
	struct {
		unsigned long etbi_ebi:4;	/* next entry index  */
		unsigned long etbi_ig1:1;	/* ignored */
		unsigned long etbi_full:1;	/* ETB overflowed at least once */
		unsigned long etbi_ig2:58;	/* ignored */
	} pmd38_mont_reg;

	/* execution trace buffer extension register (ETB) */
	struct {
		unsigned long etb_pmd48ext_b1:1;	/* pmd48 ext  */
		unsigned long etb_pmd48ext_bruflush:1;	/* pmd48 ext  */
		unsigned long etb_pmd48ext_res:2;	/* reserved   */

		unsigned long etb_pmd56ext_b1:1;	/* pmd56 ext */
		unsigned long etb_pmd56ext_bruflush:1;	/* pmd56 ext */
		unsigned long etb_pmd56ext_res:2;	/* reserved  */ 

		unsigned long etb_pmd49ext_b1:1;	/* pmd49 ext  */
		unsigned long etb_pmd49ext_bruflush:1;	/* pmd49 ext  */
		unsigned long etb_pmd49ext_res:2;	/* reserved   */

		unsigned long etb_pmd57ext_b1:1;	/* pmd57 ext */
		unsigned long etb_pmd57ext_bruflush:1;	/* pmd57 ext */
		unsigned long etb_pmd57ext_res:2;	/* reserved  */

		unsigned long etb_pmd50ext_b1:1;	/* pmd50 ext */
		unsigned long etb_pmd50ext_bruflush:1;	/* pmd50 ext */
		unsigned long etb_pmd50ext_res:2;	/* reserved  */

		unsigned long etb_pmd58ext_b1:1;	/* pmd58 ext */
		unsigned long etb_pmd58ext_bruflush:1;	/* pmd58 ext */
		unsigned long etb_pmd58ext_res:2;	/* reserved  */

		unsigned long etb_pmd51ext_b1:1;	/* pmd51 ext */
		unsigned long etb_pmd51ext_bruflush:1;	/* pmd51 ext */
		unsigned long etb_pmd51ext_res:2;	/* reserved  */

		unsigned long etb_pmd59ext_b1:1;	/* pmd59 ext */
		unsigned long etb_pmd59ext_bruflush:1;	/* pmd59 ext */
		unsigned long etb_pmd59ext_res:2;	/* reserved  */

		unsigned long etb_pmd52ext_b1:1;	/* pmd52 ext */
		unsigned long etb_pmd52ext_bruflush:1;	/* pmd52 ext */
		unsigned long etb_pmd52ext_res:2;	/* reserved  */

		unsigned long etb_pmd60ext_b1:1;	/* pmd60 ext */
		unsigned long etb_pmd60ext_bruflush:1;	/* pmd60 ext */
		unsigned long etb_pmd60ext_res:2;	/* reserved  */

		unsigned long etb_pmd53ext_b1:1;	/* pmd53 ext */
		unsigned long etb_pmd53ext_bruflush:1;	/* pmd53 ext */
		unsigned long etb_pmd53ext_res:2;	/* reserved  */

		unsigned long etb_pmd61ext_b1:1;	/* pmd61 ext */
		unsigned long etb_pmd61ext_bruflush:1;	/* pmd61 ext */
		unsigned long etb_pmd61ext_res:2;	/* reserved  */

		unsigned long etb_pmd54ext_b1:1;	/* pmd54 ext */
		unsigned long etb_pmd54ext_bruflush:1;	/* pmd54 ext */
		unsigned long etb_pmd54ext_res:2;	/* reserved  */

		unsigned long etb_pmd62ext_b1:1;	/* pmd62 ext */
		unsigned long etb_pmd62ext_bruflush:1;	/* pmd62 ext */
		unsigned long etb_pmd62ext_res:2;	/* reserved  */

		unsigned long etb_pmd55ext_b1:1;	/* pmd55 ext */
		unsigned long etb_pmd55ext_bruflush:1;	/* pmd55 ext */
		unsigned long etb_pmd55ext_res:2;	/* reserved  */

		unsigned long etb_pmd63ext_b1:1;	/* pmd63 ext */
		unsigned long etb_pmd63ext_bruflush:1;	/* pmd63 ext */
		unsigned long etb_pmd63ext_res:2;	/* reserved  */
	} pmd39_mont_reg;

	/*
	 * execution trace buffer extension register when used with IP-EAR
	 *
	 * to be used in conjunction with pmd48_63_ipear_reg (see  below)
	 */
	struct {
		unsigned long ipear_pmd48ext_cycles:2;	/* pmd48 upper 2 bits of cycles */
		unsigned long ipear_pmd48ext_f:1;	/* pmd48 flush bit    */
		unsigned long ipear_pmd48ext_ef:1;	/* pmd48 early freeze */

		unsigned long ipear_pmd56ext_cycles:2;	/* pmd56 upper 2 bits of cycles */
		unsigned long ipear_pmd56ext_f:1;	/* pmd56 flush bit    */
		unsigned long ipear_pmd56ext_ef:1;	/* pmd56 early freeze */

		unsigned long ipear_pmd49ext_cycles:2;	/* pmd49 upper 2 bits of cycles */
		unsigned long ipear_pmd49ext_f:1;	/* pmd49 flush bit    */
		unsigned long ipear_pmd49ext_ef:1;	/* pmd49 early freeze */

		unsigned long ipear_pmd57ext_cycles:2;	/* pmd57 upper 2 bits of cycles */
		unsigned long ipear_pmd57ext_f:1;	/* pmd57 flush bit    */
		unsigned long ipear_pmd57ext_ef:1;	/* pmd57 early freeze */

		unsigned long ipear_pmd50ext_cycles:2;	/* pmd50 upper 2 bits of cycles */
		unsigned long ipear_pmd50ext_f:1;	/* pmd50 flush bit    */
		unsigned long ipear_pmd50ext_ef:1;	/* pmd50 early freeze */

		unsigned long ipear_pmd58ext_cycles:2;	/* pmd58 upper 2 bits of cycles */
		unsigned long ipear_pmd58ext_f:1;	/* pmd58 flush bit    */
		unsigned long ipear_pmd58ext_ef:1;	/* pmd58 early freeze */

		unsigned long ipear_pmd51ext_cycles:2;	/* pmd51 upper 2 bits of cycles */
		unsigned long ipear_pmd51ext_f:1;	/* pmd51 flush bit    */
		unsigned long ipear_pmd51ext_ef:1;	/* pmd51 early freeze */

		unsigned long ipear_pmd59ext_cycles:2;	/* pmd59 upper 2 bits of cycles */
		unsigned long ipear_pmd59ext_f:1;	/* pmd59 flush bit    */
		unsigned long ipear_pmd59ext_ef:1;	/* pmd59 early freeze */

		unsigned long ipear_pmd52ext_cycles:2;	/* pmd52 upper 2 bits of cycles */
		unsigned long ipear_pmd52ext_f:1;	/* pmd52 flush bit    */
		unsigned long ipear_pmd52ext_ef:1;	/* pmd52 early freeze */

		unsigned long ipear_pmd60ext_cycles:2;	/* pmd60 upper 2 bits of cycles */
		unsigned long ipear_pmd60ext_f:1;	/* pmd60 flush bit    */
		unsigned long ipear_pmd60ext_ef:1;	/* pmd60  early freeze */

		unsigned long ipear_pmd53ext_cycles:2;	/* pmd53 upper 2 bits of cycles */
		unsigned long ipear_pmd53ext_f:1;	/* pmd53 flush bit    */
		unsigned long ipear_pmd53ext_ef:1;	/* pmd53 early freeze */

		unsigned long ipear_pmd61ext_cycles:2;	/* pmd61 upper 2 bits of cycles */
		unsigned long ipear_pmd61ext_f:1;	/* pmd61 flush bit    */
		unsigned long ipear_pmd61ext_ef:1;	/* pmd61 early freeze */

		unsigned long ipear_pmd54ext_cycles:2;	/* pmd54 upper 2 bits of cycles */
		unsigned long ipear_pmd54ext_f:1;	/* pmd54 flush bit    */
		unsigned long ipear_pmd54ext_ef:1;	/* pmd54 early freeze */

		unsigned long ipear_pmd62ext_cycles:2;	/* pmd62 upper 2 bits of cycles */
		unsigned long ipear_pmd62ext_f:1;	/* pmd62 flush bit    */
		unsigned long ipear_pmd62ext_ef:1;	/* pmd62 early freeze */

		unsigned long ipear_pmd55ext_cycles:2;	/* pmd55 upper 2 bits of cycles */
		unsigned long ipear_pmd55ext_f:1;	/* pmd55 flush bit    */
		unsigned long ipear_pmd55ext_ef:1;	/* pmd55 early freeze */

		unsigned long ipear_pmd63ext_cycles:2;	/* pmd63 upper 2 bits of cycles */
		unsigned long ipear_pmd63ext_f:1;	/* pmd63 flush bit    */
		unsigned long ipear_pmd63ext_ef:1;	/* pmd63 early freeze */
	} pmd39_ipear_mont_reg;

	/* 
	 * execution trace buffer data register (ETB)
	 *
	 * when pmc39.ds == 0: pmd48-63 contains branch targets
	 * when pmc39.ds == 1: pmd48-63 content is undefined
	 */
	struct {
		unsigned long etb_s:1;		/* source bit */
		unsigned long etb_mp:1;		/* mispredict bit */
		unsigned long etb_slot:2;	/* which slot, 3=not taken branch */
		unsigned long etb_addr:60;	/* bundle address(s=1), target address(s=0) */
	} pmd48_63_etb_mont_reg;

	/* 
	 * execution trace buffer when used with IP-EAR with PMD48-63.ef=0
	 *
	 * The cycles field straddles pmdXX and corresponding extension in
	 * pmd39 (pmd39_ipear_mont_reg). For instance, cycles for pmd48: 
	 *
	 * cycles= pmd39_ipear_mont_reg.etb_pmd48ext_cycles << 4
	 *       | pmd48_63_etb_ipear_mont_reg.etb_cycles
	 */
	struct {
		unsigned long	ipear_addr:60;	/* retired IP[63:4]      */
		unsigned long	ipear_cycles:4;	/* lower 4 bit of cycles */
	} pmd48_63_ipear_mont_reg;

	/* 
	 * execution trace buffer when used with IP-EAR with PMD48-63.ef=1
	 *
	 * The cycles field straddles pmdXX and corresponding extension in
	 * pmd39 (pmd39_ipear_mont_reg). For instance, cycles for pmd48: 
	 *
	 * cycles= pmd39_ipear_mont_reg.etb_pmd48ext_cycles << 4
	 *       | pmd48_63_etb_ipear_ef_mont_reg.etb_cycles
	 */

	struct {
		unsigned long	ipear_delay:8;	/* delay count           */
		unsigned long	ipear_addr:52;	/* retired IP[61:12]     */
		unsigned long	ipear_cycles:4;	/* lower 5 bit of cycles */
	} pmd48_63_ipear_ef_mont_reg;

} pfm_gen_ia64_pmd_reg_t;

#define PFMLIB_ITA2_FL_EVT_NO_QUALCHECK	0x1 /* don't check qualifier constraints */
#define PFMLIB_ITA2_RR_INV		0x1 /* inverse instruction ranges (iranges only) */
#define PFMLIB_ITA2_RR_NO_FINE_MODE	0x2 /* force non fine mode for instruction ranges */
#define PFMLIB_ITA2_EVT_NO_GRP		 0 /* event does not belong to a group */
#define PFMLIB_ITA2_EVT_L1_CACHE_GRP	 1 /* event belongs to L1 Cache group */
#define PFMLIB_ITA2_EVT_L2_CACHE_GRP	 2 /* event belongs to L2 Cache group */
#define PFMLIB_ITA2_EVT_NO_SET		-1 /* event does not belong to a set */

/*
 * counter specific flags
 */
#define PFMLIB_MONT_FL_EVT_NO_QUALCHECK	0x1 /* don't check qualifier constraints */
#define PFMLIB_MONT_FL_EVT_ALL_THRD	0x2 /* event measured for both threads */
#define PFMLIB_MONT_FL_EVT_ACTIVE_ONLY	0x4 /* measure the event only when the thread is active */
#define PFMLIB_MONT_FL_EVT_ALWAYS	0x8 /* measure the event at all times (active or inactive) */

#define PFMLIB_MONT_RR_INV		0x1 /* inverse instruction ranges (iranges only) */
#define PFMLIB_MONT_RR_NO_FINE_MODE	0x2 /* force non fine mode for instruction ranges */
#define PFMLIB_MONT_IRR_DEMAND_FETCH	0x4 /* demand fetch only for dual events */
#define PFMLIB_MONT_IRR_PREFETCH_MATCH	0x8 /* regular prefetches for dual events */

#define PFMLIB_MONT_EVT_NO_GRP		 0 /* event does not belong to a group */
#define PFMLIB_MONT_EVT_L1D_CACHE_GRP	 1 /* event belongs to L1D Cache group */
#define PFMLIB_MONT_EVT_L2D_CACHE_GRP	 2 /* event belongs to L2D Cache group */

#define PFMLIB_MONT_EVT_NO_SET		-1 /* event does not belong to a set */

#define PFMLIB_MONT_EVT_ACTIVE		 0 /* event measures only when thread is active */
#define PFMLIB_MONT_EVT_FLOATING	 1
#define PFMLIB_MONT_EVT_CAUSAL		 2
#define PFMLIB_MONT_EVT_SELF_FLOATING	 3 /* floating with .self, causal otherwise */



typedef struct {
	unsigned long db_mask:56;
	unsigned long db_plm:4;
	unsigned long db_ig:2;
	unsigned long db_w:1;
	unsigned long db_rx:1;
} br_mask_reg_t;

typedef union {
	unsigned long  val;
	br_mask_reg_t  db;
} dbreg_t;

static inline int
pfm_ia64_get_cpu_family(void)
{
	return (int)((ia64_get_cpuid(3) >> 24) & 0xff);
}

static inline int
pfm_ia64_get_cpu_model(void)
{
	return (int)((ia64_get_cpuid(3) >> 16) & 0xff);
}

/*
 * find last bit set
 */
static inline int
pfm_ia64_fls (unsigned long x)
{
	double d = x;
	long exp;

	exp = ia64_getf(d);
	return exp - 0xffff;

}
#endif /* __PFMLIB_PRIV_IA64_H__ */
