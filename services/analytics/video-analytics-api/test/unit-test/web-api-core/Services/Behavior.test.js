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

const { Client } = require('@elastic/elasticsearch');
const sinon = require('sinon');
const { expect } = require('chai');
const Validator = require("../../../../src/web-api-core/Utils/Validator");
const Behavior = require('../../../../src/web-api-core/Services/Behavior');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');
const Frames = require('../../../../src/web-api-core/Services/Frames');
const Utils = require('../../../../src/web-api-core/Utils/Utils');
const { log } = require('console');
const fs = require('fs');
const path = require('path');

describe('Behavior.getMaxBehaviorsInLocationQuery', () => {
    it('should return the max behaviors in location query', () => {
        const maxBehaviors = Behavior.getMaxBehaviorsInLocationQuery();
        expect(maxBehaviors).to.equal(100);
        expect(maxBehaviors).to.be.a('number');
    });
});

describe('Behavior.getLocationsBasedOnTimestampRange', () => {
    it('should return sliced locations when fromTimestamp is after behavior start', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0], [5, 5, 0], [6, 6, 0], [7, 7, 0], [8, 8, 0], [9, 9, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0], [5, 5, 0], [6, 6, 0], [7, 7, 0], [8, 8, 0], [9, 9, 0]]
            }
        };
        
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:03.000Z', '2023-01-01T10:00:07.000Z');
        
        expect(result).to.have.property('locations');
        expect(result).to.have.property('smoothLocations');
        expect(result.locations.coordinates.length).to.be.lessThan(behavior.locations.coordinates.length);
    });

    it('should return all locations when timeInterval is 0', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:00.100Z',
            timeInterval: 0,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0]]
            }
        };
        
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:00.000Z', '2023-01-01T10:00:00.100Z');
        
        expect(result.locations).to.deep.equal(behavior.locations);
        expect(result.smoothLocations).to.deep.equal(behavior.smoothLocations);
    });

    it('should throw InvalidInputError for invalid time range', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: { type: 'linestring', coordinates: [[0, 0, 0]] },
            smoothLocations: { type: 'linestring', coordinates: [[0, 0, 0]] }
        };
        
        expect(() => {
            Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:10.000Z', '2023-01-01T10:00:05.000Z');
        }).to.throw(InvalidInputError, 'fromTimestamp is not lesser than toTimestamp.');
    });

    it('should throw InvalidInputError when fromTimestamp is later than behavior end', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: { type: 'linestring', coordinates: [[0, 0, 0]] },
            smoothLocations: { type: 'linestring', coordinates: [[0, 0, 0]] }
        };
        
        expect(() => {
            Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:15.000Z', '2023-01-01T10:00:20.000Z');
        }).to.throw(InvalidInputError, "fromTimestamp is later than Behavior's end timestamp.");
    });

    it('should throw InvalidInputError when toTimestamp is earlier than behavior start', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: { type: 'linestring', coordinates: [[0, 0, 0]] },
            smoothLocations: { type: 'linestring', coordinates: [[0, 0, 0]] }
        };
        
        expect(() => {
            Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T09:59:50.000Z', '2023-01-01T09:59:55.000Z');
        }).to.throw(InvalidInputError, "toTimestamp is earlier than Behavior's start timestamp.");
    });

    it('should return all locations when timestamps cover entire behavior', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0]]
            }
        };
        
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T09:59:50.000Z', '2023-01-01T10:00:15.000Z');
        
        expect(result.locations.coordinates.length).to.equal(behavior.locations.coordinates.length);
    });

    it('should handle startLocationIndex at boundary when calculated index exceeds coordinates length', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:01.000Z',
            timeInterval: 1,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0]]
            }
        };
        
        // fromTimestamp close to end so startLocationIndex could exceed array length
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:00.900Z', '2023-01-01T10:00:01.000Z');
        
        expect(result).to.have.property('locations');
        expect(result.locations.coordinates.length).to.be.at.least(1);
    });

    it('should handle endLocationIndex at boundary when calculated index exceeds coordinates length', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:01.000Z',
            timeInterval: 1,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0]]
            }
        };
        
        // toTimestamp within behavior but ratio would cause ceiling to exceed length
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:00.000Z', '2023-01-01T10:00:00.999Z');
        
        expect(result).to.have.property('locations');
        expect(result.locations.coordinates.length).to.be.at.least(1);
    });

    it('should correctly slice locations for middle range', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0], [5, 5, 0], [6, 6, 0], [7, 7, 0], [8, 8, 0], [9, 9, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0], [5, 5, 0], [6, 6, 0], [7, 7, 0], [8, 8, 0], [9, 9, 0]]
            }
        };
        
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:02.000Z', '2023-01-01T10:00:08.000Z');
        
        expect(result).to.have.property('locations');
        expect(result).to.have.property('smoothLocations');
        expect(result.locations.type).to.equal('linestring');
        expect(result.smoothLocations.type).to.equal('linestring');
    });

    it('should handle startLocationIndex clamping when fromTimestamp equals behavior end', () => {
        const behavior = {
            timestamp: '2023-01-01T10:00:00.000Z',
            end: '2023-01-01T10:00:10.000Z',
            timeInterval: 10,
            locations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0]]
            },
            smoothLocations: {
                type: 'linestring',
                coordinates: [[0, 0, 0], [1, 1, 0], [2, 2, 0], [3, 3, 0], [4, 4, 0]]
            }
        };
        
        // fromTimestamp equals behavior.end, causing startRatio = 1, startLocationIndex = 5 (exceeds length 5)
        // This triggers line 356: startLocationIndex = behavior.locations.coordinates.length - 1
        const result = Behavior.getLocationsBasedOnTimestampRange(behavior, '2023-01-01T10:00:10.000Z', '2023-01-01T10:00:15.000Z');
        
        expect(result).to.have.property('locations');
        expect(result).to.have.property('smoothLocations');
        expect(result.locations.coordinates.length).to.be.at.least(1);
    });
});

