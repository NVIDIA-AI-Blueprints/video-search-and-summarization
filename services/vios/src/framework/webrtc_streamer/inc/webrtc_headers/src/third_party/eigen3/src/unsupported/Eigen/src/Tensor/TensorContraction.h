// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2014 Benoit Steiner <benoit.steiner.goog@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TENSOR_TENSOR_CONTRACTION_H
#define EIGEN_TENSOR_TENSOR_CONTRACTION_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

namespace Eigen {

namespace internal {

template <typename Dimensions, typename LhsXprType, typename RhsXprType, typename OutputKernelType>
struct traits<TensorContractionOp<Dimensions, LhsXprType, RhsXprType, OutputKernelType>> {
  // Type promotion to handle the case where the types of the lhs and the rhs are different.
  using Scalar = typename gebp_traits<std::remove_const_t<typename LhsXprType::Scalar>,
                                      std::remove_const_t<typename RhsXprType::Scalar>>::ResScalar;

  using StorageKind = typename promote_storage_type<typename traits<LhsXprType>::StorageKind,
                                                    typename traits<RhsXprType>::StorageKind>::ret;
  using Index =
      typename promote_index_type<typename traits<LhsXprType>::Index, typename traits<RhsXprType>::Index>::type;
  using LhsNested = typename LhsXprType::Nested;
  using RhsNested = typename RhsXprType::Nested;
  using LhsNested_ = std::remove_reference_t<LhsNested>;
  using RhsNested_ = std::remove_reference_t<RhsNested>;

  // From NumDims below.
  static constexpr int NumDimensions =
      traits<LhsXprType>::NumDimensions + traits<RhsXprType>::NumDimensions - 2 * array_size<Dimensions>::value;
  static constexpr int Layout = traits<LhsXprType>::Layout;
  using PointerType =
      std::conditional_t<Pointer_type_promotion<typename LhsXprType::Scalar, Scalar>::val,
                         typename traits<LhsXprType>::PointerType, typename traits<RhsXprType>::PointerType>;

  static constexpr int Flags = 0;
};

template <typename Dimensions, typename LhsXprType, typename RhsXprType, typename OutputKernelType>
struct eval<TensorContractionOp<Dimensions, LhsXprType, RhsXprType, OutputKernelType>, Eigen::Dense> {
  using type = const TensorContractionOp<Dimensions, LhsXprType, RhsXprType, OutputKernelType>&;
};

template <typename Dimensions, typename LhsXprType, typename RhsXprType, typename OutputKernelType>
struct nested<TensorContractionOp<Dimensions, LhsXprType, RhsXprType, OutputKernelType>, 1,
              typename eval<TensorContractionOp<Dimensions, LhsXprType, RhsXprType, OutputKernelType>>::type> {
  using type = TensorContractionOp<Dimensions, LhsXprType, RhsXprType, OutputKernelType>;
};

template <typename Indices_, typename LeftArgType_, typename RightArgType_, typename OutputKernelType_,
          typename Device_>
struct traits<
    TensorEvaluator<const TensorContractionOp<Indices_, LeftArgType_, RightArgType_, OutputKernelType_>, Device_>> {
  using Indices = Indices_;
  using LeftArgType = LeftArgType_;
  using RightArgType = RightArgType_;
  using OutputKernelType = OutputKernelType_;
  using Device = Device_;

  // From NumDims below.
  static constexpr int NumDimensions =
      traits<LeftArgType_>::NumDimensions + traits<RightArgType_>::NumDimensions - 2 * array_size<Indices_>::value;
};

// Helper class to allocate and deallocate temporary memory for packed buffers.
template <typename LhsScalar, typename RhsScalar>
struct TensorContractionBlockMemAllocator {
  using BlockMemHandle = void*;

  template <typename Device>
  EIGEN_DEVICE_FUNC static BlockMemHandle allocate(Device& d, const Index bm, const Index bk, const Index bn,
                                                   LhsScalar** lhs_block, RhsScalar** rhs_block) {
    eigen_assert(lhs_block);
    eigen_assert(rhs_block);
    BlockSizes sz = ComputeLhsRhsBlockSizes(bm, bk, bn);
    char* block_mem = static_cast<char*>(d.allocate(sz.lhs_size + sz.rhs_size));
    *lhs_block = static_cast<LhsScalar*>(static_cast<void*>(block_mem));
    *rhs_block = static_cast<RhsScalar*>(static_cast<void*>(block_mem + sz.lhs_size));
    return block_mem;
  }

  template <typename Device>
  EIGEN_DEVICE_FUNC static BlockMemHandle allocateSlices(Device& d, const Index bm, const Index bk, const Index bn,
                                                         const Index num_lhs, const Index num_rhs,
                                                         const Index num_slices, std::vector<LhsScalar*>* lhs_blocks,
                                                         std::vector<RhsScalar*>* rhs_blocks) {
    eigen_assert(num_slices > 0);
    eigen_assert(num_lhs >= 0 && num_rhs >= 0);
    eigen_assert(num_lhs == 0 || lhs_blocks);
    eigen_assert(num_rhs == 0 || rhs_blocks);
    BlockSizes sz = ComputeLhsRhsBlockSizes(bm, bk, bn);
    void* block_mem = d.allocate((num_lhs * sz.lhs_size + num_rhs * sz.rhs_size) * num_slices);
    eigen_assert(block_mem);
    char* mem = static_cast<char*>(block_mem);

    for (Index x = 0; x < num_slices; x++) {
      if (num_lhs > 0) lhs_blocks[x].resize(num_lhs);
      for (Index m = 0; m < num_lhs; m++) {
        lhs_blocks[x][m] = static_cast<LhsScalar*>(static_cast<void*>(mem));
        mem += sz.lhs_size;
      }
      if (num_rhs > 0) rhs_blocks[x].resize(num_rhs);
      for (Index n = 0; n < num_rhs; n++) {
        rhs_blocks[x][n] = static_cast<RhsScalar*>(static_cast<void*>(mem));
        mem += sz.rhs_size;
      }
    }

    return block_mem;
  }

  template <typename Device>
  EIGEN_DEVICE_FUNC static void deallocate(Device& d, BlockMemHandle handle) {
    d.deallocate(handle);
  }

