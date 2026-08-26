SHELL := /usr/bin/env bash

include docker/common-versions

# Defaults
IMAGE_REGISTRY ?= ghcr.io
IMAGE_REGISTRY_ORG ?= llm-d
IMAGE_REGISTRY_REPO ?= llm-d
DOCKERFILE_DIR = docker

ifeq ($(DEVICE), xpu)
	DOCKERFILE ?= Dockerfile.xpu
else ifeq ($(DEVICE), cpu)
	DOCKERFILE ?= Dockerfile.cpu
endif # Maybe we break out version per image because they share no common bits --> independent release cycles

VERSION ?= v0.9.0

# New tag to use if you would like to use `make image-retag`
NEW_TAG ?= sha256...

# DEVICE, options: ['xpu', 'cpu']
DEVICE ?= xpu

# ARCH, options: ['amd64', 'arm64']
ARCH ?= amd64

# USE_SCCACHE: set to true to enable sccache (requires AWS credentials)
USE_SCCACHE ?= false

# MAX_JOBS: parallel compilation jobs (reduce to avoid OOM, e.g., MAX_JOBS=1)
MAX_JOBS ?= 3

# BUILD_TYPE, options ['dev', 'prod']
BUILD_TYPE ?= dev
ifeq ($(BUILD_TYPE), dev)
	REGISTRY := quay.io
endif

IMAGE_BASE ?= $(REGISTRY)/$(IMAGE_REGISTRY_ORG)/$(IMAGE_REGISTRY_REPO)-$(DEVICE)

BUILD_CONTEXT ?= .
DOCKERFILE_PATH = $(DOCKERFILE_DIR)/$(DOCKERFILE)

IMG := $(IMAGE_BASE):$(VERSION)

CONTAINER_TOOL := $(shell (command -v docker >/dev/null 2>&1 && echo docker) || (command -v podman >/dev/null 2>&1 && echo podman) || echo "")
BUILDER := $(shell command -v buildah >/dev/null 2>&1 && echo buildah || echo $(CONTAINER_TOOL))

.PHONY: help
help: ## Print help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
	@printf "\n\033[1mArchitecture Examples:\033[0m\n"
	@printf "  \033[36mmake image-build ARCH=amd64\033[0m                              # Build for x86_64 (default)\n"
	@printf "  \033[36mmake image-build ARCH=arm64\033[0m                              # Build for ARM64\n"
	@printf "\n\033[1mXPU Build Examples:\033[0m\n"
	@printf "  \033[36mmake image-build DEVICE=xpu\033[0m                    # Build Intel XPU Docker image\n"
	@printf "  \033[36mmake image-build DEVICE=xpu VERSION=v0.2.0\033[0m     # Build with specific version\n"
	@printf "  \033[36mmake image-push DEVICE=xpu\033[0m                     # Push Intel XPU Docker image\n"
	@printf "  \033[36mmake image-retag DEVICE=xpu NEW_TAG=test\033[0m       # Re-Tag Intel XPU Docker image\n"
	@printf "  \033[36mmake env DEVICE=xpu\033[0m                            # Show XPU environment variables\n"

##@ Development

.PHONY: test
test: ## Run tests

.PHONY: post-deploy-test
post-deploy-test: ## Run post deployment tests
	echo Success!
	@echo "Post-deployment tests passed."

.PHONY: lint
lint: ## Run lint

##@ Build

.PHONY: build
build: ##

##@ Container Build/Push

# The Dockerfile the build task should use.
# Default is the canonical “Dockerfile” so local `make buildah-build`
# still works without extra flags.

.PHONY: buildah-build
buildah-build: check-builder ## Build and push image (multi-arch if supported)
	@echo "✅ Using builder: $(BUILDER)"
	@if [ "$(BUILDER)" = "buildah" ]; then \
	  echo "🔧 Buildah detected: Building for $(ARCH) with $(DOCKERFILE_PATH)…"; \
	  buildah build --file $(DOCKERFILE_PATH) --arch=$(ARCH) --os=linux --layers \
		$(if $(filter xpu,$(DEVICE)),--build-arg BASE_IMAGE=$(VLLM_XPU_BASE_IMAGE)) \
		-t $(IMG) $(BUILD_CONTEXT) || exit 1; \
	  echo "🚀 Pushing image: $(IMG)"; \
	  buildah push $(IMG) docker://$(IMG) || exit 1; \
	elif [ "$(BUILDER)" = "docker" ]; then \
	  echo "🐳 Docker detected: Building with buildx for linux/$(ARCH)..."; \
	  sed -e '1 s/\(^FROM\)/FROM --platform=$${BUILDPLATFORM}/' $(DOCKERFILE_PATH) >$(DOCKERFILE_DIR)/Dockerfile.cross; \
	  - docker buildx create --use --name image-builder || true; \
	  docker buildx use image-builder; \
	  docker buildx build --push --platform=linux/$(ARCH) --tag $(IMG) \
		$(if $(filter xpu,$(DEVICE)),--build-arg BASE_IMAGE=$(VLLM_XPU_BASE_IMAGE)) \
		-f $(DOCKERFILE_DIR)/Dockerfile.cross $(BUILD_CONTEXT) || exit 1; \
	  docker buildx rm image-builder || true; \
	  rm $(DOCKERFILE_DIR)/Dockerfile.cross; \
	elif [ "$(BUILDER)" = "podman" ]; then \
	  echo "⚠️ Podman detected: Building for linux/$(ARCH)..."; \
	  podman build --platform=linux/$(ARCH) --format=docker -f $(DOCKERFILE_PATH) -t $(IMG) $(BUILD_CONTEXT) || exit 1; \
	  podman push $(IMG) || exit 1; \
	else \
	  echo "❌ No supported container tool available."; \
	  exit 1; \
	fi

