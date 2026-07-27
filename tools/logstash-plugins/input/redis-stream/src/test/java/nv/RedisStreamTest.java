/*
 * SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

package nv;

import co.elastic.logstash.api.Configuration;
import co.elastic.logstash.api.PluginConfigSpec;
import org.junit.Assert;
import org.junit.Test;
import org.logstash.plugins.ConfigurationImpl;

import java.util.Collection;
import java.util.HashMap;
import java.util.Map;

public class RedisStreamTest {

    @Test
    public void testRedisStreamWithDefaultConfig() {
        Map<String, Object> configValues = new HashMap<>();
        Configuration config = new ConfigurationImpl(configValues);
        RedisStream input = new RedisStream("test-id", config, null);
        
        Assert.assertEquals("test-id", input.getId());
    }

    @Test
    public void testRedisStreamWithCustomConfig() {
        Map<String, Object> configValues = new HashMap<>();
        configValues.put(RedisStream.HOST_CONFIG.name(), "redis.example.com");
        configValues.put(RedisStream.PORT_CONFIG.name(), 6380L);
        configValues.put(RedisStream.PASSWORD_CONFIG.name(), "secret");
        configValues.put(RedisStream.STREAM_KEY_CONFIG.name(), "my_custom_stream");
        configValues.put(RedisStream.GROUP_CONFIG.name(), "my_group");
        configValues.put(RedisStream.BLOCK_MS_CONFIG.name(), 500L);
        configValues.put(RedisStream.BATCH_SIZE_CONFIG.name(), 50L);
        configValues.put(RedisStream.CREATE_GROUP_CONFIG.name(), false);
        configValues.put(RedisStream.DATA_FIELD_CONFIG.name(), "data");
        configValues.put(RedisStream.TYPE_CONFIG.name(), "redis_event");
        configValues.put(RedisStream.DECORATE_EVENTS_CONFIG.name(), false);
        
        Configuration config = new ConfigurationImpl(configValues);
        RedisStream input = new RedisStream("custom-id", config, null);
        
        Assert.assertEquals("custom-id", input.getId());
    }

    @Test
    public void testRedisStreamWithProtobufDataCodec() {
        Map<String, Object> configValues = new HashMap<>();
        
        Map<String, Object> dataCodec = new HashMap<>();
        dataCodec.put("type", "protobuf");
        dataCodec.put("class_name", "nv.Frame");
        dataCodec.put("class_file", "/path/to/schema.desc");
        configValues.put(RedisStream.DATA_CODEC_CONFIG.name(), dataCodec);
        
        Configuration config = new ConfigurationImpl(configValues);
        RedisStream input = new RedisStream("protobuf-id", config, null);
        
        Assert.assertEquals("protobuf-id", input.getId());
    }

    @Test
    public void testConfigSchema() {
        Map<String, Object> configValues = new HashMap<>();
        Configuration config = new ConfigurationImpl(configValues);
        RedisStream input = new RedisStream("test-id", config, null);
        
        Collection<PluginConfigSpec<?>> configSchema = input.configSchema();
        
        // Should contain all 13 config specs
        Assert.assertEquals(13, configSchema.size());
        
        // Verify all expected configs are present
        Assert.assertTrue(configSchema.contains(RedisStream.HOST_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.PORT_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.PASSWORD_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.STREAM_KEY_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.GROUP_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.BLOCK_MS_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.BATCH_SIZE_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.CREATE_GROUP_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.CODEC_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.DATA_FIELD_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.TYPE_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.DECORATE_EVENTS_CONFIG));
        Assert.assertTrue(configSchema.contains(RedisStream.DATA_CODEC_CONFIG));
    }

    @Test
    public void testStopAndAwaitStop() throws InterruptedException {
        Map<String, Object> configValues = new HashMap<>();
        Configuration config = new ConfigurationImpl(configValues);
        RedisStream input = new RedisStream("stop-test-id", config, null);
        
        // Start in a separate thread (will block trying to connect to Redis)
        Thread inputThread = new Thread(() -> {
            input.start(event -> {
                // no-op consumer
            });
        });
        inputThread.start();
        
        // Give it a moment to start
        Thread.sleep(100);
        
        // Stop the input
        input.stop();
        
        // Wait for it to stop, with margin over the 2s connect timeout to avoid CI flakiness
        inputThread.join(5000);
        
        // Should have stopped
        Assert.assertFalse(inputThread.isAlive());
    }

    @Test
    public void testGetId() {
        Map<String, Object> configValues = new HashMap<>();
        Configuration config = new ConfigurationImpl(configValues);
        
        RedisStream input1 = new RedisStream("unique-id-1", config, null);
        RedisStream input2 = new RedisStream("unique-id-2", config, null);
        
        Assert.assertEquals("unique-id-1", input1.getId());
        Assert.assertEquals("unique-id-2", input2.getId());
        Assert.assertNotEquals(input1.getId(), input2.getId());
    }
}

