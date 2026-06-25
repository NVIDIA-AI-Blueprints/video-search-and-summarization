/****************************/
/* THIS IS OPEN SOURCE CODE */
/****************************/

#ifndef __POWER7_EVENTS_H__
#define __POWER7_EVENTS_H__

/*
* File:    power7_events.h
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
* Documentation on the PMU events can be found at:
*  http://www.power.org/documentation/comprehensive-pmu-event-reference-power7
*/
#define POWER7_PME_PM_IC_DEMAND_L2_BR_ALL 0
#define POWER7_PME_PM_GCT_UTIL_7_TO_10_SLOTS 1
#define POWER7_PME_PM_PMC2_SAVED 2
#define POWER7_PME_PM_CMPLU_STALL_DFU 3
#define POWER7_PME_PM_VSU0_16FLOP 4
#define POWER7_PME_PM_MRK_LSU_DERAT_MISS 5
#define POWER7_PME_PM_MRK_ST_CMPL 6
#define POWER7_PME_PM_NEST_PAIR3_ADD 7
#define POWER7_PME_PM_L2_ST_DISP 8
#define POWER7_PME_PM_L2_CASTOUT_MOD 9
#define POWER7_PME_PM_ISEG 10
#define POWER7_PME_PM_MRK_INST_TIMEO 11
#define POWER7_PME_PM_L2_RCST_DISP_FAIL_ADDR 12
#define POWER7_PME_PM_LSU1_DC_PREF_STREAM_CONFIRM 13
#define POWER7_PME_PM_IERAT_WR_64K 14
#define POWER7_PME_PM_MRK_DTLB_MISS_16M 15
#define POWER7_PME_PM_IERAT_MISS 16
#define POWER7_PME_PM_MRK_PTEG_FROM_LMEM 17
#define POWER7_PME_PM_FLOP 18
#define POWER7_PME_PM_THRD_PRIO_4_5_CYC 19
#define POWER7_PME_PM_BR_PRED_TA 20
#define POWER7_PME_PM_CMPLU_STALL_FXU 21
#define POWER7_PME_PM_EXT_INT 22
#define POWER7_PME_PM_VSU_FSQRT_FDIV 23
#define POWER7_PME_PM_MRK_LD_MISS_EXPOSED_CYC 24
#define POWER7_PME_PM_LSU1_LDF 25
#define POWER7_PME_PM_IC_WRITE_ALL 26
#define POWER7_PME_PM_LSU0_SRQ_STFWD 27
#define POWER7_PME_PM_PTEG_FROM_RL2L3_MOD 28
#define POWER7_PME_PM_MRK_DATA_FROM_L31_SHR 29
#define POWER7_PME_PM_DATA_FROM_L21_MOD 30
#define POWER7_PME_PM_VSU1_SCAL_DOUBLE_ISSUED 31
#define POWER7_PME_PM_VSU0_8FLOP 32
#define POWER7_PME_PM_POWER_EVENT1 33
#define POWER7_PME_PM_DISP_CLB_HELD_BAL 34
#define POWER7_PME_PM_VSU1_2FLOP 35
#define POWER7_PME_PM_LWSYNC_HELD 36
#define POWER7_PME_PM_PTEG_FROM_DL2L3_SHR 37
#define POWER7_PME_PM_INST_FROM_L21_MOD 38
#define POWER7_PME_PM_IERAT_XLATE_WR_16MPLUS 39
#define POWER7_PME_PM_IC_REQ_ALL 40
#define POWER7_PME_PM_DSLB_MISS 41
#define POWER7_PME_PM_L3_MISS 42
#define POWER7_PME_PM_LSU0_L1_PREF 43
#define POWER7_PME_PM_VSU_SCALAR_SINGLE_ISSUED 44
#define POWER7_PME_PM_LSU1_DC_PREF_STREAM_CONFIRM_STRIDE 45
#define POWER7_PME_PM_L2_INST 46
#define POWER7_PME_PM_VSU0_FRSP 47
#define POWER7_PME_PM_FLUSH_DISP 48
#define POWER7_PME_PM_PTEG_FROM_L2MISS 49
#define POWER7_PME_PM_VSU1_DQ_ISSUED 50
#define POWER7_PME_PM_CMPLU_STALL_LSU 51
#define POWER7_PME_PM_MRK_DATA_FROM_DMEM 52
#define POWER7_PME_PM_LSU_FLUSH_ULD 53
#define POWER7_PME_PM_PTEG_FROM_LMEM 54
#define POWER7_PME_PM_MRK_DERAT_MISS_16M 55
#define POWER7_PME_PM_THRD_ALL_RUN_CYC 56
#define POWER7_PME_PM_MEM0_PREFETCH_DISP 57
#define POWER7_PME_PM_MRK_STALL_CMPLU_CYC_COUNT 58
#define POWER7_PME_PM_DATA_FROM_DL2L3_MOD 59
#define POWER7_PME_PM_VSU_FRSP 60
#define POWER7_PME_PM_MRK_DATA_FROM_L21_MOD 61
#define POWER7_PME_PM_PMC1_OVERFLOW 62
#define POWER7_PME_PM_VSU0_SINGLE 63
#define POWER7_PME_PM_MRK_PTEG_FROM_L3MISS 64
#define POWER7_PME_PM_MRK_PTEG_FROM_L31_SHR 65
#define POWER7_PME_PM_VSU0_VECTOR_SP_ISSUED 66
#define POWER7_PME_PM_VSU1_FEST 67
#define POWER7_PME_PM_MRK_INST_DISP 68
#define POWER7_PME_PM_VSU0_COMPLEX_ISSUED 69
#define POWER7_PME_PM_LSU1_FLUSH_UST 70
#define POWER7_PME_PM_INST_CMPL 71
#define POWER7_PME_PM_FXU_IDLE 72
#define POWER7_PME_PM_LSU0_FLUSH_ULD 73
#define POWER7_PME_PM_MRK_DATA_FROM_DL2L3_MOD 74
#define POWER7_PME_PM_LSU_LMQ_SRQ_EMPTY_ALL_CYC 75
#define POWER7_PME_PM_LSU1_REJECT_LMQ_FULL 76
#define POWER7_PME_PM_INST_PTEG_FROM_L21_MOD 77
#define POWER7_PME_PM_INST_FROM_RL2L3_MOD 78
#define POWER7_PME_PM_SHL_CREATED 79
#define POWER7_PME_PM_L2_ST_HIT 80
#define POWER7_PME_PM_DATA_FROM_DMEM 81
#define POWER7_PME_PM_L3_LD_MISS 82
#define POWER7_PME_PM_FXU1_BUSY_FXU0_IDLE 83
#define POWER7_PME_PM_DISP_CLB_HELD_RES 84
#define POWER7_PME_PM_L2_SN_SX_I_DONE 85
#define POWER7_PME_PM_GRP_CMPL 86
#define POWER7_PME_PM_STCX_CMPL 87
#define POWER7_PME_PM_VSU0_2FLOP 88
#define POWER7_PME_PM_L3_PREF_MISS 89
#define POWER7_PME_PM_LSU_SRQ_SYNC_CYC 90
#define POWER7_PME_PM_LSU_REJECT_ERAT_MISS 91
#define POWER7_PME_PM_L1_ICACHE_MISS 92
#define POWER7_PME_PM_LSU1_FLUSH_SRQ 93
#define POWER7_PME_PM_LD_REF_L1_LSU0 94
#define POWER7_PME_PM_VSU0_FEST 95
#define POWER7_PME_PM_VSU_VECTOR_SINGLE_ISSUED 96
#define POWER7_PME_PM_FREQ_UP 97
#define POWER7_PME_PM_DATA_FROM_LMEM 98
#define POWER7_PME_PM_LSU1_LDX 99
#define POWER7_PME_PM_PMC3_OVERFLOW 100
#define POWER7_PME_PM_MRK_BR_MPRED 101
#define POWER7_PME_PM_SHL_MATCH 102
#define POWER7_PME_PM_MRK_BR_TAKEN 103
#define POWER7_PME_PM_CMPLU_STALL_BRU 104
#define POWER7_PME_PM_ISLB_MISS 105
#define POWER7_PME_PM_CYC 106
#define POWER7_PME_PM_DISP_HELD_THERMAL 107
#define POWER7_PME_PM_INST_PTEG_FROM_RL2L3_SHR 108
#define POWER7_PME_PM_LSU1_SRQ_STFWD 109
#define POWER7_PME_PM_GCT_NOSLOT_BR_MPRED 110
#define POWER7_PME_PM_1PLUS_PPC_CMPL 111
#define POWER7_PME_PM_PTEG_FROM_DMEM 112
#define POWER7_PME_PM_VSU_2FLOP 113
#define POWER7_PME_PM_GCT_FULL_CYC 114
#define POWER7_PME_PM_MRK_DATA_FROM_L3_CYC 115
#define POWER7_PME_PM_LSU_SRQ_S0_ALLOC 116
#define POWER7_PME_PM_MRK_DERAT_MISS_4K 117
#define POWER7_PME_PM_BR_MPRED_TA 118
#define POWER7_PME_PM_INST_PTEG_FROM_L2MISS 119
#define POWER7_PME_PM_DPU_HELD_POWER 120
#define POWER7_PME_PM_RUN_INST_CMPL 121
#define POWER7_PME_PM_MRK_VSU_FIN 122
#define POWER7_PME_PM_LSU_SRQ_S0_VALID 123
#define POWER7_PME_PM_GCT_EMPTY_CYC 124
#define POWER7_PME_PM_IOPS_DISP 125
#define POWER7_PME_PM_RUN_SPURR 126
#define POWER7_PME_PM_PTEG_FROM_L21_MOD 127
#define POWER7_PME_PM_VSU0_1FLOP 128
#define POWER7_PME_PM_SNOOP_TLBIE 129
#define POWER7_PME_PM_DATA_FROM_L3MISS 130
#define POWER7_PME_PM_VSU_SINGLE 131
#define POWER7_PME_PM_DTLB_MISS_16G 132
#define POWER7_PME_PM_CMPLU_STALL_VECTOR 133
#define POWER7_PME_PM_FLUSH 134
#define POWER7_PME_PM_L2_LD_HIT 135
#define POWER7_PME_PM_NEST_PAIR2_AND 136
#define POWER7_PME_PM_VSU1_1FLOP 137
#define POWER7_PME_PM_IC_PREF_REQ 138
#define POWER7_PME_PM_L3_LD_HIT 139
#define POWER7_PME_PM_GCT_NOSLOT_IC_MISS 140
#define POWER7_PME_PM_DISP_HELD 141
#define POWER7_PME_PM_L2_LD 142
#define POWER7_PME_PM_LSU_FLUSH_SRQ 143
#define POWER7_PME_PM_BC_PLUS_8_CONV 144
#define POWER7_PME_PM_MRK_DATA_FROM_L31_MOD_CYC 145
#define POWER7_PME_PM_CMPLU_STALL_VECTOR_LONG 146
#define POWER7_PME_PM_L2_RCST_BUSY_RC_FULL 147
#define POWER7_PME_PM_TB_BIT_TRANS 148
#define POWER7_PME_PM_THERMAL_MAX 149
#define POWER7_PME_PM_LSU1_FLUSH_ULD 150
#define POWER7_PME_PM_LSU1_REJECT_LHS 151
#define POWER7_PME_PM_LSU_LRQ_S0_ALLOC 152
#define POWER7_PME_PM_L3_CO_L31 153
#define POWER7_PME_PM_POWER_EVENT4 154
#define POWER7_PME_PM_DATA_FROM_L31_SHR 155
#define POWER7_PME_PM_BR_UNCOND 156
#define POWER7_PME_PM_LSU1_DC_PREF_STREAM_ALLOC 157
#define POWER7_PME_PM_PMC4_REWIND 158
#define POWER7_PME_PM_L2_RCLD_DISP 159
#define POWER7_PME_PM_THRD_PRIO_2_3_CYC 160
#define POWER7_PME_PM_MRK_PTEG_FROM_L2MISS 161
#define POWER7_PME_PM_IC_DEMAND_L2_BHT_REDIRECT 162
#define POWER7_PME_PM_LSU_DERAT_MISS 163
#define POWER7_PME_PM_IC_PREF_CANCEL_L2 164
#define POWER7_PME_PM_MRK_FIN_STALL_CYC_COUNT 165
#define POWER7_PME_PM_BR_PRED_CCACHE 166
#define POWER7_PME_PM_GCT_UTIL_1_TO_2_SLOTS 167
#define POWER7_PME_PM_MRK_ST_CMPL_INT 168
#define POWER7_PME_PM_LSU_TWO_TABLEWALK_CYC 169
#define POWER7_PME_PM_MRK_DATA_FROM_L3MISS 170
#define POWER7_PME_PM_GCT_NOSLOT_CYC 171
#define POWER7_PME_PM_LSU_SET_MPRED 172
#define POWER7_PME_PM_FLUSH_DISP_TLBIE 173
#define POWER7_PME_PM_VSU1_FCONV 174
#define POWER7_PME_PM_DERAT_MISS_16G 175
#define POWER7_PME_PM_INST_FROM_LMEM 176
#define POWER7_PME_PM_IC_DEMAND_L2_BR_REDIRECT 177
#define POWER7_PME_PM_CMPLU_STALL_SCALAR_LONG 178
#define POWER7_PME_PM_INST_PTEG_FROM_L2 179
#define POWER7_PME_PM_PTEG_FROM_L2 180
#define POWER7_PME_PM_MRK_DATA_FROM_L21_SHR_CYC 181
#define POWER7_PME_PM_MRK_DTLB_MISS_4K 182
#define POWER7_PME_PM_VSU0_FPSCR 183
#define POWER7_PME_PM_VSU1_VECT_DOUBLE_ISSUED 184
#define POWER7_PME_PM_MRK_PTEG_FROM_RL2L3_MOD 185
#define POWER7_PME_PM_MEM0_RQ_DISP 186
#define POWER7_PME_PM_L2_LD_MISS 187
#define POWER7_PME_PM_VMX_RESULT_SAT_1 188
#define POWER7_PME_PM_L1_PREF 189
#define POWER7_PME_PM_MRK_DATA_FROM_LMEM_CYC 190
#define POWER7_PME_PM_GRP_IC_MISS_NONSPEC 191
#define POWER7_PME_PM_PB_NODE_PUMP 192
#define POWER7_PME_PM_SHL_MERGED 193
#define POWER7_PME_PM_NEST_PAIR1_ADD 194
#define POWER7_PME_PM_DATA_FROM_L3 195
#define POWER7_PME_PM_LSU_FLUSH 196
#define POWER7_PME_PM_LSU_SRQ_SYNC_COUNT 197
#define POWER7_PME_PM_PMC2_OVERFLOW 198
#define POWER7_PME_PM_LSU_LDF 199
#define POWER7_PME_PM_POWER_EVENT3 200
#define POWER7_PME_PM_DISP_WT 201
#define POWER7_PME_PM_CMPLU_STALL_REJECT 202
#define POWER7_PME_PM_IC_BANK_CONFLICT 203
#define POWER7_PME_PM_BR_MPRED_CR_TA 204
#define POWER7_PME_PM_L2_INST_MISS 205
#define POWER7_PME_PM_CMPLU_STALL_ERAT_MISS 206
#define POWER7_PME_PM_NEST_PAIR2_ADD 207
#define POWER7_PME_PM_MRK_LSU_FLUSH 208
#define POWER7_PME_PM_L2_LDST 209
#define POWER7_PME_PM_INST_FROM_L31_SHR 210
#define POWER7_PME_PM_VSU0_FIN 211
#define POWER7_PME_PM_LARX_LSU 212
#define POWER7_PME_PM_INST_FROM_RMEM 213
#define POWER7_PME_PM_DISP_CLB_HELD_TLBIE 214
#define POWER7_PME_PM_MRK_DATA_FROM_DMEM_CYC 215
#define POWER7_PME_PM_BR_PRED_CR 216
#define POWER7_PME_PM_LSU_REJECT 217
#define POWER7_PME_PM_GCT_UTIL_3_TO_6_SLOTS 218
#define POWER7_PME_PM_CMPLU_STALL_END_GCT_NOSLOT 219
#define POWER7_PME_PM_LSU0_REJECT_LMQ_FULL 220
#define POWER7_PME_PM_VSU_FEST 221
#define POWER7_PME_PM_NEST_PAIR0_AND 222
#define POWER7_PME_PM_PTEG_FROM_L3 223
#define POWER7_PME_PM_POWER_EVENT2 224
#define POWER7_PME_PM_IC_PREF_CANCEL_PAGE 225
#define POWER7_PME_PM_VSU0_FSQRT_FDIV 226
#define POWER7_PME_PM_MRK_GRP_CMPL 227
#define POWER7_PME_PM_VSU0_SCAL_DOUBLE_ISSUED 228
#define POWER7_PME_PM_GRP_DISP 229
#define POWER7_PME_PM_LSU0_LDX 230
#define POWER7_PME_PM_DATA_FROM_L2 231
#define POWER7_PME_PM_MRK_DATA_FROM_RL2L3_MOD 232
#define POWER7_PME_PM_LD_REF_L1 233
#define POWER7_PME_PM_VSU0_VECT_DOUBLE_ISSUED 234
#define POWER7_PME_PM_VSU1_2FLOP_DOUBLE 235
#define POWER7_PME_PM_THRD_PRIO_6_7_CYC 236
#define POWER7_PME_PM_BC_PLUS_8_RSLV_TAKEN 237
#define POWER7_PME_PM_BR_MPRED_CR 238
#define POWER7_PME_PM_L3_CO_MEM 239
#define POWER7_PME_PM_LD_MISS_L1 240
#define POWER7_PME_PM_DATA_FROM_RL2L3_MOD 241
#define POWER7_PME_PM_LSU_SRQ_FULL_CYC 242
#define POWER7_PME_PM_TABLEWALK_CYC 243
#define POWER7_PME_PM_MRK_PTEG_FROM_RMEM 244
#define POWER7_PME_PM_LSU_SRQ_STFWD 245
#define POWER7_PME_PM_INST_PTEG_FROM_RMEM 246
#define POWER7_PME_PM_FXU0_FIN 247
#define POWER7_PME_PM_LSU1_L1_SW_PREF 248
#define POWER7_PME_PM_PTEG_FROM_L31_MOD 249
#define POWER7_PME_PM_PMC5_OVERFLOW 250
#define POWER7_PME_PM_LD_REF_L1_LSU1 251
#define POWER7_PME_PM_INST_PTEG_FROM_L21_SHR 252
#define POWER7_PME_PM_CMPLU_STALL_THRD 253
#define POWER7_PME_PM_DATA_FROM_RMEM 254
#define POWER7_PME_PM_VSU0_SCAL_SINGLE_ISSUED 255
#define POWER7_PME_PM_BR_MPRED_LSTACK 256
#define POWER7_PME_PM_MRK_DATA_FROM_RL2L3_MOD_CYC 257
#define POWER7_PME_PM_LSU0_FLUSH_UST 258
#define POWER7_PME_PM_LSU_NCST 259
#define POWER7_PME_PM_BR_TAKEN 260
#define POWER7_PME_PM_INST_PTEG_FROM_LMEM 261
#define POWER7_PME_PM_GCT_NOSLOT_BR_MPRED_IC_MISS 262
#define POWER7_PME_PM_DTLB_MISS_4K 263
#define POWER7_PME_PM_PMC4_SAVED 264
#define POWER7_PME_PM_VSU1_PERMUTE_ISSUED 265
#define POWER7_PME_PM_SLB_MISS 266
#define POWER7_PME_PM_LSU1_FLUSH_LRQ 267
#define POWER7_PME_PM_DTLB_MISS 268
#define POWER7_PME_PM_VSU1_FRSP 269
#define POWER7_PME_PM_VSU_VECTOR_DOUBLE_ISSUED 270
#define POWER7_PME_PM_L2_CASTOUT_SHR 271
#define POWER7_PME_PM_DATA_FROM_DL2L3_SHR 272
#define POWER7_PME_PM_VSU1_STF 273
#define POWER7_PME_PM_ST_FIN 274
#define POWER7_PME_PM_PTEG_FROM_L21_SHR 275
#define POWER7_PME_PM_L2_LOC_GUESS_WRONG 276
#define POWER7_PME_PM_MRK_STCX_FAIL 277
#define POWER7_PME_PM_LSU0_REJECT_LHS 278
#define POWER7_PME_PM_IC_PREF_CANCEL_HIT 279
#define POWER7_PME_PM_L3_PREF_BUSY 280
#define POWER7_PME_PM_MRK_BRU_FIN 281
#define POWER7_PME_PM_LSU1_NCLD 282
#define POWER7_PME_PM_INST_PTEG_FROM_L31_MOD 283
#define POWER7_PME_PM_LSU_NCLD 284
#define POWER7_PME_PM_LSU_LDX 285
#define POWER7_PME_PM_L2_LOC_GUESS_CORRECT 286
#define POWER7_PME_PM_THRESH_TIMEO 287
#define POWER7_PME_PM_L3_PREF_ST 288
#define POWER7_PME_PM_DISP_CLB_HELD_SYNC 289
#define POWER7_PME_PM_VSU_SIMPLE_ISSUED 290
#define POWER7_PME_PM_VSU1_SINGLE 291
#define POWER7_PME_PM_DATA_TABLEWALK_CYC 292
#define POWER7_PME_PM_L2_RC_ST_DONE 293
#define POWER7_PME_PM_MRK_PTEG_FROM_L21_MOD 294
#define POWER7_PME_PM_LARX_LSU1 295
#define POWER7_PME_PM_MRK_DATA_FROM_RMEM 296
#define POWER7_PME_PM_DISP_CLB_HELD 297
#define POWER7_PME_PM_DERAT_MISS_4K 298
#define POWER7_PME_PM_L2_RCLD_DISP_FAIL_ADDR 299
#define POWER7_PME_PM_SEG_EXCEPTION 300
#define POWER7_PME_PM_FLUSH_DISP_SB 301
#define POWER7_PME_PM_L2_DC_INV 302
#define POWER7_PME_PM_PTEG_FROM_DL2L3_MOD 303
#define POWER7_PME_PM_DSEG 304
#define POWER7_PME_PM_BR_PRED_LSTACK 305
#define POWER7_PME_PM_VSU0_STF 306
#define POWER7_PME_PM_LSU_FX_FIN 307
#define POWER7_PME_PM_DERAT_MISS_16M 308
#define POWER7_PME_PM_MRK_PTEG_FROM_DL2L3_MOD 309
#define POWER7_PME_PM_GCT_UTIL_11_PLUS_SLOTS 310
#define POWER7_PME_PM_INST_FROM_L3 311
#define POWER7_PME_PM_MRK_IFU_FIN 312
#define POWER7_PME_PM_ITLB_MISS 313
#define POWER7_PME_PM_VSU_STF 314
#define POWER7_PME_PM_LSU_FLUSH_UST 315
#define POWER7_PME_PM_L2_LDST_MISS 316
#define POWER7_PME_PM_FXU1_FIN 317
#define POWER7_PME_PM_SHL_DEALLOCATED 318
#define POWER7_PME_PM_L2_SN_M_WR_DONE 319
#define POWER7_PME_PM_LSU_REJECT_SET_MPRED 320
#define POWER7_PME_PM_L3_PREF_LD 321
#define POWER7_PME_PM_L2_SN_M_RD_DONE 322
#define POWER7_PME_PM_MRK_DERAT_MISS_16G 323
#define POWER7_PME_PM_VSU_FCONV 324
#define POWER7_PME_PM_ANY_THRD_RUN_CYC 325
#define POWER7_PME_PM_LSU_LMQ_FULL_CYC 326
#define POWER7_PME_PM_MRK_LSU_REJECT_LHS 327
#define POWER7_PME_PM_MRK_LD_MISS_L1_CYC 328
#define POWER7_PME_PM_MRK_DATA_FROM_L2_CYC 329
#define POWER7_PME_PM_INST_IMC_MATCH_DISP 330
#define POWER7_PME_PM_MRK_DATA_FROM_RMEM_CYC 331
#define POWER7_PME_PM_VSU0_SIMPLE_ISSUED 332
#define POWER7_PME_PM_CMPLU_STALL_DIV 333
#define POWER7_PME_PM_MRK_PTEG_FROM_RL2L3_SHR 334
#define POWER7_PME_PM_VSU_FMA_DOUBLE 335
#define POWER7_PME_PM_VSU_4FLOP 336
#define POWER7_PME_PM_VSU1_FIN 337
#define POWER7_PME_PM_NEST_PAIR1_AND 338
#define POWER7_PME_PM_INST_PTEG_FROM_RL2L3_MOD 339
#define POWER7_PME_PM_RUN_CYC 340
#define POWER7_PME_PM_PTEG_FROM_RMEM 341
#define POWER7_PME_PM_LSU_LRQ_S0_VALID 342
#define POWER7_PME_PM_LSU0_LDF 343
#define POWER7_PME_PM_FLUSH_COMPLETION 344
#define POWER7_PME_PM_ST_MISS_L1 345
#define POWER7_PME_PM_L2_NODE_PUMP 346
#define POWER7_PME_PM_INST_FROM_DL2L3_SHR 347
#define POWER7_PME_PM_MRK_STALL_CMPLU_CYC 348
#define POWER7_PME_PM_VSU1_DENORM 349
#define POWER7_PME_PM_MRK_DATA_FROM_L31_SHR_CYC 350
#define POWER7_PME_PM_NEST_PAIR0_ADD 351
#define POWER7_PME_PM_INST_FROM_L3MISS 352
#define POWER7_PME_PM_EE_OFF_EXT_INT 353
#define POWER7_PME_PM_INST_PTEG_FROM_DMEM 354
#define POWER7_PME_PM_INST_FROM_DL2L3_MOD 355
#define POWER7_PME_PM_PMC6_OVERFLOW 356
#define POWER7_PME_PM_VSU_2FLOP_DOUBLE 357
#define POWER7_PME_PM_TLB_MISS 358
#define POWER7_PME_PM_FXU_BUSY 359
#define POWER7_PME_PM_L2_RCLD_DISP_FAIL_OTHER 360
#define POWER7_PME_PM_LSU_REJECT_LMQ_FULL 361
#define POWER7_PME_PM_IC_RELOAD_SHR 362
#define POWER7_PME_PM_GRP_MRK 363
#define POWER7_PME_PM_MRK_ST_NEST 364
#define POWER7_PME_PM_VSU1_FSQRT_FDIV 365
#define POWER7_PME_PM_LSU0_FLUSH_LRQ 366
#define POWER7_PME_PM_LARX_LSU0 367
#define POWER7_PME_PM_IBUF_FULL_CYC 368
#define POWER7_PME_PM_MRK_DATA_FROM_DL2L3_SHR_CYC 369
#define POWER7_PME_PM_LSU_DC_PREF_STREAM_ALLOC 370
#define POWER7_PME_PM_GRP_MRK_CYC 371
#define POWER7_PME_PM_MRK_DATA_FROM_RL2L3_SHR_CYC 372
#define POWER7_PME_PM_L2_GLOB_GUESS_CORRECT 373
#define POWER7_PME_PM_LSU_REJECT_LHS 374
#define POWER7_PME_PM_MRK_DATA_FROM_LMEM 375
#define POWER7_PME_PM_INST_PTEG_FROM_L3 376
#define POWER7_PME_PM_FREQ_DOWN 377
#define POWER7_PME_PM_PB_RETRY_NODE_PUMP 378
#define POWER7_PME_PM_INST_FROM_RL2L3_SHR 379
#define POWER7_PME_PM_MRK_INST_ISSUED 380
#define POWER7_PME_PM_PTEG_FROM_L3MISS 381
#define POWER7_PME_PM_RUN_PURR 382
#define POWER7_PME_PM_MRK_GRP_IC_MISS 383
#define POWER7_PME_PM_MRK_DATA_FROM_L3 384
#define POWER7_PME_PM_CMPLU_STALL_DCACHE_MISS 385
#define POWER7_PME_PM_PTEG_FROM_RL2L3_SHR 386
#define POWER7_PME_PM_LSU_FLUSH_LRQ 387
#define POWER7_PME_PM_MRK_DERAT_MISS_64K 388
#define POWER7_PME_PM_INST_PTEG_FROM_DL2L3_MOD 389
#define POWER7_PME_PM_L2_ST_MISS 390
#define POWER7_PME_PM_MRK_PTEG_FROM_L21_SHR 391
#define POWER7_PME_PM_LWSYNC 392
#define POWER7_PME_PM_LSU0_DC_PREF_STREAM_CONFIRM_STRIDE 393
#define POWER7_PME_PM_MRK_LSU_FLUSH_LRQ 394
#define POWER7_PME_PM_INST_IMC_MATCH_CMPL 395
#define POWER7_PME_PM_NEST_PAIR3_AND 396
#define POWER7_PME_PM_PB_RETRY_SYS_PUMP 397
#define POWER7_PME_PM_MRK_INST_FIN 398
#define POWER7_PME_PM_MRK_PTEG_FROM_DL2L3_SHR 399
#define POWER7_PME_PM_INST_FROM_L31_MOD 400
#define POWER7_PME_PM_MRK_DTLB_MISS_64K 401
#define POWER7_PME_PM_LSU_FIN 402
#define POWER7_PME_PM_MRK_LSU_REJECT 403
#define POWER7_PME_PM_L2_CO_FAIL_BUSY 404
#define POWER7_PME_PM_MEM0_WQ_DISP 405
#define POWER7_PME_PM_DATA_FROM_L31_MOD 406
#define POWER7_PME_PM_THERMAL_WARN 407
#define POWER7_PME_PM_VSU0_4FLOP 408
#define POWER7_PME_PM_BR_MPRED_CCACHE 409
#define POWER7_PME_PM_CMPLU_STALL_IFU 410
#define POWER7_PME_PM_L1_DEMAND_WRITE 411
#define POWER7_PME_PM_FLUSH_BR_MPRED 412
#define POWER7_PME_PM_MRK_DTLB_MISS_16G 413
#define POWER7_PME_PM_MRK_PTEG_FROM_DMEM 414
#define POWER7_PME_PM_L2_RCST_DISP 415
#define POWER7_PME_PM_CMPLU_STALL 416
#define POWER7_PME_PM_LSU_PARTIAL_CDF 417
#define POWER7_PME_PM_DISP_CLB_HELD_SB 418
#define POWER7_PME_PM_VSU0_FMA_DOUBLE 419
#define POWER7_PME_PM_FXU0_BUSY_FXU1_IDLE 420
#define POWER7_PME_PM_IC_DEMAND_CYC 421
#define POWER7_PME_PM_MRK_DATA_FROM_L21_SHR 422
#define POWER7_PME_PM_MRK_LSU_FLUSH_UST 423
#define POWER7_PME_PM_INST_PTEG_FROM_L3MISS 424
#define POWER7_PME_PM_VSU_DENORM 425
#define POWER7_PME_PM_MRK_LSU_PARTIAL_CDF 426
#define POWER7_PME_PM_INST_FROM_L21_SHR 427
#define POWER7_PME_PM_IC_PREF_WRITE 428
#define POWER7_PME_PM_BR_PRED 429
#define POWER7_PME_PM_INST_FROM_DMEM 430
#define POWER7_PME_PM_IC_PREF_CANCEL_ALL 431
#define POWER7_PME_PM_LSU_DC_PREF_STREAM_CONFIRM 432
#define POWER7_PME_PM_MRK_LSU_FLUSH_SRQ 433
#define POWER7_PME_PM_MRK_FIN_STALL_CYC 434
#define POWER7_PME_PM_L2_RCST_DISP_FAIL_OTHER 435
#define POWER7_PME_PM_VSU1_DD_ISSUED 436
#define POWER7_PME_PM_PTEG_FROM_L31_SHR 437
#define POWER7_PME_PM_DATA_FROM_L21_SHR 438
#define POWER7_PME_PM_LSU0_NCLD 439
#define POWER7_PME_PM_VSU1_4FLOP 440
#define POWER7_PME_PM_VSU1_8FLOP 441
#define POWER7_PME_PM_VSU_8FLOP 442
#define POWER7_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC 443
#define POWER7_PME_PM_DTLB_MISS_64K 444
#define POWER7_PME_PM_THRD_CONC_RUN_INST 445
#define POWER7_PME_PM_MRK_PTEG_FROM_L2 446
#define POWER7_PME_PM_PB_SYS_PUMP 447
#define POWER7_PME_PM_VSU_FIN 448
#define POWER7_PME_PM_MRK_DATA_FROM_L31_MOD 449
#define POWER7_PME_PM_THRD_PRIO_0_1_CYC 450
#define POWER7_PME_PM_DERAT_MISS_64K 451
#define POWER7_PME_PM_PMC2_REWIND 452
#define POWER7_PME_PM_INST_FROM_L2 453
#define POWER7_PME_PM_GRP_BR_MPRED_NONSPEC 454
#define POWER7_PME_PM_INST_DISP 455
#define POWER7_PME_PM_MEM0_RD_CANCEL_TOTAL 456
#define POWER7_PME_PM_LSU0_DC_PREF_STREAM_CONFIRM 457
#define POWER7_PME_PM_L1_DCACHE_RELOAD_VALID 458
#define POWER7_PME_PM_VSU_SCALAR_DOUBLE_ISSUED 459
#define POWER7_PME_PM_L3_PREF_HIT 460
#define POWER7_PME_PM_MRK_PTEG_FROM_L31_MOD 461
#define POWER7_PME_PM_CMPLU_STALL_STORE 462
#define POWER7_PME_PM_MRK_FXU_FIN 463
#define POWER7_PME_PM_PMC4_OVERFLOW 464
#define POWER7_PME_PM_MRK_PTEG_FROM_L3 465
#define POWER7_PME_PM_LSU0_LMQ_LHR_MERGE 466
#define POWER7_PME_PM_BTAC_HIT 467
#define POWER7_PME_PM_L3_RD_BUSY 468
#define POWER7_PME_PM_LSU0_L1_SW_PREF 469
#define POWER7_PME_PM_INST_FROM_L2MISS 470
#define POWER7_PME_PM_LSU0_DC_PREF_STREAM_ALLOC 471
#define POWER7_PME_PM_L2_ST 472
#define POWER7_PME_PM_VSU0_DENORM 473
#define POWER7_PME_PM_MRK_DATA_FROM_DL2L3_SHR 474
#define POWER7_PME_PM_BR_PRED_CR_TA 475
#define POWER7_PME_PM_VSU0_FCONV 476
#define POWER7_PME_PM_MRK_LSU_FLUSH_ULD 477
#define POWER7_PME_PM_BTAC_MISS 478
#define POWER7_PME_PM_MRK_LD_MISS_EXPOSED_CYC_COUNT 479
#define POWER7_PME_PM_MRK_DATA_FROM_L2 480
#define POWER7_PME_PM_LSU_DCACHE_RELOAD_VALID 481
#define POWER7_PME_PM_VSU_FMA 482
#define POWER7_PME_PM_LSU0_FLUSH_SRQ 483
#define POWER7_PME_PM_LSU1_L1_PREF 484
#define POWER7_PME_PM_IOPS_CMPL 485
#define POWER7_PME_PM_L2_SYS_PUMP 486
#define POWER7_PME_PM_L2_RCLD_BUSY_RC_FULL 487
#define POWER7_PME_PM_LSU_LMQ_S0_ALLOC 488
#define POWER7_PME_PM_FLUSH_DISP_SYNC 489
#define POWER7_PME_PM_MRK_DATA_FROM_DL2L3_MOD_CYC 490
#define POWER7_PME_PM_L2_IC_INV 491
#define POWER7_PME_PM_MRK_DATA_FROM_L21_MOD_CYC 492
#define POWER7_PME_PM_L3_PREF_LDST 493
#define POWER7_PME_PM_LSU_SRQ_EMPTY_CYC 494
#define POWER7_PME_PM_LSU_LMQ_S0_VALID 495
#define POWER7_PME_PM_FLUSH_PARTIAL 496
#define POWER7_PME_PM_VSU1_FMA_DOUBLE 497
#define POWER7_PME_PM_1PLUS_PPC_DISP 498
#define POWER7_PME_PM_DATA_FROM_L2MISS 499
#define POWER7_PME_PM_SUSPENDED 500
#define POWER7_PME_PM_VSU0_FMA 501
#define POWER7_PME_PM_CMPLU_STALL_SCALAR 502
#define POWER7_PME_PM_STCX_FAIL 503
#define POWER7_PME_PM_VSU0_FSQRT_FDIV_DOUBLE 504
#define POWER7_PME_PM_DC_PREF_DST 505
#define POWER7_PME_PM_VSU1_SCAL_SINGLE_ISSUED 506
#define POWER7_PME_PM_L3_HIT 507
#define POWER7_PME_PM_L2_GLOB_GUESS_WRONG 508
#define POWER7_PME_PM_MRK_DFU_FIN 509
#define POWER7_PME_PM_INST_FROM_L1 510
#define POWER7_PME_PM_BRU_FIN 511
#define POWER7_PME_PM_IC_DEMAND_REQ 512
#define POWER7_PME_PM_VSU1_FSQRT_FDIV_DOUBLE 513
#define POWER7_PME_PM_VSU1_FMA 514
#define POWER7_PME_PM_MRK_LD_MISS_L1 515
#define POWER7_PME_PM_VSU0_2FLOP_DOUBLE 516
#define POWER7_PME_PM_LSU_DC_PREF_STRIDED_STREAM_CONFIRM 517
#define POWER7_PME_PM_INST_PTEG_FROM_L31_SHR 518
#define POWER7_PME_PM_MRK_LSU_REJECT_ERAT_MISS 519
#define POWER7_PME_PM_MRK_DATA_FROM_L2MISS 520
#define POWER7_PME_PM_DATA_FROM_RL2L3_SHR 521
#define POWER7_PME_PM_INST_FROM_PREF 522
#define POWER7_PME_PM_VSU1_SQ 523
#define POWER7_PME_PM_L2_LD_DISP 524
#define POWER7_PME_PM_L2_DISP_ALL 525
#define POWER7_PME_PM_THRD_GRP_CMPL_BOTH_CYC 526
#define POWER7_PME_PM_VSU_FSQRT_FDIV_DOUBLE 527
#define POWER7_PME_PM_BR_MPRED 528
#define POWER7_PME_PM_INST_PTEG_FROM_DL2L3_SHR 529
#define POWER7_PME_PM_VSU_1FLOP 530
#define POWER7_PME_PM_HV_CYC 531
#define POWER7_PME_PM_MRK_LSU_FIN 532
#define POWER7_PME_PM_MRK_DATA_FROM_RL2L3_SHR 533
#define POWER7_PME_PM_DTLB_MISS_16M 534
#define POWER7_PME_PM_LSU1_LMQ_LHR_MERGE 535
#define POWER7_PME_PM_IFU_FIN 536
#define POWER7_PME_PM_1THRD_CON_RUN_INSTR 537
#define POWER7_PME_PM_CMPLU_STALL_COUNT 538
#define POWER7_PME_PM_MEM0_PB_RD_CL 539
#define POWER7_PME_PM_THRD_1_RUN_CYC 540
#define POWER7_PME_PM_THRD_2_CONC_RUN_INSTR 541
#define POWER7_PME_PM_THRD_2_RUN_CYC 542
#define POWER7_PME_PM_THRD_3_CONC_RUN_INST 543
#define POWER7_PME_PM_THRD_3_RUN_CYC 544
#define POWER7_PME_PM_THRD_4_CONC_RUN_INST 545
#define POWER7_PME_PM_THRD_4_RUN_CYC 546

