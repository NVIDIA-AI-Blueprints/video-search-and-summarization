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
 * PMU: hswep_unc_sbo (Intel Haswell-EP S-Box uncore PMU)
 */

static const intel_x86_umask_t hswep_unc_s_ring_ad_used[]={
   { .uname  = "UP_EVEN",
     .udesc  = "Up and Even ring polarity filter",
     .ucode = 0x100,
   },
   { .uname  = "UP_ODD",
     .udesc  = "Up and odd ring polarity filter",
     .ucode = 0x200,
   },
   { .uname  = "DOWN_EVEN",
     .udesc  = "Down and even ring polarity filter",
     .ucode = 0x400,
   },
   { .uname  = "DOWN_ODD",
     .udesc  = "Down and odd ring polarity filter",
     .ucode = 0x800,
   },
   { .uname  = "UP",
     .udesc  = "Up ring polarity filter",
     .ucode = 0x3300,
   },
   { .uname  = "DOWN",
     .udesc  = "Down ring polarity filter",
     .ucode = 0xcc00,
   },
};

static const intel_x86_umask_t hswep_unc_s_ring_bounces[]={
   { .uname  = "AD_CACHE",
     .udesc  = "AD_CACHE",
     .ucode = 0x100,
   },
   { .uname  = "AK_CORE",
     .udesc  = "Acknowledgments to core",
     .ucode = 0x200,
   },

   { .uname  = "BL_CORE",
     .udesc  = "Data responses to core",
     .ucode = 0x400,
   },
   { .uname  = "IV_CORE",
     .udesc  = "Snoops of processor cache",
     .ucode = 0x800,
   },
};

static const intel_x86_umask_t hswep_unc_s_ring_iv_used[]={
   { .uname  = "ANY",
     .udesc  = "Any filter",
     .ucode = 0x0f00,
     .uflags = INTEL_X86_DFL,
   },
   { .uname  = "UP",
     .udesc  = "Filter on any up polarity",
     .ucode = 0x0300,
   },
   { .uname  = "DOWN",
     .udesc  = "Filter on any down polarity",
     .ucode = 0xcc00,
   },
};

static const intel_x86_umask_t hswep_unc_s_rxr_bypass[]={
   { .uname  = "AD_CRD",
     .udesc  = "AD credis",
     .ucode = 0x0100,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "AD_BNC",
     .udesc  = "AD bounces",
     .ucode = 0x0200,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "BL_CRD",
     .udesc  = "BL credits",
     .ucode = 0x0400,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "BL_BNC",
     .udesc  = "BL bounces",
     .ucode = 0x0800,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "AK",
     .udesc  = "AK",
     .ucode = 0x1000,
     .uflags = INTEL_X86_NCOMBO,
   },
   { .uname  = "IV",
     .udesc  = "IV",
     .ucode = 0x2000,
     .uflags = INTEL_X86_NCOMBO,
   },
};

static const intel_x86_umask_t hswep_unc_s_txr_ads_used[]={
  { .uname = "AD",
    .udesc  = "onto AD ring",
    .ucode  = 0x100,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "AK",
    .udesc  = "Onto AK ring",
    .ucode  = 0x200,
    .uflags = INTEL_X86_NCOMBO,
  },
  { .uname = "BL",
    .udesc  = "Onto BL ring",
    .ucode  = 0x400,
    .uflags = INTEL_X86_NCOMBO,
  }
};

static const intel_x86_entry_t intel_hswep_unc_s_pe[]={
  { .name   = "UNC_S_CLOCKTICKS",
    .desc   = "S-box Uncore clockticks",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x00,
  },
  { .name   = "UNC_S_RING_AD_USED",
    .desc   = "Address ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1b,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_ring_ad_used),
    .ngrp = 1,
    .umasks = hswep_unc_s_ring_ad_used,
  },
  { .name   = "UNC_S_RING_AK_USED",
    .desc   = "Acknowledgement ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1c,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_ring_ad_used), /* identical to RING_AD_USED */
    .ngrp = 1,
    .umasks = hswep_unc_s_ring_ad_used,
  },
  { .name   = "UNC_S_RING_BL_USED",
    .desc   = "Bus or Data ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1d,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_ring_ad_used), /* identical to RING_AD_USED */
    .ngrp = 1,
    .umasks = hswep_unc_s_ring_ad_used,
  },
  { .name   = "UNC_S_RING_IV_USED",
    .desc   = "Invalidate ring in use. Counts number of cycles ring is being used at this ring stop",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x1e,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_ring_iv_used),
    .ngrp = 1,
    .umasks = hswep_unc_s_ring_iv_used,
  },
  { .name   = "UNC_S_RING_BOUNCES",
    .desc   = "Number of LLC responses that bounced in the ring",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x05,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_ring_bounces),
    .ngrp = 1,
    .umasks = hswep_unc_s_ring_bounces,
  }, { .name   = "UNC_S_FAST_ASSERTED",
    .desc   = "Number of cycles in which the local distress or incoming distress signals are asserted (FaST). Incoming distress includes both up and down",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x09,
  },
  { .name   = "UNC_C_BOUNCE_CONTROL",
    .desc   = "Bounce control",
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .cntmsk = 0xf,
    .code = 0x0a,
  },
  { .name = "UNC_S_RXR_OCCUPANCY",
    .desc = "Ingress Occupancy",
    .code = 0x11,
    .cntmsk = 0x1,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_rxr_bypass), /* shared with rxr_bypass */
    .umasks  = hswep_unc_s_rxr_bypass,
  },
  { .name = "UNC_S_RXR_BYPASS",
    .desc = "Ingress Allocations",
    .code = 0x12,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_rxr_bypass),
    .umasks  = hswep_unc_s_rxr_bypass
  },
  { .name = "UNC_S_RXR_INSERTS",
    .desc = "Ingress Allocations",
    .code = 0x13,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_rxr_bypass), /* shared with rxr_bypass */
    .umasks  = hswep_unc_s_rxr_bypass
  },
  { .name = "UNC_S_TXR_ADS_USED",
    .desc = "Egress events",
    .code = 0x04,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_txr_ads_used),
    .umasks  = hswep_unc_s_txr_ads_used
  },
  { .name = "UNC_S_TXR_INSERTS",
    .desc = "Egress allocations",
    .code = 0x02,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_rxr_bypass), /* shared with rxr_bypass */
    .umasks  = hswep_unc_s_rxr_bypass
  },
  { .name = "UNC_S_TXR_OCCUPANCY",
    .desc = "Egress allocations",
    .code = 0x01,
    .cntmsk = 0xf,
    .ngrp = 1,
    .modmsk = HSWEP_UNC_SBO_ATTRS,
    .numasks = LIBPFM_ARRAY_SIZE(hswep_unc_s_rxr_bypass), /* shared with rxr_bypass */
    .umasks  = hswep_unc_s_rxr_bypass
  },
};
