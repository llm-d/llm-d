#!/usr/bin/env bash
# Verifies the Grafana/Prometheus wiring that metric-to-trace navigation needs
# (llm-d#2462). Exemplars are dropped silently at every stage they are not
# configured for, so each of these is asserted on the values file that would
# actually be handed to helm rather than on the script source.
#
# The installer is run for real against stubbed kubectl/helm binaries: no
# cluster is required, and the helm stub captures the rendered values file
# before the installer deletes it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="${SCRIPT_DIR}/../guides/recipes/observability/install-prometheus-grafana.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAILURES=0
PROM_DS='.grafana.datasources."datasources.yaml".datasources[] | select(.type=="prometheus")'
JAEGER_DS='.grafana.datasources."datasources.yaml".datasources[] | select(.type=="jaeger")'

for cmd in yq jq bash; do
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

write_stubs() {
  mkdir -p "$WORK/bin"

  # A plain, empty, non-OpenShift cluster. JAEGER_NS unset means no tracing
  # stack is installed, which is the case the wiring has to degrade for.
  cat > "$WORK/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"cluster-info"*)              exit 0 ;;
  *"get clusterversion"*)        exit 1 ;;
  *"get crd servicemonitors"*)   exit 1 ;;
  *"--field-selector metadata.name=jaeger-collector"*)
      [[ -n "${JAEGER_NS:-}" ]] && printf '%s' "$JAEGER_NS"
      exit 0 ;;
  *"get svc jaeger-collector"*)
      # only the namespace that actually holds jaeger answers
      for a in "$@"; do
        [[ "$prev" == "-n" ]] && req="$a"
        prev="$a"
      done
      [[ -n "${JAEGER_NS:-}" && "${req:-}" == "${JAEGER_NS}" ]] && exit 0 || exit 1 ;;
  *)                             exit 0 ;;
esac
EOF

  # The installer removes the values file once helm has consumed it, so the
  # stub keeps a copy of exactly what helm was given.
  cat > "$WORK/bin/helm" <<'EOF'
#!/usr/bin/env bash
prev=""
for arg in "$@"; do
  if [[ "$prev" == "-f" && -f "$arg" ]]; then cp "$arg" "$CAPTURE_TO"; fi
  prev="$arg"
done
exit 0
EOF

  chmod +x "$WORK/bin/kubectl" "$WORK/bin/helm"
}

render() { # name [installer args...]
  local name="$1"; shift
  CAPTURE_TO="$WORK/$name.yaml" PATH="$WORK/bin:$PATH" \
    bash "$INSTALLER" "$@" >"$WORK/$name.log" 2>&1 || true

  if [[ ! -f "$WORK/$name.yaml" ]]; then
    printf '  FAIL %s: installer produced no values file (see %s)\n' "$name" "$WORK/$name.log"
    FAILURES=$((FAILURES + 1))
    return 1
  fi
}

assert_common() { # yaml-file
  local y="$1"
  yq e '.' "$y" >/dev/null 2>&1 \
    && printf '  ok   values file is valid yaml\n' \
    || { printf '  FAIL values file is not valid yaml\n'; FAILURES=$((FAILURES + 1)); return; }

  # Without this feature flag Prometheus parses the exemplar off the scrape and
  # discards it, with no error anywhere.
  check "exemplar-storage enabled" "true" \
    "$(yq e '.prometheus.prometheusSpec.enableFeatures | contains(["exemplar-storage"])' "$y")"

  # Two jsonData keys under one datasource is valid yaml in which one block
  # silently wins. There must never be more than one.
  local blocks
  blocks="$(grep -c 'jsonData:' "$y" || true)"
  if (( blocks <= 1 )); then
    printf '  ok   at most one jsonData block (%s)\n' "$blocks"
  else
    printf '  FAIL duplicate jsonData blocks: %s\n' "$blocks"
    FAILURES=$((FAILURES + 1))
  fi
}

assert_linked() { # yaml-file namespace
  local y="$1" ns="$2"
  check "jaeger datasource present" "1" "$(yq e "[$JAEGER_DS] | length" "$y")"
  check "jaeger url uses detected namespace" \
    "http://jaeger-collector.${ns}.svc.cluster.local:16686" "$(yq e "$JAEGER_DS | .url" "$y")"
  # The link is what turns the trace_id label from text into a click.
  check "exemplar link targets the jaeger datasource" "jaeger" \
    "$(yq e "[$PROM_DS.jsonData.exemplarTraceIdDestinations[]? | select(.name==\"trace_id\") | .datasourceUid] | .[0] // \"none\"" "$y")"
  check "link uid matches the jaeger datasource uid" "jaeger" "$(yq e "$JAEGER_DS | .uid" "$y")"
}

assert_unlinked() { # yaml-file
  local y="$1"
  check "no jaeger datasource" "0" "$(yq e "[$JAEGER_DS] | length" "$y")"
  check "no exemplar link" "none" \
    "$(yq e "[$PROM_DS.jsonData.exemplarTraceIdDestinations[]? | .datasourceUid] | .[0] // \"none\"" "$y")"
}

write_stubs

echo "tracing not installed (central mode)"
JAEGER_NS="" render no-tracing && { assert_common "$WORK/no-tracing.yaml"; assert_unlinked "$WORK/no-tracing.yaml"; }

echo "tracing installed (central mode)"
JAEGER_NS="tracing" render central && { assert_common "$WORK/central.yaml"; assert_linked "$WORK/central.yaml" tracing; }

echo "tracing installed, tls enabled (central mode)"
JAEGER_NS="tracing" render central-tls -t && {
  assert_common "$WORK/central-tls.yaml"
  assert_linked "$WORK/central-tls.yaml" tracing
  # The tls branch writes its own jsonData; both settings have to survive.
  check "tlsSkipVerify kept alongside the link" "true" "$(yq e "$PROM_DS.jsonData.tlsSkipVerify" "$WORK/central-tls.yaml")"
}

echo "tracing installed (individual mode)"
JAEGER_NS="tracing" render individual -i -n my-namespace && {
  assert_common "$WORK/individual.yaml"
  assert_linked "$WORK/individual.yaml" tracing
}

echo "explicit TRACING_NAMESPACE override"
JAEGER_NS="observability" TRACING_NAMESPACE="observability" render override && {
  assert_common "$WORK/override.yaml"
  assert_linked "$WORK/override.yaml" observability
}

echo "TRACING_NAMESPACE points at a namespace with no jaeger"
# The detect helper logs a warning on this path. If that warning went to stdout
# it would be captured as the namespace and rendered into the datasource url,
# producing a values file that is silently wrong rather than simply unlinked.
JAEGER_NS="tracing" TRACING_NAMESPACE="wrong-namespace" render bad-override && {
  assert_common "$WORK/bad-override.yaml"
  assert_unlinked "$WORK/bad-override.yaml"
}

echo "dashboard panel is wired to the metric that carries the exemplar"
DASHBOARD="${SCRIPT_DIR}/../guides/recipes/observability/grafana/dashboards/llm-d-failure-saturation-dashboard.json"
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
if (( FAILURES > 0 )); then
  echo "${FAILURES} assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
