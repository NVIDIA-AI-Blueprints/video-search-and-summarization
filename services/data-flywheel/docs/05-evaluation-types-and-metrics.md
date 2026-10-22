# Evaluation Types and Metrics

The Flywheel supports the following evaluation types and metrics out-of-the-box.

## Evaluation Types

### Evaluation Types Matrix

| Evaluation Type      | Dataset                                 | Model                        | Context                | Purpose                                                                                 |
|---------------------|-----------------------------------------|------------------------------|------------------------|-----------------------------------------------------------------------------------------|
| Base Evaluation     | Base (held-out, production-like)         | Out-of-the-box (no fine-tuning) | No ICL examples        | Establishes a baseline for model performance on real-world data, before any adaptation.  |
| ICL Evaluation      | ICL-augmented (with ICL examples)        | Out-of-the-box (no fine-tuning) | Few-shot (ICL) examples | Measures how well the model adapts when given a few relevant examples (prompt engineering). |
| Customized Evaluation | Base (held-out, production-like)         | Fine-tuned/customized         | No ICL examples        | Quantifies improvement (or regression) from fine-tuning a model on your specific data.   |

### Base Evaluation (`base-eval`)

Base evaluation tests a model on a standard, held-out dataset sampled from production logs, **without** any in-context learning (ICL) examples or fine-tuning. This is the default evaluation of a model's out-of-the-box performance.

- **dataset:** Base (held-out, production-like)
- **model:** Out-of-the-box (no fine-tuning)
- **context:** No ICL examples
- **purpose:** Establishes a baseline for model performance on real-world data, before any adaptation or context is provided.

**Example:**

```json
{
  "request": {
    "messages": [
      {"role": "user", "content": "How do I reset my password?"}
    ]
  },
  "response": {
    "choices": [
      {
        "message": {
          "role": "assistant",
          "content": "Click 'Forgot password' on the login page."
        }
      }
    ]
  }
}
```

> **Note** 
>
> The model receives only the `request.messages` as input and generates its own response, which is then compared against the ground truth stored in `response.choices[0].message.content` for evaluation scoring.

### ICL Evaluation (`icl-eval`)

ICL (In-Context Learning) evaluation tests a model's ability to leverage a few-shot context—that is, it injects a small number of example Q&A pairs into the system message of each prompt, simulating a "few-shot" learning scenario.

- **dataset:** ICL-augmented (each test example has a configurable number of ICL examples injected into its system message)
- **model:** Out-of-the-box (no fine-tuning)
- **context:** Few-shot (ICL) examples, controlled by `icl_config` in `config.yaml`
- **purpose:** Measures how well the model can generalize or adapt when given a few relevant examples, simulating real-world prompt engineering or agentic use cases.

**Example:**

```json
{
  "request": {
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant that can answer questions and help with tasks.\nHere are some examples of how you should respond to different types of requests:\n\nFor example, if the conversation looks like this:\nuser: How do I reset my password?\n\nThen you'll respond with:\nassistant: Click 'Forgot password' on the login page.\n\nFor example, if the conversation looks like this:\nuser: How do I change my email address?\n\nThen you'll respond with:\nassistant: Go to account settings and update your email."
      },
      {"role": "user", "content": "How do I delete my account?"}
    ]
  },
  "response": {
    "choices": [
      {
        "message": {
          "role": "assistant",
          "content": "Go to your account settings and select 'Delete Account' option."
        }
      }
    ]
  }
}
```

> **Note** 
>
> ICL examples are injected into the system message as formatted instructional text. The model receives the `request.messages` (including the system message with examples) as input, and its response is compared against the ground truth in `response.choices[0].message.content`.

### Customized Evaluation (`customized-eval`)

Customized evaluation tests a *fine-tuned* or *customized* version of the model on the same base dataset. This measures the effect of supervised fine-tuning (for example, LoRA, SFT) on model performance.

