<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# NCCL Hang Fix - Nemotron Multi-GPU Tensor Parallelism

## Problem Summary

The Nemotron NIM container was **hanging completely** after NCCL initialization when using tensor parallelism (tp=2) across GPUs 2 and 3. The container would initialize for ~24 seconds, then go silent for 4+ minutes with no log output, causing deployment timeouts.

**Last log before hang:**
```
02:14:11 - [EngineCore_DP0 pid=654] INFO vLLM is using nccl==2.27.5
<silence for 4+ minutes until timeout>
```

## Root Cause

NCCL (NVIDIA Collective Communications Library) initialization was hanging during tensor parallel setup, likely due to:
1. Missing NCCL debug/timeout configuration
2. Possible IPC (Inter-Process Communication) issues between GPU processes
3. No visibility into NCCL initialization progress

## Changes Made

### 1. Added Comprehensive NCCL Debug Environment Variables

**File:** `docker-compose.yml` (nemotron-3-nano-30b service)

Added these critical environment variables:

```yaml
# NCCL Debug and Tuning
NCCL_DEBUG: INFO                      # Enable detailed NCCL logging
NCCL_DEBUG_SUBSYS: INIT,COLL,NET     # Log initialization, collectives, network
NCCL_TIMEOUT: '1800'                  # 30-minute timeout (prevents infinite hangs)
NCCL_ASYNC_ERROR_HANDLING: '1'       # Report errors asynchronously
NCCL_IB_DISABLE: '1'                  # Disable InfiniBand (not available)
NCCL_P2P_DISABLE: '0'                 # Enable GPU peer-to-peer
NCCL_SHM_DISABLE: '0'                 # Enable shared memory
NCCL_SOCKET_IFNAME: 'lo,eth0'         # Network interfaces to use
NCCL_NET_GDR_LEVEL: '0'               # Disable GPUDirect RDMA

# vLLM Debug Settings
VLLM_LOGGING_LEVEL: DEBUG             # Verbose vLLM logs
PYTHONUNBUFFERED: '1'                 # Flush logs immediately

# Torch/NCCL Settings
TORCH_NCCL_BLOCKING_WAIT: '0'         # Non-blocking NCCL operations
TORCH_NCCL_ASYNC_ERROR_HANDLING: '1'  # Async error reporting
TORCH_DISTRIBUTED_DEBUG: INFO         # PyTorch distributed debug info
```

### 2. Enhanced Health Check Configuration

**File:** `docker-compose.yml`

- Changed health check from `/v1/health/live` to `/v1/health/ready` (more accurate)
- Increased `start_period` from 1040s (17min) to 1800s (30min)
- Increased `interval` from 10s to 15s
- Increased `retries` from 100 to 120

### 3. Real-Time Log Streaming

**File:** `ci/pipeline-helpers.groovy`

Added real-time Nemotron log streaming during deployment:
- Streams logs with `[NEMOTRON]` prefix
- NCCL debug output now visible immediately
- Background process that's cleaned up after deployment

## What to Expect Now

### Successful Case

With NCCL debug enabled, you'll see detailed initialization logs like:

```
[NEMOTRON] INFO vLLM is using nccl==2.27.5
[NEMOTRON] nemotron-3-nano-30b:654:654 [0] NCCL INFO Bootstrap : Using lo:127.0.0.1<0>
[NEMOTRON] nemotron-3-nano-30b:654:654 [0] NCCL INFO NET/Plugin: Plugin load (libnccl-net.so) returned 2 : libnccl-net.so: cannot open shared object file: No such file or directory.
[NEMOTRON] nemotron-3-nano-30b:654:655 [1] NCCL INFO NET/Socket : Using [0]eth0:172.19.0.4<0>
[NEMOTRON] nemotron-3-nano-30b:654:655 [1] NCCL INFO Using network Socket
[NEMOTRON] NCCL INFO comm 0x... rank 0 nranks 2 cudaDev 0 busId 2 - Init COMPLETE
[NEMOTRON] NCCL INFO comm 0x... rank 1 nranks 2 cudaDev 1 busId 3 - Init COMPLETE
[NEMOTRON] INFO Loading model weights...
[NEMOTRON] INFO Model loaded successfully
[NEMOTRON] INFO Application startup complete
```

### Hang Case

If it still hangs, you'll see exactly where:

```
[NEMOTRON] INFO vLLM is using nccl==2.27.5
[NEMOTRON] nemotron-3-nano-30b:654:654 [0] NCCL INFO Bootstrap : Using lo:127.0.0.1<0>
[NEMOTRON] <hangs here with no more output for >30 minutes>
```

Then NCCL_TIMEOUT will trigger after 30 minutes with an error message.

### Common NCCL Error Messages

If you see these, they indicate the specific problem:

- **"NCCL WARN Bootstrap : no socket interface found"** → Network interface issue
- **"NCCL WARN Could not enable P2P"** → GPU P2P communication problem
- **"NCCL WARN Call to ibv_reg_mr failed"** → InfiniBand issue (should be disabled now)
- **"NCCL timeout"** → 30-minute timeout hit, something is deadlocked

## Testing the Fix

### On Next Jenkins Run

1. The pipeline will now show real-time Nemotron logs prefixed with `[NEMOTRON]`
2. Watch for NCCL initialization messages
3. If it hangs, you'll see exactly where in the NCCL init sequence
4. Logs will be more actionable for debugging

### Manual Testing

To test locally:

```bash
cd compose/BlueprintBuilderGenerated/LVS_Integrated-CR2_Nemotron-3-Nano-30B-NIM

# Start with updated configuration
NGC_API_KEY=<your_key> LOCAL_NIM_CACHE=/path/to/cache docker compose up

# Watch logs in real-time
docker logs -f nemotron-3-nano-30b
```

Look for NCCL debug output showing initialization progress.

## Fallback Options

If tensor parallelism continues to hang:

### Option 1: Force Single GPU Mode (No TP)

Create override file or modify `.env`:

```bash
# In .env, change from:
NIM_TAGS_SELECTOR=tp=2,precision=bf16

# To:
NIM_TAGS_SELECTOR=tp=1,precision=bf16

# And update GPU device_ids in docker-compose.yml to use only one GPU
```

### Option 2: Try Different GPU Pair

If GPUs 2-3 have communication issues, try GPUs 0-1:

```yaml
# In docker-compose.yml, change:
device_ids:
  - '2'
  - '3'

# To:
device_ids:
  - '0'
  - '1'
```

### Option 3: Disable PyTorch Inductor Compilation

If NCCL initializes but hangs later:

```yaml
environment:
  VLLM_COMPILATION_MODE: '0'  # Disable vLLM compilation
```

## Next Steps

1. **Run the Jenkins pipeline** - the changes are already in place
2. **Monitor real-time logs** - look for NCCL debug output
3. **If it still hangs**, the NCCL logs will show exactly where
4. **Report findings** with the specific NCCL message where it hangs

## Related Files Modified

- `compose/BlueprintBuilderGenerated/LVS_Integrated-CR2_Nemotron-3-Nano-30B-NIM/docker-compose.yml`
- `ci/pipeline-helpers.groovy`

## Additional Debugging

If issues persist, check bare metal host:

```bash
# Verify GPU topology
nvidia-smi topo -m

# Check P2P between GPUs 2 and 3
python3 -c "
import torch
print('GPU 2->3 P2P:', torch.cuda.can_device_access_peer(2, 3))
print('GPU 3->2 P2P:', torch.cuda.can_device_access_peer(3, 2))
"

# Check for GPU errors
nvidia-smi --query-gpu=index,ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total --format=csv
```
