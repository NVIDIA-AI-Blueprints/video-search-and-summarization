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

import co.elastic.logstash.api.*;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.util.JsonFormat;
import com.google.protobuf.Descriptors;
import com.google.protobuf.DescriptorProtos;
import com.google.protobuf.DynamicMessage;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;
import redis.clients.jedis.resps.StreamEntryBinary;
import redis.clients.jedis.StreamEntryID;
import redis.clients.jedis.params.XReadGroupParams;
import redis.clients.jedis.exceptions.JedisException;
import redis.clients.jedis.exceptions.JedisConnectionException;
import redis.clients.jedis.exceptions.JedisDataException;

import java.nio.charset.StandardCharsets;
import java.io.FileInputStream;
import java.lang.reflect.Type;
import java.util.*;
import java.util.concurrent.CountDownLatch;
import java.util.function.Consumer;

@LogstashPlugin(name = "redis_stream")
public class RedisStream implements Input {

    private static final Logger logger = LogManager.getLogger(RedisStream.class);
    private static final Gson GSON = new GsonBuilder().serializeNulls().create();
    private static final JsonFormat.Printer PROTO_PRINTER = JsonFormat.printer()
            .includingDefaultValueFields()
            .preservingProtoFieldNames();
    private static final Type PROTO_EVENT_TYPE = new TypeToken<Map<String, Object>>() {}.getType();

    // ==== Config schema ====
    public static final PluginConfigSpec<String> HOST_CONFIG =
            PluginConfigSpec.stringSetting("host", "localhost");

    public static final PluginConfigSpec<Long> PORT_CONFIG =
            PluginConfigSpec.numSetting("port", 6379);

    public static final PluginConfigSpec<String> PASSWORD_CONFIG =
            PluginConfigSpec.stringSetting("password", null);

    public static final PluginConfigSpec<String> STREAM_KEY_CONFIG =
            PluginConfigSpec.stringSetting("stream_key", "mystream");

    public static final PluginConfigSpec<String> GROUP_CONFIG =
            PluginConfigSpec.stringSetting("group", "logstash_group");

    public static final PluginConfigSpec<Long> BLOCK_MS_CONFIG =
            PluginConfigSpec.numSetting("block_ms", 100L);

    public static final PluginConfigSpec<Long> BATCH_SIZE_CONFIG =
            PluginConfigSpec.numSetting("batch_size", 100L);

    public static final PluginConfigSpec<Boolean> CREATE_GROUP_CONFIG =
            PluginConfigSpec.booleanSetting("create_group", true);

    public static final PluginConfigSpec<Codec> CODEC_CONFIG =
            PluginConfigSpec.codecSetting("codec", "plain");

    public static final PluginConfigSpec<String> DATA_FIELD_CONFIG =
            PluginConfigSpec.stringSetting("data_field", "value");

    public static final PluginConfigSpec<String> TYPE_CONFIG =
            PluginConfigSpec.stringSetting("type", null);

    public static final PluginConfigSpec<Boolean> DECORATE_EVENTS_CONFIG =
            PluginConfigSpec.booleanSetting("decorate_events", true);

    public static final PluginConfigSpec<Map<String, Object>> DATA_CODEC_CONFIG =
            PluginConfigSpec.hashSetting("data_codec", Collections.emptyMap(), false, false);

    private final String id;
    private final String host;
    private final long port;
    private final String password;
    private final String streamKey;
    private final String group;
    private final String consumer;
    private final long blockMs;
    private final long batchSize;
    private final boolean createGroup;
    private final Codec codec;
    private final String dataField;
    private final String type;
    private final boolean decorateEvents;
    private final Map<String, Object> dataCodecConfig;

    private volatile boolean stopped = false;
    private final CountDownLatch done = new CountDownLatch(1);

    private JedisPool jedisPool;
    
    // Cache for loaded descriptors (key: "descriptorPath:messageName")
    private final Map<String, Descriptors.Descriptor> descriptorCache = new HashMap<>();

