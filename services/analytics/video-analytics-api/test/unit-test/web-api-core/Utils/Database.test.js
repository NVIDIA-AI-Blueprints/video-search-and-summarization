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
const Database = require('../../../../src/web-api-core/Utils/Database.js');

describe('Database', () => {

    describe('constructor', () => {
        it('should create a Database instance with name, client, and configs', () => {
            const mockClient = { query: () => {} };
            const mockConfigs = new Map([['key1', 'value1']]);
            const db = new Database({ name: 'TestDB', client: mockClient, configs: mockConfigs });

            expect(db).to.be.an.instanceof(Database);
        });

        it('should create a Database instance with default undefined values', () => {
            const db = new Database();

            expect(db).to.be.an.instanceof(Database);
            expect(db.getName()).to.be.undefined;
            expect(db.getClient()).to.be.undefined;
            expect(db.getConfigs()).to.be.undefined;
        });

        it('should create a Database instance with partial parameters', () => {
            const db = new Database({ name: 'PartialDB' });

            expect(db.getName()).to.equal('PartialDB');
            expect(db.getClient()).to.be.undefined;
            expect(db.getConfigs()).to.be.undefined;
        });
    });

    describe('getName', () => {
        it('should return the database name', () => {
            const db = new Database({ name: 'MyDatabase', client: {}, configs: new Map() });

            expect(db.getName()).to.equal('MyDatabase');
        });

        it('should return undefined if name was not provided', () => {
            const db = new Database({ client: {}, configs: new Map() });

            expect(db.getName()).to.be.undefined;
        });
    });

    describe('getClient', () => {
        it('should return the database client', () => {
            const mockClient = { connect: () => {}, query: () => {} };
            const db = new Database({ name: 'TestDB', client: mockClient, configs: new Map() });

            expect(db.getClient()).to.equal(mockClient);
        });

        it('should return undefined if client was not provided', () => {
            const db = new Database({ name: 'TestDB', configs: new Map() });

            expect(db.getClient()).to.be.undefined;
        });
    });

    describe('getConfigs', () => {
        it('should return the database configs', () => {
            const mockConfigs = new Map([
                ['host', 'localhost'],
                ['port', 9200]
            ]);
            const db = new Database({ name: 'TestDB', client: {}, configs: mockConfigs });

            expect(db.getConfigs()).to.equal(mockConfigs);
            expect(db.getConfigs().get('host')).to.equal('localhost');
            expect(db.getConfigs().get('port')).to.equal(9200);
        });

        it('should return undefined if configs was not provided', () => {
            const db = new Database({ name: 'TestDB', client: {} });

            expect(db.getConfigs()).to.be.undefined;
        });
    });

});
