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

# For Thor & DGX Spark, start the cache cleancer script
sudo sh ../../scripts/sys_cache_cleaner.sh &

# Start the VSS Event Verification which starts the Alert Bridge, VLM Pipeline, Alert Inspector UI and Video Storage Toolkit
# For x86 & Thor
ALERT_REVIEW_MEDIA_BASE_DIR=/tmp/alert-media-dir docker compose up -d

# For DGX Spark and GH200/GB200 SBSA platforms
IS_SBSA=1 ALERT_REVIEW_MEDIA_BASE_DIR=/tmp/alert-media-dir docker compose up -d

```
> **NOTE:** When launching for first time, VSS startup may take longer (~20 mins) due to model download, if it times out during launch increase the retries in compose.yaml.


> **NOTE:** Once the application is started, the Alert Inspector UI will be available at http://<HOST_IP>:7860 (if you are using the default port).

