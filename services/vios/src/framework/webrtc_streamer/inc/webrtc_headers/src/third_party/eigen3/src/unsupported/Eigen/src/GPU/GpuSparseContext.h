// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU sparse matrix-vector multiply (SpMV) and sparse matrix-dense matrix
// multiply (SpMM) via cuSPARSE.
//
// SparseContext manages a cuSPARSE handle and device buffers. It accepts
// Eigen SparseMatrix<Scalar, ColMajor> (CSC) and performs SpMV/SpMM on the
// GPU. RowMajor input is implicitly converted to ColMajor.
//
// Thread safety: not thread-safe. Concurrent multiply* calls on a single
// SparseContext race on the cuSPARSE handle, the bound stream, and the
// cached device buffers. Use one SparseContext per thread.
//
// Usage:
//   SparseContext<double> ctx;
//   VectorXd y = ctx.multiply(A, x);                  // y = A * x
//   ctx.multiply(A, x, y, 2.0, 1.0);                  // y = 2*A*x + y
//   ctx.multiply(A, x, y, 1.0, 0.0, gpu::GpuOp::ConjTrans);  // y = A^H * x
//   VectorXd z = ctx.multiplyT(A, x);                 // z = A^T * x
//   VectorXcd w = ctx.multiplyAdjoint(A, x);          // w = A^H * x (complex)
//   MatrixXd Y = ctx.multiplyMat(A, X);               // Y = A * X (multiple RHS)

#ifndef EIGEN_GPU_SPARSE_CONTEXT_H
#define EIGEN_GPU_SPARSE_CONTEXT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./CuSparseSupport.h"

namespace Eigen {
namespace gpu {

template <typename Scalar_>
class SparseContext {
 public:
  using Scalar = Scalar_;
  using RealScalar = typename NumTraits<Scalar>::Real;
  using StorageIndex = int;
  using SpMat = SparseMatrix<Scalar, ColMajor, StorageIndex>;
  using DenseVector = Matrix<Scalar, Dynamic, 1>;
  using DenseMatrix = Matrix<Scalar, Dynamic, Dynamic, ColMajor>;

  SparseContext() {
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamCreate(&stream_));
    EIGEN_CUSPARSE_CHECK(cusparseCreate(&handle_));
    EIGEN_CUSPARSE_CHECK(cusparseSetStream(handle_, stream_));
  }

  ~SparseContext() {
    destroy_descriptors_unchecked();
    if (handle_) (void)cusparseDestroy(handle_);
    if (stream_) (void)cudaStreamDestroy(stream_);
  }

  SparseContext(const SparseContext&) = delete;
  SparseContext& operator=(const SparseContext&) = delete;

  // ---- SpMV: y = A * x -----------------------------------------------------

  /** Compute y = A * x. Returns y as a new dense vector. */
  template <typename InputType, typename Rhs>
  DenseVector multiply(const SparseMatrixBase<InputType>& A, const MatrixBase<Rhs>& x) {
    const InputType& input = A.derived();
    check_storage_index_bounds(input.rows(), input.cols(), input.nonZeros());
    const SpMat mat(input);
    DenseVector y(mat.rows());
    y.setZero();
    multiply_impl(mat, x.derived(), y, Scalar(1), Scalar(0), CUSPARSE_OPERATION_NON_TRANSPOSE);
    return y;
  }

  /** Compute y = alpha * op(A) * x + beta * y (in-place). */
  template <typename InputType, typename Rhs, typename Dest>
  void multiply(const SparseMatrixBase<InputType>& A, const MatrixBase<Rhs>& x, MatrixBase<Dest>& y,
                Scalar alpha = Scalar(1), Scalar beta = Scalar(0), GpuOp op = GpuOp::NoTrans) {
    const InputType& input = A.derived();
    check_storage_index_bounds(input.rows(), input.cols(), input.nonZeros());
    const SpMat mat(input);
    multiply_impl(mat, x.derived(), y.derived(), alpha, beta, internal::to_cusparse_op_for_scalar<Scalar>(op));
  }