    public RedisStream(String id, Configuration config, Context context) {
        this.id = id;
        this.host = config.get(HOST_CONFIG);
        this.port = config.get(PORT_CONFIG);
        this.password = config.get(PASSWORD_CONFIG);
        this.streamKey = config.get(STREAM_KEY_CONFIG);
        this.group = config.get(GROUP_CONFIG);
        // Generate unique consumer name: c<random_64bit_hex>
        this.consumer = generateUniqueConsumerName();
        this.blockMs = config.get(BLOCK_MS_CONFIG);
        this.batchSize = config.get(BATCH_SIZE_CONFIG);
        this.createGroup = config.get(CREATE_GROUP_CONFIG);
        this.codec = config.get(CODEC_CONFIG);
        this.dataField = config.get(DATA_FIELD_CONFIG);
        this.type = config.get(TYPE_CONFIG);
        this.decorateEvents = config.get(DECORATE_EVENTS_CONFIG);
        this.dataCodecConfig = config.get(DATA_CODEC_CONFIG);

        // Validate numeric config that is later narrowed to int, so bad or overflowing
        // values fail fast with a clear message instead of silently wrapping.
        if (this.port < 1 || this.port > 65535) {
            throw new IllegalArgumentException("port must be between 1 and 65535, but was " + this.port);
        }
        if (this.blockMs < 0 || this.blockMs > Integer.MAX_VALUE) {
            throw new IllegalArgumentException("block_ms must be between 0 and " + Integer.MAX_VALUE + ", but was " + this.blockMs);
        }
        if (this.batchSize < 1 || this.batchSize > Integer.MAX_VALUE) {
            throw new IllegalArgumentException("batch_size must be between 1 and " + Integer.MAX_VALUE + ", but was " + this.batchSize);
        }

        logger.info("Initialized with data_codec config: {}", dataCodecConfig);
        logger.info("Generated unique consumer name: {}", this.consumer);
    }
    
    /**
     * Generate a unique consumer name using UUID
     * This ensures uniqueness across all instances
     */
    private String generateUniqueConsumerName() {
        return "c" + UUID.randomUUID().toString().replace("-", "");
    }

    @Override
    public void start(Consumer<Map<String, Object>> consumerFn) {
        logger.info("Registering Redis Stream Input for {}:{} stream:{}", host, port, streamKey);

        // Main runner loop
        while (!stopped) {
            try {
                // Lazy initialize connection pool
                if (jedisPool == null) {
                    jedisPool = connect();
                }
                
                // Run the stream listener
                streamRunner(consumerFn);
                
            } catch (Exception e) {
                logError(e);
                if (resetForErrorRetry(e)) {
                    continue; // retry
                } else {
                    break; // stop
                }
            }
        }
        
        done.countDown();
    }

    /**
     * Connect to Redis and initialize pool
     */
    private JedisPool connect() {
        JedisPoolConfig poolConfig = new JedisPoolConfig();
        poolConfig.setMaxTotal(10);
        poolConfig.setMaxIdle(5);
        poolConfig.setMinIdle(1);
        poolConfig.setTestOnBorrow(true);

        JedisPool pool;
        if (password != null && !password.isEmpty()) {
            pool = new JedisPool(poolConfig, host, (int) port, 2000, password);
        } else {
            pool = new JedisPool(poolConfig, host, (int) port, 2000);
        }

        // Create consumer group if needed
        if (createGroup) {
            try (Jedis jedis = pool.getResource()) {
                try {
                    jedis.xgroupCreate(streamKey, group, new StreamEntryID(0, 0), true);
                    logger.info("Created consumer group '{}' for stream '{}'", group, streamKey);
                } catch (JedisDataException e) {
                    // BUSYGROUP means the group already exists, which is fine. Anything else
                    // (auth, ACL, wrong key type, ...) is a real error and must propagate.
                    if (e.getMessage() != null && e.getMessage().contains("BUSYGROUP")) {
                        logger.info("Consumer group '{}' already exists for stream '{}'", group, streamKey);
                    } else {
                        throw e;
                    }
                }
            }
        }

        return pool;
    }

