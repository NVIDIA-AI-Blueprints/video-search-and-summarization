// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// GPU FFT via cuFFT.
//
// Standalone GPU FFT class with plan caching. Supports 1D and 2D transforms:
// C2C (complex-to-complex), R2C (real-to-complex), C2R (complex-to-real).
//
// Inverse transforms are scaled by 1/n (1D) or 1/(n*m) (2D) so that
// inv(fwd(x)) == x, matching Eigen's FFT convention.
//
// cuFFT plans are cached by (size, type) and reused across calls.
//
// Thread safety: not thread-safe. Concurrent fwd/inv calls on a single FFT
// instance race on the cached plans, the cuBLAS handle, and the bound
// stream. Use one FFT instance per thread.
//
// Usage:
//   FFT<float> fft;
//   VectorXcf X = fft.fwd(x);         // 1D C2C or R2C
//   VectorXcf y = fft.inv(X);         // 1D C2C inverse
//   VectorXf  r = fft.invReal(X, n);  // 1D C2R inverse
//   MatrixXcf B = fft.fwd2(A);       // 2D C2C forward
//   MatrixXcf C = fft.inv2(B);       // 2D C2C inverse

#ifndef EIGEN_GPU_FFT_H
#define EIGEN_GPU_FFT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./CuFftSupport.h"
#include "./CuBlasSupport.h"
#include <map>

namespace Eigen {
namespace gpu {

template <typename Scalar_>
class FFT {
 public:
  using Scalar = Scalar_;
  using Complex = std::complex<Scalar>;
  using ComplexVector = Matrix<Complex, Dynamic, 1>;
  using RealVector = Matrix<Scalar, Dynamic, 1>;
  using ComplexMatrix = Matrix<Complex, Dynamic, Dynamic, ColMajor>;

  FFT() {
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamCreate(&stream_));
    EIGEN_CUBLAS_CHECK(cublasCreate(&cublas_));
    EIGEN_CUBLAS_CHECK(cublasSetStream(cublas_, stream_));
  }

  ~FFT() {
    for (auto& kv : plans_) (void)cufftDestroy(kv.second);
    if (cublas_) (void)cublasDestroy(cublas_);
    if (stream_) (void)cudaStreamDestroy(stream_);
  }

  FFT(const FFT&) = delete;
  FFT& operator=(const FFT&) = delete;

  // ---- 1D Complex-to-Complex ------------------------------------------------

  /** Forward 1D C2C FFT. */
  template <typename Derived>
  ComplexVector fwd(const MatrixBase<Derived>& x,
                    typename std::enable_if<NumTraits<typename Derived::Scalar>::IsComplex>::type* = nullptr) {
    const ComplexVector input(x.derived());
    const int n = static_cast<int>(input.size());
    if (n == 0) return ComplexVector(0);

    ensure_buffers(n * sizeof(Complex), n * sizeof(Complex));
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_in_.get(), input.data(), n * sizeof(Complex), cudaMemcpyHostToDevice, stream_));

    cufftHandle plan = get_plan_1d(n, internal::cufft_c2c_type<Scalar>::value);
    EIGEN_CUFFT_CHECK(internal::cufftExecC2C_dispatch(plan, static_cast<Complex*>(d_in_.get()),
                                                      static_cast<Complex*>(d_out_.get()), CUFFT_FORWARD));

    ComplexVector result(n);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(result.data(), d_out_.get(), n * sizeof(Complex), cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
    return result;
  }

