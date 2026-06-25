/****************************/
/* THIS IS OPEN SOURCE CODE */
/****************************/

#ifndef __POWER6_EVENTS_H__
#define __POWER6_EVENTS_H__

/*
* File:    power6_events.h
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
#define POWER6_PME_PM_LSU_REJECT_STQ_FULL 0
#define POWER6_PME_PM_DPU_HELD_FXU_MULTI 1
#define POWER6_PME_PM_VMX1_STALL 2
#define POWER6_PME_PM_PMC2_SAVED 3
#define POWER6_PME_PM_L2SB_IC_INV 4
#define POWER6_PME_PM_IERAT_MISS_64K 5
#define POWER6_PME_PM_THRD_PRIO_DIFF_3or4_CYC 6
#define POWER6_PME_PM_LD_REF_L1_BOTH 7
#define POWER6_PME_PM_FPU1_FCONV 8
#define POWER6_PME_PM_IBUF_FULL_COUNT 9
#define POWER6_PME_PM_MRK_LSU_DERAT_MISS 10
#define POWER6_PME_PM_MRK_ST_CMPL 11
#define POWER6_PME_PM_L2_CASTOUT_MOD 12
#define POWER6_PME_PM_FPU1_ST_FOLDED 13
#define POWER6_PME_PM_MRK_INST_TIMEO 14
#define POWER6_PME_PM_DPU_WT 15
#define POWER6_PME_PM_DPU_HELD_RESTART 16
#define POWER6_PME_PM_IERAT_MISS 17
#define POWER6_PME_PM_FPU_SINGLE 18
#define POWER6_PME_PM_MRK_PTEG_FROM_LMEM 19
#define POWER6_PME_PM_HV_COUNT 20
#define POWER6_PME_PM_L2SA_ST_HIT 21
#define POWER6_PME_PM_L2_LD_MISS_INST 22
#define POWER6_PME_PM_EXT_INT 23
#define POWER6_PME_PM_LSU1_LDF 24
#define POWER6_PME_PM_FAB_CMD_ISSUED 25
#define POWER6_PME_PM_PTEG_FROM_L21 26
#define POWER6_PME_PM_L2SA_MISS 27
#define POWER6_PME_PM_PTEG_FROM_RL2L3_MOD 28
#define POWER6_PME_PM_DPU_WT_COUNT 29
#define POWER6_PME_PM_MRK_PTEG_FROM_L25_MOD 30
#define POWER6_PME_PM_LD_HIT_L2 31
#define POWER6_PME_PM_PTEG_FROM_DL2L3_SHR 32
#define POWER6_PME_PM_MEM_DP_RQ_GLOB_LOC 33
#define POWER6_PME_PM_L3SA_MISS 34
#define POWER6_PME_PM_NO_ITAG_COUNT 35
#define POWER6_PME_PM_DSLB_MISS 36
#define POWER6_PME_PM_LSU_FLUSH_ALIGN 37
#define POWER6_PME_PM_DPU_HELD_FPU_CR 38
#define POWER6_PME_PM_PTEG_FROM_L2MISS 39
#define POWER6_PME_PM_MRK_DATA_FROM_DMEM 40
#define POWER6_PME_PM_PTEG_FROM_LMEM 41
#define POWER6_PME_PM_MRK_DERAT_REF_64K 42
#define POWER6_PME_PM_L2SA_LD_REQ_INST 43
#define POWER6_PME_PM_MRK_DERAT_MISS_16M 44
#define POWER6_PME_PM_DATA_FROM_DL2L3_MOD 45
#define POWER6_PME_PM_FPU0_FXMULT 46
#define POWER6_PME_PM_L3SB_MISS 47
#define POWER6_PME_PM_STCX_CANCEL 48
#define POWER6_PME_PM_L2SA_LD_MISS_DATA 49
#define POWER6_PME_PM_IC_INV_L2 50
#define POWER6_PME_PM_DPU_HELD 51
#define POWER6_PME_PM_PMC1_OVERFLOW 52
#define POWER6_PME_PM_THRD_PRIO_6_CYC 53
#define POWER6_PME_PM_MRK_PTEG_FROM_L3MISS 54
#define POWER6_PME_PM_MRK_LSU0_REJECT_UST 55
#define POWER6_PME_PM_MRK_INST_DISP 56
#define POWER6_PME_PM_LARX 57
#define POWER6_PME_PM_INST_CMPL 58
#define POWER6_PME_PM_FXU_IDLE 59
#define POWER6_PME_PM_MRK_DATA_FROM_DL2L3_MOD 60
#define POWER6_PME_PM_L2_LD_REQ_DATA 61
#define POWER6_PME_PM_LSU_DERAT_MISS_CYC 62
#define POWER6_PME_PM_DPU_HELD_POWER_COUNT 63
#define POWER6_PME_PM_INST_FROM_RL2L3_MOD 64
#define POWER6_PME_PM_DATA_FROM_DMEM_CYC 65
#define POWER6_PME_PM_DATA_FROM_DMEM 66
#define POWER6_PME_PM_LSU_REJECT_PARTIAL_SECTOR 67
#define POWER6_PME_PM_LSU_REJECT_DERAT_MPRED 68
#define POWER6_PME_PM_LSU1_REJECT_ULD 69
#define POWER6_PME_PM_DATA_FROM_L3_CYC 70
#define POWER6_PME_PM_FXU1_BUSY_FXU0_IDLE 71
#define POWER6_PME_PM_INST_FROM_MEM_DP 72
#define POWER6_PME_PM_LSU_FLUSH_DSI 73
#define POWER6_PME_PM_MRK_DERAT_REF_16G 74
#define POWER6_PME_PM_LSU_LDF_BOTH 75
#define POWER6_PME_PM_FPU1_1FLOP 76
#define POWER6_PME_PM_DATA_FROM_RMEM_CYC 77
#define POWER6_PME_PM_INST_PTEG_SECONDARY 78
#define POWER6_PME_PM_L1_ICACHE_MISS 79
#define POWER6_PME_PM_INST_DISP_LLA 80
#define POWER6_PME_PM_THRD_BOTH_RUN_CYC 81
#define POWER6_PME_PM_LSU_ST_CHAINED 82
#define POWER6_PME_PM_FPU1_FXDIV 83
#define POWER6_PME_PM_FREQ_UP 84
#define POWER6_PME_PM_FAB_RETRY_SYS_PUMP 85
#define POWER6_PME_PM_DATA_FROM_LMEM 86
#define POWER6_PME_PM_PMC3_OVERFLOW 87
#define POWER6_PME_PM_LSU0_REJECT_SET_MPRED 88
#define POWER6_PME_PM_LSU0_REJECT_DERAT_MPRED 89
#define POWER6_PME_PM_LSU1_REJECT_STQ_FULL 90
#define POWER6_PME_PM_MRK_BR_MPRED 91
#define POWER6_PME_PM_L2SA_ST_MISS 92
#define POWER6_PME_PM_LSU0_REJECT_EXTERN 93
#define POWER6_PME_PM_MRK_BR_TAKEN 94
#define POWER6_PME_PM_ISLB_MISS 95
#define POWER6_PME_PM_CYC 96
#define POWER6_PME_PM_FPU_FXDIV 97
#define POWER6_PME_PM_DPU_HELD_LLA_END 98
#define POWER6_PME_PM_MEM0_DP_CL_WR_LOC 99
#define POWER6_PME_PM_MRK_LSU_REJECT_ULD 100
#define POWER6_PME_PM_1PLUS_PPC_CMPL 101
#define POWER6_PME_PM_PTEG_FROM_DMEM 102
#define POWER6_PME_PM_DPU_WT_BR_MPRED_COUNT 103
#define POWER6_PME_PM_GCT_FULL_CYC 104
#define POWER6_PME_PM_INST_FROM_L25_SHR 105
#define POWER6_PME_PM_MRK_DERAT_MISS_4K 106
#define POWER6_PME_PM_DC_PREF_STREAM_ALLOC 107
#define POWER6_PME_PM_FPU1_FIN 108
#define POWER6_PME_PM_BR_MPRED_TA 109
#define POWER6_PME_PM_DPU_HELD_POWER 110
#define POWER6_PME_PM_RUN_INST_CMPL 111
#define POWER6_PME_PM_GCT_EMPTY_CYC 112
#define POWER6_PME_PM_LLA_COUNT 113
#define POWER6_PME_PM_LSU0_REJECT_NO_SCRATCH 114
#define POWER6_PME_PM_DPU_WT_IC_MISS 115
#define POWER6_PME_PM_DATA_FROM_L3MISS 116
#define POWER6_PME_PM_FPU_FPSCR 117
#define POWER6_PME_PM_VMX1_INST_ISSUED 118
#define POWER6_PME_PM_FLUSH 119
#define POWER6_PME_PM_ST_HIT_L2 120
#define POWER6_PME_PM_SYNC_CYC 121
#define POWER6_PME_PM_FAB_SYS_PUMP 122
#define POWER6_PME_PM_IC_PREF_REQ 123
#define POWER6_PME_PM_MEM0_DP_RQ_GLOB_LOC 124
#define POWER6_PME_PM_FPU_ISSUE_0 125
#define POWER6_PME_PM_THRD_PRIO_2_CYC 126
#define POWER6_PME_PM_VMX_SIMPLE_ISSUED 127
#define POWER6_PME_PM_MRK_FPU1_FIN 128
#define POWER6_PME_PM_DPU_HELD_CW 129
#define POWER6_PME_PM_L3SA_REF 130
#define POWER6_PME_PM_STCX 131
#define POWER6_PME_PM_L2SB_MISS 132
#define POWER6_PME_PM_LSU0_REJECT 133
#define POWER6_PME_PM_TB_BIT_TRANS 134
#define POWER6_PME_PM_THERMAL_MAX 135
#define POWER6_PME_PM_FPU0_STF 136
#define POWER6_PME_PM_FPU1_FMA 137
#define POWER6_PME_PM_LSU1_REJECT_LHS 138
#define POWER6_PME_PM_DPU_HELD_INT 139
#define POWER6_PME_PM_THRD_LLA_BOTH_CYC 140
#define POWER6_PME_PM_DPU_HELD_THERMAL_COUNT 141
#define POWER6_PME_PM_PMC4_REWIND 142
#define POWER6_PME_PM_DERAT_REF_16M 143
#define POWER6_PME_PM_FPU0_FCONV 144
#define POWER6_PME_PM_L2SA_LD_REQ_DATA 145
#define POWER6_PME_PM_DATA_FROM_MEM_DP 146
#define POWER6_PME_PM_MRK_VMX_FLOAT_ISSUED 147
#define POWER6_PME_PM_MRK_PTEG_FROM_L2MISS 148
#define POWER6_PME_PM_THRD_PRIO_DIFF_1or2_CYC 149
#define POWER6_PME_PM_VMX0_STALL 150
#define POWER6_PME_PM_IC_DEMAND_L2_BHT_REDIRECT 151
#define POWER6_PME_PM_LSU_DERAT_MISS 152
#define POWER6_PME_PM_FPU0_SINGLE 153
#define POWER6_PME_PM_FPU_ISSUE_STEERING 154
#define POWER6_PME_PM_THRD_PRIO_1_CYC 155
#define POWER6_PME_PM_VMX_COMPLEX_ISSUED 156
#define POWER6_PME_PM_FPU_ISSUE_ST_FOLDED 157
#define POWER6_PME_PM_DFU_FIN 158
#define POWER6_PME_PM_BR_PRED_CCACHE 159
#define POWER6_PME_PM_MRK_ST_CMPL_INT 160
#define POWER6_PME_PM_FAB_MMIO 161
#define POWER6_PME_PM_MRK_VMX_SIMPLE_ISSUED 162
#define POWER6_PME_PM_FPU_STF 163
#define POWER6_PME_PM_MEM1_DP_CL_WR_GLOB 164
#define POWER6_PME_PM_MRK_DATA_FROM_L3MISS 165
#define POWER6_PME_PM_GCT_NOSLOT_CYC 166
#define POWER6_PME_PM_L2_ST_REQ_DATA 167
#define POWER6_PME_PM_INST_TABLEWALK_COUNT 168
#define POWER6_PME_PM_PTEG_FROM_L35_SHR 169
#define POWER6_PME_PM_DPU_HELD_ISYNC 170
#define POWER6_PME_PM_MRK_DATA_FROM_L25_SHR 171
#define POWER6_PME_PM_L3SA_HIT 172
#define POWER6_PME_PM_DERAT_MISS_16G 173
#define POWER6_PME_PM_DATA_PTEG_2ND_HALF 174
#define POWER6_PME_PM_L2SA_ST_REQ 175
#define POWER6_PME_PM_INST_FROM_LMEM 176
#define POWER6_PME_PM_IC_DEMAND_L2_BR_REDIRECT 177
#define POWER6_PME_PM_PTEG_FROM_L2 178
#define POWER6_PME_PM_DATA_PTEG_1ST_HALF 179
#define POWER6_PME_PM_BR_MPRED_COUNT 180
#define POWER6_PME_PM_IERAT_MISS_4K 181
#define POWER6_PME_PM_THRD_BOTH_RUN_COUNT 182
#define POWER6_PME_PM_LSU_REJECT_ULD 183
#define POWER6_PME_PM_DATA_FROM_DL2L3_MOD_CYC 184
#define POWER6_PME_PM_MRK_PTEG_FROM_RL2L3_MOD 185
#define POWER6_PME_PM_FPU0_FLOP 186
#define POWER6_PME_PM_FPU0_FEST 187
#define POWER6_PME_PM_MRK_LSU0_REJECT_LHS 188
#define POWER6_PME_PM_VMX_RESULT_SAT_1 189
#define POWER6_PME_PM_NO_ITAG_CYC 190
#define POWER6_PME_PM_LSU1_REJECT_NO_SCRATCH 191
#define POWER6_PME_PM_0INST_FETCH 192
#define POWER6_PME_PM_DPU_WT_BR_MPRED 193
#define POWER6_PME_PM_L1_PREF 194
#define POWER6_PME_PM_VMX_FLOAT_MULTICYCLE 195
#define POWER6_PME_PM_DATA_FROM_L25_SHR_CYC 196
#define POWER6_PME_PM_DATA_FROM_L3 197
#define POWER6_PME_PM_PMC2_OVERFLOW 198
#define POWER6_PME_PM_VMX0_LD_WRBACK 199
#define POWER6_PME_PM_FPU0_DENORM 200
#define POWER6_PME_PM_INST_FETCH_CYC 201
#define POWER6_PME_PM_LSU_LDF 202
#define POWER6_PME_PM_LSU_REJECT_L2_CORR 203
#define POWER6_PME_PM_DERAT_REF_64K 204
#define POWER6_PME_PM_THRD_PRIO_3_CYC 205
#define POWER6_PME_PM_FPU_FMA 206
#define POWER6_PME_PM_INST_FROM_L35_MOD 207
#define POWER6_PME_PM_DFU_CONV 208
#define POWER6_PME_PM_INST_FROM_L25_MOD 209
#define POWER6_PME_PM_PTEG_FROM_L35_MOD 210
#define POWER6_PME_PM_MRK_VMX_ST_ISSUED 211
#define POWER6_PME_PM_VMX_FLOAT_ISSUED 212
#define POWER6_PME_PM_LSU0_REJECT_L2_CORR 213
#define POWER6_PME_PM_THRD_L2MISS 214
#define POWER6_PME_PM_FPU_FCONV 215
#define POWER6_PME_PM_FPU_FXMULT 216
#define POWER6_PME_PM_FPU1_FRSP 217
#define POWER6_PME_PM_MRK_DERAT_REF_16M 218
#define POWER6_PME_PM_L2SB_CASTOUT_SHR 219
#define POWER6_PME_PM_THRD_ONE_RUN_COUNT 220
#define POWER6_PME_PM_INST_FROM_RMEM 221
#define POWER6_PME_PM_LSU_BOTH_BUS 222
#define POWER6_PME_PM_FPU1_FSQRT_FDIV 223
#define POWER6_PME_PM_L2_LD_REQ_INST 224
#define POWER6_PME_PM_MRK_PTEG_FROM_L35_SHR 225
#define POWER6_PME_PM_BR_PRED_CR 226
#define POWER6_PME_PM_MRK_LSU0_REJECT_ULD 227
#define POWER6_PME_PM_LSU_REJECT 228
#define POWER6_PME_PM_LSU_REJECT_LHS_BOTH 229
#define POWER6_PME_PM_GXO_ADDR_CYC_BUSY 230
#define POWER6_PME_PM_LSU_SRQ_EMPTY_COUNT 231
#define POWER6_PME_PM_PTEG_FROM_L3 232
#define POWER6_PME_PM_VMX0_LD_ISSUED 233
#define POWER6_PME_PM_FXU_PIPELINED_MULT_DIV 234
#define POWER6_PME_PM_FPU1_STF 235
#define POWER6_PME_PM_DFU_ADD 236
#define POWER6_PME_PM_MEM_DP_CL_WR_GLOB 237
#define POWER6_PME_PM_MRK_LSU1_REJECT_ULD 238
#define POWER6_PME_PM_ITLB_REF 239
#define POWER6_PME_PM_LSU0_REJECT_L2MISS 240
#define POWER6_PME_PM_DATA_FROM_L35_SHR 241
#define POWER6_PME_PM_MRK_DATA_FROM_RL2L3_MOD 242
#define POWER6_PME_PM_FPU0_FPSCR 243
#define POWER6_PME_PM_DATA_FROM_L2 244
#define POWER6_PME_PM_DPU_HELD_XER 245
#define POWER6_PME_PM_FAB_NODE_PUMP 246
#define POWER6_PME_PM_VMX_RESULT_SAT_0_1 247
#define POWER6_PME_PM_LD_REF_L1 248
#define POWER6_PME_PM_TLB_REF 249
#define POWER6_PME_PM_DC_PREF_OUT_OF_STREAMS 250
#define POWER6_PME_PM_FLUSH_FPU 251
#define POWER6_PME_PM_MEM1_DP_CL_WR_LOC 252
#define POWER6_PME_PM_L2SB_LD_HIT 253
#define POWER6_PME_PM_FAB_DCLAIM 254
#define POWER6_PME_PM_MEM_DP_CL_WR_LOC 255
#define POWER6_PME_PM_BR_MPRED_CR 256
#define POWER6_PME_PM_LSU_REJECT_EXTERN 257
#define POWER6_PME_PM_DATA_FROM_RL2L3_MOD 258
#define POWER6_PME_PM_DPU_HELD_RU_WQ 259
#define POWER6_PME_PM_LD_MISS_L1 260
#define POWER6_PME_PM_DC_INV_L2 261
#define POWER6_PME_PM_MRK_PTEG_FROM_RMEM 262
#define POWER6_PME_PM_FPU_FIN 263
#define POWER6_PME_PM_FXU0_FIN 264
#define POWER6_PME_PM_DPU_HELD_FPQ 265
#define POWER6_PME_PM_GX_DMA_READ 266
#define POWER6_PME_PM_LSU1_REJECT_PARTIAL_SECTOR 267
#define POWER6_PME_PM_0INST_FETCH_COUNT 268
#define POWER6_PME_PM_PMC5_OVERFLOW 269
#define POWER6_PME_PM_L2SB_LD_REQ 270
#define POWER6_PME_PM_THRD_PRIO_DIFF_0_CYC 271
#define POWER6_PME_PM_DATA_FROM_RMEM 272
#define POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_BOTH_CYC 273
#define POWER6_PME_PM_ST_REF_L1_BOTH 274
#define POWER6_PME_PM_VMX_PERMUTE_ISSUED 275
#define POWER6_PME_PM_BR_TAKEN 276
#define POWER6_PME_PM_FAB_DMA 277
#define POWER6_PME_PM_GCT_EMPTY_COUNT 278
#define POWER6_PME_PM_FPU1_SINGLE 279
#define POWER6_PME_PM_L2SA_CASTOUT_SHR 280
#define POWER6_PME_PM_L3SB_REF 281
#define POWER6_PME_PM_FPU0_FRSP 282
#define POWER6_PME_PM_PMC4_SAVED 283
#define POWER6_PME_PM_L2SA_DC_INV 284
#define POWER6_PME_PM_GXI_ADDR_CYC_BUSY 285
#define POWER6_PME_PM_FPU0_FMA 286
#define POWER6_PME_PM_SLB_MISS 287
#define POWER6_PME_PM_MRK_ST_GPS 288
#define POWER6_PME_PM_DERAT_REF_4K 289
#define POWER6_PME_PM_L2_CASTOUT_SHR 290
#define POWER6_PME_PM_DPU_HELD_STCX_CR 291
#define POWER6_PME_PM_FPU0_ST_FOLDED 292
#define POWER6_PME_PM_MRK_DATA_FROM_L21 293
#define POWER6_PME_PM_THRD_PRIO_DIFF_minus3or4_CYC 294
#define POWER6_PME_PM_DATA_FROM_L35_MOD 295
#define POWER6_PME_PM_DATA_FROM_DL2L3_SHR 296
#define POWER6_PME_PM_GXI_DATA_CYC_BUSY 297
#define POWER6_PME_PM_LSU_REJECT_STEAL 298
#define POWER6_PME_PM_ST_FIN 299
#define POWER6_PME_PM_DPU_HELD_CR_LOGICAL 300
#define POWER6_PME_PM_THRD_SEL_T0 301
#define POWER6_PME_PM_PTEG_RELOAD_VALID 302
#define POWER6_PME_PM_L2_PREF_ST 303
#define POWER6_PME_PM_MRK_STCX_FAIL 304
#define POWER6_PME_PM_LSU0_REJECT_LHS 305
#define POWER6_PME_PM_DFU_EXP_EQ 306
#define POWER6_PME_PM_DPU_HELD_FP_FX_MULT 307
#define POWER6_PME_PM_L2_LD_MISS_DATA 308
#define POWER6_PME_PM_DATA_FROM_L35_MOD_CYC 309
#define POWER6_PME_PM_FLUSH_FXU 310
#define POWER6_PME_PM_FPU_ISSUE_1 311
#define POWER6_PME_PM_DATA_FROM_LMEM_CYC 312
#define POWER6_PME_PM_DPU_HELD_LSU_SOPS 313
#define POWER6_PME_PM_INST_PTEG_2ND_HALF 314
#define POWER6_PME_PM_THRESH_TIMEO 315
#define POWER6_PME_PM_LSU_REJECT_UST_BOTH 316
#define POWER6_PME_PM_LSU_REJECT_FAST 317
#define POWER6_PME_PM_DPU_HELD_THRD_PRIO 318
#define POWER6_PME_PM_L2_PREF_LD 319
#define POWER6_PME_PM_FPU_FEST 320
#define POWER6_PME_PM_MRK_DATA_FROM_RMEM 321
#define POWER6_PME_PM_LD_MISS_L1_CYC 322
#define POWER6_PME_PM_DERAT_MISS_4K 323
#define POWER6_PME_PM_DPU_HELD_COMPLETION 324
#define POWER6_PME_PM_FPU_ISSUE_STALL_ST 325
#define POWER6_PME_PM_L2SB_DC_INV 326
#define POWER6_PME_PM_PTEG_FROM_L25_SHR 327
#define POWER6_PME_PM_PTEG_FROM_DL2L3_MOD 328
#define POWER6_PME_PM_FAB_CMD_RETRIED 329
#define POWER6_PME_PM_BR_PRED_LSTACK 330
#define POWER6_PME_PM_GXO_DATA_CYC_BUSY 331
#define POWER6_PME_PM_DFU_SUBNORM 332
#define POWER6_PME_PM_FPU_ISSUE_OOO 333
#define POWER6_PME_PM_LSU_REJECT_ULD_BOTH 334
#define POWER6_PME_PM_L2SB_ST_MISS 335
#define POWER6_PME_PM_DATA_FROM_L25_MOD_CYC 336
#define POWER6_PME_PM_INST_PTEG_1ST_HALF 337
#define POWER6_PME_PM_DERAT_MISS_16M 338
#define POWER6_PME_PM_GX_DMA_WRITE 339
#define POWER6_PME_PM_MRK_PTEG_FROM_DL2L3_MOD 340
#define POWER6_PME_PM_MEM1_DP_RQ_GLOB_LOC 341
#define POWER6_PME_PM_L2SB_LD_REQ_DATA 342
#define POWER6_PME_PM_L2SA_LD_MISS_INST 343
#define POWER6_PME_PM_MRK_LSU0_REJECT_L2MISS 344
#define POWER6_PME_PM_MRK_IFU_FIN 345
#define POWER6_PME_PM_INST_FROM_L3 346
#define POWER6_PME_PM_FXU1_FIN 347
#define POWER6_PME_PM_THRD_PRIO_4_CYC 348
#define POWER6_PME_PM_MRK_DATA_FROM_L35_MOD 349
#define POWER6_PME_PM_LSU_REJECT_SET_MPRED 350
#define POWER6_PME_PM_MRK_DERAT_MISS_16G 351
#define POWER6_PME_PM_FPU0_FXDIV 352
#define POWER6_PME_PM_MRK_LSU1_REJECT_UST 353
#define POWER6_PME_PM_FPU_ISSUE_DIV_SQRT_OVERLAP 354
#define POWER6_PME_PM_INST_FROM_L35_SHR 355
#define POWER6_PME_PM_MRK_LSU_REJECT_LHS 356
#define POWER6_PME_PM_LSU_LMQ_FULL_CYC 357
#define POWER6_PME_PM_SYNC_COUNT 358
#define POWER6_PME_PM_MEM0_DP_RQ_LOC_GLOB 359
#define POWER6_PME_PM_L2SA_CASTOUT_MOD 360
#define POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_BOTH_COUNT 361
#define POWER6_PME_PM_PTEG_FROM_MEM_DP 362
#define POWER6_PME_PM_LSU_REJECT_SLOW 363
#define POWER6_PME_PM_PTEG_FROM_L25_MOD 364
#define POWER6_PME_PM_THRD_PRIO_7_CYC 365
#define POWER6_PME_PM_MRK_PTEG_FROM_RL2L3_SHR 366
#define POWER6_PME_PM_ST_REQ_L2 367
#define POWER6_PME_PM_ST_REF_L1 368
#define POWER6_PME_PM_FPU_ISSUE_STALL_THRD 369
#define POWER6_PME_PM_RUN_COUNT 370
#define POWER6_PME_PM_RUN_CYC 371
#define POWER6_PME_PM_PTEG_FROM_RMEM 372
#define POWER6_PME_PM_LSU0_LDF 373
#define POWER6_PME_PM_ST_MISS_L1 374
#define POWER6_PME_PM_INST_FROM_DL2L3_SHR 375
#define POWER6_PME_PM_L2SA_IC_INV 376
#define POWER6_PME_PM_THRD_ONE_RUN_CYC 377
#define POWER6_PME_PM_L2SB_LD_REQ_INST 378
#define POWER6_PME_PM_MRK_DATA_FROM_L25_MOD 379
#define POWER6_PME_PM_DPU_HELD_XTHRD 380
#define POWER6_PME_PM_L2SB_ST_REQ 381
#define POWER6_PME_PM_INST_FROM_L21 382
#define POWER6_PME_PM_INST_FROM_L3MISS 383
#define POWER6_PME_PM_L3SB_HIT 384
#define POWER6_PME_PM_EE_OFF_EXT_INT 385
#define POWER6_PME_PM_INST_FROM_DL2L3_MOD 386
#define POWER6_PME_PM_PMC6_OVERFLOW 387
#define POWER6_PME_PM_FPU_FLOP 388
#define POWER6_PME_PM_FXU_BUSY 389
#define POWER6_PME_PM_FPU1_FLOP 390
#define POWER6_PME_PM_IC_RELOAD_SHR 391
#define POWER6_PME_PM_INST_TABLEWALK_CYC 392
#define POWER6_PME_PM_DATA_FROM_RL2L3_MOD_CYC 393
#define POWER6_PME_PM_THRD_PRIO_DIFF_5or6_CYC 394
#define POWER6_PME_PM_IBUF_FULL_CYC 395
#define POWER6_PME_PM_L2SA_LD_REQ 396
#define POWER6_PME_PM_VMX1_LD_WRBACK 397
#define POWER6_PME_PM_MRK_FPU_FIN 398
#define POWER6_PME_PM_THRD_PRIO_5_CYC 399
#define POWER6_PME_PM_DFU_BACK2BACK 400
#define POWER6_PME_PM_MRK_DATA_FROM_LMEM 401
#define POWER6_PME_PM_LSU_REJECT_LHS 402
#define POWER6_PME_PM_DPU_HELD_SPR 403
#define POWER6_PME_PM_FREQ_DOWN 404
#define POWER6_PME_PM_DFU_ENC_BCD_DPD 405
#define POWER6_PME_PM_DPU_HELD_GPR 406
#define POWER6_PME_PM_LSU0_NCST 407
#define POWER6_PME_PM_MRK_INST_ISSUED 408
#define POWER6_PME_PM_INST_FROM_RL2L3_SHR 409
#define POWER6_PME_PM_FPU_DENORM 410
#define POWER6_PME_PM_PTEG_FROM_L3MISS 411
#define POWER6_PME_PM_RUN_PURR 412
#define POWER6_PME_PM_MRK_VMX0_LD_WRBACK 413
#define POWER6_PME_PM_L2_MISS 414
#define POWER6_PME_PM_MRK_DATA_FROM_L3 415
#define POWER6_PME_PM_MRK_LSU1_REJECT_LHS 416
#define POWER6_PME_PM_L2SB_LD_MISS_INST 417
#define POWER6_PME_PM_PTEG_FROM_RL2L3_SHR 418
#define POWER6_PME_PM_MRK_DERAT_MISS_64K 419
#define POWER6_PME_PM_LWSYNC 420
#define POWER6_PME_PM_FPU1_FXMULT 421
#define POWER6_PME_PM_MEM0_DP_CL_WR_GLOB 422
#define POWER6_PME_PM_LSU0_REJECT_PARTIAL_SECTOR 423
#define POWER6_PME_PM_INST_IMC_MATCH_CMPL 424
#define POWER6_PME_PM_DPU_HELD_THERMAL 425
#define POWER6_PME_PM_FPU_FRSP 426
#define POWER6_PME_PM_MRK_INST_FIN 427
#define POWER6_PME_PM_MRK_PTEG_FROM_DL2L3_SHR 428
#define POWER6_PME_PM_MRK_DTLB_REF 429
#define POWER6_PME_PM_MRK_PTEG_FROM_L25_SHR 430
#define POWER6_PME_PM_DPU_HELD_LSU 431
#define POWER6_PME_PM_FPU_FSQRT_FDIV 432
#define POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_COUNT 433
#define POWER6_PME_PM_DATA_PTEG_SECONDARY 434
#define POWER6_PME_PM_FPU1_FEST 435
#define POWER6_PME_PM_L2SA_LD_HIT 436
#define POWER6_PME_PM_DATA_FROM_MEM_DP_CYC 437
#define POWER6_PME_PM_BR_MPRED_CCACHE 438
#define POWER6_PME_PM_DPU_HELD_COUNT 439
#define POWER6_PME_PM_LSU1_REJECT_SET_MPRED 440
#define POWER6_PME_PM_FPU_ISSUE_2 441
#define POWER6_PME_PM_LSU1_REJECT_L2_CORR 442
#define POWER6_PME_PM_MRK_PTEG_FROM_DMEM 443
#define POWER6_PME_PM_MEM1_DP_RQ_LOC_GLOB 444
#define POWER6_PME_PM_THRD_PRIO_DIFF_minus1or2_CYC 445
#define POWER6_PME_PM_THRD_PRIO_0_CYC 446
#define POWER6_PME_PM_FXU0_BUSY_FXU1_IDLE 447
#define POWER6_PME_PM_LSU1_REJECT_DERAT_MPRED 448
#define POWER6_PME_PM_MRK_VMX1_LD_WRBACK 449
#define POWER6_PME_PM_DATA_FROM_RL2L3_SHR_CYC 450
#define POWER6_PME_PM_IERAT_MISS_16M 451
#define POWER6_PME_PM_MRK_DATA_FROM_MEM_DP 452
#define POWER6_PME_PM_LARX_L1HIT 453
#define POWER6_PME_PM_L2_ST_MISS_DATA 454
#define POWER6_PME_PM_FPU_ST_FOLDED 455
#define POWER6_PME_PM_MRK_DATA_FROM_L35_SHR 456
#define POWER6_PME_PM_DPU_HELD_MULT_GPR 457
#define POWER6_PME_PM_FPU0_1FLOP 458
#define POWER6_PME_PM_IERAT_MISS_16G 459
#define POWER6_PME_PM_IC_PREF_WRITE 460
#define POWER6_PME_PM_THRD_PRIO_DIFF_minus5or6_CYC 461
#define POWER6_PME_PM_FPU0_FIN 462
#define POWER6_PME_PM_DATA_FROM_L2_CYC 463
#define POWER6_PME_PM_DERAT_REF_16G 464
#define POWER6_PME_PM_BR_PRED 465
#define POWER6_PME_PM_VMX1_LD_ISSUED 466
#define POWER6_PME_PM_L2SB_CASTOUT_MOD 467
#define POWER6_PME_PM_INST_FROM_DMEM 468
#define POWER6_PME_PM_DATA_FROM_L35_SHR_CYC 469
#define POWER6_PME_PM_LSU0_NCLD 470
#define POWER6_PME_PM_FAB_RETRY_NODE_PUMP 471
#define POWER6_PME_PM_VMX0_INST_ISSUED 472
#define POWER6_PME_PM_DATA_FROM_L25_MOD 473
#define POWER6_PME_PM_DPU_HELD_ITLB_ISLB 474
#define POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC 475
#define POWER6_PME_PM_THRD_CONC_RUN_INST 476
#define POWER6_PME_PM_MRK_PTEG_FROM_L2 477
#define POWER6_PME_PM_PURR 478
#define POWER6_PME_PM_DERAT_MISS_64K 479
#define POWER6_PME_PM_PMC2_REWIND 480
#define POWER6_PME_PM_INST_FROM_L2 481
#define POWER6_PME_PM_INST_DISP 482
#define POWER6_PME_PM_DATA_FROM_L25_SHR 483
#define POWER6_PME_PM_L1_DCACHE_RELOAD_VALID 484
#define POWER6_PME_PM_LSU1_REJECT_UST 485
#define POWER6_PME_PM_FAB_ADDR_COLLISION 486
#define POWER6_PME_PM_MRK_FXU_FIN 487
#define POWER6_PME_PM_LSU0_REJECT_UST 488
#define POWER6_PME_PM_PMC4_OVERFLOW 489
#define POWER6_PME_PM_MRK_PTEG_FROM_L3 490
#define POWER6_PME_PM_INST_FROM_L2MISS 491
#define POWER6_PME_PM_L2SB_ST_HIT 492
#define POWER6_PME_PM_DPU_WT_IC_MISS_COUNT 493
#define POWER6_PME_PM_MRK_DATA_FROM_DL2L3_SHR 494
#define POWER6_PME_PM_MRK_PTEG_FROM_L35_MOD 495
#define POWER6_PME_PM_FPU1_FPSCR 496
#define POWER6_PME_PM_LSU_REJECT_UST 497
#define POWER6_PME_PM_LSU0_DERAT_MISS 498
#define POWER6_PME_PM_MRK_PTEG_FROM_MEM_DP 499
#define POWER6_PME_PM_MRK_DATA_FROM_L2 500
#define POWER6_PME_PM_FPU0_FSQRT_FDIV 501
#define POWER6_PME_PM_DPU_HELD_FXU_SOPS 502
#define POWER6_PME_PM_MRK_FPU0_FIN 503
#define POWER6_PME_PM_L2SB_LD_MISS_DATA 504
#define POWER6_PME_PM_LSU_SRQ_EMPTY_CYC 505
#define POWER6_PME_PM_1PLUS_PPC_DISP 506
#define POWER6_PME_PM_VMX_ST_ISSUED 507
#define POWER6_PME_PM_DATA_FROM_L2MISS 508
#define POWER6_PME_PM_LSU0_REJECT_ULD 509
#define POWER6_PME_PM_SUSPENDED 510
#define POWER6_PME_PM_DFU_ADD_SHIFTED_BOTH 511
#define POWER6_PME_PM_LSU_REJECT_NO_SCRATCH 512
#define POWER6_PME_PM_STCX_FAIL 513
#define POWER6_PME_PM_FPU1_DENORM 514
#define POWER6_PME_PM_GCT_NOSLOT_COUNT 515
#define POWER6_PME_PM_DATA_FROM_DL2L3_SHR_CYC 516
#define POWER6_PME_PM_DATA_FROM_L21 517
#define POWER6_PME_PM_FPU_1FLOP 518
#define POWER6_PME_PM_LSU1_REJECT 519
#define POWER6_PME_PM_IC_REQ 520
#define POWER6_PME_PM_MRK_DFU_FIN 521
#define POWER6_PME_PM_NOT_LLA_CYC 522
#define POWER6_PME_PM_INST_FROM_L1 523
#define POWER6_PME_PM_MRK_VMX_COMPLEX_ISSUED 524
#define POWER6_PME_PM_BRU_FIN 525
#define POWER6_PME_PM_LSU1_REJECT_EXTERN 526
#define POWER6_PME_PM_DATA_FROM_L21_CYC 527
#define POWER6_PME_PM_GXI_CYC_BUSY 528
#define POWER6_PME_PM_MRK_LD_MISS_L1 529
#define POWER6_PME_PM_L1_WRITE_CYC 530
#define POWER6_PME_PM_LLA_CYC 531
#define POWER6_PME_PM_MRK_DATA_FROM_L2MISS 532
#define POWER6_PME_PM_GCT_FULL_COUNT 533
#define POWER6_PME_PM_MEM_DP_RQ_LOC_GLOB 534
#define POWER6_PME_PM_DATA_FROM_RL2L3_SHR 535
#define POWER6_PME_PM_MRK_LSU_REJECT_UST 536
#define POWER6_PME_PM_MRK_VMX_PERMUTE_ISSUED 537
#define POWER6_PME_PM_MRK_PTEG_FROM_L21 538
#define POWER6_PME_PM_THRD_GRP_CMPL_BOTH_CYC 539
#define POWER6_PME_PM_BR_MPRED 540
#define POWER6_PME_PM_LD_REQ_L2 541
#define POWER6_PME_PM_FLUSH_ASYNC 542
#define POWER6_PME_PM_HV_CYC 543
#define POWER6_PME_PM_LSU1_DERAT_MISS 544
#define POWER6_PME_PM_DPU_HELD_SMT 545
#define POWER6_PME_PM_MRK_LSU_FIN 546
#define POWER6_PME_PM_MRK_DATA_FROM_RL2L3_SHR 547
#define POWER6_PME_PM_LSU0_REJECT_STQ_FULL 548
#define POWER6_PME_PM_MRK_DERAT_REF_4K 549
#define POWER6_PME_PM_FPU_ISSUE_STALL_FPR 550
#define POWER6_PME_PM_IFU_FIN 551
#define POWER6_PME_PM_GXO_CYC_BUSY 552

static const pme_power_entry_t power6_pe[] = {
	[ POWER6_PME_PM_LSU_REJECT_STQ_FULL ] = {
		.pme_name = "PM_LSU_REJECT_STQ_FULL",
		.pme_code = 0x1a0030,
		.pme_short_desc = "LSU reject due to store queue full",
		.pme_long_desc = "LSU reject due to store queue full",
	},
	[ POWER6_PME_PM_DPU_HELD_FXU_MULTI ] = {
		.pme_name = "PM_DPU_HELD_FXU_MULTI",
		.pme_code = 0x210a6,
		.pme_short_desc = "DISP unit held due to FXU multicycle",
		.pme_long_desc = "DISP unit held due to FXU multicycle",
	},
	[ POWER6_PME_PM_VMX1_STALL ] = {
		.pme_name = "PM_VMX1_STALL",
		.pme_code = 0xb008c,
		.pme_short_desc = "VMX1 stall",
		.pme_long_desc = "VMX1 stall",
	},
	[ POWER6_PME_PM_PMC2_SAVED ] = {
		.pme_name = "PM_PMC2_SAVED",
		.pme_code = 0x100022,
		.pme_short_desc = "PMC2 rewind value saved",
		.pme_long_desc = "PMC2 rewind value saved",
	},
	[ POWER6_PME_PM_L2SB_IC_INV ] = {
		.pme_name = "PM_L2SB_IC_INV",
		.pme_code = 0x5068c,
		.pme_short_desc = "L2 slice B I cache invalidate",
		.pme_long_desc = "L2 slice B I cache invalidate",
	},
	[ POWER6_PME_PM_IERAT_MISS_64K ] = {
		.pme_name = "PM_IERAT_MISS_64K",
		.pme_code = 0x392076,
		.pme_short_desc = "IERAT misses for 64K page",
		.pme_long_desc = "IERAT misses for 64K page",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_3or4_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_3or4_CYC",
		.pme_code = 0x323040,
		.pme_short_desc = "Cycles thread priority difference is 3 or 4",
		.pme_long_desc = "Cycles thread priority difference is 3 or 4",
	},
	[ POWER6_PME_PM_LD_REF_L1_BOTH ] = {
		.pme_name = "PM_LD_REF_L1_BOTH",
		.pme_code = 0x180036,
		.pme_short_desc = "Both units L1 D cache load reference",
		.pme_long_desc = "Both units L1 D cache load reference",
	},
	[ POWER6_PME_PM_FPU1_FCONV ] = {
		.pme_name = "PM_FPU1_FCONV",
		.pme_code = 0xd10a8,
		.pme_short_desc = "FPU1 executed FCONV instruction",
		.pme_long_desc = "FPU1 executed FCONV instruction",
	},
	[ POWER6_PME_PM_IBUF_FULL_COUNT ] = {
		.pme_name = "PM_IBUF_FULL_COUNT",
		.pme_code = 0x40085,
		.pme_short_desc = "Periods instruction buffer full",
		.pme_long_desc = "Periods instruction buffer full",
	},
	[ POWER6_PME_PM_MRK_LSU_DERAT_MISS ] = {
		.pme_name = "PM_MRK_LSU_DERAT_MISS",
		.pme_code = 0x400012,
		.pme_short_desc = "Marked DERAT miss",
		.pme_long_desc = "Marked DERAT miss",
	},
	[ POWER6_PME_PM_MRK_ST_CMPL ] = {
		.pme_name = "PM_MRK_ST_CMPL",
		.pme_code = 0x100006,
		.pme_short_desc = "Marked store instruction completed",
		.pme_long_desc = "A sampled store has completed (data home)",
	},
	[ POWER6_PME_PM_L2_CASTOUT_MOD ] = {
		.pme_name = "PM_L2_CASTOUT_MOD",
		.pme_code = 0x150630,
		.pme_short_desc = "L2 castouts - Modified (M, Mu, Me)",
		.pme_long_desc = "L2 castouts - Modified (M, Mu, Me)",
	},
	[ POWER6_PME_PM_FPU1_ST_FOLDED ] = {
		.pme_name = "PM_FPU1_ST_FOLDED",
		.pme_code = 0xd10ac,
		.pme_short_desc = "FPU1 folded store",
		.pme_long_desc = "FPU1 folded store",
	},
	[ POWER6_PME_PM_MRK_INST_TIMEO ] = {
		.pme_name = "PM_MRK_INST_TIMEO",
		.pme_code = 0x40003e,
		.pme_short_desc = "Marked Instruction finish timeout ",
		.pme_long_desc = "Marked Instruction finish timeout ",
	},
	[ POWER6_PME_PM_DPU_WT ] = {
		.pme_name = "PM_DPU_WT",
		.pme_code = 0x300004,
		.pme_short_desc = "Cycles DISP unit is stalled waiting for instructions",
		.pme_long_desc = "Cycles DISP unit is stalled waiting for instructions",
	},
	[ POWER6_PME_PM_DPU_HELD_RESTART ] = {
		.pme_name = "PM_DPU_HELD_RESTART",
		.pme_code = 0x30086,
		.pme_short_desc = "DISP unit held after restart coming",
		.pme_long_desc = "DISP unit held after restart coming",
	},
	[ POWER6_PME_PM_IERAT_MISS ] = {
		.pme_name = "PM_IERAT_MISS",
		.pme_code = 0x420ce,
		.pme_short_desc = "IERAT miss count",
		.pme_long_desc = "IERAT miss count",
	},
	[ POWER6_PME_PM_FPU_SINGLE ] = {
		.pme_name = "PM_FPU_SINGLE",
		.pme_code = 0x4c1030,
		.pme_short_desc = "FPU executed single precision instruction",
		.pme_long_desc = "FPU is executing single precision instruction. Combined Unit 0 + Unit 1",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_LMEM ] = {
		.pme_name = "PM_MRK_PTEG_FROM_LMEM",
		.pme_code = 0x412042,
		.pme_short_desc = "Marked PTEG loaded from local memory",
		.pme_long_desc = "Marked PTEG loaded from local memory",
	},
	[ POWER6_PME_PM_HV_COUNT ] = {
		.pme_name = "PM_HV_COUNT",
		.pme_code = 0x200017,
		.pme_short_desc = "Hypervisor Periods",
		.pme_long_desc = "Periods when the processor is executing in Hypervisor (MSR[HV] = 1 and MSR[PR]=0)",
	},
	[ POWER6_PME_PM_L2SA_ST_HIT ] = {
		.pme_name = "PM_L2SA_ST_HIT",
		.pme_code = 0x50786,
		.pme_short_desc = "L2 slice A store hits",
		.pme_long_desc = "A store request made from the core hit in the L2 directory.  This event is provided on each of the three L2 slices A,B, and C.",
	},
	[ POWER6_PME_PM_L2_LD_MISS_INST ] = {
		.pme_name = "PM_L2_LD_MISS_INST",
		.pme_code = 0x250530,
		.pme_short_desc = "L2 instruction load misses",
		.pme_long_desc = "L2 instruction load misses",
	},
	[ POWER6_PME_PM_EXT_INT ] = {
		.pme_name = "PM_EXT_INT",
		.pme_code = 0x2000f8,
		.pme_short_desc = "External interrupts",
		.pme_long_desc = "An external interrupt occurred",
	},
	[ POWER6_PME_PM_LSU1_LDF ] = {
		.pme_name = "PM_LSU1_LDF",
		.pme_code = 0x8008c,
		.pme_short_desc = "LSU1 executed Floating Point load instruction",
		.pme_long_desc = "A floating point load was executed from LSU unit 1",
	},
	[ POWER6_PME_PM_FAB_CMD_ISSUED ] = {
		.pme_name = "PM_FAB_CMD_ISSUED",
		.pme_code = 0x150130,
		.pme_short_desc = "Fabric command issued",
		.pme_long_desc = "Fabric command issued",
	},
	[ POWER6_PME_PM_PTEG_FROM_L21 ] = {
		.pme_name = "PM_PTEG_FROM_L21",
		.pme_code = 0x213048,
		.pme_short_desc = "PTEG loaded from private L2 other core",
		.pme_long_desc = "PTEG loaded from private L2 other core",
	},
	[ POWER6_PME_PM_L2SA_MISS ] = {
		.pme_name = "PM_L2SA_MISS",
		.pme_code = 0x50584,
		.pme_short_desc = "L2 slice A misses",
		.pme_long_desc = "L2 slice A misses",
	},
	[ POWER6_PME_PM_PTEG_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_PTEG_FROM_RL2L3_MOD",
		.pme_code = 0x11304c,
		.pme_short_desc = "PTEG loaded from remote L2 or L3 modified",
		.pme_long_desc = "PTEG loaded from remote L2 or L3 modified",
	},
	[ POWER6_PME_PM_DPU_WT_COUNT ] = {
		.pme_name = "PM_DPU_WT_COUNT",
		.pme_code = 0x300005,
		.pme_short_desc = "Periods DISP unit is stalled waiting for instructions",
		.pme_long_desc = "Periods DISP unit is stalled waiting for instructions",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L25_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L25_MOD",
		.pme_code = 0x312046,
		.pme_short_desc = "Marked PTEG loaded from L2.5 modified",
		.pme_long_desc = "Marked PTEG loaded from L2.5 modified",
	},
	[ POWER6_PME_PM_LD_HIT_L2 ] = {
		.pme_name = "PM_LD_HIT_L2",
		.pme_code = 0x250730,
		.pme_short_desc = "L2 D cache load hits",
		.pme_long_desc = "L2 D cache load hits",
	},
	[ POWER6_PME_PM_PTEG_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_PTEG_FROM_DL2L3_SHR",
		.pme_code = 0x31304c,
		.pme_short_desc = "PTEG loaded from distant L2 or L3 shared",
		.pme_long_desc = "PTEG loaded from distant L2 or L3 shared",
	},
	[ POWER6_PME_PM_MEM_DP_RQ_GLOB_LOC ] = {
		.pme_name = "PM_MEM_DP_RQ_GLOB_LOC",
		.pme_code = 0x150230,
		.pme_short_desc = "Memory read queue marking cache line double pump state from global to local",
		.pme_long_desc = "Memory read queue marking cache line double pump state from global to local",
	},
	[ POWER6_PME_PM_L3SA_MISS ] = {
		.pme_name = "PM_L3SA_MISS",
		.pme_code = 0x50084,
		.pme_short_desc = "L3 slice A misses",
		.pme_long_desc = "L3 slice A misses",
	},
	[ POWER6_PME_PM_NO_ITAG_COUNT ] = {
		.pme_name = "PM_NO_ITAG_COUNT",
		.pme_code = 0x40089,
		.pme_short_desc = "Periods no ITAG available",
		.pme_long_desc = "Periods no ITAG available",
	},
	[ POWER6_PME_PM_DSLB_MISS ] = {
		.pme_name = "PM_DSLB_MISS",
		.pme_code = 0x830e8,
		.pme_short_desc = "Data SLB misses",
		.pme_long_desc = "A SLB miss for a data request occurred. SLB misses trap to the operating system to resolve",
	},
	[ POWER6_PME_PM_LSU_FLUSH_ALIGN ] = {
		.pme_name = "PM_LSU_FLUSH_ALIGN",
		.pme_code = 0x220cc,
		.pme_short_desc = "Flush caused by alignment exception",
		.pme_long_desc = "Flush caused by alignment exception",
	},
	[ POWER6_PME_PM_DPU_HELD_FPU_CR ] = {
		.pme_name = "PM_DPU_HELD_FPU_CR",
		.pme_code = 0x210a0,
		.pme_short_desc = "DISP unit held due to FPU updating CR",
		.pme_long_desc = "DISP unit held due to FPU updating CR",
	},
	[ POWER6_PME_PM_PTEG_FROM_L2MISS ] = {
		.pme_name = "PM_PTEG_FROM_L2MISS",
		.pme_code = 0x113028,
		.pme_short_desc = "PTEG loaded from L2 miss",
		.pme_long_desc = "PTEG loaded from L2 miss",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_DMEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_DMEM",
		.pme_code = 0x20304a,
		.pme_short_desc = "Marked data loaded from distant memory",
		.pme_long_desc = "Marked data loaded from distant memory",
	},
	[ POWER6_PME_PM_PTEG_FROM_LMEM ] = {
		.pme_name = "PM_PTEG_FROM_LMEM",
		.pme_code = 0x41304a,
		.pme_short_desc = "PTEG loaded from local memory",
		.pme_long_desc = "PTEG loaded from local memory",
	},
	[ POWER6_PME_PM_MRK_DERAT_REF_64K ] = {
		.pme_name = "PM_MRK_DERAT_REF_64K",
		.pme_code = 0x182044,
		.pme_short_desc = "Marked DERAT reference for 64K page",
		.pme_long_desc = "Marked DERAT reference for 64K page",
	},
	[ POWER6_PME_PM_L2SA_LD_REQ_INST ] = {
		.pme_name = "PM_L2SA_LD_REQ_INST",
		.pme_code = 0x50580,
		.pme_short_desc = "L2 slice A instruction load requests",
		.pme_long_desc = "L2 slice A instruction load requests",
	},
	[ POWER6_PME_PM_MRK_DERAT_MISS_16M ] = {
		.pme_name = "PM_MRK_DERAT_MISS_16M",
		.pme_code = 0x392044,
		.pme_short_desc = "Marked DERAT misses for 16M page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 16M page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_DATA_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_DATA_FROM_DL2L3_MOD",
		.pme_code = 0x40005c,
		.pme_short_desc = "Data loaded from distant L2 or L3 modified",
		.pme_long_desc = "Data loaded from distant L2 or L3 modified",
	},
	[ POWER6_PME_PM_FPU0_FXMULT ] = {
		.pme_name = "PM_FPU0_FXMULT",
		.pme_code = 0xd0086,
		.pme_short_desc = "FPU0 executed fixed point multiplication",
		.pme_long_desc = "FPU0 executed fixed point multiplication",
	},
	[ POWER6_PME_PM_L3SB_MISS ] = {
		.pme_name = "PM_L3SB_MISS",
		.pme_code = 0x5008c,
		.pme_short_desc = "L3 slice B misses",
		.pme_long_desc = "L3 slice B misses",
	},
	[ POWER6_PME_PM_STCX_CANCEL ] = {
		.pme_name = "PM_STCX_CANCEL",
		.pme_code = 0x830ec,
		.pme_short_desc = "stcx cancel by core",
		.pme_long_desc = "stcx cancel by core",
	},
	[ POWER6_PME_PM_L2SA_LD_MISS_DATA ] = {
		.pme_name = "PM_L2SA_LD_MISS_DATA",
		.pme_code = 0x50482,
		.pme_short_desc = "L2 slice A data load misses",
		.pme_long_desc = "L2 slice A data load misses",
	},
	[ POWER6_PME_PM_IC_INV_L2 ] = {
		.pme_name = "PM_IC_INV_L2",
		.pme_code = 0x250632,
		.pme_short_desc = "L1 I cache entries invalidated from L2",
		.pme_long_desc = "L1 I cache entries invalidated from L2",
	},
	[ POWER6_PME_PM_DPU_HELD ] = {
		.pme_name = "PM_DPU_HELD",
		.pme_code = 0x200004,
		.pme_short_desc = "DISP unit held",
		.pme_long_desc = "DISP unit held",
	},
	[ POWER6_PME_PM_PMC1_OVERFLOW ] = {
		.pme_name = "PM_PMC1_OVERFLOW",
		.pme_code = 0x200014,
		.pme_short_desc = "PMC1 Overflow",
		.pme_long_desc = "PMC1 Overflow",
	},
	[ POWER6_PME_PM_THRD_PRIO_6_CYC ] = {
		.pme_name = "PM_THRD_PRIO_6_CYC",
		.pme_code = 0x222046,
		.pme_short_desc = "Cycles thread running at priority level 6",
		.pme_long_desc = "Cycles thread running at priority level 6",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L3MISS ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L3MISS",
		.pme_code = 0x312054,
		.pme_short_desc = "Marked PTEG loaded from L3 miss",
		.pme_long_desc = "Marked PTEG loaded from L3 miss",
	},
	[ POWER6_PME_PM_MRK_LSU0_REJECT_UST ] = {
		.pme_name = "PM_MRK_LSU0_REJECT_UST",
		.pme_code = 0x930e2,
		.pme_short_desc = "LSU0 marked unaligned store reject",
		.pme_long_desc = "LSU0 marked unaligned store reject",
	},
	[ POWER6_PME_PM_MRK_INST_DISP ] = {
		.pme_name = "PM_MRK_INST_DISP",
		.pme_code = 0x10001a,
		.pme_short_desc = "Marked instruction dispatched",
		.pme_long_desc = "Marked instruction dispatched",
	},
	[ POWER6_PME_PM_LARX ] = {
		.pme_name = "PM_LARX",
		.pme_code = 0x830ea,
		.pme_short_desc = "Larx executed",
		.pme_long_desc = "Larx executed",
	},
	[ POWER6_PME_PM_INST_CMPL ] = {
		.pme_name = "PM_INST_CMPL",
		.pme_code = 0x2,
		.pme_short_desc = "Instructions completed",
		.pme_long_desc = "Number of PPC instructions completed. ",
	},
	[ POWER6_PME_PM_FXU_IDLE ] = {
		.pme_name = "PM_FXU_IDLE",
		.pme_code = 0x100050,
		.pme_short_desc = "FXU idle",
		.pme_long_desc = "FXU0 and FXU1 are both idle",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_DL2L3_MOD",
		.pme_code = 0x40304c,
		.pme_short_desc = "Marked data loaded from distant L2 or L3 modified",
		.pme_long_desc = "Marked data loaded from distant L2 or L3 modified",
	},
	[ POWER6_PME_PM_L2_LD_REQ_DATA ] = {
		.pme_name = "PM_L2_LD_REQ_DATA",
		.pme_code = 0x150430,
		.pme_short_desc = "L2 data load requests",
		.pme_long_desc = "L2 data load requests",
	},
	[ POWER6_PME_PM_LSU_DERAT_MISS_CYC ] = {
		.pme_name = "PM_LSU_DERAT_MISS_CYC",
		.pme_code = 0x1000fc,
		.pme_short_desc = "DERAT miss latency",
		.pme_long_desc = "DERAT miss latency",
	},
	[ POWER6_PME_PM_DPU_HELD_POWER_COUNT ] = {
		.pme_name = "PM_DPU_HELD_POWER_COUNT",
		.pme_code = 0x20003d,
		.pme_short_desc = "Periods DISP unit held due to Power Management",
		.pme_long_desc = "Periods DISP unit held due to Power Management",
	},
	[ POWER6_PME_PM_INST_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_INST_FROM_RL2L3_MOD",
		.pme_code = 0x142044,
		.pme_short_desc = "Instruction fetched from remote L2 or L3 modified",
		.pme_long_desc = "Instruction fetched from remote L2 or L3 modified",
	},
	[ POWER6_PME_PM_DATA_FROM_DMEM_CYC ] = {
		.pme_name = "PM_DATA_FROM_DMEM_CYC",
		.pme_code = 0x20002e,
		.pme_short_desc = "Load latency from distant memory",
		.pme_long_desc = "Load latency from distant memory",
	},
	[ POWER6_PME_PM_DATA_FROM_DMEM ] = {
		.pme_name = "PM_DATA_FROM_DMEM",
		.pme_code = 0x20005e,
		.pme_short_desc = "Data loaded from distant memory",
		.pme_long_desc = "Data loaded from distant memory",
	},
	[ POWER6_PME_PM_LSU_REJECT_PARTIAL_SECTOR ] = {
		.pme_name = "PM_LSU_REJECT_PARTIAL_SECTOR",
		.pme_code = 0x1a0032,
		.pme_short_desc = "LSU reject due to partial sector valid",
		.pme_long_desc = "LSU reject due to partial sector valid",
	},
	[ POWER6_PME_PM_LSU_REJECT_DERAT_MPRED ] = {
		.pme_name = "PM_LSU_REJECT_DERAT_MPRED",
		.pme_code = 0x2a0030,
		.pme_short_desc = "LSU reject due to mispredicted DERAT",
		.pme_long_desc = "LSU reject due to mispredicted DERAT",
	},
	[ POWER6_PME_PM_LSU1_REJECT_ULD ] = {
		.pme_name = "PM_LSU1_REJECT_ULD",
		.pme_code = 0x90088,
		.pme_short_desc = "LSU1 unaligned load reject",
		.pme_long_desc = "LSU1 unaligned load reject",
	},
	[ POWER6_PME_PM_DATA_FROM_L3_CYC ] = {
		.pme_name = "PM_DATA_FROM_L3_CYC",
		.pme_code = 0x200022,
		.pme_short_desc = "Load latency from L3",
		.pme_long_desc = "Load latency from L3",
	},
	[ POWER6_PME_PM_FXU1_BUSY_FXU0_IDLE ] = {
		.pme_name = "PM_FXU1_BUSY_FXU0_IDLE",
		.pme_code = 0x400050,
		.pme_short_desc = "FXU1 busy FXU0 idle",
		.pme_long_desc = "FXU0 was idle while FXU1 was busy",
	},
	[ POWER6_PME_PM_INST_FROM_MEM_DP ] = {
		.pme_name = "PM_INST_FROM_MEM_DP",
		.pme_code = 0x142042,
		.pme_short_desc = "Instruction fetched from double pump memory",
		.pme_long_desc = "Instruction fetched from double pump memory",
	},
	[ POWER6_PME_PM_LSU_FLUSH_DSI ] = {
		.pme_name = "PM_LSU_FLUSH_DSI",
		.pme_code = 0x220ce,
		.pme_short_desc = "Flush caused by DSI",
		.pme_long_desc = "Flush caused by DSI",
	},
	[ POWER6_PME_PM_MRK_DERAT_REF_16G ] = {
		.pme_name = "PM_MRK_DERAT_REF_16G",
		.pme_code = 0x482044,
		.pme_short_desc = "Marked DERAT reference for 16G page",
		.pme_long_desc = "Marked DERAT reference for 16G page",
	},
	[ POWER6_PME_PM_LSU_LDF_BOTH ] = {
		.pme_name = "PM_LSU_LDF_BOTH",
		.pme_code = 0x180038,
		.pme_short_desc = "Both LSU units executed Floating Point load instruction",
		.pme_long_desc = "Both LSU units executed Floating Point load instruction",
	},
	[ POWER6_PME_PM_FPU1_1FLOP ] = {
		.pme_name = "PM_FPU1_1FLOP",
		.pme_code = 0xc0088,
		.pme_short_desc = "FPU1 executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo",
	},
	[ POWER6_PME_PM_DATA_FROM_RMEM_CYC ] = {
		.pme_name = "PM_DATA_FROM_RMEM_CYC",
		.pme_code = 0x40002c,
		.pme_short_desc = "Load latency from remote memory",
		.pme_long_desc = "Load latency from remote memory",
	},
	[ POWER6_PME_PM_INST_PTEG_SECONDARY ] = {
		.pme_name = "PM_INST_PTEG_SECONDARY",
		.pme_code = 0x910ac,
		.pme_short_desc = "Instruction table walk matched in secondary PTEG",
		.pme_long_desc = "Instruction table walk matched in secondary PTEG",
	},
	[ POWER6_PME_PM_L1_ICACHE_MISS ] = {
		.pme_name = "PM_L1_ICACHE_MISS",
		.pme_code = 0x100056,
		.pme_short_desc = "L1 I cache miss count",
		.pme_long_desc = "L1 I cache miss count",
	},
	[ POWER6_PME_PM_INST_DISP_LLA ] = {
		.pme_name = "PM_INST_DISP_LLA",
		.pme_code = 0x310a2,
		.pme_short_desc = "Instruction dispatched under load look ahead",
		.pme_long_desc = "Instruction dispatched under load look ahead",
	},
	[ POWER6_PME_PM_THRD_BOTH_RUN_CYC ] = {
		.pme_name = "PM_THRD_BOTH_RUN_CYC",
		.pme_code = 0x400004,
		.pme_short_desc = "Both threads in run cycles",
		.pme_long_desc = "Both threads in run cycles",
	},
	[ POWER6_PME_PM_LSU_ST_CHAINED ] = {
		.pme_name = "PM_LSU_ST_CHAINED",
		.pme_code = 0x820ce,
		.pme_short_desc = "number of chained stores",
		.pme_long_desc = "number of chained stores",
	},
	[ POWER6_PME_PM_FPU1_FXDIV ] = {
		.pme_name = "PM_FPU1_FXDIV",
		.pme_code = 0xc10a8,
		.pme_short_desc = "FPU1 executed fixed point division",
		.pme_long_desc = "FPU1 executed fixed point division",
	},
	[ POWER6_PME_PM_FREQ_UP ] = {
		.pme_name = "PM_FREQ_UP",
		.pme_code = 0x40003c,
		.pme_short_desc = "Frequency is being slewed up due to Power Management",
		.pme_long_desc = "Frequency is being slewed up due to Power Management",
	},
	[ POWER6_PME_PM_FAB_RETRY_SYS_PUMP ] = {
		.pme_name = "PM_FAB_RETRY_SYS_PUMP",
		.pme_code = 0x50182,
		.pme_short_desc = "Retry of a system pump, locally mastered  ",
		.pme_long_desc = "Retry of a system pump, locally mastered  ",
	},
	[ POWER6_PME_PM_DATA_FROM_LMEM ] = {
		.pme_name = "PM_DATA_FROM_LMEM",
		.pme_code = 0x40005e,
		.pme_short_desc = "Data loaded from local memory",
		.pme_long_desc = "Data loaded from local memory",
	},
	[ POWER6_PME_PM_PMC3_OVERFLOW ] = {
		.pme_name = "PM_PMC3_OVERFLOW",
		.pme_code = 0x400014,
		.pme_short_desc = "PMC3 Overflow",
		.pme_long_desc = "PMC3 Overflow",
	},
	[ POWER6_PME_PM_LSU0_REJECT_SET_MPRED ] = {
		.pme_name = "PM_LSU0_REJECT_SET_MPRED",
		.pme_code = 0xa0084,
		.pme_short_desc = "LSU0 reject due to mispredicted set",
		.pme_long_desc = "LSU0 reject due to mispredicted set",
	},
	[ POWER6_PME_PM_LSU0_REJECT_DERAT_MPRED ] = {
		.pme_name = "PM_LSU0_REJECT_DERAT_MPRED",
		.pme_code = 0xa0082,
		.pme_short_desc = "LSU0 reject due to mispredicted DERAT",
		.pme_long_desc = "LSU0 reject due to mispredicted DERAT",
	},
	[ POWER6_PME_PM_LSU1_REJECT_STQ_FULL ] = {
		.pme_name = "PM_LSU1_REJECT_STQ_FULL",
		.pme_code = 0xa0088,
		.pme_short_desc = "LSU1 reject due to store queue full",
		.pme_long_desc = "LSU1 reject due to store queue full",
	},
	[ POWER6_PME_PM_MRK_BR_MPRED ] = {
		.pme_name = "PM_MRK_BR_MPRED",
		.pme_code = 0x300052,
		.pme_short_desc = "Marked branch mispredicted",
		.pme_long_desc = "Marked branch mispredicted",
	},
	[ POWER6_PME_PM_L2SA_ST_MISS ] = {
		.pme_name = "PM_L2SA_ST_MISS",
		.pme_code = 0x50486,
		.pme_short_desc = "L2 slice A store misses",
		.pme_long_desc = "L2 slice A store misses",
	},
	[ POWER6_PME_PM_LSU0_REJECT_EXTERN ] = {
		.pme_name = "PM_LSU0_REJECT_EXTERN",
		.pme_code = 0xa10a4,
		.pme_short_desc = "LSU0 external reject request ",
		.pme_long_desc = "LSU0 external reject request ",
	},
	[ POWER6_PME_PM_MRK_BR_TAKEN ] = {
		.pme_name = "PM_MRK_BR_TAKEN",
		.pme_code = 0x100052,
		.pme_short_desc = "Marked branch taken",
		.pme_long_desc = "Marked branch taken",
	},
	[ POWER6_PME_PM_ISLB_MISS ] = {
		.pme_name = "PM_ISLB_MISS",
		.pme_code = 0x830e0,
		.pme_short_desc = "Instruction SLB misses",
		.pme_long_desc = "A SLB miss for an instruction fetch as occurred",
	},
	[ POWER6_PME_PM_CYC ] = {
		.pme_name = "PM_CYC",
		.pme_code = 0x1e,
		.pme_short_desc = "Processor cycles",
		.pme_long_desc = "Processor cycles",
	},
	[ POWER6_PME_PM_FPU_FXDIV ] = {
		.pme_name = "PM_FPU_FXDIV",
		.pme_code = 0x1c1034,
		.pme_short_desc = "FPU executed fixed point division",
		.pme_long_desc = "FPU executed fixed point division",
	},
	[ POWER6_PME_PM_DPU_HELD_LLA_END ] = {
		.pme_name = "PM_DPU_HELD_LLA_END",
		.pme_code = 0x30084,
		.pme_short_desc = "DISP unit held due to load look ahead ended",
		.pme_long_desc = "DISP unit held due to load look ahead ended",
	},
	[ POWER6_PME_PM_MEM0_DP_CL_WR_LOC ] = {
		.pme_name = "PM_MEM0_DP_CL_WR_LOC",
		.pme_code = 0x50286,
		.pme_short_desc = "cacheline write setting dp to local side 0",
		.pme_long_desc = "cacheline write setting dp to local side 0",
	},
	[ POWER6_PME_PM_MRK_LSU_REJECT_ULD ] = {
		.pme_name = "PM_MRK_LSU_REJECT_ULD",
		.pme_code = 0x193034,
		.pme_short_desc = "Marked unaligned load reject",
		.pme_long_desc = "Marked unaligned load reject",
	},
	[ POWER6_PME_PM_1PLUS_PPC_CMPL ] = {
		.pme_name = "PM_1PLUS_PPC_CMPL",
		.pme_code = 0x100004,
		.pme_short_desc = "One or more PPC instruction completed",
		.pme_long_desc = "A group containing at least one PPC instruction completed. For microcoded instructions that span multiple groups, this will only occur once.",
	},
	[ POWER6_PME_PM_PTEG_FROM_DMEM ] = {
		.pme_name = "PM_PTEG_FROM_DMEM",
		.pme_code = 0x21304a,
		.pme_short_desc = "PTEG loaded from distant memory",
		.pme_long_desc = "PTEG loaded from distant memory",
	},
	[ POWER6_PME_PM_DPU_WT_BR_MPRED_COUNT ] = {
		.pme_name = "PM_DPU_WT_BR_MPRED_COUNT",
		.pme_code = 0x40000d,
		.pme_short_desc = "Periods DISP unit is stalled due to branch misprediction",
		.pme_long_desc = "Periods DISP unit is stalled due to branch misprediction",
	},
	[ POWER6_PME_PM_GCT_FULL_CYC ] = {
		.pme_name = "PM_GCT_FULL_CYC",
		.pme_code = 0x40086,
		.pme_short_desc = "Cycles GCT full",
		.pme_long_desc = "The ISU sends a signal indicating the gct is full. ",
	},
	[ POWER6_PME_PM_INST_FROM_L25_SHR ] = {
		.pme_name = "PM_INST_FROM_L25_SHR",
		.pme_code = 0x442046,
		.pme_short_desc = "Instruction fetched from L2.5 shared",
		.pme_long_desc = "Instruction fetched from L2.5 shared",
	},
	[ POWER6_PME_PM_MRK_DERAT_MISS_4K ] = {
		.pme_name = "PM_MRK_DERAT_MISS_4K",
		.pme_code = 0x292044,
		.pme_short_desc = "Marked DERAT misses for 4K page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 4K page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_DC_PREF_STREAM_ALLOC ] = {
		.pme_name = "PM_DC_PREF_STREAM_ALLOC",
		.pme_code = 0x810a2,
		.pme_short_desc = "D cache new prefetch stream allocated",
		.pme_long_desc = "A new Prefetch Stream was allocated",
	},
	[ POWER6_PME_PM_FPU1_FIN ] = {
		.pme_name = "PM_FPU1_FIN",
		.pme_code = 0xd0088,
		.pme_short_desc = "FPU1 produced a result",
		.pme_long_desc = "fp1 finished, produced a result. This only indicates finish, not completion. ",
	},
	[ POWER6_PME_PM_BR_MPRED_TA ] = {
		.pme_name = "PM_BR_MPRED_TA",
		.pme_code = 0x410ac,
		.pme_short_desc = "Branch mispredictions due to target address",
		.pme_long_desc = "branch miss predict due to a target address prediction. This signal will be asserted each time the branch execution unit detects an incorrect target address prediction. This signal will be asserted after a valid branch execution unit issue and will cause a branch mispredict flush unless a flush is detected from an older instruction.",
	},
	[ POWER6_PME_PM_DPU_HELD_POWER ] = {
		.pme_name = "PM_DPU_HELD_POWER",
		.pme_code = 0x20003c,
		.pme_short_desc = "DISP unit held due to Power Management",
		.pme_long_desc = "DISP unit held due to Power Management",
	},
	[ POWER6_PME_PM_RUN_INST_CMPL ] = {
		.pme_name = "PM_RUN_INST_CMPL",
		.pme_code = 0x500009,
		.pme_short_desc = "Run instructions completed",
		.pme_long_desc = "Number of run instructions completed. ",
	},
	[ POWER6_PME_PM_GCT_EMPTY_CYC ] = {
		.pme_name = "PM_GCT_EMPTY_CYC",
		.pme_code = 0x1000f8,
		.pme_short_desc = "Cycles GCT empty",
		.pme_long_desc = "The Global Completion Table is completely empty",
	},
	[ POWER6_PME_PM_LLA_COUNT ] = {
		.pme_name = "PM_LLA_COUNT",
		.pme_code = 0xc01f,
		.pme_short_desc = "Transitions into Load Look Ahead mode",
		.pme_long_desc = "Transitions into Load Look Ahead mode",
	},
	[ POWER6_PME_PM_LSU0_REJECT_NO_SCRATCH ] = {
		.pme_name = "PM_LSU0_REJECT_NO_SCRATCH",
		.pme_code = 0xa10a2,
		.pme_short_desc = "LSU0 reject due to scratch register not available",
		.pme_long_desc = "LSU0 reject due to scratch register not available",
	},
	[ POWER6_PME_PM_DPU_WT_IC_MISS ] = {
		.pme_name = "PM_DPU_WT_IC_MISS",
		.pme_code = 0x20000c,
		.pme_short_desc = "Cycles DISP unit is stalled due to I cache miss",
		.pme_long_desc = "Cycles DISP unit is stalled due to I cache miss",
	},
	[ POWER6_PME_PM_DATA_FROM_L3MISS ] = {
		.pme_name = "PM_DATA_FROM_L3MISS",
		.pme_code = 0x3000fe,
		.pme_short_desc = "Data loaded from private L3 miss",
		.pme_long_desc = "Data loaded from private L3 miss",
	},
	[ POWER6_PME_PM_FPU_FPSCR ] = {
		.pme_name = "PM_FPU_FPSCR",
		.pme_code = 0x2d0032,
		.pme_short_desc = "FPU executed FPSCR instruction",
		.pme_long_desc = "FPU executed FPSCR instruction",
	},
	[ POWER6_PME_PM_VMX1_INST_ISSUED ] = {
		.pme_name = "PM_VMX1_INST_ISSUED",
		.pme_code = 0x60088,
		.pme_short_desc = "VMX1 instruction issued",
		.pme_long_desc = "VMX1 instruction issued",
	},
	[ POWER6_PME_PM_FLUSH ] = {
		.pme_name = "PM_FLUSH",
		.pme_code = 0x100010,
		.pme_short_desc = "Flushes",
		.pme_long_desc = "Flushes",
	},
	[ POWER6_PME_PM_ST_HIT_L2 ] = {
		.pme_name = "PM_ST_HIT_L2",
		.pme_code = 0x150732,
		.pme_short_desc = "L2 D cache store hits",
		.pme_long_desc = "L2 D cache store hits",
	},
	[ POWER6_PME_PM_SYNC_CYC ] = {
		.pme_name = "PM_SYNC_CYC",
		.pme_code = 0x920cc,
		.pme_short_desc = "Sync duration",
		.pme_long_desc = "Sync duration",
	},
	[ POWER6_PME_PM_FAB_SYS_PUMP ] = {
		.pme_name = "PM_FAB_SYS_PUMP",
		.pme_code = 0x50180,
		.pme_short_desc = "System pump operation, locally mastered",
		.pme_long_desc = "System pump operation, locally mastered",
	},
	[ POWER6_PME_PM_IC_PREF_REQ ] = {
		.pme_name = "PM_IC_PREF_REQ",
		.pme_code = 0x4008c,
		.pme_short_desc = "Instruction prefetch requests",
		.pme_long_desc = "Asserted when a non-canceled prefetch is made to the cache interface unit (CIU).",
	},
	[ POWER6_PME_PM_MEM0_DP_RQ_GLOB_LOC ] = {
		.pme_name = "PM_MEM0_DP_RQ_GLOB_LOC",
		.pme_code = 0x50280,
		.pme_short_desc = "Memory read queue marking cache line double pump state from global to local side 0",
		.pme_long_desc = "Memory read queue marking cache line double pump state from global to local side 0",
	},
	[ POWER6_PME_PM_FPU_ISSUE_0 ] = {
		.pme_name = "PM_FPU_ISSUE_0",
		.pme_code = 0x320c6,
		.pme_short_desc = "FPU issue 0 per cycle",
		.pme_long_desc = "FPU issue 0 per cycle",
	},
	[ POWER6_PME_PM_THRD_PRIO_2_CYC ] = {
		.pme_name = "PM_THRD_PRIO_2_CYC",
		.pme_code = 0x322040,
		.pme_short_desc = "Cycles thread running at priority level 2",
		.pme_long_desc = "Cycles thread running at priority level 2",
	},
	[ POWER6_PME_PM_VMX_SIMPLE_ISSUED ] = {
		.pme_name = "PM_VMX_SIMPLE_ISSUED",
		.pme_code = 0x70082,
		.pme_short_desc = "VMX instruction issued to simple",
		.pme_long_desc = "VMX instruction issued to simple",
	},
	[ POWER6_PME_PM_MRK_FPU1_FIN ] = {
		.pme_name = "PM_MRK_FPU1_FIN",
		.pme_code = 0xd008a,
		.pme_short_desc = "Marked instruction FPU1 processing finished",
		.pme_long_desc = "Marked instruction FPU1 processing finished",
	},
	[ POWER6_PME_PM_DPU_HELD_CW ] = {
		.pme_name = "PM_DPU_HELD_CW",
		.pme_code = 0x20084,
		.pme_short_desc = "DISP unit held due to cache writes ",
		.pme_long_desc = "DISP unit held due to cache writes ",
	},
	[ POWER6_PME_PM_L3SA_REF ] = {
		.pme_name = "PM_L3SA_REF",
		.pme_code = 0x50080,
		.pme_short_desc = "L3 slice A references",
		.pme_long_desc = "L3 slice A references",
	},
	[ POWER6_PME_PM_STCX ] = {
		.pme_name = "PM_STCX",
		.pme_code = 0x830e6,
		.pme_short_desc = "STCX executed",
		.pme_long_desc = "STCX executed",
	},
	[ POWER6_PME_PM_L2SB_MISS ] = {
		.pme_name = "PM_L2SB_MISS",
		.pme_code = 0x5058c,
		.pme_short_desc = "L2 slice B misses",
		.pme_long_desc = "L2 slice B misses",
	},
	[ POWER6_PME_PM_LSU0_REJECT ] = {
		.pme_name = "PM_LSU0_REJECT",
		.pme_code = 0xa10a6,
		.pme_short_desc = "LSU0 reject",
		.pme_long_desc = "LSU0 reject",
	},
	[ POWER6_PME_PM_TB_BIT_TRANS ] = {
		.pme_name = "PM_TB_BIT_TRANS",
		.pme_code = 0x100026,
		.pme_short_desc = "Time Base bit transition",
		.pme_long_desc = "When the selected time base bit (as specified in MMCR0[TBSEL])transitions from 0 to 1 ",
	},
	[ POWER6_PME_PM_THERMAL_MAX ] = {
		.pme_name = "PM_THERMAL_MAX",
		.pme_code = 0x30002a,
		.pme_short_desc = "Processor in thermal MAX",
		.pme_long_desc = "Processor in thermal MAX",
	},
	[ POWER6_PME_PM_FPU0_STF ] = {
		.pme_name = "PM_FPU0_STF",
		.pme_code = 0xc10a4,
		.pme_short_desc = "FPU0 executed store instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing a store instruction.",
	},
	[ POWER6_PME_PM_FPU1_FMA ] = {
		.pme_name = "PM_FPU1_FMA",
		.pme_code = 0xc008a,
		.pme_short_desc = "FPU1 executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER6_PME_PM_LSU1_REJECT_LHS ] = {
		.pme_name = "PM_LSU1_REJECT_LHS",
		.pme_code = 0x9008e,
		.pme_short_desc = "LSU1 load hit store reject",
		.pme_long_desc = "LSU1 load hit store reject",
	},
	[ POWER6_PME_PM_DPU_HELD_INT ] = {
		.pme_name = "PM_DPU_HELD_INT",
		.pme_code = 0x310a8,
		.pme_short_desc = "DISP unit held due to exception",
		.pme_long_desc = "DISP unit held due to exception",
	},
	[ POWER6_PME_PM_THRD_LLA_BOTH_CYC ] = {
		.pme_name = "PM_THRD_LLA_BOTH_CYC",
		.pme_code = 0x400008,
		.pme_short_desc = "Both threads in Load Look Ahead",
		.pme_long_desc = "Both threads in Load Look Ahead",
	},
	[ POWER6_PME_PM_DPU_HELD_THERMAL_COUNT ] = {
		.pme_name = "PM_DPU_HELD_THERMAL_COUNT",
		.pme_code = 0x10002b,
		.pme_short_desc = "Periods DISP unit held due to thermal condition",
		.pme_long_desc = "Periods DISP unit held due to thermal condition",
	},
	[ POWER6_PME_PM_PMC4_REWIND ] = {
		.pme_name = "PM_PMC4_REWIND",
		.pme_code = 0x100020,
		.pme_short_desc = "PMC4 rewind event",
		.pme_long_desc = "PMC4 rewind event",
	},
	[ POWER6_PME_PM_DERAT_REF_16M ] = {
		.pme_name = "PM_DERAT_REF_16M",
		.pme_code = 0x382070,
		.pme_short_desc = "DERAT reference for 16M page",
		.pme_long_desc = "DERAT reference for 16M page",
	},
	[ POWER6_PME_PM_FPU0_FCONV ] = {
		.pme_name = "PM_FPU0_FCONV",
		.pme_code = 0xd10a0,
		.pme_short_desc = "FPU0 executed FCONV instruction",
		.pme_long_desc = "FPU0 executed FCONV instruction",
	},
	[ POWER6_PME_PM_L2SA_LD_REQ_DATA ] = {
		.pme_name = "PM_L2SA_LD_REQ_DATA",
		.pme_code = 0x50480,
		.pme_short_desc = "L2 slice A data load requests",
		.pme_long_desc = "L2 slice A data load requests",
	},
	[ POWER6_PME_PM_DATA_FROM_MEM_DP ] = {
		.pme_name = "PM_DATA_FROM_MEM_DP",
		.pme_code = 0x10005e,
		.pme_short_desc = "Data loaded from double pump memory",
		.pme_long_desc = "Data loaded from double pump memory",
	},
	[ POWER6_PME_PM_MRK_VMX_FLOAT_ISSUED ] = {
		.pme_name = "PM_MRK_VMX_FLOAT_ISSUED",
		.pme_code = 0x70088,
		.pme_short_desc = "Marked VMX instruction issued to float",
		.pme_long_desc = "Marked VMX instruction issued to float",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L2MISS ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L2MISS",
		.pme_code = 0x412054,
		.pme_short_desc = "Marked PTEG loaded from L2 miss",
		.pme_long_desc = "Marked PTEG loaded from L2 miss",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_1or2_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_1or2_CYC",
		.pme_code = 0x223040,
		.pme_short_desc = "Cycles thread priority difference is 1 or 2",
		.pme_long_desc = "Cycles thread priority difference is 1 or 2",
	},
	[ POWER6_PME_PM_VMX0_STALL ] = {
		.pme_name = "PM_VMX0_STALL",
		.pme_code = 0xb0084,
		.pme_short_desc = "VMX0 stall",
		.pme_long_desc = "VMX0 stall",
	},
	[ POWER6_PME_PM_IC_DEMAND_L2_BHT_REDIRECT ] = {
		.pme_name = "PM_IC_DEMAND_L2_BHT_REDIRECT",
		.pme_code = 0x420ca,
		.pme_short_desc = "L2 I cache demand request due to BHT redirect",
		.pme_long_desc = "L2 I cache demand request due to BHT redirect",
	},
	[ POWER6_PME_PM_LSU_DERAT_MISS ] = {
		.pme_name = "PM_LSU_DERAT_MISS",
		.pme_code = 0x20000e,
		.pme_short_desc = "DERAT misses",
		.pme_long_desc = "Total DERAT Misses (Unit 0 + Unit 1). Requests that miss the Derat are rejected and retried until the request hits in the Erat. This may result in multiple erat misses for the same instruction.",
	},
	[ POWER6_PME_PM_FPU0_SINGLE ] = {
		.pme_name = "PM_FPU0_SINGLE",
		.pme_code = 0xc10a6,
		.pme_short_desc = "FPU0 executed single precision instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing single precision instruction.",
	},
	[ POWER6_PME_PM_FPU_ISSUE_STEERING ] = {
		.pme_name = "PM_FPU_ISSUE_STEERING",
		.pme_code = 0x320c4,
		.pme_short_desc = "FPU issue steering",
		.pme_long_desc = "FPU issue steering",
	},
	[ POWER6_PME_PM_THRD_PRIO_1_CYC ] = {
		.pme_name = "PM_THRD_PRIO_1_CYC",
		.pme_code = 0x222040,
		.pme_short_desc = "Cycles thread running at priority level 1",
		.pme_long_desc = "Cycles thread running at priority level 1",
	},
	[ POWER6_PME_PM_VMX_COMPLEX_ISSUED ] = {
		.pme_name = "PM_VMX_COMPLEX_ISSUED",
		.pme_code = 0x70084,
		.pme_short_desc = "VMX instruction issued to complex",
		.pme_long_desc = "VMX instruction issued to complex",
	},
	[ POWER6_PME_PM_FPU_ISSUE_ST_FOLDED ] = {
		.pme_name = "PM_FPU_ISSUE_ST_FOLDED",
		.pme_code = 0x320c2,
		.pme_short_desc = "FPU issue a folded store",
		.pme_long_desc = "FPU issue a folded store",
	},
	[ POWER6_PME_PM_DFU_FIN ] = {
		.pme_name = "PM_DFU_FIN",
		.pme_code = 0xe0080,
		.pme_short_desc = "DFU instruction finish",
		.pme_long_desc = "DFU instruction finish",
	},
	[ POWER6_PME_PM_BR_PRED_CCACHE ] = {
		.pme_name = "PM_BR_PRED_CCACHE",
		.pme_code = 0x410a4,
		.pme_short_desc = "Branch count cache prediction",
		.pme_long_desc = "Branch count cache prediction",
	},
	[ POWER6_PME_PM_MRK_ST_CMPL_INT ] = {
		.pme_name = "PM_MRK_ST_CMPL_INT",
		.pme_code = 0x300006,
		.pme_short_desc = "Marked store completed with intervention",
		.pme_long_desc = "A marked store previously sent to the memory subsystem completed (data home) after requiring intervention",
	},
	[ POWER6_PME_PM_FAB_MMIO ] = {
		.pme_name = "PM_FAB_MMIO",
		.pme_code = 0x50186,
		.pme_short_desc = "MMIO operation, locally mastered",
		.pme_long_desc = "MMIO operation, locally mastered",
	},
	[ POWER6_PME_PM_MRK_VMX_SIMPLE_ISSUED ] = {
		.pme_name = "PM_MRK_VMX_SIMPLE_ISSUED",
		.pme_code = 0x7008a,
		.pme_short_desc = "Marked VMX instruction issued to simple",
		.pme_long_desc = "Marked VMX instruction issued to simple",
	},
	[ POWER6_PME_PM_FPU_STF ] = {
		.pme_name = "PM_FPU_STF",
		.pme_code = 0x3c1030,
		.pme_short_desc = "FPU executed store instruction",
		.pme_long_desc = "FPU is executing a store instruction. Combined Unit 0 + Unit 1",
	},
	[ POWER6_PME_PM_MEM1_DP_CL_WR_GLOB ] = {
		.pme_name = "PM_MEM1_DP_CL_WR_GLOB",
		.pme_code = 0x5028c,
		.pme_short_desc = "cacheline write setting dp to global side 1",
		.pme_long_desc = "cacheline write setting dp to global side 1",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L3MISS ] = {
		.pme_name = "PM_MRK_DATA_FROM_L3MISS",
		.pme_code = 0x303028,
		.pme_short_desc = "Marked data loaded from L3 miss",
		.pme_long_desc = "Marked data loaded from L3 miss",
	},
	[ POWER6_PME_PM_GCT_NOSLOT_CYC ] = {
		.pme_name = "PM_GCT_NOSLOT_CYC",
		.pme_code = 0x100008,
		.pme_short_desc = "Cycles no GCT slot allocated",
		.pme_long_desc = "Cycles this thread does not have any slots allocated in the GCT.",
	},
	[ POWER6_PME_PM_L2_ST_REQ_DATA ] = {
		.pme_name = "PM_L2_ST_REQ_DATA",
		.pme_code = 0x250432,
		.pme_short_desc = "L2 data store requests",
		.pme_long_desc = "L2 data store requests",
	},
	[ POWER6_PME_PM_INST_TABLEWALK_COUNT ] = {
		.pme_name = "PM_INST_TABLEWALK_COUNT",
		.pme_code = 0x920cb,
		.pme_short_desc = "Periods doing instruction tablewalks",
		.pme_long_desc = "Periods doing instruction tablewalks",
	},
	[ POWER6_PME_PM_PTEG_FROM_L35_SHR ] = {
		.pme_name = "PM_PTEG_FROM_L35_SHR",
		.pme_code = 0x21304e,
		.pme_short_desc = "PTEG loaded from L3.5 shared",
		.pme_long_desc = "PTEG loaded from L3.5 shared",
	},
	[ POWER6_PME_PM_DPU_HELD_ISYNC ] = {
		.pme_name = "PM_DPU_HELD_ISYNC",
		.pme_code = 0x2008a,
		.pme_short_desc = "DISP unit held due to ISYNC ",
		.pme_long_desc = "DISP unit held due to ISYNC ",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L25_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L25_SHR",
		.pme_code = 0x40304e,
		.pme_short_desc = "Marked data loaded from L2.5 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T or SL) data from the L2 of a chip on this MCM due to a marked demand load",
	},
	[ POWER6_PME_PM_L3SA_HIT ] = {
		.pme_name = "PM_L3SA_HIT",
		.pme_code = 0x50082,
		.pme_short_desc = "L3 slice A hits",
		.pme_long_desc = "L3 slice A hits",
	},
	[ POWER6_PME_PM_DERAT_MISS_16G ] = {
		.pme_name = "PM_DERAT_MISS_16G",
		.pme_code = 0x492070,
		.pme_short_desc = "DERAT misses for 16G page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 16G page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_DATA_PTEG_2ND_HALF ] = {
		.pme_name = "PM_DATA_PTEG_2ND_HALF",
		.pme_code = 0x910a2,
		.pme_short_desc = "Data table walk matched in second half pri­mary PTEG",
		.pme_long_desc = "Data table walk matched in second half pri­mary PTEG",
	},
	[ POWER6_PME_PM_L2SA_ST_REQ ] = {
		.pme_name = "PM_L2SA_ST_REQ",
		.pme_code = 0x50484,
		.pme_short_desc = "L2 slice A store requests",
		.pme_long_desc = "A store request as seen at the L2 directory has been made from the core. Stores are counted after gathering in the L2 store queues. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER6_PME_PM_INST_FROM_LMEM ] = {
		.pme_name = "PM_INST_FROM_LMEM",
		.pme_code = 0x442042,
		.pme_short_desc = "Instruction fetched from local memory",
		.pme_long_desc = "Instruction fetched from local memory",
	},
	[ POWER6_PME_PM_IC_DEMAND_L2_BR_REDIRECT ] = {
		.pme_name = "PM_IC_DEMAND_L2_BR_REDIRECT",
		.pme_code = 0x420cc,
		.pme_short_desc = "L2 I cache demand request due to branch redirect",
		.pme_long_desc = "L2 I cache demand request due to branch redirect",
	},
	[ POWER6_PME_PM_PTEG_FROM_L2 ] = {
		.pme_name = "PM_PTEG_FROM_L2",
		.pme_code = 0x113048,
		.pme_short_desc = "PTEG loaded from L2",
		.pme_long_desc = "PTEG loaded from L2",
	},
	[ POWER6_PME_PM_DATA_PTEG_1ST_HALF ] = {
		.pme_name = "PM_DATA_PTEG_1ST_HALF",
		.pme_code = 0x910a0,
		.pme_short_desc = "Data table walk matched in first half primary PTEG",
		.pme_long_desc = "Data table walk matched in first half primary PTEG",
	},
	[ POWER6_PME_PM_BR_MPRED_COUNT ] = {
		.pme_name = "PM_BR_MPRED_COUNT",
		.pme_code = 0x410aa,
		.pme_short_desc = "Branch misprediction due to count prediction",
		.pme_long_desc = "Branch misprediction due to count prediction",
	},
	[ POWER6_PME_PM_IERAT_MISS_4K ] = {
		.pme_name = "PM_IERAT_MISS_4K",
		.pme_code = 0x492076,
		.pme_short_desc = "IERAT misses for 4K page",
		.pme_long_desc = "IERAT misses for 4K page",
	},
	[ POWER6_PME_PM_THRD_BOTH_RUN_COUNT ] = {
		.pme_name = "PM_THRD_BOTH_RUN_COUNT",
		.pme_code = 0x400005,
		.pme_short_desc = "Periods both threads in run cycles",
		.pme_long_desc = "Periods both threads in run cycles",
	},
	[ POWER6_PME_PM_LSU_REJECT_ULD ] = {
		.pme_name = "PM_LSU_REJECT_ULD",
		.pme_code = 0x190030,
		.pme_short_desc = "Unaligned load reject",
		.pme_long_desc = "Unaligned load reject",
	},
	[ POWER6_PME_PM_DATA_FROM_DL2L3_MOD_CYC ] = {
		.pme_name = "PM_DATA_FROM_DL2L3_MOD_CYC",
		.pme_code = 0x40002a,
		.pme_short_desc = "Load latency from distant L2 or L3 modified",
		.pme_long_desc = "Load latency from distant L2 or L3 modified",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_RL2L3_MOD",
		.pme_code = 0x112044,
		.pme_short_desc = "Marked PTEG loaded from remote L2 or L3 modified",
		.pme_long_desc = "Marked PTEG loaded from remote L2 or L3 modified",
	},
	[ POWER6_PME_PM_FPU0_FLOP ] = {
		.pme_name = "PM_FPU0_FLOP",
		.pme_code = 0xc0086,
		.pme_short_desc = "FPU0 executed 1FLOP, FMA, FSQRT or FDIV instruction",
		.pme_long_desc = "FPU0 executed 1FLOP, FMA, FSQRT or FDIV instruction",
	},
	[ POWER6_PME_PM_FPU0_FEST ] = {
		.pme_name = "PM_FPU0_FEST",
		.pme_code = 0xd10a6,
		.pme_short_desc = "FPU0 executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. ",
	},
	[ POWER6_PME_PM_MRK_LSU0_REJECT_LHS ] = {
		.pme_name = "PM_MRK_LSU0_REJECT_LHS",
		.pme_code = 0x930e6,
		.pme_short_desc = "LSU0 marked load hit store reject",
		.pme_long_desc = "LSU0 marked load hit store reject",
	},
	[ POWER6_PME_PM_VMX_RESULT_SAT_1 ] = {
		.pme_name = "PM_VMX_RESULT_SAT_1",
		.pme_code = 0xb0086,
		.pme_short_desc = "VMX valid result with sat=1",
		.pme_long_desc = "VMX valid result with sat=1",
	},
	[ POWER6_PME_PM_NO_ITAG_CYC ] = {
		.pme_name = "PM_NO_ITAG_CYC",
		.pme_code = 0x40088,
		.pme_short_desc = "Cyles no ITAG available",
		.pme_long_desc = "Cyles no ITAG available",
	},
	[ POWER6_PME_PM_LSU1_REJECT_NO_SCRATCH ] = {
		.pme_name = "PM_LSU1_REJECT_NO_SCRATCH",
		.pme_code = 0xa10aa,
		.pme_short_desc = "LSU1 reject due to scratch register not available",
		.pme_long_desc = "LSU1 reject due to scratch register not available",
	},
	[ POWER6_PME_PM_0INST_FETCH ] = {
		.pme_name = "PM_0INST_FETCH",
		.pme_code = 0x40080,
		.pme_short_desc = "No instructions fetched",
		.pme_long_desc = "No instructions were fetched this cycles (due to IFU hold, redirect, or icache miss)",
	},
	[ POWER6_PME_PM_DPU_WT_BR_MPRED ] = {
		.pme_name = "PM_DPU_WT_BR_MPRED",
		.pme_code = 0x40000c,
		.pme_short_desc = "Cycles DISP unit is stalled due to branch misprediction",
		.pme_long_desc = "Cycles DISP unit is stalled due to branch misprediction",
	},
	[ POWER6_PME_PM_L1_PREF ] = {
		.pme_name = "PM_L1_PREF",
		.pme_code = 0x810a4,
		.pme_short_desc = "L1 cache data prefetches",
		.pme_long_desc = "A request to prefetch data into the L1 was made",
	},
	[ POWER6_PME_PM_VMX_FLOAT_MULTICYCLE ] = {
		.pme_name = "PM_VMX_FLOAT_MULTICYCLE",
		.pme_code = 0xb0082,
		.pme_short_desc = "VMX multi-cycle floating point instruction issued",
		.pme_long_desc = "VMX multi-cycle floating point instruction issued",
	},
	[ POWER6_PME_PM_DATA_FROM_L25_SHR_CYC ] = {
		.pme_name = "PM_DATA_FROM_L25_SHR_CYC",
		.pme_code = 0x200024,
		.pme_short_desc = "Load latency from L2.5 shared",
		.pme_long_desc = "Load latency from L2.5 shared",
	},
	[ POWER6_PME_PM_DATA_FROM_L3 ] = {
		.pme_name = "PM_DATA_FROM_L3",
		.pme_code = 0x300058,
		.pme_short_desc = "Data loaded from L3",
		.pme_long_desc = "DL1 was reloaded from the local L3 due to a demand load",
	},
	[ POWER6_PME_PM_PMC2_OVERFLOW ] = {
		.pme_name = "PM_PMC2_OVERFLOW",
		.pme_code = 0x300014,
		.pme_short_desc = "PMC2 Overflow",
		.pme_long_desc = "PMC2 Overflow",
	},
	[ POWER6_PME_PM_VMX0_LD_WRBACK ] = {
		.pme_name = "PM_VMX0_LD_WRBACK",
		.pme_code = 0x60084,
		.pme_short_desc = "VMX0 load writeback valid",
		.pme_long_desc = "VMX0 load writeback valid",
	},
	[ POWER6_PME_PM_FPU0_DENORM ] = {
		.pme_name = "PM_FPU0_DENORM",
		.pme_code = 0xc10a2,
		.pme_short_desc = "FPU0 received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized.",
	},
	[ POWER6_PME_PM_INST_FETCH_CYC ] = {
		.pme_name = "PM_INST_FETCH_CYC",
		.pme_code = 0x420c8,
		.pme_short_desc = "Cycles at least 1 instruction fetched",
		.pme_long_desc = "Asserted each cycle when the IFU sends at least one instruction to the IDU. ",
	},
	[ POWER6_PME_PM_LSU_LDF ] = {
		.pme_name = "PM_LSU_LDF",
		.pme_code = 0x280032,
		.pme_short_desc = "LSU executed Floating Point load instruction",
		.pme_long_desc = "LSU executed Floating Point load instruction",
	},
	[ POWER6_PME_PM_LSU_REJECT_L2_CORR ] = {
		.pme_name = "PM_LSU_REJECT_L2_CORR",
		.pme_code = 0x1a1034,
		.pme_short_desc = "LSU reject due to L2 correctable error",
		.pme_long_desc = "LSU reject due to L2 correctable error",
	},
	[ POWER6_PME_PM_DERAT_REF_64K ] = {
		.pme_name = "PM_DERAT_REF_64K",
		.pme_code = 0x282070,
		.pme_short_desc = "DERAT reference for 64K page",
		.pme_long_desc = "DERAT reference for 64K page",
	},
	[ POWER6_PME_PM_THRD_PRIO_3_CYC ] = {
		.pme_name = "PM_THRD_PRIO_3_CYC",
		.pme_code = 0x422040,
		.pme_short_desc = "Cycles thread running at priority level 3",
		.pme_long_desc = "Cycles thread running at priority level 3",
	},
	[ POWER6_PME_PM_FPU_FMA ] = {
		.pme_name = "PM_FPU_FMA",
		.pme_code = 0x2c0030,
		.pme_short_desc = "FPU executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when FPU is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs. Combined Unit 0 + Unit 1",
	},
	[ POWER6_PME_PM_INST_FROM_L35_MOD ] = {
		.pme_name = "PM_INST_FROM_L35_MOD",
		.pme_code = 0x142046,
		.pme_short_desc = "Instruction fetched from L3.5 modified",
		.pme_long_desc = "Instruction fetched from L3.5 modified",
	},
	[ POWER6_PME_PM_DFU_CONV ] = {
		.pme_name = "PM_DFU_CONV",
		.pme_code = 0xe008e,
		.pme_short_desc = "DFU convert from fixed op",
		.pme_long_desc = "DFU convert from fixed op",
	},
	[ POWER6_PME_PM_INST_FROM_L25_MOD ] = {
		.pme_name = "PM_INST_FROM_L25_MOD",
		.pme_code = 0x342046,
		.pme_short_desc = "Instruction fetched from L2.5 modified",
		.pme_long_desc = "Instruction fetched from L2.5 modified",
	},
	[ POWER6_PME_PM_PTEG_FROM_L35_MOD ] = {
		.pme_name = "PM_PTEG_FROM_L35_MOD",
		.pme_code = 0x11304e,
		.pme_short_desc = "PTEG loaded from L3.5 modified",
		.pme_long_desc = "PTEG loaded from L3.5 modified",
	},
	[ POWER6_PME_PM_MRK_VMX_ST_ISSUED ] = {
		.pme_name = "PM_MRK_VMX_ST_ISSUED",
		.pme_code = 0xb0088,
		.pme_short_desc = "Marked VMX store issued",
		.pme_long_desc = "Marked VMX store issued",
	},
	[ POWER6_PME_PM_VMX_FLOAT_ISSUED ] = {
		.pme_name = "PM_VMX_FLOAT_ISSUED",
		.pme_code = 0x70080,
		.pme_short_desc = "VMX instruction issued to float",
		.pme_long_desc = "VMX instruction issued to float",
	},
	[ POWER6_PME_PM_LSU0_REJECT_L2_CORR ] = {
		.pme_name = "PM_LSU0_REJECT_L2_CORR",
		.pme_code = 0xa10a0,
		.pme_short_desc = "LSU0 reject due to L2 correctable error",
		.pme_long_desc = "LSU0 reject due to L2 correctable error",
	},
	[ POWER6_PME_PM_THRD_L2MISS ] = {
		.pme_name = "PM_THRD_L2MISS",
		.pme_code = 0x310a0,
		.pme_short_desc = "Thread in L2 miss",
		.pme_long_desc = "Thread in L2 miss",
	},
	[ POWER6_PME_PM_FPU_FCONV ] = {
		.pme_name = "PM_FPU_FCONV",
		.pme_code = 0x1d1034,
		.pme_short_desc = "FPU executed FCONV instruction",
		.pme_long_desc = "FPU executed FCONV instruction",
	},
	[ POWER6_PME_PM_FPU_FXMULT ] = {
		.pme_name = "PM_FPU_FXMULT",
		.pme_code = 0x1d0032,
		.pme_short_desc = "FPU executed fixed point multiplication",
		.pme_long_desc = "FPU executed fixed point multiplication",
	},
	[ POWER6_PME_PM_FPU1_FRSP ] = {
		.pme_name = "PM_FPU1_FRSP",
		.pme_code = 0xd10aa,
		.pme_short_desc = "FPU1 executed FRSP instruction",
		.pme_long_desc = "FPU1 executed FRSP instruction",
	},
	[ POWER6_PME_PM_MRK_DERAT_REF_16M ] = {
		.pme_name = "PM_MRK_DERAT_REF_16M",
		.pme_code = 0x382044,
		.pme_short_desc = "Marked DERAT reference for 16M page",
		.pme_long_desc = "Marked DERAT reference for 16M page",
	},
	[ POWER6_PME_PM_L2SB_CASTOUT_SHR ] = {
		.pme_name = "PM_L2SB_CASTOUT_SHR",
		.pme_code = 0x5068a,
		.pme_short_desc = "L2 slice B castouts - Shared",
		.pme_long_desc = "L2 slice B castouts - Shared",
	},
	[ POWER6_PME_PM_THRD_ONE_RUN_COUNT ] = {
		.pme_name = "PM_THRD_ONE_RUN_COUNT",
		.pme_code = 0x1000fb,
		.pme_short_desc = "Periods one of the threads in run cycles",
		.pme_long_desc = "Periods one of the threads in run cycles",
	},
	[ POWER6_PME_PM_INST_FROM_RMEM ] = {
		.pme_name = "PM_INST_FROM_RMEM",
		.pme_code = 0x342042,
		.pme_short_desc = "Instruction fetched from remote memory",
		.pme_long_desc = "Instruction fetched from remote memory",
	},
	[ POWER6_PME_PM_LSU_BOTH_BUS ] = {
		.pme_name = "PM_LSU_BOTH_BUS",
		.pme_code = 0x810aa,
		.pme_short_desc = "Both data return buses busy simultaneously",
		.pme_long_desc = "Both data return buses busy simultaneously",
	},
	[ POWER6_PME_PM_FPU1_FSQRT_FDIV ] = {
		.pme_name = "PM_FPU1_FSQRT_FDIV",
		.pme_code = 0xc008c,
		.pme_short_desc = "FPU1 executed FSQRT or FDIV instruction",
		.pme_long_desc = "FPU1 executed FSQRT or FDIV instruction",
	},
	[ POWER6_PME_PM_L2_LD_REQ_INST ] = {
		.pme_name = "PM_L2_LD_REQ_INST",
		.pme_code = 0x150530,
		.pme_short_desc = "L2 instruction load requests",
		.pme_long_desc = "L2 instruction load requests",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L35_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L35_SHR",
		.pme_code = 0x212046,
		.pme_short_desc = "Marked PTEG loaded from L3.5 shared",
		.pme_long_desc = "Marked PTEG loaded from L3.5 shared",
	},
	[ POWER6_PME_PM_BR_PRED_CR ] = {
		.pme_name = "PM_BR_PRED_CR",
		.pme_code = 0x410a2,
		.pme_short_desc = "A conditional branch was predicted, CR prediction",
		.pme_long_desc = "A conditional branch was predicted, CR prediction",
	},
	[ POWER6_PME_PM_MRK_LSU0_REJECT_ULD ] = {
		.pme_name = "PM_MRK_LSU0_REJECT_ULD",
		.pme_code = 0x930e0,
		.pme_short_desc = "LSU0 marked unaligned load reject",
		.pme_long_desc = "LSU0 marked unaligned load reject",
	},
	[ POWER6_PME_PM_LSU_REJECT ] = {
		.pme_name = "PM_LSU_REJECT",
		.pme_code = 0x4a1030,
		.pme_short_desc = "LSU reject",
		.pme_long_desc = "LSU reject",
	},
	[ POWER6_PME_PM_LSU_REJECT_LHS_BOTH ] = {
		.pme_name = "PM_LSU_REJECT_LHS_BOTH",
		.pme_code = 0x290038,
		.pme_short_desc = "Load hit store reject both units",
		.pme_long_desc = "Load hit store reject both units",
	},
	[ POWER6_PME_PM_GXO_ADDR_CYC_BUSY ] = {
		.pme_name = "PM_GXO_ADDR_CYC_BUSY",
		.pme_code = 0x50382,
		.pme_short_desc = "Outbound GX address utilization (# of cycles address out is valid)",
		.pme_long_desc = "Outbound GX address utilization (# of cycles address out is valid)",
	},
	[ POWER6_PME_PM_LSU_SRQ_EMPTY_COUNT ] = {
		.pme_name = "PM_LSU_SRQ_EMPTY_COUNT",
		.pme_code = 0x40001d,
		.pme_short_desc = "Periods SRQ empty",
		.pme_long_desc = "The Store Request Queue is empty",
	},
	[ POWER6_PME_PM_PTEG_FROM_L3 ] = {
		.pme_name = "PM_PTEG_FROM_L3",
		.pme_code = 0x313048,
		.pme_short_desc = "PTEG loaded from L3",
		.pme_long_desc = "PTEG loaded from L3",
	},
	[ POWER6_PME_PM_VMX0_LD_ISSUED ] = {
		.pme_name = "PM_VMX0_LD_ISSUED",
		.pme_code = 0x60082,
		.pme_short_desc = "VMX0 load issued",
		.pme_long_desc = "VMX0 load issued",
	},
	[ POWER6_PME_PM_FXU_PIPELINED_MULT_DIV ] = {
		.pme_name = "PM_FXU_PIPELINED_MULT_DIV",
		.pme_code = 0x210ae,
		.pme_short_desc = "Fix point multiply/divide pipelined",
		.pme_long_desc = "Fix point multiply/divide pipelined",
	},
	[ POWER6_PME_PM_FPU1_STF ] = {
		.pme_name = "PM_FPU1_STF",
		.pme_code = 0xc10ac,
		.pme_short_desc = "FPU1 executed store instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing a store instruction.",
	},
	[ POWER6_PME_PM_DFU_ADD ] = {
		.pme_name = "PM_DFU_ADD",
		.pme_code = 0xe008c,
		.pme_short_desc = "DFU add type instruction",
		.pme_long_desc = "DFU add type instruction",
	},
	[ POWER6_PME_PM_MEM_DP_CL_WR_GLOB ] = {
		.pme_name = "PM_MEM_DP_CL_WR_GLOB",
		.pme_code = 0x250232,
		.pme_short_desc = "cache line write setting double pump state to global",
		.pme_long_desc = "cache line write setting double pump state to global",
	},
	[ POWER6_PME_PM_MRK_LSU1_REJECT_ULD ] = {
		.pme_name = "PM_MRK_LSU1_REJECT_ULD",
		.pme_code = 0x930e8,
		.pme_short_desc = "LSU1 marked unaligned load reject",
		.pme_long_desc = "LSU1 marked unaligned load reject",
	},
	[ POWER6_PME_PM_ITLB_REF ] = {
		.pme_name = "PM_ITLB_REF",
		.pme_code = 0x920c2,
		.pme_short_desc = "Instruction TLB reference",
		.pme_long_desc = "Instruction TLB reference",
	},
	[ POWER6_PME_PM_LSU0_REJECT_L2MISS ] = {
		.pme_name = "PM_LSU0_REJECT_L2MISS",
		.pme_code = 0x90084,
		.pme_short_desc = "LSU0 L2 miss reject",
		.pme_long_desc = "LSU0 L2 miss reject",
	},
	[ POWER6_PME_PM_DATA_FROM_L35_SHR ] = {
		.pme_name = "PM_DATA_FROM_L35_SHR",
		.pme_code = 0x20005a,
		.pme_short_desc = "Data loaded from L3.5 shared",
		.pme_long_desc = "Data loaded from L3.5 shared",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_RL2L3_MOD",
		.pme_code = 0x10304c,
		.pme_short_desc = "Marked data loaded from remote L2 or L3 modified",
		.pme_long_desc = "Marked data loaded from remote L2 or L3 modified",
	},
	[ POWER6_PME_PM_FPU0_FPSCR ] = {
		.pme_name = "PM_FPU0_FPSCR",
		.pme_code = 0xd0084,
		.pme_short_desc = "FPU0 executed FPSCR instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing fpscr move related instruction. This could be mtfsfi*, mtfsb0*, mtfsb1*. mffs*, mtfsf*, mcrsf* where XYZ* means XYZ, XYZs, XYZ., XYZs",
	},
	[ POWER6_PME_PM_DATA_FROM_L2 ] = {
		.pme_name = "PM_DATA_FROM_L2",
		.pme_code = 0x100058,
		.pme_short_desc = "Data loaded from L2",
		.pme_long_desc = "DL1 was reloaded from the local L2 due to a demand load",
	},
	[ POWER6_PME_PM_DPU_HELD_XER ] = {
		.pme_name = "PM_DPU_HELD_XER",
		.pme_code = 0x20088,
		.pme_short_desc = "DISP unit held due to XER dependency",
		.pme_long_desc = "DISP unit held due to XER dependency",
	},
	[ POWER6_PME_PM_FAB_NODE_PUMP ] = {
		.pme_name = "PM_FAB_NODE_PUMP",
		.pme_code = 0x50188,
		.pme_short_desc = "Node pump operation, locally mastered",
		.pme_long_desc = "Node pump operation, locally mastered",
	},
	[ POWER6_PME_PM_VMX_RESULT_SAT_0_1 ] = {
		.pme_name = "PM_VMX_RESULT_SAT_0_1",
		.pme_code = 0xb008e,
		.pme_short_desc = "VMX valid result with sat bit is set (0->1)",
		.pme_long_desc = "VMX valid result with sat bit is set (0->1)",
	},
	[ POWER6_PME_PM_LD_REF_L1 ] = {
		.pme_name = "PM_LD_REF_L1",
		.pme_code = 0x80082,
		.pme_short_desc = "L1 D cache load references",
		.pme_long_desc = "Total DL1 Load references",
	},
	[ POWER6_PME_PM_TLB_REF ] = {
		.pme_name = "PM_TLB_REF",
		.pme_code = 0x920c8,
		.pme_short_desc = "TLB reference",
		.pme_long_desc = "TLB reference",
	},
	[ POWER6_PME_PM_DC_PREF_OUT_OF_STREAMS ] = {
		.pme_name = "PM_DC_PREF_OUT_OF_STREAMS",
		.pme_code = 0x810a0,
		.pme_short_desc = "D cache out of streams",
		.pme_long_desc = "out of streams",
	},
	[ POWER6_PME_PM_FLUSH_FPU ] = {
		.pme_name = "PM_FLUSH_FPU",
		.pme_code = 0x230ec,
		.pme_short_desc = "Flush caused by FPU exception",
		.pme_long_desc = "Flush caused by FPU exception",
	},
	[ POWER6_PME_PM_MEM1_DP_CL_WR_LOC ] = {
		.pme_name = "PM_MEM1_DP_CL_WR_LOC",
		.pme_code = 0x5028e,
		.pme_short_desc = "cacheline write setting dp to local side 1",
		.pme_long_desc = "cacheline write setting dp to local side 1",
	},
	[ POWER6_PME_PM_L2SB_LD_HIT ] = {
		.pme_name = "PM_L2SB_LD_HIT",
		.pme_code = 0x5078a,
		.pme_short_desc = "L2 slice B load hits",
		.pme_long_desc = "L2 slice B load hits",
	},
	[ POWER6_PME_PM_FAB_DCLAIM ] = {
		.pme_name = "PM_FAB_DCLAIM",
		.pme_code = 0x50184,
		.pme_short_desc = "Dclaim operation, locally mastered",
		.pme_long_desc = "Dclaim operation, locally mastered",
	},
	[ POWER6_PME_PM_MEM_DP_CL_WR_LOC ] = {
		.pme_name = "PM_MEM_DP_CL_WR_LOC",
		.pme_code = 0x150232,
		.pme_short_desc = "cache line write setting double pump state to local",
		.pme_long_desc = "cache line write setting double pump state to local",
	},
	[ POWER6_PME_PM_BR_MPRED_CR ] = {
		.pme_name = "PM_BR_MPRED_CR",
		.pme_code = 0x410a8,
		.pme_short_desc = "Branch mispredictions due to CR bit setting",
		.pme_long_desc = "This signal is asserted when the branch execution unit detects a branch mispredict because the CR value is opposite of the predicted value. This signal is asserted after a branch issue event and will result in a branch redirect flush if not overridden by a flush of an older instruction.",
	},
	[ POWER6_PME_PM_LSU_REJECT_EXTERN ] = {
		.pme_name = "PM_LSU_REJECT_EXTERN",
		.pme_code = 0x3a1030,
		.pme_short_desc = "LSU external reject request ",
		.pme_long_desc = "LSU external reject request ",
	},
	[ POWER6_PME_PM_DATA_FROM_RL2L3_MOD ] = {
		.pme_name = "PM_DATA_FROM_RL2L3_MOD",
		.pme_code = 0x10005c,
		.pme_short_desc = "Data loaded from remote L2 or L3 modified",
		.pme_long_desc = "Data loaded from remote L2 or L3 modified",
	},
	[ POWER6_PME_PM_DPU_HELD_RU_WQ ] = {
		.pme_name = "PM_DPU_HELD_RU_WQ",
		.pme_code = 0x2008e,
		.pme_short_desc = "DISP unit held due to RU FXU write queue full",
		.pme_long_desc = "DISP unit held due to RU FXU write queue full",
	},
	[ POWER6_PME_PM_LD_MISS_L1 ] = {
		.pme_name = "PM_LD_MISS_L1",
		.pme_code = 0x80080,
		.pme_short_desc = "L1 D cache load misses",
		.pme_long_desc = "Total DL1 Load references that miss the DL1",
	},
	[ POWER6_PME_PM_DC_INV_L2 ] = {
		.pme_name = "PM_DC_INV_L2",
		.pme_code = 0x150632,
		.pme_short_desc = "L1 D cache entries invalidated from L2",
		.pme_long_desc = "A dcache invalidated was received from the L2 because a line in L2 was castout.",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_RMEM ] = {
		.pme_name = "PM_MRK_PTEG_FROM_RMEM",
		.pme_code = 0x312042,
		.pme_short_desc = "Marked PTEG loaded from remote memory",
		.pme_long_desc = "Marked PTEG loaded from remote memory",
	},
	[ POWER6_PME_PM_FPU_FIN ] = {
		.pme_name = "PM_FPU_FIN",
		.pme_code = 0x1d0030,
		.pme_short_desc = "FPU produced a result",
		.pme_long_desc = "FPU finished, produced a result This only indicates finish, not completion. Combined Unit 0 + Unit 1",
	},
	[ POWER6_PME_PM_FXU0_FIN ] = {
		.pme_name = "PM_FXU0_FIN",
		.pme_code = 0x300016,
		.pme_short_desc = "FXU0 produced a result",
		.pme_long_desc = "The Fixed Point unit 0 finished an instruction and produced a result",
	},
	[ POWER6_PME_PM_DPU_HELD_FPQ ] = {
		.pme_name = "PM_DPU_HELD_FPQ",
		.pme_code = 0x20086,
		.pme_short_desc = "DISP unit held due to FPU issue queue full",
		.pme_long_desc = "DISP unit held due to FPU issue queue full",
	},
	[ POWER6_PME_PM_GX_DMA_READ ] = {
		.pme_name = "PM_GX_DMA_READ",
		.pme_code = 0x5038c,
		.pme_short_desc = "DMA Read Request",
		.pme_long_desc = "DMA Read Request",
	},
	[ POWER6_PME_PM_LSU1_REJECT_PARTIAL_SECTOR ] = {
		.pme_name = "PM_LSU1_REJECT_PARTIAL_SECTOR",
		.pme_code = 0xa008e,
		.pme_short_desc = "LSU1 reject due to partial sector valid",
		.pme_long_desc = "LSU1 reject due to partial sector valid",
	},
	[ POWER6_PME_PM_0INST_FETCH_COUNT ] = {
		.pme_name = "PM_0INST_FETCH_COUNT",
		.pme_code = 0x40081,
		.pme_short_desc = "Periods with no instructions fetched",
		.pme_long_desc = "No instructions were fetched this periods (due to IFU hold, redirect, or icache miss)",
	},
	[ POWER6_PME_PM_PMC5_OVERFLOW ] = {
		.pme_name = "PM_PMC5_OVERFLOW",
		.pme_code = 0x100024,
		.pme_short_desc = "PMC5 Overflow",
		.pme_long_desc = "PMC5 Overflow",
	},
	[ POWER6_PME_PM_L2SB_LD_REQ ] = {
		.pme_name = "PM_L2SB_LD_REQ",
		.pme_code = 0x50788,
		.pme_short_desc = "L2 slice B load requests ",
		.pme_long_desc = "L2 slice B load requests ",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_0_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_0_CYC",
		.pme_code = 0x123040,
		.pme_short_desc = "Cycles no thread priority difference",
		.pme_long_desc = "Cycles no thread priority difference",
	},
	[ POWER6_PME_PM_DATA_FROM_RMEM ] = {
		.pme_name = "PM_DATA_FROM_RMEM",
		.pme_code = 0x30005e,
		.pme_short_desc = "Data loaded from remote memory",
		.pme_long_desc = "Data loaded from remote memory",
	},
	[ POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_BOTH_CYC ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_BOTH_CYC",
		.pme_code = 0x30001c,
		.pme_short_desc = "Cycles both threads LMQ and SRQ empty",
		.pme_long_desc = "Cycles both threads LMQ and SRQ empty",
	},
	[ POWER6_PME_PM_ST_REF_L1_BOTH ] = {
		.pme_name = "PM_ST_REF_L1_BOTH",
		.pme_code = 0x280038,
		.pme_short_desc = "Both units L1 D cache store reference",
		.pme_long_desc = "Both units L1 D cache store reference",
	},
	[ POWER6_PME_PM_VMX_PERMUTE_ISSUED ] = {
		.pme_name = "PM_VMX_PERMUTE_ISSUED",
		.pme_code = 0x70086,
		.pme_short_desc = "VMX instruction issued to permute",
		.pme_long_desc = "VMX instruction issued to permute",
	},
	[ POWER6_PME_PM_BR_TAKEN ] = {
		.pme_name = "PM_BR_TAKEN",
		.pme_code = 0x200052,
		.pme_short_desc = "Branches taken",
		.pme_long_desc = "Branches taken",
	},
	[ POWER6_PME_PM_FAB_DMA ] = {
		.pme_name = "PM_FAB_DMA",
		.pme_code = 0x5018c,
		.pme_short_desc = "DMA operation, locally mastered",
		.pme_long_desc = "DMA operation, locally mastered",
	},
	[ POWER6_PME_PM_GCT_EMPTY_COUNT ] = {
		.pme_name = "PM_GCT_EMPTY_COUNT",
		.pme_code = 0x200009,
		.pme_short_desc = "Periods GCT empty",
		.pme_long_desc = "The Global Completion Table is completely empty.",
	},
	[ POWER6_PME_PM_FPU1_SINGLE ] = {
		.pme_name = "PM_FPU1_SINGLE",
		.pme_code = 0xc10ae,
		.pme_short_desc = "FPU1 executed single precision instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing single precision instruction.",
	},
	[ POWER6_PME_PM_L2SA_CASTOUT_SHR ] = {
		.pme_name = "PM_L2SA_CASTOUT_SHR",
		.pme_code = 0x50682,
		.pme_short_desc = "L2 slice A castouts - Shared",
		.pme_long_desc = "L2 slice A castouts - Shared",
	},
	[ POWER6_PME_PM_L3SB_REF ] = {
		.pme_name = "PM_L3SB_REF",
		.pme_code = 0x50088,
		.pme_short_desc = "L3 slice B references",
		.pme_long_desc = "L3 slice B references",
	},
	[ POWER6_PME_PM_FPU0_FRSP ] = {
		.pme_name = "PM_FPU0_FRSP",
		.pme_code = 0xd10a2,
		.pme_short_desc = "FPU0 executed FRSP instruction",
		.pme_long_desc = "FPU0 executed FRSP instruction",
	},
	[ POWER6_PME_PM_PMC4_SAVED ] = {
		.pme_name = "PM_PMC4_SAVED",
		.pme_code = 0x300022,
		.pme_short_desc = "PMC4 rewind value saved",
		.pme_long_desc = "PMC4 rewind value saved",
	},
	[ POWER6_PME_PM_L2SA_DC_INV ] = {
		.pme_name = "PM_L2SA_DC_INV",
		.pme_code = 0x50686,
		.pme_short_desc = "L2 slice A D cache invalidate",
		.pme_long_desc = "L2 slice A D cache invalidate",
	},
	[ POWER6_PME_PM_GXI_ADDR_CYC_BUSY ] = {
		.pme_name = "PM_GXI_ADDR_CYC_BUSY",
		.pme_code = 0x50388,
		.pme_short_desc = "Inbound GX address utilization (# of cycle address is in valid)",
		.pme_long_desc = "Inbound GX address utilization (# of cycle address is in valid)",
	},
	[ POWER6_PME_PM_FPU0_FMA ] = {
		.pme_name = "PM_FPU0_FMA",
		.pme_code = 0xc0082,
		.pme_short_desc = "FPU0 executed multiply-add instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing multiply-add kind of instruction. This could be fmadd*, fnmadd*, fmsub*, fnmsub* where XYZ* means XYZ, XYZs, XYZ., XYZs.",
	},
	[ POWER6_PME_PM_SLB_MISS ] = {
		.pme_name = "PM_SLB_MISS",
		.pme_code = 0x183034,
		.pme_short_desc = "SLB misses",
		.pme_long_desc = "SLB misses",
	},
	[ POWER6_PME_PM_MRK_ST_GPS ] = {
		.pme_name = "PM_MRK_ST_GPS",
		.pme_code = 0x200006,
		.pme_short_desc = "Marked store sent to GPS",
		.pme_long_desc = "A sampled store has been sent to the memory subsystem",
	},
	[ POWER6_PME_PM_DERAT_REF_4K ] = {
		.pme_name = "PM_DERAT_REF_4K",
		.pme_code = 0x182070,
		.pme_short_desc = "DERAT reference for 4K page",
		.pme_long_desc = "DERAT reference for 4K page",
	},
	[ POWER6_PME_PM_L2_CASTOUT_SHR ] = {
		.pme_name = "PM_L2_CASTOUT_SHR",
		.pme_code = 0x250630,
		.pme_short_desc = "L2 castouts - Shared (T, Te, Si, S)",
		.pme_long_desc = "L2 castouts - Shared (T, Te, Si, S)",
	},
	[ POWER6_PME_PM_DPU_HELD_STCX_CR ] = {
		.pme_name = "PM_DPU_HELD_STCX_CR",
		.pme_code = 0x2008c,
		.pme_short_desc = "DISP unit held due to STCX updating CR ",
		.pme_long_desc = "DISP unit held due to STCX updating CR ",
	},
	[ POWER6_PME_PM_FPU0_ST_FOLDED ] = {
		.pme_name = "PM_FPU0_ST_FOLDED",
		.pme_code = 0xd10a4,
		.pme_short_desc = "FPU0 folded store",
		.pme_long_desc = "FPU0 folded store",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L21 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L21",
		.pme_code = 0x203048,
		.pme_short_desc = "Marked data loaded from private L2 other core",
		.pme_long_desc = "Marked data loaded from private L2 other core",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_minus3or4_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_minus3or4_CYC",
		.pme_code = 0x323046,
		.pme_short_desc = "Cycles thread priority difference is -3 or -4",
		.pme_long_desc = "Cycles thread priority difference is -3 or -4",
	},
	[ POWER6_PME_PM_DATA_FROM_L35_MOD ] = {
		.pme_name = "PM_DATA_FROM_L35_MOD",
		.pme_code = 0x10005a,
		.pme_short_desc = "Data loaded from L3.5 modified",
		.pme_long_desc = "Data loaded from L3.5 modified",
	},
	[ POWER6_PME_PM_DATA_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_DATA_FROM_DL2L3_SHR",
		.pme_code = 0x30005c,
		.pme_short_desc = "Data loaded from distant L2 or L3 shared",
		.pme_long_desc = "Data loaded from distant L2 or L3 shared",
	},
	[ POWER6_PME_PM_GXI_DATA_CYC_BUSY ] = {
		.pme_name = "PM_GXI_DATA_CYC_BUSY",
		.pme_code = 0x5038a,
		.pme_short_desc = "Inbound GX Data utilization (# of cycle data in is valid)",
		.pme_long_desc = "Inbound GX Data utilization (# of cycle data in is valid)",
	},
	[ POWER6_PME_PM_LSU_REJECT_STEAL ] = {
		.pme_name = "PM_LSU_REJECT_STEAL",
		.pme_code = 0x9008c,
		.pme_short_desc = "LSU reject due to steal",
		.pme_long_desc = "LSU reject due to steal",
	},
	[ POWER6_PME_PM_ST_FIN ] = {
		.pme_name = "PM_ST_FIN",
		.pme_code = 0x100054,
		.pme_short_desc = "Store instructions finished",
		.pme_long_desc = "Store instructions finished",
	},
	[ POWER6_PME_PM_DPU_HELD_CR_LOGICAL ] = {
		.pme_name = "PM_DPU_HELD_CR_LOGICAL",
		.pme_code = 0x3008e,
		.pme_short_desc = "DISP unit held due to CR, LR or CTR updated by CR logical, MTCRF, MTLR or MTCTR",
		.pme_long_desc = "DISP unit held due to CR, LR or CTR updated by CR logical, MTCRF, MTLR or MTCTR",
	},
	[ POWER6_PME_PM_THRD_SEL_T0 ] = {
		.pme_name = "PM_THRD_SEL_T0",
		.pme_code = 0x310a6,
		.pme_short_desc = "Decode selected thread 0",
		.pme_long_desc = "Decode selected thread 0",
	},
	[ POWER6_PME_PM_PTEG_RELOAD_VALID ] = {
		.pme_name = "PM_PTEG_RELOAD_VALID",
		.pme_code = 0x130e8,
		.pme_short_desc = "TLB reload valid",
		.pme_long_desc = "TLB reload valid",
	},
	[ POWER6_PME_PM_L2_PREF_ST ] = {
		.pme_name = "PM_L2_PREF_ST",
		.pme_code = 0x810a8,
		.pme_short_desc = "L2 cache prefetches",
		.pme_long_desc = "L2 cache prefetches",
	},
	[ POWER6_PME_PM_MRK_STCX_FAIL ] = {
		.pme_name = "PM_MRK_STCX_FAIL",
		.pme_code = 0x830e4,
		.pme_short_desc = "Marked STCX failed",
		.pme_long_desc = "A marked stcx (stwcx or stdcx) failed",
	},
	[ POWER6_PME_PM_LSU0_REJECT_LHS ] = {
		.pme_name = "PM_LSU0_REJECT_LHS",
		.pme_code = 0x90086,
		.pme_short_desc = "LSU0 load hit store reject",
		.pme_long_desc = "LSU0 load hit store reject",
	},
	[ POWER6_PME_PM_DFU_EXP_EQ ] = {
		.pme_name = "PM_DFU_EXP_EQ",
		.pme_code = 0xe0084,
		.pme_short_desc = "DFU operand exponents are equal for add type",
		.pme_long_desc = "DFU operand exponents are equal for add type",
	},
	[ POWER6_PME_PM_DPU_HELD_FP_FX_MULT ] = {
		.pme_name = "PM_DPU_HELD_FP_FX_MULT",
		.pme_code = 0x210a8,
		.pme_short_desc = "DISP unit held due to non fixed multiple/divide after fixed multiply/divide",
		.pme_long_desc = "DISP unit held due to non fixed multiple/divide after fixed multiply/divide",
	},
	[ POWER6_PME_PM_L2_LD_MISS_DATA ] = {
		.pme_name = "PM_L2_LD_MISS_DATA",
		.pme_code = 0x250430,
		.pme_short_desc = "L2 data load misses",
		.pme_long_desc = "L2 data load misses",
	},
	[ POWER6_PME_PM_DATA_FROM_L35_MOD_CYC ] = {
		.pme_name = "PM_DATA_FROM_L35_MOD_CYC",
		.pme_code = 0x400026,
		.pme_short_desc = "Load latency from L3.5 modified",
		.pme_long_desc = "Load latency from L3.5 modified",
	},
	[ POWER6_PME_PM_FLUSH_FXU ] = {
		.pme_name = "PM_FLUSH_FXU",
		.pme_code = 0x230ea,
		.pme_short_desc = "Flush caused by FXU exception",
		.pme_long_desc = "Flush caused by FXU exception",
	},
	[ POWER6_PME_PM_FPU_ISSUE_1 ] = {
		.pme_name = "PM_FPU_ISSUE_1",
		.pme_code = 0x320c8,
		.pme_short_desc = "FPU issue 1 per cycle",
		.pme_long_desc = "FPU issue 1 per cycle",
	},
	[ POWER6_PME_PM_DATA_FROM_LMEM_CYC ] = {
		.pme_name = "PM_DATA_FROM_LMEM_CYC",
		.pme_code = 0x20002c,
		.pme_short_desc = "Load latency from local memory",
		.pme_long_desc = "Load latency from local memory",
	},
	[ POWER6_PME_PM_DPU_HELD_LSU_SOPS ] = {
		.pme_name = "PM_DPU_HELD_LSU_SOPS",
		.pme_code = 0x30080,
		.pme_short_desc = "DISP unit held due to LSU slow ops (sync, tlbie, stcx)",
		.pme_long_desc = "DISP unit held due to LSU slow ops (sync, tlbie, stcx)",
	},
	[ POWER6_PME_PM_INST_PTEG_2ND_HALF ] = {
		.pme_name = "PM_INST_PTEG_2ND_HALF",
		.pme_code = 0x910aa,
		.pme_short_desc = "Instruction table walk matched in second half primary PTEG",
		.pme_long_desc = "Instruction table walk matched in second half primary PTEG",
	},
	[ POWER6_PME_PM_THRESH_TIMEO ] = {
		.pme_name = "PM_THRESH_TIMEO",
		.pme_code = 0x300018,
		.pme_short_desc = "Threshold timeout",
		.pme_long_desc = "The threshold timer expired",
	},
	[ POWER6_PME_PM_LSU_REJECT_UST_BOTH ] = {
		.pme_name = "PM_LSU_REJECT_UST_BOTH",
		.pme_code = 0x190036,
		.pme_short_desc = "Unaligned store reject both units",
		.pme_long_desc = "Unaligned store reject both units",
	},
	[ POWER6_PME_PM_LSU_REJECT_FAST ] = {
		.pme_name = "PM_LSU_REJECT_FAST",
		.pme_code = 0x30003e,
		.pme_short_desc = "LSU fast reject",
		.pme_long_desc = "LSU fast reject",
	},
	[ POWER6_PME_PM_DPU_HELD_THRD_PRIO ] = {
		.pme_name = "PM_DPU_HELD_THRD_PRIO",
		.pme_code = 0x3008a,
		.pme_short_desc = "DISP unit held due to lower priority thread",
		.pme_long_desc = "DISP unit held due to lower priority thread",
	},
	[ POWER6_PME_PM_L2_PREF_LD ] = {
		.pme_name = "PM_L2_PREF_LD",
		.pme_code = 0x810a6,
		.pme_short_desc = "L2 cache prefetches",
		.pme_long_desc = "L2 cache prefetches",
	},
	[ POWER6_PME_PM_FPU_FEST ] = {
		.pme_name = "PM_FPU_FEST",
		.pme_code = 0x4d1030,
		.pme_short_desc = "FPU executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. Combined Unit 0 + Unit 1.",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_RMEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_RMEM",
		.pme_code = 0x30304a,
		.pme_short_desc = "Marked data loaded from remote memory",
		.pme_long_desc = "Marked data loaded from remote memory",
	},
	[ POWER6_PME_PM_LD_MISS_L1_CYC ] = {
		.pme_name = "PM_LD_MISS_L1_CYC",
		.pme_code = 0x10000c,
		.pme_short_desc = "L1 data load miss cycles",
		.pme_long_desc = "L1 data load miss cycles",
	},
	[ POWER6_PME_PM_DERAT_MISS_4K ] = {
		.pme_name = "PM_DERAT_MISS_4K",
		.pme_code = 0x192070,
		.pme_short_desc = "DERAT misses for 4K page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 4K page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_DPU_HELD_COMPLETION ] = {
		.pme_name = "PM_DPU_HELD_COMPLETION",
		.pme_code = 0x210ac,
		.pme_short_desc = "DISP unit held due to completion holding dispatch ",
		.pme_long_desc = "DISP unit held due to completion holding dispatch ",
	},
	[ POWER6_PME_PM_FPU_ISSUE_STALL_ST ] = {
		.pme_name = "PM_FPU_ISSUE_STALL_ST",
		.pme_code = 0x320ce,
		.pme_short_desc = "FPU issue stalled due to store",
		.pme_long_desc = "FPU issue stalled due to store",
	},
	[ POWER6_PME_PM_L2SB_DC_INV ] = {
		.pme_name = "PM_L2SB_DC_INV",
		.pme_code = 0x5068e,
		.pme_short_desc = "L2 slice B D cache invalidate",
		.pme_long_desc = "L2 slice B D cache invalidate",
	},
	[ POWER6_PME_PM_PTEG_FROM_L25_SHR ] = {
		.pme_name = "PM_PTEG_FROM_L25_SHR",
		.pme_code = 0x41304e,
		.pme_short_desc = "PTEG loaded from L2.5 shared",
		.pme_long_desc = "PTEG loaded from L2.5 shared",
	},
	[ POWER6_PME_PM_PTEG_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_PTEG_FROM_DL2L3_MOD",
		.pme_code = 0x41304c,
		.pme_short_desc = "PTEG loaded from distant L2 or L3 modified",
		.pme_long_desc = "PTEG loaded from distant L2 or L3 modified",
	},
	[ POWER6_PME_PM_FAB_CMD_RETRIED ] = {
		.pme_name = "PM_FAB_CMD_RETRIED",
		.pme_code = 0x250130,
		.pme_short_desc = "Fabric command retried",
		.pme_long_desc = "Fabric command retried",
	},
	[ POWER6_PME_PM_BR_PRED_LSTACK ] = {
		.pme_name = "PM_BR_PRED_LSTACK",
		.pme_code = 0x410a6,
		.pme_short_desc = "A conditional branch was predicted, link stack",
		.pme_long_desc = "A conditional branch was predicted, link stack",
	},
	[ POWER6_PME_PM_GXO_DATA_CYC_BUSY ] = {
		.pme_name = "PM_GXO_DATA_CYC_BUSY",
		.pme_code = 0x50384,
		.pme_short_desc = "Outbound GX Data utilization (# of cycles data out is valid)",
		.pme_long_desc = "Outbound GX Data utilization (# of cycles data out is valid)",
	},
	[ POWER6_PME_PM_DFU_SUBNORM ] = {
		.pme_name = "PM_DFU_SUBNORM",
		.pme_code = 0xe0086,
		.pme_short_desc = "DFU result is a subnormal",
		.pme_long_desc = "DFU result is a subnormal",
	},
	[ POWER6_PME_PM_FPU_ISSUE_OOO ] = {
		.pme_name = "PM_FPU_ISSUE_OOO",
		.pme_code = 0x320c0,
		.pme_short_desc = "FPU issue out-of-order",
		.pme_long_desc = "FPU issue out-of-order",
	},
	[ POWER6_PME_PM_LSU_REJECT_ULD_BOTH ] = {
		.pme_name = "PM_LSU_REJECT_ULD_BOTH",
		.pme_code = 0x290036,
		.pme_short_desc = "Unaligned load reject both units",
		.pme_long_desc = "Unaligned load reject both units",
	},
	[ POWER6_PME_PM_L2SB_ST_MISS ] = {
		.pme_name = "PM_L2SB_ST_MISS",
		.pme_code = 0x5048e,
		.pme_short_desc = "L2 slice B store misses",
		.pme_long_desc = "L2 slice B store misses",
	},
	[ POWER6_PME_PM_DATA_FROM_L25_MOD_CYC ] = {
		.pme_name = "PM_DATA_FROM_L25_MOD_CYC",
		.pme_code = 0x400024,
		.pme_short_desc = "Load latency from L2.5 modified",
		.pme_long_desc = "Load latency from L2.5 modified",
	},
	[ POWER6_PME_PM_INST_PTEG_1ST_HALF ] = {
		.pme_name = "PM_INST_PTEG_1ST_HALF",
		.pme_code = 0x910a8,
		.pme_short_desc = "Instruction table walk matched in first half primary PTEG",
		.pme_long_desc = "Instruction table walk matched in first half primary PTEG",
	},
	[ POWER6_PME_PM_DERAT_MISS_16M ] = {
		.pme_name = "PM_DERAT_MISS_16M",
		.pme_code = 0x392070,
		.pme_short_desc = "DERAT misses for 16M page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 16M page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_GX_DMA_WRITE ] = {
		.pme_name = "PM_GX_DMA_WRITE",
		.pme_code = 0x5038e,
		.pme_short_desc = "All DMA Write Requests (including dma wrt lgcy)",
		.pme_long_desc = "All DMA Write Requests (including dma wrt lgcy)",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_DL2L3_MOD",
		.pme_code = 0x412044,
		.pme_short_desc = "Marked PTEG loaded from distant L2 or L3 modified",
		.pme_long_desc = "Marked PTEG loaded from distant L2 or L3 modified",
	},
	[ POWER6_PME_PM_MEM1_DP_RQ_GLOB_LOC ] = {
		.pme_name = "PM_MEM1_DP_RQ_GLOB_LOC",
		.pme_code = 0x50288,
		.pme_short_desc = "Memory read queue marking cache line double pump state from global to local side 1",
		.pme_long_desc = "Memory read queue marking cache line double pump state from global to local side 1",
	},
	[ POWER6_PME_PM_L2SB_LD_REQ_DATA ] = {
		.pme_name = "PM_L2SB_LD_REQ_DATA",
		.pme_code = 0x50488,
		.pme_short_desc = "L2 slice B data load requests",
		.pme_long_desc = "L2 slice B data load requests",
	},
	[ POWER6_PME_PM_L2SA_LD_MISS_INST ] = {
		.pme_name = "PM_L2SA_LD_MISS_INST",
		.pme_code = 0x50582,
		.pme_short_desc = "L2 slice A instruction load misses",
		.pme_long_desc = "L2 slice A instruction load misses",
	},
	[ POWER6_PME_PM_MRK_LSU0_REJECT_L2MISS ] = {
		.pme_name = "PM_MRK_LSU0_REJECT_L2MISS",
		.pme_code = 0x930e4,
		.pme_short_desc = "LSU0 marked L2 miss reject",
		.pme_long_desc = "LSU0 marked L2 miss reject",
	},
	[ POWER6_PME_PM_MRK_IFU_FIN ] = {
		.pme_name = "PM_MRK_IFU_FIN",
		.pme_code = 0x20000a,
		.pme_short_desc = "Marked instruction IFU processing finished",
		.pme_long_desc = "Marked instruction IFU processing finished",
	},
	[ POWER6_PME_PM_INST_FROM_L3 ] = {
		.pme_name = "PM_INST_FROM_L3",
		.pme_code = 0x342040,
		.pme_short_desc = "Instruction fetched from L3",
		.pme_long_desc = "An instruction fetch group was fetched from L3. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER6_PME_PM_FXU1_FIN ] = {
		.pme_name = "PM_FXU1_FIN",
		.pme_code = 0x400016,
		.pme_short_desc = "FXU1 produced a result",
		.pme_long_desc = "The Fixed Point unit 1 finished an instruction and produced a result",
	},
	[ POWER6_PME_PM_THRD_PRIO_4_CYC ] = {
		.pme_name = "PM_THRD_PRIO_4_CYC",
		.pme_code = 0x422046,
		.pme_short_desc = "Cycles thread running at priority level 4",
		.pme_long_desc = "Cycles thread running at priority level 4",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L35_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L35_MOD",
		.pme_code = 0x10304e,
		.pme_short_desc = "Marked data loaded from L3.5 modified",
		.pme_long_desc = "Marked data loaded from L3.5 modified",
	},
	[ POWER6_PME_PM_LSU_REJECT_SET_MPRED ] = {
		.pme_name = "PM_LSU_REJECT_SET_MPRED",
		.pme_code = 0x2a0032,
		.pme_short_desc = "LSU reject due to mispredicted set",
		.pme_long_desc = "LSU reject due to mispredicted set",
	},
	[ POWER6_PME_PM_MRK_DERAT_MISS_16G ] = {
		.pme_name = "PM_MRK_DERAT_MISS_16G",
		.pme_code = 0x492044,
		.pme_short_desc = "Marked DERAT misses for 16G page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 16G page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_FPU0_FXDIV ] = {
		.pme_name = "PM_FPU0_FXDIV",
		.pme_code = 0xc10a0,
		.pme_short_desc = "FPU0 executed fixed point division",
		.pme_long_desc = "FPU0 executed fixed point division",
	},
	[ POWER6_PME_PM_MRK_LSU1_REJECT_UST ] = {
		.pme_name = "PM_MRK_LSU1_REJECT_UST",
		.pme_code = 0x930ea,
		.pme_short_desc = "LSU1 marked unaligned store reject",
		.pme_long_desc = "LSU1 marked unaligned store reject",
	},
	[ POWER6_PME_PM_FPU_ISSUE_DIV_SQRT_OVERLAP ] = {
		.pme_name = "PM_FPU_ISSUE_DIV_SQRT_OVERLAP",
		.pme_code = 0x320cc,
		.pme_short_desc = "FPU divide/sqrt overlapped with other divide/sqrt",
		.pme_long_desc = "FPU divide/sqrt overlapped with other divide/sqrt",
	},
	[ POWER6_PME_PM_INST_FROM_L35_SHR ] = {
		.pme_name = "PM_INST_FROM_L35_SHR",
		.pme_code = 0x242046,
		.pme_short_desc = "Instruction fetched from L3.5 shared",
		.pme_long_desc = "Instruction fetched from L3.5 shared",
	},
	[ POWER6_PME_PM_MRK_LSU_REJECT_LHS ] = {
		.pme_name = "PM_MRK_LSU_REJECT_LHS",
		.pme_code = 0x493030,
		.pme_short_desc = "Marked load hit store reject",
		.pme_long_desc = "Marked load hit store reject",
	},
	[ POWER6_PME_PM_LSU_LMQ_FULL_CYC ] = {
		.pme_name = "PM_LSU_LMQ_FULL_CYC",
		.pme_code = 0x810ac,
		.pme_short_desc = "Cycles LMQ full",
		.pme_long_desc = "The LMQ was full",
	},
	[ POWER6_PME_PM_SYNC_COUNT ] = {
		.pme_name = "PM_SYNC_COUNT",
		.pme_code = 0x920cd,
		.pme_short_desc = "SYNC instructions completed",
		.pme_long_desc = "SYNC instructions completed",
	},
	[ POWER6_PME_PM_MEM0_DP_RQ_LOC_GLOB ] = {
		.pme_name = "PM_MEM0_DP_RQ_LOC_GLOB",
		.pme_code = 0x50282,
		.pme_short_desc = "Memory read queue marking cache line double pump state from local to global side 0",
		.pme_long_desc = "Memory read queue marking cache line double pump state from local to global side 0",
	},
	[ POWER6_PME_PM_L2SA_CASTOUT_MOD ] = {
		.pme_name = "PM_L2SA_CASTOUT_MOD",
		.pme_code = 0x50680,
		.pme_short_desc = "L2 slice A castouts - Modified",
		.pme_long_desc = "L2 slice A castouts - Modified",
	},
	[ POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_BOTH_COUNT ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_BOTH_COUNT",
		.pme_code = 0x30001d,
		.pme_short_desc = "Periods both threads LMQ and SRQ empty",
		.pme_long_desc = "Periods both threads LMQ and SRQ empty",
	},
	[ POWER6_PME_PM_PTEG_FROM_MEM_DP ] = {
		.pme_name = "PM_PTEG_FROM_MEM_DP",
		.pme_code = 0x11304a,
		.pme_short_desc = "PTEG loaded from double pump memory",
		.pme_long_desc = "PTEG loaded from double pump memory",
	},
	[ POWER6_PME_PM_LSU_REJECT_SLOW ] = {
		.pme_name = "PM_LSU_REJECT_SLOW",
		.pme_code = 0x20003e,
		.pme_short_desc = "LSU slow reject",
		.pme_long_desc = "LSU slow reject",
	},
	[ POWER6_PME_PM_PTEG_FROM_L25_MOD ] = {
		.pme_name = "PM_PTEG_FROM_L25_MOD",
		.pme_code = 0x31304e,
		.pme_short_desc = "PTEG loaded from L2.5 modified",
		.pme_long_desc = "PTEG loaded from L2.5 modified",
	},
	[ POWER6_PME_PM_THRD_PRIO_7_CYC ] = {
		.pme_name = "PM_THRD_PRIO_7_CYC",
		.pme_code = 0x122046,
		.pme_short_desc = "Cycles thread running at priority level 7",
		.pme_long_desc = "Cycles thread running at priority level 7",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_RL2L3_SHR",
		.pme_code = 0x212044,
		.pme_short_desc = "Marked PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "Marked PTEG loaded from remote L2 or L3 shared",
	},
	[ POWER6_PME_PM_ST_REQ_L2 ] = {
		.pme_name = "PM_ST_REQ_L2",
		.pme_code = 0x250732,
		.pme_short_desc = "L2 store requests",
		.pme_long_desc = "L2 store requests",
	},
	[ POWER6_PME_PM_ST_REF_L1 ] = {
		.pme_name = "PM_ST_REF_L1",
		.pme_code = 0x80086,
		.pme_short_desc = "L1 D cache store references",
		.pme_long_desc = "Total DL1 Store references",
	},
	[ POWER6_PME_PM_FPU_ISSUE_STALL_THRD ] = {
		.pme_name = "PM_FPU_ISSUE_STALL_THRD",
		.pme_code = 0x330e0,
		.pme_short_desc = "FPU issue stalled due to thread resource conflict",
		.pme_long_desc = "FPU issue stalled due to thread resource conflict",
	},
	[ POWER6_PME_PM_RUN_COUNT ] = {
		.pme_name = "PM_RUN_COUNT",
		.pme_code = 0x10000b,
		.pme_short_desc = "Run Periods",
		.pme_long_desc = "Processor Periods gated by the run latch",
	},
	[ POWER6_PME_PM_RUN_CYC ] = {
		.pme_name = "PM_RUN_CYC",
		.pme_code = 0x10000a,
		.pme_short_desc = "Run cycles",
		.pme_long_desc = "Processor Cycles gated by the run latch",
	},
	[ POWER6_PME_PM_PTEG_FROM_RMEM ] = {
		.pme_name = "PM_PTEG_FROM_RMEM",
		.pme_code = 0x31304a,
		.pme_short_desc = "PTEG loaded from remote memory",
		.pme_long_desc = "PTEG loaded from remote memory",
	},
	[ POWER6_PME_PM_LSU0_LDF ] = {
		.pme_name = "PM_LSU0_LDF",
		.pme_code = 0x80084,
		.pme_short_desc = "LSU0 executed Floating Point load instruction",
		.pme_long_desc = "A floating point load was executed from LSU unit 0",
	},
	[ POWER6_PME_PM_ST_MISS_L1 ] = {
		.pme_name = "PM_ST_MISS_L1",
		.pme_code = 0x80088,
		.pme_short_desc = "L1 D cache store misses",
		.pme_long_desc = "A store missed the dcache",
	},
	[ POWER6_PME_PM_INST_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_INST_FROM_DL2L3_SHR",
		.pme_code = 0x342044,
		.pme_short_desc = "Instruction fetched from distant L2 or L3 shared",
		.pme_long_desc = "Instruction fetched from distant L2 or L3 shared",
	},
	[ POWER6_PME_PM_L2SA_IC_INV ] = {
		.pme_name = "PM_L2SA_IC_INV",
		.pme_code = 0x50684,
		.pme_short_desc = "L2 slice A I cache invalidate",
		.pme_long_desc = "L2 slice A I cache invalidate",
	},
	[ POWER6_PME_PM_THRD_ONE_RUN_CYC ] = {
		.pme_name = "PM_THRD_ONE_RUN_CYC",
		.pme_code = 0x100016,
		.pme_short_desc = "One of the threads in run cycles",
		.pme_long_desc = "One of the threads in run cycles",
	},
	[ POWER6_PME_PM_L2SB_LD_REQ_INST ] = {
		.pme_name = "PM_L2SB_LD_REQ_INST",
		.pme_code = 0x50588,
		.pme_short_desc = "L2 slice B instruction load requests",
		.pme_long_desc = "L2 slice B instruction load requests",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L25_MOD ] = {
		.pme_name = "PM_MRK_DATA_FROM_L25_MOD",
		.pme_code = 0x30304e,
		.pme_short_desc = "Marked data loaded from L2.5 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of a chip on this MCM due to a marked demand load",
	},
	[ POWER6_PME_PM_DPU_HELD_XTHRD ] = {
		.pme_name = "PM_DPU_HELD_XTHRD",
		.pme_code = 0x30082,
		.pme_short_desc = "DISP unit held due to cross thread resource conflicts",
		.pme_long_desc = "DISP unit held due to cross thread resource conflicts",
	},
	[ POWER6_PME_PM_L2SB_ST_REQ ] = {
		.pme_name = "PM_L2SB_ST_REQ",
		.pme_code = 0x5048c,
		.pme_short_desc = "L2 slice B store requests",
		.pme_long_desc = "A store request as seen at the L2 directory has been made from the core. Stores are counted after gathering in the L2 store queues. The event is provided on each of the three slices A,B,and C.",
	},
	[ POWER6_PME_PM_INST_FROM_L21 ] = {
		.pme_name = "PM_INST_FROM_L21",
		.pme_code = 0x242040,
		.pme_short_desc = "Instruction fetched from private L2 other core",
		.pme_long_desc = "Instruction fetched from private L2 other core",
	},
	[ POWER6_PME_PM_INST_FROM_L3MISS ] = {
		.pme_name = "PM_INST_FROM_L3MISS",
		.pme_code = 0x342054,
		.pme_short_desc = "Instruction fetched missed L3",
		.pme_long_desc = "Instruction fetched missed L3",
	},
	[ POWER6_PME_PM_L3SB_HIT ] = {
		.pme_name = "PM_L3SB_HIT",
		.pme_code = 0x5008a,
		.pme_short_desc = "L3 slice B hits",
		.pme_long_desc = "L3 slice B hits",
	},
	[ POWER6_PME_PM_EE_OFF_EXT_INT ] = {
		.pme_name = "PM_EE_OFF_EXT_INT",
		.pme_code = 0x230ee,
		.pme_short_desc = "Cycles MSR(EE) bit off and external interrupt pending",
		.pme_long_desc = "Cycles MSR(EE) bit off and external interrupt pending",
	},
	[ POWER6_PME_PM_INST_FROM_DL2L3_MOD ] = {
		.pme_name = "PM_INST_FROM_DL2L3_MOD",
		.pme_code = 0x442044,
		.pme_short_desc = "Instruction fetched from distant L2 or L3 modified",
		.pme_long_desc = "Instruction fetched from distant L2 or L3 modified",
	},
	[ POWER6_PME_PM_PMC6_OVERFLOW ] = {
		.pme_name = "PM_PMC6_OVERFLOW",
		.pme_code = 0x300024,
		.pme_short_desc = "PMC6 Overflow",
		.pme_long_desc = "PMC6 Overflow",
	},
	[ POWER6_PME_PM_FPU_FLOP ] = {
		.pme_name = "PM_FPU_FLOP",
		.pme_code = 0x1c0032,
		.pme_short_desc = "FPU executed 1FLOP, FMA, FSQRT or FDIV instruction",
		.pme_long_desc = "FPU executed 1FLOP, FMA, FSQRT or FDIV instruction",
	},
	[ POWER6_PME_PM_FXU_BUSY ] = {
		.pme_name = "PM_FXU_BUSY",
		.pme_code = 0x200050,
		.pme_short_desc = "FXU busy",
		.pme_long_desc = "FXU0 and FXU1 are both busy",
	},
	[ POWER6_PME_PM_FPU1_FLOP ] = {
		.pme_name = "PM_FPU1_FLOP",
		.pme_code = 0xc008e,
		.pme_short_desc = "FPU1 executed 1FLOP, FMA, FSQRT or FDIV instruction",
		.pme_long_desc = "FPU1 executed 1FLOP, FMA, FSQRT or FDIV instruction",
	},
	[ POWER6_PME_PM_IC_RELOAD_SHR ] = {
		.pme_name = "PM_IC_RELOAD_SHR",
		.pme_code = 0x4008e,
		.pme_short_desc = "I cache line reloading to be shared by threads",
		.pme_long_desc = "I cache line reloading to be shared by threads",
	},
	[ POWER6_PME_PM_INST_TABLEWALK_CYC ] = {
		.pme_name = "PM_INST_TABLEWALK_CYC",
		.pme_code = 0x920ca,
		.pme_short_desc = "Cycles doing instruction tablewalks",
		.pme_long_desc = "Cycles doing instruction tablewalks",
	},
	[ POWER6_PME_PM_DATA_FROM_RL2L3_MOD_CYC ] = {
		.pme_name = "PM_DATA_FROM_RL2L3_MOD_CYC",
		.pme_code = 0x400028,
		.pme_short_desc = "Load latency from remote L2 or L3 modified",
		.pme_long_desc = "Load latency from remote L2 or L3 modified",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_5or6_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_5or6_CYC",
		.pme_code = 0x423040,
		.pme_short_desc = "Cycles thread priority difference is 5 or 6",
		.pme_long_desc = "Cycles thread priority difference is 5 or 6",
	},
	[ POWER6_PME_PM_IBUF_FULL_CYC ] = {
		.pme_name = "PM_IBUF_FULL_CYC",
		.pme_code = 0x40084,
		.pme_short_desc = "Cycles instruction buffer full",
		.pme_long_desc = "Cycles instruction buffer full",
	},
	[ POWER6_PME_PM_L2SA_LD_REQ ] = {
		.pme_name = "PM_L2SA_LD_REQ",
		.pme_code = 0x50780,
		.pme_short_desc = "L2 slice A load requests ",
		.pme_long_desc = "L2 slice A load requests ",
	},
	[ POWER6_PME_PM_VMX1_LD_WRBACK ] = {
		.pme_name = "PM_VMX1_LD_WRBACK",
		.pme_code = 0x6008c,
		.pme_short_desc = "VMX1 load writeback valid",
		.pme_long_desc = "VMX1 load writeback valid",
	},
	[ POWER6_PME_PM_MRK_FPU_FIN ] = {
		.pme_name = "PM_MRK_FPU_FIN",
		.pme_code = 0x2d0030,
		.pme_short_desc = "Marked instruction FPU processing finished",
		.pme_long_desc = "One of the Floating Point Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER6_PME_PM_THRD_PRIO_5_CYC ] = {
		.pme_name = "PM_THRD_PRIO_5_CYC",
		.pme_code = 0x322046,
		.pme_short_desc = "Cycles thread running at priority level 5",
		.pme_long_desc = "Cycles thread running at priority level 5",
	},
	[ POWER6_PME_PM_DFU_BACK2BACK ] = {
		.pme_name = "PM_DFU_BACK2BACK",
		.pme_code = 0xe0082,
		.pme_short_desc = "DFU back to back operations executed",
		.pme_long_desc = "DFU back to back operations executed",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_LMEM ] = {
		.pme_name = "PM_MRK_DATA_FROM_LMEM",
		.pme_code = 0x40304a,
		.pme_short_desc = "Marked data loaded from local memory",
		.pme_long_desc = "Marked data loaded from local memory",
	},
	[ POWER6_PME_PM_LSU_REJECT_LHS ] = {
		.pme_name = "PM_LSU_REJECT_LHS",
		.pme_code = 0x190032,
		.pme_short_desc = "Load hit store reject",
		.pme_long_desc = "Load hit store reject",
	},
	[ POWER6_PME_PM_DPU_HELD_SPR ] = {
		.pme_name = "PM_DPU_HELD_SPR",
		.pme_code = 0x3008c,
		.pme_short_desc = "DISP unit held due to MTSPR/MFSPR",
		.pme_long_desc = "DISP unit held due to MTSPR/MFSPR",
	},
	[ POWER6_PME_PM_FREQ_DOWN ] = {
		.pme_name = "PM_FREQ_DOWN",
		.pme_code = 0x30003c,
		.pme_short_desc = "Frequency is being slewed down due to Power Management",
		.pme_long_desc = "Frequency is being slewed down due to Power Management",
	},
	[ POWER6_PME_PM_DFU_ENC_BCD_DPD ] = {
		.pme_name = "PM_DFU_ENC_BCD_DPD",
		.pme_code = 0xe008a,
		.pme_short_desc = "DFU Encode BCD to DPD",
		.pme_long_desc = "DFU Encode BCD to DPD",
	},
	[ POWER6_PME_PM_DPU_HELD_GPR ] = {
		.pme_name = "PM_DPU_HELD_GPR",
		.pme_code = 0x20080,
		.pme_short_desc = "DISP unit held due to GPR dependencies",
		.pme_long_desc = "DISP unit held due to GPR dependencies",
	},
	[ POWER6_PME_PM_LSU0_NCST ] = {
		.pme_name = "PM_LSU0_NCST",
		.pme_code = 0x820cc,
		.pme_short_desc = "LSU0 non-cachable stores",
		.pme_long_desc = "LSU0 non-cachable stores",
	},
	[ POWER6_PME_PM_MRK_INST_ISSUED ] = {
		.pme_name = "PM_MRK_INST_ISSUED",
		.pme_code = 0x10001c,
		.pme_short_desc = "Marked instruction issued",
		.pme_long_desc = "Marked instruction issued",
	},
	[ POWER6_PME_PM_INST_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_INST_FROM_RL2L3_SHR",
		.pme_code = 0x242044,
		.pme_short_desc = "Instruction fetched from remote L2 or L3 shared",
		.pme_long_desc = "Instruction fetched from remote L2 or L3 shared",
	},
	[ POWER6_PME_PM_FPU_DENORM ] = {
		.pme_name = "PM_FPU_DENORM",
		.pme_code = 0x2c1034,
		.pme_short_desc = "FPU received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized. Combined Unit 0 + Unit 1",
	},
	[ POWER6_PME_PM_PTEG_FROM_L3MISS ] = {
		.pme_name = "PM_PTEG_FROM_L3MISS",
		.pme_code = 0x313028,
		.pme_short_desc = "PTEG loaded from L3 miss",
		.pme_long_desc = "PTEG loaded from L3 miss",
	},
	[ POWER6_PME_PM_RUN_PURR ] = {
		.pme_name = "PM_RUN_PURR",
		.pme_code = 0x4000f4,
		.pme_short_desc = "Run PURR Event",
		.pme_long_desc = "Run PURR Event",
	},
	[ POWER6_PME_PM_MRK_VMX0_LD_WRBACK ] = {
		.pme_name = "PM_MRK_VMX0_LD_WRBACK",
		.pme_code = 0x60086,
		.pme_short_desc = "Marked VMX0 load writeback valid",
		.pme_long_desc = "Marked VMX0 load writeback valid",
	},
	[ POWER6_PME_PM_L2_MISS ] = {
		.pme_name = "PM_L2_MISS",
		.pme_code = 0x250532,
		.pme_short_desc = "L2 cache misses",
		.pme_long_desc = "L2 cache misses",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L3 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L3",
		.pme_code = 0x303048,
		.pme_short_desc = "Marked data loaded from L3",
		.pme_long_desc = "DL1 was reloaded from the local L3 due to a marked demand load",
	},
	[ POWER6_PME_PM_MRK_LSU1_REJECT_LHS ] = {
		.pme_name = "PM_MRK_LSU1_REJECT_LHS",
		.pme_code = 0x930ee,
		.pme_short_desc = "LSU1 marked load hit store reject",
		.pme_long_desc = "LSU1 marked load hit store reject",
	},
	[ POWER6_PME_PM_L2SB_LD_MISS_INST ] = {
		.pme_name = "PM_L2SB_LD_MISS_INST",
		.pme_code = 0x5058a,
		.pme_short_desc = "L2 slice B instruction load misses",
		.pme_long_desc = "L2 slice B instruction load misses",
	},
	[ POWER6_PME_PM_PTEG_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_PTEG_FROM_RL2L3_SHR",
		.pme_code = 0x21304c,
		.pme_short_desc = "PTEG loaded from remote L2 or L3 shared",
		.pme_long_desc = "PTEG loaded from remote L2 or L3 shared",
	},
	[ POWER6_PME_PM_MRK_DERAT_MISS_64K ] = {
		.pme_name = "PM_MRK_DERAT_MISS_64K",
		.pme_code = 0x192044,
		.pme_short_desc = "Marked DERAT misses for 64K page",
		.pme_long_desc = "A marked data request (load or store) missed the ERAT for 64K page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_LWSYNC ] = {
		.pme_name = "PM_LWSYNC",
		.pme_code = 0x810ae,
		.pme_short_desc = "Isync instruction completed",
		.pme_long_desc = "Isync instruction completed",
	},
	[ POWER6_PME_PM_FPU1_FXMULT ] = {
		.pme_name = "PM_FPU1_FXMULT",
		.pme_code = 0xd008e,
		.pme_short_desc = "FPU1 executed fixed point multiplication",
		.pme_long_desc = "FPU1 executed fixed point multiplication",
	},
	[ POWER6_PME_PM_MEM0_DP_CL_WR_GLOB ] = {
		.pme_name = "PM_MEM0_DP_CL_WR_GLOB",
		.pme_code = 0x50284,
		.pme_short_desc = "cacheline write setting dp to global side 0",
		.pme_long_desc = "cacheline write setting dp to global side 0",
	},
	[ POWER6_PME_PM_LSU0_REJECT_PARTIAL_SECTOR ] = {
		.pme_name = "PM_LSU0_REJECT_PARTIAL_SECTOR",
		.pme_code = 0xa0086,
		.pme_short_desc = "LSU0 reject due to partial sector valid",
		.pme_long_desc = "LSU0 reject due to partial sector valid",
	},
	[ POWER6_PME_PM_INST_IMC_MATCH_CMPL ] = {
		.pme_name = "PM_INST_IMC_MATCH_CMPL",
		.pme_code = 0x1000f0,
		.pme_short_desc = "IMC matched instructions completed",
		.pme_long_desc = "Number of instructions resulting from the marked instructions expansion that completed.",
	},
	[ POWER6_PME_PM_DPU_HELD_THERMAL ] = {
		.pme_name = "PM_DPU_HELD_THERMAL",
		.pme_code = 0x10002a,
		.pme_short_desc = "DISP unit held due to thermal condition",
		.pme_long_desc = "DISP unit held due to thermal condition",
	},
	[ POWER6_PME_PM_FPU_FRSP ] = {
		.pme_name = "PM_FPU_FRSP",
		.pme_code = 0x2d1034,
		.pme_short_desc = "FPU executed FRSP instruction",
		.pme_long_desc = "FPU executed FRSP instruction",
	},
	[ POWER6_PME_PM_MRK_INST_FIN ] = {
		.pme_name = "PM_MRK_INST_FIN",
		.pme_code = 0x30000a,
		.pme_short_desc = "Marked instruction finished",
		.pme_long_desc = "One of the execution units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_DL2L3_SHR",
		.pme_code = 0x312044,
		.pme_short_desc = "Marked PTEG loaded from distant L2 or L3 shared",
		.pme_long_desc = "Marked PTEG loaded from distant L2 or L3 shared",
	},
	[ POWER6_PME_PM_MRK_DTLB_REF ] = {
		.pme_name = "PM_MRK_DTLB_REF",
		.pme_code = 0x920c0,
		.pme_short_desc = "Marked Data TLB reference",
		.pme_long_desc = "Marked Data TLB reference",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L25_SHR ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L25_SHR",
		.pme_code = 0x412046,
		.pme_short_desc = "Marked PTEG loaded from L2.5 shared",
		.pme_long_desc = "Marked PTEG loaded from L2.5 shared",
	},
	[ POWER6_PME_PM_DPU_HELD_LSU ] = {
		.pme_name = "PM_DPU_HELD_LSU",
		.pme_code = 0x210a2,
		.pme_short_desc = "DISP unit held due to LSU move or invalidate SLB and SR",
		.pme_long_desc = "DISP unit held due to LSU move or invalidate SLB and SR",
	},
	[ POWER6_PME_PM_FPU_FSQRT_FDIV ] = {
		.pme_name = "PM_FPU_FSQRT_FDIV",
		.pme_code = 0x2c0032,
		.pme_short_desc = "FPU executed FSQRT or FDIV instruction",
		.pme_long_desc = "FPU executed FSQRT or FDIV instruction",
	},
	[ POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_COUNT ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_COUNT",
		.pme_code = 0x20001d,
		.pme_short_desc = "Periods LMQ and SRQ empty",
		.pme_long_desc = "Periods when both the LMQ and SRQ are empty (LSU is idle)",
	},
	[ POWER6_PME_PM_DATA_PTEG_SECONDARY ] = {
		.pme_name = "PM_DATA_PTEG_SECONDARY",
		.pme_code = 0x910a4,
		.pme_short_desc = "Data table walk matched in secondary PTEG",
		.pme_long_desc = "Data table walk matched in secondary PTEG",
	},
	[ POWER6_PME_PM_FPU1_FEST ] = {
		.pme_name = "PM_FPU1_FEST",
		.pme_code = 0xd10ae,
		.pme_short_desc = "FPU1 executed FEST instruction",
		.pme_long_desc = "This signal is active for one cycle when fp1 is executing one of the estimate instructions. This could be fres* or frsqrte* where XYZ* means XYZ or XYZ. ",
	},
	[ POWER6_PME_PM_L2SA_LD_HIT ] = {
		.pme_name = "PM_L2SA_LD_HIT",
		.pme_code = 0x50782,
		.pme_short_desc = "L2 slice A load hits",
		.pme_long_desc = "L2 slice A load hits",
	},
	[ POWER6_PME_PM_DATA_FROM_MEM_DP_CYC ] = {
		.pme_name = "PM_DATA_FROM_MEM_DP_CYC",
		.pme_code = 0x40002e,
		.pme_short_desc = "Load latency from double pump memory",
		.pme_long_desc = "Load latency from double pump memory",
	},
	[ POWER6_PME_PM_BR_MPRED_CCACHE ] = {
		.pme_name = "PM_BR_MPRED_CCACHE",
		.pme_code = 0x410ae,
		.pme_short_desc = "Branch misprediction due to count cache prediction",
		.pme_long_desc = "Branch misprediction due to count cache prediction",
	},
	[ POWER6_PME_PM_DPU_HELD_COUNT ] = {
		.pme_name = "PM_DPU_HELD_COUNT",
		.pme_code = 0x200005,
		.pme_short_desc = "Periods DISP unit held",
		.pme_long_desc = "Dispatch unit held",
	},
	[ POWER6_PME_PM_LSU1_REJECT_SET_MPRED ] = {
		.pme_name = "PM_LSU1_REJECT_SET_MPRED",
		.pme_code = 0xa008c,
		.pme_short_desc = "LSU1 reject due to mispredicted set",
		.pme_long_desc = "LSU1 reject due to mispredicted set",
	},
	[ POWER6_PME_PM_FPU_ISSUE_2 ] = {
		.pme_name = "PM_FPU_ISSUE_2",
		.pme_code = 0x320ca,
		.pme_short_desc = "FPU issue 2 per cycle",
		.pme_long_desc = "FPU issue 2 per cycle",
	},
	[ POWER6_PME_PM_LSU1_REJECT_L2_CORR ] = {
		.pme_name = "PM_LSU1_REJECT_L2_CORR",
		.pme_code = 0xa10a8,
		.pme_short_desc = "LSU1 reject due to L2 correctable error",
		.pme_long_desc = "LSU1 reject due to L2 correctable error",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_DMEM ] = {
		.pme_name = "PM_MRK_PTEG_FROM_DMEM",
		.pme_code = 0x212042,
		.pme_short_desc = "Marked PTEG loaded from distant memory",
		.pme_long_desc = "Marked PTEG loaded from distant memory",
	},
	[ POWER6_PME_PM_MEM1_DP_RQ_LOC_GLOB ] = {
		.pme_name = "PM_MEM1_DP_RQ_LOC_GLOB",
		.pme_code = 0x5028a,
		.pme_short_desc = "Memory read queue marking cache line double pump state from local to global side 1",
		.pme_long_desc = "Memory read queue marking cache line double pump state from local to global side 1",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_minus1or2_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_minus1or2_CYC",
		.pme_code = 0x223046,
		.pme_short_desc = "Cycles thread priority difference is -1 or -2",
		.pme_long_desc = "Cycles thread priority difference is -1 or -2",
	},
	[ POWER6_PME_PM_THRD_PRIO_0_CYC ] = {
		.pme_name = "PM_THRD_PRIO_0_CYC",
		.pme_code = 0x122040,
		.pme_short_desc = "Cycles thread running at priority level 0",
		.pme_long_desc = "Cycles thread running at priority level 0",
	},
	[ POWER6_PME_PM_FXU0_BUSY_FXU1_IDLE ] = {
		.pme_name = "PM_FXU0_BUSY_FXU1_IDLE",
		.pme_code = 0x300050,
		.pme_short_desc = "FXU0 busy FXU1 idle",
		.pme_long_desc = "FXU0 is busy while FXU1 was idle",
	},
	[ POWER6_PME_PM_LSU1_REJECT_DERAT_MPRED ] = {
		.pme_name = "PM_LSU1_REJECT_DERAT_MPRED",
		.pme_code = 0xa008a,
		.pme_short_desc = "LSU1 reject due to mispredicted DERAT",
		.pme_long_desc = "LSU1 reject due to mispredicted DERAT",
	},
	[ POWER6_PME_PM_MRK_VMX1_LD_WRBACK ] = {
		.pme_name = "PM_MRK_VMX1_LD_WRBACK",
		.pme_code = 0x6008e,
		.pme_short_desc = "Marked VMX1 load writeback valid",
		.pme_long_desc = "Marked VMX1 load writeback valid",
	},
	[ POWER6_PME_PM_DATA_FROM_RL2L3_SHR_CYC ] = {
		.pme_name = "PM_DATA_FROM_RL2L3_SHR_CYC",
		.pme_code = 0x200028,
		.pme_short_desc = "Load latency from remote L2 or L3 shared",
		.pme_long_desc = "Load latency from remote L2 or L3 shared",
	},
	[ POWER6_PME_PM_IERAT_MISS_16M ] = {
		.pme_name = "PM_IERAT_MISS_16M",
		.pme_code = 0x292076,
		.pme_short_desc = "IERAT misses for 16M page",
		.pme_long_desc = "IERAT misses for 16M page",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_MEM_DP ] = {
		.pme_name = "PM_MRK_DATA_FROM_MEM_DP",
		.pme_code = 0x10304a,
		.pme_short_desc = "Marked data loaded from double pump memory",
		.pme_long_desc = "Marked data loaded from double pump memory",
	},
	[ POWER6_PME_PM_LARX_L1HIT ] = {
		.pme_name = "PM_LARX_L1HIT",
		.pme_code = 0x830e2,
		.pme_short_desc = "larx hits in L1",
		.pme_long_desc = "larx hits in L1",
	},
	[ POWER6_PME_PM_L2_ST_MISS_DATA ] = {
		.pme_name = "PM_L2_ST_MISS_DATA",
		.pme_code = 0x150432,
		.pme_short_desc = "L2 data store misses",
		.pme_long_desc = "L2 data store misses",
	},
	[ POWER6_PME_PM_FPU_ST_FOLDED ] = {
		.pme_name = "PM_FPU_ST_FOLDED",
		.pme_code = 0x3d1030,
		.pme_short_desc = "FPU folded store",
		.pme_long_desc = "FPU folded store",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L35_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_L35_SHR",
		.pme_code = 0x20304e,
		.pme_short_desc = "Marked data loaded from L3.5 shared",
		.pme_long_desc = "Marked data loaded from L3.5 shared",
	},
	[ POWER6_PME_PM_DPU_HELD_MULT_GPR ] = {
		.pme_name = "PM_DPU_HELD_MULT_GPR",
		.pme_code = 0x210aa,
		.pme_short_desc = "DISP unit held due to multiple/divide multiply/divide GPR dependencies",
		.pme_long_desc = "DISP unit held due to multiple/divide multiply/divide GPR dependencies",
	},
	[ POWER6_PME_PM_FPU0_1FLOP ] = {
		.pme_name = "PM_FPU0_1FLOP",
		.pme_code = 0xc0080,
		.pme_short_desc = "FPU0 executed add, mult, sub, cmp or sel instruction",
		.pme_long_desc = "This signal is active for one cycle when fp0 is executing an add, mult, sub, compare, or fsel kind of instruction. This could be fadd*, fmul*, fsub*, fcmp**, fsel where XYZ* means XYZ, XYZs, XYZ., XYZs. and XYZ** means XYZu, XYZo",
	},
	[ POWER6_PME_PM_IERAT_MISS_16G ] = {
		.pme_name = "PM_IERAT_MISS_16G",
		.pme_code = 0x192076,
		.pme_short_desc = "IERAT misses for 16G page",
		.pme_long_desc = "IERAT misses for 16G page",
	},
	[ POWER6_PME_PM_IC_PREF_WRITE ] = {
		.pme_name = "PM_IC_PREF_WRITE",
		.pme_code = 0x430e0,
		.pme_short_desc = "Instruction prefetch written into I cache",
		.pme_long_desc = "Instruction prefetch written into I cache",
	},
	[ POWER6_PME_PM_THRD_PRIO_DIFF_minus5or6_CYC ] = {
		.pme_name = "PM_THRD_PRIO_DIFF_minus5or6_CYC",
		.pme_code = 0x423046,
		.pme_short_desc = "Cycles thread priority difference is -5 or -6",
		.pme_long_desc = "Cycles thread priority difference is -5 or -6",
	},
	[ POWER6_PME_PM_FPU0_FIN ] = {
		.pme_name = "PM_FPU0_FIN",
		.pme_code = 0xd0080,
		.pme_short_desc = "FPU0 produced a result",
		.pme_long_desc = "fp0 finished, produced a result This only indicates finish, not completion. ",
	},
	[ POWER6_PME_PM_DATA_FROM_L2_CYC ] = {
		.pme_name = "PM_DATA_FROM_L2_CYC",
		.pme_code = 0x200020,
		.pme_short_desc = "Load latency from L2",
		.pme_long_desc = "Load latency from L2",
	},
	[ POWER6_PME_PM_DERAT_REF_16G ] = {
		.pme_name = "PM_DERAT_REF_16G",
		.pme_code = 0x482070,
		.pme_short_desc = "DERAT reference for 16G page",
		.pme_long_desc = "DERAT reference for 16G page",
	},
	[ POWER6_PME_PM_BR_PRED ] = {
		.pme_name = "PM_BR_PRED",
		.pme_code = 0x410a0,
		.pme_short_desc = "A conditional branch was predicted",
		.pme_long_desc = "A conditional branch was predicted",
	},
	[ POWER6_PME_PM_VMX1_LD_ISSUED ] = {
		.pme_name = "PM_VMX1_LD_ISSUED",
		.pme_code = 0x6008a,
		.pme_short_desc = "VMX1 load issued",
		.pme_long_desc = "VMX1 load issued",
	},
	[ POWER6_PME_PM_L2SB_CASTOUT_MOD ] = {
		.pme_name = "PM_L2SB_CASTOUT_MOD",
		.pme_code = 0x50688,
		.pme_short_desc = "L2 slice B castouts - Modified",
		.pme_long_desc = "L2 slice B castouts - Modified",
	},
	[ POWER6_PME_PM_INST_FROM_DMEM ] = {
		.pme_name = "PM_INST_FROM_DMEM",
		.pme_code = 0x242042,
		.pme_short_desc = "Instruction fetched from distant memory",
		.pme_long_desc = "Instruction fetched from distant memory",
	},
	[ POWER6_PME_PM_DATA_FROM_L35_SHR_CYC ] = {
		.pme_name = "PM_DATA_FROM_L35_SHR_CYC",
		.pme_code = 0x200026,
		.pme_short_desc = "Load latency from L3.5 shared",
		.pme_long_desc = "Load latency from L3.5 shared",
	},
	[ POWER6_PME_PM_LSU0_NCLD ] = {
		.pme_name = "PM_LSU0_NCLD",
		.pme_code = 0x820ca,
		.pme_short_desc = "LSU0 non-cacheable loads",
		.pme_long_desc = "LSU0 non-cacheable loads",
	},
	[ POWER6_PME_PM_FAB_RETRY_NODE_PUMP ] = {
		.pme_name = "PM_FAB_RETRY_NODE_PUMP",
		.pme_code = 0x5018a,
		.pme_short_desc = "Retry of a node pump, locally mastered",
		.pme_long_desc = "Retry of a node pump, locally mastered",
	},
	[ POWER6_PME_PM_VMX0_INST_ISSUED ] = {
		.pme_name = "PM_VMX0_INST_ISSUED",
		.pme_code = 0x60080,
		.pme_short_desc = "VMX0 instruction issued",
		.pme_long_desc = "VMX0 instruction issued",
	},
	[ POWER6_PME_PM_DATA_FROM_L25_MOD ] = {
		.pme_name = "PM_DATA_FROM_L25_MOD",
		.pme_code = 0x30005a,
		.pme_short_desc = "Data loaded from L2.5 modified",
		.pme_long_desc = "DL1 was reloaded with modified (M) data from the L2 of a chip on this MCM due to a demand load",
	},
	[ POWER6_PME_PM_DPU_HELD_ITLB_ISLB ] = {
		.pme_name = "PM_DPU_HELD_ITLB_ISLB",
		.pme_code = 0x210a4,
		.pme_short_desc = "DISP unit held due to SLB or TLB invalidates ",
		.pme_long_desc = "DISP unit held due to SLB or TLB invalidates ",
	},
	[ POWER6_PME_PM_LSU_LMQ_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_LMQ_SRQ_EMPTY_CYC",
		.pme_code = 0x20001c,
		.pme_short_desc = "Cycles LMQ and SRQ empty",
		.pme_long_desc = "Cycles when both the LMQ and SRQ are empty (LSU is idle)",
	},
	[ POWER6_PME_PM_THRD_CONC_RUN_INST ] = {
		.pme_name = "PM_THRD_CONC_RUN_INST",
		.pme_code = 0x300026,
		.pme_short_desc = "Concurrent run instructions",
		.pme_long_desc = "Concurrent run instructions",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L2 ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L2",
		.pme_code = 0x112040,
		.pme_short_desc = "Marked PTEG loaded from L2.5 modified",
		.pme_long_desc = "Marked PTEG loaded from L2.5 modified",
	},
	[ POWER6_PME_PM_PURR ] = {
		.pme_name = "PM_PURR",
		.pme_code = 0x10000e,
		.pme_short_desc = "PURR Event",
		.pme_long_desc = "PURR Event",
	},
	[ POWER6_PME_PM_DERAT_MISS_64K ] = {
		.pme_name = "PM_DERAT_MISS_64K",
		.pme_code = 0x292070,
		.pme_short_desc = "DERAT misses for 64K page",
		.pme_long_desc = "A data request (load or store) missed the ERAT for 64K page and resulted in an ERAT reload.",
	},
	[ POWER6_PME_PM_PMC2_REWIND ] = {
		.pme_name = "PM_PMC2_REWIND",
		.pme_code = 0x300020,
		.pme_short_desc = "PMC2 rewind event",
		.pme_long_desc = "PMC2 rewind event",
	},
	[ POWER6_PME_PM_INST_FROM_L2 ] = {
		.pme_name = "PM_INST_FROM_L2",
		.pme_code = 0x142040,
		.pme_short_desc = "Instructions fetched from L2",
		.pme_long_desc = "An instruction fetch group was fetched from L2. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER6_PME_PM_INST_DISP ] = {
		.pme_name = "PM_INST_DISP",
		.pme_code = 0x200012,
		.pme_short_desc = "Instructions dispatched",
		.pme_long_desc = "The ISU sends the number of instructions dispatched.",
	},
	[ POWER6_PME_PM_DATA_FROM_L25_SHR ] = {
		.pme_name = "PM_DATA_FROM_L25_SHR",
		.pme_code = 0x40005a,
		.pme_short_desc = "Data loaded from L2.5 shared",
		.pme_long_desc = "DL1 was reloaded with shared (T or SL) data from the L2 of a chip on this MCM due to a demand load",
	},
	[ POWER6_PME_PM_L1_DCACHE_RELOAD_VALID ] = {
		.pme_name = "PM_L1_DCACHE_RELOAD_VALID",
		.pme_code = 0x3000f6,
		.pme_short_desc = "L1 reload data source valid",
		.pme_long_desc = "The data source information is valid",
	},
	[ POWER6_PME_PM_LSU1_REJECT_UST ] = {
		.pme_name = "PM_LSU1_REJECT_UST",
		.pme_code = 0x9008a,
		.pme_short_desc = "LSU1 unaligned store reject",
		.pme_long_desc = "LSU1 unaligned store reject",
	},
	[ POWER6_PME_PM_FAB_ADDR_COLLISION ] = {
		.pme_name = "PM_FAB_ADDR_COLLISION",
		.pme_code = 0x5018e,
		.pme_short_desc = "local node launch collision with off-node address",
		.pme_long_desc = "local node launch collision with off-node address",
	},
	[ POWER6_PME_PM_MRK_FXU_FIN ] = {
		.pme_name = "PM_MRK_FXU_FIN",
		.pme_code = 0x20001a,
		.pme_short_desc = "Marked instruction FXU processing finished",
		.pme_long_desc = "The fixed point units (Unit 0 + Unit 1) finished a marked instruction. Instructions that finish may not necessary complete.",
	},
	[ POWER6_PME_PM_LSU0_REJECT_UST ] = {
		.pme_name = "PM_LSU0_REJECT_UST",
		.pme_code = 0x90082,
		.pme_short_desc = "LSU0 unaligned store reject",
		.pme_long_desc = "LSU0 unaligned store reject",
	},
	[ POWER6_PME_PM_PMC4_OVERFLOW ] = {
		.pme_name = "PM_PMC4_OVERFLOW",
		.pme_code = 0x100014,
		.pme_short_desc = "PMC4 Overflow",
		.pme_long_desc = "PMC4 Overflow",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L3 ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L3",
		.pme_code = 0x312040,
		.pme_short_desc = "Marked PTEG loaded from L3",
		.pme_long_desc = "Marked PTEG loaded from L3",
	},
	[ POWER6_PME_PM_INST_FROM_L2MISS ] = {
		.pme_name = "PM_INST_FROM_L2MISS",
		.pme_code = 0x442054,
		.pme_short_desc = "Instructions fetched missed L2",
		.pme_long_desc = "An instruction fetch group was fetched from beyond L2.",
	},
	[ POWER6_PME_PM_L2SB_ST_HIT ] = {
		.pme_name = "PM_L2SB_ST_HIT",
		.pme_code = 0x5078e,
		.pme_short_desc = "L2 slice B store hits",
		.pme_long_desc = "A store request made from the core hit in the L2 directory.  This event is provided on each of the three L2 slices A,B, and C.",
	},
	[ POWER6_PME_PM_DPU_WT_IC_MISS_COUNT ] = {
		.pme_name = "PM_DPU_WT_IC_MISS_COUNT",
		.pme_code = 0x20000d,
		.pme_short_desc = "Periods DISP unit is stalled due to I cache miss",
		.pme_long_desc = "Periods DISP unit is stalled due to I cache miss",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_DL2L3_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_DL2L3_SHR",
		.pme_code = 0x30304c,
		.pme_short_desc = "Marked data loaded from distant L2 or L3 shared",
		.pme_long_desc = "Marked data loaded from distant L2 or L3 shared",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L35_MOD ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L35_MOD",
		.pme_code = 0x112046,
		.pme_short_desc = "Marked PTEG loaded from L3.5 modified",
		.pme_long_desc = "Marked PTEG loaded from L3.5 modified",
	},
	[ POWER6_PME_PM_FPU1_FPSCR ] = {
		.pme_name = "PM_FPU1_FPSCR",
		.pme_code = 0xd008c,
		.pme_short_desc = "FPU1 executed FPSCR instruction",
		.pme_long_desc = "FPU1 executed FPSCR instruction",
	},
	[ POWER6_PME_PM_LSU_REJECT_UST ] = {
		.pme_name = "PM_LSU_REJECT_UST",
		.pme_code = 0x290030,
		.pme_short_desc = "Unaligned store reject",
		.pme_long_desc = "Unaligned store reject",
	},
	[ POWER6_PME_PM_LSU0_DERAT_MISS ] = {
		.pme_name = "PM_LSU0_DERAT_MISS",
		.pme_code = 0x910a6,
		.pme_short_desc = "LSU0 DERAT misses",
		.pme_long_desc = "A data request (load or store) from LSU Unit 0 missed the ERAT and resulted in an ERAT reload. Multiple instructions may miss the ERAT entry for the same 4K page, but only one reload will occur.",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_MEM_DP ] = {
		.pme_name = "PM_MRK_PTEG_FROM_MEM_DP",
		.pme_code = 0x112042,
		.pme_short_desc = "Marked PTEG loaded from double pump memory",
		.pme_long_desc = "Marked PTEG loaded from double pump memory",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L2 ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2",
		.pme_code = 0x103048,
		.pme_short_desc = "Marked data loaded from L2",
		.pme_long_desc = "DL1 was reloaded from the local L2 due to a marked demand load",
	},
	[ POWER6_PME_PM_FPU0_FSQRT_FDIV ] = {
		.pme_name = "PM_FPU0_FSQRT_FDIV",
		.pme_code = 0xc0084,
		.pme_short_desc = "FPU0 executed FSQRT or FDIV instruction",
		.pme_long_desc = "FPU0 executed FSQRT or FDIV instruction",
	},
	[ POWER6_PME_PM_DPU_HELD_FXU_SOPS ] = {
		.pme_name = "PM_DPU_HELD_FXU_SOPS",
		.pme_code = 0x30088,
		.pme_short_desc = "DISP unit held due to FXU slow ops (mtmsr, scv, rfscv)",
		.pme_long_desc = "DISP unit held due to FXU slow ops (mtmsr, scv, rfscv)",
	},
	[ POWER6_PME_PM_MRK_FPU0_FIN ] = {
		.pme_name = "PM_MRK_FPU0_FIN",
		.pme_code = 0xd0082,
		.pme_short_desc = "Marked instruction FPU0 processing finished",
		.pme_long_desc = "Marked instruction FPU0 processing finished",
	},
	[ POWER6_PME_PM_L2SB_LD_MISS_DATA ] = {
		.pme_name = "PM_L2SB_LD_MISS_DATA",
		.pme_code = 0x5048a,
		.pme_short_desc = "L2 slice B data load misses",
		.pme_long_desc = "L2 slice B data load misses",
	},
	[ POWER6_PME_PM_LSU_SRQ_EMPTY_CYC ] = {
		.pme_name = "PM_LSU_SRQ_EMPTY_CYC",
		.pme_code = 0x40001c,
		.pme_short_desc = "Cycles SRQ empty",
		.pme_long_desc = "The Store Request Queue is empty",
	},
	[ POWER6_PME_PM_1PLUS_PPC_DISP ] = {
		.pme_name = "PM_1PLUS_PPC_DISP",
		.pme_code = 0x100012,
		.pme_short_desc = "Cycles at least one instruction dispatched",
		.pme_long_desc = "Cycles at least one instruction dispatched",
	},
	[ POWER6_PME_PM_VMX_ST_ISSUED ] = {
		.pme_name = "PM_VMX_ST_ISSUED",
		.pme_code = 0xb0080,
		.pme_short_desc = "VMX store issued",
		.pme_long_desc = "VMX store issued",
	},
	[ POWER6_PME_PM_DATA_FROM_L2MISS ] = {
		.pme_name = "PM_DATA_FROM_L2MISS",
		.pme_code = 0x2000fe,
		.pme_short_desc = "Data loaded missed L2",
		.pme_long_desc = "DL1 was reloaded from beyond L2.",
	},
	[ POWER6_PME_PM_LSU0_REJECT_ULD ] = {
		.pme_name = "PM_LSU0_REJECT_ULD",
		.pme_code = 0x90080,
		.pme_short_desc = "LSU0 unaligned load reject",
		.pme_long_desc = "LSU0 unaligned load reject",
	},
	[ POWER6_PME_PM_SUSPENDED ] = {
		.pme_name = "PM_SUSPENDED",
		.pme_code = 0x0,
		.pme_short_desc = "Suspended",
		.pme_long_desc = "Suspended",
	},
	[ POWER6_PME_PM_DFU_ADD_SHIFTED_BOTH ] = {
		.pme_name = "PM_DFU_ADD_SHIFTED_BOTH",
		.pme_code = 0xe0088,
		.pme_short_desc = "DFU add type with both operands shifted",
		.pme_long_desc = "DFU add type with both operands shifted",
	},
	[ POWER6_PME_PM_LSU_REJECT_NO_SCRATCH ] = {
		.pme_name = "PM_LSU_REJECT_NO_SCRATCH",
		.pme_code = 0x2a1034,
		.pme_short_desc = "LSU reject due to scratch register not available",
		.pme_long_desc = "LSU reject due to scratch register not available",
	},
	[ POWER6_PME_PM_STCX_FAIL ] = {
		.pme_name = "PM_STCX_FAIL",
		.pme_code = 0x830ee,
		.pme_short_desc = "STCX failed",
		.pme_long_desc = "A stcx (stwcx or stdcx) failed",
	},
	[ POWER6_PME_PM_FPU1_DENORM ] = {
		.pme_name = "PM_FPU1_DENORM",
		.pme_code = 0xc10aa,
		.pme_short_desc = "FPU1 received denormalized data",
		.pme_long_desc = "This signal is active for one cycle when one of the operands is denormalized.",
	},
	[ POWER6_PME_PM_GCT_NOSLOT_COUNT ] = {
		.pme_name = "PM_GCT_NOSLOT_COUNT",
		.pme_code = 0x100009,
		.pme_short_desc = "Periods no GCT slot allocated",
		.pme_long_desc = "Periods this thread does not have any slots allocated in the GCT.",
	},
	[ POWER6_PME_PM_DATA_FROM_DL2L3_SHR_CYC ] = {
		.pme_name = "PM_DATA_FROM_DL2L3_SHR_CYC",
		.pme_code = 0x20002a,
		.pme_short_desc = "Load latency from distant L2 or L3 shared",
		.pme_long_desc = "Load latency from distant L2 or L3 shared",
	},
	[ POWER6_PME_PM_DATA_FROM_L21 ] = {
		.pme_name = "PM_DATA_FROM_L21",
		.pme_code = 0x200058,
		.pme_short_desc = "Data loaded from private L2 other core",
		.pme_long_desc = "Data loaded from private L2 other core",
	},
	[ POWER6_PME_PM_FPU_1FLOP ] = {
		.pme_name = "PM_FPU_1FLOP",
		.pme_code = 0x1c0030,
		.pme_short_desc = "FPU executed one flop instruction ",
		.pme_long_desc = "This event counts the number of one flop instructions. These could be fadd*, fmul*, fsub*, fneg+, fabs+, fnabs+, fres+, frsqrte+, fcmp**, or fsel where XYZ* means XYZ, XYZs, XYZ., XYZs., XYZ+ means XYZ, XYZ., and XYZ** means XYZu, XYZo.",
	},
	[ POWER6_PME_PM_LSU1_REJECT ] = {
		.pme_name = "PM_LSU1_REJECT",
		.pme_code = 0xa10ae,
		.pme_short_desc = "LSU1 reject",
		.pme_long_desc = "LSU1 reject",
	},
	[ POWER6_PME_PM_IC_REQ ] = {
		.pme_name = "PM_IC_REQ",
		.pme_code = 0x4008a,
		.pme_short_desc = "I cache demand of prefetch request",
		.pme_long_desc = "I cache demand of prefetch request",
	},
	[ POWER6_PME_PM_MRK_DFU_FIN ] = {
		.pme_name = "PM_MRK_DFU_FIN",
		.pme_code = 0x300008,
		.pme_short_desc = "DFU marked instruction finish",
		.pme_long_desc = "DFU marked instruction finish",
	},
	[ POWER6_PME_PM_NOT_LLA_CYC ] = {
		.pme_name = "PM_NOT_LLA_CYC",
		.pme_code = 0x401e,
		.pme_short_desc = "Load Look Ahead not Active",
		.pme_long_desc = "Load Look Ahead not Active",
	},
	[ POWER6_PME_PM_INST_FROM_L1 ] = {
		.pme_name = "PM_INST_FROM_L1",
		.pme_code = 0x40082,
		.pme_short_desc = "Instruction fetched from L1",
		.pme_long_desc = "An instruction fetch group was fetched from L1. Fetch Groups can contain up to 8 instructions",
	},
	[ POWER6_PME_PM_MRK_VMX_COMPLEX_ISSUED ] = {
		.pme_name = "PM_MRK_VMX_COMPLEX_ISSUED",
		.pme_code = 0x7008c,
		.pme_short_desc = "Marked VMX instruction issued to complex",
		.pme_long_desc = "Marked VMX instruction issued to complex",
	},
	[ POWER6_PME_PM_BRU_FIN ] = {
		.pme_name = "PM_BRU_FIN",
		.pme_code = 0x430e6,
		.pme_short_desc = "BRU produced a result",
		.pme_long_desc = "BRU produced a result",
	},
	[ POWER6_PME_PM_LSU1_REJECT_EXTERN ] = {
		.pme_name = "PM_LSU1_REJECT_EXTERN",
		.pme_code = 0xa10ac,
		.pme_short_desc = "LSU1 external reject request ",
		.pme_long_desc = "LSU1 external reject request ",
	},
	[ POWER6_PME_PM_DATA_FROM_L21_CYC ] = {
		.pme_name = "PM_DATA_FROM_L21_CYC",
		.pme_code = 0x400020,
		.pme_short_desc = "Load latency from private L2 other core",
		.pme_long_desc = "Load latency from private L2 other core",
	},
	[ POWER6_PME_PM_GXI_CYC_BUSY ] = {
		.pme_name = "PM_GXI_CYC_BUSY",
		.pme_code = 0x50386,
		.pme_short_desc = "Inbound GX bus utilizations (# of cycles in use)",
		.pme_long_desc = "Inbound GX bus utilizations (# of cycles in use)",
	},
	[ POWER6_PME_PM_MRK_LD_MISS_L1 ] = {
		.pme_name = "PM_MRK_LD_MISS_L1",
		.pme_code = 0x200056,
		.pme_short_desc = "Marked L1 D cache load misses",
		.pme_long_desc = "Marked L1 D cache load misses",
	},
	[ POWER6_PME_PM_L1_WRITE_CYC ] = {
		.pme_name = "PM_L1_WRITE_CYC",
		.pme_code = 0x430e2,
		.pme_short_desc = "Cycles writing to instruction L1",
		.pme_long_desc = "This signal is asserted each cycle a cache write is active.",
	},
	[ POWER6_PME_PM_LLA_CYC ] = {
		.pme_name = "PM_LLA_CYC",
		.pme_code = 0xc01e,
		.pme_short_desc = "Load Look Ahead Active",
		.pme_long_desc = "Load Look Ahead Active",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_L2MISS ] = {
		.pme_name = "PM_MRK_DATA_FROM_L2MISS",
		.pme_code = 0x103028,
		.pme_short_desc = "Marked data loaded missed L2",
		.pme_long_desc = "DL1 was reloaded from beyond L2 due to a marked demand load.",
	},
	[ POWER6_PME_PM_GCT_FULL_COUNT ] = {
		.pme_name = "PM_GCT_FULL_COUNT",
		.pme_code = 0x40087,
		.pme_short_desc = "Periods GCT full",
		.pme_long_desc = "The ISU sends a signal indicating the gct is full.",
	},
	[ POWER6_PME_PM_MEM_DP_RQ_LOC_GLOB ] = {
		.pme_name = "PM_MEM_DP_RQ_LOC_GLOB",
		.pme_code = 0x250230,
		.pme_short_desc = "Memory read queue marking cache line double pump state from local to global",
		.pme_long_desc = "Memory read queue marking cache line double pump state from local to global",
	},
	[ POWER6_PME_PM_DATA_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_DATA_FROM_RL2L3_SHR",
		.pme_code = 0x20005c,
		.pme_short_desc = "Data loaded from remote L2 or L3 shared",
		.pme_long_desc = "Data loaded from remote L2 or L3 shared",
	},
	[ POWER6_PME_PM_MRK_LSU_REJECT_UST ] = {
		.pme_name = "PM_MRK_LSU_REJECT_UST",
		.pme_code = 0x293034,
		.pme_short_desc = "Marked unaligned store reject",
		.pme_long_desc = "Marked unaligned store reject",
	},
	[ POWER6_PME_PM_MRK_VMX_PERMUTE_ISSUED ] = {
		.pme_name = "PM_MRK_VMX_PERMUTE_ISSUED",
		.pme_code = 0x7008e,
		.pme_short_desc = "Marked VMX instruction issued to permute",
		.pme_long_desc = "Marked VMX instruction issued to permute",
	},
	[ POWER6_PME_PM_MRK_PTEG_FROM_L21 ] = {
		.pme_name = "PM_MRK_PTEG_FROM_L21",
		.pme_code = 0x212040,
		.pme_short_desc = "Marked PTEG loaded from private L2 other core",
		.pme_long_desc = "Marked PTEG loaded from private L2 other core",
	},
	[ POWER6_PME_PM_THRD_GRP_CMPL_BOTH_CYC ] = {
		.pme_name = "PM_THRD_GRP_CMPL_BOTH_CYC",
		.pme_code = 0x200018,
		.pme_short_desc = "Cycles group completed by both threads",
		.pme_long_desc = "Cycles group completed by both threads",
	},
	[ POWER6_PME_PM_BR_MPRED ] = {
		.pme_name = "PM_BR_MPRED",
		.pme_code = 0x400052,
		.pme_short_desc = "Branches incorrectly predicted",
		.pme_long_desc = "Branches incorrectly predicted",
	},
	[ POWER6_PME_PM_LD_REQ_L2 ] = {
		.pme_name = "PM_LD_REQ_L2",
		.pme_code = 0x150730,
		.pme_short_desc = "L2 load requests ",
		.pme_long_desc = "L2 load requests ",
	},
	[ POWER6_PME_PM_FLUSH_ASYNC ] = {
		.pme_name = "PM_FLUSH_ASYNC",
		.pme_code = 0x220ca,
		.pme_short_desc = "Flush caused by asynchronous exception",
		.pme_long_desc = "Flush caused by asynchronous exception",
	},
	[ POWER6_PME_PM_HV_CYC ] = {
		.pme_name = "PM_HV_CYC",
		.pme_code = 0x200016,
		.pme_short_desc = "Hypervisor Cycles",
		.pme_long_desc = "Cycles when the processor is executing in Hypervisor (MSR[HV] = 1 and MSR[PR]=0)",
	},
	[ POWER6_PME_PM_LSU1_DERAT_MISS ] = {
		.pme_name = "PM_LSU1_DERAT_MISS",
		.pme_code = 0x910ae,
		.pme_short_desc = "LSU1 DERAT misses",
		.pme_long_desc = "A data request (load or store) from LSU Unit 1 missed the ERAT and resulted in an ERAT reload. Multiple instructions may miss the ERAT entry for the same 4K page, but only one reload will occur.",
	},
	[ POWER6_PME_PM_DPU_HELD_SMT ] = {
		.pme_name = "PM_DPU_HELD_SMT",
		.pme_code = 0x20082,
		.pme_short_desc = "DISP unit held due to SMT conflicts ",
		.pme_long_desc = "DISP unit held due to SMT conflicts ",
	},
	[ POWER6_PME_PM_MRK_LSU_FIN ] = {
		.pme_name = "PM_MRK_LSU_FIN",
		.pme_code = 0x40001a,
		.pme_short_desc = "Marked instruction LSU processing finished",
		.pme_long_desc = "One of the Load/Store Units finished a marked instruction. Instructions that finish may not necessary complete",
	},
	[ POWER6_PME_PM_MRK_DATA_FROM_RL2L3_SHR ] = {
		.pme_name = "PM_MRK_DATA_FROM_RL2L3_SHR",
		.pme_code = 0x20304c,
		.pme_short_desc = "Marked data loaded from remote L2 or L3 shared",
		.pme_long_desc = "Marked data loaded from remote L2 or L3 shared",
	},
	[ POWER6_PME_PM_LSU0_REJECT_STQ_FULL ] = {
		.pme_name = "PM_LSU0_REJECT_STQ_FULL",
		.pme_code = 0xa0080,
		.pme_short_desc = "LSU0 reject due to store queue full",
		.pme_long_desc = "LSU0 reject due to store queue full",
	},
	[ POWER6_PME_PM_MRK_DERAT_REF_4K ] = {
		.pme_name = "PM_MRK_DERAT_REF_4K",
		.pme_code = 0x282044,
		.pme_short_desc = "Marked DERAT reference for 4K page",
		.pme_long_desc = "Marked DERAT reference for 4K page",
	},
	[ POWER6_PME_PM_FPU_ISSUE_STALL_FPR ] = {
		.pme_name = "PM_FPU_ISSUE_STALL_FPR",
		.pme_code = 0x330e2,
		.pme_short_desc = "FPU issue stalled due to FPR dependencies",
		.pme_long_desc = "FPU issue stalled due to FPR dependencies",
	},
	[ POWER6_PME_PM_IFU_FIN ] = {
		.pme_name = "PM_IFU_FIN",
		.pme_code = 0x430e4,
		.pme_short_desc = "IFU finished an instruction",
		.pme_long_desc = "IFU finished an instruction",
	},
	[ POWER6_PME_PM_GXO_CYC_BUSY ] = {
		.pme_name = "PM_GXO_CYC_BUSY",
		.pme_code = 0x50380,
		.pme_short_desc = "Outbound GX bus utilizations (# of cycles in use)",
		.pme_long_desc = "Outbound GX bus utilizations (# of cycles in use)",
	}
};
#endif

