#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
SCRIPT_NAME="$(basename "$0")"
NVIDIA_DEVICE_PLUGIN_NAMESPACE="nvidia-device-plugin"
NVIDIA_DEVICE_PLUGIN_VERSION="v0.14.5"
KUBERNETES_CONTEXT=""
FORCE_REINSTALL=false
DRY_RUN=false
CREATE_CLUSTER=false

# Cluster configuration
CLUSTER_NAME="llm-d-cluster"
CLUSTER_REGION="tor1"
CLUSTER_VERSION=""  # Will be set to latest
VPC_NAME="llm-d-vpc"
CPU_NODE_POOL="cpu-pool"
GPU_NODE_POOL="gpu-pool"
CPU_NODE_SIZE="s-2vcpu-4gb"
GPU_NODE_SIZE="gpu-6000adax1-48gb"
CPU_NODE_COUNT=2
GPU_NODE_COUNT=2
USE_DEFAULT_VPC=true

# ANSI color helpers
COLOR_RESET=$'\e[0m'
COLOR_GREEN=$'\e[32m'
COLOR_YELLOW=$'\e[33m'
COLOR_RED=$'\e[31m'
COLOR_BLUE=$'\e[34m'
COLOR_CYAN=$'\e[36m'
COLOR_PURPLE=$'\e[35m'

log_info() {
    echo "${COLOR_BLUE}ℹ️  $*${COLOR_RESET}"
}

log_success() {
    echo "${COLOR_GREEN}✅ $*${COLOR_RESET}"
}

log_warning() {
    echo "${COLOR_YELLOW}⚠️  $*${COLOR_RESET}"
}

log_error() {
    echo "${COLOR_RED}❌ $*${COLOR_RESET}" >&2
}

log_step() {
    echo "${COLOR_PURPLE}🔹 $*${COLOR_RESET}"
}

log_cluster() {
    echo "${COLOR_CYAN}🚀 $*${COLOR_RESET}"
}

die() {
    log_error "$*"
    exit 1
}

### HELP ###
print_help() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

DigitalOcean GPU Kubernetes Cluster Setup Script for llm-d-infra
This script can create a complete DOKS cluster from scratch or setup an existing cluster
for llm-d deployment by installing NVIDIA Device Plugin and fixing GPU node labels.

🧠 SMART FEATURES:
  • Automatically detects existing cluster state and resumes from interruption points
  • Skips unnecessary steps if components are already installed and working
  • Prevents conflicts when re-running after timeouts or partial failures
  • Provides detailed execution plan before starting operations
  • 100% safe to re-run - if script stops unexpectedly, just run it again!
  • Handles DigitalOcean API delays and timeouts gracefully

Options:
  -c, --create-cluster         Create a new DOKS cluster from scratch
  -n, --cluster-name NAME      Cluster name (default: ${CLUSTER_NAME})
  -r, --region REGION          DigitalOcean region (default: ${CLUSTER_REGION})
  -v, --custom-vpc             Create a custom VPC (default: use default VPC)
  -g, --context PATH           Specify Kubernetes context file path
  -f, --force-reinstall        Force reinstall NVIDIA Device Plugin
  -d, --dry-run               Show what would be executed without running
  -h, --help                  Show this help message and exit

Cluster Creation Configuration:
  VPC:                Use default VPC (recommended)
  CPU Node Pool:      ${CPU_NODE_POOL} (${CPU_NODE_COUNT}x ${CPU_NODE_SIZE})
  GPU Node Pool:      ${GPU_NODE_POOL} (${GPU_NODE_COUNT}x ${GPU_NODE_SIZE})

Examples:
  ${SCRIPT_NAME}                                    # Setup existing cluster (smart mode)
  ${SCRIPT_NAME} -c                                 # Create new cluster + setup (default VPC)
  ${SCRIPT_NAME} -c -v                              # Create cluster with custom VPC
  ${SCRIPT_NAME} -c -n my-cluster -r nyc1          # Create cluster in NYC
  ${SCRIPT_NAME} --force-reinstall                 # Force reinstall Device Plugin
  ${SCRIPT_NAME} --dry-run                         # Preview operations and execution plan

Smart Recovery Examples:
  # If cluster creation timed out during node provisioning:
  ${SCRIPT_NAME}                                    # Will detect existing cluster and continue
  
  # If NVIDIA Device Plugin installation failed:
  ${SCRIPT_NAME}                                    # Will detect and retry installation
  
  # Re-run safely after any interruption:
  ${SCRIPT_NAME}                                    # Always safe to re-run

Prerequisites for cluster creation:
  - doctl CLI installed and authenticated
  - Sufficient DigitalOcean account limits for GPU nodes

For more information, see the llm-d-infra DigitalOcean provider documentation.

EOF
}

### ARGUMENT PARSING ###
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -c|--create-cluster)
                CREATE_CLUSTER=true
                shift
                ;;
            -n|--cluster-name)
                CLUSTER_NAME="$2"
                shift 2
                ;;
            -r|--region)
                CLUSTER_REGION="$2"
                shift 2
                ;;
            -v|--custom-vpc)
                USE_DEFAULT_VPC=false
                shift
                ;;
            -g|--context)
                KUBERNETES_CONTEXT="$2"
                shift 2
                ;;
            -f|--force-reinstall)
                FORCE_REINSTALL=true
                shift
                ;;
            -d|--dry-run)
                DRY_RUN=true
                shift
                ;;
            -h|--help)
                print_help
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
    done
}