    /**
     * Main stream consumption logic
     */
    private void streamRunner(Consumer<Map<String, Object>> consumerFn) {
        try (Jedis jedis = jedisPool.getResource()) {
            Map<byte[], StreamEntryID> streams = Collections.singletonMap(
                    streamKey.getBytes(StandardCharsets.UTF_8),
                    StreamEntryID.UNRECEIVED_ENTRY
            );

            // Use binary API to get raw bytes without UTF-8 decoding
            List<Map.Entry<byte[], List<StreamEntryBinary>>> results = jedis.xreadGroupBinary(
                    group.getBytes(StandardCharsets.UTF_8),
                    consumer.getBytes(StandardCharsets.UTF_8),
                    new XReadGroupParams().block((int) blockMs).count((int) batchSize),
                    streams
            );

            if (results == null || results.isEmpty()) {
                return; // timeout, will retry in main loop
            }

            for (Map.Entry<byte[], List<StreamEntryBinary>> res : results) {
                List<StreamEntryBinary> entries = res.getValue();
                String streamName = new String(res.getKey(), StandardCharsets.UTF_8);
                logger.debug("Received {} entries from stream '{}'", entries.size(), streamName);

                for (StreamEntryBinary entry : entries) {
                    if (stopped) {
                        return;
                    }
                    queueEventBinary(entry, streamName, consumerFn, jedis);
                }
            }
        }
    }

    /**
     * Process and queue a single binary event (Jedis 6.1.0+ binary API)
     * NOW WITH NATIVE JAVA PROTOBUF DECODING!
     */
    private void queueEventBinary(StreamEntryBinary entry, String streamKey,
                           Consumer<Map<String, Object>> consumerFn, Jedis jedis) {
        try {
            // Get BINARY fields directly from StreamEntryBinary (raw bytes!)
            Map<byte[], byte[]> binaryFields = entry.getFields();
            byte[] dataFieldKey = dataField.getBytes(StandardCharsets.UTF_8);
            
            // Get raw binary data - iterate because byte[] uses reference equality
            byte[] dataValue = null;
            for (Map.Entry<byte[], byte[]> field : binaryFields.entrySet()) {
                if (java.util.Arrays.equals(field.getKey(), dataFieldKey)) {
                    dataValue = field.getValue();
                    break;
                }
            }
            
            if (dataValue != null) {
                final byte[] data = dataValue;
                
                Map<String, Object> decodedEvent;
                
                // Decode based on data_codec config
                String dataType = getDataType();
                String protobufClassName = getProtobufClassName();
                String protobufClassPath = getProtobufClassPath();
                
                if ("protobuf".equals(dataType) && protobufClassName != null && !protobufClassName.isEmpty()) {
                    // PROTOBUF MODE: Use reflection or descriptor to parse the protobuf message
                    try {
                        com.google.protobuf.Message message = parseProtobufMessage(data, protobufClassName, protobufClassPath);
                        
                        // Convert protobuf to event map
                        decodedEvent = protobufMessageToEvent(message);
                        
                    } catch (Exception e) {
                        logger.error("❌ Failed to decode protobuf {}: {} - {}", 
                            protobufClassName, e.getClass().getName(), e.getMessage(), e);
                        // Don't ACK on failure - message will be redelivered
                        return;
                    }
                } else {
                    // PLAIN MODE: Treat data as UTF-8 string
                    String messageText = new String(data, StandardCharsets.UTF_8);
                    decodedEvent = new LinkedHashMap<>();
                    decodedEvent.put("message", messageText);
                }
                
                // Add Redis metadata if decorate_events is enabled
                if (decorateEvents) {
                    decodedEvent.put("redis_stream_id", entry.getID().toString());
                    decodedEvent.put("redis_stream_key", streamKey);
                }
                
                // Add type if configured
                if (type != null && !type.isEmpty()) {
                    decodedEvent.put("type", type);
                }
                
                // Add any other fields from stream entry (except the data field)
                binaryFields.forEach((k, v) -> {
                    String keyStr = new String(k, StandardCharsets.UTF_8);
                    if (!keyStr.equals(dataField)) {
                        String valueStr = new String(v, StandardCharsets.UTF_8);
                        decodedEvent.put(keyStr, valueStr);
                    }
                });
                
                // Hand off the event first, then ACK. ACK-ing before handoff would drop the
                // message if the consumer throws; leaving it unacked lets Redis redeliver it.
                consumerFn.accept(decodedEvent);
                jedis.xack(streamKey, group, entry.getID());
                
            } else {
                logger.warn("Data field '{}' not found in stream entry", dataField);
            }

        } catch (Exception e) {
            logger.error("Failed to create event from binary entry: {}", e.getMessage(), e);
            // Don't ACK on failure - message will be redelivered
        }
    }
    /**
     * Log errors based on type
     */
    private void logError(Exception e) {
        String message = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
        
        // Classify errors like Ruby plugin does
        if (e instanceof JedisConnectionException) {
            if (logger.isDebugEnabled()) {
                logger.warn("Redis connection error: {}", message, e);
            } else {
                logger.warn("Redis connection error: {}", message);
            }
        } else if (e instanceof JedisDataException) {
            if (logger.isDebugEnabled()) {
                logger.error("Redis data error: {}", message, e);
            } else {
                logger.error("Redis data error: {}", message);
            }
        } else if (e instanceof JedisException) {
            if (logger.isDebugEnabled()) {
                logger.error("Redis error: {}", message, e);
            } else {
                logger.error("Redis error: {}", message);
            }
        } else {
            logger.error("Unexpected error: {}", message, e);
        }
    }

