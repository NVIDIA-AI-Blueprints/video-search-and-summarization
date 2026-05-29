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
const IndexNotFoundError = require('../../../../src/web-api-core/Errors/IndexNotFoundError.js');

describe('IndexNotFoundError', () => {

    it('should create an instance of IndexNotFoundError with the correct message', () => {
        const errorMessage = 'Index not found in database';
        const error = new IndexNotFoundError(errorMessage);

        expect(error).to.be.an.instanceof(IndexNotFoundError);
        expect(error.message).to.equal(errorMessage);
    });

    it('should have the name property set to "IndexNotFoundError"', () => {
        const error = new IndexNotFoundError('Test error');

        expect(error.name).to.equal('IndexNotFoundError');
    });

    it('should be an instance of Error', () => {
        const error = new IndexNotFoundError('Test error');

        expect(error).to.be.an.instanceof(Error);
    });

    it('should have a stack trace', () => {
        const error = new IndexNotFoundError('Test error');

        expect(error.stack).to.be.a('string');
        expect(error.stack).to.include('IndexNotFoundError');
    });

    it('should handle empty message', () => {
        const error = new IndexNotFoundError('');

        expect(error.message).to.equal('');
        expect(error.name).to.equal('IndexNotFoundError');
    });

    it('should be throwable and catchable', () => {
        const errorMessage = 'Index xyz not found';

        expect(() => {
            throw new IndexNotFoundError(errorMessage);
        }).to.throw(IndexNotFoundError, errorMessage);
    });

});