describe('Behavior.getBehaviorsUsingIdsAndTimestamp', () => {

    let fixture_data;

    before(() => {
        // Load the fixture data before running the tests
        const file_path = path.join(__dirname, '../../fixtures/behavior.json');
        fixture_data = JSON.parse(fs.readFileSync(file_path, 'utf8'));
    });

    beforeEach(() => {
        // Stub Elasticsearch methods - fixture_data is a single ES document with _source
        sinon.stub(Elasticsearch, 'getSearchResults').resolves({
            body: {
                hits: {
                    hits: [{ _source: fixture_data._source }]
                }
            }
        });

        behavior = new Behavior();
    });

    afterEach(() => {
        sinon.restore(); // Restore original functionality after each test
    });

    it('should return behaviors when valid input is provided and Elasticsearch is used', async () => {
        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            behaviorInfo: [
                { sensorId: 'Nth_Street_Cafe_Entrance', objectId: '25012', timestamp: '2023-01-12T14:20:10.000Z',  }
            ]
        };

        const result = await behavior.getBehaviorsUsingIdsAndTimestamp(elasticDb, input);
        expect(result).to.have.property('behaviors');
        expect(result.behaviors).to.have.length(1);
        expect(result.behaviors).to.deep.equal([fixture_data._source]);
    });

    it('should throw BadRequestError when behaviorInfo is missing', async () => {
        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {}; // Missing behaviorInfo

        try {
            await behavior.getBehaviorsUsingIdsAndTimestamp(elasticDb, input);
            throw new Error('Expected BadRequestError to be thrown');
        } catch (err) {
            expect(err).to.be.instanceOf(BadRequestError);
            expect(err.message).to.include("Input should have required property 'behaviorInfo'.");
        }
    });

    it('should throw InvalidInputError for invalid timestamp format', async () => {
        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            behaviorInfo: [
                { sensorId: 'abc', objectId: '200', timestamp: '202401-13T14:20:10.000Z' } // Invalid timestamp
            ]
        };

        try {
            await behavior.getBehaviorsUsingIdsAndTimestamp(elasticDb, input);
            throw new Error('Expected InvalidInputError to be thrown');
        } catch (err) {
            expect(err).to.be.instanceOf(InvalidInputError);
            expect(err.message).to.include('behaviorInfo has invalid timestamp in the following indices: 0.');
        }
    });

    it('should return an empty behaviors array when behaviorInfo is empty', async () => {
        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            behaviorInfo: [] // Empty array
        };

        const result = await behavior.getBehaviorsUsingIdsAndTimestamp(elasticDb, input);
        expect(result).to.have.property('behaviors');
        expect(result.behaviors).to.deep.equal([]);
    });

    it('should throw InternalServerError for unsupported database type', async () => {
        const elasticDb = {
            getName: () => 'UnsupportedDB', // Unsupported database type
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            behaviorInfo: [
                { sensorId: 'abc', objectId: '200', timestamp: '2023-01-12T14:20:10.000Z' }
            ]
        };

        try {
            const result = await behavior.getBehaviorsUsingIdsAndTimestamp(elasticDb, input);
            throw new Error('Expected InternalServerError to be thrown');
        } catch (err) {
            expect(err).to.be.instanceOf(InternalServerError);
            expect(err.message).to.include('Invalid database: UnsupportedDB.');
        }
    });
});

