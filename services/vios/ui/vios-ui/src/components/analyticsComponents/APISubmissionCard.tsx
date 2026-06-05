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
import React from 'react';
import { Card, CardContent, CardHeader, Button, Typography, Stack, CircularProgress } from '@mui/material';
import { useTheme } from '@mui/material/styles';

interface APISubmissionCardProps {
    onSubmit: () => void;
    isSubmitting: boolean;
    canSubmit: boolean;
}

const APISubmissionCard: React.FC<APISubmissionCardProps> = ({ onSubmit, isSubmitting, canSubmit }) => {
    const theme = useTheme();

    if (!canSubmit) {
        return null;
    }

    return (
        <Card
            sx={{
                bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.success.light,
                border: `2px solid ${theme.palette.success.main}`,
            }}
        >
            <CardHeader
                title='API Submission'
                subheader='Submit ROI and Tripwire data to calibration API'
                sx={{
                    '& .MuiCardHeader-title': {
                        color: theme.palette.text.primary,
                        fontWeight: 'bold',
                    },
                    '& .MuiCardHeader-subheader': {
                        color: theme.palette.text.secondary,
                    },
                }}
            />
            <CardContent>
                <Stack spacing={2} alignItems='flex-start'>
                    <Button
                        variant='contained'
                        color='success'
                        onClick={onSubmit}
                        disabled={isSubmitting}
                        startIcon={isSubmitting ? <CircularProgress size={20} /> : null}
                        size='large'
                        sx={{
                            px: 4,
                            py: 1.5,
                            fontSize: '1.1rem',
                            boxShadow: theme.shadows[4],
                            '&:hover': {
                                boxShadow: theme.shadows[8],
                            },
                        }}
                    >
                        {isSubmitting ? 'Submitting...' : 'Submit to API'}
                    </Button>
                    <Typography
                        variant='caption'
                        display='block'
                        sx={{
                            color: theme.palette.mode === 'dark' ? theme.palette.text.secondary : theme.palette.success.dark,
                            fontStyle: 'italic',
                        }}
                    >
                        This will POST the transformed coordinates to /config/calibration/upsert
                    </Typography>
                </Stack>
            </CardContent>
        </Card>
    );
};

export default APISubmissionCard;
