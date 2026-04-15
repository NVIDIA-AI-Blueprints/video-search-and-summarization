Deploy the VSS base profile on this machine.
The GPU is H100 (hardware profile: H100).
Use remote LLM and remote VLM via NVIDIA API (https://integrate.api.nvidia.com/v1).
NVIDIA_API_KEY is set in the environment.
The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.

After deployment, verify that all containers are running and the Agent API and UI endpoints respond. Then tear down the deployment with docker compose down.