  /** Inverse 1D C2C FFT. Scaled by 1/n. */
  template <typename Derived>
  ComplexVector inv(const MatrixBase<Derived>& X) {
    static_assert(NumTraits<typename Derived::Scalar>::IsComplex, "inv() requires complex input");
    const ComplexVector input(X.derived());
    const int n = static_cast<int>(input.size());
    if (n == 0) return ComplexVector(0);

    ensure_buffers(n * sizeof(Complex), n * sizeof(Complex));
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_in_.get(), input.data(), n * sizeof(Complex), cudaMemcpyHostToDevice, stream_));

    cufftHandle plan = get_plan_1d(n, internal::cufft_c2c_type<Scalar>::value);
    EIGEN_CUFFT_CHECK(internal::cufftExecC2C_dispatch(plan, static_cast<Complex*>(d_in_.get()),
                                                      static_cast<Complex*>(d_out_.get()), CUFFT_INVERSE));

    // Scale by 1/n.
    scale_device(static_cast<Complex*>(d_out_.get()), n, Scalar(1) / Scalar(n));

    ComplexVector result(n);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(result.data(), d_out_.get(), n * sizeof(Complex), cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
    return result;
  }

  // ---- 1D Real-to-Complex ---------------------------------------------------

  /** Forward 1D R2C FFT. Returns n/2+1 complex values (half-spectrum). */
  template <typename Derived>
  ComplexVector fwd(const MatrixBase<Derived>& x,
                    typename std::enable_if<!NumTraits<typename Derived::Scalar>::IsComplex>::type* = nullptr) {
    const RealVector input(x.derived());
    const int n = static_cast<int>(input.size());
    if (n == 0) return ComplexVector(0);

    const int n_complex = n / 2 + 1;
    ensure_buffers(n * sizeof(Scalar), n_complex * sizeof(Complex));
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_in_.get(), input.data(), n * sizeof(Scalar), cudaMemcpyHostToDevice, stream_));

    cufftHandle plan = get_plan_1d(n, internal::cufft_r2c_type<Scalar>::value);
    EIGEN_CUFFT_CHECK(
        internal::cufftExecR2C_dispatch(plan, static_cast<Scalar*>(d_in_.get()), static_cast<Complex*>(d_out_.get())));

    ComplexVector result(n_complex);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(result.data(), d_out_.get(), n_complex * sizeof(Complex), cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
    return result;
  }

  // ---- 1D Complex-to-Real ---------------------------------------------------

  /** Inverse 1D C2R FFT. Input is n/2+1 complex values, output is nfft real values.
   * Scaled by 1/nfft. Caller must specify nfft (original real signal length). */
  template <typename Derived>
  RealVector invReal(const MatrixBase<Derived>& X, Index nfft) {
    static_assert(NumTraits<typename Derived::Scalar>::IsComplex, "invReal() requires complex input");
    const ComplexVector input(X.derived());
    const int n = static_cast<int>(nfft);
    const int n_complex = n / 2 + 1;
    eigen_assert(input.size() == n_complex);
    if (n == 0) return RealVector(0);

    ensure_buffers(n_complex * sizeof(Complex), n * sizeof(Scalar));
    // cuFFT C2R may overwrite the input, so we copy to d_in_.
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(d_in_.get(), input.data(), n_complex * sizeof(Complex), cudaMemcpyHostToDevice, stream_));

    cufftHandle plan = get_plan_1d(n, internal::cufft_c2r_type<Scalar>::value);
    EIGEN_CUFFT_CHECK(
        internal::cufftExecC2R_dispatch(plan, static_cast<Complex*>(d_in_.get()), static_cast<Scalar*>(d_out_.get())));

    // Scale by 1/n.
    scale_device_real(static_cast<Scalar*>(d_out_.get()), n, Scalar(1) / Scalar(n));

    RealVector result(n);
    EIGEN_CUDA_RUNTIME_CHECK(
        cudaMemcpyAsync(result.data(), d_out_.get(), n * sizeof(Scalar), cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
    return result;
  }

  // ---- 2D Complex-to-Complex ------------------------------------------------

  /** Forward 2D C2C FFT. Input and output are rows x cols complex matrices. */
  template <typename Derived>
  ComplexMatrix fwd2(const MatrixBase<Derived>& A) {
    static_assert(NumTraits<typename Derived::Scalar>::IsComplex, "fwd2() requires complex input");
    const ComplexMatrix input(A.derived());
    const int rows = static_cast<int>(input.rows());
    const int cols = static_cast<int>(input.cols());
    if (rows == 0 || cols == 0) return ComplexMatrix(rows, cols);

    const size_t total = static_cast<size_t>(rows) * static_cast<size_t>(cols) * sizeof(Complex);
    ensure_buffers(total, total);
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_in_.get(), input.data(), total, cudaMemcpyHostToDevice, stream_));

    cufftHandle plan = get_plan_2d(rows, cols, internal::cufft_c2c_type<Scalar>::value);
    EIGEN_CUFFT_CHECK(internal::cufftExecC2C_dispatch(plan, static_cast<Complex*>(d_in_.get()),
                                                      static_cast<Complex*>(d_out_.get()), CUFFT_FORWARD));

    ComplexMatrix result(rows, cols);
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(result.data(), d_out_.get(), total, cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
    return result;
  }

  /** Inverse 2D C2C FFT. Scaled by 1/(rows*cols). */
  template <typename Derived>
  ComplexMatrix inv2(const MatrixBase<Derived>& A) {
    static_assert(NumTraits<typename Derived::Scalar>::IsComplex, "inv2() requires complex input");
    const ComplexMatrix input(A.derived());
    const int rows = static_cast<int>(input.rows());
    const int cols = static_cast<int>(input.cols());
    if (rows == 0 || cols == 0) return ComplexMatrix(rows, cols);

    const size_t total = static_cast<size_t>(rows) * static_cast<size_t>(cols) * sizeof(Complex);
    ensure_buffers(total, total);
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(d_in_.get(), input.data(), total, cudaMemcpyHostToDevice, stream_));

    cufftHandle plan = get_plan_2d(rows, cols, internal::cufft_c2c_type<Scalar>::value);
    EIGEN_CUFFT_CHECK(internal::cufftExecC2C_dispatch(plan, static_cast<Complex*>(d_in_.get()),
                                                      static_cast<Complex*>(d_out_.get()), CUFFT_INVERSE));

    // Scale by 1/(rows*cols).
    const int total_elems = rows * cols;
    scale_device(static_cast<Complex*>(d_out_.get()), total_elems, Scalar(1) / Scalar(total_elems));

    ComplexMatrix result(rows, cols);
    EIGEN_CUDA_RUNTIME_CHECK(cudaMemcpyAsync(result.data(), d_out_.get(), total, cudaMemcpyDeviceToHost, stream_));
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
    return result;
  }

  // ---- Accessors ------------------------------------------------------------

  cudaStream_t stream() const { return stream_; }

 private:
  cudaStream_t stream_ = nullptr;
  cublasHandle_t cublas_ = nullptr;
  std::map<int64_t, cufftHandle> plans_;
  internal::DeviceBuffer d_in_;
  internal::DeviceBuffer d_out_;
  size_t d_in_size_ = 0;
  size_t d_out_size_ = 0;

  void ensure_buffers(size_t in_bytes, size_t out_bytes) {
    if (in_bytes > d_in_size_) {
      if (d_in_) EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
      d_in_ = internal::DeviceBuffer(in_bytes);
      d_in_size_ = in_bytes;
    }
    if (out_bytes > d_out_size_) {
      if (d_out_) EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
      d_out_ = internal::DeviceBuffer(out_bytes);
      d_out_size_ = out_bytes;
    }
  }

  // Plan key encoding: rank (1 bit) | type (4 bits) | dims.
  // cufftType uses 7 bits; the top 3 (precision discriminator) are redundant
  // since Scalar fixes precision per FFT instance, so mask to 4 bits — without
  // it, e.g. plan_key_1d(5, C2C) and plan_key_1d(7, C2C) collide.
  static constexpr int64_t kTypeMask = 0xF;
  static constexpr int kCols2DBits = 30;  // bits 5..34
  static constexpr int kRows2DBits = 29;  // bits 35..63
  static int64_t plan_key_1d(int n, cufftType type) { return (int64_t(n) << 5) | (int64_t(type & kTypeMask) << 1) | 0; }

  static int64_t plan_key_2d(int rows, int cols, cufftType type) {
    eigen_assert(rows >= 0 && int64_t(rows) < (int64_t(1) << kRows2DBits) &&
                 "FFT plan rows exceed plan-key bit budget");
    eigen_assert(cols >= 0 && int64_t(cols) < (int64_t(1) << kCols2DBits) &&
                 "FFT plan cols exceed plan-key bit budget");
    return (int64_t(rows) << 35) | (int64_t(cols) << 5) | (int64_t(type & kTypeMask) << 1) | 1;
  }

  cufftHandle get_plan_1d(int n, cufftType type) {
    int64_t key = plan_key_1d(n, type);
    auto it = plans_.find(key);
    if (it != plans_.end()) return it->second;

    cufftHandle plan;
    EIGEN_CUFFT_CHECK(cufftPlan1d(&plan, n, type, /*batch=*/1));
    EIGEN_CUFFT_CHECK(cufftSetStream(plan, stream_));
    plans_[key] = plan;
    return plan;
  }

  cufftHandle get_plan_2d(int rows, int cols, cufftType type) {
    int64_t key = plan_key_2d(rows, cols, type);
    auto it = plans_.find(key);
    if (it != plans_.end()) return it->second;

    // cuFFT uses row-major (C order) for 2D: first dim = rows, second = cols.
    // Eigen matrices are column-major, so we pass (cols, rows) to cuFFT
    // to get the correct 2D transform.
    cufftHandle plan;
    EIGEN_CUFFT_CHECK(cufftPlan2d(&plan, cols, rows, type));
    EIGEN_CUFFT_CHECK(cufftSetStream(plan, stream_));
    plans_[key] = plan;
    return plan;
  }

  // Scale complex array on device using cuBLAS scal.
  void scale_device(Complex* d_ptr, int n, Scalar alpha) {
    EIGEN_CUBLAS_CHECK(internal::cublasXscal(cublas_, n, &alpha, d_ptr, 1));
  }

  // Scale real array on device using cuBLAS scal.
  void scale_device_real(Scalar* d_ptr, int n, Scalar alpha) {
    EIGEN_CUBLAS_CHECK(internal::cublasXscal(cublas_, n, &alpha, d_ptr, 1));
  }
};

}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_FFT_H