- **dataset:** Base (held-out, production-like)
- **model:** Fine-tuned/customized (for example, via LoRA, SFT)
- **context:** No ICL examples
- **purpose:** Quantifies the improvement (or regression) from fine-tuning a model on your specific data, and enables direct comparison to the base model.

**Example:**
```json
{
  "request": {
    "messages": [
      {"role": "user", "content": "How do I reset my password?"}
    ]
  },
  "response": {
    "choices": [
      {
        "message": {
          "role": "assistant",
          "content": "Click 'Forgot password' on the login page."
        }
      }
    ]
  }
  // Model being evaluated is a fine-tuned/customized version
}
```

> **Note** 
>
> Like Base Evaluation, the customized model receives only the `request.messages` as input. The evaluation compares the customized model's response against the same ground truth reference answer to measure the impact of fine-tuning.

### Tool-Calling Evaluation Example

The Flywheel Blueprint is designed for agentic workloads, especially those using tool-calling. Tool-calling metrics are central to evaluating how well models can interact with external functions or APIs.

**Tool-calling Example:**
```json
{
  "request": {
    "model": "meta/llama-3.1-70b-instruct",
    "messages": [
      {"role": "system", "content": "You are a chatbot..."},
      {"role": "user", "content": "Has my bill been processed?"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "structured_rag",
          "parameters": {
            "properties": {
              "query": {"type": "string"},
              "user_id": {"type": "string"}
            },
            "required": ["query", "user_id"]
          }
        }
      }
    ]
  },
  "response": {
    "choices": [{
      "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
          "type": "function",
          "function": {
            "name": "structured_rag",
            "arguments": {"query": "account bill processed", "user_id": "4165"}
          }
        }]
      }
    }]
  }
}
```
> **Note**
>
> This structure is used for both evaluation and fine-tuning. The model is expected to select the correct function and provide the correct arguments in its tool call output.

### DPO Evaluation (`dpo-eval`)

DPO (Direct Preference Optimization) evaluation tests how well a model's responses align with preferred responses over rejected ones. This evaluation type uses a specialized judge prompt that compares the model's output against both chosen (preferred) and rejected (less preferred) responses.

| Evaluation Type | Dataset              | Model                        | Context            | Purpose                                                                |
|----------------|---------------------|------------------------------|--------------------|--------------------------------------------------------------------|
| DPO Evaluation | DPO preference pairs | Base, ICL, or DPO fine-tuned | Chosen vs rejected | Measures alignment with preferred responses over rejected ones on a 1-10 scale. |

- **dataset:** DPO preference pairs (prompt, chosen_response, rejected_response)
- **model:** Can be base, ICL-enhanced, or DPO fine-tuned
- **context:** Compares against both preferred and rejected responses
- **purpose:** Measures alignment with human preferences on a 1-10 scale

**Example:**
```json
{
  "prompt": [
    {"role": "user", "content": "What is Python?"}
  ],
  "chosen_response": "Python is a high-level, interpreted programming language known for its readability, simplicity, and versatility. It supports multiple programming paradigms and has a comprehensive standard library.",
  "rejected_response": "Python is a snake.",
  "model_response": "Python is a high-level programming language used for various applications including web development, data science, and automation."
}
```

> **Note**
>
> The DPO judge evaluates the model's response by comparing it to both the preferred (chosen) and rejected responses, scoring on a scale of 1-10 where higher scores indicate better alignment with the preferred response.

## Metrics

Metrics appear in the `scores` field of evaluation results.

| Metric Name | Type/Range | Computed By| Description |
|-------------------------------|--------------------|----------------------|----------------------------------------------------------------------------------------------|
| similarity | Integer (1–10) | LLM judge | For non tool-calling datasets; LLM-judged similarity between candidate and reference answers. Higher is better. |
| preference_score | Integer (1–10) | LLM judge | For DPO evaluation; measures alignment with preferred responses over rejected ones. 10=excellent alignment, 1=poor alignment. |
| function_name | Float (0–1) | Programmatic | Accuracy of function name prediction in tool-calling tasks. |
| function_name_and_args_accuracy| Float (0–1) | Programmatic | Accuracy of both function name and arguments in tool-calling tasks. |
| tool_calling_correctness | Integer (0 or 1) | LLM judge | LLM-judged correctness of tool call output (1=correct, 0=incorrect). |

