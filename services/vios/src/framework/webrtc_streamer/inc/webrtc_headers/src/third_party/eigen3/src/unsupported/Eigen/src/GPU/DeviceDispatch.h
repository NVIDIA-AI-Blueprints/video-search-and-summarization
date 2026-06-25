// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_GPU_DEVICE_DISPATCH_H
#define EIGEN_GPU_DEVICE_DISPATCH_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include <climits>

#include "./DeviceExpr.h"
#include "./DeviceBlasExpr.h"
#include "./DeviceSolverExpr.h"
#include "./GpuContext.h"
#include "./CuSolverSupport.h"

namespace Eigen {
namespace gpu {
namespace internal {

template <typename Scalar>
bool aliases_device_memory(const DeviceMatrix<Scalar>& a, const DeviceMatrix<Scalar>& b) {
  return a.data() != nullptr && a.data() == b.data();
}

template <typename Lhs, typename Rhs>
void dispatch_gemm(
    Context& ctx, DeviceMatrix<typename device_expr_traits<Lhs>::scalar_type>& dst, const GemmExpr<Lhs, Rhs>& expr,
    typename device_expr_traits<Lhs>::scalar_type beta_val,
    typename device_expr_traits<Lhs>::scalar_type alpha_scale = typename device_expr_traits<Lhs>::scalar_type(1)) {
  using Scalar = typename device_expr_traits<Lhs>::scalar_type;
  using traits_lhs = device_expr_traits<Lhs>;
  using traits_rhs = device_expr_traits<Rhs>;

  const DeviceMatrix<Scalar>& A = traits_lhs::matrix(expr.lhs());
  const DeviceMatrix<Scalar>& B = traits_rhs::matrix(expr.rhs());

  constexpr cublasOperation_t transA = to_cublas_op(traits_lhs::op);
  constexpr cublasOperation_t transB = to_cublas_op(traits_rhs::op);

  const int64_t m = (traits_lhs::op == GpuOp::NoTrans) ? A.rows() : A.cols();
  const int64_t k = (traits_lhs::op == GpuOp::NoTrans) ? A.cols() : A.rows();
  const int64_t n = (traits_rhs::op == GpuOp::NoTrans) ? B.cols() : B.rows();
  const int64_t rhs_k = (traits_rhs::op == GpuOp::NoTrans) ? B.rows() : B.cols();

  eigen_assert(k == rhs_k && "DeviceMatrix GEMM dimension mismatch");

  const int64_t lda = A.rows();
  const int64_t ldb = B.rows();

  eigen_assert(!aliases_device_memory(dst, A) && "DeviceMatrix GEMM destination aliases lhs operand");
  eigen_assert(!aliases_device_memory(dst, B) && "DeviceMatrix GEMM destination aliases rhs operand");

  if (!dst.empty()) {
    dst.waitReady(ctx.stream());
  }

  const bool resized = dst.empty() || dst.rows() != m || dst.cols() != n;
  if (resized) {
    dst.resize(m, n);
  }
  const int64_t ldc = dst.rows();

  Scalar alpha_local = alpha_scale * traits_lhs::alpha(expr.lhs()) * traits_rhs::alpha(expr.rhs());

  A.waitReady(ctx.stream());
  B.waitReady(ctx.stream());

  if (resized && beta_val != Scalar(0) && dst.sizeInBytes() > 0) {
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemsetAsync(dst.data(), 0, dst.sizeInBytes(), ctx.stream()));
  }

  eigen_assert(m <= INT_MAX && n <= INT_MAX && k <= INT_MAX && lda <= INT_MAX && ldb <= INT_MAX && ldc <= INT_MAX &&
               "cublasXgemm dimensions exceed int range");

