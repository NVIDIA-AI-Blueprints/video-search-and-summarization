/*
 * Copyright (c) 2006 Hewlett-Packard Development Company, L.P.
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

static pme_mont_entry_t montecito_pe []={
#define PME_MONT_ALAT_CAPACITY_MISS_ALL 0
{ "ALAT_CAPACITY_MISS_ALL", {0x30058}, 0xfff0, 2, {0xffff0007}, "ALAT Entry Replaced -- both integer and floating point instructions"},
#define PME_MONT_ALAT_CAPACITY_MISS_FP 1
{ "ALAT_CAPACITY_MISS_FP", {0x20058}, 0xfff0, 2, {0xffff0007}, "ALAT Entry Replaced -- only floating point instructions"},
#define PME_MONT_ALAT_CAPACITY_MISS_INT 2
{ "ALAT_CAPACITY_MISS_INT", {0x10058}, 0xfff0, 2, {0xffff0007}, "ALAT Entry Replaced -- only integer instructions"},
#define PME_MONT_BACK_END_BUBBLE_ALL 3
{ "BACK_END_BUBBLE_ALL", {0x0}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe -- Front-end, RSE, EXE, FPU/L1D stall or a pipeline flush due to an exception/branch misprediction"},
#define PME_MONT_BACK_END_BUBBLE_FE 4
{ "BACK_END_BUBBLE_FE", {0x10000}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe -- front-end"},
#define PME_MONT_BACK_END_BUBBLE_L1D_FPU_RSE 5
{ "BACK_END_BUBBLE_L1D_FPU_RSE", {0x20000}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe -- L1D_FPU or RSE."},
#define PME_MONT_BE_BR_MISPRED_DETAIL_ANY 6
{ "BE_BR_MISPRED_DETAIL_ANY", {0x61}, 0xfff0, 1, {0xffff0003}, "BE Branch Misprediction Detail -- any back-end (be) mispredictions"},
#define PME_MONT_BE_BR_MISPRED_DETAIL_PFS 7
{ "BE_BR_MISPRED_DETAIL_PFS", {0x30061}, 0xfff0, 1, {0xffff0003}, "BE Branch Misprediction Detail -- only back-end pfs mispredictions for taken branches"},
#define PME_MONT_BE_BR_MISPRED_DETAIL_ROT 8
{ "BE_BR_MISPRED_DETAIL_ROT", {0x20061}, 0xfff0, 1, {0xffff0003}, "BE Branch Misprediction Detail -- only back-end rotate mispredictions"},
#define PME_MONT_BE_BR_MISPRED_DETAIL_STG 9
{ "BE_BR_MISPRED_DETAIL_STG", {0x10061}, 0xfff0, 1, {0xffff0003}, "BE Branch Misprediction Detail -- only back-end stage mispredictions"},
#define PME_MONT_BE_EXE_BUBBLE_ALL 10
{ "BE_EXE_BUBBLE_ALL", {0x2}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe"},
#define PME_MONT_BE_EXE_BUBBLE_ARCR 11
{ "BE_EXE_BUBBLE_ARCR", {0x40002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to AR or CR dependency"},
#define PME_MONT_BE_EXE_BUBBLE_ARCR_PR_CANCEL_BANK 12
{ "BE_EXE_BUBBLE_ARCR_PR_CANCEL_BANK", {0x80002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- ARCR, PR, CANCEL or BANK_SWITCH"},
#define PME_MONT_BE_EXE_BUBBLE_BANK_SWITCH 13
{ "BE_EXE_BUBBLE_BANK_SWITCH", {0x70002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to bank switching."},
#define PME_MONT_BE_EXE_BUBBLE_CANCEL 14
{ "BE_EXE_BUBBLE_CANCEL", {0x60002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to a canceled load"},
#define PME_MONT_BE_EXE_BUBBLE_FRALL 15
{ "BE_EXE_BUBBLE_FRALL", {0x20002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to FR/FR or FR/load dependency"},
#define PME_MONT_BE_EXE_BUBBLE_GRALL 16
{ "BE_EXE_BUBBLE_GRALL", {0x10002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to GR/GR or GR/load dependency"},
#define PME_MONT_BE_EXE_BUBBLE_GRGR 17
{ "BE_EXE_BUBBLE_GRGR", {0x50002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to GR/GR dependency"},
#define PME_MONT_BE_EXE_BUBBLE_PR 18
{ "BE_EXE_BUBBLE_PR", {0x30002}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Execution Unit Stalls -- Back-end was stalled by exe due to PR dependency"},
#define PME_MONT_BE_FLUSH_BUBBLE_ALL 19
{ "BE_FLUSH_BUBBLE_ALL", {0x4}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Flushes. -- Back-end was stalled due to either an exception/interruption or branch misprediction flush"},
#define PME_MONT_BE_FLUSH_BUBBLE_BRU 20
{ "BE_FLUSH_BUBBLE_BRU", {0x10004}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Flushes. -- Back-end was stalled due to a branch misprediction flush"},
#define PME_MONT_BE_FLUSH_BUBBLE_XPN 21
{ "BE_FLUSH_BUBBLE_XPN", {0x20004}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to Flushes. -- Back-end was stalled due to an exception/interruption flush"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_ALL 22
{ "BE_L1D_FPU_BUBBLE_ALL", {0xca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D or FPU"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_FPU 23
{ "BE_L1D_FPU_BUBBLE_FPU", {0x100ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by FPU."},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D 24
{ "BE_L1D_FPU_BUBBLE_L1D", {0x200ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D. This includes all stalls caused by the L1 pipeline (created in the L1D stage of the L1 pipeline which corresponds to the DET stage of the main pipe)."},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_AR_CR 25
{ "BE_L1D_FPU_BUBBLE_L1D_AR_CR", {0x800ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to ar/cr requiring a stall"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_FILLCONF 26
{ "BE_L1D_FPU_BUBBLE_L1D_FILLCONF", {0x700ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due a store in conflict with a returning fill."},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_FULLSTBUF 27
{ "BE_L1D_FPU_BUBBLE_L1D_FULLSTBUF", {0x300ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to store buffer being full"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_HPW 28
{ "BE_L1D_FPU_BUBBLE_L1D_HPW", {0x500ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to Hardware Page Walker"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_L2BPRESS 29
{ "BE_L1D_FPU_BUBBLE_L1D_L2BPRESS", {0x900ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to L2 Back Pressure"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_LDCHK 30
{ "BE_L1D_FPU_BUBBLE_L1D_LDCHK", {0xc00ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to architectural ordering conflict"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_LDCONF 31
{ "BE_L1D_FPU_BUBBLE_L1D_LDCONF", {0xb00ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to architectural ordering conflict"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_NAT 32
{ "BE_L1D_FPU_BUBBLE_L1D_NAT", {0xd00ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to L1D data return needing recirculated NaT generation."},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_NATCONF 33
{ "BE_L1D_FPU_BUBBLE_L1D_NATCONF", {0xf00ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to ld8.fill conflict with st8.spill not written to unat."},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_PIPE_RECIRC 34
{ "BE_L1D_FPU_BUBBLE_L1D_PIPE_RECIRC", {0x400ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to recirculate"},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_STBUFRECIR 35
{ "BE_L1D_FPU_BUBBLE_L1D_STBUFRECIR", {0xe00ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to store buffer cancel needing recirculate."},
#define PME_MONT_BE_L1D_FPU_BUBBLE_L1D_TLB 36
{ "BE_L1D_FPU_BUBBLE_L1D_TLB", {0xa00ca}, 0xfff0, 1, {0x5210000}, "Full Pipe Bubbles in Main Pipe due to FPU or L1D Cache -- Back-end was stalled by L1D due to L2DTLB to L1DTLB transfer"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_ALL 37
{ "BE_LOST_BW_DUE_TO_FE_ALL", {0x72}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- count regardless of cause"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_BI 38
{ "BE_LOST_BW_DUE_TO_FE_BI", {0x90072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch initialization stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_BRQ 39
{ "BE_LOST_BW_DUE_TO_FE_BRQ", {0xa0072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch retirement queue stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_BR_ILOCK 40
{ "BE_LOST_BW_DUE_TO_FE_BR_ILOCK", {0xc0072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch interlock stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_BUBBLE 41
{ "BE_LOST_BW_DUE_TO_FE_BUBBLE", {0xd0072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by branch resteer bubble stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_FEFLUSH 42
{ "BE_LOST_BW_DUE_TO_FE_FEFLUSH", {0x10072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by a front-end flush"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_FILL_RECIRC 43
{ "BE_LOST_BW_DUE_TO_FE_FILL_RECIRC", {0x80072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by a recirculate for a cache line fill operation"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_IBFULL 44
{ "BE_LOST_BW_DUE_TO_FE_IBFULL", {0x50072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- (* meaningless for this event *)"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_IMISS 45
{ "BE_LOST_BW_DUE_TO_FE_IMISS", {0x60072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by instruction cache miss stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_PLP 46
{ "BE_LOST_BW_DUE_TO_FE_PLP", {0xb0072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by perfect loop prediction stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_TLBMISS 47
{ "BE_LOST_BW_DUE_TO_FE_TLBMISS", {0x70072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by TLB stall"},
#define PME_MONT_BE_LOST_BW_DUE_TO_FE_UNREACHED 48
{ "BE_LOST_BW_DUE_TO_FE_UNREACHED", {0x40072}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles if BE Not Stalled for Other Reasons. -- only if caused by unreachable bundle"},
#define PME_MONT_BE_RSE_BUBBLE_ALL 49
{ "BE_RSE_BUBBLE_ALL", {0x1}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE"},
#define PME_MONT_BE_RSE_BUBBLE_AR_DEP 50
{ "BE_RSE_BUBBLE_AR_DEP", {0x20001}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to AR dependencies"},
#define PME_MONT_BE_RSE_BUBBLE_BANK_SWITCH 51
{ "BE_RSE_BUBBLE_BANK_SWITCH", {0x10001}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to bank switching"},
#define PME_MONT_BE_RSE_BUBBLE_LOADRS 52
{ "BE_RSE_BUBBLE_LOADRS", {0x50001}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to loadrs calculations"},
#define PME_MONT_BE_RSE_BUBBLE_OVERFLOW 53
{ "BE_RSE_BUBBLE_OVERFLOW", {0x30001}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to need to spill"},
#define PME_MONT_BE_RSE_BUBBLE_UNDERFLOW 54
{ "BE_RSE_BUBBLE_UNDERFLOW", {0x40001}, 0xfff0, 1, {0xffff0000}, "Full Pipe Bubbles in Main Pipe due to RSE Stalls -- Back-end was stalled by RSE due to need to fill"},
#define PME_MONT_BR_MISPRED_DETAIL_ALL_ALL_PRED 55
{ "BR_MISPRED_DETAIL_ALL_ALL_PRED", {0x5b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- All branch types regardless of prediction result"},
#define PME_MONT_BR_MISPRED_DETAIL_ALL_CORRECT_PRED 56
{ "BR_MISPRED_DETAIL_ALL_CORRECT_PRED", {0x1005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- All branch types, correctly predicted branches (outcome and target)"},
#define PME_MONT_BR_MISPRED_DETAIL_ALL_WRONG_PATH 57
{ "BR_MISPRED_DETAIL_ALL_WRONG_PATH", {0x2005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- All branch types, mispredicted branches due to wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL_ALL_WRONG_TARGET 58
{ "BR_MISPRED_DETAIL_ALL_WRONG_TARGET", {0x3005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- All branch types, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_BR_MISPRED_DETAIL_IPREL_ALL_PRED 59
{ "BR_MISPRED_DETAIL_IPREL_ALL_PRED", {0x4005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only IP relative branches, regardless of prediction result"},
#define PME_MONT_BR_MISPRED_DETAIL_IPREL_CORRECT_PRED 60
{ "BR_MISPRED_DETAIL_IPREL_CORRECT_PRED", {0x5005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only IP relative branches, correctly predicted branches (outcome and target)"},
#define PME_MONT_BR_MISPRED_DETAIL_IPREL_WRONG_PATH 61
{ "BR_MISPRED_DETAIL_IPREL_WRONG_PATH", {0x6005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only IP relative branches, mispredicted branches due to wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL_IPREL_WRONG_TARGET 62
{ "BR_MISPRED_DETAIL_IPREL_WRONG_TARGET", {0x7005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only IP relative branches, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_BR_MISPRED_DETAIL_NRETIND_ALL_PRED 63
{ "BR_MISPRED_DETAIL_NRETIND_ALL_PRED", {0xc005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, regardless of prediction result"},
#define PME_MONT_BR_MISPRED_DETAIL_NRETIND_CORRECT_PRED 64
{ "BR_MISPRED_DETAIL_NRETIND_CORRECT_PRED", {0xd005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, correctly predicted branches (outcome and target)"},
#define PME_MONT_BR_MISPRED_DETAIL_NRETIND_WRONG_PATH 65
{ "BR_MISPRED_DETAIL_NRETIND_WRONG_PATH", {0xe005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, mispredicted branches due to wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL_NRETIND_WRONG_TARGET 66
{ "BR_MISPRED_DETAIL_NRETIND_WRONG_TARGET", {0xf005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only non-return indirect branches, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_BR_MISPRED_DETAIL_RETURN_ALL_PRED 67
{ "BR_MISPRED_DETAIL_RETURN_ALL_PRED", {0x8005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only return type branches, regardless of prediction result"},
#define PME_MONT_BR_MISPRED_DETAIL_RETURN_CORRECT_PRED 68
{ "BR_MISPRED_DETAIL_RETURN_CORRECT_PRED", {0x9005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only return type branches, correctly predicted branches (outcome and target)"},
#define PME_MONT_BR_MISPRED_DETAIL_RETURN_WRONG_PATH 69
{ "BR_MISPRED_DETAIL_RETURN_WRONG_PATH", {0xa005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only return type branches, mispredicted branches due to wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL_RETURN_WRONG_TARGET 70
{ "BR_MISPRED_DETAIL_RETURN_WRONG_TARGET", {0xb005b}, 0xfff0, 3, {0xffff0003}, "FE Branch Mispredict Detail -- Only return type branches, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_BR_MISPRED_DETAIL2_ALL_ALL_UNKNOWN_PRED 71
{ "BR_MISPRED_DETAIL2_ALL_ALL_UNKNOWN_PRED", {0x68}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- All branch types, branches with unknown path prediction"},
#define PME_MONT_BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_CORRECT_PRED 72
{ "BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_CORRECT_PRED", {0x10068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- All branch types, branches with unknown path prediction and correctly predicted branch (outcome & target)"},
#define PME_MONT_BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_WRONG_PATH 73
{ "BR_MISPRED_DETAIL2_ALL_UNKNOWN_PATH_WRONG_PATH", {0x20068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- All branch types, branches with unknown path prediction and wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL2_IPREL_ALL_UNKNOWN_PRED 74
{ "BR_MISPRED_DETAIL2_IPREL_ALL_UNKNOWN_PRED", {0x40068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only IP relative branches, branches with unknown path prediction"},
#define PME_MONT_BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_CORRECT_PRED 75
{ "BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_CORRECT_PRED", {0x50068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only IP relative branches, branches with unknown path prediction and correct predicted branch (outcome & target)"},
#define PME_MONT_BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_WRONG_PATH 76
{ "BR_MISPRED_DETAIL2_IPREL_UNKNOWN_PATH_WRONG_PATH", {0x60068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only IP relative branches, branches with unknown path prediction and wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL2_NRETIND_ALL_UNKNOWN_PRED 77
{ "BR_MISPRED_DETAIL2_NRETIND_ALL_UNKNOWN_PRED", {0xc0068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only non-return indirect branches, branches with unknown path prediction"},
#define PME_MONT_BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_CORRECT_PRED 78
{ "BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_CORRECT_PRED", {0xd0068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only non-return indirect branches, branches with unknown path prediction and correct predicted branch (outcome & target)"},
#define PME_MONT_BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_WRONG_PATH 79
{ "BR_MISPRED_DETAIL2_NRETIND_UNKNOWN_PATH_WRONG_PATH", {0xe0068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only non-return indirect branches, branches with unknown path prediction and wrong branch direction"},
#define PME_MONT_BR_MISPRED_DETAIL2_RETURN_ALL_UNKNOWN_PRED 80
{ "BR_MISPRED_DETAIL2_RETURN_ALL_UNKNOWN_PRED", {0x80068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only return type branches, branches with unknown path prediction"},
#define PME_MONT_BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_CORRECT_PRED 81
{ "BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_CORRECT_PRED", {0x90068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only return type branches, branches with unknown path prediction and correct predicted branch (outcome & target)"},
#define PME_MONT_BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_WRONG_PATH 82
{ "BR_MISPRED_DETAIL2_RETURN_UNKNOWN_PATH_WRONG_PATH", {0xa0068}, 0xfff0, 2, {0xffff0003}, "FE Branch Mispredict Detail (Unknown Path Component) -- Only return type branches, branches with unknown path prediction and wrong branch direction"},
#define PME_MONT_BR_PATH_PRED_ALL_MISPRED_NOTTAKEN 83
{ "BR_PATH_PRED_ALL_MISPRED_NOTTAKEN", {0x54}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- All branch types, incorrectly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_ALL_MISPRED_TAKEN 84
{ "BR_PATH_PRED_ALL_MISPRED_TAKEN", {0x10054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- All branch types, incorrectly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_ALL_OKPRED_NOTTAKEN 85
{ "BR_PATH_PRED_ALL_OKPRED_NOTTAKEN", {0x20054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- All branch types, correctly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_ALL_OKPRED_TAKEN 86
{ "BR_PATH_PRED_ALL_OKPRED_TAKEN", {0x30054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- All branch types, correctly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_IPREL_MISPRED_NOTTAKEN 87
{ "BR_PATH_PRED_IPREL_MISPRED_NOTTAKEN", {0x40054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only IP relative branches, incorrectly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_IPREL_MISPRED_TAKEN 88
{ "BR_PATH_PRED_IPREL_MISPRED_TAKEN", {0x50054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only IP relative branches, incorrectly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_IPREL_OKPRED_NOTTAKEN 89
{ "BR_PATH_PRED_IPREL_OKPRED_NOTTAKEN", {0x60054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only IP relative branches, correctly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_IPREL_OKPRED_TAKEN 90
{ "BR_PATH_PRED_IPREL_OKPRED_TAKEN", {0x70054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only IP relative branches, correctly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_NRETIND_MISPRED_NOTTAKEN 91
{ "BR_PATH_PRED_NRETIND_MISPRED_NOTTAKEN", {0xc0054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, incorrectly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_NRETIND_MISPRED_TAKEN 92
{ "BR_PATH_PRED_NRETIND_MISPRED_TAKEN", {0xd0054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, incorrectly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_NRETIND_OKPRED_NOTTAKEN 93
{ "BR_PATH_PRED_NRETIND_OKPRED_NOTTAKEN", {0xe0054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, correctly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_NRETIND_OKPRED_TAKEN 94
{ "BR_PATH_PRED_NRETIND_OKPRED_TAKEN", {0xf0054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only non-return indirect branches, correctly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_RETURN_MISPRED_NOTTAKEN 95
{ "BR_PATH_PRED_RETURN_MISPRED_NOTTAKEN", {0x80054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only return type branches, incorrectly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_RETURN_MISPRED_TAKEN 96
{ "BR_PATH_PRED_RETURN_MISPRED_TAKEN", {0x90054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only return type branches, incorrectly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED_RETURN_OKPRED_NOTTAKEN 97
{ "BR_PATH_PRED_RETURN_OKPRED_NOTTAKEN", {0xa0054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only return type branches, correctly predicted path and not taken branch"},
#define PME_MONT_BR_PATH_PRED_RETURN_OKPRED_TAKEN 98
{ "BR_PATH_PRED_RETURN_OKPRED_TAKEN", {0xb0054}, 0xfff0, 3, {0xffff0003}, "FE Branch Path Prediction Detail -- Only return type branches, correctly predicted path and taken branch"},
#define PME_MONT_BR_PATH_PRED2_ALL_UNKNOWNPRED_NOTTAKEN 99
{ "BR_PATH_PRED2_ALL_UNKNOWNPRED_NOTTAKEN", {0x6a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- All branch types, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_MONT_BR_PATH_PRED2_ALL_UNKNOWNPRED_TAKEN 100
{ "BR_PATH_PRED2_ALL_UNKNOWNPRED_TAKEN", {0x1006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- All branch types, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_MONT_BR_PATH_PRED2_IPREL_UNKNOWNPRED_NOTTAKEN 101
{ "BR_PATH_PRED2_IPREL_UNKNOWNPRED_NOTTAKEN", {0x4006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only IP relative branches, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_MONT_BR_PATH_PRED2_IPREL_UNKNOWNPRED_TAKEN 102
{ "BR_PATH_PRED2_IPREL_UNKNOWNPRED_TAKEN", {0x5006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only IP relative branches, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_MONT_BR_PATH_PRED2_NRETIND_UNKNOWNPRED_NOTTAKEN 103
{ "BR_PATH_PRED2_NRETIND_UNKNOWNPRED_NOTTAKEN", {0xc006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only non-return indirect branches, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_MONT_BR_PATH_PRED2_NRETIND_UNKNOWNPRED_TAKEN 104
{ "BR_PATH_PRED2_NRETIND_UNKNOWNPRED_TAKEN", {0xd006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only non-return indirect branches, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_MONT_BR_PATH_PRED2_RETURN_UNKNOWNPRED_NOTTAKEN 105
{ "BR_PATH_PRED2_RETURN_UNKNOWNPRED_NOTTAKEN", {0x8006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only return type branches, unknown predicted path and not taken branch (which impacts OKPRED_NOTTAKEN)"},
#define PME_MONT_BR_PATH_PRED2_RETURN_UNKNOWNPRED_TAKEN 106
{ "BR_PATH_PRED2_RETURN_UNKNOWNPRED_TAKEN", {0x9006a}, 0xfff0, 2, {0xffff0003}, "FE Branch Path Prediction Detail (Unknown pred component) -- Only return type branches, unknown predicted path and taken branch (which impacts MISPRED_TAKEN)"},
#define PME_MONT_BUS_ALL_ANY 107
{ "BUS_ALL_ANY", {0x31887}, 0x03f0, 1, {0xffff0000}, "Bus Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_ALL_EITHER 108
{ "BUS_ALL_EITHER", {0x1887}, 0x03f0, 1, {0xffff0000}, "Bus Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_ALL_IO 109
{ "BUS_ALL_IO", {0x11887}, 0x03f0, 1, {0xffff0000}, "Bus Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_ALL_SELF 110
{ "BUS_ALL_SELF", {0x21887}, 0x03f0, 1, {0xffff0000}, "Bus Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_B2B_DATA_CYCLES_ANY 111
{ "BUS_B2B_DATA_CYCLES_ANY", {0x31093}, 0x03f0, 1, {0xffff0000}, "Back to Back Data Cycles on the Bus -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_B2B_DATA_CYCLES_EITHER 112
{ "BUS_B2B_DATA_CYCLES_EITHER", {0x1093}, 0x03f0, 1, {0xffff0000}, "Back to Back Data Cycles on the Bus -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_B2B_DATA_CYCLES_IO 113
{ "BUS_B2B_DATA_CYCLES_IO", {0x11093}, 0x03f0, 1, {0xffff0000}, "Back to Back Data Cycles on the Bus -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_B2B_DATA_CYCLES_SELF 114
{ "BUS_B2B_DATA_CYCLES_SELF", {0x21093}, 0x03f0, 1, {0xffff0000}, "Back to Back Data Cycles on the Bus -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_DATA_CYCLE_ANY 115
{ "BUS_DATA_CYCLE_ANY", {0x31088}, 0x03f0, 1, {0xffff0000}, "Valid Data Cycle on the Bus -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_DATA_CYCLE_EITHER 116
{ "BUS_DATA_CYCLE_EITHER", {0x1088}, 0x03f0, 1, {0xffff0000}, "Valid Data Cycle on the Bus -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_DATA_CYCLE_IO 117
{ "BUS_DATA_CYCLE_IO", {0x11088}, 0x03f0, 1, {0xffff0000}, "Valid Data Cycle on the Bus -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_DATA_CYCLE_SELF 118
{ "BUS_DATA_CYCLE_SELF", {0x21088}, 0x03f0, 1, {0xffff0000}, "Valid Data Cycle on the Bus -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_HITM_ANY 119
{ "BUS_HITM_ANY", {0x31884}, 0x03f0, 1, {0xffff0000}, "Bus Hit Modified Line Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_HITM_EITHER 120
{ "BUS_HITM_EITHER", {0x1884}, 0x03f0, 1, {0xffff0000}, "Bus Hit Modified Line Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_HITM_IO 121
{ "BUS_HITM_IO", {0x11884}, 0x03f0, 1, {0xffff0000}, "Bus Hit Modified Line Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_HITM_SELF 122
{ "BUS_HITM_SELF", {0x21884}, 0x03f0, 1, {0xffff0000}, "Bus Hit Modified Line Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_IO_ANY 123
{ "BUS_IO_ANY", {0x31890}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Bus Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_IO_EITHER 124
{ "BUS_IO_EITHER", {0x1890}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Bus Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_IO_IO 125
{ "BUS_IO_IO", {0x11890}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Bus Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_IO_SELF 126
{ "BUS_IO_SELF", {0x21890}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Bus Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_MEMORY_ALL_ANY 127
{ "BUS_MEMORY_ALL_ANY", {0xf188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- All bus transactions from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEMORY_ALL_EITHER 128
{ "BUS_MEMORY_ALL_EITHER", {0xc188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- All bus transactions from non-CPU priority agents"},
#define PME_MONT_BUS_MEMORY_ALL_IO 129
{ "BUS_MEMORY_ALL_IO", {0xd188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- All bus transactions from 'this' local processor"},
#define PME_MONT_BUS_MEMORY_ALL_SELF 130
{ "BUS_MEMORY_ALL_SELF", {0xe188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- All bus transactions from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEMORY_EQ_128BYTE_ANY 131
{ "BUS_MEMORY_EQ_128BYTE_ANY", {0x7188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP, BIL) from either local processor"},
#define PME_MONT_BUS_MEMORY_EQ_128BYTE_EITHER 132
{ "BUS_MEMORY_EQ_128BYTE_EITHER", {0x4188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of full cache line transactions (BRL, BRIL, BWL, BRC, BCR, BCCL) from non-CPU priority agents"},
#define PME_MONT_BUS_MEMORY_EQ_128BYTE_IO 133
{ "BUS_MEMORY_EQ_128BYTE_IO", {0x5188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of full cache line transactions (BRL, BRIL, BWL, BRC, BCR, BCCL) from 'this' processor"},
#define PME_MONT_BUS_MEMORY_EQ_128BYTE_SELF 134
{ "BUS_MEMORY_EQ_128BYTE_SELF", {0x6188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of full cache line transactions (BRL, BRIL, BWL, BRC, BCR, BCCL) from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEMORY_LT_128BYTE_ANY 135
{ "BUS_MEMORY_LT_128BYTE_ANY", {0xb188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- All bus transactions from either local processor"},
#define PME_MONT_BUS_MEMORY_LT_128BYTE_EITHER 136
{ "BUS_MEMORY_LT_128BYTE_EITHER", {0x8188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP, BIL) from non-CPU priority agents"},
#define PME_MONT_BUS_MEMORY_LT_128BYTE_IO 137
{ "BUS_MEMORY_LT_128BYTE_IO", {0x9188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP, BIL) from 'this' processor"},
#define PME_MONT_BUS_MEMORY_LT_128BYTE_SELF 138
{ "BUS_MEMORY_LT_128BYTE_SELF", {0xa188a}, 0x03f0, 1, {0xffff0000}, "Bus Memory Transactions -- number of less than full cache line transactions (BRP, BWP, BIL) CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEM_READ_ALL_ANY 139
{ "BUS_MEM_READ_ALL_ANY", {0xf188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEM_READ_ALL_EITHER 140
{ "BUS_MEM_READ_ALL_EITHER", {0xc188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from either local processor"},
#define PME_MONT_BUS_MEM_READ_ALL_IO 141
{ "BUS_MEM_READ_ALL_IO", {0xd188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from non-CPU priority agents"},
#define PME_MONT_BUS_MEM_READ_ALL_SELF 142
{ "BUS_MEM_READ_ALL_SELF", {0xe188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- All memory read transactions from local processor"},
#define PME_MONT_BUS_MEM_READ_BIL_ANY 143
{ "BUS_MEM_READ_BIL_ANY", {0x3188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEM_READ_BIL_EITHER 144
{ "BUS_MEM_READ_BIL_EITHER", {0x188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from either local processor"},
#define PME_MONT_BUS_MEM_READ_BIL_IO 145
{ "BUS_MEM_READ_BIL_IO", {0x1188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from non-CPU priority agents"},
#define PME_MONT_BUS_MEM_READ_BIL_SELF 146
{ "BUS_MEM_READ_BIL_SELF", {0x2188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of BIL 0-byte memory read invalidate transactions from local processor"},
#define PME_MONT_BUS_MEM_READ_BRIL_ANY 147
{ "BUS_MEM_READ_BRIL_ANY", {0xb188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEM_READ_BRIL_EITHER 148
{ "BUS_MEM_READ_BRIL_EITHER", {0x8188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from either local processor"},
#define PME_MONT_BUS_MEM_READ_BRIL_IO 149
{ "BUS_MEM_READ_BRIL_IO", {0x9188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from non-CPU priority agents"},
#define PME_MONT_BUS_MEM_READ_BRIL_SELF 150
{ "BUS_MEM_READ_BRIL_SELF", {0xa188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read invalidate transactions from local processor"},
#define PME_MONT_BUS_MEM_READ_BRL_ANY 151
{ "BUS_MEM_READ_BRL_ANY", {0x7188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_MEM_READ_BRL_EITHER 152
{ "BUS_MEM_READ_BRL_EITHER", {0x4188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from either local processor"},
#define PME_MONT_BUS_MEM_READ_BRL_IO 153
{ "BUS_MEM_READ_BRL_IO", {0x5188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from non-CPU priority agents"},
#define PME_MONT_BUS_MEM_READ_BRL_SELF 154
{ "BUS_MEM_READ_BRL_SELF", {0x6188b}, 0x03f0, 1, {0xffff0000}, "Full Cache Line D/I Memory RD, RD Invalidate, and BRIL -- Number of full cache line memory read transactions from local processor"},
#define PME_MONT_BUS_RD_DATA_ANY 155
{ "BUS_RD_DATA_ANY", {0x3188c}, 0x03f0, 1, {0xffff0000}, "Bus Read Data Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_DATA_EITHER 156
{ "BUS_RD_DATA_EITHER", {0x188c}, 0x03f0, 1, {0xffff0000}, "Bus Read Data Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_DATA_IO 157
{ "BUS_RD_DATA_IO", {0x1188c}, 0x03f0, 1, {0xffff0000}, "Bus Read Data Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_DATA_SELF 158
{ "BUS_RD_DATA_SELF", {0x2188c}, 0x03f0, 1, {0xffff0000}, "Bus Read Data Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_RD_HIT_ANY 159
{ "BUS_RD_HIT_ANY", {0x31880}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Clean Non-local Cache Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_HIT_EITHER 160
{ "BUS_RD_HIT_EITHER", {0x1880}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Clean Non-local Cache Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_HIT_IO 161
{ "BUS_RD_HIT_IO", {0x11880}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Clean Non-local Cache Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_HIT_SELF 162
{ "BUS_RD_HIT_SELF", {0x21880}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Clean Non-local Cache Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_RD_HITM_ANY 163
{ "BUS_RD_HITM_ANY", {0x31881}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Modified Non-local Cache Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_HITM_EITHER 164
{ "BUS_RD_HITM_EITHER", {0x1881}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Modified Non-local Cache Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_HITM_IO 165
{ "BUS_RD_HITM_IO", {0x11881}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Modified Non-local Cache Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_HITM_SELF 166
{ "BUS_RD_HITM_SELF", {0x21881}, 0x03f0, 1, {0xffff0000}, "Bus Read Hit Modified Non-local Cache Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_RD_INVAL_BST_HITM_ANY 167
{ "BUS_RD_INVAL_BST_HITM_ANY", {0x31883}, 0x03f0, 1, {0xffff0000}, "Bus BRIL Transaction Results in HITM -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_INVAL_BST_HITM_EITHER 168
{ "BUS_RD_INVAL_BST_HITM_EITHER", {0x1883}, 0x03f0, 1, {0xffff0000}, "Bus BRIL Transaction Results in HITM -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_INVAL_BST_HITM_IO 169
{ "BUS_RD_INVAL_BST_HITM_IO", {0x11883}, 0x03f0, 1, {0xffff0000}, "Bus BRIL Transaction Results in HITM -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_INVAL_BST_HITM_SELF 170
{ "BUS_RD_INVAL_BST_HITM_SELF", {0x21883}, 0x03f0, 1, {0xffff0000}, "Bus BRIL Transaction Results in HITM -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_RD_INVAL_HITM_ANY 171
{ "BUS_RD_INVAL_HITM_ANY", {0x31882}, 0x03f0, 1, {0xffff0000}, "Bus BIL Transaction Results in HITM -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_INVAL_HITM_EITHER 172
{ "BUS_RD_INVAL_HITM_EITHER", {0x1882}, 0x03f0, 1, {0xffff0000}, "Bus BIL Transaction Results in HITM -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_INVAL_HITM_IO 173
{ "BUS_RD_INVAL_HITM_IO", {0x11882}, 0x03f0, 1, {0xffff0000}, "Bus BIL Transaction Results in HITM -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_INVAL_HITM_SELF 174
{ "BUS_RD_INVAL_HITM_SELF", {0x21882}, 0x03f0, 1, {0xffff0000}, "Bus BIL Transaction Results in HITM -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_RD_IO_ANY 175
{ "BUS_RD_IO_ANY", {0x31891}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Read Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_IO_EITHER 176
{ "BUS_RD_IO_EITHER", {0x1891}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Read Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_IO_IO 177
{ "BUS_RD_IO_IO", {0x11891}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Read Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_IO_SELF 178
{ "BUS_RD_IO_SELF", {0x21891}, 0x03f0, 1, {0xffff0000}, "IA-32 Compatible IO Read Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_RD_PRTL_ANY 179
{ "BUS_RD_PRTL_ANY", {0x3188d}, 0x03f0, 1, {0xffff0000}, "Bus Read Partial Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_RD_PRTL_EITHER 180
{ "BUS_RD_PRTL_EITHER", {0x188d}, 0x03f0, 1, {0xffff0000}, "Bus Read Partial Transactions -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_RD_PRTL_IO 181
{ "BUS_RD_PRTL_IO", {0x1188d}, 0x03f0, 1, {0xffff0000}, "Bus Read Partial Transactions -- transactions initiated by non-CPU priority agents"},
#define PME_MONT_BUS_RD_PRTL_SELF 182
{ "BUS_RD_PRTL_SELF", {0x2188d}, 0x03f0, 1, {0xffff0000}, "Bus Read Partial Transactions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_BUS_SNOOP_STALL_CYCLES_ANY 183
{ "BUS_SNOOP_STALL_CYCLES_ANY", {0x3188f}, 0x03f0, 1, {0xffff0000}, "Bus Snoop Stall Cycles (from any agent) -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_SNOOP_STALL_CYCLES_EITHER 184
{ "BUS_SNOOP_STALL_CYCLES_EITHER", {0x188f}, 0x03f0, 1, {0xffff0000}, "Bus Snoop Stall Cycles (from any agent) -- transactions initiated by either cpu core"},
#define PME_MONT_BUS_SNOOP_STALL_CYCLES_SELF 185
{ "BUS_SNOOP_STALL_CYCLES_SELF", {0x2188f}, 0x03f0, 1, {0xffff0000}, "Bus Snoop Stall Cycles (from any agent) -- local processor"},
#define PME_MONT_BUS_WR_WB_ALL_ANY 186
{ "BUS_WR_WB_ALL_ANY", {0xf1892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- CPU or non-CPU (all transactions)."},
#define PME_MONT_BUS_WR_WB_ALL_IO 187
{ "BUS_WR_WB_ALL_IO", {0xd1892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- non-CPU priority agents"},
#define PME_MONT_BUS_WR_WB_ALL_SELF 188
{ "BUS_WR_WB_ALL_SELF", {0xe1892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- this'  processor"},
#define PME_MONT_BUS_WR_WB_CCASTOUT_ANY 189
{ "BUS_WR_WB_CCASTOUT_ANY", {0xb1892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- CPU or non-CPU (all transactions)/Only 0-byte transactions with write back attribute (clean cast outs) will be counted"},
#define PME_MONT_BUS_WR_WB_CCASTOUT_SELF 190
{ "BUS_WR_WB_CCASTOUT_SELF", {0xa1892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- this'  processor/Only 0-byte transactions with write back attribute (clean cast outs) will be counted"},
#define PME_MONT_BUS_WR_WB_EQ_128BYTE_ANY 191
{ "BUS_WR_WB_EQ_128BYTE_ANY", {0x71892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- CPU or non-CPU (all transactions)./Only cache line transactions with write back or write coalesce attributes will be counted."},
#define PME_MONT_BUS_WR_WB_EQ_128BYTE_IO 192
{ "BUS_WR_WB_EQ_128BYTE_IO", {0x51892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- non-CPU priority agents/Only cache line transactions with write back or write coalesce attributes will be counted."},
#define PME_MONT_BUS_WR_WB_EQ_128BYTE_SELF 193
{ "BUS_WR_WB_EQ_128BYTE_SELF", {0x61892}, 0x03f0, 1, {0xffff0000}, "Bus Write Back Transactions -- this'  processor/Only cache line transactions with write back or write coalesce attributes will be counted."},
#define PME_MONT_CPU_CPL_CHANGES_ALL 194
{ "CPU_CPL_CHANGES_ALL", {0xf0013}, 0xfff0, 1, {0xffff0000}, "Privilege Level Changes -- All changes in cpl counted"},
#define PME_MONT_CPU_CPL_CHANGES_LVL0 195
{ "CPU_CPL_CHANGES_LVL0", {0x10013}, 0xfff0, 1, {0xffff0000}, "Privilege Level Changes -- All changes to/from privilege level0 are counted"},
#define PME_MONT_CPU_CPL_CHANGES_LVL1 196
{ "CPU_CPL_CHANGES_LVL1", {0x20013}, 0xfff0, 1, {0xffff0000}, "Privilege Level Changes -- All changes to/from privilege level1 are counted"},
#define PME_MONT_CPU_CPL_CHANGES_LVL2 197
{ "CPU_CPL_CHANGES_LVL2", {0x40013}, 0xfff0, 1, {0xffff0000}, "Privilege Level Changes -- All changes to/from privilege level2 are counted"},
#define PME_MONT_CPU_CPL_CHANGES_LVL3 198
{ "CPU_CPL_CHANGES_LVL3", {0x80013}, 0xfff0, 1, {0xffff0000}, "Privilege Level Changes -- All changes to/from privilege level3 are counted"},
#define PME_MONT_CPU_OP_CYCLES_ALL 199
{ "CPU_OP_CYCLES_ALL", {0x1012}, 0xfff0, 1, {0xffff0000}, "CPU Operating Cycles -- All CPU cycles counted"},
#define PME_MONT_CPU_OP_CYCLES_QUAL 200
{ "CPU_OP_CYCLES_QUAL", {0x11012}, 0xfff0, 1, {0xffff0003}, "CPU Operating Cycles -- Qualified cycles only"},
#define PME_MONT_CPU_OP_CYCLES_HALTED 201
{ "CPU_OP_CYCLES_HALTED", {0x1018}, 0x0400, 7, {0xffff0000}, "CPU Operating Cycles Halted"},
#define PME_MONT_DATA_DEBUG_REGISTER_FAULT 202
{ "DATA_DEBUG_REGISTER_FAULT", {0x52}, 0xfff0, 1, {0xffff0000}, "Fault Due to Data Debug Reg. Match to Load/Store Instruction"},
#define PME_MONT_DATA_DEBUG_REGISTER_MATCHES 203
{ "DATA_DEBUG_REGISTER_MATCHES", {0xc6}, 0xfff0, 1, {0xffff0007}, "Data Debug Register Matches Data Address of Memory Reference."},
#define PME_MONT_DATA_EAR_ALAT 204
{ "DATA_EAR_ALAT", {0xec8}, 0xfff0, 1, {0xffff0007}, "Data EAR ALAT"},
#define PME_MONT_DATA_EAR_CACHE_LAT1024 205
{ "DATA_EAR_CACHE_LAT1024", {0x80dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 1024 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT128 206
{ "DATA_EAR_CACHE_LAT128", {0x50dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 128 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT16 207
{ "DATA_EAR_CACHE_LAT16", {0x20dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 16 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT2048 208
{ "DATA_EAR_CACHE_LAT2048", {0x90dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 2048 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT256 209
{ "DATA_EAR_CACHE_LAT256", {0x60dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 256 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT32 210
{ "DATA_EAR_CACHE_LAT32", {0x30dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 32 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT4 211
{ "DATA_EAR_CACHE_LAT4", {0xdc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 4 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT4096 212
{ "DATA_EAR_CACHE_LAT4096", {0xa0dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 4096 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT512 213
{ "DATA_EAR_CACHE_LAT512", {0x70dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 512 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT64 214
{ "DATA_EAR_CACHE_LAT64", {0x40dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 64 Cycles"},
#define PME_MONT_DATA_EAR_CACHE_LAT8 215
{ "DATA_EAR_CACHE_LAT8", {0x10dc8}, 0xfff0, 1, {0xffff0007}, "Data EAR Cache -- >= 8 Cycles"},
#define PME_MONT_DATA_EAR_EVENTS 216
{ "DATA_EAR_EVENTS", {0x8c8}, 0xfff0, 1, {0xffff0007}, "L1 Data Cache EAR Events"},
#define PME_MONT_DATA_EAR_TLB_ALL 217
{ "DATA_EAR_TLB_ALL", {0xe0cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- All L1 DTLB Misses"},
#define PME_MONT_DATA_EAR_TLB_FAULT 218
{ "DATA_EAR_TLB_FAULT", {0x80cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- DTLB Misses which produce a software fault"},
#define PME_MONT_DATA_EAR_TLB_L2DTLB 219
{ "DATA_EAR_TLB_L2DTLB", {0x20cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- L1 DTLB Misses which hit L2 DTLB"},
#define PME_MONT_DATA_EAR_TLB_L2DTLB_OR_FAULT 220
{ "DATA_EAR_TLB_L2DTLB_OR_FAULT", {0xa0cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- L1 DTLB Misses which hit L2 DTLB or produce a software fault"},
#define PME_MONT_DATA_EAR_TLB_L2DTLB_OR_VHPT 221
{ "DATA_EAR_TLB_L2DTLB_OR_VHPT", {0x60cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- L1 DTLB Misses which hit L2 DTLB or VHPT"},
#define PME_MONT_DATA_EAR_TLB_VHPT 222
{ "DATA_EAR_TLB_VHPT", {0x40cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- L1 DTLB Misses which hit VHPT"},
#define PME_MONT_DATA_EAR_TLB_VHPT_OR_FAULT 223
{ "DATA_EAR_TLB_VHPT_OR_FAULT", {0xc0cc8}, 0xfff0, 1, {0xffff0007}, "Data EAR TLB -- L1 DTLB Misses which hit VHPT or produce a software fault"},
#define PME_MONT_DATA_REFERENCES_SET0 224
{ "DATA_REFERENCES_SET0", {0xc3}, 0xfff0, 4, {0x5010007}, "Data Memory References Issued to Memory Pipeline"},
#define PME_MONT_DATA_REFERENCES_SET1 225
{ "DATA_REFERENCES_SET1", {0xc5}, 0xfff0, 4, {0x5110007}, "Data Memory References Issued to Memory Pipeline"},
#define PME_MONT_DISP_STALLED 226
{ "DISP_STALLED", {0x49}, 0xfff0, 1, {0xffff0000}, "Number of Cycles Dispersal Stalled"},
#define PME_MONT_DTLB_INSERTS_HPW 227
{ "DTLB_INSERTS_HPW", {0x8c9}, 0xfff0, 4, {0xffff0000}, "Hardware Page Walker Installs to DTLB"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL_ALL_PRED 228
{ "ENCBR_MISPRED_DETAIL_ALL_ALL_PRED", {0x63}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- All encoded branches regardless of prediction result"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL_CORRECT_PRED 229
{ "ENCBR_MISPRED_DETAIL_ALL_CORRECT_PRED", {0x10063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- All encoded branches, correctly predicted branches (outcome and target)"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL_WRONG_PATH 230
{ "ENCBR_MISPRED_DETAIL_ALL_WRONG_PATH", {0x20063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- All encoded branches, mispredicted branches due to wrong branch direction"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL_WRONG_TARGET 231
{ "ENCBR_MISPRED_DETAIL_ALL_WRONG_TARGET", {0x30063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- All encoded branches, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL2_ALL_PRED 232
{ "ENCBR_MISPRED_DETAIL_ALL2_ALL_PRED", {0xc0063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, regardless of prediction result"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL2_CORRECT_PRED 233
{ "ENCBR_MISPRED_DETAIL_ALL2_CORRECT_PRED", {0xd0063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, correctly predicted branches (outcome and target)"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL2_WRONG_PATH 234
{ "ENCBR_MISPRED_DETAIL_ALL2_WRONG_PATH", {0xe0063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, mispredicted branches due to wrong branch direction"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_ALL2_WRONG_TARGET 235
{ "ENCBR_MISPRED_DETAIL_ALL2_WRONG_TARGET", {0xf0063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only non-return indirect branches, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_OVERSUB_ALL_PRED 236
{ "ENCBR_MISPRED_DETAIL_OVERSUB_ALL_PRED", {0x80063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only return type branches, regardless of prediction result"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_OVERSUB_CORRECT_PRED 237
{ "ENCBR_MISPRED_DETAIL_OVERSUB_CORRECT_PRED", {0x90063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only return type branches, correctly predicted branches (outcome and target)"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_PATH 238
{ "ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_PATH", {0xa0063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only return type branches, mispredicted branches due to wrong branch direction"},
#define PME_MONT_ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_TARGET 239
{ "ENCBR_MISPRED_DETAIL_OVERSUB_WRONG_TARGET", {0xb0063}, 0xfff0, 3, {0xffff0003}, "Number of Encoded Branches Retired -- Only return type branches, mispredicted branches due to wrong target for taken branches"},
#define PME_MONT_ER_BKSNP_ME_ACCEPTED 240
{ "ER_BKSNP_ME_ACCEPTED", {0x10bb}, 0x03f0, 2, {0xffff0000}, "Backsnoop Me Accepted"},
#define PME_MONT_ER_BRQ_LIVE_REQ_HI 241
{ "ER_BRQ_LIVE_REQ_HI", {0x10b8}, 0x03f0, 2, {0xffff0000}, "BRQ Live Requests (upper 2 bits)"},
#define PME_MONT_ER_BRQ_LIVE_REQ_LO 242
{ "ER_BRQ_LIVE_REQ_LO", {0x10b9}, 0x03f0, 7, {0xffff0000}, "BRQ Live Requests (lower 3 bits)"},
#define PME_MONT_ER_BRQ_REQ_INSERTED 243
{ "ER_BRQ_REQ_INSERTED", {0x8ba}, 0x03f0, 1, {0xffff0000}, "BRQ Requests Inserted"},
#define PME_MONT_ER_MEM_READ_OUT_HI 244
{ "ER_MEM_READ_OUT_HI", {0x8b4}, 0x03f0, 2, {0xffff0000}, "Outstanding Memory Read Transactions (upper 2 bits)"},
#define PME_MONT_ER_MEM_READ_OUT_LO 245
{ "ER_MEM_READ_OUT_LO", {0x8b5}, 0x03f0, 7, {0xffff0000}, "Outstanding Memory Read Transactions (lower 3 bits)"},
#define PME_MONT_ER_REJECT_ALL_L1D_REQ 246
{ "ER_REJECT_ALL_L1D_REQ", {0x10bd}, 0x03f0, 1, {0xffff0000}, "Reject All L1D Requests"},
#define PME_MONT_ER_REJECT_ALL_L1I_REQ 247
{ "ER_REJECT_ALL_L1I_REQ", {0x10be}, 0x03f0, 1, {0xffff0000}, "Reject All L1I Requests"},
#define PME_MONT_ER_REJECT_ALL_L1_REQ 248
{ "ER_REJECT_ALL_L1_REQ", {0x10bc}, 0x03f0, 1, {0xffff0000}, "Reject All L1 Requests"},
#define PME_MONT_ER_SNOOPQ_REQ_HI 249
{ "ER_SNOOPQ_REQ_HI", {0x10b6}, 0x03f0, 2, {0xffff0000}, "Outstanding Snoops (upper bit)"},
#define PME_MONT_ER_SNOOPQ_REQ_LO 250
{ "ER_SNOOPQ_REQ_LO", {0x10b7}, 0x03f0, 7, {0xffff0000}, "Outstanding Snoops (lower 3 bits)"},
#define PME_MONT_ETB_EVENT 251
{ "ETB_EVENT", {0x111}, 0xfff0, 1, {0xffff0003}, "Execution Trace Buffer Event Captured"},
#define PME_MONT_FE_BUBBLE_ALL 252
{ "FE_BUBBLE_ALL", {0x71}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- count regardless of cause"},
#define PME_MONT_FE_BUBBLE_ALLBUT_FEFLUSH_BUBBLE 253
{ "FE_BUBBLE_ALLBUT_FEFLUSH_BUBBLE", {0xb0071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- ALL except FEFLUSH and BUBBLE"},
#define PME_MONT_FE_BUBBLE_ALLBUT_IBFULL 254
{ "FE_BUBBLE_ALLBUT_IBFULL", {0xc0071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- ALL except IBFULl"},
#define PME_MONT_FE_BUBBLE_BRANCH 255
{ "FE_BUBBLE_BRANCH", {0x90071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by any of 4 branch recirculates"},
#define PME_MONT_FE_BUBBLE_BUBBLE 256
{ "FE_BUBBLE_BUBBLE", {0xd0071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by branch bubble stall"},
#define PME_MONT_FE_BUBBLE_FEFLUSH 257
{ "FE_BUBBLE_FEFLUSH", {0x10071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by a front-end flush"},
#define PME_MONT_FE_BUBBLE_FILL_RECIRC 258
{ "FE_BUBBLE_FILL_RECIRC", {0x80071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by a recirculate for a cache line fill operation"},
#define PME_MONT_FE_BUBBLE_GROUP1 259
{ "FE_BUBBLE_GROUP1", {0x30071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- BUBBLE or BRANCH"},
#define PME_MONT_FE_BUBBLE_GROUP2 260
{ "FE_BUBBLE_GROUP2", {0x40071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- IMISS or TLBMISS"},
#define PME_MONT_FE_BUBBLE_GROUP3 261
{ "FE_BUBBLE_GROUP3", {0xa0071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- FILL_RECIRC or BRANCH"},
#define PME_MONT_FE_BUBBLE_IBFULL 262
{ "FE_BUBBLE_IBFULL", {0x50071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by instruction buffer full stall"},
#define PME_MONT_FE_BUBBLE_IMISS 263
{ "FE_BUBBLE_IMISS", {0x60071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by instruction cache miss stall"},
#define PME_MONT_FE_BUBBLE_TLBMISS 264
{ "FE_BUBBLE_TLBMISS", {0x70071}, 0xfff0, 1, {0xffff0000}, "Bubbles Seen by FE -- only if caused by TLB stall"},
#define PME_MONT_FE_LOST_BW_ALL 265
{ "FE_LOST_BW_ALL", {0x70}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- count regardless of cause"},
#define PME_MONT_FE_LOST_BW_BI 266
{ "FE_LOST_BW_BI", {0x90070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch initialization stall"},
#define PME_MONT_FE_LOST_BW_BRQ 267
{ "FE_LOST_BW_BRQ", {0xa0070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch retirement queue stall"},
#define PME_MONT_FE_LOST_BW_BR_ILOCK 268
{ "FE_LOST_BW_BR_ILOCK", {0xc0070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch interlock stall"},
#define PME_MONT_FE_LOST_BW_BUBBLE 269
{ "FE_LOST_BW_BUBBLE", {0xd0070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by branch resteer bubble stall"},
#define PME_MONT_FE_LOST_BW_FEFLUSH 270
{ "FE_LOST_BW_FEFLUSH", {0x10070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by a front-end flush"},
#define PME_MONT_FE_LOST_BW_FILL_RECIRC 271
{ "FE_LOST_BW_FILL_RECIRC", {0x80070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by a recirculate for a cache line fill operation"},
#define PME_MONT_FE_LOST_BW_IBFULL 272
{ "FE_LOST_BW_IBFULL", {0x50070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by instruction buffer full stall"},
#define PME_MONT_FE_LOST_BW_IMISS 273
{ "FE_LOST_BW_IMISS", {0x60070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by instruction cache miss stall"},
#define PME_MONT_FE_LOST_BW_PLP 274
{ "FE_LOST_BW_PLP", {0xb0070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by perfect loop prediction stall"},
#define PME_MONT_FE_LOST_BW_TLBMISS 275
{ "FE_LOST_BW_TLBMISS", {0x70070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by TLB stall"},
#define PME_MONT_FE_LOST_BW_UNREACHED 276
{ "FE_LOST_BW_UNREACHED", {0x40070}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Entrance to IB -- only if caused by unreachable bundle"},
#define PME_MONT_FP_FAILED_FCHKF 277
{ "FP_FAILED_FCHKF", {0x6}, 0xfff0, 1, {0xffff0001}, "Failed fchkf"},
#define PME_MONT_FP_FALSE_SIRSTALL 278
{ "FP_FALSE_SIRSTALL", {0x5}, 0xfff0, 1, {0xffff0001}, "SIR Stall Without a Trap"},
#define PME_MONT_FP_FLUSH_TO_ZERO_FTZ_POSS 279
{ "FP_FLUSH_TO_ZERO_FTZ_POSS", {0x1000b}, 0xfff0, 2, {0xffff0001}, "FP Result Flushed to Zero -- "},
#define PME_MONT_FP_FLUSH_TO_ZERO_FTZ_REAL 280
{ "FP_FLUSH_TO_ZERO_FTZ_REAL", {0xb}, 0xfff0, 2, {0xffff0001}, "FP Result Flushed to Zero -- Times FTZ"},
#define PME_MONT_FP_OPS_RETIRED 281
{ "FP_OPS_RETIRED", {0x9}, 0xfff0, 6, {0xffff0001}, "Retired FP Operations"},
#define PME_MONT_FP_TRUE_SIRSTALL 282
{ "FP_TRUE_SIRSTALL", {0x3}, 0xfff0, 1, {0xffff0001}, "SIR stall asserted and leads to a trap"},
#define PME_MONT_HPW_DATA_REFERENCES 283
{ "HPW_DATA_REFERENCES", {0x2d}, 0xfff0, 4, {0xffff0000}, "Data Memory References to VHPT"},
#define PME_MONT_IA64_INST_RETIRED_THIS 284
{ "IA64_INST_RETIRED_THIS", {0x8}, 0xfff0, 6, {0xffff0003}, "Retired IA-64 Instructions -- Retired IA-64 Instructions"},
#define PME_MONT_IA64_TAGGED_INST_RETIRED_IBRP0_PMC32_33 285
{ "IA64_TAGGED_INST_RETIRED_IBRP0_PMC32_33", {0x8}, 0xfff0, 6, {0xffff0003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 0 and the opcode matcher pair PMC32 and PMC33."},
#define PME_MONT_IA64_TAGGED_INST_RETIRED_IBRP1_PMC34_35 286
{ "IA64_TAGGED_INST_RETIRED_IBRP1_PMC34_35", {0x10008}, 0xfff0, 6, {0xffff0003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 1 and the opcode matcher pair PMC34 and PMC35."},
#define PME_MONT_IA64_TAGGED_INST_RETIRED_IBRP2_PMC32_33 287
{ "IA64_TAGGED_INST_RETIRED_IBRP2_PMC32_33", {0x20008}, 0xfff0, 6, {0xffff0003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 2 and the opcode matcher pair PMC32 and PMC33."},
#define PME_MONT_IA64_TAGGED_INST_RETIRED_IBRP3_PMC34_35 288
{ "IA64_TAGGED_INST_RETIRED_IBRP3_PMC34_35", {0x30008}, 0xfff0, 6, {0xffff0003}, "Retired Tagged Instructions -- Instruction tagged by Instruction Breakpoint Pair 3 and the opcode matcher pair PMC34 and PMC35."},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_ALL 289
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_ALL", {0x73}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- count regardless of cause"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_BI 290
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BI", {0x90073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by branch initialization stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_BRQ 291
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BRQ", {0xa0073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by branch retirement queue stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_BR_ILOCK 292
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BR_ILOCK", {0xc0073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by branch interlock stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_BUBBLE 293
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_BUBBLE", {0xd0073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by branch resteer bubble stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_FEFLUSH 294
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_FEFLUSH", {0x10073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by a front-end flush"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_FILL_RECIRC 295
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_FILL_RECIRC", {0x80073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by a recirculate for a cache line fill operation"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_IBFULL 296
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_IBFULL", {0x50073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- (* meaningless for this event *)"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_IMISS 297
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_IMISS", {0x60073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by instruction cache miss stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_PLP 298
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_PLP", {0xb0073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by perfect loop prediction stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_TLBMISS 299
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_TLBMISS", {0x70073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by TLB stall"},
#define PME_MONT_IDEAL_BE_LOST_BW_DUE_TO_FE_UNREACHED 300
{ "IDEAL_BE_LOST_BW_DUE_TO_FE_UNREACHED", {0x40073}, 0xfff0, 2, {0xffff0000}, "Invalid Bundles at the Exit from IB -- only if caused by unreachable bundle"},
#define PME_MONT_INST_CHKA_LDC_ALAT_ALL 301
{ "INST_CHKA_LDC_ALAT_ALL", {0x30056}, 0xfff0, 2, {0xffff0007}, "Retired chk.a and ld.c Instructions -- both integer and floating point instructions"},
#define PME_MONT_INST_CHKA_LDC_ALAT_FP 302
{ "INST_CHKA_LDC_ALAT_FP", {0x20056}, 0xfff0, 2, {0xffff0007}, "Retired chk.a and ld.c Instructions -- only floating point instructions"},
#define PME_MONT_INST_CHKA_LDC_ALAT_INT 303
{ "INST_CHKA_LDC_ALAT_INT", {0x10056}, 0xfff0, 2, {0xffff0007}, "Retired chk.a and ld.c Instructions -- only integer instructions"},
#define PME_MONT_INST_DISPERSED 304
{ "INST_DISPERSED", {0x4d}, 0xfff0, 6, {0xffff0001}, "Syllables Dispersed from REN to REG stage"},
#define PME_MONT_INST_FAILED_CHKA_LDC_ALAT_ALL 305
{ "INST_FAILED_CHKA_LDC_ALAT_ALL", {0x30057}, 0xfff0, 1, {0xffff0007}, "Failed chk.a and ld.c Instructions -- both integer and floating point instructions"},
#define PME_MONT_INST_FAILED_CHKA_LDC_ALAT_FP 306
{ "INST_FAILED_CHKA_LDC_ALAT_FP", {0x20057}, 0xfff0, 1, {0xffff0007}, "Failed chk.a and ld.c Instructions -- only floating point instructions"},
#define PME_MONT_INST_FAILED_CHKA_LDC_ALAT_INT 307
{ "INST_FAILED_CHKA_LDC_ALAT_INT", {0x10057}, 0xfff0, 1, {0xffff0007}, "Failed chk.a and ld.c Instructions -- only integer instructions"},
#define PME_MONT_INST_FAILED_CHKS_RETIRED_ALL 308
{ "INST_FAILED_CHKS_RETIRED_ALL", {0x30055}, 0xfff0, 1, {0xffff0000}, "Failed chk.s Instructions -- both integer and floating point instructions"},
#define PME_MONT_INST_FAILED_CHKS_RETIRED_FP 309
{ "INST_FAILED_CHKS_RETIRED_FP", {0x20055}, 0xfff0, 1, {0xffff0000}, "Failed chk.s Instructions -- only floating point instructions"},
#define PME_MONT_INST_FAILED_CHKS_RETIRED_INT 310
{ "INST_FAILED_CHKS_RETIRED_INT", {0x10055}, 0xfff0, 1, {0xffff0000}, "Failed chk.s Instructions -- only integer instructions"},
#define PME_MONT_ISB_BUNPAIRS_IN 311
{ "ISB_BUNPAIRS_IN", {0x46}, 0xfff0, 1, {0xffff0001}, "Bundle Pairs Written from L2I into FE"},
#define PME_MONT_ITLB_MISSES_FETCH_ALL 312
{ "ITLB_MISSES_FETCH_ALL", {0x30047}, 0xfff0, 1, {0xffff0001}, "ITLB Misses Demand Fetch -- All tlb misses will be counted. Note that this is not equal to sum of the L1ITLB and L2ITLB umasks because any access could be a miss in L1ITLB and L2ITLB."},
#define PME_MONT_ITLB_MISSES_FETCH_L1ITLB 313
{ "ITLB_MISSES_FETCH_L1ITLB", {0x10047}, 0xfff0, 1, {0xffff0001}, "ITLB Misses Demand Fetch -- All misses in L1ITLB will be counted. even if L1ITLB is not updated for an access (Uncacheable/nat page/not present page/faulting/some flushed), it will be counted here."},
#define PME_MONT_ITLB_MISSES_FETCH_L2ITLB 314
{ "ITLB_MISSES_FETCH_L2ITLB", {0x20047}, 0xfff0, 1, {0xffff0001}, "ITLB Misses Demand Fetch -- All misses in L1ITLB which also missed in L2ITLB will be counted."},
#define PME_MONT_L1DTLB_TRANSFER 315
{ "L1DTLB_TRANSFER", {0xc0}, 0xfff0, 1, {0x5010007}, "L1DTLB Misses That Hit in the L2DTLB for Accesses Counted in L1D_READS"},
#define PME_MONT_L1D_READS_SET0 316
{ "L1D_READS_SET0", {0xc2}, 0xfff0, 2, {0x5010007}, "L1 Data Cache Reads"},
#define PME_MONT_L1D_READS_SET1 317
{ "L1D_READS_SET1", {0xc4}, 0xfff0, 2, {0x5110007}, "L1 Data Cache Reads"},
#define PME_MONT_L1D_READ_MISSES_ALL 318
{ "L1D_READ_MISSES_ALL", {0xc7}, 0xfff0, 2, {0x5110007}, "L1 Data Cache Read Misses -- all L1D read misses will be counted."},
#define PME_MONT_L1D_READ_MISSES_RSE_FILL 319
{ "L1D_READ_MISSES_RSE_FILL", {0x100c7}, 0xfff0, 2, {0x5110007}, "L1 Data Cache Read Misses -- only L1D read misses caused by RSE fills will be counted"},
#define PME_MONT_L1ITLB_INSERTS_HPW 320
{ "L1ITLB_INSERTS_HPW", {0x48}, 0xfff0, 1, {0xffff0001}, "L1ITLB Hardware Page Walker Inserts"},
#define PME_MONT_L1I_EAR_CACHE_LAT0 321
{ "L1I_EAR_CACHE_LAT0", {0x400b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- > 0 Cycles (All L1 Misses)"},
#define PME_MONT_L1I_EAR_CACHE_LAT1024 322
{ "L1I_EAR_CACHE_LAT1024", {0xc00b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 1024 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT128 323
{ "L1I_EAR_CACHE_LAT128", {0xf00b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 128 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT16 324
{ "L1I_EAR_CACHE_LAT16", {0xfc0b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 16 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT256 325
{ "L1I_EAR_CACHE_LAT256", {0xe00b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 256 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT32 326
{ "L1I_EAR_CACHE_LAT32", {0xf80b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 32 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT4 327
{ "L1I_EAR_CACHE_LAT4", {0xff0b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 4 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT4096 328
{ "L1I_EAR_CACHE_LAT4096", {0x800b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 4096 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_LAT8 329
{ "L1I_EAR_CACHE_LAT8", {0xfe0b43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- >= 8 Cycles"},
#define PME_MONT_L1I_EAR_CACHE_RAB 330
{ "L1I_EAR_CACHE_RAB", {0xb43}, 0xfff0, 1, {0xffff0001}, "L1I EAR Cache -- RAB HIT"},
#define PME_MONT_L1I_EAR_EVENTS 331
{ "L1I_EAR_EVENTS", {0x843}, 0xfff0, 1, {0xffff0001}, "Instruction EAR Events"},
#define PME_MONT_L1I_EAR_TLB_ALL 332
{ "L1I_EAR_TLB_ALL", {0x70a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- All L1 ITLB Misses"},
#define PME_MONT_L1I_EAR_TLB_FAULT 333
{ "L1I_EAR_TLB_FAULT", {0x40a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- ITLB Misses which produced a fault"},
#define PME_MONT_L1I_EAR_TLB_L2TLB 334
{ "L1I_EAR_TLB_L2TLB", {0x10a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- L1 ITLB Misses which hit L2 ITLB"},
#define PME_MONT_L1I_EAR_TLB_L2TLB_OR_FAULT 335
{ "L1I_EAR_TLB_L2TLB_OR_FAULT", {0x50a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- L1 ITLB Misses which hit L2 ITLB or produce a software fault"},
#define PME_MONT_L1I_EAR_TLB_L2TLB_OR_VHPT 336
{ "L1I_EAR_TLB_L2TLB_OR_VHPT", {0x30a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- L1 ITLB Misses which hit L2 ITLB or VHPT"},
#define PME_MONT_L1I_EAR_TLB_VHPT 337
{ "L1I_EAR_TLB_VHPT", {0x20a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- L1 ITLB Misses which hit VHPT"},
#define PME_MONT_L1I_EAR_TLB_VHPT_OR_FAULT 338
{ "L1I_EAR_TLB_VHPT_OR_FAULT", {0x60a43}, 0xfff0, 1, {0xffff0001}, "L1I EAR TLB -- L1 ITLB Misses which hit VHPT or produce a software fault"},
#define PME_MONT_L1I_FETCH_ISB_HIT 339
{ "L1I_FETCH_ISB_HIT", {0x66}, 0xfff0, 1, {0xffff0001}, "\"Just-In-Time\" Instruction Fetch Hitting in and Being Bypassed from ISB"},
#define PME_MONT_L1I_FETCH_RAB_HIT 340
{ "L1I_FETCH_RAB_HIT", {0x65}, 0xfff0, 1, {0xffff0001}, "Instruction Fetch Hitting in RAB"},
#define PME_MONT_L1I_FILLS 341
{ "L1I_FILLS", {0x841}, 0xfff0, 1, {0xffff0001}, "L1 Instruction Cache Fills"},
#define PME_MONT_L1I_PREFETCHES 342
{ "L1I_PREFETCHES", {0x44}, 0xfff0, 1, {0xffff0001}, "L1 Instruction Prefetch Requests"},
#define PME_MONT_L1I_PREFETCH_STALL_ALL 343
{ "L1I_PREFETCH_STALL_ALL", {0x30067}, 0xfff0, 1, {0xffff0000}, "Prefetch Pipeline Stalls -- Number of clocks prefetch pipeline is stalled"},
#define PME_MONT_L1I_PREFETCH_STALL_FLOW 344
{ "L1I_PREFETCH_STALL_FLOW", {0x20067}, 0xfff0, 1, {0xffff0000}, "Prefetch Pipeline Stalls -- Asserted when the streaming prefetcher is working close to the instructions being fetched for demand reads, and is not asserted when the streaming prefetcher is ranging way ahead of the demand reads."},
#define PME_MONT_L1I_PURGE 345
{ "L1I_PURGE", {0x104b}, 0xfff0, 1, {0xffff0001}, "L1ITLB Purges Handled by L1I"},
#define PME_MONT_L1I_PVAB_OVERFLOW 346
{ "L1I_PVAB_OVERFLOW", {0x69}, 0xfff0, 1, {0xffff0000}, "PVAB Overflow"},
#define PME_MONT_L1I_RAB_ALMOST_FULL 347
{ "L1I_RAB_ALMOST_FULL", {0x1064}, 0xfff0, 1, {0xffff0000}, "Is RAB Almost Full?"},
#define PME_MONT_L1I_RAB_FULL 348
{ "L1I_RAB_FULL", {0x1060}, 0xfff0, 1, {0xffff0000}, "Is RAB Full?"},
#define PME_MONT_L1I_READS 349
{ "L1I_READS", {0x40}, 0xfff0, 1, {0xffff0001}, "L1 Instruction Cache Reads"},
#define PME_MONT_L1I_SNOOP 350
{ "L1I_SNOOP", {0x104a}, 0xfff0, 1, {0xffff0007}, "Snoop Requests Handled by L1I"},
#define PME_MONT_L1I_STRM_PREFETCHES 351
{ "L1I_STRM_PREFETCHES", {0x5f}, 0xfff0, 1, {0xffff0001}, "L1 Instruction Cache Line Prefetch Requests"},
#define PME_MONT_L2DTLB_MISSES 352
{ "L2DTLB_MISSES", {0xc1}, 0xfff0, 4, {0x5010007}, "L2DTLB Misses"},
#define PME_MONT_L2D_BAD_LINES_SELECTED_ANY 353
{ "L2D_BAD_LINES_SELECTED_ANY", {0x8ec}, 0xfff0, 4, {0x4520007}, "Valid Line Replaced When Invalid Line Is Available -- Valid line replaced when invalid line is available"},
#define PME_MONT_L2D_BYPASS_L2_DATA1 354
{ "L2D_BYPASS_L2_DATA1", {0x8e4}, 0xfff0, 1, {0x4120007}, "Count L2D Bypasses -- Count only L2D data bypasses (L1D to L2A)"},
#define PME_MONT_L2D_BYPASS_L2_DATA2 355
{ "L2D_BYPASS_L2_DATA2", {0x108e4}, 0xfff0, 1, {0x4120007}, "Count L2D Bypasses -- Count only L2D data bypasses (L1W to L2I)"},
#define PME_MONT_L2D_BYPASS_L3_DATA1 356
{ "L2D_BYPASS_L3_DATA1", {0x208e4}, 0xfff0, 1, {0x4120007}, "Count L2D Bypasses -- Count only L3 data bypasses (L1D to L2A)"},
#define PME_MONT_L2D_FILLB_FULL_THIS 357
{ "L2D_FILLB_FULL_THIS", {0x8f1}, 0xfff0, 1, {0x4720000}, "L2D Fill Buffer Is Full -- L2D Fill buffer is full"},
#define PME_MONT_L2D_FILL_MESI_STATE_E 358
{ "L2D_FILL_MESI_STATE_E", {0x108f2}, 0xfff0, 1, {0x4820000}, "L2D Cache Fills with MESI state -- "},
#define PME_MONT_L2D_FILL_MESI_STATE_I 359
{ "L2D_FILL_MESI_STATE_I", {0x308f2}, 0xfff0, 1, {0x4820000}, "L2D Cache Fills with MESI state -- "},
#define PME_MONT_L2D_FILL_MESI_STATE_M 360
{ "L2D_FILL_MESI_STATE_M", {0x8f2}, 0xfff0, 1, {0x4820000}, "L2D Cache Fills with MESI state -- "},
#define PME_MONT_L2D_FILL_MESI_STATE_P 361
{ "L2D_FILL_MESI_STATE_P", {0x408f2}, 0xfff0, 1, {0x4820000}, "L2D Cache Fills with MESI state -- "},
#define PME_MONT_L2D_FILL_MESI_STATE_S 362
{ "L2D_FILL_MESI_STATE_S", {0x208f2}, 0xfff0, 1, {0x4820000}, "L2D Cache Fills with MESI state -- "},
#define PME_MONT_L2D_FORCE_RECIRC_FILL_HIT 363
{ "L2D_FORCE_RECIRC_FILL_HIT", {0x808ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count only those caused by an L2D miss which hit in the fill buffer."},
#define PME_MONT_L2D_FORCE_RECIRC_FRC_RECIRC 364
{ "L2D_FORCE_RECIRC_FRC_RECIRC", {0x908ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Caused by an L2D miss when a force recirculate already existed in the Ozq."},
#define PME_MONT_L2D_FORCE_RECIRC_L1W 365
{ "L2D_FORCE_RECIRC_L1W", {0xc08ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count only those caused by a L2D miss one cycle ahead of the current op."},
#define PME_MONT_L2D_FORCE_RECIRC_LIMBO 366
{ "L2D_FORCE_RECIRC_LIMBO", {0x108ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count operations that went into the LIMBO Ozq state. This state is entered when the the op sees a FILL_HIT or OZQ_MISS event."},
#define PME_MONT_L2D_FORCE_RECIRC_OZQ_MISS 367
{ "L2D_FORCE_RECIRC_OZQ_MISS", {0xb08ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Caused by an L2D miss when an L2D miss was already in the OZQ."},
#define PME_MONT_L2D_FORCE_RECIRC_RECIRC 368
{ "L2D_FORCE_RECIRC_RECIRC", {0x8ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Counts inserts into OzQ due to a recirculate. The recirculate due to secondary misses or various other conflicts"},
#define PME_MONT_L2D_FORCE_RECIRC_SAME_INDEX 369
{ "L2D_FORCE_RECIRC_SAME_INDEX", {0xa08ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Caused by an L2D miss when a miss to the same index was in the same issue group."},
#define PME_MONT_L2D_FORCE_RECIRC_SECONDARY_ALL 370
{ "L2D_FORCE_RECIRC_SECONDARY_ALL", {0xf08ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- CSaused by any L2D op that saw a miss to the same address in OZQ, L2 fill buffer, or one cycle ahead in the main pipeline."},
#define PME_MONT_L2D_FORCE_RECIRC_SECONDARY_READ 371
{ "L2D_FORCE_RECIRC_SECONDARY_READ", {0xd08ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Caused by L2D read op that saw a miss to the same address in OZQ, L2 fill buffer, or one cycle ahead in the main pipeline."},
#define PME_MONT_L2D_FORCE_RECIRC_SECONDARY_WRITE 372
{ "L2D_FORCE_RECIRC_SECONDARY_WRITE", {0xe08ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Caused by L2D write op that saw a miss to the same address in OZQ, L2 fill buffer, or one cycle ahead in the main pipeline."},
#define PME_MONT_L2D_FORCE_RECIRC_SNP_OR_L3 373
{ "L2D_FORCE_RECIRC_SNP_OR_L3", {0x608ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count only those caused by a snoop or L3 issue."},
#define PME_MONT_L2D_FORCE_RECIRC_TAG_NOTOK 374
{ "L2D_FORCE_RECIRC_TAG_NOTOK", {0x408ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count only those caused by L2D hits caused by in flight snoops, stores with a sibling miss to the same index, sibling probe to the same line or a pending mf.a instruction. This count can usually be ignored since its events are rare, unpredictable, and/or show up in one of the other events."},
#define PME_MONT_L2D_FORCE_RECIRC_TAG_OK 375
{ "L2D_FORCE_RECIRC_TAG_OK", {0x708ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count operations that inserted to Ozq as a hit. Thus it was NOT forced to recirculate. Likely identical to L2D_INSERT_HITS."},
#define PME_MONT_L2D_FORCE_RECIRC_TRAN_PREF 376
{ "L2D_FORCE_RECIRC_TRAN_PREF", {0x508ea}, 0xfff0, 4, {0x4420007}, "Forced Recirculates -- Count only those caused by L2D miss requests that transformed to prefetches"},
#define PME_MONT_L2D_INSERT_HITS 377
{ "L2D_INSERT_HITS", {0x8b1}, 0xfff0, 4, {0xffff0007}, "Count Number of Times an Inserting Data Request Hit in the L2D."},
#define PME_MONT_L2D_INSERT_MISSES 378
{ "L2D_INSERT_MISSES", {0x8b0}, 0xfff0, 4, {0xffff0007}, "Count Number of Times an Inserting Data Request Missed the L2D."},
#define PME_MONT_L2D_ISSUED_RECIRC_OZQ_ACC 379
{ "L2D_ISSUED_RECIRC_OZQ_ACC", {0x8eb}, 0xfff0, 1, {0x4420007}, "Count Number of Times a Recirculate Issue Was Attempted and Not Preempted"},
#define PME_MONT_L2D_L3ACCESS_CANCEL_ANY 380
{ "L2D_L3ACCESS_CANCEL_ANY", {0x208e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- count cancels due to any reason. This umask will count more than the sum of all the other umasks. It will count things that weren't committed accesses when they reached L1w, but the L2D attempted to bypass them to the L3 anyway (speculatively). This will include accesses made repeatedly while the main pipeline is stalled and the L1D is attempting to recirculate an access down the L1D pipeline. Thus, an access could get counted many times before it really does get bypassed to the L3. It is a measure of how many times we asserted a request to the L3 but didn't confirm it."},
#define PME_MONT_L2D_L3ACCESS_CANCEL_ER_REJECT 381
{ "L2D_L3ACCESS_CANCEL_ER_REJECT", {0x308e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- Count only requests that were rejected by ER"},
#define PME_MONT_L2D_L3ACCESS_CANCEL_INV_L3_BYP 382
{ "L2D_L3ACCESS_CANCEL_INV_L3_BYP", {0x8e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- L2D cancelled a bypass because it did not commit, or was not a valid opcode to bypass, or was not a true miss of L2D (either hit,recirc,or limbo)."},
#define PME_MONT_L2D_L3ACCESS_CANCEL_P2_COV_SNP_FILL_NOSNP 383
{ "L2D_L3ACCESS_CANCEL_P2_COV_SNP_FILL_NOSNP", {0x608e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- A snoop and a fill to the same address reached the L2D within a 3 cycle window of each other or a snoop hit a nosnoops entry in Ozq."},
#define PME_MONT_L2D_L3ACCESS_CANCEL_P2_COV_SNP_TEM 384
{ "L2D_L3ACCESS_CANCEL_P2_COV_SNP_TEM", {0x408e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- A snoop saw an L2D tag error and missed/"},
#define PME_MONT_L2D_L3ACCESS_CANCEL_P2_COV_SNP_VIC 385
{ "L2D_L3ACCESS_CANCEL_P2_COV_SNP_VIC", {0x508e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- A snoop hit in the L1D victim buffer"},
#define PME_MONT_L2D_L3ACCESS_CANCEL_SPEC_L3_BYP 386
{ "L2D_L3ACCESS_CANCEL_SPEC_L3_BYP", {0x108e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- L2D cancelled speculative L3 bypasses because it was not a WB memory attribute or it was an effective release."},
#define PME_MONT_L2D_L3ACCESS_CANCEL_TAIL_TRANS_DIS 387
{ "L2D_L3ACCESS_CANCEL_TAIL_TRANS_DIS", {0x708e8}, 0xfff0, 1, {0x4320007}, "L2D Access Cancelled by L2D -- Count the number of cycles that either transform to prefetches or Ozq tail collapse have been dynamically disabled. This would indicate that memory contention has lead the L2D to throttle request to prevent livelock scenarios."},
#define PME_MONT_L2D_MISSES 388
{ "L2D_MISSES", {0x8cb}, 0xfff0, 1, {0xffff0007}, "L2 Misses"},
#define PME_MONT_L2D_OPS_ISSUED_FP_LOAD 389
{ "L2D_OPS_ISSUED_FP_LOAD", {0x108f0}, 0xfff0, 4, {0xffff0007}, "Operations Issued By L2D -- Count only valid floating-point loads"},
#define PME_MONT_L2D_OPS_ISSUED_INT_LOAD 390
{ "L2D_OPS_ISSUED_INT_LOAD", {0x8f0}, 0xfff0, 4, {0xffff0007}, "Operations Issued By L2D -- Count only valid integer loads, including ld16."},
#define PME_MONT_L2D_OPS_ISSUED_LFETCH 391
{ "L2D_OPS_ISSUED_LFETCH", {0x408f0}, 0xfff0, 4, {0xffff0007}, "Operations Issued By L2D -- Count only lfetch operations."},
#define PME_MONT_L2D_OPS_ISSUED_OTHER 392
{ "L2D_OPS_ISSUED_OTHER", {0x508f0}, 0xfff0, 4, {0xffff0007}, "Operations Issued By L2D -- Count only valid non-load, no-store accesses that are not in any of the above sections."},
#define PME_MONT_L2D_OPS_ISSUED_RMW 393
{ "L2D_OPS_ISSUED_RMW", {0x208f0}, 0xfff0, 4, {0xffff0007}, "Operations Issued By L2D -- Count only valid read_modify_write stores and semaphores including cmp8xchg16."},
#define PME_MONT_L2D_OPS_ISSUED_STORE 394
{ "L2D_OPS_ISSUED_STORE", {0x308f0}, 0xfff0, 4, {0xffff0007}, "Operations Issued By L2D -- Count only valid non-read_modify_write stores, including st16."},
#define PME_MONT_L2D_OZDB_FULL_THIS 395
{ "L2D_OZDB_FULL_THIS", {0x8e9}, 0xfff0, 1, {0x4320000}, "L2D OZ Data Buffer Is Full -- L2 OZ Data Buffer is full"},
#define PME_MONT_L2D_OZQ_ACQUIRE 396
{ "L2D_OZQ_ACQUIRE", {0x8ef}, 0xfff0, 1, {0x4620000}, "Acquire Ordering Attribute Exists in L2D OZQ"},
#define PME_MONT_L2D_OZQ_CANCELS0_ACQ 397
{ "L2D_OZQ_CANCELS0_ACQ", {0x608e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- caused by an acquire somewhere in Ozq or ER."},
#define PME_MONT_L2D_OZQ_CANCELS0_BANK_CONF 398
{ "L2D_OZQ_CANCELS0_BANK_CONF", {0x808e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- a bypassed L2D hit operation had a bank conflict with an older sibling bypass or an older operation in the L2D pipeline."},
#define PME_MONT_L2D_OZQ_CANCELS0_CANC_L2M_TO_L2C_ST 399
{ "L2D_OZQ_CANCELS0_CANC_L2M_TO_L2C_ST", {0x108e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- caused by a canceled store in L2M,L2D or L2C. This is the combination of following subevents that were available separately in Itanium2: CANC_L2M_ST=caused by canceled store in L2M, CANC_L2D_ST=caused by canceled store in L2D, CANC_L2C_ST=caused by canceled store in L2C"},
#define PME_MONT_L2D_OZQ_CANCELS0_FILL_ST_CONF 400
{ "L2D_OZQ_CANCELS0_FILL_ST_CONF", {0xe08e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- an OZQ store conflicted with a returning L2D fill"},
#define PME_MONT_L2D_OZQ_CANCELS0_L2A_ST_MAT 401
{ "L2D_OZQ_CANCELS0_L2A_ST_MAT", {0x208e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- canceled due to an uncanceled store match in L2A"},
#define PME_MONT_L2D_OZQ_CANCELS0_L2C_ST_MAT 402
{ "L2D_OZQ_CANCELS0_L2C_ST_MAT", {0x508e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- canceled due to an uncanceled store match in L2C"},
#define PME_MONT_L2D_OZQ_CANCELS0_L2D_ST_MAT 403
{ "L2D_OZQ_CANCELS0_L2D_ST_MAT", {0x408e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- canceled due to an uncanceled store match in L2D"},
#define PME_MONT_L2D_OZQ_CANCELS0_L2M_ST_MAT 404
{ "L2D_OZQ_CANCELS0_L2M_ST_MAT", {0x308e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- canceled due to an uncanceled store match in L2M"},
#define PME_MONT_L2D_OZQ_CANCELS0_MISC_ORDER 405
{ "L2D_OZQ_CANCELS0_MISC_ORDER", {0xd08e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- a sync.i or mf.a . This is the combination of following subevents that were available separately in Itanium2: SYNC=caused by sync.i, MFA=a memory fence instruction"},
#define PME_MONT_L2D_OZQ_CANCELS0_OVER_SUB 406
{ "L2D_OZQ_CANCELS0_OVER_SUB", {0xa08e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- a high Ozq issue rate resulted in the L2D having to cancel due to hardware restrictions. This is the combination of following subevents that were available separately in Itanium2: OVER_SUB=oversubscription, L1DF_L2M=L1D fill in L2M"},
#define PME_MONT_L2D_OZQ_CANCELS0_OZDATA_CONF 407
{ "L2D_OZQ_CANCELS0_OZDATA_CONF", {0xf08e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- an OZQ operation that needed to read the OZQ data buffer conflicted with a fill return that needed to do the same."},
#define PME_MONT_L2D_OZQ_CANCELS0_OZQ_PREEMPT 408
{ "L2D_OZQ_CANCELS0_OZQ_PREEMPT", {0xb08e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- an L2D fill return conflicted with, and cancelled, an ozq request for various reasons. Formerly known as L1_FILL_CONF."},
#define PME_MONT_L2D_OZQ_CANCELS0_RECIRC 409
{ "L2D_OZQ_CANCELS0_RECIRC", {0x8e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- a recirculate was cancelled due h/w limitations on recirculate issue rate. This is the combination of following subevents that were available separately in Itanium2: RECIRC_OVER_SUB=caused by a recirculate oversubscription, DIDNT_RECIRC=caused because it did not recirculate, WEIRD=counts the cancels caused by attempted 5-cycle bypasses for non-aligned accesses and bypasses blocking recirculates for too long"},
#define PME_MONT_L2D_OZQ_CANCELS0_REL 410
{ "L2D_OZQ_CANCELS0_REL", {0x708e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- a release was cancelled due to some other operation"},
#define PME_MONT_L2D_OZQ_CANCELS0_SEMA 411
{ "L2D_OZQ_CANCELS0_SEMA", {0x908e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- a semaphore op was cancelled for various ordering or h/w restriction reasons. This is the combination of following subevents that were available separately in Itanium 2: SEM=a semaphore, CCV=a CCV"},
#define PME_MONT_L2D_OZQ_CANCELS0_WB_CONF 412
{ "L2D_OZQ_CANCELS0_WB_CONF", {0xc08e0}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Specific Reason Set 0) -- an OZQ request conflicted with an L2D data array read for a writeback. This is the combination of following subevents that were available separately in Itanium2: READ_WB_CONF=a write back conflict, ST_FILL_CONF=a store fill conflict"},
#define PME_MONT_L2D_OZQ_CANCELS1_ANY 413
{ "L2D_OZQ_CANCELS1_ANY", {0x8e2}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Late or Any) -- counts the total OZ Queue cancels"},
#define PME_MONT_L2D_OZQ_CANCELS1_LATE_BYP_EFFRELEASE 414
{ "L2D_OZQ_CANCELS1_LATE_BYP_EFFRELEASE", {0x308e2}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Late or Any) -- counts the late cancels caused by L1D to L2A bypass effective releases"},
#define PME_MONT_L2D_OZQ_CANCELS1_LATE_SPEC_BYP 415
{ "L2D_OZQ_CANCELS1_LATE_SPEC_BYP", {0x108e2}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Late or Any) -- counts the late cancels caused by speculative bypasses"},
#define PME_MONT_L2D_OZQ_CANCELS1_SIBLING_ACQ_REL 416
{ "L2D_OZQ_CANCELS1_SIBLING_ACQ_REL", {0x208e2}, 0xfff0, 4, {0x4020007}, "L2D OZQ Cancels (Late or Any) -- counts the late cancels caused by releases and acquires in the same issue group. This is the combination of following subevents that were available separately in Itanium2: LATE_ACQUIRE=late cancels caused by acquires, LATE_RELEASE=late cancles caused by releases"},
#define PME_MONT_L2D_OZQ_FULL_THIS 417
{ "L2D_OZQ_FULL_THIS", {0x8bc}, 0xfff0, 1, {0x4520000}, "L2D OZQ Is Full -- L2D OZQ is full"},
#define PME_MONT_L2D_OZQ_RELEASE 418
{ "L2D_OZQ_RELEASE", {0x8e5}, 0xfff0, 1, {0x4120000}, "Release Ordering Attribute Exists in L2D OZQ"},
#define PME_MONT_L2D_REFERENCES_ALL 419
{ "L2D_REFERENCES_ALL", {0x308e6}, 0xfff0, 4, {0x4220007}, "Data Read/Write Access to L2D -- count both read and write operations (semaphores will count as 2)"},
#define PME_MONT_L2D_REFERENCES_READS 420
{ "L2D_REFERENCES_READS", {0x108e6}, 0xfff0, 4, {0x4220007}, "Data Read/Write Access to L2D -- count only data read and semaphore operations."},
#define PME_MONT_L2D_REFERENCES_WRITES 421
{ "L2D_REFERENCES_WRITES", {0x208e6}, 0xfff0, 4, {0x4220007}, "Data Read/Write Access to L2D -- count only data write and semaphore operations"},
#define PME_MONT_L2D_STORE_HIT_SHARED_ANY 422
{ "L2D_STORE_HIT_SHARED_ANY", {0x8ed}, 0xfff0, 2, {0x4520007}, "Store Hit a Shared Line -- Store hit a shared line"},
#define PME_MONT_L2D_VICTIMB_FULL_THIS 423
{ "L2D_VICTIMB_FULL_THIS", {0x8f3}, 0xfff0, 1, {0x4820000}, "L2D Victim Buffer Is Full -- L2D victim buffer is full"},
#define PME_MONT_L2I_DEMAND_READS 424
{ "L2I_DEMAND_READS", {0x42}, 0xfff0, 1, {0xffff0001}, "L2 Instruction Demand Fetch Requests"},
#define PME_MONT_L2I_HIT_CONFLICTS_ALL_ALL 425
{ "L2I_HIT_CONFLICTS_ALL_ALL", {0xf087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- All fetches that reference L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_ALL_DMND 426
{ "L2I_HIT_CONFLICTS_ALL_DMND", {0xd087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- Only demand fetches that reference L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_ALL_PFTCH 427
{ "L2I_HIT_CONFLICTS_ALL_PFTCH", {0xe087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- Only prefetches that reference L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_HIT_ALL 428
{ "L2I_HIT_CONFLICTS_HIT_ALL", {0x7087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- All fetches that hit in L2I counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_HIT_DMND 429
{ "L2I_HIT_CONFLICTS_HIT_DMND", {0x5087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- Only demand fetches that hit in L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_HIT_PFTCH 430
{ "L2I_HIT_CONFLICTS_HIT_PFTCH", {0x6087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- Only prefetches that hit in L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_MISS_ALL 431
{ "L2I_HIT_CONFLICTS_MISS_ALL", {0xb087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- All fetches that miss in L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_MISS_DMND 432
{ "L2I_HIT_CONFLICTS_MISS_DMND", {0x9087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- Only demand fetches that miss in L2I are counted"},
#define PME_MONT_L2I_HIT_CONFLICTS_MISS_PFTCH 433
{ "L2I_HIT_CONFLICTS_MISS_PFTCH", {0xa087d}, 0xfff0, 1, {0xffff0001}, "L2I hit conflicts -- Only prefetches that miss in L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_ALL_ALL 434
{ "L2I_L3_REJECTS_ALL_ALL", {0xf087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- All fetches that reference L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_ALL_DMND 435
{ "L2I_L3_REJECTS_ALL_DMND", {0xd087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- Only demand fetches that reference L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_ALL_PFTCH 436
{ "L2I_L3_REJECTS_ALL_PFTCH", {0xe087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- Only prefetches that reference L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_HIT_ALL 437
{ "L2I_L3_REJECTS_HIT_ALL", {0x7087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- All fetches that hit in L2I counted"},
#define PME_MONT_L2I_L3_REJECTS_HIT_DMND 438
{ "L2I_L3_REJECTS_HIT_DMND", {0x5087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- Only demand fetches that hit in L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_HIT_PFTCH 439
{ "L2I_L3_REJECTS_HIT_PFTCH", {0x6087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- Only prefetches that hit in L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_MISS_ALL 440
{ "L2I_L3_REJECTS_MISS_ALL", {0xb087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- All fetches that miss in L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_MISS_DMND 441
{ "L2I_L3_REJECTS_MISS_DMND", {0x9087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- Only demand fetches that miss in L2I are counted"},
#define PME_MONT_L2I_L3_REJECTS_MISS_PFTCH 442
{ "L2I_L3_REJECTS_MISS_PFTCH", {0xa087c}, 0xfff0, 1, {0xffff0001}, "L3 rejects -- Only prefetches that miss in L2I are counted"},
#define PME_MONT_L2I_PREFETCHES 443
{ "L2I_PREFETCHES", {0x45}, 0xfff0, 1, {0xffff0001}, "L2 Instruction Prefetch Requests"},
#define PME_MONT_L2I_READS_ALL_ALL 444
{ "L2I_READS_ALL_ALL", {0xf0878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- All fetches that reference L2I are counted"},
#define PME_MONT_L2I_READS_ALL_DMND 445
{ "L2I_READS_ALL_DMND", {0xd0878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- Only demand fetches that reference L2I are counted"},
#define PME_MONT_L2I_READS_ALL_PFTCH 446
{ "L2I_READS_ALL_PFTCH", {0xe0878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- Only prefetches that reference L2I are counted"},
#define PME_MONT_L2I_READS_HIT_ALL 447
{ "L2I_READS_HIT_ALL", {0x70878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- All fetches that hit in L2I counted"},
#define PME_MONT_L2I_READS_HIT_DMND 448
{ "L2I_READS_HIT_DMND", {0x50878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- Only demand fetches that hit in L2I are counted"},
#define PME_MONT_L2I_READS_HIT_PFTCH 449
{ "L2I_READS_HIT_PFTCH", {0x60878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- Only prefetches that hit in L2I are counted"},
#define PME_MONT_L2I_READS_MISS_ALL 450
{ "L2I_READS_MISS_ALL", {0xb0878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- All fetches that miss in L2I are counted"},
#define PME_MONT_L2I_READS_MISS_DMND 451
{ "L2I_READS_MISS_DMND", {0x90878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- Only demand fetches that miss in L2I are counted"},
#define PME_MONT_L2I_READS_MISS_PFTCH 452
{ "L2I_READS_MISS_PFTCH", {0xa0878}, 0xfff0, 1, {0xffff0001}, "L2I Cacheable Reads -- Only prefetches that miss in L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_ALL_ALL 453
{ "L2I_RECIRCULATES_ALL_ALL", {0xf087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- All fetches that reference L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_ALL_DMND 454
{ "L2I_RECIRCULATES_ALL_DMND", {0xd087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- Only demand fetches that reference L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_ALL_PFTCH 455
{ "L2I_RECIRCULATES_ALL_PFTCH", {0xe087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- Only prefetches that reference L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_HIT_ALL 456
{ "L2I_RECIRCULATES_HIT_ALL", {0x7087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- All fetches that hit in L2I counted"},
#define PME_MONT_L2I_RECIRCULATES_HIT_DMND 457
{ "L2I_RECIRCULATES_HIT_DMND", {0x5087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- Only demand fetches that hit in L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_HIT_PFTCH 458
{ "L2I_RECIRCULATES_HIT_PFTCH", {0x6087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- Only prefetches that hit in L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_MISS_ALL 459
{ "L2I_RECIRCULATES_MISS_ALL", {0xb087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- All fetches that miss in L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_MISS_DMND 460
{ "L2I_RECIRCULATES_MISS_DMND", {0x9087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- Only demand fetches that miss in L2I are counted"},
#define PME_MONT_L2I_RECIRCULATES_MISS_PFTCH 461
{ "L2I_RECIRCULATES_MISS_PFTCH", {0xa087b}, 0xfff0, 1, {0xffff0001}, "L2I recirculates -- Only prefetches that miss in L2I are counted"},
#define PME_MONT_L2I_SNOOP_HITS 462
{ "L2I_SNOOP_HITS", {0x107f}, 0xfff0, 1, {0xffff0000}, "L2I snoop hits"},
#define PME_MONT_L2I_SPEC_ABORTS 463
{ "L2I_SPEC_ABORTS", {0x87e}, 0xfff0, 1, {0xffff0001}, "L2I speculative aborts"},
#define PME_MONT_L2I_UC_READS_ALL_ALL 464
{ "L2I_UC_READS_ALL_ALL", {0xf0879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- All fetches that reference L2I are counted"},
#define PME_MONT_L2I_UC_READS_ALL_DMND 465
{ "L2I_UC_READS_ALL_DMND", {0xd0879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- Only demand fetches that reference L2I are counted"},
#define PME_MONT_L2I_UC_READS_ALL_PFTCH 466
{ "L2I_UC_READS_ALL_PFTCH", {0xe0879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- Only prefetches that reference L2I are counted"},
#define PME_MONT_L2I_UC_READS_HIT_ALL 467
{ "L2I_UC_READS_HIT_ALL", {0x70879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- All fetches that hit in L2I counted"},
#define PME_MONT_L2I_UC_READS_HIT_DMND 468
{ "L2I_UC_READS_HIT_DMND", {0x50879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- Only demand fetches that hit in L2I are counted"},
#define PME_MONT_L2I_UC_READS_HIT_PFTCH 469
{ "L2I_UC_READS_HIT_PFTCH", {0x60879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- Only prefetches that hit in L2I are counted"},
#define PME_MONT_L2I_UC_READS_MISS_ALL 470
{ "L2I_UC_READS_MISS_ALL", {0xb0879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- All fetches that miss in L2I are counted"},
#define PME_MONT_L2I_UC_READS_MISS_DMND 471
{ "L2I_UC_READS_MISS_DMND", {0x90879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- Only demand fetches that miss in L2I are counted"},
#define PME_MONT_L2I_UC_READS_MISS_PFTCH 472
{ "L2I_UC_READS_MISS_PFTCH", {0xa0879}, 0xfff0, 1, {0xffff0001}, "L2I Uncacheable reads -- Only prefetches that miss in L2I are counted"},
#define PME_MONT_L2I_VICTIMIZATION 473
{ "L2I_VICTIMIZATION", {0x87a}, 0xfff0, 1, {0xffff0001}, "L2I victimizations"},
#define PME_MONT_L3_INSERTS 474
{ "L3_INSERTS", {0x8da}, 0xfff0, 1, {0xffff0017}, "L3 Cache Lines inserts"},
#define PME_MONT_L3_LINES_REPLACED 475
{ "L3_LINES_REPLACED", {0x8df}, 0xfff0, 1, {0xffff0010}, "L3 Cache Lines Replaced"},
#define PME_MONT_L3_MISSES 476
{ "L3_MISSES", {0x8dc}, 0xfff0, 1, {0xffff0007}, "L3 Misses"},
#define PME_MONT_L3_READS_ALL_ALL 477
{ "L3_READS_ALL_ALL", {0xf08dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Read References"},
#define PME_MONT_L3_READS_ALL_HIT 478
{ "L3_READS_ALL_HIT", {0xd08dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Read Hits"},
#define PME_MONT_L3_READS_ALL_MISS 479
{ "L3_READS_ALL_MISS", {0xe08dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Read Misses"},
#define PME_MONT_L3_READS_DATA_READ_ALL 480
{ "L3_READS_DATA_READ_ALL", {0xb08dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Load References (excludes reads for ownership used to satisfy stores)"},
#define PME_MONT_L3_READS_DATA_READ_HIT 481
{ "L3_READS_DATA_READ_HIT", {0x908dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Load Hits (excludes reads for ownership used to satisfy stores)"},
#define PME_MONT_L3_READS_DATA_READ_MISS 482
{ "L3_READS_DATA_READ_MISS", {0xa08dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Load Misses (excludes reads for ownership used to satisfy stores)"},
#define PME_MONT_L3_READS_DINST_FETCH_ALL 483
{ "L3_READS_DINST_FETCH_ALL", {0x308dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Demand Instruction References"},
#define PME_MONT_L3_READS_DINST_FETCH_HIT 484
{ "L3_READS_DINST_FETCH_HIT", {0x108dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Demand Instruction Fetch Hits"},
#define PME_MONT_L3_READS_DINST_FETCH_MISS 485
{ "L3_READS_DINST_FETCH_MISS", {0x208dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Demand Instruction Fetch Misses"},
#define PME_MONT_L3_READS_INST_FETCH_ALL 486
{ "L3_READS_INST_FETCH_ALL", {0x708dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Instruction Fetch and Prefetch References"},
#define PME_MONT_L3_READS_INST_FETCH_HIT 487
{ "L3_READS_INST_FETCH_HIT", {0x508dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Instruction Fetch and Prefetch Hits"},
#define PME_MONT_L3_READS_INST_FETCH_MISS 488
{ "L3_READS_INST_FETCH_MISS", {0x608dd}, 0xfff0, 1, {0xffff0017}, "L3 Reads -- L3 Instruction Fetch and Prefetch Misses"},
#define PME_MONT_L3_REFERENCES 489
{ "L3_REFERENCES", {0x8db}, 0xfff0, 1, {0xffff0007}, "L3 References"},
#define PME_MONT_L3_WRITES_ALL_ALL 490
{ "L3_WRITES_ALL_ALL", {0xf08de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L3 Write References"},
#define PME_MONT_L3_WRITES_ALL_HIT 491
{ "L3_WRITES_ALL_HIT", {0xd08de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L3 Write Hits"},
#define PME_MONT_L3_WRITES_ALL_MISS 492
{ "L3_WRITES_ALL_MISS", {0xe08de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L3 Write Misses"},
#define PME_MONT_L3_WRITES_DATA_WRITE_ALL 493
{ "L3_WRITES_DATA_WRITE_ALL", {0x708de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L3 Store References (excludes L2 write backs, includes L3 read for ownership requests that satisfy stores)"},
#define PME_MONT_L3_WRITES_DATA_WRITE_HIT 494
{ "L3_WRITES_DATA_WRITE_HIT", {0x508de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L3 Store Hits (excludes L2 write backs, includes L3 read for ownership requests that satisfy stores)"},
#define PME_MONT_L3_WRITES_DATA_WRITE_MISS 495
{ "L3_WRITES_DATA_WRITE_MISS", {0x608de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L3 Store Misses (excludes L2 write backs, includes L3 read for ownership requests that satisfy stores)"},
#define PME_MONT_L3_WRITES_L2_WB_ALL 496
{ "L3_WRITES_L2_WB_ALL", {0xb08de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L2 Write Back References"},
#define PME_MONT_L3_WRITES_L2_WB_HIT 497
{ "L3_WRITES_L2_WB_HIT", {0x908de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L2 Write Back Hits"},
#define PME_MONT_L3_WRITES_L2_WB_MISS 498
{ "L3_WRITES_L2_WB_MISS", {0xa08de}, 0xfff0, 1, {0xffff0017}, "L3 Writes -- L2 Write Back Misses"},
#define PME_MONT_LOADS_RETIRED 499
{ "LOADS_RETIRED", {0xcd}, 0xfff0, 4, {0x5310007}, "Retired Loads"},
#define PME_MONT_LOADS_RETIRED_INTG 500
{ "LOADS_RETIRED_INTG", {0xd8}, 0xfff0, 2, {0x5610007}, "Integer loads retired"},
#define PME_MONT_MEM_READ_CURRENT_ANY 501
{ "MEM_READ_CURRENT_ANY", {0x31089}, 0xfff0, 1, {0xffff0000}, "Current Mem Read Transactions On Bus -- CPU or non-CPU (all transactions)."},
#define PME_MONT_MEM_READ_CURRENT_IO 502
{ "MEM_READ_CURRENT_IO", {0x11089}, 0xfff0, 1, {0xffff0000}, "Current Mem Read Transactions On Bus -- non-CPU priority agents"},
#define PME_MONT_MISALIGNED_LOADS_RETIRED 503
{ "MISALIGNED_LOADS_RETIRED", {0xce}, 0xfff0, 4, {0x5310007}, "Retired Misaligned Load Instructions"},
#define PME_MONT_MISALIGNED_STORES_RETIRED 504
{ "MISALIGNED_STORES_RETIRED", {0xd2}, 0xfff0, 2, {0x5410007}, "Retired Misaligned Store Instructions"},
#define PME_MONT_NOPS_RETIRED 505
{ "NOPS_RETIRED", {0x50}, 0xfff0, 6, {0xffff0003}, "Retired NOP Instructions"},
#define PME_MONT_PREDICATE_SQUASHED_RETIRED 506
{ "PREDICATE_SQUASHED_RETIRED", {0x51}, 0xfff0, 6, {0xffff0003}, "Instructions Squashed Due to Predicate Off"},
#define PME_MONT_RSE_CURRENT_REGS_2_TO_0 507
{ "RSE_CURRENT_REGS_2_TO_0", {0x2b}, 0xfff0, 7, {0xffff0000}, "Current RSE Registers (Bits 2:0)"},
#define PME_MONT_RSE_CURRENT_REGS_5_TO_3 508
{ "RSE_CURRENT_REGS_5_TO_3", {0x2a}, 0xfff0, 7, {0xffff0000}, "Current RSE Registers (Bits 5:3)"},
#define PME_MONT_RSE_CURRENT_REGS_6 509
{ "RSE_CURRENT_REGS_6", {0x26}, 0xfff0, 1, {0xffff0000}, "Current RSE Registers (Bit 6)"},
#define PME_MONT_RSE_DIRTY_REGS_2_TO_0 510
{ "RSE_DIRTY_REGS_2_TO_0", {0x29}, 0xfff0, 7, {0xffff0000}, "Dirty RSE Registers (Bits 2:0)"},
#define PME_MONT_RSE_DIRTY_REGS_5_TO_3 511
{ "RSE_DIRTY_REGS_5_TO_3", {0x28}, 0xfff0, 7, {0xffff0000}, "Dirty RSE Registers (Bits 5:3)"},
#define PME_MONT_RSE_DIRTY_REGS_6 512
{ "RSE_DIRTY_REGS_6", {0x24}, 0xfff0, 1, {0xffff0000}, "Dirty RSE Registers (Bit 6)"},
#define PME_MONT_RSE_EVENT_RETIRED 513
{ "RSE_EVENT_RETIRED", {0x32}, 0xfff0, 1, {0xffff0000}, "Retired RSE operations"},
#define PME_MONT_RSE_REFERENCES_RETIRED_ALL 514
{ "RSE_REFERENCES_RETIRED_ALL", {0x30020}, 0xfff0, 2, {0xffff0007}, "RSE Accesses -- Both RSE loads and stores will be counted."},
#define PME_MONT_RSE_REFERENCES_RETIRED_LOAD 515
{ "RSE_REFERENCES_RETIRED_LOAD", {0x10020}, 0xfff0, 2, {0xffff0007}, "RSE Accesses -- Only RSE loads will be counted."},
#define PME_MONT_RSE_REFERENCES_RETIRED_STORE 516
{ "RSE_REFERENCES_RETIRED_STORE", {0x20020}, 0xfff0, 2, {0xffff0007}, "RSE Accesses -- Only RSE stores will be counted."},
#define PME_MONT_SERIALIZATION_EVENTS 517
{ "SERIALIZATION_EVENTS", {0x53}, 0xfff0, 1, {0xffff0000}, "Number of srlz.i Instructions"},
#define PME_MONT_SI_CCQ_COLLISIONS_EITHER 518
{ "SI_CCQ_COLLISIONS_EITHER", {0x10a8}, 0xfff0, 2, {0xffff0000}, "Clean Castout Queue Collisions -- transactions initiated by either cpu core"},
#define PME_MONT_SI_CCQ_COLLISIONS_SELF 519
{ "SI_CCQ_COLLISIONS_SELF", {0x110a8}, 0xfff0, 2, {0xffff0000}, "Clean Castout Queue Collisions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_CCQ_INSERTS_EITHER 520
{ "SI_CCQ_INSERTS_EITHER", {0x18a5}, 0xfff0, 2, {0xffff0000}, "Clean Castout Queue Insertions -- transactions initiated by either cpu core"},
#define PME_MONT_SI_CCQ_INSERTS_SELF 521
{ "SI_CCQ_INSERTS_SELF", {0x118a5}, 0xfff0, 2, {0xffff0000}, "Clean Castout Queue Insertions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_CCQ_LIVE_REQ_HI_EITHER 522
{ "SI_CCQ_LIVE_REQ_HI_EITHER", {0x10a7}, 0xfff0, 1, {0xffff0000}, "Clean Castout Queue Requests (upper bit) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_CCQ_LIVE_REQ_HI_SELF 523
{ "SI_CCQ_LIVE_REQ_HI_SELF", {0x110a7}, 0xfff0, 1, {0xffff0000}, "Clean Castout Queue Requests (upper bit) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_CCQ_LIVE_REQ_LO_EITHER 524
{ "SI_CCQ_LIVE_REQ_LO_EITHER", {0x10a6}, 0xfff0, 7, {0xffff0000}, "Clean Castout Queue Requests (lower three bits) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_CCQ_LIVE_REQ_LO_SELF 525
{ "SI_CCQ_LIVE_REQ_LO_SELF", {0x110a6}, 0xfff0, 7, {0xffff0000}, "Clean Castout Queue Requests (lower three bits) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_CYCLES 526
{ "SI_CYCLES", {0x108e}, 0xfff0, 1, {0xffff0000}, "SI Cycles"},
#define PME_MONT_SI_IOQ_COLLISIONS 527
{ "SI_IOQ_COLLISIONS", {0x10aa}, 0xfff0, 2, {0xffff0000}, "In Order Queue Collisions"},
#define PME_MONT_SI_IOQ_LIVE_REQ_HI 528
{ "SI_IOQ_LIVE_REQ_HI", {0x1098}, 0xfff0, 2, {0xffff0000}, "Inorder Bus Queue Requests (upper bit)"},
#define PME_MONT_SI_IOQ_LIVE_REQ_LO 529
{ "SI_IOQ_LIVE_REQ_LO", {0x1097}, 0xfff0, 3, {0xffff0000}, "Inorder Bus Queue Requests (lower three bits)"},
#define PME_MONT_SI_RQ_INSERTS_EITHER 530
{ "SI_RQ_INSERTS_EITHER", {0x189e}, 0xfff0, 2, {0xffff0000}, "Request Queue Insertions -- transactions initiated by either cpu core"},
#define PME_MONT_SI_RQ_INSERTS_SELF 531
{ "SI_RQ_INSERTS_SELF", {0x1189e}, 0xfff0, 2, {0xffff0000}, "Request Queue Insertions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_RQ_LIVE_REQ_HI_EITHER 532
{ "SI_RQ_LIVE_REQ_HI_EITHER", {0x10a0}, 0xfff0, 1, {0xffff0000}, "Request Queue Requests (upper bit) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_RQ_LIVE_REQ_HI_SELF 533
{ "SI_RQ_LIVE_REQ_HI_SELF", {0x110a0}, 0xfff0, 1, {0xffff0000}, "Request Queue Requests (upper bit) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_RQ_LIVE_REQ_LO_EITHER 534
{ "SI_RQ_LIVE_REQ_LO_EITHER", {0x109f}, 0xfff0, 7, {0xffff0000}, "Request Queue Requests (lower three bits) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_RQ_LIVE_REQ_LO_SELF 535
{ "SI_RQ_LIVE_REQ_LO_SELF", {0x1109f}, 0xfff0, 7, {0xffff0000}, "Request Queue Requests (lower three bits) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_SCB_INSERTS_ALL_EITHER 536
{ "SI_SCB_INSERTS_ALL_EITHER", {0xc10ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count all snoop signoffs (plus backsnoop inserts) from either cpu core"},
#define PME_MONT_SI_SCB_INSERTS_ALL_SELF 537
{ "SI_SCB_INSERTS_ALL_SELF", {0xd10ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count all snoop signoffs (plus backsnoop inserts) from 'this' cpu core"},
#define PME_MONT_SI_SCB_INSERTS_HIT_EITHER 538
{ "SI_SCB_INSERTS_HIT_EITHER", {0x410ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count HIT snoop signoffs from either cpu core"},
#define PME_MONT_SI_SCB_INSERTS_HIT_SELF 539
{ "SI_SCB_INSERTS_HIT_SELF", {0x510ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count HIT snoop signoffs from 'this' cpu core"},
#define PME_MONT_SI_SCB_INSERTS_HITM_EITHER 540
{ "SI_SCB_INSERTS_HITM_EITHER", {0x810ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count HITM snoop signoffs from either cpu core"},
#define PME_MONT_SI_SCB_INSERTS_HITM_SELF 541
{ "SI_SCB_INSERTS_HITM_SELF", {0x910ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count HITM snoop signoffs from 'this' cpu core"},
#define PME_MONT_SI_SCB_INSERTS_MISS_EITHER 542
{ "SI_SCB_INSERTS_MISS_EITHER", {0x10ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count MISS snoop signoffs (plus backsnoop inserts) from either cpu core"},
#define PME_MONT_SI_SCB_INSERTS_MISS_SELF 543
{ "SI_SCB_INSERTS_MISS_SELF", {0x110ab}, 0xfff0, 4, {0xffff0000}, "Snoop Coalescing Buffer Insertions -- count MISS snoop signoffs (plus backsnoop inserts) from 'this' cpu core"},
#define PME_MONT_SI_SCB_LIVE_REQ_HI_EITHER 544
{ "SI_SCB_LIVE_REQ_HI_EITHER", {0x10ad}, 0xfff0, 1, {0xffff0000}, "Snoop Coalescing Buffer Requests (upper bit) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_SCB_LIVE_REQ_HI_SELF 545
{ "SI_SCB_LIVE_REQ_HI_SELF", {0x110ad}, 0xfff0, 1, {0xffff0000}, "Snoop Coalescing Buffer Requests (upper bit) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_SCB_LIVE_REQ_LO_EITHER 546
{ "SI_SCB_LIVE_REQ_LO_EITHER", {0x10ac}, 0xfff0, 7, {0xffff0000}, "Snoop Coalescing Buffer Requests (lower three bits) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_SCB_LIVE_REQ_LO_SELF 547
{ "SI_SCB_LIVE_REQ_LO_SELF", {0x110ac}, 0xfff0, 7, {0xffff0000}, "Snoop Coalescing Buffer Requests (lower three bits) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_SCB_SIGNOFFS_ALL 548
{ "SI_SCB_SIGNOFFS_ALL", {0xc10ae}, 0xfff0, 1, {0xffff0000}, "Snoop Coalescing Buffer Coherency Signoffs -- count all snoop signoffs"},
#define PME_MONT_SI_SCB_SIGNOFFS_HIT 549
{ "SI_SCB_SIGNOFFS_HIT", {0x410ae}, 0xfff0, 1, {0xffff0000}, "Snoop Coalescing Buffer Coherency Signoffs -- count HIT snoop signoffs"},
#define PME_MONT_SI_SCB_SIGNOFFS_HITM 550
{ "SI_SCB_SIGNOFFS_HITM", {0x810ae}, 0xfff0, 1, {0xffff0000}, "Snoop Coalescing Buffer Coherency Signoffs -- count HITM snoop signoffs"},
#define PME_MONT_SI_SCB_SIGNOFFS_MISS 551
{ "SI_SCB_SIGNOFFS_MISS", {0x10ae}, 0xfff0, 1, {0xffff0000}, "Snoop Coalescing Buffer Coherency Signoffs -- count MISS snoop signoffs"},
#define PME_MONT_SI_WAQ_COLLISIONS_EITHER 552
{ "SI_WAQ_COLLISIONS_EITHER", {0x10a4}, 0xfff0, 1, {0xffff0000}, "Write Address Queue Collisions -- transactions initiated by either cpu core"},
#define PME_MONT_SI_WAQ_COLLISIONS_SELF 553
{ "SI_WAQ_COLLISIONS_SELF", {0x110a4}, 0xfff0, 1, {0xffff0000}, "Write Address Queue Collisions -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_WDQ_ECC_ERRORS_ALL_EITHER 554
{ "SI_WDQ_ECC_ERRORS_ALL_EITHER", {0x810af}, 0xfff0, 2, {0xffff0000}, "Write Data Queue ECC Errors -- count all ECC errors from either cpu core"},
#define PME_MONT_SI_WDQ_ECC_ERRORS_ALL_SELF 555
{ "SI_WDQ_ECC_ERRORS_ALL_SELF", {0x910af}, 0xfff0, 2, {0xffff0000}, "Write Data Queue ECC Errors -- count all ECC errors from 'this' cpu core"},
#define PME_MONT_SI_WDQ_ECC_ERRORS_DBL_EITHER 556
{ "SI_WDQ_ECC_ERRORS_DBL_EITHER", {0x410af}, 0xfff0, 2, {0xffff0000}, "Write Data Queue ECC Errors -- count double-bit ECC errors from either cpu core"},
#define PME_MONT_SI_WDQ_ECC_ERRORS_DBL_SELF 557
{ "SI_WDQ_ECC_ERRORS_DBL_SELF", {0x510af}, 0xfff0, 2, {0xffff0000}, "Write Data Queue ECC Errors -- count double-bit ECC errors from 'this' cpu core"},
#define PME_MONT_SI_WDQ_ECC_ERRORS_SGL_EITHER 558
{ "SI_WDQ_ECC_ERRORS_SGL_EITHER", {0x10af}, 0xfff0, 2, {0xffff0000}, "Write Data Queue ECC Errors -- count single-bit ECC errors from either cpu core"},
#define PME_MONT_SI_WDQ_ECC_ERRORS_SGL_SELF 559
{ "SI_WDQ_ECC_ERRORS_SGL_SELF", {0x110af}, 0xfff0, 2, {0xffff0000}, "Write Data Queue ECC Errors -- count single-bit ECC errors from 'this' cpu core"},
#define PME_MONT_SI_WRITEQ_INSERTS_ALL_EITHER 560
{ "SI_WRITEQ_INSERTS_ALL_EITHER", {0x18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_ALL_SELF 561
{ "SI_WRITEQ_INSERTS_ALL_SELF", {0x118a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_EWB_EITHER 562
{ "SI_WRITEQ_INSERTS_EWB_EITHER", {0x418a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_EWB_SELF 563
{ "SI_WRITEQ_INSERTS_EWB_SELF", {0x518a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_IWB_EITHER 564
{ "SI_WRITEQ_INSERTS_IWB_EITHER", {0x218a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_IWB_SELF 565
{ "SI_WRITEQ_INSERTS_IWB_SELF", {0x318a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_NEWB_EITHER 566
{ "SI_WRITEQ_INSERTS_NEWB_EITHER", {0xc18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_NEWB_SELF 567
{ "SI_WRITEQ_INSERTS_NEWB_SELF", {0xd18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC16_EITHER 568
{ "SI_WRITEQ_INSERTS_WC16_EITHER", {0x818a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC16_SELF 569
{ "SI_WRITEQ_INSERTS_WC16_SELF", {0x918a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC1_8A_EITHER 570
{ "SI_WRITEQ_INSERTS_WC1_8A_EITHER", {0x618a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC1_8A_SELF 571
{ "SI_WRITEQ_INSERTS_WC1_8A_SELF", {0x718a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC1_8B_EITHER 572
{ "SI_WRITEQ_INSERTS_WC1_8B_EITHER", {0xe18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC1_8B_SELF 573
{ "SI_WRITEQ_INSERTS_WC1_8B_SELF", {0xf18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC32_EITHER 574
{ "SI_WRITEQ_INSERTS_WC32_EITHER", {0xa18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_INSERTS_WC32_SELF 575
{ "SI_WRITEQ_INSERTS_WC32_SELF", {0xb18a1}, 0xfff0, 2, {0xffff0000}, "Write Queue Insertions -- "},
#define PME_MONT_SI_WRITEQ_LIVE_REQ_HI_EITHER 576
{ "SI_WRITEQ_LIVE_REQ_HI_EITHER", {0x10a3}, 0xfff0, 1, {0xffff0000}, "Write Queue Requests (upper bit) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_WRITEQ_LIVE_REQ_HI_SELF 577
{ "SI_WRITEQ_LIVE_REQ_HI_SELF", {0x110a3}, 0xfff0, 1, {0xffff0000}, "Write Queue Requests (upper bit) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SI_WRITEQ_LIVE_REQ_LO_EITHER 578
{ "SI_WRITEQ_LIVE_REQ_LO_EITHER", {0x10a2}, 0xfff0, 7, {0xffff0000}, "Write Queue Requests (lower three bits) -- transactions initiated by either cpu core"},
#define PME_MONT_SI_WRITEQ_LIVE_REQ_LO_SELF 579
{ "SI_WRITEQ_LIVE_REQ_LO_SELF", {0x110a2}, 0xfff0, 7, {0xffff0000}, "Write Queue Requests (lower three bits) -- transactions initiated by 'this' cpu core"},
#define PME_MONT_SPEC_LOADS_NATTED_ALL 580
{ "SPEC_LOADS_NATTED_ALL", {0xd9}, 0xfff0, 2, {0xffff0005}, "Number of speculative inter loads that are NaTd -- Count all NaT'd loads"},
#define PME_MONT_SPEC_LOADS_NATTED_DEF_PSR_ED 581
{ "SPEC_LOADS_NATTED_DEF_PSR_ED", {0x500d9}, 0xfff0, 2, {0xffff0005}, "Number of speculative inter loads that are NaTd -- Only loads NaT'd due to effect of PSR.ed"},
#define PME_MONT_SPEC_LOADS_NATTED_DEF_TLB_FAULT 582
{ "SPEC_LOADS_NATTED_DEF_TLB_FAULT", {0x300d9}, 0xfff0, 2, {0xffff0005}, "Number of speculative inter loads that are NaTd -- Only loads NaT'd due to deferred TLB faults"},
#define PME_MONT_SPEC_LOADS_NATTED_DEF_TLB_MISS 583
{ "SPEC_LOADS_NATTED_DEF_TLB_MISS", {0x200d9}, 0xfff0, 2, {0xffff0005}, "Number of speculative inter loads that are NaTd -- Only loads NaT'd due to deferred TLB misses"},
#define PME_MONT_SPEC_LOADS_NATTED_NAT_CNSM 584
{ "SPEC_LOADS_NATTED_NAT_CNSM", {0x400d9}, 0xfff0, 2, {0xffff0005}, "Number of speculative inter loads that are NaTd -- Only loads NaT'd due to NaT consumption"},
#define PME_MONT_SPEC_LOADS_NATTED_VHPT_MISS 585
{ "SPEC_LOADS_NATTED_VHPT_MISS", {0x100d9}, 0xfff0, 2, {0xffff0005}, "Number of speculative inter loads that are NaTd -- Only loads NaT'd due to VHPT miss"},
#define PME_MONT_STORES_RETIRED 586
{ "STORES_RETIRED", {0xd1}, 0xfff0, 2, {0x5410007}, "Retired Stores"},
#define PME_MONT_SYLL_NOT_DISPERSED_ALL 587
{ "SYLL_NOT_DISPERSED_ALL", {0xf004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Counts all syllables not dispersed. NOTE: Any combination of b0000-b1111 is valid."},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL 588
{ "SYLL_NOT_DISPERSED_EXPL", {0x1004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits. These consist of  programmer specified architected S-bit and templates 1 and 5. Dispersal takes a 6-syllable (3-syllable) hit for every template 1/5 in bundle 0(1). Dispersal takes a 3-syllable (0 syllable) hit for every S-bit in bundle 0(1)"},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL_OR_FE 589
{ "SYLL_NOT_DISPERSED_EXPL_OR_FE", {0x5004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits or front-end not providing valid bundles or providing valid illegal templates."},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL_OR_FE_OR_MLX 590
{ "SYLL_NOT_DISPERSED_EXPL_OR_FE_OR_MLX", {0xd004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits or due to front-end not providing valid bundles or providing valid illegal templates or due to MLX bundle and resteers to non-0 syllable."},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL_OR_IMPL 591
{ "SYLL_NOT_DISPERSED_EXPL_OR_IMPL", {0x3004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit/implicit stop bits."},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_FE 592
{ "SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_FE", {0x7004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit or implicit stop bits or due to front-end not providing valid bundles or providing valid illegal template."},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_MLX 593
{ "SYLL_NOT_DISPERSED_EXPL_OR_IMPL_OR_MLX", {0xb004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit or implicit stop bits or due to MLX bundle and resteers to non-0 syllable."},
#define PME_MONT_SYLL_NOT_DISPERSED_EXPL_OR_MLX 594
{ "SYLL_NOT_DISPERSED_EXPL_OR_MLX", {0x9004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to explicit stop bits or to MLX bundle and resteers to non-0 syllable."},
#define PME_MONT_SYLL_NOT_DISPERSED_FE 595
{ "SYLL_NOT_DISPERSED_FE", {0x4004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to front-end not providing valid bundles or providing valid illegal templates. Dispersal takes a 3-syllable hit for every invalid bundle or valid illegal template from front-end. Bundle 1 with front-end fault, is counted here (3-syllable hit).."},
#define PME_MONT_SYLL_NOT_DISPERSED_FE_OR_MLX 596
{ "SYLL_NOT_DISPERSED_FE_OR_MLX", {0xc004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to MLI bundle and resteers to non-0 syllable or due to front-end not providing valid bundles or providing valid illegal templates."},
#define PME_MONT_SYLL_NOT_DISPERSED_IMPL 597
{ "SYLL_NOT_DISPERSED_IMPL", {0x2004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits. These consist of all of the non-architected stop bits (asymmetry, oversubscription, implicit). Dispersal takes a 6-syllable(3-syllable) hit for every implicit stop bits in bundle 0(1)."},
#define PME_MONT_SYLL_NOT_DISPERSED_IMPL_OR_FE 598
{ "SYLL_NOT_DISPERSED_IMPL_OR_FE", {0x6004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits or to front-end not providing valid bundles or providing valid illegal templates."},
#define PME_MONT_SYLL_NOT_DISPERSED_IMPL_OR_FE_OR_MLX 599
{ "SYLL_NOT_DISPERSED_IMPL_OR_FE_OR_MLX", {0xe004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits or due to front-end not providing valid bundles or providing valid illegal templates or due to MLX bundle and resteers to non-0 syllable."},
#define PME_MONT_SYLL_NOT_DISPERSED_IMPL_OR_MLX 600
{ "SYLL_NOT_DISPERSED_IMPL_OR_MLX", {0xa004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to implicit stop bits or to MLX bundle and resteers to non-0 syllable."},
#define PME_MONT_SYLL_NOT_DISPERSED_MLX 601
{ "SYLL_NOT_DISPERSED_MLX", {0x8004e}, 0xfff0, 5, {0xffff0001}, "Syllables Not Dispersed -- Count syllables not dispersed due to MLX bundle and resteers to non-0 syllable. Dispersal takes a 1 syllable hit for each MLX bundle . Dispersal could take 0-2 syllable hit depending on which syllable we resteer to. Bundle 1 with front-end fault which is split, is counted here (0-2 syllable hit)."},
#define PME_MONT_SYLL_OVERCOUNT_ALL 602
{ "SYLL_OVERCOUNT_ALL", {0x3004f}, 0xfff0, 2, {0xffff0001}, "Syllables Overcounted -- syllables overcounted in implicit & explicit bucket"},
#define PME_MONT_SYLL_OVERCOUNT_EXPL 603
{ "SYLL_OVERCOUNT_EXPL", {0x1004f}, 0xfff0, 2, {0xffff0001}, "Syllables Overcounted -- Only syllables overcounted in the explicit bucket"},
#define PME_MONT_SYLL_OVERCOUNT_IMPL 604
{ "SYLL_OVERCOUNT_IMPL", {0x2004f}, 0xfff0, 2, {0xffff0001}, "Syllables Overcounted -- Only syllables overcounted in the implicit bucket"},
#define PME_MONT_THREAD_SWITCH_CYCLE_ALL_GATED 605
{ "THREAD_SWITCH_CYCLE_ALL_GATED", {0x6000e}, 0xfff0, 1, {0xffff0000}, "Thread switch overhead cycles. -- Cycles TSs are gated due to any reason"},
#define PME_MONT_THREAD_SWITCH_CYCLE_ANYSTALL 606
{ "THREAD_SWITCH_CYCLE_ANYSTALL", {0x3000e}, 0xfff0, 1, {0xffff0000}, "Thread switch overhead cycles. -- Cycles TSs are stalled due to any reason"},
#define PME_MONT_THREAD_SWITCH_CYCLE_CRAB 607
{ "THREAD_SWITCH_CYCLE_CRAB", {0x1000e}, 0xfff0, 1, {0xffff0000}, "Thread switch overhead cycles. -- Cycles TSs are stalled due to CRAB operation"},
#define PME_MONT_THREAD_SWITCH_CYCLE_L2D 608
{ "THREAD_SWITCH_CYCLE_L2D", {0x2000e}, 0xfff0, 1, {0xffff0000}, "Thread switch overhead cycles. -- Cycles TSs are stalled due to L2D return operation"},
#define PME_MONT_THREAD_SWITCH_CYCLE_PCR 609
{ "THREAD_SWITCH_CYCLE_PCR", {0x4000e}, 0xfff0, 1, {0xffff0000}, "Thread switch overhead cycles. -- Cycles we run with PCR.sd set"},
#define PME_MONT_THREAD_SWITCH_CYCLE_TOTAL 610
{ "THREAD_SWITCH_CYCLE_TOTAL", {0x7000e}, 0xfff0, 1, {0xffff0000}, "Thread switch overhead cycles. -- Total time from TS opportunity is seized to TS happens."},
#define PME_MONT_THREAD_SWITCH_EVENTS_ALL 611
{ "THREAD_SWITCH_EVENTS_ALL", {0x7000c}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- All taken TSs"},
#define PME_MONT_THREAD_SWITCH_EVENTS_DBG 612
{ "THREAD_SWITCH_EVENTS_DBG", {0x5000c}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- TSs due to debug operations"},
#define PME_MONT_THREAD_SWITCH_EVENTS_HINT 613
{ "THREAD_SWITCH_EVENTS_HINT", {0x3000c}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- TSs due to hint instruction"},
#define PME_MONT_THREAD_SWITCH_EVENTS_L3MISS 614
{ "THREAD_SWITCH_EVENTS_L3MISS", {0x1000c}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- TSs due to L3 miss"},
#define PME_MONT_THREAD_SWITCH_EVENTS_LP 615
{ "THREAD_SWITCH_EVENTS_LP", {0x4000c}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- TSs due to low power operation"},
#define PME_MONT_THREAD_SWITCH_EVENTS_MISSED 616
{ "THREAD_SWITCH_EVENTS_MISSED", {0xc}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- TS opportunities missed"},
#define PME_MONT_THREAD_SWITCH_EVENTS_TIMER 617
{ "THREAD_SWITCH_EVENTS_TIMER", {0x2000c}, 0xfff0, 1, {0xffff0000}, "Thread switch events. -- TSs due to time out"},
#define PME_MONT_THREAD_SWITCH_GATED_ALL 618
{ "THREAD_SWITCH_GATED_ALL", {0x7000d}, 0xfff0, 1, {0xffff0000}, "Thread switches gated -- TSs gated for any reason"},
#define PME_MONT_THREAD_SWITCH_GATED_FWDPRO 619
{ "THREAD_SWITCH_GATED_FWDPRO", {0x5000d}, 0xfff0, 1, {0xffff0000}, "Thread switches gated -- Gated due to forward progress reasons"},
#define PME_MONT_THREAD_SWITCH_GATED_LP 620
{ "THREAD_SWITCH_GATED_LP", {0x1000d}, 0xfff0, 1, {0xffff0000}, "Thread switches gated -- TSs gated due to LP"},
#define PME_MONT_THREAD_SWITCH_GATED_PIPE 621
{ "THREAD_SWITCH_GATED_PIPE", {0x4000d}, 0xfff0, 1, {0xffff0000}, "Thread switches gated -- Gated due to pipeline operations"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_1024 622
{ "THREAD_SWITCH_STALL_GTE_1024", {0x8000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 1024 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_128 623
{ "THREAD_SWITCH_STALL_GTE_128", {0x5000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 128 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_16 624
{ "THREAD_SWITCH_STALL_GTE_16", {0x2000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 16 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_2048 625
{ "THREAD_SWITCH_STALL_GTE_2048", {0x9000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 2048 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_256 626
{ "THREAD_SWITCH_STALL_GTE_256", {0x6000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 256 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_32 627
{ "THREAD_SWITCH_STALL_GTE_32", {0x3000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 32 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_4 628
{ "THREAD_SWITCH_STALL_GTE_4", {0xf}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 4 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_4096 629
{ "THREAD_SWITCH_STALL_GTE_4096", {0xa000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 4096 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_512 630
{ "THREAD_SWITCH_STALL_GTE_512", {0x7000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 512 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_64 631
{ "THREAD_SWITCH_STALL_GTE_64", {0x4000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 64 cycles"},
#define PME_MONT_THREAD_SWITCH_STALL_GTE_8 632
{ "THREAD_SWITCH_STALL_GTE_8", {0x1000f}, 0xfff0, 1, {0xffff0000}, "Thread switch stall -- Thread switch stall >= 8 cycles"},
#define PME_MONT_UC_LOADS_RETIRED 633
{ "UC_LOADS_RETIRED", {0xcf}, 0xfff0, 4, {0x5310007}, "Retired Uncacheable Loads"},
#define PME_MONT_UC_STORES_RETIRED 634
{ "UC_STORES_RETIRED", {0xd0}, 0xfff0, 2, {0x5410007}, "Retired Uncacheable Stores"},
#define PME_MONT_IA64_INST_RETIRED 635
{ "IA64_INST_RETIRED", {0x8}, 0xfff0, 6, {0xffff0003}, "Retired IA-64 Instructions -- Retired IA-64 Instructions -- Alias to IA64_INST_RETIRED_THIS"},
#define PME_MONT_BRANCH_EVENT 636
{ "BRANCH_EVENT", {0x111}, 0xfff0, 1, {0xffff0003}, "Execution Trace Buffer Event Captured. Alias to ETB_EVENT"},
};
#define PME_MONT_EVENT_COUNT (sizeof(montecito_pe)/sizeof(pme_mont_entry_t))