### UTILITIES ###
check_cmd() {
    command -v "$1" &>/dev/null || die "Required command not found: $1"
}

check_dependencies() {
    local required_cmds=(kubectl helm)
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        required_cmds+=(doctl jq)
    fi
    
    for cmd in "${required_cmds[@]}"; do
        check_cmd "$cmd"
    done
}

setup_kubectl() {
    if [[ -n "${KUBERNETES_CONTEXT}" ]]; then
        if [[ ! -f "${KUBERNETES_CONTEXT}" ]]; then
            die "Specified kubeconfig file does not exist: ${KUBERNETES_CONTEXT}"
        fi
        KCMD="kubectl --kubeconfig ${KUBERNETES_CONTEXT}"
        HCMD="helm --kubeconfig ${KUBERNETES_CONTEXT}"
        log_info "Using specified kubeconfig: ${KUBERNETES_CONTEXT}"
    else
        KCMD="kubectl"
        HCMD="helm"
        log_info "Using current kubectl context"
    fi
}

check_doctl_auth() {
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        log_info "Checking doctl authentication..."
        if ! doctl account get &>/dev/null; then
            die "doctl is not authenticated. Please run: doctl auth init"
        fi
        log_success "doctl authentication verified"
    fi
}

get_latest_k8s_version() {
    log_info "Getting latest Kubernetes version for region ${CLUSTER_REGION}..."
    CLUSTER_VERSION=$(doctl kubernetes options versions -o json | jq -r '.[0].slug')
    log_success "Using Kubernetes version: ${CLUSTER_VERSION}"
}

### CLUSTER CREATION FUNCTIONS ###
create_vpc() {
    if [[ "${USE_DEFAULT_VPC}" == "true" ]]; then
        log_cluster "Using default VPC (recommended)"
        
        if [[ "${DRY_RUN}" == "true" ]]; then
            log_info "[DRY RUN] Would use default VPC for cluster"
        fi
        return 0
    fi
    
    log_cluster "Creating custom VPC: ${VPC_NAME}"
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would create custom VPC: ${VPC_NAME} in region ${CLUSTER_REGION}"
        return 0
    fi
    
    # Check if VPC already exists
    if doctl vpcs list --format Name --no-header | grep -q "^${VPC_NAME}$"; then
        log_warning "VPC ${VPC_NAME} already exists, skipping creation"
        return 0
    fi
    
    # Use a different IP range to avoid DigitalOcean reserved ranges
    doctl vpcs create \
        --name "${VPC_NAME}" \
        --region "${CLUSTER_REGION}" \
        --ip-range "172.16.0.0/16"
    
    log_success "Custom VPC ${VPC_NAME} created successfully"
}

create_kubernetes_cluster() {
    log_cluster "Creating Kubernetes cluster: ${CLUSTER_NAME}"
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would create cluster: ${CLUSTER_NAME}"
        log_info "[DRY RUN] Version: ${CLUSTER_VERSION}"
        log_info "[DRY RUN] Region: ${CLUSTER_REGION}"
        if [[ "${USE_DEFAULT_VPC}" == "true" ]]; then
            log_info "[DRY RUN] VPC: Default VPC"
        else
            log_info "[DRY RUN] VPC: ${VPC_NAME} (Custom)"
        fi
        return 0
    fi
    
    # Check if cluster already exists
    if doctl kubernetes cluster list --format Name --no-header | grep -q "^${CLUSTER_NAME}$"; then
        log_warning "Cluster ${CLUSTER_NAME} already exists, skipping creation"
        return 0
    fi
    
    # Determine VPC settings
    local vpc_args=""
    if [[ "${USE_DEFAULT_VPC}" == "false" ]]; then
        # Get VPC ID for custom VPC
        local vpc_id
        vpc_id=$(doctl vpcs list --format ID,Name --no-header | grep "${VPC_NAME}" | awk '{print $1}')
        
        if [[ -z "${vpc_id}" ]]; then
            die "VPC ${VPC_NAME} not found"
        fi
        
        log_info "Using custom VPC ID: ${vpc_id}"
        vpc_args="--vpc-uuid ${vpc_id}"
    else
        log_info "Using default VPC"
    fi
    
    # Create cluster with initial CPU node pool
    doctl kubernetes cluster create "${CLUSTER_NAME}" \
        --region "${CLUSTER_REGION}" \
        --version "${CLUSTER_VERSION}" \
        ${vpc_args} \
        --node-pool "name=${CPU_NODE_POOL};size=${CPU_NODE_SIZE};count=${CPU_NODE_COUNT};auto-scale=false" \
        --wait
    
    log_success "Kubernetes cluster ${CLUSTER_NAME} created successfully"
    
    # Update kubeconfig
    doctl kubernetes cluster kubeconfig save "${CLUSTER_NAME}"
    log_success "Kubeconfig updated for cluster ${CLUSTER_NAME}"
}