    /**
     * Reset connection and determine if retry is appropriate
     * (matches Ruby's reset_for_error_retry method)
     * 
     * @return true if operation should retry, false otherwise
     */
    private boolean resetForErrorRetry(Exception e) {
        if (stopped) {
            return false;
        }

        // Reset the connection to trigger reconnect
        if (jedisPool != null) {
            try {
                jedisPool.close();
            } catch (Exception ex) {
                // ignore
            }
            jedisPool = null;
        }

        // Stoppable sleep - check stop flag during wait
        return stoppableSleep(1000);
    }


    /**
     * Extract data type from data_codec config
     */
    private String getDataType() {
        if (dataCodecConfig == null || dataCodecConfig.isEmpty()) {
            return "plain";  // default to plain
        }
        
        Object typeValue = dataCodecConfig.get("type");
        if (typeValue != null) {
            return typeValue.toString();
        }
        
        // If class_name is present, assume protobuf
        if (dataCodecConfig.containsKey("class_name")) {
            return "protobuf";
        }
        
        return "plain";
    }
    
    /**
     * Extract class_name from data_codec config
     */
    private String getProtobufClassName() {
        if (dataCodecConfig == null) {
            return null;
        }
        
        Object className = dataCodecConfig.get("class_name");
        if (className != null) {
            return className.toString();
        }
        
        return null;
    }
    
    /**
     * Extract class_file from data_codec config (path to .desc file)
     */
    private String getProtobufClassPath() {
        if (dataCodecConfig == null) {
            return null;
        }
        
        Object classFile = dataCodecConfig.get("class_file");
        if (classFile != null) {
            return classFile.toString();
        }
        
        return null;
    }

    /**
     * Parse protobuf message using reflection or descriptor file
     */
    private com.google.protobuf.Message parseProtobufMessage(byte[] data, String className, String classPath) throws Exception {
        // If class_path is provided, use descriptor-based parsing
        if (classPath != null && !classPath.isEmpty()) {
            return parseProtobufFromDescriptor(data, className, classPath);
        }
        
        // Otherwise, use reflection-based parsing (requires compiled Java class).
        // These run per decoded message, so keep them at debug to avoid flooding logs.
        logger.debug("Attempting to load protobuf class: {}", className);

        // Load the class
        Class<?> messageClass = Class.forName(className);
        logger.debug("Successfully loaded class: {}", messageClass.getName());

        // Get the parseFrom(byte[]) method
        java.lang.reflect.Method parseMethod = messageClass.getMethod("parseFrom", byte[].class);
        logger.debug("Found parseFrom method: {}", parseMethod);

        // Invoke parseFrom to decode the message
        logger.debug("Invoking parseFrom with {} bytes", data.length);
        com.google.protobuf.Message message = (com.google.protobuf.Message) parseMethod.invoke(null, data);
        logger.debug("Successfully parsed message of type: {}", message.getClass().getName());
        
        return message;
    }
    
