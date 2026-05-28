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
const Histogram = require('../../../../src/web-api-core/Utils/Histogram.js');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError.js');

describe('Histogram', () => {

    describe('getDefaultHistogramBucketCount', () => {
        it('should return default histogram bucket count of 20', () => {
            const result = Histogram.getDefaultHistogramBucketCount();
            expect(result).to.equal(20);
        });

        it('should return an integer', () => {
            const result = Histogram.getDefaultHistogramBucketCount();
            expect(Number.isInteger(result)).to.be.true;
        });
    });

    describe('getMaxHistogramBucketCount', () => {
        it('should return max histogram bucket count of 1000', () => {
            const result = Histogram.getMaxHistogramBucketCount();
            expect(result).to.equal(1000);
        });

        it('should return an integer', () => {
            const result = Histogram.getMaxHistogramBucketCount();
            expect(Number.isInteger(result)).to.be.true;
        });
    });

    describe('computeBucketSizeInSec', () => {
        it('should compute bucket size with default bucket count', () => {
            const result = Histogram.computeBucketSizeInSec({
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });
            // 1 hour = 3600 seconds / 20 buckets = 180 seconds, rounded to multiple of 5
            expect(result).to.be.a('number');
            expect(result % 5).to.equal(0); // Should be multiple of 5
        });

        it('should compute bucket size with custom bucket count', () => {
            const result = Histogram.computeBucketSizeInSec({
                bucketCount: 10,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });
            // 1 hour = 3600 seconds / 10 buckets = 360 seconds
            expect(result).to.equal(360);
        });

        it('should round bucket size to multiple of 5', () => {
            const result = Histogram.computeBucketSizeInSec({
                bucketCount: 7,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });
            expect(result % 5).to.equal(0);
        });

        it('should throw InvalidInputError when fromTimestamp is missing', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /fromTimestamp/);
        });

        it('should throw InvalidInputError when toTimestamp is missing', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    fromTimestamp: '2023-01-12T11:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /toTimestamp/);
        });

        it('should throw InvalidInputError for invalid time range', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    fromTimestamp: '2023-01-12T12:00:00.000Z',
                    toTimestamp: '2023-01-12T11:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /fromTimestamp/);
        });

        it('should throw InvalidInputError for invalid fromTimestamp format', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    fromTimestamp: 'invalid',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /fromTimestamp/);
        });

        it('should throw InvalidInputError when bucketCount is not an integer', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    bucketCount: 10.5,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /bucketCount/);
        });

        it('should throw InvalidInputError when bucketCount is less than 1', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    bucketCount: 0,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /bucketCount/);
        });

        it('should throw InvalidInputError when bucketCount exceeds max', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    bucketCount: 1001,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /bucketCount/);
        });

        it('should throw InvalidInputError for additional properties', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z',
                    invalidProp: 'value'
                });
            }).to.throw(InvalidInputError);
        });

        it('should throw InvalidInputError when bucketCount is Infinity', () => {
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    bucketCount: Infinity,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError);
        });

        it('should handle bucket size that is already multiple of 5', () => {
            const result = Histogram.computeBucketSizeInSec({
                bucketCount: 12,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });
            // 3600 / 12 = 300, which is already multiple of 5
            expect(result).to.equal(300);
        });

        it('should round up bucket size when not multiple of 5 (line 115)', () => {
            // 3600 / 11 = 327.27 -> ceil = 328, 328 % 5 = 3 (not 0)
            // Should round to 330 (next multiple of 5)
            const result = Histogram.computeBucketSizeInSec({
                bucketCount: 11,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });
            expect(result).to.equal(330);
            expect(result % 5).to.equal(0);
        });

        it('should throw InvalidInputError when bucketCount is NaN (line 109)', () => {
            // NaN passes isFinite check as false
            const input = {
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };
            // Manually set bucketCount to NaN after schema validation would set default
            // By providing a value that coerces to NaN
            expect(() => {
                Histogram.computeBucketSizeInSec({
                    bucketCount: NaN,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError);
        });

        it('should handle another case requiring rounding to multiple of 5', () => {
            // 1800 seconds (30 min) / 7 = 257.14 -> ceil = 258, 258 % 5 = 3
            // Should round to 260
            const result = Histogram.computeBucketSizeInSec({
                bucketCount: 7,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T11:30:00.000Z'
            });
            expect(result).to.equal(260);
        });
    });

    describe('getEmptyHistogram', () => {
        it('should return an array of histogram buckets', () => {
            const result = Histogram.getEmptyHistogram({
                bucketSizeInSec: 600,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });

            expect(result).to.be.an('array');
            expect(result.length).to.be.greaterThan(0);
        });

        it('should return buckets with start and end timestamps', () => {
            const result = Histogram.getEmptyHistogram({
                bucketSizeInSec: 600,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });

            expect(result[0]).to.deep.equal({
                start: '2023-01-12T11:00:00.000Z',
                end: '2023-01-12T11:10:00.000Z'
            });
        });

        it('should create correct number of buckets for 1 hour with 10 min buckets', () => {
            const result = Histogram.getEmptyHistogram({
                bucketSizeInSec: 600, // 10 minutes
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });

            expect(result).to.have.lengthOf(6); // 60 minutes / 10 minutes = 6 buckets
        });

        it('should have consecutive bucket timestamps', () => {
            const result = Histogram.getEmptyHistogram({
                bucketSizeInSec: 600,
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });

            for (let i = 1; i < result.length; i++) {
                expect(result[i].start).to.equal(result[i - 1].end);
            }
        });

        it('should throw InvalidInputError when bucketSizeInSec is missing', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /bucketSizeInSec/);
        });

        it('should throw InvalidInputError when fromTimestamp is missing', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    bucketSizeInSec: 600,
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /fromTimestamp/);
        });

        it('should throw InvalidInputError when toTimestamp is missing', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    bucketSizeInSec: 600,
                    fromTimestamp: '2023-01-12T11:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /toTimestamp/);
        });

        it('should throw InvalidInputError for invalid time range', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    bucketSizeInSec: 600,
                    fromTimestamp: '2023-01-12T12:00:00.000Z',
                    toTimestamp: '2023-01-12T11:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /fromTimestamp/);
        });

        it('should throw InvalidInputError when bucketSizeInSec is not an integer', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    bucketSizeInSec: 600.5,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /bucketSizeInSec/);
        });

        it('should throw InvalidInputError when bucketSizeInSec is less than 1', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    bucketSizeInSec: 0,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z'
                });
            }).to.throw(InvalidInputError, /bucketSizeInSec/);
        });

        it('should throw InvalidInputError for additional properties', () => {
            expect(() => {
                Histogram.getEmptyHistogram({
                    bucketSizeInSec: 600,
                    fromTimestamp: '2023-01-12T11:00:00.000Z',
                    toTimestamp: '2023-01-12T12:00:00.000Z',
                    invalidProp: 'value'
                });
            }).to.throw(InvalidInputError);
        });

        it('should align bucket start to bucket size boundary', () => {
            const result = Histogram.getEmptyHistogram({
                bucketSizeInSec: 600, // 10 minutes
                fromTimestamp: '2023-01-12T11:05:30.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            });

            // First bucket should start at aligned time (11:00:00 or similar)
            const firstBucketStart = new Date(result[0].start);
            expect(firstBucketStart.getSeconds()).to.equal(0);
            expect(firstBucketStart.getMilliseconds()).to.equal(0);
        });

        it('should handle small bucket sizes', () => {
            const result = Histogram.getEmptyHistogram({
                bucketSizeInSec: 60, // 1 minute
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T11:05:00.000Z'
            });

            expect(result).to.have.lengthOf(5); // 5 minutes / 1 minute = 5 buckets
        });
    });

});
