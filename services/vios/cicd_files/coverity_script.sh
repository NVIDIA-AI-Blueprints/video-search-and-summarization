#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2020-2020 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

set -e
ARCH="aarch64"
if [[ "${CROSS_COMPILE}" == "" ]]; then
	ARCH="x86"
fi
export PATH=$PATH:/coverity/bin
SCRIPT_DIR=$(dirname $0)
TOP=${TOP:-$(realpath ${SCRIPT_DIR}/..)}
cd ${TOP}
OUT=${TOP}/out
COVERITY_OUT=${OUT}/coverity
COVERITY_CONFIG=${COVERITY_OUT}/coverity/config.xml
COVERITY_EMIT_DIR=${COVERITY_OUT}/emit
COVERITY_REPORT_DIR=${OUT}/coverity_reports
rm -rf ${COVERITY_OUT}
mkdir -p ${COVERITY_OUT}
rm -rf ${COVERITY_REPORT_DIR}
mkdir -p ${COVERITY_REPORT_DIR}
make clean
cov-configure --config ${COVERITY_CONFIG} --comptype gcc --compiler ${CROSS_COMPILE}g++ --template

cov-build --config ${COVERITY_CONFIG} --dir ${COVERITY_EMIT_DIR} make -j12

cov-analyze --dir ${COVERITY_EMIT_DIR} --all --enable AUDIT.SPECULATIVE_EXECUTION_DATA_LEAK --checker-option AUDIT.SPECULATIVE_EXECUTION_DATA_LEAK:max_sensitive_read_size:"2" --concurrency --enable-fnptr --enable-virtual --enable-constraint-fpp --enable ATOMICITY -j auto

cov-format-errors --dir ${COVERITY_EMIT_DIR} --html-output ${COVERITY_REPORT_DIR}

cov-commit-defects --certs /CoverityAutomation/SSLCert/ca-chain.crt --on-new-cert trust --auth-key-file /CoverityAutomation/CoreDVSScan/auth-key.txt  --host txcovline --https-port 8443 --stream  vms_defect_${ARCH} --dir ${COVERITY_EMIT_DIR} --description "VMS coverity reports for ${ARCH} build"

echo "Reports are generated at ${COVERITY_REPORT_DIR}"
