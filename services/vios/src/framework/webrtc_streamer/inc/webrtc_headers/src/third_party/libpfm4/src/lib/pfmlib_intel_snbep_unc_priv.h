/*
 * pfmlib_intel_snbep_unc_priv.c : Intel SandyBridge/IvyBridge-EP common definitions
 *
 * Copyright (c) 2012 Google, Inc
 * Contributed by Stephane Eranian <eranian@gmail.com>
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
#ifndef __PFMLIB_INTEL_SNBEP_UNC_PRIV_H__
#define __PFMLIB_INTEL_SNBEP_UNC_PRIV_H__

/*
 * Intel x86 specific pmu flags (pmu->flags 16 MSB)
 */
#define INTEL_PMU_FL_UNC_OCC 0x10000	/* PMU has occupancy counter filters */
#define INTEL_PMU_FL_UNC_CBO 0x20000	/* PMU is Cbox */
#define INTEL_PMU_FL_UNC_CHA 0x40000	/* PMU is CHA (skylake and later) */


#define SNBEP_UNC_ATTR_E		0
#define SNBEP_UNC_ATTR_I		1
#define SNBEP_UNC_ATTR_T8		2
#define SNBEP_UNC_ATTR_T5		3
#define SNBEP_UNC_ATTR_TF		4
#define SNBEP_UNC_ATTR_CF		5
#define SNBEP_UNC_ATTR_NF		6 /* for filter0 */
#define SNBEP_UNC_ATTR_FF		7
#define SNBEP_UNC_ATTR_A		8
#define SNBEP_UNC_ATTR_NF1		9 /* for filter1 */
#define SNBEP_UNC_ATTR_ISOC	       10 /* isochronous */
#define SNBEP_UNC_ATTR_NC	       11 /* non-coherent */
#define SNBEP_UNC_ATTR_CF1	       12 /* core-filter hswep */
#define SNBEP_UNC_ATTR_TF1	       13 /* thread-filter skx */
#define SNBEP_UNC_ATTR_CF2	       14 /* core-filter (src filter) skx */
#define SNBEP_UNC_ATTR_LOC	       15 /* local node target skx */
#define SNBEP_UNC_ATTR_REM	       16 /* remote node target skx */
#define SNBEP_UNC_ATTR_LMEM	       17 /* near memory cacheable skx */
#define SNBEP_UNC_ATTR_RMEM	       18 /* not near memory cacheable skx */
#define SNBEP_UNC_ATTR_DNID	       19 /* destination node id */
#define SNBEP_UNC_ATTR_RCSNID	       20 /* RCS node id */
#define SNBEP_UNC_ATTR_T6	       21 /* threshold (cmask) 6-bit */
#define SNBEP_UNC_ATTR_OCC_I           22 /* occupancy invert */
#define SNBEP_UNC_ATTR_OCC_E           23 /* occupancy edge */