### Embedding Evaluation Metrics

For embedding models, the system uses standard information retrieval metrics:

| Metric Name | Type/Range | Description |
|-------------|------------|-------------|
| **MAP** | Float (0–1) | Mean Average Precision - measures the quality of the entire ranking |
| **Recall@k** | Float (0–1) | Fraction of relevant documents retrieved in the top k results |
| **Precision@k** | Float (0–1) | Fraction of top k results that are relevant |
| **NDCG_CUT@k** | Float (0–1) | Normalized Discounted Cumulative Gain - evaluates ranking quality with position-based weighting |

These metrics are computed for embedding models during:
- **Base evaluation**: Tests the pre-trained embedding model
- **Customized evaluation**: Tests the fine-tuned embedding model
- **Custom dataset evaluation**: Tests on domain-specific BEIR format datasets

### Embedding Evaluation Types

The Data Flywheel Blueprint supports two evaluation approaches for embedding models:

#### 1. Split Dataset Evaluation

**When it runs**: **Always** - automatically with every embedding training job

- Uses a held-out portion of your training data (configured via `data_split_config.eval_size`)
- Tests how well the model generalizes to unseen data from the same distribution
- Results appear in the standard evaluation metrics

#### 2. Custom Dataset Evaluation  

**When it runs**: **Only when custom datasets are provided** in the configuration

- Uses independently loaded BEIR format evaluation datasets
- Tests model performance on specific domains or benchmark datasets
- Configure via `evaluation_config.custom_evaluations`
- Can evaluate on multiple custom datasets in a single job

### Key Difference

| | Split Dataset | Custom Dataset |
|---|---|---|
| **Runs** | Always (automatic) | Only when configured |
| **Data source** | From training data | Separate BEIR datasets |
| **Purpose** | Validate training | Domain-specific testing |

### Custom Evaluation Datasets

Custom evaluations allow you to test your fine-tuned embedding models against domain-specific or task-specific evaluation sets that are completely independent of your training data.

> **Note:** Custom evaluations are currently available only for embedding models. Support for LLM custom evaluations is planned for a future release.

#### Configuring Custom Evaluations

Custom evaluations are configured in the `evaluation_config` section of the embedding job configuration:

```yaml
embedding_job_config:
  evaluation_config:
    custom_evaluations:
      - name: "custom-eval-dataset"
        workload_id: "val-set-1"
        client_id: "mozillacorebugs"
```

#### Custom Evaluation Process

When custom evaluations are configured:
1. The system retrieves your pre-loaded BEIR format dataset based on `workload_id` and `client_id`
2. Runs evaluation for both base model (before fine-tuning) and customized model (after fine-tuning)
3. Applies standard retrieval metrics (MAP, Recall@k, Precision@k, NDCG@k)
4. Reports results separately for each custom dataset, allowing comparison across different test scenarios

#### Loading Custom Evaluation Data

Custom evaluation datasets for embedding models must be in BEIR format, which is distinct from the training data format:

```bash
# Place your custom-eval-dataset directory in the data/ directory
# Load BEIR format evaluation dataset
uv run python src/scripts/load_embedding_eval_data.py \
  --workload-id "val-set-1" \
  --client-id "mozillacorebugs" \
  --data-dir "custom-eval-dataset"
```

> **💡 Data Directory Location:** The `--data-dir` parameter accepts relative paths (resolved from `data/` directory) or absolute paths.

**BEIR Format Requirements**:
- `corpus.jsonl`: Document collection with `_id` and `text` fields
- `queries.jsonl`: Query set with `_id` and `text` fields  
- `qrels/test.tsv`: Relevance judgments mapping query IDs to document IDs

