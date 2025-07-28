# Distributed Documentation Aggregation Strategy

##  Summary
We need to automatically collect documentation from multiple llm-d repositories into our main website. Currently, we manually configure each file, which doesn't scale as we add more repositories and components.

## Background
Our documentation website (llm-d.ai) is built using Docusaurus, a documentation platform that generates websites from Markdown files. Currently, we use a plugin called `docusaurus-plugin-remote-content` to pull documentation files from different GitHub repositories and display them on our unified website.

## Problem Statement

The llm-d ecosystem spans multiple repositories, each containing valuable technical documentation that should be accessible through our [central documentation site](https://llm-d.ai/). Currently, we manually specify individual files using the `docusaurus-plugin-remote-content`, which creates several challenges:

- **Scalability Issues**: Each new documentation file requires manual configuration and deployment
- **Maintenance Overhead**: Changes to repository structures require updates to our remote content configuration
- **Discovery Problems**: New documentation in repositories may go unnoticed and be difficult for users to find
- **Inconsistent Experience**: Users must know which repository contains specific documentation

This problem becomes more acute as our project ecosystem expands and we need to serve documentation to developers across multiple sub-projects efficiently.

## Proposed Solution

This proposal outlines a comprehensive strategy for automatically aggregating and rendering documentation from multiple llm-d/llm-d-incubation open source repositories into a unified documentation site. The current approach of individually specifying each remote file does not scale effectively as our project ecosystem grows across multiple repositories.

## Benefits

- **Standardized Structure**: Use `docs/` folder as the default location for all documentation
- **Automatic Integration**: New repositories automatically include their documentation without manual setup
- **Automated Discovery**: Automatically detect and aggregate all Markdown documentation from the standard `docs/` directory
- **Flexible Overrides**: Support custom configuration paths for repositories with non-standard structures
- **Developer-Friendly**: Make contributing documentation as simple as placing files in the `docs/` directory
- **Source Attribution**: Clearly indicate the source repository and provide edit links for each document(similar to [existing documents](https://llm-d.ai/docs/community/contribute))
- **Support Versioned Docs**: Docusaurus supports multiple versions of docs, and we should sync both latest and specific tagged versions

## Non-Goals

- Real-time synchronization (build-time aggregation is sufficient, we could also have a GitHub action on a cron schedule for daily builds)
- Documentation format conversion beyond Markdown
- Replacing existing individual file remote content for special cases

## Requirements & Constraints

### Core Requirements
1. Auto-scan all configured repositories for `docs/` directories (zero-config)
2. Mirror folder structure exactly in website sidebar
3. Add minimal Docusaurus frontmatter (metadata headers that control how pages appear) and source attribution
4. Build-time integration with existing remote content system
5. Support custom configurations for edge cases
6. Automatically discover and fetch image assets from `docs/assets/images/` directories
7. Rewrite image paths in markdown content to work on aggregated site

### Technical Constraints
1. Must respect GitHub API rate limits
2. Work within existing Docusaurus + `docusaurus-plugin-remote-content` architecture
3. Maintain backward compatibility with current individual file configurations

## Scope

**In Scope**: Auto-discovery of Markdown files from `docs/` directories, folder structure mirroring, minimal content transformation, zero-config setup with custom override support, multi-repository versioning support

**Out of Scope**: Real-time sync, non-Markdown formats, automatic repository discovery

## Versioning Strategy

Our project has multiple versions (v0.2 and latest), and each component repository is expected to use the same tag for each release version (e.g., all components should have a `v0.2` tag for the 0.2 release). While the system can support cases where a component uses a different tag for a given release, this should be considered an edge case rather than the norm. The system will:

1. **Collect documentation for each version**: Pull docs from the appropriate git tags/branches for each version
2. **Handle missing versions gracefully**: If a repository doesn't have a specific version tag, skip it for that version
3. **Present standard version navigation**: Users see a normal version dropdown (latest, 0.2) like other documentation sites

**Example**: For version 0.2, the system pulls documentation from the `v0.2` tag in each repository and creates a separate documentation tree under `/docs/0.2/`.

## Website Structure

### Current vs. Proposed Navigation
**Current**: `What is llm-d? | User Guide | Community | News`  
**Proposed**: `What is llm-d? | Start Here | Components | Community | News`

### Individual Repository Documentation Structure
Here are examples of what component repositories would contain in their `docs/` folder (these are sample structures for illustration):

```
Repository: llm-d/llm-d-inference-scheduler (example)
docs/
├── README.md                    # Overview and quick start
├── assets/
│   └── images/                  # Store all images here
│       ├── architecture.png
│       ├── setup-flow.jpg
│       └── api/
│           └── request-flow.svg
├── installation/
│   ├── requirements.md
│   └── setup.md
├── configuration/
│   ├── basic.md
│   └── advanced.md
├── api/
│   ├── endpoints.md
│   └── authentication.md
└── troubleshooting.md

Repository: llm-d/llm-d-kv-cache-manager (example)
docs/
├── README.md
├── assets/
│   └── images/                  # Store all images here
│       ├── cache-diagram.png
│       ├── performance/
│       │   └── benchmark-results.png
│       └── monitoring/
│           └── metrics-dashboard.png
├── architecture.md
├── configuration/
│   └── cache-settings.md
├── performance/
│   ├── benchmarks.md
│   └── tuning.md
└── monitoring/
    └── metrics.md
```

### Unified Website Structure (Latest Version)
The system automatically combines all repository documentation into this structure (using example repositories for illustration):

```
llm-d.ai website structure:
├── What is llm-d?               # Manual content (unchanged)
│   ├── Architecture
│   └── Overview
├── Start Here                   # Points to llm-d-incubation/llm-d-infra for onboarding
│   ├── Quick Start              # Sourced from llm-d-infra project
│   └── Well-Lit Paths           # Sourced from llm-d-infra project
├── Components/                  # Auto-generated from repositories
│   ├── llm-d-inference-scheduler/  # From llm-d/llm-d-inference-scheduler/docs/
│   │   ├── README
│   │   ├── installation/
│   │   │   ├── requirements
│   │   │   └── setup
│   │   ├── configuration/
│   │   │   ├── basic
│   │   │   └── advanced
│   │   ├── api/
│   │   │   ├── endpoints
│   │   │   └── authentication
│   │   └── troubleshooting
│   └── llm-d-kv-cache-manager/     # From llm-d/llm-d-kv-cache-manager/docs/
│       ├── README
│       ├── architecture
│       ├── configuration/
│       │   └── cache-settings
│       ├── performance/
│       │   ├── benchmarks
│       │   └── tuning
│       └── monitoring/
│           └── metrics
├── Community                    # (unchanged)
└── News                         # (unchanged)
```

### Versioned Website Structure
The system creates separate documentation trees for each version per the requirements for Docusaurus versioning:

```
llm-d.ai with versions:
├── /docs/latest/                       # Latest development version
│   ├── start-here/
│   └── components/
│       ├── llm-d-inference-scheduler/  # From main branch
│       └── llm-d-kv-cache-manager/     # From main branch
└── /docs/0.2/                         # Version 0.2 release
    ├── start-here/
    └── components/
        ├── llm-d-inference-scheduler/  # From v0.2 tag
        └── llm-d-kv-cache-manager/     # From v0.2 tag
        # Note: If a repository doesn't have v0.2 tag, it's skipped for that version
```

### User Experience
Users see a familiar documentation site with:
- **Version dropdown**: Latest, 0.2
- **Automatic navigation**: Folder structure becomes sidebar menu
- **Source links**: Each page shows "Edit this page" linking back to the original repository
- **Consistent URLs**: `/docs/latest/components/llm-d-inference-scheduler/api/endpoints`

### Developer Workflow
1. **Create documentation**: Add markdown files to `docs/` folder in any component repository
2. **Organize content**: Use folders to create logical groupings (e.g., `installation/`, `api/`, `troubleshooting/`)
3. **Commit changes**: Standard git workflow - commit and push to main branch
4. **Automatic inclusion**: Documentation appears on next website build (daily automated builds)
5. **No configuration needed**: System automatically discovers and includes new content

### Documentation Standards for llm-d Contributors

To ensure your repository documentation renders correctly on the llm-d website, please follow these guidelines when creating markdown files in your `docs/` directory:

**✅ Recommended Practices**

**HTML Elements**: Use standard markdown syntax instead of HTML when possible. If you must use HTML tags, ensure they are self-closing:
```markdown
<!-- Good -->
<img src="image.png" alt="Description" />

<!-- Avoid -->
<img src="image.png" alt="Description">
```

**Internal Links**: Reference other documentation files by their relative paths within your repository's `docs/` folder:
```markdown
[Configuration Guide](./configuration/basic.md)
[API Reference](../api/endpoints.md)
```

**External Repository References**: When linking to files in other llm-d repositories, use full GitHub URLs:
```markdown
[llm-d Architecture](https://github.com/llm-d/llm-d/blob/main/docs/architecture.md)
```

**🖼️ Image Assets**: Store and reference images using standardized paths to ensure proper aggregation:
```markdown
<!-- Store images in docs/assets/images/ directory -->
![Architecture Diagram](./assets/images/architecture.png)
![Setup Screenshot](../assets/images/setup-flow.jpg)

<!-- For images in subdirectories, maintain relative paths -->
![API Flow](./assets/images/api/request-flow.svg)
```

**📁 File Organization**
- Place all documentation in your repository's `docs/` directory
- Store images in `docs/assets/images/` directory (required for automatic fetching)
- Use descriptive filenames (they become URL slugs)
- Organize subdirectories logically (they preserve hierarchy on the website)
- Include a README.md in your repository root for component overview
- Supported image formats: `.png`, `.jpg`, `.jpeg`, `.svg`, `.gif`, `.webp`

**🔗 Cross-References**
- Link to other sections within your component's documentation freely
- For links to other llm-d components, use the format: `[Component Name](/docs/components/repo-name/page)`
- For links to general llm-d documentation, use: `[Guide Name](/docs/guide/page-name)`

**⚠️ Elements That May Need Adjustment**
- Complex HTML structures may not render correctly
- Embedded videos or interactive content should use standard markdown image/link syntax
- Tables with complex formatting should use standard markdown table syntax
- Code blocks should specify language for proper syntax highlighting
- **Images outside `docs/assets/images/`** will not be automatically fetched (use external URLs for these)
- **Absolute image paths** should be avoided - use relative paths from markdown file location
- **External images** (URLs to other domains) will work but may have loading/availability issues

Following these standards ensures your documentation automatically appears correctly on the llm-d website without requiring manual intervention from the documentation team.

### Core Goals & Scope
**Primary Goals (In Scope)**:
- Standardize on `docs/` directory structure across all component repositories
- Automatically sync documentation to versioned Docusaurus website (latest and v0.2 tags)
- Maintain zero-configuration setup for new repositories

**Future Enhancements (Out of Scope for Initial Implementation)**:
- Custom sidebar ordering and organization strategies
- Advanced content organization beyond folder structure mirroring

*Note: While the system supports sidebar customization capabilities, the initial focus is on documentation synchronization and standardization. Content organization strategies will be addressed in future phases.*

## Alternatives Considered

### Manual File Specification (Current Approach)
**Pros**: Full control, simple implementation
**Cons**: Does not scale, high maintenance overhead, prone to missing new content

### Git Submodules
**Pros**: Native git integration, local file access
**Cons**: Complex dependency management, synchronization challenges, repository coupling

### Monorepo Documentation
**Pros**: Single source of truth, easy cross-references
**Cons**: Violates project separation, difficult to maintain across teams

## Appendix: Technical Implementation Details

### Repository Configuration
```javascript
const documentationSources = {
  versions: ['latest', '0.2'],
  autoDiscovery: [
    'llm-d/llm-d-inference-scheduler',
    'llm-d/llm-d-kv-cache-manager'
  ],
  customConfigurations: {
    'llm-d/llm-d': {
      branch: 'dev',
      paths: ['docs/', 'README.md'],
      exclude: ['docs/internal/']
    }
  }
};
```

### Path Mapping Examples
- Latest: `repo/docs/path/file.md` → `/docs/latest/components/repo-name/path/file`
- Versioned: `repo/docs/path/file.md` → `/docs/0.2/components/repo-name/path/file`

### Image Asset Handling Strategy

**Asset Discovery and Fetching**:
```javascript
// Scan markdown content for image references
const imagePattern = /!\[([^\]]*)\]\(([^)]+)\)/g;
const assetPattern = /^\.\/(assets\/images\/|\.\.\/assets\/images\/)/;

function extractImageAssets(markdownContent, repoName, version) {
  const images = [];
  let match;
  
  while ((match = imagePattern.exec(markdownContent)) !== null) {
    const [fullMatch, altText, imagePath] = match;
    
    // Only process local images in assets/images directory
    if (assetPattern.test(imagePath)) {
      images.push({
        originalPath: imagePath,
        repoPath: resolveImagePath(imagePath, repoName),
        targetPath: `/docs/${version}/components/${repoName}/assets/images/`,
        altText: altText
      });
    }
  }
  
  return images;
}
```

**Path Rewriting**: 
- Source: `![Diagram](./assets/images/arch.png)` 
- Target: `![Diagram](/docs/latest/components/repo-name/assets/images/arch.png)`

**Asset Storage Structure**:
```
Website assets:
├── /docs/latest/components/
│   ├── llm-d-inference-scheduler/assets/images/
│   └── llm-d-kv-cache-manager/assets/images/
└── /docs/0.2/components/
    ├── llm-d-inference-scheduler/assets/images/
    └── llm-d-kv-cache-manager/assets/images/
```

**Processing Workflow**:
1. **Scan markdown files**: Extract all image references using regex patterns
2. **Filter local assets**: Only process images that reference `./assets/images/` paths
3. **Fetch from GitHub API**: Download images from the same repository/branch/tag as the markdown
4. **Store with versioning**: Save images in version-specific directories on the website
5. **Rewrite paths**: Update markdown content to use new absolute paths for the aggregated site
6. **Handle missing images**: Log warnings for broken image references, continue build process

**Edge Cases**:
- **Missing images**: Referenced but not found in repository → log warning, keep original reference
- **External images**: URLs to other domains → leave unchanged (may have availability issues)
- **Large assets**: Images over 5MB → log warning, may need optimization
- **Binary formats**: Unsupported formats → log warning, skip processing

### Technical Constraints
- Must respect GitHub API rate limits
- Work within existing Docusaurus + `docusaurus-plugin-remote-content` architecture
- Maintain backward compatibility with current individual file configurations
- Generate standard Docusaurus `versions.json` and versioned content structure
- Handle image asset fetching and storage within build process limits
- Support common image formats while managing file size constraints

## References

- [Docusaurus Plugin Remote Content](https://github.com/rdilweb/docusaurus-plugin-remote-content)
- [GitHub Issue #37 - Pattern Matching Support](https://github.com/rdilweb/docusaurus-plugin-remote-content/issues/37)
- [Docusaurus Versioning Documentation](https://docusaurus.io/docs/versioning)
- [Docusaurus Plugin Development Guide](https://docusaurus.io/docs/api/plugin-methods) 