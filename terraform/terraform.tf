# ==============================================================================
# TERRAFORM CONFIGURATION FOR AMAZON REVIEWS PROCESSING PIPELINE
# ==============================================================================
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = "eu-north-1"
}

# ==============================================================================
# VARIABLES
# ==============================================================================

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "reviews-processor"
}

variable "profanity_threshold" {
  description = "Number of profanity violations before customer ban"
  type        = number
  default     = 4
}

variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

# ==============================================================================
# LOCALS
# ==============================================================================

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = {
    Environment = var.environment
    Project     = var.project_name
    ManagedBy   = "terraform"
  }
}

# ==============================================================================
# DATA SOURCES
# ==============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ==============================================================================
# S3 BUCKETS
# ==============================================================================

# Raw reviews bucket - receives uploaded review files
resource "aws_s3_bucket" "raw_reviews" {
  bucket = "${local.name_prefix}-raw-reviews"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "raw_reviews" {
  bucket = aws_s3_bucket.raw_reviews.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_reviews" {
  bucket = aws_s3_bucket.raw_reviews.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Processed reviews bucket - stores preprocessed review data
resource "aws_s3_bucket" "processed_reviews" {
  bucket = "${local.name_prefix}-processed-reviews"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "processed_reviews" {
  bucket = aws_s3_bucket.processed_reviews.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "processed_reviews" {
  bucket = aws_s3_bucket.processed_reviews.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ==============================================================================
# DYNAMODB TABLES
# ==============================================================================

# Reviews table - stores review metadata and processing status
resource "aws_dynamodb_table" "reviews" {
  name             = "${local.name_prefix}-reviews"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "reviewId"
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"
  attribute {
    name = "reviewId"
    type = "S"
  }

  # GSI for querying by user
  attribute {
    name = "userId"
    type = "S"
  }

  global_secondary_index {
    name            = "UserIndex"
    hash_key        = "userId"
    projection_type = "ALL"

  }

  # GSI for querying by product
  attribute {
    name = "productId"
    type = "S"
  }

  global_secondary_index {
    name            = "ProductIndex"
    hash_key        = "productId"
    projection_type = "ALL"

  }

  tags = local.common_tags
}

# Customers table - tracks customer behavior and violation history
resource "aws_dynamodb_table" "customers" {
  name         = "${local.name_prefix}-customers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"

  attribute {
    name = "userId"
    type = "S"
  }

  tags = local.common_tags
}

# ==============================================================================
# SSM PARAMETERS
# ==============================================================================

resource "aws_ssm_parameter" "raw_bucket" {
  name  = "/app/config/raw_bucket"
  type  = "String"
  value = aws_s3_bucket.raw_reviews.bucket
  tags  = local.common_tags
}

resource "aws_ssm_parameter" "processed_bucket" {
  name  = "/app/config/processed_bucket"
  type  = "String"
  value = aws_s3_bucket.processed_reviews.bucket
  tags  = local.common_tags
}

resource "aws_ssm_parameter" "reviews_table" {
  name  = "/app/config/reviews_table"
  type  = "String"
  value = aws_dynamodb_table.reviews.name
  tags  = local.common_tags
}

resource "aws_ssm_parameter" "customers_table" {
  name  = "/app/config/customers_table"
  type  = "String"
  value = aws_dynamodb_table.customers.name
  tags  = local.common_tags
}

resource "aws_ssm_parameter" "profanity_threshold" {
  name  = "/app/config/profanity_threshold"
  type  = "String"
  value = tostring(var.profanity_threshold)
  tags  = local.common_tags
}

# ==============================================================================
# IAM ROLES AND POLICIES
# ==============================================================================

# Lambda execution role
resource "aws_iam_role" "lambda_role" {
  name = "${local.name_prefix}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

# Lambda execution policy
resource "aws_iam_role_policy" "lambda_policy" {
  name = "${local.name_prefix}-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.raw_reviews.arn,
          "${aws_s3_bucket.raw_reviews.arn}/*",
          aws_s3_bucket.processed_reviews.arn,
          "${aws_s3_bucket.processed_reviews.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.reviews.arn,
          "${aws_dynamodb_table.reviews.arn}/index/*",
          aws_dynamodb_table.customers.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeStream",
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:ListStreams"
        ]
        Resource = aws_dynamodb_table.reviews.stream_arn
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/app/config/*"
      }
    ]
  })
}

# ==============================================================================
# LAMBDA FUNCTIONS
# ==============================================================================

# Create deployment packages for Lambda functions
data "archive_file" "preprocess_lambda" {
  type        = "zip"
  output_path = "${path.module}/preprocess_lambda.zip"
  source_dir  = "${path.module}/../lambda/preprocess"
  excludes    = ["*.pyc", "__pycache__", "*.zip", ".DS_Store"]
}

data "archive_file" "profanity_lambda" {
  type        = "zip"
  output_path = "${path.module}/profanity_lambda.zip"
  source_dir  = "${path.module}/../lambda/profanity"
  excludes    = ["*.pyc", "__pycache__", "*.zip", ".DS_Store"]
}

data "archive_file" "sentiment_lambda" {
  type        = "zip"
  output_path = "${path.module}/sentiment_lambda.zip"
  source_dir  = "${path.module}/../lambda/sentiment"
  excludes    = ["*.pyc", "__pycache__", "*.zip", ".DS_Store"]
}

# Preprocess Lambda Function
resource "aws_lambda_function" "preprocess" {
  filename         = data.archive_file.preprocess_lambda.output_path
  function_name    = "${local.name_prefix}-preprocess"
  role             = aws_iam_role.lambda_role.arn
  handler          = "preprocess.lambda_handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 512
  source_code_hash = data.archive_file.preprocess_lambda.output_base64sha256

  environment {
    variables = {
      STAGE = "aws"
    }
  }

  layers = [
    aws_lambda_layer_version.python_dependencies.arn
  ]
  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy.lambda_policy,
    aws_cloudwatch_log_group.preprocess_logs
  ]
}

# Profanity Check Lambda Function
resource "aws_lambda_function" "profanity" {
  filename         = data.archive_file.profanity_lambda.output_path
  function_name    = "${local.name_prefix}-profanity"
  role             = aws_iam_role.lambda_role.arn
  handler          = "profanity.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30
  memory_size      = 256
  source_code_hash = data.archive_file.profanity_lambda.output_base64sha256

  environment {
    variables = {
      STAGE = "aws"
    }
  }
  layers = [
    aws_lambda_layer_version.python_dependencies.arn
  ]

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy.lambda_policy,
    aws_cloudwatch_log_group.profanity_logs
  ]
}

# Sentiment Analysis Lambda Function
resource "aws_lambda_function" "sentiment" {
  filename         = data.archive_file.sentiment_lambda.output_path
  function_name    = "${local.name_prefix}-sentiment"
  role             = aws_iam_role.lambda_role.arn
  handler          = "sentiment.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30
  memory_size      = 256
  source_code_hash = data.archive_file.sentiment_lambda.output_base64sha256

  layers = [
    aws_lambda_layer_version.python_dependencies.arn
  ]
  environment {
    variables = {
      STAGE = "aws"
    }
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy.lambda_policy,
    aws_cloudwatch_log_group.sentiment_logs
  ]
}

# ==============================================================================
# CLOUDWATCH LOG GROUPS
# ==============================================================================

resource "aws_cloudwatch_log_group" "preprocess_logs" {
  name              = "/aws/lambda/${local.name_prefix}-preprocess"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "profanity_logs" {
  name              = "/aws/lambda/${local.name_prefix}-profanity"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "sentiment_logs" {
  name              = "/aws/lambda/${local.name_prefix}-sentiment"
  retention_in_days = 14
  tags              = local.common_tags
}

# ==============================================================================
# S3 BUCKET NOTIFICATIONS
# ==============================================================================

# Lambda permission for S3 to invoke preprocess function
resource "aws_lambda_permission" "s3_invoke_preprocess" {
  statement_id  = "AllowExecutionFromS3Bucket"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.preprocess.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_reviews.arn
}

# S3 bucket notification to trigger preprocessing
resource "aws_s3_bucket_notification" "raw_reviews_notification" {
  bucket = aws_s3_bucket.raw_reviews.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.preprocess.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.s3_invoke_preprocess]
}

# ==============================================================================
# DYNAMODB STREAM EVENT SOURCE MAPPINGS
# ==============================================================================

# DynamoDB stream trigger for profanity check
resource "aws_lambda_event_source_mapping" "profanity_stream" {
  event_source_arn  = aws_dynamodb_table.reviews.stream_arn
  function_name     = aws_lambda_function.profanity.arn
  starting_position = "LATEST"

  depends_on = [aws_iam_role_policy.lambda_policy]
}

# DynamoDB stream trigger for sentiment analysis
resource "aws_lambda_event_source_mapping" "sentiment_stream" {
  event_source_arn  = aws_dynamodb_table.reviews.stream_arn
  function_name     = aws_lambda_function.sentiment.arn
  starting_position = "LATEST"

  depends_on = [aws_iam_role_policy.lambda_policy]
}

# ==============================================================================
# LAMBDA LAYERS (for Python dependencies)
# ==============================================================================

# Create a layer for common Python dependencies
data "archive_file" "python_dependencies_layer" {
  type        = "zip"
  output_path = "${path.module}/python_dependencies_layer.zip"

  source {
    content  = <<EOF
nltk
regex
profanityfilter
EOF
    filename = "python/requirements.txt"
  }
}

resource "aws_lambda_layer_version" "python_dependencies" {
  filename         = data.archive_file.python_dependencies_layer.output_path
  layer_name       = "${local.name_prefix}-python-deps"
  source_code_hash = data.archive_file.python_dependencies_layer.output_base64sha256

  compatible_runtimes = ["python3.11"]
  description         = "Python dependencies for reviews processing (nltk, boto3, profanityfilter)"
}

# ==============================================================================
# OUTPUTS
# ==============================================================================

output "raw_reviews_bucket" {
  description = "Name of the raw reviews S3 bucket"
  value       = aws_s3_bucket.raw_reviews.bucket
}

output "processed_reviews_bucket" {
  description = "Name of the processed reviews S3 bucket"
  value       = aws_s3_bucket.processed_reviews.bucket
}

output "reviews_table_name" {
  description = "Name of the reviews DynamoDB table"
  value       = aws_dynamodb_table.reviews.name
}

output "customers_table_name" {
  description = "Name of the customers DynamoDB table"
  value       = aws_dynamodb_table.customers.name
}

output "preprocess_function_name" {
  description = "Name of the preprocess Lambda function"
  value       = aws_lambda_function.preprocess.function_name
}

output "profanity_function_name" {
  description = "Name of the profanity check Lambda function"
  value       = aws_lambda_function.profanity.function_name
}

output "sentiment_function_name" {
  description = "Name of the sentiment analysis Lambda function"
  value       = aws_lambda_function.sentiment.function_name
}

output "reviews_table_stream_arn" {
  description = "ARN of the DynamoDB stream for the reviews table"
  value       = aws_dynamodb_table.reviews.stream_arn
}

output "upload_command_example" {
  description = "Example AWS CLI command to upload a review file"
  value       = "aws s3 cp review.json s3://${aws_s3_bucket.raw_reviews.bucket}/review.json"
}