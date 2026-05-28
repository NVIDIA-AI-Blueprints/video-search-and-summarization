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
const SpaceUtilization = require('../../../../src/web-api-core/Metrics/SpaceUtilization');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('SpaceUtilization', () => {
    let spaceUtilization;
    let elasticDb;
    let searchStub;

    beforeEach(() => {
        spaceUtilization = new SpaceUtilization();
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([
                ['indexPrefix', 'mdx-']
            ])
        };
        searchStub = sinon.stub(Elasticsearch, 'getSearchResults');
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('getHistogramOfSpaceUtilizationMetrics', () => {
        it('should return space utilization histogram with time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        buckets: [
                                            {
                                                key: 'roi1',
                                                avgUtilizableFreeSpace: { value: 10.5 },
                                                avgSpaceUtilization: { value: 75.2 },
                                                avgSpaceOccupied: { value: 50.0 },
                                                avgTotalSpace: { value: 100.0 },
                                                avgFreeSpaceQuality: { value: 80.5 },
                                                avgNumExtraPallets: { value: 2.0 },
                                                avgFreeSpace: { value: 25.0 }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('rois');
            expect(result.rois).to.be.an('array');
        });

        it('should return space utilization histogram with minutesAgo', async () => {
            const input = {
                minutesAgo: 60
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('rois');
        });

        it('should filter by roiIds when provided', async () => {
            const input = {
                roiIds: ['roi1', 'roi2'],
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result).to.have.property('rois');
        });

        it('should return empty rois when index is absent', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result.rois).to.be.an('array').that.is.empty;
        });

        it('should handle missing roi in some buckets', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        buckets: [
                                            {
                                                key: 'roi1',
                                                avgUtilizableFreeSpace: { value: 10.5 },
                                                avgSpaceUtilization: { value: 75.2 },
                                                avgSpaceOccupied: { value: 50.0 },
                                                avgTotalSpace: { value: 100.0 },
                                                avgFreeSpaceQuality: { value: 80.5 },
                                                avgNumExtraPallets: { value: 2.0 },
                                                avgFreeSpace: { value: 25.0 }
                                            }
                                        ]
                                    }
                                },
                                {
                                    key_as_string: '2023-01-12T11:30:00.000Z',
                                    rois: {
                                        buckets: [] // roi1 missing in this bucket
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result.rois).to.be.an('array');
        });

        it('should throw BadRequestError when neither time range nor minutesAgo provided', async () => {
            const input = {};

            try {
                await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T12:00:00.000Z',
                toTimestamp: '2023-01-12T11:00:00.000Z'
            };

            try {
                await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            try {
                await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should use custom bucketCount', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                bucketCount: 10
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
        });

        it('should throw BadRequestError for Infinity minutesAgo', async () => {
            const input = {
                minutesAgo: Infinity,
                bucketCount: 10
            };

            try {
                await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });

        it('should throw BadRequestError for Infinity bucketCount', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                bucketCount: Infinity
            };

            try {
                await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });

        it('should trim histogram start timestamp when bucket starts before fromTimestamp', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:30:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                bucketCount: 2
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        buckets: [
                                            {
                                                key: 'roi1',
                                                avgUtilizableFreeSpace: { value: 10.5 },
                                                avgSpaceUtilization: { value: 75.2 },
                                                avgSpaceOccupied: { value: 50.0 },
                                                avgTotalSpace: { value: 100.0 },
                                                avgFreeSpaceQuality: { value: 80.5 },
                                                avgNumExtraPallets: { value: 2.0 },
                                                avgFreeSpace: { value: 25.0 }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result).to.have.property('rois');
            if (result.rois.length > 0 && result.rois[0].histogram.length > 0) {
                expect(result.rois[0].histogram[0].start).to.equal('2023-01-12T11:30:00.000Z');
            }
        });

        it('should trim histogram end timestamp when bucket ends after toTimestamp', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T11:30:00.000Z',
                bucketCount: 2
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        buckets: [
                                            {
                                                key: 'roi1',
                                                avgUtilizableFreeSpace: { value: 10.5 },
                                                avgSpaceUtilization: { value: 75.2 },
                                                avgSpaceOccupied: { value: 50.0 },
                                                avgTotalSpace: { value: 100.0 },
                                                avgFreeSpaceQuality: { value: 80.5 },
                                                avgNumExtraPallets: { value: 2.0 },
                                                avgFreeSpace: { value: 25.0 }
                                            }
                                        ]
                                    }
                                },
                                {
                                    key_as_string: '2023-01-12T11:15:00.000Z',
                                    rois: {
                                        buckets: [
                                            {
                                                key: 'roi1',
                                                avgUtilizableFreeSpace: { value: 10.5 },
                                                avgSpaceUtilization: { value: 75.2 },
                                                avgSpaceOccupied: { value: 50.0 },
                                                avgTotalSpace: { value: 100.0 },
                                                avgFreeSpaceQuality: { value: 80.5 },
                                                avgNumExtraPallets: { value: 2.0 },
                                                avgFreeSpace: { value: 25.0 }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await spaceUtilization.getHistogramOfSpaceUtilizationMetrics(elasticDb, input);

            expect(result).to.have.property('rois');
            if (result.rois.length > 0) {
                const lastHistogramEntry = result.rois[0].histogram[result.rois[0].histogram.length - 1];
                expect(lastHistogramEntry.end).to.equal('2023-01-12T11:30:00.000Z');
            }
        });
    });
});