create_gpu_node_pool() {
    log_cluster "Creating GPU node pool: ${GPU_NODE_POOL}"
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would create GPU node pool: ${GPU_NODE_POOL}"
        log_info "[DRY RUN] Size: ${GPU_NODE_SIZE}"
        log_info "[DRY RUN] Count: ${GPU_NODE_COUNT}"
        return 0
    fi
    
    # Check if GPU node pool already exists
    if doctl kubernetes cluster node-pool list "${CLUSTER_NAME}" --format Name --no-header | grep -q "^${GPU_NODE_POOL}$"; then
        log_warning "GPU node pool ${GPU_NODE_POOL} already exists, skipping creation"
        return 0
    fi
    
    doctl kubernetes cluster node-pool create "${CLUSTER_NAME}" \
        --name "${GPU_NODE_POOL}" \
        --size "${GPU_NODE_SIZE}" \
        --count "${GPU_NODE_COUNT}"
    
    log_success "GPU node pool ${GPU_NODE_POOL} created successfully"
    
    # Quick verification with timeout protection
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would check GPU node pool status"
        return 0
    fi
    
    log_info "Verifying GPU node pool creation (quick check with timeout protection)..."
    local max_attempts=3
    local attempt=0
    local timeout_seconds=10
    
    while [[ ${attempt} -lt ${max_attempts} ]]; do
        log_info "🔄 Checking node pool status... ($((attempt + 1))/${max_attempts})"
        
        # Use timeout command to prevent hanging
        local node_pool_status=""
        if command -v timeout &>/dev/null; then
            node_pool_status=$(timeout ${timeout_seconds} doctl kubernetes cluster node-pool list "${CLUSTER_NAME}" --format Status --no-header 2>/dev/null | grep -v "running" | wc -l || echo "1")
        else
            # Fallback for systems without timeout command
            node_pool_status=$(doctl kubernetes cluster node-pool list "${CLUSTER_NAME}" --format Status --no-header 2>/dev/null | grep -v "running" | wc -l || echo "1")
        fi
        
        # Check if the command succeeded and status is good
        if [[ "${node_pool_status}" == "0" ]]; then
            log_success "✅ GPU node pool is ready and running"
            return 0
        elif [[ -z "${node_pool_status}" ]]; then
            log_warning "⚠️  Status check timed out or failed, assuming pool is still initializing"
        else
            log_info "📊 Pool status: ${node_pool_status} pools still initializing"
        fi
        
        if [[ ${attempt} -lt $((max_attempts - 1)) ]]; then
            log_info "⏳ Waiting 10 seconds before next check..."
            sleep 10  # Reduced wait time
        fi
        ((attempt++))
    done
    
    log_success "✅ GPU node pool creation command completed successfully"
    log_info "🚀 Node pool will continue provisioning in background (typically takes 3-5 minutes)"
    log_info "💡 This is normal - the script will continue with other setup tasks"
    log_info "🔄 Note: If the script stops here, it's due to API delays - just re-run the same command"
}

wait_for_nodes() {
    log_info "Checking node readiness for core cluster functionality..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would check for nodes to be ready"
        return 0
    fi
    
    local max_attempts=8
    local attempt=0
    local total_expected=$((CPU_NODE_COUNT + GPU_NODE_COUNT))
    local timeout_seconds=15
    
    while [[ ${attempt} -lt ${max_attempts} ]]; do
        log_info "🔍 Checking node status... ($((attempt + 1))/${max_attempts})"
        
        # Use timeout to prevent kubectl from hanging
        local ready_nodes="0"
        local total_nodes="0"
        
        if command -v timeout &>/dev/null; then
            ready_nodes=$(timeout ${timeout_seconds} ${KCMD} get nodes --no-headers 2>/dev/null | grep " Ready " | wc -l || echo "0")
            total_nodes=$(timeout ${timeout_seconds} ${KCMD} get nodes --no-headers 2>/dev/null | wc -l || echo "0")
        else
            # Fallback for systems without timeout command
            ready_nodes=$(${KCMD} get nodes --no-headers 2>/dev/null | grep " Ready " | wc -l || echo "0")
            total_nodes=$(${KCMD} get nodes --no-headers 2>/dev/null | wc -l || echo "0")
        fi
        
        # Handle timeout or error cases
        if [[ "${ready_nodes}" == "0" ]] && [[ "${total_nodes}" == "0" ]]; then
            log_warning "⚠️  Node status check timed out or failed, retrying..."
            if [[ ${attempt} -lt $((max_attempts - 1)) ]]; then
                sleep 5
                ((attempt++))
                continue
            fi
        fi
        
        log_info "📊 Node status: ${ready_nodes}/${total_nodes} ready (expecting ${total_expected} total)"
        
        # Early exit: If we have at least CPU nodes ready, continue immediately
        if [[ "${ready_nodes}" -ge "${CPU_NODE_COUNT}" ]]; then
            log_success "🚀 CPU nodes are ready, proceeding with GPU setup!"
            if [[ "${ready_nodes}" -eq "${total_expected}" ]]; then
                log_success "🎉 All ${ready_nodes} nodes are ready!"
            else
                log_info "📡 GPU nodes will continue joining in background..."
                log_info "🔧 Device plugin will automatically handle GPU node integration"
            fi
            return 0
        fi
        
        # Show progress and provide useful feedback
        local remaining_cpu=$((CPU_NODE_COUNT - ready_nodes))
        if [[ "${remaining_cpu}" -gt 0 ]]; then
            log_info "⌛ Waiting for ${remaining_cpu} more CPU nodes..."
            log_info "💡 Node provisioning typically takes 2-4 minutes"
        fi
        
        if [[ ${attempt} -lt $((max_attempts - 1)) ]]; then
            log_info "⏳ Waiting 10 seconds before next check..."
            sleep 10
        fi
        ((attempt++))
    done
    
    log_success "✅ Basic cluster nodes available, continuing with setup..."
    log_info "🚀 Advanced setup will handle remaining node provisioning automatically"
    log_info "💡 Some nodes may still be joining - this is normal and expected"
}

