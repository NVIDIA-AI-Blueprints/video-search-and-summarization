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
import React, { useState, useEffect, useCallback, useImperativeHandle, forwardRef } from 'react';
import { Box } from '@mui/material';
import { StreamOverlayOptions, StreamCompositeOptions } from 'vst-streaming-lib';
import { StreamType } from 'vst-streaming-lib';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import { Sensor } from '../../interfaces/interfaces';
import { LiveStreamConfig, RGBAColor, ColorMap, EnabledMap } from './types';
import GeneralSettingsSection from './GeneralSettingsSection';
import BboxSettingsSection from './BboxSettingsSection';
import DisplaySettingsSection from './DisplaySettingsSection';
import ProximitySettingsSection from './ProximitySettingsSection';
import ColorConfigSection from './ColorConfigSection';

export interface OverlaySettingsPanelHandle {
    save: () => void;
}

interface OverlaySettingsPanelProps {
    onSettingsChange?: (
        settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions; framerate?: number },
        tag?: string
    ) => void;
    sensors?: Sensor[];
    streamType?: StreamType;
    /** When true, calls onSettingsChange on every state change rather than only on explicit save */
    autoApply?: boolean;
}

const STORAGE_KEY = 'overlaySettings';

function initColorMaps(labels: string[], colorCode: Array<{ [key: string]: RGBAColor }>): { colors: ColorMap; enabled: EnabledMap } {
    const colors: ColorMap = {};
    const enabled: EnabledMap = {};
    labels.forEach(label => {
        const colorObj = colorCode.find(c => Object.keys(c)[0] === label);
        if (colorObj) {
            colors[label] = colorObj[label];
            enabled[label] = true;
        }
    });
    return { colors, enabled };
}