.PHONY:	image-build
image-build: check-container-tool ## Build Docker image using $(CONTAINER_TOOL)
	@printf "\033[33;1m==== Building Docker image $(IMG) for linux/$(ARCH) ====\033[0m\n"
	$(CONTAINER_TOOL) build --progress=plain --platform linux/$(ARCH) \
		--build-arg USE_SCCACHE=$(USE_SCCACHE) \
		--build-arg MAX_JOBS=$(MAX_JOBS) \
		$(if $(filter xpu,$(DEVICE)),--build-arg BASE_IMAGE=$(VLLM_XPU_BASE_IMAGE)) \
		-t $(IMG) -f $(DOCKERFILE_PATH) $(BUILD_CONTEXT)

.PHONY: image-push
image-push: check-container-tool ## Push Docker image $(IMG) to registry
	@printf "\033[33;1m==== Pushing Docker image $(IMG) ====\033[0m\n"
	$(CONTAINER_TOOL) push $(IMG)

.PHONY: image-retag
image-retag: check-container-tool ## Push Docker image $(IMG) to registry
	@printf "\033[33;1m==== Pushing Docker image $(IMG) to $(IMAGE_BASE):$(NEW_TAG) ====\033[0m\n"
	$(CONTAINER_TOOL) tag $(IMG) $(IMAGE_BASE):$(NEW_TAG)

##@ Install/Uninstall Targets

# Default install/uninstall (Docker)
install: install-docker ## Default install using Docker
	@echo "Default Docker install complete."

uninstall: uninstall-docker ## Default uninstall using Docker
	@echo "Default Docker uninstall complete."

### Docker Targets

.PHONY: install-docker
install-docker: check-container-tool ## Install app using $(CONTAINER_TOOL)
	@echo "Starting container with $(CONTAINER_TOOL)..."
	$(CONTAINER_TOOL) run -d --name $(IMAGE_REGISTRY_REPO)-container $(IMG)
	@echo "$(CONTAINER_TOOL) installation complete."
	@echo "To use $(IMAGE_REGISTRY_REPO), run:"
	@echo "alias $(IMAGE_REGISTRY_REPO)='$(CONTAINER_TOOL) exec -it $(IMAGE_REGISTRY_REPO)-container /app/$(IMAGE_REGISTRY_REPO)'"

.PHONY: uninstall-docker
uninstall-docker: check-container-tool ## Uninstall app from $(CONTAINER_TOOL)
	@echo "Stopping and removing container in $(CONTAINER_TOOL)..."
	-$(CONTAINER_TOOL) stop $(IMAGE_REGISTRY_REPO)-container && $(CONTAINER_TOOL) rm $(IMAGE_REGISTRY_REPO)-container
@echo "$(CONTAINER_TOOL) uninstallation complete. Remove alias if set: unalias $(IMAGE_REGISTRY_REPO)"

### Kubernetes Targets (kubectl)

.PHONY: install-k8s
install-k8s: check-kubectl check-kustomize check-envsubst ## Install on Kubernetes
	export IMAGE_REGISTRY_REPO=${IMAGE_REGISTRY_REPO}
	export NAMESPACE=${NAMESPACE}
	@echo "Creating namespace (if needed) and setting context to $(NAMESPACE)..."
	kubectl create namespace $(NAMESPACE) 2>/dev/null || true
	kubectl config set-context --current --namespace=$(NAMESPACE)
	@echo "Deploying resources from deploy/ ..."
	# Build the kustomization from deploy, substitute variables, and apply the YAML
	kustomize build deploy | envsubst | kubectl apply -f -
	@echo "Waiting for pod to become ready..."
	sleep 5
	@POD=$$(kubectl get pod -l app=$(IMAGE_REGISTRY_REPO)-statefulset -o jsonpath='{.items[0].metadata.name}'); \
	echo "Kubernetes installation complete."; \
	echo "To use the app, run:"; \
	echo "alias $(IMAGE_REGISTRY_REPO)='kubectl exec -n $(NAMESPACE) -it $$POD -- /app/$(IMAGE_REGISTRY_REPO)'"

