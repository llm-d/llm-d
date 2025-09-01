#!/bin/bash

set -e  # Exit on any error

# Script to build Intel XPU Docker image for vLLM
# Usage: ./build-xpu.sh [IMAGE_NAME] [VLLM_VERSION]
# Example: ./build-xpu.sh ghcr.io/llm-d/llm-d-xpu:v0.2.3 v0.10.0

# Default configuration
DEFAULT_PROJECT_NAME="llm-d"
DEFAULT_VLLM_VERSION="v0.10.0"
DEFAULT_IMAGE_TAG_BASE="ghcr.io/llm-d/llm-d"
DEFAULT_DEV_VERSION="v0.2.3"
DEFAULT_XPU_IMG="${DEFAULT_IMAGE_TAG_BASE}-xpu:${DEFAULT_DEV_VERSION}"

# Parse command line arguments
XPU_IMG="${1:-$DEFAULT_XPU_IMG}"
VLLM_VERSION="${2:-$DEFAULT_VLLM_VERSION}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_usage() {
    echo -e "${BLUE}Usage: $0 [IMAGE_NAME] [VLLM_VERSION]${NC}"
    echo -e "${YELLOW}  IMAGE_NAME    : Target Docker image name (default: $DEFAULT_XPU_IMG)${NC}"
    echo -e "${YELLOW}  VLLM_VERSION  : vLLM version to build (default: $DEFAULT_VLLM_VERSION)${NC}"
    echo -e "${BLUE}Examples:${NC}"
    echo -e "${CYAN}  $0${NC}"
    echo -e "${CYAN}  $0 my-registry/vllm-xpu:latest${NC}"
    echo -e "${CYAN}  $0 ghcr.io/llm-d/llm-d-xpu:v0.3.0 v0.11.0${NC}"
}

# Show help if requested
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    print_usage
    exit 0
fi

echo -e "${CYAN}==== Intel XPU Docker Build Script ====${NC}"
echo -e "${GREEN}Target image: ${XPU_IMG}${NC}"
echo -e "${GREEN}vLLM version: ${VLLM_VERSION}${NC}"

# Check if docker is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed or not in PATH${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker found: $(docker --version)${NC}"

# Clone vLLM repository if not exists
if [ ! -d "vllm-source" ]; then
    echo -e "${YELLOW}==== Cloning vLLM repository ====${NC}"
    git clone https://github.com/vllm-project/vllm.git vllm-source
    echo -e "${GREEN}==== vLLM repository cloned successfully ====${NC}"
else
    echo -e "${YELLOW}==== vLLM repository already exists ====${NC}"
fi

# Checkout to specified version
echo -e "${YELLOW}==== Checking out vLLM version ${VLLM_VERSION} ====${NC}"
cd vllm-source
git fetch --all
git checkout ${VLLM_VERSION}
echo -e "${GREEN}==== Successfully checked out ${VLLM_VERSION} ====${NC}"

# Verify Dockerfile exists
if [ ! -f "docker/Dockerfile.xpu" ]; then
    echo -e "${RED}❌ XPU Dockerfile not found at docker/Dockerfile.xpu${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Found XPU Dockerfile at docker/Dockerfile.xpu${NC}"

# Build Intel XPU Docker image
echo -e "${YELLOW}==== Building Intel XPU Docker image ====${NC}"
echo -e "${BLUE}Command: docker build --progress=plain -f docker/Dockerfile.xpu -t ${XPU_IMG} --build-arg VLLM_VERSION=${VLLM_VERSION} .${NC}"

docker build --progress=plain \
    -f docker/Dockerfile.xpu \
    -t ${XPU_IMG} \
    --build-arg VLLM_VERSION=${VLLM_VERSION} \
    .

echo -e "${GREEN}==== Intel XPU Docker image build completed: ${XPU_IMG} ====${NC}"

# Verify image was created
IMAGE_REPO=$(echo ${XPU_IMG} | cut -d':' -f1)
if docker images | grep -q "${IMAGE_REPO}"; then
    echo -e "${GREEN}✅ Image successfully created${NC}"
    docker images | grep "${IMAGE_REPO}"
else
    echo -e "${RED}❌ Image creation failed${NC}"
    exit 1
fi

echo -e "${CYAN}==== Build process completed successfully! ====${NC}"
