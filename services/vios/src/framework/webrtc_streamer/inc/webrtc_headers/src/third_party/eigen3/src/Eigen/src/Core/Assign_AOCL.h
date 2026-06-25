/*
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 *
 * Assign_AOCL.h - AOCL Vectorized Math Dispatch Layer for Eigen
 *
 * Copyright (c) 2025, Advanced Micro Devices, Inc. All rights reserved.
 *
 * Description:
 * ------------
 * This file implements a high-performance dispatch layer that automatically
 * routes Eigen's element-wise mathematical operations to AMD Optimizing CPU
 * Libraries (AOCL) Vector Math Library (VML) functions when beneficial for
 * performance.
 *
 * The dispatch system uses C++ template specialization to intercept Eigen's
 * assignment operations and redirect them to AOCL's VRDA functions, which
 * provide optimized implementations for AMD Zen architectures.
 *
 * Key Features:
 * -------------
 * 1. Automatic Dispatch: Seamlessly routes supported operations to AOCL without
 *    requiring code changes in user applications
 *
 * 2. Performance Optimization: Uses AOCL VRDA functions optimized for Zen
 * family processors with automatic SIMD instruction selection (AVX2, AVX-512)
 *
 * 3. Threshold-Based Activation: Only activates for vectors larger than
 *    EIGEN_AOCL_VML_THRESHOLD (default: 128 elements) to avoid overhead on
 * small vectors
 *
 * 4. Precision-Specific Handling:
 *    - Double precision: AOCL VRDA vectorized functions
 *    - Single precision: Scalar fallback (preserves correctness)
 *
 * 5. Memory Layout Compatibility: Ensures direct memory access and compatible
 *    storage orders between source and destination for optimal performance
 *
 * Supported Operations:
 * ---------------------
 * UNARY OPERATIONS (vector → vector):
 * - Transcendental: exp(), sin(), cos(), sqrt(), log(), log10(), log2()
 *
 * BINARY OPERATIONS (vector op vector → vector):
 * - Arithmetic: +, *, pow()
 *
 * Template Specialization Mechanism:
 * -----------------------------------
 * The system works by specializing Eigen's Assignment template for:
 * 1. CwiseUnaryOp with scalar_*_op functors (unary operations)
 * 2. CwiseBinaryOp with scalar_*_op functors (binary operations)
 * 3. Dense2Dense assignment context with AOCL-compatible traits
 *
 * Dispatch conditions (all must be true):
 * - Source and destination have DirectAccessBit (contiguous memory)
 * - Compatible storage orders (both row-major or both column-major)
 * - Vector size ≥ EIGEN_AOCL_VML_THRESHOLD or Dynamic size
 * - Supported data type (currently double precision for VRDA)
 *
 * Integration Example:
 * --------------------
 * // Standard Eigen code - no changes required
 * VectorXd x = VectorXd::Random(10000);
 * VectorXd y = VectorXd::Random(10000);
 * VectorXd result;
 *
 * // These operations are automatically dispatched to AOCL:
 * result = x.array().exp();              // → amd_vrda_exp()
 * result = x.array().sin();              // → amd_vrda_sin()
 * result = x.array() + y.array();        // → amd_vrda_add()
 * result = x.array().pow(y.array());     // → amd_vrda_pow()
 *
 * Configuration:
 * --------------
 * Required preprocessor definitions:
 * - EIGEN_USE_AOCL_ALL or EIGEN_USE_AOCL_MT: Enable AOCL integration
 * - EIGEN_USE_AOCL_VML: Enable Vector Math Library dispatch
 *
 * Compilation Requirements:
 * -------------------------
 * Include paths:
 * - AOCL headers: -I${AOCL_ROOT}/include
 * - Eigen headers: -I/path/to/eigen
 *
 * Link libraries:
 * - AOCL MathLib: -lamdlibm
 * - Standard math: -lm
 *
 * Compiler flags:
 * - Optimization: -O3 (required for inlining)
 * - Architecture: -march=znver5 or -march=native
 * - Vectorization: -mfma -mavx512f (if supported)
 *
 * Platform Support:
 * ------------------
 * - Primary: Linux x86_64 with AMD Zen family processors
 * - Compilers: GCC 8+, Clang 10+, AOCC (recommended)
 * - AOCL Version: 4.0+ (with VRDA support)
 *
 * Error Handling:
 * ---------------
 * - Graceful fallback to scalar operations for unsupported configurations
 * - Compile-time detection of AOCL availability
 * - Runtime size and alignment validation with eigen_assert()
 *
 * Developer:
 * ----------
 * Name: Sharad Saurabh Bhaskar
 * Email: shbhaska@amd.com
 * Organization: Advanced Micro Devices, Inc.
 */
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_ASSIGN_AOCL_H
#define EIGEN_ASSIGN_AOCL_H

