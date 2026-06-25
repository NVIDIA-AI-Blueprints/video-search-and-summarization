/*
 * Copyright (c) 2016 Intel Corp. All rights reserved
 * Contributed by Peinan Zhang <peinan.zhang@intel.com>
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
 * PMU: knl_unc_m2pcie (Intel Knights Landing M2PCIe uncore)
 */


static const intel_x86_umask_t knl_unc_m2p_ingress_cycles_ne[]={
  { .uname = "CBO_IDI",
    .udesc  = "CBO_IDI",
    .ucode  = 0x0100,
  },
  { .uname = "CBO_NCB",
    .udesc  = "CBO_NCB",
    .ucode  = 0x0200,
  },
  { .uname = "CBO_NCS",
    .udesc  = "CBO_NCS",
    .ucode  = 0x0400,
  },
  { .uname = "ALL",
    .udesc  = "All",
    .ucode  = 0x0800,
    .uflags = INTEL_X86_NCOMBO | INTEL_X86_DFL,
  },
};


static const intel_x86_umask_t knl_unc_m2p_egress_cycles[]={
  { .uname = "AD_0",
    .udesc  = "AD_0",
    .ucode  = 0x0100,
  },
  { .uname = "AK_0",
    .udesc  = "AK_0",
    .ucode  = 0x0200,
  },
  { .uname = "BL_0",
    .udesc  = "BL_0",
    .ucode  = 0x0400,
  },
  { .uname = "AD_1",
    .udesc  = "AD_1",
    .ucode  = 0x0800,
  },
  { .uname = "AK_1",
    .udesc  = "AK_1",
    .ucode  = 0x1000,
  },
  { .uname = "BL_1",
    .udesc  = "BL_1",
    .ucode  = 0x2000,
  },
};

static const intel_x86_umask_t knl_unc_m2p_egress_inserts[]={
  { .uname = "AD_0",
    .udesc  = "AD_0",
    .ucode  = 0x0100,
  },
  { .uname = "AK_0",
    .udesc  = "AK_0",
    .ucode  = 0x0200,
  },
  { .uname = "BL_0",
    .udesc  = "BL_0",
    .ucode  = 0x0400,
  },
  { .uname = "AK_CRD_0",
    .udesc  = "AK_CRD_0",
    .ucode  = 0x0800,
  },
  { .uname = "AD_1",
    .udesc  = "AD_1",
    .ucode  = 0x1000,
  },
  { .uname = "AK_1",
    .udesc  = "AK_1",
    .ucode  = 0x2000,
  },
  { .uname = "BL_1",
    .udesc  = "BL_1",
    .ucode  = 0x4000,
  },
  { .uname = "AK_CRD_1",
    .udesc  = "AK_CRD_1",
    .ucode  = 0x8000,
  },
};

static const intel_x86_entry_t intel_knl_unc_m2pcie_pe[]={
  { .name = "UNC_M2P_INGRESS_CYCLES_NE",
    .desc = "Ingress Queue Cycles Not Empty. Counts the number of cycles when the M2PCIe Ingress is not empty",
    .code = 0x10,
    .cntmsk = 0xf,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(knl_unc_m2p_ingress_cycles_ne),
    .umasks  = knl_unc_m2p_ingress_cycles_ne
  },
  { .name = "UNC_M2P_EGRESS_CYCLES_NE",
    .desc = "Egress (to CMS) Cycles Not Empty. Counts the number of cycles when the M2PCIe Egress is not empty",
    .code = 0x23,
    .cntmsk = 0x3,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(knl_unc_m2p_egress_cycles),
    .umasks  = knl_unc_m2p_egress_cycles
  },
  { .name = "UNC_M2P_EGRESS_INSERTS",
    .desc = "Egress (to CMS) Ingress. Counts the number of number of messages inserted into the  the M2PCIe Egress queue",
    .code = 0x24,
    .cntmsk = 0xf,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(knl_unc_m2p_egress_inserts),
    .umasks  = knl_unc_m2p_egress_inserts
  },
  { .name = "UNC_M2P_EGRESS_CYCLES_FULL",
    .desc = "Egress (to CMS) Cycles Full. Counts the number of cycles when the M2PCIe Egress is full",
    .code = 0x25,
    .cntmsk = 0xf,
    .ngrp = 1,
    .numasks = LIBPFM_ARRAY_SIZE(knl_unc_m2p_egress_cycles),
    .umasks  = knl_unc_m2p_egress_cycles
  },
};
