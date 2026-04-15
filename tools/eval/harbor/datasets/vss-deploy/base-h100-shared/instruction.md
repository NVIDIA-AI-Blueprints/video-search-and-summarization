Deploy the VSS base profile on this machine.
The GPU is H100 (hardware profile: H100).
Use the default shared GPU mode (LLM and VLM on same GPU).
NGC_CLI_API_KEY is set in the environment.
The VSS repo is cloned at /home/ubuntu/video-search-and-summarization.

After deployment, verify that all containers are running and the Agent API and UI endpoints respond. Then tear down the deployment with docker compose down.
