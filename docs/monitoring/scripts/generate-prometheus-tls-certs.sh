#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-

set -euo pipefail

### GLOBALS ###
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-llm-d-monitoring}"
RELEASE_NAME="${RELEASE_NAME:-llmd}"
CERT_DIR="${CERT_DIR:-/tmp/prometheus-certs}"
CERT_VALIDITY_DAYS="${CERT_VALIDITY_DAYS:-3650}"  # 10 years
KUBERNETES_CONTEXT=""

### HELP & LOGGING ###
print_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Generate TLS certificates for Prometheus and create a Kubernetes secret.

Options:
  -n, --namespace NAME        Monitoring namespace (default: llm-d-monitoring)
  -d, --cert-dir DIR          Directory to store certificates (default: /tmp/prometheus-certs)
  -v, --validity DAYS         Certificate validity in days (default: 3650)
  -g, --context PATH          Supply a specific Kubernetes context/config file
  -h, --help                  Show this help and exit

Environment Variables:
  MONITORING_NAMESPACE        Override default monitoring namespace
  CERT_DIR                    Override default certificate directory
  CERT_VALIDITY_DAYS          Override certificate validity period

Examples:
  $(basename "$0")                              # Generate certs for llm-d-monitoring namespace
  $(basename "$0") -n monitoring                # Generate certs for 'monitoring' namespace
  $(basename "$0") -v 365                       # Generate certs valid for 1 year
EOF
}

# ANSI colour helpers
COLOR_RESET=$'\e[0m'
COLOR_GREEN=$'\e[32m'
COLOR_YELLOW=$'\e[33m'
COLOR_RED=$'\e[31m'
COLOR_BLUE=$'\e[34m'

log_info() {
  echo "${COLOR_BLUE}ℹ️  $*${COLOR_RESET}"
}

log_success() {
  echo "${COLOR_GREEN}✅ $*${COLOR_RESET}"
}

log_error() {
  echo "${COLOR_RED}❌ $*${COLOR_RESET}" >&2
}

fail() { log_error "$*"; exit 1; }

### UTILITIES ###
check_cmd() {
  command -v "$1" &>/dev/null || fail "Required command not found: $1"
}

check_dependencies() {
  local required_cmds=(openssl kubectl)
  for cmd in "${required_cmds[@]}"; do
    check_cmd "$cmd"
  done
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace)          MONITORING_NAMESPACE="$2"; shift 2 ;;
      -d|--cert-dir)           CERT_DIR="$2"; shift 2 ;;
      -v|--validity)           CERT_VALIDITY_DAYS="$2"; shift 2 ;;
      -g|--context)            KUBERNETES_CONTEXT="$2"; shift 2 ;;
      -h|--help)               print_help; exit 0 ;;
      *)                       fail "Unknown option: $1" ;;
    esac
  done
}

setup_env() {
  if [[ ! -z $KUBERNETES_CONTEXT ]]; then
    if [[ ! -f $KUBERNETES_CONTEXT ]]; then
      log_error "Error, the context file \"$KUBERNETES_CONTEXT\", passed via command-line option, does not exist!"
      exit 1
    fi
    KCMD="kubectl --kubeconfig $KUBERNETES_CONTEXT"
  else
    KCMD="kubectl"
  fi
}

