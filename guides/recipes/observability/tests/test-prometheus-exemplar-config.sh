#!/usr/bin/env bash
# Verifies the Grafana/Prometheus wiring that metric-to-trace navigation needs
# (llm-d#2462). Exemplars are dropped silently at every stage they are not
# configured for, so each of these is asserted on the values file that would
# actually be handed to helm rather than on the script source.
#
# The installer runs for real against the stub binaries in tests/bin, so no
# cluster is required. The helm stub keeps a copy of the values file it is
# given, since the installer deletes it once helm returns.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="$(cd "${TEST_DIR}/../../../.." && pwd)"
INSTALLER="${REPOSITORY}/guides/recipes/observability/install-prometheus-grafana.sh"
DASHBOARD="${REPOSITORY}/guides/recipes/observability/grafana/dashboards/llm-d-failure-saturation-dashboard.json"
STUB_DIR="${TEST_DIR}/bin"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/llm-d-exemplar-test.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

FAILURES=0
PROM_DS='.grafana.datasources."datasources.yaml".datasources[] | select(.type=="prometheus")'
JAEGER_DS='.grafana.datasources."datasources.yaml".datasources[] | select(.type=="jaeger")'
LINKS="$PROM_DS.jsonData.exemplarTraceIdDestinations[]?"

for cmd in yq jq; do
  command -v "$cmd" >/dev/null || { echo "missing required tool: $cmd" >&2; exit 1; }
done

check() { # description expected actual
  if [[ "$2" == "$3" ]]; then
    printf '  ok   %s\n' "$1"
  else
    printf '  FAIL %s: expected [%s], got [%s]\n' "$1" "$2" "$3"
    FAILURES=$((FAILURES + 1))
  fi
}

check_contains() { # description file text
  if grep -F -- "$3" "$2" >/dev/null; then
    printf '  ok   %s\n' "$1"
  else
    printf '  FAIL %s: [%s] not in %s\n' "$1" "$3" "$2"
    FAILURES=$((FAILURES + 1))
  fi
}

check_lacks() { # description file text
  if grep -F -- "$3" "$2" >/dev/null; then
    printf '  FAIL %s: [%s] unexpectedly in %s\n' "$1" "$3" "$2"
    FAILURES=$((FAILURES + 1))
  else
    printf '  ok   %s\n' "$1"
  fi
}

# render NAME [installer args...] runs the installer and leaves the values file
# helm received in $WORK_DIR/NAME.yaml, its output in NAME.log and the commands
# that changed the cluster in NAME.mutations. Callers set STUB_* and installer
# variables in the environment.
render() {
  local name="$1"
  shift
  : > "${WORK_DIR}/${name}.stub"
  : > "${WORK_DIR}/${name}.mutations"
  : > "${WORK_DIR}/${name}.openssl"
  env \
    PATH="${STUB_DIR}:/usr/bin:/bin" \
    STUB_COMMAND=unused \
    STUB_MODE=exemplar \
    STUB_LOG="${WORK_DIR}/${name}.stub" \
    STUB_MUTATIONS="${WORK_DIR}/${name}.mutations" \
    STUB_OPENSSL_LOG="${WORK_DIR}/${name}.openssl" \
    STUB_CAPTURE_VALUES="${WORK_DIR}/${name}.yaml" \
    bash "$INSTALLER" "$@" >"${WORK_DIR}/${name}.log" 2>&1 || true
}

rendered() { # name
  if [[ -f "${WORK_DIR}/$1.yaml" ]]; then
    return 0
  fi
  printf '  FAIL %s: installer gave helm no values file (see %s)\n' "$1" "${WORK_DIR}/$1.log"
  FAILURES=$((FAILURES + 1))
  return 1
}

assert_common() { # yaml-file
  local y="$1"
  if yq e '.' "$y" >/dev/null 2>&1; then
    printf '  ok   values file is valid yaml\n'
  else
    printf '  FAIL values file is not valid yaml\n'
    FAILURES=$((FAILURES + 1))
    return
  fi

  # Without this feature flag Prometheus parses the exemplar off the scrape and
  # discards it, with no error anywhere.
  check "exemplar-storage enabled" "true" \
    "$(yq e '.prometheus.prometheusSpec.enableFeatures | contains(["exemplar-storage"])' "$y")"

  # Two jsonData keys under one datasource is valid yaml in which one block
  # silently wins. There must never be more than one.
  local blocks
  blocks="$(grep -c 'jsonData:' "$y" || true)"
  check "at most one jsonData block" "true" "$([[ "$blocks" -le 1 ]] && echo true || echo false)"
}

