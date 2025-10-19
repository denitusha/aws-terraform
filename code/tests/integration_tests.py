#!/usr/bin/env python3
"""
Integration Test Suite for Review Processing Pipeline
====================================================

This test suite validates the complete end-to-end workflow:
1. Upload review to S3 raw bucket
2. Preprocess Lambda processes the review
3. Profanity Lambda checks for inappropriate content
4. Sentiment Lambda analyzes text sentiment
5. Validate all data is correctly stored in DynamoDB

Requirements:
- LocalStack running with services configured
- All Lambda functions deployed
- DynamoDB tables created
- S3 buckets created
"""

import json
import time
import boto3
import pytest
import os
from datetime import datetime
from decimal import Decimal

# ==============================================================================
# TEST CONFIGURATION
# ==============================================================================

# LocalStack endpoint for testing
ENDPOINT_URL = "http://localhost.localstack.cloud:4566"

# AWS clients
def create_aws_clients():
    """Create AWS clients with proper error handling"""
    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=ENDPOINT_URL,
            region_name='us-east-1',
            aws_access_key_id='test',
            aws_secret_access_key='test'
        )

        dynamodb = boto3.resource(
            'dynamodb',
            endpoint_url=ENDPOINT_URL,
            region_name='us-east-1',
            aws_access_key_id='test',
            aws_secret_access_key='test'
        )

        lambda_client = boto3.client(
            'lambda',
            endpoint_url=ENDPOINT_URL,
            region_name='us-east-1',
            aws_access_key_id='test',
            aws_secret_access_key='test'
        )

        return s3_client, dynamodb, lambda_client
    except Exception as e:
        print(f"❌ Error creating AWS clients: {e}")
        print("💡 Make sure LocalStack is running: docker run --rm -it -p 4566:4566 localstack/localstack")
        raise


# Create clients
s3_client, dynamodb, lambda_client = create_aws_clients()

# Table references
reviews_table = dynamodb.Table('reviews')
customers_table = dynamodb.Table('customers')

# Table references
reviews_table = dynamodb.Table('reviews')
customers_table = dynamodb.Table('customers')

# Test configuration
RAW_BUCKET = "raw-reviews-bucket"
PROCESSED_BUCKET = "processed-reviews-bucket"

# ==============================================================================
# TEST DATA SAMPLES
# ==============================================================================
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "test"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
POSITIVE_REVIEW = {
    "reviewerID": "test_user_001",
    "asin": "B001E94B8Y",
    "reviewerName": "John Smith",
    "helpful": [2, 3],
    "reviewText": "This product is absolutely amazing! I love it so much and would definitely recommend it to everyone.",
    "overall": 5.0,
    "summary": "Excellent product, highly recommended!",
    "unixReviewTime": 1393632000,
    "reviewTime": "02 28, 2014",
    "category": "Electronics"
}

NEGATIVE_REVIEW = {
    "reviewerID": "test_user_002",
    "asin": "B002E94B8Z",
    "reviewerName": "Jane Doe",
    "helpful": [1, 5],
    "reviewText": "This product is terrible and completely useless. I hate it and want my money back.",
    "overall": 1.0,
    "summary": "Worst purchase ever, avoid at all costs",
    "unixReviewTime": 1393632001,
    "reviewTime": "02 28, 2014",
    "category": "Home & Garden"
}

PROFANITY_REVIEW = {
    "reviewerID": "test_user_003",
    "asin": "B003E94B8X",
    "reviewerName": "Bad User",
    "helpful": [0, 1],
    "reviewText": "This damn product is complete shit and fucking useless!",
    "overall": 1.0,
    "summary": "Absolute crap",
    "unixReviewTime": 1393632002,
    "reviewTime": "02 28, 2014",
    "category": "Books"
}

NEUTRAL_REVIEW = {
    "reviewerID": "test_user_004",
    "asin": "B004E94B8W",
    "reviewerName": "Neutral User",
    "helpful": [3, 6],
    "reviewText": "This product is okay. It works as expected but nothing special.",
    "overall": 3.0,
    "summary": "Average product",
    "unixReviewTime": 1393632003,
    "reviewTime": "02 28, 2014",
    "category": "Sports"
}


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def upload_review_to_s3(review_data, key_suffix=""):
    """Upload a review JSON to the raw S3 bucket"""
    key = f"test_review_{review_data['reviewerID']}_{key_suffix}.json"

    s3_client.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=json.dumps(review_data, indent=2),
        ContentType='application/json'
    )
    return key


