/*****************************************************************************
  Copyright (c) 2010, Intel Corp.
  All rights reserved.

  Redistribution and use in source and binary forms, with or without
  modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright notice,
      this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of Intel Corporation nor the names of its contributors
      may be used to endorse or promote products derived from this software
      without specific prior written permission.

  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
  THE POSSIBILITY OF SUCH DAMAGE.
******************************************************************************
* Contents: Native C interface to LAPACK (subset used by Eigen)
* Author: Intel Corporation
* Generated November, 2011
* Modified: stripped to the subset of declarations actually used by Eigen.
*****************************************************************************/
// SPDX-License-Identifier: BSD-3-Clause

#ifndef _MKL_LAPACKE_H_

#ifndef _LAPACKE_H_
#define _LAPACKE_H_

#ifdef HAVE_LAPACK_CONFIG_H
#include "lapacke_config.h"
#endif

#include <stdlib.h>

#ifndef lapack_int
#ifdef LAPACK_ILP64
#define lapack_int int64_t
#else
#define lapack_int int
#endif
#endif

#ifndef lapack_logical
#define lapack_logical lapack_int
#endif

#ifndef LAPACK_COMPLEX_CUSTOM

#ifndef lapack_complex_float
#define lapack_complex_float std::complex<float>
#endif

#ifndef lapack_complex_float_real
#define lapack_complex_float_real(z) (creal(z))
#endif

#ifndef lapack_complex_float_imag
#define lapack_complex_float_imag(z) (cimag(z))
#endif

lapack_complex_float lapack_make_complex_float(float re, float im);

#ifndef lapack_complex_double
#define lapack_complex_double std::complex<double>
#endif

#ifndef lapack_complex_double_real
#define lapack_complex_double_real(z) (creal(z))
#endif

#ifndef lapack_complex_double_imag
#define lapack_complex_double_imag(z) (cimag(z))
#endif

lapack_complex_double lapack_make_complex_double(double re, double im);

#endif