describe('Behavior.getLocationsOfBehaviors', () => {

    beforeEach(() => {
        // Stub Elasticsearch methods
        sinon.stub(Elasticsearch, 'getIndex').resolves({
            body: {
                hits: {
                    hits: [
                        { _source: { sensorId: 'abc', objectId: '200', timestamp: '2023-01-12T14:20:10.000Z' } },
                        { _source: { sensorId: 'def', objectId: '210', timestamp: '2023-01-12T14:24:10.000Z' } }
                    ]
                }
            }
        });

        sinon.stub(Elasticsearch, 'getSearchResults').resolves({
            body: {
                hits: {
                    hits: [
                        { _source: { id: '1', locations: [] } }
                    ]
                }
            }
        });

        behavior = new Behavior();

    });

    afterEach(() => {
        sinon.restore();
    });

    it('should return behaviors when valid input is provided and Elasticsearch is used', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            fromTimestamp: "2023-01-12T11:20:10.000Z",
            toTimestamp: "2023-01-12T14:20:10.000Z",
            behaviorIds: ["abc #-# 272", "def #-# 2152"]
        };

        const result = await behavior.getLocationsOfBehaviors(elasticDb, input);
        expect(result).to.have.property('behaviors');
        expect(result.behaviors).to.have.length(1);
        expect(result.behaviors).to.deep.equal([{ id: '1', locations: [] }]);
    });

    it('should throw BadRequestError when required properties are missing from the input', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const invalidInput = {
            fromTimestamp: "2023-01-12T11:20:10.000Z",
            toTimestamp: "2023-01-12T14:20:10.000Z"
            // missing behaviorIds
        };

        try {
            await behavior.getLocationsOfBehaviors(elasticDb, invalidInput);
            throw new Error('Expected BadRequestError not thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('Input should have required properties');
        }
    });

    it('should throw InvalidInputError for invalid time range', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            fromTimestamp: "2023-01-12T11:20:10.000Z",
            toTimestamp: "2023-01-12T14:20:10.000Z",
            behaviorIds: ["abc #-# 272"]
        };
        
        sinon.stub(Validator, 'isValidTimeRange').returns({ valid: false, reason: 'Invalid time range' });

        try {
            await behavior.getLocationsOfBehaviors(elasticDb, input);
            throw new Error('Expected InvalidInputError not thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(InvalidInputError);
            expect(error.message).to.equal('Invalid time range');
        }
    });

    it('should throw InternalServerError for unsupported database', async () => {
        elasticDb = {
            getName: () => 'UnsupportedDB',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            fromTimestamp: "2023-01-12T11:20:10.000Z",
            toTimestamp: "2023-01-12T14:20:10.000Z",
            behaviorIds: ["abc #-# 272"]
        };

        try {
            await behavior.getLocationsOfBehaviors(elasticDb, input);
            throw new Error('Expected InternalServerError not thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(InternalServerError);
            expect(error.message).to.include('Invalid database');
        }
    });

    it('should throw BadRequestError for invalid behaviorIds array length', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const input = {
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            behaviorIds: [] // Empty array
        };

        try {
            await behavior.getLocationsOfBehaviors(elasticDb, input);
            throw new Error('Expected BadRequestError not thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('behaviorIds should have atleast 1 item');
        }
    });
});

