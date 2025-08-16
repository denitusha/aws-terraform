# ==============================================================================
# LAMBDA FUNCTION 1: REVIEW PREPROCESSOR
# ==============================================================================
# Purpose: Processes raw review data uploaded to S3, performs text preprocessing,
# and stores processed data in both S3 and DynamoDB for downstream analysis
# Trigger: S3 bucket upload events
# ==============================================================================

import os
import json
import typing
import string
import hashlib
from datetime import datetime
import boto3
import nltk
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from decimal import Decimal

# ==============================================================================
# NLTK CONFIGURATION
# ==============================================================================
# Lambda functions have limited writable space, so NLTK data must be downloaded
# to the /tmp directory which provides 512MB of temporary storage
NLTK_DATA_DIR = '/tmp/nltk_data'
os.environ['NLTK_DATA'] = NLTK_DATA_DIR
os.makedirs(NLTK_DATA_DIR, exist_ok=True)

# Global flag to prevent re-downloading NLTK data on warm Lambda starts
# This optimization reduces cold start times for subsequent invocations
nltk_data_loaded = False

# ==============================================================================
# AWS SERVICE CONFIGURATION
# ==============================================================================
# LocalStack configuration for local development/testing
# LocalStack provides local AWS service emulation for development
endpoint_url = None
if os.getenv("STAGE") == "local":
    endpoint_url = "https://localhost.localstack.cloud:4566"

# Initialize AWS service clients with optional LocalStack endpoint
s3 = boto3.client("s3", endpoint_url=endpoint_url)
ssm = boto3.client("ssm", endpoint_url=endpoint_url)
dynamodb = boto3.resource("dynamodb", endpoint_url=endpoint_url)


def load_nltk_data():
    """
    Downloads required NLTK datasets to Lambda's /tmp directory
    Only executes once per Lambda container lifecycle to optimize performance

    Downloads:
    - punkt_tab: Updated sentence tokenizer
    - punkt: Traditional sentence tokenizer (fallback)
    - stopwords: Common words to filter out (the, and, or, etc.)
    - wordnet: WordNet database for lemmatization
    """
    global nltk_data_loaded
    if not nltk_data_loaded:
        nltk.download('punkt_tab', download_dir=NLTK_DATA_DIR, quiet=True)
        nltk.download('punkt', download_dir=NLTK_DATA_DIR, quiet=True)
        nltk.download('stopwords', download_dir=NLTK_DATA_DIR, quiet=True)
        nltk.download('wordnet', download_dir=NLTK_DATA_DIR, quiet=True)
        nltk_data_loaded = True


def preprocess_text(text: str) -> str:
    """
    Performs comprehensive text preprocessing for NLP analysis

    Steps performed:
    1. Lowercase conversion - standardizes case for consistent analysis
    2. Tokenization - splits text into individual words
    3. Stopword removal - removes common words that don't add semantic value
    4. Punctuation removal - removes non-alphabetic characters
    5. Lemmatization - reduces words to their base form (running -> run)

    Args:
        text (str): Raw text to preprocess

    Returns:
        str: Cleaned, preprocessed text ready for analysis

    Example:
        Input: "The products are really amazing!"
        Output: "product really amazing"
    """
    if not text or not text.strip():
        return ""

    # Ensure NLTK data is available
    load_nltk_data()
    nltk.data.path.append(NLTK_DATA_DIR)

    # Convert to lowercase and split into tokens (words)
    tokens = word_tokenize(text.lower())

    # Create set of English stopwords for efficient lookup
    stop_words = set(stopwords.words('english'))

    # Filter tokens: keep only alphabetic words that aren't stopwords or punctuation
    filtered = [
        word for word in tokens
        if word not in stop_words
           and word not in string.punctuation
           and word.isalpha()  # Only keep alphabetic tokens
    ]

    # Lemmatize words to their base form (reduces dimensionality)
    lemmatizer = WordNetLemmatizer()
    return ' '.join([lemmatizer.lemmatize(w) for w in filtered])


def get_ssm_param(name: str) -> str:
    """
    Safely retrieves configuration parameters from AWS Systems Manager Parameter Store

    Using Parameter Store allows configuration changes without code deployment
    and provides secure storage for sensitive configuration values

    Args:
        name (str): Parameter name in Parameter Store

    Returns:
        str: Parameter value

    Raises:
        ValueError: If parameter cannot be retrieved
    """
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except Exception as e:
        raise ValueError(f"SSM Error ({name}): {str(e)}")


def parse_review_time(review: dict) -> str:
    """
    Extracts and normalizes timestamp from review data

    Handles multiple timestamp formats commonly found in review datasets:
    - Unix timestamp (unixReviewTime) - preferred format
    - Human readable date (reviewTime) - format: "MM DD, YYYY"
    - Fallback to current time if no timestamp available

    Args:
        review (dict): Review data dictionary

    Returns:
        str: ISO formatted timestamp string
    """
    if "unixReviewTime" in review:
        return datetime.utcfromtimestamp(review["unixReviewTime"]).isoformat()
    elif "reviewTime" in review:
        try:
            return datetime.strptime(review["reviewTime"], "%m %d, %Y").isoformat()
        except:
            pass
    return datetime.utcnow().isoformat()


