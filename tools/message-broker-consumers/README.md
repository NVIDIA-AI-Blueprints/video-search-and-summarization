# Message Broker Consumers

Multiprocessing consumers for various message brokers (Redis, Kafka, and extensible to MQTT, AMQP, etc.) that export messages to JSON Lines files.

## Architecture

The consumers follow an object-oriented design with a common base class (`BaseMessageConsumer`) that handles all shared logic. This design reduces code duplication by ~60% and ensures consistent behavior across all message broker implementations.

### File Structure
- **base_consumer.py**: Abstract base class with common functionality (~380 lines)
  - Multiprocessing management with proper signal handling
  - Protobuf decoding for all message types
  - Statistics tracking (read/written/errors)
  - Signal handling and graceful shutdown
  - File I/O operations
  - Process monitoring
  
- **kafka_to_file.py**: Kafka-specific implementation (~120 lines)
  - Kafka consumer connection
  - Message polling with timeout
  - Kafka error handling (partition EOF, etc.)
  - Auto-commit configuration
  
- **redis_to_file.py**: Redis-specific implementation (~130 lines)
  - Redis connection and consumer group creation
  - Stream reading with xreadgroup
  - Message acknowledgment
  - Batch processing

- **Future**: Easy to add MQTT, AMQP, or other broker implementations

## Features

- **Multiprocessing**: Each stream/topic is processed in its own process for true parallelism
- **Protobuf Support**: Automatically decodes Protocol Buffer messages based on stream/topic name
- **Configurable**: Customizable connection parameters and consumer groups
- **Separate Output Files**: Each stream/topic writes to its own `.txt` file (JSON Lines format)
- **Graceful Shutdown**: Proper signal handling for clean process termination
- **Progress Monitoring**: Real-time stats and periodic status updates
- **Extensible Design**: Easy to add new message broker types by extending the base class

## Quick Start

```bash
# For Kafka
python3 kafka_to_file.py --topics mdx-raw,mdx-behavior

# For Redis  
python3 redis_to_file.py --streams mdx-raw,mdx-behavior

# Output files will be created in:
# - kafka_dumps/mdx-raw.txt
# - kafka_dumps/mdx-behavior.txt
# or
# - redis_dumps/mdx-raw.txt
# - redis_dumps/mdx-behavior.txt
```

## Installation

1. Install required Python packages:
```bash
pip install -r requirements.txt
```

2. Ensure you have the required protobuf definitions (`schema_pb2.py` and `ext_pb2.py`) in the same directory.

## Supported Stream/Topic Types

Both consumers automatically detect the message type based on the stream/topic name:

- **Frame messages**: `mdx-raw`, `mdx-bev`, `mdx-frames`
- **Behavior messages**: `mdx-behavior`, `mdx-events`, `mdx-alerts`, `mdx-behavior-plus`
- **SpaceUtilization messages**: `mdx-space-utilization`
- **Incident messages**: `mdx-incidents`

---

## Redis Consumer (`redis_to_file.py`)

Consumes messages from Redis streams and exports them to JSON Lines files. Built on the `BaseMessageConsumer` class for consistent behavior across all consumers.

### Basic Usage

Process a single stream with default settings:
```bash
python3 redis_to_file.py --streams mdx-raw
```
This will create `redis_dumps/mdx-raw.txt`

### Multiple Streams

Process multiple streams (each in its own process):
```bash
python3 redis_to_file.py --streams mdx-raw,mdx-behavior,mdx-events --output-dir ./output
```
This will create:
- `./output/mdx-raw.txt`
- `./output/mdx-behavior.txt`
- `./output/mdx-events.txt`

### Custom Redis Connection

Connect to a remote Redis server:
```bash
python3 redis_to_file.py \
  --streams mdx-raw \
  --redis-host redis.example.com \
  --redis-port 6380 \
  --consumer-group my-consumer-group
```

### Redis Command-Line Arguments

- `--streams`: Comma-separated list of Redis streams to consume (default: `mdx-raw`)
- `--output-dir`: Directory for output JSON Lines files (default: `redis_dumps`)
- `--redis-host`: Redis server hostname (default: `localhost`)
- `--redis-port`: Redis server port (default: `6379`)
- `--consumer-group`: Redis consumer group name (default: `redis-json-dumper`)

---

## Kafka Consumer (`kafka_to_file.py`)

Consumes messages from Kafka topics and exports them to JSON Lines files. Built on the `BaseMessageConsumer` class for consistent behavior across all consumers.

### Basic Usage

Process a single topic with default settings:
```bash
python3 kafka_to_file.py --topics mdx-raw
```
This will create `kafka_dumps/mdx-raw.txt`

### Multiple Topics

Process multiple topics (each in its own process):
```bash
python3 kafka_to_file.py --topics mdx-raw,mdx-behavior,mdx-events --output-dir ./output
```
This will create:
- `./output/mdx-raw.txt`
- `./output/mdx-behavior.txt`
- `./output/mdx-events.txt`

### Custom Kafka Connection