 private:
  struct BlockSizes {
    Index lhs_size;
    Index rhs_size;
  };
  EIGEN_DEVICE_FUNC static BlockSizes ComputeLhsRhsBlockSizes(const Index bm, const Index bk, const Index bn) {
    Index align = numext::maxi(EIGEN_MAX_ALIGN_BYTES, 1);
    BlockSizes sz;
    sz.lhs_size = numext::div_ceil<Index>(bm * bk * sizeof(LhsScalar), align) * align;
    sz.rhs_size = numext::div_ceil<Index>(bn * bk * sizeof(RhsScalar), align) * align;
    return sz;
  }
};

// WARNING: In this code we assume that Lhs and Rhs tensor expressions are in
// ColMajor storage order. This property is guaranteed by the
// TensorContractionOp evaluator. TensorContractionKernel specifies how we pack
// blocks of Lhs and Rhs tensor expressions, and how we invoke matrix
// multiplication for these blocks. Default tensor contraction uses
// gemm_pack_rhs, gemm_pack_lhs and gebp_kernel from Eigen Core (see
// GeneralBlockPanelKernel.h for details).
//
// By specializing contraction kernels we can use other low level libraries to
// perform matrix multiplication, and still rely on Eigen contraction evaluator.
// This also includes full support in TensorContractionThreadPool, assuming that
// underlying gemm does not use its own threading.
//
// - ResScalar/LhsScalar/RhsScalar - scalar type for the result of
//   multiplication, lhs tensor and rhs tensor respectively.
//
// - StorageIndex - index type for the tensor expressions. In practice almost
//   always is Eigen::Index.
//
// - OutputMapper provides access to the memory of the output matrix. In
//   practice it's always column major blas_data_mapper (it must be of ResScalar
//   type).
//
// - LhsMapper/RhsMapper similarly to blas_data_mapper provide a two dimensional
//   view into the Lhs/Rhs tensor expressions. In practice it's
//   TensorContractionInputMapper, or some specialization of it based on the
//   type of tensor expression (e.g. TensorImagePatchOp has optimized input
//   mapper).
template <typename ResScalar, typename LhsScalar, typename RhsScalar, typename StorageIndex, typename OutputMapper,
          typename LhsMapper, typename RhsMapper>
struct TensorContractionKernel {
  // True if `invoke()` supports `beta` in `C <- alpha * A * B + beta * C`
  // (otherwise beta should be always equal to 1).
  static constexpr bool HasBeta = false;

  EIGEN_DEVICE_FUNC TensorContractionKernel(StorageIndex m_, StorageIndex k_, StorageIndex n_, StorageIndex bm_,
                                            StorageIndex bk_, StorageIndex bn_)
      : m(m_), k(k_), n(n_), bm(bm_), bk(bk_), bn(bn_) {}

  // Pack blocks of Lhs and Rhs into contiguous blocks in memory.
  using LhsBlock = LhsScalar*;
  using RhsBlock = RhsScalar*;

  // Packed Lhs/Rhs block memory allocator.
  using BlockMemAllocator = TensorContractionBlockMemAllocator<LhsScalar, RhsScalar>;
  using BlockMemHandle = typename BlockMemAllocator::BlockMemHandle;

  using Traits = typename internal::gebp_traits<LhsScalar, RhsScalar>;

  using LhsPacker = internal::gemm_pack_lhs<LhsScalar, StorageIndex, typename LhsMapper::SubMapper, Traits::mr,
                                            Traits::LhsProgress, typename Traits::LhsPacket4Packing, ColMajor>;

  using RhsPacker =
      internal::gemm_pack_rhs<RhsScalar, StorageIndex, typename RhsMapper::SubMapper, Traits::nr, ColMajor>;

  using GebpKernel = internal::gebp_kernel<LhsScalar, RhsScalar, StorageIndex, OutputMapper, Traits::mr, Traits::nr,
                                           /*ConjugateLhs*/ false, /*ConjugateRhs*/ false>;

  template <typename Device>
  EIGEN_DEVICE_FUNC BlockMemHandle allocate(Device& d, LhsBlock* lhs_block, RhsBlock* rhs_block) {
    return BlockMemAllocator::allocate(d, bm, bk, bn, lhs_block, rhs_block);
  }

  template <typename Device>
  EIGEN_DEVICE_FUNC BlockMemHandle allocateSlices(Device& d, const StorageIndex num_lhs, const StorageIndex num_rhs,
                                                  const StorageIndex num_slices, std::vector<LhsBlock>* lhs_blocks,
                                                  std::vector<RhsBlock>* rhs_blocks) {
    return BlockMemAllocator::allocateSlices(d, bm, bk, bn, num_lhs, num_rhs, num_slices, lhs_blocks, rhs_blocks);
  }

  template <typename Device>
  EIGEN_DEVICE_FUNC static void deallocate(Device& d, BlockMemHandle handle) {
    BlockMemAllocator::deallocate(d, handle);
  }

  EIGEN_DEVICE_FUNC EIGEN_DONT_INLINE void packLhs(LhsBlock* lhsBlock, const typename LhsMapper::SubMapper& data_mapper,
                                                   const StorageIndex depth, const StorageIndex rows) {
    LhsPacker()(*lhsBlock, data_mapper, depth, rows, /*stride*/ 0,
                /*offset*/ 0);
  }

  EIGEN_DEVICE_FUNC EIGEN_DONT_INLINE void packRhs(RhsBlock* rhsBlock, const typename RhsMapper::SubMapper& data_mapper,
                                                   const StorageIndex depth, const StorageIndex cols) {
    RhsPacker()(*rhsBlock, data_mapper, depth, cols);
  }

  EIGEN_DEVICE_FUNC EIGEN_DONT_INLINE void invoke(const OutputMapper& output_mapper, const LhsBlock& lhsBlock,
                                                  const RhsBlock& rhsBlock, const StorageIndex rows,
                                                  const StorageIndex depth, const StorageIndex cols,
                                                  const ResScalar alpha, const ResScalar beta) {
    // Default GEBP kernel does not support beta.
    EIGEN_ONLY_USED_FOR_DEBUG(beta);
    eigen_assert(beta == ResScalar(1));
    static constexpr int kComputeStrideFromBlockDimensions = -1;
    GebpKernel()(output_mapper, lhsBlock, rhsBlock, rows, depth, cols, alpha,
                 /*strideA*/ kComputeStrideFromBlockDimensions,
                 /*strideB*/ kComputeStrideFromBlockDimensions,
                 /*offsetA*/ 0, /*offsetB*/ 0);
  }

