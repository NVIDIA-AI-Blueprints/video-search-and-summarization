// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU SVD decomposition using cuSOLVER (divide-and-conquer).
//
// Wraps cusolverDnXgesvd. Stores U, S, VT on device. Solve uses
// cuBLAS GEMM: X = VT^H * diag(D) * U^H * B.
//
// cuSOLVER returns VT (not V). We store and expose VT directly.
//
// Usage:
//   SVD<double> svd(A, ComputeThinU | ComputeThinV);
//   VectorXd S = svd.singularValues();
//   MatrixXd U = svd.matrixU();       // m×k or m×m
//   MatrixXd VT = svd.matrixVT();      // k×n or n×n (this is V^T)
//   MatrixXd X = svd.solve(B);        // pseudoinverse
//   MatrixXd X = svd.solve(B, k);     // truncated (top k triplets)
//   MatrixXd X = svd.solve(B, 0.1);   // Tikhonov regularized

#ifndef EIGEN_GPU_SVD_H
#define EIGEN_GPU_SVD_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./GpuSolverContext.h"

namespace Eigen {
namespace gpu {

template <typename Scalar_>
class SVD {
 public:
  using Scalar = Scalar_;
  using RealScalar = typename NumTraits<Scalar>::Real;
  using PlainMatrix = Eigen::Matrix<Scalar, Dynamic, Dynamic, ColMajor>;
  using PlainVector = Eigen::Matrix<Scalar, Dynamic, 1>;
  using RealVector = Eigen::Matrix<RealScalar, Dynamic, 1>;

  SVD() = default;

  template <typename InputType>
  explicit SVD(const EigenBase<InputType>& A, unsigned int options = ComputeThinU | ComputeThinV) {
    compute(A, options);
  }

  explicit SVD(const DeviceMatrix<Scalar>& d_A, unsigned int options = ComputeThinU | ComputeThinV) {
    compute(d_A, options);
  }

  ~SVD() = default;

  SVD(const SVD&) = delete;
  SVD& operator=(const SVD&) = delete;

  SVD(SVD&& o) noexcept
      : solver_ctx_(std::move(o.solver_ctx_)),
        d_A_(std::move(o.d_A_)),
        d_U_(std::move(o.d_U_)),
        d_S_(std::move(o.d_S_)),
        d_VT_(std::move(o.d_VT_)),
        options_(o.options_),
        m_(o.m_),
        n_(o.n_),
        lda_(o.lda_),
        transposed_(o.transposed_) {
    o.options_ = 0;
    o.m_ = 0;
    o.n_ = 0;
    o.lda_ = 0;
    o.transposed_ = false;
  }

  SVD& operator=(SVD&& o) noexcept {
    if (this != &o) {
      solver_ctx_ = std::move(o.solver_ctx_);
      d_A_ = std::move(o.d_A_);
      d_U_ = std::move(o.d_U_);
      d_S_ = std::move(o.d_S_);
      d_VT_ = std::move(o.d_VT_);
      options_ = o.options_;
      m_ = o.m_;
      n_ = o.n_;
      lda_ = o.lda_;
      transposed_ = o.transposed_;
      o.options_ = 0;
      o.m_ = 0;
      o.n_ = 0;
      o.lda_ = 0;
      o.transposed_ = false;
    }
    return *this;
  }

  // ---- Factorization -------------------------------------------------------

  template <typename InputType>
  SVD& compute(const EigenBase<InputType>& A, unsigned int options = ComputeThinU | ComputeThinV) {
    // Upload to device, then delegate. The wide-matrix transpose runs on the
    // GPU (via cublasXgeam) inside the device-input path; no host transpose.
    return compute(DeviceMatrix<Scalar>::fromHost(A.derived(), solver_ctx_.stream_), options);
  }

