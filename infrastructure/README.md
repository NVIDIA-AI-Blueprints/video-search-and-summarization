# Infrastructure

- `terraform/` — S3 media/artifact buckets, DynamoDB tables, EventBridge rule,
  Step Functions state machine, Cognito, IAM.
  Worker Lambda ARNs are injected via variables (defaults are placeholders)
  until the workers module is added.

## Usage

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
