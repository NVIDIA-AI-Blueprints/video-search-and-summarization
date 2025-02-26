# VSS Docker Compose Examples 

For custom VSS deployments through docker compose, three samples are provided to show different combinations of remote and local model deployments. 

| Deployment Sample | VLM (VILA-1.5 35B) | LLM (Llama 3.1 70B) | Embedding (llama-3.2-nv-embedqa-1b-v2) | Reranker (llama-3.2-nv-rerankqa-1b-v2) | Minimum GPU Requirement | 
| ------------------|-----|-----|-----------|----------| --------------- | 
| remote_vlm_deployment | Remote| Remote | Remote | Remote | Minimum 8GB VRAM GPU | 
| remote_llm_deployment | Local | Remote | Remote | Remote | 1xH100, 1xA100, 2xL40S |
| local_deployment      | Local | Local | Local | Local |  4xH100, 8xA100, 8xL40S |

For further details on deploying VSS with these docker compose examples, visit the [Custom Deployment Page](https://docs.nvidia.com/vss) in the VSS Documentation. 