namespace Eigen {
namespace internal {

// Traits for unary operations.
template <typename Dst, typename Src>
class aocl_assign_traits {
 private:
  enum {
    DstHasDirectAccess = !!(Dst::Flags & DirectAccessBit),
    SrcHasDirectAccess = !!(Src::Flags & DirectAccessBit),
    StorageOrdersAgree = (int(Dst::IsRowMajor) == int(Src::IsRowMajor)),
    InnerSize = Dst::IsVectorAtCompileTime   ? int(Dst::SizeAtCompileTime)
                : (Dst::Flags & RowMajorBit) ? int(Dst::ColsAtCompileTime)
                                             : int(Dst::RowsAtCompileTime),
    LargeEnough = (InnerSize == Dynamic) || (InnerSize >= EIGEN_AOCL_VML_THRESHOLD)
  };

 public:
  enum {
    EnableAoclVML = DstHasDirectAccess && SrcHasDirectAccess && StorageOrdersAgree && LargeEnough,
    Traversal = LinearTraversal
  };
};

// Traits for binary operations (e.g., add, pow).
template <typename Dst, typename Lhs, typename Rhs>
class aocl_assign_binary_traits {
 private:
  enum {
    DstHasDirectAccess = !!(Dst::Flags & DirectAccessBit),
    LhsHasDirectAccess = !!(Lhs::Flags & DirectAccessBit),
    RhsHasDirectAccess = !!(Rhs::Flags & DirectAccessBit),
    StorageOrdersAgree =
        (int(Dst::IsRowMajor) == int(Lhs::IsRowMajor)) && (int(Dst::IsRowMajor) == int(Rhs::IsRowMajor)),
    InnerSize = Dst::IsVectorAtCompileTime   ? int(Dst::SizeAtCompileTime)
                : (Dst::Flags & RowMajorBit) ? int(Dst::ColsAtCompileTime)
                                             : int(Dst::RowsAtCompileTime),
    LargeEnough = (InnerSize == Dynamic) || (InnerSize >= EIGEN_AOCL_VML_THRESHOLD)
  };

