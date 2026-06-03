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
import React, { useMemo, useState } from 'react';
import { ThemeProvider, createTheme, PaletteMode } from '@mui/material';
import CssBaseline from '@mui/material/CssBaseline';
import ThemeContext from './themeContext';
import { Paper, Backdrop, CircularProgress } from '@mui/material';
import useSettings from '../hooks/useSettings';

/**
 * NVIDIA brand color palette
 * Primary colors based on NVIDIA's signature green
 */
const PRIMARY_COLORS = {
    MAIN: '#76B900', // Base NVIDIA green
    LIGHT: '#8ED100', // Elevated state, hover
    DARK: '#5A8C00', // Pressed state, active
    LIGHTER: '#A4E000', // Focus state, highlight
    DARKER: '#2D5F00', // Selected state, emphasis
    CONTRAST_TEXT: '#FFFFFF',
};

/**
 * Secondary color palette
 * Professional grayscale for UI elements and text
 */
const SECONDARY_COLORS_LIGHT = {
    MAIN: '#2C2C2C', // Primary text, icons
    LIGHT: '#4A4A4A', // Secondary text, borders
    DARK: '#1A1A1A', // Tertiary text, dividers
    LIGHTER: '#6E6E6E', // Disabled state
    DARKER: '#000000', // Emphasis, strong contrast
    CONTRAST_TEXT: '#FFFFFF',
};

const SECONDARY_COLORS_DARK = {
    MAIN: '#E0E0E0', // Primary text, icons
    LIGHT: '#F5F5F5', // Secondary text, borders
    DARK: '#B0B0B0', // Tertiary text, dividers
    LIGHTER: '#9E9E9E', // Disabled state
    DARKER: '#FFFFFF', // Emphasis, strong contrast
    CONTRAST_TEXT: '#000000',
};

/**
 * Global color tokens
 * System-wide color definitions for consistent theming
 */
const COLORS = {
    LIGHT_BACKGROUND: '#F8F8F8', // Light theme background
    DARK_BACKGROUND: '#121212', // Dark theme background
    LIGHT_PAPER: '#FFFFFF', // Light theme surface
    DARK_PAPER: '#1E1E1E', // Dark theme surface
    LIGHT_TEXT_PRIMARY: '#2C2C2C', // Light theme primary text
    DARK_TEXT_PRIMARY: '#F5F5F5', // Dark theme primary text
    LIGHT_TEXT_SECONDARY: '#4A4A4A', // Light theme secondary text
    DARK_TEXT_SECONDARY: '#B0B0B0', // Dark theme secondary text
    SUCCESS: '#76B900', // Success state, positive feedback
    ERROR: '#D32F2F', // Error state, negative feedback
    WARNING: '#ED6C02', // Warning state, caution
    INFO: '#0288D1', // Information state, neutral feedback
};

/**
 * Typography configuration
 * Font family and weight definitions
 */
const TYPOGRAPHY = {
    FONT_FAMILY: '"DIN Pro", "Roboto", "Helvetica", "Arial", sans-serif',
    H1_WEIGHT: 700,
    H2_WEIGHT: 600,
};

/**
 * Shape configuration
 * Global border radius for consistent component styling
 */
const SHAPE = {
    BORDER_RADIUS: 8,
};

const THEME_TRANSITION_DELAY = 100; // Delay for theme transition in milliseconds

interface ChildrenProps {
    children?: React.ReactNode;
}

// Add these new utility functions
const getBackgroundColor = (mode: PaletteMode) => (mode === 'light' ? COLORS.LIGHT_BACKGROUND : COLORS.DARK_BACKGROUND);

const getTextColor = (mode: PaletteMode) => (mode === 'light' ? COLORS.LIGHT_TEXT_PRIMARY : COLORS.DARK_TEXT_PRIMARY);

const getShadowColor = (mode: PaletteMode) => (mode === 'light' ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)');

const getSecondaryColors = (mode: PaletteMode) => (mode === 'light' ? SECONDARY_COLORS_LIGHT : SECONDARY_COLORS_DARK);

