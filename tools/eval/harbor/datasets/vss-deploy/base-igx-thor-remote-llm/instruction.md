Deploy the VSS base profile on this machine.
The GPU is IGX (hardware profile: IGX-THOR).
Use remote LLM via NVIDIA API (https://integrate.api.nvidia.com/v1). Keep VLM local.
NGC_CLI_API_KEY and NVIDIA_API_KEY are set in the environment.
The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.

After deployment, verify that all containers are running and the Agent API and UI endpoints respond. Then tear down the deployment with docker compose down.
