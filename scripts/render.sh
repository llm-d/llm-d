#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 4; sh-indentation: 4; -*-
#
# render.sh - hydrate llm-d guide manifests into plain YAML.
#
# Renders the same manifests the guide READMEs deploy (helm router chart +
# kustomize model server overlays) without installing anything, so the full
# stack can be inspected, diffed, reviewed, or applied with plain kubectl.
#
# stdout carries only manifests; all logging goes to stderr. The output of
# `router` and `overlay` is byte-identical to the underlying `helm template`
# and `kustomize build` commands documented in the guides.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export REPO_ROOT
# shellcheck disable=SC1091
source "${REPO_ROOT}/guides/env.sh"

GUIDES_DIR="${REPO_ROOT}/guides"
BASE_VALUES="${GUIDES_DIR}/recipes/router/base.values.yaml"
FEATURES_DIR="${GUIDES_DIR}/recipes/router/features"

print_help() {
    cat <<'EOF'
Usage: scripts/render.sh <command> [arguments] [options]

Render llm-d guide manifests (helm router chart + kustomize overlays) to
plain YAML. stdout carries only manifests; logs go to stderr.

Commands:
  router <guide>    Render the llm-d router helm chart(s) for a guide
  overlay <dir>     Render a single kustomize overlay directory
  list <guide>      List all buildable kustomize overlays of a guide
  all <guide>       Render the router plus overlays of a guide

Router options (for `router` and `all`):
  --chart standalone|gateway|both
                    Which router chart to render (default: standalone)
  --release NAME    Helm release name (default: the guide name, which the
                    guide READMEs require for inference pool pairing)
  --version VER     Chart version override (default: ROUTER_CHART_VERSION
                    from guides/env.sh)
  --monitoring      Layer recipes/router/features/monitoring.values.yaml
  --feature NAME    Layer recipes/router/features/NAME.values.yaml (or
                    NAME.yaml, e.g. httproute-flags); repeatable, applied
                    in the order given
  --guide-values FILE
                    Replace the default guide values file
                    (guides/<guide>/router/<guide>.values.yaml), for
                    variants such as optimized-baseline-trtllm.values.yaml
  -f, --values FILE Extra values file layered AFTER the guide values;
                    repeatable (e.g. wide-ep-lws router/xpu.values.yaml)
  --set KEY=VALUE   Passed through to helm; repeatable
  -n, --namespace NS
                    Namespace passed to helm template (default: helm's own
                    default)

`all` options:
  --modelserver PATH
                    Render only the named model server overlay(s), PATH
                    relative to guides/<guide>/modelserver/; repeatable.
                    Default: every overlay found under the guide.

General options:
  -o, --output DIR  Write one YAML file per rendered unit into DIR, each
                    with a generated-by header. Default: raw stdout.
  -h, --help        Show this help

Examples:
  # Full stack for pd-disaggregation on GPU + GKE, standalone router:
  scripts/render.sh all pd-disaggregation --modelserver gpu/vllm/gke -o rendered/

  # Router only, with monitoring, gateway mode (mirrors the README helm flags):
  scripts/render.sh router pd-disaggregation --monitoring \
      --chart gateway --feature httproute-flags --set provider.name=gke

  # One overlay, straight to stdout:
  scripts/render.sh overlay guides/pd-disaggregation/modelserver/gpu/vllm/gke

Note: helm values are layered in the fixed order base -> features -> guide
values -> extra --values -> --set, matching the guide READMEs. Escaping
inside --set values follows helm's own rules.
EOF
}

log() { echo "$*" >&2; }
die() { echo "ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found in PATH"
}

