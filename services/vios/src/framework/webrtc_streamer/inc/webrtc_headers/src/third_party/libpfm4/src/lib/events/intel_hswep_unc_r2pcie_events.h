/*
 * Copyright (c) 2014 Google Inc. All rights reserved
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
 *
 * This file is part of libpfm, a performance monitoring support library for
 * applications on Linux.
 *
 * PMU: hswep_unc_r2pcie (Intel Haswell-EP R2PCIe uncore)
 */

static const intel_x86_umask_t hswep_unc_r2_ring_ad_used[]={
  { .uname = "CCW_EVEN",
    .udesc  = "Counter-clockwise and even ring polarity on virtual ring",
    .ucode  = 0x400,
  },
  { .uname = "CCW_ODD",
    .udesc  = "Counter-clockwise and odd ring polarity on virtual ring",
    .ucode  = 0x800,
  },
  { .uname = "CW_EVEN",
    .udesc  = "Clockwise and even ring polarity on virtual ring",
    .ucode  = 0x100,
  },
  { .uname = "CW_ODD",
    .udesc  = "Clockwise and odd ring polarity on virtual ring",
    .ucode  = 0x200,
  },
  { .uname = "CW",
    .udesc  = "Clockwise with any polarity on either virtual rings",
    .ucode  = 0x0300,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "CCW",
    .udesc  = "Counter-clockwise with any polarity on either virtual rings",
    .ucode  = 0x0c00,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t hswep_unc_r2_rxr_ak_bounces[]={
  { .uname = "UP",
    .udesc  = "Up",
    .ucode  = 0x100,
  },
  { .uname = "DOWN",
    .udesc  = "Down",
    .ucode  = 0x200,
  },
};

static const intel_x86_umask_t hswep_unc_r2_rxr_occupancy[]={
  { .uname = "DRS",
    .udesc  = "DRS Ingress queue",
    .ucode  = 0x800,
    .uflags = INTEL_X86_DFL,
  },
};

static const intel_x86_umask_t hswep_unc_r2_ring_iv_used[]={
  { .uname = "CW",
    .udesc  = "Clockwise with any polarity on virtual ring",
    .ucode  = 0x300,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "CCW",
    .udesc  = "Counter-clockwise with any polarity on virtual ring",
    .ucode  = 0xc00,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "ANY",
    .udesc  = "any direction and any polarity on virtual ring",
    .ucode  = 0xff00,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
};

static const intel_x86_umask_t hswep_unc_r2_rxr_cycles_ne[]={
  { .uname = "NCB",
    .udesc  = "NCB Ingress queue",
    .ucode  = 0x1000,
  },
  { .uname = "NCS",
    .udesc  = "NCS Ingress queue",
    .ucode  = 0x2000,
  },
};

static const intel_x86_umask_t hswep_unc_r2_sbo0_credits_acquired[]={
  { .uname = "AD",
    .udesc  = "For ring AD",
    .ucode  = 0x100,
  },
  { .uname = "BL",
    .udesc  = "For ring BL",
    .ucode  = 0x200,
  },
};

static const intel_x86_umask_t hswep_unc_r2_iio_credit[]={
  { .uname = "PRQ_QPI0",
    .udesc  = "QPI0",
    .ucode  = 0x100,
  },
  { .uname = "PRQ_QPI1",
    .udesc  = "QPI1",
    .ucode  = 0x200,
  },
  { .uname = "ISOCH_QPI0",
    .udesc  = "Isochronous QPI0",
    .ucode  = 0x400,
  },
  { .uname = "ISOCH_QPI1",
    .udesc  = "Isochronous QPI1",
    .ucode  = 0x800,
  },
};

static const intel_x86_umask_t hswep_unc_r2_txr_nack_cw[]={
  { .uname = "DN_AD",
    .udesc  = "AD counter clockwise Egress queue",
    .ucode  = 0x100,
  },
  { .uname = "DN_BL",
    .udesc  = "BL counter clockwise Egress queue",
    .ucode  = 0x200,
  },
  { .uname = "DN_AK",
    .udesc  = "AK counter clockwise Egress queue",
    .ucode  = 0x400,
  },
  { .uname = "UP_AD",
    .udesc  = "AD clockwise Egress queue",
    .ucode  = 0x800,
  },
  { .uname = "UP_BL",
    .udesc  = "BL clockwise Egress queue",
    .ucode  = 0x1000,
  },
  { .uname = "UP_AK",
    .udesc  = "AK clockwise Egress queue",
    .ucode  = 0x2000,
  },
};

static const intel_x86_umask_t hswep_unc_r2_stall_no_sbo_credit[]={
  { .uname = "SBO0_AD",
    .udesc  = "For SBO0, AD ring",
    .ucode  = 0x100,
  },
  { .uname = "SBO1_AD",
    .udesc  = "For SBO1, AD ring",
    .ucode  = 0x100,
  },
  { .uname = "SBO0_BL",
    .udesc  = "For SBO0, BL ring",
    .ucode  = 0x100,
  },
  { .uname = "SBO1_BL",
    .udesc  = "For SBO1, BL ring",
    .ucode  = 0x100,
  },
};

static const intel_x86_entry_t intel_hswep_unc_r2_pe[]={
  { .name = "UNC_R2_CLOCKTICKS",
    .desc = "Number of uclks in domain",
    .code = 0x1,
    .cntmsk = 0xf,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
  },
  { .name = "UNC_R2_RING_AD_USED",
    .desc = "R2 AD Ring in Use",
    .code = 0x7,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_ring_ad_used),
    .umasks  = hswep_unc_r2_ring_ad_used
  },
  { .name = "UNC_R2_RING_AK_USED",
    .desc = "R2 AK Ring in Use",
    .code = 0x8,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_ring_ad_used),
    .umasks  = hswep_unc_r2_ring_ad_used /* shared */
  },
  { .name = "UNC_R2_RING_BL_USED",
    .desc = "R2 BL Ring in Use",
    .code = 0x9,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_ring_ad_used),
    .umasks  = hswep_unc_r2_ring_ad_used /* shared */
  },
  { .name = "UNC_R2_RING_IV_USED",
    .desc = "R2 IV Ring in Use",
    .code = 0xa,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_ring_iv_used),
    .umasks  = hswep_unc_r2_ring_iv_used
  },
  { .name = "UNC_R2_RXR_AK_BOUNCES",
    .desc = "AK Ingress Bounced",
    .code = 0x12,
    .cntmsk = 0xf,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_rxr_ak_bounces),
    .umasks  = hswep_unc_r2_rxr_ak_bounces
  },
  { .name = "UNC_R2_RXR_OCCUPANCY",
    .desc = "Ingress occupancy accumulator",
    .code = 0x13,
    .cntmsk = 0x1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_rxr_occupancy),
    .umasks  = hswep_unc_r2_rxr_occupancy
  },
  { .name = "UNC_R2_RXR_CYCLES_NE",
    .desc = "Ingress Cycles Not Empty",
    .code = 0x10,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_rxr_cycles_ne),
    .umasks  = hswep_unc_r2_rxr_cycles_ne
  },
  { .name = "UNC_R2_RXR_INSERTS",
    .desc = "Ingress inserts",
    .code = 0x11,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_rxr_cycles_ne),
    .umasks  = hswep_unc_r2_rxr_cycles_ne, /* shared */
  },
    { .name = "UNC_R2_TXR_NACK_CW",
    .desc = "Egress clockwise BACK",
    .code = 0x26,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_txr_nack_cw),
    .umasks  = hswep_unc_r2_txr_nack_cw,
  },
  { .name = "UNC_R2_SBO0_CREDITS_ACQUIRED",
    .desc = "SBO0 credits acquired",
    .code = 0x28,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_sbo0_credits_acquired),
    .umasks  = hswep_unc_r2_sbo0_credits_acquired,
  },
  { .name = "UNC_R2_STALL_NO_SBO_CREDIT",
    .desc = "Stall on No SBo Credits",
    .code = 0x2c,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_stall_no_sbo_credit),
    .umasks  = hswep_unc_r2_stall_no_sbo_credit
  },
  { .name = "UNC_R2_IIO_CREDIT",
    .desc = "Egress counter-clockwise BACK",
    .code = 0x2d,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_r2_iio_credit),
    .umasks  = hswep_unc_r2_iio_credit,
  },
};
