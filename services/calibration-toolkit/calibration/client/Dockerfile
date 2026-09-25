# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM node:18
WORKDIR /calibration/client/


ENV YARN_IGNORE_NODE=1
COPY .yarn /calibration/client/.yarn
COPY .yarnrc.yml /calibration/client/
COPY package.json /calibration/client/
COPY yarn.lock /calibration/client/
COPY public /calibration/client/
COPY build /calibration/client/
COPY src /calibration/client/
COPY .env* /calibration/client/
COPY target /calibration/client/

RUN yarn set version berry
RUN yarn install
# RUN npx npm-force-resolutions




EXPOSE 3000

# CMD ["npm","start"]r