 private:
  // These are dimensions of the original Tensors, and selected block sizes. The
  // actual block sizes passed to all function above might be smaller because of
  // the partial blocks at the end.
  const StorageIndex m;
  const StorageIndex k;
  const StorageIndex n;
  const StorageIndex bm;
  const StorageIndex bk;
  const StorageIndex bn;
};

// Dispatches a contraction operation over all 8 combinations of the three
// runtime boolean parameters (lhs_inner_dim_contiguous, rhs_inner_dim_contiguous,
// rhs_inner_dim_reordered), passing them as compile-time bool_constant
// tags to the callable `fn`.
template <typename Func>
EIGEN_STRONG_INLINE void tensor_contraction_dispatch(Func&& fn, bool lhs_inner_dim_contiguous,
                                                     bool rhs_inner_dim_contiguous, bool rhs_inner_dim_reordered) {
  if (lhs_inner_dim_contiguous) {
    if (rhs_inner_dim_contiguous) {
      if (rhs_inner_dim_reordered)
        fn(bool_constant<true>{}, bool_constant<true>{}, bool_constant<true>{});
      else
        fn(bool_constant<true>{}, bool_constant<true>{}, bool_constant<false>{});
    } else {
      if (rhs_inner_dim_reordered)
        fn(bool_constant<true>{}, bool_constant<false>{}, bool_constant<true>{});
      else
        fn(bool_constant<true>{}, bool_constant<false>{}, bool_constant<false>{});
    }
  } else {
    if (rhs_inner_dim_contiguous) {
      if (rhs_inner_dim_reordered)
        fn(bool_constant<false>{}, bool_constant<true>{}, bool_constant<true>{});
      else
        fn(bool_constant<false>{}, bool_constant<true>{}, bool_constant<false>{});
    } else {
      if (rhs_inner_dim_reordered)
        fn(bool_constant<false>{}, bool_constant<false>{}, bool_constant<true>{});
      else
        fn(bool_constant<false>{}, bool_constant<false>{}, bool_constant<false>{});
    }
  }
}

}  // end namespace internal

// Legacy macros kept for backward compatibility with code that overrides them
// (e.g. TensorFlow Lite restricts template instantiations for binary size).
// New Eigen code should use internal::tensor_contraction_dispatch() instead.
#ifndef TENSOR_CONTRACTION_DISPATCH
#define TENSOR_CONTRACTION_DISPATCH(METHOD, ALIGNMENT, ARGS)                                          \
  ::Eigen::internal::tensor_contraction_dispatch(                                                     \
      [&](auto lhs_c, auto rhs_c, auto rhs_r) { METHOD<lhs_c(), rhs_c(), rhs_r(), ALIGNMENT> ARGS; }, \
      this->m_lhs_inner_dim_contiguous, this->m_rhs_inner_dim_contiguous, this->m_rhs_inner_dim_reordered)
#endif

#ifndef TENSOR_CONTRACTION_ASYNC_DISPATCH
#define TENSOR_CONTRACTION_ASYNC_DISPATCH(METHOD, DONE, ALIGNMENT, ARGS, FN)                                          \
  ::Eigen::internal::tensor_contraction_dispatch(                                                                     \
      [&](auto lhs_c, auto rhs_c, auto rhs_r) { (new METHOD<DONE, lhs_c(), rhs_c(), rhs_r(), ALIGNMENT> ARGS)->FN; }, \
      this->m_lhs_inner_dim_contiguous, this->m_rhs_inner_dim_contiguous, this->m_rhs_inner_dim_reordered)
#endif

// Tensor contraction params that should enable to get from output matrix
// 2-dimensional coordinates to the output tensor dimensions.
struct TensorContractionParams {
  // TensorContraction evaluator assumes that both tensors are in ColMajor
  // layout, if tensors are in RowMajor evaluator swap lhs with rhs.
  bool swapped_arguments;
};

// Output kernel allows to fuse operations into the tensor contraction.
//
// Examples:
//   1. Elementwise Relu transformation following Conv2D.
//   2. AddBias to the Conv2D output channels dimension.
//
// The NoOpOutputKernel implements an output kernel that does absolutely nothing.
struct NoOpOutputKernel {
  /**
   * Tensor contraction evaluator calls this kernel after finishing each block
   * of output matrix. Output blocks belong to the 2-dimensional output tensor.
   *
   * TensorContractionParams contains contraction dimensions information
   * required to map output 2-d space into the expected output tensor space
   * (potentially higher dimensional).
   *
   * \param[in] output_mapper Access to output tensor memory
   * \param[in] params   Tensor contraction parameters
   * \param[in] i        Index of a first row available through output_mapper
   * \param[in] j        Index of a first column available through output_mapper
   * \param[in] num_rows Number of available rows
   * \param[in] num_cols Number of available columns
   */
  template <typename Index, typename Scalar>
  EIGEN_ALWAYS_INLINE void operator()(const internal::blas_data_mapper<Scalar, Index, ColMajor>&,
                                      const TensorContractionParams&, Index, Index, Index, Index) const {}
};

/** Tensor contraction class.
 * \ingroup Tensor_Module
 */
template <typename Indices, typename LhsXprType, typename RhsXprType,
          typename OutputKernelType = const NoOpOutputKernel>
class TensorContractionOp
    : public TensorBase<TensorContractionOp<Indices, LhsXprType, RhsXprType, OutputKernelType>, ReadOnlyAccessors> {
 public:
  using Scalar = typename Eigen::internal::traits<TensorContractionOp>::Scalar;
  using CoeffReturnType = typename internal::gebp_traits<typename LhsXprType::CoeffReturnType,
                                                         typename RhsXprType::CoeffReturnType>::ResScalar;
  using Nested = typename Eigen::internal::nested<TensorContractionOp>::type;
  using StorageKind = typename Eigen::internal::traits<TensorContractionOp>::StorageKind;
  using Index = typename Eigen::internal::traits<TensorContractionOp>::Index;

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TensorContractionOp(const LhsXprType& lhs, const RhsXprType& rhs,
                                                            const Indices& dims,
                                                            const OutputKernelType& output_kernel = OutputKernelType())
      : m_lhs_xpr(lhs), m_rhs_xpr(rhs), m_indices(dims), m_output_kernel(output_kernel) {}

  EIGEN_DEVICE_FUNC const Indices& indices() const { return m_indices; }

  /** \returns the nested expressions */
  EIGEN_DEVICE_FUNC const internal::remove_all_t<typename LhsXprType::Nested>& lhsExpression() const {
    return m_lhs_xpr;
  }

  EIGEN_DEVICE_FUNC const internal::remove_all_t<typename RhsXprType::Nested>& rhsExpression() const {
    return m_rhs_xpr;
  }

  EIGEN_DEVICE_FUNC const OutputKernelType& outputKernel() const { return m_output_kernel; }

 protected:
  typename LhsXprType::Nested m_lhs_xpr;
  typename RhsXprType::Nested m_rhs_xpr;
  const Indices m_indices;
  const OutputKernelType m_output_kernel;
};

template <typename Derived>
struct TensorContractionEvaluatorBase {
  using Indices = typename internal::traits<Derived>::Indices;
  using LeftArgType = typename internal::traits<Derived>::LeftArgType;
  using RightArgType = typename internal::traits<Derived>::RightArgType;
  using OutputKernelType = typename internal::traits<Derived>::OutputKernelType;
  using Device = typename internal::traits<Derived>::Device;

