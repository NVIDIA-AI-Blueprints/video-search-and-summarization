// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Eigen Authors
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU Cholesky (LLT) decomposition using cuSOLVER.
//
// Unlike Eigen's CPU LLT<MatrixType>, gpu::LLT keeps the factored Cholesky
// factor in device memory for the lifetime of the object. Multiple solves
// against the same factor therefore only transfer the RHS and solution
// vectors, not the factor itself.
//
// Requires CUDA 11.0+ (cusolverDnXpotrf / cusolverDnXpotrs generic API).
// Requires CUDA 11.4+ (cusolverDnX generic API + cudaMallocAsync).
//
// Usage:
//   gpu::LLT<double> llt(A);            // upload A, potrf, L stays on device
//   if (llt.info() != Success) { ... }
//   MatrixXd x1 = llt.solve(b1);        // potrs, only b1 transferred
//   MatrixXd x2 = llt.solve(b2);        // L already on device

#ifndef EIGEN_GPU_LLT_H
#define EIGEN_GPU_LLT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSolverContext.h"

namespace Eigen {
namespace gpu {

/** \ingroup GPU_Module
 * \class LLT
 * \brief GPU Cholesky (LL^T) decomposition via cuSOLVER
 *
 * \tparam Scalar_  Element type: float, double, complex<float>, complex<double>
 * \tparam UpLo_    Triangle used: Lower (default) or Upper
 *
 * Factorizes a symmetric positive-definite matrix A = LL^H on the GPU and
 * caches the factor L in device memory. Each subsequent solve(B) uploads only
 * B, calls cusolverDnXpotrs, and downloads the result — the factor is not
 * re-transferred.
 *
 * Each LLT object owns a dedicated CUDA stream and cuSOLVER handle,
 * enabling concurrent factorizations from multiple objects on the same host
 * thread.
 */
template <typename Scalar_, int UpLo_ = Lower>
class LLT {
 public:
  using Scalar = Scalar_;
  using RealScalar = typename NumTraits<Scalar>::Real;
  using PlainMatrix = Eigen::Matrix<Scalar, Dynamic, Dynamic, ColMajor>;

  static constexpr int UpLo = UpLo_;

  // ---- Construction / destruction ------------------------------------------

  /** Default constructor. Does not factorize; call compute() before solve(). */
  LLT() = default;

  /** Factor A immediately. Equivalent to LLT llt; llt.compute(A). */
  template <typename InputType>
  explicit LLT(const EigenBase<InputType>& A) {
    compute(A);
  }

  ~LLT() = default;

  // Non-copyable (owns device memory and library handles).
  LLT(const LLT&) = delete;
  LLT& operator=(const LLT&) = delete;

  // Movable.
  LLT(LLT&& o) noexcept
      : solver_ctx_(std::move(o.solver_ctx_)),
        d_factor_(std::move(o.d_factor_)),
        factor_alloc_size_(o.factor_alloc_size_),
        n_(o.n_),
        lda_(o.lda_) {
    o.factor_alloc_size_ = 0;
    o.n_ = 0;
    o.lda_ = 0;
  }

  LLT& operator=(LLT&& o) noexcept {
    if (this != &o) {
      solver_ctx_ = std::move(o.solver_ctx_);
      d_factor_ = std::move(o.d_factor_);
      factor_alloc_size_ = o.factor_alloc_size_;
      n_ = o.n_;
      lda_ = o.lda_;
      o.factor_alloc_size_ = 0;
      o.n_ = 0;
      o.lda_ = 0;
    }
    return *this;
  }

  // ---- Factorization -------------------------------------------------------

  /** Compute the Cholesky factorization of A (host matrix). */
  template <typename InputType>
  LLT& compute(const EigenBase<InputType>& A) {
    eigen_assert(A.rows() == A.cols());
    if (!begin_compute(A.rows())) return *this;

    const PlainMatrix mat(A.derived());
    lda_ = static_cast<int64_t>(mat.rows());
    allocate_factor_storage();
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_factor_.get(), mat.data(), factorBytes(), cudaMemcpyHostToDevice, solver_ctx_.stream_));

    factorize();
    return *this;
  }

