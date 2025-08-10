# Migration from Tekton to GitHub Actions

This document describes the migration from Tekton pipelines to GitHub Actions for building the llm-d Docker image.

## Overview

The original Tekton pipeline (`.tekton/pipelinerun.yaml`) was complex and included multiple tasks for building, testing, and deploying. We've simplified this to two GitHub Action workflows that focus on the core functionality: building the Docker image and scanning for vulnerabilities.

## New GitHub Actions Workflows

### 1. Basic Build Workflow (`build-image.yml`)

**File**: `.github/workflows/build-image.yml`

This is a simplified workflow that:
- Builds the Docker image using Docker Buildx
- Uses the version from `.version.json`
- Includes vulnerability scanning with Trivy
- Uploads scan results to GitHub Security tab

**Triggers**:
- Manual dispatch
- Push to `main` or `dev` branches
- Pull requests to `main` or `dev` branches

### 2. Advanced Build Workflow (`build-image-advanced.yml`)

**File**: `.github/workflows/build-image-advanced.yml`

This workflow provides more options and includes:
- Choice between Docker and Buildah builders
- Configurable Dockerfile selection
- Manual workflow dispatch with input parameters
- Same triggers as the basic workflow

## Key Simplifications

### Removed from Tekton Pipeline:
- Complex permission fixing tasks
- Branch-specific logic
- Cluster name reading
- Submodule updates
- Benchmarking
- OpenShift deployment
- Version incrementing
- Production promotion logic

### Kept in GitHub Actions:
- Docker image building
- Vulnerability scanning with Trivy
- Version management from `.version.json`
- Registry authentication
- Multi-architecture support (amd64)

## Usage

### Automatic Builds
The workflows run automatically on:
- Push to `main` or `dev` branches
- Pull requests to `main` or `dev` branches

### Manual Builds
1. Go to the "Actions" tab in GitHub
2. Select either "Build LLM-D Image" or "Build LLM-D Image (Advanced)"
3. Click "Run workflow"
4. For the advanced workflow, you can choose:
   - Use Buildah instead of Docker
   - Select which Dockerfile to use

## Configuration

### Version Management
The workflows read version information from `.version.json`:
```json
{
  "dev-version": "v0.2.2",
  "dev-registry": "ghcr.io/llm-d/llm-d-dev",
  "prod-version": "v0.2.1",
  "prod-registry": "ghcr.io/llm-d/llm-d"
}
```

### Registry
Images are pushed to GitHub Container Registry (ghcr.io) using the repository name and version.

## Security

### Vulnerability Scanning
- Uses Trivy for container vulnerability scanning
- Scans for CRITICAL, HIGH, and MEDIUM severity issues
- Results are uploaded to GitHub Security tab
- SARIF format for detailed reporting

### Permissions
The workflows use minimal required permissions:
- `contents: read` - to read the repository
- `packages: write` - to push images to registry
- `security-events: write` - to upload security scan results

## Migration Benefits

1. **Simplified**: Removed complex Tekton pipeline logic
2. **Faster**: Direct GitHub Actions execution without Kubernetes overhead
3. **Maintainable**: Standard GitHub Actions syntax and tooling
4. **Integrated**: Native GitHub Security tab integration
5. **Flexible**: Manual dispatch with configurable options

## Next Steps

To complete the migration:

1. **Test the workflows**: Run them manually to ensure they work correctly
2. **Update CI/CD documentation**: Update any references to Tekton pipelines
3. **Remove Tekton files**: Once confirmed working, remove `.tekton/` directory
4. **Update team processes**: Inform team members about the new workflow

## Troubleshooting

### Common Issues

1. **Permission errors**: Ensure the repository has the required permissions for packages and security-events
2. **Build failures**: Check that the Dockerfile and dependencies are correct
3. **Registry authentication**: Verify that the GITHUB_TOKEN has package write permissions

### Debugging

- Check the Actions tab for detailed logs
- Review the Security tab for vulnerability scan results
- Use the manual dispatch feature to test different configurations
