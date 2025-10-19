#!/bin/bash

# ==============================================================================
# INTEGRATION TEST RUNNER
# ==============================================================================
# This script provides easy commands to run different types of integration tests
# for the review processing pipeline
# ==============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test script location
TEST_SCRIPT="ex3/tests/integration_tests.py"

print_header() {
    echo -e "${BLUE}"
    echo "=============================================="
    echo "  Review Processing Pipeline Test Suite"
    echo "=============================================="
    echo -e "${NC}"
}

print_usage() {
    echo -e "${YELLOW}Usage:${NC}"
    echo "  $0 check      - Check if Lambda functions are deployed"
    echo "  $0 single     - Run a single comprehensive test"
    echo "  $0 full       - Run the complete test suite"
    echo "  $0 cleanup    - Clean up test data"
    echo "  $0 setup      - Install test dependencies"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 single     # Quick test of the full pipeline"
    echo "  $0 full       # Run all test scenarios"
}

check_dependencies() {
    echo -e "${BLUE}🔍 Checking dependencies...${NC}"

    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}❌ Python3 is not installed${NC}"
        exit 1
    fi

    if ! python3 -c "import boto3, pytest" &> /dev/null; then
        echo -e "${YELLOW}⚠️  Missing Python dependencies. Run: $0 setup${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Dependencies OK${NC}"
}

setup_dependencies() {
    echo -e "${BLUE}📦 Installing test dependencies...${NC}"
    pip3 install boto3 pytest
    echo -e "${GREEN}✅ Dependencies installed${NC}"
}

check_localstack() {
    echo -e "${BLUE}🔍 Checking LocalStack connection...${NC}"

    if ! curl -s http://localhost:4566/health > /dev/null; then
        echo -e "${RED}❌ LocalStack is not running or not accessible${NC}"
        echo "Please start LocalStack and ensure it's running on port 4566"
        exit 1
    fi

    echo -e "${GREEN}✅ LocalStack is running${NC}"
}

run_check() {
    print_header
    check_dependencies
    check_localstack

    echo -e "${BLUE}🔍 Checking Lambda function deployment status...${NC}"
    python3 "$TEST_SCRIPT" check
}

run_single_test() {
    print_header
    check_dependencies
    check_localstack

    echo -e "${BLUE}🧪 Running single integration test...${NC}"
    echo "This will upload a test review and verify the complete pipeline"
    echo ""

    python3 "$TEST_SCRIPT" single

    echo ""
    echo -e "${GREEN}🎉 Single test completed!${NC}"
}

run_full_tests() {
    print_header
    check_dependencies
    check_localstack

    echo -e "${BLUE}🧪 Running full integration test suite...${NC}"
    echo "This will run all test scenarios including:"
    echo "  • Positive review processing"
    echo "  • Negative review processing"
    echo "  • Profanity detection"
    echo "  • Neutral review processing"
    echo "  • Customer banning logic"
    echo ""

    python3 "$TEST_SCRIPT"

    echo ""
    echo -e "${GREEN}🎉 Full test suite completed!${NC}"
}

run_cleanup() {
    print_header
    check_dependencies
    check_localstack

    echo -e "${BLUE}🧹 Cleaning up test data...${NC}"
    python3 "$TEST_SCRIPT" cleanup
    echo -e "${GREEN}✅ Cleanup completed${NC}"
}

# Main command handling
case "${1:-}" in
    "check")
        run_check
        ;;
    "single")
        run_single_test
        ;;
    "full")
        run_full_tests
        ;;
    "cleanup")
        run_cleanup
        ;;
    "setup")
        setup_dependencies
        ;;
    "help"|"-h"|"--help")
        print_header
        print_usage
        ;;
    *)
        print_header
        echo -e "${RED}❌ Invalid command: ${1:-}${NC}"
        echo ""
        print_usage
        exit 1
        ;;
esac