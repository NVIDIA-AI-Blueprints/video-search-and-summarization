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
const Validator = require('../../../../src/web-api-core/Utils/Validator.js');

describe('Validator', () => {

    describe('isStringAFiniteNumber', () => {
        it('should return true for a valid finite number string', () => {
            expect(Validator.isStringAFiniteNumber('12.5')).to.be.true;
        });

        it('should return true for integer string', () => {
            expect(Validator.isStringAFiniteNumber('42')).to.be.true;
        });

        it('should return true for negative number string', () => {
            expect(Validator.isStringAFiniteNumber('-123.45')).to.be.true;
        });

        it('should return true for zero string', () => {
            expect(Validator.isStringAFiniteNumber('0')).to.be.true;
        });

        it('should return false for non-string input (number)', () => {
            expect(Validator.isStringAFiniteNumber(123)).to.be.false;
        });

        it('should return false for empty string', () => {
            expect(Validator.isStringAFiniteNumber('')).to.be.false;
        });

        it('should return false for whitespace-only string', () => {
            expect(Validator.isStringAFiniteNumber('   ')).to.be.false;
        });

        it('should return false for Infinity string', () => {
            expect(Validator.isStringAFiniteNumber('Infinity')).to.be.false;
        });

        it('should return false for NaN string', () => {
            expect(Validator.isStringAFiniteNumber('NaN')).to.be.false;
        });

        it('should return false for non-numeric string', () => {
            expect(Validator.isStringAFiniteNumber('abc')).to.be.false;
        });

        it('should return true for string with leading/trailing whitespace', () => {
            expect(Validator.isStringAFiniteNumber('  42  ')).to.be.true;
        });
    });

    describe('isStringAFiniteInteger', () => {
        it('should return true for integer string', () => {
            expect(Validator.isStringAFiniteInteger('42')).to.be.true;
        });

        it('should return true for negative integer string', () => {
            expect(Validator.isStringAFiniteInteger('-42')).to.be.true;
        });

        it('should return true for zero string', () => {
            expect(Validator.isStringAFiniteInteger('0')).to.be.true;
        });

        it('should return false for decimal number string', () => {
            expect(Validator.isStringAFiniteInteger('12.5')).to.be.false;
        });

        it('should return false for non-string input', () => {
            expect(Validator.isStringAFiniteInteger(42)).to.be.false;
        });

        it('should return false for empty string', () => {
            expect(Validator.isStringAFiniteInteger('')).to.be.false;
        });

        it('should return false for non-numeric string', () => {
            expect(Validator.isStringAFiniteInteger('abc')).to.be.false;
        });
    });

    describe('isValidISOTimestamp', () => {
        it('should return true for valid ISO timestamp', () => {
            expect(Validator.isValidISOTimestamp('2023-01-12T11:20:10.000Z')).to.be.true;
        });

        it('should return false for invalid timestamp format', () => {
            expect(Validator.isValidISOTimestamp('2023-01-12')).to.be.false;
        });

        it('should return false for invalid date', () => {
            expect(Validator.isValidISOTimestamp('invalid-timestamp')).to.be.false;
        });

        it('should return false for empty string', () => {
            expect(Validator.isValidISOTimestamp('')).to.be.false;
        });
    });

    describe('isValidTimeRange', () => {
        it('should return valid true for valid time range', () => {
            const result = Validator.isValidTimeRange(
                '2023-01-12T11:20:10.000Z',
                '2023-01-12T14:20:10.000Z'
            );
            expect(result.valid).to.be.true;
            expect(result.reason).to.be.null;
        });

        it('should return invalid for invalid fromTimestamp', () => {
            const result = Validator.isValidTimeRange(
                'invalid-timestamp',
                '2023-01-12T14:20:10.000Z'
            );
            expect(result.valid).to.be.false;
            expect(result.reason).to.equal('Invalid fromTimestamp.');
        });

        it('should return invalid for invalid toTimestamp', () => {
            const result = Validator.isValidTimeRange(
                '2023-01-12T11:20:10.000Z',
                'invalid-timestamp'
            );
            expect(result.valid).to.be.false;
            expect(result.reason).to.equal('Invalid toTimestamp.');
        });

        it('should return invalid when fromTimestamp is not less than toTimestamp', () => {
            const result = Validator.isValidTimeRange(
                '2023-01-12T14:20:10.000Z',
                '2023-01-12T11:20:10.000Z'
            );
            expect(result.valid).to.be.false;
            expect(result.reason).to.equal('fromTimestamp is not lesser than toTimestamp.');
        });

        it('should return invalid when fromTimestamp equals toTimestamp', () => {
            const result = Validator.isValidTimeRange(
                '2023-01-12T11:20:10.000Z',
                '2023-01-12T11:20:10.000Z'
            );
            expect(result.valid).to.be.false;
            expect(result.reason).to.equal('fromTimestamp is not lesser than toTimestamp.');
        });
    });

    describe('validateJsonSchema', () => {
        it('should return valid for input matching schema', () => {
            const schema = {
                type: 'object',
                properties: {
                    fromTimestamp: { type: 'string' },
                    toTimestamp: { type: 'string' },
                    bucketCount: { type: 'integer' }
                },
                required: ['fromTimestamp', 'toTimestamp']
            };
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                bucketCount: 10
            };

            const result = Validator.validateJsonSchema(input, schema);

            expect(result.valid).to.be.true;
            expect(result.reason).to.be.null;
        });

        it('should return invalid for input not matching schema', () => {
            const schema = {
                type: 'object',
                properties: {
                    fromTimestamp: { type: 'string' }
                },
                required: ['fromTimestamp']
            };
            const input = {};

            const result = Validator.validateJsonSchema(input, schema);

            expect(result.valid).to.be.false;
            expect(result.reason).to.include('Invalid input');
        });

        it('should apply default values from schema', () => {
            const schema = {
                type: 'object',
                properties: {
                    fromTimestamp: { type: 'string' },
                    bucketSizeInSec: { type: 'integer', default: 600 }
                }
            };
            const input = { fromTimestamp: '2023-01-12T11:20:10.000Z' };

            Validator.validateJsonSchema(input, schema);

            expect(input.bucketSizeInSec).to.equal(600);
        });

        it('should handle multiple validation errors', () => {
            const schema = {
                type: 'object',
                properties: {
                    fromTimestamp: { type: 'string' },
                    toTimestamp: { type: 'string' }
                },
                required: ['fromTimestamp', 'toTimestamp']
            };
            const input = {};

            const result = Validator.validateJsonSchema(input, schema);

            expect(result.valid).to.be.false;
            expect(result.reason).to.include('Error 1:');
            expect(result.reason).to.include('Error 2:');
        });

        it('should coerce types when coerceTypes is true (default)', () => {
            const schema = {
                type: 'object',
                properties: {
                    bucketCount: { type: 'integer' }
                }
            };
            const input = { bucketCount: '42' };

            const result = Validator.validateJsonSchema(input, schema, true);

            expect(result.valid).to.be.true;
        });

        it('should not coerce types when coerceTypes is false', () => {
            const schema = {
                type: 'object',
                properties: {
                    bucketCount: { type: 'integer' }
                }
            };
            const input = { bucketCount: '42' };

            const result = Validator.validateJsonSchema(input, schema, false);

            expect(result.valid).to.be.false;
        });

        it('should handle custom error messages', () => {
            const schema = {
                type: 'object',
                properties: {
                    bucketCount: {
                        type: 'integer',
                        minimum: 1,
                        errorMessage: {
                            type: 'bucketCount is not an integer.',
                            minimum: 'bucketCount can have a minimum value of 1.'
                        }
                    }
                }
            };
            const input = { bucketCount: 'not a number' };

            const result = Validator.validateJsonSchema(input, schema, false);

            expect(result.valid).to.be.false;
        });
    });

});
