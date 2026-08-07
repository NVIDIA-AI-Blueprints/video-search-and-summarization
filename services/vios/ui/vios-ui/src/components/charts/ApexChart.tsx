/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import React, { useEffect, useRef } from 'react';
import ApexCharts, { ApexOptions } from 'apexcharts';

import { ApexChartController, type ApexChartConfig, type ApexChartInstance } from './ApexChartController';

type ApexChartType = NonNullable<NonNullable<ApexOptions['chart']>['type']>;
type ApexChartSeries = NonNullable<ApexOptions['series']>;

interface ApexChartProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'children'> {
    options: ApexOptions;
    series: ApexChartSeries;
    type?: ApexChartType;
    width?: string | number;
    height?: string | number;
}

const createChart = (element: HTMLElement, options: ApexOptions): ApexChartInstance => new ApexCharts(element, options);

const ApexChart: React.FC<ApexChartProps> = ({ options, series, type = 'line', width = '100%', height = 'auto', ...containerProps }) => {
    const elementRef = useRef<HTMLDivElement>(null);
    const config: ApexChartConfig = { options, series, type, width, height };
    const initialConfigRef = useRef(config);
    const controllerRef = useRef<ApexChartController | null>(null);

    if (!controllerRef.current) {
        controllerRef.current = new ApexChartController(createChart);
    }

    useEffect(() => {
        const element = elementRef.current;
        const controller = controllerRef.current;
        if (!element || !controller) {
            return;
        }

        controller.mount(element, initialConfigRef.current);
        return () => controller.destroy();
    }, []);

    useEffect(() => {
        controllerRef.current?.update(config);
    }, [height, options, series, type, width]);

    return <div {...containerProps} ref={elementRef} />;
};

export default ApexChart;