  // cuBLAS reads alpha and beta through host pointers. Store them in an array
  // to prevent the compiler from eliding their stack slots — clang and MSVC
  // at -O1+ otherwise optimise away the stores for complex types, leaving
  // cuBLAS with a dangling pointer.
  Scalar scalars[2] = {alpha_local, beta_val};
  EIGEN_CUBLAS_CHECK(cublasXgemm(ctx.cublasHandle(), transA, transB, static_cast<int>(m), static_cast<int>(n),
                                 static_cast<int>(k), &scalars[0], A.data(), static_cast<int>(lda), B.data(),
                                 static_cast<int>(ldb), &scalars[1], dst.data(), static_cast<int>(ldc)));

  dst.recordReady(ctx.stream());
}

template <typename Scalar, int UpLo>
void dispatch_llt_solve(Context& ctx, DeviceMatrix<Scalar>& dst, const LltSolveExpr<Scalar, UpLo>& expr) {
  const DeviceMatrix<Scalar>& A = expr.matrix();
  const DeviceMatrix<Scalar>& B = expr.rhs();

  eigen_assert(A.rows() == A.cols() && "LLT requires a square matrix");
  eigen_assert(B.rows() == A.rows() && "LLT solve: RHS rows must match matrix size");

  const int64_t n = static_cast<int64_t>(A.rows());
  const int64_t nrhs = static_cast<int64_t>(B.cols());

  if (n == 0 || nrhs == 0) {
    if (!dst.empty()) dst.waitReady(ctx.stream());
    dst.resize(n, B.cols());
    return;
  }

  A.waitReady(ctx.stream());
  B.waitReady(ctx.stream());
  if (!dst.empty()) dst.waitReady(ctx.stream());

  constexpr cudaDataType_t dtype = cuda_data_type<Scalar>::value;
  constexpr cublasFillMode_t uplo = cusolver_fill_mode<UpLo>::value;
  const int64_t lda = static_cast<int64_t>(A.rows());
  const int64_t ldb = static_cast<int64_t>(B.rows());
  const size_t mat_bytes = static_cast<size_t>(lda) * static_cast<size_t>(n) * sizeof(Scalar);
  const size_t rhs_bytes = static_cast<size_t>(ldb) * static_cast<size_t>(nrhs) * sizeof(Scalar);

  DeviceBuffer d_factor(mat_bytes);
  EIGEN_CUDA_RUNTIME_CHECK(
      cudaMemcpyAsync(d_factor.get(), A.data(), mat_bytes, cudaMemcpyDeviceToDevice, ctx.stream()));

  // Two info slots (potrf, potrs) so we can queue both kernels back-to-back
  // and host-sync once at the end. If potrf fails, potrs runs on garbage but
  // the assert fires after the single sync — saving a round trip.
  PinnedHostBuffer h_info(2 * sizeof(int));
  int* info_words = static_cast<int*>(h_info.get());

  CusolverParams params;
  DeviceBuffer d_info(2 * sizeof(int));
  int* d_info_potrf = static_cast<int*>(d_info.get());
  int* d_info_potrs = d_info_potrf + 1;
  size_t dev_ws = 0, host_ws = 0;
  EIGEN_CUSOLVER_CHECK(cusolverDnXpotrf_bufferSize(ctx.cusolverHandle(), params.p, uplo, n, dtype, d_factor.get(), lda,
                                                   dtype, &dev_ws, &host_ws));

  DeviceBuffer d_workspace(dev_ws);
  std::vector<char> h_workspace(host_ws);

  EIGEN_CUSOLVER_CHECK(cusolverDnXpotrf(ctx.cusolverHandle(), params.p, uplo, n, dtype, d_factor.get(), lda, dtype,
                                        d_workspace.get(), dev_ws, host_ws > 0 ? h_workspace.data() : nullptr, host_ws,
                                        d_info_potrf));

  EIGEN_CUDA_RUNTIME_CHECK(
      cudaMemcpyAsync(&info_words[0], d_info_potrf, sizeof(int), cudaMemcpyDeviceToHost, ctx.stream()));

  dst.resize(n, B.cols());
  EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(dst.data(), B.data(), rhs_bytes, cudaMemcpyDeviceToDevice, ctx.stream()));

