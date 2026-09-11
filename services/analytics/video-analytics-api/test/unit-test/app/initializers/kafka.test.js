/*
 * SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

'use strict';

const { expect } = require('chai');
const sinon = require('sinon');
const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

describe('Kafka Initializer', () => {
    const kafkaInitializerPath = '../../../../src/app/initializers/kafka';
    let originalStreamType;

    beforeEach(() => {
        originalStreamType = process.env.STREAM_TYPE;
    });

    afterEach(() => {
        if (originalStreamType === undefined) {
            delete process.env.STREAM_TYPE;
        } else {
            process.env.STREAM_TYPE = originalStreamType;
        }
    });

    const loadKafkaInitializer = ({ streamType, brokers }) => {
        if (streamType === undefined) {
            delete process.env.STREAM_TYPE;
        } else {
            process.env.STREAM_TYPE = streamType;
        }

        const kafkaInstance = {};
        const kafkaConstructor = sinon.stub().returns(kafkaInstance);
        const cache = {
            get: sinon.stub().withArgs('bootstrap-config').returns({
                kafka: {
                    brokers,
                    retries: 15
                }
            })
        };

        const kafka = proxyquire(kafkaInitializerPath, {
            './cache': cache,
            '@nvidia-mdx/web-api-core': {
                Utils: {
                    Kafka: kafkaConstructor
                }
            }
        });

        return { kafka, kafkaConstructor, kafkaInstance };
    };

    it('should not create a Kafka client in Redis mode when brokers are configured', () => {
        const { kafka, kafkaConstructor } = loadKafkaInitializer({
            streamType: 'redis',
            brokers: ['kafka:29092']
        });

        expect(kafka).to.be.null;
        expect(kafkaConstructor.notCalled).to.be.true;
    });

    it('should create a Kafka client in Kafka mode when brokers are configured', () => {
        const { kafka, kafkaConstructor, kafkaInstance } = loadKafkaInitializer({
            streamType: 'kafka',
            brokers: ['kafka:29092']
        });

        expect(kafka).to.equal(kafkaInstance);
        expect(kafkaConstructor.calledOnce).to.be.true;
        expect(kafkaConstructor.firstCall.args[0]).to.deep.include({
            brokers: ['kafka:29092'],
            retry: { retries: 15 }
        });
    });

    it('should default to Kafka mode when STREAM_TYPE is unset', () => {
        const { kafka, kafkaConstructor, kafkaInstance } = loadKafkaInitializer({
            streamType: undefined,
            brokers: ['kafka:29092']
        });

        expect(kafka).to.equal(kafkaInstance);
        expect(kafkaConstructor.calledOnce).to.be.true;
    });
});