static const pme_power_entry_t power7_pe[] = {
	[ POWER7_PME_PM_IC_DEMAND_L2_BR_ALL ] = {
		.pme_name = "PM_IC_DEMAND_L2_BR_ALL",
		.pme_code = 0x4898,
		.pme_short_desc = " L2 I cache demand request due to BHT or redirect",
		.pme_long_desc = " L2 I cache demand request due to BHT or redirect",
	},
	[ POWER7_PME_PM_GCT_UTIL_7_TO_10_SLOTS ] = {
		.pme_name = "PM_GCT_UTIL_7_TO_10_SLOTS",
		.pme_code = 0x20a0,
		.pme_short_desc = "GCT Utilization 7-10 entries",
		.pme_long_desc = "GCT Utilization 7-10 entries",
	},
	[ POWER7_PME_PM_PMC2_SAVED ] = {
		.pme_name = "PM_PMC2_SAVED",
		.pme_code = 0x10022,
		.pme_short_desc = "PMC2 Rewind Value saved",
		.pme_long_desc = "PMC2 was counting speculatively. The speculative condition was met and the counter value was committed by copying it to the backup register.",
	},
	[ POWER7_PME_PM_CMPLU_STALL_DFU ] = {
		.pme_name = "PM_CMPLU_STALL_DFU",
		.pme_code = 0x2003c,
		.pme_short_desc = "Completion stall caused by Decimal Floating Point Unit",
		.pme_long_desc = "Completion stall caused by Decimal Floating Point Unit",
	},
	[ POWER7_PME_PM_VSU0_16FLOP ] = {
		.pme_name = "PM_VSU0_16FLOP",
		.pme_code = 0xa0a4,
		.pme_short_desc = "Sixteen flops operation (SP vector versions of fdiv,fsqrt)",
		.pme_long_desc = "Sixteen flops operation (SP vector versions of fdiv,fsqrt)",
	},
	[ POWER7_PME_PM_MRK_LSU_DERAT_MISS ] = {
		.pme_name = "PM_MRK_LSU_DERAT_MISS",
		.pme_code = 0x3d05a,
		.pme_short_desc = "Marked DERAT Miss",
		.pme_long_desc = "Marked DERAT Miss",
	},
	[ POWER7_PME_PM_MRK_ST_CMPL ] = {
		.pme_name = "PM_MRK_ST_CMPL",
		.pme_code = 0x10034,
		.pme_short_desc = "marked  store finished (was complete)",
		.pme_long_desc = "A sampled store has completed (data home)",
	},
	[ POWER7_PME_PM_NEST_PAIR3_ADD ] = {
		.pme_name = "PM_NEST_PAIR3_ADD",
		.pme_code = 0x40881,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair3 ADD",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair3 ADD",
	},
	[ POWER7_PME_PM_L2_ST_DISP ] = {
		.pme_name = "PM_L2_ST_DISP",
		.pme_code = 0x46180,
		.pme_short_desc = "All successful store dispatches",
		.pme_long_desc = "All successful store dispatches",
	},
	[ POWER7_PME_PM_L2_CASTOUT_MOD ] = {
		.pme_name = "PM_L2_CASTOUT_MOD",
		.pme_code = 0x16180,
		.pme_short_desc = "L2 Castouts - Modified (M, Mu, Me)",
		.pme_long_desc = "An L2 line in the Modified state was castout. Total for all slices.",
	},
	[ POWER7_PME_PM_ISEG ] = {
		.pme_name = "PM_ISEG",
		.pme_code = 0x20a4,
		.pme_short_desc = "ISEG Exception",
		.pme_long_desc = "ISEG Exception",
	},
	[ POWER7_PME_PM_MRK_INST_TIMEO ] = {
		.pme_name = "PM_MRK_INST_TIMEO",
		.pme_code = 0x40034,
		.pme_short_desc = "marked Instruction finish timeout ",
		.pme_long_desc = "The number of instructions finished since the last progress indicator from a marked instruction exceeded the threshold. The marked instruction was flushed.",
	},
	[ POWER7_PME_PM_L2_RCST_DISP_FAIL_ADDR ] = {
		.pme_name = "PM_L2_RCST_DISP_FAIL_ADDR",
		.pme_code = 0x36282,
		.pme_short_desc = " L2  RC store dispatch attempt failed due to address collision with RC/CO/SN/SQ",
		.pme_long_desc = " L2  RC store dispatch attempt failed due to address collision with RC/CO/SN/SQ",
	},
	[ POWER7_PME_PM_LSU1_DC_PREF_STREAM_CONFIRM ] = {
		.pme_name = "PM_LSU1_DC_PREF_STREAM_CONFIRM",
		.pme_code = 0xd0b6,
		.pme_short_desc = "LS1 'Dcache prefetch stream confirmed",
		.pme_long_desc = "LS1 'Dcache prefetch stream confirmed",
	},
	[ POWER7_PME_PM_IERAT_WR_64K ] = {
		.pme_name = "PM_IERAT_WR_64K",
		.pme_code = 0x40be,
		.pme_short_desc = "large page 64k ",
		.pme_long_desc = "large page 64k ",
	},
	[ POWER7_PME_PM_MRK_DTLB_MISS_16M ] = {
		.pme_name = "PM_MRK_DTLB_MISS_16M",
		.pme_code = 0x4d05e,
		.pme_short_desc = "Marked Data TLB misses for 16M page",
		.pme_long_desc = "Data TLB references to 16M pages by a marked instruction that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_IERAT_MISS ] = {
		.pme_name = "PM_IERAT_MISS",
		.pme_code = 0x100f6,
		.pme_short_desc = "IERAT Miss (Not implemented as DI on POWER6)",
		.pme_long_desc = "A translation request missed the Instruction Effective to Real Address Translation (ERAT) table",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_LMEM ] = {
		.pme_name = "PM_MRK_PTEG_FROM_LMEM",
		.pme_code = 0x4d052,
		.pme_short_desc = "Marked PTEG loaded from local memory",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from memory attached to the same module this proccessor is located on due to a marked load or store.",
	},
	[ POWER7_PME_PM_FLOP ] = {
		.pme_name = "PM_FLOP",
		.pme_code = 0x100f4,
		.pme_short_desc = "Floating Point Operation Finished",
		.pme_long_desc = "A floating point operation has completed",
	},
	[ POWER7_PME_PM_THRD_PRIO_4_5_CYC ] = {
		.pme_name = "PM_THRD_PRIO_4_5_CYC",
		.pme_code = 0x40b4,
		.pme_short_desc = " Cycles thread running at priority level 4 or 5",
		.pme_long_desc = " Cycles thread running at priority level 4 or 5",
	},
	[ POWER7_PME_PM_BR_PRED_TA ] = {
		.pme_name = "PM_BR_PRED_TA",
		.pme_code = 0x40aa,
		.pme_short_desc = "Branch predict - target address",
		.pme_long_desc = "The target address of a branch instruction was predicted.",
	},
	[ POWER7_PME_PM_CMPLU_STALL_FXU ] = {
		.pme_name = "PM_CMPLU_STALL_FXU",
		.pme_code = 0x20014,
		.pme_short_desc = "Completion stall caused by FXU instruction",
		.pme_long_desc = "Following a completion stall (any period when no groups completed) the last instruction to finish before completion resumes was a fixed point instruction.",
	},
	[ POWER7_PME_PM_EXT_INT ] = {
		.pme_name = "PM_EXT_INT",
		.pme_code = 0x200f8,
		.pme_short_desc = "external interrupt",
		.pme_long_desc = "An interrupt due to an external exception occurred",
	},
	[ POWER7_PME_PM_VSU_FSQRT_FDIV ] = {
		.pme_name = "PM_VSU_FSQRT_FDIV",
		.pme_code = 0xa888,
		.pme_short_desc = "four flops operation (fdiv,fsqrt) Scalar Instructions only!",
		.pme_long_desc = "DP vector versions of fdiv,fsqrt ",
	},
	[ POWER7_PME_PM_MRK_LD_MISS_EXPOSED_CYC ] = {
		.pme_name = "PM_MRK_LD_MISS_EXPOSED_CYC",
		.pme_code = 0x1003e,
		.pme_short_desc = "Marked Load exposed Miss ",
		.pme_long_desc = "Marked Load exposed Miss ",
	},
	[ POWER7_PME_PM_LSU1_LDF ] = {
		.pme_name = "PM_LSU1_LDF",
		.pme_code = 0xc086,
		.pme_short_desc = "LS1  Scalar Loads ",
		.pme_long_desc = "A floating point load was executed by LSU1",
	},
	[ POWER7_PME_PM_IC_WRITE_ALL ] = {
		.pme_name = "PM_IC_WRITE_ALL",
		.pme_code = 0x488c,
		.pme_short_desc = "Icache sectors written, prefetch + demand",
		.pme_long_desc = "Icache sectors written, prefetch + demand",
	},
	[ POWER7_PME_PM_LSU0_SRQ_STFWD ] = {
		.pme_name = "PM_LSU0_SRQ_STFWD",
		.pme_code = 0xc0a0,
		.pme_short_desc = "LS0 SRQ forwarded data to a load",
		.pme_long_desc = "Data from a store instruction was forwarded to a load on unit 0.  A load that misses L1 but becomes a store forward is treated as a load miss and it causes the DL1 load miss event to be counted.  It does not go into the LMQ. If a load that hits L1 but becomes a store forward, then it's not treated as a load miss.",
	},
	[ POWER7_PME_PM_PTEG_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_PTEG_FROM_RL2L3_MOD",
		.pme_code = 0x1c052,
		.pme_short_desc = "PTEG loaded from remote L2 or L3 modified",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with modified (M) data from an L2  or L3 on a remote module due to a demand load or store.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L31_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L31_SHR",
		.pme_code = 0x1d04e,
		.pme_short_desc = "Marked data loaded from another L3 on same chip shared",
		.pme_long_desc = "Marked data loaded from another L3 on same chip shared",
	},
	[ POWER7_PME_PM_DATA_FROM_L21_MOD ] = {
		.pme_name = "PM_DATA_FROM_L21_MOD",
		.pme_code = 0x3c046,
		.pme_short_desc = "Data loaded from another L2 on same chip modified",
		.pme_long_desc = "Data loaded from another L2 on same chip modified",
	},
	[ POWER7_PME_PM_VSU1_SCAL_DOUBLE_ISSUED ] = {
		.pme_name = "PM_VSU1_SCAL_DOUBLE_ISSUED",
		.pme_code = 0xb08a,
		.pme_short_desc = "Double Precision scalar instruction issued on Pipe1",
		.pme_long_desc = "Double Precision scalar instruction issued on Pipe1",
	},
	[ POWER7_PME_PM_VSU0_8FLOP ] = {
		.pme_name = "PM_VSU0_8FLOP",
		.pme_code = 0xa0a0,
		.pme_short_desc = "eight flops operation (DP vector versions of fdiv,fsqrt and SP vector versions of fmadd,fnmadd,fmsub,fnmsub) ",
		.pme_long_desc = "eight flops operation (DP vector versions of fdiv,fsqrt and SP vector versions of fmadd,fnmadd,fmsub,fnmsub) ",
	},
	[ POWER7_PME_PM_POWER_EVENT1 ] = {
		.pme_name = "PM_POWER_EVENT1",
		.pme_code = 0x1006e,
		.pme_short_desc = "Power Management Event 1",
		.pme_long_desc = "Power Management Event 1",
	},
	[ POWER7_PME_PM_DISP_CLB_HELD_BAL ] = {
		.pme_name = "PM_DISP_CLB_HELD_BAL",
		.pme_code = 0x2092,
		.pme_short_desc = "Dispatch/CLB Hold: Balance",
		.pme_long_desc = "Dispatch/CLB Hold: Balance",
	},
	[ POWER7_PME_PM_VSU1_2FLOP ] = {
		.pme_name = "PM_VSU1_2FLOP",
		.pme_code = 0xa09a,
		.pme_short_desc = "two flops operation (scalar fmadd, fnmadd, fmsub, fnmsub and DP vector versions of single flop instructions)",
		.pme_long_desc = "two flops operation (scalar fmadd, fnmadd, fmsub, fnmsub and DP vector versions of single flop instructions)",
	},
	[ POWER7_PME_PM_LWSYNC_HELD ] = {
		.pme_name = "PM_LWSYNC_HELD",
		.pme_code = 0x209a,
		.pme_short_desc = "LWSYNC held at dispatch",
		.pme_long_desc = "Cycles a LWSYNC instruction was held at dispatch. LWSYNC instructions are held at dispatch until all previous loads are done and all previous stores have issued. LWSYNC enters the Store Request Queue and is sent to the storage subsystem but does not wait for a response.",
	},
	[ POWER7_PME_PM_PTEG_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_PTEG_FROM_DL2L3_SHR",
		.pme_code = 0x3c054,
		.pme_short_desc = "PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with shared (T or SL) data from an L2 or L3 on a remote module due to a demand load or store.",
	},
	[ POWER7_PME_PM_INST_FROM_L21_MOD ] = {
		.pme_name = "PM_INST_FROM_L21_MOD",
		.pme_code = 0x34046,
		.pme_short_desc = "Instruction fetched from another L2 on same chip modified",
		.pme_long_desc = "Instruction fetched from another L2 on same chip modified",
	},
	[ POWER7_PME_PM_IERAT_XLATE_WR_16MPLUS ] = {
		.pme_name = "PM_IERAT_XLATE_WR_16MPLUS",
		.pme_code = 0x40bc,
		.pme_short_desc = "large page 16M+",
		.pme_long_desc = "large page 16M+",
	},
	[ POWER7_PME_PM_IC_REQ_ALL ] = {
		.pme_name = "PM_IC_REQ_ALL",
		.pme_code = 0x4888,
		.pme_short_desc = "Icache requests, prefetch + demand",
		.pme_long_desc = "Icache requests, prefetch + demand",
	},
	[ POWER7_PME_PM_DSLB_MISS ] = {
		.pme_name = "PM_DSLB_MISS",
		.pme_code = 0xd090,
		.pme_short_desc = "Data SLB Miss - Total of all segment sizes",
		.pme_long_desc = "A SLB miss for a data request occurred. SLB misses trap to the operating system to resolve.",
	},
	[ POWER7_PME_PM_L3_MISS ] = {
		.pme_name = "PM_L3_MISS",
		.pme_code = 0x1f082,
		.pme_short_desc = "L3 Misses ",
		.pme_long_desc = "L3 Misses ",
	},
	[ POWER7_PME_PM_LSU0_L1_PREF ] = {
		.pme_name = "PM_LSU0_L1_PREF",
		.pme_code = 0xd0b8,
		.pme_short_desc = " LS0 L1 cache data prefetches",
		.pme_long_desc = " LS0 L1 cache data prefetches",
	},
	[ POWER7_PME_PM_VSU_SCALAR_SINGLE_ISSUED ] = {
		.pme_name = "PM_VSU_SCALAR_SINGLE_ISSUED",
		.pme_code = 0xb884,
		.pme_short_desc = "Single Precision scalar instruction issued on Pipe0",
		.pme_long_desc = "Single Precision scalar instruction issued on Pipe0",
	},
	[ POWER7_PME_PM_LSU1_DC_PREF_STREAM_CONFIRM_STRIDE ] = {
		.pme_name = "PM_LSU1_DC_PREF_STREAM_CONFIRM_STRIDE",
		.pme_code = 0xd0be,
		.pme_short_desc = "LS1  Dcache Strided prefetch stream confirmed",
		.pme_long_desc = "LS1  Dcache Strided prefetch stream confirmed",
	},
	[ POWER7_PME_PM_L2_INST ] = {
		.pme_name = "PM_L2_INST",
		.pme_code = 0x36080,
		.pme_short_desc = "Instruction Load Count",
		.pme_long_desc = "Instruction Load Count",
	},
	[ POWER7_PME_PM_VSU0_FRSP ] = {
		.pme_name = "PM_VSU0_FRSP",
		.pme_code = 0xa0b4,
		.pme_short_desc = "Round to single precision instruction executed",
		.pme_long_desc = "Round to single precision instruction executed",
	},
	[ POWER7_PME_PM_FLUSH_DISP ] = {
		.pme_name = "PM_FLUSH_DISP",
		.pme_code = 0x2082,
		.pme_short_desc = "Dispatch flush",
		.pme_long_desc = "Dispatch flush",
	},
	[ POWER7_PME_PM_PTEG_FROM_L2MISS ] = {
		.pme_name = "PM_PTEG_FROM_L2MISS",
		.pme_code = 0x4c058,
		.pme_short_desc = "PTEG loaded from L2 miss",
		.pme_long_desc = "A Page Table Entry was loaded into the TLB but not from the local L2.",
	},
	[ POWER7_PME_PM_VSU1_DQ_ISSUED ] = {
		.pme_name = "PM_VSU1_DQ_ISSUED",
		.pme_code = 0xb09a,
		.pme_short_desc = "128BIT Decimal Issued on Pipe1",
		.pme_long_desc = "128BIT Decimal Issued on Pipe1",
	},
	[ POWER7_PME_PM_CMPLU_STALL_LSU ] = {
		.pme_name = "PM_CMPLU_STALL_LSU",
		.pme_code = 0x20012,
		.pme_short_desc = "Completion stall caused by LSU instruction",
		.pme_long_desc = "Following a completion stall (any period when no groups completed) the last instruction to finish before completion resumes was a load/store instruction.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_DMEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_DMEM",
		.pme_code = 0x1d04a,
		.pme_short_desc = "Marked data loaded from distant memory",
		.pme_long_desc = "The processor's Data Cache was reloaded with data from memory attached to a distant module due to a marked load.",
	},
	[ POWER7_PME_PM_LSU_FLUSH_ULD ] = {
		.pme_name = "PM_LSU_FLUSH_ULD",
		.pme_code = 0xc8b0,
		.pme_short_desc = "Flush: Unaligned Load",
		.pme_long_desc = "A load was flushed because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1).  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_PTEG_FROM_LMEM ] = {
		.pme_name = "PM_PTEG_FROM_LMEM",
		.pme_code = 0x4c052,
		.pme_short_desc = "PTEG loaded from local memory",
		.pme_long_desc = "A Page Table Entry was loaded into the TLB from memory attached to the same module this proccessor is located on.",
	},
	[ POWER7_PME_PM_MRK_DERAT_MISS_16M ] = {
		.pme_name = "PM_MRK_DERAT_MISS_16M",
		.pme_code = 0x3d05c,
		.pme_short_desc = "Marked DERAT misses for 16M page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 16M page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_THRD_ALL_RUN_CYC ] = {
		.pme_name = "PM_THRD_ALL_RUN_CYC",
		.pme_code = 0x2000c,
		.pme_short_desc = "All Threads in run_cycles",
		.pme_long_desc = "Cycles when all threads had their run latches set. Operating systems use the run latch to indicate when they are doing useful work.",
	},
	[ POWER7_PME_PM_MEM0_PREFETCH_DISP ] = {
		.pme_name = "PM_MEM0_PREFETCH_DISP",
		.pme_code = 0x20083,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair1 Bit1",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair1 Bit1",
	},
	[ POWER7_PME_PM_MRK_STALL_CMPLU_CYC_COUNT ] = {
		.pme_name = "PM_MRK_STALL_CMPLU_CYC_COUNT",
		.pme_code = 0x3003f,
		.pme_short_desc = "Marked Group Completion Stall cycles (use edge detect to count #)",
		.pme_long_desc = "Marked Group Completion Stall cycles (use edge detect to count #)",
	},
	[ POWER7_PME_PM_DATA_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_DATA_FROM_DL2L3_MOD",
		.pme_code = 0x3c04c,
		.pme_short_desc = "Data loaded from distant L2 or L3 modified",
		.pme_long_desc = "The processor's Data Cache was reloaded with modified (M) data from an L2  or L3 on a distant module due to a demand load",
	},
	[ POWER7_PME_PM_VSU_FRSP ] = {
		.pme_name = "PM_VSU_FRSP",
		.pme_code = 0xa8b4,
		.pme_short_desc = "Round to single precision instruction executed",
		.pme_long_desc = "Round to single precision instruction executed",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L21_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L21_MOD",
		.pme_code = 0x3d046,
		.pme_short_desc = "Marked data loaded from another L2 on same chip modified",
		.pme_long_desc = "Marked data loaded from another L2 on same chip modified",
	},
	[ POWER7_PME_PM_PMC1_OVERFLOW ] = {
		.pme_name = "PM_PMC1_OVERFLOW",
		.pme_code = 0x20010,
		.pme_short_desc = "Overflow from counter 1",
		.pme_long_desc = "Overflows from PMC1 are counted.  This effectively widens the PMC. The Overflow from the original PMC will not trigger an exception even if the PMU is configured to generate exceptions on overflow.",
	},
	[ POWER7_PME_PM_VSU0_SINGLE ] = {
		.pme_name = "PM_VSU0_SINGLE",
		.pme_code = 0xa0a8,
		.pme_short_desc = "FPU single precision",
		.pme_long_desc = "VSU0 executed single precision instruction",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L3MISS ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L3MISS",
		.pme_code = 0x2d058,
		.pme_short_desc = "Marked PTEG loaded from L3 miss",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from beyond the L3 due to a marked load or store",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L31_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L31_SHR",
		.pme_code = 0x2d056,
		.pme_short_desc = "Marked PTEG loaded from another L3 on same chip shared",
		.pme_long_desc = "Marked PTEG loaded from another L3 on same chip shared",
	},
	[ POWER7_PME_PM_VSU0_VECTOR_SP_ISSUED ] = {
		.pme_name = "PM_VSU0_VECTOR_SP_ISSUED",
		.pme_code = 0xb090,
		.pme_short_desc = "Single Precision vector instruction issued (executed)",
		.pme_long_desc = "Single Precision vector instruction issued (executed)",
	},
	[ POWER7_PME_PM_VSU1_FEST ] = {
		.pme_name = "PM_VSU1_FEST",
		.pme_code = 0xa0ba,
		.pme_short_desc = "Estimate instruction executed",
		.pme_long_desc = "Estimate instruction executed",
	},
	[ POWER7_PME_PM_MRK_INST_DISP ] = {
		.pme_name = "PM_MRK_INST_DISP",
		.pme_code = 0x20030,
		.pme_short_desc = "marked instruction dispatch",
		.pme_long_desc = "A marked instruction was dispatched",
	},
	[ POWER7_PME_PM_VSU0_COMPLEX_ISSUED ] = {
		.pme_name = "PM_VSU0_COMPLEX_ISSUED",
		.pme_code = 0xb096,
		.pme_short_desc = "Complex VMX instruction issued",
		.pme_long_desc = "Complex VMX instruction issued",
	},
	[ POWER7_PME_PM_LSU1_FLUSH_UST ] = {
		.pme_name = "PM_LSU1_FLUSH_UST",
		.pme_code = 0xc0b6,
		.pme_short_desc = "LS1 Flush: Unaligned Store",
		.pme_long_desc = "A store was flushed from unit 1 because it was unaligned (crossed a 4K boundary)",
	},
	[ POWER7_PME_PM_INST_CMPL ] = {
		.pme_name = "PM_INST_CMPL",
		.pme_code = 0x2,
		.pme_short_desc = "# PPC Instructions Finished",
		.pme_long_desc = "Number of PowerPC Instructions that completed.",
	},
	[ POWER7_PME_PM_FXU_IDLE ] = {
		.pme_name = "PM_FXU_IDLE",
		.pme_code = 0x1000e,
		.pme_short_desc = "fxu0 idle and fxu1 idle",
		.pme_long_desc = "FXU0 and FXU1 are both idle.",
	},
	[ POWER7_PME_PM_LSU0_FLUSH_ULD ] = {
		.pme_name = "PM_LSU0_FLUSH_ULD",
		.pme_code = 0xc0b0,
		.pme_short_desc = "LS0 Flush: Unaligned Load",
		.pme_long_desc = "A load was flushed from unit 0 because it was unaligned (crossed a 64 byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_DL2L3_MOD",
		.pme_code = 0x3d04c,
		.pme_short_desc = "Marked data loaded from distant L2 or L3 modified",
		.pme_long_desc = "The processor's Data Cache was reloaded with modified (M) data from an L2  or L3 on a distant module due to a marked load.",
	},
	[ POWER7_PME_PM_LSU_LMQ_SRQ_EMPTY_ALL_CYC ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_ALL_CYC",
		.pme_code = 0x3001c,
		.pme_short_desc = "ALL threads lsu empty (lmq and srq empty)",
		.pme_long_desc = "ALL threads lsu empty (lmq and srq empty)",
	},
	[ POWER7_PME_PM_LSU1_REJECT_LMQ_FULL ] = {
		.pme_name = "PM_LSU1_REJECT_LMQ_FULL",
		.pme_code = 0xc0a6,
		.pme_short_desc = "LS1 Reject: LMQ Full (LHR)",
		.pme_long_desc = "Total cycles the Load Store Unit 1 is busy rejecting instructions because the Load Miss Queue was full. The LMQ has eight entries.  If all eight entries are full, subsequent load instructions are rejected.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L21_MOD ] = {
		.pme_name = "PM_INST_PTEG_FROM_L21_MOD",
		.pme_code = 0x3e056,
		.pme_short_desc = "Instruction PTEG loaded from another L2 on same chip modified",
		.pme_long_desc = "Instruction PTEG loaded from another L2 on same chip modified",
	},
	[ POWER7_PME_PM_INST_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_INST_FROM_RL2L3_MOD",
		.pme_code = 0x14042,
		.pme_short_desc = "Instruction fetched from remote L2 or L3 modified",
		.pme_long_desc = "An instruction fetch group was fetched with modified  (M) data from an L2 or L3 on a remote module. Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_SHL_CREATED ] = {
		.pme_name = "PM_SHL_CREATED",
		.pme_code = 0x5082,
		.pme_short_desc = "SHL table entry Created",
		.pme_long_desc = "SHL table entry Created",
	},
	[ POWER7_PME_PM_L2_ST_HIT ] = {
		.pme_name = "PM_L2_ST_HIT",
		.pme_code = 0x46182,
		.pme_short_desc = "All successful store dispatches that were L2Hits",
		.pme_long_desc = "A store request hit in the L2 directory.  This event includes all requests to this L2 from all sources. Total for all slices.",
	},
	[ POWER7_PME_PM_DATA_FROM_DMEM ] = {
		.pme_name = "PM_DATA_FROM_DMEM",
		.pme_code = 0x1c04a,
		.pme_short_desc = "Data loaded from distant memory",
		.pme_long_desc = "The processor's Data Cache was reloaded with data from memory attached to a distant module due to a demand load",
	},
	[ POWER7_PME_PM_L3_LD_MISS ] = {
		.pme_name = "PM_L3_LD_MISS",
		.pme_code = 0x2f082,
		.pme_short_desc = "L3 demand LD Miss",
		.pme_long_desc = "L3 demand LD Miss",
	},
	[ POWER7_PME_PM_FXU1_BUSY_FXU0_IDLE ] = {
		.pme_name = "PM_FXU1_BUSY_FXU0_IDLE",
		.pme_code = 0x4000e,
		.pme_short_desc = "fxu0 idle and fxu1 busy. ",
		.pme_long_desc = "FXU0 was idle while FXU1 was busy",
	},
	[ POWER7_PME_PM_DISP_CLB_HELD_RES ] = {
		.pme_name = "PM_DISP_CLB_HELD_RES",
		.pme_code = 0x2094,
		.pme_short_desc = "Dispatch/CLB Hold: Resource",
		.pme_long_desc = "Dispatch/CLB Hold: Resource",
	},
	[ POWER7_PME_PM_L2_SN_SX_I_DONE ] = {
		.pme_name = "PM_L2_SN_SX_I_DONE",
		.pme_code = 0x36382,
		.pme_short_desc = "SNP dispatched and went from Sx or Tx to Ix",
		.pme_long_desc = "SNP dispatched and went from Sx or Tx to Ix",
	},
	[ POWER7_PME_PM_GRP_CMPL ] = {
		.pme_name = "PM_GRP_CMPL",
		.pme_code = 0x30004,
		.pme_short_desc = "group completed",
		.pme_long_desc = "A group completed. Microcoded instructions that span multiple groups will generate this event once per group.",
	},
	[ POWER7_PME_PM_STCX_CMPL ] = {
		.pme_name = "PM_STCX_CMPL",
		.pme_code = 0xc098,
		.pme_short_desc = "STCX executed",
		.pme_long_desc = "Conditional stores with reservation completed",
	},
	[ POWER7_PME_PM_VSU0_2FLOP ] = {
		.pme_name = "PM_VSU0_2FLOP",
		.pme_code = 0xa098,
		.pme_short_desc = "two flops operation (scalar fmadd, fnmadd, fmsub, fnmsub and DP vector versions of single flop instructions)",
		.pme_long_desc = "two flops operation (scalar fmadd, fnmadd, fmsub, fnmsub and DP vector versions of single flop instructions)",
	},
	[ POWER7_PME_PM_L3_PREF_MISS ] = {
		.pme_name = "PM_L3_PREF_MISS",
		.pme_code = 0x3f082,
		.pme_short_desc = "L3 Prefetch  Directory Miss",
		.pme_long_desc = "L3 Prefetch  Directory Miss",
	},
	[ POWER7_PME_PM_LSU_SRQ_SYNC_CYC ] = {
		.pme_name = "PM_LSU_SRQ_SYNC_CYC",
		.pme_code = 0xd096,
		.pme_short_desc = "A sync is in the SRQ",
		.pme_long_desc = "Cycles that a sync instruction is active in the Store Request Queue.",
	},
	[ POWER7_PME_PM_LSU_REJECT_ERAT_MISS ] = {
		.pme_name = "PM_LSU_REJECT_ERAT_MISS",
		.pme_code = 0x20064,
		.pme_short_desc = "LSU Reject due to ERAT (up to 2 per cycles)",
		.pme_long_desc = "Total cycles the Load Store Unit is busy rejecting instructions due to an ERAT miss. Combined unit 0 + 1. Requests that miss the Derat are rejected and retried until the request hits in the Erat.",
	},
	[ POWER7_PME_PM_L1_ICACHE_MISS ] = {
		.pme_name = "PM_L1_ICACHE_MISS",
		.pme_code = 0x200fc,
		.pme_short_desc = "Demand iCache Miss",
		.pme_long_desc = "An instruction fetch request missed the L1 cache.",
	},
	[ POWER7_PME_PM_LSU1_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU1_FLUSH_SRQ",
		.pme_code = 0xc0be,
		.pme_short_desc = "LS1 Flush: SRQ",
		.pme_long_desc = "Load Hit Store flush.  A younger load was flushed from unit 1 because it hits (overlaps) an older store that is already in the SRQ or in the same group.  If the real addresses match but the effective addresses do not, an alias condition exists that prevents store forwarding.  If the load and store are in the same group the load must be flushed to separate the two instructions. ",
	},
	[ POWER7_PME_PM_LD_REF_L1_LSU0 ] = {
		.pme_name = "PM_LD_REF_L1_LSU0",
		.pme_code = 0xc080,
		.pme_short_desc = "LS0 L1 D cache load references counted at finish",
		.pme_long_desc = "Load references to Level 1 Data Cache, by unit 0.",
	},
	[ POWER7_PME_PM_VSU0_FEST ] = {
		.pme_name = "PM_VSU0_FEST",
		.pme_code = 0xa0b8,
		.pme_short_desc = "Estimate instruction executed",
		.pme_long_desc = "Estimate instruction executed",
	},
	[ POWER7_PME_PM_VSU_VECTOR_SINGLE_ISSUED ] = {
		.pme_name = "PM_VSU_VECTOR_SINGLE_ISSUED",
		.pme_code = 0xb890,
		.pme_short_desc = "Single Precision vector instruction issued (executed)",
		.pme_long_desc = "Single Precision vector instruction issued (executed)",
	},
	[ POWER7_PME_PM_FREQ_UP ] = {
		.pme_name = "PM_FREQ_UP",
		.pme_code = 0x4000c,
		.pme_short_desc = "Power Management: Above Threshold A",
		.pme_long_desc = "Processor frequency was sped up due to power management",
	},
	[ POWER7_PME_PM_DATA_FROM_LMEM ] = {
		.pme_name = "PM_DATA_FROM_LMEM",
		.pme_code = 0x3c04a,
		.pme_short_desc = "Data loaded from local memory",
		.pme_long_desc = "The processor's Data Cache was reloaded from memory attached to the same module this proccessor is located on.",
	},
	[ POWER7_PME_PM_LSU1_LDX ] = {
		.pme_name = "PM_LSU1_LDX",
		.pme_code = 0xc08a,
		.pme_short_desc = "LS1  Vector Loads",
		.pme_long_desc = "LS1  Vector Loads",
	},
	[ POWER7_PME_PM_PMC3_OVERFLOW ] = {
		.pme_name = "PM_PMC3_OVERFLOW",
		.pme_code = 0x40010,
		.pme_short_desc = "Overflow from counter 3",
		.pme_long_desc = "Overflows from PMC3 are counted.  This effectively widens the PMC. The Overflow from the original PMC will not trigger an exception even if the PMU is configured to generate exceptions on overflow.",
	},
	[ POWER7_PME_PM_MRK_BR_MPRED ] = {
		.pme_name = "PM_MRK_BR_MPRED",
		.pme_code = 0x30036,
		.pme_short_desc = "Marked Branch Mispredicted",
		.pme_long_desc = "A marked branch was mispredicted",
	},
	[ POWER7_PME_PM_SHL_MATCH ] = {
		.pme_name = "PM_SHL_MATCH",
		.pme_code = 0x5086,
		.pme_short_desc = "SHL Table Match",
		.pme_long_desc = "SHL Table Match",
	},
	[ POWER7_PME_PM_MRK_BR_TAKEN ] = {
		.pme_name = "PM_MRK_BR_TAKEN",
		.pme_code = 0x10036,
		.pme_short_desc = "Marked Branch Taken",
		.pme_long_desc = "A marked branch was taken",
	},
	[ POWER7_PME_PM_CMPLU_STALL_BRU ] = {
		.pme_name = "PM_CMPLU_STALL_BRU",
		.pme_code = 0x4004e,
		.pme_short_desc = "Completion stall due to BRU",
		.pme_long_desc = "Completion stall due to BRU",
	},
	[ POWER7_PME_PM_ISLB_MISS ] = {
		.pme_name = "PM_ISLB_MISS",
		.pme_code = 0xd092,
		.pme_short_desc = "Instruction SLB Miss - Tota of all segment sizes",
		.pme_long_desc = "A SLB miss for an instruction fetch as occurred",
	},
	[ POWER7_PME_PM_CYC ] = {
		.pme_name = "PM_CYC",
		.pme_code = 0x1e,
		.pme_short_desc = "Cycles",
		.pme_long_desc = "Processor Cycles",
	},
	[ POWER7_PME_PM_DISP_HELD_THERMAL ] = {
		.pme_name = "PM_DISP_HELD_THERMAL",
		.pme_code = 0x30006,
		.pme_short_desc = "Dispatch Held due to Thermal",
		.pme_long_desc = "Dispatch Held due to Thermal",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_INST_PTEG_FROM_RL2L3_SHR",
		.pme_code = 0x2e054,
		.pme_short_desc = "Instruction PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "Instruction PTEG loaded from remote L2 or L3 shared",
	},
	[ POWER7_PME_PM_LSU1_SRQ_STFWD ] = {
		.pme_name = "PM_LSU1_SRQ_STFWD",
		.pme_code = 0xc0a2,
		.pme_short_desc = "LS1 SRQ forwarded data to a load",
		.pme_long_desc = "Data from a store instruction was forwarded to a load on unit 1.  A load that misses L1 but becomes a store forward is treated as a load miss and it causes the DL1 load miss event to be counted.  It does not go into the LMQ. If a load that hits L1 but becomes a store forward, then it's not treated as a load miss.",
	},
	[ POWER7_PME_PM_GCT_NOSLOT_BR_MPRED ] = {
		.pme_name = "PM_GCT_NOSLOT_BR_MPRED",
		.pme_code = 0x4001a,
		.pme_short_desc = "GCT empty by branch  mispredict",
		.pme_long_desc = "Cycles when the Global Completion Table has no slots from this thread because of a branch misprediction.",
	},
	[ POWER7_PME_PM_1PLUS_PPC_CMPL ] = {
		.pme_name = "PM_1PLUS_PPC_CMPL",
		.pme_code = 0x100f2,
		.pme_short_desc = "1 or more ppc  insts finished",
		.pme_long_desc = "A group containing at least one PPC instruction completed. For microcoded instructions that span multiple groups, this will only occur once.",
	},
	[ POWER7_PME_PM_PTEG_FROM_DMEM ] = {
		.pme_name = "PM_PTEG_FROM_DMEM",
		.pme_code = 0x2c052,
		.pme_short_desc = "PTEG loaded from distant memory",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with data from memory attached to a distant module due to a demand load or store.",
	},
	[ POWER7_PME_PM_VSU_2FLOP ] = {
		.pme_name = "PM_VSU_2FLOP",
		.pme_code = 0xa898,
		.pme_short_desc = "two flops operation (scalar fmadd, fnmadd, fmsub, fnmsub and DP vector versions of single flop instructions)",
		.pme_long_desc = "two flops operation (scalar fmadd, fnmadd, fmsub, fnmsub and DP vector versions of single flop instructions)",
	},
	[ POWER7_PME_PM_GCT_FULL_CYC ] = {
		.pme_name = "PM_GCT_FULL_CYC",
		.pme_code = 0x4086,
		.pme_short_desc = "Cycles No room in EAT",
		.pme_long_desc = "The Global Completion Table is completely full.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L3_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_L3_CYC",
		.pme_code = 0x40020,
		.pme_short_desc = "Marked ld latency Data source 0001 (L3)",
		.pme_long_desc = "Cycles a marked load waited for data from this level of the storage system.  Counting begins when a marked load misses the data cache and ends when the data is reloaded into the data cache.  To calculate average latency divide this count by the number of marked misses to the same level.",
	},
	[ POWER7_PME_PM_LSU_SRQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_SRQ_S0_ALLOC",
		.pme_code = 0xd09d,
		.pme_short_desc = "Slot 0 of SRQ valid",
		.pme_long_desc = "Slot 0 of SRQ valid",
	},
	[ POWER7_PME_PM_MRK_DERAT_MISS_4K ] = {
		.pme_name = "PM_MRK_DERAT_MISS_4K",
		.pme_code = 0x1d05c,
		.pme_short_desc = "Marked DERAT misses for 4K page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 4K page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_BR_MPRED_TA ] = {
		.pme_name = "PM_BR_MPRED_TA",
		.pme_code = 0x40ae,
		.pme_short_desc = "Branch mispredict - target address",
		.pme_long_desc = "A branch instruction target was incorrectly predicted. This will result in a branch mispredict flush unless a flush is detected from an older instruction.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L2MISS ] = {
		.pme_name = "PM_INST_PTEG_FROM_L2MISS",
		.pme_code = 0x4e058,
		.pme_short_desc = "Instruction PTEG loaded from L2 miss",
		.pme_long_desc = "Instruction PTEG loaded from L2 miss",
	},
	[ POWER7_PME_PM_DPU_HELD_POWER ] = {
		.pme_name = "PM_DPU_HELD_POWER",
		.pme_code = 0x20006,
		.pme_short_desc = "Dispatch Held due to Power Management",
		.pme_long_desc = "Cycles that Instruction Dispatch was held due to power management. More than one hold condition can exist at the same time",
	},
	[ POWER7_PME_PM_RUN_INST_CMPL ] = {
		.pme_name = "PM_RUN_INST_CMPL",
		.pme_code = 0x400fa,
		.pme_short_desc = "Run_Instructions",
		.pme_long_desc = "Number of run instructions completed. ",
	},
	[ POWER7_PME_PM_MRK_VSU_FIN ] = {
		.pme_name = "PM_MRK_VSU_FIN",
		.pme_code = 0x30032,
		.pme_short_desc = "vsu (fpu) marked  instr finish",
		.pme_long_desc = "vsu (fpu) marked  instr finish",
	},
	[ POWER7_PME_PM_LSU_SRQ_S0_VALID ] = {
		.pme_name = "PM_LSU_SRQ_S0_VALID",
		.pme_code = 0xd09c,
		.pme_short_desc = "Slot 0 of SRQ valid",
		.pme_long_desc = "This signal is asserted every cycle that the Store Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.  In SMT mode the SRQ is split between the two threads (16 entries each).",
	},
	[ POWER7_PME_PM_GCT_EMPTY_CYC ] = {
		.pme_name = "PM_GCT_EMPTY_CYC",
		.pme_code = 0x20008,
		.pme_short_desc = "GCT empty, all threads",
		.pme_long_desc = "Cycles when the Global Completion Table was completely empty.  No thread had an entry allocated.",
	},
	[ POWER7_PME_PM_IOPS_DISP ] = {
		.pme_name = "PM_IOPS_DISP",
		.pme_code = 0x30014,
		.pme_short_desc = "IOPS dispatched",
		.pme_long_desc = "IOPS dispatched",
	},
	[ POWER7_PME_PM_RUN_SPURR ] = {
		.pme_name = "PM_RUN_SPURR",
		.pme_code = 0x10008,
		.pme_short_desc = "Run SPURR",
		.pme_long_desc = "Run SPURR",
	},
	[ POWER7_PME_PM_PTEG_FROM_L21_MOD ] = {
		.pme_name = "PM_PTEG_FROM_L21_MOD",
		.pme_code = 0x3c056,
		.pme_short_desc = "PTEG loaded from another L2 on same chip modified",
		.pme_long_desc = "PTEG loaded from another L2 on same chip modified",
	},
	[ POWER7_PME_PM_VSU0_1FLOP ] = {
		.pme_name = "PM_VSU0_1FLOP",
		.pme_code = 0xa080,
		.pme_short_desc = "one flop (fadd, fmul, fsub, fcmp, fsel, fabs, fnabs, fres, fsqrte, fneg, xsadd, xsmul, xssub, xscmp, xssel, xsabs, xsnabs, xsre, xssqrte, xsneg) operation finished",
		.pme_long_desc = "one flop (fadd, fmul, fsub, fcmp, fsel, fabs, fnabs, fres, fsqrte, fneg, xsadd, xsmul, xssub, xscmp, xssel, xsabs, xsnabs, xsre, xssqrte, xsneg) operation finished",
	},
	[ POWER7_PME_PM_SNOOP_TLBIE ] = {
		.pme_name = "PM_SNOOP_TLBIE",
		.pme_code = 0xd0b2,
		.pme_short_desc = "TLBIE snoop",
		.pme_long_desc = "A tlbie was snooped from another processor.",
	},
	[ POWER7_PME_PM_DATA_FROM_L3MISS ] = {
		.pme_name = "PM_DATA_FROM_L3MISS",
		.pme_code = 0x2c048,
		.pme_short_desc = "Demand LD - L3 Miss (not L2 hit and not L3 hit)",
		.pme_long_desc = "The processor's Data Cache was reloaded from beyond L3 due to a demand load",
	},
	[ POWER7_PME_PM_VSU_SINGLE ] = {
		.pme_name = "PM_VSU_SINGLE",
		.pme_code = 0xa8a8,
		.pme_short_desc = "Vector or Scalar single precision",
		.pme_long_desc = "Vector or Scalar single precision",
	},
	[ POWER7_PME_PM_DTLB_MISS_16G ] = {
		.pme_name = "PM_DTLB_MISS_16G",
		.pme_code = 0x1c05e,
		.pme_short_desc = "Data TLB miss for 16G page",
		.pme_long_desc = "Data TLB references to 16GB pages that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_CMPLU_STALL_VECTOR ] = {
		.pme_name = "PM_CMPLU_STALL_VECTOR",
		.pme_code = 0x2001c,
		.pme_short_desc = "Completion stall caused by Vector instruction",
		.pme_long_desc = "Completion stall caused by Vector instruction",
	},
	[ POWER7_PME_PM_FLUSH ] = {
		.pme_name = "PM_FLUSH",
		.pme_code = 0x400f8,
		.pme_short_desc = "Flush (any type)",
		.pme_long_desc = "Flushes occurred including LSU and Branch flushes.",
	},
	[ POWER7_PME_PM_L2_LD_HIT ] = {
		.pme_name = "PM_L2_LD_HIT",
		.pme_code = 0x36182,
		.pme_short_desc = "All successful load dispatches that were L2 hits",
		.pme_long_desc = "A load request (data or instruction) hit in the L2 directory.  Includes speculative, prefetched, and demand requests.  This event includes all requests to this L2 from all sources.  Total for all slices",
	},
	[ POWER7_PME_PM_NEST_PAIR2_AND ] = {
		.pme_name = "PM_NEST_PAIR2_AND",
		.pme_code = 0x30883,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair2 AND",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair2 AND",
	},
	[ POWER7_PME_PM_VSU1_1FLOP ] = {
		.pme_name = "PM_VSU1_1FLOP",
		.pme_code = 0xa082,
		.pme_short_desc = "one flop (fadd, fmul, fsub, fcmp, fsel, fabs, fnabs, fres, fsqrte, fneg, xsadd, xsmul, xssub, xscmp, xssel, xsabs, xsnabs, xsre, xssqrte, xsneg) operation finished",
		.pme_long_desc = "one flop (fadd, fmul, fsub, fcmp, fsel, fabs, fnabs, fres, fsqrte, fneg, xsadd, xsmul, xssub, xscmp, xssel, xsabs, xsnabs, xsre, xssqrte, xsneg) operation finished",
	},
	[ POWER7_PME_PM_IC_PREF_REQ ] = {
		.pme_name = "PM_IC_PREF_REQ",
		.pme_code = 0x408a,
		.pme_short_desc = "Instruction prefetch requests",
		.pme_long_desc = "An instruction prefetch request has been made.",
	},
	[ POWER7_PME_PM_L3_LD_HIT ] = {
		.pme_name = "PM_L3_LD_HIT",
		.pme_code = 0x2f080,
		.pme_short_desc = "L3 demand LD Hits",
		.pme_long_desc = "L3 demand LD Hits",
	},
	[ POWER7_PME_PM_GCT_NOSLOT_IC_MISS ] = {
		.pme_name = "PM_GCT_NOSLOT_IC_MISS",
		.pme_code = 0x2001a,
		.pme_short_desc = "GCT empty by I cache miss",
		.pme_long_desc = "Cycles when the Global Completion Table has no slots from this thread because of an Instruction Cache miss.",
	},
	[ POWER7_PME_PM_DISP_HELD ] = {
		.pme_name = "PM_DISP_HELD",
		.pme_code = 0x10006,
		.pme_short_desc = "Dispatch Held",
		.pme_long_desc = "Dispatch Held",
	},
	[ POWER7_PME_PM_L2_LD ] = {
		.pme_name = "PM_L2_LD",
		.pme_code = 0x16080,
		.pme_short_desc = "Data Load Count",
		.pme_long_desc = "Data Load Count",
	},
	[ POWER7_PME_PM_LSU_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU_FLUSH_SRQ",
		.pme_code = 0xc8bc,
		.pme_short_desc = "Flush: SRQ",
		.pme_long_desc = "Load Hit Store flush.  A younger load was flushed because it hits (overlaps) an older store that is already in the SRQ or in the same group.  If the real addresses match but the effective addresses do not, an alias condition exists that prevents store forwarding.  If the load and store are in the same group the load must be flushed to separate the two instructions.  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_BC_PLUS_8_CONV ] = {
		.pme_name = "PM_BC_PLUS_8_CONV",
		.pme_code = 0x40b8,
		.pme_short_desc = "BC+8 Converted",
		.pme_long_desc = "BC+8 Converted",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L31_MOD_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_L31_MOD_CYC",
		.pme_code = 0x40026,
		.pme_short_desc = "Marked ld latency Data source 0111  (L3.1 M same chip)",
		.pme_long_desc = "Marked ld latency Data source 0111  (L3.1 M same chip)",
	},
	[ POWER7_PME_PM_CMPLU_STALL_VECTOR_LONG ] = {
		.pme_name = "PM_CMPLU_STALL_VECTOR_LONG",
		.pme_code = 0x4004a,
		.pme_short_desc = "completion stall due to long latency vector instruction",
		.pme_long_desc = "completion stall due to long latency vector instruction",
	},
	[ POWER7_PME_PM_L2_RCST_BUSY_RC_FULL ] = {
		.pme_name = "PM_L2_RCST_BUSY_RC_FULL",
		.pme_code = 0x26282,
		.pme_short_desc = " L2  activated Busy to the core for stores due to all RC full",
		.pme_long_desc = " L2  activated Busy to the core for stores due to all RC full",
	},
	[ POWER7_PME_PM_TB_BIT_TRANS ] = {
		.pme_name = "PM_TB_BIT_TRANS",
		.pme_code = 0x300f8,
		.pme_short_desc = "Time Base bit transition",
		.pme_long_desc = "When the selected time base bit (as specified in MMCR0[TBSEL])transitions from 0 to 1 ",
	},
	[ POWER7_PME_PM_THERMAL_MAX ] = {
		.pme_name = "PM_THERMAL_MAX",
		.pme_code = 0x40006,
		.pme_short_desc = "Processor In Thermal MAX",
		.pme_long_desc = "The processor experienced a thermal overload condition. This bit is sticky, it remains set until cleared by software.",
	},
	[ POWER7_PME_PM_LSU1_FLUSH_ULD ] = {
		.pme_name = "PM_LSU1_FLUSH_ULD",
		.pme_code = 0xc0b2,
		.pme_short_desc = "LS 1 Flush: Unaligned Load",
		.pme_long_desc = "A load was flushed from unit 1 because it was unaligned (crossed a 64 byte boundary, or 32 byte if it missed the L1).",
	},
	[ POWER7_PME_PM_LSU1_REJECT_LHS ] = {
		.pme_name = "PM_LSU1_REJECT_LHS",
		.pme_code = 0xc0ae,
		.pme_short_desc = "LS1  Reject: Load Hit Store",
		.pme_long_desc = "Load Store Unit 1 rejected a load instruction that had an address overlap with an older store in the store queue. The store must be committed and de-allocated from the Store Queue before the load can execute successfully.",
	},
	[ POWER7_PME_PM_LSU_LRQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_LRQ_S0_ALLOC",
		.pme_code = 0xd09f,
		.pme_short_desc = "Slot 0 of LRQ valid",
		.pme_long_desc = "Slot 0 of LRQ valid",
	},
	[ POWER7_PME_PM_L3_CO_L31 ] = {
		.pme_name = "PM_L3_CO_L31",
		.pme_code = 0x4f080,
		.pme_short_desc = "L3 Castouts to Memory",
		.pme_long_desc = "L3 Castouts to Memory",
	},
	[ POWER7_PME_PM_POWER_EVENT4 ] = {
		.pme_name = "PM_POWER_EVENT4",
		.pme_code = 0x4006e,
		.pme_short_desc = "Power Management Event 4",
		.pme_long_desc = "Power Management Event 4",
	},
	[ POWER7_PME_PM_DATA_FROM_L31_SHR ] = {
		.pme_name = "PM_DATA_FROM_L31_SHR",
		.pme_code = 0x1c04e,
		.pme_short_desc = "Data loaded from another L3 on same chip shared",
		.pme_long_desc = "Data loaded from another L3 on same chip shared",
	},
	[ POWER7_PME_PM_BR_UNCOND ] = {
		.pme_name = "PM_BR_UNCOND",
		.pme_code = 0x409e,
		.pme_short_desc = "Unconditional Branch",
		.pme_long_desc = "An unconditional branch was executed.",
	},
	[ POWER7_PME_PM_LSU1_DC_PREF_STREAM_ALLOC ] = {
		.pme_name = "PM_LSU1_DC_PREF_STREAM_ALLOC",
		.pme_code = 0xd0aa,
		.pme_short_desc = "LS 1 D cache new prefetch stream allocated",
		.pme_long_desc = "LS 1 D cache new prefetch stream allocated",
	},
	[ POWER7_PME_PM_PMC4_REWIND ] = {
		.pme_name = "PM_PMC4_REWIND",
		.pme_code = 0x10020,
		.pme_short_desc = "PMC4 Rewind Event",
		.pme_long_desc = "PMC4 was counting speculatively. The speculative condition was not met and the counter was restored to its previous value.",
	},
	[ POWER7_PME_PM_L2_RCLD_DISP ] = {
		.pme_name = "PM_L2_RCLD_DISP",
		.pme_code = 0x16280,
		.pme_short_desc = " L2  RC load dispatch attempt",
		.pme_long_desc = " L2  RC load dispatch attempt",
	},
	[ POWER7_PME_PM_THRD_PRIO_2_3_CYC ] = {
		.pme_name = "PM_THRD_PRIO_2_3_CYC",
		.pme_code = 0x40b2,
		.pme_short_desc = " Cycles thread running at priority level 2 or 3",
		.pme_long_desc = " Cycles thread running at priority level 2 or 3",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L2MISS ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L2MISS",
		.pme_code = 0x4d058,
		.pme_short_desc = "Marked PTEG loaded from L2 miss",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT but not from the local L2 due to a marked load or store.",
	},
	[ POWER7_PME_PM_IC_DEMAND_L2_BHT_REDIRECT ] = {
		.pme_name = "PM_IC_DEMAND_L2_BHT_REDIRECT",
		.pme_code = 0x4098,
		.pme_short_desc = " L2 I cache demand request due to BHT redirect",
		.pme_long_desc = "A demand (not prefetch) miss to the instruction cache was sent to the L2 as a result of a branch prediction redirect (CR mispredict).",
	},
	[ POWER7_PME_PM_LSU_DERAT_MISS ] = {
		.pme_name = "PM_LSU_DERAT_MISS",
		.pme_code = 0x200f6,
		.pme_short_desc = "DERAT Reloaded due to a DERAT miss",
		.pme_long_desc = "Total D-ERAT Misses.  Requests that miss the Derat are rejected and retried until the request hits in the Erat. This may result in multiple erat misses for the same instruction.  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_IC_PREF_CANCEL_L2 ] = {
		.pme_name = "PM_IC_PREF_CANCEL_L2",
		.pme_code = 0x4094,
		.pme_short_desc = "L2 Squashed request",
		.pme_long_desc = "L2 Squashed request",
	},
	[ POWER7_PME_PM_MRK_FIN_STALL_CYC_COUNT ] = {
		.pme_name = "PM_MRK_FIN_STALL_CYC_COUNT",
		.pme_code = 0x1003d,
		.pme_short_desc = "Marked instruction Finish Stall cycles (marked finish after NTC) (use edge detect to count #)",
		.pme_long_desc = "Marked instruction Finish Stall cycles (marked finish after NTC) (use edge detect to count #)",
	},
	[ POWER7_PME_PM_BR_PRED_CCACHE ] = {
		.pme_name = "PM_BR_PRED_CCACHE",
		.pme_code = 0x40a0,
		.pme_short_desc = "Count Cache Predictions",
		.pme_long_desc = "The count value of a Branch and Count instruction was predicted",
	},
	[ POWER7_PME_PM_GCT_UTIL_1_TO_2_SLOTS ] = {
		.pme_name = "PM_GCT_UTIL_1_TO_2_SLOTS",
		.pme_code = 0x209c,
		.pme_short_desc = "GCT Utilization 1-2 entries",
		.pme_long_desc = "GCT Utilization 1-2 entries",
	},
	[ POWER7_PME_PM_MRK_ST_CMPL_INT ] = {
		.pme_name = "PM_MRK_ST_CMPL_INT",
		.pme_code = 0x30034,
		.pme_short_desc = "marked  store complete (data home) with intervention",
		.pme_long_desc = "A marked store previously sent to the memory subsystem completed (data home) after requiring intervention",
	},
	[ POWER7_PME_PM_LSU_TWO_TABLEWALK_CYC ] = {
		.pme_name = "PM_LSU_TWO_TABLEWALK_CYC",
		.pme_code = 0xd0a6,
		.pme_short_desc = "Cycles when two tablewalks pending on this thread",
		.pme_long_desc = "Cycles when two tablewalks pending on this thread",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L3MISS ] = {
		.pme_name = "PM_MRK_DATA_FROM_L3MISS",
		.pme_code = 0x2d048,
		.pme_short_desc = "Marked data loaded from L3 miss",
		.pme_long_desc = "DL1 was reloaded from beyond L3 due to a marked load.",
	},
	[ POWER7_PME_PM_GCT_NOSLOT_CYC ] = {
		.pme_name = "PM_GCT_NOSLOT_CYC",
		.pme_code = 0x100f8,
		.pme_short_desc = "No itags assigned ",
		.pme_long_desc = "Cycles when the Global Completion Table has no slots from this thread.",
	},
	[ POWER7_PME_PM_LSU_SET_MPRED ] = {
		.pme_name = "PM_LSU_SET_MPRED",
		.pme_code = 0xc0a8,
		.pme_short_desc = "Line already in cache at reload time",
		.pme_long_desc = "Line already in cache at reload time",
	},
	[ POWER7_PME_PM_FLUSH_DISP_TLBIE ] = {
		.pme_name = "PM_FLUSH_DISP_TLBIE",
		.pme_code = 0x208a,
		.pme_short_desc = "Dispatch Flush: TLBIE",
		.pme_long_desc = "Dispatch Flush: TLBIE",
	},
	[ POWER7_PME_PM_VSU1_FCONV ] = {
		.pme_name = "PM_VSU1_FCONV",
		.pme_code = 0xa0b2,
		.pme_short_desc = "Convert instruction executed",
		.pme_long_desc = "Convert instruction executed",
	},
	[ POWER7_PME_PM_DERAT_MISS_16G ] = {
		.pme_name = "PM_DERAT_MISS_16G",
		.pme_code = 0x4c05c,
		.pme_short_desc = "DERAT misses for 16G page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 16G page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_INST_FROM_LMEM ] = {
		.pme_name = "PM_INST_FROM_LMEM",
		.pme_code = 0x3404a,
		.pme_short_desc = "Instruction fetched from local memory",
		.pme_long_desc = "An instruction fetch group was fetched from memory attached to the same module this proccessor is located on.  Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_IC_DEMAND_L2_BR_REDIRECT ] = {
		.pme_name = "PM_IC_DEMAND_L2_BR_REDIRECT",
		.pme_code = 0x409a,
		.pme_short_desc = " L2 I cache demand request due to branch redirect",
		.pme_long_desc = "A demand (not prefetch) miss to the instruction cache was sent to the L2 as a result of a branch prediction redirect (either ALL mispredicted or Target).",
	},
	[ POWER7_PME_PM_CMPLU_STALL_SCALAR_LONG ] = {
		.pme_name = "PM_CMPLU_STALL_SCALAR_LONG",
		.pme_code = 0x20018,
		.pme_short_desc = "Completion stall caused by long latency scalar instruction",
		.pme_long_desc = "Completion stall caused by long latency scalar instruction",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L2 ] = {
		.pme_name = "PM_INST_PTEG_FROM_L2",
		.pme_code = 0x1e050,
		.pme_short_desc = "Instruction PTEG loaded from L2",
		.pme_long_desc = "Instruction PTEG loaded from L2",
	},
	[ POWER7_PME_PM_PTEG_FROM_L2 ] = {
		.pme_name = "PM_PTEG_FROM_L2",
		.pme_code = 0x1c050,
		.pme_short_desc = "PTEG loaded from L2",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from the local L2 due to a demand load or store.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L21_SHR_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_L21_SHR_CYC",
		.pme_code = 0x20024,
		.pme_short_desc = "Marked ld latency Data source 0100 (L2.1 S)",
		.pme_long_desc = "Marked load latency Data source 0100 (L2.1 S)",
	},
	[ POWER7_PME_PM_MRK_DTLB_MISS_4K ] = {
		.pme_name = "PM_MRK_DTLB_MISS_4K",
		.pme_code = 0x2d05a,
		.pme_short_desc = "Marked Data TLB misses for 4K page",
		.pme_long_desc = "Data TLB references to 4KB pages by a marked instruction that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_VSU0_FPSCR ] = {
		.pme_name = "PM_VSU0_FPSCR",
		.pme_code = 0xb09c,
		.pme_short_desc = "Move to/from FPSCR type instruction issued on Pipe 0",
		.pme_long_desc = "Move to/from FPSCR type instruction issued on Pipe 0",
	},
	[ POWER7_PME_PM_VSU1_VECT_DOUBLE_ISSUED ] = {
		.pme_name = "PM_VSU1_VECT_DOUBLE_ISSUED",
		.pme_code = 0xb082,
		.pme_short_desc = "Double Precision vector instruction issued on Pipe1",
		.pme_long_desc = "Double Precision vector instruction issued on Pipe1",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_RL2L3_MOD",
		.pme_code = 0x1d052,
		.pme_short_desc = "Marked PTEG loaded from remote L2 or L3 modified",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with shared (T or SL) data from an L2 or L3 on a remote module due to a marked load or store.",
	},
	[ POWER7_PME_PM_MEM0_RQ_DISP ] = {
		.pme_name = "PM_MEM0_RQ_DISP",
		.pme_code = 0x10083,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair0 Bit1",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair0 Bit1",
	},
	[ POWER7_PME_PM_L2_LD_MISS ] = {
		.pme_name = "PM_L2_LD_MISS",
		.pme_code = 0x26080,
		.pme_short_desc = "Data Load Miss",
		.pme_long_desc = "Data Load Miss",
	},
	[ POWER7_PME_PM_VMX_RESULT_SAT_1 ] = {
		.pme_name = "PM_VMX_RESULT_SAT_1",
		.pme_code = 0xb0a0,
		.pme_short_desc = "Valid result with sat=1",
		.pme_long_desc = "Valid result with sat=1",
	},
	[ POWER7_PME_PM_L1_PREF ] = {
		.pme_name = "PM_L1_PREF",
		.pme_code = 0xd8b8,
		.pme_short_desc = "L1 Prefetches",
		.pme_long_desc = "A request to prefetch data into the L1 was made",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_LMEM_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_LMEM_CYC",
		.pme_code = 0x2002c,
		.pme_short_desc = "Marked ld latency Data Source 1100 (Local Memory)",
		.pme_long_desc = "Cycles a marked load waited for data from this level of the storage system.  Counting begins when a marked load misses the data cache and ends when the data is reloaded into the data cache.  To calculate average latency divide this count by the number of marked misses to the same level.",
	},
	[ POWER7_PME_PM_GRP_IC_MISS_NONSPEC ] = {
		.pme_name = "PM_GRP_IC_MISS_NONSPEC",
		.pme_code = 0x1000c,
		.pme_short_desc = "Group experienced non-speculative I cache miss",
		.pme_long_desc = "Number of groups, counted at completion, that have encountered an instruction cache miss.",
	},
	[ POWER7_PME_PM_PB_NODE_PUMP ] = {
		.pme_name = "PM_PB_NODE_PUMP",
		.pme_code = 0x10081,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair0 Bit0",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair0 Bit0",
	},
	[ POWER7_PME_PM_SHL_MERGED ] = {
		.pme_name = "PM_SHL_MERGED",
		.pme_code = 0x5084,
		.pme_short_desc = "SHL table entry merged with existing",
		.pme_long_desc = "SHL table entry merged with existing",
	},
	[ POWER7_PME_PM_NEST_PAIR1_ADD ] = {
		.pme_name = "PM_NEST_PAIR1_ADD",
		.pme_code = 0x20881,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair1 ADD",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair1 ADD",
	},
	[ POWER7_PME_PM_DATA_FROM_L3 ] = {
		.pme_name = "PM_DATA_FROM_L3",
		.pme_code = 0x1c048,
		.pme_short_desc = "Data loaded from L3",
		.pme_long_desc = "The processor's Data Cache was reloaded from the local L3 due to a demand load.",
	},
	[ POWER7_PME_PM_LSU_FLUSH ] = {
		.pme_name = "PM_LSU_FLUSH",
		.pme_code = 0x208e,
		.pme_short_desc = "Flush initiated by LSU",
		.pme_long_desc = "A flush was initiated by the Load Store Unit.",
	},
	[ POWER7_PME_PM_LSU_SRQ_SYNC_COUNT ] = {
		.pme_name = "PM_LSU_SRQ_SYNC_COUNT",
		.pme_code = 0xd097,
		.pme_short_desc = "SRQ sync count (edge of PM_LSU_SRQ_SYNC_CYC)",
		.pme_long_desc = "SRQ sync count (edge of PM_LSU_SRQ_SYNC_CYC)",
	},
	[ POWER7_PME_PM_PMC2_OVERFLOW ] = {
		.pme_name = "PM_PMC2_OVERFLOW",
		.pme_code = 0x30010,
		.pme_short_desc = "Overflow from counter 2",
		.pme_long_desc = "Overflows from PMC2 are counted.  This effectively widens the PMC. The Overflow from the original PMC will not trigger an exception even if the PMU is configured to generate exceptions on overflow.",
	},
	[ POWER7_PME_PM_LSU_LDF ] = {
		.pme_name = "PM_LSU_LDF",
		.pme_code = 0xc884,
		.pme_short_desc = "All Scalar Loads",
		.pme_long_desc = "LSU executed Floating Point load instruction.  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_POWER_EVENT3 ] = {
		.pme_name = "PM_POWER_EVENT3",
		.pme_code = 0x3006e,
		.pme_short_desc = "Power Management Event 3",
		.pme_long_desc = "Power Management Event 3",
	},
	[ POWER7_PME_PM_DISP_WT ] = {
		.pme_name = "PM_DISP_WT",
		.pme_code = 0x30008,
		.pme_short_desc = "Dispatched Starved (not held, nothing to dispatch)",
		.pme_long_desc = "Dispatched Starved (not held, nothing to dispatch)",
	},
	[ POWER7_PME_PM_CMPLU_STALL_REJECT ] = {
		.pme_name = "PM_CMPLU_STALL_REJECT",
		.pme_code = 0x40016,
		.pme_short_desc = "Completion stall caused by reject",
		.pme_long_desc = "Following a completion stall (any period when no groups completed) the last instruction to finish before completion resumes suffered a load/store reject. This is a subset of PM_CMPLU_STALL_LSU.",
	},
	[ POWER7_PME_PM_IC_BANK_CONFLICT ] = {
		.pme_name = "PM_IC_BANK_CONFLICT",
		.pme_code = 0x4082,
		.pme_short_desc = "Read blocked due to interleave conflict.",
		.pme_long_desc = "Read blocked due to interleave conflict.",
	},
	[ POWER7_PME_PM_BR_MPRED_CR_TA ] = {
		.pme_name = "PM_BR_MPRED_CR_TA",
		.pme_code = 0x48ae,
		.pme_short_desc = "Branch mispredict - taken/not taken and target",
		.pme_long_desc = "Branch mispredict - taken/not taken and target",
	},
	[ POWER7_PME_PM_L2_INST_MISS ] = {
		.pme_name = "PM_L2_INST_MISS",
		.pme_code = 0x36082,
		.pme_short_desc = "Instruction Load Misses",
		.pme_long_desc = "Instruction Load Misses",
	},
	[ POWER7_PME_PM_CMPLU_STALL_ERAT_MISS ] = {
		.pme_name = "PM_CMPLU_STALL_ERAT_MISS",
		.pme_code = 0x40018,
		.pme_short_desc = "Completion stall caused by ERAT miss",
		.pme_long_desc = "Following a completion stall (any period when no groups completed) the last instruction to finish before completion resumes suffered an ERAT miss. This is a subset of  PM_CMPLU_STALL_REJECT.",
	},
	[ POWER7_PME_PM_NEST_PAIR2_ADD ] = {
		.pme_name = "PM_NEST_PAIR2_ADD",
		.pme_code = 0x30881,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair2 ADD",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair2 ADD",
	},
	[ POWER7_PME_PM_MRK_LSU_FLUSH ] = {
		.pme_name = "PM_MRK_LSU_FLUSH",
		.pme_code = 0xd08c,
		.pme_short_desc = "Flush: (marked) : All Cases",
		.pme_long_desc = "Marked flush initiated by LSU",
	},
	[ POWER7_PME_PM_L2_LDST ] = {
		.pme_name = "PM_L2_LDST",
		.pme_code = 0x16880,
		.pme_short_desc = "Data Load+Store Count",
		.pme_long_desc = "Data Load+Store Count",
	},
	[ POWER7_PME_PM_INST_FROM_L31_SHR ] = {
		.pme_name = "PM_INST_FROM_L31_SHR",
		.pme_code = 0x1404e,
		.pme_short_desc = "Instruction fetched from another L3 on same chip shared",
		.pme_long_desc = "Instruction fetched from another L3 on same chip shared",
	},
	[ POWER7_PME_PM_VSU0_FIN ] = {
		.pme_name = "PM_VSU0_FIN",
		.pme_code = 0xa0bc,
		.pme_short_desc = "VSU0 Finished an instruction",
		.pme_long_desc = "VSU0 Finished an instruction",
	},
	[ POWER7_PME_PM_LARX_LSU ] = {
		.pme_name = "PM_LARX_LSU",
		.pme_code = 0xc894,
		.pme_short_desc = "Larx Finished",
		.pme_long_desc = "Larx Finished",
	},
	[ POWER7_PME_PM_INST_FROM_RMEM ] = {
		.pme_name = "PM_INST_FROM_RMEM",
		.pme_code = 0x34042,
		.pme_short_desc = "Instruction fetched from remote memory",
		.pme_long_desc = "An instruction fetch group was fetched from memory attached to a different module than this proccessor is located on.  Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_DISP_CLB_HELD_TLBIE ] = {
		.pme_name = "PM_DISP_CLB_HELD_TLBIE",
		.pme_code = 0x2096,
		.pme_short_desc = "Dispatch Hold: Due to TLBIE",
		.pme_long_desc = "Dispatch Hold: Due to TLBIE",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_DMEM_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_DMEM_CYC",
		.pme_code = 0x2002e,
		.pme_short_desc = "Marked ld latency Data Source 1110 (Distant Memory)",
		.pme_long_desc = "Marked ld latency Data Source 1110 (Distant Memory)",
	},
	[ POWER7_PME_PM_BR_PRED_CR ] = {
		.pme_name = "PM_BR_PRED_CR",
		.pme_code = 0x40a8,
		.pme_short_desc = "Branch predict - taken/not taken",
		.pme_long_desc = "A conditional branch instruction was predicted as taken or not taken.",
	},
	[ POWER7_PME_PM_LSU_REJECT ] = {
		.pme_name = "PM_LSU_REJECT",
		.pme_code = 0x10064,
		.pme_short_desc = "LSU Reject (up to 2 per cycle)",
		.pme_long_desc = "The Load Store Unit rejected an instruction. Combined Unit 0 + 1",
	},
	[ POWER7_PME_PM_GCT_UTIL_3_TO_6_SLOTS ] = {
		.pme_name = "PM_GCT_UTIL_3_TO_6_SLOTS",
		.pme_code = 0x209e,
		.pme_short_desc = "GCT Utilization 3-6 entries",
		.pme_long_desc = "GCT Utilization 3-6 entries",
	},
	[ POWER7_PME_PM_CMPLU_STALL_END_GCT_NOSLOT ] = {
		.pme_name = "PM_CMPLU_STALL_END_GCT_NOSLOT",
		.pme_code = 0x10028,
		.pme_short_desc = "Count ended because GCT went empty",
		.pme_long_desc = "Count ended because GCT went empty",
	},
	[ POWER7_PME_PM_LSU0_REJECT_LMQ_FULL ] = {
		.pme_name = "PM_LSU0_REJECT_LMQ_FULL",
		.pme_code = 0xc0a4,
		.pme_short_desc = "LS0 Reject: LMQ Full (LHR)",
		.pme_long_desc = "Total cycles the Load Store Unit 0 is busy rejecting instructions because the Load Miss Queue was full. The LMQ has eight entries.  If all eight entries are full, subsequent load instructions are rejected.",
	},
	[ POWER7_PME_PM_VSU_FEST ] = {
		.pme_name = "PM_VSU_FEST",
		.pme_code = 0xa8b8,
		.pme_short_desc = "Estimate instruction executed",
		.pme_long_desc = "Estimate instruction executed",
	},
	[ POWER7_PME_PM_NEST_PAIR0_AND ] = {
		.pme_name = "PM_NEST_PAIR0_AND",
		.pme_code = 0x10883,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair0 AND",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair0 AND",
	},
	[ POWER7_PME_PM_PTEG_FROM_L3 ] = {
		.pme_name = "PM_PTEG_FROM_L3",
		.pme_code = 0x2c050,
		.pme_short_desc = "PTEG loaded from L3",
		.pme_long_desc = "A Page Table Entry was loaded into the TLB from the local L3 due to a demand load.",
	},
	[ POWER7_PME_PM_POWER_EVENT2 ] = {
		.pme_name = "PM_POWER_EVENT2",
		.pme_code = 0x2006e,
		.pme_short_desc = "Power Management Event 2",
		.pme_long_desc = "Power Management Event 2",
	},
	[ POWER7_PME_PM_IC_PREF_CANCEL_PAGE ] = {
		.pme_name = "PM_IC_PREF_CANCEL_PAGE",
		.pme_code = 0x4090,
		.pme_short_desc = "Prefetch Canceled due to page boundary",
		.pme_long_desc = "Prefetch Canceled due to page boundary",
	},
	[ POWER7_PME_PM_VSU0_FSQRT_FDIV ] = {
		.pme_name = "PM_VSU0_FSQRT_FDIV",
		.pme_code = 0xa088,
		.pme_short_desc = "four flops operation (fdiv,fsqrt,xsdiv,xssqrt) Scalar Instructions only!",
		.pme_long_desc = "four flops operation (fdiv,fsqrt,xsdiv,xssqrt) Scalar Instructions only!",
	},
	[ POWER7_PME_PM_MRK_GRP_CMPL ] = {
		.pme_name = "PM_MRK_GRP_CMPL",
		.pme_code = 0x40030,
		.pme_short_desc = "Marked group complete",
		.pme_long_desc = "A group containing a sampled instruction completed.  Microcoded instructions that span multiple groups will generate this event once per group.",
	},
	[ POWER7_PME_PM_VSU0_SCAL_DOUBLE_ISSUED ] = {
		.pme_name = "PM_VSU0_SCAL_DOUBLE_ISSUED",
		.pme_code = 0xb088,
		.pme_short_desc = "Double Precision scalar instruction issued on Pipe0",
		.pme_long_desc = "Double Precision scalar instruction issued on Pipe0",
	},
	[ POWER7_PME_PM_GRP_DISP ] = {
		.pme_name = "PM_GRP_DISP",
		.pme_code = 0x3000a,
		.pme_short_desc = "dispatch_success (Group Dispatched)",
		.pme_long_desc = "A group was dispatched",
	},
	[ POWER7_PME_PM_LSU0_LDX ] = {
		.pme_name = "PM_LSU0_LDX",
		.pme_code = 0xc088,
		.pme_short_desc = "LS0 Vector Loads",
		.pme_long_desc = "LS0 Vector Loads",
	},
	[ POWER7_PME_PM_DATA_FROM_L2 ] = {
		.pme_name = "PM_DATA_FROM_L2",
		.pme_code = 0x1c040,
		.pme_short_desc = "Data loaded from L2",
		.pme_long_desc = "The processor's Data Cache was reloaded from the local L2 due to a demand load.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_RL2L3_MOD",
		.pme_code = 0x1d042,
		.pme_short_desc = "Marked data loaded from remote L2 or L3 modified",
		.pme_long_desc = "The processor's Data Cache was reloaded with modified (M) data from an L2  or L3 on a remote module due to a marked load.",
	},
	[ POWER7_PME_PM_LD_REF_L1 ] = {
		.pme_name = "PM_LD_REF_L1",
		.pme_code = 0xc880,
		.pme_short_desc = " L1 D cache load references counted at finish",
		.pme_long_desc = " L1 D cache load references counted at finish",
	},
	[ POWER7_PME_PM_VSU0_VECT_DOUBLE_ISSUED ] = {
		.pme_name = "PM_VSU0_VECT_DOUBLE_ISSUED",
		.pme_code = 0xb080,
		.pme_short_desc = "Double Precision vector instruction issued on Pipe0",
		.pme_long_desc = "Double Precision vector instruction issued on Pipe0",
	},
	[ POWER7_PME_PM_VSU1_2FLOP_DOUBLE ] = {
		.pme_name = "PM_VSU1_2FLOP_DOUBLE",
		.pme_code = 0xa08e,
		.pme_short_desc = "two flop DP vector operation (xvadddp, xvmuldp, xvsubdp, xvcmpdp, xvseldp, xvabsdp, xvnabsdp, xvredp ,xvsqrtedp, vxnegdp)",
		.pme_long_desc = "two flop DP vector operation (xvadddp, xvmuldp, xvsubdp, xvcmpdp, xvseldp, xvabsdp, xvnabsdp, xvredp ,xvsqrtedp, vxnegdp)",
	},
	[ POWER7_PME_PM_THRD_PRIO_6_7_CYC ] = {
		.pme_name = "PM_THRD_PRIO_6_7_CYC",
		.pme_code = 0x40b6,
		.pme_short_desc = " Cycles thread running at priority level 6 or 7",
		.pme_long_desc = " Cycles thread running at priority level 6 or 7",
	},
	[ POWER7_PME_PM_BC_PLUS_8_RSLV_TAKEN ] = {
		.pme_name = "PM_BC_PLUS_8_RSLV_TAKEN",
		.pme_code = 0x40ba,
		.pme_short_desc = "BC+8 Resolve outcome was Taken, resulting in the conditional instruction being canceled",
		.pme_long_desc = "BC+8 Resolve outcome was Taken, resulting in the conditional instruction being canceled",
	},
	[ POWER7_PME_PM_BR_MPRED_CR ] = {
		.pme_name = "PM_BR_MPRED_CR",
		.pme_code = 0x40ac,
		.pme_short_desc = "Branch mispredict - taken/not taken",
		.pme_long_desc = "A conditional branch instruction was incorrectly predicted as taken or not taken.  The branch execution unit detects a branch mispredict because the CR value is opposite of the predicted value. This will result in a branch redirect flush if not overfidden by a flush of an older instruction.",
	},
	[ POWER7_PME_PM_L3_CO_MEM ] = {
		.pme_name = "PM_L3_CO_MEM",
		.pme_code = 0x4f082,
		.pme_short_desc = "L3 Castouts to L3.1",
		.pme_long_desc = "L3 Castouts to L3.1",
	},
	[ POWER7_PME_PM_LD_MISS_L1 ] = {
		.pme_name = "PM_LD_MISS_L1",
		.pme_code = 0x400f0,
		.pme_short_desc = "Load Missed L1",
		.pme_long_desc = "Load references that miss the Level 1 Data cache. Combined unit 0 + 1.",
	},
	[ POWER7_PME_PM_DATA_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_DATA_FROM_RL2L3_MOD",
		.pme_code = 0x1c042,
		.pme_short_desc = "Data loaded from remote L2 or L3 modified",
		.pme_long_desc = "The processor's Data Cache was reloaded with modified (M) data from an L2  or L3 on a remote module due to a demand load",
	},
	[ POWER7_PME_PM_LSU_SRQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_SRQ_FULL_CYC",
		.pme_code = 0x1001a,
		.pme_short_desc = "Storage Queue is full and is blocking dispatch",
		.pme_long_desc = "Cycles the Store Request Queue is full.",
	},
	[ POWER7_PME_PM_TABLEWALK_CYC ] = {
		.pme_name = "PM_TABLEWALK_CYC",
		.pme_code = 0x10026,
		.pme_short_desc = "Cycles when a tablewalk (I or D) is active",
		.pme_long_desc = "Cycles doing instruction or data tablewalks",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_RMEM ] = {
		.pme_name = "PM_MRK_PTEG_FROM_RMEM",
		.pme_code = 0x3d052,
		.pme_short_desc = "Marked PTEG loaded from remote memory",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT. POWER6 does not have a TLB",
	},
	[ POWER7_PME_PM_LSU_SRQ_STFWD ] = {
		.pme_name = "PM_LSU_SRQ_STFWD",
		.pme_code = 0xc8a0,
		.pme_short_desc = "Load got data from a store",
		.pme_long_desc = "Data from a store instruction was forwarded to a load.  A load that misses L1 but becomes a store forward is treated as a load miss and it causes the DL1 load miss event to be counted.  It does not go into the LMQ. If a load that hits L1 but becomes a store forward, then it's not treated as a load miss. Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_RMEM ] = {
		.pme_name = "PM_INST_PTEG_FROM_RMEM",
		.pme_code = 0x3e052,
		.pme_short_desc = "Instruction PTEG loaded from remote memory",
		.pme_long_desc = "Instruction PTEG loaded from remote memory",
	},
	[ POWER7_PME_PM_FXU0_FIN ] = {
		.pme_name = "PM_FXU0_FIN",
		.pme_code = 0x10004,
		.pme_short_desc = "FXU0 Finished",
		.pme_long_desc = "The Fixed Point unit 0 finished an instruction and produced a result.  Instructions that finish may not necessary complete.",
	},
	[ POWER7_PME_PM_LSU1_L1_SW_PREF ] = {
		.pme_name = "PM_LSU1_L1_SW_PREF",
		.pme_code = 0xc09e,
		.pme_short_desc = "LSU1 Software L1 Prefetches, including SW Transient Prefetches",
		.pme_long_desc = "LSU1 Software L1 Prefetches, including SW Transient Prefetches",
	},
	[ POWER7_PME_PM_PTEG_FROM_L31_MOD ] = {
		.pme_name = "PM_PTEG_FROM_L31_MOD",
		.pme_code = 0x1c054,
		.pme_short_desc = "PTEG loaded from another L3 on same chip modified",
		.pme_long_desc = "PTEG loaded from another L3 on same chip modified",
	},
	[ POWER7_PME_PM_PMC5_OVERFLOW ] = {
		.pme_name = "PM_PMC5_OVERFLOW",
		.pme_code = 0x10024,
		.pme_short_desc = "Overflow from counter 5",
		.pme_long_desc = "Overflows from PMC5 are counted.  This effectively widens the PMC. The Overflow from the original PMC will not trigger an exception even if the PMU is configured to generate exceptions on overflow.",
	},
	[ POWER7_PME_PM_LD_REF_L1_LSU1 ] = {
		.pme_name = "PM_LD_REF_L1_LSU1",
		.pme_code = 0xc082,
		.pme_short_desc = "LS1 L1 D cache load references counted at finish",
		.pme_long_desc = "Load references to Level 1 Data Cache, by unit 1.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L21_SHR ] = {
		.pme_name = "PM_INST_PTEG_FROM_L21_SHR",
		.pme_code = 0x4e056,
		.pme_short_desc = "Instruction PTEG loaded from another L2 on same chip shared",
		.pme_long_desc = "Instruction PTEG loaded from another L2 on same chip shared",
	},
	[ POWER7_PME_PM_CMPLU_STALL_THRD ] = {
		.pme_name = "PM_CMPLU_STALL_THRD",
		.pme_code = 0x1001c,
		.pme_short_desc = "Completion Stalled due to thread conflict.  Group ready to complete but it was another thread's turn",
		.pme_long_desc = "Completion Stalled due to thread conflict.  Group ready to complete but it was another thread's turn",
	},
	[ POWER7_PME_PM_DATA_FROM_RMEM ] = {
		.pme_name = "PM_DATA_FROM_RMEM",
		.pme_code = 0x3c042,
		.pme_short_desc = "Data loaded from remote memory",
		.pme_long_desc = "The processor's Data Cache was reloaded from memory attached to a different module than this proccessor is located on.",
	},
	[ POWER7_PME_PM_VSU0_SCAL_SINGLE_ISSUED ] = {
		.pme_name = "PM_VSU0_SCAL_SINGLE_ISSUED",
		.pme_code = 0xb084,
		.pme_short_desc = "Single Precision scalar instruction issued on Pipe0",
		.pme_long_desc = "Single Precision scalar instruction issued on Pipe0",
	},
	[ POWER7_PME_PM_BR_MPRED_LSTACK ] = {
		.pme_name = "PM_BR_MPRED_LSTACK",
		.pme_code = 0x40a6,
		.pme_short_desc = "Branch Mispredict due to Link Stack",
		.pme_long_desc = "Branch Mispredict due to Link Stack",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_RL2L3_MOD_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_RL2L3_MOD_CYC",
		.pme_code = 0x40028,
		.pme_short_desc = "Marked ld latency Data source 1001 (L2.5/L3.5 M same 4 chip node)",
		.pme_long_desc = "Marked ld latency Data source 1001 (L2.5/L3.5 M same 4 chip node)",
	},
	[ POWER7_PME_PM_LSU0_FLUSH_UST ] = {
		.pme_name = "PM_LSU0_FLUSH_UST",
		.pme_code = 0xc0b4,
		.pme_short_desc = "LS0 Flush: Unaligned Store",
		.pme_long_desc = "A store was flushed from unit 0 because it was unaligned (crossed a 4K boundary).",
	},
	[ POWER7_PME_PM_LSU_NCST ] = {
		.pme_name = "PM_LSU_NCST",
		.pme_code = 0xc090,
		.pme_short_desc = "Non-cachable Stores sent to nest",
		.pme_long_desc = "Non-cachable Stores sent to nest",
	},
	[ POWER7_PME_PM_BR_TAKEN ] = {
		.pme_name = "PM_BR_TAKEN",
		.pme_code = 0x20004,
		.pme_short_desc = "Branch Taken",
		.pme_long_desc = "A branch instruction was taken. This could have been a conditional branch or an unconditional branch",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_LMEM ] = {
		.pme_name = "PM_INST_PTEG_FROM_LMEM",
		.pme_code = 0x4e052,
		.pme_short_desc = "Instruction PTEG loaded from local memory",
		.pme_long_desc = "Instruction PTEG loaded from local memory",
	},
	[ POWER7_PME_PM_GCT_NOSLOT_BR_MPRED_IC_MISS ] = {
		.pme_name = "PM_GCT_NOSLOT_BR_MPRED_IC_MISS",
		.pme_code = 0x4001c,
		.pme_short_desc = "GCT empty by branch  mispredict + IC miss",
		.pme_long_desc = "No slot in GCT caused by branch mispredict or I cache miss",
	},
	[ POWER7_PME_PM_DTLB_MISS_4K ] = {
		.pme_name = "PM_DTLB_MISS_4K",
		.pme_code = 0x2c05a,
		.pme_short_desc = "Data TLB miss for 4K page",
		.pme_long_desc = "Data TLB references to 4KB pages that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_PMC4_SAVED ] = {
		.pme_name = "PM_PMC4_SAVED",
		.pme_code = 0x30022,
		.pme_short_desc = "PMC4 Rewind Value saved (matched condition)",
		.pme_long_desc = "PMC4 was counting speculatively. The speculative condition was met and the counter value was committed by copying it to the backup register.",
	},
	[ POWER7_PME_PM_VSU1_PERMUTE_ISSUED ] = {
		.pme_name = "PM_VSU1_PERMUTE_ISSUED",
		.pme_code = 0xb092,
		.pme_short_desc = "Permute VMX Instruction Issued",
		.pme_long_desc = "Permute VMX Instruction Issued",
	},
	[ POWER7_PME_PM_SLB_MISS ] = {
		.pme_name = "PM_SLB_MISS",
		.pme_code = 0xd890,
		.pme_short_desc = "Data + Instruction SLB Miss - Total of all segment sizes",
		.pme_long_desc = "Total of all Segment Lookaside Buffer (SLB) misses, Instructions + Data.",
	},
	[ POWER7_PME_PM_LSU1_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU1_FLUSH_LRQ",
		.pme_code = 0xc0ba,
		.pme_short_desc = "LS1 Flush: LRQ",
		.pme_long_desc = "Load Hit Load or Store Hit Load flush.  A younger load was flushed from unit 1 because it executed before an older store and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER7_PME_PM_DTLB_MISS ] = {
		.pme_name = "PM_DTLB_MISS",
		.pme_code = 0x300fc,
		.pme_short_desc = "TLB reload valid",
		.pme_long_desc = "Data TLB misses, all page sizes.",
	},
	[ POWER7_PME_PM_VSU1_FRSP ] = {
		.pme_name = "PM_VSU1_FRSP",
		.pme_code = 0xa0b6,
		.pme_short_desc = "Round to single precision instruction executed",
		.pme_long_desc = "Round to single precision instruction executed",
	},
	[ POWER7_PME_PM_VSU_VECTOR_DOUBLE_ISSUED ] = {
		.pme_name = "PM_VSU_VECTOR_DOUBLE_ISSUED",
		.pme_code = 0xb880,
		.pme_short_desc = "Double Precision vector instruction issued on Pipe0",
		.pme_long_desc = "Double Precision vector instruction issued on Pipe0",
	},
	[ POWER7_PME_PM_L2_CASTOUT_SHR ] = {
		.pme_name = "PM_L2_CASTOUT_SHR",
		.pme_code = 0x16182,
		.pme_short_desc = "L2 Castouts - Shared (T, Te, Si, S)",
		.pme_long_desc = "An L2 line in the Shared state was castout. Total for all slices.",
	},
	[ POWER7_PME_PM_DATA_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_DATA_FROM_DL2L3_SHR",
		.pme_code = 0x3c044,
		.pme_short_desc = "Data loaded from distant L2 or L3 shared",
		.pme_long_desc = "The processor's Data Cache was reloaded with shared (T or SL) data from an L2 or L3 on a distant module due to a demand load",
	},
	[ POWER7_PME_PM_VSU1_STF ] = {
		.pme_name = "PM_VSU1_STF",
		.pme_code = 0xb08e,
		.pme_short_desc = "FPU store (SP or DP) issued on Pipe1",
		.pme_long_desc = "FPU store (SP or DP) issued on Pipe1",
	},
	[ POWER7_PME_PM_ST_FIN ] = {
		.pme_name = "PM_ST_FIN",
		.pme_code = 0x200f0,
		.pme_short_desc = "Store Instructions Finished",
		.pme_long_desc = "Store requests sent to the nest.",
	},
	[ POWER7_PME_PM_PTEG_FROM_L21_SHR ] = {
		.pme_name = "PM_PTEG_FROM_L21_SHR",
		.pme_code = 0x4c056,
		.pme_short_desc = "PTEG loaded from another L2 on same chip shared",
		.pme_long_desc = "PTEG loaded from another L2 on same chip shared",
	},
	[ POWER7_PME_PM_L2_LOC_GUESS_WRONG ] = {
		.pme_name = "PM_L2_LOC_GUESS_WRONG",
		.pme_code = 0x26480,
		.pme_short_desc = "L2 guess loc and guess was not correct (ie data remote)",
		.pme_long_desc = "L2 guess loc and guess was not correct (ie data remote)",
	},
	[ POWER7_PME_PM_MRK_STCX_FAIL ] = {
		.pme_name = "PM_MRK_STCX_FAIL",
		.pme_code = 0xd08e,
		.pme_short_desc = "Marked STCX failed",
		.pme_long_desc = "A marked stcx (stwcx or stdcx) failed",
	},
	[ POWER7_PME_PM_LSU0_REJECT_LHS ] = {
		.pme_name = "PM_LSU0_REJECT_LHS",
		.pme_code = 0xc0ac,
		.pme_short_desc = "LS0 Reject: Load Hit Store",
		.pme_long_desc = "Load Store Unit 0 rejected a load instruction that had an address overlap with an older store in the store queue. The store must be committed and de-allocated from the Store Queue before the load can execute successfully.",
	},
	[ POWER7_PME_PM_IC_PREF_CANCEL_HIT ] = {
		.pme_name = "PM_IC_PREF_CANCEL_HIT",
		.pme_code = 0x4092,
		.pme_short_desc = "Prefetch Canceled due to icache hit",
		.pme_long_desc = "Prefetch Canceled due to icache hit",
	},
	[ POWER7_PME_PM_L3_PREF_BUSY ] = {
		.pme_name = "PM_L3_PREF_BUSY",
		.pme_code = 0x4f080,
		.pme_short_desc = "Prefetch machines >= threshold (8,16,20,24)",
		.pme_long_desc = "Prefetch machines >= threshold (8,16,20,24)",
	},
	[ POWER7_PME_PM_MRK_BRU_FIN ] = {
		.pme_name = "PM_MRK_BRU_FIN",
		.pme_code = 0x2003a,
		.pme_short_desc = "bru marked instr finish",
		.pme_long_desc = "The branch unit finished a marked instruction. Instructions that finish may not necessary complete.",
	},
	[ POWER7_PME_PM_LSU1_NCLD ] = {
		.pme_name = "PM_LSU1_NCLD",
		.pme_code = 0xc08e,
		.pme_short_desc = "LS1 Non-cachable Loads counted at finish",
		.pme_long_desc = "A non-cacheable load was executed by Unit 0.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L31_MOD ] = {
		.pme_name = "PM_INST_PTEG_FROM_L31_MOD",
		.pme_code = 0x1e054,
		.pme_short_desc = "Instruction PTEG loaded from another L3 on same chip modified",
		.pme_long_desc = "Instruction PTEG loaded from another L3 on same chip modified",
	},
	[ POWER7_PME_PM_LSU_NCLD ] = {
		.pme_name = "PM_LSU_NCLD",
		.pme_code = 0xc88c,
		.pme_short_desc = "Non-cachable Loads counted at finish",
		.pme_long_desc = "A non-cacheable load was executed. Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_LSU_LDX ] = {
		.pme_name = "PM_LSU_LDX",
		.pme_code = 0xc888,
		.pme_short_desc = "All Vector loads (vsx vector + vmx vector)",
		.pme_long_desc = "All Vector loads (vsx vector + vmx vector)",
	},
	[ POWER7_PME_PM_L2_LOC_GUESS_CORRECT ] = {
		.pme_name = "PM_L2_LOC_GUESS_CORRECT",
		.pme_code = 0x16480,
		.pme_short_desc = "L2 guess loc and guess was correct (ie data local)",
		.pme_long_desc = "L2 guess loc and guess was correct (ie data local)",
	},
	[ POWER7_PME_PM_THRESH_TIMEO ] = {
		.pme_name = "PM_THRESH_TIMEO",
		.pme_code = 0x10038,
		.pme_short_desc = "Threshold  timeout  event",
		.pme_long_desc = "The threshold timer expired",
	},
	[ POWER7_PME_PM_L3_PREF_ST ] = {
		.pme_name = "PM_L3_PREF_ST",
		.pme_code = 0xd0ae,
		.pme_short_desc = "L3 cache ST prefetches",
		.pme_long_desc = "L3 cache ST prefetches",
	},
	[ POWER7_PME_PM_DISP_CLB_HELD_SYNC ] = {
		.pme_name = "PM_DISP_CLB_HELD_SYNC",
		.pme_code = 0x2098,
		.pme_short_desc = "Dispatch/CLB Hold: Sync type instruction",
		.pme_long_desc = "Dispatch/CLB Hold: Sync type instruction",
	},
	[ POWER7_PME_PM_VSU_SIMPLE_ISSUED ] = {
		.pme_name = "PM_VSU_SIMPLE_ISSUED",
		.pme_code = 0xb894,
		.pme_short_desc = "Simple VMX instruction issued",
		.pme_long_desc = "Simple VMX instruction issued",
	},
	[ POWER7_PME_PM_VSU1_SINGLE ] = {
		.pme_name = "PM_VSU1_SINGLE",
		.pme_code = 0xa0aa,
		.pme_short_desc = "FPU single precision",
		.pme_long_desc = "VSU1 executed single precision instruction",
	},
	[ POWER7_PME_PM_DATA_TABLEWALK_CYC ] = {
		.pme_name = "PM_DATA_TABLEWALK_CYC",
		.pme_code = 0x3001a,
		.pme_short_desc = "Data Tablewalk Active",
		.pme_long_desc = "Cycles a translation tablewalk is active.  While a tablewalk is active any request attempting to access the TLB will be rejected and retried.",
	},
	[ POWER7_PME_PM_L2_RC_ST_DONE ] = {
		.pme_name = "PM_L2_RC_ST_DONE",
		.pme_code = 0x36380,
		.pme_short_desc = "RC did st to line that was Tx or Sx",
		.pme_long_desc = "RC did st to line that was Tx or Sx",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L21_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L21_MOD",
		.pme_code = 0x3d056,
		.pme_short_desc = "Marked PTEG loaded from another L2 on same chip modified",
		.pme_long_desc = "Marked PTEG loaded from another L2 on same chip modified",
	},
	[ POWER7_PME_PM_LARX_LSU1 ] = {
		.pme_name = "PM_LARX_LSU1",
		.pme_code = 0xc096,
		.pme_short_desc = "ls1 Larx Finished",
		.pme_long_desc = "A larx (lwarx or ldarx) was executed on side 1 ",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_RMEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_RMEM",
		.pme_code = 0x3d042,
		.pme_short_desc = "Marked data loaded from remote memory",
		.pme_long_desc = "The processor's Data Cache was reloaded due to a marked load from memory attached to a different module than this proccessor is located on.",
	},
	[ POWER7_PME_PM_DISP_CLB_HELD ] = {
		.pme_name = "PM_DISP_CLB_HELD",
		.pme_code = 0x2090,
		.pme_short_desc = "CLB Hold: Any Reason",
		.pme_long_desc = "CLB Hold: Any Reason",
	},
	[ POWER7_PME_PM_DERAT_MISS_4K ] = {
		.pme_name = "PM_DERAT_MISS_4K",
		.pme_code = 0x1c05c,
		.pme_short_desc = "DERAT misses for 4K page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 4K page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_L2_RCLD_DISP_FAIL_ADDR ] = {
		.pme_name = "PM_L2_RCLD_DISP_FAIL_ADDR",
		.pme_code = 0x16282,
		.pme_short_desc = " L2  RC load dispatch attempt failed due to address collision with RC/CO/SN/SQ",
		.pme_long_desc = " L2  RC load dispatch attempt failed due to address collision with RC/CO/SN/SQ",
	},
	[ POWER7_PME_PM_SEG_EXCEPTION ] = {
		.pme_name = "PM_SEG_EXCEPTION",
		.pme_code = 0x28a4,
		.pme_short_desc = "ISEG + DSEG Exception",
		.pme_long_desc = "ISEG + DSEG Exception",
	},
	[ POWER7_PME_PM_FLUSH_DISP_SB ] = {
		.pme_name = "PM_FLUSH_DISP_SB",
		.pme_code = 0x208c,
		.pme_short_desc = "Dispatch Flush: Scoreboard",
		.pme_long_desc = "Dispatch Flush: Scoreboard",
	},
	[ POWER7_PME_PM_L2_DC_INV ] = {
		.pme_name = "PM_L2_DC_INV",
		.pme_code = 0x26182,
		.pme_short_desc = "Dcache invalidates from L2 ",
		.pme_long_desc = "The L2 invalidated a line in processor's data cache.  This is caused by the L2 line being cast out or invalidated. Total for all slices",
	},
	[ POWER7_PME_PM_PTEG_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_PTEG_FROM_DL2L3_MOD",
		.pme_code = 0x4c054,
		.pme_short_desc = "PTEG loaded from distant L2 or L3 modified",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with modified (M) data from an L2  or L3 on a distant module due to a demand load or store.",
	},
	[ POWER7_PME_PM_DSEG ] = {
		.pme_name = "PM_DSEG",
		.pme_code = 0x20a6,
		.pme_short_desc = "DSEG Exception",
		.pme_long_desc = "DSEG Exception",
	},
	[ POWER7_PME_PM_BR_PRED_LSTACK ] = {
		.pme_name = "PM_BR_PRED_LSTACK",
		.pme_code = 0x40a2,
		.pme_short_desc = "Link Stack Predictions",
		.pme_long_desc = "The target address of a Branch to Link instruction was predicted by the link stack.",
	},
	[ POWER7_PME_PM_VSU0_STF ] = {
		.pme_name = "PM_VSU0_STF",
		.pme_code = 0xb08c,
		.pme_short_desc = "FPU store (SP or DP) issued on Pipe0",
		.pme_long_desc = "FPU store (SP or DP) issued on Pipe0",
	},
	[ POWER7_PME_PM_LSU_FX_FIN ] = {
		.pme_name = "PM_LSU_FX_FIN",
		.pme_code = 0x10066,
		.pme_short_desc = "LSU Finished a FX operation  (up to 2 per cycle)",
		.pme_long_desc = "LSU Finished a FX operation  (up to 2 per cycle)",
	},
	[ POWER7_PME_PM_DERAT_MISS_16M ] = {
		.pme_name = "PM_DERAT_MISS_16M",
		.pme_code = 0x3c05c,
		.pme_short_desc = "DERAT misses for 16M page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 16M page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_DL2L3_MOD",
		.pme_code = 0x4d054,
		.pme_short_desc = "Marked PTEG loaded from distant L2 or L3 modified",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with modified (M) data from an L2  or L3 on a distant module due to a marked load or store.",
	},
	[ POWER7_PME_PM_GCT_UTIL_11_PLUS_SLOTS ] = {
		.pme_name = "PM_GCT_UTIL_11_PLUS_SLOTS",
		.pme_code = 0x20a2,
		.pme_short_desc = "GCT Utilization 11+ entries",
		.pme_long_desc = "GCT Utilization 11+ entries",
	},
	[ POWER7_PME_PM_INST_FROM_L3 ] = {
		.pme_name = "PM_INST_FROM_L3",
		.pme_code = 0x14048,
		.pme_short_desc = "Instruction fetched from L3",
		.pme_long_desc = "An instruction fetch group was fetched from L3. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_MRK_IFU_FIN ] = {
		.pme_name = "PM_MRK_IFU_FIN",
		.pme_code = 0x3003a,
		.pme_short_desc = "IFU non-branch marked instruction finished",
		.pme_long_desc = "The Instruction Fetch Unit finished a marked instruction.",
	},
	[ POWER7_PME_PM_ITLB_MISS ] = {
		.pme_name = "PM_ITLB_MISS",
		.pme_code = 0x400fc,
		.pme_short_desc = "ITLB Reloaded (always zero on POWER6)",
		.pme_long_desc = "A TLB miss for an Instruction Fetch has occurred",
	},
	[ POWER7_PME_PM_VSU_STF ] = {
		.pme_name = "PM_VSU_STF",
		.pme_code = 0xb88c,
		.pme_short_desc = "FPU store (SP or DP) issued on Pipe0",
		.pme_long_desc = "FPU store (SP or DP) issued on Pipe0",
	},
	[ POWER7_PME_PM_LSU_FLUSH_UST ] = {
		.pme_name = "PM_LSU_FLUSH_UST",
		.pme_code = 0xc8b4,
		.pme_short_desc = "Flush: Unaligned Store",
		.pme_long_desc = "A store was flushed because it was unaligned (crossed a 4K boundary).  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_L2_LDST_MISS ] = {
		.pme_name = "PM_L2_LDST_MISS",
		.pme_code = 0x26880,
		.pme_short_desc = "Data Load+Store Miss",
		.pme_long_desc = "Data Load+Store Miss",
	},
	[ POWER7_PME_PM_FXU1_FIN ] = {
		.pme_name = "PM_FXU1_FIN",
		.pme_code = 0x40004,
		.pme_short_desc = "FXU1 Finished",
		.pme_long_desc = "The Fixed Point unit 1 finished an instruction and produced a result. Instructions that finish may not necessary complete.",
	},
	[ POWER7_PME_PM_SHL_DEALLOCATED ] = {
		.pme_name = "PM_SHL_DEALLOCATED",
		.pme_code = 0x5080,
		.pme_short_desc = "SHL Table entry deallocated",
		.pme_long_desc = "SHL Table entry deallocated",
	},
	[ POWER7_PME_PM_L2_SN_M_WR_DONE ] = {
		.pme_name = "PM_L2_SN_M_WR_DONE",
		.pme_code = 0x46382,
		.pme_short_desc = "SNP dispatched for a write and was M",
		.pme_long_desc = "SNP dispatched for a write and was M",
	},
	[ POWER7_PME_PM_LSU_REJECT_SET_MPRED ] = {
		.pme_name = "PM_LSU_REJECT_SET_MPRED",
		.pme_code = 0xc8a8,
		.pme_short_desc = "Reject: Set Predict Wrong",
		.pme_long_desc = "The Load Store Unit rejected an instruction because the cache set was improperly predicted. This is a fast reject and will be immediately redispatched. Combined Unit 0 + 1",
	},
	[ POWER7_PME_PM_L3_PREF_LD ] = {
		.pme_name = "PM_L3_PREF_LD",
		.pme_code = 0xd0ac,
		.pme_short_desc = "L3 cache LD prefetches",
		.pme_long_desc = "L3 cache LD prefetches",
	},
	[ POWER7_PME_PM_L2_SN_M_RD_DONE ] = {
		.pme_name = "PM_L2_SN_M_RD_DONE",
		.pme_code = 0x46380,
		.pme_short_desc = "SNP dispatched for a read and was M",
		.pme_long_desc = "SNP dispatched for a read and was M",
	},
	[ POWER7_PME_PM_MRK_DERAT_MISS_16G ] = {
		.pme_name = "PM_MRK_DERAT_MISS_16G",
		.pme_code = 0x4d05c,
		.pme_short_desc = "Marked DERAT misses for 16G page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 16G page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_VSU_FCONV ] = {
		.pme_name = "PM_VSU_FCONV",
		.pme_code = 0xa8b0,
		.pme_short_desc = "Convert instruction executed",
		.pme_long_desc = "Convert instruction executed",
	},
	[ POWER7_PME_PM_ANY_THRD_RUN_CYC ] = {
		.pme_name = "PM_ANY_THRD_RUN_CYC",
		.pme_code = 0x100fa,
		.pme_short_desc = "One of threads in run_cycles ",
		.pme_long_desc = "One of threads in run_cycles ",
	},
	[ POWER7_PME_PM_LSU_LMQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_LMQ_FULL_CYC",
		.pme_code = 0xd0a4,
		.pme_short_desc = "LMQ full",
		.pme_long_desc = "The Load Miss Queue was full.",
	},
	[ POWER7_PME_PM_MRK_LSU_REJECT_LHS ] = {
		.pme_name = "PM_MRK_LSU_REJECT_LHS",
		.pme_code = 0xd082,
		.pme_short_desc = " Reject(marked): Load Hit Store",
		.pme_long_desc = "The Load Store Unit rejected a marked load instruction that had an address overlap with an older store in the store queue. The store must be committed and de-allocated from the Store Queue before the load can execute successfully",
	},
	[ POWER7_PME_PM_MRK_LD_MISS_L1_CYC ] = {
		.pme_name = "PM_MRK_LD_MISS_L1_CYC",
		.pme_code = 0x4003e,
		.pme_short_desc = "L1 data load miss cycles",
		.pme_long_desc = "L1 data load miss cycles",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L2_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2_CYC",
		.pme_code = 0x20020,
		.pme_short_desc = "Marked ld latency Data source 0000 (L2 hit)",
		.pme_long_desc = "Cycles a marked load waited for data from this level of the storage system.  Counting begins when a marked load misses the data cache and ends when the data is reloaded into the data cache.  To calculate average latency divide this count by the number of marked misses to the same level.",
	},
	[ POWER7_PME_PM_INST_IMC_MATCH_DISP ] = {
		.pme_name = "PM_INST_IMC_MATCH_DISP",
		.pme_code = 0x30016,
		.pme_short_desc = "IMC Matches dispatched",
		.pme_long_desc = "IMC Matches dispatched",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_RMEM_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_RMEM_CYC",
		.pme_code = 0x4002c,
		.pme_short_desc = "Marked ld latency Data source 1101  (Memory same 4 chip node)",
		.pme_long_desc = "Cycles a marked load waited for data from this level of the storage system.  Counting begins when a marked load misses the data cache and ends when the data is reloaded into the data cache.  To calculate average latency divide this count by the number of marked misses to the same level.",
	},
	[ POWER7_PME_PM_VSU0_SIMPLE_ISSUED ] = {
		.pme_name = "PM_VSU0_SIMPLE_ISSUED",
		.pme_code = 0xb094,
		.pme_short_desc = "Simple VMX instruction issued",
		.pme_long_desc = "Simple VMX instruction issued",
	},
	[ POWER7_PME_PM_CMPLU_STALL_DIV ] = {
		.pme_name = "PM_CMPLU_STALL_DIV",
		.pme_code = 0x40014,
		.pme_short_desc = "Completion stall caused by DIV instruction",
		.pme_long_desc = "Following a completion stall (any period when no groups completed) the last instruction to finish before completion resumes was a fixed point divide instruction. This is a subset of PM_CMPLU_STALL_FXU.",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_RL2L3_SHR",
		.pme_code = 0x2d054,
		.pme_short_desc = "Marked PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from memory attached to a different module than this proccessor is located on due to a marked load or store.",
	},
	[ POWER7_PME_PM_VSU_FMA_DOUBLE ] = {
		.pme_name = "PM_VSU_FMA_DOUBLE",
		.pme_code = 0xa890,
		.pme_short_desc = "DP vector version of fmadd,fnmadd,fmsub,fnmsub",
		.pme_long_desc = "DP vector version of fmadd,fnmadd,fmsub,fnmsub",
	},
	[ POWER7_PME_PM_VSU_4FLOP ] = {
		.pme_name = "PM_VSU_4FLOP",
		.pme_code = 0xa89c,
		.pme_short_desc = "four flops operation (scalar fdiv, fsqrt; DP vector version of fmadd, fnmadd, fmsub, fnmsub; SP vector versions of single flop instructions)",
		.pme_long_desc = "four flops operation (scalar fdiv, fsqrt; DP vector version of fmadd, fnmadd, fmsub, fnmsub; SP vector versions of single flop instructions)",
	},
	[ POWER7_PME_PM_VSU1_FIN ] = {
		.pme_name = "PM_VSU1_FIN",
		.pme_code = 0xa0be,
		.pme_short_desc = "VSU1 Finished an instruction",
		.pme_long_desc = "VSU1 Finished an instruction",
	},
	[ POWER7_PME_PM_NEST_PAIR1_AND ] = {
		.pme_name = "PM_NEST_PAIR1_AND",
		.pme_code = 0x20883,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair1 AND",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair1 AND",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_INST_PTEG_FROM_RL2L3_MOD",
		.pme_code = 0x1e052,
		.pme_short_desc = "Instruction PTEG loaded from remote L2 or L3 modified",
		.pme_long_desc = "Instruction PTEG loaded from remote L2 or L3 modified",
	},
	[ POWER7_PME_PM_RUN_CYC ] = {
		.pme_name = "PM_RUN_CYC",
		.pme_code = 0x200f4,
		.pme_short_desc = "Run_cycles",
		.pme_long_desc = "Processor Cycles gated by the run latch.  Operating systems use the run latch to indicate when they are doing useful work.  The run latch is typically cleared in the OS idle loop.  Gating by the run latch filters out the idle loop.",
	},
	[ POWER7_PME_PM_PTEG_FROM_RMEM ] = {
		.pme_name = "PM_PTEG_FROM_RMEM",
		.pme_code = 0x3c052,
		.pme_short_desc = "PTEG loaded from remote memory",
		.pme_long_desc = "A Page Table Entry was loaded into the TLB from memory attached to a different module than this proccessor is located on.",
	},
	[ POWER7_PME_PM_LSU_LRQ_S0_VALID ] = {
		.pme_name = "PM_LSU_LRQ_S0_VALID",
		.pme_code = 0xd09e,
		.pme_short_desc = "Slot 0 of LRQ valid",
		.pme_long_desc = "This signal is asserted every cycle that the Load Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.  In SMT mode the LRQ is split between the two threads (16 entries each).",
	},
	[ POWER7_PME_PM_LSU0_LDF ] = {
		.pme_name = "PM_LSU0_LDF",
		.pme_code = 0xc084,
		.pme_short_desc = "LS0 Scalar  Loads",
		.pme_long_desc = "A floating point load was executed by LSU0",
	},
	[ POWER7_PME_PM_FLUSH_COMPLETION ] = {
		.pme_name = "PM_FLUSH_COMPLETION",
		.pme_code = 0x30012,
		.pme_short_desc = "Completion Flush",
		.pme_long_desc = "Completion Flush",
	},
	[ POWER7_PME_PM_ST_MISS_L1 ] = {
		.pme_name = "PM_ST_MISS_L1",
		.pme_code = 0x300f0,
		.pme_short_desc = "L1 D cache store misses",
		.pme_long_desc = "A store missed the dcache.  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_L2_NODE_PUMP ] = {
		.pme_name = "PM_L2_NODE_PUMP",
		.pme_code = 0x36480,
		.pme_short_desc = "RC req that was a local (aka node) pump attempt",
		.pme_long_desc = "RC req that was a local (aka node) pump attempt",
	},
	[ POWER7_PME_PM_INST_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_INST_FROM_DL2L3_SHR",
		.pme_code = 0x34044,
		.pme_short_desc = "Instruction fetched from distant L2 or L3 shared",
		.pme_long_desc = "An instruction fetch group was fetched with shared  (S) data from the L2 or L3 on a distant module. Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_MRK_STALL_CMPLU_CYC ] = {
		.pme_name = "PM_MRK_STALL_CMPLU_CYC",
		.pme_code = 0x3003e,
		.pme_short_desc = "Marked Group Completion Stall cycles ",
		.pme_long_desc = "Marked Group Completion Stall cycles ",
	},
	[ POWER7_PME_PM_VSU1_DENORM ] = {
		.pme_name = "PM_VSU1_DENORM",
		.pme_code = 0xa0ae,
		.pme_short_desc = "FPU denorm operand",
		.pme_long_desc = "VSU1 received denormalized data",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L31_SHR_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_L31_SHR_CYC",
		.pme_code = 0x20026,
		.pme_short_desc = "Marked ld latency Data source 0110 (L3.1 S) ",
		.pme_long_desc = "Marked load latency Data source 0110 (L3.1 S) ",
	},
	[ POWER7_PME_PM_NEST_PAIR0_ADD ] = {
		.pme_name = "PM_NEST_PAIR0_ADD",
		.pme_code = 0x10881,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair0 ADD",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair0 ADD",
	},
	[ POWER7_PME_PM_INST_FROM_L3MISS ] = {
		.pme_name = "PM_INST_FROM_L3MISS",
		.pme_code = 0x24048,
		.pme_short_desc = "Instruction fetched missed L3",
		.pme_long_desc = "An instruction fetch group was fetched from beyond L3. Fetch groups can contain up to 8 instructions.",
	},
	[ POWER7_PME_PM_EE_OFF_EXT_INT ] = {
		.pme_name = "PM_EE_OFF_EXT_INT",
		.pme_code = 0x2080,
		.pme_short_desc = "ee off and external interrupt",
		.pme_long_desc = "Cycles when an interrupt due to an external exception is pending but external exceptions were masked.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_DMEM ] = {
		.pme_name = "PM_INST_PTEG_FROM_DMEM",
		.pme_code = 0x2e052,
		.pme_short_desc = "Instruction PTEG loaded from distant memory",
		.pme_long_desc = "Instruction PTEG loaded from distant memory",
	},
	[ POWER7_PME_PM_INST_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_INST_FROM_DL2L3_MOD",
		.pme_code = 0x3404c,
		.pme_short_desc = "Instruction fetched from distant L2 or L3 modified",
		.pme_long_desc = "An instruction fetch group was fetched with modified  (M) data from an L2 or L3 on a distant module. Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_PMC6_OVERFLOW ] = {
		.pme_name = "PM_PMC6_OVERFLOW",
		.pme_code = 0x30024,
		.pme_short_desc = "Overflow from counter 6",
		.pme_long_desc = "Overflows from PMC6 are counted.  This effectively widens the PMC. The Overflow from the original PMC will not trigger an exception even if the PMU is configured to generate exceptions on overflow.",
	},
	[ POWER7_PME_PM_VSU_2FLOP_DOUBLE ] = {
		.pme_name = "PM_VSU_2FLOP_DOUBLE",
		.pme_code = 0xa88c,
		.pme_short_desc = "DP vector version of fmul, fsub, fcmp, fsel, fabs, fnabs, fres ,fsqrte, fneg",
		.pme_long_desc = "DP vector version of fmul, fsub, fcmp, fsel, fabs, fnabs, fres ,fsqrte, fneg",
	},
	[ POWER7_PME_PM_TLB_MISS ] = {
		.pme_name = "PM_TLB_MISS",
		.pme_code = 0x20066,
		.pme_short_desc = "TLB Miss (I + D)",
		.pme_long_desc = "Total of Data TLB mises + Instruction TLB misses",
	},
	[ POWER7_PME_PM_FXU_BUSY ] = {
		.pme_name = "PM_FXU_BUSY",
		.pme_code = 0x2000e,
		.pme_short_desc = "fxu0 busy and fxu1 busy.",
		.pme_long_desc = "Cycles when both FXU0 and FXU1 are busy.",
	},
	[ POWER7_PME_PM_L2_RCLD_DISP_FAIL_OTHER ] = {
		.pme_name = "PM_L2_RCLD_DISP_FAIL_OTHER",
		.pme_code = 0x26280,
		.pme_short_desc = " L2  RC load dispatch attempt failed due to other reasons",
		.pme_long_desc = " L2  RC load dispatch attempt failed due to other reasons",
	},
	[ POWER7_PME_PM_LSU_REJECT_LMQ_FULL ] = {
		.pme_name = "PM_LSU_REJECT_LMQ_FULL",
		.pme_code = 0xc8a4,
		.pme_short_desc = "Reject: LMQ Full (LHR)",
		.pme_long_desc = "Total cycles the Load Store Unit is busy rejecting instructions because the Load Miss Queue was full. The LMQ has eight entries.  If all the eight entries are full, subsequent load instructions are rejected. Combined unit 0 + 1.",
	},
	[ POWER7_PME_PM_IC_RELOAD_SHR ] = {
		.pme_name = "PM_IC_RELOAD_SHR",
		.pme_code = 0x4096,
		.pme_short_desc = "Reloading line to be shared between the threads",
		.pme_long_desc = "An Instruction Cache request was made by this thread and the cache line was already in the cache for the other thread. The line is marked valid for all threads.",
	},
	[ POWER7_PME_PM_GRP_MRK ] = {
		.pme_name = "PM_GRP_MRK",
		.pme_code = 0x10031,
		.pme_short_desc = "IDU Marked Instruction",
		.pme_long_desc = "A group was sampled (marked).  The group is called a marked group.  One instruction within the group is tagged for detailed monitoring.  The sampled instruction is called a marked instructions.  Events associated with the marked instruction are annotated with the marked term.",
	},
	[ POWER7_PME_PM_MRK_ST_NEST ] = {
		.pme_name = "PM_MRK_ST_NEST",
		.pme_code = 0x20034,
		.pme_short_desc = "marked store sent to Nest",
		.pme_long_desc = "A sampled store has been sent to the memory subsystem",
	},
	[ POWER7_PME_PM_VSU1_FSQRT_FDIV ] = {
		.pme_name = "PM_VSU1_FSQRT_FDIV",
		.pme_code = 0xa08a,
		.pme_short_desc = "four flops operation (fdiv,fsqrt,xsdiv,xssqrt) Scalar Instructions only!",
		.pme_long_desc = "four flops operation (fdiv,fsqrt,xsdiv,xssqrt) Scalar Instructions only!",
	},
	[ POWER7_PME_PM_LSU0_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU0_FLUSH_LRQ",
		.pme_code = 0xc0b8,
		.pme_short_desc = "LS0 Flush: LRQ",
		.pme_long_desc = "Load Hit Load or Store Hit Load flush.  A younger load was flushed from unit 0 because it executed before an older store and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER7_PME_PM_LARX_LSU0 ] = {
		.pme_name = "PM_LARX_LSU0",
		.pme_code = 0xc094,
		.pme_short_desc = "ls0 Larx Finished",
		.pme_long_desc = "A larx (lwarx or ldarx) was executed on side 0 ",
	},
	[ POWER7_PME_PM_IBUF_FULL_CYC ] = {
		.pme_name = "PM_IBUF_FULL_CYC",
		.pme_code = 0x4084,
		.pme_short_desc = "Cycles No room in ibuff",
		.pme_long_desc = "Cycles with the Instruction Buffer was full.  The Instruction Buffer is a circular queue of 64 instructions per thread, organized as 16 groups of 4 instructions.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_DL2L3_SHR_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_DL2L3_SHR_CYC",
		.pme_code = 0x2002a,
		.pme_short_desc = "Marked ld latency Data Source 1010 (Distant L2.75/L3.75 S)",
		.pme_long_desc = "Marked ld latency Data Source 1010 (Distant L2.75/L3.75 S)",
	},
	[ POWER7_PME_PM_LSU_DC_PREF_STREAM_ALLOC ] = {
		.pme_name = "PM_LSU_DC_PREF_STREAM_ALLOC",
		.pme_code = 0xd8a8,
		.pme_short_desc = "D cache new prefetch stream allocated",
		.pme_long_desc = "D cache new prefetch stream allocated",
	},
	[ POWER7_PME_PM_GRP_MRK_CYC ] = {
		.pme_name = "PM_GRP_MRK_CYC",
		.pme_code = 0x10030,
		.pme_short_desc = "cycles IDU marked instruction before dispatch",
		.pme_long_desc = "cycles IDU marked instruction before dispatch",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_RL2L3_SHR_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_RL2L3_SHR_CYC",
		.pme_code = 0x20028,
		.pme_short_desc = "Marked ld latency Data Source 1000 (Remote L2.5/L3.5 S)",
		.pme_long_desc = "Marked load latency Data Source 1000 (Remote L2.5/L3.5 S)",
	},
	[ POWER7_PME_PM_L2_GLOB_GUESS_CORRECT ] = {
		.pme_name = "PM_L2_GLOB_GUESS_CORRECT",
		.pme_code = 0x16482,
		.pme_short_desc = "L2 guess glb and guess was correct (ie data remote)",
		.pme_long_desc = "L2 guess glb and guess was correct (ie data remote)",
	},
	[ POWER7_PME_PM_LSU_REJECT_LHS ] = {
		.pme_name = "PM_LSU_REJECT_LHS",
		.pme_code = 0xc8ac,
		.pme_short_desc = "Reject: Load Hit Store",
		.pme_long_desc = "The Load Store Unit rejected a load load instruction that had an address overlap with an older store in the store queue. The store must be committed and de-allocated from the Store Queue before the load can execute successfully. Combined Unit 0 + 1",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_LMEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_LMEM",
		.pme_code = 0x3d04a,
		.pme_short_desc = "Marked data loaded from local memory",
		.pme_long_desc = "The processor's Data Cache was reloaded due to a marked load from memory attached to the same module this proccessor is located on.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L3 ] = {
		.pme_name = "PM_INST_PTEG_FROM_L3",
		.pme_code = 0x2e050,
		.pme_short_desc = "Instruction PTEG loaded from L3",
		.pme_long_desc = "Instruction PTEG loaded from L3",
	},
	[ POWER7_PME_PM_FREQ_DOWN ] = {
		.pme_name = "PM_FREQ_DOWN",
		.pme_code = 0x3000c,
		.pme_short_desc = "Frequency is being slewed down due to Power Management",
		.pme_long_desc = "Processor frequency was slowed down due to power management",
	},
	[ POWER7_PME_PM_PB_RETRY_NODE_PUMP ] = {
		.pme_name = "PM_PB_RETRY_NODE_PUMP",
		.pme_code = 0x30081,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair2 Bit0",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair2 Bit0",
	},
	[ POWER7_PME_PM_INST_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_INST_FROM_RL2L3_SHR",
		.pme_code = 0x1404c,
		.pme_short_desc = "Instruction fetched from remote L2 or L3 shared",
		.pme_long_desc = "An instruction fetch group was fetched with shared  (S) data from the L2 or L3 on a remote module. Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_MRK_INST_ISSUED ] = {
		.pme_name = "PM_MRK_INST_ISSUED",
		.pme_code = 0x10032,
		.pme_short_desc = "Marked instruction issued",
		.pme_long_desc = "A marked instruction was issued to an execution unit.",
	},
	[ POWER7_PME_PM_PTEG_FROM_L3MISS ] = {
		.pme_name = "PM_PTEG_FROM_L3MISS",
		.pme_code = 0x2c058,
		.pme_short_desc = "PTEG loaded from L3 miss",
		.pme_long_desc = " Page Table Entry was loaded into the ERAT from beyond the L3 due to a demand load or store.",
	},
	[ POWER7_PME_PM_RUN_PURR ] = {
		.pme_name = "PM_RUN_PURR",
		.pme_code = 0x400f4,
		.pme_short_desc = "Run_PURR",
		.pme_long_desc = "The Processor Utilization of Resources Register was incremented while the run latch was set. The PURR registers will be incremented roughly in the ratio in which the instructions are dispatched from the two threads. ",
	},
	[ POWER7_PME_PM_MRK_GRP_IC_MISS ] = {
		.pme_name = "PM_MRK_GRP_IC_MISS",
		.pme_code = 0x40038,
		.pme_short_desc = "Marked group experienced  I cache miss",
		.pme_long_desc = "A group containing a marked (sampled) instruction experienced an instruction cache miss.",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L3 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L3",
		.pme_code = 0x1d048,
		.pme_short_desc = "Marked data loaded from L3",
		.pme_long_desc = "The processor's Data Cache was reloaded from the local L3 due to a marked load.",
	},
	[ POWER7_PME_PM_CMPLU_STALL_DCACHE_MISS ] = {
		.pme_name = "PM_CMPLU_STALL_DCACHE_MISS",
		.pme_code = 0x20016,
		.pme_short_desc = "Completion stall caused by D cache miss",
		.pme_long_desc = "Following a completion stall (any period when no groups completed) the last instruction to finish before completion resumes suffered a Data Cache Miss. Data Cache Miss has higher priority than any other Load/Store delay, so if an instruction encounters multiple delays only the Data Cache Miss will be reported and the entire delay period will be charged to Data Cache Miss. This is a subset of PM_CMPLU_STALL_LSU.",
	},
	[ POWER7_PME_PM_PTEG_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_PTEG_FROM_RL2L3_SHR",
		.pme_code = 0x2c054,
		.pme_short_desc = "PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT with shared (T or SL) data from an L2 or L3 on a remote module due to a demand load or store.",
	},
	[ POWER7_PME_PM_LSU_FLUSH_LRQ ] = {
		.pme_name = "PM_LSU_FLUSH_LRQ",
		.pme_code = 0xc8b8,
		.pme_short_desc = "Flush: LRQ",
		.pme_long_desc = "Load Hit Load or Store Hit Load flush.  A younger load was flushed because it executed before an older store and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.  Combined Unit 0 + 1.",
	},
	[ POWER7_PME_PM_MRK_DERAT_MISS_64K ] = {
		.pme_name = "PM_MRK_DERAT_MISS_64K",
		.pme_code = 0x2d05c,
		.pme_short_desc = "Marked DERAT misses for 64K page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 64K page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_INST_PTEG_FROM_DL2L3_MOD",
		.pme_code = 0x4e054,
		.pme_short_desc = "Instruction PTEG loaded from distant L2 or L3 modified",
		.pme_long_desc = "Instruction PTEG loaded from distant L2 or L3 modified",
	},
	[ POWER7_PME_PM_L2_ST_MISS ] = {
		.pme_name = "PM_L2_ST_MISS",
		.pme_code = 0x26082,
		.pme_short_desc = "Data Store Miss",
		.pme_long_desc = "Data Store Miss",
	},
	[ POWER7_PME_PM_LWSYNC ] = {
		.pme_name = "PM_LWSYNC",
		.pme_code = 0xd094,
		.pme_short_desc = "lwsync count (easier to use than IMC)",
		.pme_long_desc = "lwsync count (easier to use than IMC)",
	},
	[ POWER7_PME_PM_LSU0_DC_PREF_STREAM_CONFIRM_STRIDE ] = {
		.pme_name = "PM_LSU0_DC_PREF_STREAM_CONFIRM_STRIDE",
		.pme_code = 0xd0bc,
		.pme_short_desc = "LS0 Dcache Strided prefetch stream confirmed",
		.pme_long_desc = "LS0 Dcache Strided prefetch stream confirmed",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L21_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L21_SHR",
		.pme_code = 0x4d056,
		.pme_short_desc = "Marked PTEG loaded from another L2 on same chip shared",
		.pme_long_desc = "Marked PTEG loaded from another L2 on same chip shared",
	},
	[ POWER7_PME_PM_MRK_LSU_FLUSH_LRQ ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_LRQ",
		.pme_code = 0xd088,
		.pme_short_desc = "Flush: (marked) LRQ",
		.pme_long_desc = "Load Hit Load or Store Hit Load flush.  A marked load was flushed because it executed before an older store and they had overlapping data OR two loads executed out of order and they have byte overlap and there was a snoop in between to an overlapped byte.",
	},
	[ POWER7_PME_PM_INST_IMC_MATCH_CMPL ] = {
		.pme_name = "PM_INST_IMC_MATCH_CMPL",
		.pme_code = 0x100f0,
		.pme_short_desc = "IMC Match Count",
		.pme_long_desc = "Number of instructions resulting from the marked instructions expansion that completed.",
	},
	[ POWER7_PME_PM_NEST_PAIR3_AND ] = {
		.pme_name = "PM_NEST_PAIR3_AND",
		.pme_code = 0x40883,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair3 AND",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair3 AND",
	},
	[ POWER7_PME_PM_PB_RETRY_SYS_PUMP ] = {
		.pme_name = "PM_PB_RETRY_SYS_PUMP",
		.pme_code = 0x40081,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair3 Bit0",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair3 Bit0",
	},
	[ POWER7_PME_PM_MRK_INST_FIN ] = {
		.pme_name = "PM_MRK_INST_FIN",
		.pme_code = 0x30030,
		.pme_short_desc = "marked instr finish any unit ",
		.pme_long_desc = "One of the execution units finished a marked instruction.  Instructions that finish may not necessary complete",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_DL2L3_SHR",
		.pme_code = 0x3d054,
		.pme_short_desc = "Marked PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from memory attached to a different module than this proccessor is located on due to a marked load or store.",
	},
	[ POWER7_PME_PM_INST_FROM_L31_MOD ] = {
		.pme_name = "PM_INST_FROM_L31_MOD",
		.pme_code = 0x14044,
		.pme_short_desc = "Instruction fetched from another L3 on same chip modified",
		.pme_long_desc = "Instruction fetched from another L3 on same chip modified",
	},
	[ POWER7_PME_PM_MRK_DTLB_MISS_64K ] = {
		.pme_name = "PM_MRK_DTLB_MISS_64K",
		.pme_code = 0x3d05e,
		.pme_short_desc = "Marked Data TLB misses for 64K page",
		.pme_long_desc = "Data TLB references to 64KB pages by a marked instruction that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_LSU_FIN ] = {
		.pme_name = "PM_LSU_FIN",
		.pme_code = 0x30066,
		.pme_short_desc = "LSU Finished an instruction (up to 2 per cycle)",
		.pme_long_desc = "LSU Finished an instruction (up to 2 per cycle)",
	},
	[ POWER7_PME_PM_MRK_LSU_REJECT ] = {
		.pme_name = "PM_MRK_LSU_REJECT",
		.pme_code = 0x40064,
		.pme_short_desc = "LSU marked reject (up to 2 per cycle)",
		.pme_long_desc = "LSU marked reject (up to 2 per cycle)",
	},
	[ POWER7_PME_PM_L2_CO_FAIL_BUSY ] = {
		.pme_name = "PM_L2_CO_FAIL_BUSY",
		.pme_code = 0x16382,
		.pme_short_desc = " L2  RC Cast Out dispatch attempt failed due to all CO machines busy",
		.pme_long_desc = " L2  RC Cast Out dispatch attempt failed due to all CO machines busy",
	},
	[ POWER7_PME_PM_MEM0_WQ_DISP ] = {
		.pme_name = "PM_MEM0_WQ_DISP",
		.pme_code = 0x40083,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair3 Bit1",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair3 Bit1",
	},
	[ POWER7_PME_PM_DATA_FROM_L31_MOD ] = {
		.pme_name = "PM_DATA_FROM_L31_MOD",
		.pme_code = 0x1c044,
		.pme_short_desc = "Data loaded from another L3 on same chip modified",
		.pme_long_desc = "Data loaded from another L3 on same chip modified",
	},
	[ POWER7_PME_PM_THERMAL_WARN ] = {
		.pme_name = "PM_THERMAL_WARN",
		.pme_code = 0x10016,
		.pme_short_desc = "Processor in Thermal Warning",
		.pme_long_desc = "Processor in Thermal Warning",
	},
	[ POWER7_PME_PM_VSU0_4FLOP ] = {
		.pme_name = "PM_VSU0_4FLOP",
		.pme_code = 0xa09c,
		.pme_short_desc = "four flops operation (scalar fdiv, fsqrt; DP vector version of fmadd, fnmadd, fmsub, fnmsub; SP vector versions of single flop instructions)",
		.pme_long_desc = "four flops operation (scalar fdiv, fsqrt; DP vector version of fmadd, fnmadd, fmsub, fnmsub; SP vector versions of single flop instructions)",
	},
	[ POWER7_PME_PM_BR_MPRED_CCACHE ] = {
		.pme_name = "PM_BR_MPRED_CCACHE",
		.pme_code = 0x40a4,
		.pme_short_desc = "Branch Mispredict due to Count Cache prediction",
		.pme_long_desc = "A branch instruction target was incorrectly predicted by the ccount cache. This will result in a branch redirect flush if not overfidden by a flush of an older instruction.",
	},
	[ POWER7_PME_PM_CMPLU_STALL_IFU ] = {
		.pme_name = "PM_CMPLU_STALL_IFU",
		.pme_code = 0x4004c,
		.pme_short_desc = "Completion stall due to IFU ",
		.pme_long_desc = "Completion stall due to IFU ",
	},
	[ POWER7_PME_PM_L1_DEMAND_WRITE ] = {
		.pme_name = "PM_L1_DEMAND_WRITE",
		.pme_code = 0x408c,
		.pme_short_desc = "Instruction Demand sectors wriittent into IL1",
		.pme_long_desc = "Instruction Demand sectors wriittent into IL1",
	},
	[ POWER7_PME_PM_FLUSH_BR_MPRED ] = {
		.pme_name = "PM_FLUSH_BR_MPRED",
		.pme_code = 0x2084,
		.pme_short_desc = "Flush caused by branch mispredict",
		.pme_long_desc = "A flush was caused by a branch mispredict.",
	},
	[ POWER7_PME_PM_MRK_DTLB_MISS_16G ] = {
		.pme_name = "PM_MRK_DTLB_MISS_16G",
		.pme_code = 0x1d05e,
		.pme_short_desc = "Marked Data TLB misses for 16G page",
		.pme_long_desc = "Data TLB references to 16GB pages by a marked instruction that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_DMEM ] = {
		.pme_name = "PM_MRK_PTEG_FROM_DMEM",
		.pme_code = 0x2d052,
		.pme_short_desc = "Marked PTEG loaded from distant memory",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from memory attached to a different module than this proccessor is located on due to a marked load or store.",
	},
	[ POWER7_PME_PM_L2_RCST_DISP ] = {
		.pme_name = "PM_L2_RCST_DISP",
		.pme_code = 0x36280,
		.pme_short_desc = " L2  RC store dispatch attempt",
		.pme_long_desc = " L2  RC store dispatch attempt",
	},
	[ POWER7_PME_PM_CMPLU_STALL ] = {
		.pme_name = "PM_CMPLU_STALL",
		.pme_code = 0x4000a,
		.pme_short_desc = "No groups completed, GCT not empty",
		.pme_long_desc = "No groups completed, GCT not empty",
	},
	[ POWER7_PME_PM_LSU_PARTIAL_CDF ] = {
		.pme_name = "PM_LSU_PARTIAL_CDF",
		.pme_code = 0xc0aa,
		.pme_short_desc = "A partial cacheline was returned from the L3",
		.pme_long_desc = "A partial cacheline was returned from the L3",
	},
	[ POWER7_PME_PM_DISP_CLB_HELD_SB ] = {
		.pme_name = "PM_DISP_CLB_HELD_SB",
		.pme_code = 0x20a8,
		.pme_short_desc = "Dispatch/CLB Hold: Scoreboard",
		.pme_long_desc = "Dispatch/CLB Hold: Scoreboard",
	},
	[ POWER7_PME_PM_VSU0_FMA_DOUBLE ] = {
		.pme_name = "PM_VSU0_FMA_DOUBLE",
		.pme_code = 0xa090,
		.pme_short_desc = "four flop DP vector operations (xvmadddp, xvnmadddp, xvmsubdp, xvmsubdp)",
		.pme_long_desc = "four flop DP vector operations (xvmadddp, xvnmadddp, xvmsubdp, xvmsubdp)",
	},
	[ POWER7_PME_PM_FXU0_BUSY_FXU1_IDLE ] = {
		.pme_name = "PM_FXU0_BUSY_FXU1_IDLE",
		.pme_code = 0x3000e,
		.pme_short_desc = "fxu0 busy and fxu1 idle",
		.pme_long_desc = "FXU0 is busy while FXU1 was idle",
	},
	[ POWER7_PME_PM_IC_DEMAND_CYC ] = {
		.pme_name = "PM_IC_DEMAND_CYC",
		.pme_code = 0x10018,
		.pme_short_desc = "Cycles when a demand ifetch was pending",
		.pme_long_desc = "Cycles when a demand ifetch was pending",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L21_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L21_SHR",
		.pme_code = 0x3d04e,
		.pme_short_desc = "Marked data loaded from another L2 on same chip shared",
		.pme_long_desc = "Marked data loaded from another L2 on same chip shared",
	},
	[ POWER7_PME_PM_MRK_LSU_FLUSH_UST ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_UST",
		.pme_code = 0xd086,
		.pme_short_desc = "Flush: (marked) Unaligned Store",
		.pme_long_desc = "A marked store was flushed because it was unaligned",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L3MISS ] = {
		.pme_name = "PM_INST_PTEG_FROM_L3MISS",
		.pme_code = 0x2e058,
		.pme_short_desc = "Instruction PTEG loaded from L3 miss",
		.pme_long_desc = "Instruction PTEG loaded from L3 miss",
	},
	[ POWER7_PME_PM_VSU_DENORM ] = {
		.pme_name = "PM_VSU_DENORM",
		.pme_code = 0xa8ac,
		.pme_short_desc = "Vector or Scalar denorm operand",
		.pme_long_desc = "Vector or Scalar denorm operand",
	},
	[ POWER7_PME_PM_MRK_LSU_PARTIAL_CDF ] = {
		.pme_name = "PM_MRK_LSU_PARTIAL_CDF",
		.pme_code = 0xd080,
		.pme_short_desc = "A partial cacheline was returned from the L3 for a marked load",
		.pme_long_desc = "A partial cacheline was returned from the L3 for a marked load",
	},
	[ POWER7_PME_PM_INST_FROM_L21_SHR ] = {
		.pme_name = "PM_INST_FROM_L21_SHR",
		.pme_code = 0x3404e,
		.pme_short_desc = "Instruction fetched from another L2 on same chip shared",
		.pme_long_desc = "Instruction fetched from another L2 on same chip shared",
	},
	[ POWER7_PME_PM_IC_PREF_WRITE ] = {
		.pme_name = "PM_IC_PREF_WRITE",
		.pme_code = 0x408e,
		.pme_short_desc = "Instruction prefetch written into IL1",
		.pme_long_desc = "Number of Instruction Cache entries written because of prefetch. Prefetch entries are marked least recently used and are candidates for eviction if they are not needed to satisfy a demand fetch.",
	},
	[ POWER7_PME_PM_BR_PRED ] = {
		.pme_name = "PM_BR_PRED",
		.pme_code = 0x409c,
		.pme_short_desc = "Branch Predictions made",
		.pme_long_desc = "A branch prediction was made. This could have been a target prediction, a condition prediction, or both",
	},
	[ POWER7_PME_PM_INST_FROM_DMEM ] = {
		.pme_name = "PM_INST_FROM_DMEM",
		.pme_code = 0x1404a,
		.pme_short_desc = "Instruction fetched from distant memory",
		.pme_long_desc = "An instruction fetch group was fetched from memory attached to a distant module. Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_IC_PREF_CANCEL_ALL ] = {
		.pme_name = "PM_IC_PREF_CANCEL_ALL",
		.pme_code = 0x4890,
		.pme_short_desc = "Prefetch Canceled due to page boundary or icache hit",
		.pme_long_desc = "Prefetch Canceled due to page boundary or icache hit",
	},
	[ POWER7_PME_PM_LSU_DC_PREF_STREAM_CONFIRM ] = {
		.pme_name = "PM_LSU_DC_PREF_STREAM_CONFIRM",
		.pme_code = 0xd8b4,
		.pme_short_desc = "Dcache new prefetch stream confirmed",
		.pme_long_desc = "Dcache new prefetch stream confirmed",
	},
	[ POWER7_PME_PM_MRK_LSU_FLUSH_SRQ ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_SRQ",
		.pme_code = 0xd08a,
		.pme_short_desc = "Flush: (marked) SRQ",
		.pme_long_desc = "Load Hit Store flush.  A marked load was flushed because it hits (overlaps) an older store that is already in the SRQ or in the same group.  If the real addresses match but the effective addresses do not, an alias condition exists that prevents store forwarding.  If the load and store are in the same group the load must be flushed to separate the two instructions. ",
	},
	[ POWER7_PME_PM_MRK_FIN_STALL_CYC ] = {
		.pme_name = "PM_MRK_FIN_STALL_CYC",
		.pme_code = 0x1003c,
		.pme_short_desc = "Marked instruction Finish Stall cycles (marked finish after NTC) ",
		.pme_long_desc = "Marked instruction Finish Stall cycles (marked finish after NTC) ",
	},
	[ POWER7_PME_PM_L2_RCST_DISP_FAIL_OTHER ] = {
		.pme_name = "PM_L2_RCST_DISP_FAIL_OTHER",
		.pme_code = 0x46280,
		.pme_short_desc = " L2  RC store dispatch attempt failed due to other reasons",
		.pme_long_desc = " L2  RC store dispatch attempt failed due to other reasons",
	},
	[ POWER7_PME_PM_VSU1_DD_ISSUED ] = {
		.pme_name = "PM_VSU1_DD_ISSUED",
		.pme_code = 0xb098,
		.pme_short_desc = "64BIT Decimal Issued on Pipe1",
		.pme_long_desc = "64BIT Decimal Issued on Pipe1",
	},
	[ POWER7_PME_PM_PTEG_FROM_L31_SHR ] = {
		.pme_name = "PM_PTEG_FROM_L31_SHR",
		.pme_code = 0x2c056,
		.pme_short_desc = "PTEG loaded from another L3 on same chip shared",
		.pme_long_desc = "PTEG loaded from another L3 on same chip shared",
	},
	[ POWER7_PME_PM_DATA_FROM_L21_SHR ] = {
		.pme_name = "PM_DATA_FROM_L21_SHR",
		.pme_code = 0x3c04e,
		.pme_short_desc = "Data loaded from another L2 on same chip shared",
		.pme_long_desc = "Data loaded from another L2 on same chip shared",
	},
	[ POWER7_PME_PM_LSU0_NCLD ] = {
		.pme_name = "PM_LSU0_NCLD",
		.pme_code = 0xc08c,
		.pme_short_desc = "LS0 Non-cachable Loads counted at finish",
		.pme_long_desc = "A non-cacheable load was executed by unit 0.",
	},
	[ POWER7_PME_PM_VSU1_4FLOP ] = {
		.pme_name = "PM_VSU1_4FLOP",
		.pme_code = 0xa09e,
		.pme_short_desc = "four flops operation (scalar fdiv, fsqrt; DP vector version of fmadd, fnmadd, fmsub, fnmsub; SP vector versions of single flop instructions)",
		.pme_long_desc = "four flops operation (scalar fdiv, fsqrt; DP vector version of fmadd, fnmadd, fmsub, fnmsub; SP vector versions of single flop instructions)",
	},
	[ POWER7_PME_PM_VSU1_8FLOP ] = {
		.pme_name = "PM_VSU1_8FLOP",
		.pme_code = 0xa0a2,
		.pme_short_desc = "eight flops operation (DP vector versions of fdiv,fsqrt and SP vector versions of fmadd,fnmadd,fmsub,fnmsub) ",
		.pme_long_desc = "eight flops operation (DP vector versions of fdiv,fsqrt and SP vector versions of fmadd,fnmadd,fmsub,fnmsub) ",
	},
	[ POWER7_PME_PM_VSU_8FLOP ] = {
		.pme_name = "PM_VSU_8FLOP",
		.pme_code = 0xa8a0,
		.pme_short_desc = "eight flops operation (DP vector versions of fdiv,fsqrt and SP vector versions of fmadd,fnmadd,fmsub,fnmsub) ",
		.pme_long_desc = "eight flops operation (DP vector versions of fdiv,fsqrt and SP vector versions of fmadd,fnmadd,fmsub,fnmsub) ",
	},
	[ POWER7_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_CYC",
		.pme_code = 0x2003e,
		.pme_short_desc = "LSU empty (lmq and srq empty)",
		.pme_long_desc = "Cycles when both the LMQ and SRQ are empty (LSU is idle)",
	},
	[ POWER7_PME_PM_DTLB_MISS_64K ] = {
		.pme_name = "PM_DTLB_MISS_64K",
		.pme_code = 0x3c05e,
		.pme_short_desc = "Data TLB miss for 64K page",
		.pme_long_desc = "Data TLB references to 64KB pages that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_THRD_CONC_RUN_INST ] = {
		.pme_name = "PM_THRD_CONC_RUN_INST",
		.pme_code = 0x300f4,
		.pme_short_desc = "Concurrent Run Instructions",
		.pme_long_desc = "Instructions completed by this thread when both threads had their run latches set.",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L2 ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L2",
		.pme_code = 0x1d050,
		.pme_short_desc = "Marked PTEG loaded from L2",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from the local L2 due to a marked load or store.",
	},
	[ POWER7_PME_PM_PB_SYS_PUMP ] = {
		.pme_name = "PM_PB_SYS_PUMP",
		.pme_code = 0x20081,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair1 Bit0",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair1 Bit0",
	},
	[ POWER7_PME_PM_VSU_FIN ] = {
		.pme_name = "PM_VSU_FIN",
		.pme_code = 0xa8bc,
		.pme_short_desc = "VSU0 Finished an instruction",
		.pme_long_desc = "VSU0 Finished an instruction",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L31_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L31_MOD",
		.pme_code = 0x1d044,
		.pme_short_desc = "Marked data loaded from another L3 on same chip modified",
		.pme_long_desc = "Marked data loaded from another L3 on same chip modified",
	},
	[ POWER7_PME_PM_THRD_PRIO_0_1_CYC ] = {
		.pme_name = "PM_THRD_PRIO_0_1_CYC",
		.pme_code = 0x40b0,
		.pme_short_desc = " Cycles thread running at priority level 0 or 1",
		.pme_long_desc = " Cycles thread running at priority level 0 or 1",
	},
	[ POWER7_PME_PM_DERAT_MISS_64K ] = {
		.pme_name = "PM_DERAT_MISS_64K",
		.pme_code = 0x2c05c,
		.pme_short_desc = "DERAT misses for 64K page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 64K page and resulted in an ERAT reload.",
	},
	[ POWER7_PME_PM_PMC2_REWIND ] = {
		.pme_name = "PM_PMC2_REWIND",
		.pme_code = 0x30020,
		.pme_short_desc = "PMC2 Rewind Event (did not match condition)",
		.pme_long_desc = "PMC2 was counting speculatively. The speculative condition was not met and the counter was restored to its previous value.",
	},
	[ POWER7_PME_PM_INST_FROM_L2 ] = {
		.pme_name = "PM_INST_FROM_L2",
		.pme_code = 0x14040,
		.pme_short_desc = "Instruction fetched from L2",
		.pme_long_desc = "An instruction fetch group was fetched from L2. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_GRP_BR_MPRED_NONSPEC ] = {
		.pme_name = "PM_GRP_BR_MPRED_NONSPEC",
		.pme_code = 0x1000a,
		.pme_short_desc = "Group experienced non-speculative branch redirect",
		.pme_long_desc = "Group experienced non-speculative branch redirect",
	},
	[ POWER7_PME_PM_INST_DISP ] = {
		.pme_name = "PM_INST_DISP",
		.pme_code = 0x200f2,
		.pme_short_desc = "# PPC Dispatched",
		.pme_long_desc = "Number of PowerPC instructions successfully dispatched.",
	},
	[ POWER7_PME_PM_MEM0_RD_CANCEL_TOTAL ] = {
		.pme_name = "PM_MEM0_RD_CANCEL_TOTAL",
		.pme_code = 0x30083,
		.pme_short_desc = " Nest events (MC0/MC1/PB/GX), Pair2 Bit1",
		.pme_long_desc = " Nest events (MC0/MC1/PB/GX), Pair2 Bit1",
	},
	[ POWER7_PME_PM_LSU0_DC_PREF_STREAM_CONFIRM ] = {
		.pme_name = "PM_LSU0_DC_PREF_STREAM_CONFIRM",
		.pme_code = 0xd0b4,
		.pme_short_desc = "LS0 Dcache prefetch stream confirmed",
		.pme_long_desc = "LS0 Dcache prefetch stream confirmed",
	},
	[ POWER7_PME_PM_L1_DCACHE_RELOAD_VALID ] = {
		.pme_name = "PM_L1_DCACHE_RELOAD_VALID",
		.pme_code = 0x300f6,
		.pme_short_desc = "L1 reload data source valid",
		.pme_long_desc = "The data source information is valid,the data cache has been reloaded.  Prior to POWER5+ this included data cache reloads due to prefetch activity.  With POWER5+ this now only includes reloads due to demand loads.",
	},
	[ POWER7_PME_PM_VSU_SCALAR_DOUBLE_ISSUED ] = {
		.pme_name = "PM_VSU_SCALAR_DOUBLE_ISSUED",
		.pme_code = 0xb888,
		.pme_short_desc = "Double Precision scalar instruction issued on Pipe0",
		.pme_long_desc = "Double Precision scalar instruction issued on Pipe0",
	},
	[ POWER7_PME_PM_L3_PREF_HIT ] = {
		.pme_name = "PM_L3_PREF_HIT",
		.pme_code = 0x3f080,
		.pme_short_desc = "L3 Prefetch Directory Hit",
		.pme_long_desc = "L3 Prefetch Directory Hit",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L31_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L31_MOD",
		.pme_code = 0x1d054,
		.pme_short_desc = "Marked PTEG loaded from another L3 on same chip modified",
		.pme_long_desc = "Marked PTEG loaded from another L3 on same chip modified",
	},
	[ POWER7_PME_PM_CMPLU_STALL_STORE ] = {
		.pme_name = "PM_CMPLU_STALL_STORE",
		.pme_code = 0x2004a,
		.pme_short_desc = "Completion stall due to store instruction",
		.pme_long_desc = "Completion stall due to store instruction",
	},
	[ POWER7_PME_PM_MRK_FXU_FIN ] = {
		.pme_name = "PM_MRK_FXU_FIN",
		.pme_code = 0x20038,
		.pme_short_desc = "fxu marked  instr finish",
		.pme_long_desc = "One of the Fixed Point Units finished a marked instruction.  Instructions that finish may not necessary complete.",
	},
	[ POWER7_PME_PM_PMC4_OVERFLOW ] = {
		.pme_name = "PM_PMC4_OVERFLOW",
		.pme_code = 0x10010,
		.pme_short_desc = "Overflow from counter 4",
		.pme_long_desc = "Overflows from PMC4 are counted.  This effectively widens the PMC. The Overflow from the original PMC will not trigger an exception even if the PMU is configured to generate exceptions on overflow.",
	},
	[ POWER7_PME_PM_MRK_PTEG_FROM_L3 ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L3",
		.pme_code = 0x2d050,
		.pme_short_desc = "Marked PTEG loaded from L3",
		.pme_long_desc = "A Page Table Entry was loaded into the ERAT from the local L3 due to a marked load or store.",
	},
	[ POWER7_PME_PM_LSU0_LMQ_LHR_MERGE ] = {
		.pme_name = "PM_LSU0_LMQ_LHR_MERGE",
		.pme_code = 0xd098,
		.pme_short_desc = "LS0  Load Merged with another cacheline request",
		.pme_long_desc = "LS0  Load Merged with another cacheline request",
	},
	[ POWER7_PME_PM_BTAC_HIT ] = {
		.pme_name = "PM_BTAC_HIT",
		.pme_code = 0x508a,
		.pme_short_desc = "BTAC Correct Prediction",
		.pme_long_desc = "BTAC Correct Prediction",
	},
	[ POWER7_PME_PM_L3_RD_BUSY ] = {
		.pme_name = "PM_L3_RD_BUSY",
		.pme_code = 0x4f082,
		.pme_short_desc = "Rd machines busy >= threshold (2,4,6,8)",
		.pme_long_desc = "Rd machines busy >= threshold (2,4,6,8)",
	},
	[ POWER7_PME_PM_LSU0_L1_SW_PREF ] = {
		.pme_name = "PM_LSU0_L1_SW_PREF",
		.pme_code = 0xc09c,
		.pme_short_desc = "LSU0 Software L1 Prefetches, including SW Transient Prefetches",
		.pme_long_desc = "LSU0 Software L1 Prefetches, including SW Transient Prefetches",
	},
	[ POWER7_PME_PM_INST_FROM_L2MISS ] = {
		.pme_name = "PM_INST_FROM_L2MISS",
		.pme_code = 0x44048,
		.pme_short_desc = "Instruction fetched missed L2",
		.pme_long_desc = "An instruction fetch group was fetched from beyond the local L2.",
	},
	[ POWER7_PME_PM_LSU0_DC_PREF_STREAM_ALLOC ] = {
		.pme_name = "PM_LSU0_DC_PREF_STREAM_ALLOC",
		.pme_code = 0xd0a8,
		.pme_short_desc = "LS0 D cache new prefetch stream allocated",
		.pme_long_desc = "LS0 D cache new prefetch stream allocated",
	},
	[ POWER7_PME_PM_L2_ST ] = {
		.pme_name = "PM_L2_ST",
		.pme_code = 0x16082,
		.pme_short_desc = "Data Store Count",
		.pme_long_desc = "Data Store Count",
	},
	[ POWER7_PME_PM_VSU0_DENORM ] = {
		.pme_name = "PM_VSU0_DENORM",
		.pme_code = 0xa0ac,
		.pme_short_desc = "FPU denorm operand",
		.pme_long_desc = "VSU0 received denormalized data",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_DL2L3_SHR",
		.pme_code = 0x3d044,
		.pme_short_desc = "Marked data loaded from distant L2 or L3 shared",
		.pme_long_desc = "The processor's Data Cache was reloaded with shared (T or SL) data from an L2 or L3 on a distant module due to a marked load.",
	},
	[ POWER7_PME_PM_BR_PRED_CR_TA ] = {
		.pme_name = "PM_BR_PRED_CR_TA",
		.pme_code = 0x48aa,
		.pme_short_desc = "Branch predict - taken/not taken and target",
		.pme_long_desc = "Both the condition (taken or not taken) and the target address of a branch instruction was predicted.",
	},
	[ POWER7_PME_PM_VSU0_FCONV ] = {
		.pme_name = "PM_VSU0_FCONV",
		.pme_code = 0xa0b0,
		.pme_short_desc = "Convert instruction executed",
		.pme_long_desc = "Convert instruction executed",
	},
	[ POWER7_PME_PM_MRK_LSU_FLUSH_ULD ] = {
		.pme_name = "PM_MRK_LSU_FLUSH_ULD",
		.pme_code = 0xd084,
		.pme_short_desc = "Flush: (marked) Unaligned Load",
		.pme_long_desc = "A marked load was flushed because it was unaligned (crossed a 64byte boundary, or 32 byte if it missed the L1)",
	},
	[ POWER7_PME_PM_BTAC_MISS ] = {
		.pme_name = "PM_BTAC_MISS",
		.pme_code = 0x5088,
		.pme_short_desc = "BTAC Mispredicted",
		.pme_long_desc = "BTAC Mispredicted",
	},
	[ POWER7_PME_PM_MRK_LD_MISS_EXPOSED_CYC_COUNT ] = {
		.pme_name = "PM_MRK_LD_MISS_EXPOSED_CYC_COUNT",
		.pme_code = 0x1003f,
		.pme_short_desc = "Marked Load exposed Miss (use edge detect to count #)",
		.pme_long_desc = "Marked Load exposed Miss (use edge detect to count #)",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L2 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2",
		.pme_code = 0x1d040,
		.pme_short_desc = "Marked data loaded from L2",
		.pme_long_desc = "The processor's Data Cache was reloaded from the local L2 due to a marked load.",
	},
	[ POWER7_PME_PM_LSU_DCACHE_RELOAD_VALID ] = {
		.pme_name = "PM_LSU_DCACHE_RELOAD_VALID",
		.pme_code = 0xd0a2,
		.pme_short_desc = "count per sector of lines reloaded in L1 (demand + prefetch) ",
		.pme_long_desc = "count per sector of lines reloaded in L1 (demand + prefetch) ",
	},
	[ POWER7_PME_PM_VSU_FMA ] = {
		.pme_name = "PM_VSU_FMA",
		.pme_code = 0xa884,
		.pme_short_desc = "two flops operation (fmadd, fnmadd, fmsub, fnmsub) Scalar instructions only!",
		.pme_long_desc = "two flops operation (fmadd, fnmadd, fmsub, fnmsub) Scalar instructions only!",
	},
	[ POWER7_PME_PM_LSU0_FLUSH_SRQ ] = {
		.pme_name = "PM_LSU0_FLUSH_SRQ",
		.pme_code = 0xc0bc,
		.pme_short_desc = "LS0 Flush: SRQ",
		.pme_long_desc = "Load Hit Store flush.  A younger load was flushed from unit 0 because it hits (overlaps) an older store that is already in the SRQ or in the same group.  If the real addresses match but the effective addresses do not, an alias condition exists that prevents store forwarding.  If the load and store are in the same group the load must be flushed to separate the two instructions. ",
	},
	[ POWER7_PME_PM_LSU1_L1_PREF ] = {
		.pme_name = "PM_LSU1_L1_PREF",
		.pme_code = 0xd0ba,
		.pme_short_desc = " LS1 L1 cache data prefetches",
		.pme_long_desc = " LS1 L1 cache data prefetches",
	},
	[ POWER7_PME_PM_IOPS_CMPL ] = {
		.pme_name = "PM_IOPS_CMPL",
		.pme_code = 0x10014,
		.pme_short_desc = "Internal Operations completed",
		.pme_long_desc = "Number of internal operations that completed.",
	},
	[ POWER7_PME_PM_L2_SYS_PUMP ] = {
		.pme_name = "PM_L2_SYS_PUMP",
		.pme_code = 0x36482,
		.pme_short_desc = "RC req that was a global (aka system) pump attempt",
		.pme_long_desc = "RC req that was a global (aka system) pump attempt",
	},
	[ POWER7_PME_PM_L2_RCLD_BUSY_RC_FULL ] = {
		.pme_name = "PM_L2_RCLD_BUSY_RC_FULL",
		.pme_code = 0x46282,
		.pme_short_desc = " L2  activated Busy to the core for loads due to all RC full",
		.pme_long_desc = " L2  activated Busy to the core for loads due to all RC full",
	},
	[ POWER7_PME_PM_LSU_LMQ_S0_ALLOC ] = {
		.pme_name = "PM_LSU_LMQ_S0_ALLOC",
		.pme_code = 0xd0a1,
		.pme_short_desc = "Slot 0 of LMQ valid",
		.pme_long_desc = "Slot 0 of LMQ valid",
	},
	[ POWER7_PME_PM_FLUSH_DISP_SYNC ] = {
		.pme_name = "PM_FLUSH_DISP_SYNC",
		.pme_code = 0x2088,
		.pme_short_desc = "Dispatch Flush: Sync",
		.pme_long_desc = "Dispatch Flush: Sync",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_DL2L3_MOD_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_DL2L3_MOD_CYC",
		.pme_code = 0x4002a,
		.pme_short_desc = "Marked ld latency Data source 1011  (L2.75/L3.75 M different 4 chip node)",
		.pme_long_desc = "Marked ld latency Data source 1011  (L2.75/L3.75 M different 4 chip node)",
	},
	[ POWER7_PME_PM_L2_IC_INV ] = {
		.pme_name = "PM_L2_IC_INV",
		.pme_code = 0x26180,
		.pme_short_desc = "Icache Invalidates from L2 ",
		.pme_long_desc = "Icache Invalidates from L2 ",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L21_MOD_CYC ] = {
		.pme_name = "PM_MRK_DATA_FROM_L21_MOD_CYC",
		.pme_code = 0x40024,
		.pme_short_desc = "Marked ld latency Data source 0101 (L2.1 M same chip)",
		.pme_long_desc = "Marked ld latency Data source 0101 (L2.1 M same chip)",
	},
	[ POWER7_PME_PM_L3_PREF_LDST ] = {
		.pme_name = "PM_L3_PREF_LDST",
		.pme_code = 0xd8ac,
		.pme_short_desc = "L3 cache prefetches LD + ST",
		.pme_long_desc = "L3 cache prefetches LD + ST",
	},
	[ POWER7_PME_PM_LSU_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_SRQ_EMPTY_CYC",
		.pme_code = 0x40008,
		.pme_short_desc = "ALL threads srq empty",
		.pme_long_desc = "The Store Request Queue is empty",
	},
	[ POWER7_PME_PM_LSU_LMQ_S0_VALID ] = {
		.pme_name = "PM_LSU_LMQ_S0_VALID",
		.pme_code = 0xd0a0,
		.pme_short_desc = "Slot 0 of LMQ valid",
		.pme_long_desc = "This signal is asserted every cycle that the Load Request Queue slot zero is valid. The SRQ is 32 entries long and is allocated round-robin.  In SMT mode the LRQ is split between the two threads (16 entries each).",
	},
	[ POWER7_PME_PM_FLUSH_PARTIAL ] = {
		.pme_name = "PM_FLUSH_PARTIAL",
		.pme_code = 0x2086,
		.pme_short_desc = "Partial flush",
		.pme_long_desc = "Partial flush",
	},
	[ POWER7_PME_PM_VSU1_FMA_DOUBLE ] = {
		.pme_name = "PM_VSU1_FMA_DOUBLE",
		.pme_code = 0xa092,
		.pme_short_desc = "four flop DP vector operations (xvmadddp, xvnmadddp, xvmsubdp, xvmsubdp)",
		.pme_long_desc = "four flop DP vector operations (xvmadddp, xvnmadddp, xvmsubdp, xvmsubdp)",
	},
	[ POWER7_PME_PM_1PLUS_PPC_DISP ] = {
		.pme_name = "PM_1PLUS_PPC_DISP",
		.pme_code = 0x400f2,
		.pme_short_desc = "Cycles at least one Instr Dispatched",
		.pme_long_desc = "A group containing at least one PPC instruction was dispatched. For microcoded instructions that span multiple groups, this will only occur once.",
	},
	[ POWER7_PME_PM_DATA_FROM_L2MISS ] = {
		.pme_name = "PM_DATA_FROM_L2MISS",
		.pme_code = 0x200fe,
		.pme_short_desc = "Demand LD - L2 Miss (not L2 hit)",
		.pme_long_desc = "The processor's Data Cache was reloaded but not from the local L2.",
	},
	[ POWER7_PME_PM_SUSPENDED ] = {
		.pme_name = "PM_SUSPENDED",
		.pme_code = 0x0,
		.pme_short_desc = "Counter OFF",
		.pme_long_desc = "The counter is suspended (does not count)",
	},
	[ POWER7_PME_PM_VSU0_FMA ] = {
		.pme_name = "PM_VSU0_FMA",
		.pme_code = 0xa084,
		.pme_short_desc = "two flops operation (fmadd, fnmadd, fmsub, fnmsub, xsmadd, xsnmadd, xsmsub, xsnmsub) Scalar instructions only!",
		.pme_long_desc = "two flops operation (fmadd, fnmadd, fmsub, fnmsub, xsmadd, xsnmadd, xsmsub, xsnmsub) Scalar instructions only!",
	},
	[ POWER7_PME_PM_CMPLU_STALL_SCALAR ] = {
		.pme_name = "PM_CMPLU_STALL_SCALAR",
		.pme_code = 0x40012,
		.pme_short_desc = "Completion stall caused by FPU instruction",
		.pme_long_desc = "Completion stall caused by FPU instruction",
	},
	[ POWER7_PME_PM_STCX_FAIL ] = {
		.pme_name = "PM_STCX_FAIL",
		.pme_code = 0xc09a,
		.pme_short_desc = "STCX failed",
		.pme_long_desc = "A stcx (stwcx or stdcx) failed",
	},
	[ POWER7_PME_PM_VSU0_FSQRT_FDIV_DOUBLE ] = {
		.pme_name = "PM_VSU0_FSQRT_FDIV_DOUBLE",
		.pme_code = 0xa094,
		.pme_short_desc = "eight flop DP vector operations (xvfdivdp, xvsqrtdp ",
		.pme_long_desc = "eight flop DP vector operations (xvfdivdp, xvsqrtdp ",
	},
	[ POWER7_PME_PM_DC_PREF_DST ] = {
		.pme_name = "PM_DC_PREF_DST",
		.pme_code = 0xd0b0,
		.pme_short_desc = "Data Stream Touch",
		.pme_long_desc = "A prefetch stream was started using the DST instruction.",
	},
	[ POWER7_PME_PM_VSU1_SCAL_SINGLE_ISSUED ] = {
		.pme_name = "PM_VSU1_SCAL_SINGLE_ISSUED",
		.pme_code = 0xb086,
		.pme_short_desc = "Single Precision scalar instruction issued on Pipe1",
		.pme_long_desc = "Single Precision scalar instruction issued on Pipe1",
	},
	[ POWER7_PME_PM_L3_HIT ] = {
		.pme_name = "PM_L3_HIT",
		.pme_code = 0x1f080,
		.pme_short_desc = "L3 Hits",
		.pme_long_desc = "L3 Hits",
	},
	[ POWER7_PME_PM_L2_GLOB_GUESS_WRONG ] = {
		.pme_name = "PM_L2_GLOB_GUESS_WRONG",
		.pme_code = 0x26482,
		.pme_short_desc = "L2 guess glb and guess was not correct (ie data local)",
		.pme_long_desc = "L2 guess glb and guess was not correct (ie data local)",
	},
	[ POWER7_PME_PM_MRK_DFU_FIN ] = {
		.pme_name = "PM_MRK_DFU_FIN",
		.pme_code = 0x20032,
		.pme_short_desc = "Decimal Unit marked Instruction Finish",
		.pme_long_desc = "The Decimal Floating Point Unit finished a marked instruction.",
	},
	[ POWER7_PME_PM_INST_FROM_L1 ] = {
		.pme_name = "PM_INST_FROM_L1",
		.pme_code = 0x4080,
		.pme_short_desc = "Instruction fetches from L1",
		.pme_long_desc = "An instruction fetch group was fetched from L1. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_BRU_FIN ] = {
		.pme_name = "PM_BRU_FIN",
		.pme_code = 0x10068,
		.pme_short_desc = "Branch Instruction Finished ",
		.pme_long_desc = "The Branch execution unit finished an instruction",
	},
	[ POWER7_PME_PM_IC_DEMAND_REQ ] = {
		.pme_name = "PM_IC_DEMAND_REQ",
		.pme_code = 0x4088,
		.pme_short_desc = "Demand Instruction fetch request",
		.pme_long_desc = "Demand Instruction fetch request",
	},
	[ POWER7_PME_PM_VSU1_FSQRT_FDIV_DOUBLE ] = {
		.pme_name = "PM_VSU1_FSQRT_FDIV_DOUBLE",
		.pme_code = 0xa096,
		.pme_short_desc = "eight flop DP vector operations (xvfdivdp, xvsqrtdp ",
		.pme_long_desc = "eight flop DP vector operations (xvfdivdp, xvsqrtdp ",
	},
	[ POWER7_PME_PM_VSU1_FMA ] = {
		.pme_name = "PM_VSU1_FMA",
		.pme_code = 0xa086,
		.pme_short_desc = "two flops operation (fmadd, fnmadd, fmsub, fnmsub, xsmadd, xsnmadd, xsmsub, xsnmsub) Scalar instructions only!",
		.pme_long_desc = "two flops operation (fmadd, fnmadd, fmsub, fnmsub, xsmadd, xsnmadd, xsmsub, xsnmsub) Scalar instructions only!",
	},
	[ POWER7_PME_PM_MRK_LD_MISS_L1 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1",
		.pme_code = 0x20036,
		.pme_short_desc = "Marked DL1 Demand Miss",
		.pme_long_desc = "Marked L1 D cache load misses",
	},
	[ POWER7_PME_PM_VSU0_2FLOP_DOUBLE ] = {
		.pme_name = "PM_VSU0_2FLOP_DOUBLE",
		.pme_code = 0xa08c,
		.pme_short_desc = "two flop DP vector operation (xvadddp, xvmuldp, xvsubdp, xvcmpdp, xvseldp, xvabsdp, xvnabsdp, xvredp ,xvsqrtedp, vxnegdp)",
		.pme_long_desc = "two flop DP vector operation (xvadddp, xvmuldp, xvsubdp, xvcmpdp, xvseldp, xvabsdp, xvnabsdp, xvredp ,xvsqrtedp, vxnegdp)",
	},
	[ POWER7_PME_PM_LSU_DC_PREF_STRIDED_STREAM_CONFIRM ] = {
		.pme_name = "PM_LSU_DC_PREF_STRIDED_STREAM_CONFIRM",
		.pme_code = 0xd8bc,
		.pme_short_desc = "Dcache Strided prefetch stream confirmed (software + hardware)",
		.pme_long_desc = "Dcache Strided prefetch stream confirmed (software + hardware)",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_L31_SHR ] = {
		.pme_name = "PM_INST_PTEG_FROM_L31_SHR",
		.pme_code = 0x2e056,
		.pme_short_desc = "Instruction PTEG loaded from another L3 on same chip shared",
		.pme_long_desc = "Instruction PTEG loaded from another L3 on same chip shared",
	},
	[ POWER7_PME_PM_MRK_LSU_REJECT_ERAT_MISS ] = {
		.pme_name = "PM_MRK_LSU_REJECT_ERAT_MISS",
		.pme_code = 0x30064,
		.pme_short_desc = "LSU marked reject due to ERAT (up to 2 per cycle)",
		.pme_long_desc = "LSU marked reject due to ERAT (up to 2 per cycle)",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_L2MISS ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2MISS",
		.pme_code = 0x4d048,
		.pme_short_desc = "Marked data loaded missed L2",
		.pme_long_desc = "DL1 was reloaded from beyond L2 due to a marked demand load.",
	},
	[ POWER7_PME_PM_DATA_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_DATA_FROM_RL2L3_SHR",
		.pme_code = 0x1c04c,
		.pme_short_desc = "Data loaded from remote L2 or L3 shared",
		.pme_long_desc = "The processor's Data Cache was reloaded with shared (T or SL) data from an L2 or L3 on a remote module due to a demand load",
	},
	[ POWER7_PME_PM_INST_FROM_PREF ] = {
		.pme_name = "PM_INST_FROM_PREF",
		.pme_code = 0x14046,
		.pme_short_desc = "Instruction fetched from prefetch",
		.pme_long_desc = "An instruction fetch group was fetched from the prefetch buffer. Fetch groups can contain up to 8 instructions",
	},
	[ POWER7_PME_PM_VSU1_SQ ] = {
		.pme_name = "PM_VSU1_SQ",
		.pme_code = 0xb09e,
		.pme_short_desc = "Store Vector Issued on Pipe1",
		.pme_long_desc = "Store Vector Issued on Pipe1",
	},
	[ POWER7_PME_PM_L2_LD_DISP ] = {
		.pme_name = "PM_L2_LD_DISP",
		.pme_code = 0x36180,
		.pme_short_desc = "All successful load dispatches",
		.pme_long_desc = "All successful load dispatches",
	},
	[ POWER7_PME_PM_L2_DISP_ALL ] = {
		.pme_name = "PM_L2_DISP_ALL",
		.pme_code = 0x46080,
		.pme_short_desc = "All successful LD/ST dispatches for this thread(i+d)",
		.pme_long_desc = "All successful LD/ST dispatches for this thread(i+d)",
	},
	[ POWER7_PME_PM_THRD_GRP_CMPL_BOTH_CYC ] = {
		.pme_name = "PM_THRD_GRP_CMPL_BOTH_CYC",
		.pme_code = 0x10012,
		.pme_short_desc = "Cycles group completed by both threads",
		.pme_long_desc = "Cycles that both threads completed.",
	},
	[ POWER7_PME_PM_VSU_FSQRT_FDIV_DOUBLE ] = {
		.pme_name = "PM_VSU_FSQRT_FDIV_DOUBLE",
		.pme_code = 0xa894,
		.pme_short_desc = "DP vector versions of fdiv,fsqrt ",
		.pme_long_desc = "DP vector versions of fdiv,fsqrt ",
	},
	[ POWER7_PME_PM_BR_MPRED ] = {
		.pme_name = "PM_BR_MPRED",
		.pme_code = 0x400f6,
		.pme_short_desc = "Number of Branch Mispredicts",
		.pme_long_desc = "A branch instruction was incorrectly predicted. This could have been a target prediction, a condition prediction, or both",
	},
	[ POWER7_PME_PM_INST_PTEG_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_INST_PTEG_FROM_DL2L3_SHR",
		.pme_code = 0x3e054,
		.pme_short_desc = "Instruction PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "Instruction PTEG loaded from remote L2 or L3 shared",
	},
	[ POWER7_PME_PM_VSU_1FLOP ] = {
		.pme_name = "PM_VSU_1FLOP",
		.pme_code = 0xa880,
		.pme_short_desc = "one flop (fadd, fmul, fsub, fcmp, fsel, fabs, fnabs, fres, fsqrte, fneg) operation finished",
		.pme_long_desc = "one flop (fadd, fmul, fsub, fcmp, fsel, fabs, fnabs, fres, fsqrte, fneg) operation finished",
	},
	[ POWER7_PME_PM_HV_CYC ] = {
		.pme_name = "PM_HV_CYC",
		.pme_code = 0x2000a,
		.pme_short_desc = "cycles in hypervisor mode ",
		.pme_long_desc = "Cycles when the processor is executing in Hypervisor (MSR[HV] = 1 and MSR[PR]=0)",
	},
	[ POWER7_PME_PM_MRK_DATA_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_RL2L3_SHR",
		.pme_code = 0x1d04c,
		.pme_short_desc = "Marked data loaded from remote L2 or L3 shared",
		.pme_long_desc = "The processor's Data Cache was reloaded with shared (T or SL) data from an L2 or L3 on a remote module due to a marked load",
	},
	[ POWER7_PME_PM_DTLB_MISS_16M ] = {
		.pme_name = "PM_DTLB_MISS_16M",
		.pme_code = 0x4c05e,
		.pme_short_desc = "Data TLB miss for 16M page",
		.pme_long_desc = "Data TLB references to 16MB pages that missed the TLB. Page size is determined at TLB reload time.",
	},
	[ POWER7_PME_PM_MRK_LSU_FIN ] = {
		.pme_name = "PM_MRK_LSU_FIN",
		.pme_code = 0x40032,
		.pme_short_desc = "Marked LSU instruction finished",
		.pme_long_desc = "One of the Load/Store Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER7_PME_PM_LSU1_LMQ_LHR_MERGE ] = {
		.pme_name = "PM_LSU1_LMQ_LHR_MERGE",
		.pme_code = 0xd09a,
		.pme_short_desc = "LS1 Load Merge with another cacheline request",
		.pme_long_desc = "LS1 Load Merge with another cacheline request",
	},
	[ POWER7_PME_PM_IFU_FIN ] = {
		.pme_name = "PM_IFU_FIN",
		.pme_code = 0x40066,
		.pme_short_desc = "IFU Finished a (non-branch) instruction",
		.pme_long_desc = "The Instruction Fetch Unit finished an instruction",
	},
	[ POWER7_PME_PM_1THRD_CON_RUN_INSTR ] = {
		.pme_name = "PM_1THRD_CON_RUN_INSTR",
		.pme_code = 0x30062,
		.pme_short_desc = "1 thread Concurrent Run Instructions",
		.pme_long_desc = "1 thread Concurrent Run Instructions",
	},
	[ POWER7_PME_PM_CMPLU_STALL_COUNT ] = {
		.pme_name = "PM_CMPLU_STALL_COUNT",
		.pme_code = 0x4000B,
		.pme_short_desc = "Marked LSU instruction finished",
		.pme_long_desc = "One of the Load/Store Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER7_PME_PM_MEM0_PB_RD_CL ] = {
		.pme_name = "PM_MEM0_PB_RD_CL",
		.pme_code = 0x30083,
		.pme_short_desc = "Nest events (MC0/MC1/PB/GX), Pair2 Bit1",
		.pme_long_desc = "Nest events (MC0/MC1/PB/GX), Pair2 Bit1",
	},
	[ POWER7_PME_PM_THRD_1_RUN_CYC ] = {
		.pme_name = "PM_THRD_1_RUN_CYC",
		.pme_code = 0x10060,
		.pme_short_desc = "1 thread in Run Cycles",
		.pme_long_desc = "At least one thread has set its run latch. Operating systems use the run latch to indicate when they are doing useful work.  The run latch is typically cleared in the OS idle loop. This event does not respect FCWAIT.",
	},
	[ POWER7_PME_PM_THRD_2_CONC_RUN_INSTR ] = {
		.pme_name = "PM_THRD_2_CONC_RUN_INSTR",
		.pme_code = 0x40062,
		.pme_short_desc = "2 thread Concurrent Run Instructions",
		.pme_long_desc = "2 thread Concurrent Run Instructions",
	},
	[ POWER7_PME_PM_THRD_2_RUN_CYC ] = {
		.pme_name = "PM_THRD_2_RUN_CYC",
		.pme_code = 0x20060,
		.pme_short_desc = "2 thread in Run Cycles",
		.pme_long_desc = "2 thread in Run Cycles",
	},
	[ POWER7_PME_PM_THRD_3_CONC_RUN_INST ] = {
		.pme_name = "PM_THRD_3_CONC_RUN_INST",
		.pme_code = 0x10062,
		.pme_short_desc = "3 thread in Run Cycles",
		.pme_long_desc = "3 thread in Run Cycles",
	},
	[ POWER7_PME_PM_THRD_3_RUN_CYC ] = {
		.pme_name = "PM_THRD_3_RUN_CYC",
		.pme_code = 0x30060,
		.pme_short_desc = "3 thread in Run Cycles",
		.pme_long_desc = "3 thread in Run Cycles",
	},
	[ POWER7_PME_PM_THRD_4_CONC_RUN_INST ] = {
		.pme_name = "PM_THRD_4_CONC_RUN_INST",
		.pme_code = 0x20062,
		.pme_short_desc = "4 thread in Run Cycles",
		.pme_long_desc = "4 thread in Run Cycles",
	},
	[ POWER7_PME_PM_THRD_4_RUN_CYC ] = {
		.pme_name = "PM_THRD_4_RUN_CYC",
		.pme_code = 0x40060,
		.pme_short_desc = "4 thread in Run Cycles",
		.pme_long_desc = "4 thread in Run Cycles",
	},
};
#endif