  EIGEN_CUSOLVER_CHECK(cusolverDnXpotrs(ctx.cusolverHandle(), params.p, uplo, n, nrhs, dtype, d_factor.get(), lda,
                                        dtype, dst.data(), static_cast<int64_t>(dst.rows()), d_info_potrs));

  // Workspace locals must outlive the async kernels — sync before they unwind.
  EIGEN_CUDA_RUNTIME_CHECK(
      cudaMemcpyAsync(&info_words[1], d_info_potrs, sizeof(int), cudaMemcpyDeviceToHost, ctx.stream()));
  EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(ctx.stream()));
  eigen_assert(info_words[0] == 0 && "cuSOLVER LLT factorization failed (matrix not positive definite)");
  eigen_assert(info_words[1] == 0 && "cuSOLVER LLT solve failed");

  dst.recordReady(ctx.stream());
}

template <typename Scalar>
void dispatch_lu_solve(Context& ctx, DeviceMatrix<Scalar>& dst, const LuSolveExpr<Scalar>& expr) {
  const DeviceMatrix<Scalar>& A = expr.matrix();
  const DeviceMatrix<Scalar>& B = expr.rhs();

  eigen_assert(A.rows() == A.cols() && "LU requires a square matrix");
  eigen_assert(B.rows() == A.rows() && "LU solve: RHS rows must match matrix size");

  const int64_t n = static_cast<int64_t>(A.rows());
  const int64_t nrhs = static_cast<int64_t>(B.cols());

  if (n == 0 || nrhs == 0) {
    if (!dst.empty()) dst.waitReady(ctx.stream());
    dst.resize(n, B.cols());
    return;
  }

  A.waitReady(ctx.stream());
  B.waitReady(ctx.stream());
  if (!dst.empty()) dst.waitReady(ctx.stream());

  constexpr cudaDataType_t dtype = cuda_data_type<Scalar>::value;
  const int64_t lda = static_cast<int64_t>(A.rows());
  const int64_t ldb = static_cast<int64_t>(B.rows());
  const size_t mat_bytes = static_cast<size_t>(lda) * static_cast<size_t>(n) * sizeof(Scalar);
  const size_t rhs_bytes = static_cast<size_t>(ldb) * static_cast<size_t>(nrhs) * sizeof(Scalar);
  const size_t ipiv_bytes = static_cast<size_t>(n) * sizeof(int64_t);

  DeviceBuffer d_lu(mat_bytes);
  EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_lu.get(), A.data(), mat_bytes, cudaMemcpyDeviceToDevice, ctx.stream()));

  DeviceBuffer d_ipiv(ipiv_bytes);

  PinnedHostBuffer h_info(2 * sizeof(int));
  int* info_words = static_cast<int*>(h_info.get());

  CusolverParams params;
  DeviceBuffer d_info(2 * sizeof(int));
  int* d_info_getrf = static_cast<int*>(d_info.get());
  int* d_info_getrs = d_info_getrf + 1;
  size_t dev_ws = 0, host_ws = 0;
  EIGEN_CUSOLVER_CHECK(cusolverDnXgetrf_bufferSize(ctx.cusolverHandle(), params.p, n, n, dtype, d_lu.get(), lda, dtype,
                                                   &dev_ws, &host_ws));

  DeviceBuffer d_workspace(dev_ws);
  std::vector<char> h_workspace(host_ws);

  EIGEN_CUSOLVER_CHECK(cusolverDnXgetrf(ctx.cusolverHandle(), params.p, n, n, dtype, d_lu.get(), lda,
                                        static_cast<int64_t*>(d_ipiv.get()), dtype, d_workspace.get(), dev_ws,
                                        host_ws > 0 ? h_workspace.data() : nullptr, host_ws, d_info_getrf));

  EIGEN_CUDA_RUNTIME_CHECK(
      cudaMemcpyAsync(&info_words[0], d_info_getrf, sizeof(int), cudaMemcpyDeviceToHost, ctx.stream()));

  dst.resize(n, B.cols());
  EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(dst.data(), B.data(), rhs_bytes, cudaMemcpyDeviceToDevice, ctx.stream()));

  EIGEN_CUSOLVER_CHECK(cusolverDnXgetrs(ctx.cusolverHandle(), params.p, CUBLAS_OP_N, n, nrhs, dtype, d_lu.get(), lda,
                                        static_cast<const int64_t*>(d_ipiv.get()), dtype, dst.data(),
                                        static_cast<int64_t>(dst.rows()), d_info_getrs));

  // Workspace locals must outlive the async kernels — sync before they unwind.
  EIGEN_CUDA_RUNTIME_CHECK(
      cudaMemcpyAsync(&info_words[1], d_info_getrs, sizeof(int), cudaMemcpyDeviceToHost, ctx.stream()));
  EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(ctx.stream()));
  eigen_assert(info_words[0] == 0 && "cuSOLVER LU factorization failed (singular matrix)");
  eigen_assert(info_words[1] == 0 && "cuSOLVER LU solve failed");

  dst.recordReady(ctx.stream());
}

