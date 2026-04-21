---
name: vlm-qna
description: Call the vision-language model directly with video plus a text question. Use when the user asks about video content, or about visual details that cannot be answered from conversation history.
metadata:
  { "openclaw": { "emoji": "👁", "os": ["linux"] } }
---

# Direct VLM (video Q&A)

Use this skill when you must **invoke the VLM HTTP API yourself**—for example the agent has **no** usable prior answer and needs a **fresh look at the pixels** for a specific clip.

The payload matches how **`video_understanding`** builds requests: a **user** message with **`text`** (the question or instruction) and **`video_url`** (HTTP(S) or `data:` URL the VLM can read). Optionally add a **`system`** message aligned with **`video_understanding.system_prompt`** in your agent `config.yml`.

---

## When to Use

- The user asks **what happens in the video**, what **objects / people / actions** appear, **colors**, **timing**, **safety**, or other **visual facts** that require watching the clip.
- The user asks for **details** that **cannot be answered** from existing messages, summaries, Elasticsearch/MCP results, or filenames alone—you need **model inference on the video**.
- Follow-up questions about **content details** after a coarse summary or after report generation.

Do **not** use this skill when a **database / MCP / prior tool output** already answers the question, unless the user explicitly wants **verification** against the video.

---

## OpenClaw workflow

1. **Clip** — Identify **sensor id**, **filename**, or **URL** for one video segment. If ambiguous, ask the user.
2. **Video URL** — Resolve a URL the **VLM service** can fetch (see **sensor-ops** for VST). Apply the same **public vs internal** rules as **`video_understanding`** (`vlm_mode` in config: remote VLM often needs a **public** URL).

To retrieve the video file from VST:

```bash
curl -s "http://localhost:30888/vst/api/v1/storage/file/<streamId>/url" | jq .
```

3. **Question** — Set the **`text`** part to the user’s query. You may prepend instructions from your deployment’s **`video_understanding`** behavior (plain text, timestamps in seconds, etc.).
4. **Call VLM** — **`POST ${VLM_BASE_URL}/v1/chat/completions`** with **`model`** = **`VLM_NAME`** from env/compose. Parse **`choices[0].message.content`** and **return that text** to the user and strip the thinking tags.

---

## Direct VLM call (OpenAI-compatible)

Agent VLM profiles use **`base_url: ${VLM_BASE_URL}/v1`**, so:

`POST ${VLM_BASE_URL}/v1/chat/completions`

Use the same **`VLM_BASE_URL`** and **`VLM_NAME`** as the running stack (`.env` / compose).

**Example** — user question in **`question.txt`**, optional **`system_prompt.txt`** copied from **`video_understanding.system_prompt`** in `config.yml`:

```bash
export VLM_BASE_URL="http://localhost:8001"
export VLM_NAME="nvidia/cosmos-reason2-8b"
export VIDEO_URL="http://<host>:30888/.../clip.mp4"

curl -s -X POST "${VLM_BASE_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --rawfile sys system_prompt.txt \
    --rawfile user question.txt \
    --arg model "$VLM_NAME" \
    --arg vurl "$VIDEO_URL" \
    '{
      model: $model,
      temperature: 0,
      max_tokens: 4096,
      messages: [
        { role: "system", content: $sys },
        { role: "user", content: [
            {type: "text", text: $user},
            {type: "video_url", video_url: {url: $vurl}}
        ]}
      ]
    }')" | jq -r '.choices[0].message.content'
```

**Minimal variant** (no system message): build **`messages`** with only one **`user`** object containing **`text`** + **`video_url`** (no **`role: system`** entry).

**Extras:** NIM/Cosmos may require **extra JSON** fields (`model_kwargs` / chunking / resolution) per your deployment. If **`vlm_name`** is **`openai_*`**, the agent may send **frames as images** instead of **`video_url`**—match your endpoint’s docs.

---

## Cross-Reference

- **sensor-ops** — VST storage/replay URLs so **`VIDEO_URL`** is valid for the VLM.
- **report-generation** — timestamped **reports** via the **VSS agent** (`/generate`); this skill is **direct VLM** for ad-hoc **video Q&A**.
