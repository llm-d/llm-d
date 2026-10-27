#!/bin/bash
# Prepare the guides and docs for a release (or restore them for main).
#
# Rewrites every occurrence of:
#   export BRANCH=<value>
#   export branch="<value>"
# in tracked markdown/yaml files under docs/ and guides/, preserving indentation,
# quoting and trailing comments.
#
# Also rewrites the defaults of GATEWAY_API_VERSION, GAIE_VERSION,
# ROUTER_RELEASE_VERSION, ROUTER_CHART_VERSION and ROUTER_EPP_VERSION (see
# guides/env.sh): release-X.Y pins them to concrete upstream releases, main
# restores the floating defaults. Release runs require curl, oras and skopeo.
#
# Usage: scripts/prepare_for_release.sh <release-name>
#   e.g. scripts/prepare_for_release.sh release-0.10
#        scripts/prepare_for_release.sh main
set -Eeuo pipefail

if [[ $# -ne 1 || -z "$1" ]]; then
  echo "Usage: $0 <release-name>  (e.g. release-0.10, main)" >&2
  exit 1
fi

RELEASE="$1"
if [[ "${RELEASE}" != "main" && ! "${RELEASE}" =~ ^release-[0-9]+\.[0-9]+$ ]]; then
  echo "Error: invalid release name '${RELEASE}': expected 'main' or 'release-<major>.<minor>' (e.g. release-0.10)" >&2
  exit 1
fi

REPO_ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
cd "${REPO_ROOT}"

GH_CURL=(curl -fsSL ${GITHUB_TOKEN:+-H "Authorization: Bearer ${GITHUB_TOKEN}"})

# latest_release <github-repo>
# Prints the tag of the latest release of <github-repo>.
latest_release() {
  local tag
  tag=$("${GH_CURL[@]}" "https://api.github.com/repos/$1/releases/latest" \
    | sed -nE 's/^[[:space:]]*"tag_name":[[:space:]]*"([^"]+)".*/\1/p')
  if [[ ! "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
    echo "Error: could not determine latest $1 release (got '${tag}')" >&2
    return 1
  fi
  printf '%s\n' "${tag}"
}

# match_tag <release-X.Y> <tags>
# Prints the newest stable vX.Y.Z tag from the newline-separated <tags>.
match_tag() {
  local version="${1#release-}" tag
  tag=$(grep -E "^v${version//./\\.}\.[0-9]+$" <<<"$2" | sort -V | tail -n1 || true)
  [[ -n "${tag}" ]] && printf '%s\n' "${tag}"
}

# matching_release <github-repo> <release-name>
# Prints the release tag of <github-repo> matching <release-name> (see match_tag).
matching_release() {
  local repo="$1" name="$2" tags
  if ! tags=$("${GH_CURL[@]}" "https://api.github.com/repos/${repo}/releases?per_page=100"); then
    echo "Error: could not list releases of ${repo}" >&2
    return 1
  fi
  tags=$(sed -nE 's/^[[:space:]]*"tag_name":[[:space:]]*"([^"]+)".*/\1/p' <<<"${tags}")
  match_tag "${name}" "${tags}" || {
    echo "Error: no ${repo} release matches '${name}'" >&2
    return 1
  }
}

# matching_chart <oci-repo> <release-name>
# Prints the tag of the OCI chart <oci-repo> matching <release-name>
# (see match_tag).
matching_chart() {
  local repo="$1" name="$2" tags
  if ! tags=$(oras repo tags "${repo}"); then
    echo "Error: could not list tags of ${repo}" >&2
    return 1
  fi
  match_tag "${name}" "${tags}" || {
    echo "Error: no oci://${repo} chart version matches '${name}'" >&2
    return 1
  }
}

# matching_image <image-repo> <release-name>
# Prints the tag of the container image <image-repo> matching <release-name>
# (see match_tag).
matching_image() {
  local repo="$1" name="$2" tags
  if ! tags=$(skopeo list-tags "docker://${repo}"); then
    echo "Error: could not list tags of ${repo}" >&2
    return 1
  fi
  tags=$(sed -nE 's/^[[:space:]]*"([^"]+)",?$/\1/p' <<<"${tags}")
  match_tag "${name}" "${tags}" || {
    echo "Error: no ${repo} image tag matches '${name}'" >&2
    return 1
  }
}

# pin_files <VAR_NAME>
# Prints the tracked files containing
#   export <VAR_NAME>=${<VAR_NAME>:-<value>}
pin_files() {
  git ls-files | xargs -r grep -lE "^[[:space:]]*export $1=\\\$\\{$1:-" || true
}

# pin_version <VAR_NAME> <value>
# Rewrites every tracked
#   export <VAR_NAME>=${<VAR_NAME>:-<old>}
# so the default becomes <value>.
pin_version() {
  local var="$1" tag="$2" f
  local -a files
  mapfile -t files < <(pin_files "${var}")
  for f in "${files[@]}"; do
    sed -i -E "s/^([[:space:]]*export ${var}=\\\$\\{${var}:-)[^}]*\}/\1${tag}}/" "$f"
  done
  echo "Pinned ${var} to '${tag}' in:"
  printf '  %s\n' "${files[@]}"
}

### Phase 1: resolve every version and check every target exists.
# Nothing is modified until all of these succeed.
# For release branches, pin CRD, router, router chart and EPP image versions to
# concrete upstream releases instead of resolving "latest" at install time.
# main restores the floating defaults: "latest" releases, the v0 chart and the
# main EPP image.
abort() {
  echo "Error: $1; no files were modified." >&2
  exit 1
}

PIN_VARS=(GATEWAY_API_VERSION GAIE_VERSION ROUTER_RELEASE_VERSION ROUTER_CHART_VERSION ROUTER_EPP_VERSION)
declare -A PIN_VALUES
if [[ "${RELEASE}" != "main" ]]; then
  PIN_VALUES[GATEWAY_API_VERSION]=$(latest_release kubernetes-sigs/gateway-api) \
    || abort "could not resolve GATEWAY_API_VERSION"
  PIN_VALUES[GAIE_VERSION]=$(latest_release kubernetes-sigs/gateway-api-inference-extension) \
    || abort "could not resolve GAIE_VERSION"
  PIN_VALUES[ROUTER_RELEASE_VERSION]=$(matching_release llm-d/llm-d-router "${RELEASE}") \
    || abort "could not resolve ROUTER_RELEASE_VERSION"
  PIN_VALUES[ROUTER_CHART_VERSION]=$(matching_chart ghcr.io/llm-d/charts/llm-d-router-standalone "${RELEASE}") \
    || abort "could not resolve ROUTER_CHART_VERSION"
  PIN_VALUES[ROUTER_EPP_VERSION]=$(matching_image ghcr.io/llm-d/llm-d-router-endpoint-picker "${RELEASE}") \
    || abort "could not resolve ROUTER_EPP_VERSION"
else
  PIN_VALUES=(
    [GATEWAY_API_VERSION]=latest
    [GAIE_VERSION]=latest
    [ROUTER_RELEASE_VERSION]=latest
    [ROUTER_CHART_VERSION]=v0
    [ROUTER_EPP_VERSION]=main
  )
fi

for var in "${PIN_VARS[@]}"; do
  [[ -n "$(pin_files "${var}")" ]] \
    || abort "no tracked file contains 'export ${var}=\${${var}:-...}'"
done

### Phase 2: apply the changes.
for var in "${PIN_VARS[@]}"; do
  pin_version "${var}" "${PIN_VALUES[${var}]}"
done

# Escape '/' and '&' for use in the sed replacement.
ESCAPED=$(printf '%s' "${RELEASE}" | sed -e 's/[\/&]/\\&/g')

mapfile -t FILES < <(git ls-files -- 'docs/*.md' 'guides/*.md' 'guides/*.yaml' 'guides/*.yml' \
  | xargs -r grep -lE 'export (BRANCH=|branch=")' || true)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No branch entries found."
  exit 0
fi

for f in "${FILES[@]}"; do
  sed -i -E \
    -e "s/^([[:space:]]*export BRANCH=)[^[:space:]#]+/\1${ESCAPED}/" \
    -e "s/^([[:space:]]*export branch=\")[^\"]*\"/\1${ESCAPED}\"/" \
    "$f"
done

echo "Updated branch to '${RELEASE}' in:"
git diff --name-only -- "${FILES[@]}" | sed 's/^/  /'