#define _SNBEP_UNC_ATTR_I	(1 << SNBEP_UNC_ATTR_I)
#define _SNBEP_UNC_ATTR_E	(1 << SNBEP_UNC_ATTR_E)
#define _SNBEP_UNC_ATTR_T8	(1 << SNBEP_UNC_ATTR_T8)
#define _SNBEP_UNC_ATTR_T5	(1 << SNBEP_UNC_ATTR_T5)
#define _SNBEP_UNC_ATTR_TF	(1 << SNBEP_UNC_ATTR_TF)
#define _SNBEP_UNC_ATTR_CF	(1 << SNBEP_UNC_ATTR_CF)
#define _SNBEP_UNC_ATTR_NF	(1 << SNBEP_UNC_ATTR_NF)
#define _SNBEP_UNC_ATTR_FF	(1 << SNBEP_UNC_ATTR_FF)
#define _SNBEP_UNC_ATTR_A	(1 << SNBEP_UNC_ATTR_A)
#define _SNBEP_UNC_ATTR_NF1	(1 << SNBEP_UNC_ATTR_NF1)
#define _SNBEP_UNC_ATTR_ISOC	(1 << SNBEP_UNC_ATTR_ISOC)
#define _SNBEP_UNC_ATTR_NC	(1 << SNBEP_UNC_ATTR_NC)
#define _SNBEP_UNC_ATTR_CF1	(1 << SNBEP_UNC_ATTR_CF1)
#define _SNBEP_UNC_ATTR_TF1	(1 << SNBEP_UNC_ATTR_TF1)
#define _SNBEP_UNC_ATTR_CF2	(1 << SNBEP_UNC_ATTR_CF2)
#define _SNBEP_UNC_ATTR_LOC	(1 << SNBEP_UNC_ATTR_LOC)
#define _SNBEP_UNC_ATTR_REM	(1 << SNBEP_UNC_ATTR_REM)
#define _SNBEP_UNC_ATTR_LMEM	(1 << SNBEP_UNC_ATTR_LMEM)
#define _SNBEP_UNC_ATTR_RMEM	(1 << SNBEP_UNC_ATTR_RMEM)
#define _SNBEP_UNC_ATTR_DNID    (1 << SNBEP_UNC_ATTR_DNID)
#define _SNBEP_UNC_ATTR_RCSNID  (1 << SNBEP_UNC_ATTR_RCSNID)
#define _SNBEP_UNC_ATTR_T6	(1 << SNBEP_UNC_ATTR_T6)
#define _SNBEP_UNC_ATTR_OCC_I	(1 << SNBEP_UNC_ATTR_OCC_I)
#define _SNBEP_UNC_ATTR_OCC_E	(1 << SNBEP_UNC_ATTR_OCC_E)

#define SNBEP_UNC_IRP_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_IRP_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8|_SNBEP_UNC_ATTR_I)

#define BDX_UNC_IRP_ATTRS HSWEP_UNC_IRP_ATTRS

#define SNBEP_UNC_R3QPI_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_R3QPI_ATTRS SNBEP_UNC_R3QPI_ATTRS
#define BDX_UNC_R3QPI_ATTRS   SNBEP_UNC_R3QPI_ATTRS

#define IVBEP_UNC_R3QPI_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define SNBEP_UNC_R2PCIE_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_R2PCIE_ATTRS SNBEP_UNC_R2PCIE_ATTRS
#define BDX_UNC_R2PCIE_ATTRS   SNBEP_UNC_R2PCIE_ATTRS

#define IVBEP_UNC_R2PCIE_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define SNBEP_UNC_QPI_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define IVBEP_UNC_QPI_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_QPI_ATTRS SNBEP_UNC_QPI_ATTRS
#define BDX_UNC_QPI_ATTRS   SNBEP_UNC_QPI_ATTRS

#define SNBEP_UNC_UBO_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define IVBEP_UNC_UBO_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_UBO_ATTRS SNBEP_UNC_UBO_ATTRS
#define BDX_UNC_UBO_ATTRS   SNBEP_UNC_UBO_ATTRS

#define SNBEP_UNC_PCU_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T5)

#define IVBEP_UNC_PCU_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T5)

#define HSWEP_UNC_PCU_ATTRS SNBEP_UNC_PCU_ATTRS

#define BDX_UNC_PCU_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T5)

#define SKX_UNC_PCU_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define ICX_UNC_PCU_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T6)

#define ICX_UNC_PCU_OCC_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T6|_SNBEP_UNC_ATTR_OCC_I|_SNBEP_UNC_ATTR_OCC_E)

#define SNBEP_UNC_PCU_BAND_ATTRS \
	(SNBEP_UNC_PCU_ATTRS | _SNBEP_UNC_ATTR_FF)

#define IVBEP_UNC_PCU_BAND_ATTRS \
	(IVBEP_UNC_PCU_ATTRS | _SNBEP_UNC_ATTR_FF)

