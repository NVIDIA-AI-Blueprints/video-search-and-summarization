/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

/**
 * Export all step components
 * Each step component will be implemented separately
 */

// Step components (to be implemented)
export { default as ProjectSetup } from './ProjectSetup';
export { default as VideoConfiguration } from './VideoConfiguration';
export { default as Rectification } from './Rectification';
export { default as Parameters } from './Parameters';
export { default as ManualAlignment } from './ManualAlignment';
export { default as Execute } from './Execute';
export { default as Results } from './Results';

// Types for step components
export interface StepComponentProps {
  project: import('../../types').Project;
  onProjectUpdate: (project: import('../../types').Project) => void;
}

export interface ProjectSetupProps {
  onProjectSelect: (project: import('../../types').Project) => void;
}

