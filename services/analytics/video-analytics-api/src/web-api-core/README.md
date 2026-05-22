# Web API Core

`@nvidia-mdx/web-api-core` is a package which helps to create web-api server for VSS applications.

## Introduction

The package mainly consists of the following namespaces:

### Metrics

Used to compute metrics by querying database.

- `Behavior` - Behavior metrics computation
- `LastProcessedTimestamp` - Timestamp tracking
- `Occupancy` - Occupancy metrics
- `SpaceUtilization` - Space utilization metrics
- `TripwireEvent` - Tripwire event metrics

### Services

Used to retrieve records from database.

- `Alerts` - Alert management
- `Behavior` - Behavior data retrieval
- `Calibration` - Calibration configuration
- `Clustering` - Clustering results
- `ConfigManager` - Configuration management
- `Events` - Event data
- `Frames` - Frame metadata
- `Incidents` - Incident tracking
- `MTMC` - Multi-target multi-camera operations
- `NotificationManager` - Notification handling
- `Place` - Place/location management
- `RoadNetwork` - Road network data
- `Sensor` - Sensor operations
- `UsdAssets` - USD asset management

### Errors

Custom error types used by VSS Video Analytics APIs.

- `BadRequestError`
- `IndexNotFoundError`
- `InternalServerError`
- `InvalidInputError`
- `ResourceNotFoundError`
- `ServiceUnavailableError`

### Utils

Utilities used by VSS Video Analytics APIs.

- `Config` - Configuration utilities
- `Database` - Database operations
- `Elasticsearch` - Elasticsearch client wrapper
- `FileUploadHandler` - File upload handling
- `Histogram` - Histogram utilities
- `Kafka` - Kafka client wrapper
- `MessageBroker` - Message broker abstraction
- `Utils` - General utilities
- `Validator` - Input validation

## Getting Started

### Dependencies

1. [Node.js](https://nodejs.org/en/ "Nodejs.org") version 22.22.3.
2. Elasticsearch version 9.3.4
3. Kafka (optional) - Required for RTLS application and notification related functionalities


