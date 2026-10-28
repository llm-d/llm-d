#!/usr/bin/env bash
# Generates a Squid CA certificate and creates the corresponding Kubernetes secrets
# for SSL Bump support.
#
# Usage: ./generate-ssl-certs.sh [--namespace <ns>] [--out-dir <dir>] [--org <org>]
#
# Defaults:
#   --namespace  default
#   --out-dir    squid-ssl-certs
#   --org        YourOrganization

set -euo pipefail

NAMESPACE="dpikus-epd8"
OUT_DIR="squid-ssl-certs"
ORG="YourOrganization"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --out-dir)   OUT_DIR="$2";   shift 2 ;;
    --org)       ORG="$2";       shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

KEY="$OUT_DIR/squid-ca-key.pem"
CERT="$OUT_DIR/squid-ca-cert.pem"

# ---------------------------------------------------------------------------
# Step 1: Generate certificates
# ---------------------------------------------------------------------------
mkdir -p "$OUT_DIR"

if [[ -f "$KEY" || -f "$CERT" ]]; then
  echo "ERROR: Certificate files already exist in '$OUT_DIR/'. Delete them to regenerate." >&2
  exit 1
fi

echo "Generating CA private key (4096-bit)..."
openssl genrsa -out "$KEY" 4096

echo "Generating CA certificate (valid 10 years)..."
openssl req -new -x509 -days 3650 \
  -key "$KEY" \
  -out "$CERT" \
  -subj "/C=US/ST=State/L=City/O=${ORG}/OU=IT/CN=Squid Proxy CA"

# ---------------------------------------------------------------------------
# Step 2: Verify the certificate matches the key
# ---------------------------------------------------------------------------
echo "Verifying certificate/key pair..."
CERT_MOD=$(openssl x509 -noout -modulus -in "$CERT" | openssl md5)
KEY_MOD=$(openssl rsa  -noout -modulus -in "$KEY"  | openssl md5)
if [[ "$CERT_MOD" != "$KEY_MOD" ]]; then
  echo "ERROR: certificate and key moduli do not match!" >&2
  exit 1
fi
echo "Certificate/key pair verified."

# ---------------------------------------------------------------------------
# Step 3: Create Kubernetes secrets
# ---------------------------------------------------------------------------
echo "Applying Kubernetes secret 'squid-ssl-certs' (cert + key) in namespace '$NAMESPACE'..."
kubectl create secret generic squid-ssl-certs \
  --from-file=squid-ca-cert.pem="$CERT" \
  --from-file=squid-ca-key.pem="$KEY" \
  -n "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Applying Kubernetes secret 'squid-ca-public-cert' (cert only) in namespace '$NAMESPACE'..."
kubectl create secret generic squid-ca-public-cert \
  --from-file=squid-ca-cert.pem="$CERT" \
  -n "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "Done. Files written to '$OUT_DIR/'."
echo "⚠️  Keep '$KEY' secure"