assert_linked() { # yaml-file namespace [jaeger-ui-url]
  local y="$1" ns="$2" ui="${3:-http://localhost:16686}"
  check "jaeger datasource present" "1" "$(yq e "[$JAEGER_DS] | length" "$y")"
  check "jaeger url uses namespace ${ns}" \
    "http://jaeger-collector.${ns}.svc.cluster.local:16686" "$(yq e "$JAEGER_DS | .url" "$y")"
  # The link is what turns the trace_id label from text into a click.
  check "exemplar link targets the jaeger datasource" "jaeger" \
    "$(yq e "[$LINKS | select(.name==\"trace_id\" and .datasourceUid != null) | .datasourceUid] | .[0] // \"none\"" "$y")"
  check "link uid matches the jaeger datasource uid" "jaeger" "$(yq e "$JAEGER_DS | .uid" "$y")"
  # The datasource link shows "No data" with Jaeger, so the URL link is the
  # working one. A single "$" gets expanded away by Grafana's provisioning.
  check "exemplar url link opens the trace in the jaeger ui" "${ui}/trace/\$\${__value.raw}" \
    "$(yq e "[$LINKS | select(.name==\"trace_id\" and .url != null) | .url] | .[0] // \"none\"" "$y")"
}

assert_unlinked() { # yaml-file
  local y="$1"
  check "no jaeger datasource" "0" "$(yq e "[$JAEGER_DS] | length" "$y")"
  check "no exemplar link" "0" "$(yq e "[$LINKS] | length" "$y")"
}

echo "tracing not installed (central mode)"
render no-tracing
if rendered no-tracing; then
  assert_common "${WORK_DIR}/no-tracing.yaml"
  assert_unlinked "${WORK_DIR}/no-tracing.yaml"
  # Following the setup docs installs monitoring before tracing, so the skip
  # has to say how to add the links afterwards.
  check_contains "skip message says to re-run after installing tracing" \
    "${WORK_DIR}/no-tracing.log" "then re-run this script"
fi

echo "tracing installed (central mode)"
STUB_JAEGER_NAMESPACES=tracing render central
if rendered central; then
  assert_common "${WORK_DIR}/central.yaml"
  assert_linked "${WORK_DIR}/central.yaml" tracing
fi

echo "tracing installed, tls enabled (central mode)"
STUB_JAEGER_NAMESPACES=tracing render central-tls -t
if rendered central-tls; then
  assert_common "${WORK_DIR}/central-tls.yaml"
  assert_linked "${WORK_DIR}/central-tls.yaml" tracing
  # The tls branch writes its own jsonData; both settings have to survive.
  check "tlsSkipVerify kept alongside the link" "true" \
    "$(yq e "$PROM_DS.jsonData.tlsSkipVerify" "${WORK_DIR}/central-tls.yaml")"
fi

echo "tracing installed (individual mode)"
STUB_JAEGER_NAMESPACES=tracing render individual -i -n my-namespace
if rendered individual; then
  assert_common "${WORK_DIR}/individual.yaml"
  assert_linked "${WORK_DIR}/individual.yaml" tracing
fi

echo "explicit TRACING_NAMESPACE override"
STUB_JAEGER_NAMESPACES="observability tracing" TRACING_NAMESPACE=observability render override
if rendered override; then
  assert_common "${WORK_DIR}/override.yaml"
  assert_linked "${WORK_DIR}/override.yaml" observability
fi

echo "explicit JAEGER_UI_URL override"
STUB_JAEGER_NAMESPACES=tracing JAEGER_UI_URL="https://jaeger.example.com" render ui-override
if rendered ui-override; then
  assert_common "${WORK_DIR}/ui-override.yaml"
  assert_linked "${WORK_DIR}/ui-override.yaml" tracing "https://jaeger.example.com"
fi

echo "TRACING_NAMESPACE points at a namespace with no jaeger"
# The detect helper logs a warning on this path. If that warning went to stdout
# it would be captured as the namespace and rendered into the datasource url,
# producing a values file that is silently wrong rather than simply unlinked.
STUB_JAEGER_NAMESPACES=tracing TRACING_NAMESPACE=wrong-namespace render bad-override
if rendered bad-override; then
  assert_common "${WORK_DIR}/bad-override.yaml"
  assert_unlinked "${WORK_DIR}/bad-override.yaml"
fi

echo "jaeger in several namespaces"
# The tracing script installs Jaeger per workload namespace. The pick has to be
# stable, and the operator has to be told how to choose another.
STUB_JAEGER_NAMESPACES="team-b team-a" render several
if rendered several; then
  assert_common "${WORK_DIR}/several.yaml"
  assert_linked "${WORK_DIR}/several.yaml" team-a
  check_contains "warning names every match" "${WORK_DIR}/several.log" "team-a, team-b"
  check_contains "warning points at TRACING_NAMESPACE" "${WORK_DIR}/several.log" "Set TRACING_NAMESPACE"
fi

# Re-running against an existing release. The setup docs install monitoring
# first and tracing later, so this is how the links get added.
printf 'grafana:\n  adminPassword: admin\n' > "${WORK_DIR}/plain-release.yaml"

echo "existing release, tracing installed since"
STUB_RELEASE_CHART_VERSION=62.7.0 STUB_RELEASE_VALUES="${WORK_DIR}/plain-release.yaml" \
  STUB_JAEGER_NAMESPACES=tracing render upgrade
if rendered upgrade; then
  assert_common "${WORK_DIR}/upgrade.yaml"
  assert_linked "${WORK_DIR}/upgrade.yaml" tracing
  check_contains "upgrades the existing release" "${WORK_DIR}/upgrade.mutations" "args=[upgrade][llmd]"
  # Without the pin, helm upgrade would move to the latest chart as a side effect.
  check_contains "pins the installed chart version" "${WORK_DIR}/upgrade.mutations" "[--version][62.7.0]"
  check_contains "keeps the rest of the release's values" "${WORK_DIR}/upgrade.mutations" "[--reuse-values]"
  check_lacks "does not install a second release" "${WORK_DIR}/upgrade.mutations" "args=[install]"
fi

echo "existing tls release, tracing installed since"
printf 'grafana:\n  datasources:\n    datasources.yaml:\n      datasources:\n      - name: Prometheus\n        jsonData:\n          tlsSkipVerify: true\n' \
  > "${WORK_DIR}/tls-release.yaml"
STUB_RELEASE_CHART_VERSION=62.7.0 STUB_RELEASE_VALUES="${WORK_DIR}/tls-release.yaml" \
  STUB_JAEGER_NAMESPACES=tracing render upgrade-tls
if rendered upgrade-tls; then
  assert_common "${WORK_DIR}/upgrade-tls.yaml"
  assert_linked "${WORK_DIR}/upgrade-tls.yaml" tracing
  # The upgrade replaces the datasource list, so it has to carry TLS over even
  # though -t was not passed again.
  check "tlsSkipVerify carried over" "true" \
    "$(yq e "$PROM_DS.jsonData.tlsSkipVerify" "${WORK_DIR}/upgrade-tls.yaml")"
  check "prometheus url stays https" "https" \
    "$(yq e "$PROM_DS.url" "${WORK_DIR}/upgrade-tls.yaml" | cut -d: -f1)"
fi

echo "existing release, already linked"
printf 'grafana:\n  datasources:\n    datasources.yaml:\n      datasources:\n      - name: Prometheus\n        jsonData:\n          exemplarTraceIdDestinations:\n          - name: trace_id\n            datasourceUid: jaeger\n' \
  > "${WORK_DIR}/linked-release.yaml"
STUB_RELEASE_CHART_VERSION=62.7.0 STUB_RELEASE_VALUES="${WORK_DIR}/linked-release.yaml" \
  STUB_JAEGER_NAMESPACES=tracing render already-linked
check_lacks "leaves an already linked release alone" "${WORK_DIR}/already-linked.mutations" "args=[upgrade]"

echo "existing release, still no tracing"
STUB_RELEASE_CHART_VERSION=62.7.0 STUB_RELEASE_VALUES="${WORK_DIR}/plain-release.yaml" render no-tracing-upgrade
check_lacks "does not upgrade without jaeger" "${WORK_DIR}/no-tracing-upgrade.mutations" "args=[upgrade]"
check_contains "says how to add the links later" "${WORK_DIR}/no-tracing-upgrade.log" "then re-run this script"

echo "dashboard panel is wired to the metric that carries the exemplar"
EXEMPLAR_METRIC="llm_d_epp_request_duration_seconds_bucket"
if jq -e . "$DASHBOARD" >/dev/null 2>&1; then
  printf '  ok   dashboard is valid json\n'

  # A query with exemplars enabled on a metric that carries none renders an
  # empty panel with no error, which is the failure this whole path is about.
  check "every exemplar query uses ${EXEMPLAR_METRIC}" "0" \
    "$(jq "[.panels[]?.targets[]? | select(.exemplar == true) | select(.expr | contains(\"${EXEMPLAR_METRIC}\") | not)] | length" "$DASHBOARD")"

  check "at least one panel requests exemplars" "true" \
    "$(jq '[.panels[]?.targets[]? | select(.exemplar == true)] | length > 0' "$DASHBOARD")"

  # The EPP renamed this metric; queries left on the old name return nothing.
  check "no query uses the retired objective latency metric" "0" \
    "$(jq '[.panels[]?.targets[]? | select(.expr // "" | contains("inference_objective_request_duration_seconds"))] | length' "$DASHBOARD")"
else
  printf '  FAIL dashboard is not valid json\n'
  FAILURES=$((FAILURES + 1))
fi

echo
if ((FAILURES > 0)); then
  echo "${FAILURES} assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