const ThemeContextProvider: React.FC<ChildrenProps> = ({ children }) => {
    const { settings } = useSettings();
    const [loading, setLoading] = useState(false);

    const colorToggleMode = useMemo(
        () => ({
            toggleColorMode: () => {
                setLoading(true);
                setTimeout(() => {
                    setLoading(false);
                }, THEME_TRANSITION_DELAY);
            },
        }),
        []
    );

    const theme = useMemo(
        () =>
            createTheme({
                palette: {
                    mode: settings.theme as PaletteMode,
                    primary: {
                        main: PRIMARY_COLORS.MAIN,
                        light: PRIMARY_COLORS.LIGHT,
                        dark: PRIMARY_COLORS.DARK,
                        contrastText: PRIMARY_COLORS.CONTRAST_TEXT,
                    },
                    secondary: {
                        main: getSecondaryColors(settings.theme).MAIN,
                        light: getSecondaryColors(settings.theme).LIGHT,
                        dark: getSecondaryColors(settings.theme).DARK,
                        contrastText: getSecondaryColors(settings.theme).CONTRAST_TEXT,
                    },
                    background: {
                        default: getBackgroundColor(settings.theme),
                        paper: settings.theme === 'light' ? COLORS.LIGHT_PAPER : COLORS.DARK_PAPER,
                    },
                    text: {
                        primary: getTextColor(settings.theme),
                        secondary: settings.theme === 'light' ? COLORS.LIGHT_TEXT_SECONDARY : COLORS.DARK_TEXT_SECONDARY,
                    },
                    success: {
                        main: COLORS.SUCCESS,
                        light: PRIMARY_COLORS.LIGHT,
                        dark: PRIMARY_COLORS.DARK,
                        contrastText: PRIMARY_COLORS.CONTRAST_TEXT,
                    },
                    error: {
                        main: COLORS.ERROR,
                        light: '#EF5350',
                        dark: '#C62828',
                        contrastText: '#FFFFFF',
                    },
                    warning: {
                        main: COLORS.WARNING,
                        light: '#FFB74D',
                        dark: '#E65100',
                        contrastText: '#000000',
                    },
                    info: {
                        main: COLORS.INFO,
                        light: '#4FC3F7',
                        dark: '#01579B',
                        contrastText: '#FFFFFF',
                    },
                },
                typography: {
                    fontFamily: TYPOGRAPHY.FONT_FAMILY,
                    h1: {
                        fontWeight: TYPOGRAPHY.H1_WEIGHT,
                        fontSize: '2.5rem',
                        lineHeight: 1.2,
                    },
                    h2: {
                        fontWeight: TYPOGRAPHY.H2_WEIGHT,
                        fontSize: '2rem',
                        lineHeight: 1.3,
                    },
                    h3: {
                        fontSize: '1.75rem',
                        lineHeight: 1.4,
                    },
                    h4: {
                        fontSize: '1.5rem',
                        lineHeight: 1.4,
                    },
                    h5: {
                        fontSize: '1.25rem',
                        lineHeight: 1.4,
                    },
                    h6: {
                        fontSize: '1rem',
                        lineHeight: 1.4,
                    },
                },
                shape: { borderRadius: SHAPE.BORDER_RADIUS },
                components: {
                    MuiCssBaseline: {
                        styleOverrides: {
                            body: {
                                transition: 'all 0.3s ease-in-out',
                            },
                            '*::-webkit-scrollbar': {
                                width: '8px',
                                height: '8px',
                            },
                            '*::-webkit-scrollbar-track': {
                                background: settings.theme === 'light' ? COLORS.LIGHT_BACKGROUND : COLORS.DARK_BACKGROUND,
                                borderRadius: '4px',
                            },
                            '*::-webkit-scrollbar-thumb': {
                                background: settings.theme === 'light' ? PRIMARY_COLORS.LIGHT : PRIMARY_COLORS.DARK,
                                borderRadius: '4px',
                                '&:hover': {
                                    background: settings.theme === 'light' ? PRIMARY_COLORS.MAIN : PRIMARY_COLORS.DARKER,
                                },
                            },
                            '*': {
                                scrollbarWidth: 'thin',
                                scrollbarColor: `${settings.theme === 'light' ? PRIMARY_COLORS.LIGHT : PRIMARY_COLORS.DARK} ${settings.theme === 'light' ? COLORS.LIGHT_BACKGROUND : COLORS.DARK_BACKGROUND}`,
                            },
                        },
                    },
                    MuiButton: {
                        styleOverrides: {
                            root: {
                                textTransform: 'none',
                                fontWeight: 500,
                                borderRadius: SHAPE.BORDER_RADIUS,
                                padding: '8px 24px',
                                boxShadow: 'none',
                                '&:hover': {
                                    boxShadow: '0 4px 6px rgba(0,0,0,0.1)',
                                    transform: 'translateY(-1px)',
                                },
                            },
                            containedPrimary: {
                                backgroundColor: PRIMARY_COLORS.MAIN,
                                color: PRIMARY_COLORS.CONTRAST_TEXT,
                                '&:hover': {
                                    backgroundColor: PRIMARY_COLORS.DARK,
                                    color: PRIMARY_COLORS.CONTRAST_TEXT,
                                },
                            },
                        },
                    },
                    MuiCard: {
                        styleOverrides: {
                            root: {
                                boxShadow: `0 2px 4px ${getShadowColor(settings.theme)}`,
                                borderRadius: SHAPE.BORDER_RADIUS,
                                transition: 'all 0.3s ease-in-out',
                                '&:hover': {
                                    boxShadow: `0 4px 8px ${getShadowColor(settings.theme)}`,
                                },
                            },
                        },
                    },
                    MuiPaper: {
                        styleOverrides: {
                            root: {
                                backgroundColor: settings.theme === 'light' ? COLORS.LIGHT_PAPER : COLORS.DARK_PAPER,
                                borderRadius: SHAPE.BORDER_RADIUS,
                            },
                        },
                    },
                    MuiAppBar: {
                        styleOverrides: {
                            root: {
                                backgroundColor: settings.theme === 'light' ? '#FFFFFF' : '#121212',
                                color: settings.theme === 'light' ? COLORS.LIGHT_TEXT_PRIMARY : COLORS.DARK_TEXT_PRIMARY,
                                boxShadow: 'none',
                                borderBottom: `1px solid ${getShadowColor(settings.theme)}`,
                            },
                        },
                    },
                },
            }),
        [settings.theme]
    );

    return (
        <ThemeContext.Provider value={colorToggleMode}>
            <ThemeProvider theme={theme}>
                <CssBaseline />
                <Paper
                    sx={{
                        minHeight: '100vh',
                        transition: 'all 0.3s ease-in-out',
                    }}
                >
                    {children}
                </Paper>
                <Backdrop
                    sx={{
                        color: '#fff',
                        zIndex: theme => theme.zIndex.drawer + 1,
                    }}
                    open={loading}
                >
                    <CircularProgress color='inherit' />
                </Backdrop>
            </ThemeProvider>
        </ThemeContext.Provider>
    );
};

export default ThemeContextProvider;
