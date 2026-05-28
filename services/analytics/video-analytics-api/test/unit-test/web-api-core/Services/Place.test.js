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
const Place = require('../../../../src/web-api-core/Services/Place');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');

describe('Place', () => {

    describe('buildEsSensorIdAggQueryForLeafPlace', () => {
        it('should return a valid query body with sensor.id field', () => {
            const input = {
                place: 'city=abc/building=xyz',
                sensorIdField: 'sensor.id'
            };

            const result = Place.buildEsSensorIdAggQueryForLeafPlace(input);

            expect(result).to.deep.equal({
                query: {
                    bool: {
                        must: [
                            { term: { 'place.name.keyword': 'city=abc/building=xyz' } }
                        ]
                    }
                },
                aggs: {
                    sensorIds: {
                        terms: {
                            field: 'sensor.id.keyword',
                            size: 10000
                        }
                    }
                }
            });
        });

        it('should return a valid query body with sensorId field', () => {
            const input = {
                place: 'city=abc/building=xyz',
                sensorIdField: 'sensorId'
            };

            const result = Place.buildEsSensorIdAggQueryForLeafPlace(input);

            expect(result).to.have.property('query');
            expect(result).to.have.property('aggs');
            expect(result.aggs.sensorIds.terms.field).to.equal('sensorId.keyword');
        });

        it('should include place term in query', () => {
            const input = {
                place: 'city=abc/building=xyz/room=123',
                sensorIdField: 'sensor.id'
            };

            const result = Place.buildEsSensorIdAggQueryForLeafPlace(input);

            const placeTermClause = result.query.bool.must.find(
                clause => clause.term && clause.term['place.name.keyword']
            );
            expect(placeTermClause).to.exist;
            expect(placeTermClause.term['place.name.keyword']).to.equal('city=abc/building=xyz/room=123');
        });

        it('should throw InvalidInputError when place is missing', () => {
            const input = {
                sensorIdField: 'sensor.id'
            };

            expect(() => {
                Place.buildEsSensorIdAggQueryForLeafPlace(input);
            }).to.throw(InvalidInputError, "Input should have required properties 'place' and 'sensorIdField'");
        });

        it('should throw InvalidInputError when sensorIdField is missing', () => {
            const input = {
                place: 'city=abc'
            };

            expect(() => {
                Place.buildEsSensorIdAggQueryForLeafPlace(input);
            }).to.throw(InvalidInputError, "Input should have required properties 'place' and 'sensorIdField'");
        });

        it('should throw InvalidInputError when sensorIdField has invalid value', () => {
            const input = {
                place: 'city=abc',
                sensorIdField: 'invalidField'
            };

            expect(() => {
                Place.buildEsSensorIdAggQueryForLeafPlace(input);
            }).to.throw(InvalidInputError, "sensorIdField must be one of the following values");
        });

        it('should throw InvalidInputError when place is empty string', () => {
            const input = {
                place: '',
                sensorIdField: 'sensor.id'
            };

            expect(() => {
                Place.buildEsSensorIdAggQueryForLeafPlace(input);
            }).to.throw(InvalidInputError, 'place should have atleast 1 character.');
        });

        it('should throw InvalidInputError for additional properties', () => {
            const input = {
                place: 'city=abc',
                sensorIdField: 'sensor.id',
                extraField: 'should not be here'
            };

            expect(() => {
                Place.buildEsSensorIdAggQueryForLeafPlace(input);
            }).to.throw(InvalidInputError, "Invalid additional Input 'extraField'");
        });
    });

    describe('buildEsPlaceSuccessorAggQueryForNonLeafPlace', () => {
        it('should return a valid query body for a place', () => {
            const input = {
                place: 'city=abc'
            };

            const result = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);

            expect(result).to.have.property('query');
            expect(result).to.have.property('aggs');
            expect(result.aggs).to.have.property('placeSuccessor');
        });

        it('should include place prefix in query', () => {
            const input = {
                place: 'city=abc/building=xyz'
            };

            const result = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);

            const prefixClause = result.query.bool.must.find(
                clause => clause.prefix && clause.prefix['place.name.keyword']
            );
            expect(prefixClause).to.exist;
            expect(prefixClause.prefix['place.name.keyword']).to.equal('city=abc/building=xyz');
        });

        it('should generate correct painless script for place hierarchy level 1', () => {
            const input = {
                place: 'city=abc'
            };

            const result = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);

            expect(result.aggs.placeSuccessor.terms.script.lang).to.equal('painless');
            expect(result.aggs.placeSuccessor.terms.script.source).to.include('i==2');
        });

        it('should generate correct painless script for place hierarchy level 2', () => {
            const input = {
                place: 'city=abc/building=xyz'
            };

            const result = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);

            expect(result.aggs.placeSuccessor.terms.script.source).to.include('i==3');
        });

        it('should throw InvalidInputError when place is missing', () => {
            const input = {};

            expect(() => {
                Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);
            }).to.throw(InvalidInputError, "Input should have the required property 'place'");
        });

        it('should throw InvalidInputError when place is empty string', () => {
            const input = {
                place: ''
            };

            expect(() => {
                Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);
            }).to.throw(InvalidInputError, 'place should have atleast 1 character.');
        });

        it('should throw InvalidInputError for additional properties', () => {
            const input = {
                place: 'city=abc',
                extraField: 'should not be here'
            };

            expect(() => {
                Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);
            }).to.throw(InvalidInputError, "Invalid additional Input 'extraField'");
        });

        it('should set aggregation size to 10000', () => {
            const input = {
                place: 'city=abc'
            };

            const result = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);

            expect(result.aggs.placeSuccessor.terms.size).to.equal(10000);
        });
    });

});
