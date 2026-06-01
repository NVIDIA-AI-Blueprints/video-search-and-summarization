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
const Incident = require('../../../../src/web-api-core/Services/Incidents');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const Calibration = require('../../../../src/web-api-core/Services/Calibration');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Incident', () => {
    let incident;
    let elasticDb;
    let searchStub;

    beforeEach(() => {
        incident = new Incident();
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

    describe('getIncidents', () => {
        it('should return incidents with sensorId', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'incident1', category: 'collision' } },
                            { _source: { id: 'incident2', category: 'intrusion' } }
                        ]
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
            expect(result.incidents).to.be.an('array').with.length(2);
        });

        it('should return incidents with place', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'incident1' } }]
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return incidents with category filter', async () => {
            const input = {
                sensorId: 'sensor123',
                category: 'collision'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'incident1', category: 'collision' } }]
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return incidents with objectId filter', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return incidents with queryString filter', async () => {
            const input = {
                sensorId: 'sensor123',
                queryString: 'collision',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return VLM verified incidents', async () => {
            const input = {
                sensorId: 'sensor123',
                vlmVerified: true,
                vlmVerdict: 'confirmed',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return VLM incidents with not-confirmed verdict', async () => {
            const input = {
                sensorId: 'sensor123',
                vlmVerified: true,
                vlmVerdict: 'not-confirmed',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return VLM incidents with all verdict', async () => {
            const input = {
                sensorId: 'sensor123',
                vlmVerified: true,
                vlmVerdict: 'all',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result.incidents).to.be.an('array').that.is.empty;
        });

        it('should use custom maxResultSize', async () => {
            const input = {
                sensorId: 'sensor123',
                maxResultSize: 100
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await incident.getIncidents(elasticDb, input);

            expect(result).to.have.property('incidents');
        });

        it('should throw BadRequestError when both sensorId and place provided', async () => {
            const input = {
                sensorId: 'sensor123',
                place: 'city=abc'
            };

            try {
                await incident.getIncidents(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("both 'sensorId' and 'place' cannot be provided together");
            }
        });

        it('should throw BadRequestError when queryString and objectId both provided', async () => {
            const input = {
                sensorId: 'sensor123',
                queryString: 'test',
                objectId: 'obj123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await incident.getIncidents(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("'queryString' can't be used together with 'objectId'");
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await incident.getIncidents(elasticDb, input);
                throw new Error('Expected InvalidInputError');
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
                sensorId: 'sensor123'
            };

            try {
                await incident.getIncidents(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getSevereIncidentsResult', () => {
        describe('with sensorId', () => {
            it('should return severe incidents for sensor', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: [{ _source: { id: 'incident1' } }]
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.include('sensor123');
            });

            it('should return empty sensors when no severe incidents', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: []
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result.severeIncidents.sensors).to.be.an('array').that.is.empty;
            });

            it('should use custom severeIncidentTypes', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    severeIncidentTypes: ['Custom Incident']
                };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: []
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should return VLM verified severe incidents', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'confirmed'
                };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: []
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle VLM verified with all verdict for sensorId (line 279)', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'all'
                };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: [{ _source: { id: 'incident1' } }]
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.include('sensor123');
            });

            it('should handle VLM verified with not-confirmed verdict for sensorId (line 281-282)', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'not-confirmed'
                };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: []
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.be.an('array').that.is.empty;
            });

            it('should handle index absent for sensorId', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                searchStub.resolves({ indexAbsent: true });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.be.an('array').that.is.empty;
            });
        });

        describe('with place', () => {
            let calibrationStub;

            beforeEach(() => {
                calibrationStub = sinon.stub(Calibration.prototype, 'getCalibration');
                sinon.stub(Calibration.prototype, 'getCalibrationMaps').returns({
                    placeHierarchyMap: new Map([
                        ['city=abc', { places: ['city=abc/building=xyz'], sensors: ['sensor1'] }],
                        ['city=abc/building=xyz', { places: null, sensors: ['sensor1', 'sensor2'] }],
                        ['city=def', { places: ['city=def/building=uvw'], sensors: null }]
                    ]),
                    sensorPlaceMap: new Map([
                        ['sensor1', 'city=abc/building=xyz'],
                        ['sensor2', 'city=abc/building=xyz']
                    ])
                });
            });

            it('should return null when place not found in calibration', async () => {
                const input = {
                    place: 'nonexistent',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result.severeIncidents).to.be.null;
            });

            it('should handle non-leaf place with sensors', async () => {
                const input = {
                    place: 'city=abc',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            placeSuccessor: { buckets: [] },
                            sensorIds: { buckets: [] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle non-leaf place without sensors', async () => {
                const input = {
                    place: 'city=def',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            placeSuccessor: { buckets: [] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle leaf place with sensors', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            sensorIds: { buckets: [{ key: 'sensor1' }] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle VLM verified with all verdict for place (lines 333, 369)', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'all'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            sensorIds: { buckets: [{ key: 'sensor1' }] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle VLM verified with not-confirmed verdict for place (lines 335-336, 371-372)', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'not-confirmed'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            sensorIds: { buckets: [] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle VLM verified with confirmed verdict for place (lines 337-338, 373-374)', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'confirmed'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            sensorIds: { buckets: [{ key: 'sensor1' }, { key: 'sensor2' }] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.include('sensor1');
                expect(result.severeIncidents.sensors).to.include('sensor2');
            });

            it('should handle VLM verified for non-leaf place with vlmVerdict all (line 333)', async () => {
                const input = {
                    place: 'city=def',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'all'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            placeSuccessor: { buckets: [{ key: 'city=def/building=uvw' }] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.places).to.include('city=def/building=uvw');
            });

            it('should handle VLM verified for non-leaf place with not-confirmed verdict (lines 335-336)', async () => {
                const input = {
                    place: 'city=def',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'not-confirmed'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            placeSuccessor: { buckets: [] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle VLM verified for non-leaf place with confirmed verdict (lines 337-338)', async () => {
                const input = {
                    place: 'city=def',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'confirmed'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            placeSuccessor: { buckets: [{ key: 'city=def/building=uvw' }] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.places).to.include('city=def/building=uvw');
            });

            it('should handle VLM verified for non-leaf place with sensors (lines 333, 369)', async () => {
                const input = {
                    place: 'city=abc',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z',
                    vlmVerified: true,
                    vlmVerdict: 'all'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            placeSuccessor: { buckets: [{ key: 'city=abc/building=xyz' }] },
                            sensorIds: { buckets: [{ key: 'sensor1' }] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.places).to.include('city=abc/building=xyz');
                expect(result.severeIncidents.sensors).to.include('sensor1');
            });

            it('should handle index absent for leaf place', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({ indexAbsent: true });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.be.an('array').that.is.empty;
            });

            it('should handle index absent for non-leaf place', async () => {
                const input = {
                    place: 'city=def',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({ indexAbsent: true });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.places).to.be.an('array').that.is.empty;
            });

            it('should handle empty calibration warning (line 552)', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: '', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        aggregations: {
                            sensorIds: { buckets: [] }
                        }
                    }
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
            });

            it('should handle missing aggregations in response for leaf place', async () => {
                const input = {
                    place: 'city=abc/building=xyz',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {}
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.sensors).to.be.an('array').that.is.empty;
            });

            it('should handle missing aggregations in response for non-leaf place', async () => {
                const input = {
                    place: 'city=def',
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {}
                });

                const result = await incident.getSevereIncidentsResult(elasticDb, input);

                expect(result).to.have.property('severeIncidents');
                expect(result.severeIncidents.places).to.be.an('array').that.is.empty;
            });
        });

        describe('validation', () => {
            it('should throw BadRequestError when neither sensorId nor place provided', async () => {
                const input = {
                    fromTimestamp: '2023-01-12T11:20:10.000Z',
                    toTimestamp: '2023-01-12T14:20:10.000Z'
                };

                try {
                    await incident.getSevereIncidentsResult(elasticDb, input);
                    throw new Error('Expected BadRequestError');
                } catch (error) {
                    expect(error).to.be.instanceOf(BadRequestError);
                    expect(error.message).to.include("Only one of 'sensorId' or 'place' should exist");
                }
            });

            it('should throw InvalidInputError when timestamps are null (default)', async () => {
                // Schema defaults fromTimestamp/toTimestamp to null, which passes schema validation
                // but fails time range validation with InvalidInputError
                const input = {
                    sensorId: 'sensor123'
                };

                try {
                    await incident.getSevereIncidentsResult(elasticDb, input);
                    throw new Error('Expected InvalidInputError');
                } catch (error) {
                    expect(error).to.be.instanceOf(InvalidInputError);
                    expect(error.message).to.equal('Invalid fromTimestamp.');
                }
            });

            it('should throw InvalidInputError for invalid time range', async () => {
                const input = {
                    sensorId: 'sensor123',
                    fromTimestamp: '2023-01-12T14:20:10.000Z',
                    toTimestamp: '2023-01-12T11:20:10.000Z'
                };

                try {
                    await incident.getSevereIncidentsResult(elasticDb, input);
                    throw new Error('Expected InvalidInputError');
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
                    await incident.getSevereIncidentsResult(unsupportedDb, input);
                    throw new Error('Expected InternalServerError');
                } catch (error) {
                    expect(error).to.be.instanceOf(InternalServerError);
                    expect(error.message).to.equal('Invalid database: UnsupportedDB.');
                }
            });
        });
    });
});