Connect to a remote Kafka broker:
```bash
python3 kafka_to_file.py \
  --topics mdx-raw \
  --kafka-broker kafka.example.com:9092 \
  --consumer-group my-consumer-group
```

### Kafka Command-Line Arguments

- `--topics`: Comma-separated list of Kafka topics to consume (default: `mdx-raw`)
- `--output-dir`: Directory for output JSON Lines files (default: `kafka_dumps`)
- `--kafka-broker`: Kafka broker address (default: `localhost:9092`)
- `--consumer-group`: Kafka consumer group name (default: `kafka-json-dumper`)

---

## Output Format

Both consumers extract the message content from protobuf data and write it directly as JSON lines.

Example output (one message per line):
```json
{"frame_id": "12345", "timestamp": 1642266000, "objects": [...]}
{"behavior_id": "67890", "type": "loitering", "confidence": 0.95}
```

## Architecture

- Each stream/topic runs in a separate process for parallel processing
- Each process maintains its own connection (Redis client or Kafka consumer)
- Messages are acknowledged after successful writing
- Automatic consumer group creation (Redis) or registration (Kafka)
- Parent process monitors child processes and handles signals

## Signal Handling

The applications use proper multiprocessing signal handling for graceful shutdown:

**Parent Process:**
- Handles `SIGINT` (Ctrl+C) and `SIGTERM`
- Sends `SIGTERM` to all child processes
- Waits for children to finish (with 2-second timeout)
- Exits cleanly

**Child Processes:**
- Ignore `SIGINT` (parent handles it)
- Handle `SIGTERM` for graceful shutdown
- Finish processing current message batch
- Log final statistics before exiting

## Monitoring

- Progress is logged every 1000 messages processed
- Status updates every 30 seconds showing active processes
- Dead processes are detected and reported
- Final count is always logged when a processor exits

Example log output:
```
2025-01-15 10:31:02 - INFO - [mdx-raw] Read 1000, Written 1000, Errors 0
2025-01-15 10:31:32 - INFO - Status: 3/3 processes running
2025-01-15 10:35:15 - INFO - [mdx-raw] Final count - Read: 5432, Written: 5432, Errors: 0
```

## Dependencies

- `redis`: Redis Python client (for Redis consumer)
- `confluent-kafka`: Kafka Python client (for Kafka consumer)
- `protobuf`: Protocol Buffers support
- `google.protobuf`: Protobuf JSON formatting

## Extending to New Message Brokers

To add support for a new message broker (e.g., MQTT), create a new file that extends `BaseMessageConsumer`:

```python
# mqtt_to_file.py
import argparse
import os
from base_consumer import BaseMessageConsumer

class MQTTMessageConsumer(BaseMessageConsumer):
    def get_consumer_type(self):
        return "MQTT"
    
    def get_source_names(self):
        # Parse MQTT topics from args
        return [t.strip() for t in self.args.topics.split(',')]
    
    def create_connection(self, source_name, consumer_group, **kwargs):
        # Create MQTT client connection
        mqtt_broker = kwargs.get('mqtt_broker', self.args.mqtt_broker)
        # Initialize and return MQTT client
        pass
    
    def consume_messages(self, connection, source_name, consumer_group, **kwargs):
        # Yield messages from MQTT
        # Return format: (message_id, message_data)
        # Use None for message_id if not needed for acknowledgment
        pass
    
    def acknowledge_messages(self, connection, source_name, consumer_group, message_ids):
        # Handle message acknowledgment if needed (e.g., for QoS > 0)
        # Can be empty if using QoS 0
        pass
    
    def close_connection(self, connection):
        # Close MQTT connection
        connection.disconnect()
    
    def get_connection_info(self):
        # Return connection info for logging
        return f"{self.args.mqtt_broker}"
    
    def get_process_args(self, source_name):
        # Return process arguments
        output_file_path = os.path.join(self.args.output_dir, f"{source_name}.txt")
        return {
            'output_file_path': output_file_path,
            'connection_params': {
                'mqtt_broker': self.args.mqtt_broker
            }
        }

def main(args):
    consumer = MQTTMessageConsumer(args)
    consumer.run()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Dump MQTT topics to JSON lines files')
    parser.add_argument('--topics', default='mdx-raw',
                        help='Comma-separated list of MQTT topics')
    parser.add_argument('--output-dir', default='mqtt_dumps',
                        help='Output directory for JSON lines files')
    parser.add_argument('--mqtt-broker', default='localhost:1883',
                        help='MQTT broker address')
    parser.add_argument('--consumer-group', default='mqtt-json-dumper',
                        help='Consumer group name')
    
    args = parser.parse_args()
    main(args)
```

The base class handles all common logic including:
- Multiprocessing management
- Protobuf decoding
- Statistics tracking
- Error handling
- Signal handling
- File I/O

## Notes

- Both scripts automatically create consumer groups if they don't exist
- Each stream/topic's output is written to `<output-dir>/<name>.txt`
- Process monitoring ensures failed processes are detected and reported
- Files are flushed after each write for real-time availability
- Supports concurrent processing of multiple streams/topics
- Errors are tracked and reported in statistics but don't stop processing
- Temporary failures trigger a 5-second retry delay
