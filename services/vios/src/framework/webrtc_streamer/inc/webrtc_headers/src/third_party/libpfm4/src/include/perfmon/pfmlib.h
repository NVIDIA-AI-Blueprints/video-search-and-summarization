/*
 * Copyright (c) 2009 Google, Inc
 * Contributed by Stephane Eranian <eranian@gmail.com>
 *
 * Copyright (c) 2022-2026, NVIDIA CORPORATION & AFFILIATES.
 * Contributed by John Linford <jlinford@nvidia.com>
 * Contributed by Thomas Makin <tmakin@nvidia.com>
 *
 * Based on:
 * Copyright (c) 2001-2007 Hewlett-Packard Development Company, L.P.
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
 */
#ifndef __PFMLIB_H__
#define __PFMLIB_H__

#pragma GCC visibility push(default)

#ifdef __cplusplus
extern "C" {
#endif
#include <sys/types.h>
#include <unistd.h>
#include <inttypes.h>
#include <stdio.h>

#define LIBPFM_VERSION		(4 << 16 | 0)
#define PFM_MAJ_VERSION(v)	((v)>>16)
#define PFM_MIN_VERSION(v)	((v) & 0xffff)

/*
 * ABI revision level
 */
#define LIBPFM_ABI_VERSION	0

/*
 * priv level mask (for dfl_plm)
 */
#define PFM_PLM0	0x01 /* kernel */
#define PFM_PLM1	0x02 /* not yet used */
#define PFM_PLM2	0x04 /* not yet used */
#define PFM_PLM3	0x08 /* priv level 3, 2, 1 (x86) */
#define PFM_PLMH	0x10 /* hypervisor */

/*
 * Performance Event Source
 *
 * The source is what is providing events.
 * It can be:
 * 	- Hardware Performance Monitoring Unit (PMU)
 * 	- a particular kernel subsystem
 *
 * Identifiers are guaranteed constant across libpfm revisions
 *
 * New sources must be added at the end before PFM_PMU_MAX
 */
typedef enum {
	PFM_PMU_NONE= 0,		/* no PMU */
	PFM_PMU_GEN_IA64,	 	/* Intel IA-64 architected PMU */
	PFM_PMU_ITANIUM,	 	/* Intel Itanium   */
	PFM_PMU_ITANIUM2,		/* Intel Itanium 2 */
	PFM_PMU_MONTECITO,		/* Intel Dual-Core Itanium 2 9000 */
	PFM_PMU_AMD64,			/* AMD AMD64 (obsolete) */
	PFM_PMU_I386_P6,		/* Intel PIII (P6 core) */
	PFM_PMU_INTEL_NETBURST,		/* Intel Netburst (Pentium 4) */
	PFM_PMU_INTEL_NETBURST_P,	/* Intel Netburst Prescott (Pentium 4) */
	PFM_PMU_COREDUO,		/* Intel Core Duo/Core Solo */
	PFM_PMU_I386_PM,		/* Intel Pentium M */
	PFM_PMU_INTEL_CORE,		/* Intel Core */
	PFM_PMU_INTEL_PPRO,		/* Intel Pentium Pro */
	PFM_PMU_INTEL_PII,		/* Intel Pentium II */
	PFM_PMU_INTEL_ATOM,		/* Intel Atom */
	PFM_PMU_INTEL_NHM,		/* Intel Nehalem core PMU */
	PFM_PMU_INTEL_NHM_EX,		/* Intel Nehalem-EX core PMU */
	PFM_PMU_INTEL_NHM_UNC,		/* Intel Nehalem uncore PMU */
	PFM_PMU_INTEL_X86_ARCH,		/* Intel X86 architectural PMU */

	PFM_PMU_MIPS_20KC,		/* MIPS 20KC */
	PFM_PMU_MIPS_24K,		/* MIPS 24K */
	PFM_PMU_MIPS_25KF,		/* MIPS 25KF */
	PFM_PMU_MIPS_34K,		/* MIPS 34K */
	PFM_PMU_MIPS_5KC,		/* MIPS 5KC */
	PFM_PMU_MIPS_74K,		/* MIPS 74K */
	PFM_PMU_MIPS_R10000,		/* MIPS R10000 */
	PFM_PMU_MIPS_R12000,		/* MIPS R12000 */
	PFM_PMU_MIPS_RM7000,		/* MIPS RM7000 */
	PFM_PMU_MIPS_RM9000,		/* MIPS RM9000 */
	PFM_PMU_MIPS_SB1,		/* MIPS SB1/SB1A */
	PFM_PMU_MIPS_VR5432,		/* MIPS VR5432 */
	PFM_PMU_MIPS_VR5500,		/* MIPS VR5500 */
	PFM_PMU_MIPS_ICE9A,		/* SiCortex ICE9A */
	PFM_PMU_MIPS_ICE9B,		/* SiCortex ICE9B */
	PFM_PMU_POWERPC,		/* POWERPC */
	PFM_PMU_CELL,			/* IBM CELL */

	PFM_PMU_SPARC_ULTRA12,		/* UltraSPARC I, II, IIi, and IIe */
	PFM_PMU_SPARC_ULTRA3,		/* UltraSPARC III */
	PFM_PMU_SPARC_ULTRA3I,		/* UltraSPARC IIIi and IIIi+ */
	PFM_PMU_SPARC_ULTRA3PLUS,	/* UltraSPARC III+ and IV */
	PFM_PMU_SPARC_ULTRA4PLUS,	/* UltraSPARC IV+ */
	PFM_PMU_SPARC_NIAGARA1,		/* Niagara-1 */
	PFM_PMU_SPARC_NIAGARA2,		/* Niagara-2 */

	PFM_PMU_PPC970,			/* IBM PowerPC 970(FX,GX) */
	PFM_PMU_PPC970MP,		/* IBM PowerPC 970MP */
	PFM_PMU_POWER3,			/* IBM POWER3 */
	PFM_PMU_POWER4,			/* IBM POWER4 */
	PFM_PMU_POWER5,			/* IBM POWER5 */
	PFM_PMU_POWER5p,		/* IBM POWER5+ */
	PFM_PMU_POWER6,			/* IBM POWER6 */
	PFM_PMU_POWER7,			/* IBM POWER7 */

	PFM_PMU_PERF_EVENT,		/* perf_event PMU */
	PFM_PMU_INTEL_WSM,		/* Intel Westmere single-socket (Clarkdale) */
	PFM_PMU_INTEL_WSM_DP,		/* Intel Westmere dual-socket (Westmere-EP, Gulftwon) */
	PFM_PMU_INTEL_WSM_UNC,		/* Intel Westmere uncore PMU */

	PFM_PMU_AMD64_K7,		/* AMD AMD64 K7 */
	PFM_PMU_AMD64_K8_REVB,		/* AMD AMD64 K8 RevB */
	PFM_PMU_AMD64_K8_REVC,		/* AMD AMD64 K8 RevC */
	PFM_PMU_AMD64_K8_REVD,		/* AMD AMD64 K8 RevD */
	PFM_PMU_AMD64_K8_REVE,		/* AMD AMD64 K8 RevE */
	PFM_PMU_AMD64_K8_REVF,		/* AMD AMD64 K8 RevF */
	PFM_PMU_AMD64_K8_REVG,		/* AMD AMD64 K8 RevG */
	PFM_PMU_AMD64_FAM10H_BARCELONA,	/* AMD AMD64 Fam10h Barcelona RevB */
	PFM_PMU_AMD64_FAM10H_SHANGHAI,	/* AMD AMD64 Fam10h Shanghai RevC  */
	PFM_PMU_AMD64_FAM10H_ISTANBUL,	/* AMD AMD64 Fam10h Istanbul RevD  */

	PFM_PMU_ARM_CORTEX_A8,		/* ARM Cortex A8 */
	PFM_PMU_ARM_CORTEX_A9,		/* ARM Cortex A9 */

	PFM_PMU_TORRENT,		/* IBM Torrent hub chip */

	PFM_PMU_INTEL_SNB,		/* Intel Sandy Bridge (single socket) */
	PFM_PMU_AMD64_FAM14H_BOBCAT,	/* AMD AMD64 Fam14h Bobcat */
	PFM_PMU_AMD64_FAM15H_INTERLAGOS,/* AMD AMD64 Fam15h Interlagos */

	PFM_PMU_INTEL_SNB_EP,		/* Intel SandyBridge EP */
	PFM_PMU_AMD64_FAM12H_LLANO,	/* AMD AMD64 Fam12h Llano */
	PFM_PMU_AMD64_FAM11H_TURION,	/* AMD AMD64 Fam11h Turion */
	PFM_PMU_INTEL_IVB,		/* Intel IvyBridge */
	PFM_PMU_ARM_CORTEX_A15,		/* ARM Cortex A15 */

	PFM_PMU_INTEL_SNB_UNC_CB0,	/* Intel SandyBridge C-box 0 uncore PMU */
	PFM_PMU_INTEL_SNB_UNC_CB1,	/* Intel SandyBridge C-box 1 uncore PMU */
	PFM_PMU_INTEL_SNB_UNC_CB2,	/* Intel SandyBridge C-box 2 uncore PMU */
	PFM_PMU_INTEL_SNB_UNC_CB3,	/* Intel SandyBridge C-box 3 uncore PMU */

	PFM_PMU_INTEL_SNBEP_UNC_CB0,	/* Intel SandyBridge-EP C-Box core 0 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB1,	/* Intel SandyBridge-EP C-Box core 1 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB2,	/* Intel SandyBridge-EP C-Box core 2 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB3,	/* Intel SandyBridge-EP C-Box core 3 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB4,	/* Intel SandyBridge-EP C-Box core 4 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB5,	/* Intel SandyBridge-EP C-Box core 5 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB6,	/* Intel SandyBridge-EP C-Box core 6 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_CB7,	/* Intel SandyBridge-EP C-Box core 7 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_HA,	/* Intel SandyBridge-EP HA uncore */
	PFM_PMU_INTEL_SNBEP_UNC_IMC0,	/* Intel SandyBridge-EP IMC socket 0 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_IMC1,	/* Intel SandyBridge-EP IMC socket 1 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_IMC2,	/* Intel SandyBridge-EP IMC socket 2 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_IMC3,	/* Intel SandyBridge-EP IMC socket 3 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_PCU,	/* Intel SandyBridge-EP PCU uncore */
	PFM_PMU_INTEL_SNBEP_UNC_QPI0,	/* Intel SandyBridge-EP QPI link 0 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_QPI1,	/* Intel SandyBridge-EP QPI link 1 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_UBOX,	/* Intel SandyBridge-EP U-Box uncore */
	PFM_PMU_INTEL_SNBEP_UNC_R2PCIE,	/* Intel SandyBridge-EP R2PCIe uncore */
	PFM_PMU_INTEL_SNBEP_UNC_R3QPI0,	/* Intel SandyBridge-EP R3QPI 0 uncore */
	PFM_PMU_INTEL_SNBEP_UNC_R3QPI1,	/* Intel SandyBridge-EP R3QPI 1 uncore */
	PFM_PMU_INTEL_KNC,		/* Intel Knights Corner (Xeon Phi) */

	PFM_PMU_S390X_CPUM_CF,		/* s390x: CPU-M counter facility */

	PFM_PMU_ARM_1176,		/* ARM 1176 */

	PFM_PMU_INTEL_IVB_EP,		/* Intel IvyBridge EP */
	PFM_PMU_INTEL_HSW,		/* Intel Haswell */

	PFM_PMU_INTEL_IVB_UNC_CB0,	/* Intel IvyBridge C-box 0 uncore PMU */
	PFM_PMU_INTEL_IVB_UNC_CB1,	/* Intel IvyBridge C-box 1 uncore PMU */
	PFM_PMU_INTEL_IVB_UNC_CB2,	/* Intel IvyBridge C-box 2 uncore PMU */
	PFM_PMU_INTEL_IVB_UNC_CB3,	/* Intel IvyBridge C-box 3 uncore PMU */

	PFM_PMU_POWER8,			/* IBM POWER8 */
	PFM_PMU_INTEL_RAPL,		/* Intel RAPL */

	PFM_PMU_INTEL_SLM,		/* Intel Silvermont */
	PFM_PMU_AMD64_FAM15H_NB,	/* AMD AMD64 Fam15h NorthBridge */

	PFM_PMU_ARM_QCOM_KRAIT,		/* Qualcomm Krait */
	PFM_PMU_PERF_EVENT_RAW,		/* perf_events RAW event syntax */

	PFM_PMU_INTEL_IVBEP_UNC_CB0,	/* Intel IvyBridge-EP C-Box core 0 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB1,	/* Intel IvyBridge-EP C-Box core 1 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB2,	/* Intel IvyBridge-EP C-Box core 2 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB3,	/* Intel IvyBridge-EP C-Box core 3 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB4,	/* Intel IvyBridge-EP C-Box core 4 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB5,	/* Intel IvyBridge-EP C-Box core 5 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB6,	/* Intel IvyBridge-EP C-Box core 6 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB7,	/* Intel IvyBridge-EP C-Box core 7 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB8,	/* Intel IvyBridge-EP C-Box core 8 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB9,	/* Intel IvyBridge-EP C-Box core 9 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB10,	/* Intel IvyBridge-EP C-Box core 10 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB11,	/* Intel IvyBridge-EP C-Box core 11 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB12,	/* Intel IvyBridge-EP C-Box core 12 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB13,	/* Intel IvyBridge-EP C-Box core 13 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_CB14,	/* Intel IvyBridge-EP C-Box core 14 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_HA0,	/* Intel IvyBridge-EP HA 0 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_HA1,	/* Intel IvyBridge-EP HA 1 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC0,	/* Intel IvyBridge-EP IMC socket 0 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC1,	/* Intel IvyBridge-EP IMC socket 1 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC2,	/* Intel IvyBridge-EP IMC socket 2 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC3,	/* Intel IvyBridge-EP IMC socket 3 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC4,	/* Intel IvyBridge-EP IMC socket 4 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC5,	/* Intel IvyBridge-EP IMC socket 5 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC6,	/* Intel IvyBridge-EP IMC socket 6 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IMC7,	/* Intel IvyBridge-EP IMC socket 7 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_PCU,	/* Intel IvyBridge-EP PCU uncore */
	PFM_PMU_INTEL_IVBEP_UNC_QPI0,	/* Intel IvyBridge-EP QPI link 0 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_QPI1,	/* Intel IvyBridge-EP QPI link 1 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_QPI2,	/* Intel IvyBridge-EP QPI link 2 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_UBOX,	/* Intel IvyBridge-EP U-Box uncore */
	PFM_PMU_INTEL_IVBEP_UNC_R2PCIE,	/* Intel IvyBridge-EP R2PCIe uncore */
	PFM_PMU_INTEL_IVBEP_UNC_R3QPI0,	/* Intel IvyBridge-EP R3QPI 0 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_R3QPI1,	/* Intel IvyBridge-EP R3QPI 1 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_R3QPI2,	/* Intel IvyBridge-EP R3QPI 2 uncore */
	PFM_PMU_INTEL_IVBEP_UNC_IRP,	/* Intel IvyBridge-EP IRP uncore */

	PFM_PMU_S390X_CPUM_SF,		/* s390x: CPU-M sampling facility */

	PFM_PMU_ARM_CORTEX_A57,		/* ARM Cortex A57 (ARMv8) */
	PFM_PMU_ARM_CORTEX_A53,		/* ARM Cortex A53 (ARMv8) */

	PFM_PMU_ARM_CORTEX_A7,		/* ARM Cortex A7 */

	PFM_PMU_INTEL_HSW_EP,		/* Intel Haswell EP */
	PFM_PMU_INTEL_BDW,		/* Intel Broadwell */

	PFM_PMU_ARM_XGENE,		/* Applied Micro X-Gene (ARMv8) */

	PFM_PMU_INTEL_HSWEP_UNC_CB0,	/* Intel Haswell-EP C-Box core 0 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB1,	/* Intel Haswell-EP C-Box core 1 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB2,	/* Intel Haswell-EP C-Box core 2 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB3,	/* Intel Haswell-EP C-Box core 3 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB4,	/* Intel Haswell-EP C-Box core 4 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB5,	/* Intel Haswell-EP C-Box core 5 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB6,	/* Intel Haswell-EP C-Box core 6 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB7,	/* Intel Haswell-EP C-Box core 7 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB8,	/* Intel Haswell-EP C-Box core 8 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB9,	/* Intel Haswell-EP C-Box core 9 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB10,	/* Intel Haswell-EP C-Box core 10 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB11,	/* Intel Haswell-EP C-Box core 11 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB12,	/* Intel Haswell-EP C-Box core 12 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB13,	/* Intel Haswell-EP C-Box core 13 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB14,	/* Intel Haswell-EP C-Box core 14 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB15,	/* Intel Haswell-EP C-Box core 15 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB16,	/* Intel Haswell-EP C-Box core 16 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_CB17,	/* Intel Haswell-EP C-Box core 17 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_HA0,	/* Intel Haswell-EP HA 0 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_HA1,	/* Intel Haswell-EP HA 1 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC0,	/* Intel Haswell-EP IMC socket 0 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC1,	/* Intel Haswell-EP IMC socket 1 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC2,	/* Intel Haswell-EP IMC socket 2 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC3,	/* Intel Haswell-EP IMC socket 3 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC4,	/* Intel Haswell-EP IMC socket 4 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC5,	/* Intel Haswell-EP IMC socket 5 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC6,	/* Intel Haswell-EP IMC socket 6 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IMC7,	/* Intel Haswell-EP IMC socket 7 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_PCU,	/* Intel Haswell-EP PCU uncore */
	PFM_PMU_INTEL_HSWEP_UNC_QPI0,	/* Intel Haswell-EP QPI link 0 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_QPI1,	/* Intel Haswell-EP QPI link 1 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_UBOX,	/* Intel Haswell-EP U-Box uncore */
	PFM_PMU_INTEL_HSWEP_UNC_R2PCIE,	/* Intel Haswell-EP R2PCIe uncore */
	PFM_PMU_INTEL_HSWEP_UNC_R3QPI0,	/* Intel Haswell-EP R3QPI 0 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_R3QPI1,	/* Intel Haswell-EP R3QPI 1 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_R3QPI2,	/* Intel Haswell-EP R3QPI 2 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_IRP,	/* Intel Haswell-EP IRP uncore */
	PFM_PMU_INTEL_HSWEP_UNC_SB0,	/* Intel Haswell-EP S-Box 0 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_SB1,	/* Intel Haswell-EP S-Box 1 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_SB2,	/* Intel Haswell-EP S-Box 2 uncore */
	PFM_PMU_INTEL_HSWEP_UNC_SB3,	/* Intel Haswell-EP S-Box 3 uncore */

	PFM_PMU_POWERPC_NEST_MCS_READ_BW,   /* POWERPC Nest Memory Read bandwidth */
	PFM_PMU_POWERPC_NEST_MCS_WRITE_BW,  /* POWERPC Nest Memory Write bandwidth */

	PFM_PMU_INTEL_SKL,		/* Intel Skylake */

	PFM_PMU_INTEL_BDW_EP,		/* Intel Broadwell EP */

	PFM_PMU_INTEL_GLM,		/* Intel Goldmont */

	PFM_PMU_INTEL_KNL,		/* Intel Knights Landing */
	PFM_PMU_INTEL_KNL_UNC_IMC0,	/* Intel KnightLanding IMC channel 0 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC1,	/* Intel KnightLanding IMC channel 1 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC2,	/* Intel KnightLanding IMC channel 2 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC3,	/* Intel KnightLanding IMC channel 3 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC4,	/* Intel KnightLanding IMC channel 4 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC5,	/* Intel KnightLanding IMC channel 5 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC_UCLK0,/* Intel KnightLanding IMC UCLK unit 0 uncore */
	PFM_PMU_INTEL_KNL_UNC_IMC_UCLK1,/* Intel KnightLanding IMC UCLK unit 1 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK0,/* Intel KnightLanding EDC ECLK unit 0 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK1,/* Intel KnightLanding EDC ECLK unit 1 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK2,/* Intel KnightLanding EDC ECLK unit 2 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK3,/* Intel KnightLanding EDC ECLK unit 3 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK4,/* Intel KnightLanding EDC ECLK unit 4 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK5,/* Intel KnightLanding EDC ECLK unit 5 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK6,/* Intel KnightLanding EDC ECLK unit 6 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_ECLK7,/* Intel KnightLanding EDC ECLK unit 7 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK0,/* Intel KnightLanding EDC UCLK unit 0 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK1,/* Intel KnightLanding EDC UCLK unit 1 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK2,/* Intel KnightLanding EDC UCLK unit 2 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK3,/* Intel KnightLanding EDC UCLK unit 3 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK4,/* Intel KnightLanding EDC UCLK unit 4 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK5,/* Intel KnightLanding EDC UCLK unit 5 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK6,/* Intel KnightLanding EDC UCLK unit 6 uncore */
	PFM_PMU_INTEL_KNL_UNC_EDC_UCLK7,/* Intel KnightLanding EDC UCLK unit 7 uncore */

	PFM_PMU_INTEL_KNL_UNC_CHA0,	/* Intel KnightLanding CHA unit 0 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA1,	/* Intel KnightLanding CHA unit 1 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA2,	/* Intel KnightLanding CHA unit 2 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA3,	/* Intel KnightLanding CHA unit 3 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA4,	/* Intel KnightLanding CHA unit 4 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA5,	/* Intel KnightLanding CHA unit 5 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA6,	/* Intel KnightLanding CHA unit 6 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA7,	/* Intel KnightLanding CHA unit 7 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA8,	/* Intel KnightLanding CHA unit 8 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA9,	/* Intel KnightLanding CHA unit 9 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA10,	/* Intel KnightLanding CHA unit 10 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA11,	/* Intel KnightLanding CHA unit 11 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA12,	/* Intel KnightLanding CHA unit 12 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA13,	/* Intel KnightLanding CHA unit 13 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA14,	/* Intel KnightLanding CHA unit 14 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA15,	/* Intel KnightLanding CHA unit 15 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA16,	/* Intel KnightLanding CHA unit 16 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA17,	/* Intel KnightLanding CHA unit 17 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA18,	/* Intel KnightLanding CHA unit 18 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA19,	/* Intel KnightLanding CHA unit 19 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA20,	/* Intel KnightLanding CHA unit 20 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA21,	/* Intel KnightLanding CHA unit 21 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA22,	/* Intel KnightLanding CHA unit 22 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA23,	/* Intel KnightLanding CHA unit 23 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA24,	/* Intel KnightLanding CHA unit 24 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA25,	/* Intel KnightLanding CHA unit 25 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA26,	/* Intel KnightLanding CHA unit 26 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA27,	/* Intel KnightLanding CHA unit 27 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA28,	/* Intel KnightLanding CHA unit 28 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA29,	/* Intel KnightLanding CHA unit 29 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA30,	/* Intel KnightLanding CHA unit 30 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA31,	/* Intel KnightLanding CHA unit 31 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA32,	/* Intel KnightLanding CHA unit 32 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA33,	/* Intel KnightLanding CHA unit 33 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA34,	/* Intel KnightLanding CHA unit 34 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA35,	/* Intel KnightLanding CHA unit 35 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA36,	/* Intel KnightLanding CHA unit 36 uncore */
	PFM_PMU_INTEL_KNL_UNC_CHA37,	/* Intel KnightLanding CHA unit 37 uncore */

	PFM_PMU_INTEL_KNL_UNC_UBOX,	/* Intel KnightLanding Ubox uncore */
	PFM_PMU_INTEL_KNL_UNC_M2PCIE,	/* Intel KnightLanding M2PCIe uncore */

	PFM_PMU_POWER9,			/* IBM POWER9 */

	PFM_PMU_INTEL_BDX_UNC_CB0,	/* Intel Broadwell-X C-Box core 0 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB1,	/* Intel Broadwell-X C-Box core 1 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB2,	/* Intel Broadwell-X C-Box core 2 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB3,	/* Intel Broadwell-X C-Box core 3 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB4,	/* Intel Broadwell-X C-Box core 4 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB5,	/* Intel Broadwell-X C-Box core 5 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB6,	/* Intel Broadwell-X C-Box core 6 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB7,	/* Intel Broadwell-X C-Box core 7 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB8,	/* Intel Broadwell-X C-Box core 8 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB9,	/* Intel Broadwell-X C-Box core 9 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB10,	/* Intel Broadwell-X C-Box core 10 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB11,	/* Intel Broadwell-X C-Box core 11 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB12,	/* Intel Broadwell-X C-Box core 12 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB13,	/* Intel Broadwell-X C-Box core 13 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB14,	/* Intel Broadwell-X C-Box core 14 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB15,	/* Intel Broadwell-X C-Box core 15 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB16,	/* Intel Broadwell-X C-Box core 16 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB17,	/* Intel Broadwell-X C-Box core 17 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB18,	/* Intel Broadwell-X C-Box core 18 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB19,	/* Intel Broadwell-X C-Box core 19 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB20,	/* Intel Broadwell-X C-Box core 20 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB21,	/* Intel Broadwell-X C-Box core 21 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB22,	/* Intel Broadwell-X C-Box core 22 uncore */
	PFM_PMU_INTEL_BDX_UNC_CB23,	/* Intel Broadwell-X C-Box core 23 uncore */
	PFM_PMU_INTEL_BDX_UNC_HA0,	/* Intel Broadwell-X HA 0 uncore */
	PFM_PMU_INTEL_BDX_UNC_HA1,	/* Intel Broadwell-X HA 1 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC0,	/* Intel Broadwell-X IMC socket 0 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC1,	/* Intel Broadwell-X IMC socket 1 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC2,	/* Intel Broadwell-X IMC socket 2 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC3,	/* Intel Broadwell-X IMC socket 3 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC4,	/* Intel Broadwell-X IMC socket 4 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC5,	/* Intel Broadwell-X IMC socket 5 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC6,	/* Intel Broadwell-X IMC socket 6 uncore */
	PFM_PMU_INTEL_BDX_UNC_IMC7,	/* Intel Broadwell-X IMC socket 7 uncore */
	PFM_PMU_INTEL_BDX_UNC_PCU,	/* Intel Broadwell-X PCU uncore */
	PFM_PMU_INTEL_BDX_UNC_QPI0,	/* Intel Broadwell-X QPI link 0 uncore */
	PFM_PMU_INTEL_BDX_UNC_QPI1,	/* Intel Broadwell-X QPI link 1 uncore */
	PFM_PMU_INTEL_BDX_UNC_QPI2,	/* Intel Broadwell-X QPI link 2 uncore */
	PFM_PMU_INTEL_BDX_UNC_UBOX,	/* Intel Broadwell-X U-Box uncore */
	PFM_PMU_INTEL_BDX_UNC_R2PCIE,	/* Intel Broadwell-X R2PCIe uncore */
	PFM_PMU_INTEL_BDX_UNC_R3QPI0,	/* Intel Broadwell-X R3QPI 0 uncore */
	PFM_PMU_INTEL_BDX_UNC_R3QPI1,	/* Intel Broadwell-X R3QPI 1 uncore */
	PFM_PMU_INTEL_BDX_UNC_R3QPI2,	/* Intel Broadwell-X R3QPI 2 uncore */
	PFM_PMU_INTEL_BDX_UNC_IRP,	/* Intel Broadwell-X IRP uncore */
	PFM_PMU_INTEL_BDX_UNC_SB0,	/* Intel Broadwell-X S-Box 0 uncore */
	PFM_PMU_INTEL_BDX_UNC_SB1,	/* Intel Broadwell-X S-Box 1 uncore */
	PFM_PMU_INTEL_BDX_UNC_SB2,	/* Intel Broadwell-X S-Box 2 uncore */
	PFM_PMU_INTEL_BDX_UNC_SB3,	/* Intel Broadwell-X S-Box 3 uncore */

	PFM_PMU_AMD64_FAM17H,		/* AMD AMD64 Fam17h Zen1 (deprecated) */
	PFM_PMU_AMD64_FAM16H,		/* AMD AMD64 Fam16h Jaguar */

	PFM_PMU_INTEL_SKX,		/* Intel Skylake-X */

	PFM_PMU_INTEL_SKX_UNC_CHA0,	/* Intel Skylake-X CHA core 0 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA1,	/* Intel Skylake-X CHA core 1 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA2,	/* Intel Skylake-X CHA core 2 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA3,	/* Intel Skylake-X CHA core 3 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA4,	/* Intel Skylake-X CHA core 4 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA5,	/* Intel Skylake-X CHA core 5 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA6,	/* Intel Skylake-X CHA core 6 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA7,	/* Intel Skylake-X CHA core 7 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA8,	/* Intel Skylake-X CHA core 8 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA9,	/* Intel Skylake-X CHA core 9 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA10,	/* Intel Skylake-X CHA core 10 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA11,	/* Intel Skylake-X CHA core 11 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA12,	/* Intel Skylake-X CHA core 12 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA13,	/* Intel Skylake-X CHA core 13 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA14,	/* Intel Skylake-X CHA core 14 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA15,	/* Intel Skylake-X CHA core 15 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA16,	/* Intel Skylake-X CHA core 16 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA17,	/* Intel Skylake-X CHA core 17 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA18,	/* Intel Skylake-X CHA core 18 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA19,	/* Intel Skylake-X CHA core 19 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA20,	/* Intel Skylake-X CHA core 20 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA21,	/* Intel Skylake-X CHA core 21 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA22,	/* Intel Skylake-X CHA core 22 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA23,	/* Intel Skylake-X CHA core 23 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA24,	/* Intel Skylake-X CHA core 24 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA25,	/* Intel Skylake-X CHA core 25 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA26,	/* Intel Skylake-X CHA core 26 uncore */
	PFM_PMU_INTEL_SKX_UNC_CHA27,	/* Intel Skylake-X CHA core 27 uncore */
	PFM_PMU_INTEL_SKX_UNC_IIO0,	/* Intel Skylake-X IIO0 uncore */
	PFM_PMU_INTEL_SKX_UNC_IIO1,	/* Intel Skylake-X IIO1 uncore */
	PFM_PMU_INTEL_SKX_UNC_IIO2,	/* Intel Skylake-X IIO2 uncore */
	PFM_PMU_INTEL_SKX_UNC_IIO3,	/* Intel Skylake-X IIO3 uncore */
	PFM_PMU_INTEL_SKX_UNC_IIO4,	/* Intel Skylake-X IIO4 uncore */
	PFM_PMU_INTEL_SKX_UNC_IIO5,	/* Intel Skylake-X IIO5 uncore */
	PFM_PMU_INTEL_SKX_UNC_IMC0,	/* Intel Skylake-X IMC0 uncore */
	PFM_PMU_INTEL_SKX_UNC_IMC1,	/* Intel Skylake-X IMC1 uncore */
	PFM_PMU_INTEL_SKX_UNC_IMC2,	/* Intel Skylake-X IMC2 uncore */
	PFM_PMU_INTEL_SKX_UNC_IMC3,	/* Intel Skylake-X IMC3 uncore */
	PFM_PMU_INTEL_SKX_UNC_IMC4,	/* Intel Skylake-X IMC4 uncore */
	PFM_PMU_INTEL_SKX_UNC_IMC5,	/* Intel Skylake-X IMC5 uncore */
	PFM_PMU_INTEL_SKX_UNC_IRP,	/* Intel Skylake-X IRP  uncore */
	PFM_PMU_INTEL_SKX_UNC_M2M0,	/* Intel Skylake-X M2M0 uncore */
	PFM_PMU_INTEL_SKX_UNC_M2M1,	/* Intel Skylake-X M2M1 uncore */
	PFM_PMU_INTEL_SKX_UNC_M3UPI0,	/* Intel Skylake-X M3UPI0 uncore */
	PFM_PMU_INTEL_SKX_UNC_M3UPI1,	/* Intel Skylake-X M3UPI1 uncore */
	PFM_PMU_INTEL_SKX_UNC_M3UPI2,	/* Intel Skylake-X M3UPI2 uncore */
	PFM_PMU_INTEL_SKX_UNC_PCU,	/* Intel Skylake-X PCU uncore */
	PFM_PMU_INTEL_SKX_UNC_UBOX,	/* Intel Skylake-X U-Box uncore */
	PFM_PMU_INTEL_SKX_UNC_UPI0,	/* Intel Skylake-X UPI link 0 uncore */
	PFM_PMU_INTEL_SKX_UNC_UPI1,	/* Intel Skylake-X UPI link 1 uncore */
	PFM_PMU_INTEL_SKX_UNC_UPI2,	/* Intel Skylake-X UPI link 2 uncore */

	PFM_PMU_INTEL_KNM,		/* Intel Knights Mill */
	PFM_PMU_INTEL_KNM_UNC_IMC0,	/* Intel Knights Mill IMC channel 0 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC1,	/* Intel Knights Mill IMC channel 1 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC2,	/* Intel Knights Mill IMC channel 2 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC3,	/* Intel Knights Mill IMC channel 3 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC4,	/* Intel Knights Mill IMC channel 4 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC5,	/* Intel Knights Mill IMC channel 5 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC_UCLK0,/* Intel Knights Mill IMC UCLK unit 0 uncore */
	PFM_PMU_INTEL_KNM_UNC_IMC_UCLK1,/* Intel Knights Mill IMC UCLK unit 1 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK0,/* Intel Knights Mill EDC ECLK unit 0 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK1,/* Intel Knights Mill EDC ECLK unit 1 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK2,/* Intel Knights Mill EDC ECLK unit 2 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK3,/* Intel Knights Mill EDC ECLK unit 3 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK4,/* Intel Knights Mill EDC ECLK unit 4 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK5,/* Intel Knights Mill EDC ECLK unit 5 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK6,/* Intel Knights Mill EDC ECLK unit 6 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_ECLK7,/* Intel Knights Mill EDC ECLK unit 7 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK0,/* Intel Knights Mill EDC UCLK unit 0 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK1,/* Intel Knights Mill EDC UCLK unit 1 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK2,/* Intel Knights Mill EDC UCLK unit 2 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK3,/* Intel Knights Mill EDC UCLK unit 3 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK4,/* Intel Knights Mill EDC UCLK unit 4 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK5,/* Intel Knights Mill EDC UCLK unit 5 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK6,/* Intel Knights Mill EDC UCLK unit 6 uncore */
	PFM_PMU_INTEL_KNM_UNC_EDC_UCLK7,/* Intel Knights Mill EDC UCLK unit 7 uncore */

	PFM_PMU_INTEL_KNM_UNC_CHA0,	/* Intel Knights Mill CHA unit 0 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA1,	/* Intel Knights Mill CHA unit 1 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA2,	/* Intel Knights Mill CHA unit 2 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA3,	/* Intel Knights Mill CHA unit 3 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA4,	/* Intel Knights Mill CHA unit 4 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA5,	/* Intel Knights Mill CHA unit 5 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA6,	/* Intel Knights Mill CHA unit 6 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA7,	/* Intel Knights Mill CHA unit 7 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA8,	/* Intel Knights Mill CHA unit 8 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA9,	/* Intel Knights Mill CHA unit 9 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA10,	/* Intel Knights Mill CHA unit 10 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA11,	/* Intel Knights Mill CHA unit 11 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA12,	/* Intel Knights Mill CHA unit 12 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA13,	/* Intel Knights Mill CHA unit 13 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA14,	/* Intel Knights Mill CHA unit 14 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA15,	/* Intel Knights Mill CHA unit 15 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA16,	/* Intel Knights Mill CHA unit 16 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA17,	/* Intel Knights Mill CHA unit 17 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA18,	/* Intel Knights Mill CHA unit 18 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA19,	/* Intel Knights Mill CHA unit 19 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA20,	/* Intel Knights Mill CHA unit 20 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA21,	/* Intel Knights Mill CHA unit 21 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA22,	/* Intel Knights Mill CHA unit 22 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA23,	/* Intel Knights Mill CHA unit 23 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA24,	/* Intel Knights Mill CHA unit 24 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA25,	/* Intel Knights Mill CHA unit 25 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA26,	/* Intel Knights Mill CHA unit 26 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA27,	/* Intel Knights Mill CHA unit 27 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA28,	/* Intel Knights Mill CHA unit 28 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA29,	/* Intel Knights Mill CHA unit 29 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA30,	/* Intel Knights Mill CHA unit 30 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA31,	/* Intel Knights Mill CHA unit 31 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA32,	/* Intel Knights Mill CHA unit 32 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA33,	/* Intel Knights Mill CHA unit 33 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA34,	/* Intel Knights Mill CHA unit 34 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA35,	/* Intel Knights Mill CHA unit 35 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA36,	/* Intel Knights Mill CHA unit 36 uncore */
	PFM_PMU_INTEL_KNM_UNC_CHA37,	/* Intel Knights Mill CHA unit 37 uncore */

	PFM_PMU_INTEL_KNM_UNC_UBOX,	/* Intel Knights Mill Ubox uncore */
	PFM_PMU_INTEL_KNM_UNC_M2PCIE,	/* Intel Knights Mill M2PCIe uncore */
	PFM_PMU_ARM_THUNDERX2,		/* Marvell ThunderX2 */

	PFM_PMU_INTEL_CLX,		/* Intel CascadeLake X */

	PFM_PMU_ARM_THUNDERX2_DMC0,	/* Marvell ThunderX2 DMC unit 0 uncore */
	PFM_PMU_ARM_THUNDERX2_DMC1,	/* Marvell ThunderX2 DMC unit 1 uncore */
	PFM_PMU_ARM_THUNDERX2_LLC0,	/* Marvell ThunderX2 LLC unit 0 uncore */
	PFM_PMU_ARM_THUNDERX2_LLC1,	/* Marvell ThunderX2 LLC unit 1 uncore */
	PFM_PMU_ARM_THUNDERX2_CCPI0,	/* Marvell ThunderX2 Cross-Socket Interconnect unit 0 uncore */
	PFM_PMU_ARM_THUNDERX2_CCPI1,	/* Marvell ThunderX2 Cross-Socket Interconnect unit 1 uncore */

	PFM_PMU_AMD64_FAM17H_ZEN1,	/* AMD AMD64 Fam17h Zen1 */
	PFM_PMU_AMD64_FAM17H_ZEN2,	/* AMD AMD64 Fam17h Zen2 */

	PFM_PMU_INTEL_TMT,		/* Intel Tremont */
	PFM_PMU_INTEL_ICL,		/* Intel IceLake */

	PFM_PMU_ARM_A64FX,		/* Fujitsu A64FX processor */
	PFM_PMU_ARM_N1,			/* Arm Neoverse N1 */

	PFM_PMU_AMD64_FAM19H_ZEN3,	/* AMD AMD64 Fam19h Zen3 */
	PFM_PMU_AMD64_RAPL,		/* AMD64 RAPL */
	PFM_PMU_AMD64_FAM19H_ZEN3_L3,	/* AMD64 Fam17h Zen3 L3 */

	PFM_PMU_INTEL_ICX,		/* Intel IceLakeX */

	PFM_PMU_ARM_N2,			/* Arm Neoverse N2 */

	PFM_PMU_ARM_KUNPENG,		/* HiSilicon Kunpeng processor */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_DDRC0, /* Hisilicon Kunpeng SCCL unit 1 DDRC 0 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_DDRC1, /* Hisilicon Kunpeng SCCL unit 1 DDRC 1 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_DDRC2, /* Hisilicon Kunpeng SCCL unit 1 DDRC 2 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_DDRC3, /* Hisilicon Kunpeng SCCL unit 1 DDRC 3 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_DDRC0, /* Hisilicon Kunpeng SCCL unit 3 DDRC 0 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_DDRC1, /* Hisilicon Kunpeng SCCL unit 3 DDRC 1 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_DDRC2, /* Hisilicon Kunpeng SCCL unit 3 DDRC 2 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_DDRC3, /* Hisilicon Kunpeng SCCL unit 3 DDRC 3 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_DDRC0, /* Hisilicon Kunpeng SCCL unit 5 DDRC 0 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_DDRC1, /* Hisilicon Kunpeng SCCL unit 5 DDRC 1 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_DDRC2, /* Hisilicon Kunpeng SCCL unit 5 DDRC 2 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_DDRC3, /* Hisilicon Kunpeng SCCL unit 5 DDRC 3 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_DDRC0, /* Hisilicon Kunpeng SCCL unit 7 DDRC 0 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_DDRC1, /* Hisilicon Kunpeng SCCL unit 7 DDRC 1 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_DDRC2, /* Hisilicon Kunpeng SCCL unit 7 DDRC 2 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_DDRC3, /* Hisilicon Kunpeng SCCL unit 7 DDRC 3 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_HHA2,  /* Hisilicon Kunpeng SCCL unit 1 HHA 2 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_HHA3,  /* Hisilicon Kunpeng SCCL unit 1 HHA 3 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_HHA0,  /* Hisilicon Kunpeng SCCL unit 3 HHA 0 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_HHA1,  /* Hisilicon Kunpeng SCCL unit 3 HHA 1 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_HHA6,  /* Hisilicon Kunpeng SCCL unit 5 HHA 6 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_HHA7,  /* Hisilicon Kunpeng SCCL unit 5 HHA 7 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_HHA4,  /* Hisilicon Kunpeng SCCL unit 7 HHA 4 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_HHA5,  /* Hisilicon Kunpeng SCCL unit 7 HHA 5 uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C10, /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C11, /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C12, /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C13, /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C14, /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C15, /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C8,  /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL1_L3C9,  /* Hisilicon Kunpeng SCCL unit 1 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C0,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C1,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C2,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C3,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C4,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C5,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C6,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL3_L3C7,  /* Hisilicon Kunpeng SCCL unit 3 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C24, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C25, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C26, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C27, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C28, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C29, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C30, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL5_L3C31, /* Hisilicon Kunpeng SCCL unit 5 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C16, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C17, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C18, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C19, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C20, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C21, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C22, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */
	PFM_PMU_ARM_KUNPENG_UNC_SCCL7_L3C23, /* Hisilicon Kunpeng SCCL unit 7 L3C uncore */

	PFM_PMU_INTEL_SPR,		/* Intel SapphireRapid */

	PFM_PMU_POWER10,		/* IBM POWER10 */
	PFM_PMU_AMD64_FAM19H_ZEN4,	/* AMD AMD64 Fam19h Zen4 */
	PFM_PMU_ARM_V1,			/* ARM Neoverse V1 */
	PFM_PMU_ARM_V2,			/* Arm Neoverse V2 */
	PFM_PMU_INTEL_EMR,		/* Intel EmeraldRapid */

	PFM_PMU_INTEL_ICX_UNC_CHA0,	/* Intel Icelake-X CHA core 0 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA1,	/* Intel Icelake-X CHA core 1 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA2,	/* Intel Icelake-X CHA core 2 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA3,	/* Intel Icelake-X CHA core 3 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA4,	/* Intel Icelake-X CHA core 4 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA5,	/* Intel Icelake-X CHA core 5 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA6,	/* Intel Icelake-X CHA core 6 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA7,	/* Intel Icelake-X CHA core 7 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA8,	/* Intel Icelake-X CHA core 8 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA9,	/* Intel Icelake-X CHA core 9 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA10,	/* Intel Icelake-X CHA core 10 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA11,	/* Intel Icelake-X CHA core 11 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA12,	/* Intel Icelake-X CHA core 12 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA13,	/* Intel Icelake-X CHA core 13 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA14,	/* Intel Icelake-X CHA core 14 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA15,	/* Intel Icelake-X CHA core 15 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA16,	/* Intel Icelake-X CHA core 16 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA17,	/* Intel Icelake-X CHA core 17 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA18,	/* Intel Icelake-X CHA core 18 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA19,	/* Intel Icelake-X CHA core 19 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA20,	/* Intel Icelake-X CHA core 20 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA21,	/* Intel Icelake-X CHA core 21 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA22,	/* Intel Icelake-X CHA core 22 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA23,	/* Intel Icelake-X CHA core 23 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA24,	/* Intel Icelake-X CHA core 24 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA25,	/* Intel Icelake-X CHA core 25 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA26,	/* Intel Icelake-X CHA core 26 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA27,	/* Intel Icelake-X CHA core 27 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA28,	/* Intel Icelake-X CHA core 28 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA29,	/* Intel Icelake-X CHA core 39 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA30,	/* Intel Icelake-X CHA core 30 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA31,	/* Intel Icelake-X CHA core 31 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA32,	/* Intel Icelake-X CHA core 32 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA33,	/* Intel Icelake-X CHA core 33 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA34,	/* Intel Icelake-X CHA core 34 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA35,	/* Intel Icelake-X CHA core 35 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA36,	/* Intel Icelake-X CHA core 36 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA37,	/* Intel Icelake-X CHA core 37 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA38,	/* Intel Icelake-X CHA core 38 uncore */
	PFM_PMU_INTEL_ICX_UNC_CHA39,	/* Intel Icelake-X CHA core 39 uncore */

	PFM_PMU_INTEL_ICX_UNC_IMC0,	/* Intel Icelake-X IMC channel 0 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC1,	/* Intel Icelake-X IMC channel 1 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC2,	/* Intel Icelake-X IMC channel 2 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC3,	/* Intel Icelake-X IMC channel 3 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC4,	/* Intel Icelake-X IMC channel 4 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC5,	/* Intel Icelake-X IMC channel 5 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC6,	/* Intel Icelake-X IMC channel 6 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC7,	/* Intel Icelake-X IMC channel 7 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC8,	/* Intel Icelake-X IMC channel 8 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC9,	/* Intel Icelake-X IMC channel 9 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC10,	/* Intel Icelake-X IMC channel 10 uncore */
	PFM_PMU_INTEL_ICX_UNC_IMC11,	/* Intel Icelake-X IMC channel 11 uncore */

	PFM_PMU_INTEL_ICX_UNC_IIO0,	/* Intel Icelake-X IIO 0 uncore */
	PFM_PMU_INTEL_ICX_UNC_IIO1,	/* Intel Icelake-X IIO 1 uncore */
	PFM_PMU_INTEL_ICX_UNC_IIO2,	/* Intel Icelake-X IIO 2 uncore */
	PFM_PMU_INTEL_ICX_UNC_IIO3,	/* Intel Icelake-X IIO 3 uncore */
	PFM_PMU_INTEL_ICX_UNC_IIO4,	/* Intel Icelake-X IIO 4 uncore */
	PFM_PMU_INTEL_ICX_UNC_IIO5,	/* Intel Icelake-X IIO 5 uncore */

	PFM_PMU_INTEL_ICX_UNC_IRP0,	/* Intel Icelake-X IRP 0 uncore */
	PFM_PMU_INTEL_ICX_UNC_IRP1,	/* Intel Icelake-X IRP 1 uncore */
	PFM_PMU_INTEL_ICX_UNC_IRP2,	/* Intel Icelake-X IRP 2 uncore */
	PFM_PMU_INTEL_ICX_UNC_IRP3,	/* Intel Icelake-X IRP 3 uncore */
	PFM_PMU_INTEL_ICX_UNC_IRP4,	/* Intel Icelake-X IRP 4 uncore */
	PFM_PMU_INTEL_ICX_UNC_IRP5,	/* Intel Icelake-X IRP 5 uncore */

	PFM_PMU_INTEL_ICX_UNC_M2M0,	/* Intel Icelake-X M2M 0 uncore */
	PFM_PMU_INTEL_ICX_UNC_M2M1,	/* Intel Icelake-X M2M 1 uncore */

	PFM_PMU_INTEL_ICX_UNC_PCU,	/* Intel Icelake-X PCU uncore */

	PFM_PMU_INTEL_ICX_UNC_UPI0,	/* Intel Icelake-X UPI0 uncore */
	PFM_PMU_INTEL_ICX_UNC_UPI1,	/* Intel Icelake-X UPI1 uncore */
	PFM_PMU_INTEL_ICX_UNC_UPI2,	/* Intel Icelake-X UPI2 uncore */
	PFM_PMU_INTEL_ICX_UNC_UPI3,	/* Intel Icelake-X UPI3 uncore */

	PFM_PMU_INTEL_ICX_UNC_M3UPI0,	/* Intel Icelake-X M3UPI0 uncore */
	PFM_PMU_INTEL_ICX_UNC_M3UPI1,	/* Intel Icelake-X M3UPI1 uncore */
	PFM_PMU_INTEL_ICX_UNC_M3UPI2,	/* Intel Icelake-X M3UPI2 uncore */
	PFM_PMU_INTEL_ICX_UNC_M3UPI3,	/* Intel Icelake-X M3UPI3 uncore */

	PFM_PMU_INTEL_ICX_UNC_UBOX,	/* Intel Icelake-X UBOX uncore */
	PFM_PMU_INTEL_ICX_UNC_M2PCIE0,	/* Intel Icelake-X M2PCIE0 uncore */
	PFM_PMU_INTEL_ICX_UNC_M2PCIE1,	/* Intel Icelake-X M2PCIE1 uncore */
	PFM_PMU_INTEL_ICX_UNC_M2PCIE2,	/* Intel Icelake-X M2PCIE2 uncore */

	PFM_PMU_INTEL_ADL_GLC,		/* Intel AlderLake Goldencove (P-Core) */
	PFM_PMU_INTEL_ADL_GRT,		/* Intel AlderLake Gracemont (E-Core) */

	PFM_PMU_INTEL_SPR_UNC_IMC0,	/* Intel SapphireRapids IMC channel 0 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC1,	/* Intel SapphireRapids IMC channel 1 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC2,	/* Intel SapphireRapids IMC channel 2 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC3,	/* Intel SapphireRapids IMC channel 3 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC4,	/* Intel SapphireRapids IMC channel 4 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC5,	/* Intel SapphireRapids IMC channel 5 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC6,	/* Intel SapphireRapids IMC channel 6 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC7,	/* Intel SapphireRapids IMC channel 7 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC8,	/* Intel SapphireRapids IMC channel 8 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC9,	/* Intel SapphireRapids IMC channel 9 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC10,	/* Intel SapphireRapids IMC channel 10 uncore */
	PFM_PMU_INTEL_SPR_UNC_IMC11,	/* Intel SapphireRapids IMC channel 11 uncore */

	PFM_PMU_INTEL_SPR_UNC_UPI0,	/* Intel SapphireRapids UPI0 uncore */
	PFM_PMU_INTEL_SPR_UNC_UPI1,	/* Intel SapphireRapids UPI1 uncore */
	PFM_PMU_INTEL_SPR_UNC_UPI2,	/* Intel SapphireRapids UPI2 uncore */
	PFM_PMU_INTEL_SPR_UNC_UPI3,	/* Intel SapphireRapids UPI3 uncore */

	PFM_PMU_INTEL_SPR_UNC_CHA0,	/* Intel SapphireRapids CHA core 0 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA1,	/* Intel SapphireRapids CHA core 1 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA2,	/* Intel SapphireRapids CHA core 2 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA3,	/* Intel SapphireRapids CHA core 3 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA4,	/* Intel SapphireRapids CHA core 4 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA5,	/* Intel SapphireRapids CHA core 5 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA6,	/* Intel SapphireRapids CHA core 6 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA7,	/* Intel SapphireRapids CHA core 7 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA8,	/* Intel SapphireRapids CHA core 8 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA9,	/* Intel SapphireRapids CHA core 9 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA10,	/* Intel SapphireRapids CHA core 10 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA11,	/* Intel SapphireRapids CHA core 11 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA12,	/* Intel SapphireRapids CHA core 12 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA13,	/* Intel SapphireRapids CHA core 13 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA14,	/* Intel SapphireRapids CHA core 14 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA15,	/* Intel SapphireRapids CHA core 15 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA16,	/* Intel SapphireRapids CHA core 16 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA17,	/* Intel SapphireRapids CHA core 17 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA18,	/* Intel SapphireRapids CHA core 18 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA19,	/* Intel SapphireRapids CHA core 19 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA20,	/* Intel SapphireRapids CHA core 20 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA21,	/* Intel SapphireRapids CHA core 21 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA22,	/* Intel SapphireRapids CHA core 22 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA23,	/* Intel SapphireRapids CHA core 23 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA24,	/* Intel SapphireRapids CHA core 24 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA25,	/* Intel SapphireRapids CHA core 25 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA26,	/* Intel SapphireRapids CHA core 26 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA27,	/* Intel SapphireRapids CHA core 27 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA28,	/* Intel SapphireRapids CHA core 28 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA29,	/* Intel SapphireRapids CHA core 39 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA30,	/* Intel SapphireRapids CHA core 30 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA31,	/* Intel SapphireRapids CHA core 31 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA32,	/* Intel SapphireRapids CHA core 32 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA33,	/* Intel SapphireRapids CHA core 33 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA34,	/* Intel SapphireRapids CHA core 34 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA35,	/* Intel SapphireRapids CHA core 35 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA36,	/* Intel SapphireRapids CHA core 36 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA37,	/* Intel SapphireRapids CHA core 37 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA38,	/* Intel SapphireRapids CHA core 38 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA39,	/* Intel SapphireRapids CHA core 39 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA40,	/* Intel SapphireRapids CHA core 40 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA41,	/* Intel SapphireRapids CHA core 41 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA42,	/* Intel SapphireRapids CHA core 42 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA43,	/* Intel SapphireRapids CHA core 43 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA44,	/* Intel SapphireRapids CHA core 44 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA45,	/* Intel SapphireRapids CHA core 45 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA46,	/* Intel SapphireRapids CHA core 46 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA47,	/* Intel SapphireRapids CHA core 47 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA48,	/* Intel SapphireRapids CHA core 48 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA49,	/* Intel SapphireRapids CHA core 49 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA50,	/* Intel SapphireRapids CHA core 50 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA51,	/* Intel SapphireRapids CHA core 51 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA52,	/* Intel SapphireRapids CHA core 52 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA53,	/* Intel SapphireRapids CHA core 53 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA54,	/* Intel SapphireRapids CHA core 54 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA55,	/* Intel SapphireRapids CHA core 55 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA56,	/* Intel SapphireRapids CHA core 56 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA57,	/* Intel SapphireRapids CHA core 57 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA58,	/* Intel SapphireRapids CHA core 58 uncore */
	PFM_PMU_INTEL_SPR_UNC_CHA59,	/* Intel SapphireRapids CHA core 59 uncore */

	PFM_PMU_INTEL_GNR,		/* Intel GraniteRapids core PMU */

	PFM_PMU_AMD64_FAM1AH_ZEN5,      /* AMD64 Fam1Ah Zen5 */
	PFM_PMU_AMD64_FAM1AH_ZEN5_L3,	/* AMD64 Fam1Ah Zen5 L3 */

	PFM_PMU_ARM_CORTEX_A72,		/* ARM Cortex A72 (ARMv8) */
	PFM_PMU_ARM_V3,			/* Arm Neoverse V3 (ARMv9) */
	PFM_PMU_ARM_CORTEX_A55,		/* ARM Cortex A55 (ARMv8) */
	PFM_PMU_ARM_CORTEX_A76,		/* ARM Cortex A76 (ARMv8) */
	PFM_PMU_ARM_N3,			/* Arm Neoverse N3 */

	PFM_PMU_ARM_MONAKA,		/* Fujitsu FUJITSU-MONAKA processor */

	PFM_PMU_INTEL_GNR_UNC_IMC0,	/* Intel GraniteRapids IMC channel 0 */
	PFM_PMU_INTEL_GNR_UNC_IMC1,	/* Intel GraniteRapids IMC channel 1 */
	PFM_PMU_INTEL_GNR_UNC_IMC2,	/* Intel GraniteRapids IMC channel 2 */
	PFM_PMU_INTEL_GNR_UNC_IMC3,	/* Intel GraniteRapids IMC channel 3 */
	PFM_PMU_INTEL_GNR_UNC_IMC4,	/* Intel GraniteRapids IMC channel 4 */
	PFM_PMU_INTEL_GNR_UNC_IMC5,	/* Intel GraniteRapids IMC channel 5 */
	PFM_PMU_INTEL_GNR_UNC_IMC6,	/* Intel GraniteRapids IMC channel 6 */
	PFM_PMU_INTEL_GNR_UNC_IMC7,	/* Intel GraniteRapids IMC channel 7 */
	PFM_PMU_INTEL_GNR_UNC_IMC8,	/* Intel GraniteRapids IMC channel 8 */
	PFM_PMU_INTEL_GNR_UNC_IMC9,	/* Intel GraniteRapids IMC channel 9 */
	PFM_PMU_INTEL_GNR_UNC_IMC10,	/* Intel GraniteRapids IMC channel 10 */
	PFM_PMU_INTEL_GNR_UNC_IMC11,	/* Intel GraniteRapids IMC channel 10 */

	PFM_PMU_ARM_CORTEX_X4,		/* ARM Cortex X4 (ARMv9) */

	PFM_PMU_ARM_OLYMPUS,		/* NVIDIA Olympus (ARMv9) */
	/* MUST ADD NEW PMU MODELS HERE */

	PFM_PMU_MAX			/* end marker */
} pfm_pmu_t;

typedef enum {
	PFM_PMU_TYPE_UNKNOWN=0,	/* unknown PMU type */
	PFM_PMU_TYPE_CORE,	/* processor core PMU */
	PFM_PMU_TYPE_UNCORE,	/* processor socket-level PMU */
	PFM_PMU_TYPE_OS_GENERIC,/* generic OS-provided PMU */
	PFM_PMU_TYPE_MAX
} pfm_pmu_type_t;

typedef enum {
	PFM_ATTR_NONE=0,	/* no attribute */
	PFM_ATTR_UMASK,		/* unit mask */
	PFM_ATTR_MOD_BOOL,	/* register modifier */
	PFM_ATTR_MOD_INTEGER,	/* register modifier */
	PFM_ATTR_RAW_UMASK,	/* raw umask (not user visible) */

	PFM_ATTR_MAX		/* end-marker */
} pfm_attr_t;

/*
 * define additional event data types beyond historic uint64
 * what else can fit in 64 bits?
 */
typedef enum {
	PFM_DTYPE_UNKNOWN=0,	/* unkown */
	PFM_DTYPE_UINT64,	/* uint64 */
	PFM_DTYPE_INT64,	/* int64 */
	PFM_DTYPE_DOUBLE,	/* IEEE double precision float */
	PFM_DTYPE_FIXED,	/* 32.32 fixed point */
	PFM_DTYPE_RATIO,	/* 32/32 integer ratio */
	PFM_DTYPE_CHAR8,	/* 8 char unterminated string */

	PFM_DTYPE_MAX		/* end-marker */
} pfm_dtype_t;

/*
 * event attribute control: which layer is controlling
 * the attribute could be PMU, OS APIs
 */
typedef enum {
	PFM_ATTR_CTRL_UNKNOWN = 0,	/* unknown */
	PFM_ATTR_CTRL_PMU,		/* PMU hardware */
	PFM_ATTR_CTRL_PERF_EVENT,	/* perf_events kernel interface */

	PFM_ATTR_CTRL_MAX
} pfm_attr_ctrl_t;

/*
 * OS layer
 * Used when querying event or attribute information
 */
typedef enum {
	PFM_OS_NONE = 0,	/* only PMU */
	PFM_OS_PERF_EVENT,	/* perf_events PMU attribute subset + PMU */
	PFM_OS_PERF_EVENT_EXT,	/* perf_events all attributes + PMU */

	PFM_OS_MAX,
} pfm_os_t;

/* SWIG doesn't deal well with anonymous nested structures */
#ifdef SWIG
#define SWIG_NAME(x) x
#else
#define SWIG_NAME(x)
#endif /* SWIG */

/*
 * special data type for libpfm error value used to help
 * with Python support and in particular for SWIG. By using
 * a specific type we can detect library calls and trap errors
 * in one SWIG statement as opposed to having to keep track of
 * each call individually. Programs can use 'int' safely for
 * the return value.
 */
typedef int pfm_err_t;		/* error if !PFM_SUCCESS */
typedef int os_err_t;		/* error if a syscall fails */

typedef struct {
	const char		*name;		/* event name */
	const char		*desc;		/* event description */
	size_t			size;		/* struct sizeof */
	pfm_pmu_t		pmu;		/* PMU identification */
	pfm_pmu_type_t		type;		/* PMU type */
	int			nevents;	/* how many events for this PMU */
	int			first_event;	/* opaque index of first event */
	int			max_encoding;	/* max number of uint64_t to encode an event */
	int			num_cntrs;	/* number of generic counters */
	int			num_fixed_cntrs;/* number of fixed counters */
	struct {
		unsigned int	is_present:1;	/* present on host system */
		unsigned int	is_dfl:1;	/* is architecture default PMU */
		unsigned int	reserved_bits:30;
	} SWIG_NAME(flags);
} pfm_pmu_info_t;

/*
 * possible values for pfm_event_info_t.is_speculative
 * possible values for pfm_event_attr_info_t.is_speculative
 */
typedef enum {
	PFM_EVENT_INFO_SPEC_NA    = 0, /* speculation info not available */
	PFM_EVENT_INFO_SPEC_TRUE  = 1, /* counts speculative exec events */
	PFM_EVENT_INFO_SPEC_FALSE = 2, /* counts non-speculative exec events */
} pfm_event_info_spec_t;

typedef struct {
	const char		*name;	/* event name */
	const char		*desc;	/* event description */
	const char		*equiv;	/* event is equivalent to */
	size_t			size;	/* struct sizeof */
	uint64_t		code;	/* event raw code (not encoding) */
	pfm_pmu_t		pmu;	/* which PMU */
	pfm_dtype_t		dtype;	/* data type of event value */
	int			idx;	/* unique event identifier */
	int			nattrs;	/* number of attributes */
	int			reserved; /* for future use */
	struct {
		unsigned int	is_precise:1;	  /* precise sampling (Intel X86=PEBS) */
		unsigned int	is_speculative:2; /* count correct and wrong path occurrences */
		unsigned int	support_hw_smpl:1;/* can be recorded by hw buffer (Intel X86=EXTPEBS) */
		unsigned int	reserved_bits:28;
	} SWIG_NAME(flags);
} pfm_event_info_t;

typedef struct {
	const char		*name;	/* attribute symbolic name */
	const char		*desc;	/* attribute description */
	const char		*equiv;	/* attribute is equivalent to */
	size_t			size;	/* struct sizeof */
	uint64_t		code;	/* attribute code */
	pfm_attr_t		type;	/* attribute type */
	int			idx;	/* attribute opaque index */
	pfm_attr_ctrl_t		ctrl;	/* what is providing attr */
	struct {
		unsigned int    is_dfl:1;	  /* is default umask */
		unsigned int    is_precise:1;	  /* Intel X86: supports PEBS */
		unsigned int	is_speculative:2; /* count correct and wrong path occurrences */
		unsigned int	support_hw_smpl:1;/* can be recorded by hw buffer (Intel X86=EXTPEBS) */
		unsigned int	support_no_mods:1;/* attribute does not support modifiers (umask only) */
		unsigned int	reserved_bits:26;
	} SWIG_NAME(flags);
	union {
		uint64_t	dfl_val64;	/* default 64-bit value */
		const char	*dfl_str;	/* default string value */
		int		dfl_bool;	/* default boolean value */
		int		dfl_int;	/* default integer value */
	} SWIG_NAME(defaults);
} pfm_event_attr_info_t;

/*
 * use with PFM_OS_NONE for pfm_get_os_event_encoding()
 */
typedef struct {
	uint64_t	*codes;		/* out/in: event codes array */
	char		**fstr;		/* out/in: fully qualified event string */
	size_t		size;		/* sizeof struct */
	int		count;		/* out/in: # of elements in array */
	int		idx;		/* out: unique event identifier */
} pfm_pmu_encode_arg_t;

#if __WORDSIZE == 64
#define PFM_PMU_INFO_ABI0	56
#define PFM_EVENT_INFO_ABI0	64
#define PFM_ATTR_INFO_ABI0	64

#define PFM_RAW_ENCODE_ABI0	32
#else
#define PFM_PMU_INFO_ABI0	44
#define PFM_EVENT_INFO_ABI0	48
#define PFM_ATTR_INFO_ABI0	48

#define PFM_RAW_ENCODE_ABI0	20
#endif


/*
 * initialization, configuration, errors
 */
extern pfm_err_t pfm_initialize(void);
extern void pfm_terminate(void);
extern const char *pfm_strerror(int code);
extern int pfm_get_version(void);

/*
 * PMU API
 */
extern pfm_err_t pfm_get_pmu_info(pfm_pmu_t pmu, pfm_pmu_info_t *output);

/*
 * event API
 */
extern int pfm_get_event_next(int idx);
extern int pfm_find_event(const char *str);
extern pfm_err_t pfm_get_event_info(int idx, pfm_os_t os, pfm_event_info_t *output);

/*
 * event encoding API
 *
 * content of args depends on value of os (refer to man page)
 */
extern pfm_err_t pfm_get_os_event_encoding(const char *str, int dfl_plm, pfm_os_t os, void *args);

/*
 * attribute API
 */
extern pfm_err_t pfm_get_event_attr_info(int eidx, int aidx, pfm_os_t os, pfm_event_attr_info_t *output);

/*
 * library validation API
 */
extern pfm_err_t pfm_pmu_validate(pfm_pmu_t pmu_id, FILE *fp);

/*
 * older encoding API
 */
extern pfm_err_t pfm_get_event_encoding(const char *str, int dfl_plm, char **fstr, int *idx, uint64_t **codes, int *count);

/*
 * error codes
 */
#define PFM_SUCCESS		0	/* success */
#define PFM_ERR_NOTSUPP		-1	/* function not supported */
#define PFM_ERR_INVAL		-2	/* invalid parameters */
#define PFM_ERR_NOINIT		-3	/* library was not initialized */
#define PFM_ERR_NOTFOUND	-4	/* event not found */
#define PFM_ERR_FEATCOMB	-5	/* invalid combination of features */
#define PFM_ERR_UMASK	 	-6	/* invalid or missing unit mask */
#define PFM_ERR_NOMEM	 	-7	/* out of memory */
#define PFM_ERR_ATTR		-8	/* invalid event attribute */
#define PFM_ERR_ATTR_VAL	-9	/* invalid event attribute value */
#define PFM_ERR_ATTR_SET	-10	/* attribute value already set */
#define PFM_ERR_TOOMANY		-11	/* too many parameters */
#define PFM_ERR_TOOSMALL	-12	/* parameter is too small */

/*
 * event, attribute iterators
 * must be used because no guarante indexes are contiguous
 *
 * for pmu, simply iterate over pfm_pmu_t enum and use
 * pfm_get_pmu_info() and the is_present field
 */
#define pfm_for_each_event_attr(x, z) \
	for((x)=0; (x) < (z)->nattrs; (x) = (x)+1)

#define pfm_for_all_pmus(x) \
	for((x)= PFM_PMU_NONE ; (x) < PFM_PMU_MAX; (x)++)

#ifdef __cplusplus /* extern C */
}
#endif

#pragma GCC visibility pop

#endif /* __PFMLIB_H__ */
