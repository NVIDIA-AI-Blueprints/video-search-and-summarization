// SPDX-FileCopyrightText: The Eigen Authors
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_TEST_BUG1213_H
#define EIGEN_TEST_BUG1213_H

#include <Eigen/Core>

template <typename T, int dim>
bool bug1213_2(const Eigen::Matrix<T, dim, 1>& x);

bool bug1213_1(const Eigen::Vector3f& x);

#endif  // EIGEN_TEST_BUG1213_H