template <typename Scalar, int UpLo>
void dispatch_trsm(Context& ctx, DeviceMatrix<Scalar>& dst, const TrsmExpr<Scalar, UpLo>& expr) {
  const DeviceMatrix<Scalar>& A = expr.matrix();
  const DeviceMatrix<Scalar>& B = expr.rhs();

  eigen_assert(A.rows() == A.cols() && "TRSM requires a square triangular matrix");
  eigen_assert(B.rows() == A.rows() && "TRSM: RHS rows must match matrix size");

  eigen_assert(A.rows() <= INT_MAX && B.cols() <= INT_MAX && "cublasXtrsm dimensions exceed int range");

  const int n = static_cast<int>(A.rows());
  const int nrhs = static_cast<int>(B.cols());

  if (n == 0 || nrhs == 0) {
    if (!dst.empty()) dst.waitReady(ctx.stream());
    dst.resize(n, B.cols());
    return;
  }

  A.waitReady(ctx.stream());
  B.waitReady(ctx.stream());
  eigen_assert(!aliases_device_memory(dst, A) && "DeviceMatrix TRSM destination aliases triangular operand");
  eigen_assert(!aliases_device_memory(dst, B) && "DeviceMatrix TRSM destination aliases RHS operand");
  if (!dst.empty()) dst.waitReady(ctx.stream());

  dst.resize(n, B.cols());
  const size_t rhs_bytes = static_cast<size_t>(dst.rows()) * static_cast<size_t>(nrhs) * sizeof(Scalar);
  EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(dst.data(), B.data(), rhs_bytes, cudaMemcpyDeviceToDevice, ctx.stream()));

  constexpr cublasFillMode_t uplo = (UpLo == Lower) ? CUBLAS_FILL_MODE_LOWER : CUBLAS_FILL_MODE_UPPER;
  Scalar alpha(1);

  EIGEN_CUBLAS_CHECK(cublasXtrsm(ctx.cublasHandle(), CUBLAS_SIDE_LEFT, uplo, CUBLAS_OP_N, CUBLAS_DIAG_NON_UNIT, n, nrhs,
                                 &alpha, A.data(), static_cast<int>(A.rows()), dst.data(),
                                 static_cast<int>(dst.rows())));

  dst.recordReady(ctx.stream());
}

