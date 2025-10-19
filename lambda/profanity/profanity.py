# ==============================================================================
# LAMBDA FUNCTION 2: PROFANITY CHECKER & CUSTOMER TRACKER
# ==============================================================================
# Purpose: Monitors DynamoDB streams for new reviews, checks for profanity,
# tracks customer violation history, and implements automated banning logic
# Trigger: DynamoDB Streams from reviews table
# ==============================================================================

import os
import boto3
from datetime import datetime
import json
from profanityfilter import ProfanityFilter

# ==============================================================================
# AWS SERVICE CONFIGURATION FOR PROFANITY CHECKER
# ==============================================================================
# Updated LocalStack endpoint for local development
endpoint_url = "http://localhost.localstack.cloud:4566" if os.getenv("STAGE") == "local" else None
dynamodb = boto3.resource('dynamodb', endpoint_url=endpoint_url)
ssm = boto3.client('ssm', endpoint_url=endpoint_url)
s3 = boto3.client('s3', endpoint_url=endpoint_url)


def lambda_handler(event, context):
    """
    Processes DynamoDB stream events to check for profanity and track customer behavior

    Workflow:
    1. Initialize profanity filter and database connections
    2. Process each DynamoDB stream record (INSERT events only)
    3. Retrieve review text from S3 or DynamoDB
    4. Perform profanity detection on review text and summary
    5. Update review record with profanity check results
    6. Update or create customer violation tracking record
    7. Apply automated banning logic based on violation thresholds

    Args:
        event: DynamoDB Streams event containing record changes
        context: Lambda runtime context

    Returns:
        dict: Status response
    """
    # Initialize profanity detection library
    # ProfanityFilter provides robust detection of inappropriate content
    pf = ProfanityFilter()
    reviews_table = dynamodb.Table('reviews')

    # Process each stream record
    for record in event['Records']:
        # Only process new record insertions (ignore updates/deletes)
        if record['eventName'] != 'INSERT':
            continue

        # Extract review data from DynamoDB stream event
        new_image = record['dynamodb']['NewImage']
        review_id = new_image['reviewId']['S']
        user_id = new_image['userId']['S']

        try:
            # Retrieve review text content
            # Text may be stored directly in DynamoDB or referenced in S3
            if 'originalReviewText' not in new_image:
                # Text is stored in S3 - retrieve from referenced location
                bucket, key = new_image['originalTextLocation']['S'].replace('s3://', '').split('/', 1)
                obj = s3.get_object(Bucket=bucket, Key=key)
                review_data = json.loads(obj['Body'].read())
                review_text = review_data.get('reviewText', '')
                summary_text = review_data.get('summary', '')
            else:
                # Text is stored directly in DynamoDB record
                review_text = new_image['originalReviewText']['S']
                summary_text = new_image['originalSummary']['S']

            # Perform profanity detection on both review text and summary
            # Check both fields as inappropriate content could appear in either
            has_profanity = (pf.is_profane(review_text) or pf.is_profane(summary_text))
            print(f"Review {review_id} profanity check: {has_profanity}")

            # Update the review record with profanity check results
            reviews_table.update_item(
                Key={'reviewId': review_id},
                UpdateExpression="""
                    SET profanityCheckStatus = :status,
                        hasProfanity = :profanity,
                        lastUpdated = :now
                """,
                ExpressionAttributeValues={
                    ':status': 'completed',
                    ':profanity': has_profanity,
                    ':now': datetime.now().isoformat()
                }
            )

            # Update customer tracking record
            # This maintains a history of customer behavior for moderation purposes
            customers_table_name = ssm.get_parameter(Name='/app/config/customers_table')['Parameter']['Value']
            customers_table = dynamodb.Table(customers_table_name)
            current_time = datetime.now().isoformat()

            try:
                # Check if customer record already exists
                existing_customer = customers_table.get_item(Key={'userId': user_id})

                if 'Item' in existing_customer:
                    # Update existing customer record
                    item = existing_customer['Item']
                    current_total = item.get('totalReviews', 0)
                    current_violations = item.get('violationCount', 0)

                    # Build dynamic update expression based on existing fields
                    update_expression_parts = [
                        "lastUpdated = :now",
                        "lastReviewDate = :now",
                        "totalReviews = :total"
                    ]
                    expression_values = {
                        ':now': current_time,
                        ':total': current_total + 1
                    }

                    # Initialize default fields if missing
                    if 'isBanned' not in item:
                        update_expression_parts.append("isBanned = :false")
                        expression_values[':false'] = False

                    if 'createdDate' not in item:
                        update_expression_parts.append("createdDate = :now")

                    if 'firstReviewDate' not in item:
                        update_expression_parts.append("firstReviewDate = :now")

                    # Handle profanity violation tracking
                    if has_profanity:
                        expression_values[':violations'] = current_violations + 1
                        update_expression_parts.append("violationCount = :violations")
                        update_expression_parts.append("lastViolationDate = :now")

                        # Track first violation date for analytics
                        if 'firstViolationDate' not in item:
                            update_expression_parts.append("firstViolationDate = :now")
                    else:
                        # Initialize violation count if missing
                        if 'violationCount' not in item:
                            update_expression_parts.append("violationCount = :zero")
                            expression_values[':zero'] = 0

                    # Execute the update
                    update_expr = "SET " + ", ".join(update_expression_parts)

                    response = customers_table.update_item(
                        Key={'userId': user_id},
                        UpdateExpression=update_expr,
                        ExpressionAttributeValues=expression_values,
                        ReturnValues='ALL_NEW'
                    )

                    updated_customer = response['Attributes']
                    print(
                        f"Updated customer {user_id}: total reviews = {updated_customer.get('totalReviews')}, violations = {updated_customer.get('violationCount')}")

                else:
                    # Create new customer record
                    new_customer_item = {
                        'userId': user_id,
                        'totalReviews': 1,
                        'violationCount': 1 if has_profanity else 0,
                        'isBanned': False,
                        'createdDate': current_time,
                        'lastUpdated': current_time,
                        'lastReviewDate': current_time,
                        'firstReviewDate': current_time
                    }

                    # Set violation dates if this first review contains profanity
                    if has_profanity:
                        new_customer_item['firstViolationDate'] = current_time
                        new_customer_item['lastViolationDate'] = current_time

                    customers_table.put_item(Item=new_customer_item)
                    updated_customer = new_customer_item
                    print(
                        f"Created new customer {user_id}: total reviews = 1, violations = {new_customer_item['violationCount']}")

            except Exception as e:
                print(f"Error processing customer {user_id}: {str(e)}")
                continue

            # Automated banning logic based on violation threshold
            if isinstance(updated_customer, dict):
                violation_count = updated_customer.get('violationCount', 0)
                if violation_count > 0 and not updated_customer.get('isBanned', False):
                    # Get configurable profanity threshold from Parameter Store
                    threshold = int(ssm.get_parameter(Name='/app/config/profanity_threshold')['Parameter']['Value'])

                    # Ban customer if they exceed the violation threshold
                    if violation_count >= threshold:
                        customers_table.update_item(
                            Key={'userId': user_id},
                            UpdateExpression="SET isBanned = :true, banDate = :now",
                            ExpressionAttributeValues={
                                ':true': True,
                                ':now': current_time
                            }
                        )
                        print(f"Customer {user_id} has been banned (violations: {violation_count})")

        except Exception as e:
            print(f"Error processing record {review_id}: {str(e)}")
            continue

    return {'statusCode': 200}


# Local testing entry point
if __name__ == "__main__":
    # For debugging, mock event structure would be needed here
    print(lambda_handler({"Records": []}, None))