  SVD& compute(const DeviceMatrix<Scalar>& d_A, unsigned int options = ComputeThinU | ComputeThinV) {
    options_ = options;
    m_ = d_A.rows();
    n_ = d_A.cols();
    lda_ = 0;
    transposed_ = false;

    if (m_ == 0 || n_ == 0) {
      d_A_ = internal::DeviceBuffer();
      d_U_ = internal::DeviceBuffer();
      d_S_ = internal::DeviceBuffer();
      d_VT_ = internal::DeviceBuffer();
      solver_ctx_.info_ = Success;
      solver_ctx_.info_synced_ = true;
      return *this;
    }

    transposed_ = (m_ < n_);
    d_A.waitReady(solver_ctx_.stream_);

    if (transposed_) {
      // Transpose on device via cuBLAS geam: d_A_ = A^H.
      std::swap(m_, n_);
      lda_ = m_;
      const size_t mat_bytes = static_cast<size_t>(lda_) * static_cast<size_t>(n_) * sizeof(Scalar);
      d_A_ = internal::DeviceBuffer(mat_bytes);
      // geam: C(m×n) = alpha * op(A) + beta * op(B). beta=0, B=nullptr.
      Scalar alpha_one(1), beta_zero(0);
      EIGEN_CUBLAS_CHECK(internal::cublasXgeam(
          solver_ctx_.cublas_, CUBLAS_OP_C, CUBLAS_OP_N, internal::to_blas_int(m_), internal::to_blas_int(n_),
          &alpha_one, d_A.data(), internal::to_blas_int(d_A.rows()), &beta_zero, static_cast<const Scalar*>(nullptr),
          internal::to_blas_int(m_), static_cast<Scalar*>(d_A_.get()), internal::to_blas_int(m_)));
    } else {
      lda_ = static_cast<int64_t>(d_A.rows());
      const size_t mat_bytes = static_cast<size_t>(lda_) * static_cast<size_t>(n_) * sizeof(Scalar);
      d_A_ = internal::DeviceBuffer(mat_bytes);
      EIGEN_CUDA_RUNTIME_CHECK(
          cudaMemcpyAsync(d_A_.get(), d_A.data(), mat_bytes, cudaMemcpyDeviceToDevice, solver_ctx_.stream_));
    }

    factorize();
    return *this;
  }

  // ---- Accessors -----------------------------------------------------------

  ComputationInfo info() const { return solver_ctx_.info(); }

  Index rows() const { return transposed_ ? n_ : m_; }
  Index cols() const { return transposed_ ? m_ : n_; }

  /** Singular values (always available). Downloads from device on each call. */
  RealVector singularValues() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    const Index k = (std::min)(m_, n_);
    RealVector S(k);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpy(S.data(), d_S_.get(), static_cast<size_t>(k) * sizeof(RealScalar), cudaMemcpyDeviceToHost));
    return S;
  }

