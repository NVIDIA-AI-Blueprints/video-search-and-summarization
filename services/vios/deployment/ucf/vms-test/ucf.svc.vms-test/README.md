vms-test
==============================

Developer test for vms service.

Project Organization
------------

    ├── README.md          <- Documentation for the service to display in microservice catalog.
    │
    ├── configs            <- Config files for the applications.
    │                         Supported formats: `json`, `yaml`, `text`, `properties`.
    │                         Mounted under `/opt/configs/`
    │
    ├── containers         <- Contains container builder config files if container image must be
    │                         generated. File name shall match with the container component name
    │                         in `manifest.yaml`
    │
    ├── endpoints          <- Endpoint definition files. File name shall match with the endpoint
    │                         name in `manifest.yaml`. File extension is based on the type of endpoint
    │                         e.g. `.json` for REST endpoints while `.protobuf` for gRPC endpoints.
    │
    ├── manifest.yaml      <- Manifest file describing the microservice metadata.
    │
    ├── scripts            <- All the scripts to be executed at runtime of the service.
    │                         Scripts must be in ascii format not greater than 1MB.
    │                         Mounted under `/opt/scripts/`
    │
    ├── tests              <- Test applications for the service.

----------