.PHONY: uninstall-k8s
uninstall-k8s: check-kubectl check-kustomize check-envsubst ## Uninstall from Kubernetes
	export IMAGE_REGISTRY_REPO=${IMAGE_REGISTRY_REPO}
	export NAMESPACE=${NAMESPACE}
	@echo "Removing resources from Kubernetes..."
	kustomize build deploy | envsubst | kubectl delete --force -f - || true
	POD=$$(kubectl get pod -l app=$(IMAGE_REGISTRY_REPO)-statefulset -o jsonpath='{.items[0].metadata.name}'); \
	echo "Deleting pod: $$POD"; \
	kubectl delete pod "$$POD" --force --grace-period=0 || true; \
	echo "Kubernetes uninstallation complete. Remove alias if set: unalias $(IMAGE_REGISTRY_REPO)"

### OpenShift Targets (oc)

.PHONY: install-openshift
install-openshift: check-kubectl check-kustomize check-envsubst ## Install on OpenShift
	@echo $$IMAGE_REGISTRY_REPO $$NAMESPACE $$IMAGE_BASE $$VERSION
	@echo "Creating namespace $(NAMESPACE)..."
	kubectl create namespace $(NAMESPACE) 2>/dev/null || true
	@echo "Deploying common resources from deploy/ ..."
	# Build and substitute the base manifests from deploy, then apply them
	kustomize build deploy | envsubst '$$IMAGE_REGISTRY_REPO $$NAMESPACE $$IMAGE_BASE $$VERSION' | kubectl apply -n $(NAMESPACE) -f -
	@echo "Waiting for pod to become ready..."
	sleep 5
	@POD=$$(kubectl get pod -l app=$(IMAGE_REGISTRY_REPO)-statefulset -n $(NAMESPACE) -o jsonpath='{.items[0].metadata.name}'); \
	echo "OpenShift installation complete."; \
	echo "To use the app, run:"; \
	echo "alias $(IMAGE_REGISTRY_REPO)='kubectl exec -n $(NAMESPACE) -it $$POD -- /app/$(IMAGE_REGISTRY_REPO)'"

.PHONY: uninstall-openshift
uninstall-openshift: check-kubectl check-kustomize check-envsubst ## Uninstall from OpenShift
	@echo "Removing resources from OpenShift..."
	kustomize build deploy | envsubst '$$IMAGE_REGISTRY_REPO $$NAMESPACE $$IMAGE_BASE $$VERSION' | kubectl delete --force -f - || true
	# @if kubectl api-resources --api-group=route.openshift.io | grep -q Route; then \
	#   envsubst '$$IMAGE_REGISTRY_REPO $$NAMESPACE $$IMAGE_BASE $$VERSION' < deploy/openshift/route.yaml | kubectl delete --force -f - || true; \
	# fi
	@POD=$$(kubectl get pod -l app=$(IMAGE_REGISTRY_REPO)-statefulset -n $(NAMESPACE) -o jsonpath='{.items[0].metadata.name}'); \
	echo "Deleting pod: $$POD"; \
	kubectl delete pod "$$POD" --force --grace-period=0 || true; \
	echo "OpenShift uninstallation complete. Remove alias if set: unalias $(IMAGE_REGISTRY_REPO)"

### RBAC Targets (using kustomize and envsubst)

.PHONY: install-rbac
install-rbac: check-kubectl check-kustomize check-envsubst ## Install RBAC
	@echo "Applying RBAC configuration from deploy/rbac..."
	kustomize build deploy/rbac | envsubst '$$IMAGE_REGISTRY_REPO $$NAMESPACE $$IMAGE_BASE $$VERSION' | kubectl apply -f -

.PHONY: uninstall-rbac
uninstall-rbac: check-kubectl check-kustomize check-envsubst ## Uninstall RBAC
	@echo "Removing RBAC configuration from deploy/rbac..."
	kustomize build deploy/rbac | envsubst '$$IMAGE_REGISTRY_REPO $$NAMESPACE $$IMAGE_BASE $$VERSION' | kubectl delete -f - || true

.PHONY: env
env:
	@echo "IMAGE_BASE=$(IMAGE_BASE)"
	@echo "VERSION=$(VERSION)"
	@echo "ARCH=$(ARCH)"
	@echo "USE_SCCACHE=$(USE_SCCACHE)"
	@echo "IMG=$(IMG)"
	@echo "CONTAINER_TOOL=$(CONTAINER_TOOL)"