#ifdef __cplusplus
extern "C" {
#endif /* __cplusplus */

#ifndef LAPACKE_malloc
#define LAPACKE_malloc(size) malloc(size)
#endif
#ifndef LAPACKE_free
#define LAPACKE_free(p) free(p)
#endif

#define LAPACK_C2INT(x) (lapack_int)(*((float*)&x))
#define LAPACK_Z2INT(x) (lapack_int)(*((double*)&x))

#define LAPACK_ROW_MAJOR 101
#define LAPACK_COL_MAJOR 102

#define LAPACK_WORK_MEMORY_ERROR -1010
#define LAPACK_TRANSPOSE_MEMORY_ERROR -1011

/* Callback logical functions used to select eigenvalues for Schur form. */

typedef lapack_logical (*LAPACK_S_SELECT2)(const float*, const float*);
typedef lapack_logical (*LAPACK_S_SELECT3)(const float*, const float*, const float*);
typedef lapack_logical (*LAPACK_D_SELECT2)(const double*, const double*);
typedef lapack_logical (*LAPACK_D_SELECT3)(const double*, const double*, const double*);

typedef lapack_logical (*LAPACK_C_SELECT1)(const lapack_complex_float*);
typedef lapack_logical (*LAPACK_C_SELECT2)(const lapack_complex_float*, const lapack_complex_float*);
typedef lapack_logical (*LAPACK_Z_SELECT1)(const lapack_complex_double*);
typedef lapack_logical (*LAPACK_Z_SELECT2)(const lapack_complex_double*, const lapack_complex_double*);

#include "lapacke_mangling.h"

#define LAPACK_lsame LAPACK_GLOBAL(lsame, LSAME)
lapack_logical LAPACK_lsame(char* ca, char* cb, lapack_int lca, lapack_int lcb);

/*
 * LAPACKE function prototypes used by Eigen.
 *
 * Only the subset of LAPACKE routines that Eigen actually calls is declared
 * here.  If you need the full LAPACKE API, include your system's <lapacke.h>
 * after including Eigen headers, or define EIGEN_LAPACKE_SYSTEM before
 * including Eigen to use the system header instead of this bundled subset.
 */

/* Schur decomposition (gees) — used by RealSchur_LAPACKE.h, ComplexSchur_LAPACKE.h */
lapack_int LAPACKE_sgees(int matrix_order, char jobvs, char sort, LAPACK_S_SELECT2 select, lapack_int n, float* a,
                         lapack_int lda, lapack_int* sdim, float* wr, float* wi, float* vs, lapack_int ldvs);
lapack_int LAPACKE_dgees(int matrix_order, char jobvs, char sort, LAPACK_D_SELECT2 select, lapack_int n, double* a,
                         lapack_int lda, lapack_int* sdim, double* wr, double* wi, double* vs, lapack_int ldvs);
lapack_int LAPACKE_cgees(int matrix_order, char jobvs, char sort, LAPACK_C_SELECT1 select, lapack_int n,
                         lapack_complex_float* a, lapack_int lda, lapack_int* sdim, lapack_complex_float* w,
                         lapack_complex_float* vs, lapack_int ldvs);
lapack_int LAPACKE_zgees(int matrix_order, char jobvs, char sort, LAPACK_Z_SELECT1 select, lapack_int n,
                         lapack_complex_double* a, lapack_int lda, lapack_int* sdim, lapack_complex_double* w,
                         lapack_complex_double* vs, lapack_int ldvs);

/* QR with column pivoting (geqp3) — used by ColPivHouseholderQR_LAPACKE.h */
lapack_int LAPACKE_sgeqp3(int matrix_order, lapack_int m, lapack_int n, float* a, lapack_int lda, lapack_int* jpvt,
                          float* tau);
lapack_int LAPACKE_dgeqp3(int matrix_order, lapack_int m, lapack_int n, double* a, lapack_int lda, lapack_int* jpvt,
                          double* tau);
lapack_int LAPACKE_cgeqp3(int matrix_order, lapack_int m, lapack_int n, lapack_complex_float* a, lapack_int lda,
                          lapack_int* jpvt, lapack_complex_float* tau);
lapack_int LAPACKE_zgeqp3(int matrix_order, lapack_int m, lapack_int n, lapack_complex_double* a, lapack_int lda,
                          lapack_int* jpvt, lapack_complex_double* tau);

/* QR factorization (geqrf) — used by HouseholderQR_LAPACKE.h */
lapack_int LAPACKE_sgeqrf(int matrix_order, lapack_int m, lapack_int n, float* a, lapack_int lda, float* tau);
lapack_int LAPACKE_dgeqrf(int matrix_order, lapack_int m, lapack_int n, double* a, lapack_int lda, double* tau);
lapack_int LAPACKE_cgeqrf(int matrix_order, lapack_int m, lapack_int n, lapack_complex_float* a, lapack_int lda,
                          lapack_complex_float* tau);
lapack_int LAPACKE_zgeqrf(int matrix_order, lapack_int m, lapack_int n, lapack_complex_double* a, lapack_int lda,
                          lapack_complex_double* tau);

/* SVD via divide-and-conquer (gesdd) — used by BDCSVD_LAPACKE.h */
lapack_int LAPACKE_sgesdd(int matrix_order, char jobz, lapack_int m, lapack_int n, float* a, lapack_int lda, float* s,
                          float* u, lapack_int ldu, float* vt, lapack_int ldvt);
lapack_int LAPACKE_dgesdd(int matrix_order, char jobz, lapack_int m, lapack_int n, double* a, lapack_int lda, double* s,
                          double* u, lapack_int ldu, double* vt, lapack_int ldvt);
lapack_int LAPACKE_cgesdd(int matrix_order, char jobz, lapack_int m, lapack_int n, lapack_complex_float* a,
                          lapack_int lda, float* s, lapack_complex_float* u, lapack_int ldu, lapack_complex_float* vt,
                          lapack_int ldvt);
lapack_int LAPACKE_zgesdd(int matrix_order, char jobz, lapack_int m, lapack_int n, lapack_complex_double* a,
                          lapack_int lda, double* s, lapack_complex_double* u, lapack_int ldu,
                          lapack_complex_double* vt, lapack_int ldvt);

/* SVD (gesvd) — used by JacobiSVD_LAPACKE.h */
lapack_int LAPACKE_sgesvd(int matrix_order, char jobu, char jobvt, lapack_int m, lapack_int n, float* a, lapack_int lda,
                          float* s, float* u, lapack_int ldu, float* vt, lapack_int ldvt, float* superb);
lapack_int LAPACKE_dgesvd(int matrix_order, char jobu, char jobvt, lapack_int m, lapack_int n, double* a,
                          lapack_int lda, double* s, double* u, lapack_int ldu, double* vt, lapack_int ldvt,
                          double* superb);
lapack_int LAPACKE_cgesvd(int matrix_order, char jobu, char jobvt, lapack_int m, lapack_int n, lapack_complex_float* a,
                          lapack_int lda, float* s, lapack_complex_float* u, lapack_int ldu, lapack_complex_float* vt,
                          lapack_int ldvt, float* superb);
lapack_int LAPACKE_zgesvd(int matrix_order, char jobu, char jobvt, lapack_int m, lapack_int n, lapack_complex_double* a,
                          lapack_int lda, double* s, lapack_complex_double* u, lapack_int ldu,
                          lapack_complex_double* vt, lapack_int ldvt, double* superb);

/* LU factorization (getrf) — used by PartialPivLU_LAPACKE.h */
lapack_int LAPACKE_sgetrf(int matrix_order, lapack_int m, lapack_int n, float* a, lapack_int lda, lapack_int* ipiv);
lapack_int LAPACKE_dgetrf(int matrix_order, lapack_int m, lapack_int n, double* a, lapack_int lda, lapack_int* ipiv);
lapack_int LAPACKE_cgetrf(int matrix_order, lapack_int m, lapack_int n, lapack_complex_float* a, lapack_int lda,
                          lapack_int* ipiv);
lapack_int LAPACKE_zgetrf(int matrix_order, lapack_int m, lapack_int n, lapack_complex_double* a, lapack_int lda,
                          lapack_int* ipiv);

/* Cholesky factorization (potrf) — used by LLT_LAPACKE.h */
lapack_int LAPACKE_spotrf(int matrix_order, char uplo, lapack_int n, float* a, lapack_int lda);
lapack_int LAPACKE_dpotrf(int matrix_order, char uplo, lapack_int n, double* a, lapack_int lda);
lapack_int LAPACKE_cpotrf(int matrix_order, char uplo, lapack_int n, lapack_complex_float* a, lapack_int lda);
lapack_int LAPACKE_zpotrf(int matrix_order, char uplo, lapack_int n, lapack_complex_double* a, lapack_int lda);

/* Symmetric/Hermitian eigenvalues (syev/heev) — used by SelfAdjointEigenSolver_LAPACKE.h */
lapack_int LAPACKE_ssyev(int matrix_order, char jobz, char uplo, lapack_int n, float* a, lapack_int lda, float* w);
lapack_int LAPACKE_dsyev(int matrix_order, char jobz, char uplo, lapack_int n, double* a, lapack_int lda, double* w);
lapack_int LAPACKE_cheev(int matrix_order, char jobz, char uplo, lapack_int n, lapack_complex_float* a, lapack_int lda,
                         float* w);
lapack_int LAPACKE_zheev(int matrix_order, char jobz, char uplo, lapack_int n, lapack_complex_double* a, lapack_int lda,
                         double* w);

#ifdef __cplusplus
}
#endif /* __cplusplus */

#endif /* _LAPACKE_H_ */

#endif /* _MKL_LAPACKE_H_ */