 public:
  enum {
    EnableAoclVML = DstHasDirectAccess && LhsHasDirectAccess && RhsHasDirectAccess && StorageOrdersAgree && LargeEnough
  };
};

// Unary operation dispatch for float (scalar fallback).
#define EIGEN_AOCL_VML_UNARY_CALL_FLOAT(EIGENOP)                                                                   \
  template <typename DstXprType, typename SrcXprNested>                                                            \
  struct Assignment<DstXprType, CwiseUnaryOp<scalar_##EIGENOP##_op<float>, SrcXprNested>, assign_op<float, float>, \
                    Dense2Dense, std::enable_if_t<aocl_assign_traits<DstXprType, SrcXprNested>::EnableAoclVML>> {  \
    typedef CwiseUnaryOp<scalar_##EIGENOP##_op<float>, SrcXprNested> SrcXprType;                                   \
    static void run(DstXprType &dst, const SrcXprType &src, const assign_op<float, float> &) {                     \
      eigen_assert(dst.rows() == src.rows() && dst.cols() == src.cols());                                          \
      Eigen::Index n = dst.size();                                                                                 \
      if (n <= 0) return;                                                                                          \
      const float *input = reinterpret_cast<const float *>(src.nestedExpression().data());                         \
      float *output = reinterpret_cast<float *>(dst.data());                                                       \
      for (Eigen::Index i = 0; i < n; ++i) {                                                                       \
        output[i] = std::EIGENOP(input[i]);                                                                        \
      }                                                                                                            \
    }                                                                                                              \
  };

// Unary operation dispatch for double (AOCL vectorized).
#define EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(EIGENOP, AOCLOP)                                                             \
  template <typename DstXprType, typename SrcXprNested>                                                               \
  struct Assignment<DstXprType, CwiseUnaryOp<scalar_##EIGENOP##_op<double>, SrcXprNested>, assign_op<double, double>, \
                    Dense2Dense, std::enable_if_t<aocl_assign_traits<DstXprType, SrcXprNested>::EnableAoclVML>> {     \
    typedef CwiseUnaryOp<scalar_##EIGENOP##_op<double>, SrcXprNested> SrcXprType;                                     \
    static void run(DstXprType &dst, const SrcXprType &src, const assign_op<double, double> &) {                      \
      eigen_assert(dst.rows() == src.rows() && dst.cols() == src.cols());                                             \
      Eigen::Index n = dst.size();                                                                                    \
      eigen_assert(n <= INT_MAX && "AOCL does not support arrays larger than INT_MAX");                               \
      if (n <= 0) return;                                                                                             \
      const double *input = reinterpret_cast<const double *>(src.nestedExpression().data());                          \
      double *output = reinterpret_cast<double *>(dst.data());                                                        \
      int aocl_n = internal::convert_index<int>(n);                                                                   \
      AOCLOP(aocl_n, const_cast<double *>(input), output);                                                            \
    }                                                                                                                 \
  };

// Instantiate unary calls for float (scalar).
// EIGEN_AOCL_VML_UNARY_CALL_FLOAT(exp)

// Instantiate unary calls for double (AOCL vectorized).
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(exp2, amd_vrda_exp2)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(exp, amd_vrda_exp)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(sin, amd_vrda_sin)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(cos, amd_vrda_cos)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(sqrt, amd_vrda_sqrt)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(cbrt, amd_vrda_cbrt)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(abs, amd_vrda_fabs)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(log, amd_vrda_log)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(log10, amd_vrda_log10)
EIGEN_AOCL_VML_UNARY_CALL_DOUBLE(log2, amd_vrda_log2)

// Binary operation dispatch for float (scalar fallback).
#define EIGEN_AOCL_VML_BINARY_CALL_FLOAT(EIGENOP, STDFUNC)                                                  \
  template <typename DstXprType, typename LhsXprNested, typename RhsXprNested>                              \
  struct Assignment<                                                                                        \
      DstXprType, CwiseBinaryOp<scalar_##EIGENOP##_op<float, float>, LhsXprNested, RhsXprNested>,           \
      assign_op<float, float>, Dense2Dense,                                                                 \
      std::enable_if_t<aocl_assign_binary_traits<DstXprType, LhsXprNested, RhsXprNested>::EnableAoclVML>> { \
    typedef CwiseBinaryOp<scalar_##EIGENOP##_op<float, float>, LhsXprNested, RhsXprNested> SrcXprType;      \
    static void run(DstXprType &dst, const SrcXprType &src, const assign_op<float, float> &) {              \
      eigen_assert(dst.rows() == src.rows() && dst.cols() == src.cols());                                   \
      Eigen::Index n = dst.size();                                                                          \
      if (n <= 0) return;                                                                                   \
      const float *lhs = reinterpret_cast<const float *>(src.lhs().data());                                 \
      const float *rhs = reinterpret_cast<const float *>(src.rhs().data());                                 \
      float *output = reinterpret_cast<float *>(dst.data());                                                \
      for (Eigen::Index i = 0; i < n; ++i) {                                                                \
        output[i] = STDFUNC(lhs[i], rhs[i]);                                                                \
      }                                                                                                     \
    }                                                                                                       \
  };

// Binary operation dispatch for double (AOCL vectorized).
#define EIGEN_AOCL_VML_BINARY_CALL_DOUBLE(EIGENOP, AOCLOP)                                                  \
  template <typename DstXprType, typename LhsXprNested, typename RhsXprNested>                              \
  struct Assignment<                                                                                        \
      DstXprType, CwiseBinaryOp<scalar_##EIGENOP##_op<double, double>, LhsXprNested, RhsXprNested>,         \
      assign_op<double, double>, Dense2Dense,                                                               \
      std::enable_if_t<aocl_assign_binary_traits<DstXprType, LhsXprNested, RhsXprNested>::EnableAoclVML>> { \
    typedef CwiseBinaryOp<scalar_##EIGENOP##_op<double, double>, LhsXprNested, RhsXprNested> SrcXprType;    \
    static void run(DstXprType &dst, const SrcXprType &src, const assign_op<double, double> &) {            \
      eigen_assert(dst.rows() == src.rows() && dst.cols() == src.cols());                                   \
      Eigen::Index n = dst.size();                                                                          \
      eigen_assert(n <= INT_MAX && "AOCL does not support arrays larger than INT_MAX");                     \
      if (n <= 0) return;                                                                                   \
      const double *lhs = reinterpret_cast<const double *>(src.lhs().data());                               \
      const double *rhs = reinterpret_cast<const double *>(src.rhs().data());                               \
      double *output = reinterpret_cast<double *>(dst.data());                                              \
      int aocl_n = internal::convert_index<int>(n);                                                         \
      AOCLOP(aocl_n, const_cast<double *>(lhs), const_cast<double *>(rhs), output);                         \
    }                                                                                                       \
  };

// Instantiate binary calls for float (scalar).
// EIGEN_AOCL_VML_BINARY_CALL_FLOAT(sum, std::plus<float>)  // Using
// scalar_sum_op for addition EIGEN_AOCL_VML_BINARY_CALL_FLOAT(pow, std::pow)

// Instantiate binary calls for double (AOCL vectorized).
EIGEN_AOCL_VML_BINARY_CALL_DOUBLE(sum, amd_vrda_add)  // Using scalar_sum_op for addition
EIGEN_AOCL_VML_BINARY_CALL_DOUBLE(pow, amd_vrda_pow)
EIGEN_AOCL_VML_BINARY_CALL_DOUBLE(max, amd_vrda_fmax)
EIGEN_AOCL_VML_BINARY_CALL_DOUBLE(min, amd_vrda_fmin)

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_ASSIGN_AOCL_H