generate_certificates() {
  log_info "🔐 Generating TLS certificates for Prometheus..."

  # Create certificate directory
  mkdir -p "${CERT_DIR}"
  cd "${CERT_DIR}"

  # Define the Prometheus service DNS names
  PROMETHEUS_SVC="${RELEASE_NAME}-kube-prometheus-stack-prometheus"
  DNS_NAMES="DNS.1:${PROMETHEUS_SVC}.${MONITORING_NAMESPACE}.svc,DNS.2:${PROMETHEUS_SVC}.${MONITORING_NAMESPACE}.svc.cluster.local,DNS.3:localhost"

  log_info "📝 Certificate will be valid for: ${DNS_NAMES}"

  # Create OpenSSL configuration file
  cat > openssl.cnf <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
req_extensions = req_ext
distinguished_name = dn

[dn]
C = US
ST = State
L = City
O = Organization
OU = Kubernetes
CN = Prometheus

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${PROMETHEUS_SVC}.${MONITORING_NAMESPACE}.svc
DNS.2 = ${PROMETHEUS_SVC}.${MONITORING_NAMESPACE}.svc.cluster.local
DNS.3 = localhost
IP.1 = 127.0.0.1

[v3_ext]
authorityKeyIdentifier=keyid,issuer:always
basicConstraints=CA:FALSE
keyUsage=keyEncipherment,dataEncipherment,digitalSignature
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
EOF

  # Generate CA private key
  log_info "🔑 Generating CA private key..."
  openssl genrsa -out ca.key 2048

  # Generate CA certificate
  log_info "📜 Generating CA certificate..."
  openssl req -x509 -new -nodes -key ca.key \
    -sha256 -days "${CERT_VALIDITY_DAYS}" \
    -out ca.crt \
    -subj "/CN=Prometheus-CA"

  # Generate server private key
  log_info "🔑 Generating server private key..."
  openssl genrsa -out tls.key 2048

  # Generate certificate signing request
  log_info "📝 Generating certificate signing request..."
  openssl req -new -key tls.key -out tls.csr -config openssl.cnf

  # Generate server certificate signed by CA
  log_info "📜 Generating server certificate..."
  openssl x509 -req -in tls.csr \
    -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out tls.crt -days "${CERT_VALIDITY_DAYS}" \
    -extensions v3_ext -extfile openssl.cnf

  # Verify the certificate
  log_info "🔍 Verifying certificate..."
  openssl x509 -in tls.crt -text -noout | grep -A 1 "Subject Alternative Name" || true

  log_success "Certificates generated successfully in ${CERT_DIR}"
  log_info "📋 Generated files:"
  log_info "   - ca.crt: Certificate Authority certificate"
  log_info "   - ca.key: Certificate Authority private key"
  log_info "   - tls.crt: Server certificate"
  log_info "   - tls.key: Server private key"
}

create_kubernetes_secret() {
  log_info "🔐 Creating Kubernetes secret for Prometheus TLS..."

  # Check if namespace exists
  if ! $KCMD get namespace "${MONITORING_NAMESPACE}" &>/dev/null; then
    log_error "Namespace ${MONITORING_NAMESPACE} does not exist. Please create it first or install Prometheus first."
    exit 1
  fi

  # Delete existing secret if it exists
  if $KCMD get secret prometheus-web-tls -n "${MONITORING_NAMESPACE}" &>/dev/null; then
    log_info "⚠️  Existing secret found, deleting..."
    $KCMD delete secret prometheus-web-tls -n "${MONITORING_NAMESPACE}"
  fi

  # Create the secret
  $KCMD create secret generic prometheus-web-tls \
    -n "${MONITORING_NAMESPACE}" \
    --from-file=tls.crt="${CERT_DIR}/tls.crt" \
    --from-file=tls.key="${CERT_DIR}/tls.key" \
    --from-file=ca.crt="${CERT_DIR}/ca.crt"

  log_success "Secret 'prometheus-web-tls' created in namespace ${MONITORING_NAMESPACE}"

  # Also create a configmap with the CA cert for clients to use
  if $KCMD get configmap prometheus-web-tls-ca -n "${MONITORING_NAMESPACE}" &>/dev/null; then
    log_info "⚠️  Existing CA ConfigMap found, deleting..."
    $KCMD delete configmap prometheus-web-tls-ca -n "${MONITORING_NAMESPACE}"
  fi

  $KCMD create configmap prometheus-web-tls-ca \
    -n "${MONITORING_NAMESPACE}" \
    --from-file=ca.crt="${CERT_DIR}/ca.crt"

  log_success "ConfigMap 'prometheus-web-tls-ca' created for client certificate verification"
}

print_next_steps() {
  log_info ""
  log_info "📋 Next Steps:"
  log_info ""
  log_info "1. Update your Prometheus installation to use TLS:"
  log_info "   Run: ./scripts/install-prometheus-grafana.sh --enable-tls"
  log_info ""
  log_info "2. Or manually upgrade your Prometheus helm release with TLS configuration"
  log_info ""
  log_info "3. Clients connecting to Prometheus will need to:"
  log_info "   - Use https:// instead of http://"
  log_info "   - Trust the CA certificate (available in ConfigMap: prometheus-web-tls-ca)"
  log_info ""
  log_info "4. To extract the CA cert for external clients:"
  log_info "   kubectl get configmap prometheus-web-tls-ca -n ${MONITORING_NAMESPACE} -o jsonpath='{.data.ca\.crt}' > prometheus-ca.crt"
  log_info ""
}

main() {
  parse_args "$@"
  setup_env
  check_dependencies

  generate_certificates
  create_kubernetes_secret
  print_next_steps

  log_success "🎉 TLS certificate generation complete!"
}

main "$@"