    /**
     * Load a message descriptor from a .desc file
     */
    private Descriptors.Descriptor loadDescriptor(String path, String fullName) {
        try (FileInputStream fis = new FileInputStream(path)) {
            DescriptorProtos.FileDescriptorSet fds = DescriptorProtos.FileDescriptorSet.parseFrom(fis);
            
            logger.info("Descriptor file loaded from {}, contains {} file descriptors", path, fds.getFileCount());

            // Build FileDescriptors so we can look up the message
            Map<String, Descriptors.FileDescriptor> fileMap = new HashMap<>();

            for (DescriptorProtos.FileDescriptorProto fdp : fds.getFileList()) {
                Descriptors.FileDescriptor[] deps = new Descriptors.FileDescriptor[fdp.getDependencyCount()];
                for (int i = 0; i < fdp.getDependencyCount(); i++) {
                    String depName = fdp.getDependency(i);
                    deps[i] = fileMap.get(depName);
                }
                Descriptors.FileDescriptor fd =
                    Descriptors.FileDescriptor.buildFrom(fdp, deps);
                fileMap.put(fd.getName(), fd);
                logger.info("Loaded file descriptor: {}", fd.getName());
            }

            // Now search for the message by its fullName (e.g. "nv.Frame")
            for (Descriptors.FileDescriptor fd : fileMap.values()) {
                Descriptors.Descriptor desc = fd.findMessageTypeByName(fullName.substring(fullName.lastIndexOf('.') + 1));
                if (desc != null && desc.getFullName().equals(fullName)) {
                    logger.info("Found message descriptor: {}", fullName);
                    return desc;
                }
            }

            throw new IllegalArgumentException("Message type not found in descriptor set: " + fullName);
        } catch (Exception e) {
            throw new RuntimeException("Failed to load descriptor from " + path, e);
        }
    }
    
    /**
     * Parse protobuf message using descriptor file (.desc)
     */
    private com.google.protobuf.Message parseProtobufFromDescriptor(byte[] data, String messageName, String descriptorPath) throws Exception {
        // Check cache first
        String cacheKey = descriptorPath + ":" + messageName;
        Descriptors.Descriptor messageDescriptor = descriptorCache.get(cacheKey);
        
        if (messageDescriptor == null) {
            logger.info("Loading descriptor from file (first time): path={}, messageName={}", descriptorPath, messageName);
            messageDescriptor = loadDescriptor(descriptorPath, messageName);
            descriptorCache.put(cacheKey, messageDescriptor);
            logger.info("Descriptor cached for future use: {}", cacheKey);
        }
        
        // Parse the binary data using the descriptor
        DynamicMessage message = DynamicMessage.parseFrom(messageDescriptor, data);
        
        return message;
    }

    /**
     * Convert protobuf Message to Logstash event Map
     */
    private Map<String, Object> protobufMessageToEvent(com.google.protobuf.Message message) {
        try {
            String json = PROTO_PRINTER.print(message);
            Map<String, Object> event = GSON.fromJson(json, PROTO_EVENT_TYPE);
            if (event == null) {
                event = new LinkedHashMap<>();
            }
            return event;
        } catch (InvalidProtocolBufferException e) {
            throw new IllegalStateException("Failed to convert protobuf message to event", e);
        }
    }
    
    /**
     * Sleep while checking stop flag
     */
    private boolean stoppableSleep(long millis) {
        try {
            long sleepTime = 100; // check every 100ms
            long remaining = millis;
            
            while (remaining > 0 && !stopped) {
                long toSleep = Math.min(sleepTime, remaining);
                Thread.sleep(toSleep);
                remaining -= toSleep;
            }
            
            return !stopped; // return true if not stopping
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    @Override
    public void stop() {
        logger.info("Stop requested for Redis Stream Input plugin");
        stopped = true;
        
        // Clean shutdown of pool (matches Ruby's list_stop pattern)
        JedisPool pool = jedisPool;
        if (pool != null && !pool.isClosed()) {
            try {
                pool.close();
            } catch (Exception e) {
                logger.info("Error closing Jedis pool: {}", e.getMessage());
            }
        }
        jedisPool = null;
    }

    @Override
    public void awaitStop() throws InterruptedException {
        done.await();
    }

    @Override
    public Collection<PluginConfigSpec<?>> configSchema() {
        return Arrays.asList(
                HOST_CONFIG,
                PORT_CONFIG,
                PASSWORD_CONFIG,
                STREAM_KEY_CONFIG,
                GROUP_CONFIG,
                BLOCK_MS_CONFIG,
                BATCH_SIZE_CONFIG,
                CREATE_GROUP_CONFIG,
                CODEC_CONFIG,
                DATA_FIELD_CONFIG,
                TYPE_CONFIG,
                DECORATE_EVENTS_CONFIG,
                DATA_CODEC_CONFIG
        );
    }

    @Override
    public String getId() {
        return this.id;
    }
}

