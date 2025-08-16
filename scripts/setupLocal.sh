echo "📦 Creating S3 buckets..."

awslocal s3 mb s3://raw-reviews-bucket
awslocal s3 mb s3://processed-reviews-bucket

echo "✅ Created S3 buckets: raw-reviews-bucket, processed-reviews-bucket"

# Put the bucket names into the parameter store
echo "⚙️ Setting up SSM parameters..."

awslocal ssm put-parameter \
    --name /app/config/raw_bucket \
    --type "String" \
    --value "raw-reviews-bucket"

awslocal ssm put-parameter \
    --name /app/config/processed_bucket \
    --type "String" \
    --value "processed-reviews-bucket"

awslocal ssm put-parameter \
    --name /app/config/reviews_table \
    --type "String" \
    --value "reviews"

awslocal ssm put-parameter \
    --name /app/config/customers_table \
    --type "String" \
    --value "customers"

awslocal ssm put-parameter \
    --name /app/config/profanity_threshold \
    --type "String" \
    --value "4"

echo "✅ SSM parameters configured"

# Create DynamoDB tables
echo "🗄️ Creating DynamoDB tables..."

# Reviews table with DynamoDB Streams enabled
awslocal dynamodb create-table \
    --table-name reviews \
    --attribute-definitions \
        AttributeName=reviewId,AttributeType=S \
    --key-schema \
        AttributeName=reviewId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --stream-specification StreamEnabled=true,StreamViewType=NEW_IMAGE

awslocal dynamodb create-table \
    --table-name customers \
    --attribute-definitions \
        AttributeName=userId,AttributeType=S \
    --key-schema \
        AttributeName=userId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

echo "✅ DynamoDB tables created with streams enabled"

# Create the Lambda functions
echo "⚡ Creating Lambda functions..."

# Preprocess Lambda
(
  cd ../code/lambdas/preprocess
  echo "📦 Installing dependencies and creating lambda.zip..."

  rm -rf package lambda.zip
  mkdir -p package

  pip install -r requirements.txt -t package --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 --only-binary=:all:

  cp preprocess.py package/

  (cd package && zip -r ../lambda.zip .) || exit 1
)

awslocal lambda create-function \
--function-name preprocess \
--runtime python3.11 \
--timeout 50 \
--zip-file fileb://code/lambdas/preprocess/lambda.zip \
--handler preprocess.lambda_handler \
--role arn:aws:iam::000000000000:role/lambda-role \
--environment Variables="{STAGE=local}"

awslocal lambda create-function-url-config \
--function-name preprocess \
--auth-type NONE

# Sentiment Lambda
(
  cd ../code/lambdas/sentiment
  echo "📦 Installing dependencies and creating lambda.zip..."

  rm -rf package lambda.zip
  mkdir -p package

  pip install -r requirements.txt -t package --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 --only-binary=:all:

  cp sentiment.py package/

  (cd package && zip -r ../lambda.zip .) || exit 1
)

awslocal lambda create-function \
  --function-name sentiment \
  --runtime python3.11 \
  --timeout 50 \
  --zip-file fileb://code/lambdas/sentiment/lambda.zip \
  --role arn:aws:iam::000000000000:role/lambda-role \
  --handler sentiment.lambda_handler \
  --environment Variables="{STAGE=local}"

# Profanity Check Lambda
(
  cd ../code/lambdas/profanity
  echo "📦 Installing dependencies and creating lambda.zip..."

  rm -rf package lambda.zip
  mkdir -p package

  pip install -r requirements.txt -t package --platform manylinux2014_x86_64 --implementation cp --python-version 3.11 --only-binary=:all:

  cp profanity.py package/

  (cd package && zip -r ../lambda.zip .) || exit 1
)

awslocal lambda create-function \
  --function-name profanity \
  --runtime python3.11 \
  --timeout 50 \
  --zip-file fileb://code/lambdas/profanity/lambda.zip \
  --role arn:aws:iam::000000000000:role/lambda-role \
  --handler profanity.lambda_handler \
  --environment Variables="{STAGE=local}"

# Step 2: Get DynamoDB Stream ARN
echo -e "\n🔍 Retrieving DynamoDB stream ARN..."

streamArn=$(awslocal dynamodbstreams list-streams \
  --table-name reviews \
  --query "Streams[0].StreamArn" \
  --output text)

echo "✅ Stream ARN: $streamArn"

# Connect both sentiment and profanity to DynamoDB stream
awslocal lambda create-event-source-mapping \
  --function-name sentiment \
  --event-source-arn "$streamArn" \
  --starting-position LATEST

awslocal lambda create-event-source-mapping \
  --function-name profanity \
  --event-source-arn "$streamArn" \
  --starting-position LATEST

awslocal s3api put-bucket-notification-configuration \
--bucket raw-reviews-bucket \
--notification-configuration '{
 "LambdaFunctionConfigurations": [
 {
 "LambdaFunctionArn": "arn:aws:lambda:us-east-1:000000000000:function:preprocess",
 "Events": ["s3:ObjectCreated:*"]
 }
 ]
 }'

echo "✅ All Lambda functions created and configured"