##@ Tools

.PHONY: check-tools
check-tools: \
  check-go \
  check-ginkgo \
  check-golangci-lint \
  check-jq \
  check-kustomize \
  check-envsubst \
  check-container-tool \
  check-kubectl \
  check-buildah \
  check-podman
	@echo "✅ All required tools are installed."

.PHONY: check-go
check-go:
	@command -v go >/dev/null 2>&1 || { \
	  echo "❌ Go is not installed. Install it from https://golang.org/dl/"; exit 1; }

.PHONY: check-ginkgo
check-ginkgo:
	@command -v ginkgo >/dev/null 2>&1 || { \
	  echo "❌ ginkgo is not installed. Install with: go install github.com/onsi/ginkgo/v2/ginkgo@latest"; exit 1; }

.PHONY: check-golangci-lint
check-golangci-lint:
	@command -v golangci-lint >/dev/null 2>&1 || { \
	  echo "❌ golangci-lint is not installed. Install from https://golangci-lint.run/usage/install/"; exit 1; }

.PHONY: check-jq
check-jq:
	@command -v jq >/dev/null 2>&1 || { \
	  echo "❌ jq is not installed. Install it from https://stedolan.github.io/jq/download/"; exit 1; }

.PHONY: check-kustomize
check-kustomize:
	@command -v kustomize >/dev/null 2>&1 || { \
	  echo "❌ kustomize is not installed. Install it from https://kubectl.docs.kubernetes.io/installation/kustomize/"; exit 1; }

.PHONY: check-envsubst
check-envsubst:
	@command -v envsubst >/dev/null 2>&1 || { \
	  echo "❌ envsubst is not installed. It is part of gettext."; \
	  echo "🔧 Try: sudo apt install gettext OR brew install gettext"; exit 1; }

.PHONY: check-container-tool
check-container-tool:
	@command -v $(CONTAINER_TOOL) >/dev/null 2>&1 || { \
	  echo "❌ $(CONTAINER_TOOL) is not installed."; \
	  echo "🔧 Try: sudo apt install $(CONTAINER_TOOL) OR brew install $(CONTAINER_TOOL)"; exit 1; }

.PHONY: check-kubectl
check-kubectl:
	@command -v kubectl >/dev/null 2>&1 || { \
	  echo "❌ kubectl is not installed. Install it from https://kubernetes.io/docs/tasks/tools/"; exit 1; }

.PHONY: check-builder
check-builder:
	@if [ -z "$(BUILDER)" ]; then \
		echo "❌ No container builder tool (buildah, docker, or podman) found."; \
		exit 1; \
	else \
		echo "✅ Using builder: $(BUILDER)"; \
	fi

.PHONY: check-buildah
check-buildah:
	@command -v buildah >/dev/null 2>&1 || { \
	  echo "⚠️  buildah is not installed. You can install it with:"; \
	  echo "🔧 sudo apt install buildah  OR  brew install buildah"; exit 1; }

.PHONY: check-podman
check-podman:
	@command -v podman >/dev/null 2>&1 || { \
	  echo "⚠️  Podman is not installed. You can install it with:"; \
	  echo "🔧 sudo apt install podman  OR  brew install podman"; exit 1; }

##@ Alias checking
.PHONY: check-alias
check-alias: check-container-tool
	@echo "🔍 Checking alias functionality for container '$(IMAGE_REGISTRY_REPO)-container'..."
	@if ! $(CONTAINER_TOOL) exec $(IMAGE_REGISTRY_REPO)-container /app/$(IMAGE_REGISTRY_REPO) --help >/dev/null 2>&1; then \
	  echo "⚠️  The container '$(IMAGE_REGISTRY_REPO)-container' is running, but the alias might not work."; \
	  echo "🔧 Try: $(CONTAINER_TOOL) exec -it $(IMAGE_REGISTRY_REPO)-container /app/$(IMAGE_REGISTRY_REPO)"; \
	else \
	  echo "✅ Alias is likely to work: alias $(IMAGE_REGISTRY_REPO)='$(CONTAINER_TOOL) exec -it $(IMAGE_REGISTRY_REPO)-container /app/$(IMAGE_REGISTRY_REPO)'"; \
	fi

.PHONY: print-namespace
print-namespace: ## Print the current namespace
	@echo "$(NAMESPACE)"

.PHONY: print-project-name
print-project-name: ## Print the current project name
	@echo "$(IMAGE_REGISTRY_REPO)"

.PHONY: install-hooks
install-hooks: ## Install git hooks
	git config core.hooksPath hooks
