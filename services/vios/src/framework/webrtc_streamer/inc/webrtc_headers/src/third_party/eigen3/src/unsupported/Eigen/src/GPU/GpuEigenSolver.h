// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU self-adjoint eigenvalue decomposition using cuSOLVER.
//
// Wraps cusolverDnXsyevd (symmetric/Hermitian divide-and-conquer).
// Stores eigenvalues and eigenvectors on device.
//
// Usage:
//   SelfAdjointEigenSolver<double> es(A);
//   VectorXd eigenvals = es.eigenvalues();
//   MatrixXd eigenvecs = es.eigenvectors();

#ifndef EIGEN_GPU_EIGENSOLVER_H
#define EIGEN_GPU_EIGENSOLVER_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSolverContext.h"

namespace Eigen {
namespace gpu {

template <typename Scalar_>
class SelfAdjointEigenSolver {
 public:
  using Scalar = Scalar_;
  using RealScalar = typename NumTraits<Scalar>::Real;
  using PlainMatrix = Eigen::Matrix<Scalar, Dynamic, Dynamic, ColMajor>;
  using RealVector = Eigen::Matrix<RealScalar, Dynamic, 1>;

  SelfAdjointEigenSolver() = default;

  /** \param options  Eigen::ComputeEigenvectors (default) or Eigen::EigenvaluesOnly. */
  template <typename InputType>
  explicit SelfAdjointEigenSolver(const EigenBase<InputType>& A, int options = ComputeEigenvectors) {
    compute(A, options);
  }

  explicit SelfAdjointEigenSolver(const DeviceMatrix<Scalar>& d_A, int options = ComputeEigenvectors) {
    compute(d_A, options);
  }

  ~SelfAdjointEigenSolver() = default;

  SelfAdjointEigenSolver(const SelfAdjointEigenSolver&) = delete;
  SelfAdjointEigenSolver& operator=(const SelfAdjointEigenSolver&) = delete;

  SelfAdjointEigenSolver(SelfAdjointEigenSolver&& o) noexcept
      : solver_ctx_(std::move(o.solver_ctx_)),
        d_A_(std::move(o.d_A_)),
        d_W_(std::move(o.d_W_)),
        compute_eigenvectors_(o.compute_eigenvectors_),
        n_(o.n_),
        lda_(o.lda_) {
    o.compute_eigenvectors_ = true;
    o.n_ = 0;
    o.lda_ = 0;
  }

  SelfAdjointEigenSolver& operator=(SelfAdjointEigenSolver&& o) noexcept {
    if (this != &o) {
      solver_ctx_ = std::move(o.solver_ctx_);
      d_A_ = std::move(o.d_A_);
      d_W_ = std::move(o.d_W_);
      compute_eigenvectors_ = o.compute_eigenvectors_;
      n_ = o.n_;
      lda_ = o.lda_;
      o.compute_eigenvectors_ = true;
      o.n_ = 0;
      o.lda_ = 0;
    }
    return *this;
  }

  // ---- Factorization -------------------------------------------------------

  template <typename InputType>
  SelfAdjointEigenSolver& compute(const EigenBase<InputType>& A, int options = ComputeEigenvectors) {
    return compute(DeviceMatrix<Scalar>::fromHost(A.derived(), solver_ctx_.stream_), options);
  }

  SelfAdjointEigenSolver& compute(const DeviceMatrix<Scalar>& d_A, int options = ComputeEigenvectors) {
    eigen_assert(d_A.rows() == d_A.cols() && "SelfAdjointEigenSolver requires a square matrix");
    eigen_assert((options == ComputeEigenvectors || options == EigenvaluesOnly) &&
                 "options must be ComputeEigenvectors or EigenvaluesOnly");
    compute_eigenvectors_ = (options == ComputeEigenvectors);
    n_ = d_A.rows();

    if (n_ == 0) {
      solver_ctx_.info_ = Success;
      solver_ctx_.info_synced_ = true;
      return *this;
    }

    d_A.waitReady(solver_ctx_.stream_);
    lda_ = n_;
    const size_t mat_bytes = static_cast<size_t>(lda_) * static_cast<size_t>(n_) * sizeof(Scalar);

    d_A_ = internal::DeviceBuffer(mat_bytes);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_A_.get(), d_A.data(), mat_bytes, cudaMemcpyDeviceToDevice, solver_ctx_.stream_));

    factorize();
    return *this;
  }

  // ---- Accessors -----------------------------------------------------------

  ComputationInfo info() const { return solver_ctx_.info(); }