  using XprType = TensorContractionOp<Indices, LeftArgType, RightArgType, OutputKernelType>;
  using Scalar = std::remove_const_t<typename XprType::Scalar>;
  using Index = typename XprType::Index;
  using CoeffReturnType = typename XprType::CoeffReturnType;
  using PacketReturnType = typename PacketType<CoeffReturnType, Device>::type;
  using Storage = StorageMemory<Scalar, Device>;
  using EvaluatorPointerType = typename Storage::Type;

  static constexpr int Layout = TensorEvaluator<LeftArgType, Device>::Layout;
  static constexpr bool IsAligned = true;
  static constexpr bool PacketAccess = (PacketType<CoeffReturnType, Device>::size > 1);
  static constexpr bool BlockAccess = false;
  static constexpr bool PreferBlockAccess = false;
  static constexpr bool CoordAccess = false;  // to be implemented
  static constexpr bool RawAccess = true;

  //===- Tensor block evaluation strategy (see TensorBlock.h) -------------===//
  using TensorBlock = internal::TensorBlockNotImplemented;
  //===--------------------------------------------------------------------===//

  // Most of the code is assuming that both input tensors are ColMajor. If the
  // inputs are RowMajor, we will "cheat" by swapping the LHS and RHS:
  // If we want to compute A * B = C, where A is LHS and B is RHS, the code
  // will pretend B is LHS and A is RHS.
  using EvalLeftArgType =
      std::conditional_t<static_cast<int>(Layout) == static_cast<int>(ColMajor), LeftArgType, RightArgType>;
  using EvalRightArgType =
      std::conditional_t<static_cast<int>(Layout) == static_cast<int>(ColMajor), RightArgType, LeftArgType>;

  using LeftEvaluatorType = TensorEvaluator<EvalLeftArgType, Device>;
  using RightEvaluatorType = TensorEvaluator<EvalRightArgType, Device>;

  static constexpr int LDims =
      internal::array_size<typename TensorEvaluator<EvalLeftArgType, Device>::Dimensions>::value;
  static constexpr int RDims =
      internal::array_size<typename TensorEvaluator<EvalRightArgType, Device>::Dimensions>::value;
  static constexpr int ContractDims = internal::array_size<Indices>::value;
  static constexpr int NumDims = LDims + RDims - 2 * ContractDims;

  using contract_t = array<Index, ContractDims>;
  using left_nocontract_t = array<Index, LDims - ContractDims>;
  using right_nocontract_t = array<Index, RDims - ContractDims>;

  using Dimensions = DSizes<Index, NumDims>;

