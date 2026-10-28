#!/bin/sh
set -euo pipefail

# Re-initialize SSL DB if missing (e.g., first start with an emptyDir volume mount)
if [ ! -f "/var/lib/squid/ssl_db/size" ]; then
    echo "Initializing SSL certificate database..."
    /usr/lib64/squid/security_file_certgen -c -s /var/lib/squid/ssl_db -M 4MB
fi

echo "Starting Squid..."
exec "$@"