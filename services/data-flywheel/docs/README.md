# Data Flywheel Blueprint Documentation

Welcome to the Data Flywheel Blueprint documentation! This guide helps you navigate our comprehensive documentation to find exactly what you need.

## 🚀 Quick Start Paths

### I want to...

**🏃‍♂️ Get up and running quickly**
→ Start with [Get Started](get-started/index.md)

**🔧 Configure for my environment**  
→ Jump to [Configuration Guide](02-configuration.md)

**🏢 Deploy in production**  
→ Go to [Production Deployment](09-production-deployment.md) or [Helm Installation](10-helm-installation.md)

**📊 Understand evaluation results**  
→ Read [Evaluation Types and Metrics](05-evaluation-types-and-metrics.md)

**🔌 Integrate my application**  
→ Check out [Data Logging for AI Apps](data-logging.md)

**🔄 Automate model retraining**  
→ Set up [Automatic Retraining Triggers](15-retraining-triggers.md) and [Data Ingestion Plugins](ingestion-plugin/index.md)

**🚨 Troubleshoot issues**  
→ See [FAQ and Troubleshooting Guide](faq-troubleshooting.md) or [Limitations & Best Practices](04-limitations-best-practices.md)

---

## 👥 Documentation by Role

### For Leadership & Product Teams
- **Start here:** [Audience Guide - Leadership](03-audience-guide.md#for-leadership-cto-vp-engineering)
- **Key concepts:** [Architecture Overview](01-architecture.md)
- **Success metrics:** [Evaluation Types and Metrics](05-evaluation-types-and-metrics.md)

### For ML Engineers & Researchers  
- **Start here:** [Audience Guide - Researchers](03-audience-guide.md#for-researchers--ml-engineers)
- **Deep dive:** [Configuration Guide](02-configuration.md) → [NeMo Platform Integration](08-nemo-platform-integration.md)
- **Workflow:** [Task Orchestration](07-workflow-orchestration.md)

### For Application Engineers
- **Start here:** [Audience Guide - App Engineers](03-audience-guide.md#for-application-engineers)
- **Implementation:** [Data Logging for AI Apps](data-logging.md) → [Dataset Validation](dataset-validation.md)
- **API integration:** [API Reference](06-api-reference.md)

### For DevOps & Platform Teams
- **Start here:** [Production Deployment](09-production-deployment.md)
- **Installation:** [Helm Installation](10-helm-installation.md)
- **Operations:** [Scripts Guide](scripts.md)

---

## 📚 Complete Documentation Index

### Getting Started
| Document | Purpose | When to Read |
|----------|---------|--------------|
| **[Get Started](get-started/index.md)** | End-to-end setup guide | First time setup, demos |
| **[Architecture Overview](01-architecture.md)** | High-level system design | Understanding components |
| **[Audience Guide](03-audience-guide.md)** | Role-specific guidance | Finding your starting point |

### Configuration & Setup  
| Document | Purpose | When to Read |
|----------|---------|--------------|
| **[Configuration Guide](02-configuration.md)** | Complete configuration reference | Setting up models, evaluation, training |
| **[Data Logging for AI Apps](data-logging.md)** | Application instrumentation | Integrating your AI application |
| **[Dataset Validation](dataset-validation.md)** | Data quality requirements | Troubleshooting data issues |

### Advanced Topics
| Document | Purpose | When to Read |
|----------|---------|--------------|
| **[Evaluation Types and Metrics](05-evaluation-types-and-metrics.md)** | Understanding results | Interpreting model performance |
| **[Task Orchestration](07-workflow-orchestration.md)** | Workflow internals | Debugging, customizing workflows |
| **[NeMo Platform Integration](08-nemo-platform-integration.md)** | Advanced NeMo features | Deep NeMo customization |
| **[LoRA Model Extraction](11-lora-model-extraction.md)** | Extract LoRA adapters | Working with LoRA models |
| **[DPO Training](13-dpo-training.md)** | Preference optimization | Training with DPO |
| **[Embedding Training](14-embedding-training.md)** | Train embedding models | Working with embeddings |
| **[Automatic Retraining Triggers](15-retraining-triggers.md)** | Automated model retraining | Setting up continuous improvement |
| **[Data Ingestion Plugins](ingestion-plugin/index.md)** | Continuous data ingestion from external sources | Plugins (e.g. BugNemo), deployment, architecture|
| **[Model Deployment](model-deployment/index.md)** | Deploy fine-tuned models to DataRobot / NMP | Deploying customized models to inference |
| **[E2E Integration Testing](12-e2e-integration-testing.md)** | Testing guide | Running integration tests |
| **[Configuration Management System](02-configuration.md#configuration-management-system)** | Managing configs | Advanced configuration |

### Deployment & Operations
| Document | Purpose | When to Read |
|----------|---------|--------------|
| **[Data Ingestion Plugins](ingestion-plugin/index.md)** | Ingest data from external sources before training | Plugin setup, container deployment |
| **[Model Deployment](model-deployment/index.md)** | Deploy fine-tuned models to inference platforms | Deploying models to DataRobot / NMP |
| **[Production Deployment](09-production-deployment.md)** | Kubernetes production setup | Production deployments |
| **[Helm Installation](10-helm-installation.md)** | Helm chart deployment | Kubernetes with Helm |
| **[Scripts Guide](scripts.md)** | Utility scripts reference | Development, maintenance |

### Reference & Troubleshooting
| Document | Purpose | When to Read |
|----------|---------|--------------|
| **[API Reference](06-api-reference.md)** | Complete API documentation | Building integrations |
| **[FAQ and Troubleshooting Guide](faq-troubleshooting.md)** | Solutions to common issues | Setup problems, runtime errors |
| **[Limitations & Best Practices](04-limitations-best-practices.md)** | Common issues and solutions | Troubleshooting, optimization |

---

## 🗺️ Common Reading Paths

### 🆕 First-Time Users
1. [Architecture Overview](01-architecture.md) *(5 min - understand the big picture)*
2. [Get Started](get-started/index.md) *(30 min - get it running)*
3. [Audience Guide](03-audience-guide.md) *(choose your role)*
4. Role-specific deep dive documents

### 🔧 Setting Up for Your Use Case
1. [Configuration Guide](02-configuration.md) *(understand all options)*
2. [Data Logging for AI Apps](data-logging.md) *(instrument your app)*
3. [Dataset Validation](dataset-validation.md) *(ensure data quality)*
4. [API Reference](06-api-reference.md) *(integrate programmatically)*

### 🏢 Production Deployment
1. [Configuration Guide](02-configuration.md) *(plan your setup)*
2. [Production Deployment](09-production-deployment.md) *(deployment architecture)*
3. [Helm Installation](10-helm-installation.md) *(step-by-step install)*
4. [Limitations & Best Practices](04-limitations-best-practices.md) *(avoid common pitfalls)*

### 🔬 Research & Development
1. [NeMo Platform Integration](08-nemo-platform-integration.md) *(advanced features)*
2. [Task Orchestration](07-workflow-orchestration.md) *(workflow customization)*
3. [Evaluation Types and Metrics](05-evaluation-types-and-metrics.md) *(interpret results)*
4. [API Reference](06-api-reference.md) *(programmatic access)*

---

## 🔗 How Documents Connect

```mermaid
graph TD
    A[README.md<br/>Start Here] --> B[Audience Guide<br/>Find Your Role]
    A --> C[Get Started<br/>Get Running]
    
    B --> D[Configuration Guide<br/>Setup Details]
    C --> D
    
    D --> E[Data Logging<br/>App Integration]
    D --> F[Production Deploy<br/>or Helm Install]
    
    E --> G[Dataset Validation<br/>Data Quality]
    E --> H[API Reference<br/>Integration]
    
    F --> I[Scripts Guide<br/>Operations]
    F --> J[Best Practices<br/>Troubleshooting]
    
    D --> K[Advanced Topics:<br/>NeMo Integration,<br/>Workflow Orchestration,<br/>Evaluation Metrics]
    
    style A fill:#e1f5fe
    style B fill:#f3e5f5
    style C fill:#e8f5e8
    style D fill:#fff3e0
```

---

## 🤝 Getting Help

- **GitHub Issues:** [Report bugs or request features](https://github.com/NVIDIA-AI-Blueprints/data-flywheel/issues)
- **Documentation Issues:** Found something unclear? Let us know!
- **Community:** Join discussions and share experiences

---

**Ready to get started?** Choose your path above or dive into the [Get Started Guide](get-started/index.md)! 🚀 