  EIGEN_STRONG_INLINE TensorContractionEvaluatorBase(const XprType& op, const Device& device)
      : m_leftImpl(choose(Cond<static_cast<int>(Layout) == static_cast<int>(ColMajor)>(), op.lhsExpression(),
                          op.rhsExpression()),
                   device),
        m_rightImpl(choose(Cond<static_cast<int>(Layout) == static_cast<int>(ColMajor)>(), op.rhsExpression(),
                           op.lhsExpression()),
                    device),
        m_device(device),
        m_output_kernel(op.outputKernel()),
        m_result(nullptr) {
    EIGEN_STATIC_ASSERT((static_cast<int>(TensorEvaluator<LeftArgType, Device>::Layout) ==
                         static_cast<int>(TensorEvaluator<RightArgType, Device>::Layout)),
                        YOU_MADE_A_PROGRAMMING_MISTAKE);

    DSizes<Index, LDims> eval_left_dims;
    DSizes<Index, RDims> eval_right_dims;
    array<IndexPair<Index>, ContractDims> eval_op_indices;
    if (static_cast<int>(Layout) == static_cast<int>(ColMajor)) {
      // For ColMajor, we keep using the existing dimensions
      for (int i = 0; i < LDims; i++) {
        eval_left_dims[i] = m_leftImpl.dimensions()[i];
      }
      for (int i = 0; i < RDims; i++) {
        eval_right_dims[i] = m_rightImpl.dimensions()[i];
      }
      // We keep the pairs of contracting indices.
      for (int i = 0; i < ContractDims; i++) {
        eval_op_indices[i].first = op.indices()[i].first;
        eval_op_indices[i].second = op.indices()[i].second;
      }
    } else {
      // For RowMajor, we need to reverse the existing dimensions
      for (int i = 0; i < LDims; i++) {
        eval_left_dims[i] = m_leftImpl.dimensions()[LDims - i - 1];
      }
      for (int i = 0; i < RDims; i++) {
        eval_right_dims[i] = m_rightImpl.dimensions()[RDims - i - 1];
      }
      // We need to flip all the pairs of contracting indices as well as
      // reversing the dimensions.
      for (int i = 0; i < ContractDims; i++) {
        eval_op_indices[i].first = LDims - 1 - op.indices()[ContractDims - 1 - i].second;
        eval_op_indices[i].second = RDims - 1 - op.indices()[ContractDims - 1 - i].first;
      }
    }

    // Check for duplicate axes and make sure the first index in eval_op_indices
    // is increasing. Using O(n^2) sorting is OK since ContractDims is small
    for (int i = 0; i < ContractDims; i++) {
      for (int j = i + 1; j < ContractDims; j++) {
        eigen_assert(eval_op_indices[j].first != eval_op_indices[i].first &&
                     eval_op_indices[j].second != eval_op_indices[i].second && "contraction axes should be unique");
        if (eval_op_indices[j].first < eval_op_indices[i].first) {
          numext::swap(eval_op_indices[j], eval_op_indices[i]);
        }
      }
    }

    array<Index, LDims> lhs_strides;
    lhs_strides[0] = 1;
    for (int i = 0; i < LDims - 1; ++i) {
      lhs_strides[i + 1] = lhs_strides[i] * eval_left_dims[i];
    }

    array<Index, RDims> rhs_strides;
    rhs_strides[0] = 1;
    for (int i = 0; i < RDims - 1; ++i) {
      rhs_strides[i + 1] = rhs_strides[i] * eval_right_dims[i];
    }

    if (m_i_strides.size() > 0) m_i_strides[0] = 1;
    if (m_j_strides.size() > 0) m_j_strides[0] = 1;
    if (m_k_strides.size() > 0) m_k_strides[0] = 1;

    m_i_size = 1;
    m_j_size = 1;
    m_k_size = 1;

    // To compute the dimension, we simply concatenate the non-contracting
    // dimensions of the left and then the right tensor. Additionally, we also
    // compute the strides corresponding to the left non-contracting
    // dimensions and right non-contracting dimensions.
    m_lhs_inner_dim_contiguous = true;
    int dim_idx = 0;
    Index nocontract_idx = 0;

    for (int i = 0; i < LDims; i++) {
      // find if we are contracting on index i of left tensor
      bool contracting = false;
      for (int j = 0; j < ContractDims; j++) {
        if (eval_op_indices[j].first == i) {
          contracting = true;
          break;
        }
      }
      if (!contracting) {
        // add dimension size to output dimensions
        m_dimensions[dim_idx] = eval_left_dims[i];
        m_left_nocontract_strides[nocontract_idx] = lhs_strides[i];
        if (dim_idx != i) {
          m_lhs_inner_dim_contiguous = false;
        }
        // Suppress false-positive GCC -Warray-bounds warning at -O3 when
        // left_nocontract_t has size 1 (the runtime check prevents OOB access).
        EIGEN_DIAGNOSTICS(push)
        EIGEN_DIAGNOSTICS_OFF(disable : 4789, ignored "-Warray-bounds")
        if (nocontract_idx + 1 < internal::array_size<left_nocontract_t>::value) {
          m_i_strides[nocontract_idx + 1] = m_i_strides[nocontract_idx] * eval_left_dims[i];
        } else {
          m_i_size = m_i_strides[nocontract_idx] * eval_left_dims[i];
        }
        EIGEN_DIAGNOSTICS(pop)
        dim_idx++;
        nocontract_idx++;
      }
    }

    nocontract_idx = 0;
    for (int i = 0; i < RDims; i++) {
      bool contracting = false;
      // find if we are contracting on index i of right tensor
      for (int j = 0; j < ContractDims; j++) {
        if (eval_op_indices[j].second == i) {
          contracting = true;
          break;
        }
      }
      if (!contracting) {
        m_dimensions[dim_idx] = eval_right_dims[i];
        EIGEN_DIAGNOSTICS(push)
        EIGEN_DIAGNOSTICS_OFF(disable : 4789, ignored "-Warray-bounds")
        if (nocontract_idx + 1 < internal::array_size<right_nocontract_t>::value) {
          m_j_strides[nocontract_idx + 1] = m_j_strides[nocontract_idx] * eval_right_dims[i];
        } else {
          m_j_size = m_j_strides[nocontract_idx] * eval_right_dims[i];
        }
        EIGEN_DIAGNOSTICS(pop)
        m_right_nocontract_strides[nocontract_idx] = rhs_strides[i];
        dim_idx++;
        nocontract_idx++;
      }
    }

    // Now compute the strides corresponding to the contracting dimensions. We
    // assumed above that non-contracting axes are represented in the same order
    // in the matrix as they are in the tensor. This is not the case for
    // contracting axes. As the contracting axes must be of the same size in
    // each tensor, we'll only look at the first tensor here.
    m_rhs_inner_dim_contiguous = true;
    m_rhs_inner_dim_reordered = false;
    for (int i = 0; i < ContractDims; i++) {
      Index left = eval_op_indices[i].first;
      Index right = eval_op_indices[i].second;

      Index size = eval_left_dims[left];
      eigen_assert(size == eval_right_dims[right] && "Contraction axes must be same size");

      EIGEN_DIAGNOSTICS(push)
      EIGEN_DIAGNOSTICS_OFF(disable : 4789, ignored "-Warray-bounds")
      if (i + 1 < static_cast<int>(internal::array_size<contract_t>::value)) {
        m_k_strides[i + 1] = m_k_strides[i] * size;
      } else {
        m_k_size = m_k_strides[i] * size;
      }
      EIGEN_DIAGNOSTICS(pop)
      m_left_contracting_strides[i] = lhs_strides[left];
      m_right_contracting_strides[i] = rhs_strides[right];

      if (i > 0 && right < eval_op_indices[i - 1].second) {
        m_rhs_inner_dim_reordered = true;
      }
      if (right != i) {
        m_rhs_inner_dim_contiguous = false;
      }
    }

    // After the sort above, eval_op_indices[*].first is in ascending order. The
    // contracted LHS dims are the contiguous prefix of storage iff that prefix
    // is exactly {0, 1, ..., ContractDims-1}. Similarly for the RHS — and we
    // also detect the "contracted dims at the trailing end" case, which
    // matters when LHS/RHS were swapped at the type level for RowMajor inputs.
    m_lhs_contracted_dims_leading = true;
    m_rhs_contracted_dims_leading = true;
    m_rhs_contracted_dims_trailing = true;
    const int rhs_trail_start = RDims - ContractDims;
    for (int i = 0; i < ContractDims; i++) {
      if (eval_op_indices[i].first != i) m_lhs_contracted_dims_leading = false;
      if (eval_op_indices[i].second != i) m_rhs_contracted_dims_leading = false;
      if (eval_op_indices[i].second != rhs_trail_start + i) m_rhs_contracted_dims_trailing = false;
    }

    // If the layout is RowMajor, we need to reverse the m_dimensions
    if (static_cast<int>(Layout) == static_cast<int>(RowMajor)) {
      for (int i = 0, j = NumDims - 1; i < j; i++, j--) {
        numext::swap(m_dimensions[i], m_dimensions[j]);
      }
    }

    // A set of parameters that will allow output kernel to get from output
    // tensor dimensions (i, j) into the original tensor dimensions.
    // TODO(ezhulenev): Add parameters required to infer output tensor index for
    // more complex contractions than 2x2 on internal dimension.
    m_tensor_contraction_params.swapped_arguments = static_cast<int>(Layout) == RowMajor;
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const Dimensions& dimensions() const { return m_dimensions; }

  EIGEN_STRONG_INLINE bool evalSubExprsIfNeeded(EvaluatorPointerType data) {
    m_leftImpl.evalSubExprsIfNeeded(nullptr);
    m_rightImpl.evalSubExprsIfNeeded(nullptr);
    if (data) {
      evalTo(data);
      return false;
    } else {
      m_result = static_cast<EvaluatorPointerType>(m_device.allocate(dimensions().TotalSize() * sizeof(Scalar)));
      evalTo(m_result);
      return true;
    }
  }

#ifdef EIGEN_USE_THREADS
  template <typename EvalSubExprsCallback>
  EIGEN_STRONG_INLINE void evalSubExprsIfNeededAsync(EvaluatorPointerType dest, EvalSubExprsCallback done) {
    m_leftImpl.evalSubExprsIfNeededAsync(nullptr, [this, done, dest](bool) {
      m_rightImpl.evalSubExprsIfNeededAsync(nullptr, [this, done, dest](bool) {
        if (dest) {
          evalToAsync(dest, [done]() { done(false); });
        } else {
          m_result = static_cast<EvaluatorPointerType>(m_device.allocate(dimensions().TotalSize() * sizeof(Scalar)));
          evalToAsync(m_result, [done]() { done(true); });
        }
      });
    });
  }
#endif  // EIGEN_USE_THREADS

  EIGEN_DEVICE_FUNC void evalTo(Scalar* buffer) const {
    static_cast<const Derived*>(this)->template evalProduct<Unaligned>(buffer);
  }

#ifdef EIGEN_USE_THREADS
  template <typename EvalToCallback>
  void evalToAsync(Scalar* buffer, EvalToCallback done) const {
    static_cast<const Derived*>(this)->template evalProductAsync<EvalToCallback, Unaligned>(buffer, std::move(done));
  }
#endif  // EIGEN_USE_THREADS

  template <bool lhs_inner_dim_contiguous, bool rhs_inner_dim_contiguous, bool rhs_inner_dim_reordered, int Alignment>
  void evalProductSequential(Scalar* buffer) const {
    // Gemv-shape contractions (output is a vector) get a direct GEMV kernel
    // call when the operand layout admits one. There are four shapes:
    //
    //   m_j_size == 1: ColMajor inputs. m_leftImpl is the matrix.
    //     (A) lhs_inner_dim_contiguous: contracted dims at non-contiguous end
    //         of LHS. Existing evalGemv via TensorContractionInputMapper.
    //     (B) m_lhs_contracted_dims_leading: contracted dims at contiguous end.
    //         RowMajor view of LHS via const_blas_data_mapper.
    //
    //   m_i_size == 1: RowMajor inputs (lines 407-414 swap LHS/RHS at the type
    //         level, so the matrix is m_rightImpl).
    //     (C) m_rhs_contracted_dims_leading: contracted dims at contiguous end
    //         of (eval-)RHS. RowMajor view of m_rightImpl.
    //     (D) m_rhs_contracted_dims_trailing: contracted dims at non-contiguous
    //         end. ColMajor view of m_rightImpl.
    //
    // Cases B/C/D require direct memory (data() != nullptr) on both impls.
    // Anything else falls back to evalGemm.
    if (this->m_j_size == 1) {
      if (lhs_inner_dim_contiguous) {
        this->template evalGemv<lhs_inner_dim_contiguous, rhs_inner_dim_contiguous, rhs_inner_dim_reordered, Alignment>(
            buffer);
        return;
      }
      if (m_lhs_contracted_dims_leading && m_leftImpl.data() != nullptr && m_rightImpl.data() != nullptr) {
        evalGemvDirect<RowMajor, /*MatrixIsRight=*/false>(buffer);
        return;
      }
    } else if (this->m_i_size == 1 && m_leftImpl.data() != nullptr && m_rightImpl.data() != nullptr) {
      if (m_rhs_contracted_dims_leading) {
        evalGemvDirect<RowMajor, /*MatrixIsRight=*/true>(buffer);
        return;
      }
      if (m_rhs_contracted_dims_trailing) {
        evalGemvDirect<ColMajor, /*MatrixIsRight=*/true>(buffer);
        return;
      }
    }
    this->template evalGemm<lhs_inner_dim_contiguous, rhs_inner_dim_contiguous, rhs_inner_dim_reordered, Alignment>(
        buffer);
  }

  template <bool lhs_inner_dim_contiguous, bool rhs_inner_dim_contiguous, bool rhs_inner_dim_reordered, int Alignment>
#if !defined(EIGEN_HIPCC)
  EIGEN_DEVICE_FUNC
#endif
      void
      evalGemv(Scalar* buffer) const {
    const Index rows = m_i_size;
    const Index cols = m_k_size;

    using LhsScalar = std::remove_const_t<typename EvalLeftArgType::Scalar>;
    using RhsScalar = std::remove_const_t<typename EvalRightArgType::Scalar>;
    using LeftEvaluator = TensorEvaluator<EvalLeftArgType, Device>;
    using RightEvaluator = TensorEvaluator<EvalRightArgType, Device>;
    const int lhs_packet_size = internal::unpacket_traits<typename LeftEvaluator::PacketReturnType>::size;
    const int rhs_packet_size = internal::unpacket_traits<typename RightEvaluator::PacketReturnType>::size;
    const int lhs_alignment = LeftEvaluator::IsAligned ? Aligned : Unaligned;
    const int rhs_alignment = RightEvaluator::IsAligned ? Aligned : Unaligned;
    using LhsMapper = internal::TensorContractionInputMapper<LhsScalar, Index, internal::Lhs, LeftEvaluator,
                                                             left_nocontract_t, contract_t, lhs_packet_size,
                                                             lhs_inner_dim_contiguous, false, lhs_alignment>;

    using RhsMapper =
        internal::TensorContractionInputMapper<RhsScalar, Index, internal::Rhs, RightEvaluator, right_nocontract_t,
                                               contract_t, rhs_packet_size, rhs_inner_dim_contiguous,
                                               rhs_inner_dim_reordered, rhs_alignment>;

    LhsMapper lhs(m_leftImpl, m_left_nocontract_strides, m_i_strides, m_left_contracting_strides, m_k_strides);
    RhsMapper rhs(m_rightImpl, m_right_nocontract_strides, m_j_strides, m_right_contracting_strides, m_k_strides);

    const Scalar alpha(1);
    const Index resIncr(1);

    // zero out the result buffer (which must be of size at least rows * sizeof(Scalar)
    m_device.fill(buffer, buffer + rows, Scalar(0));

    internal::general_matrix_vector_product<Index, LhsScalar, LhsMapper, ColMajor, false, RhsScalar, RhsMapper,
                                            false>::run(rows, cols, lhs, rhs, buffer, resIncr, alpha);

    using OutputMapper = internal::blas_data_mapper<Scalar, Index, ColMajor>;
    m_output_kernel(OutputMapper(buffer, rows), m_tensor_contraction_params, static_cast<Index>(0),
                    static_cast<Index>(0), rows, static_cast<Index>(1));
  }

  // Direct gemv fast path: drives the GEMV kernel against raw tensor memory via
  // const_blas_data_mapper, bypassing TensorContractionInputMapper (which only
  // delivers vectorized loads along the free dim) and the GEBP packing overhead
  // of evalGemm.
  //
  // StorageOrder is the layout of the (rows × cols) matrix view in memory.
  // MatrixIsRight selects which TensorEvaluator holds the matrix and which
  // holds the vector — needed because the constructor swaps LHS/RHS for
  // RowMajor inputs (lines 407-414), so the gemv shape lives in m_rightImpl
  // there. Caller guarantees both impls expose direct memory.
  template <int StorageOrder, bool MatrixIsRight>
#if !defined(EIGEN_HIPCC)
  EIGEN_DEVICE_FUNC
#endif
      void
      evalGemvDirect(Scalar* buffer) const {
    using MatScalar = std::remove_const_t<
        std::conditional_t<MatrixIsRight, typename EvalRightArgType::Scalar, typename EvalLeftArgType::Scalar>>;
    using VecScalar = std::remove_const_t<
        std::conditional_t<MatrixIsRight, typename EvalLeftArgType::Scalar, typename EvalRightArgType::Scalar>>;

    const Index rows = MatrixIsRight ? m_j_size : m_i_size;
    const Index cols = m_k_size;
    // For a row-major (rows × cols) view the row stride is cols; for a
    // column-major view the column stride is rows.
    const Index mat_stride = (StorageOrder == RowMajor) ? cols : rows;

    const MatScalar* mat_data = MatrixIsRight ? m_rightImpl.data() : m_leftImpl.data();
    const VecScalar* vec_data = MatrixIsRight ? m_leftImpl.data() : m_rightImpl.data();
    eigen_assert(mat_data != nullptr && vec_data != nullptr);

    using LhsMapper = internal::const_blas_data_mapper<MatScalar, Index, StorageOrder>;
    using RhsMapper = internal::const_blas_data_mapper<VecScalar, Index, ColMajor>;
    LhsMapper lhs(mat_data, mat_stride);
    RhsMapper rhs(vec_data, /*stride=*/1);

    m_device.fill(buffer, buffer + rows, Scalar(0));

    internal::general_matrix_vector_product<Index, MatScalar, LhsMapper, StorageOrder, false, VecScalar, RhsMapper,
                                            false>::run(rows, cols, lhs, rhs, buffer, /*resIncr=*/1, Scalar(1));

    using OutputMapper = internal::blas_data_mapper<Scalar, Index, ColMajor>;
    m_output_kernel(OutputMapper(buffer, rows), m_tensor_contraction_params, static_cast<Index>(0),
                    static_cast<Index>(0), rows, static_cast<Index>(1));
  }

  template <bool lhs_inner_dim_contiguous, bool rhs_inner_dim_contiguous, bool rhs_inner_dim_reordered, int Alignment>
#if !defined(EIGEN_HIPCC)
  EIGEN_DEVICE_FUNC
#endif
      void
      evalGemm(Scalar* buffer) const {
    // columns in left side, rows in right side
    const Index k = this->m_k_size;
    this->template evalGemmPartial<lhs_inner_dim_contiguous, rhs_inner_dim_contiguous, rhs_inner_dim_reordered,
                                   Alignment, true>(buffer, 0, k, 1);
  }

  template <bool lhs_inner_dim_contiguous, bool rhs_inner_dim_contiguous, bool rhs_inner_dim_reordered, int Alignment>
  EIGEN_DEVICE_FUNC void evalGemmPartialWithoutOutputKernel(Scalar* buffer, Index k_start, Index k_end,
                                                            int num_threads) const {
    evalGemmPartial<lhs_inner_dim_contiguous, rhs_inner_dim_contiguous, rhs_inner_dim_reordered, Alignment,
                    /*use_output_kernel*/ false>(buffer, k_start, k_end, num_threads);
  }

  template <bool lhs_inner_dim_contiguous, bool rhs_inner_dim_contiguous, bool rhs_inner_dim_reordered, int Alignment,
            bool use_output_kernel>
  EIGEN_DEVICE_FUNC void evalGemmPartial(Scalar* buffer, Index k_start, Index k_end, int num_threads) const {
    eigen_assert(k_end >= k_start && k_start >= 0 && k_end <= this->m_k_size);
    // columns in slice on left side, rows on right side
    const Index k_slice = k_end - k_start;

    // rows in left side
    const Index m = this->m_i_size;

    // columns in right side
    const Index n = this->m_j_size;

    // define data mappers for Lhs and Rhs
    using LhsScalar = std::remove_const_t<typename EvalLeftArgType::Scalar>;
    using RhsScalar = std::remove_const_t<typename EvalRightArgType::Scalar>;

    using LeftEvaluator = TensorEvaluator<EvalLeftArgType, Device>;
    using RightEvaluator = TensorEvaluator<EvalRightArgType, Device>;

    const int lhs_packet_size = internal::unpacket_traits<typename LeftEvaluator::PacketReturnType>::size;
    const int rhs_packet_size = internal::unpacket_traits<typename RightEvaluator::PacketReturnType>::size;

    using LhsMapper =
        internal::TensorContractionInputMapper<LhsScalar, Index, internal::Lhs, LeftEvaluator, left_nocontract_t,
                                               contract_t, lhs_packet_size, lhs_inner_dim_contiguous, false, Unaligned>;

    using RhsMapper =
        internal::TensorContractionInputMapper<RhsScalar, Index, internal::Rhs, RightEvaluator, right_nocontract_t,
                                               contract_t, rhs_packet_size, rhs_inner_dim_contiguous,
                                               rhs_inner_dim_reordered, Unaligned>;

    using OutputMapper = internal::blas_data_mapper<Scalar, Index, ColMajor>;

    using TensorContractionKernel =
        internal::TensorContractionKernel<Scalar, LhsScalar, RhsScalar, Index, OutputMapper, LhsMapper, RhsMapper>;

    // initialize data mappers
    LhsMapper lhs(this->m_leftImpl, this->m_left_nocontract_strides, this->m_i_strides,
                  this->m_left_contracting_strides, this->m_k_strides);

    RhsMapper rhs(this->m_rightImpl, this->m_right_nocontract_strides, this->m_j_strides,
                  this->m_right_contracting_strides, this->m_k_strides);

    OutputMapper output(buffer, m);

    // Sizes of the blocks to load in cache. See the Goto paper for details.
    internal::TensorContractionBlocking<Scalar, LhsScalar, RhsScalar, Index, internal::ShardByCol> blocking(
        k_slice, m, n, num_threads);
    const Index kc = blocking.kc();
    const Index mc = numext::mini(m, blocking.mc());
    const Index nc = numext::mini(n, blocking.nc());

    using LhsBlock = typename TensorContractionKernel::LhsBlock;
    using RhsBlock = typename TensorContractionKernel::RhsBlock;

    LhsBlock blockA;
    RhsBlock blockB;

    TensorContractionKernel kernel(m, k_slice, n, mc, kc, nc);

    using BlockMemHandle = typename TensorContractionKernel::BlockMemHandle;
    const BlockMemHandle packed_mem = kernel.allocate(this->m_device, &blockA, &blockB);

    // If a contraction kernel does not support beta, explicitly initialize
    // output buffer with zeroes.
    if (!TensorContractionKernel::HasBeta) {
      this->m_device.fill(buffer, buffer + m * n, Scalar(0));
    }

    for (Index i2 = 0; i2 < m; i2 += mc) {
      const Index actual_mc = numext::mini(i2 + mc, m) - i2;
      for (Index k2 = k_start; k2 < k_end; k2 += kc) {
        // make sure we don't overshoot right edge of left matrix, then pack vertical panel
        const Index actual_kc = numext::mini(k2 + kc, k_end) - k2;
        kernel.packLhs(&blockA, lhs.getSubMapper(i2, k2), actual_kc, actual_mc);

        // If kernel supports beta, there is no need to initialize output
        // buffer with zeroes.
        const Scalar alpha = Scalar(1);
        const Scalar beta = (TensorContractionKernel::HasBeta && k2 == k_start) ? Scalar(0) : Scalar(1);

        // series of horizontal blocks
        for (Index j2 = 0; j2 < n; j2 += nc) {
          // make sure we don't overshoot right edge of right matrix, then pack block
          const Index actual_nc = numext::mini(j2 + nc, n) - j2;
          kernel.packRhs(&blockB, rhs.getSubMapper(k2, j2), actual_kc, actual_nc);

          // call gebp (matrix kernel)
          // The parameters here are copied from Eigen's GEMM implementation
          const OutputMapper output_mapper = output.getSubMapper(i2, j2);
          kernel.invoke(output_mapper, blockA, blockB, actual_mc, actual_kc, actual_nc, alpha, beta);

          // We are done with this [i2, j2] output block.
          if (use_output_kernel && k2 + kc >= k_end) {
            m_output_kernel(output_mapper, m_tensor_contraction_params, i2, j2, actual_mc, actual_nc);
          }
        }
      }
    }

    kernel.deallocate(this->m_device, packed_mem);
  }

  EIGEN_STRONG_INLINE void cleanup() {
    m_leftImpl.cleanup();
    m_rightImpl.cleanup();

    if (m_result != nullptr) {
      m_device.deallocate(m_result);
      m_result = nullptr;
    }
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE CoeffReturnType coeff(Index index) const { return m_result[index]; }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE TensorOpCost costPerCoeff(bool) const {
    return TensorOpCost(sizeof(CoeffReturnType), 0, 0);
  }

  template <int LoadMode>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE PacketReturnType packet(Index index) const {
    return internal::ploadt<PacketReturnType, LoadMode>(m_result + index);
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE EvaluatorPointerType data() const { return m_result; }

  // Required so a contraction can be composed with operators whose own
  // getResourceRequirements() forwards into m_impl (TensorPaddingOp,
  // TensorBroadcastingOp, etc.). Without this, e.g. an expression like
  // `Tensor B = A.contract(C, dims).pad(p)` fails to compile because
  // Pad's BlockAccess is gated on m_impl.RawAccess (which is true here)
  // and instantiating Pad's getResourceRequirements then requires this
  // method on the operand evaluator.
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE internal::TensorBlockResourceRequirements getResourceRequirements() const {
    return internal::TensorBlockResourceRequirements::any();
  }

 protected:
  Dimensions m_dimensions;

  contract_t m_k_strides{};
  contract_t m_left_contracting_strides{};
  contract_t m_right_contracting_strides{};

  bool m_lhs_inner_dim_contiguous;
  bool m_rhs_inner_dim_contiguous;
  bool m_rhs_inner_dim_reordered;
  // Set in the constructor; describe whether the contracted LHS/RHS dims
  // form a contiguous block at the leading or trailing end of storage. Used
  // by evalProductSequential to pick a direct GEMV path when applicable.
  bool m_lhs_contracted_dims_leading;
  bool m_rhs_contracted_dims_leading;
  bool m_rhs_contracted_dims_trailing;

  left_nocontract_t m_i_strides{};
  right_nocontract_t m_j_strides{};
  left_nocontract_t m_left_nocontract_strides{};
  right_nocontract_t m_right_nocontract_strides{};

  Index m_i_size;
  Index m_j_size;
  Index m_k_size;

  TensorContractionParams m_tensor_contraction_params;

  TensorEvaluator<EvalLeftArgType, Device> m_leftImpl;
  TensorEvaluator<EvalRightArgType, Device> m_rightImpl;
  const Device EIGEN_DEVICE_REF m_device;
  OutputKernelType m_output_kernel;
  EvaluatorPointerType m_result;
};

// evaluator for default device
template <typename Indices, typename LeftArgType, typename RightArgType, typename OutputKernelType, typename Device>
struct TensorEvaluator<const TensorContractionOp<Indices, LeftArgType, RightArgType, OutputKernelType>, Device>
    : public TensorContractionEvaluatorBase<
          TensorEvaluator<const TensorContractionOp<Indices, LeftArgType, RightArgType, OutputKernelType>, Device>> {
  using Self = TensorEvaluator<const TensorContractionOp<Indices, LeftArgType, RightArgType, OutputKernelType>, Device>;
  using Base = TensorContractionEvaluatorBase<Self>;

  using XprType = TensorContractionOp<Indices, LeftArgType, RightArgType, OutputKernelType>;
  using Scalar = std::remove_const_t<typename XprType::Scalar>;
  using Index = typename XprType::Index;
  using CoeffReturnType = typename XprType::CoeffReturnType;
  using PacketReturnType = typename PacketType<CoeffReturnType, Device>::type;

  static constexpr int Layout = TensorEvaluator<LeftArgType, Device>::Layout;

  // Most of the code is assuming that both input tensors are ColMajor. If the
  // inputs are RowMajor, we will "cheat" by swapping the LHS and RHS:
  // If we want to compute A * B = C, where A is LHS and B is RHS, the code
  // will pretend B is LHS and A is RHS.
  using EvalLeftArgType = std::conditional_t<Layout == static_cast<int>(ColMajor), LeftArgType, RightArgType>;
  using EvalRightArgType = std::conditional_t<Layout == static_cast<int>(ColMajor), RightArgType, LeftArgType>;

  static constexpr int LDims =
      internal::array_size<typename TensorEvaluator<EvalLeftArgType, Device>::Dimensions>::value;
  static constexpr int RDims =
      internal::array_size<typename TensorEvaluator<EvalRightArgType, Device>::Dimensions>::value;
  static constexpr int ContractDims = internal::array_size<Indices>::value;

  using contract_t = array<Index, ContractDims>;
  using left_nocontract_t = array<Index, LDims - ContractDims>;
  using right_nocontract_t = array<Index, RDims - ContractDims>;

  static constexpr int NumDims = LDims + RDims - 2 * ContractDims;

  // Could we use NumDimensions here?
  using Dimensions = DSizes<Index, NumDims>;

  TensorEvaluator(const XprType& op, const Device& device) : Base(op, device) {}

  template <int Alignment>
  void evalProduct(Scalar* buffer) const {
    internal::tensor_contraction_dispatch(
        [&](auto lhs_c, auto rhs_c, auto rhs_r) {
          this->template evalProductSequential<lhs_c(), rhs_c(), rhs_r(), Alignment>(buffer);
        },
        this->m_lhs_inner_dim_contiguous, this->m_rhs_inner_dim_contiguous, this->m_rhs_inner_dim_reordered);
  }
};

}  // end namespace Eigen

#endif  // EIGEN_TENSOR_TENSOR_CONTRACTION_H
