/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import React, { useEffect, useState } from 'react';
import { has, isNil } from 'lodash';
import { Box, Button, Modal, Typography } from '@mui/material';
import nvAxios from '../../services/Axios';
import config from '../../config';
import useVSTUIStore from '../../services/StateManagement';
import { logError, logInfo } from '../../utils/misc/Logs';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';
import { VstAdaptorWrapperProps } from '../../interfaces/interfaces';
import { getEMDXEndpoint } from '../../components/videoPlayer/videoPlayerUtils/emdxAPIs';

const VST_ADAPTOR = 'vst';
const MMS_ADAPTOR = 'mms';
const STREAMER_ADAPTOR = 'streamer';

const modalStyle = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: 400,
    bgcolor: 'background.paper',
    border: '2px solid #000',
    boxShadow: 24,
    p: 4,
};

export const VstAdaptorWrapper: React.FC<VstAdaptorWrapperProps> = ({ children }) => {
    const [modalOpen, setModalOpen] = useState(false);
    const setVstAdaptorType = useVSTUIStore(state => state.setVstAdaptorType);
    const setEmdxEndpoint = useVSTUIStore(state => state.setEmdxEndpoint);
    const setVstVersion = useVSTUIStore(state => state.setVstVersion);

    useEffect(() => {
        logInfo('Fetching VST adaptor type and version');

        nvAxios
            .get(`${config.sensorManagementEndpoint}/api/v1/sensor/version`)
            .then(response => {
                if (!isNil(response.data)) {
                    if (has(response.data, 'type') && has(response.data, 'version')) {
                        setVstAdaptorType(response.data.type);
                        setVstVersion(response.data.version);
                        updateSensorsAndStreams();
                    }
                }
            })
            .catch(() => {
                logError('Failed to fetch VST version and type from sensor/version, trying replay/version');
                // Try replay/version as fallback
                nvAxios
                    .get(`${config.replayStreamEndpoint}/api/v1/replay/version`)
                    .then(response => {
                        if (!isNil(response.data)) {
                            if (has(response.data, 'type') && has(response.data, 'version')) {
                                setVstAdaptorType(response.data.type);
                                setVstVersion(response.data.version);
                                updateSensorsAndStreams();
                            }
                        }
                    })
                    .catch(() => {
                        logError('Failed to fetch VST version and type from replay/version, trying live/version');
                        // Try live/version as final fallback
                        nvAxios
                            .get(`${config.liveStreamEndpoint}/api/v1/live/version`)
                            .then(response => {
                                if (!isNil(response.data)) {
                                    if (has(response.data, 'type') && has(response.data, 'version')) {
                                        setVstAdaptorType(response.data.type);
                                        setVstVersion(response.data.version);
                                        updateSensorsAndStreams();
                                    }
                                }
                            })
                            .catch(() => {
                                logError(
                                    'Failed to fetch VST version and type from all endpoints: sensor/version, replay/version, and live/version'
                                );
                                handleOpen();
                            });
                    });
            });
        getEMDXEndpoint().then(endpoint => {
            setEmdxEndpoint(endpoint);
        });
    }, [setEmdxEndpoint, setVstAdaptorType, setVstVersion]);

    const handleOpen = () => setModalOpen(true);
    const handleClose = () => setModalOpen(false);

    const handleAdaptorSelection = (selectedAdaptor: string) => {
        setVstAdaptorType(selectedAdaptor);
        setVstVersion('Not available');
        handleClose();
    };

    const ChoiceModal = () => {
        return (
            <Modal open={modalOpen} onClose={handleClose}>
                <Box sx={modalStyle}>
                    <Typography variant='h6' component='h2'>
                        Select Adaptor type
                    </Typography>
                    <Typography sx={{ mt: 2 }}>Failed to fetch adaptor type, select manually.</Typography>
                    <Box
                        sx={{
                            mt: 2,
                            display: 'flex',
                            justifyContent: 'space-between',
                            gap: 1,
                        }}
                    >
                        <Button variant='contained' color='primary' onClick={() => handleAdaptorSelection(VST_ADAPTOR)}>
                            VST
                        </Button>
                        <Button variant='contained' color='primary' onClick={() => handleAdaptorSelection(MMS_ADAPTOR)}>
                            MMS
                        </Button>
                        <Button variant='contained' color='primary' onClick={() => handleAdaptorSelection(STREAMER_ADAPTOR)}>
                            NV-STREAMER
                        </Button>
                    </Box>
                </Box>
            </Modal>
        );
    };

    return (
        <Box>
            {!modalOpen && <Box>{children}</Box>}
            <ChoiceModal />
        </Box>
    );
};