def wait_for_processing(review_id, max_wait=60):
    """Wait for all processing stages to complete"""
    start_time = time.time()

    while time.time() - start_time < max_wait:
        try:
            response = reviews_table.get_item(Key={'reviewId': review_id})
            if 'Item' in response:
                item = response['Item']

                # Check if all processing stages are complete
                preprocessing_done = item.get('processingStatus') == 'preprocessed'
                profanity_done = item.get('profanityCheckStatus') == 'completed'
                sentiment_done = item.get('sentimentAnalysisStatus') == 'processed'

                if preprocessing_done and profanity_done and sentiment_done:
                    return item

            time.sleep(2)
        except Exception as e:
            print(f"Error checking processing status: {e}")
            time.sleep(2)

    raise TimeoutError(f"Processing did not complete within {max_wait} seconds")


def cleanup_test_data():
    """Clean up test data from DynamoDB tables"""
    # Clean reviews table
    try:
        scan_response = reviews_table.scan()
        for item in scan_response.get('Items', []):
            if item['userId'].startswith('test_user_'):
                reviews_table.delete_item(Key={'reviewId': item['reviewId']})
                print(f"Deleted review: {item['reviewId']}")
    except Exception as e:
        print(f"Error cleaning reviews table: {e}")

    # Clean customers table
    try:
        scan_response = customers_table.scan()
        for item in scan_response.get('Items', []):
            if item['userId'].startswith('test_user_'):
                customers_table.delete_item(Key={'userId': item['userId']})
                print(f"Deleted customer: {item['userId']}")
    except Exception as e:
        print(f"Error cleaning customers table: {e}")


# ==============================================================================
# TEST CASES
# ==============================================================================

