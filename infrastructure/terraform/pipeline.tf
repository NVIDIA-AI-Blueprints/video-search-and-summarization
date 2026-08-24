# ---------------------------------------------------------------------------
# EventBridge: S3 object created -> processing state machine
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "media_uploaded" {
  name        = "${local.name_prefix}-media-uploaded"
  description = "Trigger video processing when a new media object lands in S3"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.media.bucket] }
      key    = [{ prefix = "videos/" }]
    }
  })
}

# ---------------------------------------------------------------------------
# IAM: worker execution roles
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "workers_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com", "states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "workers" {
  name               = "${local.name_prefix}-workers"
  assume_role_policy = data.aws_iam_policy_document.workers_assume.json
}

data "aws_iam_policy_document" "workers_permissions" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.media.arn,
      "${aws_s3_bucket.media.arn}/*",
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]
  }
  statement {
    actions   = ["transcribe:*"]
    resources = ["*"] # Transcribe does not support resource-level perms for all calls
  }
  statement {
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_vision_model_id}",
      "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0",
    ]
  }
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.videos.arn,
      aws_dynamodb_table.users.arn,
      aws_dynamodb_table.audit.arn,
    ]
  }
}

resource "aws_iam_role_policy" "workers" {
  name   = "${local.name_prefix}-workers-policy"
  role   = aws_iam_role.workers.id
  policy = data.aws_iam_policy_document.workers_permissions.json
}

resource "aws_iam_role" "api" {
  name               = "${local.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.workers_assume.json
}

# ---------------------------------------------------------------------------
# Step Functions: upload -> transcribe -> vision -> chunk -> embed -> ready
#
# NOTE: the worker Lambda functions are deployed separately (services/workers).
# Once their ARNs are available (terraform_remote_state or SSM), replace the
# placeholders below.
# ---------------------------------------------------------------------------

locals {
  sfn_definition = templatefile("${path.module}/state-machine.asl.json.tftpl", {
    transcribe_start_lambda_arn = var.transcribe_start_lambda_arn
    transcribe_poll_lambda_arn  = var.transcribe_poll_lambda_arn
    vision_lambda_arn           = var.vision_lambda_arn
    chunking_lambda_arn         = var.chunking_lambda_arn
    embeddings_lambda_arn       = var.embeddings_lambda_arn
    status_update_lambda_arn    = var.status_update_lambda_arn
  })
}

variable "transcribe_start_lambda_arn" {
  type    = string
  default = "REPLACE_WITH_LAMBDA_ARN"
}
variable "transcribe_poll_lambda_arn" {
  type    = string
  default = "REPLACE_WITH_LAMBDA_ARN"
}
variable "vision_lambda_arn" {
  type    = string
  default = "REPLACE_WITH_LAMBDA_ARN"
}
variable "chunking_lambda_arn" {
  type    = string
  default = "REPLACE_WITH_LAMBDA_ARN"
}
variable "embeddings_lambda_arn" {
  type    = string
  default = "REPLACE_WITH_LAMBDA_ARN"
}
variable "status_update_lambda_arn" {
  type    = string
  default = "REPLACE_WITH_LAMBDA_ARN"
}

resource "aws_sfn_state_machine" "video_processing" {
  name     = "${local.name_prefix}-video-processing"
  role_arn = aws_iam_role.workers.arn

  definition = local.sfn_definition
}

# ---------------------------------------------------------------------------
# Cognito user pool (auth boundary)
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool" "main" {
  name = "${local.name_prefix}-users"

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name_prefix}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  callback_urls                        = var.cognito_callback_urls
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  depends_on = [aws_cognito_user_pool_domain.main]
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${local.name_prefix}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "media_bucket" {
  value = aws_s3_bucket.media.bucket
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "videos_table" {
  value = aws_dynamodb_table.videos.name
}

output "processing_state_machine_arn" {
  value = aws_sfn_state_machine.video_processing.arn
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}
