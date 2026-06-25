// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2016 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TENSOR_TENSOR_COST_MODEL_H
#define EIGEN_TENSOR_TENSOR_COST_MODEL_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

// Class storing the cost of evaluating a tensor expression in terms of the
// estimated number of operand bytes loads, bytes stored, and compute cycles.
class TensorOpCost {
 public:
  // TODO(rmlarsen): Fix the scalar op costs in Eigen proper. Even a simple
  // model based on minimal reciprocal throughput numbers from Intel or
  // Agner Fog's tables would be better than what is there now.
  template <typename ArgType>
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE int MulCost() {
    return internal::functor_traits<internal::scalar_product_op<ArgType, ArgType> >::Cost;
  }
  template <typename ArgType>
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE int AddCost() {
    return internal::functor_traits<internal::scalar_sum_op<ArgType> >::Cost;
  }
  template <typename ArgType>
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE int DivCost() {
    return internal::functor_traits<internal::scalar_quotient_op<ArgType, ArgType> >::Cost;
  }
  template <typename ArgType>
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE int ModCost() {
    return internal::functor_traits<internal::scalar_mod_op<ArgType> >::Cost;
  }
  template <typename SrcType, typename TargetType>
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE int CastCost() {
    return internal::functor_traits<internal::scalar_cast_op<SrcType, TargetType> >::Cost;
  }

  constexpr EIGEN_DEVICE_FUNC TensorOpCost() : bytes_loaded_(0), bytes_stored_(0), compute_cycles_(0) {}
  constexpr EIGEN_DEVICE_FUNC TensorOpCost(double bytes_loaded, double bytes_stored, double compute_cycles)
      : bytes_loaded_(bytes_loaded), bytes_stored_(bytes_stored), compute_cycles_(compute_cycles) {}

  EIGEN_DEVICE_FUNC TensorOpCost(double bytes_loaded, double bytes_stored, double compute_cycles, bool vectorized,
                                 double packet_size)
      : bytes_loaded_(bytes_loaded),
        bytes_stored_(bytes_stored),
        compute_cycles_(vectorized ? compute_cycles / packet_size : compute_cycles) {
    eigen_assert(bytes_loaded >= 0 && (numext::isfinite)(bytes_loaded));
    eigen_assert(bytes_stored >= 0 && (numext::isfinite)(bytes_stored));
    eigen_assert(compute_cycles >= 0 && (numext::isfinite)(compute_cycles));
  }

  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double bytes_loaded() const { return bytes_loaded_; }
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double bytes_stored() const { return bytes_stored_; }
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double compute_cycles() const { return compute_cycles_; }
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double total_bytes() const { return bytes_loaded_ + bytes_stored_; }
  constexpr EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double total_cost(double load_cost, double store_cost,
                                                                    double compute_cost) const {
    return load_cost * bytes_loaded_ + store_cost * bytes_stored_ + compute_cost * compute_cycles_;
  }

  // Drop memory access component. Intended for cases when memory accesses are
  // sequential or are completely masked by computations.
  EIGEN_DEVICE_FUNC void dropMemoryCost() {
    bytes_loaded_ = 0;
    bytes_stored_ = 0;
  }

  // TODO(rmlarsen): Define min in terms of total cost, not elementwise.
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TensorOpCost cwiseMin(const TensorOpCost& rhs) const {
    double bytes_loaded = numext::mini(bytes_loaded_, rhs.bytes_loaded());
    double bytes_stored = numext::mini(bytes_stored_, rhs.bytes_stored());
    double compute_cycles = numext::mini(compute_cycles_, rhs.compute_cycles());
    return TensorOpCost(bytes_loaded, bytes_stored, compute_cycles);
  }

