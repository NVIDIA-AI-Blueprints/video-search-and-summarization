/****************************/
/* THIS IS OPEN SOURCE CODE */
/****************************/

#ifndef __PPC970MP_EVENTS_H__
#define __PPC970MP_EVENTS_H__

/*
* File:    ppc970mp_events.h
* CVS:
* Author:  Corey Ashford
*          cjashfor@us.ibm.com
* Mods:    <your name here>
*          <your email address>
*
* (C) Copyright IBM Corporation, 2009.  All Rights Reserved.
* Contributed by Corey Ashford <cjashfor.ibm.com>
*
* Note: This code was automatically generated and should not be modified by
* hand.
*
*/
#define PPC970MP_PME_PM_LSU_REJECT_RELOAD_CDF 0
#define PPC970MP_PME_PM_MRK_LSU_SRQ_INST_VALID 1
#define PPC970MP_PME_PM_FPU1_SINGLE 2
#define PPC970MP_PME_PM_FPU0_STALL3 3
#define PPC970MP_PME_PM_TB_BIT_TRANS 4
#define PPC970MP_PME_PM_GPR_MAP_FULL_CYC 5
#define PPC970MP_PME_PM_MRK_ST_CMPL 6
#define PPC970MP_PME_PM_FPU0_STF 7
#define PPC970MP_PME_PM_FPU1_FMA 8
#define PPC970MP_PME_PM_LSU1_FLUSH_ULD 9
#define PPC970MP_PME_PM_MRK_INST_FIN 10
#define PPC970MP_PME_PM_MRK_LSU0_FLUSH_UST 11
#define PPC970MP_PME_PM_LSU_LRQ_S0_ALLOC 12
#define PPC970MP_PME_PM_FPU_FDIV 13
#define PPC970MP_PME_PM_FPU0_FULL_CYC 14
#define PPC970MP_PME_PM_FPU_SINGLE 15
#define PPC970MP_PME_PM_FPU0_FMA 16
#define PPC970MP_PME_PM_MRK_LSU1_FLUSH_ULD 17
#define PPC970MP_PME_PM_LSU1_FLUSH_LRQ 18
#define PPC970MP_PME_PM_DTLB_MISS 19
#define PPC970MP_PME_PM_CMPLU_STALL_FXU 20
#define PPC970MP_PME_PM_MRK_ST_MISS_L1 21
#define PPC970MP_PME_PM_EXT_INT 22
#define PPC970MP_PME_PM_MRK_LSU1_FLUSH_LRQ 23
#define PPC970MP_PME_PM_MRK_ST_GPS 24
#define PPC970MP_PME_PM_GRP_DISP_SUCCESS 25
#define PPC970MP_PME_PM_LSU1_LDF 26
#define PPC970MP_PME_PM_LSU0_SRQ_STFWD 27
#define PPC970MP_PME_PM_CR_MAP_FULL_CYC 28
#define PPC970MP_PME_PM_MRK_LSU0_FLUSH_ULD 29
#define PPC970MP_PME_PM_LSU_DERAT_MISS 30
#define PPC970MP_PME_PM_FPU0_SINGLE 31
#define PPC970MP_PME_PM_FPU1_FDIV 32
#define PPC970MP_PME_PM_FPU1_FEST 33
#define PPC970MP_PME_PM_FPU0_FRSP_FCONV 34
#define PPC970MP_PME_PM_GCT_EMPTY_SRQ_FULL 35
#define PPC970MP_PME_PM_MRK_ST_CMPL_INT 36
#define PPC970MP_PME_PM_FLUSH_BR_MPRED 37
#define PPC970MP_PME_PM_FXU_FIN 38
#define PPC970MP_PME_PM_FPU_STF 39
#define PPC970MP_PME_PM_DSLB_MISS 40
#define PPC970MP_PME_PM_FXLS1_FULL_CYC 41
#define PPC970MP_PME_PM_CMPLU_STALL_FPU 42
#define PPC970MP_PME_PM_LSU_LMQ_LHR_MERGE 43
#define PPC970MP_PME_PM_MRK_STCX_FAIL 44
#define PPC970MP_PME_PM_FXU0_BUSY_FXU1_IDLE 45
#define PPC970MP_PME_PM_CMPLU_STALL_LSU 46
#define PPC970MP_PME_PM_MRK_DATA_FROM_L25_SHR 47
#define PPC970MP_PME_PM_LSU_FLUSH_ULD 48
#define PPC970MP_PME_PM_MRK_BRU_FIN 49
#define PPC970MP_PME_PM_IERAT_XLATE_WR 50
#define PPC970MP_PME_PM_GCT_EMPTY_BR_MPRED 51
#define PPC970MP_PME_PM_LSU0_BUSY 52
#define PPC970MP_PME_PM_DATA_FROM_MEM 53
#define PPC970MP_PME_PM_FPR_MAP_FULL_CYC 54
#define PPC970MP_PME_PM_FPU1_FULL_CYC 55
#define PPC970MP_PME_PM_FPU0_FIN 56
#define PPC970MP_PME_PM_GRP_BR_REDIR 57
#define PPC970MP_PME_PM_GCT_EMPTY_IC_MISS 58
#define PPC970MP_PME_PM_THRESH_TIMEO 59
#define PPC970MP_PME_PM_FPU_FSQRT 60
#define PPC970MP_PME_PM_MRK_LSU0_FLUSH_LRQ 61
#define PPC970MP_PME_PM_PMC1_OVERFLOW 62
#define PPC970MP_PME_PM_FXLS0_FULL_CYC 63
#define PPC970MP_PME_PM_FPU0_ALL 64
#define PPC970MP_PME_PM_DATA_TABLEWALK_CYC 65
#define PPC970MP_PME_PM_FPU0_FEST 66
#define PPC970MP_PME_PM_DATA_FROM_L25_MOD 67
#define PPC970MP_PME_PM_LSU0_REJECT_ERAT_MISS 68
#define PPC970MP_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC 69
#define PPC970MP_PME_PM_LSU0_REJECT_RELOAD_CDF 70
#define PPC970MP_PME_PM_FPU_FEST 71
#define PPC970MP_PME_PM_0INST_FETCH 72
#define PPC970MP_PME_PM_LD_MISS_L1_LSU0 73
#define PPC970MP_PME_PM_LSU1_REJECT_RELOAD_CDF 74
#define PPC970MP_PME_PM_L1_PREF 75
#define PPC970MP_PME_PM_FPU1_STALL3 76
#define PPC970MP_PME_PM_BRQ_FULL_CYC 77
#define PPC970MP_PME_PM_PMC8_OVERFLOW 78
#define PPC970MP_PME_PM_PMC7_OVERFLOW 79
#define PPC970MP_PME_PM_WORK_HELD 80
#define PPC970MP_PME_PM_MRK_LD_MISS_L1_LSU0 81
#define PPC970MP_PME_PM_FXU_IDLE 82
#define PPC970MP_PME_PM_INST_CMPL 83
#define PPC970MP_PME_PM_LSU1_FLUSH_UST 84
#define PPC970MP_PME_PM_LSU0_FLUSH_ULD 85
#define PPC970MP_PME_PM_LSU_FLUSH 86
#define PPC970MP_PME_PM_INST_FROM_L2 87
#define PPC970MP_PME_PM_LSU1_REJECT_LMQ_FULL 88
#define PPC970MP_PME_PM_PMC2_OVERFLOW 89
#define PPC970MP_PME_PM_FPU0_DENORM 90
#define PPC970MP_PME_PM_FPU1_FMOV_FEST 91
#define PPC970MP_PME_PM_INST_FETCH_CYC 92
#define PPC970MP_PME_PM_GRP_DISP_REJECT 93
#define PPC970MP_PME_PM_LSU_LDF 94
#define PPC970MP_PME_PM_INST_DISP 95
#define PPC970MP_PME_PM_DATA_FROM_L25_SHR 96
#define PPC970MP_PME_PM_L1_DCACHE_RELOAD_VALID 97
#define PPC970MP_PME_PM_MRK_GRP_ISSUED 98
#define PPC970MP_PME_PM_FPU_FMA 99
#define PPC970MP_PME_PM_MRK_CRU_FIN 100
#define PPC970MP_PME_PM_CMPLU_STALL_REJECT 101
#define PPC970MP_PME_PM_MRK_LSU1_FLUSH_UST 102
#define PPC970MP_PME_PM_MRK_FXU_FIN 103
#define PPC970MP_PME_PM_LSU1_REJECT_ERAT_MISS 104
#define PPC970MP_PME_PM_BR_ISSUED 105
#define PPC970MP_PME_PM_PMC4_OVERFLOW 106
#define PPC970MP_PME_PM_EE_OFF 107
#define PPC970MP_PME_PM_INST_FROM_L25_MOD 108
#define PPC970MP_PME_PM_CMPLU_STALL_ERAT_MISS 109
#define PPC970MP_PME_PM_ITLB_MISS 110
#define PPC970MP_PME_PM_FXU1_BUSY_FXU0_IDLE 111
#define PPC970MP_PME_PM_GRP_DISP_VALID 112
#define PPC970MP_PME_PM_MRK_GRP_DISP 113
#define PPC970MP_PME_PM_LSU_FLUSH_UST 114
#define PPC970MP_PME_PM_FXU1_FIN 115
#define PPC970MP_PME_PM_GRP_CMPL 116
#define PPC970MP_PME_PM_FPU_FRSP_FCONV 117
#define PPC970MP_PME_PM_MRK_LSU0_FLUSH_SRQ 118
#define PPC970MP_PME_PM_CMPLU_STALL_OTHER 119
#define PPC970MP_PME_PM_LSU_LMQ_FULL_CYC 120
#define PPC970MP_PME_PM_ST_REF_L1_LSU0 121
#define PPC970MP_PME_PM_LSU0_DERAT_MISS 122
#define PPC970MP_PME_PM_LSU_SRQ_SYNC_CYC 123
#define PPC970MP_PME_PM_FPU_STALL3 124
#define PPC970MP_PME_PM_LSU_REJECT_ERAT_MISS 125
#define PPC970MP_PME_PM_MRK_DATA_FROM_L2 126
#define PPC970MP_PME_PM_LSU0_FLUSH_SRQ 127
#define PPC970MP_PME_PM_FPU0_FMOV_FEST 128
#define PPC970MP_PME_PM_IOPS_CMPL 129
#define PPC970MP_PME_PM_LD_REF_L1_LSU0 130
#define PPC970MP_PME_PM_LSU1_FLUSH_SRQ 131
#define PPC970MP_PME_PM_CMPLU_STALL_DIV 132
#define PPC970MP_PME_PM_GRP_BR_MPRED 133
#define PPC970MP_PME_PM_LSU_LMQ_S0_ALLOC 134
#define PPC970MP_PME_PM_LSU0_REJECT_LMQ_FULL 135
#define PPC970MP_PME_PM_ST_REF_L1 136
#define PPC970MP_PME_PM_MRK_VMX_FIN 137
#define PPC970MP_PME_PM_LSU_SRQ_EMPTY_CYC 138
#define PPC970MP_PME_PM_FPU1_STF 139
#define PPC970MP_PME_PM_RUN_CYC 140
#define PPC970MP_PME_PM_LSU_LMQ_S0_VALID 141
#define PPC970MP_PME_PM_LSU0_LDF 142
#define PPC970MP_PME_PM_LSU_LRQ_S0_VALID 143
#define PPC970MP_PME_PM_PMC3_OVERFLOW 144
#define PPC970MP_PME_PM_MRK_IMR_RELOAD 145
#define PPC970MP_PME_PM_MRK_GRP_TIMEO 146
#define PPC970MP_PME_PM_FPU_FMOV_FEST 147
#define PPC970MP_PME_PM_GRP_DISP_BLK_SB_CYC 148
#define PPC970MP_PME_PM_XER_MAP_FULL_CYC 149
#define PPC970MP_PME_PM_ST_MISS_L1 150
#define PPC970MP_PME_PM_STOP_COMPLETION 151
#define PPC970MP_PME_PM_MRK_GRP_CMPL 152
#define PPC970MP_PME_PM_ISLB_MISS 153
#define PPC970MP_PME_PM_SUSPENDED 154
#define PPC970MP_PME_PM_CYC 155
#define PPC970MP_PME_PM_LD_MISS_L1_LSU1 156
#define PPC970MP_PME_PM_STCX_FAIL 157
#define PPC970MP_PME_PM_LSU1_SRQ_STFWD 158
#define PPC970MP_PME_PM_GRP_DISP 159
#define PPC970MP_PME_PM_L2_PREF 160
#define PPC970MP_PME_PM_FPU1_DENORM 161
#define PPC970MP_PME_PM_DATA_FROM_L2 162
#define PPC970MP_PME_PM_FPU0_FPSCR 163
#define PPC970MP_PME_PM_MRK_DATA_FROM_L25_MOD 164
#define PPC970MP_PME_PM_FPU0_FSQRT 165
#define PPC970MP_PME_PM_LD_REF_L1 166
#define PPC970MP_PME_PM_MRK_L1_RELOAD_VALID 167
#define PPC970MP_PME_PM_1PLUS_PPC_CMPL 168
#define PPC970MP_PME_PM_INST_FROM_L1 169
#define PPC970MP_PME_PM_EE_OFF_EXT_INT 170
#define PPC970MP_PME_PM_PMC6_OVERFLOW 171
#define PPC970MP_PME_PM_LSU_LRQ_FULL_CYC 172
#define PPC970MP_PME_PM_IC_PREF_INSTALL 173
#define PPC970MP_PME_PM_DC_PREF_OUT_OF_STREAMS 174
#define PPC970MP_PME_PM_MRK_LSU1_FLUSH_SRQ 175
#define PPC970MP_PME_PM_GCT_FULL_CYC 176
#define PPC970MP_PME_PM_INST_FROM_MEM 177
#define PPC970MP_PME_PM_FLUSH_LSU_BR_MPRED 178
#define PPC970MP_PME_PM_FXU_BUSY 179
#define PPC970MP_PME_PM_ST_REF_L1_LSU1 180
#define PPC970MP_PME_PM_MRK_LD_MISS_L1 181
#define PPC970MP_PME_PM_L1_WRITE_CYC 182
#define PPC970MP_PME_PM_LSU1_BUSY 183
#define PPC970MP_PME_PM_LSU_REJECT_LMQ_FULL 184
#define PPC970MP_PME_PM_CMPLU_STALL_FDIV 185
#define PPC970MP_PME_PM_FPU_ALL 186
#define PPC970MP_PME_PM_LSU_SRQ_S0_ALLOC 187
#define PPC970MP_PME_PM_INST_FROM_L25_SHR 188
#define PPC970MP_PME_PM_GRP_MRK 189
#define PPC970MP_PME_PM_BR_MPRED_CR 190
#define PPC970MP_PME_PM_DC_PREF_STREAM_ALLOC 191
#define PPC970MP_PME_PM_FPU1_FIN 192
#define PPC970MP_PME_PM_LSU_REJECT_SRQ 193
#define PPC970MP_PME_PM_BR_MPRED_TA 194
#define PPC970MP_PME_PM_CRQ_FULL_CYC 195
#define PPC970MP_PME_PM_LD_MISS_L1 196
#define PPC970MP_PME_PM_INST_FROM_PREF 197
#define PPC970MP_PME_PM_STCX_PASS 198
#define PPC970MP_PME_PM_DC_INV_L2 199
#define PPC970MP_PME_PM_LSU_SRQ_FULL_CYC 200
#define PPC970MP_PME_PM_LSU0_FLUSH_LRQ 201
#define PPC970MP_PME_PM_LSU_SRQ_S0_VALID 202
#define PPC970MP_PME_PM_LARX_LSU0 203
#define PPC970MP_PME_PM_GCT_EMPTY_CYC 204
#define PPC970MP_PME_PM_FPU1_ALL 205
#define PPC970MP_PME_PM_FPU1_FSQRT 206
#define PPC970MP_PME_PM_FPU_FIN 207
#define PPC970MP_PME_PM_LSU_SRQ_STFWD 208
#define PPC970MP_PME_PM_MRK_LD_MISS_L1_LSU1 209
#define PPC970MP_PME_PM_FXU0_FIN 210
#define PPC970MP_PME_PM_MRK_FPU_FIN 211
#define PPC970MP_PME_PM_PMC5_OVERFLOW 212
#define PPC970MP_PME_PM_SNOOP_TLBIE 213
#define PPC970MP_PME_PM_FPU1_FRSP_FCONV 214
#define PPC970MP_PME_PM_FPU0_FDIV 215
#define PPC970MP_PME_PM_LD_REF_L1_LSU1 216
#define PPC970MP_PME_PM_HV_CYC 217
#define PPC970MP_PME_PM_LR_CTR_MAP_FULL_CYC 218
#define PPC970MP_PME_PM_FPU_DENORM 219
#define PPC970MP_PME_PM_LSU0_REJECT_SRQ 220
#define PPC970MP_PME_PM_LSU1_REJECT_SRQ 221
#define PPC970MP_PME_PM_LSU1_DERAT_MISS 222
#define PPC970MP_PME_PM_IC_PREF_REQ 223
#define PPC970MP_PME_PM_MRK_LSU_FIN 224
#define PPC970MP_PME_PM_MRK_DATA_FROM_MEM 225
#define PPC970MP_PME_PM_CMPLU_STALL_DCACHE_MISS 226
#define PPC970MP_PME_PM_LSU0_FLUSH_UST 227
#define PPC970MP_PME_PM_LSU_FLUSH_LRQ 228
#define PPC970MP_PME_PM_LSU_FLUSH_SRQ 229