  /** Compute the Cholesky factorization from a device-resident matrix (D2D copy). */
  LLT& compute(const DeviceMatrix<Scalar>& d_A) {
    eigen_assert(d_A.rows() == d_A.cols());
    if (!begin_compute(d_A.rows())) return *this;

    lda_ = static_cast<int64_t>(d_A.rows());
    d_A.waitReady(solver_ctx_.stream_);
    allocate_factor_storage();
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_factor_.get(), d_A.data(), factorBytes(), cudaMemcpyDeviceToDevice, solver_ctx_.stream_));

    factorize();
    return *this;
  }

  /** Compute the Cholesky factorization from a device matrix (move, no copy). */
  LLT& compute(DeviceMatrix<Scalar>&& d_A) {
    eigen_assert(d_A.rows() == d_A.cols());
    if (!begin_compute(d_A.rows())) return *this;

    lda_ = static_cast<int64_t>(d_A.rows());
    d_A.waitReady(solver_ctx_.stream_);
    d_factor_ = internal::DeviceBuffer::adopt(static_cast<void*>(d_A.release()));
    factor_alloc_size_ = factorBytes();

    factorize();
    return *this;
  }

  // ---- Solve ---------------------------------------------------------------

  /** Solve A * X = B using the cached Cholesky factor (host → host). */
  template <typename Rhs>
  PlainMatrix solve(const MatrixBase<Rhs>& B) const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success && "LLT::solve called on a failed or uninitialized factorization");
    eigen_assert(B.rows() == n_);

    const PlainMatrix rhs(B);
    const int64_t nrhs = static_cast<int64_t>(rhs.cols());
    const int64_t ldb = static_cast<int64_t>(rhs.rows());
    internal::DeviceBuffer d_x(rhsBytes(nrhs, ldb));
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_x.get(), rhs.data(), rhsBytes(nrhs, ldb), cudaMemcpyHostToDevice, solver_ctx_.stream_));
    DeviceMatrix<Scalar> d_X = solve_impl(nrhs, ldb, std::move(d_x));

    PlainMatrix X(n_, B.cols());
    int solve_info = 0;
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(X.data(), d_X.data(), rhsBytes(nrhs, ldb), cudaMemcpyDeviceToHost, solver_ctx_.stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(&solve_info, solver_ctx_.scratch_info(), sizeof(int),
                                             cudaMemcpyDeviceToHost, solver_ctx_.stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(solver_ctx_.stream_));

    eigen_assert(solve_info == 0 && "cusolverDnXpotrs reported an error");
    return X;
  }

  /** Solve A * X = B with device-resident RHS. Returns immediately after enqueuing
   * the solve; the sync_info()/info_ check at entry forces a host wait on the
   * factorization status so a singular A causes a clean assert rather than a
   * silently-bad result. */
  DeviceMatrix<Scalar> solve(const DeviceMatrix<Scalar>& d_B) const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success && "LLT::solve called on a failed or uninitialized factorization");
    eigen_assert(d_B.rows() == n_);
    d_B.waitReady(solver_ctx_.stream_);
    const int64_t nrhs = static_cast<int64_t>(d_B.cols());
    const int64_t ldb = static_cast<int64_t>(d_B.rows());
    internal::DeviceBuffer d_x(rhsBytes(nrhs, ldb));
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_x.get(), d_B.data(), rhsBytes(nrhs, ldb), cudaMemcpyDeviceToDevice, solver_ctx_.stream_));
    return solve_impl(nrhs, ldb, std::move(d_x));
  }

  // ---- Accessors -----------------------------------------------------------

  ComputationInfo info() const { return solver_ctx_.info(); }
  Index rows() const { return n_; }
  Index cols() const { return n_; }
  cudaStream_t stream() const { return solver_ctx_.stream_; }

 private:
  mutable internal::GpuSolverContext solver_ctx_;
  internal::DeviceBuffer d_factor_;
  size_t factor_alloc_size_ = 0;
  int64_t n_ = 0;
  int64_t lda_ = 0;

  bool begin_compute(Index rows) {
    n_ = rows;
    solver_ctx_.info_ = InvalidInput;
    if (n_ == 0) {
      solver_ctx_.info_ = Success;
      solver_ctx_.info_synced_ = true;
      return false;
    }
    return true;
  }

  size_t factorBytes() const { return rhsBytes(n_, lda_); }

  static size_t rhsBytes(int64_t cols, int64_t ld) {
    return static_cast<size_t>(ld) * static_cast<size_t>(cols) * sizeof(Scalar);
  }

  void allocate_factor_storage() {
    size_t needed = factorBytes();
    if (needed > factor_alloc_size_) {
      d_factor_ = internal::DeviceBuffer(needed);
      factor_alloc_size_ = needed;
    }
  }

  // Solve in place on `d_x` (which already holds B), then re-wrap as a typed
  // DeviceMatrix carrying shape and a ready event. The release/adopt hop hands
  // ownership of the raw cudaMalloc pointer from the untyped DeviceBuffer to
  // the typed DeviceMatrix without copying.
  DeviceMatrix<Scalar> solve_impl(int64_t nrhs, int64_t ldb, internal::DeviceBuffer&& d_x) const {
    constexpr cudaDataType_t dtype = internal::cusolver_data_type<Scalar>::value;
    constexpr cublasFillMode_t uplo = internal::cusolver_fill_mode<UpLo_>::value;

    EIGEN_CUSOLVER_CHECK(cusolverDnXpotrs(solver_ctx_.cusolver_, solver_ctx_.params_.p, uplo, n_, nrhs, dtype,
                                          d_factor_.get(), lda_, dtype, d_x.get(), ldb, solver_ctx_.scratch_info()));

    DeviceMatrix<Scalar> result =
        DeviceMatrix<Scalar>::adopt(static_cast<Scalar*>(d_x.release()), n_, static_cast<Index>(nrhs));
    result.recordReady(solver_ctx_.stream_);
    return result;
  }

  void factorize() {
    constexpr cudaDataType_t dtype = internal::cusolver_data_type<Scalar>::value;
    constexpr cublasFillMode_t uplo = internal::cusolver_fill_mode<UpLo_>::value;

    solver_ctx_.mark_pending();

    size_t dev_ws_bytes = 0, host_ws_bytes = 0;
    EIGEN_CUSOLVER_CHECK(cusolverDnXpotrf_bufferSize(solver_ctx_.cusolver_, solver_ctx_.params_.p, uplo, n_, dtype,
                                                     d_factor_.get(), lda_, dtype, &dev_ws_bytes, &host_ws_bytes));

    solver_ctx_.ensure_scratch(dev_ws_bytes);
    solver_ctx_.h_workspace_.resize(host_ws_bytes);

    EIGEN_CUSOLVER_CHECK(cusolverDnXpotrf(solver_ctx_.cusolver_, solver_ctx_.params_.p, uplo, n_, dtype,
                                          d_factor_.get(), lda_, dtype, solver_ctx_.scratch_workspace(), dev_ws_bytes,
                                          host_ws_bytes > 0 ? solver_ctx_.h_workspace_.data() : nullptr, host_ws_bytes,
                                          solver_ctx_.scratch_info()));

    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(&solver_ctx_.info_word(), solver_ctx_.scratch_info(), sizeof(int),
                                             cudaMemcpyDeviceToHost, solver_ctx_.stream_));
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_LLT_H
