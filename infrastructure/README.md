# Infrastructure

- `docker/` — local development compose stack (ui, api, agent, dynamodb-local).
- `terraform/` — S3 media/artifact buckets, DynamoDB tables, EventBridge rule,
  Step Functions state machine, Cognito, IAM.
  Worker Lambda ARNs are injected via variables (defaults are placeholders)
  until the workers module is added.

## Usage

Local stack:

```bash
cd docker && docker compose up --build
../../scripts/create_local_tables.sh   # one time
```

AWS stack:

```bash
./scripts/bootstrap.sh          # init + validate + plan
./scripts/deploy.sh apply
```

## TODO

- [ ] Terraform module for the four worker Lambdas (container images) + status updater
- [ ] Wire EventBridge target -> Step Functions with input transformation
      (extract video_id/media_key from S3 event detail)
- [ ] API service deployment (ECS Fargate or App Runner) + Cognito app client wiring
- [ ] Local dev compose (dynamodb-local, minio optional)
