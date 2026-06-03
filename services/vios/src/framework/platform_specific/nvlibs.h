/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef NV_LIBS_H
#define NV_LIBS_H

typedef int (*v4l2_ioctl_t) (int , unsigned long int , ...);
typedef int (*v4l2_open_t) (const char *, int, ...);
typedef int (*v4l2_close_t) (int);

class NvLibs
{
public:
  static NvLibs* getInstance();

  v4l2_ioctl_t v4l2_ioctl;
  v4l2_open_t v4l2_open;
  v4l2_close_t v4l2_close;

  bool isError() { return error; }
  bool isV4l2EncPresent ();
  bool isV4l2DecPresent();

private:
  static NvLibs* _instance;
  bool error;
  void* handle_v4l2;

  NvLibs();

  ~NvLibs();
};

#endif
