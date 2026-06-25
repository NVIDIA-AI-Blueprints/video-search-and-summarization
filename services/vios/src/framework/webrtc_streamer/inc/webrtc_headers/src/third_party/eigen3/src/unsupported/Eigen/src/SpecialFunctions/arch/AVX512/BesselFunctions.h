// SPDX-FileCopyrightText: The Eigen Authors
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_AVX512_BESSELFUNCTIONS_H
#define EIGEN_AVX512_BESSELFUNCTIONS_H

namespace Eigen {
namespace internal {

EIGEN_INSTANTIATE_BESSEL_FUNCS_F16(Packet16f, Packet16h)
EIGEN_INSTANTIATE_BESSEL_FUNCS_BF16(Packet16f, Packet16bf)

}  // namespace internal
}  // namespace Eigen

#endif  // EIGEN_AVX512_BESSELFUNCTIONS_H