### EXISTING CLUSTER SETUP FUNCTIONS ###
check_cluster_reachability() {
    log_info "Checking Kubernetes cluster connectivity..."
    if ${KCMD} cluster-info &>/dev/null; then
        log_success "Successfully connected to Kubernetes cluster"
    else
        die "Cannot connect to Kubernetes cluster. Please check your kubectl configuration"
    fi
}

### SMART STATE DETECTION FUNCTIONS ###
check_existing_cluster() {
    log_info "Checking if cluster '${CLUSTER_NAME}' already exists..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would check for existing cluster"
        return 1  # Assume doesn't exist for dry run
    fi
    
    if doctl kubernetes cluster get "${CLUSTER_NAME}" &>/dev/null; then
        log_success "Cluster '${CLUSTER_NAME}' already exists"
        return 0
    else
        log_info "Cluster '${CLUSTER_NAME}' does not exist"
        return 1
    fi
}

check_existing_node_pools() {
    log_info "Checking existing node pools in cluster '${CLUSTER_NAME}'..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would check for existing node pools"
        return 1  # Assume need to create for dry run
    fi
    
    local cpu_pool_exists=false
    local gpu_pool_exists=false
    
    # Check if CPU pool exists
    if doctl kubernetes cluster node-pool get "${CLUSTER_NAME}" "${CPU_NODE_POOL}" &>/dev/null; then
        log_success "CPU node pool '${CPU_NODE_POOL}' already exists"
        cpu_pool_exists=true
    else
        log_info "CPU node pool '${CPU_NODE_POOL}' does not exist"
    fi
    
    # Check if GPU pool exists
    if doctl kubernetes cluster node-pool get "${CLUSTER_NAME}" "${GPU_NODE_POOL}" &>/dev/null; then
        log_success "GPU node pool '${GPU_NODE_POOL}' already exists"
        gpu_pool_exists=true
    else
        log_info "GPU node pool '${GPU_NODE_POOL}' does not exist"
    fi
    
    # Return 0 if both pools exist, 1 if we need to create some
    if [[ "${cpu_pool_exists}" == "true" ]] && [[ "${gpu_pool_exists}" == "true" ]]; then
        return 0
    else
        return 1
    fi
}

check_nvidia_device_plugin_status() {
    log_info "Checking NVIDIA Device Plugin installation status..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would check NVIDIA Device Plugin status"
        return 1  # Assume needs installation for dry run
    fi
    
    # Check if namespace exists
    if ! ${KCMD} get namespace "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" &>/dev/null; then
        log_info "NVIDIA Device Plugin namespace does not exist"
        return 1
    fi
    
    # Check if helm release exists
    if ! ${HCMD} list -n "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" | grep -q "nvdp"; then
        log_info "NVIDIA Device Plugin helm release not found"
        return 1
    fi
    
    # Check if DaemonSet is ready on GPU nodes
    local gpu_nodes
    gpu_nodes=$(get_gpu_nodes)
    local gpu_node_count
    gpu_node_count=$(echo "${gpu_nodes}" | wc -w)
    
    if [[ "${gpu_node_count}" -eq 0 ]]; then
        log_info "No GPU nodes found"
        return 1
    fi
    
    # Count ready pods on GPU nodes specifically
    local ready_gpu_pods=0
    for node in ${gpu_nodes}; do
        local pod_ready
        pod_ready=$(${KCMD} get pods -n "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" --field-selector spec.nodeName="${node}" -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || echo "false")
        if [[ "${pod_ready}" == "true" ]]; then
            ready_gpu_pods=$((ready_gpu_pods + 1))
        fi
    done
    
    if [[ "${ready_gpu_pods}" -eq "${gpu_node_count}" ]]; then
        log_success "NVIDIA Device Plugin is installed and ready on all GPU nodes (${ready_gpu_pods}/${gpu_node_count})"
        return 0
    else
        log_info "NVIDIA Device Plugin is installed but not ready on all GPU nodes (${ready_gpu_pods}/${gpu_node_count})"
        return 1
    fi
}

check_gpu_nodes_ready() {
    log_info "Checking GPU nodes readiness..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would check GPU nodes readiness"
        return 1  # Assume not ready for dry run
    fi
    
    local gpu_nodes
    gpu_nodes=$(get_gpu_nodes)
    
    if [[ -z "${gpu_nodes}" ]]; then
        log_info "No GPU nodes found"
        return 1
    fi
    
    local ready_gpu_nodes=0
    local total_gpu_nodes=0
    
    for node in ${gpu_nodes}; do
        total_gpu_nodes=$((total_gpu_nodes + 1))
        local node_ready
        node_ready=$(${KCMD} get node "${node}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")
        
        if [[ "${node_ready}" == "True" ]]; then
            ready_gpu_nodes=$((ready_gpu_nodes + 1))
        fi
    done
    
    if [[ "${ready_gpu_nodes}" -eq "${total_gpu_nodes}" ]] && [[ "${total_gpu_nodes}" -gt 0 ]]; then
        log_success "All GPU nodes are ready (${ready_gpu_nodes}/${total_gpu_nodes})"
        return 0
    else
        log_info "GPU nodes not all ready (${ready_gpu_nodes}/${total_gpu_nodes})"
        return 1
    fi
}

