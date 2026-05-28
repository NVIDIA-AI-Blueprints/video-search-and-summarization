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

describe('Cache Initializer', () => {
    let cache;

    beforeEach(() => {
        // Clear require cache to get fresh instance
        delete require.cache[require.resolve('../../../../src/app/initializers/cache')];
        cache = require('../../../../src/app/initializers/cache');
    });

    describe('NodeCache instance', () => {
        it('should be a NodeCache instance', () => {
            expect(cache).to.have.property('get');
            expect(cache).to.have.property('set');
            expect(cache).to.have.property('del');
            expect(cache).to.have.property('has');
        });

        it('should set and get values', () => {
            cache.set('test-key', 'test-value');
            const value = cache.get('test-key');
            expect(value).to.equal('test-value');
        });

        it('should return undefined for non-existent keys', () => {
            const value = cache.get('non-existent-key');
            expect(value).to.be.undefined;
        });

        it('should check if key exists', () => {
            cache.set('existing-key', 'value');
            expect(cache.has('existing-key')).to.be.true;
            expect(cache.has('non-existing-key')).to.be.false;
        });

        it('should delete keys', () => {
            cache.set('to-delete', 'value');
            expect(cache.has('to-delete')).to.be.true;
            cache.del('to-delete');
            expect(cache.has('to-delete')).to.be.false;
        });

        it('should store objects', () => {
            const obj = { 
                server: { port: 8080 },
                configs: new Map([['key', 'value']])
            };
            cache.set('config-object', obj);
            const retrieved = cache.get('config-object');
            expect(retrieved.server.port).to.equal(8080);
        });
    });
});
