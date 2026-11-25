# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
#########
# Internal configuration 
# for debugging
DEBUG = False 

# Filters  
AR_FILTER_LOW_BOUND = (-np.inf, -np.inf, -np.inf, -1.0e-6, -1.0e-6, -1.0e-6)
AR_FILTER_HIGH_BOUND = (np.inf, np.inf, np.inf, 1.0e-6, 1.0e-6, 1.0e-6) 
WIDTH_FILTER_LOW_BOUND = (-np.inf, -np.inf, -np.inf, -1.0e-5, -5.0e-5, -np.inf) 
WIDTH_FILTER_HIGH_BOUND = (np.inf, np.inf, np.inf, 1.0e-5, 5.0e-5, np.inf) 
HEIGHT_FILTER_LOW_BOUND = (-np.inf, -np.inf, -np.inf, -5.0e-5, -5.0e-4, -np.inf) 
HEIGHT_FILTER_HIGH_BOUND = (np.inf, np.inf, np.inf, 0.0, 1.0e-4, np.inf)