  Index cols() const { return n_; }
  Index rows() const { return n_; }

  /** Eigenvalues in ascending order. Downloads from device. */
  RealVector eigenvalues() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    RealVector W(n_);
    if (n_ > 0) {
      EIGEN_CUDA_RUNTIME_CHECK(
          cudaMemcpy(W.data(), d_W_.get(), static_cast<size_t>(n_) * sizeof(RealScalar), cudaMemcpyDeviceToHost));
    }
    return W;
  }

  /** Eigenvectors (columns). Downloads from device.
   * Requires ComputeEigenvectors mode. */
  PlainMatrix eigenvectors() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    eigen_assert(compute_eigenvectors_ && "eigenvectors() requires ComputeEigenvectors option");
    PlainMatrix V(n_, n_);
    if (n_ > 0) {
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpy(V.data(), d_A_.get(),
                                          static_cast<size_t>(lda_) * static_cast<size_t>(n_) * sizeof(Scalar),
                                          cudaMemcpyDeviceToHost));
    }
    return V;
  }

  // ---- Device-side accessors (zero-copy views; chain into cuBLAS without D2D) ---------
  //
  // These return non-owning DeviceMatrix views over this solver's internal storage. The
  // view borrows the pointer: destruction does not free; this solver must outlive any
  // view derived from it. Both accessors are pure metadata — zero kernel launches.

  /** Eigenvalues as an n × 1 view on this solver's stream. */
  DeviceMatrix<RealScalar> d_eigenvalues() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    auto v = DeviceMatrix<RealScalar>::view(static_cast<RealScalar*>(d_W_.get()), n_, 1);
    v.recordReady(solver_ctx_.stream_);
    return v;
  }

  /** Eigenvectors (columns) as an n × n view on this solver's stream. */
  DeviceMatrix<Scalar> d_eigenvectors() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    eigen_assert(compute_eigenvectors_ && "d_eigenvectors() requires ComputeEigenvectors option");
    auto v = DeviceMatrix<Scalar>::view(static_cast<Scalar*>(d_A_.get()), n_, n_);
    v.recordReady(solver_ctx_.stream_);
    return v;
  }

  cudaStream_t stream() const { return solver_ctx_.stream_; }

 private:
  mutable internal::GpuSolverContext solver_ctx_;
  internal::DeviceBuffer d_A_;  // overwritten with eigenvectors by syevd
  internal::DeviceBuffer d_W_;  // eigenvalues (RealScalar, length n)
  bool compute_eigenvectors_ = true;
  int64_t n_ = 0;
  int64_t lda_ = 0;

  void factorize() {
    constexpr cudaDataType_t dtype = internal::cusolver_data_type<Scalar>::value;
    constexpr cudaDataType_t rtype = internal::cuda_data_type<RealScalar>::value;

    solver_ctx_.mark_pending();

    d_W_ = internal::DeviceBuffer(static_cast<size_t>(n_) * sizeof(RealScalar));

    const cusolverEigMode_t jobz = compute_eigenvectors_ ? CUSOLVER_EIG_MODE_VECTOR : CUSOLVER_EIG_MODE_NOVECTOR;

    // Use lower triangle (standard convention).
    constexpr cublasFillMode_t uplo = CUBLAS_FILL_MODE_LOWER;

    size_t dev_ws = 0, host_ws = 0;
    EIGEN_CUSOLVER_CHECK(cusolverDnXsyevd_bufferSize(solver_ctx_.cusolver_, solver_ctx_.params_.p, jobz, uplo, n_,
                                                     dtype, d_A_.get(), lda_, rtype, d_W_.get(), dtype, &dev_ws,
                                                     &host_ws));

    solver_ctx_.ensure_scratch(dev_ws);
    solver_ctx_.h_workspace_.resize(host_ws);

    EIGEN_CUSOLVER_CHECK(cusolverDnXsyevd(solver_ctx_.cusolver_, solver_ctx_.params_.p, jobz, uplo, n_, dtype,
                                          d_A_.get(), lda_, rtype, d_W_.get(), dtype, solver_ctx_.scratch_workspace(),
                                          dev_ws, host_ws > 0 ? solver_ctx_.h_workspace_.data() : nullptr, host_ws,
                                          solver_ctx_.scratch_info()));

    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(&solver_ctx_.info_word(), solver_ctx_.scratch_info(), sizeof(int),
                                             cudaMemcpyDeviceToHost, solver_ctx_.stream_));
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_EIGENSOLVER_H
