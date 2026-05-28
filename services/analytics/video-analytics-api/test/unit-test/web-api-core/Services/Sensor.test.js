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

const sinon = require('sinon');
const { expect } = require('chai');
const Sensor = require('../../../../src/web-api-core/Services/Sensor');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Sensor', () => {

    describe('lookup', () => {
        let sensor;
        let elasticDb;
        let elasticsearchStub;

        beforeEach(() => {
            sensor = new Sensor();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return sensorIds when lookup finds results', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                x: 10.5,
                y: 20.5
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { sensorIds: ['sensor1', 'sensor2'] } }
                        ]
                    }
                }
            });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
            expect(result.sensorIds).to.deep.equal(['sensor1', 'sensor2']);
        });

        it('should return empty sensorIds when no results found', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                x: 10,
                y: 20
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
            expect(result.sensorIds).to.be.an('array').that.is.empty;
        });

        it('should return empty sensorIds when index is absent', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                x: 10,
                y: 20
            };

            elasticsearchStub.resolves({ indexAbsent: true });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
            expect(result.sensorIds).to.be.an('array').that.is.empty;
        });

        it('should use default z value of 0 when not provided', async () => {
            const input = {
                place: 'city=abc',
                x: 10,
                y: 20
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { sensorIds: ['sensor1'] } }]
                    }
                }
            });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
            expect(elasticsearchStub.calledOnce).to.be.true;
        });

        it('should round x, y, z values to integers', async () => {
            const input = {
                place: 'city=abc',
                x: 10.7,
                y: 20.3,
                z: 5.9
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { sensorIds: ['sensor1'] } }]
                    }
                }
            });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
        });

        it('should throw BadRequestError when place is missing', async () => {
            const input = {
                x: 10,
                y: 20
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have required properties");
            }
        });

        it('should throw BadRequestError when x is missing', async () => {
            const input = {
                place: 'city=abc',
                y: 20
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have required properties");
            }
        });

        it('should throw BadRequestError when y is missing', async () => {
            const input = {
                place: 'city=abc',
                x: 10
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have required properties");
            }
        });

        it('should throw BadRequestError when place is empty', async () => {
            const input = {
                place: '',
                x: 10,
                y: 20
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('place should have atleast 1 character');
            }
        });

        it('should throw BadRequestError when x is not a number', async () => {
            const input = {
                place: 'city=abc',
                x: 'not-a-number',
                y: 20
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("'x'");
            }
        });

        it('should throw BadRequestError when y is not a number', async () => {
            const input = {
                place: 'city=abc',
                x: 10,
                y: 'not-a-number'
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("'y'");
            }
        });

        it('should throw BadRequestError for additional properties', async () => {
            const input = {
                place: 'city=abc',
                x: 10,
                y: 20,
                extraField: 'invalid'
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Invalid additional Input");
            }
        });

        it('should throw BadRequestError when x is Infinity (caught by schema validation)', async () => {
            const input = {
                place: 'city=abc',
                x: Infinity,
                y: 20
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("'x'");
            }
        });

        it('should throw BadRequestError when y is Infinity (caught by schema validation)', async () => {
            const input = {
                place: 'city=abc',
                x: 10,
                y: Infinity
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("'y'");
            }
        });

        it('should throw BadRequestError when z is Infinity (caught by schema validation)', async () => {
            const input = {
                place: 'city=abc',
                x: 10,
                y: 20,
                z: Infinity
            };

            try {
                await sensor.lookup(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("'z'");
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                place: 'city=abc',
                x: 10,
                y: 20
            };

            try {
                await sensor.lookup(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should handle negative coordinate values', async () => {
            const input = {
                place: 'city=abc',
                x: -10.5,
                y: -20.5,
                z: -5.5
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { sensorIds: ['sensor1'] } }]
                    }
                }
            });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
        });

        it('should handle zero coordinate values', async () => {
            const input = {
                place: 'city=abc',
                x: 0,
                y: 0,
                z: 0
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { sensorIds: ['sensor1'] } }]
                    }
                }
            });

            const result = await sensor.lookup(elasticDb, input);

            expect(result).to.have.property('sensorIds');
        });
    });

});