  // ---- SpMV transpose: y = A^T * x -----------------------------------------

  /** Compute y = A^T * x. Returns y as a new dense vector. */
  template <typename InputType, typename Rhs>
  DenseVector multiplyT(const SparseMatrixBase<InputType>& A, const MatrixBase<Rhs>& x) {
    const InputType& input = A.derived();
    check_storage_index_bounds(input.rows(), input.cols(), input.nonZeros());
    const SpMat mat(input);
    DenseVector y(mat.cols());
    y.setZero();
    multiply_impl(mat, x.derived(), y, Scalar(1), Scalar(0), CUSPARSE_OPERATION_TRANSPOSE);
    return y;
  }

  // ---- SpMV adjoint: y = A^H * x -------------------------------------------

  /** Compute y = A^H * x (conjugate transpose). For real Scalar this is equivalent to multiplyT. */
  template <typename InputType, typename Rhs>
  DenseVector multiplyAdjoint(const SparseMatrixBase<InputType>& A, const MatrixBase<Rhs>& x) {
    const InputType& input = A.derived();
    check_storage_index_bounds(input.rows(), input.cols(), input.nonZeros());
    const SpMat mat(input);
    DenseVector y(mat.cols());
    y.setZero();
    multiply_impl(mat, x.derived(), y, Scalar(1), Scalar(0),
                  internal::to_cusparse_op_for_scalar<Scalar>(GpuOp::ConjTrans));
    return y;
  }

  // ---- SpMM: Y = op(A) * X (multiple RHS) ----------------------------------

  /** Compute Y = op(A) * X where X is a dense matrix (multiple RHS). Returns Y. */
  template <typename InputType, typename Rhs>
  DenseMatrix multiplyMat(const SparseMatrixBase<InputType>& A, const MatrixBase<Rhs>& X, GpuOp op = GpuOp::NoTrans) {
    const InputType& input = A.derived();
    check_storage_index_bounds(input.rows(), input.cols(), input.nonZeros());
    const SpMat mat(input);
    const DenseMatrix rhs(X.derived());

    const cusparseOperation_t cu_op = internal::to_cusparse_op_for_scalar<Scalar>(op);
    const Index m = (op == GpuOp::NoTrans) ? mat.rows() : mat.cols();
    const Index k = (op == GpuOp::NoTrans) ? mat.cols() : mat.rows();
    eigen_assert(k == rhs.rows());

    const Index n = rhs.cols();
    if (m == 0 || n == 0 || mat.nonZeros() == 0) return DenseMatrix::Zero(m, n);

    DenseMatrix Y = DenseMatrix::Zero(m, n);
    spmm_impl(mat, rhs, Y, Scalar(1), Scalar(0), cu_op);
    return Y;
  }

  // ---- Accessors ------------------------------------------------------------

  cudaStream_t stream() const { return stream_; }

 private:
  cudaStream_t stream_ = nullptr;
  cusparseHandle_t handle_ = nullptr;

  // Cached device buffers (grow-only).
  internal::DeviceBuffer d_outerPtr_;
  internal::DeviceBuffer d_innerIdx_;
  internal::DeviceBuffer d_values_;
  internal::DeviceBuffer d_x_;
  internal::DeviceBuffer d_y_;
  internal::DeviceBuffer d_workspace_;
  size_t d_outerPtr_size_ = 0;
  size_t d_innerIdx_size_ = 0;
  size_t d_values_size_ = 0;
  size_t d_x_size_ = 0;
  size_t d_y_size_ = 0;
  size_t d_workspace_size_ = 0;

  // Cached cuSPARSE descriptors.
  cusparseSpMatDescr_t spmat_desc_ = nullptr;
  Index cached_rows_ = -1;
  Index cached_cols_ = -1;
  Index cached_nnz_ = -1;

  // ---- SpMV implementation --------------------------------------------------