**Important**: This is different from the triplet format used for training data. BEIR format is specifically designed for retrieval evaluation and allows for:
- Multiple relevant documents per query
- Graded relevance scores
- Standard benchmarking across different models

> **Note:** Custom evaluations use the same retrieval metrics as split dataset evaluations but operate on independent test data. See [Embedding Training Guide](14-embedding-training.md#loading-beir-format-evaluation-data) for detailed format specifications.

### Custom Metrics and Evaluations

The Flywheel evaluation framework is designed to be extensible. Advanced users can define custom evaluation metrics or scoring logic by:

- **Custom LLM Judge Prompts:**  
  Supply your own prompts and criteria for LLM-based judging, enabling new types of qualitative or task-specific metrics.
- **Programmatic Metrics:**  
  Add new programmatic checks (e.g., for specific function arguments, output structure, or other behaviors) by extending the evaluation configuration and pipeline.
- **Flexible Configuration:**  
  The evaluation configuration (see `src/lib/nemo/evaluator.py`) allows you to specify additional metrics, scoring rules, or even new evaluation tasks.

> **Note:**
> If you need to track a new metric, you can add it to your evaluation config and update the result parsing logic. The results will include your custom metric in the `scores` dictionary, alongside the built-in metrics.

## Evaluation Results Format

Evaluation results are returned in a consistent structure, regardless of evaluation type. Each result includes metadata (such as evaluation type, timestamps, and progress) and a `scores` dictionary containing the relevant metrics for that evaluation.

### General Structure

```json
{
  "eval_type": "base-eval",
  "scores": { /* metric values here */ },
  "started_at": "...",
  "finished_at": "...",
  "runtime_seconds": 123.4,
  "progress": 100.0,
  "nmp_uri": "...",
  "error": null
}
```

The **keys in the `scores` dictionary vary** depending on the evaluation and workload type:

| Eval Type         | Workload Type      | scores keys (examples)                                                |
|-------------------|-------------------|-----------------------------------------------------------------------|
| base-eval         | Q&A               | similarity                                                           |
| icl-eval          | Q&A               | similarity                                                           |
| customized-eval   | Q&A               | similarity                                                           |
| base-eval         | Tool-calling      | function_name, function_name_and_args_accuracy, tool_calling_correctness |
| icl-eval          | Tool-calling      | function_name, function_name_and_args_accuracy, tool_calling_correctness |
| customized-eval   | Tool-calling      | function_name, function_name_and_args_accuracy, tool_calling_correctness |
| base-eval         | DPO               | preference_score                                                     |
| icl-eval          | DPO               | preference_score                                                     |
| customized-eval   | DPO               | preference_score                                                     |


### Examples 


**Q&A (no tool-calling)**

```json
{
  "eval_type": "base-eval",
  "scores": {
    "similarity": 9
  },
  "started_at": "...",
  "finished_at": "...",
  "runtime_seconds": 123.4,
  "progress": 100.0,
  "nmp_uri": "...",
  "error": null
}
```

**Tool-calling**

```json
{
  "eval_type": "base-eval",
  "scores": {
    "function_name": 1.0,
    "function_name_and_args_accuracy": 0.95,
    "tool_calling_correctness": 1
  },
  "started_at": "...",
  "finished_at": "...",
  "runtime_seconds": 123.4,
  "progress": 100.0,
  "nmp_uri": "...",
  "error": null
}
```

**DPO (preference alignment)**

```json
{
  "eval_type": "base-eval",
  "scores": {
    "preference_score": 8
  },
  "started_at": "...",
  "finished_at": "...",
  "runtime_seconds": 145.2,
  "progress": 100.0,
  "nmp_uri": "...",
  "error": null
}
```

> **Note**
>
> DPO preference scores range from 1-10:
> - **10**: Model response matches or exceeds the quality of the preferred response
> - **7-9**: Model response aligns well with the preferred response
> - **4-6**: Model response is acceptable, some alignment with preferred response
> - **1-3**: Model response is poor, more similar to rejected response