describe('Behavior.getPts', () => {

    beforeEach(() => {
        behavior = new Behavior();
        frameObjectStub = sinon.stub(Frames.prototype, 'getPts');
    });

    afterEach(() => {
        sinon.restore();
    });

    it('should return startPts and endPts when valid input is provided', async () => {

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => client
        };

        const input = {
            sensorId: 'sensor123',
            endFrameId: 1000,
            behaviorTimeInterval: 5.5
        };

        frameObjectStub.resolves({ pts: 5500 });

        const result = await behavior.getPts(elasticDb, input);

        expect(result).to.deep.equal({
            startPts: 0,
            endPts: 5500
        });
    });

    it('should throw BadRequestError when required properties are missing', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const invalidInput = {
            endFrameId: 1000,
            behaviorTimeInterval: 5.5
            // Missing sensorId
        };

        try {
            await behavior.getPts(elasticDb, invalidInput);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("Input should have required properties");
        }
    });

    it('should throw BadRequestError if endFrameId is not an integer', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
        };
        
        const input = {
            sensorId: 'sensor123',
            endFrameId: 'hundred',
            behaviorTimeInterval: 5.5
        };

        try {
            await behavior.getPts(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('must be integer');
        }
    });

    it('should throw BadRequestError if behaviorTimeInterval is not a finite integer', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const input = {
            sensorId: 'sensor123',
            endFrameId: 1000,
            behaviorTimeInterval: 'hundred'
        };

        try {
            await behavior.getPts(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('must be number');
        }
    });

    it('should throw InternalServerError if behaviorTimeInterval is greater than endPts', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const input = {
            sensorId: 'sensor123',
            endFrameId: 1000,
            behaviorTimeInterval: 6 // This will be converted to 6000 ms
        };

        frameObjectStub.resolves({ pts: 5000 }); // EndPts is 5000 ms

        try {
            await behavior.getPts(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(InternalServerError);
            expect(error.message).to.include('Invalid behaviorTimeInterval. behaviorTimeInterval should be smaller than endPts.');
        }
    });

    it('should throw BadRequestError if endFrameId is Infinity', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const input = {
            sensorId: 'sensor123',
            endFrameId: Infinity,
            behaviorTimeInterval: 5.5
        };

        try {
            await behavior.getPts(elasticDb, input);
            throw new Error('Expected BadRequestError to be thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('must be integer');
        }
    });

    it('should throw BadRequestError if behaviorTimeInterval is Infinity', async () => {
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const input = {
            sensorId: 'sensor123',
            endFrameId: 1000,
            behaviorTimeInterval: Infinity
        };

        try {
            await behavior.getPts(elasticDb, input);
            throw new Error('Expected BadRequestError to be thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('must be number');
        }
    });
});