  /** Left singular vectors U. */
  PlainMatrix matrixU() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    eigen_assert((options_ & (ComputeThinU | ComputeFullU)) && "matrixU() requires ComputeThinU or ComputeFullU");
    const Index m_orig = transposed_ ? n_ : m_;
    const Index n_orig = transposed_ ? m_ : n_;
    const Index k = (std::min)(m_orig, n_orig);
    if (!transposed_) {
      const Index ucols = (options_ & ComputeFullU) ? m_ : k;
      PlainMatrix U(m_, ucols);
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpy(U.data(), d_U_.get(),
                                          static_cast<size_t>(m_) * static_cast<size_t>(ucols) * sizeof(Scalar),
                                          cudaMemcpyDeviceToHost));
      return U;
    } else {
      const Index vtrows = (options_ & ComputeFullU) ? m_orig : k;
      PlainMatrix VT_stored(vtrows, n_);
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpy(VT_stored.data(), d_VT_.get(),
                                          static_cast<size_t>(vtrows) * static_cast<size_t>(n_) * sizeof(Scalar),
                                          cudaMemcpyDeviceToHost));
      return VT_stored.adjoint();
    }
  }

  /** Right singular vectors V (matches host JacobiSVD/BDCSVD). */
  PlainMatrix matrixV() const { return matrixVT().adjoint(); }

  /** Right singular vectors transposed V^T. */
  PlainMatrix matrixVT() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    eigen_assert((options_ & (ComputeThinV | ComputeFullV)) && "matrixVT() requires ComputeThinV or ComputeFullV");
    const Index m_orig = transposed_ ? n_ : m_;
    const Index n_orig = transposed_ ? m_ : n_;
    const Index k = (std::min)(m_orig, n_orig);
    if (!transposed_) {
      const Index vtrows = (options_ & ComputeFullV) ? n_ : k;
      PlainMatrix VT(vtrows, n_);
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpy(VT.data(), d_VT_.get(),
                                          static_cast<size_t>(vtrows) * static_cast<size_t>(n_) * sizeof(Scalar),
                                          cudaMemcpyDeviceToHost));
      return VT;
    } else {
      const Index ucols = (options_ & ComputeFullV) ? n_orig : k;
      PlainMatrix U_stored(m_, ucols);
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpy(U_stored.data(), d_U_.get(),
                                          static_cast<size_t>(m_) * static_cast<size_t>(ucols) * sizeof(Scalar),
                                          cudaMemcpyDeviceToHost));
      return U_stored.adjoint();
    }
  }

  // ---- Device-side accessors (zero-copy views; chain into cuBLAS without D2D) ---------
  //
  // These return non-owning DeviceMatrix views over the SVD's internal device storage.
  // The view borrows the pointer: destruction does not free; the SVD object must outlive
  // any view derived from it. For the common case (m >= n) all three accessors are pure
  // metadata: zero kernel launches, zero allocations.
  //
  // For wide matrices (m < n, internally factored as A^H), original U and V^T are the
  // adjoints of the stored buffers, so d_matrixU() / d_matrixVT() build them via a
  // cublasXgeam into an owning temporary. d_singularValues() remains zero-copy.

  /** Singular values as a k × 1 view on this solver's stream. */
  DeviceMatrix<RealScalar> d_singularValues() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    const Index k = (std::min)(m_, n_);
    auto v = DeviceMatrix<RealScalar>::view(static_cast<RealScalar*>(d_S_.get()), k, 1);
    v.recordReady(solver_ctx_.stream_);
    return v;
  }

  /** Left singular vectors U as a DeviceMatrix on this solver's stream.
   * For m >= n: zero-copy view. For m < n: owning (one cublasXgeam adjoint pass). */
  DeviceMatrix<Scalar> d_matrixU() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    eigen_assert((options_ & (ComputeThinU | ComputeFullU)) && "d_matrixU() requires ComputeThinU or ComputeFullU");
    const Index m_orig = transposed_ ? n_ : m_;
    const Index n_orig = transposed_ ? m_ : n_;
    const Index k = (std::min)(m_orig, n_orig);
    if (!transposed_) {
      const Index ucols = (options_ & ComputeFullU) ? m_ : k;
      auto v = DeviceMatrix<Scalar>::view(static_cast<Scalar*>(d_U_.get()), m_, ucols);
      v.recordReady(solver_ctx_.stream_);
      return v;
    }
    // transposed: U_orig = VT_stored^H -> conjugate-transpose via cublasXgeam.
    const Index vtrows_stored = (options_ & ComputeFullU) ? n_ : k;
    DeviceMatrix<Scalar> result(n_, vtrows_stored);
    if (n_ > 0 && vtrows_stored > 0) {
      Scalar alpha_one(1), beta_zero(0);
      EIGEN_CUBLAS_CHECK(internal::cublasXgeam(
          solver_ctx_.cublas_, CUBLAS_OP_C, CUBLAS_OP_N, internal::to_blas_int(n_),
          internal::to_blas_int(vtrows_stored), &alpha_one, static_cast<const Scalar*>(d_VT_.get()),
          internal::to_blas_int(vtrows_stored), &beta_zero, static_cast<const Scalar*>(nullptr),
          internal::to_blas_int(n_), result.data(), internal::to_blas_int(n_)));
      result.recordReady(solver_ctx_.stream_);
    }
    return result;
  }

  /** Right singular vectors transposed V^T as a DeviceMatrix on this solver's stream.
   * For m >= n: zero-copy view. For m < n: owning (one cublasXgeam adjoint pass). */
  DeviceMatrix<Scalar> d_matrixVT() const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success);
    eigen_assert((options_ & (ComputeThinV | ComputeFullV)) && "d_matrixVT() requires ComputeThinV or ComputeFullV");
    const Index m_orig = transposed_ ? n_ : m_;
    const Index n_orig = transposed_ ? m_ : n_;
    const Index k = (std::min)(m_orig, n_orig);
    if (!transposed_) {
      const Index vtrows = (options_ & ComputeFullV) ? n_ : k;
      auto v = DeviceMatrix<Scalar>::view(static_cast<Scalar*>(d_VT_.get()), vtrows, n_);
      v.recordReady(solver_ctx_.stream_);
      return v;
    }
    // transposed: VT_orig = U_stored^H.
    const Index ucols = (options_ & ComputeFullV) ? n_orig : k;
    DeviceMatrix<Scalar> result(ucols, m_);
    if (ucols > 0 && m_ > 0) {
      Scalar alpha_one(1), beta_zero(0);
      EIGEN_CUBLAS_CHECK(
          internal::cublasXgeam(solver_ctx_.cublas_, CUBLAS_OP_C, CUBLAS_OP_N, internal::to_blas_int(ucols),
                                internal::to_blas_int(m_), &alpha_one, static_cast<const Scalar*>(d_U_.get()),
                                internal::to_blas_int(m_), &beta_zero, static_cast<const Scalar*>(nullptr),
                                internal::to_blas_int(ucols), result.data(), internal::to_blas_int(ucols)));
      result.recordReady(solver_ctx_.stream_);
    }
    return result;
  }

  /** Number of singular values above threshold. */
  Index rank(RealScalar threshold = RealScalar(-1)) const {
    RealVector S = singularValues();
    if (S.size() == 0) return 0;
    if (threshold < 0) {
      threshold = (std::max)(m_, n_) * S(0) * NumTraits<RealScalar>::epsilon();
    }
    return (S.array() > threshold).count();
  }

  // ---- Solve ---------------------------------------------------------------

  /** Pseudoinverse solve: X = V * diag(1/S) * U^H * B. */
  template <typename Rhs>
  PlainMatrix solve(const MatrixBase<Rhs>& B) const {
    return solve_impl(B, (std::min)(m_, n_), RealScalar(0));
  }

  /** Truncated solve: use only top trunc singular triplets. */
  template <typename Rhs>
  PlainMatrix solve(const MatrixBase<Rhs>& B, Index trunc) const {
    eigen_assert(trunc > 0 && trunc <= (std::min)(m_, n_));
    return solve_impl(B, trunc, RealScalar(0));
  }

  /** Tikhonov-regularized solve: D_ii = S_i / (S_i^2 + lambda^2). */
  template <typename Rhs>
  PlainMatrix solve(const MatrixBase<Rhs>& B, RealScalar lambda) const {
    eigen_assert(lambda > 0);
    return solve_impl(B, (std::min)(m_, n_), lambda);
  }

  cudaStream_t stream() const { return solver_ctx_.stream_; }

 private:
  mutable internal::GpuSolverContext solver_ctx_;
  internal::DeviceBuffer d_A_;
  internal::DeviceBuffer d_U_;
  internal::DeviceBuffer d_S_;
  internal::DeviceBuffer d_VT_;
  unsigned int options_ = 0;
  int64_t m_ = 0;
  int64_t n_ = 0;
  int64_t lda_ = 0;
  bool transposed_ = false;

  // Swap U↔V flags for the transposed case.
  static unsigned int swap_uv_options(unsigned int opts) {
    unsigned int result = 0;
    if (opts & ComputeThinU) result |= ComputeThinV;
    if (opts & ComputeFullU) result |= ComputeFullV;
    if (opts & ComputeThinV) result |= ComputeThinU;
    if (opts & ComputeFullV) result |= ComputeFullU;
    return result;
  }

  static signed char jobu(unsigned int opts) {
    if (opts & ComputeFullU) return 'A';
    if (opts & ComputeThinU) return 'S';
    return 'N';
  }

  static signed char jobvt(unsigned int opts) {
    if (opts & ComputeFullV) return 'A';
    if (opts & ComputeThinV) return 'S';
    return 'N';
  }

  void factorize() {
    constexpr cudaDataType_t dtype = internal::cusolver_data_type<Scalar>::value;
    constexpr cudaDataType_t rtype = internal::cuda_data_type<RealScalar>::value;
    const Index k = (std::min)(m_, n_);

    solver_ctx_.mark_pending();

    d_S_ = internal::DeviceBuffer(static_cast<size_t>(k) * sizeof(RealScalar));

    const unsigned int int_opts = transposed_ ? swap_uv_options(options_) : options_;

    const Index ucols = (int_opts & ComputeFullU) ? m_ : ((int_opts & ComputeThinU) ? k : 0);
    const Index vtrows = (int_opts & ComputeFullV) ? n_ : ((int_opts & ComputeThinV) ? k : 0);
    const int64_t ldu = m_;
    const int64_t ldvt = vtrows > 0 ? vtrows : 1;

    d_U_ = internal::DeviceBuffer();
    d_VT_ = internal::DeviceBuffer();
    if (ucols > 0) d_U_ = internal::DeviceBuffer(static_cast<size_t>(m_) * static_cast<size_t>(ucols) * sizeof(Scalar));
    if (vtrows > 0)
      d_VT_ = internal::DeviceBuffer(static_cast<size_t>(vtrows) * static_cast<size_t>(n_) * sizeof(Scalar));

    eigen_assert(m_ >= n_ && "Internal error: m_ < n_ should have been handled by transpose in compute()");
    size_t dev_ws = 0, host_ws = 0;
    EIGEN_CUSOLVER_CHECK(cusolverDnXgesvd_bufferSize(
        solver_ctx_.cusolver_, solver_ctx_.params_.p, jobu(int_opts), jobvt(int_opts), m_, n_, dtype, d_A_.get(), lda_,
        rtype, d_S_.get(), dtype, ucols > 0 ? d_U_.get() : nullptr, ldu, dtype, vtrows > 0 ? d_VT_.get() : nullptr,
        ldvt, dtype, &dev_ws, &host_ws));

    solver_ctx_.ensure_scratch(dev_ws);
    solver_ctx_.h_workspace_.resize(host_ws);

    EIGEN_CUSOLVER_CHECK(
        cusolverDnXgesvd(solver_ctx_.cusolver_, solver_ctx_.params_.p, jobu(int_opts), jobvt(int_opts), m_, n_, dtype,
                         d_A_.get(), lda_, rtype, d_S_.get(), dtype, ucols > 0 ? d_U_.get() : nullptr, ldu, dtype,
                         vtrows > 0 ? d_VT_.get() : nullptr, ldvt, dtype, solver_ctx_.scratch_workspace(), dev_ws,
                         host_ws > 0 ? solver_ctx_.h_workspace_.data() : nullptr, host_ws, solver_ctx_.scratch_info()));

    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(&solver_ctx_.info_word(), solver_ctx_.scratch_info(), sizeof(int),
                                             cudaMemcpyDeviceToHost, solver_ctx_.stream_));
  }

  template <typename Rhs>
  PlainMatrix solve_impl(const MatrixBase<Rhs>& B, Index trunc, RealScalar lambda) const {
    solver_ctx_.sync_info();
    eigen_assert(solver_ctx_.info_ == Success && "SVD::solve called on a failed or uninitialized decomposition");
    eigen_assert((options_ & (ComputeThinU | ComputeFullU)) && "solve requires U");
    eigen_assert((options_ & (ComputeThinV | ComputeFullV)) && "solve requires V");

    const Index m_orig = transposed_ ? n_ : m_;
    const Index n_orig = transposed_ ? m_ : n_;
    eigen_assert(B.rows() == m_orig);

    const Index k = (std::min)(m_, n_);
    const Index kk = (std::min)(trunc, k);
    const Index nrhs = B.cols();

    // Empty problem: no rank, no RHS, or zero domain -> result is the zero matrix.
    // Returning early avoids reading S(0) below when k == 0 and prevents zero-extent
    // GEMM/dgmm calls.
    if (kk == 0 || nrhs == 0 || n_orig == 0) {
      return PlainMatrix::Zero(n_orig, nrhs);
    }

    // Enqueue both transfers on solver_ctx_.stream_ in one batch and sync once. Issuing the
    // B upload before reading S means B's H2D is already in flight while we wait for
    // gesvd-then-S-D2H, instead of two back-to-back blocking syncs.
    const PlainMatrix rhs(B);
    internal::DeviceBuffer d_B(static_cast<size_t>(m_orig) * static_cast<size_t>(nrhs) * sizeof(Scalar));
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_B.get(), rhs.data(),
                                             static_cast<size_t>(m_orig) * static_cast<size_t>(nrhs) * sizeof(Scalar),
                                             cudaMemcpyHostToDevice, solver_ctx_.stream_));
    RealVector S(k);
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(S.data(), d_S_.get(), static_cast<size_t>(k) * sizeof(RealScalar),
                                             cudaMemcpyDeviceToHost, solver_ctx_.stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(solver_ctx_.stream_));

    // Typed pointers for device buffers.
    auto* U_dev = static_cast<const Scalar*>(d_U_.get());
    auto* VT_dev = static_cast<const Scalar*>(d_VT_.get());
    auto* B_dev = static_cast<const Scalar*>(d_B.get());

    Scalar scalars[2] = {Scalar(1), Scalar(0)};

    // Step 1: tmp = U_orig^H * B  (kk × nrhs).
    internal::DeviceBuffer d_tmp(static_cast<size_t>(kk) * static_cast<size_t>(nrhs) * sizeof(Scalar));
    auto* tmp_dev = static_cast<Scalar*>(d_tmp.get());
    if (!transposed_) {
      EIGEN_CUBLAS_CHECK(internal::cublasXgemm(solver_ctx_.cublas_, CUBLAS_OP_C, CUBLAS_OP_N, internal::to_blas_int(kk),
                                               internal::to_blas_int(nrhs), internal::to_blas_int(m_), &scalars[0],
                                               U_dev, internal::to_blas_int(m_), B_dev, internal::to_blas_int(m_orig),
                                               &scalars[1], tmp_dev, internal::to_blas_int(kk)));
    } else {
      const Index vtrows_stored = (swap_uv_options(options_) & ComputeFullV) ? n_ : k;
      EIGEN_CUBLAS_CHECK(internal::cublasXgemm(
          solver_ctx_.cublas_, CUBLAS_OP_N, CUBLAS_OP_N, internal::to_blas_int(kk), internal::to_blas_int(nrhs),
          internal::to_blas_int(m_orig), &scalars[0], VT_dev, internal::to_blas_int(vtrows_stored), B_dev,
          internal::to_blas_int(m_orig), &scalars[1], tmp_dev, internal::to_blas_int(kk)));
    }

    // Step 2: Apply diag(D) to tmp on device via cublasXdgmm.
    // D is built on host from the (small, k-entry) singular-values vector S
    // and uploaded to a small device buffer; tmp itself stays on device.
    //
    // For lambda == 0 we mirror Eigen's SVDBase::_solve_impl: drop singular
    // values below S(0) * k * eps (numerical-rank truncation), so this
    // pseudoinverse solve agrees with CPU BDCSVD::solve on near-singular A.
    // dgmm wants the diagonal in the matrix scalar type — for complex Scalar
    // the diagonal is still real, so we build the real values then cast.
    const RealScalar drop_threshold = S(0) * RealScalar(k) * NumTraits<RealScalar>::epsilon();
    auto S_head = S.head(kk).array();
    PlainVector D(kk);
    if (lambda == RealScalar(0)) {
      D = (S_head > drop_threshold).select(S_head.inverse(), RealScalar(0)).matrix().template cast<Scalar>();
    } else {
      D = (S_head / (S_head.square() + lambda * lambda)).matrix().template cast<Scalar>();
    }

    internal::DeviceBuffer d_D(static_cast<size_t>(kk) * sizeof(Scalar));
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_D.get(), D.data(), static_cast<size_t>(kk) * sizeof(Scalar),
                                             cudaMemcpyHostToDevice, solver_ctx_.stream_));

    EIGEN_CUBLAS_CHECK(internal::cublasXdgmm(
        solver_ctx_.cublas_, CUBLAS_SIDE_LEFT, internal::to_blas_int(kk), internal::to_blas_int(nrhs), tmp_dev,
        internal::to_blas_int(kk), static_cast<const Scalar*>(d_D.get()), 1, tmp_dev, internal::to_blas_int(kk)));

    // Step 3: X = V_orig * tmp  (n_orig × nrhs).
    PlainMatrix X(n_orig, nrhs);
    internal::DeviceBuffer d_X(static_cast<size_t>(n_orig) * static_cast<size_t>(nrhs) * sizeof(Scalar));
    auto* X_dev = static_cast<Scalar*>(d_X.get());

    if (!transposed_) {
      const Index vtrows = (options_ & ComputeFullV) ? n_ : k;
      EIGEN_CUBLAS_CHECK(internal::cublasXgemm(
          solver_ctx_.cublas_, CUBLAS_OP_C, CUBLAS_OP_N, internal::to_blas_int(n_orig), internal::to_blas_int(nrhs),
          internal::to_blas_int(kk), &scalars[0], VT_dev, internal::to_blas_int(vtrows), tmp_dev,
          internal::to_blas_int(kk), &scalars[1], X_dev, internal::to_blas_int(n_orig)));
    } else {
      EIGEN_CUBLAS_CHECK(internal::cublasXgemm(
          solver_ctx_.cublas_, CUBLAS_OP_N, CUBLAS_OP_N, internal::to_blas_int(n_orig), internal::to_blas_int(nrhs),
          internal::to_blas_int(kk), &scalars[0], U_dev, internal::to_blas_int(m_), tmp_dev, internal::to_blas_int(kk),
          &scalars[1], X_dev, internal::to_blas_int(n_orig)));
    }

    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(X.data(), d_X.get(),
                                             static_cast<size_t>(n_orig) * static_cast<size_t>(nrhs) * sizeof(Scalar),
                                             cudaMemcpyDeviceToHost, solver_ctx_.stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(solver_ctx_.stream_));

    return X;
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_SVD_H