template <typename Scalar, int UpLo>
void dispatch_symm(Context& ctx, DeviceMatrix<Scalar>& dst, const SymmExpr<Scalar, UpLo>& expr) {
  const DeviceMatrix<Scalar>& A = expr.matrix();
  const DeviceMatrix<Scalar>& B = expr.rhs();

  eigen_assert(A.rows() == A.cols() && "SYMM requires a square matrix");
  eigen_assert(B.rows() == A.rows() && "SYMM: RHS rows must match matrix size");
  eigen_assert(A.rows() <= INT_MAX && B.cols() <= INT_MAX && B.rows() <= INT_MAX &&
               "cublasXsymm dimensions exceed int range");

  const int m = static_cast<int>(A.rows());
  const int n = static_cast<int>(B.cols());

  if (m == 0 || n == 0) {
    if (!dst.empty()) dst.waitReady(ctx.stream());
    dst.resize(m, B.cols());
    return;
  }

  A.waitReady(ctx.stream());
  B.waitReady(ctx.stream());
  eigen_assert(!aliases_device_memory(dst, A) && "DeviceMatrix SYMM destination aliases self-adjoint operand");
  eigen_assert(!aliases_device_memory(dst, B) && "DeviceMatrix SYMM destination aliases RHS operand");
  if (!dst.empty()) dst.waitReady(ctx.stream());

  dst.resize(m, n);

  constexpr cublasFillMode_t uplo = (UpLo == Lower) ? CUBLAS_FILL_MODE_LOWER : CUBLAS_FILL_MODE_UPPER;
  // See dispatch_gemm: array prevents compiler from eliding host-pointer stack slots.
  Scalar scalars[2] = {Scalar(1), Scalar(0)};

  EIGEN_CUBLAS_CHECK(cublasXsymm(ctx.cublasHandle(), CUBLAS_SIDE_LEFT, uplo, m, n, &scalars[0], A.data(),
                                 static_cast<int>(A.rows()), B.data(), static_cast<int>(B.rows()), &scalars[1],
                                 dst.data(), static_cast<int>(dst.rows())));

  dst.recordReady(ctx.stream());
}

template <typename Scalar, int UpLo>
void dispatch_syrk(Context& ctx, DeviceMatrix<Scalar>& dst, const SyrkExpr<Scalar, UpLo>& expr,
                   typename NumTraits<Scalar>::Real alpha_val, typename NumTraits<Scalar>::Real beta_val) {
  using RealScalar = typename NumTraits<Scalar>::Real;
  const DeviceMatrix<Scalar>& A = expr.matrix();

  eigen_assert(A.rows() <= INT_MAX && A.cols() <= INT_MAX && "cublasXsyrk dimensions exceed int range");

  const int n = static_cast<int>(A.rows());
  const int k = static_cast<int>(A.cols());

  if (n == 0) {
    if (!dst.empty()) dst.waitReady(ctx.stream());
    dst.resize(0, 0);
    return;
  }

  A.waitReady(ctx.stream());
  eigen_assert(!aliases_device_memory(dst, A) && "DeviceMatrix SYRK destination aliases input operand");
  if (!dst.empty()) dst.waitReady(ctx.stream());

  if (dst.empty() || dst.rows() != n || dst.cols() != n) {
    dst.resize(n, n);
    if (beta_val != RealScalar(0)) {
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemsetAsync(dst.data(), 0, dst.sizeInBytes(), ctx.stream()));
    }
  }

  constexpr cublasFillMode_t uplo = (UpLo == Lower) ? CUBLAS_FILL_MODE_LOWER : CUBLAS_FILL_MODE_UPPER;

  EIGEN_CUBLAS_CHECK(cublasXsyrk(ctx.cublasHandle(), uplo, CUBLAS_OP_N, n, k, &alpha_val, A.data(),
                                 static_cast<int>(A.rows()), &beta_val, dst.data(), static_cast<int>(dst.rows())));

  dst.recordReady(ctx.stream());
}

}  // namespace internal

template <typename Scalar_>
class Assignment {
 public:
  using Scalar = Scalar_;

  Assignment(DeviceMatrix<Scalar>& dst, Context& ctx) : dst_(dst), ctx_(ctx) {}

  template <typename Lhs, typename Rhs>
  DeviceMatrix<Scalar>& operator=(const GemmExpr<Lhs, Rhs>& expr) {
    internal::dispatch_gemm(ctx_, dst_, expr, Scalar(0));
    return dst_;
  }

  template <typename Lhs, typename Rhs>
  DeviceMatrix<Scalar>& operator+=(const GemmExpr<Lhs, Rhs>& expr) {
    internal::dispatch_gemm(ctx_, dst_, expr, Scalar(1));
    return dst_;
  }

