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
import type { ApexOptions } from 'apexcharts';
import isEqual from 'lodash/isEqual.js';

export type ApexChartConfig = {
    options: ApexOptions;
    series: NonNullable<ApexOptions['series']>;
    type: NonNullable<ApexOptions['chart']>['type'];
    width: string | number;
    height: string | number;
};

export type ApexChartInstance = {
    render: () => unknown;
    updateSeries: (series: ApexChartConfig['series']) => unknown;
    updateOptions: (options: ApexOptions) => unknown;
    destroy: () => unknown;
};

type ApexChartFactory = (element: HTMLElement, options: ApexOptions) => ApexChartInstance;

const buildOptions = (config: ApexChartConfig): ApexOptions => ({
    ...config.options,
    chart: {
        ...config.options.chart,
        type: config.type,
        width: config.width,
        height: config.height,
    },
    series: config.series,
});

export class ApexChartController {
    private readonly factory: ApexChartFactory;
    private chart?: ApexChartInstance;
    private config?: ApexChartConfig;

    constructor(factory: ApexChartFactory) {
        this.factory = factory;
    }

    mount(element: HTMLElement, config: ApexChartConfig) {
        this.chart = this.factory(element, buildOptions(config));
        this.config = config;
        this.chart.render();
    }

    update(config: ApexChartConfig) {
        if (!this.chart || !this.config) {
            return;
        }

        const seriesChanged = !isEqual(this.config.series, config.series);
        const optionsChanged =
            !isEqual(this.config.options, config.options) ||
            this.config.type !== config.type ||
            this.config.width !== config.width ||
            this.config.height !== config.height;

        if (optionsChanged) {
            this.chart.updateOptions(buildOptions(config));
        } else if (seriesChanged) {
            this.chart.updateSeries(config.series);
        }

        this.config = config;
    }

    destroy() {
        if (!this.chart) {
            return;
        }

        this.chart.destroy();
        this.chart = undefined;
        this.config = undefined;
    }
}
