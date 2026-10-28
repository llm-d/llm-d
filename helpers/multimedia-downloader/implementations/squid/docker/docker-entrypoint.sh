#!/bin/sh
set -euo pipefail

# Initialize the SSL DB on first start. The size must match sslcrtd_program -M in squid.conf.
if [ ! -f "/var/lib/squid/ssl_db/size" ]; then
    echo "Initializing SSL certificate database..."
    /usr/lib64/squid/security_file_certgen -c -s /var/lib/squid/ssl_db -M 16MB
fi

echo "Starting Squid..."
exec "$@"