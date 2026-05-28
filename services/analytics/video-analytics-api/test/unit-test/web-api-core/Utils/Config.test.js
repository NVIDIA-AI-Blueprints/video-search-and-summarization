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
const Config = require('../../../../src/web-api-core/Utils/Config.js');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError.js');

describe('Config', () => {

    describe('constructor', () => {
        it('should create a Config instance with a valid bootstrap object', () => {
            const bootstrap = {
                server: {
                    port: 3000,
                    configs: [{ name: 'timeout', value: 5000 }]
                },
                kafka: {
                    brokers: ['localhost:9092']
                },
                elasticsearch: {
                    node: 'http://localhost:9200',
                    indexPrefix: 'test_',
                    rawIndex: 'raw_data'
                }
            };
            const config = new Config({ bootstrap });

            expect(config).to.be.an.instanceof(Config);
        });

        it('should throw InvalidInputError when bootstrap is null', () => {
            expect(() => {
                new Config({ bootstrap: null });
            }).to.throw(InvalidInputError, 'bootstrap is a required param.');
        });

        it('should throw InvalidInputError when bootstrap is not provided', () => {
            expect(() => {
                new Config({});
            }).to.throw(InvalidInputError, 'bootstrap is a required param.');
        });

        it('should throw InvalidInputError when no arguments are provided', () => {
            expect(() => {
                new Config();
            }).to.throw(InvalidInputError, 'bootstrap is a required param.');
        });

        it('should create a Config instance with an empty bootstrap object', () => {
            const config = new Config({ bootstrap: {} });

            expect(config).to.be.an.instanceof(Config);
        });

        it('should create a Config instance with only server config', () => {
            const bootstrap = {
                server: {
                    port: 8080,
                    configs: [
                        { name: 'maxConnections', value: 100 },
                        { name: 'timeout', value: 3000 }
                    ]
                }
            };
            const config = new Config({ bootstrap });

            expect(config).to.be.an.instanceof(Config);
            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(8080);
            expect(bootstrapMap.server.configs.get('maxConnections')).to.equal(100);
            expect(bootstrapMap.server.configs.get('timeout')).to.equal(3000);
        });

        it('should create a Config instance with only kafka config', () => {
            const bootstrap = {
                kafka: {
                    brokers: ['broker1:9092', 'broker2:9092']
                }
            };
            const config = new Config({ bootstrap });

            expect(config).to.be.an.instanceof(Config);
            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['broker1:9092', 'broker2:9092']);
        });

        it('should handle kafka config without brokers property', () => {
            const bootstrap = {
                kafka: {}
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.kafka.brokers).to.be.null;
        });

        it('should create a Config instance with only elasticsearch config', () => {
            const bootstrap = {
                elasticsearch: {
                    node: 'http://es-node:9200',
                    indexPrefix: 'prod_',
                    rawIndex: 'raw_index'
                }
            };
            const config = new Config({ bootstrap });

            expect(config).to.be.an.instanceof(Config);
            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.elasticsearch.node).to.equal('http://es-node:9200');
            expect(bootstrapMap.elasticsearch.indexPrefix).to.equal('prod_');
            expect(bootstrapMap.elasticsearch.rawIndex).to.equal('raw_index');
        });

        it('should handle server config without port', () => {
            const bootstrap = {
                server: {
                    configs: [{ name: 'key', value: 'value' }]
                }
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.be.null;
            expect(bootstrapMap.server.configs.get('key')).to.equal('value');
        });

        it('should handle server config without configs array', () => {
            const bootstrap = {
                server: {
                    port: 4000
                }
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(4000);
            expect(bootstrapMap.server.configs.size).to.equal(0);
        });

        it('should handle elasticsearch config with only node', () => {
            const bootstrap = {
                elasticsearch: {
                    node: 'http://localhost:9200'
                }
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.elasticsearch.node).to.equal('http://localhost:9200');
            expect(bootstrapMap.elasticsearch.indexPrefix).to.be.null;
            expect(bootstrapMap.elasticsearch.rawIndex).to.be.null;
        });

        it('should handle elasticsearch config with only indexPrefix', () => {
            const bootstrap = {
                elasticsearch: {
                    indexPrefix: 'my_prefix_'
                }
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.elasticsearch.node).to.be.null;
            expect(bootstrapMap.elasticsearch.indexPrefix).to.equal('my_prefix_');
            expect(bootstrapMap.elasticsearch.rawIndex).to.be.null;
        });

        it('should handle elasticsearch config with only rawIndex', () => {
            const bootstrap = {
                elasticsearch: {
                    rawIndex: 'my_raw_index'
                }
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.elasticsearch.node).to.be.null;
            expect(bootstrapMap.elasticsearch.indexPrefix).to.be.null;
            expect(bootstrapMap.elasticsearch.rawIndex).to.equal('my_raw_index');
        });
    });

    describe('getBootstrapObjectMap', () => {
        it('should return the bootstrap object map', () => {
            const bootstrap = {
                server: {
                    port: 3000,
                    configs: [{ name: 'debug', value: true }]
                },
                kafka: {
                    brokers: ['localhost:9092']
                },
                elasticsearch: {
                    node: 'http://localhost:9200',
                    indexPrefix: 'test_',
                    rawIndex: 'raw'
                }
            };
            const config = new Config({ bootstrap });

            const bootstrapMap = config.getBootstrapObjectMap();

            expect(bootstrapMap).to.be.an('object');
            expect(bootstrapMap.server.port).to.equal(3000);
            expect(bootstrapMap.server.configs).to.be.an.instanceof(Map);
            expect(bootstrapMap.server.configs.get('debug')).to.equal(true);
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['localhost:9092']);
            expect(bootstrapMap.elasticsearch.node).to.equal('http://localhost:9200');
            expect(bootstrapMap.elasticsearch.indexPrefix).to.equal('test_');
            expect(bootstrapMap.elasticsearch.rawIndex).to.equal('raw');
        });

        it('should return bootstrap map with null values for unset properties', () => {
            const config = new Config({ bootstrap: {} });

            const bootstrapMap = config.getBootstrapObjectMap();

            expect(bootstrapMap.server.port).to.be.null;
            expect(bootstrapMap.server.configs).to.be.an.instanceof(Map);
            expect(bootstrapMap.server.configs.size).to.equal(0);
            expect(bootstrapMap.kafka.brokers).to.be.null;
            expect(bootstrapMap.elasticsearch.node).to.be.null;
            expect(bootstrapMap.elasticsearch.indexPrefix).to.be.null;
            expect(bootstrapMap.elasticsearch.rawIndex).to.be.null;
        });
    });

    describe('overrideBootstrapConfig', () => {
        it('should override existing bootstrap config values', () => {
            const initialBootstrap = {
                server: {
                    port: 3000,
                    configs: [{ name: 'timeout', value: 5000 }]
                },
                kafka: {
                    brokers: ['localhost:9092']
                },
                elasticsearch: {
                    node: 'http://localhost:9200',
                    indexPrefix: 'dev_',
                    rawIndex: 'dev_raw'
                }
            };
            const config = new Config({ bootstrap: initialBootstrap });

            const overrideBootstrap = {
                server: {
                    port: 8080,
                    configs: [{ name: 'timeout', value: 10000 }]
                },
                kafka: {
                    brokers: ['prod-broker:9092']
                },
                elasticsearch: {
                    node: 'http://prod-es:9200',
                    indexPrefix: 'prod_',
                    rawIndex: 'prod_raw'
                }
            };
            config.overrideBootstrapConfig(overrideBootstrap);

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(8080);
            expect(bootstrapMap.server.configs.get('timeout')).to.equal(10000);
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['prod-broker:9092']);
            expect(bootstrapMap.elasticsearch.node).to.equal('http://prod-es:9200');
            expect(bootstrapMap.elasticsearch.indexPrefix).to.equal('prod_');
            expect(bootstrapMap.elasticsearch.rawIndex).to.equal('prod_raw');
        });

        it('should add new config values while preserving existing ones', () => {
            const initialBootstrap = {
                server: {
                    port: 3000,
                    configs: [{ name: 'timeout', value: 5000 }]
                }
            };
            const config = new Config({ bootstrap: initialBootstrap });

            const overrideBootstrap = {
                server: {
                    configs: [{ name: 'maxRetries', value: 3 }]
                }
            };
            config.overrideBootstrapConfig(overrideBootstrap);

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(3000);
            expect(bootstrapMap.server.configs.get('timeout')).to.equal(5000);
            expect(bootstrapMap.server.configs.get('maxRetries')).to.equal(3);
        });

        it('should override only server config', () => {
            const initialBootstrap = {
                server: { port: 3000 },
                kafka: { brokers: ['localhost:9092'] },
                elasticsearch: { node: 'http://localhost:9200' }
            };
            const config = new Config({ bootstrap: initialBootstrap });

            config.overrideBootstrapConfig({
                server: { port: 9000 }
            });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(9000);
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['localhost:9092']);
            expect(bootstrapMap.elasticsearch.node).to.equal('http://localhost:9200');
        });

        it('should override only kafka config', () => {
            const initialBootstrap = {
                server: { port: 3000 },
                kafka: { brokers: ['localhost:9092'] },
                elasticsearch: { node: 'http://localhost:9200' }
            };
            const config = new Config({ bootstrap: initialBootstrap });

            config.overrideBootstrapConfig({
                kafka: { brokers: ['new-broker:9092'] }
            });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(3000);
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['new-broker:9092']);
            expect(bootstrapMap.elasticsearch.node).to.equal('http://localhost:9200');
        });

        it('should override only elasticsearch config', () => {
            const initialBootstrap = {
                server: { port: 3000 },
                kafka: { brokers: ['localhost:9092'] },
                elasticsearch: { node: 'http://localhost:9200', indexPrefix: 'old_', rawIndex: 'old_raw' }
            };
            const config = new Config({ bootstrap: initialBootstrap });

            config.overrideBootstrapConfig({
                elasticsearch: { node: 'http://new-es:9200', indexPrefix: 'new_', rawIndex: 'new_raw' }
            });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(3000);
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['localhost:9092']);
            expect(bootstrapMap.elasticsearch.node).to.equal('http://new-es:9200');
            expect(bootstrapMap.elasticsearch.indexPrefix).to.equal('new_');
            expect(bootstrapMap.elasticsearch.rawIndex).to.equal('new_raw');
        });

        it('should handle empty override config', () => {
            const initialBootstrap = {
                server: { port: 3000 },
                kafka: { brokers: ['localhost:9092'] }
            };
            const config = new Config({ bootstrap: initialBootstrap });

            config.overrideBootstrapConfig({});

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(3000);
            expect(bootstrapMap.kafka.brokers).to.deep.equal(['localhost:9092']);
        });

        it('should allow multiple overrides', () => {
            const config = new Config({ bootstrap: { server: { port: 1000 } } });

            config.overrideBootstrapConfig({ server: { port: 2000 } });
            config.overrideBootstrapConfig({ server: { port: 3000 } });

            const bootstrapMap = config.getBootstrapObjectMap();
            expect(bootstrapMap.server.port).to.equal(3000);
        });
    });

});