# --- kustomize tool selection: prefer the standalone binary (what CI uses),
# --- fall back to kubectl's embedded kustomize for users who only have kubectl.
KUSTOMIZE_CMD=()
init_kustomize() {
    if [[ ${#KUSTOMIZE_CMD[@]} -gt 0 ]]; then return 0; fi
    if command -v kustomize >/dev/null 2>&1; then
        KUSTOMIZE_CMD=(kustomize build)
    elif command -v kubectl >/dev/null 2>&1; then
        KUSTOMIZE_CMD=(kubectl kustomize)
        log "note: 'kustomize' not found, using 'kubectl kustomize' (embedded kustomize version may differ)"
    else
        die "neither 'kustomize' nor 'kubectl' found in PATH"
    fi
}

resolve_guide_dir() {
    local guide="$1"
    case "$guide" in
        */*|.|..) die "'$guide' is not a guide name (expected a directory name under guides/)" ;;
    esac
    [[ -d "${GUIDES_DIR}/${guide}" ]] || die "guide '${guide}' not found under guides/"
    printf '%s\n' "${GUIDES_DIR}/${guide}"
}

resolve_feature_file() {
    local name="$1"
    if [[ -f "${FEATURES_DIR}/${name}.values.yaml" ]]; then
        printf '%s\n' "${FEATURES_DIR}/${name}.values.yaml"
    elif [[ -f "${FEATURES_DIR}/${name}.yaml" ]]; then
        printf '%s\n' "${FEATURES_DIR}/${name}.yaml"
    else
        die "unknown feature '${name}'; available: $(cd "${FEATURES_DIR}" && ls -- *.yaml 2>/dev/null | sed 's/\.values\.yaml$//;s/\.yaml$//' | tr '\n' ' ')"
    fi
}

resolve_chart_ref() {
    local kind="$1" ref
    case "$kind" in
        standalone) ref="${ROUTER_STANDALONE_CHART}" ;;
        gateway)    ref="${ROUTER_GATEWAY_CHART}" ;;
        *) die "internal: unknown chart kind '${kind}'" ;;
    esac
    printf '%s\n' "$ref"
}

resolve_chart_version() {
    if [[ -n "$CHART_VERSION_OVERRIDE" ]]; then
        printf '%s\n' "$CHART_VERSION_OVERRIDE"
    else
        printf '%s\n' "${ROUTER_CHART_VERSION}"
    fi
}

repo_relative() {
    local abs
    abs="$(cd "$1" && pwd)"
    printf '%s\n' "${abs#"${REPO_ROOT}"/}"
}

# Unit name for output files: path relative to the current guide dir when
# under it (modelserver-gpu-vllm-gke), else relative to guides/ or the repo.
unit_name_for_dir() {
    local dir="$1" abs rel
    abs="$(cd "$dir" && pwd)"
    if [[ -n "${GUIDE_DIR}" && "$abs" == "${GUIDE_DIR}"/* ]]; then
        rel="${abs#"${GUIDE_DIR}"/}"
    elif [[ "$abs" == "${GUIDES_DIR}"/* ]]; then
        rel="${abs#"${GUIDES_DIR}"/}"
    elif [[ "$abs" == "${REPO_ROOT}"/* ]]; then
        rel="${abs#"${REPO_ROOT}"/}"
    else
        rel="$(basename "$abs")"
    fi
    printf '%s\n' "${rel//\//-}"
}

# All standalone-buildable kustomization dirs of a guide (absolute paths):
# every kustomization.yaml, excluding kustomize components (not buildable).
list_overlays() {
    local guide_dir="$1" f
    find "$guide_dir" -name kustomization.yaml | sort | while IFS= read -r f; do
        case "$f" in */components/*) continue ;; esac
        grep -Eq '^kind:[[:space:]]*Component[[:space:]]*$' "$f" && continue
        dirname "$f"
    done
}

# emit_unit NAME HEADER_LINE... < rendered-manifests
# In -o mode writes DIR/NAME.yaml with a generated-by header; otherwise
# streams to stdout, inserting a `---` separator between units when the
# next unit does not start with one (helm output does, kustomize's doesn't).
EMITTED=0
emit_unit() {
    local name="$1"; shift
    if [[ ! -s "$TMPFILE" ]]; then
        log "note: '${name}' rendered no resources; skipping"
        return 0
    fi
    if [[ -n "$OUTPUT_DIR" ]]; then
        mkdir -p "$OUTPUT_DIR"
        local out="${OUTPUT_DIR}/${name}.yaml" line
        {
            printf '# Generated by scripts/render.sh -- DO NOT EDIT; regenerate with:\n'
            printf '#   scripts/render.sh %s\n' "${ORIG_ARGS_STR}"
            for line in "$@"; do
                printf '# %s\n' "$line"
            done
            cat "$TMPFILE"
        } > "$out"
        log "wrote ${out}"
    else
        if [[ "$EMITTED" -gt 0 && "$(head -c 3 "$TMPFILE")" != "---" ]]; then
            printf -- '---\n'
        fi
        cat "$TMPFILE"
    fi
    EMITTED=$((EMITTED + 1))
}

# render_router KIND -> renders one chart into TMPFILE and emits it.
# Returns non-zero (without exiting) on helm failure so `all` can continue.
render_router() {
    local kind="$1" chart_ref ver v
    chart_ref="$(resolve_chart_ref "$kind")"
    ver="$(resolve_chart_version)"
    local -a args=(template "$RELEASE" "$chart_ref")
    for v in "${VALUES_FILES[@]}"; do
        args+=(-f "$v")
    done
    args+=(--version "$ver")
    if [[ -n "$NAMESPACE" ]]; then
        args+=(-n "$NAMESPACE")
    fi
    for v in ${SET_ARGS[@]+"${SET_ARGS[@]}"}; do
        args+=(--set "$v")
    done
    if ! helm "${args[@]}" > "$TMPFILE"; then
        return 1
    fi
    local values_desc="" f
    for f in "${VALUES_FILES[@]}"; do
        values_desc="${values_desc}${values_desc:+, }${f#"${REPO_ROOT}"/}"
    done
    emit_unit "router-${kind}" \
        "Chart: ${chart_ref} (version ${ver}), release: ${RELEASE}" \
        "Values: ${values_desc}"
}

# render_overlay DIR -> kustomize build into TMPFILE and emit.
# Returns non-zero (without exiting) on build failure so `all` can continue.
render_overlay() {
    local dir="$1"
    [[ -d "$dir" ]] || die "overlay directory '${dir}' not found"
    [[ -f "${dir}/kustomization.yaml" ]] || die "no kustomization.yaml in '${dir}'"
    if ! "${KUSTOMIZE_CMD[@]}" "$dir" > "$TMPFILE"; then
        return 1
    fi
    emit_unit "$(unit_name_for_dir "$dir")" "Overlay: $(repo_relative "$dir")"
}

# Resolve the router values layering for the current guide into VALUES_FILES:
# base -> features (flag order) -> guide values (or --guide-values) -> extras.
resolve_values_files() {
    local guide="$1" guide_values f
    if [[ -n "$GUIDE_VALUES_OVERRIDE" ]]; then
        guide_values="$GUIDE_VALUES_OVERRIDE"
        [[ -f "$guide_values" ]] || die "values file '${guide_values}' not found"
    else
        guide_values="${GUIDE_DIR}/router/${guide}.values.yaml"
        [[ -f "$guide_values" ]] || die "guide '${guide}' has no router values file ($(repo_relative "${GUIDE_DIR}")/router/${guide}.values.yaml); use 'overlay' or 'all' for kustomize-only guides"
    fi
    VALUES_FILES=("$BASE_VALUES")
    for f in ${FEATURES[@]+"${FEATURES[@]}"}; do
        VALUES_FILES+=("$(resolve_feature_file "$f")")
    done
    VALUES_FILES+=("$guide_values")
    for f in ${EXTRA_VALUES[@]+"${EXTRA_VALUES[@]}"}; do
        [[ -f "$f" ]] || die "values file '${f}' not found"
        VALUES_FILES+=("$f")
    done
}

router_kinds() {
    case "$CHART_MODE" in
        standalone|gateway) printf '%s\n' "$CHART_MODE" ;;
        both) printf 'standalone\ngateway\n' ;;
        *) die "--chart must be standalone, gateway, or both (got '${CHART_MODE}')" ;;
    esac
}

cmd_router() {
    GUIDE_DIR="$(resolve_guide_dir "$TARGET")"
    require_cmd helm
    resolve_values_files "$TARGET"
    RELEASE="${RELEASE:-$TARGET}"
    local kind
    for kind in $(router_kinds); do
        render_router "$kind" || die "helm template failed for chart kind '${kind}'"
    done
}

cmd_overlay() {
    init_kustomize
    render_overlay "$TARGET"
}

cmd_list() {
    GUIDE_DIR="$(resolve_guide_dir "$TARGET")"
    local d
    while IFS= read -r d; do
        repo_relative "$d"
    done < <(list_overlays "$GUIDE_DIR")
}

cmd_all() {
    GUIDE_DIR="$(resolve_guide_dir "$TARGET")"
    init_kustomize
    local rc=0 kind d m

    if [[ -d "${GUIDE_DIR}/router" ]]; then
        require_cmd helm
        resolve_values_files "$TARGET"
        RELEASE="${RELEASE:-$TARGET}"
        for kind in $(router_kinds); do
            if ! render_router "$kind"; then
                log "FAIL: router (${kind})"
                rc=1
            fi
        done
    else
        log "note: guide '${TARGET}' has no router/ directory; skipping router rendering"
    fi

    local -a overlays=()
    if [[ ${#MODELSERVERS[@]} -gt 0 ]]; then
        for m in "${MODELSERVERS[@]}"; do
            d="${GUIDE_DIR}/modelserver/${m}"
            [[ -f "${d}/kustomization.yaml" ]] || die "no kustomization.yaml at $(repo_relative "$GUIDE_DIR")/modelserver/${m}; run 'scripts/render.sh list ${TARGET}' to see available overlays"
            overlays+=("$d")
        done
    else
        while IFS= read -r d; do
            overlays+=("$d")
        done < <(list_overlays "$GUIDE_DIR")
    fi
    for d in ${overlays[@]+"${overlays[@]}"}; do
        if ! render_overlay "$d"; then
            log "FAIL: overlay $(repo_relative "$d")"
            rc=1
        fi
    done
    return "$rc"
}

main() {
    [[ $# -ge 1 ]] || { print_help >&2; exit 1; }
    case "$1" in
        -h|--help) print_help; exit 0 ;;
    esac
    CMD="$1"; shift
    case "$CMD" in
        router|overlay|list|all) ;;
        *) die "unknown command '${CMD}' (see --help)" ;;
    esac

    ORIG_ARGS_STR="${CMD}${*:+ $*}"
    TARGET=""
    CHART_MODE="standalone"
    RELEASE=""
    CHART_VERSION_OVERRIDE=""
    FEATURES=()
    GUIDE_VALUES_OVERRIDE=""
    EXTRA_VALUES=()
    SET_ARGS=()
    NAMESPACE=""
    MODELSERVERS=()
    OUTPUT_DIR=""
    GUIDE_DIR=""
    VALUES_FILES=()
    local router_flag="" ms_flag="" out_flag=""

    req() { [[ $# -eq 2 && -n "$2" ]] || die "option '$1' requires a value"; }

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --chart)        req "$1" "${2:-}"; CHART_MODE="$2"; router_flag="$1"; shift 2 ;;
            --release)      req "$1" "${2:-}"; RELEASE="$2"; router_flag="$1"; shift 2 ;;
            --version)      req "$1" "${2:-}"; CHART_VERSION_OVERRIDE="$2"; router_flag="$1"; shift 2 ;;
            --monitoring)   FEATURES+=("monitoring"); router_flag="$1"; shift ;;
            --feature)      req "$1" "${2:-}"; FEATURES+=("$2"); router_flag="$1"; shift 2 ;;
            --guide-values) req "$1" "${2:-}"; GUIDE_VALUES_OVERRIDE="$2"; router_flag="$1"; shift 2 ;;
            -f|--values)    req "$1" "${2:-}"; EXTRA_VALUES+=("$2"); router_flag="$1"; shift 2 ;;
            --set)          req "$1" "${2:-}"; SET_ARGS+=("$2"); router_flag="$1"; shift 2 ;;
            -n|--namespace) req "$1" "${2:-}"; NAMESPACE="$2"; router_flag="$1"; shift 2 ;;
            --modelserver)  req "$1" "${2:-}"; MODELSERVERS+=("$2"); ms_flag="$1"; shift 2 ;;
            -o|--output)    req "$1" "${2:-}"; OUTPUT_DIR="$2"; out_flag="$1"; shift 2 ;;
            -h|--help)      print_help; exit 0 ;;
            -*)             die "unknown option '$1' (see --help)" ;;
            *)
                [[ -z "$TARGET" ]] || die "unexpected argument '$1'"
                TARGET="$1"; shift ;;
        esac
    done

    [[ -n "$TARGET" ]] || die "command '${CMD}' requires an argument (see --help)"
    case "$CMD" in
        overlay|list)
            [[ -z "$router_flag" ]] || die "option '${router_flag}' is not valid for command '${CMD}'"
            [[ -z "$ms_flag" ]] || die "option '${ms_flag}' is not valid for command '${CMD}'"
            ;;
        router)
            [[ -z "$ms_flag" ]] || die "option '${ms_flag}' is only valid for command 'all'"
            ;;
    esac
    if [[ "$CMD" == "list" && -n "$out_flag" ]]; then
        die "option '${out_flag}' is not valid for command 'list'"
    fi

    TMPFILE="$(mktemp)"
    trap 'rm -f "$TMPFILE"' EXIT

    case "$CMD" in
        router)  cmd_router ;;
        overlay) cmd_overlay ;;
        list)    cmd_list ;;
        all)     cmd_all ;;
    esac
}

main "$@"
