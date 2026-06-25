/*
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
 *
 * This file is part of libpfm, a performance monitoring support library for
 * applications on Linux.
 *
 * This file has been automatically generated.
 *
 * PMU: snbep_unc_r2pcie (Intel SandyBridge-EP R2PCIe uncore)
 */

static const intel_x86_umask_t snbep_unc_r2_ring_ad_used[]={
  { .uname = "CCW_EVEN",
    .udesc  = "Counter-clockwise and even ring polarity",
    .ucode  = 0x400,
  },
  { .uname = "CCW_ODD",
    .udesc  = "Counter-clockwise and odd ring polarity",
    .ucode  = 0x800,
  },
  { .uname = "CW_EVEN",
    .udesc  = "Clockwise and even ring polarity",
    .ucode  = 0x100,
  },
  { .uname = "CW_ODD",
    .udesc  = "Clockwise and odd ring polarity",
    .ucode  = 0x200,
  },
  { .uname = "CW_ANY",
    .udesc  = "Clockwise with any polarity",
    .ucode  = 0x300,
  },
  { .uname = "CCW_ANY",
    .udesc  = "Counter-clockwise with any polarity",
    .ucode  = 0xc00,
  },
  { .uname = "ANY",
    .udesc  = "any direction and any polarity",
    .ucode  = 0xf00,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
};

static const intel_x86_umask_t snbep_unc_r2_ring_iv_used[]={
  { .uname = "ANY",
    .udesc  = "R2 IV Ring in Use",
    .ucode  = 0xf00,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
};

static const intel_x86_umask_t snbep_unc_r2_rxr_cycles_ne[]={
  { .uname = "DRS",
    .udesc  = "DRS Ingress queue",
    .ucode  = 0x800,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "NCB",
    .udesc  = "NCB Ingress queue",
    .ucode  = 0x1000,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "NCS",
    .udesc  = "NCS Ingress queue",
    .ucode  = 0x2000,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_umask_t snbep_unc_r2_txr_cycles_full[]={
  { .uname = "AD",
    .udesc  = "AD Egress queue",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "AK",
    .udesc  = "AK Egress queue",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL",
    .udesc  = "BL Egress queue",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  },
};

static const intel_x86_entry_t intel_snbep_unc_r2_pe[]={
  { .name = "UNC_R2_CLOCKTICKS",
    .desc = "Number of uclks in domain",
    .code = 0x1,
    .cntmsk = 0xf,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
  },
  { .name = "UNC_R2_RING_AD_USED",
    .desc = "R2 AD Ring in Use",
    .code = 0x7,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_ring_ad_used),
    .umasks  = snbep_unc_r2_ring_ad_used
  },
  { .name = "UNC_R2_RING_AK_USED",
    .desc = "R2 AK Ring in Use",
    .code = 0x8,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_ring_ad_used),
    .umasks  = snbep_unc_r2_ring_ad_used /* shared */
  },
  { .name = "UNC_R2_RING_BL_USED",
    .desc = "R2 BL Ring in Use",
    .code = 0x9,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_ring_ad_used),
    .umasks  = snbep_unc_r2_ring_ad_used /* shared */
  },
  { .name = "UNC_R2_RING_IV_USED",
    .desc = "R2 IV Ring in Use",
    .code = 0xa,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_ring_iv_used),
    .umasks  = snbep_unc_r2_ring_iv_used
  },
  { .name = "UNC_R2_RXR_AK_BOUNCES",
    .desc = "AK Ingress Bounced",
    .code = 0x12,
    .cntmsk = 0x1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
  },
  { .name = "UNC_R2_RXR_CYCLES_NE",
    .desc = "Ingress Cycles Not Empty",
    .code = 0x10,
    .cntmsk = 0x3,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_rxr_cycles_ne),
    .umasks  = snbep_unc_r2_rxr_cycles_ne
  },
  { .name = "UNC_R2_TXR_CYCLES_FULL",
    .desc = "Egress Cycles Full",
    .code = 0x25,
    .cntmsk = 0x1,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_txr_cycles_full),
    .umasks  = snbep_unc_r2_txr_cycles_full
  },
  { .name = "UNC_R2_TXR_CYCLES_NE",
    .desc = "Egress Cycles Not Empty",
    .code = 0x23,
    .cntmsk = 0x1,
    .ngrp = 1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(snbep_unc_r2_txr_cycles_full),
    .umasks  = snbep_unc_r2_txr_cycles_full /* shared */
  },
  { .name = "UNC_R2_TXR_INSERTS",
    .desc = "Egress allocations",
    .code = 0x24,
    .cntmsk = 0x1,
    .modmsk = SNBEP_UNC_R2PCIE_ATTRS,
  },
};