#define HSWEP_UNC_PCU_BAND_ATTRS SNBEP_UNC_PCU_BAND_ATTRS
#define BDX_UNC_PCU_BAND_ATTRS   SNBEP_UNC_PCU_BAND_ATTRS
#define SKX_UNC_PCU_BAND_ATTRS  \
	(SKX_UNC_PCU_ATTRS | _SNBEP_UNC_ATTR_FF)

#define SNBEP_UNC_IMC_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define IVBEP_UNC_IMC_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_IMC_ATTRS SNBEP_UNC_IMC_ATTRS

#define BDX_UNC_IMC_ATTRS SNBEP_UNC_IMC_ATTRS

#define SNBEP_UNC_CBO_ATTRS   \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8  |\
	 _SNBEP_UNC_ATTR_CF  |\
	 _SNBEP_UNC_ATTR_TF)

#define IVBEP_UNC_CBO_ATTRS   \
	(_SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8  |\
	 _SNBEP_UNC_ATTR_CF  |\
	 _SNBEP_UNC_ATTR_TF)

#define HSWEP_UNC_CBO_ATTRS   \
	(_SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8  |\
	 _SNBEP_UNC_ATTR_CF1 |\
	 _SNBEP_UNC_ATTR_TF)

#define BDX_UNC_CBO_ATTRS HSWEP_UNC_CBO_ATTRS

#define SNBEP_UNC_CBO_NID_ATTRS	\
	(SNBEP_UNC_CBO_ATTRS|_SNBEP_UNC_ATTR_NF)

#define IVBEP_UNC_CBO_NID_ATTRS	\
	(IVBEP_UNC_CBO_ATTRS|_SNBEP_UNC_ATTR_NF1)

#define HSWEP_UNC_CBO_NID_ATTRS	\
	(HSWEP_UNC_CBO_ATTRS | _SNBEP_UNC_ATTR_NF1)

#define BDX_UNC_CBO_NID_ATTRS HSWEP_UNC_CBO_NID_ATTRS

#define SNBEP_UNC_HA_ATTRS \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define IVBEP_UNC_HA_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define HSWEP_UNC_HA_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8|_SNBEP_UNC_ATTR_I)

#define BDX_UNC_HA_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8|_SNBEP_UNC_ATTR_I)

#define SNBEP_UNC_HA_OPC_ATTRS \
	(SNBEP_UNC_HA_ATTRS|_SNBEP_UNC_ATTR_A)

#define HSWEP_UNC_SBO_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8|_SNBEP_UNC_ATTR_I)

#define BDX_UNC_SBO_ATTRS \
	(_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8|_SNBEP_UNC_ATTR_I)

#define KNL_UNC_CHA_TOR_ATTRS    _SNBEP_UNC_ATTR_NF1

#define SKX_UNC_CHA_ATTRS     \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define ICX_UNC_CHA_ATTRS SKX_UNC_CHA_ATTRS
#define SPR_UNC_CHA_ATTRS SKX_UNC_CHA_ATTRS

#define SKX_UNC_CHA_FILT1_ATTRS \
	(SKX_UNC_CHA_ATTRS   |\
	 _SNBEP_UNC_ATTR_LOC |\
	 _SNBEP_UNC_ATTR_REM |\
	 _SNBEP_UNC_ATTR_LMEM|\
	 _SNBEP_UNC_ATTR_RMEM|\
	 _SNBEP_UNC_ATTR_NC  |\
	 _SNBEP_UNC_ATTR_ISOC)

#define SKX_UNC_IIO_ATTRS     \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define ICX_UNC_IIO_ATTRS     \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define SKX_UNC_IMC_ATTRS     \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define ICX_UNC_IMC_ATTRS SKX_UNC_IMC_ATTRS
#define SPR_UNC_IMC_ATTRS SKX_UNC_IMC_ATTRS
#define GNR_UNC_IMC_ATTRS SKX_UNC_IMC_ATTRS

#define ICX_UNC_M2PCIE_ATTRS SKX_UNC_IMC_ATTRS

#define SKX_UNC_IRP_ATTRS     \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define ICX_UNC_IRP_ATTRS     SKX_UNC_IRP_ATTRS