  template <typename RhsDerived, typename DestDerived>
  void multiply_impl(const SpMat& A, const RhsDerived& x, DestDerived& y, Scalar alpha, Scalar beta,
                     cusparseOperation_t op) {
    eigen_assert(A.isCompressed());

    const Index m = A.rows();
    const Index n = A.cols();
    const Index nnz = A.nonZeros();
    const Index x_size = (op == CUSPARSE_OPERATION_NON_TRANSPOSE) ? n : m;
    const Index y_size = (op == CUSPARSE_OPERATION_NON_TRANSPOSE) ? m : n;

    eigen_assert(x.size() == x_size);
    eigen_assert(y.size() == y_size);

    if (m == 0 || n == 0 || nnz == 0) {
      if (beta == Scalar(0))
        y.setZero();
      else
        y *= beta;
      return;
    }

    // Upload sparse matrix to device.
    upload_sparse(A);

    // Upload x to device.
    ensure_buffer(d_x_, d_x_size_, static_cast<size_t>(x_size) * sizeof(Scalar));
    const DenseVector x_tmp(x);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_x_.get(), x_tmp.data(), x_size * sizeof(Scalar), cudaMemcpyHostToDevice, stream_));

    // Upload y to device (for beta != 0).
    ensure_buffer(d_y_, d_y_size_, static_cast<size_t>(y_size) * sizeof(Scalar));
    if (beta != Scalar(0)) {
      const DenseVector y_tmp(y);
      EIGEN_CUDA_RUNTIME_CHECK(
          cudaMemcpyAsync(d_y_.get(), y_tmp.data(), y_size * sizeof(Scalar), cudaMemcpyHostToDevice, stream_));
    }

    // Create dense vector descriptors.
    constexpr cudaDataType_t dtype = internal::cuda_data_type<Scalar>::value;
    cusparseDnVecDescr_t x_desc = nullptr, y_desc = nullptr;
    EIGEN_CUSPARSE_CHECK(cusparseCreateDnVec(&x_desc, x_size, d_x_.get(), dtype));
    EIGEN_CUSPARSE_CHECK(cusparseCreateDnVec(&y_desc, y_size, d_y_.get(), dtype));

    // Query workspace size.
    size_t ws_size = 0;
    EIGEN_CUSPARSE_CHECK(cusparseSpMV_bufferSize(handle_, op, &alpha, spmat_desc_, x_desc, &beta, y_desc, dtype,
                                                 CUSPARSE_SPMV_ALG_DEFAULT, &ws_size));
    ensure_buffer(d_workspace_, d_workspace_size_, ws_size);

    // Execute SpMV.
    EIGEN_CUSPARSE_CHECK(cusparseSpMV(handle_, op, &alpha, spmat_desc_, x_desc, &beta, y_desc, dtype,
                                      CUSPARSE_SPMV_ALG_DEFAULT, d_workspace_.get()));

