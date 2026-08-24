variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "project_name" {
  type    = string
  default = "ava"
}

variable "cognito_callback_urls" {
  type    = list(string)
  default = ["http://localhost:3000/auth/callback"]
}

variable "bedrock_vision_model_id" {
  type    = string
  default = "anthropic.claude-3-5-haiku-20241022-v1:0"
}
