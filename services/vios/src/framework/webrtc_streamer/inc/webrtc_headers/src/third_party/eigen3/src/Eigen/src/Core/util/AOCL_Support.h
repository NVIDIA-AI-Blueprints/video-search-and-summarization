/*
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 *
 * AOCL_Support.h - AMD Optimizing CPU Libraries Integration Header for Eigen
 *
 * Copyright (c) 2025, Advanced Micro Devices, Inc. All rights reserved.
 *
 * Description:
 * ------------
 * This header file serves as the central configuration and integration point
 * for AMD Optimizing CPU Libraries (AOCL) with the Eigen C++ template library.
 * It orchestrates the integration of multiple AOCL components to provide
 * optimal mathematical computing performance on AMD Zen family processors.
 *
 * AOCL Component Integration:
 * ---------------------------
 * 1. AOCL Vector Math Library (VML):
 *    - Provides VRDA (Vector Rapid Double-precision Arithmetic) functions
 *    - Optimized transcendental functions: exp, sin, cos, sqrt, log, pow, etc.
 *    - SIMD vectorization for AMD architectures (AVX2, AVX-512)
 *    - Headers: amdlibm.h, amdlibm_vec.h
 *
 * 2. AOCL BLAS (BLIS - BLAS-like Library Instantiation Software):
 *    - High-performance Basic Linear Algebra Subprograms
 *    - Supports single-threaded (libblis) and multithreaded (libblis-mt)
 * variants
 *    - Optimized matrix operations: GEMM, GEMV, TRSM, etc.
 *    - Headers: cblas.h, blis.h
 *
 * 3. AOCL LAPACK (libFLAME - Formal Linear Algebra Methods Environment):
 *    - Dense linear algebra operations: factorizations, eigenvalue solvers
 *    - Matrix decompositions: LU, Cholesky, QR, SVD
 *    - Eigenvalue/eigenvector computations optimized for AMD hardware
 *    - Headers: LAPACKE interface
 *
 * ------------------------------
 * EIGEN_AOCL_VML_THRESHOLD (default: 128):
 *   - Minimum vector size for AOCL VML dispatch
 *   - Smaller vectors use standard Eigen to avoid function call overhead
 *   - Optimal values: 64-512 depending on operation and data characteristics
 *
 *
 *
 * Architecture Support:
 * ---------------------
 * Optimized for AMD processor families:
 * - Zen Architecture (Naples, Rome): AVX2 optimization
 * - Zen 2 Architecture (Rome, Matisse): Enhanced AVX2
 * - Zen 3 Architecture (Milan, Vermeer): Improved IPC and cache
 * - Zen 4 Architecture (Genoa, Raphael): AVX-512 support
 * - Zen 5 Architecture (Turin, Granite Ridge): Enhanced AVX-512
 *
 *
 * Dependencies:
 * -------------
 * Required AOCL components:
 * - libamdlibm: Core math library with VRDA functions
 * - libblis or libblis-mt: BLAS implementation
 * - libflame: LAPACK implementation
 *
 * System requirements:
 * - AMD x86_64 processor (optimal performance)
 * - Linux, Windows, or compatible POSIX system
 * - C++14 or later standard
 * - CMake 3.5+ for build system integration
 *
 * Developer:
 * ----------
 * Name: Sharad Saurabh Bhaskar
 * Email: shbhaska@amd.com
 * Organization: Advanced Micro Devices, Inc.
 */
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_AOCL_SUPPORT_H
#define EIGEN_AOCL_SUPPORT_H

#if defined(EIGEN_USE_AOCL_ALL) || defined(EIGEN_USE_AOCL_MT)

#include <complex>

// Define AOCL component flags based on main flags
#ifdef EIGEN_USE_AOCL_ALL
#define EIGEN_USE_AOCL_VML   // Enable AOCL Vector Math Library
#define EIGEN_USE_AOCL_BLAS  // Enable AOCL BLAS (BLIS)

// Enable Eigen BLAS backend only if BLIS provides compatible interface
#if defined(EIGEN_AOCL_BLIS_COMPATIBLE)
#define EIGEN_USE_BLAS  // Enable Eigen BLAS backend
#endif

#define EIGEN_USE_LAPACKE  // Enable LAPACK backend (FLAME)
#endif

#ifdef EIGEN_USE_AOCL_MT
#define EIGEN_USE_AOCL_VML   // Enable AOCL Vector Math Library
#define EIGEN_USE_AOCL_BLAS  // Enable AOCL BLAS (BLIS)

// For multithreaded: disable EIGEN_USE_BLAS to avoid signature conflicts
// Use direct BLIS calls instead through EIGEN_USE_AOCL_BLAS
// #define EIGEN_USE_BLAS       // Commented out - causes conflicts with BLIS
// interface

// Note: LAPACKE disabled in MT mode to avoid header conflicts
#define EIGEN_USE_LAPACKE         // Commented out - causes conflicts with BLIS LAPACKE
#define EIGEN_AOCL_USE_BLIS_MT 1  // Enable multithreaded BLIS
#endif

// Handle standalone EIGEN_USE_AOCL_VML flag
#ifndef EIGEN_USE_AOCL_VML
#ifdef EIGEN_USE_AOCL_ALL
#define EIGEN_USE_AOCL_VML
#endif
#ifdef EIGEN_USE_AOCL_MT
#define EIGEN_USE_AOCL_VML
#endif
#endif

// Configuration constants - define these for any AOCL usage
#ifndef EIGEN_AOCL_VML_THRESHOLD
#define EIGEN_AOCL_VML_THRESHOLD 128  // Threshold for VML dispatch
#endif

#ifndef AOCL_SIMD_WIDTH
#define AOCL_SIMD_WIDTH 8  // AVX-512: 512 bits / 64 bits per double
#endif

// Include AOCL Math Library headers for VML
#if defined(EIGEN_USE_AOCL_VML) || defined(EIGEN_USE_AOCL_ALL) || defined(EIGEN_USE_AOCL_MT)
#if defined(__has_include)
#if __has_include("amdlibm.h")
#include "amdlibm.h"
#ifndef AMD_LIBM_VEC_EXPERIMENTAL
#define AMD_LIBM_VEC_EXPERIMENTAL
#endif
#if __has_include("amdlibm_vec.h")
#include "amdlibm_vec.h"
#endif
#endif
#else
// Fallback for compilers without __has_include
#include "amdlibm.h"
#ifndef AMD_LIBM_VEC_EXPERIMENTAL
#define AMD_LIBM_VEC_EXPERIMENTAL
#endif
#include "amdlibm_vec.h"
#endif
#endif

// Include CBLAS headers when BLAS is enabled
#ifdef EIGEN_USE_AOCL_BLAS
#if defined(__has_include)
#if __has_include("cblas.h")
#include "cblas.h"
#elif __has_include("blis.h")
#include "blis.h"
#endif
#else
// Fallback
#include "cblas.h"
#endif
#endif

namespace Eigen {
// AOCL-specific type definitions
typedef std::complex<double> dcomplex;
typedef std::complex<float> scomplex;
typedef int BlasIndex;  // Standard BLAS index type
}  // namespace Eigen

#endif  // EIGEN_USE_AOCL_ALL || EIGEN_USE_AOCL_MT

#endif  // EIGEN_AOCL_SUPPORT_H
