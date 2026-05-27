****** This section describes the generated template and must be removed before publishing the microservice ******

Project Organization
------------

    ├── README.md          <- Documentation for the microservice to display in microservice catalog.
    │
    ├── configs            <- Config files for the applications.
    │                         Supported formats: `json`, `yaml`, `text`, `properties`.
    │                         Mounted under `/opt/configs/`
    │
    │
    ├── endpoints          <- Endpoint definition files. File name shall match with the endpoint
    │                         name in `manifest.yaml`. File extension is based on the type of endpoint
    │                         e.g. `.json` for REST endpoints while `.protobuf` for gRPC endpoints.
    │
    ├── manifest.yaml      <- Manifest file describing the microservice metadata.
    |
    |
    ├── manual_compliance_test_results.yaml  <- Results for the manual compliance tests to be updated by developer.
    |
    │
    ├── scripts            <- All the scripts to be executed at runtime of the microservice.
    │                         Scripts must be in ascii format not greater than 1MB.
    │                         Mounted under `/opt/scripts/`
    │
    ├── tests              <- Test applications for the microservice.

----------

********************************************** Remove upto this line **********************************************

testapp-wdm-envoy
==============================

## Description
testapp-wdm-envoy

<Detailed description of the microservice, functionality, features>

## Usage
<Any parameter and endpoint details that cannot be documented in the manifest>
<Examples on how to add the microservice to app.yaml and set connections>

## Performance
<Performance/KPIs>

## Supported Platforms
<Information on supported platforms / GPUs>

## Deployment requirements
<Deployment requirements like CPU / memory / NICs / node host configuration>

## License
<License to use the microservice under. Detailed license text & 3rdparty licenses can be added to LICENSE.txt>

## Known Issues / Limitations
<Known issues & workarounds>

## References
<This section is optional. Useful links like sample apps on github, gitlab using the microservice, link to SDKs used in the microservice etc.>