def generate_review_id(review: dict) -> str:
    """
    Generates a unique identifier for each review

    Priority order:
    1. Use existing reviewId if present
    2. Generate hash-based ID from review metadata

    The generated ID combines product ASIN, reviewer ID, and timestamp
    to ensure uniqueness even across different data sources

    Args:
        review (dict): Review data dictionary

    Returns:
        str: Unique review identifier
    """
    if "reviewId" in review:
        return review["reviewId"]

    # Create hash from multiple fields to ensure uniqueness
    hash_input = f"{review['asin']}_{review['reviewerID']}_{review.get('unixReviewTime', datetime.utcnow().timestamp())}"
    hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    return f"{review['asin']}_{review['reviewerID']}_{hash_suffix}"


def lambda_handler(event, context):
    """
    Main Lambda function handler for review preprocessing

    Workflow:
    1. Load configuration from Parameter Store
    2. Parse S3 event to get uploaded file details
    3. Download and validate review data
    4. Generate unique review ID
    5. Preprocess review text using NLP techniques
    6. Store processed data in S3 for reference
    7. Create DynamoDB record to trigger downstream processing

    Args:
        event: S3 event notification containing bucket and key information
        context: Lambda runtime context (unused)

    Returns:
        dict: HTTP response with status code and processing results
    """
    try:
        # 1. Load configuration from Parameter Store
        # This approach allows environment-specific configuration without code changes
        config = {
            "raw_bucket": get_ssm_param("/app/config/raw_bucket"),
            "processed_bucket": get_ssm_param("/app/config/processed_bucket"),
            "reviews_table": dynamodb.Table(get_ssm_param("/app/config/reviews_table"))
        }

        # 2. Extract S3 event details
        # Lambda receives S3 events when objects are uploaded to monitored buckets
        record = event["Records"][0]["s3"]
        source_bucket = record["bucket"]["name"]
        key = record["object"]["key"]

        print(f"Processing review from s3://{source_bucket}/{key}")

        # 3. Download and parse review data from S3
        obj = s3.get_object(Bucket=source_bucket, Key=key)
        review = json.loads(obj["Body"].read())

        # 4. Validate that all required fields are present
        # Early validation prevents processing incomplete data
        required_fields = ["reviewerID", "asin", "reviewText", "overall"]
        for field in required_fields:
            if field not in review:
                raise ValueError(f"Missing required field: {field}")

        # 5. Generate unique review identifier
        review_id = generate_review_id(review)

        # 6. Extract text fields for preprocessing
        review_text = review.get("reviewText", "")
        summary_text = review.get("summary", "")

        # Create processed data structure with both original and preprocessed text
        processed_data = {
            "reviewId": review_id,
            "reviewerID": review["reviewerID"],
            "asin": review["asin"],
            "overall": float(review["overall"]),
            # Preprocessed text optimized for sentiment analysis
            "preprocessedReviewText": preprocess_text(review_text),
            "preprocessedSummary": preprocess_text(summary_text),
            # Original text preserved for profanity checking
            "originalReviewText": review_text,
            "originalSummary": summary_text,
            # Metadata fields
            "category": review.get("category", "uncategorized"),
            "reviewerName": review.get("reviewerName", ""),
            "timestamp": parse_review_time(review),
            "helpful": review.get("helpful", [0, 0])
        }

        # 7. Store processed data in S3 for downstream access
        # Organized by reviewer ID for efficient querying
        processed_key = f"preprocessed/{review['reviewerID']}/{review_id}.json"
        s3.put_object(
            Bucket=config["processed_bucket"],
            Key=processed_key,
            Body=json.dumps(processed_data, indent=2),
            ContentType="application/json"
        )

        # 8. Create DynamoDB record with metadata and S3 references
        # DynamoDB Streams will trigger profanity check Lambda on INSERT
        helpful = review.get("helpful", [0, 0])

        # Store metadata only in DynamoDB to minimize storage costs
        # Large text content remains in S3 with references stored here
        dynamo_item = {
            "reviewId": review_id,
            "userId": review["reviewerID"],
            "productId": review["asin"],
            "originalTextLocation": f"s3://{source_bucket}/{key}",
            "preprocessedLocation": f"s3://{config['processed_bucket']}/{processed_key}",
            "overallRating": Decimal(str(review["overall"])),  # DynamoDB requires Decimal for numbers
            "timestamp": parse_review_time(review),
            "wordCount": Decimal(str(len(processed_data["preprocessedReviewText"].split()))) if processed_data[
                "preprocessedReviewText"] else Decimal('0'),
            "summaryWordCount": Decimal(str(len(processed_data["preprocessedSummary"].split()))) if processed_data[
                "preprocessedSummary"] else Decimal('0'),
            "helpfulVotes": Decimal(str(helpful[0])) if len(helpful) > 0 else Decimal('0'),
            "totalVotes": Decimal(str(helpful[1])) if len(helpful) > 1 else Decimal('0'),
            "reviewerName": review.get("reviewerName", ""),
            "productCategory": review.get("category", "uncategorized"),
            "processingStatus": "preprocessed",
            "createdAt": datetime.utcnow().isoformat(),
            # Placeholder fields for downstream processing results
            "hasProfanity": None,  # Will be set by profanity check Lambda
            "sentiment": None,  # Will be set by sentiment analysis Lambda
            "profanityCheckStatus": "pending",
            "sentimentAnalysisStatus": "pending"
        }

        config["reviews_table"].put_item(Item=dynamo_item)

        print(f"Successfully preprocessed review {review_id}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "reviewId": review_id,
                "preprocessedLocation": processed_key,
                "message": "Review preprocessed successfully"
            })
        }

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "message": "Failed to preprocess review"
            })
        }


# Local testing
if __name__ == "__main__":
    # For debugging, you'll need to pass a mock event here
    print(lambda_handler({"Records": []}, None))