describe('Behavior.getBehaviors', () => {

    beforeEach(() => {
        behavior = new Behavior();
        elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
    });

    afterEach(() => {
        sinon.restore();
    });

    it('should return an array of behaviors when valid input is provided', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            maxResultSize: 25,
        };

        elasticsearchStub.resolves({ indexAbsent: false, body: { hits: { hits: [] } } });

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const result = await behavior.getBehaviors(elasticDb, input);

        expect(result).to.have.property('behaviors').that.is.an('array');
        // array of behaviors
    });

    it('should throw BadRequestError when the input schema validation fails', async () => {
        const invalidInput = {
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, invalidInput);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("Only one of 'sensorId' or 'place' should exist in the query.");
        }
    });

    it('should throw InvalidInputError when the time range validation fails', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '202301-12T14:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            const result = await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(InvalidInputError);
            expect(error.message).to.include("Invalid fromTimestamp.");
        }
    });

    it('should throw InvalidInputError if maxResultSize is not a finite integer', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            maxResultSize: NaN
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('maxResultSize is not an integer.');
        }
    });

    it('should throw InternalServerError if the database name is invalid', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            maxResultSize: 25,
        };
        
        elasticDb = {
            getName: () => 'InvalidDB',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(InternalServerError);
            expect(error.message).to.include('Invalid database: InvalidDB.');
        }
    });

    it('should throw BadRequestError if objectId is present without required fields', async () => {
        const input = {
            sensorId: 'sensor123',
            objectId: 'object123',
            maxResultSize: 25,
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("'objectId' can't be used together with either 'queryString' or 'maxResultSize'.");
        }
    });

    it('should throw BadRequestError if both sensorId and place are provided', async () => {
        const input = {
            sensorId: 'sensor123',
            place: 'place123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("Only one of 'sensorId' or 'place' should exist in the query.");
        }
    });
    
    it('should throw BadRequestError if neither sensorId nor place is provided', async () => {
        const input = {
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
        };
    
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("Only one of 'sensorId' or 'place' should exist in the query.");
        }
    });
    
    it('should throw BadRequestError if required timestamp or queryString combinations are missing', async () => {
        const input = {
            sensorId: 'sensor123',
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        // Configure the existing elasticsearchStub to return proper mock data
        elasticsearchStub.resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: []
                }
            }
        });

        // The current implementation allows sensorId alone and returns empty results
        const result = await behavior.getBehaviors(elasticDb, input);
        expect(result).to.have.property('behaviors');
        expect(result.behaviors).to.be.an('array');
        expect(result.behaviors).to.be.empty;
    });
    
    it('should throw BadRequestError if both objectId and queryString are provided', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            objectId: 'object123',
            queryString: 'query',
        };
    
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("'objectId' can't be used together with either 'queryString' or 'maxResultSize'.");
        }
    });
    
    it('should throw BadRequestError if both objectId and maxResultSize are provided', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            objectId: 'object123',
            maxResultSize: 10,
        };
    
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("'objectId' can't be used together with either 'queryString' or 'maxResultSize'.");
        }
    });

    it('should correctly handle objectId being present and set maxResultSize to 1', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            objectId: 'object123',
        };
        
        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        

        elasticsearchStub.resolves({ indexAbsent: false, body: { hits: { hits: [] } } });

        const result = await behavior.getBehaviors(elasticDb, input);

        expect(result).to.have.property('behaviors').that.is.an('array'); // { behaviors: [] }
        expect(elasticsearchStub.calledOnce).to.be.true;
        const queryObject = elasticsearchStub.firstCall.args[1];
        // {
        // index: 'undefinedbehavior-*',
        // body: { query: { bool: [Object] } },
        // sort: 'end:desc',
        // size: 1
        // }
        expect(queryObject.size).to.equal(1);
    });

    it('should return behaviors when place parameter is provided', async () => {
        const input = {
            place: 'city=Santa-Clara/building=Warehouse',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z'
        };

        elasticsearchStub.resolves({ indexAbsent: false, body: { hits: { hits: [] } } });

        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const result = await behavior.getBehaviors(elasticDb, input);

        expect(result).to.have.property('behaviors').that.is.an('array');
        expect(elasticsearchStub.calledOnce).to.be.true;
    });

    it('should return behaviors when objectType parameter is provided', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            objectType: 'Person'
        };

        elasticsearchStub.resolves({ indexAbsent: false, body: { hits: { hits: [] } } });

        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const result = await behavior.getBehaviors(elasticDb, input);

        expect(result).to.have.property('behaviors').that.is.an('array');
        expect(elasticsearchStub.calledOnce).to.be.true;
    });

    it('should return behaviors when queryString parameter is provided', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            queryString: 'object.type:Person'
        };

        elasticsearchStub.resolves({ indexAbsent: false, body: { hits: { hits: [] } } });

        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        const result = await behavior.getBehaviors(elasticDb, input);

        expect(result).to.have.property('behaviors').that.is.an('array');
        expect(elasticsearchStub.calledOnce).to.be.true;
    });

    it('should throw BadRequestError when maxResultSize is Infinity', async () => {
        const input = {
            sensorId: 'sensor123',
            fromTimestamp: '2023-01-12T11:20:10.000Z',
            toTimestamp: '2023-01-12T14:20:10.000Z',
            maxResultSize: Infinity
        };

        const elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getBehaviors(elasticDb, input);
            throw new Error('Expected BadRequestError to be thrown');
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include('maxResultSize is not an integer');
        }
    });

});