const OverlaySettingsPanel = forwardRef<OverlaySettingsPanelHandle, OverlaySettingsPanelProps>(
    ({ onSettingsChange, sensors, streamType, autoApply = false }, ref) => {
        const [overlayBbox, setOverlayBbox] = useState(true);
        const [framerateValue, setFramerateValue] = useState(15);
        const [includeFloorPlan, setIncludeFloorPlan] = useState(false);
        const [overlayColor, setOverlayColor] = useState('red');
        const [bboxThickness, setBboxThickness] = useState(4);
        const [overlayDebug, setOverlayDebug] = useState(false);
        const [overlayOpacity, setOverlayOpacity] = useState(255);
        const [proximityClass, setProximityClass] = useState<string[]>([]);
        const [entrantClass, setEntrantClass] = useState<string[]>([]);
        const [proximityAreaFactor, setProximityAreaFactor] = useState(1.3);
        const [proximityAnimation, setProximityAnimation] = useState('circleAndLine');
        const [tag, setTag] = useState('');
        const [configData, setConfigData] = useState<LiveStreamConfig | null>(null);
        const [proximityColors, setProximityColors] = useState<ColorMap>({});
        const [bboxColors, setBboxColors] = useState<ColorMap>({});
        const [enabledProximityColors, setEnabledProximityColors] = useState<EnabledMap>({});
        const [enabledBboxColors, setEnabledBboxColors] = useState<EnabledMap>({});
        const [pose, setPose] = useState(false);
        const [needHalo, setNeedHalo] = useState(false);
        const [objectIds, setObjectIds] = useState<string>('');
        const [classType, setClassType] = useState<string[]>([]);
        const [showObjId, setShowObjId] = useState(false);
        const [objIdPosition, setObjIdPosition] = useState<number>(0);
        const [objIdTextColor, setObjIdTextColor] = useState('white');
        const [objIdTextBGColor, setObjIdTextBGColor] = useState('black');

        useEffect(() => {
            const fetchConfig = async () => {
                try {
                    const response = await nvAxios.get(`${config.liveStreamEndpoint}/api/v1/live/configuration`);
                    setConfigData(response.data);
                } catch (error) {
                    LOG.error('Failed to fetch configuration:', error);
                }
            };
            fetchConfig();
        }, []);

        useEffect(() => {
            if (!configData) return;

            const savedSettings = localStorage.getItem(STORAGE_KEY);
            let saved: Record<string, unknown> | null = null;
            try {
                saved = savedSettings ? JSON.parse(savedSettings) : null;
            } catch {
                LOG.error('Failed to parse saved overlay settings from localStorage');
            }

            if (saved) {
                if (saved.bboxColors && Object.keys(saved.bboxColors as object).length > 0) setBboxColors(saved.bboxColors as ColorMap);
                if (saved.proximityColors && Object.keys(saved.proximityColors as object).length > 0)
                    setProximityColors(saved.proximityColors as ColorMap);
                if (saved.enabledBboxColors && Object.keys(saved.enabledBboxColors as object).length > 0)
                    setEnabledBboxColors(saved.enabledBboxColors as EnabledMap);
                if (saved.enabledProximityColors && Object.keys(saved.enabledProximityColors as object).length > 0)
                    setEnabledProximityColors(saved.enabledProximityColors as EnabledMap);

                setOverlayBbox((saved.bboxShowAll ?? saved.needBbox ?? false) as boolean);
                setIncludeFloorPlan((saved.includeFloorPlan ?? false) as boolean);
                setOverlayColor((saved.color ?? 'red') as string);
                setBboxThickness((saved.thickness ?? 4) as number);
                setOverlayDebug((saved.debug ?? false) as boolean);
                setOverlayOpacity((saved.opacity ?? 255) as number);
                setProximityClass((saved.proximityClass ?? []) as string[]);
                setEntrantClass((saved.entrantClass ?? []) as string[]);
                setProximityAreaFactor((saved.proximityAreaFactor ?? 1.3) as number);
                setProximityAnimation((saved.proximityAnimation ?? 'circleAndLine') as string);
                setTag((saved.tag ?? '') as string);
                setPose((saved.pose ?? false) as boolean);
                setNeedHalo((saved.needHalo ?? false) as boolean);
                setFramerateValue((saved.framerateValue ?? 15) as number);
                setObjectIds((saved.objectIds ?? '') as string);
                setClassType((saved.classType ?? []) as string[]);
                setShowObjId((saved.showObjId ?? false) as boolean);
                setObjIdPosition((saved.objIdPosition ?? 0) as number);
                setObjIdTextColor((saved.objIdTextColor ?? 'white') as string);
                setObjIdTextBGColor((saved.objIdTextBGColor ?? 'black') as string);
            } else if (configData.overlayColorCode) {
                if (configData.overlayProximityLabels) {
                    const prox = initColorMaps(configData.overlayProximityLabels, configData.overlayColorCode);
                    setProximityColors(prox.colors);
                    setEnabledProximityColors(prox.enabled);
                }
                if (configData.overlayClassLabels) {
                    const bbox = initColorMaps(configData.overlayClassLabels, configData.overlayColorCode);
                    setBboxColors(bbox.colors);
                    setEnabledBboxColors(bbox.enabled);
                }
                if (configData.overlayClassLabels?.length > 0) {
                    setClassType(configData.overlayClassLabels);
                }
            }
        }, [configData]);

        const buildOverlay = useCallback((): StreamOverlayOptions => {
            const filteredProximityColors = Object.entries(proximityColors).reduce((acc, [label, color]) => {
                if (color.every(val => typeof val === 'number' && !isNaN(val))) acc[label] = color;
                return acc;
            }, {} as ColorMap);
            const filteredBboxColors = Object.entries(bboxColors).reduce((acc, [label, color]) => {
                if (color.every(val => typeof val === 'number' && !isNaN(val))) acc[label] = color;
                return acc;
            }, {} as ColorMap);

            return {
                bbox: {
                    showAll: overlayBbox,
                    objectId: objectIds
                        ? objectIds
                              .split(',')
                              .map(id => parseInt(id.trim()))
                              .filter(id => !isNaN(id))
                        : [],
                    classType,
                    showObjId,
                    objIdPosition,
                    objIdTextColor,
                    objIdTextBGColor,
                },
                color: overlayColor,
                thickness: bboxThickness,
                debug: overlayDebug,
                opacity: overlayOpacity,
                proximityClass,
                entrantClass,
                proximityAreaFactor,
                overlayColorCode: [
                    ...Object.entries(filteredProximityColors)
                        .filter(([label]) => enabledProximityColors[label])
                        .map(([label, color]) => ({ [label]: color })),
                    ...Object.entries(filteredBboxColors)
                        .filter(([label]) => enabledBboxColors[label])
                        .map(([label, color]) => ({ [label]: color })),
                ],
                proximityAnimation,
                ...(streamType === StreamType.Live || streamType === StreamType.Replay ? { pose } : {}),
                needHalo,
            };
        }, [
            overlayBbox,
            objectIds,
            classType,
            showObjId,
            objIdPosition,
            objIdTextColor,
            objIdTextBGColor,
            overlayColor,
            bboxThickness,
            overlayDebug,
            overlayOpacity,
            proximityClass,
            entrantClass,
            proximityAreaFactor,
            proximityColors,
            enabledProximityColors,
            bboxColors,
            enabledBboxColors,
            proximityAnimation,
            pose,
            needHalo,
            streamType,
        ]);

        const buildComposite = useCallback((): StreamCompositeOptions | undefined => {
            if (streamType !== StreamType.VideoWall) return undefined;
            return {
                includeFloorPlan,
                doComposite: true,
                streamIds: (sensors?.map((s: Sensor) => s.streamId ?? s.sensorId).filter(Boolean) as string[]) || [],
                showSensorName: { enable: true, position: [10, 10] as [number, number] },
            };
        }, [streamType, includeFloorPlan, sensors]);

        const persistAndNotify = useCallback(() => {
            const overlay = buildOverlay();
            const composite = buildComposite();

            const filteredProximityColors = Object.entries(proximityColors).reduce((acc, [label, color]) => {
                if (color.every(val => typeof val === 'number' && !isNaN(val))) acc[label] = color;
                return acc;
            }, {} as ColorMap);
            const filteredBboxColors = Object.entries(bboxColors).reduce((acc, [label, color]) => {
                if (color.every(val => typeof val === 'number' && !isNaN(val))) acc[label] = color;
                return acc;
            }, {} as ColorMap);

            const settings = {
                overlay,
                ...(composite && { composite }),
                proximityColors: filteredProximityColors,
                bboxColors: filteredBboxColors,
                enabledProximityColors,
                enabledBboxColors,
                bboxShowAll: overlayBbox,
                includeFloorPlan,
                color: overlayColor,
                thickness: bboxThickness,
                debug: overlayDebug,
                opacity: overlayOpacity,
                proximityClass,
                entrantClass,
                proximityAreaFactor,
                proximityAnimation,
                pose,
                needHalo,
                framerateValue,
                tag,
                objectIds,
                classType,
                showObjId,
                objIdPosition,
                objIdTextColor,
                objIdTextBGColor,
            };

            localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
            const fr = typeof framerateValue === 'number' && !isNaN(framerateValue) && framerateValue > 0 ? framerateValue : undefined;
            onSettingsChange?.({ overlay, ...(composite && { composite }), ...(fr && { framerate: fr }) }, tag);
        }, [
            buildOverlay,
            buildComposite,
            proximityColors,
            bboxColors,
            enabledProximityColors,
            enabledBboxColors,
            overlayBbox,
            includeFloorPlan,
            overlayColor,
            bboxThickness,
            overlayDebug,
            overlayOpacity,
            proximityClass,
            entrantClass,
            proximityAreaFactor,
            proximityAnimation,
            pose,
            needHalo,
            framerateValue,
            tag,
            objectIds,
            classType,
            showObjId,
            objIdPosition,
            objIdTextColor,
            objIdTextBGColor,
            onSettingsChange,
        ]);

        useImperativeHandle(ref, () => ({ save: persistAndNotify }), [persistAndNotify]);

        useEffect(() => {
            if (autoApply) persistAndNotify();
        }, [autoApply, persistAndNotify]);

        const handleProximityColorChange = (label: string, color: RGBAColor) => {
            setProximityColors(prev => ({ ...prev, [label]: color }));
        };
        const handleBboxColorChange = (label: string, color: RGBAColor) => {
            setBboxColors(prev => ({ ...prev, [label]: color }));
        };
        const handleProximityColorToggle = (label: string) => {
            setEnabledProximityColors(prev => ({ ...prev, [label]: !prev[label] }));
        };
        const handleBboxColorToggle = (label: string) => {
            setEnabledBboxColors(prev => ({ ...prev, [label]: !prev[label] }));
        };

        const resetBboxColors = () => {
            if (!configData?.overlayClassLabels || !configData?.overlayColorCode) return;
            const result = initColorMaps(configData.overlayClassLabels, configData.overlayColorCode);
            setBboxColors(result.colors);
            setEnabledBboxColors(result.enabled);
        };

        const resetProximityColors = () => {
            if (!configData?.overlayProximityLabels || !configData?.overlayColorCode) return;
            const result = initColorMaps(configData.overlayProximityLabels, configData.overlayColorCode);
            setProximityColors(result.colors);
            setEnabledProximityColors(result.enabled);
        };

        return (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0, p: 2 }}>
                <GeneralSettingsSection
                    overlayDebug={overlayDebug}
                    setOverlayDebug={setOverlayDebug}
                    pose={pose}
                    setPose={setPose}
                    needHalo={needHalo}
                    setNeedHalo={setNeedHalo}
                    tag={tag}
                    setTag={setTag}
                    framerateValue={framerateValue}
                    setFramerateValue={setFramerateValue}
                    includeFloorPlan={includeFloorPlan}
                    setIncludeFloorPlan={setIncludeFloorPlan}
                    streamType={streamType}
                />

                <BboxSettingsSection
                    overlayBbox={overlayBbox}
                    setOverlayBbox={setOverlayBbox}
                    classType={classType}
                    setClassType={setClassType}
                    objectIds={objectIds}
                    setObjectIds={setObjectIds}
                    showObjId={showObjId}
                    setShowObjId={setShowObjId}
                    objIdPosition={objIdPosition}
                    setObjIdPosition={setObjIdPosition}
                    objIdTextColor={objIdTextColor}
                    setObjIdTextColor={setObjIdTextColor}
                    objIdTextBGColor={objIdTextBGColor}
                    setObjIdTextBGColor={setObjIdTextBGColor}
                    availableClassLabels={configData?.overlayClassLabels}
                />

                <DisplaySettingsSection
                    bboxThickness={bboxThickness}
                    setBboxThickness={setBboxThickness}
                    overlayOpacity={overlayOpacity}
                    setOverlayOpacity={setOverlayOpacity}
                    proximityAreaFactor={proximityAreaFactor}
                    setProximityAreaFactor={setProximityAreaFactor}
                />

                <ProximitySettingsSection
                    proximityClass={proximityClass}
                    setProximityClass={setProximityClass}
                    entrantClass={entrantClass}
                    setEntrantClass={setEntrantClass}
                    proximityAnimation={proximityAnimation}
                    setProximityAnimation={setProximityAnimation}
                    availableClassLabels={configData?.overlayClassLabels}
                />

                {configData && (
                    <>
                        <ColorConfigSection
                            title='Bounding Box Colors'
                            labels={configData.overlayClassLabels || []}
                            colors={bboxColors}
                            enabledColors={enabledBboxColors}
                            onColorChange={handleBboxColorChange}
                            onColorToggle={handleBboxColorToggle}
                            onReset={resetBboxColors}
                        />
                        <ColorConfigSection
                            title='Proximity Colors'
                            labels={configData.overlayProximityLabels || []}
                            colors={proximityColors}
                            enabledColors={enabledProximityColors}
                            onColorChange={handleProximityColorChange}
                            onColorToggle={handleProximityColorToggle}
                            onReset={resetProximityColors}
                        />
                    </>
                )}
            </Box>
        );
    }
);

OverlaySettingsPanel.displayName = 'OverlaySettingsPanel';

export { OverlaySettingsPanel };
export type { OverlaySettingsPanelProps };
export default OverlaySettingsPanel;