#define SKX_UNC_M2M_ATTRS     \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define ICX_UNC_M2M_ATTRS     SKX_UNC_M2M_ATTRS

#define SKX_UNC_M3UPI_ATTRS   \
	(_SNBEP_UNC_ATTR_I   |\
	 _SNBEP_UNC_ATTR_E   |\
	 _SNBEP_UNC_ATTR_T8)

#define ICX_UNC_M3UPI_ATTRS  SKX_UNC_M3UPI_ATTRS

#define SKX_UNC_UBO_ATTRS   SNBEP_UNC_UBO_ATTRS
#define ICX_UNC_UBO_ATTRS   SNBEP_UNC_UBO_ATTRS

#define SKX_UNC_UPI_ATTRS   \
	(_SNBEP_UNC_ATTR_I|_SNBEP_UNC_ATTR_E|_SNBEP_UNC_ATTR_T8)

#define ICX_UNC_UPI_ATTRS   SKX_UNC_UPI_ATTRS
#define SPR_UNC_UPI_ATTRS   SKX_UNC_UPI_ATTRS

#define SKX_UNC_UPI_OPC_ATTRS   \
	(SKX_UNC_UPI_ATTRS |\
	 _SNBEP_UNC_ATTR_DNID| _SNBEP_UNC_ATTR_RCSNID)

