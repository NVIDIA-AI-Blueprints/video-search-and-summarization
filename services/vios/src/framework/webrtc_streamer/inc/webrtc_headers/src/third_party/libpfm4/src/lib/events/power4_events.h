/****************************/
/* THIS IS OPEN SOURCE CODE */
/****************************/

#ifndef __POWER4_EVENTS_H__
#define __POWER4_EVENTS_H__

/*
* File:    power4_events.h
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
#define POWER4_PME_PM_MRK_LSU_SRQ_INST_VALID 0
#define POWER4_PME_PM_FPU1_SINGLE 1
#define POWER4_PME_PM_DC_PREF_OUT_STREAMS 2
#define POWER4_PME_PM_FPU0_STALL3 3
#define POWER4_PME_PM_TB_BIT_TRANS 4
#define POWER4_PME_PM_GPR_MAP_FULL_CYC 5
#define POWER4_PME_PM_MRK_ST_CMPL 6
#define POWER4_PME_PM_MRK_LSU_FLUSH_LRQ 7
#define POWER4_PME_PM_FPU0_STF 8
#define POWER4_PME_PM_FPU1_FMA 9
#define POWER4_PME_PM_L2SA_MOD_TAG 10
#define POWER4_PME_PM_MRK_DATA_FROM_L275_SHR 11
#define POWER4_PME_PM_1INST_CLB_CYC 12
#define POWER4_PME_PM_LSU1_FLUSH_ULD 13
#define POWER4_PME_PM_MRK_INST_FIN 14
#define POWER4_PME_PM_MRK_LSU0_FLUSH_UST 15
#define POWER4_PME_PM_FPU_FDIV 16
#define POWER4_PME_PM_LSU_LRQ_S0_ALLOC 17
#define POWER4_PME_PM_FPU0_FULL_CYC 18
#define POWER4_PME_PM_FPU_SINGLE 19
#define POWER4_PME_PM_FPU0_FMA 20
#define POWER4_PME_PM_MRK_LSU1_FLUSH_ULD 21
#define POWER4_PME_PM_LSU1_FLUSH_LRQ 22
#define POWER4_PME_PM_L2SA_ST_HIT 23
#define POWER4_PME_PM_L2SB_SHR_INV 24
#define POWER4_PME_PM_DTLB_MISS 25
#define POWER4_PME_PM_MRK_ST_MISS_L1 26
#define POWER4_PME_PM_EXT_INT 27
#define POWER4_PME_PM_MRK_LSU1_FLUSH_LRQ 28
#define POWER4_PME_PM_MRK_ST_GPS 29
#define POWER4_PME_PM_GRP_DISP_SUCCESS 30
#define POWER4_PME_PM_LSU1_LDF 31
#define POWER4_PME_PM_FAB_CMD_ISSUED 32
#define POWER4_PME_PM_LSU0_SRQ_STFWD 33
#define POWER4_PME_PM_CR_MAP_FULL_CYC 34
#define POWER4_PME_PM_MRK_LSU0_FLUSH_ULD 35
#define POWER4_PME_PM_LSU_DERAT_MISS 36
#define POWER4_PME_PM_FPU0_SINGLE 37
#define POWER4_PME_PM_FPU1_FDIV 38
#define POWER4_PME_PM_FPU1_FEST 39
#define POWER4_PME_PM_FPU0_FRSP_FCONV 40
#define POWER4_PME_PM_MRK_ST_CMPL_INT 41
#define POWER4_PME_PM_FXU_FIN 42
#define POWER4_PME_PM_FPU_STF 43
#define POWER4_PME_PM_DSLB_MISS 44
#define POWER4_PME_PM_DATA_FROM_L275_SHR 45
#define POWER4_PME_PM_FXLS1_FULL_CYC 46
#define POWER4_PME_PM_L3B0_DIR_MIS 47
#define POWER4_PME_PM_2INST_CLB_CYC 48
#define POWER4_PME_PM_MRK_STCX_FAIL 49
#define POWER4_PME_PM_LSU_LMQ_LHR_MERGE 50
#define POWER4_PME_PM_FXU0_BUSY_FXU1_IDLE 51
#define POWER4_PME_PM_L3B1_DIR_REF 52
#define POWER4_PME_PM_MRK_LSU_FLUSH_UST 53
#define POWER4_PME_PM_MRK_DATA_FROM_L25_SHR 54
#define POWER4_PME_PM_LSU_FLUSH_ULD 55
#define POWER4_PME_PM_MRK_BRU_FIN 56
#define POWER4_PME_PM_IERAT_XLATE_WR 57
#define POWER4_PME_PM_LSU0_BUSY 58
#define POWER4_PME_PM_L2SA_ST_REQ 59
#define POWER4_PME_PM_DATA_FROM_MEM 60
#define POWER4_PME_PM_FPR_MAP_FULL_CYC 61
#define POWER4_PME_PM_FPU1_FULL_CYC 62
#define POWER4_PME_PM_FPU0_FIN 63
#define POWER4_PME_PM_3INST_CLB_CYC 64
#define POWER4_PME_PM_DATA_FROM_L35 65
#define POWER4_PME_PM_L2SA_SHR_INV 66
#define POWER4_PME_PM_MRK_LSU_FLUSH_SRQ 67
#define POWER4_PME_PM_THRESH_TIMEO 68
#define POWER4_PME_PM_FPU_FSQRT 69
#define POWER4_PME_PM_MRK_LSU0_FLUSH_LRQ 70
#define POWER4_PME_PM_FXLS0_FULL_CYC 71
#define POWER4_PME_PM_DATA_TABLEWALK_CYC 72
#define POWER4_PME_PM_FPU0_ALL 73
#define POWER4_PME_PM_FPU0_FEST 74
#define POWER4_PME_PM_DATA_FROM_L25_MOD 75
#define POWER4_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC 76
#define POWER4_PME_PM_FPU_FEST 77
#define POWER4_PME_PM_0INST_FETCH 78
#define POWER4_PME_PM_LARX_LSU1 79
#define POWER4_PME_PM_LD_MISS_L1_LSU0 80
#define POWER4_PME_PM_L1_PREF 81
#define POWER4_PME_PM_FPU1_STALL3 82
#define POWER4_PME_PM_BRQ_FULL_CYC 83
#define POWER4_PME_PM_LARX 84
#define POWER4_PME_PM_MRK_DATA_FROM_L35 85
#define POWER4_PME_PM_WORK_HELD 86
#define POWER4_PME_PM_MRK_LD_MISS_L1_LSU0 87
#define POWER4_PME_PM_FXU_IDLE 88
#define POWER4_PME_PM_INST_CMPL 89
#define POWER4_PME_PM_LSU1_FLUSH_UST 90
#define POWER4_PME_PM_LSU0_FLUSH_ULD 91
#define POWER4_PME_PM_INST_FROM_L2 92
#define POWER4_PME_PM_DATA_FROM_L3 93
#define POWER4_PME_PM_FPU0_DENORM 94
#define POWER4_PME_PM_FPU1_FMOV_FEST 95
#define POWER4_PME_PM_GRP_DISP_REJECT 96
#define POWER4_PME_PM_INST_FETCH_CYC 97
#define POWER4_PME_PM_LSU_LDF 98
#define POWER4_PME_PM_INST_DISP 99
#define POWER4_PME_PM_L2SA_MOD_INV 100
#define POWER4_PME_PM_DATA_FROM_L25_SHR 101
#define POWER4_PME_PM_FAB_CMD_RETRIED 102
#define POWER4_PME_PM_L1_DCACHE_RELOAD_VALID 103
#define POWER4_PME_PM_MRK_GRP_ISSUED 104
#define POWER4_PME_PM_FPU_FULL_CYC 105
#define POWER4_PME_PM_FPU_FMA 106
#define POWER4_PME_PM_MRK_CRU_FIN 107
#define POWER4_PME_PM_MRK_LSU1_FLUSH_UST 108
#define POWER4_PME_PM_MRK_FXU_FIN 109
#define POWER4_PME_PM_BR_ISSUED 110
#define POWER4_PME_PM_EE_OFF 111
#define POWER4_PME_PM_INST_FROM_L3 112
#define POWER4_PME_PM_ITLB_MISS 113
#define POWER4_PME_PM_FXLS_FULL_CYC 114
#define POWER4_PME_PM_FXU1_BUSY_FXU0_IDLE 115
#define POWER4_PME_PM_GRP_DISP_VALID 116
#define POWER4_PME_PM_L2SC_ST_HIT 117
#define POWER4_PME_PM_MRK_GRP_DISP 118
#define POWER4_PME_PM_L2SB_MOD_TAG 119
#define POWER4_PME_PM_INST_FROM_L25_L275 120
#define POWER4_PME_PM_LSU_FLUSH_UST 121
#define POWER4_PME_PM_L2SB_ST_HIT 122
#define POWER4_PME_PM_FXU1_FIN 123
#define POWER4_PME_PM_L3B1_DIR_MIS 124
#define POWER4_PME_PM_4INST_CLB_CYC 125
#define POWER4_PME_PM_GRP_CMPL 126
#define POWER4_PME_PM_DC_PREF_L2_CLONE_L3 127
#define POWER4_PME_PM_FPU_FRSP_FCONV 128
#define POWER4_PME_PM_5INST_CLB_CYC 129
#define POWER4_PME_PM_MRK_LSU0_FLUSH_SRQ 130
#define POWER4_PME_PM_MRK_LSU_FLUSH_ULD 131
#define POWER4_PME_PM_8INST_CLB_CYC 132
#define POWER4_PME_PM_LSU_LMQ_FULL_CYC 133
#define POWER4_PME_PM_ST_REF_L1_LSU0 134
#define POWER4_PME_PM_LSU0_DERAT_MISS 135
#define POWER4_PME_PM_LSU_SRQ_SYNC_CYC 136
#define POWER4_PME_PM_FPU_STALL3 137
#define POWER4_PME_PM_MRK_DATA_FROM_L2 138
#define POWER4_PME_PM_FPU0_FMOV_FEST 139
#define POWER4_PME_PM_LSU0_FLUSH_SRQ 140
#define POWER4_PME_PM_LD_REF_L1_LSU0 141
#define POWER4_PME_PM_L2SC_SHR_INV 142
#define POWER4_PME_PM_LSU1_FLUSH_SRQ 143
#define POWER4_PME_PM_LSU_LMQ_S0_ALLOC 144
#define POWER4_PME_PM_ST_REF_L1 145
#define POWER4_PME_PM_LSU_SRQ_EMPTY_CYC 146
#define POWER4_PME_PM_FPU1_STF 147
#define POWER4_PME_PM_L3B0_DIR_REF 148
#define POWER4_PME_PM_RUN_CYC 149
#define POWER4_PME_PM_LSU_LMQ_S0_VALID 150
#define POWER4_PME_PM_LSU_LRQ_S0_VALID 151
#define POWER4_PME_PM_LSU0_LDF 152
#define POWER4_PME_PM_MRK_IMR_RELOAD 153
#define POWER4_PME_PM_7INST_CLB_CYC 154
#define POWER4_PME_PM_MRK_GRP_TIMEO 155
#define POWER4_PME_PM_FPU_FMOV_FEST 156
#define POWER4_PME_PM_GRP_DISP_BLK_SB_CYC 157
#define POWER4_PME_PM_XER_MAP_FULL_CYC 158
#define POWER4_PME_PM_ST_MISS_L1 159
#define POWER4_PME_PM_STOP_COMPLETION 160
#define POWER4_PME_PM_MRK_GRP_CMPL 161
#define POWER4_PME_PM_ISLB_MISS 162
#define POWER4_PME_PM_CYC 163
#define POWER4_PME_PM_LD_MISS_L1_LSU1 164
#define POWER4_PME_PM_STCX_FAIL 165
#define POWER4_PME_PM_LSU1_SRQ_STFWD 166
#define POWER4_PME_PM_GRP_DISP 167
#define POWER4_PME_PM_DATA_FROM_L2 168
#define POWER4_PME_PM_L2_PREF 169
#define POWER4_PME_PM_FPU0_FPSCR 170
#define POWER4_PME_PM_FPU1_DENORM 171
#define POWER4_PME_PM_MRK_DATA_FROM_L25_MOD 172
#define POWER4_PME_PM_L2SB_ST_REQ 173
#define POWER4_PME_PM_L2SB_MOD_INV 174
#define POWER4_PME_PM_FPU0_FSQRT 175
#define POWER4_PME_PM_LD_REF_L1 176
#define POWER4_PME_PM_MRK_L1_RELOAD_VALID 177
#define POWER4_PME_PM_L2SB_SHR_MOD 178
#define POWER4_PME_PM_INST_FROM_L1 179
#define POWER4_PME_PM_1PLUS_PPC_CMPL 180
#define POWER4_PME_PM_EE_OFF_EXT_INT 181
#define POWER4_PME_PM_L2SC_SHR_MOD 182
#define POWER4_PME_PM_LSU_LRQ_FULL_CYC 183
#define POWER4_PME_PM_IC_PREF_INSTALL 184
#define POWER4_PME_PM_MRK_LSU1_FLUSH_SRQ 185
#define POWER4_PME_PM_GCT_FULL_CYC 186
#define POWER4_PME_PM_INST_FROM_MEM 187
#define POWER4_PME_PM_FXU_BUSY 188
#define POWER4_PME_PM_ST_REF_L1_LSU1 189
#define POWER4_PME_PM_MRK_LD_MISS_L1 190
#define POWER4_PME_PM_MRK_LSU1_INST_FIN 191
#define POWER4_PME_PM_L1_WRITE_CYC 192
#define POWER4_PME_PM_BIQ_IDU_FULL_CYC 193
#define POWER4_PME_PM_MRK_LSU0_INST_FIN 194
#define POWER4_PME_PM_L2SC_ST_REQ 195
#define POWER4_PME_PM_LSU1_BUSY 196
#define POWER4_PME_PM_FPU_ALL 197
#define POWER4_PME_PM_LSU_SRQ_S0_ALLOC 198
#define POWER4_PME_PM_GRP_MRK 199
#define POWER4_PME_PM_FPU1_FIN 200
#define POWER4_PME_PM_DC_PREF_STREAM_ALLOC 201
#define POWER4_PME_PM_BR_MPRED_CR 202
#define POWER4_PME_PM_BR_MPRED_TA 203
#define POWER4_PME_PM_CRQ_FULL_CYC 204
#define POWER4_PME_PM_INST_FROM_PREF 205
#define POWER4_PME_PM_LD_MISS_L1 206
#define POWER4_PME_PM_STCX_PASS 207
#define POWER4_PME_PM_DC_INV_L2 208
#define POWER4_PME_PM_LSU_SRQ_FULL_CYC 209
#define POWER4_PME_PM_LSU0_FLUSH_LRQ 210
#define POWER4_PME_PM_LSU_SRQ_S0_VALID 211
#define POWER4_PME_PM_LARX_LSU0 212
#define POWER4_PME_PM_GCT_EMPTY_CYC 213
#define POWER4_PME_PM_FPU1_ALL 214
#define POWER4_PME_PM_FPU1_FSQRT 215
#define POWER4_PME_PM_FPU_FIN 216
#define POWER4_PME_PM_L2SA_SHR_MOD 217
#define POWER4_PME_PM_MRK_LD_MISS_L1_LSU1 218
#define POWER4_PME_PM_LSU_SRQ_STFWD 219
#define POWER4_PME_PM_FXU0_FIN 220
#define POWER4_PME_PM_MRK_FPU_FIN 221
#define POWER4_PME_PM_LSU_BUSY 222
#define POWER4_PME_PM_INST_FROM_L35 223
#define POWER4_PME_PM_FPU1_FRSP_FCONV 224
#define POWER4_PME_PM_SNOOP_TLBIE 225
#define POWER4_PME_PM_FPU0_FDIV 226
#define POWER4_PME_PM_LD_REF_L1_LSU1 227
#define POWER4_PME_PM_MRK_DATA_FROM_L275_MOD 228
#define POWER4_PME_PM_HV_CYC 229
#define POWER4_PME_PM_6INST_CLB_CYC 230
#define POWER4_PME_PM_LR_CTR_MAP_FULL_CYC 231
#define POWER4_PME_PM_L2SC_MOD_INV 232
#define POWER4_PME_PM_FPU_DENORM 233
#define POWER4_PME_PM_DATA_FROM_L275_MOD 234
#define POWER4_PME_PM_LSU1_DERAT_MISS 235
#define POWER4_PME_PM_IC_PREF_REQ 236
#define POWER4_PME_PM_MRK_LSU_FIN 237
#define POWER4_PME_PM_MRK_DATA_FROM_L3 238
#define POWER4_PME_PM_MRK_DATA_FROM_MEM 239
#define POWER4_PME_PM_LSU0_FLUSH_UST 240
#define POWER4_PME_PM_LSU_FLUSH_LRQ 241
#define POWER4_PME_PM_LSU_FLUSH_SRQ 242
#define POWER4_PME_PM_L2SC_MOD_TAG 243

static const pme_power_entry_t power4_pe[] = {
	[ POWER4_PME_PM_MRK_LSU_SRQ_INST_VALID ] = {
		.pme_name = "PM_MRK_LSU_SRQ_INST_VALID",
		.pme_code = 0x933,
		.pme_short_desc = "Marked instruction valid in SRQ",
		.pme_long_desc = "This signal is asserted every cycle when a marked request is resident in the Store Request Queue",
	},
	[ POWER4_PME_PM_FPU1_SINGLE ] = {
		.pme_name = "PM_FPU1_SINGLE",
		.pme_code = 0x127,
		.pme_short_desc = "FPU1 executed single precision instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing single precision instruction.",
	},
	[ POWER4_PME_PM_DC_PREF_OUT_STREAMS ] = {
		.pme_name = "PM_DC_PREF_OUT_STREAMS",
		.pme_code = 0xc36,
		.pme_short_desc = "Out of prefetch streams",
		.pme_long_desc = "A new prefetch stream was detected, but no more stream entries were available",
	},
	[ POWER4_PME_PM_FPU0_STALL3 ] = {
		.pme_name = "PM_FPU0_STALL3",
		.pme_code = 0x121,
		.pme_short_desc = "FPU0 stalled in pipe3",
		.pme_long_desc = "This signal indicates that fp0 has generated a stall in pipe3 due to overflow, underflow, massive cancel, convert to integer (sometimes), or convert from integer (always). This signal is active during the entire duration of the stall. ",
	},
	[ POWER4_PME_PM_TB_BIT_TRANS ] = {
		.pme_name = "PM_TB_BIT_TRANS",
		.pme_code = 0x8005,
		.pme_short_desc = "Time Base bit transition",
		.pme_long_desc = "When the selected time base bit (as specified in MMCR0[TBSEL])transitions from 0 to 1 ",
	},
	[ POWER4_PME_PM_GPR_MAP_FULL_CYC ] = {
		.pme_name = "PM_GPR_MAP_FULL_CYC",
		.pme_code = 0x235,
		.pme_short_desc = "Cycles GPR mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the gpr mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ POWER4_PME_PM_MRK_ST_CMPL ] = {
		.pme_name = "PM_MRK_ST_CMPL",
		.pme_code = 0x1003,
		.pme_short_desc = "Marked store instruction completed",
		.pme_long_desc = "A sampled store has completed (data home)",
	},
	[ POWER4_PME_PM_MRK_LSU_FLUSH_LRQ ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_LRQ",
		.pme_code = 0x3910,
		.pme_short_desc = "Marked LRQ flushes",
		.pme_long_desc = "A marked load was flushed because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER4_PME_PM_FPU0_STF ] = {
		.pme_name = "PM_FPU0_STF",
		.pme_code = 0x122,
		.pme_short_desc = "FPU0 executed store instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing a store instruction.",
	},
	[ POWER4_PME_PM_FPU1_FMA ] = {
		.pme_name = "PM_FPU1_FMA",
		.pme_code = 0x105,
		.pme_short_desc = "FPU1 executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER4_PME_PM_L2SA_MOD_TAG ] = {
		.pme_name = "PM_L2SA_MOD_TAG",
		.pme_code = 0xf06,
		.pme_short_desc = "L2 slice A transition from modified to tagged",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from the Modified state to the Tagged state. This transition was caused by a read snoop request that hit against a modified entry in the local L2. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L275_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L275_SHR",
		.pme_code = 0x6c76,
		.pme_short_desc = "Marked data loaded from L2.75 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T) data from the L2 of another MCM due to a marked demand load",
	},
	[ POWER4_PME_PM_1INST_CLB_CYC ] = {
		.pme_name = "PM_1INST_CLB_CYC",
		.pme_code = 0x450,
		.pme_short_desc = "Cycles 1 instruction in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_LSU1_FLUSH_ULD ] = {
		.pme_name = "PM_LSU1_FLUSH_ULD",
		.pme_code = 0xc04,
		.pme_short_desc = "LSU1 unaligned load flushes",
		.pme_long_desc = "A load was flushed from unit 1 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER4_PME_PM_MRK_INST_FIN ] = {
		.pme_name = "PM_MRK_INST_FIN",
		.pme_code = 0x7005,
		.pme_short_desc = "Marked instruction finished",
		.pme_long_desc = "One of the execution units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER4_PME_PM_MRK_LSU0_FLUSH_UST ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_UST",
		.pme_code = 0x911,
		.pme_short_desc = "LSU0 marked unaligned store flushes",
		.pme_long_desc = "A marked store was flushed from unit 0 because it was unaligned",
	},
	[ POWER4_PME_PM_FPU_FDIV ] = {
		.pme_name = "PM_FPU_FDIV",
		.pme_code = 0x1100,
		.pme_short_desc = "FPU executed FDIV instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when FPU is executing a divide instruction. This could be fdiv, fdivs, fdiv. fdivs. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_LSU_LRQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_LRQ_S0_ALLOC",
		.pme_code = 0xc26,
		.pme_short_desc = "LRQ slot 0 allocated",
		.pme_long_desc = "LRQ slot zero was allocated",
	},
	[ POWER4_PME_PM_FPU0_FULL_CYC ] = {
		.pme_name = "PM_FPU0_FULL_CYC",
		.pme_code = 0x203,
		.pme_short_desc = "Cycles FPU0 issue queue full",
		.pme_long_desc = "The issue queue for FPU unit 0 cannot accept any more instructions. Issue is stopped",
	},
	[ POWER4_PME_PM_FPU_SINGLE ] = {
		.pme_name = "PM_FPU_SINGLE",
		.pme_code = 0x5120,
		.pme_short_desc = "FPU executed single precision instruction",
		.pme_long_desc = "FPU is executing single precision instruction. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_FPU0_FMA ] = {
		.pme_name = "PM_FPU0_FMA",
		.pme_code = 0x101,
		.pme_short_desc = "FPU0 executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER4_PME_PM_MRK_LSU1_FLUSH_ULD ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_ULD",
		.pme_code = 0x914,
		.pme_short_desc = "LSU1 marked unaligned load flushes",
		.pme_long_desc = "A marked load was flushed from unit 1 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER4_PME_PM_LSU1_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU1_FLUSH_LRQ",
		.pme_code = 0xc06,
		.pme_short_desc = "LSU1 LRQ flushes",
		.pme_long_desc = "A load was flushed by unit 1 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER4_PME_PM_L2SA_ST_HIT ] = {
		.pme_name = "PM_L2SA_ST_HIT",
		.pme_code = 0xf11,
		.pme_short_desc = "L2 slice A store hits",
		.pme_long_desc = "A store request made from the core hit in the L2 directory.  This event is provided on each of the three L2 slices A,B, and C.",
	},
	[ POWER4_PME_PM_L2SB_SHR_INV ] = {
		.pme_name = "PM_L2SB_SHR_INV",
		.pme_code = 0xf21,
		.pme_short_desc = "L2 slice B transition from shared to invalid",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from Shared (Shared, Shared L, or Tagged) to the Invalid state. This transition was caused by any external snoop request. The event is provided on each of the three slices A,B,and C. NOTE: For this event to be useful the tablewalk duration event should also be counted.",
	},
	[ POWER4_PME_PM_DTLB_MISS ] = {
		.pme_name = "PM_DTLB_MISS",
		.pme_code = 0x904,
		.pme_short_desc = "Data TLB misses",
		.pme_long_desc = "A TLB miss for a data request occurred. Requests that miss the TLB may be retried until the instruction is in the next to complete group (unless HID4 is set to allow speculative tablewalks). This may result in multiple TLB misses for the same instruction.",
	},
	[ POWER4_PME_PM_MRK_ST_MISS_L1 ] = {
		.pme_name = "PM_MRK_ST_MISS_L1",
		.pme_code = 0x923,
		.pme_short_desc = "Marked L1 D cache store misses",
		.pme_long_desc = "A marked store missed the dcache",
	},
	[ POWER4_PME_PM_EXT_INT ] = {
		.pme_name = "PM_EXT_INT",
		.pme_code = 0x8002,
		.pme_short_desc = "External interrupts",
		.pme_long_desc = "An external interrupt occurred",
	},
	[ POWER4_PME_PM_MRK_LSU1_FLUSH_LRQ ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_LRQ",
		.pme_code = 0x916,
		.pme_short_desc = "LSU1 marked LRQ flushes",
		.pme_long_desc = "A marked load was flushed by unit 1 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER4_PME_PM_MRK_ST_GPS ] = {
		.pme_name = "PM_MRK_ST_GPS",
		.pme_code = 0x6003,
		.pme_short_desc = "Marked store sent to GPS",
		.pme_long_desc = "A sampled store has been sent to the memory subsystem",
	},
	[ POWER4_PME_PM_GRP_DISP_SUCCESS ] = {
		.pme_name = "PM_GRP_DISP_SUCCESS",
		.pme_code = 0x5001,
		.pme_short_desc = "Group dispatch success",
		.pme_long_desc = "Number of groups successfully dispatched (not rejected)",
	},
	[ POWER4_PME_PM_LSU1_LDF ] = {
		.pme_name = "PM_LSU1_LDF",
		.pme_code = 0x934,
		.pme_short_desc = "LSU1 executed Floating Point load instruction",
		.pme_long_desc = "A floating point load was executed from LSU unit 1",
	},
	[ POWER4_PME_PM_FAB_CMD_ISSUED ] = {
		.pme_name = "PM_FAB_CMD_ISSUED",
		.pme_code = 0xf16,
		.pme_short_desc = "Fabric command issued",
		.pme_long_desc = "A bus command was issued on the MCM to MCM fabric from the local (this chip's) Fabric Bus Controller.  This event is scaled to the fabric frequency and must be adjusted for a true count.  i.e. if the fabric is running 2:1, divide the count by 2.",
	},
	[ POWER4_PME_PM_LSU0_SRQ_STFWD ] = {
		.pme_name = "PM_LSU0_SRQ_STFWD",
		.pme_code = 0xc20,
		.pme_short_desc = "LSU0 SRQ store forwarded",
		.pme_long_desc = "Data from a store instruction was forwarded to a load on unit 0",
	},
	[ POWER4_PME_PM_CR_MAP_FULL_CYC ] = {
		.pme_name = "PM_CR_MAP_FULL_CYC",
		.pme_code = 0x204,
		.pme_short_desc = "Cycles CR logical operation mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the cr mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ POWER4_PME_PM_MRK_LSU0_FLUSH_ULD ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_ULD",
		.pme_code = 0x910,
		.pme_short_desc = "LSU0 marked unaligned load flushes",
		.pme_long_desc = "A marked load was flushed from unit 0 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER4_PME_PM_LSU_DERAT_MISS ] = {
		.pme_name = "PM_LSU_DERAT_MISS",
		.pme_code = 0x6900,
		.pme_short_desc = "DERAT misses",
		.pme_long_desc = "Total D-ERAT Misses (Unit 0 + Unit 1). Requests that miss the Derat are rejected and retried until the request hits in the Erat. This may result in multiple erat misses for the same instruction.",
	},
	[ POWER4_PME_PM_FPU0_SINGLE ] = {
		.pme_name = "PM_FPU0_SINGLE",
		.pme_code = 0x123,
		.pme_short_desc = "FPU0 executed single precision instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing single precision instruction.",
	},
	[ POWER4_PME_PM_FPU1_FDIV ] = {
		.pme_name = "PM_FPU1_FDIV",
		.pme_code = 0x104,
		.pme_short_desc = "FPU1 executed FDIV instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp1 is executing a divide instruction. This could be fdiv, fdivs, fdiv. fdivs.",
	},
	[ POWER4_PME_PM_FPU1_FEST ] = {
		.pme_name = "PM_FPU1_FEST",
		.pme_code = 0x116,
		.pme_short_desc = "FPU1 executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. ",
	},
	[ POWER4_PME_PM_FPU0_FRSP_FCONV ] = {
		.pme_name = "PM_FPU0_FRSP_FCONV",
		.pme_code = 0x111,
		.pme_short_desc = "FPU0 executed FRSP or FCONV instructions",
		.pme_long_desc = "fThis signal is active for one cycle when fp0 is executing frsp or convert kind of instruction. This could be frsp*, fcfid*, fcti* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER4_PME_PM_MRK_ST_CMPL_INT ] = {
		.pme_name = "PM_MRK_ST_CMPL_INT",
		.pme_code = 0x3003,
		.pme_short_desc = "Marked store completed with intervention",
		.pme_long_desc = "A marked store previously sent to the memory subsystem completed (data home) after requiring intervention",
	},
	[ POWER4_PME_PM_FXU_FIN ] = {
		.pme_name = "PM_FXU_FIN",
		.pme_code = 0x3230,
		.pme_short_desc = "FXU produced a result",
		.pme_long_desc = "The fixed point unit (Unit 0 + Unit 1) finished a marked instruction. Instructions that finish may not necessary complete.",
	},
	[ POWER4_PME_PM_FPU_STF ] = {
		.pme_name = "PM_FPU_STF",
		.pme_code = 0x6120,
		.pme_short_desc = "FPU executed store instruction",
		.pme_long_desc = "FPU is executing a store instruction. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_DSLB_MISS ] = {
		.pme_name = "PM_DSLB_MISS",
		.pme_code = 0x905,
		.pme_short_desc = "Data SLB misses",
		.pme_long_desc = "A SLB miss for a data request occurred. SLB misses trap to the operating system to resolve",
	},
	[ POWER4_PME_PM_DATA_FROM_L275_SHR ] = {
		.pme_name = "PM_DATA_FROM_L275_SHR",
		.pme_code = 0x6c66,
		.pme_short_desc = "Data loaded from L2.75 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T) data from the L2 of another MCM due to a demand load",
	},
	[ POWER4_PME_PM_FXLS1_FULL_CYC ] = {
		.pme_name = "PM_FXLS1_FULL_CYC",
		.pme_code = 0x214,
		.pme_short_desc = "Cycles FXU1/LS1 queue full",
		.pme_long_desc = "The issue queue for FXU/LSU unit 1 cannot accept any more instructions. Issue is stopped",
	},
	[ POWER4_PME_PM_L3B0_DIR_MIS ] = {
		.pme_name = "PM_L3B0_DIR_MIS",
		.pme_code = 0xf01,
		.pme_short_desc = "L3 bank 0 directory misses",
		.pme_long_desc = "A reference was made to the local L3 directory by a local CPU and it missed in the L3. Only requests from on-MCM CPUs are counted. This event is scaled to the L3 speed and the count must be scaled. i.e. if the L3 is running 3:1, divide the count by 3",
	},
	[ POWER4_PME_PM_2INST_CLB_CYC ] = {
		.pme_name = "PM_2INST_CLB_CYC",
		.pme_code = 0x451,
		.pme_short_desc = "Cycles 2 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_MRK_STCX_FAIL ] = {
		.pme_name = "PM_MRK_STCX_FAIL",
		.pme_code = 0x925,
		.pme_short_desc = "Marked STCX failed",
		.pme_long_desc = "A marked stcx (stwcx or stdcx) failed",
	},
	[ POWER4_PME_PM_LSU_LMQ_LHR_MERGE ] = {
		.pme_name = "PM_LSU_LMQ_LHR_MERGE",
		.pme_code = 0x926,
		.pme_short_desc = "LMQ LHR merges",
		.pme_long_desc = "A dcache miss occurred for the same real cache line address as an earlier request already in the Load Miss Queue and was merged into the LMQ entry.",
	},
	[ POWER4_PME_PM_FXU0_BUSY_FXU1_IDLE ] = {
		.pme_name = "PM_FXU0_BUSY_FXU1_IDLE",
		.pme_code = 0x7002,
		.pme_short_desc = "FXU0 busy FXU1 idle",
		.pme_long_desc = "FXU0 is busy while FXU1 was idle",
	},
	[ POWER4_PME_PM_L3B1_DIR_REF ] = {
		.pme_name = "PM_L3B1_DIR_REF",
		.pme_code = 0xf02,
		.pme_short_desc = "L3 bank 1 directory references",
		.pme_long_desc = "A reference was made to the local L3 directory by a local CPU. Only requests from on-MCM CPUs are counted. This event is scaled to the L3 speed and the count must be scaled. i.e. if the L3 is running 3:1, divide the count by 3",
	},
	[ POWER4_PME_PM_MRK_LSU_FLUSH_UST ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_UST",
		.pme_code = 0x7910,
		.pme_short_desc = "Marked unaligned store flushes",
		.pme_long_desc = "A marked store was flushed because it was unaligned",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L25_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L25_SHR",
		.pme_code = 0x5c76,
		.pme_short_desc = "Marked data loaded from L2.5 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T or SL) data from the L2 of a chip on this MCM due to a marked demand load",
	},
	[ POWER4_PME_PM_LSU_FLUSH_ULD ] = {
		.pme_name = "PM_LSU_FLUSH_ULD",
		.pme_code = 0x1c00,
		.pme_short_desc = "LRQ unaligned load flushes",
		.pme_long_desc = "A load was flushed because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER4_PME_PM_MRK_BRU_FIN ] = {
		.pme_name = "PM_MRK_BRU_FIN",
		.pme_code = 0x2005,
		.pme_short_desc = "Marked instruction BRU processing finished",
		.pme_long_desc = "The branch unit finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER4_PME_PM_IERAT_XLATE_WR ] = {
		.pme_name = "PM_IERAT_XLATE_WR",
		.pme_code = 0x327,
		.pme_short_desc = "Translation written to ierat",
		.pme_long_desc = "This signal will be asserted each time the I-ERAT is written. This indicates that an ERAT miss has been serviced. ERAT misses will initiate a sequence resulting in the ERAT being written. ERAT misses that are later ignored will not be counted unless the ERAT is written before the instruction stream is changed, This should be a fairly accurate count of ERAT missed (best available).",
	},
	[ POWER4_PME_PM_LSU0_BUSY ] = {
		.pme_name = "PM_LSU0_BUSY",
		.pme_code = 0xc33,
		.pme_short_desc = "LSU0 busy",
		.pme_long_desc = "LSU unit 0 is busy rejecting instructions",
	},
	[ POWER4_PME_PM_L2SA_ST_REQ ] = {
		.pme_name = "PM_L2SA_ST_REQ",
		.pme_code = 0xf10,
		.pme_short_desc = "L2 slice A store requests",
		.pme_long_desc = "A store request as seen at the L2 directory has been made from the core. Stores are counted after gathering in the L2 store queues. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_DATA_FROM_MEM ] = {
		.pme_name = "PM_DATA_FROM_MEM",
		.pme_code = 0x2c66,
		.pme_short_desc = "Data loaded from memory",
		.pme_long_desc = "DL1 was reloaded from memory due to a demand load",
	},
	[ POWER4_PME_PM_FPR_MAP_FULL_CYC ] = {
		.pme_name = "PM_FPR_MAP_FULL_CYC",
		.pme_code = 0x201,
		.pme_short_desc = "Cycles FPR mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the FPR mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ POWER4_PME_PM_FPU1_FULL_CYC ] = {
		.pme_name = "PM_FPU1_FULL_CYC",
		.pme_code = 0x207,
		.pme_short_desc = "Cycles FPU1 issue queue full",
		.pme_long_desc = "The issue queue for FPU unit 1 cannot accept any more instructions. Issue is stopped",
	},
	[ POWER4_PME_PM_FPU0_FIN ] = {
		.pme_name = "PM_FPU0_FIN",
		.pme_code = 0x113,
		.pme_short_desc = "FPU0 produced a result",
		.pme_long_desc = "fp0 finished, produced a result This only indicates finish, not completion. ",
	},
	[ POWER4_PME_PM_3INST_CLB_CYC ] = {
		.pme_name = "PM_3INST_CLB_CYC",
		.pme_code = 0x452,
		.pme_short_desc = "Cycles 3 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_DATA_FROM_L35 ] = {
		.pme_name = "PM_DATA_FROM_L35",
		.pme_code = 0x3c66,
		.pme_short_desc = "Data loaded from L3.5",
		.pme_long_desc = "DL1 was reloaded from the L3 of another MCM due to a demand load",
	},
	[ POWER4_PME_PM_L2SA_SHR_INV ] = {
		.pme_name = "PM_L2SA_SHR_INV",
		.pme_code = 0xf05,
		.pme_short_desc = "L2 slice A transition from shared to invalid",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from Shared (Shared, Shared L, or Tagged) to the Invalid state. This transition was caused by any external snoop request. The event is provided on each of the three slices A,B,and C. NOTE: For this event to be useful the tablewalk duration event should also be counted.",
	},
	[ POWER4_PME_PM_MRK_LSU_FLUSH_SRQ ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_SRQ",
		.pme_code = 0x4910,
		.pme_short_desc = "Marked SRQ flushes",
		.pme_long_desc = "A marked store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ POWER4_PME_PM_THRESH_TIMEO ] = {
		.pme_name = "PM_THRESH_TIMEO",
		.pme_code = 0x2003,
		.pme_short_desc = "Threshold timeout",
		.pme_long_desc = "The threshold timer expired",
	},
	[ POWER4_PME_PM_FPU_FSQRT ] = {
		.pme_name = "PM_FPU_FSQRT",
		.pme_code = 0x6100,
		.pme_short_desc = "FPU executed FSQRT instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when FPU is executing a square root instruction. This could be fsqrt* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_MRK_LSU0_FLUSH_LRQ ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_LRQ",
		.pme_code = 0x912,
		.pme_short_desc = "LSU0 marked LRQ flushes",
		.pme_long_desc = "A marked load was flushed by unit 0 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER4_PME_PM_FXLS0_FULL_CYC ] = {
		.pme_name = "PM_FXLS0_FULL_CYC",
		.pme_code = 0x210,
		.pme_short_desc = "Cycles FXU0/LS0 queue full",
		.pme_long_desc = "The issue queue for FXU/LSU unit 0 cannot accept any more instructions. Issue is stopped",
	},
	[ POWER4_PME_PM_DATA_TABLEWALK_CYC ] = {
		.pme_name = "PM_DATA_TABLEWALK_CYC",
		.pme_code = 0x936,
		.pme_short_desc = "Cycles doing data tablewalks",
		.pme_long_desc = "This signal is asserted every cycle when a tablewalk is active. While a tablewalk is active any request attempting to access the TLB will be rejected and retried.",
	},
	[ POWER4_PME_PM_FPU0_ALL ] = {
		.pme_name = "PM_FPU0_ALL",
		.pme_code = 0x103,
		.pme_short_desc = "FPU0 executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo",
	},
	[ POWER4_PME_PM_FPU0_FEST ] = {
		.pme_name = "PM_FPU0_FEST",
		.pme_code = 0x112,
		.pme_short_desc = "FPU0 executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. ",
	},
	[ POWER4_PME_PM_DATA_FROM_L25_MOD ] = {
		.pme_name = "PM_DATA_FROM_L25_MOD",
		.pme_code = 0x8c66,
		.pme_short_desc = "Data loaded from L2.5 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of a chip on this MCM due to a demand load",
	},
	[ POWER4_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_CYC",
		.pme_code = 0x2002,
		.pme_short_desc = "Cycles LMQ and SRQ empty",
		.pme_long_desc = "Cycles when both the LMQ and SRQ are empty (LSU is idle)",
	},
	[ POWER4_PME_PM_FPU_FEST ] = {
		.pme_name = "PM_FPU_FEST",
		.pme_code = 0x3110,
		.pme_short_desc = "FPU executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. Combined Unit 0 + Unit 1.",
	},
	[ POWER4_PME_PM_0INST_FETCH ] = {
		.pme_name = "PM_0INST_FETCH",
		.pme_code = 0x8327,
		.pme_short_desc = "No instructions fetched",
		.pme_long_desc = "No instructions were fetched this cycles (due to IFU hold, redirect, or icache miss)",
	},
	[ POWER4_PME_PM_LARX_LSU1 ] = {
		.pme_name = "PM_LARX_LSU1",
		.pme_code = 0xc77,
		.pme_short_desc = "Larx executed on LSU1",
		.pme_long_desc = "Invalid event, larx instructions are never executed on unit 1",
	},
	[ POWER4_PME_PM_LD_MISS_L1_LSU0 ] = {
		.pme_name = "PM_LD_MISS_L1_LSU0",
		.pme_code = 0xc12,
		.pme_short_desc = "LSU0 L1 D cache load misses",
		.pme_long_desc = "A load, executing on unit 0, missed the dcache",
	},
	[ POWER4_PME_PM_L1_PREF ] = {
		.pme_name = "PM_L1_PREF",
		.pme_code = 0xc35,
		.pme_short_desc = "L1 cache data prefetches",
		.pme_long_desc = "A request to prefetch data into the L1 was made",
	},
	[ POWER4_PME_PM_FPU1_STALL3 ] = {
		.pme_name = "PM_FPU1_STALL3",
		.pme_code = 0x125,
		.pme_short_desc = "FPU1 stalled in pipe3",
		.pme_long_desc = "This signal indicates that fp1 has generated a stall in pipe3 due to overflow, underflow, massive cancel, convert to integer (sometimes), or convert from integer (always). This signal is active during the entire duration of the stall. ",
	},
	[ POWER4_PME_PM_BRQ_FULL_CYC ] = {
		.pme_name = "PM_BRQ_FULL_CYC",
		.pme_code = 0x205,
		.pme_short_desc = "Cycles branch queue full",
		.pme_long_desc = "The ISU sends a signal indicating that the issue queue that feeds the ifu br unit cannot accept any more group (queue is full of groups).",
	},
	[ POWER4_PME_PM_LARX ] = {
		.pme_name = "PM_LARX",
		.pme_code = 0x4c70,
		.pme_short_desc = "Larx executed",
		.pme_long_desc = "A Larx (lwarx or ldarx) was executed. This is the combined count from LSU0 + LSU1, but these instructions only execute on LSU0",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L35 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L35",
		.pme_code = 0x3c76,
		.pme_short_desc = "Marked data loaded from L3.5",
		.pme_long_desc = "DL1 was reloaded from the L3 of another MCM due to a marked demand load",
	},
	[ POWER4_PME_PM_WORK_HELD ] = {
		.pme_name = "PM_WORK_HELD",
		.pme_code = 0x2001,
		.pme_short_desc = "Work held",
		.pme_long_desc = "RAS Unit has signaled completion to stop and there are groups waiting to complete",
	},
	[ POWER4_PME_PM_MRK_LD_MISS_L1_LSU0 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1_LSU0",
		.pme_code = 0x920,
		.pme_short_desc = "LSU0 L1 D cache load misses",
		.pme_long_desc = "A marked load, executing on unit 0, missed the dcache",
	},
	[ POWER4_PME_PM_FXU_IDLE ] = {
		.pme_name = "PM_FXU_IDLE",
		.pme_code = 0x5002,
		.pme_short_desc = "FXU idle",
		.pme_long_desc = "FXU0 and FXU1 are both idle",
	},
	[ POWER4_PME_PM_INST_CMPL ] = {
		.pme_name = "PM_INST_CMPL",
		.pme_code = 0x8001,
		.pme_short_desc = "Instructions completed",
		.pme_long_desc = "Number of Eligible Instructions that completed. ",
	},
	[ POWER4_PME_PM_LSU1_FLUSH_UST ] = {
		.pme_name = "PM_LSU1_FLUSH_UST",
		.pme_code = 0xc05,
		.pme_short_desc = "LSU1 unaligned store flushes",
		.pme_long_desc = "A store was flushed from unit 1 because it was unaligned (crossed a 4k boundary)",
	},
	[ POWER4_PME_PM_LSU0_FLUSH_ULD ] = {
		.pme_name = "PM_LSU0_FLUSH_ULD",
		.pme_code = 0xc00,
		.pme_short_desc = "LSU0 unaligned load flushes",
		.pme_long_desc = "A load was flushed from unit 0 because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER4_PME_PM_INST_FROM_L2 ] = {
		.pme_name = "PM_INST_FROM_L2",
		.pme_code = 0x3327,
		.pme_short_desc = "Instructions fetched from L2",
		.pme_long_desc = "An instruction fetch group was fetched from L2. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_DATA_FROM_L3 ] = {
		.pme_name = "PM_DATA_FROM_L3",
		.pme_code = 0x1c66,
		.pme_short_desc = "Data loaded from L3",
		.pme_long_desc = "DL1 was reloaded from the local L3 due to a demand load",
	},
	[ POWER4_PME_PM_FPU0_DENORM ] = {
		.pme_name = "PM_FPU0_DENORM",
		.pme_code = 0x120,
		.pme_short_desc = "FPU0 received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized.",
	},
	[ POWER4_PME_PM_FPU1_FMOV_FEST ] = {
		.pme_name = "PM_FPU1_FMOV_FEST",
		.pme_code = 0x114,
		.pme_short_desc = "FPU1 executing FMOV or FEST instructions",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing a move kind of instruction or one of the estimate instructions.. This could be fmr*, fneg*, fabs*, fnabs* , fres* or frsqrte* where XYZ* means XYZ or XYZ",
	},
	[ POWER4_PME_PM_GRP_DISP_REJECT ] = {
		.pme_name = "PM_GRP_DISP_REJECT",
		.pme_code = 0x8003,
		.pme_short_desc = "Group dispatch rejected",
		.pme_long_desc = "A group that previously attempted dispatch was rejected.",
	},
	[ POWER4_PME_PM_INST_FETCH_CYC ] = {
		.pme_name = "PM_INST_FETCH_CYC",
		.pme_code = 0x323,
		.pme_short_desc = "Cycles at least 1 instruction fetched",
		.pme_long_desc = "Asserted each cycle when the IFU sends at least one instruction to the IDU. ",
	},
	[ POWER4_PME_PM_LSU_LDF ] = {
		.pme_name = "PM_LSU_LDF",
		.pme_code = 0x8930,
		.pme_short_desc = "LSU executed Floating Point load instruction",
		.pme_long_desc = "LSU executed Floating Point load instruction",
	},
	[ POWER4_PME_PM_INST_DISP ] = {
		.pme_name = "PM_INST_DISP",
		.pme_code = 0x221,
		.pme_short_desc = "Instructions dispatched",
		.pme_long_desc = "The ISU sends the number of instructions dispatched.",
	},
	[ POWER4_PME_PM_L2SA_MOD_INV ] = {
		.pme_name = "PM_L2SA_MOD_INV",
		.pme_code = 0xf07,
		.pme_short_desc = "L2 slice A transition from modified to invalid",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from the Modified state to the Invalid state. This transition was caused by any RWITM snoop request that hit against a modified entry in the local L2. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_DATA_FROM_L25_SHR ] = {
		.pme_name = "PM_DATA_FROM_L25_SHR",
		.pme_code = 0x5c66,
		.pme_short_desc = "Data loaded from L2.5 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T or SL) data from the L2 of a chip on this MCM due to a demand load",
	},
	[ POWER4_PME_PM_FAB_CMD_RETRIED ] = {
		.pme_name = "PM_FAB_CMD_RETRIED",
		.pme_code = 0xf17,
		.pme_short_desc = "Fabric command retried",
		.pme_long_desc = "A bus command on the MCM to MCM fabric was retried.  This event is the total count of all retried fabric commands for the local MCM (all four chips report the same value).  This event is scaled to the fabric frequency and must be adjusted for a true count.  i.e. if the fabric is running 2:1, divide the count by 2.",
	},
	[ POWER4_PME_PM_L1_DCACHE_RELOAD_VALID ] = {
		.pme_name = "PM_L1_DCACHE_RELOAD_VALID",
		.pme_code = 0xc64,
		.pme_short_desc = "L1 reload data source valid",
		.pme_long_desc = "The data source information is valid",
	},
	[ POWER4_PME_PM_MRK_GRP_ISSUED ] = {
		.pme_name = "PM_MRK_GRP_ISSUED",
		.pme_code = 0x6005,
		.pme_short_desc = "Marked group issued",
		.pme_long_desc = "A sampled instruction was issued",
	},
	[ POWER4_PME_PM_FPU_FULL_CYC ] = {
		.pme_name = "PM_FPU_FULL_CYC",
		.pme_code = 0x5200,
		.pme_short_desc = "Cycles FPU issue queue full",
		.pme_long_desc = "Cycles when one or both FPU issue queues are full",
	},
	[ POWER4_PME_PM_FPU_FMA ] = {
		.pme_name = "PM_FPU_FMA",
		.pme_code = 0x2100,
		.pme_short_desc = "FPU executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when FPU is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_MRK_CRU_FIN ] = {
		.pme_name = "PM_MRK_CRU_FIN",
		.pme_code = 0x4005,
		.pme_short_desc = "Marked instruction CRU processing finished",
		.pme_long_desc = "The Condition Register Unit finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER4_PME_PM_MRK_LSU1_FLUSH_UST ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_UST",
		.pme_code = 0x915,
		.pme_short_desc = "LSU1 marked unaligned store flushes",
		.pme_long_desc = "A marked store was flushed from unit 1 because it was unaligned (crossed a 4k boundary)",
	},
	[ POWER4_PME_PM_MRK_FXU_FIN ] = {
		.pme_name = "PM_MRK_FXU_FIN",
		.pme_code = 0x6004,
		.pme_short_desc = "Marked instruction FXU processing finished",
		.pme_long_desc = "One of the Fixed Point Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER4_PME_PM_BR_ISSUED ] = {
		.pme_name = "PM_BR_ISSUED",
		.pme_code = 0x330,
		.pme_short_desc = "Branches issued",
		.pme_long_desc = "This signal will be asserted each time the ISU issues a branch instruction. This signal will be asserted each time the ISU selects a branch instruction to issue.",
	},
	[ POWER4_PME_PM_EE_OFF ] = {
		.pme_name = "PM_EE_OFF",
		.pme_code = 0x233,
		.pme_short_desc = "Cycles MSR(EE) bit off",
		.pme_long_desc = "The number of Cycles MSR(EE) bit was off.",
	},
	[ POWER4_PME_PM_INST_FROM_L3 ] = {
		.pme_name = "PM_INST_FROM_L3",
		.pme_code = 0x5327,
		.pme_short_desc = "Instruction fetched from L3",
		.pme_long_desc = "An instruction fetch group was fetched from L3. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_ITLB_MISS ] = {
		.pme_name = "PM_ITLB_MISS",
		.pme_code = 0x900,
		.pme_short_desc = "Instruction TLB misses",
		.pme_long_desc = "A TLB miss for an Instruction Fetch has occurred",
	},
	[ POWER4_PME_PM_FXLS_FULL_CYC ] = {
		.pme_name = "PM_FXLS_FULL_CYC",
		.pme_code = 0x8210,
		.pme_short_desc = "Cycles FXLS queue is full",
		.pme_long_desc = "Cycles when one or both FXU/LSU issue queue are full",
	},
	[ POWER4_PME_PM_FXU1_BUSY_FXU0_IDLE ] = {
		.pme_name = "PM_FXU1_BUSY_FXU0_IDLE",
		.pme_code = 0x4002,
		.pme_short_desc = "FXU1 busy FXU0 idle",
		.pme_long_desc = "FXU0 was idle while FXU1 was busy",
	},
	[ POWER4_PME_PM_GRP_DISP_VALID ] = {
		.pme_name = "PM_GRP_DISP_VALID",
		.pme_code = 0x223,
		.pme_short_desc = "Group dispatch valid",
		.pme_long_desc = "Dispatch has been attempted for a valid group.  Some groups may be rejected.  The total number of successful dispatches is the number of dispatch valid minus dispatch reject.",
	},
	[ POWER4_PME_PM_L2SC_ST_HIT ] = {
		.pme_name = "PM_L2SC_ST_HIT",
		.pme_code = 0xf15,
		.pme_short_desc = "L2 slice C store hits",
		.pme_long_desc = "A store request made from the core hit in the L2 directory.  This event is provided on each of the three L2 slices A,B, and C.",
	},
	[ POWER4_PME_PM_MRK_GRP_DISP ] = {
		.pme_name = "PM_MRK_GRP_DISP",
		.pme_code = 0x1002,
		.pme_short_desc = "Marked group dispatched",
		.pme_long_desc = "A group containing a sampled instruction was dispatched",
	},
	[ POWER4_PME_PM_L2SB_MOD_TAG ] = {
		.pme_name = "PM_L2SB_MOD_TAG",
		.pme_code = 0xf22,
		.pme_short_desc = "L2 slice B transition from modified to tagged",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from the Modified state to the Tagged state. This transition was caused by a read snoop request that hit against a modified entry in the local L2. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_INST_FROM_L25_L275 ] = {
		.pme_name = "PM_INST_FROM_L25_L275",
		.pme_code = 0x2327,
		.pme_short_desc = "Instruction fetched from L2.5/L2.75",
		.pme_long_desc = "An instruction fetch group was fetched from the L2 of another chip. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_LSU_FLUSH_UST ] = {
		.pme_name = "PM_LSU_FLUSH_UST",
		.pme_code = 0x2c00,
		.pme_short_desc = "SRQ unaligned store flushes",
		.pme_long_desc = "A store was flushed because it was unaligned",
	},
	[ POWER4_PME_PM_L2SB_ST_HIT ] = {
		.pme_name = "PM_L2SB_ST_HIT",
		.pme_code = 0xf13,
		.pme_short_desc = "L2 slice B store hits",
		.pme_long_desc = "A store request made from the core hit in the L2 directory.  This event is provided on each of the three L2 slices A,B, and C.",
	},
	[ POWER4_PME_PM_FXU1_FIN ] = {
		.pme_name = "PM_FXU1_FIN",
		.pme_code = 0x236,
		.pme_short_desc = "FXU1 produced a result",
		.pme_long_desc = "The Fixed Point unit 1 finished an instruction and produced a result",
	},
	[ POWER4_PME_PM_L3B1_DIR_MIS ] = {
		.pme_name = "PM_L3B1_DIR_MIS",
		.pme_code = 0xf03,
		.pme_short_desc = "L3 bank 1 directory misses",
		.pme_long_desc = "A reference was made to the local L3 directory by a local CPU and it missed in the L3. Only requests from on-MCM CPUs are counted. This event is scaled to the L3 speed and the count must be scaled. i.e. if the L3 is running 3:1, divide the count by 3",
	},
	[ POWER4_PME_PM_4INST_CLB_CYC ] = {
		.pme_name = "PM_4INST_CLB_CYC",
		.pme_code = 0x453,
		.pme_short_desc = "Cycles 4 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_GRP_CMPL ] = {
		.pme_name = "PM_GRP_CMPL",
		.pme_code = 0x7003,
		.pme_short_desc = "Group completed",
		.pme_long_desc = "A group completed. Microcoded instructions that span multiple groups will generate this event once per group.",
	},
	[ POWER4_PME_PM_DC_PREF_L2_CLONE_L3 ] = {
		.pme_name = "PM_DC_PREF_L2_CLONE_L3",
		.pme_code = 0xc27,
		.pme_short_desc = "L2 prefetch cloned with L3",
		.pme_long_desc = "A prefetch request was made to the L2 with a cloned request sent to the L3",
	},
	[ POWER4_PME_PM_FPU_FRSP_FCONV ] = {
		.pme_name = "PM_FPU_FRSP_FCONV",
		.pme_code = 0x7110,
		.pme_short_desc = "FPU executed FRSP or FCONV instructions",
		.pme_long_desc = "This signal is active for one cycle when executing frsp or convert kind of instruction. This could be frsp*, fcfid*, fcti* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_5INST_CLB_CYC ] = {
		.pme_name = "PM_5INST_CLB_CYC",
		.pme_code = 0x454,
		.pme_short_desc = "Cycles 5 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_MRK_LSU0_FLUSH_SRQ ] = {
		.pme_name = "PM_MRK_LSU0_FLUSH_SRQ",
		.pme_code = 0x913,
		.pme_short_desc = "LSU0 marked SRQ flushes",
		.pme_long_desc = "A marked store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ POWER4_PME_PM_MRK_LSU_FLUSH_ULD ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_ULD",
		.pme_code = 0x8910,
		.pme_short_desc = "Marked unaligned load flushes",
		.pme_long_desc = "A marked load was flushed because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER4_PME_PM_8INST_CLB_CYC ] = {
		.pme_name = "PM_8INST_CLB_CYC",
		.pme_code = 0x457,
		.pme_short_desc = "Cycles 8 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_LSU_LMQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_LMQ_FULL_CYC",
		.pme_code = 0x927,
		.pme_short_desc = "Cycles LMQ full",
		.pme_long_desc = "The LMQ was full",
	},
	[ POWER4_PME_PM_ST_REF_L1_LSU0 ] = {
		.pme_name = "PM_ST_REF_L1_LSU0",
		.pme_code = 0xc11,
		.pme_short_desc = "LSU0 L1 D cache store references",
		.pme_long_desc = "A store executed on unit 0",
	},
	[ POWER4_PME_PM_LSU0_DERAT_MISS ] = {
		.pme_name = "PM_LSU0_DERAT_MISS",
		.pme_code = 0x902,
		.pme_short_desc = "LSU0 DERAT misses",
		.pme_long_desc = "A data request (load or store) from LSU Unit 0 missed the ERAT and resulted in an ERAT reload. Multiple instructions may miss the ERAT entry for the same 4K page, but only one reload will occur.",
	},
	[ POWER4_PME_PM_LSU_SRQ_SYNC_CYC ] = {
		.pme_name = "PM_LSU_SRQ_SYNC_CYC",
		.pme_code = 0x932,
		.pme_short_desc = "SRQ sync duration",
		.pme_long_desc = "This signal is asserted every cycle when a sync is in the SRQ.",
	},
	[ POWER4_PME_PM_FPU_STALL3 ] = {
		.pme_name = "PM_FPU_STALL3",
		.pme_code = 0x2120,
		.pme_short_desc = "FPU stalled in pipe3",
		.pme_long_desc = "FPU has generated a stall in pipe3 due to overflow, underflow, massive cancel, convert to integer (sometimes), or convert from integer (always). This signal is active during the entire duration of the stall. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L2 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2",
		.pme_code = 0x4c76,
		.pme_short_desc = "Marked data loaded from L2",
		.pme_long_desc = "DL1 was reloaded from the local L2 due to a marked demand load",
	},
	[ POWER4_PME_PM_FPU0_FMOV_FEST ] = {
		.pme_name = "PM_FPU0_FMOV_FEST",
		.pme_code = 0x110,
		.pme_short_desc = "FPU0 executed FMOV or FEST instructions",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing a move kind of instruction or one of the estimate instructions.. This could be fmr*, fneg*, fabs*, fnabs* , fres* or frsqrte* where XYZ* means XYZ or XYZ",
	},
	[ POWER4_PME_PM_LSU0_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU0_FLUSH_SRQ",
		.pme_code = 0xc03,
		.pme_short_desc = "LSU0 SRQ flushes",
		.pme_long_desc = "A store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ POWER4_PME_PM_LD_REF_L1_LSU0 ] = {
		.pme_name = "PM_LD_REF_L1_LSU0",
		.pme_code = 0xc10,
		.pme_short_desc = "LSU0 L1 D cache load references",
		.pme_long_desc = "A load executed on unit 0",
	},
	[ POWER4_PME_PM_L2SC_SHR_INV ] = {
		.pme_name = "PM_L2SC_SHR_INV",
		.pme_code = 0xf25,
		.pme_short_desc = "L2 slice C transition from shared to invalid",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from Shared (Shared, Shared L, or Tagged) to the Invalid state. This transition was caused by any external snoop request. The event is provided on each of the three slices A,B,and C. NOTE: For this event to be useful the tablewalk duration event should also be counted.",
	},
	[ POWER4_PME_PM_LSU1_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU1_FLUSH_SRQ",
		.pme_code = 0xc07,
		.pme_short_desc = "LSU1 SRQ flushes",
		.pme_long_desc = "A store was flushed because younger load hits and older store that is already in the SRQ or in the same group. ",
	},
	[ POWER4_PME_PM_LSU_LMQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_LMQ_S0_ALLOC",
		.pme_code = 0x935,
		.pme_short_desc = "LMQ slot 0 allocated",
		.pme_long_desc = "The first entry in the LMQ was allocated.",
	},
	[ POWER4_PME_PM_ST_REF_L1 ] = {
		.pme_name = "PM_ST_REF_L1",
		.pme_code = 0x7c10,
		.pme_short_desc = "L1 D cache store references",
		.pme_long_desc = "Total DL1 Store references",
	},
	[ POWER4_PME_PM_LSU_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_SRQ_EMPTY_CYC",
		.pme_code = 0x4003,
		.pme_short_desc = "Cycles SRQ empty",
		.pme_long_desc = "The Store Request Queue is empty",
	},
	[ POWER4_PME_PM_FPU1_STF ] = {
		.pme_name = "PM_FPU1_STF",
		.pme_code = 0x126,
		.pme_short_desc = "FPU1 executed store instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing a store instruction.",
	},
	[ POWER4_PME_PM_L3B0_DIR_REF ] = {
		.pme_name = "PM_L3B0_DIR_REF",
		.pme_code = 0xf00,
		.pme_short_desc = "L3 bank 0 directory references",
		.pme_long_desc = "A reference was made to the local L3 directory by a local CPU. Only requests from on-MCM CPUs are counted. This event is scaled to the L3 speed and the count must be scaled. i.e. if the L3 is running 3:1, divide the count by 3",
	},
	[ POWER4_PME_PM_RUN_CYC ] = {
		.pme_name = "PM_RUN_CYC",
		.pme_code = 0x1005,
		.pme_short_desc = "Run cycles",
		.pme_long_desc = "Processor Cycles gated by the run latch",
	},
	[ POWER4_PME_PM_LSU_LMQ_S0_VALID ] = {
		.pme_name = "PM_LSU_LMQ_S0_VALID",
		.pme_code = 0x931,
		.pme_short_desc = "LMQ slot 0 valid",
		.pme_long_desc = "This signal is asserted every cycle when the first entry in the LMQ is valid. The LMQ had eight entries that are allocated FIFO",
	},
	[ POWER4_PME_PM_LSU_LRQ_S0_VALID ] = {
		.pme_name = "PM_LSU_LRQ_S0_VALID",
		.pme_code = 0xc22,
		.pme_short_desc = "LRQ slot 0 valid",
		.pme_long_desc = "This signal is asserted every cycle that the Load Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.",
	},
	[ POWER4_PME_PM_LSU0_LDF ] = {
		.pme_name = "PM_LSU0_LDF",
		.pme_code = 0x930,
		.pme_short_desc = "LSU0 executed Floating Point load instruction",
		.pme_long_desc = "A floating point load was executed from LSU unit 0",
	},
	[ POWER4_PME_PM_MRK_IMR_RELOAD ] = {
		.pme_name = "PM_MRK_IMR_RELOAD",
		.pme_code = 0x922,
		.pme_short_desc = "Marked IMR reloaded",
		.pme_long_desc = "A DL1 reload occurred due to marked load",
	},
	[ POWER4_PME_PM_7INST_CLB_CYC ] = {
		.pme_name = "PM_7INST_CLB_CYC",
		.pme_code = 0x456,
		.pme_short_desc = "Cycles 7 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_MRK_GRP_TIMEO ] = {
		.pme_name = "PM_MRK_GRP_TIMEO",
		.pme_code = 0x5005,
		.pme_short_desc = "Marked group completion timeout",
		.pme_long_desc = "The sampling timeout expired indicating that the previously sampled instruction is no longer in the processor",
	},
	[ POWER4_PME_PM_FPU_FMOV_FEST ] = {
		.pme_name = "PM_FPU_FMOV_FEST",
		.pme_code = 0x8110,
		.pme_short_desc = "FPU executing FMOV or FEST instructions",
		.pme_long_desc = "This signal is active for one cycle when executing a move kind of instruction or one of the estimate instructions.. This could be fmr*, fneg*, fabs*, fnabs* , fres* or frsqrte* where XYZ* means XYZ or XYZ . Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_GRP_DISP_BLK_SB_CYC ] = {
		.pme_name = "PM_GRP_DISP_BLK_SB_CYC",
		.pme_code = 0x231,
		.pme_short_desc = "Cycles group dispatch blocked by scoreboard",
		.pme_long_desc = "The ISU sends a signal indicating that dispatch is blocked by scoreboard.",
	},
	[ POWER4_PME_PM_XER_MAP_FULL_CYC ] = {
		.pme_name = "PM_XER_MAP_FULL_CYC",
		.pme_code = 0x202,
		.pme_short_desc = "Cycles XER mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the xer mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ POWER4_PME_PM_ST_MISS_L1 ] = {
		.pme_name = "PM_ST_MISS_L1",
		.pme_code = 0xc23,
		.pme_short_desc = "L1 D cache store misses",
		.pme_long_desc = "A store missed the dcache",
	},
	[ POWER4_PME_PM_STOP_COMPLETION ] = {
		.pme_name = "PM_STOP_COMPLETION",
		.pme_code = 0x3001,
		.pme_short_desc = "Completion stopped",
		.pme_long_desc = "RAS Unit has signaled completion to stop",
	},
	[ POWER4_PME_PM_MRK_GRP_CMPL ] = {
		.pme_name = "PM_MRK_GRP_CMPL",
		.pme_code = 0x4004,
		.pme_short_desc = "Marked group completed",
		.pme_long_desc = "A group containing a sampled instruction completed. Microcoded instructions that span multiple groups will generate this event once per group.",
	},
	[ POWER4_PME_PM_ISLB_MISS ] = {
		.pme_name = "PM_ISLB_MISS",
		.pme_code = 0x901,
		.pme_short_desc = "Instruction SLB misses",
		.pme_long_desc = "A SLB miss for an instruction fetch as occurred",
	},
	[ POWER4_PME_PM_CYC ] = {
		.pme_name = "PM_CYC",
		.pme_code = 0x7,
		.pme_short_desc = "Processor cycles",
		.pme_long_desc = "Processor cycles",
	},
	[ POWER4_PME_PM_LD_MISS_L1_LSU1 ] = {
		.pme_name = "PM_LD_MISS_L1_LSU1",
		.pme_code = 0xc16,
		.pme_short_desc = "LSU1 L1 D cache load misses",
		.pme_long_desc = "A load, executing on unit 1, missed the dcache",
	},
	[ POWER4_PME_PM_STCX_FAIL ] = {
		.pme_name = "PM_STCX_FAIL",
		.pme_code = 0x921,
		.pme_short_desc = "STCX failed",
		.pme_long_desc = "A stcx (stwcx or stdcx) failed",
	},
	[ POWER4_PME_PM_LSU1_SRQ_STFWD ] = {
		.pme_name = "PM_LSU1_SRQ_STFWD",
		.pme_code = 0xc24,
		.pme_short_desc = "LSU1 SRQ store forwarded",
		.pme_long_desc = "Data from a store instruction was forwarded to a load on unit 1",
	},
	[ POWER4_PME_PM_GRP_DISP ] = {
		.pme_name = "PM_GRP_DISP",
		.pme_code = 0x2004,
		.pme_short_desc = "Group dispatches",
		.pme_long_desc = "A group was dispatched",
	},
	[ POWER4_PME_PM_DATA_FROM_L2 ] = {
		.pme_name = "PM_DATA_FROM_L2",
		.pme_code = 0x4c66,
		.pme_short_desc = "Data loaded from L2",
		.pme_long_desc = "DL1 was reloaded from the local L2 due to a demand load",
	},
	[ POWER4_PME_PM_L2_PREF ] = {
		.pme_name = "PM_L2_PREF",
		.pme_code = 0xc34,
		.pme_short_desc = "L2 cache prefetches",
		.pme_long_desc = "A request to prefetch data into L2 was made",
	},
	[ POWER4_PME_PM_FPU0_FPSCR ] = {
		.pme_name = "PM_FPU0_FPSCR",
		.pme_code = 0x130,
		.pme_short_desc = "FPU0 executed FPSCR instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing fpscr move related instruction. This could be mtfsfi*, mtfsb0*, mtfsb1*. mffs*, mtfsf*, mcrsf* where XYZ* means XYZ, XYZs, XYZ., XYZs",
	},
	[ POWER4_PME_PM_FPU1_DENORM ] = {
		.pme_name = "PM_FPU1_DENORM",
		.pme_code = 0x124,
		.pme_short_desc = "FPU1 received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized.",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L25_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L25_MOD",
		.pme_code = 0x8c76,
		.pme_short_desc = "Marked data loaded from L2.5 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of a chip on this MCM due to a marked demand load",
	},
	[ POWER4_PME_PM_L2SB_ST_REQ ] = {
		.pme_name = "PM_L2SB_ST_REQ",
		.pme_code = 0xf12,
		.pme_short_desc = "L2 slice B store requests",
		.pme_long_desc = "A store request as seen at the L2 directory has been made from the core. Stores are counted after gathering in the L2 store queues. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_L2SB_MOD_INV ] = {
		.pme_name = "PM_L2SB_MOD_INV",
		.pme_code = 0xf23,
		.pme_short_desc = "L2 slice B transition from modified to invalid",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from the Modified state to the Invalid state. This transition was caused by any RWITM snoop request that hit against a modified entry in the local L2. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_FPU0_FSQRT ] = {
		.pme_name = "PM_FPU0_FSQRT",
		.pme_code = 0x102,
		.pme_short_desc = "FPU0 executed FSQRT instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp0 is executing a square root instruction. This could be fsqrt* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER4_PME_PM_LD_REF_L1 ] = {
		.pme_name = "PM_LD_REF_L1",
		.pme_code = 0x8c10,
		.pme_short_desc = "L1 D cache load references",
		.pme_long_desc = "Total DL1 Load references",
	},
	[ POWER4_PME_PM_MRK_L1_RELOAD_VALID ] = {
		.pme_name = "PM_MRK_L1_RELOAD_VALID",
		.pme_code = 0xc74,
		.pme_short_desc = "Marked L1 reload data source valid",
		.pme_long_desc = "The source information is valid and is for a marked load",
	},
	[ POWER4_PME_PM_L2SB_SHR_MOD ] = {
		.pme_name = "PM_L2SB_SHR_MOD",
		.pme_code = 0xf20,
		.pme_short_desc = "L2 slice B transition from shared to modified",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from Shared (Shared, Shared L , or Tagged) to the Modified state. This transition was caused by a store from either of the two local CPUs to a cache line in any of the Shared states. The event is provided on each of the three slices A,B,and C. ",
	},
	[ POWER4_PME_PM_INST_FROM_L1 ] = {
		.pme_name = "PM_INST_FROM_L1",
		.pme_code = 0x6327,
		.pme_short_desc = "Instruction fetched from L1",
		.pme_long_desc = "An instruction fetch group was fetched from L1. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_1PLUS_PPC_CMPL ] = {
		.pme_name = "PM_1PLUS_PPC_CMPL",
		.pme_code = 0x5003,
		.pme_short_desc = "One or more PPC instruction completed",
		.pme_long_desc = "A group containing at least one PPC instruction completed. For microcoded instructions that span multiple groups, this will only occur once.",
	},
	[ POWER4_PME_PM_EE_OFF_EXT_INT ] = {
		.pme_name = "PM_EE_OFF_EXT_INT",
		.pme_code = 0x237,
		.pme_short_desc = "Cycles MSR(EE) bit off and external interrupt pending",
		.pme_long_desc = "Cycles MSR(EE) bit off and external interrupt pending",
	},
	[ POWER4_PME_PM_L2SC_SHR_MOD ] = {
		.pme_name = "PM_L2SC_SHR_MOD",
		.pme_code = 0xf24,
		.pme_short_desc = "L2 slice C transition from shared to modified",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from Shared (Shared, Shared L , or Tagged) to the Modified state. This transition was caused by a store from either of the two local CPUs to a cache line in any of the Shared states. The event is provided on each of the three slices A,B,and C. ",
	},
	[ POWER4_PME_PM_LSU_LRQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_LRQ_FULL_CYC",
		.pme_code = 0x212,
		.pme_short_desc = "Cycles LRQ full",
		.pme_long_desc = "The isu sends this signal when the lrq is full.",
	},
	[ POWER4_PME_PM_IC_PREF_INSTALL ] = {
		.pme_name = "PM_IC_PREF_INSTALL",
		.pme_code = 0x325,
		.pme_short_desc = "Instruction prefetched installed in prefetch buffer",
		.pme_long_desc = "This signal is asserted when a prefetch buffer entry (line) is allocated but the request is not a demand fetch.",
	},
	[ POWER4_PME_PM_MRK_LSU1_FLUSH_SRQ ] = {
		.pme_name = "PM_MRK_LSU1_FLUSH_SRQ",
		.pme_code = 0x917,
		.pme_short_desc = "LSU1 marked SRQ flushes",
		.pme_long_desc = "A marked store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ POWER4_PME_PM_GCT_FULL_CYC ] = {
		.pme_name = "PM_GCT_FULL_CYC",
		.pme_code = 0x200,
		.pme_short_desc = "Cycles GCT full",
		.pme_long_desc = "The ISU sends a signal indicating the gct is full. ",
	},
	[ POWER4_PME_PM_INST_FROM_MEM ] = {
		.pme_name = "PM_INST_FROM_MEM",
		.pme_code = 0x1327,
		.pme_short_desc = "Instruction fetched from memory",
		.pme_long_desc = "An instruction fetch group was fetched from memory. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_FXU_BUSY ] = {
		.pme_name = "PM_FXU_BUSY",
		.pme_code = 0x6002,
		.pme_short_desc = "FXU busy",
		.pme_long_desc = "FXU0 and FXU1 are both busy",
	},
	[ POWER4_PME_PM_ST_REF_L1_LSU1 ] = {
		.pme_name = "PM_ST_REF_L1_LSU1",
		.pme_code = 0xc15,
		.pme_short_desc = "LSU1 L1 D cache store references",
		.pme_long_desc = "A store executed on unit 1",
	},
	[ POWER4_PME_PM_MRK_LD_MISS_L1 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1",
		.pme_code = 0x1920,
		.pme_short_desc = "Marked L1 D cache load misses",
		.pme_long_desc = "Marked L1 D cache load misses",
	},
	[ POWER4_PME_PM_MRK_LSU1_INST_FIN ] = {
		.pme_name = "PM_MRK_LSU1_INST_FIN",
		.pme_code = 0xc32,
		.pme_short_desc = "LSU1 finished a marked instruction",
		.pme_long_desc = "LSU unit 1 finished a marked instruction",
	},
	[ POWER4_PME_PM_L1_WRITE_CYC ] = {
		.pme_name = "PM_L1_WRITE_CYC",
		.pme_code = 0x333,
		.pme_short_desc = "Cycles writing to instruction L1",
		.pme_long_desc = "This signal is asserted each cycle a cache write is active.",
	},
	[ POWER4_PME_PM_BIQ_IDU_FULL_CYC ] = {
		.pme_name = "PM_BIQ_IDU_FULL_CYC",
		.pme_code = 0x324,
		.pme_short_desc = "Cycles BIQ or IDU full",
		.pme_long_desc = "This signal will be asserted each time either the IDU is full or the BIQ is full.",
	},
	[ POWER4_PME_PM_MRK_LSU0_INST_FIN ] = {
		.pme_name = "PM_MRK_LSU0_INST_FIN",
		.pme_code = 0xc31,
		.pme_short_desc = "LSU0 finished a marked instruction",
		.pme_long_desc = "LSU unit 0 finished a marked instruction",
	},
	[ POWER4_PME_PM_L2SC_ST_REQ ] = {
		.pme_name = "PM_L2SC_ST_REQ",
		.pme_code = 0xf14,
		.pme_short_desc = "L2 slice C store requests",
		.pme_long_desc = "A store request as seen at the L2 directory has been made from the core. Stores are counted after gathering in the L2 store queues. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_LSU1_BUSY ] = {
		.pme_name = "PM_LSU1_BUSY",
		.pme_code = 0xc37,
		.pme_short_desc = "LSU1 busy",
		.pme_long_desc = "LSU unit 1 is busy rejecting instructions ",
	},
	[ POWER4_PME_PM_FPU_ALL ] = {
		.pme_name = "PM_FPU_ALL",
		.pme_code = 0x5100,
		.pme_short_desc = "FPU executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when FPU is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_LSU_SRQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_SRQ_S0_ALLOC",
		.pme_code = 0xc25,
		.pme_short_desc = "SRQ slot 0 allocated",
		.pme_long_desc = "SRQ Slot zero was allocated",
	},
	[ POWER4_PME_PM_GRP_MRK ] = {
		.pme_name = "PM_GRP_MRK",
		.pme_code = 0x5004,
		.pme_short_desc = "Group marked in IDU",
		.pme_long_desc = "A group was sampled (marked)",
	},
	[ POWER4_PME_PM_FPU1_FIN ] = {
		.pme_name = "PM_FPU1_FIN",
		.pme_code = 0x117,
		.pme_short_desc = "FPU1 produced a result",
		.pme_long_desc = "fp1 finished, produced a result. This only indicates finish, not completion. ",
	},
	[ POWER4_PME_PM_DC_PREF_STREAM_ALLOC ] = {
		.pme_name = "PM_DC_PREF_STREAM_ALLOC",
		.pme_code = 0x907,
		.pme_short_desc = "D cache new prefetch stream allocated",
		.pme_long_desc = "A new Prefetch Stream was allocated",
	},
	[ POWER4_PME_PM_BR_MPRED_CR ] = {
		.pme_name = "PM_BR_MPRED_CR",
		.pme_code = 0x331,
		.pme_short_desc = "Branch mispredictions due CR bit setting",
		.pme_long_desc = "This signal is asserted when the branch execution unit detects a branch mispredict because the CR value is opposite of the predicted value. This signal is asserted after a branch issue event and will result in a branch redirect flush if not overridden by a flush of an older instruction.",
	},
	[ POWER4_PME_PM_BR_MPRED_TA ] = {
		.pme_name = "PM_BR_MPRED_TA",
		.pme_code = 0x332,
		.pme_short_desc = "Branch mispredictions due to target address",
		.pme_long_desc = "branch miss predict due to a target address prediction. This signal will be asserted each time the branch execution unit detects an incorrect target address prediction. This signal will be asserted after a valid branch execution unit issue and will cause a branch mispredict flush unless a flush is detected from an older instruction.",
	},
	[ POWER4_PME_PM_CRQ_FULL_CYC ] = {
		.pme_name = "PM_CRQ_FULL_CYC",
		.pme_code = 0x211,
		.pme_short_desc = "Cycles CR issue queue full",
		.pme_long_desc = "The ISU sends a signal indicating that the issue queue that feeds the ifu cr unit cannot accept any more group (queue is full of groups).",
	},
	[ POWER4_PME_PM_INST_FROM_PREF ] = {
		.pme_name = "PM_INST_FROM_PREF",
		.pme_code = 0x7327,
		.pme_short_desc = "Instructions fetched from prefetch",
		.pme_long_desc = "An instruction fetch group was fetched from the prefetch buffer. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_LD_MISS_L1 ] = {
		.pme_name = "PM_LD_MISS_L1",
		.pme_code = 0x3c10,
		.pme_short_desc = "L1 D cache load misses",
		.pme_long_desc = "Total DL1 Load references that miss the DL1",
	},
	[ POWER4_PME_PM_STCX_PASS ] = {
		.pme_name = "PM_STCX_PASS",
		.pme_code = 0xc75,
		.pme_short_desc = "Stcx passes",
		.pme_long_desc = "A stcx (stwcx or stdcx) instruction was successful",
	},
	[ POWER4_PME_PM_DC_INV_L2 ] = {
		.pme_name = "PM_DC_INV_L2",
		.pme_code = 0xc17,
		.pme_short_desc = "L1 D cache entries invalidated from L2",
		.pme_long_desc = "A dcache invalidated was received from the L2 because a line in L2 was castout.",
	},
	[ POWER4_PME_PM_LSU_SRQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_SRQ_FULL_CYC",
		.pme_code = 0x213,
		.pme_short_desc = "Cycles SRQ full",
		.pme_long_desc = "The isu sends this signal when the srq is full.",
	},
	[ POWER4_PME_PM_LSU0_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU0_FLUSH_LRQ",
		.pme_code = 0xc02,
		.pme_short_desc = "LSU0 LRQ flushes",
		.pme_long_desc = "A load was flushed by unit 1 because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER4_PME_PM_LSU_SRQ_S0_VALID ] = {
		.pme_name = "PM_LSU_SRQ_S0_VALID",
		.pme_code = 0xc21,
		.pme_short_desc = "SRQ slot 0 valid",
		.pme_long_desc = "This signal is asserted every cycle that the Store Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.",
	},
	[ POWER4_PME_PM_LARX_LSU0 ] = {
		.pme_name = "PM_LARX_LSU0",
		.pme_code = 0xc73,
		.pme_short_desc = "Larx executed on LSU0",
		.pme_long_desc = "A larx (lwarx or ldarx) was executed on side 0 (there is no corresponding unit 1 event since larx instructions can only execute on unit 0)",
	},
	[ POWER4_PME_PM_GCT_EMPTY_CYC ] = {
		.pme_name = "PM_GCT_EMPTY_CYC",
		.pme_code = 0x1004,
		.pme_short_desc = "Cycles GCT empty",
		.pme_long_desc = "The Global Completion Table is completely empty",
	},
	[ POWER4_PME_PM_FPU1_ALL ] = {
		.pme_name = "PM_FPU1_ALL",
		.pme_code = 0x107,
		.pme_short_desc = "FPU1 executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo",
	},
	[ POWER4_PME_PM_FPU1_FSQRT ] = {
		.pme_name = "PM_FPU1_FSQRT",
		.pme_code = 0x106,
		.pme_short_desc = "FPU1 executed FSQRT instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp1 is executing a square root instruction. This could be fsqrt* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER4_PME_PM_FPU_FIN ] = {
		.pme_name = "PM_FPU_FIN",
		.pme_code = 0x4110,
		.pme_short_desc = "FPU produced a result",
		.pme_long_desc = "FPU finished, produced a result This only indicates finish, not completion. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_L2SA_SHR_MOD ] = {
		.pme_name = "PM_L2SA_SHR_MOD",
		.pme_code = 0xf04,
		.pme_short_desc = "L2 slice A transition from shared to modified",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from Shared (Shared, Shared L , or Tagged) to the Modified state. This transition was caused by a store from either of the two local CPUs to a cache line in any of the Shared states. The event is provided on each of the three slices A,B,and C. ",
	},
	[ POWER4_PME_PM_MRK_LD_MISS_L1_LSU1 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1_LSU1",
		.pme_code = 0x924,
		.pme_short_desc = "LSU1 L1 D cache load misses",
		.pme_long_desc = "A marked load, executing on unit 1, missed the dcache",
	},
	[ POWER4_PME_PM_LSU_SRQ_STFWD ] = {
		.pme_name = "PM_LSU_SRQ_STFWD",
		.pme_code = 0x1c20,
		.pme_short_desc = "SRQ store forwarded",
		.pme_long_desc = "Data from a store instruction was forwarded to a load",
	},
	[ POWER4_PME_PM_FXU0_FIN ] = {
		.pme_name = "PM_FXU0_FIN",
		.pme_code = 0x232,
		.pme_short_desc = "FXU0 produced a result",
		.pme_long_desc = "The Fixed Point unit 0 finished an instruction and produced a result",
	},
	[ POWER4_PME_PM_MRK_FPU_FIN ] = {
		.pme_name = "PM_MRK_FPU_FIN",
		.pme_code = 0x7004,
		.pme_short_desc = "Marked instruction FPU processing finished",
		.pme_long_desc = "One of the Floating Point Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER4_PME_PM_LSU_BUSY ] = {
		.pme_name = "PM_LSU_BUSY",
		.pme_code = 0x4c30,
		.pme_short_desc = "LSU busy",
		.pme_long_desc = "LSU (unit 0 + unit 1) is busy rejecting instructions ",
	},
	[ POWER4_PME_PM_INST_FROM_L35 ] = {
		.pme_name = "PM_INST_FROM_L35",
		.pme_code = 0x4327,
		.pme_short_desc = "Instructions fetched from L3.5",
		.pme_long_desc = "An instruction fetch group was fetched from the L3 of another module. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER4_PME_PM_FPU1_FRSP_FCONV ] = {
		.pme_name = "PM_FPU1_FRSP_FCONV",
		.pme_code = 0x115,
		.pme_short_desc = "FPU1 executed FRSP or FCONV instructions",
		.pme_long_desc = "fThis signal is active for one cycle when fp1 is executing frsp or convert kind of instruction. This could be frsp*, fcfid*, fcti* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER4_PME_PM_SNOOP_TLBIE ] = {
		.pme_name = "PM_SNOOP_TLBIE",
		.pme_code = 0x903,
		.pme_short_desc = "Snoop TLBIE",
		.pme_long_desc = "A TLB miss for a data request occurred. Requests that miss the TLB may be retried until the instruction is in the next to complete group (unless HID4 is set to allow speculative tablewalks). This may result in multiple TLB misses for the same instruction.",
	},
	[ POWER4_PME_PM_FPU0_FDIV ] = {
		.pme_name = "PM_FPU0_FDIV",
		.pme_code = 0x100,
		.pme_short_desc = "FPU0 executed FDIV instruction",
		.pme_long_desc = "This signal is active for one cycle at the end of the microcode executed when fp0 is executing a divide instruction. This could be fdiv, fdivs, fdiv. fdivs.",
	},
	[ POWER4_PME_PM_LD_REF_L1_LSU1 ] = {
		.pme_name = "PM_LD_REF_L1_LSU1",
		.pme_code = 0xc14,
		.pme_short_desc = "LSU1 L1 D cache load references",
		.pme_long_desc = "A load executed on unit 1",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L275_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L275_MOD",
		.pme_code = 0x7c76,
		.pme_short_desc = "Marked data loaded from L2.75 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of another MCM due to a marked demand load. ",
	},
	[ POWER4_PME_PM_HV_CYC ] = {
		.pme_name = "PM_HV_CYC",
		.pme_code = 0x3004,
		.pme_short_desc = "Hypervisor Cycles",
		.pme_long_desc = "Cycles when the processor is executing in Hypervisor (MSR[HV] = 0 and MSR[PR]=0)",
	},
	[ POWER4_PME_PM_6INST_CLB_CYC ] = {
		.pme_name = "PM_6INST_CLB_CYC",
		.pme_code = 0x455,
		.pme_short_desc = "Cycles 6 instructions in CLB",
		.pme_long_desc = "The cache line buffer (CLB) is an 8-deep, 4-wide instruction buffer. Fullness is indicated in the 8 valid bits associated with each of the 4-wide slots with full(0) correspanding to the number of cycles there are 8 instructions in the queue and full (7) corresponding to the number of cycles there is 1 instruction in the queue. This signal gives a real time history of the number of instruction quads valid in the instruction queue.",
	},
	[ POWER4_PME_PM_LR_CTR_MAP_FULL_CYC ] = {
		.pme_name = "PM_LR_CTR_MAP_FULL_CYC",
		.pme_code = 0x206,
		.pme_short_desc = "Cycles LR/CTR mapper full",
		.pme_long_desc = "The ISU sends a signal indicating that the lr/ctr mapper cannot accept any more groups. Dispatch is stopped. Note: this condition indicates that a pool of mapper is full but the entire mapper may not be.",
	},
	[ POWER4_PME_PM_L2SC_MOD_INV ] = {
		.pme_name = "PM_L2SC_MOD_INV",
		.pme_code = 0xf27,
		.pme_short_desc = "L2 slice C transition from modified to invalid",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from the Modified state to the Invalid state. This transition was caused by any RWITM snoop request that hit against a modified entry in the local L2. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER4_PME_PM_FPU_DENORM ] = {
		.pme_name = "PM_FPU_DENORM",
		.pme_code = 0x1120,
		.pme_short_desc = "FPU received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized. Combined Unit 0 + Unit 1",
	},
	[ POWER4_PME_PM_DATA_FROM_L275_MOD ] = {
		.pme_name = "PM_DATA_FROM_L275_MOD",
		.pme_code = 0x7c66,
		.pme_short_desc = "Data loaded from L2.75 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of another MCM due to a demand load. ",
	},
	[ POWER4_PME_PM_LSU1_DERAT_MISS ] = {
		.pme_name = "PM_LSU1_DERAT_MISS",
		.pme_code = 0x906,
		.pme_short_desc = "LSU1 DERAT misses",
		.pme_long_desc = "A data request (load or store) from LSU Unit 1 missed the ERAT and resulted in an ERAT reload. Multiple instructions may miss the ERAT entry for the same 4K page, but only one reload will occur.",
	},
	[ POWER4_PME_PM_IC_PREF_REQ ] = {
		.pme_name = "PM_IC_PREF_REQ",
		.pme_code = 0x326,
		.pme_short_desc = "Instruction prefetch requests",
		.pme_long_desc = "Asserted when a non-canceled prefetch is made to the cache interface unit (CIU).",
	},
	[ POWER4_PME_PM_MRK_LSU_FIN ] = {
		.pme_name = "PM_MRK_LSU_FIN",
		.pme_code = 0x8004,
		.pme_short_desc = "Marked instruction LSU processing finished",
		.pme_long_desc = "One of the Load/Store Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_L3 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L3",
		.pme_code = 0x1c76,
		.pme_short_desc = "Marked data loaded from L3",
		.pme_long_desc = "DL1 was reloaded from the local L3 due to a marked demand load",
	},
	[ POWER4_PME_PM_MRK_DATA_FROM_MEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_MEM",
		.pme_code = 0x2c76,
		.pme_short_desc = "Marked data loaded from memory",
		.pme_long_desc = "DL1 was reloaded from memory due to a marked demand load",
	},
	[ POWER4_PME_PM_LSU0_FLUSH_UST ] = {
		.pme_name = "PM_LSU0_FLUSH_UST",
		.pme_code = 0xc01,
		.pme_short_desc = "LSU0 unaligned store flushes",
		.pme_long_desc = "A store was flushed from unit 0 because it was unaligned (crossed a 4k boundary)",
	},
	[ POWER4_PME_PM_LSU_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU_FLUSH_LRQ",
		.pme_code = 0x6c00,
		.pme_short_desc = "LRQ flushes",
		.pme_long_desc = "A load was flushed because a younger load executed before an older store executed and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER4_PME_PM_LSU_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU_FLUSH_SRQ",
		.pme_code = 0x5c00,
		.pme_short_desc = "SRQ flushes",
		.pme_long_desc = "A store was flushed because younger load hits and older store that is already in the SRQ or in the same group.",
	},
	[ POWER4_PME_PM_L2SC_MOD_TAG ] = {
		.pme_name = "PM_L2SC_MOD_TAG",
		.pme_code = 0xf26,
		.pme_short_desc = "L2 slice C transition from modified to tagged",
		.pme_long_desc = "A cache line in the local L2 directory made a state transition from the Modified state to the Tagged state. This transition was caused by a read snoop request that hit against a modified entry in the local L2. The event is provided on each of the three slices A,B,and C.",
	}
};
#endif

