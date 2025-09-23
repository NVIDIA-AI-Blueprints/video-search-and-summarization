# VSS Event Reviewer Deployment

## Setup Instructions

Clone the current repository and change directory to event_reviewer.

```sh
cd deploy/docker/event_reviewer/
```
Obtain [NGC API key](https://via.gitlab-master-pages.nvidia.com/via-docs/content/quickstart-installation-overview.html#obtain-ngc-api-key).
Update `NGC_API_KEY` in [.env](.env) to a valid key 


## Launch Instructions

```sh
# First create docker shared network
docker network create vss-shared-network

# For x86
# Start the VSS Event Verification which starts the Alert Bridge, VLM Pipeline, Alert Inspector UI and Video Storage Toolkit
ALERT_REVIEW_MEDIA_BASE_DIR=/tmp/alert-media-dir docker compose up -d

# For Thor, start the cache cleancer script
sudo sh ../../scripts/sys_cache_cleaner.sh &

# And then start the VSS Event Verification which starts the Alert Bridge, VLM Pipeline, Alert Inspector UI and Video Storage Toolkit
IS_AARCH64=1 ALERT_REVIEW_MEDIA_BASE_DIR=/tmp/alert-media-dir docker compose up -d

```
> **NOTE:** When launching for first time, VSS startup may take longer (~20 mins) due to model download, if it times out during launch increase the retries in compose.yaml.


> **NOTE:** Once the application is started, the Alert Inspector UI will be available at http://<HOST_IP>:7860 (if you are using the default port).


## Launch Configurations

### Using 4K / 16K context for Cosmos-Reason1 (Affects performance and accuracy)
Enable `VLM_INPUT_WIDTH` and `VLM_INPUT_HEIGHT` in [.env](.env)