class TestReviewProcessingPipeline:
    """Integration tests for the complete review processing pipeline"""

    @classmethod
    def setup_class(cls):
        """Setup before running tests"""
        print("🧹 Cleaning up any existing test data...")
        cleanup_test_data()
        time.sleep(2)

    @classmethod
    def teardown_class(cls):
        """Cleanup after running tests"""
        print("🧹 Cleaning up test data...")
        cleanup_test_data()

    def test_positive_review_processing(self):
        """Test processing of a positive review"""
        print("\n🧪 Testing positive review processing...")

        # Upload review to S3
        key = upload_review_to_s3(POSITIVE_REVIEW, "positive")
        print(f"✅ Uploaded review to s3://{RAW_BUCKET}/{key}")

        # Generate expected review ID (matches logic in preprocess Lambda)
        import hashlib
        hash_input = f"{POSITIVE_REVIEW['asin']}_{POSITIVE_REVIEW['reviewerID']}_{POSITIVE_REVIEW['unixReviewTime']}"
        hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        expected_review_id = f"{POSITIVE_REVIEW['asin']}_{POSITIVE_REVIEW['reviewerID']}_{hash_suffix}"

        # Wait for processing to complete
        processed_item = wait_for_processing(expected_review_id)
        print(f"✅ Processing completed for review: {expected_review_id}")

        # Validate preprocessing results
        assert processed_item['processingStatus'] == 'preprocessed'
        assert processed_item['userId'] == POSITIVE_REVIEW['reviewerID']
        assert processed_item['productId'] == POSITIVE_REVIEW['asin']
        assert float(processed_item['overallRating']) == POSITIVE_REVIEW['overall']
        assert processed_item['wordCount'] > 0  # Should have processed text
        print("✅ Preprocessing validation passed")

        # Validate profanity check results
        assert processed_item['profanityCheckStatus'] == 'completed'
        assert processed_item['hasProfanity'] == False  # Should be clean
        print("✅ Profanity check validation passed")

        # Validate sentiment analysis results
        assert processed_item['sentimentAnalysisStatus'] == 'processed'
        assert processed_item['sentiment'] == 'positive'  # Should detect positive sentiment
        print("✅ Sentiment analysis validation passed")

        # Validate customer tracking
        customer_response = customers_table.get_item(Key={'userId': POSITIVE_REVIEW['reviewerID']})
        assert 'Item' in customer_response
        customer = customer_response['Item']
        assert customer['totalReviews'] >= 1
        assert customer['violationCount'] == 0  # No profanity violations
        assert customer['isBanned'] == False
        print("✅ Customer tracking validation passed")

        print("🎉 Positive review test completed successfully!")

    def test_negative_review_processing(self):
        """Test processing of a negative review"""
        print("\n🧪 Testing negative review processing...")

        key = upload_review_to_s3(NEGATIVE_REVIEW, "negative")
        print(f"✅ Uploaded review to s3://{RAW_BUCKET}/{key}")

        # Generate expected review ID
        import hashlib
        hash_input = f"{NEGATIVE_REVIEW['asin']}_{NEGATIVE_REVIEW['reviewerID']}_{NEGATIVE_REVIEW['unixReviewTime']}"
        hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        expected_review_id = f"{NEGATIVE_REVIEW['asin']}_{NEGATIVE_REVIEW['reviewerID']}_{hash_suffix}"

        processed_item = wait_for_processing(expected_review_id)
        print(f"✅ Processing completed for review: {expected_review_id}")

        # Validate sentiment is correctly identified as negative
        assert processed_item['sentiment'] == 'negative'
        assert processed_item['hasProfanity'] == False  # Negative but not profane
        print("✅ Negative sentiment correctly detected")

        print("🎉 Negative review test completed successfully!")

    def test_profanity_review_processing(self):
        """Test processing of a review with profanity"""
        print("\n🧪 Testing profanity review processing...")

        key = upload_review_to_s3(PROFANITY_REVIEW, "profanity")
        print(f"✅ Uploaded review to s3://{RAW_BUCKET}/{key}")

        # Generate expected review ID
        import hashlib
        hash_input = f"{PROFANITY_REVIEW['asin']}_{PROFANITY_REVIEW['reviewerID']}_{PROFANITY_REVIEW['unixReviewTime']}"
        hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        expected_review_id = f"{PROFANITY_REVIEW['asin']}_{PROFANITY_REVIEW['reviewerID']}_{hash_suffix}"

        processed_item = wait_for_processing(expected_review_id)
        print(f"✅ Processing completed for review: {expected_review_id}")

        # Validate profanity detection
        assert processed_item['hasProfanity'] == True
        assert processed_item['sentiment'] == 'negative'  # Profane content is typically negative
        print("✅ Profanity correctly detected")

        # Validate customer violation tracking
        customer_response = customers_table.get_item(Key={'userId': PROFANITY_REVIEW['reviewerID']})
        assert 'Item' in customer_response
        customer = customer_response['Item']
        assert customer['violationCount'] >= 1
        print("✅ Customer violation tracking working")

        print("🎉 Profanity review test completed successfully!")

    def test_neutral_review_processing(self):
        """Test processing of a neutral review"""
        print("\n🧪 Testing neutral review processing...")

        key = upload_review_to_s3(NEUTRAL_REVIEW, "neutral")
        print(f"✅ Uploaded review to s3://{RAW_BUCKET}/{key}")

        # Generate expected review ID
        import hashlib
        hash_input = f"{NEUTRAL_REVIEW['asin']}_{NEUTRAL_REVIEW['reviewerID']}_{NEUTRAL_REVIEW['unixReviewTime']}"
        hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        expected_review_id = f"{NEUTRAL_REVIEW['asin']}_{NEUTRAL_REVIEW['reviewerID']}_{hash_suffix}"

        processed_item = wait_for_processing(expected_review_id)
        print(f"✅ Processing completed for review: {expected_review_id}")

        # Validate neutral sentiment detection
        assert processed_item['sentiment'] == 'neutral'
        assert processed_item['hasProfanity'] == False
        print("✅ Neutral sentiment correctly detected")

        print("🎉 Neutral review test completed successfully!")

    def test_customer_banning_logic(self):
        """Test automated customer banning after multiple violations"""
        print("\n🧪 Testing customer banning logic...")

        # Create multiple profanity reviews from the same user to trigger banning
        problem_user = "test_user_ban_me"

        for i in range(5):  # Create 5 profane reviews (threshold is 4)
            profanity_review = {
                **PROFANITY_REVIEW,
                "reviewerID": problem_user,
                "asin": f"B00{i}E94B8X",
                "unixReviewTime": 1393632000 + i,
                "reviewText": f"This damn product {i} is complete shit!"
            }

            key = upload_review_to_s3(profanity_review, f"ban_test_{i}")
            print(f"✅ Uploaded violation review {i + 1}")

            # Generate review ID for waiting
            import hashlib
            hash_input = f"{profanity_review['asin']}_{profanity_review['reviewerID']}_{profanity_review['unixReviewTime']}"
            hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
            review_id = f"{profanity_review['asin']}_{profanity_review['reviewerID']}_{hash_suffix}"

            # Wait for this review to be processed
            wait_for_processing(review_id)
            print(f"✅ Review {i + 1} processed")

        # Check if customer is banned after multiple violations
        time.sleep(5)  # Give extra time for customer record updates
        customer_response = customers_table.get_item(Key={'userId': problem_user})
        assert 'Item' in customer_response
        customer = customer_response['Item']

        print(f"Customer violation count: {customer.get('violationCount', 0)}")
        print(f"Customer banned status: {customer.get('isBanned', False)}")

        # Should be banned after exceeding threshold
        assert customer['violationCount'] >= 4
        assert customer['isBanned'] == True
        assert 'banDate' in customer
        print("✅ Customer correctly banned after multiple violations")

        print("🎉 Customer banning test completed successfully!")


