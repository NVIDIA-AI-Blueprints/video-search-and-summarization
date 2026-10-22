# Audience Guide

Different stakeholders engage with the Flywheel at different layers. Use the section that matches your role.

## For Leadership (CTO, VP Engineering)

- **Why it matters**: v1 targets inference **cost & latency** reduction by 50-98% while maintaining quality; future releases will pursue accuracy and agentic insights.
- **Mental Model**: Treat the flywheel as a *flashlight* that reveals promising smaller models, not an autopilot that swaps models automatically.
- **Expectations & KPIs**:
  - Cost per 1,000 tokens before/after Flywheel cycles
  - Percentage of workloads covered by instrumentation
  - Turn-around time for one Flywheel iteration (**data** → **eval** → **candidate**)
- **Organizational Investments**:
  1. **Data Logging**: green-light adding prompt/completion logs to production.
  2. **GPU/CPU Budgets**: allocate capacity for evaluator + fine-tune jobs (bursty workloads).
  3. **Review Process**: define who signs off on model promotion and what checklists (safety, compliance) apply.
- **Risk Mitigation**: Early cycles may yield *no* winner; that is a success signal that data or techniques must evolve—not a failure of the platform.

## For Product Managers

- **Opportunity**: Iterate on model quality/features without a full research team.
- **Key Questions to Answer**:
  1. Which *workloads* (features, agent nodes) matter most for cost or latency?
  2. What accuracy or UX thresholds are non-negotiable?
- **Your Inputs to Flywheel**:
  - Provide clear *workload IDs* and user intent descriptions (used for eval splitting and future classification).
  - Flag workloads that carry extra compliance or brand-risk sensitivity.
- **Metrics Dashboard** (latency & cost first, accuracy later):
  - Track evaluation scores vs. reference model per workload.
  - Monitor cost deltas for candidate models surfaced by Flywheel.

> **📖 For evaluation metrics details:** See [Evaluation Types and Metrics](05-evaluation-types-and-metrics.md)

## For Researchers / ML Engineers

- **What you get**:
  - Auto-generated evaluation datasets (base, ICL, fine-tune) from live traffic.
  - One-click comparative evaluation across many NIMs.
  - Fine-tuning jobs (LoRA) with sensible defaults.
- **How to Drill Deeper**:
  1. Inspect *divergent answers* between reference and candidate models; add them to a specialist evaluation set if needed.
  2. Experiment with advanced data-splitting or per-workload hyper-parameters.
  3. Incorporate **test-time compute** in cost models: `total_tokens × latency`.
- **Caveats & Gotchas**:
  - Flywheel performs *distillation*, not RLHF/DPO.
  - The system does **not** ingest thumbs-up / thumbs-down user feedback; if you want preference-based training, you can extend the pipeline.

> **📖 For model configuration:** See [Model Integration & Training Settings](02-configuration.md#model-integration)  
> **📖 For evaluation implementation:** See [Evaluation Types and Metrics](05-evaluation-types-and-metrics.md)  
> **📖 For NeMo platform integration:** See [NeMo Platform Integration](08-nemo-platform-integration.md)

## For Application Engineers

- **Instrumentation Requirements**

  | Task | Required | Optional | Notes |
  |------|----------|----------|-------|
  | Log prompt & completion text | ✅ | | Essential for training data |
  | Include `workload_id` | ✅ | | Critical for data partitioning |
  | Include `client_id` | ✅ | | Required for job identification |
  | Add long-form `description` | | ✅ | Recommended for better insights |
  | Record latency, tokens_in/out | | ✅ | Useful for performance analysis |

> **📖 For complete implementation guide:** See [Data Logging for AI Apps](data-logging.md)

- **Implementation Approaches**:
  1. **Production (Recommended)**: Use continuous log exportation to Elasticsearch
  2. **Development/Demo**: Use provided JSONL sample data loader
  3. **Custom Integration**: Direct Elasticsearch integration with your application

> **📖 For data validation requirements:** See [Dataset Validation](dataset-validation.md)

- **Development Tools**: 
  - Use `./scripts/run-dev.sh` for development environment with Kibana (browse `log-store-*` index) and Flower for task monitoring
  - Query API endpoint `/api/jobs/{id}` for job status and results
  - Use example notebooks for interactive exploration

> **📖 For complete API documentation:** See [API Reference](06-api-reference.md)  
> **📖 For development scripts:** See [Scripts Guide](scripts.md)

- **After Flywheel Runs**: Review results through API endpoints or notebooks to identify promising model candidates for further evaluation.

> **📖 For operational best practices:** See [Limitations & Best Practices](04-limitations-best-practices.md)
