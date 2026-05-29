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
const MessageBroker = require('../../../../src/web-api-core/Utils/MessageBroker.js');

describe('MessageBroker', () => {

    describe('constructor', () => {
        it('should create a MessageBroker instance with name, client, and configs', () => {
            const mockClient = { publish: () => {} };
            const mockConfigs = new Map([['key1', 'value1']]);
            const broker = new MessageBroker({ name: 'TestBroker', client: mockClient, configs: mockConfigs });

            expect(broker).to.be.an.instanceof(MessageBroker);
        });

        it('should create a MessageBroker instance with default undefined values', () => {
            const broker = new MessageBroker();

            expect(broker).to.be.an.instanceof(MessageBroker);
            expect(broker.getName()).to.be.undefined;
            expect(broker.getClient()).to.be.undefined;
            expect(broker.getConfigs()).to.be.undefined;
        });

        it('should create a MessageBroker instance with partial parameters', () => {
            const broker = new MessageBroker({ name: 'PartialBroker' });

            expect(broker.getName()).to.equal('PartialBroker');
            expect(broker.getClient()).to.be.undefined;
            expect(broker.getConfigs()).to.be.undefined;
        });
    });

    describe('getName', () => {
        it('should return the message broker name', () => {
            const broker = new MessageBroker({ name: 'Kafka', client: {}, configs: new Map() });

            expect(broker.getName()).to.equal('Kafka');
        });

        it('should return undefined if name was not provided', () => {
            const broker = new MessageBroker({ client: {}, configs: new Map() });

            expect(broker.getName()).to.be.undefined;
        });
    });

    describe('getClient', () => {
        it('should return the message broker client', () => {
            const mockClient = { connect: () => {}, publish: () => {} };
            const broker = new MessageBroker({ name: 'TestBroker', client: mockClient, configs: new Map() });

            expect(broker.getClient()).to.equal(mockClient);
        });

        it('should return undefined if client was not provided', () => {
            const broker = new MessageBroker({ name: 'TestBroker', configs: new Map() });

            expect(broker.getClient()).to.be.undefined;
        });
    });

    describe('getConfigs', () => {
        it('should return the message broker configs', () => {
            const mockConfigs = new Map([
                ['brokers', ['localhost:9092']],
                ['timeout', 30000]
            ]);
            const broker = new MessageBroker({ name: 'TestBroker', client: {}, configs: mockConfigs });

            expect(broker.getConfigs()).to.equal(mockConfigs);
            expect(broker.getConfigs().get('timeout')).to.equal(30000);
        });

        it('should return undefined if configs was not provided', () => {
            const broker = new MessageBroker({ name: 'TestBroker', client: {} });

            expect(broker.getConfigs()).to.be.undefined;
        });
    });

});