typedef union {
	uint64_t val;
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_res2:3;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_res3:32;	/* reserved */
	} com; /* covers common fields for cbox, ha, imc, ubox, r2pcie, r3qpi, sbox */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detect */
		unsigned long unc_tid:1;	/* tid filter enable */
		unsigned long unc_res2:2;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_res3:32;	/* reserved */
	} cbo; /* covers c-box */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detect */
		unsigned long unc_tid:1;	/* tid filter enable */
		unsigned long unc_ov:1;		/* overflow enable */
		unsigned long unc_res2:1;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_res3:32;	/* reserved */
	} cha; /* covers skx cha */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detect */
		unsigned long unc_tid:1;	/* tid filter enable */
		unsigned long unc_ov:1;		/* overflow enable */
		unsigned long unc_res2:1;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_umask_ext:26;	/* extended umask */
		unsigned long unc_res3:9;	/* reserved */
	} icx_cha; /* covers icx cha */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_res2:3;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_umask2:8;	/* extended unit mask */
		unsigned long unc_res3:24;	/* reserved */
	} icx_m2m; /* covers icx m2m */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_tid_en:1;	/* tid enable */
		unsigned long unc_res2:2;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:6;	/* counter mask */
		unsigned long unc_occ_inv:1;	/* occupancy event invert */
		unsigned long unc_occ_edge:1;	/* occupancy event edge */
		unsigned long unc_res3:24;	/* reserved */
	} icx_pcu; /* covers icx pcu */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detect */
		unsigned long unc_tid:1;	/* tid filter enable */
		unsigned long unc_ov:1;		/* overflow enable */
		unsigned long unc_res2:1;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_chmsk:8;	/* channel mask */
		unsigned long unc_fcmsk:8;	/* fc mask */
		unsigned long unc_res3:16;	/* reserved */
	} iio; /* covers skx iio*/
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_res1:6;	/* reserved */
		unsigned long unc_occ:2;	/* occ select */
		unsigned long unc_res2:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_res3:1;	/* reserved */
		unsigned long unc_res4:2;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:5;	/* threshold */
		unsigned long unc_res5:1;	/* reserved */
		unsigned long unc_occ_inv:1;	/* occupancy invert */
		unsigned long unc_occ_edge:1;	/* occupancy edge detect */
		unsigned long unc_res6:32;	/* reserved */
	} pcu; /* covers pcu */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_res1:6;	/* reserved */
		unsigned long unc_occ:2;	/* occ select */
		unsigned long unc_res2:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_res3:1;	/* reserved */
		unsigned long unc_ov_en:1;	/* overflow enable */
		unsigned long unc_sel_ext:1;	/* event_sel extension */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_res4:1;	/* reserved */
		unsigned long unc_thres:5;	/* threshold */
		unsigned long unc_res5:1;	/* reserved */
		unsigned long unc_occ_inv:1;	/* occupancy invert */
		unsigned long unc_occ_edge:1;	/* occupancy edge detect */
		unsigned long unc_res6:32;	/* reserved */
	} ivbep_pcu; /* covers ivb-ep pcu */
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit maks */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_res2:1;	/* reserved */
		unsigned long unc_res3:1;	/* reserved */
		unsigned long unc_event_ext:1;	/* event code extension */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_inv:1;	/* invert counter mask */
		unsigned long unc_thres:8;	/* threshold */
		unsigned long unc_res4:32;	/* reserved */
	} qpi; /* covers qpi */
	struct {
		unsigned long tid:1;
		unsigned long cid:3;
		unsigned long res0:1;
		unsigned long res1:3;
		unsigned long res2:2;
		unsigned long nid:8;
		unsigned long state:5;
		unsigned long opc:9;
		unsigned long res3:1;
		unsigned long res4:32;
	} cbo_filt; /* cbox filter */
	struct {
		unsigned long tid:1;
		unsigned long cid:4;
		unsigned long res0:12;
		unsigned long state:6;
		unsigned long res1:9;
		unsigned long res2:32;
	} ivbep_cbo_filt0; /* ivbep cbox filter0 */
	struct {
		unsigned long nid:16;
		unsigned long res0:4;
		unsigned long opc:9;
		unsigned long res1:1;
		unsigned long nc:1;
		unsigned long isoc:1;
		unsigned long res2:32;
	} ivbep_cbo_filt1; /* ivbep cbox filter1 */
	struct {
		unsigned long tid:1;
		unsigned long cid:5;
		unsigned long res0:11;
		unsigned long state:7;
		unsigned long res1:8;
		unsigned long res2:32;
	} hswep_cbo_filt0; /* hswep cbox filter0 */
	struct {
		unsigned long nid:16;
		unsigned long res0:4;
		unsigned long opc:9;
		unsigned long res1:1;
		unsigned long nc:1;
		unsigned long isoc:1;
		unsigned long res2:32;
	} hswep_cbo_filt1; /* hswep cbox filter1 */
	struct {
		unsigned long tid:3; /* thread 0-3 */
		unsigned long sid:6; /* source id */
		unsigned long res0:8;
		unsigned long state:10; /* llc lookup cacheline state */
		unsigned long res1:32;
		unsigned long res2:5;
	} skx_cha_filt0; /* skx cha filter0 */
	struct {
		unsigned long rem:1;
		unsigned long loc:1;
		unsigned long res0:1;
		unsigned long all_opc:1;
		unsigned long nm:1;
		unsigned long not_nm:1;
		unsigned long res1:3;
		unsigned long opc0:10;
		unsigned long opc1:10;
		unsigned long res2:1;
		unsigned long nc:1;
		unsigned long isoc:1;
		unsigned long res3:32;
	} skx_cha_filt1; /* skx cha filter1 */
	struct {
		unsigned long opc:1;
		unsigned long loc:1;
		unsigned long rem:1;
		unsigned long data:1;
		unsigned long nondata:1;
		unsigned long dualslot:1;
		unsigned long sglslot:1;
		unsigned long isoch:1;
		unsigned long dnid:4;
		unsigned long res1:1;
		unsigned long en_dnidd:1;
		unsigned long rcsnid:4;
		unsigned long en_rcsnid:1;
		unsigned long slot0:1;
		unsigned long slot1:1;
		unsigned long slot2:1;
		unsigned long llcrd_non0:1;
		unsigned long llcrd_implnull:1;
		unsigned long res2:9;
	} skx_upi_filt; /* skx upi basic_hdr_filt */
	struct {
		unsigned long filt0:8; /* band0 freq filter */
		unsigned long filt1:8; /* band1 freq filter */
		unsigned long filt2:8; /* band2 freq filter */
		unsigned long filt3:8; /* band3 freq filter */
		unsigned long res1:32; /* reserved */
	} pcu_filt;
	struct {
		unsigned long res1:6;
		unsigned long lo_addr:26; /* lo order 26b */
		unsigned long hi_addr:14; /* hi order 14b */
		unsigned long res2:18; /* reserved */
	} ha_addr;
	struct {
		unsigned long opc:6; /* opcode match */
		unsigned long res1:26; /* reserved */
		unsigned long res2:32; /* reserved */
	} ha_opc;
	struct {
		unsigned long unc_event:8;	/* event code */
		unsigned long unc_umask:8;	/* unit mask */
		unsigned long unc_res1:1;	/* reserved */
		unsigned long unc_rst:1;	/* reset */
		unsigned long unc_edge:1;	/* edge detec */
		unsigned long unc_res2:3;	/* reserved */
		unsigned long unc_en:1;		/* enable */
		unsigned long unc_res3:1;	/* reserved */
		unsigned long unc_thres:8;	/* counter mask */
		unsigned long unc_res4:32;	/* reserved */
	} irp; /* covers irp */
} pfm_snbep_unc_reg_t;