    // Download result.
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(y.data(), d_y_.get(), y_size * sizeof(Scalar), cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));

    EIGEN_CUSPARSE_CHECK(cusparseDestroyDnVec(x_desc));
    EIGEN_CUSPARSE_CHECK(cusparseDestroyDnVec(y_desc));
  }

  // ---- SpMM implementation --------------------------------------------------

  void spmm_impl(const SpMat& A, const DenseMatrix& X, DenseMatrix& Y, Scalar alpha, Scalar beta,
                 cusparseOperation_t op) {
    eigen_assert(A.isCompressed());

    // For op != NON_TRANSPOSE, Y = op(A) * X. The dense X / Y descriptors must
    // describe the *post-op* shapes: X has k_op rows (= input dim of op(A)),
    // Y has m_op rows (= output dim of op(A)).
    const bool transposed = (op != CUSPARSE_OPERATION_NON_TRANSPOSE);
    const Index m_op = transposed ? A.cols() : A.rows();
    const Index k_op = transposed ? A.rows() : A.cols();
    const Index n = X.cols();
    const Index nnz = A.nonZeros();

    if (m_op == 0 || n == 0 || k_op == 0 || nnz == 0) {
      if (beta == Scalar(0))
        Y.setZero();
      else
        Y *= beta;
      return;
    }

    upload_sparse(A);

    // Upload X to device. X is k_op x n, Y is m_op x n (column-major).
    const size_t x_bytes = static_cast<size_t>(k_op) * static_cast<size_t>(n) * sizeof(Scalar);
    const size_t y_bytes = static_cast<size_t>(m_op) * static_cast<size_t>(n) * sizeof(Scalar);
    ensure_buffer(d_x_, d_x_size_, x_bytes);
    ensure_buffer(d_y_, d_y_size_, y_bytes);
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_x_.get(), X.data(), x_bytes, cudaMemcpyHostToDevice, stream_));
    if (beta != Scalar(0)) {
      EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_y_.get(), Y.data(), y_bytes, cudaMemcpyHostToDevice, stream_));
    }

    // Create dense matrix descriptors.
    constexpr cudaDataType_t dtype = internal::cuda_data_type<Scalar>::value;
    cusparseDnMatDescr_t x_desc = nullptr, y_desc = nullptr;
    // Eigen is column-major, so ld = rows.
    EIGEN_CUSPARSE_CHECK(cusparseCreateDnMat(&x_desc, k_op, n, k_op, d_x_.get(), dtype, CUSPARSE_ORDER_COL));
    EIGEN_CUSPARSE_CHECK(cusparseCreateDnMat(&y_desc, m_op, n, m_op, d_y_.get(), dtype, CUSPARSE_ORDER_COL));

    // Query workspace.
    size_t ws_size = 0;
    EIGEN_CUSPARSE_CHECK(cusparseSpMM_bufferSize(handle_, op, CUSPARSE_OPERATION_NON_TRANSPOSE, &alpha, spmat_desc_,
                                                 x_desc, &beta, y_desc, dtype, CUSPARSE_SPMM_ALG_DEFAULT, &ws_size));
    ensure_buffer(d_workspace_, d_workspace_size_, ws_size);

    // Execute SpMM.
    EIGEN_CUSPARSE_CHECK(cusparseSpMM(handle_, op, CUSPARSE_OPERATION_NON_TRANSPOSE, &alpha, spmat_desc_, x_desc, &beta,
                                      y_desc, dtype, CUSPARSE_SPMM_ALG_DEFAULT, d_workspace_.get()));

    // Download result.
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(Y.data(), d_y_.get(), y_bytes, cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));

    EIGEN_CUSPARSE_CHECK(cusparseDestroyDnMat(x_desc));
    EIGEN_CUSPARSE_CHECK(cusparseDestroyDnMat(y_desc));
  }

  // ---- Helpers --------------------------------------------------------------

  static void check_storage_index_bounds(Index rows, Index cols, Index nnz) {
    const Index max_storage_index = static_cast<Index>((std::numeric_limits<StorageIndex>::max)());
    eigen_assert(rows <= max_storage_index && cols <= max_storage_index && nnz <= max_storage_index &&
                 "gpu::SparseContext currently uses int StorageIndex; matrix dimensions or nonzeros exceed int range");
    EIGEN_UNUSED_VARIABLE(rows);
    EIGEN_UNUSED_VARIABLE(cols);
    EIGEN_UNUSED_VARIABLE(nnz);
    EIGEN_UNUSED_VARIABLE(max_storage_index);
  }

  void upload_sparse(const SpMat& A) {
    // cuSPARSE 12.0+ accepts CSC directly for both SpMV and SpMM. cuSPARSE
    // 11.x SpMM rejects CSC ("unsupported matrix format for matA (CSC)") and
    // SpMV with CONJUGATE_TRANSPOSE on CSC+complex silently demotes to
    // TRANSPOSE. CSR works on every version, so on 11.x we transpose-copy
    // the user's ColMajor input into a RowMajor (CSR) representation.
#if CUSPARSE_VERSION >= 12000
    upload_compressed_arrays(A.rows(), A.cols(), A.nonZeros(),
                             /*outer_count=*/A.cols() + 1, A.outerIndexPtr(), A.innerIndexPtr(), A.valuePtr(),
                             /*is_csr=*/false);
#else
    using CsrMat = SparseMatrix<Scalar, RowMajor, StorageIndex>;
    const CsrMat csr(A);
    upload_compressed_arrays(csr.rows(), csr.cols(), csr.nonZeros(),
                             /*outer_count=*/csr.rows() + 1, csr.outerIndexPtr(), csr.innerIndexPtr(), csr.valuePtr(),
                             /*is_csr=*/true);
    // cudaMemcpyAsync from pageable host memory blocks the host until the
    // source is consumed, so the CsrMat temporary's lifetime is sufficient.
#endif
  }

  void upload_compressed_arrays(Index m, Index n, Index nnz, Index outer_count, const StorageIndex* host_outer,
                                const StorageIndex* host_inner, const Scalar* host_values, bool is_csr) {
    const size_t outer_bytes = static_cast<size_t>(outer_count) * sizeof(StorageIndex);
    const size_t inner_bytes = static_cast<size_t>(nnz) * sizeof(StorageIndex);
    const size_t val_bytes = static_cast<size_t>(nnz) * sizeof(Scalar);

    ensure_buffer(d_outerPtr_, d_outerPtr_size_, outer_bytes);
    ensure_buffer(d_innerIdx_, d_innerIdx_size_, inner_bytes);
    ensure_buffer(d_values_, d_values_size_, val_bytes);

    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_outerPtr_.get(), host_outer, outer_bytes, cudaMemcpyHostToDevice, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_innerIdx_.get(), host_inner, inner_bytes, cudaMemcpyHostToDevice, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_values_.get(), host_values, val_bytes, cudaMemcpyHostToDevice, stream_));

    if (m != cached_rows_ || n != cached_cols_ || nnz != cached_nnz_) {
      destroy_descriptors_checked();

      constexpr cusparseIndexType_t idx_type = (sizeof(StorageIndex) == 4) ? CUSPARSE_INDEX_32I : CUSPARSE_INDEX_64I;
      constexpr cudaDataType_t val_type = internal::cuda_data_type<Scalar>::value;

      if (is_csr) {
        EIGEN_CUSPARSE_CHECK(cusparseCreateCsr(&spmat_desc_, m, n, nnz, d_outerPtr_.get(), d_innerIdx_.get(),
                                               d_values_.get(), idx_type, idx_type, CUSPARSE_INDEX_BASE_ZERO,
                                               val_type));
      } else {
        EIGEN_CUSPARSE_CHECK(cusparseCreateCsc(&spmat_desc_, m, n, nnz, d_outerPtr_.get(), d_innerIdx_.get(),
                                               d_values_.get(), idx_type, idx_type, CUSPARSE_INDEX_BASE_ZERO,
                                               val_type));
      }
      cached_rows_ = m;
      cached_cols_ = n;
      cached_nnz_ = nnz;
    } else if (is_csr) {
      EIGEN_CUSPARSE_CHECK(cusparseCsrSetPointers(spmat_desc_, d_outerPtr_.get(), d_innerIdx_.get(), d_values_.get()));
    } else {
      EIGEN_CUSPARSE_CHECK(cusparseCscSetPointers(spmat_desc_, d_outerPtr_.get(), d_innerIdx_.get(), d_values_.get()));
    }
  }

  // Destructor-only cleanup: there is no useful recovery path for failures.
  void destroy_descriptors_unchecked() {
    if (spmat_desc_) {
      (void)cusparseDestroySpMat(spmat_desc_);
      spmat_desc_ = nullptr;
    }
    cached_rows_ = -1;
    cached_cols_ = -1;
    cached_nnz_ = -1;
  }

  void destroy_descriptors_checked() {
    if (spmat_desc_) {
      EIGEN_CUSPARSE_CHECK(cusparseDestroySpMat(spmat_desc_));
      spmat_desc_ = nullptr;
    }
    cached_rows_ = -1;
    cached_cols_ = -1;
    cached_nnz_ = -1;
  }

  void ensure_buffer(internal::DeviceBuffer& buf, size_t& current_size, size_t needed) {
    if (needed > current_size) {
      if (buf) EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
      buf = internal::DeviceBuffer(needed);
      current_size = needed;
    }
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_SPARSE_CONTEXT_H
