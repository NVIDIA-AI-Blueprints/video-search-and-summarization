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
import React, { useState, useMemo, useEffect } from 'react';
import { Slider, Box, Tooltip, useTheme, useMediaQuery, CircularProgress } from '@mui/material';
import { styled } from '@mui/material/styles';
import { format } from 'date-fns';
import { Timeline } from '../../interfaces/interfaces';

interface EventMarker {
    value: number;
    label: string;
    timestamp: string;
    color: string;
    description: string;
}

interface TimeRangeSliderProps {
    min: string;
    max: string;
    onChange: (range: [string, string]) => void;
    onSingleTimeSelect?: (time: string) => void;
    singleSelectMode?: boolean;
    disabledRange?: Timeline[];
    actualRecordingBounds?: {
        start: string;
        end: string;
    };
    eventMarkers?: EventMarker[];
    onEventMarkerClick?: (timestamp: string) => void;
    onFetchEventImage?: (timestamp: string) => Promise<void>;
    getEventImage?: (imageKey: string) => string | undefined;
    loadingImages?: Record<string, boolean>;
    sensorId?: string;
}

interface ValueLabelProps {
    children: React.ReactElement;
    value: number;
}

const StyledSlider = styled(Slider)(({ theme }) => ({
    color: theme.palette.secondary.main,
    height: 8,
    '& .MuiSlider-thumb': {
        height: 24,
        width: 8,
        backgroundColor: theme.palette.mode === 'dark' ? '#fff' : theme.palette.primary.main,
        border: `2px solid ${theme.palette.mode === 'dark' ? theme.palette.secondary.main : '#fff'}`,
        borderRadius: '4px',
        boxShadow: '0 0 4px rgba(0,0,0,0.3)',
        '&:focus, &:hover, &.Mui-active, &.Mui-focusVisible': {
            boxShadow: '0 0 6px rgba(0,0,0,0.4)',
        },
        '&:before': {
            display: 'none',
        },
    },
    '& .MuiSlider-valueLabel': {
        lineHeight: 1.2,
        fontSize: 12,
        background: 'unset',
        padding: 0,
        width: 32,
        height: 32,
        borderRadius: '50% 50% 50% 0',
        backgroundColor: theme.palette.primary.main,
        transformOrigin: 'bottom left',
        transform: 'translate(50%, -100%) rotate(-45deg) scale(0)',
        '&:before': { display: 'none' },
        '&.MuiSlider-valueLabelOpen': {
            transform: 'translate(50%, -100%) rotate(-45deg) scale(1)',
        },
        '& > *': {
            transform: 'rotate(45deg)',
        },
    },
    '& .MuiSlider-mark': {
        backgroundColor: '#bfbfbf',
        height: 8,
        width: 1,
        // Disable pointer events on base marks so clicks go to our custom markers/labels
        pointerEvents: 'none',
        '&.MuiSlider-markActive': {
            opacity: 1,
            backgroundColor: 'currentColor',
        },
    },
    '& .MuiSlider-rail': {
        backgroundColor: '#bfbfbf',
    },
    '& .MuiSlider-track': {
        backgroundColor: 'theme.palette.primary.main',
        opacity: 0.5,
    },
    '& .MuiSlider-disabled': {
        backgroundColor: '#e0e0e0',
    },
    '& .MuiSlider-markLabel': {
        transform: 'translateX(-50%)',
        whiteSpace: 'nowrap',
        fontSize: '0.75rem',
        // Keep labels interactive (for our custom marker content)
        pointerEvents: 'auto',
        zIndex: 1200,
    },
}));

function ValueLabelComponent({ children, value }: ValueLabelProps) {
    const formattedValue = useMemo(() => {
        try {
            const date = new Date(value);
            if (isNaN(date.getTime())) {
                return 'Invalid Date';
            }
            return format(date, 'HH:mm:ss.SSS');
        } catch (error) {
            return new Date(value).toLocaleString();
        }
    }, [value]);

    return (
        <Tooltip enterTouchDelay={0} placement='top' title={formattedValue}>
            {children}
        </Tooltip>
    );
}