extern void pfm_intel_snbep_unc_perf_validate_pattrs(void *this, pfmlib_event_desc_t *e);
extern int  pfm_intel_snbep_unc_get_encoding(void *this, pfmlib_event_desc_t *e);
extern const pfmlib_attr_desc_t snbep_unc_mods[];
extern int  pfm_intel_snbep_unc_detect(void *this);
extern int  pfm_intel_ivbep_unc_detect(void *this);
extern int  pfm_intel_hswep_unc_detect(void *this);
extern int  pfm_intel_knl_unc_detect(void *this);
extern int  pfm_intel_knm_unc_detect(void *this);
extern int  pfm_intel_bdx_unc_detect(void *this);
extern int  pfm_intel_skx_unc_detect(void *this);
extern int  pfm_intel_icx_unc_detect(void *this);
extern int  pfm_intel_spr_unc_detect(void *this);
extern int  pfm_intel_gnr_unc_detect(void *this);
extern int  pfm_intel_snbep_unc_get_perf_encoding(void *this, pfmlib_event_desc_t *e);
extern int  pfm_intel_snbep_unc_can_auto_encode(void *this, int pidx, int uidx);
extern int pfm_intel_snbep_unc_get_event_attr_info(void *this, int pidx, int attr_idx, pfmlib_event_attr_info_t *info);

static inline int
is_cha_filt_event(void *this, int x, pfm_snbep_unc_reg_t reg)
{
	pfmlib_pmu_t *pmu = this;
	uint64_t sel = reg.com.unc_event;
	/*
	 * TOR_INSERT: event code 0x35
	 * TOR_OCCUPANCY: event code 0x36
	 * LLC_LOOKUP : event code 0x34
	 */
	if (!(pmu->flags & INTEL_PMU_FL_UNC_CHA))
		return 0;
	if (x == 0)
		return sel == 0x34;
	if (x == 1)
		return sel == 0x35 || sel == 0x36;
	return 0;
}


static inline int
is_cbo_filt_event(void *this, pfm_snbep_unc_reg_t reg)
{
	pfmlib_pmu_t *pmu = this;
	uint64_t sel = reg.com.unc_event;
	/*
	 * Cbox-only: umask bit 0 must be 1 (OPCODE)
	 *
	 * TOR_INSERT: event code 0x35
	 * TOR_OCCUPANCY: event code 0x36
	 * LLC_LOOKUP : event code 0x34
	 */
	if (pmu->flags & INTEL_PMU_FL_UNC_CBO)
		return (reg.com.unc_umask & 0x1) && (sel == 0x35 || sel == 0x36 || sel == 0x34);
	return 0;
}

#endif /* __PFMLIB_INTEL_SNBEP_UNC_PRIV_H__ */