static const pme_power_entry_t ppc970mp_pe[] = {
	[ PPC970MP_PME_PM_LSU_REJECT_RELOAD_CDF ] = {
		.pme_name = "PM_LSU_REJECT_RELOAD_CDF",
		.pme_code = 0x6920,
		.pme_short_desc = "LSU reject due to reload CDF or tag update collision",
		.pme_long_desc = "LSU reject due to reload CDF or tag update collision",
	},
	[ PPC970MP_PME_PM_MRK_LSU_SRQ_INST_VALID ] = {
		.pme_name = "PM_MRK_LSU_SRQ_INST_VALID",
		.pme_code = 0x936,
		.pme_short_desc = "Marked instruction valid in SRQ",
		.pme_long_desc = "This signal is asserted every cycle when a marked request is resident in the Store Request Queue",
	},
	[ PPC970MP_PME_PM_FPU1_SINGLE ] = {
		.pme_name = "PM_FPU1_SINGLE",
		.pme_code = 0x127,
		.pme_short_desc = "FPU1 executed single precision instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing single precision instruction.",
	},
	[ PPC970MP_PME_PM_FPU0_STALL3 ] = {
		.pme_name = "PM_FPU0_STALL3",
		.pme_code = 0x121,
		.pme_short_desc = "FPU0 stalled in pipe3",
		.pme_long_desc = "This signal indicates that fp0 has generated a stall in pipe3 due to overflow, underflow, massive cancel, convert to integer (sometimes), or convert from integer (always). This signal is active during the entire duration of the stall. ",
	},
	[ PPC970MP_PME_PM_TB_BIT_TRANS ] = {
		.pme_name = "PM_TB_BIT_TRANS",
		.pme_code = 0x8005,
		.pme_short_desc = "Time Base bit transition",
		.pme_long_desc = "When the selected time base bit (as specified in MMCR0[TBSEL])transitions from 0 to 1 ",
	},
	[ PPC970MP_PME_PM_GPR_MAP_FULL_CYC ] = {
		.pme_name = "PM_GPR_MAP_FULL_CYC",
		.pme_code = 0x335,
		.pme_short_desc = "Cycles GPR mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the gpr mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ PPC970MP_PME_PM_MRK_ST_CMPL ] = {
		.pme_name = "PM_MRK_ST_CMPL",
		.pme_code = 0x1003,
		.pme_short_desc = "Marked store instruction completed",
		.pme_long_desc = "A sampled store has completed (data home)",
	},
	[ PPC970MP_PME_PM_FPU0_STF ] = {
		.pme_name = "PM_FPU0_STF",
		.pme_code = 0x122,
		.pme_short_desc = "FPU0 executed store instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing a store instruction.",
	},
	[ PPC970MP_PME_PM_FPU1_FMA ] = {
		.pme_name = "PM_FPU1_FMA",
		.pme_code = 0x105,
		.pme_short_desc = "FPU1 executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ PPC970MP_PME_PM_LSU1_FLUSH_ULD ] = {
		.pme_name = "PM_LSU1_FLUSH_ULD",
		.pme_code = 0x804,
		.pme_short_desc = "LSU1 unaligned load flushes",
		.pme_long_desc = "A load was flushed from unit 1 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ PPC970MP_PME_PM_MRK_INST_FIN ] = {
		.pme_name = "PM_MRK_INST_FIN",
		.pme_code = 0x7005,
		.pme_short_desc = "Marked instruction finished",
		.pme_long_desc = "One of the execution units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ PPC970MP_PME_PM_MRK_LSU0_FLUSH_UST ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_UST",
		.pme_code = 0x711,
		.pme_short_desc = "LSU0 marked unaligned store flushes",
		.pme_long_desc = "A marked store was flushed from unit 0 because it was unaligned",
	},
	[ PPC970MP_PME_PM_LSU_LRQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_LRQ_S0_ALLOC",
		.pme_code = 0x826,
		.pme_short_desc = "LRQ slot 0 allocated",
		.pme_long_desc = "LRQ slot zero was allocated",
	},
	[ PPC970MP_PME_PM_FPU_FDIV ] = {
		.pme_name = "PM_FPU_FDIV",
		.pme_code = 0x1100,
		.pme_short_desc = "FPU executed FDIV instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when FPU is executing a divide instruction. This could be fdiv, fdivs, fdiv. fdivs. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_FPU0_FULL_CYC ] = {
		.pme_name = "PM_FPU0_FULL_CYC",
		.pme_code = 0x303,
		.pme_short_desc = "Cycles FPU0 issue queue full",
		.pme_long_desc = "The issue queue for FPU unit 0 cannot accept any more instructions. Issue is stopped",
	},
	[ PPC970MP_PME_PM_FPU_SINGLE ] = {
		.pme_name = "PM_FPU_SINGLE",
		.pme_code = 0x5120,
		.pme_short_desc = "FPU executed single precision instruction",
		.pme_long_desc = "FPU is executing single precision instruction. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_FPU0_FMA ] = {
		.pme_name = "PM_FPU0_FMA",
		.pme_code = 0x101,
		.pme_short_desc = "FPU0 executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ PPC970MP_PME_PM_MRK_LSU1_FLUSH_ULD ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_ULD",
		.pme_code = 0x714,
		.pme_short_desc = "LSU1 marked unaligned load flushes",
		.pme_long_desc = "A marked load was flushed from unit 1 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ PPC970MP_PME_PM_LSU1_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU1_FLUSH_LRQ",
		.pme_code = 0x806,
		.pme_short_desc = "LSU1 LRQ flushes",
		.pme_long_desc = "A load was flushed by unit 1 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ PPC970MP_PME_PM_DTLB_MISS ] = {
		.pme_name = "PM_DTLB_MISS",
		.pme_code = 0x704,
		.pme_short_desc = "Data TLB misses",
		.pme_long_desc = "A TLB miss for a data request occurred. Requests that miss the TLB may be retried until the instruction is in the next to complete group (unless HID4 is set to allow speculative tablewalks). This may result in multiple TLB misses for the same instruction.",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_FXU ] = {
		.pme_name = "PM_CMPLU_STALL_FXU",
		.pme_code = 0x508b,
		.pme_short_desc = "Completion stall caused by FXU instruction",
		.pme_long_desc = "Completion stall caused by FXU instruction",
	},
	[ PPC970MP_PME_PM_MRK_ST_MISS_L1 ] = {
		.pme_name = "PM_MRK_ST_MISS_L1",
		.pme_code = 0x723,
		.pme_short_desc = "Marked L1 D cache store misses",
		.pme_long_desc = "A marked store missed the dcache",
	},
	[ PPC970MP_PME_PM_EXT_INT ] = {
		.pme_name = "PM_EXT_INT",
		.pme_code = 0x8002,
		.pme_short_desc = "External interrupts",
		.pme_long_desc = "An external interrupt occurred",
	},
	[ PPC970MP_PME_PM_MRK_LSU1_FLUSH_LRQ ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_LRQ",
		.pme_code = 0x716,
		.pme_short_desc = "LSU1 marked LRQ flushes",
		.pme_long_desc = "A marked load was flushed by unit 1 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ PPC970MP_PME_PM_MRK_ST_GPS ] = {
		.pme_name = "PM_MRK_ST_GPS",
		.pme_code = 0x6003,
		.pme_short_desc = "Marked store sent to GPS",
		.pme_long_desc = "A sampled store has been sent to the memory subsystem",
	},
	[ PPC970MP_PME_PM_GRP_DISP_SUCCESS ] = {
		.pme_name = "PM_GRP_DISP_SUCCESS",
		.pme_code = 0x5001,
		.pme_short_desc = "Group dispatch success",
		.pme_long_desc = "Number of groups successfully dispatched (not rejected)",
	},
	[ PPC970MP_PME_PM_LSU1_LDF ] = {
		.pme_name = "PM_LSU1_LDF",
		.pme_code = 0x734,
		.pme_short_desc = "LSU1 executed Floating Point load instruction",
		.pme_long_desc = "A floating point load was executed from LSU unit 1",
	},
	[ PPC970MP_PME_PM_LSU0_SRQ_STFWD ] = {
		.pme_name = "PM_LSU0_SRQ_STFWD",
		.pme_code = 0x820,
		.pme_short_desc = "LSU0 SRQ store forwarded",
		.pme_long_desc = "Data from a store instruction was forwarded to a load on unit 0",
	},
	[ PPC970MP_PME_PM_CR_MAP_FULL_CYC ] = {
		.pme_name = "PM_CR_MAP_FULL_CYC",
		.pme_code = 0x304,
		.pme_short_desc = "Cycles CR logical operation mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the cr mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ PPC970MP_PME_PM_MRK_LSU0_FLUSH_ULD ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_ULD",
		.pme_code = 0x710,
		.pme_short_desc = "LSU0 marked unaligned load flushes",
		.pme_long_desc = "A marked load was flushed from unit 0 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ PPC970MP_PME_PM_LSU_DERAT_MISS ] = {
		.pme_name = "PM_LSU_DERAT_MISS",
		.pme_code = 0x6700,
		.pme_short_desc = "DERAT misses",
		.pme_long_desc = "Total D-ERAT Misses (Unit 0 + Unit 1). Requests that miss the Derat are rejected and retried until the request hits in the Erat. This may result in multiple erat misses for the same instruction.",
	},
	[ PPC970MP_PME_PM_FPU0_SINGLE ] = {
		.pme_name = "PM_FPU0_SINGLE",
		.pme_code = 0x123,
		.pme_short_desc = "FPU0 executed single precision instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing single precision instruction.",
	},
	[ PPC970MP_PME_PM_FPU1_FDIV ] = {
		.pme_name = "PM_FPU1_FDIV",
		.pme_code = 0x104,
		.pme_short_desc = "FPU1 executed FDIV instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp1 is executing a divide instruction. This could be fdiv, fdivs, fdiv. fdivs.",
	},
	[ PPC970MP_PME_PM_FPU1_FEST ] = {
		.pme_name = "PM_FPU1_FEST",
		.pme_code = 0x116,
		.pme_short_desc = "FPU1 executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. ",
	},
	[ PPC970MP_PME_PM_FPU0_FRSP_FCONV ] = {
		.pme_name = "PM_FPU0_FRSP_FCONV",
		.pme_code = 0x111,
		.pme_short_desc = "FPU0 executed FRSP or FCONV instructions",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing frsp or convert kind of instruction. This could be frsp*, fcfid*, fcti* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ PPC970MP_PME_PM_GCT_EMPTY_SRQ_FULL ] = {
		.pme_name = "PM_GCT_EMPTY_SRQ_FULL",
		.pme_code = 0x200b,
		.pme_short_desc = "GCT empty caused by SRQ full",
		.pme_long_desc = "GCT empty caused by SRQ full",
	},
	[ PPC970MP_PME_PM_MRK_ST_CMPL_INT ] = {
		.pme_name = "PM_MRK_ST_CMPL_INT",
		.pme_code = 0x3003,
		.pme_short_desc = "Marked store completed with intervention",
		.pme_long_desc = "A marked store previously sent to the memory subsystem completed (data home) after requiring intervention",
	},
	[ PPC970MP_PME_PM_FLUSH_BR_MPRED ] = {
		.pme_name = "PM_FLUSH_BR_MPRED",
		.pme_code = 0x316,
		.pme_short_desc = "Flush caused by branch mispredict",
		.pme_long_desc = "Flush caused by branch mispredict",
	},
	[ PPC970MP_PME_PM_FXU_FIN ] = {
		.pme_name = "PM_FXU_FIN",
		.pme_code = 0x3330,
		.pme_short_desc = "FXU produced a result",
		.pme_long_desc = "The fixed point unit (Unit 0 + Unit 1) finished an instruction. Instructions that finish may not necessary complete.",
	},
	[ PPC970MP_PME_PM_FPU_STF ] = {
		.pme_name = "PM_FPU_STF",
		.pme_code = 0x6120,
		.pme_short_desc = "FPU executed store instruction",
		.pme_long_desc = "FPU is executing a store instruction. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_DSLB_MISS ] = {
		.pme_name = "PM_DSLB_MISS",
		.pme_code = 0x705,
		.pme_short_desc = "Data SLB misses",
		.pme_long_desc = "A SLB miss for a data request occurred. SLB misses trap to the operating system to resolve",
	},
	[ PPC970MP_PME_PM_FXLS1_FULL_CYC ] = {
		.pme_name = "PM_FXLS1_FULL_CYC",
		.pme_code = 0x314,
		.pme_short_desc = "Cycles FXU1/LS1 queue full",
		.pme_long_desc = "The issue queue for FXU/LSU unit 0 cannot accept any more instructions. Issue is stopped",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_FPU ] = {
		.pme_name = "PM_CMPLU_STALL_FPU",
		.pme_code = 0x704b,
		.pme_short_desc = "Completion stall caused by FPU instruction",
		.pme_long_desc = "Completion stall caused by FPU instruction",
	},
	[ PPC970MP_PME_PM_LSU_LMQ_LHR_MERGE ] = {
		.pme_name = "PM_LSU_LMQ_LHR_MERGE",
		.pme_code = 0x935,
		.pme_short_desc = "LMQ LHR merges",
		.pme_long_desc = "A dcache miss occurred for the same real cache line address as an earlier request already in the Load Miss Queue and was merged into the LMQ entry.",
	},
	[ PPC970MP_PME_PM_MRK_STCX_FAIL ] = {
		.pme_name = "PM_MRK_STCX_FAIL",
		.pme_code = 0x726,
		.pme_short_desc = "Marked STCX failed",
		.pme_long_desc = "A marked stcx (stwcx or stdcx) failed",
	},
	[ PPC970MP_PME_PM_FXU0_BUSY_FXU1_IDLE ] = {
		.pme_name = "PM_FXU0_BUSY_FXU1_IDLE",
		.pme_code = 0x7002,
		.pme_short_desc = "FXU0 busy FXU1 idle",
		.pme_long_desc = "FXU0 is busy while FXU1 was idle",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_LSU ] = {
		.pme_name = "PM_CMPLU_STALL_LSU",
		.pme_code = 0x504b,
		.pme_short_desc = "Completion stall caused by LSU instruction",
		.pme_long_desc = "Completion stall caused by LSU instruction",
	},
	[ PPC970MP_PME_PM_MRK_DATA_FROM_L25_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L25_SHR",
		.pme_code = 0x5937,
		.pme_short_desc = "Marked data loaded from L2.5 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T or SL) data from the L2 of a chip on this MCM due to a marked demand load",
	},
	[ PPC970MP_PME_PM_LSU_FLUSH_ULD ] = {
		.pme_name = "PM_LSU_FLUSH_ULD",
		.pme_code = 0x1800,
		.pme_short_desc = "LRQ unaligned load flushes",
		.pme_long_desc = "A load was flushed because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ PPC970MP_PME_PM_MRK_BRU_FIN ] = {
		.pme_name = "PM_MRK_BRU_FIN",
		.pme_code = 0x2005,
		.pme_short_desc = "Marked instruction BRU processing finished",
		.pme_long_desc = "The branch unit finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ PPC970MP_PME_PM_IERAT_XLATE_WR ] = {
		.pme_name = "PM_IERAT_XLATE_WR",
		.pme_code = 0x430,
		.pme_short_desc = "Translation written to ierat",
		.pme_long_desc = "This signal will be asserted each time the I-ERAT is written. This indicates that an ERAT miss has been serviced. ERAT misses will initiate a sequence resulting in the ERAT being written. ERAT misses that are later ignored will not be counted unless the ERAT is written before the instruction stream is changed, This should be a fairly accurate count of ERAT missed (best available).",
	},
	[ PPC970MP_PME_PM_GCT_EMPTY_BR_MPRED ] = {
		.pme_name = "PM_GCT_EMPTY_BR_MPRED",
		.pme_code = 0x708c,
		.pme_short_desc = "GCT empty due to branch mispredict",
		.pme_long_desc = "GCT empty due to branch mispredict",
	},
	[ PPC970MP_PME_PM_LSU0_BUSY ] = {
		.pme_name = "PM_LSU0_BUSY",
		.pme_code = 0x823,
		.pme_short_desc = "LSU0 busy",
		.pme_long_desc = "LSU unit 0 is busy rejecting instructions",
	},
	[ PPC970MP_PME_PM_DATA_FROM_MEM ] = {
		.pme_name = "PM_DATA_FROM_MEM",
		.pme_code = 0x2837,
		.pme_short_desc = "Data loaded from memory",
		.pme_long_desc = "Data loaded from memory",
	},
	[ PPC970MP_PME_PM_FPR_MAP_FULL_CYC ] = {
		.pme_name = "PM_FPR_MAP_FULL_CYC",
		.pme_code = 0x301,
		.pme_short_desc = "Cycles FPR mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the FPR mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ PPC970MP_PME_PM_FPU1_FULL_CYC ] = {
		.pme_name = "PM_FPU1_FULL_CYC",
		.pme_code = 0x307,
		.pme_short_desc = "Cycles FPU1 issue queue full",
		.pme_long_desc = "The issue queue for FPU unit 1 cannot accept any more instructions. Issue is stopped",
	},
	[ PPC970MP_PME_PM_FPU0_FIN ] = {
		.pme_name = "PM_FPU0_FIN",
		.pme_code = 0x113,
		.pme_short_desc = "FPU0 produced a result",
		.pme_long_desc = "fp0 finished, produced a result This only indicates finish, not completion. ",
	},
	[ PPC970MP_PME_PM_GRP_BR_REDIR ] = {
		.pme_name = "PM_GRP_BR_REDIR",
		.pme_code = 0x326,
		.pme_short_desc = "Group experienced branch redirect",
		.pme_long_desc = "Group experienced branch redirect",
	},
	[ PPC970MP_PME_PM_GCT_EMPTY_IC_MISS ] = {
		.pme_name = "PM_GCT_EMPTY_IC_MISS",
		.pme_code = 0x508c,
		.pme_short_desc = "GCT empty due to I cache miss",
		.pme_long_desc = "GCT empty due to I cache miss",
	},
	[ PPC970MP_PME_PM_THRESH_TIMEO ] = {
		.pme_name = "PM_THRESH_TIMEO",
		.pme_code = 0x2003,
		.pme_short_desc = "Threshold timeout",
		.pme_long_desc = "The threshold timer expired",
	},
	[ PPC970MP_PME_PM_FPU_FSQRT ] = {
		.pme_name = "PM_FPU_FSQRT",
		.pme_code = 0x6100,
		.pme_short_desc = "FPU executed FSQRT instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when FPU is executing a square root instruction. This could be fsqrt* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_MRK_LSU0_FLUSH_LRQ ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_LRQ",
		.pme_code = 0x712,
		.pme_short_desc = "LSU0 marked LRQ flushes",
		.pme_long_desc = "A marked load was flushed by unit 0 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ PPC970MP_PME_PM_PMC1_OVERFLOW ] = {
		.pme_name = "PM_PMC1_OVERFLOW",
		.pme_code = 0x200a,
		.pme_short_desc = "PMC1 Overflow",
		.pme_long_desc = "PMC1 Overflow",
	},
	[ PPC970MP_PME_PM_FXLS0_FULL_CYC ] = {
		.pme_name = "PM_FXLS0_FULL_CYC",
		.pme_code = 0x310,
		.pme_short_desc = "Cycles FXU0/LS0 queue full",
		.pme_long_desc = "The issue queue for FXU/LSU unit 0 cannot accept any more instructions. Issue is stopped",
	},
	[ PPC970MP_PME_PM_FPU0_ALL ] = {
		.pme_name = "PM_FPU0_ALL",
		.pme_code = 0x103,
		.pme_short_desc = "FPU0 executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo",
	},
	[ PPC970MP_PME_PM_DATA_TABLEWALK_CYC ] = {
		.pme_name = "PM_DATA_TABLEWALK_CYC",
		.pme_code = 0x707,
		.pme_short_desc = "Cycles doing data tablewalks",
		.pme_long_desc = "This signal is asserted every cycle when a tablewalk is active. While a tablewalk is active any request attempting to access the TLB will be rejected and retried.",
	},
	[ PPC970MP_PME_PM_FPU0_FEST ] = {
		.pme_name = "PM_FPU0_FEST",
		.pme_code = 0x112,
		.pme_short_desc = "FPU0 executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. ",
	},
	[ PPC970MP_PME_PM_DATA_FROM_L25_MOD ] = {
		.pme_name = "PM_DATA_FROM_L25_MOD",
		.pme_code = 0x6837,
		.pme_short_desc = "Data loaded from L2.5 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of a chip on this MCM due to a demand load",
	},
	[ PPC970MP_PME_PM_LSU0_REJECT_ERAT_MISS ] = {
		.pme_name = "PM_LSU0_REJECT_ERAT_MISS",
		.pme_code = 0x923,
		.pme_short_desc = "LSU0 reject due to ERAT miss",
		.pme_long_desc = "LSU0 reject due to ERAT miss",
	},
	[ PPC970MP_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_CYC",
		.pme_code = 0x2002,
		.pme_short_desc = "Cycles LMQ and SRQ empty",
		.pme_long_desc = "Cycles when both the LMQ and SRQ are empty (LSU is idle)",
	},
	[ PPC970MP_PME_PM_LSU0_REJECT_RELOAD_CDF ] = {
		.pme_name = "PM_LSU0_REJECT_RELOAD_CDF",
		.pme_code = 0x922,
		.pme_short_desc = "LSU0 reject due to reload CDF or tag update collision",
		.pme_long_desc = "LSU0 reject due to reload CDF or tag update collision",
	},
	[ PPC970MP_PME_PM_FPU_FEST ] = {
		.pme_name = "PM_FPU_FEST",
		.pme_code = 0x3110,
		.pme_short_desc = "FPU executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. Combined Unit 0 + Unit 1.",
	},
	[ PPC970MP_PME_PM_0INST_FETCH ] = {
		.pme_name = "PM_0INST_FETCH",
		.pme_code = 0x442d,
		.pme_short_desc = "No instructions fetched",
		.pme_long_desc = "No instructions were fetched this cycles (due to IFU hold, redirect, or icache miss)",
	},
	[ PPC970MP_PME_PM_LD_MISS_L1_LSU0 ] = {
		.pme_name = "PM_LD_MISS_L1_LSU0",
		.pme_code = 0x812,
		.pme_short_desc = "LSU0 L1 D cache load misses",
		.pme_long_desc = "A load, executing on unit 0, missed the dcache",
	},
	[ PPC970MP_PME_PM_LSU1_REJECT_RELOAD_CDF ] = {
		.pme_name = "PM_LSU1_REJECT_RELOAD_CDF",
		.pme_code = 0x926,
		.pme_short_desc = "LSU1 reject due to reload CDF or tag update collision",
		.pme_long_desc = "LSU1 reject due to reload CDF or tag update collision",
	},
	[ PPC970MP_PME_PM_L1_PREF ] = {
		.pme_name = "PM_L1_PREF",
		.pme_code = 0x731,
		.pme_short_desc = "L1 cache data prefetches",
		.pme_long_desc = "A request to prefetch data into the L1 was made",
	},
	[ PPC970MP_PME_PM_FPU1_STALL3 ] = {
		.pme_name = "PM_FPU1_STALL3",
		.pme_code = 0x125,
		.pme_short_desc = "FPU1 stalled in pipe3",
		.pme_long_desc = "This signal indicates that fp1 has generated a stall in pipe3 due to overflow, underflow, massive cancel, convert to integer (sometimes), or convert from integer (always). This signal is active during the entire duration of the stall. ",
	},
	[ PPC970MP_PME_PM_BRQ_FULL_CYC ] = {
		.pme_name = "PM_BRQ_FULL_CYC",
		.pme_code = 0x305,
		.pme_short_desc = "Cycles branch queue full",
		.pme_long_desc = "The ISU sends a signal indicating that the issue queue that feeds the ifu br unit cannot accept any more group (queue is full of groups).",
	},
	[ PPC970MP_PME_PM_PMC8_OVERFLOW ] = {
		.pme_name = "PM_PMC8_OVERFLOW",
		.pme_code = 0x100a,
		.pme_short_desc = "PMC8 Overflow",
		.pme_long_desc = "PMC8 Overflow",
	},
	[ PPC970MP_PME_PM_PMC7_OVERFLOW ] = {
		.pme_name = "PM_PMC7_OVERFLOW",
		.pme_code = 0x800a,
		.pme_short_desc = "PMC7 Overflow",
		.pme_long_desc = "PMC7 Overflow",
	},
	[ PPC970MP_PME_PM_WORK_HELD ] = {
		.pme_name = "PM_WORK_HELD",
		.pme_code = 0x2001,
		.pme_short_desc = "Work held",
		.pme_long_desc = "RAS Unit has signaled completion to stop and there are groups waiting to complete",
	},
	[ PPC970MP_PME_PM_MRK_LD_MISS_L1_LSU0 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1_LSU0",
		.pme_code = 0x720,
		.pme_short_desc = "LSU0 L1 D cache load misses",
		.pme_long_desc = "A marked load, executing on unit 0, missed the dcache",
	},
	[ PPC970MP_PME_PM_FXU_IDLE ] = {
		.pme_name = "PM_FXU_IDLE",
		.pme_code = 0x5002,
		.pme_short_desc = "FXU idle",
		.pme_long_desc = "FXU0 and FXU1 are both idle",
	},
	[ PPC970MP_PME_PM_INST_CMPL ] = {
		.pme_name = "PM_INST_CMPL",
		.pme_code = 0x1,
		.pme_short_desc = "Instructions completed",
		.pme_long_desc = "Number of Eligible Instructions that completed. ",
	},
	[ PPC970MP_PME_PM_LSU1_FLUSH_UST ] = {
		.pme_name = "PM_LSU1_FLUSH_UST",
		.pme_code = 0x805,
		.pme_short_desc = "LSU1 unaligned store flushes",
		.pme_long_desc = "A store was flushed from unit 1 because it was unaligned (crossed a 4k boundary)",
	},
	[ PPC970MP_PME_PM_LSU0_FLUSH_ULD ] = {
		.pme_name = "PM_LSU0_FLUSH_ULD",
		.pme_code = 0x800,
		.pme_short_desc = "LSU0 unaligned load flushes",
		.pme_long_desc = "A load was flushed from unit 0 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ PPC970MP_PME_PM_LSU_FLUSH ] = {
		.pme_name = "PM_LSU_FLUSH",
		.pme_code = 0x315,
		.pme_short_desc = "Flush initiated by LSU",
		.pme_long_desc = "Flush initiated by LSU",
	},
	[ PPC970MP_PME_PM_INST_FROM_L2 ] = {
		.pme_name = "PM_INST_FROM_L2",
		.pme_code = 0x1426,
		.pme_short_desc = "Instructions fetched from L2",
		.pme_long_desc = "An instruction fetch group was fetched from L2. Fetch Groups can contain up to 8 instructions",
	},
	[ PPC970MP_PME_PM_LSU1_REJECT_LMQ_FULL ] = {
		.pme_name = "PM_LSU1_REJECT_LMQ_FULL",
		.pme_code = 0x925,
		.pme_short_desc = "LSU1 reject due to LMQ full or missed data coming",
		.pme_long_desc = "LSU1 reject due to LMQ full or missed data coming",
	},
	[ PPC970MP_PME_PM_PMC2_OVERFLOW ] = {
		.pme_name = "PM_PMC2_OVERFLOW",
		.pme_code = 0x300a,
		.pme_short_desc = "PMC2 Overflow",
		.pme_long_desc = "PMC2 Overflow",
	},
	[ PPC970MP_PME_PM_FPU0_DENORM ] = {
		.pme_name = "PM_FPU0_DENORM",
		.pme_code = 0x120,
		.pme_short_desc = "FPU0 received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized.",
	},
	[ PPC970MP_PME_PM_FPU1_FMOV_FEST ] = {
		.pme_name = "PM_FPU1_FMOV_FEST",
		.pme_code = 0x114,
		.pme_short_desc = "FPU1 executing FMOV or FEST instructions",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing a move kind of instruction or one of the estimate instructions.. This could be fmr*, fneg*, fabs*, fnabs* , fres* or frsqrte* where XYZ* means XYZ or XYZ",
	},
	[ PPC970MP_PME_PM_INST_FETCH_CYC ] = {
		.pme_name = "PM_INST_FETCH_CYC",
		.pme_code = 0x424,
		.pme_short_desc = "Cycles at least 1 instruction fetched",
		.pme_long_desc = "Asserted each cycle when the IFU sends at least one instruction to the IDU. ",
	},
	[ PPC970MP_PME_PM_GRP_DISP_REJECT ] = {
		.pme_name = "PM_GRP_DISP_REJECT",
		.pme_code = 0x324,
		.pme_short_desc = "Group dispatch rejected",
		.pme_long_desc = "A group that previously attempted dispatch was rejected.",
	},
	[ PPC970MP_PME_PM_LSU_LDF ] = {
		.pme_name = "PM_LSU_LDF",
		.pme_code = 0x8730,
		.pme_short_desc = "LSU executed Floating Point load instruction",
		.pme_long_desc = "LSU executed Floating Point load instruction",
	},
	[ PPC970MP_PME_PM_INST_DISP ] = {
		.pme_name = "PM_INST_DISP",
		.pme_code = 0x320,
		.pme_short_desc = "Instructions dispatched",
		.pme_long_desc = "The ISU sends the number of instructions dispatched.",
	},
	[ PPC970MP_PME_PM_DATA_FROM_L25_SHR ] = {
		.pme_name = "PM_DATA_FROM_L25_SHR",
		.pme_code = 0x5837,
		.pme_short_desc = "Data loaded from L2.5 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T or SL) data from the L2 of a chip on this MCM due to a demand load",
	},
	[ PPC970MP_PME_PM_L1_DCACHE_RELOAD_VALID ] = {
		.pme_name = "PM_L1_DCACHE_RELOAD_VALID",
		.pme_code = 0x834,
		.pme_short_desc = "L1 reload data source valid",
		.pme_long_desc = "The data source information is valid",
	},
	[ PPC970MP_PME_PM_MRK_GRP_ISSUED ] = {
		.pme_name = "PM_MRK_GRP_ISSUED",
		.pme_code = 0x6005,
		.pme_short_desc = "Marked group issued",
		.pme_long_desc = "A sampled instruction was issued",
	},
	[ PPC970MP_PME_PM_FPU_FMA ] = {
		.pme_name = "PM_FPU_FMA",
		.pme_code = 0x2100,
		.pme_short_desc = "FPU executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when FPU is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_MRK_CRU_FIN ] = {
		.pme_name = "PM_MRK_CRU_FIN",
		.pme_code = 0x4005,
		.pme_short_desc = "Marked instruction CRU processing finished",
		.pme_long_desc = "The Condition Register Unit finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_REJECT ] = {
		.pme_name = "PM_CMPLU_STALL_REJECT",
		.pme_code = 0x70cb,
		.pme_short_desc = "Completion stall caused by reject",
		.pme_long_desc = "Completion stall caused by reject",
	},
	[ PPC970MP_PME_PM_MRK_LSU1_FLUSH_UST ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_UST",
		.pme_code = 0x715,
		.pme_short_desc = "LSU1 marked unaligned store flushes",
		.pme_long_desc = "A marked store was flushed from unit 1 because it was unaligned (crossed a 4k boundary)",
	},
	[ PPC970MP_PME_PM_MRK_FXU_FIN ] = {
		.pme_name = "PM_MRK_FXU_FIN",
		.pme_code = 0x6004,
		.pme_short_desc = "Marked instruction FXU processing finished",
		.pme_long_desc = "Marked instruction FXU processing finished",
	},
	[ PPC970MP_PME_PM_LSU1_REJECT_ERAT_MISS ] = {
		.pme_name = "PM_LSU1_REJECT_ERAT_MISS",
		.pme_code = 0x927,
		.pme_short_desc = "LSU1 reject due to ERAT miss",
		.pme_long_desc = "LSU1 reject due to ERAT miss",
	},
	[ PPC970MP_PME_PM_BR_ISSUED ] = {
		.pme_name = "PM_BR_ISSUED",
		.pme_code = 0x431,
		.pme_short_desc = "Branches issued",
		.pme_long_desc = "This signal will be asserted each time the ISU issues a branch instruction. This signal will be asserted each time the ISU selects a branch instruction to issue.",
	},
	[ PPC970MP_PME_PM_PMC4_OVERFLOW ] = {
		.pme_name = "PM_PMC4_OVERFLOW",
		.pme_code = 0x500a,
		.pme_short_desc = "PMC4 Overflow",
		.pme_long_desc = "PMC4 Overflow",
	},
	[ PPC970MP_PME_PM_EE_OFF ] = {
		.pme_name = "PM_EE_OFF",
		.pme_code = 0x333,
		.pme_short_desc = "Cycles MSR(EE) bit off",
		.pme_long_desc = "The number of Cycles MSR(EE) bit was off.",
	},
	[ PPC970MP_PME_PM_INST_FROM_L25_MOD ] = {
		.pme_name = "PM_INST_FROM_L25_MOD",
		.pme_code = 0x6426,
		.pme_short_desc = "Instruction fetched from L2.5 modified",
		.pme_long_desc = "Instruction fetched from L2.5 modified",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_ERAT_MISS ] = {
		.pme_name = "PM_CMPLU_STALL_ERAT_MISS",
		.pme_code = 0x704c,
		.pme_short_desc = "Completion stall caused by ERAT miss",
		.pme_long_desc = "Completion stall caused by ERAT miss",
	},
	[ PPC970MP_PME_PM_ITLB_MISS ] = {
		.pme_name = "PM_ITLB_MISS",
		.pme_code = 0x700,
		.pme_short_desc = "Instruction TLB misses",
		.pme_long_desc = "A TLB miss for an Instruction Fetch has occurred",
	},
	[ PPC970MP_PME_PM_FXU1_BUSY_FXU0_IDLE ] = {
		.pme_name = "PM_FXU1_BUSY_FXU0_IDLE",
		.pme_code = 0x4002,
		.pme_short_desc = "FXU1 busy FXU0 idle",
		.pme_long_desc = "FXU0 was idle while FXU1 was busy",
	},
	[ PPC970MP_PME_PM_GRP_DISP_VALID ] = {
		.pme_name = "PM_GRP_DISP_VALID",
		.pme_code = 0x323,
		.pme_short_desc = "Group dispatch valid",
		.pme_long_desc = "Dispatch has been attempted for a valid group.  Some groups may be rejected.  The total number of successful dispatches is the number of dispatch valid minus dispatch reject.",
	},
	[ PPC970MP_PME_PM_MRK_GRP_DISP ] = {
		.pme_name = "PM_MRK_GRP_DISP",
		.pme_code = 0x1002,
		.pme_short_desc = "Marked group dispatched",
		.pme_long_desc = "A group containing a sampled instruction was dispatched",
	},
	[ PPC970MP_PME_PM_LSU_FLUSH_UST ] = {
		.pme_name = "PM_LSU_FLUSH_UST",
		.pme_code = 0x2800,
		.pme_short_desc = "SRQ unaligned store flushes",
		.pme_long_desc = "A store was flushed because it was unaligned",
	},
	[ PPC970MP_PME_PM_FXU1_FIN ] = {
		.pme_name = "PM_FXU1_FIN",
		.pme_code = 0x336,
		.pme_short_desc = "FXU1 produced a result",
		.pme_long_desc = "The Fixed Point unit 1 finished an instruction and produced a result",
	},
	[ PPC970MP_PME_PM_GRP_CMPL ] = {
		.pme_name = "PM_GRP_CMPL",
		.pme_code = 0x7003,
		.pme_short_desc = "Group completed",
		.pme_long_desc = "A group completed. Microcoded instructions that span multiple groups will generate this event once per group.",
	},
	[ PPC970MP_PME_PM_FPU_FRSP_FCONV ] = {
		.pme_name = "PM_FPU_FRSP_FCONV",
		.pme_code = 0x7110,
		.pme_short_desc = "FPU executed FRSP or FCONV instructions",
		.pme_long_desc = "This signal is active for one cycle when executing frsp or convert kind of instruction. This could be frsp*, fcfid*, fcti* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_MRK_LSU0_FLUSH_SRQ ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_SRQ",
		.pme_code = 0x713,
		.pme_short_desc = "LSU0 marked SRQ flushes",
		.pme_long_desc = "A marked store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_OTHER ] = {
		.pme_name = "PM_CMPLU_STALL_OTHER",
		.pme_code = 0x100b,
		.pme_short_desc = "Completion stall caused by other reason",
		.pme_long_desc = "Completion stall caused by other reason",
	},
	[ PPC970MP_PME_PM_LSU_LMQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_LMQ_FULL_CYC",
		.pme_code = 0x837,
		.pme_short_desc = "Cycles LMQ full",
		.pme_long_desc = "The LMQ was full",
	},
	[ PPC970MP_PME_PM_ST_REF_L1_LSU0 ] = {
		.pme_name = "PM_ST_REF_L1_LSU0",
		.pme_code = 0x811,
		.pme_short_desc = "LSU0 L1 D cache store references",
		.pme_long_desc = "A store executed on unit 0",
	},
	[ PPC970MP_PME_PM_LSU0_DERAT_MISS ] = {
		.pme_name = "PM_LSU0_DERAT_MISS",
		.pme_code = 0x702,
		.pme_short_desc = "LSU0 DERAT misses",
		.pme_long_desc = "A data request (load or store) from LSU Unit 0 missed the ERAT and resulted in an ERAT reload. Multiple instructions may miss the ERAT entry for the same 4K page, but only one reload will occur.",
	},
	[ PPC970MP_PME_PM_LSU_SRQ_SYNC_CYC ] = {
		.pme_name = "PM_LSU_SRQ_SYNC_CYC",
		.pme_code = 0x735,
		.pme_short_desc = "SRQ sync duration",
		.pme_long_desc = "This signal is asserted every cycle when a sync is in the SRQ.",
	},
	[ PPC970MP_PME_PM_FPU_STALL3 ] = {
		.pme_name = "PM_FPU_STALL3",
		.pme_code = 0x2120,
		.pme_short_desc = "FPU stalled in pipe3",
		.pme_long_desc = "FPU has generated a stall in pipe3 due to overflow, underflow, massive cancel, convert to integer (sometimes), or convert from integer (always). This signal is active during the entire duration of the stall. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_LSU_REJECT_ERAT_MISS ] = {
		.pme_name = "PM_LSU_REJECT_ERAT_MISS",
		.pme_code = 0x5920,
		.pme_short_desc = "LSU reject due to ERAT miss",
		.pme_long_desc = "LSU reject due to ERAT miss",
	},
	[ PPC970MP_PME_PM_MRK_DATA_FROM_L2 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2",
		.pme_code = 0x1937,
		.pme_short_desc = "Marked data loaded from L2",
		.pme_long_desc = "DL1 was reloaded from the local L2 due to a marked demand load",
	},
	[ PPC970MP_PME_PM_LSU0_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU0_FLUSH_SRQ",
		.pme_code = 0x803,
		.pme_short_desc = "LSU0 SRQ flushes",
		.pme_long_desc = "A store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ PPC970MP_PME_PM_FPU0_FMOV_FEST ] = {
		.pme_name = "PM_FPU0_FMOV_FEST",
		.pme_code = 0x110,
		.pme_short_desc = "FPU0 executed FMOV or FEST instructions",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing a move kind of instruction or one of the estimate instructions.. This could be fmr*, fneg*, fabs*, fnabs* , fres* or frsqrte* where XYZ* means XYZ or XYZ",
	},
	[ PPC970MP_PME_PM_IOPS_CMPL ] = {
		.pme_name = "PM_IOPS_CMPL",
		.pme_code = 0x1001,
		.pme_short_desc = "IOPS instructions completed",
		.pme_long_desc = "Number of IOPS Instructions that completed.",
	},
	[ PPC970MP_PME_PM_LD_REF_L1_LSU0 ] = {
		.pme_name = "PM_LD_REF_L1_LSU0",
		.pme_code = 0x810,
		.pme_short_desc = "LSU0 L1 D cache load references",
		.pme_long_desc = "A load executed on unit 0",
	},
	[ PPC970MP_PME_PM_LSU1_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU1_FLUSH_SRQ",
		.pme_code = 0x807,
		.pme_short_desc = "LSU1 SRQ flushes",
		.pme_long_desc = "A store was flushed because younger load hits and older store that is already in the SRQ or in the same group. ",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_DIV ] = {
		.pme_name = "PM_CMPLU_STALL_DIV",
		.pme_code = 0x708b,
		.pme_short_desc = "Completion stall caused by DIV instruction",
		.pme_long_desc = "Completion stall caused by DIV instruction",
	},
	[ PPC970MP_PME_PM_GRP_BR_MPRED ] = {
		.pme_name = "PM_GRP_BR_MPRED",
		.pme_code = 0x327,
		.pme_short_desc = "Group experienced a branch mispredict",
		.pme_long_desc = "Group experienced a branch mispredict",
	},
	[ PPC970MP_PME_PM_LSU_LMQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_LMQ_S0_ALLOC",
		.pme_code = 0x836,
		.pme_short_desc = "LMQ slot 0 allocated",
		.pme_long_desc = "The first entry in the LMQ was allocated.",
	},
	[ PPC970MP_PME_PM_LSU0_REJECT_LMQ_FULL ] = {
		.pme_name = "PM_LSU0_REJECT_LMQ_FULL",
		.pme_code = 0x921,
		.pme_short_desc = "LSU0 reject due to LMQ full or missed data coming",
		.pme_long_desc = "LSU0 reject due to LMQ full or missed data coming",
	},
	[ PPC970MP_PME_PM_ST_REF_L1 ] = {
		.pme_name = "PM_ST_REF_L1",
		.pme_code = 0x7810,
		.pme_short_desc = "L1 D cache store references",
		.pme_long_desc = "Total DL1 Store references",
	},
	[ PPC970MP_PME_PM_MRK_VMX_FIN ] = {
		.pme_name = "PM_MRK_VMX_FIN",
		.pme_code = 0x3005,
		.pme_short_desc = "Marked instruction VMX processing finished",
		.pme_long_desc = "Marked instruction VMX processing finished",
	},
	[ PPC970MP_PME_PM_LSU_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_SRQ_EMPTY_CYC",
		.pme_code = 0x4003,
		.pme_short_desc = "Cycles SRQ empty",
		.pme_long_desc = "The Store Request Queue is empty",
	},
	[ PPC970MP_PME_PM_FPU1_STF ] = {
		.pme_name = "PM_FPU1_STF",
		.pme_code = 0x126,
		.pme_short_desc = "FPU1 executed store instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing a store instruction.",
	},
	[ PPC970MP_PME_PM_RUN_CYC ] = {
		.pme_name = "PM_RUN_CYC",
		.pme_code = 0x1005,
		.pme_short_desc = "Run cycles",
		.pme_long_desc = "Processor Cycles gated by the run latch",
	},
	[ PPC970MP_PME_PM_LSU_LMQ_S0_VALID ] = {
		.pme_name = "PM_LSU_LMQ_S0_VALID",
		.pme_code = 0x835,
		.pme_short_desc = "LMQ slot 0 valid",
		.pme_long_desc = "This signal is asserted every cycle when the first entry in the LMQ is valid. The LMQ had eight entries that are allocated FIFO",
	},
	[ PPC970MP_PME_PM_LSU0_LDF ] = {
		.pme_name = "PM_LSU0_LDF",
		.pme_code = 0x730,
		.pme_short_desc = "LSU0 executed Floating Point load instruction",
		.pme_long_desc = "A floating point load was executed from LSU unit 0",
	},
	[ PPC970MP_PME_PM_LSU_LRQ_S0_VALID ] = {
		.pme_name = "PM_LSU_LRQ_S0_VALID",
		.pme_code = 0x822,
		.pme_short_desc = "LRQ slot 0 valid",
		.pme_long_desc = "This signal is asserted every cycle that the Load Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.",
	},
	[ PPC970MP_PME_PM_PMC3_OVERFLOW ] = {
		.pme_name = "PM_PMC3_OVERFLOW",
		.pme_code = 0x400a,
		.pme_short_desc = "PMC3 Overflow",
		.pme_long_desc = "PMC3 Overflow",
	},
	[ PPC970MP_PME_PM_MRK_IMR_RELOAD ] = {
		.pme_name = "PM_MRK_IMR_RELOAD",
		.pme_code = 0x722,
		.pme_short_desc = "Marked IMR reloaded",
		.pme_long_desc = "A DL1 reload occurred due to marked load",
	},
	[ PPC970MP_PME_PM_MRK_GRP_TIMEO ] = {
		.pme_name = "PM_MRK_GRP_TIMEO",
		.pme_code = 0x5005,
		.pme_short_desc = "Marked group completion timeout",
		.pme_long_desc = "The sampling timeout expired indicating that the previously sampled instruction is no longer in the processor",
	},
	[ PPC970MP_PME_PM_FPU_FMOV_FEST ] = {
		.pme_name = "PM_FPU_FMOV_FEST",
		.pme_code = 0x8110,
		.pme_short_desc = "FPU executing FMOV or FEST instructions",
		.pme_long_desc = "This signal is active for one cycle when executing a move kind of instruction or one of the estimate instructions.. This could be fmr*, fneg*, fabs*, fnabs* , fres* or frsqrte* where XYZ* means XYZ or XYZ . Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_GRP_DISP_BLK_SB_CYC ] = {
		.pme_name = "PM_GRP_DISP_BLK_SB_CYC",
		.pme_code = 0x331,
		.pme_short_desc = "Cycles group dispatch blocked by scoreboard",
		.pme_long_desc = "The ISU sends a signal indicating that dispatch is blocked by scoreboard.",
	},
	[ PPC970MP_PME_PM_XER_MAP_FULL_CYC ] = {
		.pme_name = "PM_XER_MAP_FULL_CYC",
		.pme_code = 0x302,
		.pme_short_desc = "Cycles XER mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the xer mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ PPC970MP_PME_PM_ST_MISS_L1 ] = {
		.pme_name = "PM_ST_MISS_L1",
		.pme_code = 0x813,
		.pme_short_desc = "L1 D cache store misses",
		.pme_long_desc = "A store missed the dcache",
	},
	[ PPC970MP_PME_PM_STOP_COMPLETION ] = {
		.pme_name = "PM_STOP_COMPLETION",
		.pme_code = 0x3001,
		.pme_short_desc = "Completion stopped",
		.pme_long_desc = "RAS Unit has signaled completion to stop",
	},
	[ PPC970MP_PME_PM_MRK_GRP_CMPL ] = {
		.pme_name = "PM_MRK_GRP_CMPL",
		.pme_code = 0x4004,
		.pme_short_desc = "Marked group completed",
		.pme_long_desc = "A group containing a sampled instruction completed. Microcoded instructions that span multiple groups will generate this event once per group.",
	},
	[ PPC970MP_PME_PM_ISLB_MISS ] = {
		.pme_name = "PM_ISLB_MISS",
		.pme_code = 0x701,
		.pme_short_desc = "Instruction SLB misses",
		.pme_long_desc = "A SLB miss for an instruction fetch as occurred",
	},
	[ PPC970MP_PME_PM_SUSPENDED ] = {
		.pme_name = "PM_SUSPENDED",
		.pme_code = 0x0,
		.pme_short_desc = "Suspended",
		.pme_long_desc = "Suspended",
	},
	[ PPC970MP_PME_PM_CYC ] = {
		.pme_name = "PM_CYC",
		.pme_code = 0x7,
		.pme_short_desc = "Processor cycles",
		.pme_long_desc = "Processor cycles",
	},
	[ PPC970MP_PME_PM_LD_MISS_L1_LSU1 ] = {
		.pme_name = "PM_LD_MISS_L1_LSU1",
		.pme_code = 0x816,
		.pme_short_desc = "LSU1 L1 D cache load misses",
		.pme_long_desc = "A load, executing on unit 1, missed the dcache",
	},
	[ PPC970MP_PME_PM_STCX_FAIL ] = {
		.pme_name = "PM_STCX_FAIL",
		.pme_code = 0x721,
		.pme_short_desc = "STCX failed",
		.pme_long_desc = "A stcx (stwcx or stdcx) failed",
	},
	[ PPC970MP_PME_PM_LSU1_SRQ_STFWD ] = {
		.pme_name = "PM_LSU1_SRQ_STFWD",
		.pme_code = 0x824,
		.pme_short_desc = "LSU1 SRQ store forwarded",
		.pme_long_desc = "Data from a store instruction was forwarded to a load on unit 1",
	},
	[ PPC970MP_PME_PM_GRP_DISP ] = {
		.pme_name = "PM_GRP_DISP",
		.pme_code = 0x2004,
		.pme_short_desc = "Group dispatches",
		.pme_long_desc = "A group was dispatched",
	},
	[ PPC970MP_PME_PM_L2_PREF ] = {
		.pme_name = "PM_L2_PREF",
		.pme_code = 0x733,
		.pme_short_desc = "L2 cache prefetches",
		.pme_long_desc = "A request to prefetch data into L2 was made",
	},
	[ PPC970MP_PME_PM_FPU1_DENORM ] = {
		.pme_name = "PM_FPU1_DENORM",
		.pme_code = 0x124,
		.pme_short_desc = "FPU1 received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized.",
	},
	[ PPC970MP_PME_PM_DATA_FROM_L2 ] = {
		.pme_name = "PM_DATA_FROM_L2",
		.pme_code = 0x1837,
		.pme_short_desc = "Data loaded from L2",
		.pme_long_desc = "DL1 was reloaded from the local L2 due to a demand load",
	},
	[ PPC970MP_PME_PM_FPU0_FPSCR ] = {
		.pme_name = "PM_FPU0_FPSCR",
		.pme_code = 0x130,
		.pme_short_desc = "FPU0 executed FPSCR instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing fpscr move related instruction. This could be mtfsfi*, mtfsb0*, mtfsb1*. mffs*, mtfsf*, mcrsf* where XYZ* means XYZ, XYZs, XYZ., XYZs",
	},
	[ PPC970MP_PME_PM_MRK_DATA_FROM_L25_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L25_MOD",
		.pme_code = 0x6937,
		.pme_short_desc = "Marked data loaded from L2.5 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of a chip on this MCM due to a marked demand load",
	},
	[ PPC970MP_PME_PM_FPU0_FSQRT ] = {
		.pme_name = "PM_FPU0_FSQRT",
		.pme_code = 0x102,
		.pme_short_desc = "FPU0 executed FSQRT instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp0 is executing a square root instruction. This could be fsqrt* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ PPC970MP_PME_PM_LD_REF_L1 ] = {
		.pme_name = "PM_LD_REF_L1",
		.pme_code = 0x8810,
		.pme_short_desc = "L1 D cache load references",
		.pme_long_desc = "Total DL1 Load references",
	},
	[ PPC970MP_PME_PM_MRK_L1_RELOAD_VALID ] = {
		.pme_name = "PM_MRK_L1_RELOAD_VALID",
		.pme_code = 0x934,
		.pme_short_desc = "Marked L1 reload data source valid",
		.pme_long_desc = "The source information is valid and is for a marked load",
	},
	[ PPC970MP_PME_PM_1PLUS_PPC_CMPL ] = {
		.pme_name = "PM_1PLUS_PPC_CMPL",
		.pme_code = 0x5003,
		.pme_short_desc = "One or more PPC instruction completed",
		.pme_long_desc = "A group containing at least one PPC instruction completed. For microcoded instructions that span multiple groups, this will only occur once.",
	},
	[ PPC970MP_PME_PM_INST_FROM_L1 ] = {
		.pme_name = "PM_INST_FROM_L1",
		.pme_code = 0x142d,
		.pme_short_desc = "Instruction fetched from L1",
		.pme_long_desc = "An instruction fetch group was fetched from L1. Fetch Groups can contain up to 8 instructions",
	},
	[ PPC970MP_PME_PM_EE_OFF_EXT_INT ] = {
		.pme_name = "PM_EE_OFF_EXT_INT",
		.pme_code = 0x337,
		.pme_short_desc = "Cycles MSR(EE) bit off and external interrupt pending",
		.pme_long_desc = "Cycles MSR(EE) bit off and external interrupt pending",
	},
	[ PPC970MP_PME_PM_PMC6_OVERFLOW ] = {
		.pme_name = "PM_PMC6_OVERFLOW",
		.pme_code = 0x700a,
		.pme_short_desc = "PMC6 Overflow",
		.pme_long_desc = "PMC6 Overflow",
	},
	[ PPC970MP_PME_PM_LSU_LRQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_LRQ_FULL_CYC",
		.pme_code = 0x312,
		.pme_short_desc = "Cycles LRQ full",
		.pme_long_desc = "The ISU sends this signal when the LRQ is full.",
	},
	[ PPC970MP_PME_PM_IC_PREF_INSTALL ] = {
		.pme_name = "PM_IC_PREF_INSTALL",
		.pme_code = 0x427,
		.pme_short_desc = "Instruction prefetched installed in prefetch",
		.pme_long_desc = "New line coming into the prefetch buffer",
	},
	[ PPC970MP_PME_PM_DC_PREF_OUT_OF_STREAMS ] = {
		.pme_name = "PM_DC_PREF_OUT_OF_STREAMS",
		.pme_code = 0x732,
		.pme_short_desc = "D cache out of streams",
		.pme_long_desc = "out of streams",
	},
	[ PPC970MP_PME_PM_MRK_LSU1_FLUSH_SRQ ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_SRQ",
		.pme_code = 0x717,
		.pme_short_desc = "LSU1 marked SRQ flushes",
		.pme_long_desc = "A marked store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ PPC970MP_PME_PM_GCT_FULL_CYC ] = {
		.pme_name = "PM_GCT_FULL_CYC",
		.pme_code = 0x300,
		.pme_short_desc = "Cycles GCT full",
		.pme_long_desc = "The ISU sends a signal indicating the gct is full. ",
	},
	[ PPC970MP_PME_PM_INST_FROM_MEM ] = {
		.pme_name = "PM_INST_FROM_MEM",
		.pme_code = 0x2426,
		.pme_short_desc = "Instruction fetched from memory",
		.pme_long_desc = "Instruction fetched from memory",
	},
	[ PPC970MP_PME_PM_FLUSH_LSU_BR_MPRED ] = {
		.pme_name = "PM_FLUSH_LSU_BR_MPRED",
		.pme_code = 0x317,
		.pme_short_desc = "Flush caused by LSU or branch mispredict",
		.pme_long_desc = "Flush caused by LSU or branch mispredict",
	},
	[ PPC970MP_PME_PM_FXU_BUSY ] = {
		.pme_name = "PM_FXU_BUSY",
		.pme_code = 0x6002,
		.pme_short_desc = "FXU busy",
		.pme_long_desc = "FXU0 and FXU1 are both busy",
	},
	[ PPC970MP_PME_PM_ST_REF_L1_LSU1 ] = {
		.pme_name = "PM_ST_REF_L1_LSU1",
		.pme_code = 0x815,
		.pme_short_desc = "LSU1 L1 D cache store references",
		.pme_long_desc = "A store executed on unit 1",
	},
	[ PPC970MP_PME_PM_MRK_LD_MISS_L1 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1",
		.pme_code = 0x1720,
		.pme_short_desc = "Marked L1 D cache load misses",
		.pme_long_desc = "Marked L1 D cache load misses",
	},
	[ PPC970MP_PME_PM_L1_WRITE_CYC ] = {
		.pme_name = "PM_L1_WRITE_CYC",
		.pme_code = 0x434,
		.pme_short_desc = "Cycles writing to instruction L1",
		.pme_long_desc = "This signal is asserted each cycle a cache write is active.",
	},
	[ PPC970MP_PME_PM_LSU1_BUSY ] = {
		.pme_name = "PM_LSU1_BUSY",
		.pme_code = 0x827,
		.pme_short_desc = "LSU1 busy",
		.pme_long_desc = "LSU unit 0 is busy rejecting instructions ",
	},
	[ PPC970MP_PME_PM_LSU_REJECT_LMQ_FULL ] = {
		.pme_name = "PM_LSU_REJECT_LMQ_FULL",
		.pme_code = 0x2920,
		.pme_short_desc = "LSU reject due to LMQ full or missed data coming",
		.pme_long_desc = "LSU reject due to LMQ full or missed data coming",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_FDIV ] = {
		.pme_name = "PM_CMPLU_STALL_FDIV",
		.pme_code = 0x504c,
		.pme_short_desc = "Completion stall caused by FDIV or FQRT instruction",
		.pme_long_desc = "Completion stall caused by FDIV or FQRT instruction",
	},
	[ PPC970MP_PME_PM_FPU_ALL ] = {
		.pme_name = "PM_FPU_ALL",
		.pme_code = 0x5100,
		.pme_short_desc = "FPU executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when FPU is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_LSU_SRQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_SRQ_S0_ALLOC",
		.pme_code = 0x825,
		.pme_short_desc = "SRQ slot 0 allocated",
		.pme_long_desc = "SRQ Slot zero was allocated",
	},
	[ PPC970MP_PME_PM_INST_FROM_L25_SHR ] = {
		.pme_name = "PM_INST_FROM_L25_SHR",
		.pme_code = 0x5426,
		.pme_short_desc = "Instruction fetched from L2.5 shared",
		.pme_long_desc = "Instruction fetched from L2.5 shared",
	},
	[ PPC970MP_PME_PM_GRP_MRK ] = {
		.pme_name = "PM_GRP_MRK",
		.pme_code = 0x5004,
		.pme_short_desc = "Group marked in IDU",
		.pme_long_desc = "A group was sampled (marked)",
	},
	[ PPC970MP_PME_PM_BR_MPRED_CR ] = {
		.pme_name = "PM_BR_MPRED_CR",
		.pme_code = 0x432,
		.pme_short_desc = "Branch mispredictions due to CR bit setting",
		.pme_long_desc = "This signal is asserted when the branch execution unit detects a branch mispredict because the CR value is opposite of the predicted value. This signal is asserted after a branch issue event and will result in a branch redirect flush if not overridden by a flush of an older instruction.",
	},
	[ PPC970MP_PME_PM_DC_PREF_STREAM_ALLOC ] = {
		.pme_name = "PM_DC_PREF_STREAM_ALLOC",
		.pme_code = 0x737,
		.pme_short_desc = "D cache new prefetch stream allocated",
		.pme_long_desc = "A new Prefetch Stream was allocated",
	},
	[ PPC970MP_PME_PM_FPU1_FIN ] = {
		.pme_name = "PM_FPU1_FIN",
		.pme_code = 0x117,
		.pme_short_desc = "FPU1 produced a result",
		.pme_long_desc = "fp1 finished, produced a result. This only indicates finish, not completion. ",
	},
	[ PPC970MP_PME_PM_LSU_REJECT_SRQ ] = {
		.pme_name = "PM_LSU_REJECT_SRQ",
		.pme_code = 0x1920,
		.pme_short_desc = "LSU SRQ rejects",
		.pme_long_desc = "LSU SRQ rejects",
	},
	[ PPC970MP_PME_PM_BR_MPRED_TA ] = {
		.pme_name = "PM_BR_MPRED_TA",
		.pme_code = 0x433,
		.pme_short_desc = "Branch mispredictions due to target address",
		.pme_long_desc = "branch miss predict due to a target address prediction. This signal will be asserted each time the branch execution unit detects an incorrect target address prediction. This signal will be asserted after a valid branch execution unit issue and will cause a branch mispredict flush unless a flush is detected from an older instruction.",
	},
	[ PPC970MP_PME_PM_CRQ_FULL_CYC ] = {
		.pme_name = "PM_CRQ_FULL_CYC",
		.pme_code = 0x311,
		.pme_short_desc = "Cycles CR issue queue full",
		.pme_long_desc = "The ISU sends a signal indicating that the issue queue that feeds the ifu cr unit cannot accept any more group (queue is full of groups).",
	},
	[ PPC970MP_PME_PM_LD_MISS_L1 ] = {
		.pme_name = "PM_LD_MISS_L1",
		.pme_code = 0x3810,
		.pme_short_desc = "L1 D cache load misses",
		.pme_long_desc = "Total DL1 Load references that miss the DL1",
	},
	[ PPC970MP_PME_PM_INST_FROM_PREF ] = {
		.pme_name = "PM_INST_FROM_PREF",
		.pme_code = 0x342d,
		.pme_short_desc = "Instructions fetched from prefetch",
		.pme_long_desc = "An instruction fetch group was fetched from the prefetch buffer. Fetch Groups can contain up to 8 instructions",
	},
	[ PPC970MP_PME_PM_STCX_PASS ] = {
		.pme_name = "PM_STCX_PASS",
		.pme_code = 0x725,
		.pme_short_desc = "Stcx passes",
		.pme_long_desc = "A stcx (stwcx or stdcx) instruction was successful",
	},
	[ PPC970MP_PME_PM_DC_INV_L2 ] = {
		.pme_name = "PM_DC_INV_L2",
		.pme_code = 0x817,
		.pme_short_desc = "L1 D cache entries invalidated from L2",
		.pme_long_desc = "A dcache invalidated was received from the L2 because a line in L2 was castout.",
	},
	[ PPC970MP_PME_PM_LSU_SRQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_SRQ_FULL_CYC",
		.pme_code = 0x313,
		.pme_short_desc = "Cycles SRQ full",
		.pme_long_desc = "The ISU sends this signal when the srq is full.",
	},
	[ PPC970MP_PME_PM_LSU0_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU0_FLUSH_LRQ",
		.pme_code = 0x802,
		.pme_short_desc = "LSU0 LRQ flushes",
		.pme_long_desc = "A load was flushed by unit 1 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ PPC970MP_PME_PM_LSU_SRQ_S0_VALID ] = {
		.pme_name = "PM_LSU_SRQ_S0_VALID",
		.pme_code = 0x821,
		.pme_short_desc = "SRQ slot 0 valid",
		.pme_long_desc = "This signal is asserted every cycle that the Store Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.",
	},
	[ PPC970MP_PME_PM_LARX_LSU0 ] = {
		.pme_name = "PM_LARX_LSU0",
		.pme_code = 0x727,
		.pme_short_desc = "Larx executed on LSU0",
		.pme_long_desc = "A larx (lwarx or ldarx) was executed on side 0 (there is no corresponding unit 1 event since larx instructions can only execute on unit 0)",
	},
	[ PPC970MP_PME_PM_GCT_EMPTY_CYC ] = {
		.pme_name = "PM_GCT_EMPTY_CYC",
		.pme_code = 0x1004,
		.pme_short_desc = "Cycles GCT empty",
		.pme_long_desc = "The Global Completion Table is completely empty",
	},
	[ PPC970MP_PME_PM_FPU1_ALL ] = {
		.pme_name = "PM_FPU1_ALL",
		.pme_code = 0x107,
		.pme_short_desc = "FPU1 executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo",
	},
	[ PPC970MP_PME_PM_FPU1_FSQRT ] = {
		.pme_name = "PM_FPU1_FSQRT",
		.pme_code = 0x106,
		.pme_short_desc = "FPU1 executed FSQRT instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp1 is executing a square root instruction. This could be fsqrt* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ PPC970MP_PME_PM_FPU_FIN ] = {
		.pme_name = "PM_FPU_FIN",
		.pme_code = 0x4110,
		.pme_short_desc = "FPU produced a result",
		.pme_long_desc = "FPU finished, produced a result This only indicates finish, not completion. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_LSU_SRQ_STFWD ] = {
		.pme_name = "PM_LSU_SRQ_STFWD",
		.pme_code = 0x1820,
		.pme_short_desc = "SRQ store forwarded",
		.pme_long_desc = "Data from a store instruction was forwarded to a load",
	},
	[ PPC970MP_PME_PM_MRK_LD_MISS_L1_LSU1 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1_LSU1",
		.pme_code = 0x724,
		.pme_short_desc = "LSU1 L1 D cache load misses",
		.pme_long_desc = "A marked load, executing on unit 1, missed the dcache",
	},
	[ PPC970MP_PME_PM_FXU0_FIN ] = {
		.pme_name = "PM_FXU0_FIN",
		.pme_code = 0x332,
		.pme_short_desc = "FXU0 produced a result",
		.pme_long_desc = "The Fixed Point unit 0 finished an instruction and produced a result",
	},
	[ PPC970MP_PME_PM_MRK_FPU_FIN ] = {
		.pme_name = "PM_MRK_FPU_FIN",
		.pme_code = 0x7004,
		.pme_short_desc = "Marked instruction FPU processing finished",
		.pme_long_desc = "One of the Floating Point Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ PPC970MP_PME_PM_PMC5_OVERFLOW ] = {
		.pme_name = "PM_PMC5_OVERFLOW",
		.pme_code = 0x600a,
		.pme_short_desc = "PMC5 Overflow",
		.pme_long_desc = "PMC5 Overflow",
	},
	[ PPC970MP_PME_PM_SNOOP_TLBIE ] = {
		.pme_name = "PM_SNOOP_TLBIE",
		.pme_code = 0x703,
		.pme_short_desc = "Snoop TLBIE",
		.pme_long_desc = "A TLB miss for a data request occurred. Requests that miss the TLB may be retried until the instruction is in the next to complete group (unless HID4 is set to allow speculative tablewalks). This may result in multiple TLB misses for the same instruction.",
	},
	[ PPC970MP_PME_PM_FPU1_FRSP_FCONV ] = {
		.pme_name = "PM_FPU1_FRSP_FCONV",
		.pme_code = 0x115,
		.pme_short_desc = "FPU1 executed FRSP or FCONV instructions",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing frsp or convert kind of instruction. This could be frsp*, fcfid*, fcti* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ PPC970MP_PME_PM_FPU0_FDIV ] = {
		.pme_name = "PM_FPU0_FDIV",
		.pme_code = 0x100,
		.pme_short_desc = "FPU0 executed FDIV instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp0 is executing a divide instruction. This could be fdiv, fdivs, fdiv. fdivs.",
	},
	[ PPC970MP_PME_PM_LD_REF_L1_LSU1 ] = {
		.pme_name = "PM_LD_REF_L1_LSU1",
		.pme_code = 0x814,
		.pme_short_desc = "LSU1 L1 D cache load references",
		.pme_long_desc = "A load executed on unit 1",
	},
	[ PPC970MP_PME_PM_HV_CYC ] = {
		.pme_name = "PM_HV_CYC",
		.pme_code = 0x3004,
		.pme_short_desc = "Hypervisor Cycles",
		.pme_long_desc = "Cycles when the processor is executing in Hypervisor (MSR[HV] = 1 and MSR[PR]=0)",
	},
	[ PPC970MP_PME_PM_LR_CTR_MAP_FULL_CYC ] = {
		.pme_name = "PM_LR_CTR_MAP_FULL_CYC",
		.pme_code = 0x306,
		.pme_short_desc = "Cycles LR/CTR mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the lr/ctr mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ PPC970MP_PME_PM_FPU_DENORM ] = {
		.pme_name = "PM_FPU_DENORM",
		.pme_code = 0x1120,
		.pme_short_desc = "FPU received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized. Combined Unit 0 + Unit 1",
	},
	[ PPC970MP_PME_PM_LSU0_REJECT_SRQ ] = {
		.pme_name = "PM_LSU0_REJECT_SRQ",
		.pme_code = 0x920,
		.pme_short_desc = "LSU0 SRQ rejects",
		.pme_long_desc = "LSU0 SRQ rejects",
	},
	[ PPC970MP_PME_PM_LSU1_REJECT_SRQ ] = {
		.pme_name = "PM_LSU1_REJECT_SRQ",
		.pme_code = 0x924,
		.pme_short_desc = "LSU1 SRQ rejects",
		.pme_long_desc = "LSU1 SRQ rejects",
	},
	[ PPC970MP_PME_PM_LSU1_DERAT_MISS ] = {
		.pme_name = "PM_LSU1_DERAT_MISS",
		.pme_code = 0x706,
		.pme_short_desc = "LSU1 DERAT misses",
		.pme_long_desc = "A data request (load or store) from LSU Unit 1 missed the ERAT and resulted in an ERAT reload. Multiple instructions may miss the ERAT entry for the same 4K page, but only one reload will occur.",
	},
	[ PPC970MP_PME_PM_IC_PREF_REQ ] = {
		.pme_name = "PM_IC_PREF_REQ",
		.pme_code = 0x426,
		.pme_short_desc = "Instruction prefetch requests",
		.pme_long_desc = "Asserted when a non-canceled prefetch is made to the cache interface unit (CIU).",
	},
	[ PPC970MP_PME_PM_MRK_LSU_FIN ] = {
		.pme_name = "PM_MRK_LSU_FIN",
		.pme_code = 0x8004,
		.pme_short_desc = "Marked instruction LSU processing finished",
		.pme_long_desc = "One of the Load/Store Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ PPC970MP_PME_PM_MRK_DATA_FROM_MEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_MEM",
		.pme_code = 0x2937,
		.pme_short_desc = "Marked data loaded from memory",
		.pme_long_desc = "Marked data loaded from memory",
	},
	[ PPC970MP_PME_PM_CMPLU_STALL_DCACHE_MISS ] = {
		.pme_name = "PM_CMPLU_STALL_DCACHE_MISS",
		.pme_code = 0x50cb,
		.pme_short_desc = "Completion stall caused by D cache miss",
		.pme_long_desc = "Completion stall caused by D cache miss",
	},
	[ PPC970MP_PME_PM_LSU0_FLUSH_UST ] = {
		.pme_name = "PM_LSU0_FLUSH_UST",
		.pme_code = 0x801,
		.pme_short_desc = "LSU0 unaligned store flushes",
		.pme_long_desc = "A store was flushed from unit 0 because it was unaligned (crossed a 4k boundary)",
	},
	[ PPC970MP_PME_PM_LSU_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU_FLUSH_LRQ",
		.pme_code = 0x6800,
		.pme_short_desc = "LRQ flushes",
		.pme_long_desc = "A load was flushed because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ PPC970MP_PME_PM_LSU_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU_FLUSH_SRQ",
		.pme_code = 0x5800,
		.pme_short_desc = "SRQ flushes",
		.pme_long_desc = "A store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	}
};
#endif