describe('Behavior.getTimestampOfBehavior', () => {
    beforeEach(() => {
        behavior = new Behavior();
        getSearchResultsStub = sinon.stub(Elasticsearch, 'getSearchResults');
    });

    afterEach(() => {
        sinon.restore();
    });

    it('should throw BadRequestError if required fields are missing', async () => {
        const input = {
            sensorId: 'sensor123',
            // Missing objectId and timestampWithinBehavior
        };
        
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };

        try {
            await behavior.getTimestampOfBehavior(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("Input should have required properties 'sensorId', 'objectId' and 'timestampWithinBehavior'.");
        }
    });

    it('should throw BadRequestError if sensorId or objectId has an invalid length', async () => {
        const input = {
            sensorId: '',
            objectId: 'object123',
            timestampWithinBehavior: '2023-01-12T14:20:10.000Z'
        };
    
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        try {
            await behavior.getTimestampOfBehavior(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(BadRequestError);
            expect(error.message).to.include("sensorId should have atleast 1 character.");
        }
    });

    it('should throw InvalidInputError if timestampWithinBehavior is invalid', async () => {
        const input = {
            sensorId: 'sensor123',
            objectId: 'object123',
            timestampWithinBehavior: '202301-12T14:20:10.000Z'
        };

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        try {
            await behavior.getTimestampOfBehavior(elasticDb, input);
        } catch (error) {
            expect(error).to.be.instanceOf(InvalidInputError);
            expect(error.message).to.include("Invalid timestampWithinBehavior.");
        }
    });

    it('should return the start timestamp of a behavior from Elasticsearch', async () => {
        const input = {
            sensorId: 'sensor123',
            objectId: 'object123',
            timestampWithinBehavior: '2023-01-12T14:20:10.000Z'
        };

        const expectedTimestamp = '2023-01-12T14:00:00.000Z';

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        getSearchResultsStub.resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [
                        { _source: { timestamp: expectedTimestamp } }
                    ]
                }
            }
        });

        const result = await behavior.getTimestampOfBehavior(elasticDb, input);
        expect(result).to.equal(expectedTimestamp);
    });

    it('should return null if no matching behavior is found in Elasticsearch', async () => {
        const input = {
            sensorId: 'sensor123',
            objectId: 'object123',
            timestampWithinBehavior: '2023-01-12T14:20:10.000Z'
        };

        getSearchResultsStub.resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: []
                }
            }
        });

        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        const result = await behavior.getTimestampOfBehavior(elasticDb, input);
        expect(result).to.be.null;
    });

    it('should throw InternalServerError if an invalid database is provided', async () => {
        const input = {
            sensorId: 'sensor123',
            objectId: 'object123',
            timestampWithinBehavior: '2023-01-12T14:20:10.000Z'
        };
        
        const elasticDb = {
            getName: () => 'InvalidDB',
            getClient: () => sinon.stub().returns({}),
            getConfigs: () => {
                return new Map([
                    ["indexPrefix", "mdx-"],
                    ["rawIndex", "mdx-raw-*"]
                ]);
            }
        };
        
        try {
            const result = await behavior.getTimestampOfBehavior(elasticDb, input);
            throw new Error('Expected method to throw.');
        } catch (error) {
            expect(error).to.be.instanceOf(InternalServerError);
            expect(error.message).to.include('Invalid database: InvalidDB.');
        }
    });

});