# ==============================================================================
# MANUAL TEST FUNCTIONS
# ==============================================================================

def run_single_test():
    """Run a single comprehensive test"""
    print("🚀 Running single integration test...")

    # Upload a test review
    key = upload_review_to_s3(POSITIVE_REVIEW, "manual_test")
    print(f"✅ Uploaded test review to s3://{RAW_BUCKET}/{key}")

    # Generate expected review ID
    import hashlib
    hash_input = f"{POSITIVE_REVIEW['asin']}_{POSITIVE_REVIEW['reviewerID']}_{POSITIVE_REVIEW['unixReviewTime']}"
    hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    expected_review_id = f"{POSITIVE_REVIEW['asin']}_{POSITIVE_REVIEW['reviewerID']}_{hash_suffix}"
    print(f"Expected review ID: {expected_review_id}")

    # Wait and check processing
    print("⏳ Waiting for processing to complete...")
    try:
        processed_item = wait_for_processing(expected_review_id, max_wait=90)

        print("\n📊 PROCESSING RESULTS:")
        print("=" * 50)
        print(f"Review ID: {processed_item['reviewId']}")
        print(f"User ID: {processed_item['userId']}")
        print(f"Product ID: {processed_item['productId']}")
        print(f"Rating: {processed_item['overallRating']}")
        print(f"Word Count: {processed_item['wordCount']}")
        print(f"Processing Status: {processed_item['processingStatus']}")
        print(f"Has Profanity: {processed_item['hasProfanity']}")
        print(f"Profanity Check Status: {processed_item['profanityCheckStatus']}")
        print(f"Sentiment: {processed_item['sentiment']}")
        print(f"Sentiment Analysis Status: {processed_item['sentimentAnalysisStatus']}")

        # Check customer record
        customer_response = customers_table.get_item(Key={'userId': POSITIVE_REVIEW['reviewerID']})
        if 'Item' in customer_response:
            customer = customer_response['Item']
            print(f"\n👤 CUSTOMER RECORD:")
            print("=" * 30)
            print(f"Total Reviews: {customer.get('totalReviews', 0)}")
            print(f"Violation Count: {customer.get('violationCount', 0)}")
            print(f"Is Banned: {customer.get('isBanned', False)}")
            print(f"Created Date: {customer.get('createdDate', 'N/A')}")

        print("\n🎉 Integration test completed successfully!")

    except TimeoutError:
        print("❌ Test timed out - check Lambda functions and DynamoDB streams")

        # Debug information
        response = reviews_table.get_item(Key={'reviewId': expected_review_id})
        if 'Item' in response:
            item = response['Item']
            print("\n🔍 CURRENT STATE:")
            print(f"Processing Status: {item.get('processingStatus', 'N/A')}")
            print(f"Profanity Check Status: {item.get('profanityCheckStatus', 'N/A')}")
            print(f"Sentiment Analysis Status: {item.get('sentimentAnalysisStatus', 'N/A')}")
        else:
            print("❌ No record found in DynamoDB - preprocessing may have failed")


def check_lambda_functions():
    """Check if all Lambda functions are properly deployed"""
    print("🔍 Checking Lambda function status...")

    functions = ['preprocess', 'profanity', 'sentiment']
    for func_name in functions:
        try:
            response = lambda_client.get_function(FunctionName=func_name)
            print(f"✅ {func_name}: {response['Configuration']['State']}")
        except Exception as e:
            print(f"❌ {func_name}: {str(e)}")


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check_lambda_functions()
    elif len(sys.argv) > 1 and sys.argv[1] == "single":
        run_single_test()
    elif len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup_test_data()
    else:
        # Run pytest
        print("🧪 Running full integration test suite...")
        pytest.main([__file__, "-v", "-s"])