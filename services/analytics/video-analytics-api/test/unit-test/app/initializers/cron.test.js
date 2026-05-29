/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

const { expect } = require('chai');
const sinon = require('sinon');
const path = require('path');
const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

describe('Cron Initializer', () => {
    let cache;
    let cron;
    let sandbox;
    let clock;
    let elastic;
    let configManagerObject;
    let logger;
    let configManagerConstructor;

    const buildBootstrapConfig = (configValues) => ({
        server: {
            configs: new Map(Object.entries(configValues))
        }
    });

    beforeEach(() => {
        delete require.cache[require.resolve('../../../../src/app/initializers/cache')];
        delete require.cache[require.resolve('../../../../src/app/initializers/cron')];

        sandbox = sinon.createSandbox();
        logger = {
            error: sandbox.stub()
        };
        const winston = require(require.resolve('winston', {
            paths: [path.resolve(__dirname, '../../../../src/app/initializers')]
        }));
        sandbox.stub(winston, 'createLogger').returns(logger);

        clock = sandbox.useFakeTimers({
            shouldClearNativeTimers: true
        });
        elastic = {};
        configManagerObject = {
            runConfigStatusTimeoutCheck: sandbox.stub()
        };
        configManagerConstructor = sandbox.stub().returns(configManagerObject);

        cache = require('../../../../src/app/initializers/cache');
        cron = proxyquire('../../../../src/app/initializers/cron', {
            './cache': cache,
            '@nvidia-mdx/web-api-core': {
                Services: {
                    ConfigManager: configManagerConstructor
                }
            }
        });
    });

    afterEach(() => {
        cache.flushAll();
        sandbox.restore();
    });

    it('should schedule config status timeout checks with configured settings', () => {
        cache.set("bootstrap-config", buildBootstrapConfig({
            configStatusTimeoutMs: '30000',
            configStatusTimeoutCheckFrequencyMs: '1000'
        }));

        cron.startConfigStatusTimeoutCheck(elastic);

        expect(cache.get("configStatusTimeoutMs")).to.equal(30000);
        expect(cache.get("configStatusTimeoutCheckFrequencyMs")).to.equal(1000);
        expect(configManagerConstructor.calledOnce).to.be.true;

        clock.tick(999);
        expect(configManagerObject.runConfigStatusTimeoutCheck.notCalled).to.be.true;

        clock.tick(1);
        expect(configManagerObject.runConfigStatusTimeoutCheck.calledOnceWithExactly(elastic, 30000)).to.be.true;
    });

    it('should use the default config status timeout when unset', () => {
        cache.set("bootstrap-config", buildBootstrapConfig({
            configStatusTimeoutCheckFrequencyMs: '5000'
        }));

        cron.startConfigStatusTimeoutCheck(elastic);

        expect(cache.get("configStatusTimeoutMs")).to.equal(15000);

        clock.tick(5000);
        expect(configManagerObject.runConfigStatusTimeoutCheck.calledOnceWithExactly(elastic, 15000)).to.be.true;
    });

    it('should log an error and skip scheduling for invalid check frequency', () => {
        cache.set("bootstrap-config", buildBootstrapConfig({
            configStatusTimeoutMs: '30000',
            configStatusTimeoutCheckFrequencyMs: '0'
        }));

        cron.startConfigStatusTimeoutCheck(elastic);

        expect(logger.error.calledOnceWithExactly('[CONFIG STATUS ERROR] Invalid config timeout settings: configStatusTimeoutMs=30000, configStatusTimeoutCheckFrequencyMs=0.')).to.be.true;
        expect(cache.get("configStatusTimeoutCheckFrequencyMs")).to.be.undefined;

        clock.tick(1000);
        expect(configManagerObject.runConfigStatusTimeoutCheck.notCalled).to.be.true;
    });

    it('should log an error and skip scheduling when check frequency is missing', () => {
        cache.set("bootstrap-config", buildBootstrapConfig({
            configStatusTimeoutMs: '30000'
        }));

        cron.startConfigStatusTimeoutCheck(elastic);

        expect(logger.error.calledOnceWithExactly('[CONFIG STATUS ERROR] Invalid config timeout settings: configStatusTimeoutMs=30000, configStatusTimeoutCheckFrequencyMs=undefined.')).to.be.true;
        expect(cache.get("configStatusTimeoutCheckFrequencyMs")).to.be.undefined;

        clock.tick(1000);
        expect(configManagerObject.runConfigStatusTimeoutCheck.notCalled).to.be.true;
    });

    it('should exit for invalid config status timeout', () => {
        cache.set("bootstrap-config", buildBootstrapConfig({
            configStatusTimeoutMs: 'abc',
            configStatusTimeoutCheckFrequencyMs: '1000'
        }));
        const exitStub = sandbox.stub(process, 'exit').callsFake((code) => {
            throw new Error(`process.exit ${code}`);
        });

        expect(() => cron.startConfigStatusTimeoutCheck(elastic)).to.throw('process.exit 1');
        expect(logger.error.calledOnceWithExactly('[CONFIG STATUS ERROR] Invalid configStatusTimeoutMs=abc. Expected a non-negative integer.')).to.be.true;
        expect(exitStub.calledOnceWithExactly(1)).to.be.true;
    });

    it('should log an error and skip scheduling when bootstrap config is missing from cache', () => {
        cron.startConfigStatusTimeoutCheck(elastic);

        expect(logger.error.calledOnceWithExactly('[CONFIG STATUS ERROR] Missing bootstrap-config in cache.')).to.be.true;

        clock.tick(1000);
        expect(configManagerObject.runConfigStatusTimeoutCheck.notCalled).to.be.true;
    });
});