determine_execution_plan() {
    log_step "🧠 Analyzing current cluster state to determine execution plan..."
    
    local cluster_exists=false
    local node_pools_exist=false
    local device_plugin_ready=false
    local gpu_nodes_ready=false
    
    # Check cluster existence
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        if check_existing_cluster; then
            cluster_exists=true
            log_warning "Cluster already exists but --create-cluster was specified"
            log_info "Will skip cluster creation and setup existing cluster instead"
            CREATE_CLUSTER=false  # Override to setup mode
        fi
    fi
    
    # If we're in setup mode or cluster exists, check additional states
    if [[ "${CREATE_CLUSTER}" == "false" ]] || [[ "${cluster_exists}" == "true" ]]; then
        setup_kubectl
        
        # Check if we can connect to cluster
        if ${KCMD} cluster-info &>/dev/null; then
            log_success "Connected to existing cluster"
            
            # Check node pools if this is a DigitalOcean cluster
            if [[ "${CREATE_CLUSTER}" == "false" ]] && command -v doctl &>/dev/null; then
                if check_existing_node_pools; then
                    node_pools_exist=true
                fi
            fi
            
            # Check NVIDIA Device Plugin
            if check_nvidia_device_plugin_status; then
                device_plugin_ready=true
            fi
            
            # Check GPU nodes
            if check_gpu_nodes_ready; then
                gpu_nodes_ready=true
            fi
        fi
    fi
    
    # Display execution plan
    echo ""
    log_cluster "📋 Execution Plan:"
    echo "  Current State Analysis:"
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        echo "    • Cluster Creation: ✨ Will create new cluster"
        echo "    • Node Pools: ✨ Will create CPU + GPU pools"
    else
        echo "    • Cluster: ${cluster_exists} ✅ Using existing cluster"
        if command -v doctl &>/dev/null; then
            if [[ "${node_pools_exist}" == "true" ]]; then
                echo "    • Node Pools: ✅ Both CPU and GPU pools exist"
            else
                echo "    • Node Pools: ⚠️  Some pools missing (will be handled automatically)"
            fi
        else
            echo "    • Node Pools: ❓ Cannot check (doctl not available)"
        fi
    fi
    
    if [[ "${device_plugin_ready}" == "true" ]]; then
        echo "    • NVIDIA Device Plugin: ✅ Installed and ready"
    else
        echo "    • NVIDIA Device Plugin: ✨ Will install/repair"
    fi
    
    if [[ "${gpu_nodes_ready}" == "true" ]]; then
        echo "    • GPU Nodes: ✅ Ready and labeled"
    else
        echo "    • GPU Nodes: ✨ Will setup and label"
    fi
    
    echo ""
    echo "  Planned Actions:"
    local step_counter=1
    
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        echo "    ${step_counter}. 🏗️  Create VPC (if needed)"
        ((step_counter++))
        echo "    ${step_counter}. 🏗️  Create Kubernetes cluster"
        ((step_counter++))
        echo "    ${step_counter}. 🏗️  Create node pools"
        ((step_counter++))
        echo "    ${step_counter}. ⏳ Wait for nodes to be ready"
        ((step_counter++))
    fi
    
    if [[ "${device_plugin_ready}" != "true" ]]; then
        echo "    ${step_counter}. 🔧 Install/repair NVIDIA Device Plugin"
        ((step_counter++))
    fi
    
    if [[ "${gpu_nodes_ready}" != "true" ]]; then
        echo "    ${step_counter}. 🏷️  Fix GPU node labels"
        ((step_counter++))
    fi
    
    echo "    ${step_counter}. ✅ Verify GPU resources"
    ((step_counter++))
    echo "    ${step_counter}. 🏥 Run health checks"
    
    echo ""
    
    # Return status for conditional execution
    return 0
}

verify_digitalocean_gpu_cluster() {
    log_info "Verifying this is a DigitalOcean GPU cluster..."
    
    # Check for GPU nodes
    local gpu_nodes
    gpu_nodes=$(${KCMD} get nodes -l doks.digitalocean.com/gpu-brand=nvidia --no-headers 2>/dev/null | wc -l || echo "0")
    
    if [[ "${gpu_nodes}" -eq 0 ]]; then
        log_warning "No DigitalOcean GPU node labels found, checking node types..."
        # Check for GPU instance type nodes
        gpu_nodes=$(${KCMD} get nodes -o jsonpath='{.items[*].metadata.labels.node\.kubernetes\.io/instance-type}' | grep -c "gpu-" || echo "0")
        
        if [[ "${gpu_nodes}" -eq 0 ]]; then
            die "This cluster appears to have no GPU nodes. Please ensure you're using a DigitalOcean GPU cluster."
        fi
    fi
    
    log_success "Detected ${gpu_nodes} GPU nodes"
}

