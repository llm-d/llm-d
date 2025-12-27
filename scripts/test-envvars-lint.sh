#!/bin/bash
# test the shell script env var linter
set -Eeuo pipefail

echo "Testing shell script environment variable linter..."

# Test 1: Script with undeclared variable (should warn)
cat > /tmp/test-undeclared.sh <<'EOF'
#!/bin/bash
# test script with undeclared variable
echo "Using ${UNDECLARED_VAR}"
EOF

echo ""
echo "Test 1: Script with undeclared variable (should WARN)"
if ./scripts/lint-envvars.py /tmp/test-undeclared.sh 2>&1 | grep -q "UNDECLARED_VAR"; then
    echo "✓ Test passed: correctly detected undeclared variable"
else
    echo "✗ Test failed: should have detected UNDECLARED_VAR"
    exit 1
fi

# Test 2: Script with properly declared variable (should pass)
cat > /tmp/test-declared.sh <<'EOF'
#!/bin/bash
# test script with declared variable
#
# Required environment variables:
# - MY_VAR: a test variable
echo "Using ${MY_VAR}"
EOF

echo ""
echo "Test 2: Script with properly declared variable (should PASS)"
if ./scripts/lint-envvars.py /tmp/test-declared.sh 2>&1 | grep -q "all environment variables properly declared"; then
    echo "✓ Test passed: correctly validated declared variable"
else
    echo "✗ Test failed: should have passed with declared variable"
    exit 1
fi

# Test 3: Script with locally defined variable (should pass)
cat > /tmp/test-local.sh <<'EOF'
#!/bin/bash
# test script with locally defined variable
LOCAL_VAR="value"
echo "Using ${LOCAL_VAR}"
EOF

echo ""
echo "Test 3: Script with locally defined variable (should PASS)"
if ./scripts/lint-envvars.py /tmp/test-local.sh 2>&1 | grep -q "all environment variables properly declared"; then
    echo "✓ Test passed: correctly recognized locally defined variable"
else
    echo "✗ Test failed: should have passed with locally defined variable"
    exit 1
fi

# Test 4: Script with exempt variable (should pass)
cat > /tmp/test-exempt.sh <<'EOF'
#!/bin/bash
# test script using exempt variables
echo "Home is ${HOME} and path is ${PATH}"
EOF

echo ""
echo "Test 4: Script with exempt variables (should PASS)"
if ./scripts/lint-envvars.py /tmp/test-exempt.sh 2>&1 | grep -q "all environment variables properly declared"; then
    echo "✓ Test passed: correctly exempted common variables"
else
    echo "✗ Test failed: should have passed with exempt variables"
    exit 1
fi

# Test 5: Script with exported locally defined variable (should pass)
cat > /tmp/test-export.sh <<'EOF'
#!/bin/bash
# test script with exported variable
export EXPORTED_VAR="value"
echo "Using ${EXPORTED_VAR}"
EOF

echo ""
echo "Test 5: Script with exported locally defined variable (should PASS)"
if ./scripts/lint-envvars.py /tmp/test-export.sh 2>&1 | grep -q "all environment variables properly declared"; then
    echo "✓ Test passed: correctly recognized exported variable"
else
    echo "✗ Test failed: should have passed with exported variable"
    exit 1
fi

# Test 6: Script with array variable (should pass)
cat > /tmp/test-array.sh <<'EOF'
#!/bin/bash
# test script with array variable
ARRAY_VAR=(one two three)
echo "Using ${ARRAY_VAR[0]}"
EOF

echo ""
echo "Test 6: Script with array variable (should PASS)"
if ./scripts/lint-envvars.py /tmp/test-array.sh 2>&1 | grep -q "all environment variables properly declared"; then
    echo "✓ Test passed: correctly recognized array variable"
else
    echo "✗ Test failed: should have passed with array variable"
    exit 1
fi

# Test 7: Script with $VAR syntax (no braces) (should detect undeclared)
cat > /tmp/test-nobrace.sh <<'EOF'
#!/bin/bash
# test script with $VAR syntax
echo "Using $NOBRACE_VAR"
EOF

echo ""
echo "Test 7: Script with \$VAR syntax undeclared (should WARN)"
if ./scripts/lint-envvars.py /tmp/test-nobrace.sh 2>&1 | grep -q "NOBRACE_VAR"; then
    echo "✓ Test passed: correctly detected undeclared variable with \$VAR syntax"
else
    echo "✗ Test failed: should have detected NOBRACE_VAR"
    exit 1
fi

# Test 8: Script with variable default syntax ${VAR:-default} (should detect if undeclared)
cat > /tmp/test-default.sh <<'EOF'
#!/bin/bash
# test script with default syntax
echo "Using ${DEFAULT_VAR:-fallback}"
EOF

echo ""
echo "Test 8: Script with \${VAR:-default} syntax undeclared (should WARN)"
if ./scripts/lint-envvars.py /tmp/test-default.sh 2>&1 | grep -q "DEFAULT_VAR"; then
    echo "✓ Test passed: correctly detected undeclared variable with default syntax"
else
    echo "✗ Test failed: should have detected DEFAULT_VAR"
    exit 1
fi

# cleanup
rm -f /tmp/test-undeclared.sh /tmp/test-declared.sh /tmp/test-local.sh
rm -f /tmp/test-exempt.sh /tmp/test-export.sh /tmp/test-array.sh
rm -f /tmp/test-nobrace.sh /tmp/test-default.sh

echo ""
echo "All tests passed!"