const TimeRangeSlider: React.FC<TimeRangeSliderProps> = ({
    min,
    max,
    onChange,
    onSingleTimeSelect,
    singleSelectMode = false,
    disabledRange = [],
    actualRecordingBounds,
    eventMarkers = [],
    onEventMarkerClick,
    onFetchEventImage,
    getEventImage,
    loadingImages = {},
    sensorId,
}) => {
    const theme = useTheme();
    const isSmallScreen = useMediaQuery(theme.breakpoints.down('sm'));
    const isMediumScreen = useMediaQuery(theme.breakpoints.between('sm', 'md'));

    // Validate and sanitize time values
    const minTime = useMemo(() => {
        const time = new Date(min).getTime();
        return isNaN(time) ? Date.now() : time;
    }, [min]);

    const maxTime = useMemo(() => {
        const time = new Date(max).getTime();
        return isNaN(time) ? Date.now() + 86400000 : time; // fallback to 24 hours later
    }, [max]);

    // Ensure min <= max
    const validatedMinTime = useMemo(() => Math.min(minTime, maxTime), [minTime, maxTime]);
    const validatedMaxTime = useMemo(() => Math.max(minTime, maxTime), [minTime, maxTime]);

    const [values, setValues] = useState<[number, number]>([validatedMinTime, validatedMaxTime]);
    const [singleValue, setSingleValue] = useState<number>(validatedMinTime);

    // Calculate combined disabled ranges (gaps + out-of-bounds)
    const combinedDisabledRanges = useMemo(() => {
        const ranges: Timeline[] = [...(disabledRange || [])];

        if (actualRecordingBounds?.start && actualRecordingBounds?.end) {
            const recordingStart = new Date(actualRecordingBounds.start).getTime();
            const recordingEnd = new Date(actualRecordingBounds.end).getTime();

            // Validate recording bounds
            if (!isNaN(recordingStart) && !isNaN(recordingEnd) && recordingStart <= recordingEnd) {
                // Add range before recording starts (if selected range starts before recording)
                if (validatedMinTime < recordingStart) {
                    ranges.push({
                        startTime: new Date(validatedMinTime).toISOString(),
                        endTime: new Date(Math.min(recordingStart, validatedMaxTime)).toISOString(),
                    });
                }

                // Add range after recording ends (if selected range extends beyond recording)
                if (validatedMaxTime > recordingEnd) {
                    ranges.push({
                        startTime: new Date(Math.max(recordingEnd, validatedMinTime)).toISOString(),
                        endTime: new Date(validatedMaxTime).toISOString(),
                    });
                }
            }
        }

        return ranges;
    }, [disabledRange, actualRecordingBounds, validatedMinTime, validatedMaxTime]);

    // Synchronize internal state with prop changes and reset handle position
    useEffect(() => {
        // Reset range to new boundaries
        setValues([validatedMinTime, validatedMaxTime]);

        // Always reset handle position to start when range changes
        if (singleSelectMode) {
            setSingleValue(validatedMinTime);
        }
    }, [validatedMinTime, validatedMaxTime, singleSelectMode]);

    const handleChange = (event: Event, newValues: number | number[]) => {
        // Check if click originated from an event marker
        const isEventMarkerClick = (target: EventTarget | null): boolean => {
            if (!target || !(target instanceof Element)) return false;

            // Check current element and walk up the DOM tree
            let element: Element | null = target;
            while (element) {
                if (element.getAttribute('data-event-marker') === 'true') {
                    return true;
                }
                element = element.parentElement;
            }
            return false;
        };

        if (isEventMarkerClick(event.target)) {
            return;
        }

        if (singleSelectMode && !Array.isArray(newValues)) {
            const isDisabled = combinedDisabledRanges.some(range => {
                const rangeStart = new Date(range.startTime).getTime();
                const rangeEnd = new Date(range.endTime).getTime();
                return newValues >= rangeStart && newValues <= rangeEnd;
            });

            if (!isDisabled) {
                setSingleValue(newValues);
                onSingleTimeSelect?.(new Date(newValues).toISOString());
            }
        } else if (!singleSelectMode && Array.isArray(newValues)) {
            const [newMin, newMax] = newValues;
            setValues([newMin, newMax]);
            onChange([new Date(newMin).toISOString(), new Date(newMax).toISOString()]);
        }
    };

    const marks = useMemo(() => {
        const numberOfMarks = isSmallScreen ? 2 : isMediumScreen ? 3 : 5;
        const totalTime = validatedMaxTime - validatedMinTime;

        // Generate regular timeline marks
        let regularMarks = [];
        if (totalTime === 0) {
            regularMarks = [
                {
                    value: validatedMinTime,
                    label: format(new Date(validatedMinTime), 'MM/dd HH:mm'),
                },
            ];
        } else {
            regularMarks = Array.from({ length: numberOfMarks }, (_, i) => {
                const value = validatedMinTime + (totalTime / (numberOfMarks - 1)) * i;
                try {
                    return {
                        value: value,
                        label: format(new Date(value), 'MM/dd HH:mm'),
                    };
                } catch (error) {
                    // Fallback if date formatting fails
                    return {
                        value: value,
                        label: new Date(value).toLocaleString(),
                    };
                }
            });
        }

        // Add event markers if provided
        const eventMarksStyled = eventMarkers.map(marker => {
            const imageKey = sensorId ? `${sensorId}-${marker.timestamp}` : marker.timestamp;
            const isLoading = loadingImages[imageKey];
            const imageUrl = getEventImage ? getEventImage(imageKey) : undefined;

            const handleMarkerHover = () => {
                if (onFetchEventImage && !imageUrl && !isLoading) {
                    onFetchEventImage(marker.timestamp);
                }
            };

            const tooltipContent = (
                <Box sx={{ maxWidth: 300, p: 1 }}>
                    <Box sx={{ mb: 1, fontWeight: 'bold' }}>Event at {marker.label}</Box>
                    <Box sx={{ mb: 1, fontSize: '0.875rem', opacity: 0.8 }}>
                        {format(new Date(marker.timestamp), 'MM/dd/yyyy HH:mm:ss')}
                    </Box>
                    <Box sx={{ mb: 1, fontSize: '0.875rem', color: '#ffffff' }}>{marker.description}</Box>
                    <Box sx={{ mb: 1, fontSize: '0.75rem', color: '#ffeb3b', fontStyle: 'italic' }}>Click to seek to event</Box>
                    {isLoading && (
                        <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
                            <CircularProgress size={20} />
                        </Box>
                    )}
                    {imageUrl && (
                        <Box sx={{ mt: 1 }}>
                            <img
                                src={imageUrl}
                                alt='Event preview'
                                style={{
                                    maxWidth: '200px',
                                    maxHeight: '150px',
                                    width: 'auto',
                                    height: 'auto',
                                    borderRadius: '4px',
                                    border: '1px solid #ddd',
                                }}
                            />
                        </Box>
                    )}
                </Box>
            );

            return {
                value: marker.value,
                label: (
                    <Tooltip
                        title={tooltipContent}
                        placement='top'
                        arrow
                        data-event-marker='true'
                        sx={{
                            zIndex: 1200,
                        }}
                        componentsProps={{
                            tooltip: {
                                sx: {
                                    bgcolor: 'rgba(0, 0, 0, 0.9)',
                                    border: '1px solid #333',
                                    borderRadius: 2,
                                    zIndex: 1200,
                                },
                            },
                        }}
                    >
                        <Box
                            onMouseEnter={handleMarkerHover}
                            onClick={event => {
                                event.stopPropagation();
                                onEventMarkerClick?.(marker.timestamp);
                            }}
                            data-event-marker='true'
                            sx={{
                                width: 0,
                                height: 0,
                                borderLeft: '6px solid transparent',
                                borderRight: '6px solid transparent',
                                borderBottom: `12px solid ${marker.color}`,
                                position: 'relative',
                                top: -12,
                                cursor: 'pointer',
                                zIndex: 9999,
                                filter: 'drop-shadow(0 2px 4px rgba(0,0,0,0.3))',
                                '&:hover': {
                                    transform: 'scale(1.3)',
                                    filter: 'drop-shadow(0 3px 6px rgba(0,0,0,0.4))',
                                    borderLeft: '8px solid transparent',
                                    borderRight: '8px solid transparent',
                                    borderBottom: `16px solid #ffeb3b`, // Yellow highlight on hover
                                },
                                '&:active': {
                                    transform: 'scale(1.1)',
                                    borderBottom: '12px solid #d32f2f', // Darker red when clicked
                                },
                                transition: 'all 0.2s ease',
                            }}
                        />
                    </Tooltip>
                ),
            };
        });

        return [...regularMarks, ...eventMarksStyled];
    }, [
        validatedMinTime,
        validatedMaxTime,
        isSmallScreen,
        isMediumScreen,
        eventMarkers,
        onFetchEventImage,
        getEventImage,
        loadingImages,
        sensorId,
    ]);

    const Rail: React.FC<{
        disabledRanges: Timeline[];
        min: number;
        max: number;
    }> = ({ disabledRanges, min, max }) => {
        const theme = useTheme();
        const totalRange = max - min;

        // Prevent division by zero
        if (totalRange === 0) {
            return (
                <div
                    style={{
                        position: 'absolute',
                        width: '100%',
                        height: 8,
                        backgroundColor: theme.palette.primary.main,
                    }}
                />
            );
        }

        return (
            <div
                style={{
                    position: 'absolute',
                    width: '100%',
                    height: 8,
                    backgroundColor: theme.palette.primary.main,
                }}
            >
                {disabledRanges.map((range, index) => {
                    const start = new Date(range.startTime).getTime();
                    const end = new Date(range.endTime).getTime();

                    // Skip invalid ranges
                    if (isNaN(start) || isNaN(end) || start >= end) {
                        return null;
                    }

                    const clampedStart = Math.max(start, min);
                    const clampedEnd = Math.min(end, max);

                    // Skip ranges that don't intersect with current view
                    if (clampedStart >= clampedEnd) {
                        return null;
                    }

                    const startPercent = ((clampedStart - min) / totalRange) * 100;
                    const endPercent = ((clampedEnd - min) / totalRange) * 100;

                    return (
                        <div
                            key={index}
                            style={{
                                position: 'absolute',
                                left: `${Math.max(0, startPercent)}%`,
                                width: `${Math.max(0, endPercent - startPercent)}%`,
                                height: '100%',
                                backgroundColor: theme.palette.error.main,
                            }}
                        />
                    );
                })}
            </div>
        );
    };

    return (
        <Box sx={{ width: '100%', paddingLeft: 3, paddingRight: 3 }}>
            <Box sx={{ px: 3 }}>
                <StyledSlider
                    key={`${validatedMinTime}-${validatedMaxTime}`} // Force re-render when range changes
                    value={singleSelectMode ? singleValue : values}
                    min={validatedMinTime}
                    max={validatedMaxTime}
                    onChange={handleChange}
                    valueLabelDisplay='auto'
                    components={{
                        ValueLabel: ValueLabelComponent,
                        Rail: props => (
                            <Rail {...props} disabledRanges={combinedDisabledRanges} min={validatedMinTime} max={validatedMaxTime} />
                        ),
                    }}
                    marks={marks}
                />
            </Box>
        </Box>
    );
};

export default TimeRangeSlider;
export type { EventMarker, TimeRangeSliderProps };