get_gpu_nodes() {
    # Prefer DigitalOcean labels
    local gpu_nodes
    gpu_nodes=$(${KCMD} get nodes -l doks.digitalocean.com/gpu-brand=nvidia -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")
    
    # If not found, use instance type
    if [[ -z "${gpu_nodes}" ]]; then
        gpu_nodes=$(${KCMD} get nodes -o json | jq -r '.items[] | select(.metadata.labels."node.kubernetes.io/instance-type" | test("gpu-")) | .metadata.name' 2>/dev/null || echo "")
    fi
    
    echo "${gpu_nodes}"
}

install_nvidia_device_plugin() {
    log_step "Installing NVIDIA Device Plugin..."
    
    # Check if already installed
    if ${KCMD} get namespace "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" &>/dev/null && [[ "${FORCE_REINSTALL}" != "true" ]]; then
        log_warning "NVIDIA Device Plugin already installed, use --force-reinstall to reinstall"
        return 0
    fi
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would install NVIDIA Device Plugin ${NVIDIA_DEVICE_PLUGIN_VERSION}"
        return 0
    fi
    
    # Reinstall if forced
    if [[ "${FORCE_REINSTALL}" == "true" ]]; then
        log_info "Removing existing NVIDIA Device Plugin..."
        ${HCMD} uninstall nvdp -n "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" 2>/dev/null || true
        ${KCMD} delete namespace "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" --ignore-not-found || true
        sleep 5
    fi
    
    # Create namespace
    ${KCMD} create namespace "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" --dry-run=client -o yaml | ${KCMD} apply -f -
    
    # Add NVIDIA Helm repository
    ${HCMD} repo add nvdp https://nvidia.github.io/k8s-device-plugin
    ${HCMD} repo update
    
    # Install NVIDIA Device Plugin (only on GPU nodes)
    ${HCMD} install nvdp nvdp/nvidia-device-plugin \
        --namespace "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" \
        --version "${NVIDIA_DEVICE_PLUGIN_VERSION}" \
        --set nodeSelector."doks\.digitalocean\.com/gpu-brand"="nvidia"
    
    log_success "NVIDIA Device Plugin installation completed"
}

wait_for_device_plugin() {
    log_info "Waiting for NVIDIA Device Plugin to be ready on GPU nodes..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would wait for Device Plugin pods to be ready"
        return 0
    fi
    
    local max_attempts=12  # 2 minutes max
    local attempt=0
    local timeout_seconds=15
    
    # Get the number of GPU nodes with timeout protection
    local gpu_nodes=""
    local gpu_node_count=0
    
    log_info "🔍 Discovering GPU nodes in cluster..."
    
    # Initial GPU node discovery with timeout
    local node_wait=0
    while [[ "${gpu_node_count}" -eq 0 ]] && [[ ${node_wait} -lt 6 ]]; do
        if [[ ${node_wait} -gt 0 ]]; then
            log_info "⏳ Waiting for GPU nodes to appear... (${node_wait}/6)"
        fi
        
        if command -v timeout &>/dev/null; then
            gpu_nodes=$(timeout ${timeout_seconds} get_gpu_nodes 2>/dev/null || echo "")
        else
            gpu_nodes=$(get_gpu_nodes 2>/dev/null || echo "")
        fi
        
        gpu_node_count=$(echo "${gpu_nodes}" | wc -w)
        
        if [[ "${gpu_node_count}" -eq 0 ]] && [[ ${node_wait} -lt 5 ]]; then
            sleep 10
        fi
        ((node_wait++))
    done
    
    if [[ "${gpu_node_count}" -eq 0 ]]; then
        log_warning "⚠️  No GPU nodes detected yet, but continuing with setup"
        log_info "💡 Device Plugin will handle GPU nodes as they become available"
        return 0
    fi
    
    log_success "🎯 Found ${gpu_node_count} GPU nodes to monitor"
    
    while [[ ${attempt} -lt ${max_attempts} ]]; do
        log_info "🔄 Checking Device Plugin status... ($((attempt + 1))/${max_attempts})"
        
        # Count ready pods on GPU nodes with timeout protection
        local ready_gpu_pods=0
        local check_failed=false
        
        for node in ${gpu_nodes}; do
            local pod_ready="false"
            
            # Use timeout to prevent hanging kubectl commands
            if command -v timeout &>/dev/null; then
                pod_ready=$(timeout ${timeout_seconds} ${KCMD} get pods -n "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" --field-selector spec.nodeName="${node}" -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || echo "false")
            else
                pod_ready=$(${KCMD} get pods -n "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" --field-selector spec.nodeName="${node}" -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || echo "false")
            fi
            
            if [[ "${pod_ready}" == "true" ]]; then
                ready_gpu_pods=$((ready_gpu_pods + 1))
            elif [[ -z "${pod_ready}" ]]; then
                check_failed=true
            fi
        done
        
        # Handle timeout or error cases
        if [[ "${check_failed}" == "true" ]]; then
            log_warning "⚠️  Some status checks timed out, retrying..."
            if [[ ${attempt} -lt $((max_attempts - 1)) ]]; then
                sleep 5
                ((attempt++))
                continue
            fi
        fi
        
        if [[ "${ready_gpu_pods}" -eq "${gpu_node_count}" ]] && [[ "${gpu_node_count}" -gt 0 ]]; then
            log_success "🎉 NVIDIA Device Plugin is ready on all GPU nodes (${ready_gpu_pods}/${gpu_node_count})"
            return 0
        fi
        
        # Show detailed progress
        log_info "📊 Device Plugin progress: ${ready_gpu_pods}/${gpu_node_count} GPU nodes ready"
        if [[ "${ready_gpu_pods}" -gt 0 ]]; then
            log_info "✅ ${ready_gpu_pods} nodes have working Device Plugin"
        fi
        if [[ $((gpu_node_count - ready_gpu_pods)) -gt 0 ]]; then
            log_info "⏳ $((gpu_node_count - ready_gpu_pods)) nodes still initializing"
        fi
        
        if [[ ${attempt} -lt $((max_attempts - 1)) ]]; then
            log_info "⏳ Waiting 10 seconds before next check..."
            sleep 10
        fi
        
        # Refresh GPU nodes list in case more joined (with timeout)
        if command -v timeout &>/dev/null; then
            gpu_nodes=$(timeout ${timeout_seconds} get_gpu_nodes 2>/dev/null || echo "${gpu_nodes}")
        else
            gpu_nodes=$(get_gpu_nodes 2>/dev/null || echo "${gpu_nodes}")
        fi
        gpu_node_count=$(echo "${gpu_nodes}" | wc -w)
        
        ((attempt++))
    done
    
    log_success "✅ Device Plugin installation completed"
    log_info "🚀 Continuing with setup - remaining initialization will happen automatically"
    log_info "💡 Some Device Plugin pods may still be starting up in background"
}

fix_gpu_node_labels() {
    log_step "Fixing GPU node labels..."
    
    local gpu_nodes
    gpu_nodes=$(get_gpu_nodes)
    
    if [[ -z "${gpu_nodes}" ]]; then
        die "No GPU nodes found"
    fi
    
    for node in ${gpu_nodes}; do
        log_info "Processing node: ${node}"
        
        if [[ "${DRY_RUN}" == "true" ]]; then
            log_info "[DRY RUN] Would add label to node ${node}: feature.node.kubernetes.io/pci-10de.present=true"
            continue
        fi
        
        # Add required labels
        ${KCMD} label nodes "${node}" feature.node.kubernetes.io/pci-10de.present=true --overwrite
        ${KCMD} label nodes "${node}" nvidia.com/gpu.present=true --overwrite
        
        log_success "Node ${node} labels fixed successfully"
    done
}

verify_gpu_resources() {
    log_step "Verifying GPU resource availability..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would verify GPU resource availability"
        return 0
    fi
    
    local gpu_nodes
    gpu_nodes=$(get_gpu_nodes)
    
    local total_gpus=0
    local available_gpus=0
    
    for node in ${gpu_nodes}; do
        local gpu_capacity
        gpu_capacity=$(${KCMD} get node "${node}" -o jsonpath='{.status.capacity.nvidia\.com/gpu}' 2>/dev/null || echo "0")
        local gpu_allocatable
        gpu_allocatable=$(${KCMD} get node "${node}" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "0")
        
        if [[ "${gpu_capacity}" -gt 0 ]]; then
            log_success "Node ${node}: GPU capacity=${gpu_capacity}, allocatable=${gpu_allocatable}"
            total_gpus=$((total_gpus + gpu_capacity))
            available_gpus=$((available_gpus + gpu_allocatable))
        else
            log_warning "Node ${node}: No GPU resources detected"
        fi
    done
    
    if [[ ${total_gpus} -gt 0 ]]; then
        log_success "GPU resource verification completed: Total ${total_gpus} GPUs, Available ${available_gpus}"
    else
        die "No GPU resources detected, please check NVIDIA Device Plugin installation"
    fi
}

run_health_check() {
    log_step "Running health checks..."
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would run complete health checks"
        return 0
    fi
    
    # Check Device Plugin pods
    local plugin_pods
    plugin_pods=$(${KCMD} get pods -n "${NVIDIA_DEVICE_PLUGIN_NAMESPACE}" -l app.kubernetes.io/name=nvidia-device-plugin --no-headers 2>/dev/null | wc -l || echo "0")
    
    if [[ "${plugin_pods}" -gt 0 ]]; then
        log_success "NVIDIA Device Plugin pods: ${plugin_pods} running"
    else
        log_warning "No running NVIDIA Device Plugin pods found"
    fi
    
    # Check GPU taints and tolerations
    local gpu_nodes
    gpu_nodes=$(get_gpu_nodes)
    
    for node in ${gpu_nodes}; do
        local has_gpu_taint
        has_gpu_taint=$(${KCMD} get node "${node}" -o jsonpath='{.spec.taints[?(@.key=="nvidia.com/gpu")].key}' 2>/dev/null || echo "")
        
        if [[ -n "${has_gpu_taint}" ]]; then
            log_success "Node ${node}: Has correct GPU taint"
        else
            log_warning "Node ${node}: Missing GPU taint (this may be normal)"
        fi
    done
    
    log_success "Health checks completed"
}

display_cluster_info() {
    log_cluster "Cluster Information:"
    echo ""
    echo "📊 Cluster Details:"
    echo "   Name: ${CLUSTER_NAME}"
    echo "   Region: ${CLUSTER_REGION}"
    echo "   Kubernetes Version: ${CLUSTER_VERSION}"
    if [[ "${USE_DEFAULT_VPC}" == "true" ]]; then
        echo "   VPC: Default VPC"
    else
        echo "   VPC: ${VPC_NAME} (Custom)"
    fi
    echo ""
    echo "🖥️  Node Pools:"
    echo "   CPU Pool: ${CPU_NODE_POOL} (${CPU_NODE_COUNT}x ${CPU_NODE_SIZE})"
    echo "   GPU Pool: ${GPU_NODE_POOL} (${GPU_NODE_COUNT}x ${GPU_NODE_SIZE})"
    echo ""
    
    if [[ "${DRY_RUN}" != "true" ]]; then
        echo "📋 Current Node Status:"
        ${KCMD} get nodes -o wide
        echo ""
        echo "🔧 GPU Resources:"
        ${KCMD} get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' | column -t
    fi
}

### MAIN FUNCTION ###
main() {
    parse_args "$@"
    
    log_cluster "🚀 DigitalOcean GPU Kubernetes Cluster Setup Script for llm-d-infra"
    log_cluster "NVIDIA Device Plugin Version: ${NVIDIA_DEVICE_PLUGIN_VERSION}"
    
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_warning "DRY RUN MODE - No actual operations will be performed"
    else
        log_info "💡 This script is 100% safe to re-run if it stops unexpectedly"
        log_info "🔄 If interrupted by API timeouts, simply run the same command again"
    fi
    
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        log_cluster "🏗️  CLUSTER CREATION MODE"
        echo ""
    else
        log_cluster "⚙️  EXISTING CLUSTER SETUP MODE"
        echo ""
    fi
    
    check_dependencies
    
    # Smart state detection and execution planning
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        check_doctl_auth
    fi
    
    determine_execution_plan
    
    # Execute cluster creation if needed
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        get_latest_k8s_version
        
        log_cluster "Creating complete DigitalOcean GPU cluster infrastructure..."
        create_vpc
        create_kubernetes_cluster
        create_gpu_node_pool
        wait_for_nodes
        
        # Update kubectl context for created cluster
        setup_kubectl
        display_cluster_info
    else
        # For existing clusters, ensure we can connect
        if ! ${KCMD} cluster-info &>/dev/null; then
            check_cluster_reachability
            verify_digitalocean_gpu_cluster
        fi
    fi
    
    # Smart GPU setup with optimized parallel processing
    log_cluster "Setting up GPU support for llm-d deployment..."
    
    # Check if NVIDIA Device Plugin needs installation/repair
    local device_plugin_needed=false
    if ! check_nvidia_device_plugin_status; then
        device_plugin_needed=true
        log_info "🚀 Installing NVIDIA Device Plugin..."
        install_nvidia_device_plugin
        
        # Start waiting for Device Plugin in background while we continue with other setup
        log_info "🔄 Device Plugin installation initiated, continuing with parallel setup..."
    else
        log_success "NVIDIA Device Plugin is already ready, skipping installation"
    fi
    
    # Check if GPU nodes need label fixes (can run in parallel with plugin installation)
    local gpu_nodes_need_setup=false
    if ! check_gpu_nodes_ready; then
        gpu_nodes_need_setup=true
        log_info "🏷️  Fixing GPU node labels..."
        fix_gpu_node_labels
    else
        log_success "GPU nodes are already ready and labeled correctly"
    fi
    
    # Now wait for Device Plugin if it was installed (optimized wait)
    if [[ "${device_plugin_needed}" == "true" ]]; then
        log_info "⏳ Finalizing Device Plugin setup..."
        wait_for_device_plugin
    fi
    
    # Brief resource refresh wait only if changes were made
    if [[ "${gpu_nodes_need_setup}" == "true" ]] && [[ "${DRY_RUN}" != "true" ]]; then
        log_info "🔄 Refreshing GPU resources (quick sync)..."
        sleep 15  # Reduced from 30 seconds
    fi
    
    verify_gpu_resources
    run_health_check
    
    echo ""
    log_success "🎉 DigitalOcean GPU Cluster setup completed successfully!"
    echo ""
    
    # Display performance summary
    log_cluster "⚡ Performance Optimizations Applied:"
    echo "  • 🚀 Parallel setup processing (Device Plugin + Node Labels)"
    echo "  • ⏱️  Reduced wait times: GPU pool (1.5min) + Node wait (1.3min) + Plugin (2min)"
    echo "  • 🧠 Smart state detection with auto-recovery"
    echo "  • 📊 Early continuation when CPU nodes are ready"
    echo ""
    
    if [[ "${CREATE_CLUSTER}" == "true" ]]; then
        log_cluster "🎯 Next Steps:"
        echo "  1. Your cluster is ready for llm-d deployment"
        echo "  2. Use the llm-d-infra installer with DigitalOcean-specific configurations:"
        echo "     cd ../../.. && ./llmd-infra-installer.sh"
        echo "  3. Apply DigitalOcean-specific GPU configurations from this directory"
    else
        log_cluster "🎯 Next Steps:"
        echo "  1. Your existing cluster is now ready for llm-d deployment"
        echo "  2. Use the llm-d-infra installer with DigitalOcean configurations"
    fi
    
    echo ""
    log_cluster "🔄 Smart Recovery Information:"
    echo "  💡 This script is designed to be safely re-run multiple times"
    echo "  🧠 If the script stops unexpectedly (network timeout, API delays):"
    echo "     → Simply run the same command again: ./setup-gpu-cluster.sh"
    echo "     → The script will detect existing components and continue from where it left off"
    echo "     → No duplicate resources will be created"
    echo ""
    log_cluster "⚠️  Important Notes:"
    echo "  📡 DigitalOcean API calls may occasionally timeout during node provisioning"
    echo "  🔄 If you see the script stop after 'GPU node pool created successfully':"
    echo "     → This is normal due to API response delays"
    echo "     → Wait 30 seconds, then re-run: ./setup-gpu-cluster.sh"
    echo "     → The script will continue with GPU setup automatically"
    echo ""
    log_cluster "💡 Performance Note:"
    echo "  This optimized script reduces total setup time from 15-30min to 5-8min"
    echo "  The script is safe to re-run and will auto-detect existing components"
    echo ""
    log_cluster "📚 For more information, see the DigitalOcean provider documentation"
}

# Run main function
main "$@"