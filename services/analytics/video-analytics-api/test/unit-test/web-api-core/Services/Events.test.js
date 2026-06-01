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
const Events = require('../../../../src/web-api-core/Services/Events');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Events', () => {

    describe('getTripwireEvents', () => {
        let events;
        let elasticDb;
        let elasticsearchStub;
        let scrollStub;

        beforeEach(() => {
            events = new Events();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            scrollStub = sinon.stub(Elasticsearch, 'getScrollSearchResults');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return tripwire events with sensorId (effective=true)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: [
                    { id: 'event1', timestamp: '2023-01-12T12:00:00.000Z' },
                    { id: 'event2', timestamp: '2023-01-12T13:00:00.000Z' }
                ]
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result).to.have.property('tripwireEvents');
            expect(result.tripwireEvents).to.be.an('array');
        });

        it('should return tripwire events with sensorId (effective=false)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                effective: false
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'event1' } },
                            { _source: { id: 'event2' } }
                        ]
                    }
                }
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result).to.have.property('tripwireEvents');
        });

        it('should return tripwire events with place', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: []
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result).to.have.property('tripwireEvents');
        });

        it('should return tripwire events with sensorId and tripwireId', async () => {
            const input = {
                sensorId: 'sensor123',
                tripwireId: 'tripwire1',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: [{ id: 'event1' }]
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result).to.have.property('tripwireEvents');
        });

        it('should filter by objectType when provided', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                objectType: 'Person'
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: []
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result).to.have.property('tripwireEvents');
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            scrollStub.resolves({ indexAbsent: true });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result.tripwireEvents).to.be.an('array').that.is.empty;
        });

        it('should deduplicate events by id when effective=true', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                effective: true
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: [
                    { id: 'event1', timestamp: '2023-01-12T12:00:00.000Z' },
                    { id: 'event1', timestamp: '2023-01-12T12:30:00.000Z' }, // duplicate
                    { id: 'event2', timestamp: '2023-01-12T13:00:00.000Z' }
                ]
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result.tripwireEvents.length).to.equal(2);
        });

        it('should throw BadRequestError when timestamps are missing', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await events.getTripwireEvents(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw BadRequestError when neither sensorId nor place is provided', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await events.getTripwireEvents(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have");
            }
        });

        it('should ignore tripwireId when place is provided (tripwireId requires sensorId)', async () => {
            // When place is provided with tripwireId but no sensorId,
            // the schema defaults sensorId to null and the code uses place instead
            const input = {
                place: 'city=abc',
                tripwireId: 'tripwire1',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: []
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result).to.have.property('tripwireEvents');
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await events.getTripwireEvents(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('fromTimestamp is not lesser than toTimestamp.');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await events.getTripwireEvents(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should respect maxResultSize limit', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                maxResultSize: 2
            };

            scrollStub.resolves({
                indexAbsent: false,
                hitSources: [
                    { id: 'event1' },
                    { id: 'event2' },
                    { id: 'event3' },
                    { id: 'event4' }
                ]
            });

            const result = await events.getTripwireEvents(elasticDb, input);

            expect(result.tripwireEvents.length).to.equal(2);
        });
    });

    describe('getRoiEvents', () => {
        let events;
        let elasticDb;
        let elasticsearchStub;

        beforeEach(() => {
            events = new Events();
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

        it('should return ROI events with sensorId', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'roi-event1' } },
                            { _source: { id: 'roi-event2' } }
                        ]
                    }
                }
            });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result).to.have.property('roiEvents');
            expect(result.roiEvents).to.be.an('array');
        });

        it('should return ROI events with place', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result).to.have.property('roiEvents');
        });

        it('should return ROI events with sensorId and roiId', async () => {
            const input = {
                sensorId: 'sensor123',
                roiId: 'roi1',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'roi-event1' } }]
                    }
                }
            });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result).to.have.property('roiEvents');
        });

        it('should filter by objectType when provided', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                objectType: 'Vehicle'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result).to.have.property('roiEvents');
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            elasticsearchStub.resolves({ indexAbsent: true });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result.roiEvents).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError when timestamps are missing', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await events.getRoiEvents(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw BadRequestError when neither sensorId nor place is provided', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await events.getRoiEvents(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have");
            }
        });

        it('should ignore roiId when place is provided (roiId requires sensorId)', async () => {
            // When place is provided with roiId but no sensorId,
            // the schema defaults sensorId to null and the code uses place instead
            const input = {
                place: 'city=abc',
                roiId: 'roi1',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result).to.have.property('roiEvents');
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await events.getRoiEvents(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('fromTimestamp is not lesser than toTimestamp.');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await events.getRoiEvents(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should use custom maxResultSize', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                maxResultSize: 100
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await events.getRoiEvents(elasticDb, input);

            expect(result).to.have.property('roiEvents');
        });
    });

});