  // TODO(rmlarsen): Define max in terms of total cost, not elementwise.
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TensorOpCost cwiseMax(const TensorOpCost& rhs) const {
    double bytes_loaded = numext::maxi(bytes_loaded_, rhs.bytes_loaded());
    double bytes_stored = numext::maxi(bytes_stored_, rhs.bytes_stored());
    double compute_cycles = numext::maxi(compute_cycles_, rhs.compute_cycles());
    return TensorOpCost(bytes_loaded, bytes_stored, compute_cycles);
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TensorOpCost& operator+=(const TensorOpCost& rhs) {
    bytes_loaded_ += rhs.bytes_loaded();
    bytes_stored_ += rhs.bytes_stored();
    compute_cycles_ += rhs.compute_cycles();
    return *this;
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TensorOpCost& operator*=(double rhs) {
    bytes_loaded_ *= rhs;
    bytes_stored_ *= rhs;
    compute_cycles_ *= rhs;
    return *this;
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE friend TensorOpCost operator+(TensorOpCost lhs, const TensorOpCost& rhs) {
    lhs += rhs;
    return lhs;
  }
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE friend TensorOpCost operator*(TensorOpCost lhs, double rhs) {
    lhs *= rhs;
    return lhs;
  }
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE friend TensorOpCost operator*(double lhs, TensorOpCost rhs) {
    rhs *= lhs;
    return rhs;
  }

  friend std::ostream& operator<<(std::ostream& os, const TensorOpCost& tc) {
    return os << "[bytes_loaded = " << tc.bytes_loaded() << ", bytes_stored = " << tc.bytes_stored()
              << ", compute_cycles = " << tc.compute_cycles() << "]";
  }

 private:
  double bytes_loaded_;
  double bytes_stored_;
  double compute_cycles_;
};

/**
 * \ingroup Tensor_Module
 *
 * \brief A cost model used to limit the number of threads used for evaluating
 * tensor expression.
 *
 * Uses a roofline model: cost = max(memory_time, compute_time) instead of
 * summing them. This avoids overestimating cost for balanced workloads.
 * Memory-bound operations are capped at a limited number of threads to
 * avoid wasting cycles competing for shared memory bandwidth.
 */
template <typename Device>
class TensorCostModel {
 public:
  // Scaling from Eigen compute cost to device cycles.
  static constexpr int kDeviceCyclesPerComputeCycle = 1;

  // Thread overhead in device cycles. ~8us at 3GHz.
  // Minimum total work to justify thread pool dispatch.
  static constexpr int kStartupCycles = 25000;
  // Minimum work per thread to amortize dispatch and synchronization overhead.
  static constexpr int kPerThreadCycles = 25000;
  static constexpr int kTaskSize = 40000;

  // Memory bandwidth saturation: on typical multi-socket servers, 2-6 cores
  // saturate DRAM bandwidth. 4 is a conservative default.
  static constexpr int kMemBandwidthSaturationThreads = 4;

  // If memory_time / compute_time exceeds this ratio, the op is memory-bound.
  // With vectorized costs (AVX2, PacketSize=8), typical ratios are:
  //   Add/Mul (2 loads + 1 store): mem/comp = 6.0
  //   FMA (3 loads + 1 store):     mem/comp = 4.0
  //   ReLU max(x,0) (1 load + 1 store): mem/comp = 4.0
  //   Polynomial 3rd-order (3 loads + 1 store, 6 ops): mem/comp = 1.3
  //   Exp (1 load + 1 store, ~8 ops): mem/comp = 0.5
  // Threshold of 2.0 cleanly separates memory-bound (>=4) from compute-bound.
  static constexpr double kMemBoundThreshold = 2.0;

  // Data sets larger than this are assumed to be DRAM-resident.
  // Below this threshold, data is likely L2-cache-resident and benefits from
  // high per-core L2 bandwidth, so no bandwidth saturation cap is applied.
  static constexpr double kDramThresholdBytes = 1024.0 * 1024.0;

  // Returns the number of threads in [1:max_threads] to use for
  // evaluating an expression with the given output size and cost per
  // coefficient.
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE int numThreads(double output_size, const TensorOpCost& cost_per_coeff,
                                                              int max_threads) {
    if (max_threads <= 1) return 1;

    double mem = memoryTime(cost_per_coeff);
    double comp = computeTime(cost_per_coeff);
    double per_coeff = numext::maxi(mem, comp);
    double total = output_size * per_coeff;

    // Not enough total work to justify thread pool dispatch.
    if (total < kStartupCycles) return 1;

    // Each thread needs at least kPerThreadCycles of work to
    // amortize dispatch and synchronization overhead.
    double threads = total / kPerThreadCycles;
    // Guard against integer overflow.
    threads = numext::mini<double>(threads, GenericNumTraits<int>::highest());
    int candidate = numext::mini(max_threads, numext::maxi<int>(1, static_cast<int>(threads)));

    // Memory-bound ops on DRAM-resident data: cap at bandwidth saturation.
    // Cache-resident data has high per-core L2 bandwidth, so no cap needed.
    //
    // The total-traffic proxy below overestimates the working set whenever an
    // operand is reused (e.g. broadcasted): every output coefficient charges a
    // fresh load, so a 1xN broadcast can show up as MxN bytes even though the
    // live data is N. A working-set-aware estimate would need each evaluator to
    // surface its unique-operand footprint; until that exists we accept a small
    // bias toward over-capping for reuse-heavy expressions.
    if (candidate > kMemBandwidthSaturationThreads) {
      bool is_memory_bound = (comp > 0) ? (mem / comp > kMemBoundThreshold) : (mem > 0);
      if (is_memory_bound) {
        double total_bytes = output_size * cost_per_coeff.total_bytes();
        if (total_bytes > kDramThresholdBytes) {
          candidate = numext::mini(candidate, kMemBandwidthSaturationThreads);
        }
      }
    }
    return candidate;
  }

  // taskSize assesses parallel task size.
  // Value of 1.0 means ideal parallel task size. Values < 1.0 mean that task
  // granularity needs to be increased to mitigate parallelization overheads.
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double taskSize(double output_size, const TensorOpCost& cost_per_coeff) {
    return totalCost(output_size, cost_per_coeff) / kTaskSize;
  }

  // Roofline model: cost is the max of memory time and compute time.
  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double totalCost(double output_size,
                                                                const TensorOpCost& cost_per_coeff) {
    double mem_cost = memoryTime(cost_per_coeff);
    double comp_cost = computeTime(cost_per_coeff);
    return output_size * numext::maxi(mem_cost, comp_cost);
  }

 private:
  // Effective sustained bandwidth cost in cycles/byte.
  // ~1/16 = 0.0625 cycles/byte represents L3/DRAM streaming bandwidth
  // on modern CPUs (~16 bytes/cycle single-core sustained throughput).
  // For L1/L2-resident data, compute typically dominates anyway.
  static constexpr double kByteCost = 1.0 / 16.0;

  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double memoryTime(const TensorOpCost& cost) {
    return cost.total_bytes() * kByteCost;
  }

  static EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE double computeTime(const TensorOpCost& cost) {
    return cost.compute_cycles() * kDeviceCyclesPerComputeCycle;
  }
};

}  // namespace Eigen

#endif  // EIGEN_TENSOR_TENSOR_COST_MODEL_H
