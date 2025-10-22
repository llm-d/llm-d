#!/bin/bash
# test the dockerfile env var linter
set -Eeuo pipefail

echo "Testing Dockerfile environment variable linter..."

# create a test script with requirements
cat > /tmp/test-script.sh <<'EOF'
#!/bin/bash
# test script
#
# Required environment variables:
# - TEST_VAR1: first test variable
# - TEST_VAR2: second test variable
echo "Using ${TEST_VAR1} and ${TEST_VAR2}"
EOF

# create a test dockerfile that's missing TEST_VAR2
cat > /tmp/test-Dockerfile <<'EOF'
FROM ubuntu:22.04 AS test

ARG TEST_VAR1=value1

RUN /tmp/test-script.sh
EOF

# create temp scripts dir
mkdir -p /tmp/test-scripts
cp /tmp/test-script.sh /tmp/test-scripts/

echo ""
echo "Test 1: Dockerfile missing required variable (should FAIL)"
if ./scripts/lint-dockerfile-envvars.py /tmp/test-Dockerfile /tmp/test-scripts 2>&1; then
    echo "✗ Test failed: should have detected missing TEST_VAR2"
    exit 1
else
    echo "✓ Test passed: correctly detected missing variable"
fi

# create a corrected dockerfile
cat > /tmp/test-Dockerfile-fixed <<'EOF'
FROM ubuntu:22.04 AS test

ARG TEST_VAR1=value1
ARG TEST_VAR2=value2

RUN /tmp/test-script.sh
EOF

echo ""
echo "Test 2: Dockerfile with all required variables (should PASS)"
if ./scripts/lint-dockerfile-envvars.py /tmp/test-Dockerfile-fixed /tmp/test-scripts 2>&1; then
    echo "✓ Test passed: all variables declared"
else
    echo "✗ Test failed: should have passed with all variables declared"
    exit 1
fi

# cleanup
rm -f /tmp/test-script.sh /tmp/test-Dockerfile /tmp/test-Dockerfile-fixed
rm -rf /tmp/test-scripts

echo ""
echo "All tests passed!"
