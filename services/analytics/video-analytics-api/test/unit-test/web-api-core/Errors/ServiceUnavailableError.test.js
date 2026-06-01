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
const ServiceUnavailableError = require('../../../../src/web-api-core/Errors/ServiceUnavailableError.js');

describe('ServiceUnavailableError', () => {

    it('should create an instance of ServiceUnavailableError with the correct message', () => {
        const errorMessage = 'Service temporarily unavailable';
        const error = new ServiceUnavailableError(errorMessage);

        expect(error).to.be.an.instanceof(ServiceUnavailableError);
        expect(error.message).to.equal(errorMessage);
    });

    it('should have the name property set to "ServiceUnavailableError"', () => {
        const error = new ServiceUnavailableError('Test error');

        expect(error.name).to.equal('ServiceUnavailableError');
    });

    it('should be an instance of Error', () => {
        const error = new ServiceUnavailableError('Test error');

        expect(error).to.be.an.instanceof(Error);
    });

    it('should have a stack trace', () => {
        const error = new ServiceUnavailableError('Test error');

        expect(error.stack).to.be.a('string');
        expect(error.stack).to.include('ServiceUnavailableError');
    });

    it('should handle empty message', () => {
        const error = new ServiceUnavailableError('');

        expect(error.message).to.equal('');
        expect(error.name).to.equal('ServiceUnavailableError');
    });

    it('should be throwable and catchable', () => {
        const errorMessage = 'Database service unavailable';

        expect(() => {
            throw new ServiceUnavailableError(errorMessage);
        }).to.throw(ServiceUnavailableError, errorMessage);
    });

});
