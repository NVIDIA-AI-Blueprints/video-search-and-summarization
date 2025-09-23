# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

FROM nvcr.io/nvidia/blueprint/nv-cv-event-detector:2.4.0

RUN rm /opt/nvidia/nv-cv-event-detector/*.py
RUN rm /opt/nvidia/nv-cv-event-detector/*.sh

COPY *.py /opt/nvidia/nv-cv-event-detector/
COPY gst-plugins/python/*.py /opt/nvidia/deepstream/deepstream/service-maker/gst-plugins/python/
COPY *.txt /opt/nvidia/nv-cv-event-detector/
COPY *.sh /opt/nvidia/nv-cv-event-detector/
COPY via_tracker_config_fast.yml /opt/nvidia/nv-cv-event-detector/

WORKDIR /opt/nvidia/nv-cv-event-detector/
ENTRYPOINT ["/opt/nvidia/nv-cv-event-detector/start_nv_cv_event_detector.sh"]
