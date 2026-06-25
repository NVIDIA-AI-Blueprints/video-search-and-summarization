/*
 * Copyright (c) 2002-2006 Hewlett-Packard Development Company, L.P.
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
 *
 * This file is part of libpfm, a performance monitoring support library for
 * applications on Linux.
 */

/*
 * This file is generated automatically
 * !! DO NOT CHANGE !!
 */

static pme_ita2_entry_t itanium2_pe []={
#define PME_ITA2_ALAT_CAPACITY_MISS_ALL 0
{ "ALAT_CAPACITY_MISS_ALL", {0x30058}, 0xf0, 2, {0xf00007}, "ALAT Entry Replaced -- both integer and floating point instructions"},
#define PME_ITA2_ALAT_CAPACITY_MISS_FP 1
{ "ALAT_CAPACITY_MISS_FP", {0x20058}, 0xf0, 2, {0xf00007}, "ALAT Entry Replaced -- only floating point instructions"},
#define PME_ITA2_ALAT_CAPACITY_MISS_INT 2
{ "ALAT_CAPACITY_MISS_INT", {0x10058}, 0xf0, 2, {0xf00007}, "ALAT Entry Replaced -- only integer instructions"},
#define PME_ITA2_BACK_END_BUBBLE_ALL 3
{ "BACK_END_BUBBLE_ALL", {0x0}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe -- Front-end, RSE, EXE, FPU/L1D stall or a pipeline flush due to an exception/branch misprediction"},
#define PME_ITA2_BACK_END_BUBBLE_FE 4
{ "BACK_END_BUBBLE_FE", {0x10000}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe -- front-end"},
#define PME_ITA2_BACK_END_BUBBLE_L1D_FPU_RSE 5
{ "BACK_END_BUBBLE_L1D_FPU_RSE", {0x20000}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe -- L1D_FPU or RSE."},
#define PME_ITA2_BE_BR_MISPRED_DETAIL_ANY 6
{ "BE_BR_MISPRED_DETAIL_ANY", {0x61}, 0xf0, 1, {0xf00003}, "BE Branch Misprediction Detail -- any back-end (be) mispredictions"},
#define PME_ITA2_BE_BR_MISPRED_DETAIL_PFS 7
{ "BE_BR_MISPRED_DETAIL_PFS", {0x30061}, 0xf0, 1, {0xf00003}, "BE Branch Misprediction Detail -- only back-end pfs mispredictions for taken branches"},
#define PME_ITA2_BE_BR_MISPRED_DETAIL_ROT 8
{ "BE_BR_MISPRED_DETAIL_ROT", {0x20061}, 0xf0, 1, {0xf00003}, "BE Branch Misprediction Detail -- only back-end rotate mispredictions"},
#define PME_ITA2_BE_BR_MISPRED_DETAIL_STG 9
{ "BE_BR_MISPRED_DETAIL_STG", {0x10061}, 0xf0, 1, {0xf00003}, "BE Branch Misprediction Detail -- only back-end stage mispredictions"},
#define PME_ITA2_BE_EXE_BUBBLE_ALL 10
{ "BE_EXE_BUBBLE_ALL", {0x2}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe"},
#define PME_ITA2_BE_EXE_BUBBLE_ARCR 11
{ "BE_EXE_BUBBLE_ARCR", {0x40002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to AR or CR dependency"},
#define PME_ITA2_BE_EXE_BUBBLE_ARCR_PR_CANCEL_BANK 12
{ "BE_EXE_BUBBLE_ARCR_PR_CANCEL_BANK", {0x80002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- ARCR, PR, CANCEL or BANK_SWITCH"},
#define PME_ITA2_BE_EXE_BUBBLE_BANK_SWITCH 13
{ "BE_EXE_BUBBLE_BANK_SWITCH", {0x70002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to bank switching."},
#define PME_ITA2_BE_EXE_BUBBLE_CANCEL 14
{ "BE_EXE_BUBBLE_CANCEL", {0x60002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to a canceled load"},
#define PME_ITA2_BE_EXE_BUBBLE_FRALL 15
{ "BE_EXE_BUBBLE_FRALL", {0x20002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to FR/FR or FR/load dependency"},
#define PME_ITA2_BE_EXE_BUBBLE_GRALL 16
{ "BE_EXE_BUBBLE_GRALL", {0x10002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to GR/GR or GR/load dependency"},
#define PME_ITA2_BE_EXE_BUBBLE_GRGR 17
{ "BE_EXE_BUBBLE_GRGR", {0x50002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to GR/GR dependency"},
#define PME_ITA2_BE_EXE_BUBBLE_PR 18
{ "BE_EXE_BUBBLE_PR", {0x30002}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to PR dependency"},
#define PME_ITA2_BE_FLUSH_BUBBLE_ALL 19
{ "BE_FLUSH_BUBBLE_ALL", {0x4}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Flushes. -- Back-end was stalled due to either an exception/interruption or branch misprediction flush"},
#define PME_ITA2_BE_FLUSH_BUBBLE_BRU 20
{ "BE_FLUSH_BUBBLE_BRU", {0x10004}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Flushes. -- Back-end was stalled due to a branch misprediction flush"},
#define PME_ITA2_BE_FLUSH_BUBBLE_XPN 21
{ "BE_FLUSH_BUBBLE_XPN", {0x20004}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to Flushes. -- Back-end was stalled due to an exception/interruption flush"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_ALL 22
{ "BE_L1D_FPU_BUBBLE_ALL", {0xca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D or FPU"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_FPU 23
{ "BE_L1D_FPU_BUBBLE_FPU", {0x100ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by FPU."},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D 24
{ "BE_L1D_FPU_BUBBLE_L1D", {0x200ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D. This includes all stalls caused by the L1 pipeline (created in the L1D stage of the L1 pipeline which corresponds to the DET stage of the main pipe)."},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_DCS 25
{ "BE_L1D_FPU_BUBBLE_L1D_DCS", {0x800ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to DCS requiring a stall"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_DCURECIR 26
{ "BE_L1D_FPU_BUBBLE_L1D_DCURECIR", {0x400ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to DCU recirculating"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_FILLCONF 27
{ "BE_L1D_FPU_BUBBLE_L1D_FILLCONF", {0x700ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due a store in conflict with a returning fill."},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_FULLSTBUF 28
{ "BE_L1D_FPU_BUBBLE_L1D_FULLSTBUF", {0x300ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to store buffer being full"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_HPW 29
{ "BE_L1D_FPU_BUBBLE_L1D_HPW", {0x500ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to Hardware Page Walker"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_L2BPRESS 30
{ "BE_L1D_FPU_BUBBLE_L1D_L2BPRESS", {0x900ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to L2 Back Pressure"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_LDCHK 31
{ "BE_L1D_FPU_BUBBLE_L1D_LDCHK", {0xc00ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to architectural ordering conflict"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_LDCONF 32
{ "BE_L1D_FPU_BUBBLE_L1D_LDCONF", {0xb00ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to architectural ordering conflict"},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_NAT 33
{ "BE_L1D_FPU_BUBBLE_L1D_NAT", {0xd00ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to L1D data return needing recirculated NaT generation."},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_NATCONF 34
{ "BE_L1D_FPU_BUBBLE_L1D_NATCONF", {0xf00ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to ld8.fill conflict with st8.spill not written to unat."},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_STBUFRECIR 35
{ "BE_L1D_FPU_BUBBLE_L1D_STBUFRECIR", {0xe00ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to store buffer cancel needing recirculate."},
#define PME_ITA2_BE_L1D_FPU_BUBBLE_L1D_TLB 36
{ "BE_L1D_FPU_BUBBLE_L1D_TLB", {0xa00ca}, 0xf0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to L2DTLB to L1DTLB transfer"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_ALL 37
{ "BE_LOST_BW_DUE_TO_FE_ALL", {0x72}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- count regardless of cause"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_BI 38
{ "BE_LOST_BW_DUE_TO_FE_BI", {0x90072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch initialization stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_BRQ 39
{ "BE_LOST_BW_DUE_TO_FE_BRQ", {0xa0072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch retirement queue stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_BR_ILOCK 40
{ "BE_LOST_BW_DUE_TO_FE_BR_ILOCK", {0xc0072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch interlock stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_BUBBLE 41
{ "BE_LOST_BW_DUE_TO_FE_BUBBLE", {0xd0072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch resteer bubble stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_FEFLUSH 42
{ "BE_LOST_BW_DUE_TO_FE_FEFLUSH", {0x10072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by a front-end flush"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_FILL_RECIRC 43
{ "BE_LOST_BW_DUE_TO_FE_FILL_RECIRC", {0x80072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by a recirculate for a cache line fill operation"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_IBFULL 44
{ "BE_LOST_BW_DUE_TO_FE_IBFULL", {0x50072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- (* meaningless for this event *)"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_IMISS 45
{ "BE_LOST_BW_DUE_TO_FE_IMISS", {0x60072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by instruction cache miss stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_PLP 46
{ "BE_LOST_BW_DUE_TO_FE_PLP", {0xb0072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by perfect loop prediction stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_TLBMISS 47
{ "BE_LOST_BW_DUE_TO_FE_TLBMISS", {0x70072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by TLB stall"},
#define PME_ITA2_BE_LOST_BW_DUE_TO_FE_UNREACHED 48
{ "BE_LOST_BW_DUE_TO_FE_UNREACHED", {0x40072}, 0xf0, 2, {0xf00000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by unreachable bundle"},
#define PME_ITA2_BE_RSE_BUBBLE_ALL 49
{ "BE_RSE_BUBBLE_ALL", {0x1}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE"},
#define PME_ITA2_BE_RSE_BUBBLE_AR_DEP 50
{ "BE_RSE_BUBBLE_AR_DEP", {0x20001}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to AR dependencies"},
#define PME_ITA2_BE_RSE_BUBBLE_BANK_SWITCH 51
{ "BE_RSE_BUBBLE_BANK_SWITCH", {0x10001}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to bank switching"},
#define PME_ITA2_BE_RSE_BUBBLE_LOADRS 52
{ "BE_RSE_BUBBLE_LOADRS", {0x50001}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to loadrs calculations"},
#define PME_ITA2_BE_RSE_BUBBLE_OVERFLOW 53
{ "BE_RSE_BUBBLE_OVERFLOW", {0x30001}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to need to spill"},
#define PME_ITA2_BE_RSE_BUBBLE_UNDERFLOW 54
{ "BE_RSE_BUBBLE_UNDERFLOW", {0x40001}, 0xf0, 1, {0xf00000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to need to fill"},
#define PME_ITA2_BRANCH_EVENT 55
{ "BRANCH_EVENT", {0x111}, 0xf0, 1, {0xf00003}, "Branch Event Captured"},
#define PME_ITA2_BR_MISPRED_DETAIL_ALL_ALL_PRED 56
{ "BR_MISPRED_DETAIL_ALL_ALL_PRED", {0x5b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- All branch types regardless of prediction result"},
#define PME_ITA2_BR_MISPRED_DETAIL_ALL_CORRECT_PRED 57
{ "BR_MISPRED_DETAIL_ALL_CORRECT_PRED", {0x1005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- All branch types, correctly predicted branches (outcome and target)"},
#define PME_ITA2_BR_MISPRED_DETAIL_ALL_WRONG_PATH 58
{ "BR_MISPRED_DETAIL_ALL_WRONG_PATH", {0x2005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- All branch types, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL_ALL_WRONG_TARGET 59
{ "BR_MISPRED_DETAIL_ALL_WRONG_TARGET", {0x3005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- All branch types, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_BR_MISPRED_DETAIL_IPREL_ALL_PRED 60
{ "BR_MISPRED_DETAIL_IPREL_ALL_PRED", {0x4005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only IP relative branches, regardless of prediction result"},
#define PME_ITA2_BR_MISPRED_DETAIL_IPREL_CORRECT_PRED 61
{ "BR_MISPRED_DETAIL_IPREL_CORRECT_PRED", {0x5005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only IP relative branches, correctly predicted branches (outcome and target)"},
#define PME_ITA2_BR_MISPRED_DETAIL_IPREL_WRONG_PATH 62
{ "BR_MISPRED_DETAIL_IPREL_WRONG_PATH", {0x6005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only IP relative branches, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL_IPREL_WRONG_TARGET 63
{ "BR_MISPRED_DETAIL_IPREL_WRONG_TARGET", {0x7005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only IP relative branches, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_BR_MISPRED_DETAIL_NTRETIND_ALL_PRED 64
{ "BR_MISPRED_DETAIL_NTRETIND_ALL_PRED", {0xc005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, regardless of prediction result"},
#define PME_ITA2_BR_MISPRED_DETAIL_NTRETIND_CORRECT_PRED 65
{ "BR_MISPRED_DETAIL_NTRETIND_CORRECT_PRED", {0xd005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, correctly predicted branches (outcome and target)"},
#define PME_ITA2_BR_MISPRED_DETAIL_NTRETIND_WRONG_PATH 66
{ "BR_MISPRED_DETAIL_NTRETIND_WRONG_PATH", {0xe005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL_NTRETIND_WRONG_TARGET 67
{ "BR_MISPRED_DETAIL_NTRETIND_WRONG_TARGET", {0xf005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_BR_MISPRED_DETAIL_RETURN_ALL_PRED 68
{ "BR_MISPRED_DETAIL_RETURN_ALL_PRED", {0x8005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only return type branches, regardless of prediction result"},
#define PME_ITA2_BR_MISPRED_DETAIL_RETURN_CORRECT_PRED 69
{ "BR_MISPRED_DETAIL_RETURN_CORRECT_PRED", {0x9005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only return type branches, correctly predicted branches (outcome and target)"},
#define PME_ITA2_BR_MISPRED_DETAIL_RETURN_WRONG_PATH 70
{ "BR_MISPRED_DETAIL_RETURN_WRONG_PATH", {0xa005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only return type branches, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL_RETURN_WRONG_TARGET 71
{ "BR_MISPRED_DETAIL_RETURN_WRONG_TARGET", {0xb005b}, 0xf0, 3, {0xf00003}, "FE Branch Mispredict Detail -- Only return type branches, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_BR_MISPRED_DETAIL2_ALL_ALL_UNKNOWN_PRED 72
{ "BR_MISPRED_DETAIL2_ALL_ALL_UNKNOWN_PRED", {0x68}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- All branch types, branches with unknown path prediction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_CORRECT_PRED 73
{ "BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_CORRECT_PRED", {0x10068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- All branch types, branches with unknown path prediction and correctly predicted branch (outcome & target)"},
#define PME_ITA2_BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_WRONG_PATH 74
{ "BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_WRONG_PATH", {0x20068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- All branch types, branches with unknown path prediction and wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_IPREL_ALL_UNKNOWN_PRED 75
{ "BR_MISPRED_DETAIL2_IPREL_ALL_UNKNOWN_PRED", {0x40068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only IP relative branches, branches with unknown path prediction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_CORRECT_PRED 76
{ "BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_CORRECT_PRED", {0x50068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only IP relative branches, branches with unknown path prediction and correct predicted branch (outcome & target)"},
#define PME_ITA2_BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_WRONG_PATH 77
{ "BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_WRONG_PATH", {0x60068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only IP relative branches, branches with unknown path prediction and wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_NRETIND_ALL_UNKNOWN_PRED 78
{ "BR_MISPRED_DETAIL2_NRETIND_ALL_UNKNOWN_PRED", {0xc0068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only non-return indirect branches, branches with unknown path prediction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_CORRECT_PRED 79
{ "BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_CORRECT_PRED", {0xd0068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only non-return indirect branches, branches with unknown path prediction and correct predicted branch (outcome & target)"},
#define PME_ITA2_BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_WRONG_PATH 80
{ "BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_WRONG_PATH", {0xe0068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only non-return indirect branches, branches with unknown path prediction and wrong branch direction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_RETURN_ALL_UNKNOWN_PRED 81
{ "BR_MISPRED_DETAIL2_RETURN_ALL_UNKNOWN_PRED", {0x80068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only return type branches, branches with unknown path prediction"},
#define PME_ITA2_BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_CORRECT_PRED 82
{ "BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_CORRECT_PRED", {0x90068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only return type branches, branches with unknown path prediction and correct predicted branch (outcome & target)"},
#define PME_ITA2_BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_WRONG_PATH 83
{ "BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_WRONG_PATH", {0xa0068}, 0xf0, 2, {0xf00003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only return type branches, branches with unknown path prediction and wrong branch direction"},
#define PME_ITA2_BR_PATH_PRED_ALL_MISPRED_NOTTAKEN 84
{ "BR_PATH_PRED_ALL_MISPRED_NOTTAKEN", {0x54}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- All branch types, incorrectly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_ALL_MISPRED_TAKEN 85
{ "BR_PATH_PRED_ALL_MISPRED_TAKEN", {0x10054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- All branch types, incorrectly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_ALL_OKPRED_NOTTAKEN 86
{ "BR_PATH_PRED_ALL_OKPRED_NOTTAKEN", {0x20054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- All branch types, correctly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_ALL_OKPRED_TAKEN 87
{ "BR_PATH_PRED_ALL_OKPRED_TAKEN", {0x30054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- All branch types, correctly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_IPREL_MISPRED_NOTTAKEN 88
{ "BR_PATH_PRED_IPREL_MISPRED_NOTTAKEN", {0x40054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only IP relative branches, incorrectly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_IPREL_MISPRED_TAKEN 89
{ "BR_PATH_PRED_IPREL_MISPRED_TAKEN", {0x50054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only IP relative branches, incorrectly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_IPREL_OKPRED_NOTTAKEN 90
{ "BR_PATH_PRED_IPREL_OKPRED_NOTTAKEN", {0x60054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only IP relative branches, correctly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_IPREL_OKPRED_TAKEN 91
{ "BR_PATH_PRED_IPREL_OKPRED_TAKEN", {0x70054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only IP relative branches, correctly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_NRETIND_MISPRED_NOTTAKEN 92
{ "BR_PATH_PRED_NRETIND_MISPRED_NOTTAKEN", {0xc0054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, incorrectly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_NRETIND_MISPRED_TAKEN 93
{ "BR_PATH_PRED_NRETIND_MISPRED_TAKEN", {0xd0054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, incorrectly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_NRETIND_OKPRED_NOTTAKEN 94
{ "BR_PATH_PRED_NRETIND_OKPRED_NOTTAKEN", {0xe0054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, correctly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_NRETIND_OKPRED_TAKEN 95
{ "BR_PATH_PRED_NRETIND_OKPRED_TAKEN", {0xf0054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, correctly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_RETURN_MISPRED_NOTTAKEN 96
{ "BR_PATH_PRED_RETURN_MISPRED_NOTTAKEN", {0x80054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only return type branches, incorrectly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_RETURN_MISPRED_TAKEN 97
{ "BR_PATH_PRED_RETURN_MISPRED_TAKEN", {0x90054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only return type branches, incorrectly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED_RETURN_OKPRED_NOTTAKEN 98
{ "BR_PATH_PRED_RETURN_OKPRED_NOTTAKEN", {0xa0054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only return type branches, correctly predicted path and not taken branch"},
#define PME_ITA2_BR_PATH_PRED_RETURN_OKPRED_TAKEN 99
{ "BR_PATH_PRED_RETURN_OKPRED_TAKEN", {0xb0054}, 0xf0, 3, {0xf00003}, "FE Branch Path Prediction Detail -- Only return type branches, correctly predicted path and taken branch"},
#define PME_ITA2_BR_PATH_PRED2_ALL_UNKNOWNPRED_NOTTAKEN 100
{ "BR_PATH_PRED2_ALL_UNKNOWNPRED_NOTTAKEN", {0x6a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- All branch types, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_ALL_UNKNOWNPRED_TAKEN 101
{ "BR_PATH_PRED2_ALL_UNKNOWNPRED_TAKEN", {0x1006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- All branch types, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_IPREL_UNKNOWNPRED_NOTTAKEN 102
{ "BR_PATH_PRED2_IPREL_UNKNOWNPRED_NOTTAKEN", {0x4006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only IP relative branches, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_IPREL_UNKNOWNPRED_TAKEN 103
{ "BR_PATH_PRED2_IPREL_UNKNOWNPRED_TAKEN", {0x5006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only IP relative branches, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_NRETIND_UNKNOWNPRED_NOTTAKEN 104
{ "BR_PATH_PRED2_NRETIND_UNKNOWNPRED_NOTTAKEN", {0xc006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only non-return indirect branches, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_NRETIND_UNKNOWNPRED_TAKEN 105
{ "BR_PATH_PRED2_NRETIND_UNKNOWNPRED_TAKEN", {0xd006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only non-return indirect branches, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_RETURN_UNKNOWNPRED_NOTTAKEN 106
{ "BR_PATH_PRED2_RETURN_UNKNOWNPRED_NOTTAKEN", {0x8006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only return type branches, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_ITA2_BR_PATH_PRED2_RETURN_UNKNOWNPRED_TAKEN 107
{ "BR_PATH_PRED2_RETURN_UNKNOWNPRED_TAKEN", {0x9006a}, 0xf0, 2, {0xf00003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only return type branches, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_ITA2_BUS_ALL_ANY 108
{ "BUS_ALL_ANY", {0x30087}, 0xf0, 1, {0xf00000}, "Bus Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_ALL_IO 109
{ "BUS_ALL_IO", {0x10087}, 0xf0, 1, {0xf00000}, "Bus Transactions -- non-CPU priority agents"},
#define PME_ITA2_BUS_ALL_SELF 110
{ "BUS_ALL_SELF", {0x20087}, 0xf0, 1, {0xf00000}, "Bus Transactions -- local processor"},
#define PME_ITA2_BUS_BACKSNP_REQ_THIS 111
{ "BUS_BACKSNP_REQ_THIS", {0x1008e}, 0xf0, 1, {0xf00000}, "Bus Back Snoop Requests -- Counts the number of bus back snoop me requests"},
#define PME_ITA2_BUS_BRQ_LIVE_REQ_HI 112
{ "BUS_BRQ_LIVE_REQ_HI", {0x9c}, 0xf0, 2, {0xf00000}, "BRQ Live Requests (upper 2 bits)"},
#define PME_ITA2_BUS_BRQ_LIVE_REQ_LO 113
{ "BUS_BRQ_LIVE_REQ_LO", {0x9b}, 0xf0, 7, {0xf00000}, "BRQ Live Requests (lower 3 bits)"},
#define PME_ITA2_BUS_BRQ_REQ_INSERTED 114
{ "BUS_BRQ_REQ_INSERTED", {0x9d}, 0xf0, 1, {0xf00000}, "BRQ Requests Inserted"},
#define PME_ITA2_BUS_DATA_CYCLE 115
{ "BUS_DATA_CYCLE", {0x88}, 0xf0, 1, {0xf00000}, "Valid Data Cycle on the Bus"},
#define PME_ITA2_BUS_HITM 116
{ "BUS_HITM", {0x84}, 0xf0, 1, {0xf00000}, "Bus Hit Modified Line Transactions"},
#define PME_ITA2_BUS_IO_ANY 117
{ "BUS_IO_ANY", {0x30090}, 0xf0, 1, {0xf00000}, "IA-32 Compatible IO Bus Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_IO_IO 118
{ "BUS_IO_IO", {0x10090}, 0xf0, 1, {0xf00000}, "IA-32 Compatible IO Bus Transactions -- non-CPU priority agents"},
#define PME_ITA2_BUS_IO_SELF 119
{ "BUS_IO_SELF", {0x20090}, 0xf0, 1, {0xf00000}, "IA-32 Compatible IO Bus Transactions -- local processor"},
#define PME_ITA2_BUS_IOQ_LIVE_REQ_HI 120
{ "BUS_IOQ_LIVE_REQ_HI", {0x98}, 0xf0, 2, {0xf00000}, "Inorder Bus Queue Requests (upper 2 bits)"},
#define PME_ITA2_BUS_IOQ_LIVE_REQ_LO 121
{ "BUS_IOQ_LIVE_REQ_LO", {0x97}, 0xf0, 3, {0xf00000}, "Inorder Bus Queue Requests (lower2 bitst)"},
#define PME_ITA2_BUS_LOCK_ANY 122
{ "BUS_LOCK_ANY", {0x30093}, 0xf0, 1, {0xf00000}, "IA-32 Compatible Bus Lock Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_LOCK_SELF 123
{ "BUS_LOCK_SELF", {0x20093}, 0xf0, 1, {0xf00000}, "IA-32 Compatible Bus Lock Transactions -- local processor"},
#define PME_ITA2_BUS_MEMORY_ALL_ANY 124
{ "BUS_MEMORY_ALL_ANY", {0xf008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- All bus transactions from CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEMORY_ALL_IO 125
{ "BUS_MEMORY_ALL_IO", {0xd008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- All bus transactions from non-CPU priority agents"},
#define PME_ITA2_BUS_MEMORY_ALL_SELF 126
{ "BUS_MEMORY_ALL_SELF", {0xe008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- All bus transactions from local processor"},
#define PME_ITA2_BUS_MEMORY_EQ_128BYTE_ANY 127
{ "BUS_MEMORY_EQ_128BYTE_ANY", {0x7008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- number of full cache line transactions (BRL, BRIL, BWL) from CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEMORY_EQ_128BYTE_IO 128
{ "BUS_MEMORY_EQ_128BYTE_IO", {0x5008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- number of full cache line transactions (BRL, BRIL, BWL) from non-CPU priority agents"},
#define PME_ITA2_BUS_MEMORY_EQ_128BYTE_SELF 129
{ "BUS_MEMORY_EQ_128BYTE_SELF", {0x6008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- number of full cache line transactions (BRL, BRIL, BWL) from local processor"},
#define PME_ITA2_BUS_MEMORY_LT_128BYTE_ANY 130
{ "BUS_MEMORY_LT_128BYTE_ANY", {0xb008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP) CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEMORY_LT_128BYTE_IO 131
{ "BUS_MEMORY_LT_128BYTE_IO", {0x9008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP) from non-CPU priority agents"},
#define PME_ITA2_BUS_MEMORY_LT_128BYTE_SELF 132
{ "BUS_MEMORY_LT_128BYTE_SELF", {0xa008a}, 0xf0, 1, {0xf00000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP) local processor"},
#define PME_ITA2_BUS_MEM_READ_ALL_ANY 133
{ "BUS_MEM_READ_ALL_ANY", {0xf008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEM_READ_ALL_IO 134
{ "BUS_MEM_READ_ALL_IO", {0xd008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from non-CPU priority agents"},
#define PME_ITA2_BUS_MEM_READ_ALL_SELF 135
{ "BUS_MEM_READ_ALL_SELF", {0xe008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from local processor"},
#define PME_ITA2_BUS_MEM_READ_BIL_ANY 136
{ "BUS_MEM_READ_BIL_ANY", {0x3008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEM_READ_BIL_IO 137
{ "BUS_MEM_READ_BIL_IO", {0x1008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from non-CPU priority agents"},
#define PME_ITA2_BUS_MEM_READ_BIL_SELF 138
{ "BUS_MEM_READ_BIL_SELF", {0x2008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from local processor"},
#define PME_ITA2_BUS_MEM_READ_BRIL_ANY 139
{ "BUS_MEM_READ_BRIL_ANY", {0xb008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEM_READ_BRIL_IO 140
{ "BUS_MEM_READ_BRIL_IO", {0x9008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from non-CPU priority agents"},
#define PME_ITA2_BUS_MEM_READ_BRIL_SELF 141
{ "BUS_MEM_READ_BRIL_SELF", {0xa008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from local processor"},
#define PME_ITA2_BUS_MEM_READ_BRL_ANY 142
{ "BUS_MEM_READ_BRL_ANY", {0x7008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_MEM_READ_BRL_IO 143
{ "BUS_MEM_READ_BRL_IO", {0x5008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from non-CPU priority agents"},
#define PME_ITA2_BUS_MEM_READ_BRL_SELF 144
{ "BUS_MEM_READ_BRL_SELF", {0x6008b}, 0xf0, 1, {0xf00000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from local processor"},
#define PME_ITA2_BUS_MEM_READ_OUT_HI 145
{ "BUS_MEM_READ_OUT_HI", {0x94}, 0xf0, 2, {0xf00000}, "Outstanding Memory Read Transactions (upper 2 bits)"},
#define PME_ITA2_BUS_MEM_READ_OUT_LO 146
{ "BUS_MEM_READ_OUT_LO", {0x95}, 0xf0, 7, {0xf00000}, "Outstanding Memory Read Transactions (lower 3 bits)"},
#define PME_ITA2_BUS_OOQ_LIVE_REQ_HI 147
{ "BUS_OOQ_LIVE_REQ_HI", {0x9a}, 0xf0, 2, {0xf00000}, "Out-of-order Bus Queue Requests (upper 2 bits)"},
#define PME_ITA2_BUS_OOQ_LIVE_REQ_LO 148
{ "BUS_OOQ_LIVE_REQ_LO", {0x99}, 0xf0, 7, {0xf00000}, "Out-of-order Bus Queue Requests (lower 3 bits)"},
#define PME_ITA2_BUS_RD_DATA_ANY 149
{ "BUS_RD_DATA_ANY", {0x3008c}, 0xf0, 1, {0xf00000}, "Bus Read Data Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_RD_DATA_IO 150
{ "BUS_RD_DATA_IO", {0x1008c}, 0xf0, 1, {0xf00000}, "Bus Read Data Transactions -- non-CPU priority agents"},
#define PME_ITA2_BUS_RD_DATA_SELF 151
{ "BUS_RD_DATA_SELF", {0x2008c}, 0xf0, 1, {0xf00000}, "Bus Read Data Transactions -- local processor"},
#define PME_ITA2_BUS_RD_HIT 152
{ "BUS_RD_HIT", {0x80}, 0xf0, 1, {0xf00000}, "Bus Read Hit Clean Non-local Cache Transactions"},
#define PME_ITA2_BUS_RD_HITM 153
{ "BUS_RD_HITM", {0x81}, 0xf0, 1, {0xf00000}, "Bus Read Hit Modified Non-local Cache Transactions"},
#define PME_ITA2_BUS_RD_INVAL_ALL_HITM 154
{ "BUS_RD_INVAL_ALL_HITM", {0x83}, 0xf0, 1, {0xf00000}, "Bus BRIL Burst Transaction Results in HITM"},
#define PME_ITA2_BUS_RD_INVAL_HITM 155
{ "BUS_RD_INVAL_HITM", {0x82}, 0xf0, 1, {0xf00000}, "Bus BIL Transaction Results in HITM"},
#define PME_ITA2_BUS_RD_IO_ANY 156
{ "BUS_RD_IO_ANY", {0x30091}, 0xf0, 1, {0xf00000}, "IA-32 Compatible IO Read Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_RD_IO_IO 157
{ "BUS_RD_IO_IO", {0x10091}, 0xf0, 1, {0xf00000}, "IA-32 Compatible IO Read Transactions -- non-CPU priority agents"},
#define PME_ITA2_BUS_RD_IO_SELF 158
{ "BUS_RD_IO_SELF", {0x20091}, 0xf0, 1, {0xf00000}, "IA-32 Compatible IO Read Transactions -- local processor"},
#define PME_ITA2_BUS_RD_PRTL_ANY 159
{ "BUS_RD_PRTL_ANY", {0x3008d}, 0xf0, 1, {0xf00000}, "Bus Read Partial Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_RD_PRTL_IO 160
{ "BUS_RD_PRTL_IO", {0x1008d}, 0xf0, 1, {0xf00000}, "Bus Read Partial Transactions -- non-CPU priority agents"},
#define PME_ITA2_BUS_RD_PRTL_SELF 161
{ "BUS_RD_PRTL_SELF", {0x2008d}, 0xf0, 1, {0xf00000}, "Bus Read Partial Transactions -- local processor"},
#define PME_ITA2_BUS_SNOOPQ_REQ 162
{ "BUS_SNOOPQ_REQ", {0x96}, 0xf0, 7, {0xf00000}, "Bus Snoop Queue Requests"},
#define PME_ITA2_BUS_SNOOPS_ANY 163
{ "BUS_SNOOPS_ANY", {0x30086}, 0xf0, 1, {0xf00000}, "Bus Snoops Total -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_SNOOPS_IO 164
{ "BUS_SNOOPS_IO", {0x10086}, 0xf0, 1, {0xf00000}, "Bus Snoops Total -- non-CPU priority agents"},
#define PME_ITA2_BUS_SNOOPS_SELF 165
{ "BUS_SNOOPS_SELF", {0x20086}, 0xf0, 1, {0xf00000}, "Bus Snoops Total -- local processor"},
#define PME_ITA2_BUS_SNOOPS_HITM_ANY 166
{ "BUS_SNOOPS_HITM_ANY", {0x30085}, 0xf0, 1, {0xf00000}, "Bus Snoops HIT Modified Cache Line -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_SNOOPS_HITM_SELF 167
{ "BUS_SNOOPS_HITM_SELF", {0x20085}, 0xf0, 1, {0xf00000}, "Bus Snoops HIT Modified Cache Line -- local processor"},
#define PME_ITA2_BUS_SNOOP_STALL_CYCLES_ANY 168
{ "BUS_SNOOP_STALL_CYCLES_ANY", {0x3008f}, 0xf0, 1, {0xf00000}, "Bus Snoop Stall Cycles (from any agent) -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_SNOOP_STALL_CYCLES_SELF 169
{ "BUS_SNOOP_STALL_CYCLES_SELF", {0x2008f}, 0xf0, 1, {0xf00000}, "Bus Snoop Stall Cycles (from any agent) -- local processor"},
#define PME_ITA2_BUS_WR_WB_ALL_ANY 170
{ "BUS_WR_WB_ALL_ANY", {0xf0092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_BUS_WR_WB_ALL_IO 171
{ "BUS_WR_WB_ALL_IO", {0xd0092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- non-CPU priority agents"},
#define PME_ITA2_BUS_WR_WB_ALL_SELF 172
{ "BUS_WR_WB_ALL_SELF", {0xe0092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- local processor"},
#define PME_ITA2_BUS_WR_WB_CCASTOUT_ANY 173
{ "BUS_WR_WB_CCASTOUT_ANY", {0xb0092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- CPU or non-CPU (all transactions)/Only 0-byte transactions with write back attribute (clean cast outs) will be counted"},
#define PME_ITA2_BUS_WR_WB_CCASTOUT_SELF 174
{ "BUS_WR_WB_CCASTOUT_SELF", {0xa0092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- local processor/Only 0-byte transactions with write back attribute (clean cast outs) will be counted"},
#define PME_ITA2_BUS_WR_WB_EQ_128BYTE_ANY 175
{ "BUS_WR_WB_EQ_128BYTE_ANY", {0x70092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- CPU or non-CPU (all transactions)./Only cache line transactions with write back or write coalesce attributes will be counted."},
#define PME_ITA2_BUS_WR_WB_EQ_128BYTE_IO 176
{ "BUS_WR_WB_EQ_128BYTE_IO", {0x50092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- non-CPU priority agents/Only cache line transactions with write back or write coalesce attributes will be counted."},
#define PME_ITA2_BUS_WR_WB_EQ_128BYTE_SELF 177
{ "BUS_WR_WB_EQ_128BYTE_SELF", {0x60092}, 0xf0, 1, {0xf00000}, "Bus Write Back Transactions -- local processor/Only cache line transactions with write back or write coalesce attributes will be counted."},
#define PME_ITA2_CPU_CPL_CHANGES 178
{ "CPU_CPL_CHANGES", {0x13}, 0xf0, 1, {0xf00000}, "Privilege Level Changes"},
#define PME_ITA2_CPU_CYCLES 179
{ "CPU_CYCLES", {0x12}, 0xf0, 1, {0xf00000}, "CPU Cycles"},
#define PME_ITA2_DATA_DEBUG_REGISTER_FAULT 180
{ "DATA_DEBUG_REGISTER_FAULT", {0x52}, 0xf0, 1, {0xf00000}, "Fault Due to Data Debug Reg. Match to Load/Store Instruction"},
#define PME_ITA2_DATA_DEBUG_REGISTER_MATCHES 181
{ "DATA_DEBUG_REGISTER_MATCHES", {0xc6}, 0xf0, 1, {0xf00007}, "Data Debug Register Matches Data Address of Memory Reference."},
#define PME_ITA2_DATA_EAR_ALAT 182
{ "DATA_EAR_ALAT", {0x6c8}, 0xf0, 1, {0xf00007}, "Data EAR ALAT"},
#define PME_ITA2_DATA_EAR_CACHE_LAT1024 183
{ "DATA_EAR_CACHE_LAT1024", {0x805c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 1024 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT128 184
{ "DATA_EAR_CACHE_LAT128", {0x505c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 128 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT16 185
{ "DATA_EAR_CACHE_LAT16", {0x205c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 16 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT2048 186
{ "DATA_EAR_CACHE_LAT2048", {0x905c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 2048 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT256 187
{ "DATA_EAR_CACHE_LAT256", {0x605c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 256 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT32 188
{ "DATA_EAR_CACHE_LAT32", {0x305c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 32 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT4 189
{ "DATA_EAR_CACHE_LAT4", {0x5c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 4 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT4096 190
{ "DATA_EAR_CACHE_LAT4096", {0xa05c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 4096 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT512 191
{ "DATA_EAR_CACHE_LAT512", {0x705c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 512 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT64 192
{ "DATA_EAR_CACHE_LAT64", {0x405c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 64 Cycles"},
#define PME_ITA2_DATA_EAR_CACHE_LAT8 193
{ "DATA_EAR_CACHE_LAT8", {0x105c8}, 0xf0, 1, {0xf00007}, "Data EAR Cache -- >= 8 Cycles"},
#define PME_ITA2_DATA_EAR_EVENTS 194
{ "DATA_EAR_EVENTS", {0xc8}, 0xf0, 1, {0xf00007}, "L1 Data Cache EAR Events"},
#define PME_ITA2_DATA_EAR_TLB_ALL 195
{ "DATA_EAR_TLB_ALL", {0xe04c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- All L1 DTLB Misses"},
#define PME_ITA2_DATA_EAR_TLB_FAULT 196
{ "DATA_EAR_TLB_FAULT", {0x804c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- DTLB Misses which produce a software fault"},
#define PME_ITA2_DATA_EAR_TLB_L2DTLB 197
{ "DATA_EAR_TLB_L2DTLB", {0x204c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- L1 DTLB Misses which hit L2 DTLB"},
#define PME_ITA2_DATA_EAR_TLB_L2DTLB_OR_FAULT 198
{ "DATA_EAR_TLB_L2DTLB_OR_FAULT", {0xa04c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- L1 DTLB Misses which hit L2 DTLB or produce a software fault"},
#define PME_ITA2_DATA_EAR_TLB_L2DTLB_OR_VHPT 199
{ "DATA_EAR_TLB_L2DTLB_OR_VHPT", {0x604c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- L1 DTLB Misses which hit L2 DTLB or VHPT"},
#define PME_ITA2_DATA_EAR_TLB_VHPT 200
{ "DATA_EAR_TLB_VHPT", {0x404c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- L1 DTLB Misses which hit VHPT"},
#define PME_ITA2_DATA_EAR_TLB_VHPT_OR_FAULT 201
{ "DATA_EAR_TLB_VHPT_OR_FAULT", {0xc04c8}, 0xf0, 1, {0xf00007}, "Data EAR TLB -- L1 DTLB Misses which hit VHPT or produce a software fault"},
#define PME_ITA2_DATA_REFERENCES_SET0 202
{ "DATA_REFERENCES_SET0", {0xc3}, 0xf0, 4, {0x5010007}, "Data Memory References Issued to Memory Pipeline"},
#define PME_ITA2_DATA_REFERENCES_SET1 203
{ "DATA_REFERENCES_SET1", {0xc5}, 0xf0, 4, {0x5110007}, "Data Memory References Issued to Memory Pipeline"},
#define PME_ITA2_DISP_STALLED 204
{ "DISP_STALLED", {0x49}, 0xf0, 1, {0xf00000}, "Number of Cycles Dispersal Stalled"},
#define PME_ITA2_DTLB_INSERTS_HPW 205
{ "DTLB_INSERTS_HPW", {0xc9}, 0xf0, 4, {0xf00007}, "Hardware Page Walker Installs to DTLB"},
#define PME_ITA2_DTLB_INSERTS_HPW_RETIRED 206
{ "DTLB_INSERTS_HPW_RETIRED", {0x2c}, 0xf0, 4, {0xf00007}, "VHPT Entries Inserted into DTLB by the Hardware Page Walker"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL_ALL_PRED 207
{ "ENCBR_MISPRED_DETAIL_ALL_ALL_PRED", {0x63}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- All encoded branches regardless of prediction result"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL_CORRECT_PRED 208
{ "ENCBR_MISPRED_DETAIL_ALL_CORRECT_PRED", {0x10063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- All encoded branches, correctly predicted branches (outcome and target)"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL_WRONG_PATH 209
{ "ENCBR_MISPRED_DETAIL_ALL_WRONG_PATH", {0x20063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- All encoded branches, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL_WRONG_TARGET 210
{ "ENCBR_MISPRED_DETAIL_ALL_WRONG_TARGET", {0x30063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- All encoded branches, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL2_ALL_PRED 211
{ "ENCBR_MISPRED_DETAIL_ALL2_ALL_PRED", {0xc0063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, regardless of prediction result"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL2_CORRECT_PRED 212
{ "ENCBR_MISPRED_DETAIL_ALL2_CORRECT_PRED", {0xd0063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, correctly predicted branches (outcome and target)"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL2_WRONG_PATH 213
{ "ENCBR_MISPRED_DETAIL_ALL2_WRONG_PATH", {0xe0063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_ALL2_WRONG_TARGET 214
{ "ENCBR_MISPRED_DETAIL_ALL2_WRONG_TARGET", {0xf0063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_OVERSUB_ALL_PRED 215
{ "ENCBR_MISPRED_DETAIL_OVERSUB_ALL_PRED", {0x80063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only return type branches, regardless of prediction result"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_OVERSUB_CORRECT_PRED 216
{ "ENCBR_MISPRED_DETAIL_OVERSUB_CORRECT_PRED", {0x90063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only return type branches, correctly predicted branches (outcome and target)"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_PATH 217
{ "ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_PATH", {0xa0063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only return type branches, mispredicted branches due to wrong branch direction"},
#define PME_ITA2_ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_TARGET 218
{ "ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_TARGET", {0xb0063}, 0xf0, 3, {0xf00003}, "Number of Encoded Branches Retired -- Only return type branches, mispredicted branches due to wrong target for taken branches"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_ALL 219
{ "EXTERN_DP_PINS_0_TO_3_ALL", {0xf009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0 220
{ "EXTERN_DP_PINS_0_TO_3_PIN0", {0x1009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN1 221
{ "EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN1", {0x3009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 or pin1 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN1_OR_PIN2 222
{ "EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN1_OR_PIN2", {0x7009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 or pin1 or pin2 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN1_OR_PIN3 223
{ "EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN1_OR_PIN3", {0xb009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 or pin1 or pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN2 224
{ "EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN2", {0x5009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 or pin2 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN2_OR_PIN3 225
{ "EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN2_OR_PIN3", {0xd009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 or pin2 or pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN3 226
{ "EXTERN_DP_PINS_0_TO_3_PIN0_OR_PIN3", {0x9009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin0 or pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN1 227
{ "EXTERN_DP_PINS_0_TO_3_PIN1", {0x2009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin1 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN1_OR_PIN2 228
{ "EXTERN_DP_PINS_0_TO_3_PIN1_OR_PIN2", {0x6009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin1 or pin2 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN1_OR_PIN2_OR_PIN3 229
{ "EXTERN_DP_PINS_0_TO_3_PIN1_OR_PIN2_OR_PIN3", {0xe009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin1 or pin2 or pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN1_OR_PIN3 230
{ "EXTERN_DP_PINS_0_TO_3_PIN1_OR_PIN3", {0xa009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin1 or pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN2 231
{ "EXTERN_DP_PINS_0_TO_3_PIN2", {0x4009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin2 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN2_OR_PIN3 232
{ "EXTERN_DP_PINS_0_TO_3_PIN2_OR_PIN3", {0xc009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin2 or pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_0_TO_3_PIN3 233
{ "EXTERN_DP_PINS_0_TO_3_PIN3", {0x8009e}, 0xf0, 1, {0xf00000}, "DP Pins 0-3 Asserted -- include pin3 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_4_TO_5_ALL 234
{ "EXTERN_DP_PINS_4_TO_5_ALL", {0x3009f}, 0xf0, 1, {0xf00000}, "DP Pins 4-5 Asserted -- include pin5 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_4_TO_5_PIN4 235
{ "EXTERN_DP_PINS_4_TO_5_PIN4", {0x1009f}, 0xf0, 1, {0xf00000}, "DP Pins 4-5 Asserted -- include pin4 assertion"},
#define PME_ITA2_EXTERN_DP_PINS_4_TO_5_PIN5 236
{ "EXTERN_DP_PINS_4_TO_5_PIN5", {0x2009f}, 0xf0, 1, {0xf00000}, "DP Pins 4-5 Asserted -- include pin5 assertion"},
#define PME_ITA2_FE_BUBBLE_ALL 237
{ "FE_BUBBLE_ALL", {0x71}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- count regardless of cause"},
#define PME_ITA2_FE_BUBBLE_ALLBUT_FEFLUSH_BUBBLE 238
{ "FE_BUBBLE_ALLBUT_FEFLUSH_BUBBLE", {0xb0071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- ALL except FEFLUSH and BUBBLE"},
#define PME_ITA2_FE_BUBBLE_ALLBUT_IBFULL 239
{ "FE_BUBBLE_ALLBUT_IBFULL", {0xc0071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- ALL except IBFULl"},
#define PME_ITA2_FE_BUBBLE_BRANCH 240
{ "FE_BUBBLE_BRANCH", {0x90071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by any of 4 branch recirculates"},
#define PME_ITA2_FE_BUBBLE_BUBBLE 241
{ "FE_BUBBLE_BUBBLE", {0xd0071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by branch bubble stall"},
#define PME_ITA2_FE_BUBBLE_FEFLUSH 242
{ "FE_BUBBLE_FEFLUSH", {0x10071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by a front-end flush"},
#define PME_ITA2_FE_BUBBLE_FILL_RECIRC 243
{ "FE_BUBBLE_FILL_RECIRC", {0x80071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by a recirculate for a cache line fill operation"},
#define PME_ITA2_FE_BUBBLE_GROUP1 244
{ "FE_BUBBLE_GROUP1", {0x30071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- BUBBLE or BRANCH"},
#define PME_ITA2_FE_BUBBLE_GROUP2 245
{ "FE_BUBBLE_GROUP2", {0x40071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- IMISS or TLBMISS"},
#define PME_ITA2_FE_BUBBLE_GROUP3 246
{ "FE_BUBBLE_GROUP3", {0xa0071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- FILL_RECIRC or BRANCH"},
#define PME_ITA2_FE_BUBBLE_IBFULL 247
{ "FE_BUBBLE_IBFULL", {0x50071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by instruction buffer full stall"},
#define PME_ITA2_FE_BUBBLE_IMISS 248
{ "FE_BUBBLE_IMISS", {0x60071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by instruction cache miss stall"},
#define PME_ITA2_FE_BUBBLE_TLBMISS 249
{ "FE_BUBBLE_TLBMISS", {0x70071}, 0xf0, 1, {0xf00000}, "Bubbles Seen by FE -- only if caused by TLB stall"},
#define PME_ITA2_FE_LOST_BW_ALL 250
{ "FE_LOST_BW_ALL", {0x70}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- count regardless of cause"},
#define PME_ITA2_FE_LOST_BW_BI 251
{ "FE_LOST_BW_BI", {0x90070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch initialization stall"},
#define PME_ITA2_FE_LOST_BW_BRQ 252
{ "FE_LOST_BW_BRQ", {0xa0070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch retirement queue stall"},
#define PME_ITA2_FE_LOST_BW_BR_ILOCK 253
{ "FE_LOST_BW_BR_ILOCK", {0xc0070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch interlock stall"},
#define PME_ITA2_FE_LOST_BW_BUBBLE 254
{ "FE_LOST_BW_BUBBLE", {0xd0070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch resteer bubble stall"},
#define PME_ITA2_FE_LOST_BW_FEFLUSH 255
{ "FE_LOST_BW_FEFLUSH", {0x10070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by a front-end flush"},
#define PME_ITA2_FE_LOST_BW_FILL_RECIRC 256
{ "FE_LOST_BW_FILL_RECIRC", {0x80070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by a recirculate for a cache line fill operation"},
#define PME_ITA2_FE_LOST_BW_IBFULL 257
{ "FE_LOST_BW_IBFULL", {0x50070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by instruction buffer full stall"},
#define PME_ITA2_FE_LOST_BW_IMISS 258
{ "FE_LOST_BW_IMISS", {0x60070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by instruction cache miss stall"},
#define PME_ITA2_FE_LOST_BW_PLP 259
{ "FE_LOST_BW_PLP", {0xb0070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by perfect loop prediction stall"},
#define PME_ITA2_FE_LOST_BW_TLBMISS 260
{ "FE_LOST_BW_TLBMISS", {0x70070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by TLB stall"},
#define PME_ITA2_FE_LOST_BW_UNREACHED 261
{ "FE_LOST_BW_UNREACHED", {0x40070}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Entrance to IB -- only if caused by unreachable bundle"},
#define PME_ITA2_FP_FAILED_FCHKF 262
{ "FP_FAILED_FCHKF", {0x6}, 0xf0, 1, {0xf00001}, "Failed fchkf"},
#define PME_ITA2_FP_FALSE_SIRSTALL 263
{ "FP_FALSE_SIRSTALL", {0x5}, 0xf0, 1, {0xf00001}, "SIR Stall Without a Trap"},
#define PME_ITA2_FP_FLUSH_TO_ZERO 264
{ "FP_FLUSH_TO_ZERO", {0xb}, 0xf0, 2, {0xf00001}, "FP Result Flushed to Zero"},
#define PME_ITA2_FP_OPS_RETIRED 265
{ "FP_OPS_RETIRED", {0x9}, 0xf0, 4, {0xf00001}, "Retired FP Operations"},
#define PME_ITA2_FP_TRUE_SIRSTALL 266
{ "FP_TRUE_SIRSTALL", {0x3}, 0xf0, 1, {0xf00001}, "SIR stall asserted and leads to a trap"},
#define PME_ITA2_HPW_DATA_REFERENCES 267
{ "HPW_DATA_REFERENCES", {0x2d}, 0xf0, 4, {0xf00007}, "Data Memory References to VHPT"},
#define PME_ITA2_IA32_INST_RETIRED 268
{ "IA32_INST_RETIRED", {0x59}, 0xf0, 2, {0xf00000}, "IA-32 Instructions Retired"},
#define PME_ITA2_IA32_ISA_TRANSITIONS 269
{ "IA32_ISA_TRANSITIONS", {0x7}, 0xf0, 1, {0xf00000}, "IA-64 to/from IA-32 ISA Transitions"},
#define PME_ITA2_IA64_INST_RETIRED 270
{ "IA64_INST_RETIRED", {0x8}, 0xf0, 6, {0xf00003}, "Retired IA-64 Instructions, alias to IA64_INST_RETIRED_THIS"},
#define PME_ITA2_IA64_INST_RETIRED_THIS 271
{ "IA64_INST_RETIRED_THIS", {0x8}, 0xf0, 6, {0xf00003}, "Retired IA-64 Instructions -- Retired IA-64 Instructions"},
#define PME_ITA2_IA64_TAGGED_INST_RETIRED_IBRP0_PMC8 272
{ "IA64_TAGGED_INST_RETIRED_IBRP0_PMC8", {0x8}, 0xf0, 6, {0xf00003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 0 and opcode matcher PMC8. Code executed with PSR.is=1 is included."},
#define PME_ITA2_IA64_TAGGED_INST_RETIRED_IBRP1_PMC9 273
{ "IA64_TAGGED_INST_RETIRED_IBRP1_PMC9", {0x10008}, 0xf0, 6, {0xf00003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 1 and opcode matcher PMC9. Code executed with PSR.is=1 is included."},
#define PME_ITA2_IA64_TAGGED_INST_RETIRED_IBRP2_PMC8 274
{ "IA64_TAGGED_INST_RETIRED_IBRP2_PMC8", {0x20008}, 0xf0, 6, {0xf00003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 2 and opcode matcher PMC8. Code executed with PSR.is=1 is not included."},
#define PME_ITA2_IA64_TAGGED_INST_RETIRED_IBRP3_PMC9 275
{ "IA64_TAGGED_INST_RETIRED_IBRP3_PMC9", {0x30008}, 0xf0, 6, {0xf00003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 3 and opcode matcher PMC9. Code executed with PSR.is=1 is not included."},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_ALL 276
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_ALL", {0x73}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- count regardless of cause"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_BI 277
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BI", {0x90073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by branch initialization stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_BRQ 278
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BRQ", {0xa0073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by branch retirement queue stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_BR_ILOCK 279
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BR_ILOCK", {0xc0073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by branch interlock stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_BUBBLE 280
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BUBBLE", {0xd0073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by branch resteer bubble stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_FEFLUSH 281
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_FEFLUSH", {0x10073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by a front-end flush"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_FILL_RECIRC 282
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_FILL_RECIRC", {0x80073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by a recirculate for a cache line fill operation"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_IBFULL 283
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_IBFULL", {0x50073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- (* meaningless for this event *)"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_IMISS 284
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_IMISS", {0x60073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by instruction cache miss stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_PLP 285
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_PLP", {0xb0073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by perfect loop prediction stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_TLBMISS 286
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_TLBMISS", {0x70073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by TLB stall"},
#define PME_ITA2_IDEAL_BE_LOST_BW_DUE_TO_FE_UNREACHED 287
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_UNREACHED", {0x40073}, 0xf0, 2, {0xf00000}, "Invalid Bundles at the Exit from IB -- only if caused by unreachable bundle"},
#define PME_ITA2_INST_CHKA_LDC_ALAT_ALL 288
{ "INST_CHKA_LDC_ALAT_ALL", {0x30056}, 0xf0, 2, {0xf00007}, "Retired chk.a and ld.c Instructions -- both integer and floating point instructions"},
#define PME_ITA2_INST_CHKA_LDC_ALAT_FP 289
{ "INST_CHKA_LDC_ALAT_FP", {0x20056}, 0xf0, 2, {0xf00007}, "Retired chk.a and ld.c Instructions -- only floating point instructions"},
#define PME_ITA2_INST_CHKA_LDC_ALAT_INT 290
{ "INST_CHKA_LDC_ALAT_INT", {0x10056}, 0xf0, 2, {0xf00007}, "Retired chk.a and ld.c Instructions -- only integer instructions"},
#define PME_ITA2_INST_DISPERSED 291
{ "INST_DISPERSED", {0x4d}, 0xf0, 6, {0xf00001}, "Syllables Dispersed from REN to REG stage"},
#define PME_ITA2_INST_FAILED_CHKA_LDC_ALAT_ALL 292
{ "INST_FAILED_CHKA_LDC_ALAT_ALL", {0x30057}, 0xf0, 1, {0xf00007}, "Failed chk.a and ld.c Instructions -- both integer and floating point instructions"},
#define PME_ITA2_INST_FAILED_CHKA_LDC_ALAT_FP 293
{ "INST_FAILED_CHKA_LDC_ALAT_FP", {0x20057}, 0xf0, 1, {0xf00007}, "Failed chk.a and ld.c Instructions -- only floating point instructions"},
#define PME_ITA2_INST_FAILED_CHKA_LDC_ALAT_INT 294
{ "INST_FAILED_CHKA_LDC_ALAT_INT", {0x10057}, 0xf0, 1, {0xf00007}, "Failed chk.a and ld.c Instructions -- only integer instructions"},
#define PME_ITA2_INST_FAILED_CHKS_RETIRED_ALL 295
{ "INST_FAILED_CHKS_RETIRED_ALL", {0x30055}, 0xf0, 1, {0xf00000}, "Failed chk.s Instructions -- both integer and floating point instructions"},
#define PME_ITA2_INST_FAILED_CHKS_RETIRED_FP 296
{ "INST_FAILED_CHKS_RETIRED_FP", {0x20055}, 0xf0, 1, {0xf00000}, "Failed chk.s Instructions -- only floating point instructions"},
#define PME_ITA2_INST_FAILED_CHKS_RETIRED_INT 297
{ "INST_FAILED_CHKS_RETIRED_INT", {0x10055}, 0xf0, 1, {0xf00000}, "Failed chk.s Instructions -- only integer instructions"},
#define PME_ITA2_ISB_BUNPAIRS_IN 298
{ "ISB_BUNPAIRS_IN", {0x46}, 0xf0, 1, {0xf00001}, "Bundle Pairs Written from L2 into FE"},
#define PME_ITA2_ITLB_MISSES_FETCH_ALL 299
{ "ITLB_MISSES_FETCH_ALL", {0x30047}, 0xf0, 1, {0xf00001}, "ITLB Misses Demand Fetch -- All tlb misses will be counted. Note that this is not equal to sum of the L1ITLB and L2ITLB umasks because any access could be a miss in L1ITLB and L2ITLB."},
#define PME_ITA2_ITLB_MISSES_FETCH_L1ITLB 300
{ "ITLB_MISSES_FETCH_L1ITLB", {0x10047}, 0xf0, 1, {0xf00001}, "ITLB Misses Demand Fetch -- All misses in L1ITLB will be counted. even if L1ITLB is not updated for an access (Uncacheable/nat page/not present page/faulting/some flushed), it will be counted here."},
#define PME_ITA2_ITLB_MISSES_FETCH_L2ITLB 301
{ "ITLB_MISSES_FETCH_L2ITLB", {0x20047}, 0xf0, 1, {0xf00001}, "ITLB Misses Demand Fetch -- All misses in L1ITLB which also missed in L2ITLB will be counted."},
#define PME_ITA2_L1DTLB_TRANSFER 302
{ "L1DTLB_TRANSFER", {0xc0}, 0xf0, 1, {0x5010007}, "L1DTLB Misses That Hit in the L2DTLB for Accesses Counted in L1D_READS"},
#define PME_ITA2_L1D_READS_SET0 303
{ "L1D_READS_SET0", {0xc2}, 0xf0, 2, {0x5010007}, "L1 Data Cache Reads"},
#define PME_ITA2_L1D_READS_SET1 304
{ "L1D_READS_SET1", {0xc4}, 0xf0, 2, {0x5110007}, "L1 Data Cache Reads"},
#define PME_ITA2_L1D_READ_MISSES_ALL 305
{ "L1D_READ_MISSES_ALL", {0xc7}, 0xf0, 2, {0x5110007}, "L1 Data Cache Read Misses -- all L1D read misses will be counted."},
#define PME_ITA2_L1D_READ_MISSES_RSE_FILL 306
{ "L1D_READ_MISSES_RSE_FILL", {0x100c7}, 0xf0, 2, {0x5110007}, "L1 Data Cache Read Misses -- only L1D read misses caused by RSE fills will be counted"},
#define PME_ITA2_L1ITLB_INSERTS_HPW 307
{ "L1ITLB_INSERTS_HPW", {0x48}, 0xf0, 1, {0xf00001}, "L1ITLB Hardware Page Walker Inserts"},
#define PME_ITA2_L1I_EAR_CACHE_LAT0 308
{ "L1I_EAR_CACHE_LAT0", {0x400343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- > 0 Cycles (All L1 Misses)"},
#define PME_ITA2_L1I_EAR_CACHE_LAT1024 309
{ "L1I_EAR_CACHE_LAT1024", {0xc00343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 1024 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT128 310
{ "L1I_EAR_CACHE_LAT128", {0xf00343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 128 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT16 311
{ "L1I_EAR_CACHE_LAT16", {0xfc0343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 16 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT256 312
{ "L1I_EAR_CACHE_LAT256", {0xe00343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 256 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT32 313
{ "L1I_EAR_CACHE_LAT32", {0xf80343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 32 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT4 314
{ "L1I_EAR_CACHE_LAT4", {0xff0343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 4 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT4096 315
{ "L1I_EAR_CACHE_LAT4096", {0x800343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 4096 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_LAT8 316
{ "L1I_EAR_CACHE_LAT8", {0xfe0343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- >= 8 Cycles"},
#define PME_ITA2_L1I_EAR_CACHE_RAB 317
{ "L1I_EAR_CACHE_RAB", {0x343}, 0xf0, 1, {0xf00001}, "L1I EAR Cache -- RAB HIT"},
#define PME_ITA2_L1I_EAR_EVENTS 318
{ "L1I_EAR_EVENTS", {0x43}, 0xf0, 1, {0xf00001}, "Instruction EAR Events"},
#define PME_ITA2_L1I_EAR_TLB_ALL 319
{ "L1I_EAR_TLB_ALL", {0x70243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- All L1 ITLB Misses"},
#define PME_ITA2_L1I_EAR_TLB_FAULT 320
{ "L1I_EAR_TLB_FAULT", {0x40243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- ITLB Misses which produced a fault"},
#define PME_ITA2_L1I_EAR_TLB_L2TLB 321
{ "L1I_EAR_TLB_L2TLB", {0x10243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- L1 ITLB Misses which hit L2 ITLB"},
#define PME_ITA2_L1I_EAR_TLB_L2TLB_OR_FAULT 322
{ "L1I_EAR_TLB_L2TLB_OR_FAULT", {0x50243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- L1 ITLB Misses which hit L2 ITLB or produce a software fault"},
#define PME_ITA2_L1I_EAR_TLB_L2TLB_OR_VHPT 323
{ "L1I_EAR_TLB_L2TLB_OR_VHPT", {0x30243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- L1 ITLB Misses which hit L2 ITLB or VHPT"},
#define PME_ITA2_L1I_EAR_TLB_VHPT 324
{ "L1I_EAR_TLB_VHPT", {0x20243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- L1 ITLB Misses which hit VHPT"},
#define PME_ITA2_L1I_EAR_TLB_VHPT_OR_FAULT 325
{ "L1I_EAR_TLB_VHPT_OR_FAULT", {0x60243}, 0xf0, 1, {0xf00001}, "L1I EAR TLB -- L1 ITLB Misses which hit VHPT or produce a software fault"},
#define PME_ITA2_L1I_FETCH_ISB_HIT 326
{ "L1I_FETCH_ISB_HIT", {0x66}, 0xf0, 1, {0xf00001}, "\"Just-In-Time\" Instruction Fetch Hitting in and Being Bypassed from ISB"},
#define PME_ITA2_L1I_FETCH_RAB_HIT 327
{ "L1I_FETCH_RAB_HIT", {0x65}, 0xf0, 1, {0xf00001}, "Instruction Fetch Hitting in RAB"},
#define PME_ITA2_L1I_FILLS 328
{ "L1I_FILLS", {0x41}, 0xf0, 1, {0xf00001}, "L1 Instruction Cache Fills"},
#define PME_ITA2_L1I_PREFETCHES 329
{ "L1I_PREFETCHES", {0x44}, 0xf0, 1, {0xf00001}, "L1 Instruction Prefetch Requests"},
#define PME_ITA2_L1I_PREFETCH_STALL_ALL 330
{ "L1I_PREFETCH_STALL_ALL", {0x30067}, 0xf0, 1, {0xf00000}, "Prefetch Pipeline Stalls -- Number of clocks prefetch pipeline is stalled"},
#define PME_ITA2_L1I_PREFETCH_STALL_FLOW 331
{ "L1I_PREFETCH_STALL_FLOW", {0x20067}, 0xf0, 1, {0xf00000}, "Prefetch Pipeline Stalls -- Number of clocks flow is not asserted"},
#define PME_ITA2_L1I_PURGE 332
{ "L1I_PURGE", {0x4b}, 0xf0, 1, {0xf00001}, "L1ITLB Purges Handled by L1I"},
#define PME_ITA2_L1I_PVAB_OVERFLOW 333
{ "L1I_PVAB_OVERFLOW", {0x69}, 0xf0, 1, {0xf00000}, "PVAB Overflow"},
#define PME_ITA2_L1I_RAB_ALMOST_FULL 334
{ "L1I_RAB_ALMOST_FULL", {0x64}, 0xf0, 1, {0xf00000}, "Is RAB Almost Full?"},
#define PME_ITA2_L1I_RAB_FULL 335
{ "L1I_RAB_FULL", {0x60}, 0xf0, 1, {0xf00000}, "Is RAB Full?"},
#define PME_ITA2_L1I_READS 336
{ "L1I_READS", {0x40}, 0xf0, 1, {0xf00001}, "L1 Instruction Cache Reads"},
#define PME_ITA2_L1I_SNOOP 337
{ "L1I_SNOOP", {0x4a}, 0xf0, 1, {0xf00007}, "Snoop Requests Handled by L1I"},
#define PME_ITA2_L1I_STRM_PREFETCHES 338
{ "L1I_STRM_PREFETCHES", {0x5f}, 0xf0, 1, {0xf00001}, "L1 Instruction Cache Line Prefetch Requests"},
#define PME_ITA2_L2DTLB_MISSES 339
{ "L2DTLB_MISSES", {0xc1}, 0xf0, 4, {0x5010007}, "L2DTLB Misses"},
#define PME_ITA2_L2_BAD_LINES_SELECTED_ANY 340
{ "L2_BAD_LINES_SELECTED_ANY", {0xb9}, 0xf0, 4, {0x4320007}, "Valid Line Replaced When Invalid Line Is Available -- Valid line replaced when invalid line is available"},
#define PME_ITA2_L2_BYPASS_L2_DATA1 341
{ "L2_BYPASS_L2_DATA1", {0xb8}, 0xf0, 1, {0x4320007}, "Count L2 Bypasses -- Count only L2 data bypasses (L1D to L2A)"},
#define PME_ITA2_L2_BYPASS_L2_DATA2 342
{ "L2_BYPASS_L2_DATA2", {0x100b8}, 0xf0, 1, {0x4320007}, "Count L2 Bypasses -- Count only L2 data bypasses (L1W to L2I)"},
#define PME_ITA2_L2_BYPASS_L2_INST1 343
{ "L2_BYPASS_L2_INST1", {0x400b8}, 0xf0, 1, {0x4320007}, "Count L2 Bypasses -- Count only L2 instruction bypasses (L1D to L2A)"},
#define PME_ITA2_L2_BYPASS_L2_INST2 344
{ "L2_BYPASS_L2_INST2", {0x500b8}, 0xf0, 1, {0x4320007}, "Count L2 Bypasses -- Count only L2 instruction bypasses (L1W to L2I)"},
#define PME_ITA2_L2_BYPASS_L3_DATA1 345
{ "L2_BYPASS_L3_DATA1", {0x200b8}, 0xf0, 1, {0x4320007}, "Count L2 Bypasses -- Count only L3 data bypasses (L1D to L2A)"},
#define PME_ITA2_L2_BYPASS_L3_INST1 346
{ "L2_BYPASS_L3_INST1", {0x600b8}, 0xf0, 1, {0x4320007}, "Count L2 Bypasses -- Count only L3 instruction bypasses (L1D to L2A)"},
#define PME_ITA2_L2_DATA_REFERENCES_L2_ALL 347
{ "L2_DATA_REFERENCES_L2_ALL", {0x300b2}, 0xf0, 4, {0x4120007}, "Data Read/Write Access to L2 -- count both read and write operations (semaphores will count as 2)"},
#define PME_ITA2_L2_DATA_REFERENCES_L2_DATA_READS 348
{ "L2_DATA_REFERENCES_L2_DATA_READS", {0x100b2}, 0xf0, 4, {0x4120007}, "Data Read/Write Access to L2 -- count only data read and semaphore operations."},
#define PME_ITA2_L2_DATA_REFERENCES_L2_DATA_WRITES 349
{ "L2_DATA_REFERENCES_L2_DATA_WRITES", {0x200b2}, 0xf0, 4, {0x4120007}, "Data Read/Write Access to L2 -- count only data write and semaphore operations"},
#define PME_ITA2_L2_FILLB_FULL_THIS 350
{ "L2_FILLB_FULL_THIS", {0xbf}, 0xf0, 1, {0x4520000}, "L2D Fill Buffer Is Full -- L2 Fill buffer is full"},
#define PME_ITA2_L2_FORCE_RECIRC_ANY 351
{ "L2_FORCE_RECIRC_ANY", {0xb4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count forced recirculates regardless of cause. SMC_HIT, TRAN_PREF & SNP_OR_L3 will not be included here."},
#define PME_ITA2_L2_FORCE_RECIRC_FILL_HIT 352
{ "L2_FORCE_RECIRC_FILL_HIT", {0x900b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by an L2 miss which hit in the fill buffer."},
#define PME_ITA2_L2_FORCE_RECIRC_FRC_RECIRC 353
{ "L2_FORCE_RECIRC_FRC_RECIRC", {0xe00b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- caused by an L2 miss when a force recirculate already existed"},
#define PME_ITA2_L2_FORCE_RECIRC_IPF_MISS 354
{ "L2_FORCE_RECIRC_IPF_MISS", {0xa00b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- caused by L2 miss when instruction prefetch buffer miss already existed"},
#define PME_ITA2_L2_FORCE_RECIRC_L1W 355
{ "L2_FORCE_RECIRC_L1W", {0x200b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by forced limbo"},
#define PME_ITA2_L2_FORCE_RECIRC_OZQ_MISS 356
{ "L2_FORCE_RECIRC_OZQ_MISS", {0xc00b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- caused by an L2 miss when an OZQ miss already existed"},
#define PME_ITA2_L2_FORCE_RECIRC_SAME_INDEX 357
{ "L2_FORCE_RECIRC_SAME_INDEX", {0xd00b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- caused by an L2 miss when a miss to the same index already existed"},
#define PME_ITA2_L2_FORCE_RECIRC_SMC_HIT 358
{ "L2_FORCE_RECIRC_SMC_HIT", {0x100b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by SMC hits due to an ifetch and load to same cache line or a pending WT store"},
#define PME_ITA2_L2_FORCE_RECIRC_SNP_OR_L3 359
{ "L2_FORCE_RECIRC_SNP_OR_L3", {0x600b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by a snoop or L3 issue"},
#define PME_ITA2_L2_FORCE_RECIRC_TAG_NOTOK 360
{ "L2_FORCE_RECIRC_TAG_NOTOK", {0x400b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by L2 hits caused by in flight snoops, stores with a sibling miss to the same index, sibling probe to the same line or pending sync.ia instructions."},
#define PME_ITA2_L2_FORCE_RECIRC_TRAN_PREF 361
{ "L2_FORCE_RECIRC_TRAN_PREF", {0x500b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by transforms to prefetches"},
#define PME_ITA2_L2_FORCE_RECIRC_VIC_BUF_FULL 362
{ "L2_FORCE_RECIRC_VIC_BUF_FULL", {0xb00b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by an L2 miss with victim buffer full"},
#define PME_ITA2_L2_FORCE_RECIRC_VIC_PEND 363
{ "L2_FORCE_RECIRC_VIC_PEND", {0x800b4}, 0x10, 4, {0x4220007}, "Forced Recirculates -- count only those caused by an L2 miss with pending victim"},
#define PME_ITA2_L2_GOT_RECIRC_IFETCH_ANY 364
{ "L2_GOT_RECIRC_IFETCH_ANY", {0x800ba}, 0xf0, 1, {0x4420007}, "Instruction Fetch Recirculates Received by L2D -- Instruction fetch recirculates received by L2"},
#define PME_ITA2_L2_GOT_RECIRC_OZQ_ACC 365
{ "L2_GOT_RECIRC_OZQ_ACC", {0xb6}, 0xf0, 1, {0x4220007}, "Counts Number of OZQ Accesses Recirculated to L1D"},
#define PME_ITA2_L2_IFET_CANCELS_ANY 366
{ "L2_IFET_CANCELS_ANY", {0xa1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- total instruction fetch cancels by L2"},
#define PME_ITA2_L2_IFET_CANCELS_BYPASS 367
{ "L2_IFET_CANCELS_BYPASS", {0x200a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch cancels due to bypassing"},
#define PME_ITA2_L2_IFET_CANCELS_CHG_PRIO 368
{ "L2_IFET_CANCELS_CHG_PRIO", {0xc00a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch cancels due to change priority"},
#define PME_ITA2_L2_IFET_CANCELS_DATA_RD 369
{ "L2_IFET_CANCELS_DATA_RD", {0x700a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch/prefetch cancels due to a data read"},
#define PME_ITA2_L2_IFET_CANCELS_DIDNT_RECIR 370
{ "L2_IFET_CANCELS_DIDNT_RECIR", {0x400a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch cancels because it did not recirculate"},
#define PME_ITA2_L2_IFET_CANCELS_IFETCH_BYP 371
{ "L2_IFET_CANCELS_IFETCH_BYP", {0xd00a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- due to ifetch bypass during last clock"},
#define PME_ITA2_L2_IFET_CANCELS_PREEMPT 372
{ "L2_IFET_CANCELS_PREEMPT", {0x800a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch cancels due to preempts"},
#define PME_ITA2_L2_IFET_CANCELS_RECIR_OVER_SUB 373
{ "L2_IFET_CANCELS_RECIR_OVER_SUB", {0x500a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch cancels because of recirculate oversubscription"},
#define PME_ITA2_L2_IFET_CANCELS_ST_FILL_WB 374
{ "L2_IFET_CANCELS_ST_FILL_WB", {0x600a1}, 0xf0, 1, {0x4020007}, "Instruction Fetch Cancels by the L2 -- ifetch cancels due to a store or fill or write back"},
#define PME_ITA2_L2_INST_DEMAND_READS 375
{ "L2_INST_DEMAND_READS", {0x42}, 0xf0, 1, {0xf00001}, "L2 Instruction Demand Fetch Requests"},
#define PME_ITA2_L2_INST_PREFETCHES 376
{ "L2_INST_PREFETCHES", {0x45}, 0xf0, 1, {0xf00001}, "L2 Instruction Prefetch Requests"},
#define PME_ITA2_L2_ISSUED_RECIRC_IFETCH_ANY 377
{ "L2_ISSUED_RECIRC_IFETCH_ANY", {0x800b9}, 0xf0, 1, {0x4420007}, "Instruction Fetch Recirculates Issued by L2 -- Instruction fetch recirculates issued by L2"},
#define PME_ITA2_L2_ISSUED_RECIRC_OZQ_ACC 378
{ "L2_ISSUED_RECIRC_OZQ_ACC", {0xb5}, 0xf0, 1, {0x4220007}, "Count Number of Times a Recirculate Issue Was Attempted and Not Preempted"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_ANY 379
{ "L2_L3ACCESS_CANCEL_ANY", {0x900b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- count cancels due to any reason. This umask will count more than the sum of all the other umasks. It will count things that weren't committed accesses when they reached L1w, but the L2 attempted to bypass them to the L3 anyway (speculatively). This will include accesses made repeatedly while the main pipeline is stalled and the L1d is attempting to recirculate an access down the L1d pipeline. Thus, an access could get counted many times before it really does get bypassed to the L3. It is a measure of how many times we asserted a request to the L3 but didn't confirm it."},
#define PME_ITA2_L2_L3ACCESS_CANCEL_DFETCH 380
{ "L2_L3ACCESS_CANCEL_DFETCH", {0xa00b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- data fetches"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_EBL_REJECT 381
{ "L2_L3ACCESS_CANCEL_EBL_REJECT", {0x800b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- ebl rejects"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_FILLD_FULL 382
{ "L2_L3ACCESS_CANCEL_FILLD_FULL", {0x200b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- filld being full"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_IFETCH 383
{ "L2_L3ACCESS_CANCEL_IFETCH", {0xb00b0}, 0xf0, 1, {0x4120007}, "Canceled L3 Accesses -- instruction fetches"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_INV_L3_BYP 384
{ "L2_L3ACCESS_CANCEL_INV_L3_BYP", {0x600b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- invalid L3 bypasses"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_SPEC_L3_BYP 385
{ "L2_L3ACCESS_CANCEL_SPEC_L3_BYP", {0x100b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- speculative L3 bypasses"},
#define PME_ITA2_L2_L3ACCESS_CANCEL_UC_BLOCKED 386
{ "L2_L3ACCESS_CANCEL_UC_BLOCKED", {0x500b0}, 0x10, 1, {0x4120007}, "Canceled L3 Accesses -- Uncacheable blocked L3 Accesses"},
#define PME_ITA2_L2_MISSES 387
{ "L2_MISSES", {0xcb}, 0xf0, 1, {0xf00007}, "L2 Misses"},
#define PME_ITA2_L2_OPS_ISSUED_FP_LOAD 388
{ "L2_OPS_ISSUED_FP_LOAD", {0x900b8}, 0xf0, 4, {0x4420007}, "Different Operations Issued by L2D -- Count only valid floating point loads"},
#define PME_ITA2_L2_OPS_ISSUED_INT_LOAD 389
{ "L2_OPS_ISSUED_INT_LOAD", {0x800b8}, 0xf0, 4, {0x4420007}, "Different Operations Issued by L2D -- Count only valid integer loads"},
#define PME_ITA2_L2_OPS_ISSUED_NST_NLD 390
{ "L2_OPS_ISSUED_NST_NLD", {0xc00b8}, 0xf0, 4, {0x4420007}, "Different Operations Issued by L2D -- Count only valid non-load, no-store accesses"},
#define PME_ITA2_L2_OPS_ISSUED_RMW 391
{ "L2_OPS_ISSUED_RMW", {0xa00b8}, 0xf0, 4, {0x4420007}, "Different Operations Issued by L2D -- Count only valid read_modify_write stores"},
#define PME_ITA2_L2_OPS_ISSUED_STORE 392
{ "L2_OPS_ISSUED_STORE", {0xb00b8}, 0xf0, 4, {0x4420007}, "Different Operations Issued by L2D -- Count only valid non-read_modify_write stores"},
#define PME_ITA2_L2_OZDB_FULL_THIS 393
{ "L2_OZDB_FULL_THIS", {0xbd}, 0xf0, 1, {0x4520000}, "L2 OZ Data Buffer Is Full -- L2 OZ Data Buffer is full"},
#define PME_ITA2_L2_OZQ_ACQUIRE 394
{ "L2_OZQ_ACQUIRE", {0xa2}, 0xf0, 1, {0x4020000}, "Clocks With Acquire Ordering Attribute Existed in L2 OZQ"},
#define PME_ITA2_L2_OZQ_CANCELS0_ANY 395
{ "L2_OZQ_CANCELS0_ANY", {0xa0}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Late or Any) -- counts the total OZ Queue cancels"},
#define PME_ITA2_L2_OZQ_CANCELS0_LATE_ACQUIRE 396
{ "L2_OZQ_CANCELS0_LATE_ACQUIRE", {0x300a0}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Late or Any) -- counts the late cancels caused by acquires"},
#define PME_ITA2_L2_OZQ_CANCELS0_LATE_BYP_EFFRELEASE 397
{ "L2_OZQ_CANCELS0_LATE_BYP_EFFRELEASE", {0x400a0}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Late or Any) -- counts the late cancels caused by L1D to L2A bypass effective releases"},
#define PME_ITA2_L2_OZQ_CANCELS0_LATE_RELEASE 398
{ "L2_OZQ_CANCELS0_LATE_RELEASE", {0x200a0}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Late or Any) -- counts the late cancels caused by releases"},
#define PME_ITA2_L2_OZQ_CANCELS0_LATE_SPEC_BYP 399
{ "L2_OZQ_CANCELS0_LATE_SPEC_BYP", {0x100a0}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Late or Any) -- counts the late cancels caused by speculative bypasses"},
#define PME_ITA2_L2_OZQ_CANCELS1_BANK_CONF 400
{ "L2_OZQ_CANCELS1_BANK_CONF", {0x100ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- bank conflicts"},
#define PME_ITA2_L2_OZQ_CANCELS1_CANC_L2M_ST 401
{ "L2_OZQ_CANCELS1_CANC_L2M_ST", {0x600ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- caused by a canceled store in L2M"},
#define PME_ITA2_L2_OZQ_CANCELS1_CCV 402
{ "L2_OZQ_CANCELS1_CCV", {0x900ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a ccv"},
#define PME_ITA2_L2_OZQ_CANCELS1_ECC 403
{ "L2_OZQ_CANCELS1_ECC", {0xf00ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- ECC hardware detecting a problem"},
#define PME_ITA2_L2_OZQ_CANCELS1_HPW_IFETCH_CONF 404
{ "L2_OZQ_CANCELS1_HPW_IFETCH_CONF", {0x500ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a ifetch conflict (canceling HPW?)"},
#define PME_ITA2_L2_OZQ_CANCELS1_L1DF_L2M 405
{ "L2_OZQ_CANCELS1_L1DF_L2M", {0xe00ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- L1D fill in L2M"},
#define PME_ITA2_L2_OZQ_CANCELS1_L1_FILL_CONF 406
{ "L2_OZQ_CANCELS1_L1_FILL_CONF", {0x700ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- an L1 fill conflict"},
#define PME_ITA2_L2_OZQ_CANCELS1_L2A_ST_MAT 407
{ "L2_OZQ_CANCELS1_L2A_ST_MAT", {0xd00ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a store match in L2A"},
#define PME_ITA2_L2_OZQ_CANCELS1_L2D_ST_MAT 408
{ "L2_OZQ_CANCELS1_L2D_ST_MAT", {0x200ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a store match in L2D"},
#define PME_ITA2_L2_OZQ_CANCELS1_L2M_ST_MAT 409
{ "L2_OZQ_CANCELS1_L2M_ST_MAT", {0xb00ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a store match in L2M"},
#define PME_ITA2_L2_OZQ_CANCELS1_MFA 410
{ "L2_OZQ_CANCELS1_MFA", {0xc00ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a memory fence instruction"},
#define PME_ITA2_L2_OZQ_CANCELS1_REL 411
{ "L2_OZQ_CANCELS1_REL", {0xac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- caused by release"},
#define PME_ITA2_L2_OZQ_CANCELS1_SEM 412
{ "L2_OZQ_CANCELS1_SEM", {0xa00ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a semaphore"},
#define PME_ITA2_L2_OZQ_CANCELS1_ST_FILL_CONF 413
{ "L2_OZQ_CANCELS1_ST_FILL_CONF", {0x800ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- a store fill conflict"},
#define PME_ITA2_L2_OZQ_CANCELS1_SYNC 414
{ "L2_OZQ_CANCELS1_SYNC", {0x400ac}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 1) -- caused by sync.i"},
#define PME_ITA2_L2_OZQ_CANCELS2_ACQ 415
{ "L2_OZQ_CANCELS2_ACQ", {0x400a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- caused by an acquire"},
#define PME_ITA2_L2_OZQ_CANCELS2_CANC_L2C_ST 416
{ "L2_OZQ_CANCELS2_CANC_L2C_ST", {0x100a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- caused by a canceled store in L2C"},
#define PME_ITA2_L2_OZQ_CANCELS2_CANC_L2D_ST 417
{ "L2_OZQ_CANCELS2_CANC_L2D_ST", {0xd00a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- caused by a canceled store in L2D"},
#define PME_ITA2_L2_OZQ_CANCELS2_DIDNT_RECIRC 418
{ "L2_OZQ_CANCELS2_DIDNT_RECIRC", {0x900a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- caused because it did not recirculate"},
#define PME_ITA2_L2_OZQ_CANCELS2_D_IFET 419
{ "L2_OZQ_CANCELS2_D_IFET", {0xf00a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- a demand ifetch"},
#define PME_ITA2_L2_OZQ_CANCELS2_L2C_ST_MAT 420
{ "L2_OZQ_CANCELS2_L2C_ST_MAT", {0x200a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- a store match in L2C"},
#define PME_ITA2_L2_OZQ_CANCELS2_L2FILL_ST_CONF 421
{ "L2_OZQ_CANCELS2_L2FILL_ST_CONF", {0x800a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- a L2fill and store conflict in L2C"},
#define PME_ITA2_L2_OZQ_CANCELS2_OVER_SUB 422
{ "L2_OZQ_CANCELS2_OVER_SUB", {0xc00a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- oversubscription"},
#define PME_ITA2_L2_OZQ_CANCELS2_OZ_DATA_CONF 423
{ "L2_OZQ_CANCELS2_OZ_DATA_CONF", {0x600a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- an OZ data conflict"},
#define PME_ITA2_L2_OZQ_CANCELS2_READ_WB_CONF 424
{ "L2_OZQ_CANCELS2_READ_WB_CONF", {0x500a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- a write back conflict (canceling read?)"},
#define PME_ITA2_L2_OZQ_CANCELS2_RECIRC_OVER_SUB 425
{ "L2_OZQ_CANCELS2_RECIRC_OVER_SUB", {0xa8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- caused by a recirculate oversubscription"},
#define PME_ITA2_L2_OZQ_CANCELS2_SCRUB 426
{ "L2_OZQ_CANCELS2_SCRUB", {0x300a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- 32/64 byte HPW/L2D fill which needs scrub"},
#define PME_ITA2_L2_OZQ_CANCELS2_WEIRD 427
{ "L2_OZQ_CANCELS2_WEIRD", {0xa00a8}, 0xf0, 4, {0x4020007}, "L2 OZQ Cancels (Specific Reason Set 2) -- counts the cancels caused by attempted 5-cycle bypasses for non-aligned accesses and bypasses blocking recirculates for too long"},
#define PME_ITA2_L2_OZQ_FULL_THIS 428
{ "L2_OZQ_FULL_THIS", {0xbc}, 0xf0, 1, {0x4520000}, "L2D OZQ Is Full -- L2D OZQ is full"},
#define PME_ITA2_L2_OZQ_RELEASE 429
{ "L2_OZQ_RELEASE", {0xa3}, 0xf0, 1, {0x4020000}, "Clocks With Release Ordering Attribute Existed in L2 OZQ"},
#define PME_ITA2_L2_REFERENCES 430
{ "L2_REFERENCES", {0xb1}, 0xf0, 4, {0x4120007}, "Requests Made To L2"},
#define PME_ITA2_L2_STORE_HIT_SHARED_ANY 431
{ "L2_STORE_HIT_SHARED_ANY", {0xba}, 0xf0, 2, {0x4320007}, "Store Hit a Shared Line -- Store hit a shared line"},
#define PME_ITA2_L2_SYNTH_PROBE 432
{ "L2_SYNTH_PROBE", {0xb7}, 0xf0, 1, {0x4220007}, "Synthesized Probe"},
#define PME_ITA2_L2_VICTIMB_FULL_THIS 433
{ "L2_VICTIMB_FULL_THIS", {0xbe}, 0xf0, 1, {0x4520000}, "L2D Victim Buffer Is Full -- L2D victim buffer is full"},
#define PME_ITA2_L3_LINES_REPLACED 434
{ "L3_LINES_REPLACED", {0xdf}, 0xf0, 1, {0xf00000}, "L3 Cache Lines Replaced"},
#define PME_ITA2_L3_MISSES 435
{ "L3_MISSES", {0xdc}, 0xf0, 1, {0xf00007}, "L3 Misses"},
#define PME_ITA2_L3_READS_ALL_ALL 436
{ "L3_READS_ALL_ALL", {0xf00dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Read References"},
#define PME_ITA2_L3_READS_ALL_HIT 437
{ "L3_READS_ALL_HIT", {0xd00dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Read Hits"},
#define PME_ITA2_L3_READS_ALL_MISS 438
{ "L3_READS_ALL_MISS", {0xe00dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Read Misses"},
#define PME_ITA2_L3_READS_DATA_READ_ALL 439
{ "L3_READS_DATA_READ_ALL", {0xb00dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Load References (excludes reads for ownership used to satisfy stores)"},
#define PME_ITA2_L3_READS_DATA_READ_HIT 440
{ "L3_READS_DATA_READ_HIT", {0x900dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Load Hits (excludes reads for ownership used to satisfy stores)"},
#define PME_ITA2_L3_READS_DATA_READ_MISS 441
{ "L3_READS_DATA_READ_MISS", {0xa00dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Load Misses (excludes reads for ownership used to satisfy stores)"},
#define PME_ITA2_L3_READS_DINST_FETCH_ALL 442
{ "L3_READS_DINST_FETCH_ALL", {0x300dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Demand Instruction References"},
#define PME_ITA2_L3_READS_DINST_FETCH_HIT 443
{ "L3_READS_DINST_FETCH_HIT", {0x100dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Demand Instruction Fetch Hits"},
#define PME_ITA2_L3_READS_DINST_FETCH_MISS 444
{ "L3_READS_DINST_FETCH_MISS", {0x200dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Demand Instruction Fetch Misses"},
#define PME_ITA2_L3_READS_INST_FETCH_ALL 445
{ "L3_READS_INST_FETCH_ALL", {0x700dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Instruction Fetch and Prefetch References"},
#define PME_ITA2_L3_READS_INST_FETCH_HIT 446
{ "L3_READS_INST_FETCH_HIT", {0x500dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Instruction Fetch and Prefetch Hits"},
#define PME_ITA2_L3_READS_INST_FETCH_MISS 447
{ "L3_READS_INST_FETCH_MISS", {0x600dd}, 0xf0, 1, {0xf00007}, "L3 Reads -- L3 Instruction Fetch and Prefetch Misses"},
#define PME_ITA2_L3_REFERENCES 448
{ "L3_REFERENCES", {0xdb}, 0xf0, 1, {0xf00007}, "L3 References"},
#define PME_ITA2_L3_WRITES_ALL_ALL 449
{ "L3_WRITES_ALL_ALL", {0xf00de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L3 Write References"},
#define PME_ITA2_L3_WRITES_ALL_HIT 450
{ "L3_WRITES_ALL_HIT", {0xd00de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L3 Write Hits"},
#define PME_ITA2_L3_WRITES_ALL_MISS 451
{ "L3_WRITES_ALL_MISS", {0xe00de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L3 Write Misses"},
#define PME_ITA2_L3_WRITES_DATA_WRITE_ALL 452
{ "L3_WRITES_DATA_WRITE_ALL", {0x700de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L3 Store References (excludes L2 write backs, includes L3 read for ownership requests that satisfy stores)"},
#define PME_ITA2_L3_WRITES_DATA_WRITE_HIT 453
{ "L3_WRITES_DATA_WRITE_HIT", {0x500de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L3 Store Hits (excludes L2 write backs, includes L3 read for ownership requests that satisfy stores)"},
#define PME_ITA2_L3_WRITES_DATA_WRITE_MISS 454
{ "L3_WRITES_DATA_WRITE_MISS", {0x600de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L3 Store Misses (excludes L2 write backs, includes L3 read for ownership requests that satisfy stores)"},
#define PME_ITA2_L3_WRITES_L2_WB_ALL 455
{ "L3_WRITES_L2_WB_ALL", {0xb00de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L2 Write Back References"},
#define PME_ITA2_L3_WRITES_L2_WB_HIT 456
{ "L3_WRITES_L2_WB_HIT", {0x900de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L2 Write Back Hits"},
#define PME_ITA2_L3_WRITES_L2_WB_MISS 457
{ "L3_WRITES_L2_WB_MISS", {0xa00de}, 0xf0, 1, {0xf00007}, "L3 Writes -- L2 Write Back Misses"},
#define PME_ITA2_LOADS_RETIRED 458
{ "LOADS_RETIRED", {0xcd}, 0xf0, 4, {0x5310007}, "Retired Loads"},
#define PME_ITA2_MEM_READ_CURRENT_ANY 459
{ "MEM_READ_CURRENT_ANY", {0x30089}, 0xf0, 1, {0xf00000}, "Current Mem Read Transactions On Bus -- CPU or non-CPU (all transactions)."},
#define PME_ITA2_MEM_READ_CURRENT_IO 460
{ "MEM_READ_CURRENT_IO", {0x10089}, 0xf0, 1, {0xf00000}, "Current Mem Read Transactions On Bus -- non-CPU priority agents"},
#define PME_ITA2_MISALIGNED_LOADS_RETIRED 461
{ "MISALIGNED_LOADS_RETIRED", {0xce}, 0xf0, 4, {0x5310007}, "Retired Misaligned Load Instructions"},
#define PME_ITA2_MISALIGNED_STORES_RETIRED 462
{ "MISALIGNED_STORES_RETIRED", {0xd2}, 0xf0, 2, {0x5410007}, "Retired Misaligned Store Instructions"},
#define PME_ITA2_NOPS_RETIRED 463
{ "NOPS_RETIRED", {0x50}, 0xf0, 6, {0xf00003}, "Retired NOP Instructions"},
#define PME_ITA2_PREDICATE_SQUASHED_RETIRED 464
{ "PREDICATE_SQUASHED_RETIRED", {0x51}, 0xf0, 6, {0xf00003}, "Instructions Squashed Due to Predicate Off"},
#define PME_ITA2_RSE_CURRENT_REGS_2_TO_0 465
{ "RSE_CURRENT_REGS_2_TO_0", {0x2b}, 0xf0, 7, {0xf00000}, "Current RSE Registers (Bits 2:0)"},
#define PME_ITA2_RSE_CURRENT_REGS_5_TO_3 466
{ "RSE_CURRENT_REGS_5_TO_3", {0x2a}, 0xf0, 7, {0xf00000}, "Current RSE Registers (Bits 5:3)"},
#define PME_ITA2_RSE_CURRENT_REGS_6 467
{ "RSE_CURRENT_REGS_6", {0x26}, 0xf0, 1, {0xf00000}, "Current RSE Registers (Bit 6)"},
#define PME_ITA2_RSE_DIRTY_REGS_2_TO_0 468
{ "RSE_DIRTY_REGS_2_TO_0", {0x29}, 0xf0, 7, {0xf00000}, "Dirty RSE Registers (Bits 2:0)"},
#define PME_ITA2_RSE_DIRTY_REGS_5_TO_3 469
{ "RSE_DIRTY_REGS_5_TO_3", {0x28}, 0xf0, 7, {0xf00000}, "Dirty RSE Registers (Bits 5:3)"},
#define PME_ITA2_RSE_DIRTY_REGS_6 470
{ "RSE_DIRTY_REGS_6", {0x24}, 0xf0, 1, {0xf00000}, "Dirty RSE Registers (Bit 6)"},
#define PME_ITA2_RSE_EVENT_RETIRED 471
{ "RSE_EVENT_RETIRED", {0x32}, 0xf0, 1, {0xf00000}, "Retired RSE operations"},
#define PME_ITA2_RSE_REFERENCES_RETIRED_ALL 472
{ "RSE_REFERENCES_RETIRED_ALL", {0x30020}, 0xf0, 2, {0xf00007}, "RSE Accesses -- Both RSE loads and stores will be counted."},
#define PME_ITA2_RSE_REFERENCES_RETIRED_LOAD 473
{ "RSE_REFERENCES_RETIRED_LOAD", {0x10020}, 0xf0, 2, {0xf00007}, "RSE Accesses -- Only RSE loads will be counted."},
#define PME_ITA2_RSE_REFERENCES_RETIRED_STORE 474
{ "RSE_REFERENCES_RETIRED_STORE", {0x20020}, 0xf0, 2, {0xf00007}, "RSE Accesses -- Only RSE stores will be counted."},
#define PME_ITA2_SERIALIZATION_EVENTS 475
{ "SERIALIZATION_EVENTS", {0x53}, 0xf0, 1, {0xf00000}, "Number of srlz.i Instructions"},
#define PME_ITA2_STORES_RETIRED 476
{ "STORES_RETIRED", {0xd1}, 0xf0, 2, {0x5410007}, "Retired Stores"},
#define PME_ITA2_SYLL_NOT_DISPERSED_ALL 477
{ "SYLL_NOT_DISPERSED_ALL", {0xf004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Counts all syllables not dispersed. NOTE: Any combination of b0000-b1111 is valid."},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL 478
{ "SYLL_NOT_DISPERSED_EXPL", {0x1004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits. These consist of  programmer specified architected S-bit and templates 1 and 5. Dispersal takes a 6-syllable (3-syllable) hit for every template 1/5 in bundle 0(1). Dispersal takes a 3-syllable (0 syllable) hit for every S-bit in bundle 0(1)"},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL_OR_FE 479
{ "SYLL_NOT_DISPERSED_EXPL_OR_FE", {0x5004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits or front-end not providing valid bundles or providing valid illegal templates."},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL_OR_FE_OR_MLI 480
{ "SYLL_NOT_DISPERSED_EXPL_OR_FE_OR_MLI", {0xd004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits or due to front-end not providing valid bundles or providing valid illegal templates or due to MLI bundle and resteers to non-0 syllable."},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL_OR_IMPL 481
{ "SYLL_NOT_DISPERSED_EXPL_OR_IMPL", {0x3004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit/implicit stop bits."},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_FE 482
{ "SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_FE", {0x7004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit or implicit stop bits or due to front-end not providing valid bundles or providing valid illegal template."},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_MLI 483
{ "SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_MLI", {0xb004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit or implicit stop bits or due to MLI bundle and resteers to non-0 syllable."},
#define PME_ITA2_SYLL_NOT_DISPERSED_EXPL_OR_MLI 484
{ "SYLL_NOT_DISPERSED_EXPL_OR_MLI", {0x9004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits or to MLI bundle and resteers to non-0 syllable."},
#define PME_ITA2_SYLL_NOT_DISPERSED_FE 485
{ "SYLL_NOT_DISPERSED_FE", {0x4004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to front-end not providing valid bundles or providing valid illegal templates. Dispersal takes a 3-syllable hit for every invalid bundle or valid illegal template from front-end. Bundle 1 with front-end fault, is counted here (3-syllable hit).."},
#define PME_ITA2_SYLL_NOT_DISPERSED_FE_OR_MLI 486
{ "SYLL_NOT_DISPERSED_FE_OR_MLI", {0xc004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to MLI bundle and resteers to non-0 syllable or due to front-end not providing valid bundles or providing valid illegal templates."},
#define PME_ITA2_SYLL_NOT_DISPERSED_IMPL 487
{ "SYLL_NOT_DISPERSED_IMPL", {0x2004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits. These consist of all of the non-architected stop bits (asymmetry, oversubscription, implicit). Dispersal takes a 6-syllable(3-syllable) hit for every implicit stop bits in bundle 0(1)."},
#define PME_ITA2_SYLL_NOT_DISPERSED_IMPL_OR_FE 488
{ "SYLL_NOT_DISPERSED_IMPL_OR_FE", {0x6004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits or to front-end not providing valid bundles or providing valid illegal templates."},
#define PME_ITA2_SYLL_NOT_DISPERSED_IMPL_OR_FE_OR_MLI 489
{ "SYLL_NOT_DISPERSED_IMPL_OR_FE_OR_MLI", {0xe004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits or due to front-end not providing valid bundles or providing valid illegal templates or due to MLI bundle and resteers to non-0 syllable."},
#define PME_ITA2_SYLL_NOT_DISPERSED_IMPL_OR_MLI 490
{ "SYLL_NOT_DISPERSED_IMPL_OR_MLI", {0xa004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits or to MLI bundle and resteers to non-0 syllable."},
#define PME_ITA2_SYLL_NOT_DISPERSED_MLI 491
{ "SYLL_NOT_DISPERSED_MLI", {0x8004e}, 0xf0, 5, {0xf00001}, "Syllables Not Dispersed -- Count syllables not dispersed due to MLI bundle and resteers to non-0 syllable. Dispersal takes a 1 syllable hit for each MLI bundle . Dispersal could take 0-2 syllable hit depending on which syllable we resteer to. Bundle 1 with front-end fault which is split, is counted here (0-2 syllable hit)."},
#define PME_ITA2_SYLL_OVERCOUNT_ALL 492
{ "SYLL_OVERCOUNT_ALL", {0x3004f}, 0xf0, 2, {0xf00001}, "Syllables Overcounted -- syllables overcounted in implicit & explicit bucket"},
#define PME_ITA2_SYLL_OVERCOUNT_EXPL 493
{ "SYLL_OVERCOUNT_EXPL", {0x1004f}, 0xf0, 2, {0xf00001}, "Syllables Overcounted -- Only syllables overcounted in the explicit bucket"},
#define PME_ITA2_SYLL_OVERCOUNT_IMPL 494
{ "SYLL_OVERCOUNT_IMPL", {0x2004f}, 0xf0, 2, {0xf00001}, "Syllables Overcounted -- Only syllables overcounted in the implicit bucket"},
#define PME_ITA2_UC_LOADS_RETIRED 495
{ "UC_LOADS_RETIRED", {0xcf}, 0xf0, 4, {0x5310007}, "Retired Uncacheable Loads"},
#define PME_ITA2_UC_STORES_RETIRED 496
{ "UC_STORES_RETIRED", {0xd0}, 0xf0, 2, {0x5410007}, "Retired Uncacheable Stores"},
};
#define PME_ITA2_EVENT_COUNT 497
