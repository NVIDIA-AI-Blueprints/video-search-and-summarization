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
import { alpha, styled, Theme } from '@mui/material/styles';
import { Card, Typography } from '@mui/material';
import React from 'react';
import { WidgetProps } from '../../interfaces/interfaces';

const StyledCard = styled(Card)(
    ({ theme, color = 'primary' }: { theme?: Theme; color: 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning' }) => ({
        padding: theme?.spacing(4) || 0,
        boxShadow: `0 8px 24px ${alpha(theme?.palette[color].main || '#000', 0.15)}`,
        textAlign: 'center',
        color: theme?.palette.text.primary,
        backgroundColor: alpha(theme?.palette[color].light || '#fff', 0.9),
        borderRadius: theme?.spacing(2) || 0,
        transition: 'all 0.3s ease-in-out',
        height: '220px',
        width: '100%',
        maxWidth: '400px',
        margin: '0 auto',
        position: 'relative',
        overflow: 'hidden',
        '&::before': {
            content: '""',
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: '4px',
            background: `linear-gradient(90deg, 
                ${alpha(theme?.palette[color].main || '#000', 0.8)} 0%, 
                ${alpha(theme?.palette[color].light || '#fff', 0.8)} 100%)`,
        },
        '&:hover': {
            transform: 'translateY(-4px)',
            boxShadow: `0 12px 28px ${alpha(theme?.palette[color].main || '#000', 0.25)}`,
            '& > div:first-of-type': {
                transform: 'translateX(-50%) scale(1.1)',
                boxShadow: `0 8px 16px ${alpha(theme?.palette[color].main || '#000', 0.2)}`,
            },
        },
    })
);

const StyledIcon = styled('div')(({ theme, color = 'primary' }) => ({
    position: 'absolute',
    top: theme.spacing(4),
    left: '50%',
    transform: 'translateX(-50%)',
    display: 'flex',
    borderRadius: '50%',
    alignItems: 'center',
    width: theme.spacing(7),
    height: theme.spacing(7),
    justifyContent: 'center',
    transition: 'all 0.3s ease-in-out',
    color: theme.palette.common.black,
    backgroundImage: `linear-gradient(135deg, 
        ${alpha(theme.palette[color as 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'].light, 1)} 0%, 
        ${alpha(theme.palette[color as 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'].light, 0.12)} 100%)`,
    boxShadow: `0 4px 12px ${alpha(theme.palette[color as 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning'].main, 0.15)}`,
    '& svg': {
        width: '45%',
        height: '45%',
        filter: 'none',
        transition: 'transform 0.3s ease-in-out',
    },
}));

const StyledTitle = styled(Typography)(({ theme }) => ({
    position: 'absolute',
    bottom: theme.spacing(4),
    left: '50%',
    transform: 'translateX(-50%)',
    color: theme.palette.common.black,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    fontSize: '0.875rem',
    width: '100%',
    fontWeight: 500,
    opacity: 0.9,
}));

const StyledValue = styled(Typography)(({ theme }) => ({
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    color: theme.palette.common.black,
    fontWeight: 'bold',
    fontSize: '1.75rem',
    lineHeight: 1.2,
    width: '100%',
    textShadow: `0 2px 4px ${alpha(theme.palette.common.black, 0.1)}`,
}));

const formatTotal = (title: string, total: number | string): string => {
    if (title === 'Record size') {
        return `${total} MB`;
    }
    // Add thousand separator for numbers
    if (typeof total === 'number') {
        return total.toLocaleString();
    }
    return String(total);
};

const Widget = ({ title, total, icon, color = 'primary', sx, ...other }: WidgetProps) => {
    return (
        <StyledCard color={color} sx={sx} {...other}>
            <StyledIcon color={color}>{icon}</StyledIcon>
            <StyledValue variant='h3'>{formatTotal(title, total)}</StyledValue>
            <StyledTitle variant='subtitle1'>{title}</StyledTitle>
        </StyledCard>
    );
};

export default Widget;