  template <typename Lhs, typename Rhs>
  DeviceMatrix<Scalar>& operator-=(const GemmExpr<Lhs, Rhs>& expr) {
    internal::dispatch_gemm(ctx_, dst_, expr, Scalar(1), Scalar(-1));
    return dst_;
  }

  template <int UpLo>
  DeviceMatrix<Scalar>& operator=(const LltSolveExpr<Scalar, UpLo>& expr) {
    internal::dispatch_llt_solve(ctx_, dst_, expr);
    return dst_;
  }

  DeviceMatrix<Scalar>& operator=(const LuSolveExpr<Scalar>& expr) {
    internal::dispatch_lu_solve(ctx_, dst_, expr);
    return dst_;
  }

  template <int UpLo>
  DeviceMatrix<Scalar>& operator=(const TrsmExpr<Scalar, UpLo>& expr) {
    internal::dispatch_trsm(ctx_, dst_, expr);
    return dst_;
  }

  template <int UpLo>
  DeviceMatrix<Scalar>& operator=(const SymmExpr<Scalar, UpLo>& expr) {
    internal::dispatch_symm(ctx_, dst_, expr);
    return dst_;
  }

  template <typename Expr>
  DeviceMatrix<Scalar>& operator=(const Expr&) {
    static_assert(sizeof(Expr) == 0,
                  "DeviceMatrix expression not supported: no cuBLAS/cuSOLVER mapping. "
                  "Supported: GEMM (A*B), TRSM (.triangularView().solve()), "
                  "SYMM (.selfadjointView()*B), LLT (.llt().solve()), LU (.lu().solve()).");
    return dst_;
  }

 private:
  DeviceMatrix<Scalar>& dst_;
  Context& ctx_;
};

// Out-of-line definitions: these depend on Context::threadLocal(), which
// requires the full Context definition unavailable in DeviceMatrix.h.

template <typename Scalar_>
template <typename Lhs, typename Rhs>
DeviceMatrix<Scalar_>& DeviceMatrix<Scalar_>::operator=(const GemmExpr<Lhs, Rhs>& expr) {
  device(Context::threadLocal()) = expr;
  return *this;
}

template <typename Scalar_>
template <typename Lhs, typename Rhs>
DeviceMatrix<Scalar_>& DeviceMatrix<Scalar_>::operator+=(const GemmExpr<Lhs, Rhs>& expr) {
  device(Context::threadLocal()) += expr;
  return *this;
}

template <typename Scalar_>
template <int UpLo>
DeviceMatrix<Scalar_>& DeviceMatrix<Scalar_>::operator=(const LltSolveExpr<Scalar_, UpLo>& expr) {
  device(Context::threadLocal()) = expr;
  return *this;
}

template <typename Scalar_>
DeviceMatrix<Scalar_>& DeviceMatrix<Scalar_>::operator=(const LuSolveExpr<Scalar_>& expr) {
  device(Context::threadLocal()) = expr;
  return *this;
}

template <typename Scalar_>
template <int UpLo>
DeviceMatrix<Scalar_>& DeviceMatrix<Scalar_>::operator=(const TrsmExpr<Scalar_, UpLo>& expr) {
  device(Context::threadLocal()) = expr;
  return *this;
}

template <typename Scalar_>
template <int UpLo>
DeviceMatrix<Scalar_>& DeviceMatrix<Scalar_>::operator=(const SymmExpr<Scalar_, UpLo>& expr) {
  device(Context::threadLocal()) = expr;
  return *this;
}

template <typename Scalar_, int UpLo_>
void SelfAdjointView<Scalar_, UpLo_>::rankUpdate(const DeviceMatrix<Scalar_>& A, RealScalar alpha) {
  SyrkExpr<Scalar_, UpLo_> expr(A);
  RealScalar beta = matrix().empty() ? RealScalar(0) : RealScalar(1);
  internal::dispatch_syrk(Context::threadLocal(), matrix(), expr, alpha, beta);
}

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_DEVICE_DISPATCH_H
