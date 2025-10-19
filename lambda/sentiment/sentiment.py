# ==============================================================================
# LAMBDA FUNCTION 3: SENTIMENT ANALYZER
# ==============================================================================
# Purpose: Analyzes sentiment of preprocessed reviews using NLTK VADER and
# overall rating correlation, then updates DynamoDB with results
# Trigger: DynamoDB Streams from reviews table (after preprocessing)
# ==============================================================================

import boto3
import re
import nltk
import json
import os
from nltk.sentiment import SentimentIntensityAnalyzer

# ==============================================================================
# NLTK CONFIGURATION FOR SENTIMENT ANALYSIS
# ==============================================================================
# Lambda functions require NLTK data to be downloaded to /tmp directory
NLTK_DATA_DIR = '/tmp/nltk_data'
os.environ['NLTK_DATA'] = NLTK_DATA_DIR
os.makedirs(NLTK_DATA_DIR, exist_ok=True)

# ==============================================================================
# AWS SERVICE SETUP FOR SENTIMENT ANALYSIS
# ==============================================================================
ENDPOINT =  None

s3 = boto3.client("s3", endpoint_url=ENDPOINT)
ssm = boto3.client("ssm", endpoint_url=ENDPOINT)
dynamodb = boto3.resource("dynamodb", endpoint_url=ENDPOINT)
nltk_data_loaded = False


def load_nltk_data():
    """
    Downloads VADER sentiment lexicon to Lambda's /tmp directory
    VADER (Valence Aware Dictionary and sEntiment Reasoner) is specifically
    designed for social media text and handles informal language well
    """
    global nltk_data_loaded
    if not nltk_data_loaded:
        nltk.download('vader_lexicon', download_dir=NLTK_DATA_DIR, quiet=True)
        nltk_data_loaded = True


# Utility function to fetch configuration from Parameter Store
def get_parameter(name):
    """Retrieve configuration parameter from AWS Systems Manager"""
    return ssm.get_parameter(Name=name)["Parameter"]["Value"]


# Initialize DynamoDB table connection


def parse_s3_uri(uri: str):
    """
    Parse S3 URI format (s3://bucket/key) into components

    Args:
        uri (str): S3 URI in format s3://bucket/key

    Returns:
        tuple: (bucket_name, object_key)

    Raises:
        ValueError: If URI format is invalid
    """
    match = re.match(r"s3://([^/]+)/(.+)", uri)
    if not match:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return match.group(1), match.group(2)


def analyze_sentiment(summary, review_text, overall):
    """
    Performs advanced sentiment analysis combining NLTK VADER with rating correlation

    This hybrid approach improves accuracy by considering both:
    1. Text-based sentiment using VADER lexicon
    2. Numerical rating as sentiment indicator

    VADER scores range from -1 (most negative) to +1 (most positive)
    The compound score provides an overall sentiment measure

    Args:
        summary (str): Preprocessed review summary text
        review_text (str): Preprocessed review text
        overall (float): Numerical rating (1-5 scale)

    Returns:
        str: Sentiment classification ('positive', 'negative', or 'neutral')
    """
    # Ensure NLTK data is available
    load_nltk_data()
    nltk.data.path.append(NLTK_DATA_DIR)

    # Combine summary and review text for comprehensive analysis
    combined_text = f"{summary.strip()} {review_text.strip()}"

    # Initialize VADER sentiment analyzer
    sia = SentimentIntensityAnalyzer()
    scores = sia.polarity_scores(combined_text)
    compound = scores['compound']  # Overall sentiment score

    # Derive sentiment indication from numerical rating
    rating_sentiment = None
    if overall >= 4:
        rating_sentiment = "positive"
    elif overall <= 2:
        rating_sentiment = "negative"
    # 3-star ratings are considered neutral

    # Apply hybrid classification logic
    # Strong text sentiment or alignment with rating sentiment
    if compound >= 0.5 or (compound > 0 and rating_sentiment == "positive"):
        return "positive"
    elif compound <= -0.5 or (compound < 0 and rating_sentiment == "negative"):
        return "negative"
    else:
        return "neutral"


def lambda_handler(event, context):
    """
    Processes DynamoDB stream events to perform sentiment analysis on reviews

    Workflow:
    1. Filter for INSERT events (new reviews)
    2. Extract review metadata from DynamoDB stream
    3. Retrieve preprocessed text from S3
    4. Perform sentiment analysis using hybrid approach
    5. Update DynamoDB record with sentiment results

    Args:
        event: DynamoDB Streams event containing record changes
        context: Lambda runtime context

    Returns:
        dict: Processing status response
    """
    REVIEWS_TABLE_NAME = get_parameter("/app/config/reviews_table")
    reviews_table = dynamodb.Table(REVIEWS_TABLE_NAME)

    # Process each record in the stream event
    for record in event.get("Records", []):
        # Only process new record insertions
        if record["eventName"] != "INSERT":
            continue

        # Extract review data from DynamoDB stream format
        new_image = record["dynamodb"].get("NewImage", {})

        review_id = new_image["reviewId"]["S"]

        # Retrieve preprocessed text from S3 using stored reference
        preprocessed_uri = new_image.get("preprocessedLocation", {}).get("S", "")
        bucket, key = parse_s3_uri(preprocessed_uri)

        # Download and parse preprocessed review data
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read())

        # Extract preprocessed text fields (already cleaned and lemmatized)
        summary = data.get("preprocessedSummary", "")
        review_text = data.get("preprocessedReviewText", "")

        # Extract overall rating with error handling
        overall_str = new_image.get("overallRating", {}).get("N", "3")
        try:
            overall = float(overall_str)
        except ValueError:
            overall = 3.0  # Default to neutral rating if parsing fails

        # Perform sentiment analysis using hybrid approach
        sentiment = analyze_sentiment(summary, review_text, overall)

        # Update the review record with sentiment analysis results
        try:
            reviews_table.update_item(
                Key={"reviewId": review_id},
                UpdateExpression="SET sentiment = :s, sentimentAnalysisStatus = :status",
                ExpressionAttributeValues={
                    ":s": sentiment,
                    ":status": "processed"
                }
            )
            print(f"Updated sentiment for review {review_id}: {sentiment}")
        except Exception as e:
            print(f"Failed to update sentiment for reviewId {review_id}: {e}")

    return {
        "statusCode": 200,
        "message": "Sentiment analysis completed successfully."
    }


# Local testing entry point
if __name__ == "__main__":
    print(lambda_handler(None, None))