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

- **Simplified Maintenance**: No complex asset pipelines or content transformations to maintain
- **Always Current**: Images and links automatically reflect current repository state via GitHub URLs
- **Developer-Friendly**: No special directory requirements - documentation works from any repository structure
- **Predictable Behavior**: All relative links consistently direct users to authoritative source repositories
- **Reduced Build Complexity**: Direct GitHub linking eliminates asset fetching and complex path rewriting
- **Source Attribution**: Clear repository headers and links keep users connected to source content
- **Zero Configuration**: New repositories work automatically with repository-specific transformation rules

## Non-Goals

- Real-time synchronization (build-time aggregation is sufficient, we could also have a GitHub action on a cron schedule for daily builds)
- Documentation format conversion beyond Markdown
- Replacing existing individual file remote content for special cases

## Requirements & Constraints

### Core Requirements
1. Auto-discover all markdown files in `/docs` directories for tree view rendering
2. Apply repository-specific content transformations based on source repository
3. Mirror `/docs` folder structure exactly in component documentation tree
4. Add minimal Docusaurus frontmatter and source attribution headers
5. Build-time integration with existing remote content system
6. Direct all relative links and images to GitHub URLs for authoritative source access
7. Support both main repository (with internal doc links) and component repositories (all external links)

### Technical Constraints
1. Must respect GitHub API rate limits
2. Work within existing Docusaurus + `docusaurus-plugin-remote-content` architecture
3. Maintain backward compatibility with current individual file configurations

## Scope

**In Scope**: Auto-discovery of markdown files from `/docs` directories, folder structure mirroring for tree views, repository-specific content transformations, GitHub-direct linking for all assets and references, minimal MDX compatibility fixes, source attribution

## Versioning Strategy

Our project has multiple versions (v0.2 and latest), and each component repository is expected to use the same tag for each release version (e.g., all components should have a `v0.2` tag for the 0.2 release). While the system can support cases where a component uses a different tag for a given release, this should be considered an edge case rather than the norm. The system will:

1. **Collect documentation for each version**: Pull docs from the appropriate git tags/branches for each version
2. **Handle missing versions gracefully**: If a repository doesn't have a specific version tag, skip it for that version
3. **Present standard version navigation**: Users see a normal version dropdown (latest, 0.2) like other documentation sites

**Example**: For version 0.2, the system pulls documentation from the `v0.2` tag in each repository and creates a separate documentation tree under `/docs/0.2/`.

## Website Structure

### Current vs. Proposed Navigation
**Current**: `What is llm-d? | User Guide | Community | News`  
**Proposed**: `llm-d Architecture | Start Here | Community | News`

### Individual Repository Documentation Structure
Here's how component repositories would be structured for automatic documentation aggregation:

```
Any llm-d component repository:
├── README.md                    # Becomes the main component landing page
└── docs/                        # Any files/folders here get rendered as sub-pages
    ├── assets/images/           # Images automatically fetched and rewritten
    ├── [any-folder]/            # Folder structure preserved in sidebar
    └── [any-file].md            # Individual documentation pages
```

**Key Points:**
- **README.md**: Automatically becomes the main overview page for that component
- **docs/ directory**: Any markdown files and folders are automatically discovered and rendered
- **Folder structure**: Preserved exactly in the website sidebar navigation  
- **Zero configuration**: Just commit files to these locations and they appear on the website

### Unified Website Structure (Latest Version)
The system automatically combines all repository documentation into this structure:

```
llm-d.ai website structure:
├── llm-d Architecture                   # Combined architecture and component documentation
│   ├── Overview                         # From llm-d/llm-d README.md
│   ├── llm-d-inference-scheduler/       # From llm-d/llm-d-inference-scheduler
│   │   ├── Overview                     # README.md content
│   │   └── [docs/ tree structure]       # From docs/ directory
│   ├── llm-d-kv-cache-manager/         # From llm-d/llm-d-kv-cache-manager
│   │   ├── Overview                     # README.md content
│   │   └── [docs/ tree structure]       # From docs/ directory
│   ├── llm-d-inference-sim/            # From llm-d/llm-d-inference-sim
│   │   ├── Overview                     # README.md content
│   │   └── [docs/ tree structure]       # From docs/ directory
│   └── llm-d-routing-sidecar/          # From llm-d/llm-d-routing-sidecar
│       ├── Overview                     # README.md content
│       └── [docs/ tree structure]       # From docs/ directory
├── Start Here                           # From llm-d-incubation/llm-d-infra
│   ├── Overview                         # llm-d-infra/README.md content
│   └── [docs/ tree structure]           # From llm-d-infra/docs/ directory
├── Community                            # (unchanged)
└── News                                 # (unchanged)
```

### Versioned Website Structure
The system creates separate documentation trees for each version per the requirements for Docusaurus versioning:

```
llm-d.ai with versions:
├── /docs/latest/                                    # Latest development version
│   ├── llm-d-architecture/                         # Combined architecture and components
│   │   ├── overview/                               # From llm-d/llm-d main branch README.md
│   │   ├── llm-d-inference-scheduler/              # From main branch
│   │   ├── llm-d-kv-cache-manager/                 # From main branch  
│   │   ├── llm-d-inference-sim/                    # From main branch
│   │   └── llm-d-routing-sidecar/                  # From main branch
│   └── start-here/                                 # From llm-d-infra main branch
└── /docs/0.2/                                      # Version 0.2 release
    ├── llm-d-architecture/                         # Combined architecture and components
    │   ├── overview/                               # From llm-d/llm-d v0.2 tag README.md
    │   ├── llm-d-inference-scheduler/              # From v0.2 tag
    │   ├── llm-d-kv-cache-manager/                 # From v0.2 tag
    │   ├── llm-d-inference-sim/                    # From v0.2 tag (if available)
    │   └── llm-d-routing-sidecar/                  # From v0.2 tag (if available)
    └── start-here/                                 # From llm-d-infra v0.2 tag
        # Note: If a repository doesn't have v0.2 tag, it's skipped for that version
```

### User Experience
Users see a familiar documentation site with comprehensive component documentation:

**Component Tree Views**: Each component shows its complete documentation structure:
```
llm-d Architecture
├── Overview (from README.md)
├── llm-d-inference-scheduler/
│   ├── Overview (from README.md)
│   ├── Installation (from docs/installation.md)
│   ├── API Reference (from docs/api.md)
│   └── Troubleshooting (from docs/troubleshooting.md)
├── llm-d-kv-cache-manager/
│   ├── Overview (from README.md)
│   ├── Configuration (from docs/configuration.md)
│   └── Performance (from docs/performance.md)
└── [all other components with their complete docs trees]
```

**Standard Features**:
- **Version dropdown**: Latest, 0.2
- **Automatic tree navigation**: `/docs` folder structure becomes expandable sidebar tree
- **Source links**: Each page shows "Edit this page" linking back to the original repository
- **Consistent URLs**: 
  - `/docs/latest/llm-d-architecture/overview` (from llm-d/llm-d README.md)
  - `/docs/latest/llm-d-architecture/llm-d-inference-scheduler/api` (from component docs/api.md)
  - `/docs/latest/start-here/deployment/prerequisites` (from llm-d-infra docs/)

### Developer Workflow
1. **Create documentation**: Add markdown files to `docs/` folder in any component repository, or update README.md for main landing pages
2. **Organize content**: Use folders to create logical groupings (e.g., `installation/`, `api/`, `troubleshooting/`)
3. **Commit changes**: Standard git workflow - commit and push to main branch
4. **Automatic inclusion**: Documentation appears on next website build (daily automated builds)
5. **No configuration needed**: System automatically discovers and includes new content

### Documentation Standards for llm-d Contributors

The llm-d documentation system uses repository-specific transformations that automatically direct users to authoritative source content. Follow these simple guidelines:

**✅ Write Natural Repository Documentation**

**Repository Structure**: Standardize on `/docs` directory for automatic tree view discovery:
```
Your repository:
├── README.md              # Component overview (automatically surfaced)
├── docs/                  # REQUIRED - All documentation for tree view rendering
│   ├── installation/      # Automatically becomes tree node
│   ├── api/              # Automatically becomes tree node
│   ├── troubleshooting/   # Automatically becomes tree node
│   └── assets/           # Store images anywhere in docs
├── examples/             # Not included in docs tree (stays in repository)
└── [other repository content]
```

**Linking**: Use relative links naturally as if users are browsing your repository:
```markdown
<!-- All of these automatically point users to GitHub -->
[Configuration Guide](./docs/configuration/basic.md)
[API Reference](../api/endpoints.md)
[Setup Guide](README.md)
[Examples](./examples/)
```

**Images**: Store and reference images anywhere in your repository:
```markdown
<!-- Images work from any location and display directly from GitHub -->
![Architecture](./docs/images/arch.png)
![Diagram](../assets/diagram.svg)
![Flow](./examples/setup/flow.jpg)
```

**Cross-Repository Links**: For links to other llm-d components, use full GitHub URLs:
```markdown
[llm-d Scheduler](https://github.com/llm-d/llm-d-inference-scheduler)
[Main Architecture](https://github.com/llm-d/llm-d/blob/dev/README.md)
```

**✅ Key Benefits of This Approach**
- **Standardized discovery** - `/docs` directory enables automatic tree view rendering for each component
- **Images always current** - displayed directly from GitHub, automatically stay in sync  
- **Authoritative links** - users always directed to the source repository for detailed exploration
- **Automatic tree structure** - folder organization in `/docs` becomes sidebar navigation
- **Developer-friendly** - write documentation as if users are browsing your repository on GitHub

**⚠️ Documentation Standards**
- **Place all documentation in `/docs` directory** - required for automatic tree view discovery
- **Organize logically** - folder structure in `/docs` becomes sidebar navigation tree
- Use standard markdown syntax (avoid complex HTML)
- Ensure HTML tags are self-closing when needed: `<img />`, `<br />`
- Code blocks should specify language for syntax highlighting

The system automatically discovers all `.md` files in `/docs`, renders them as a tree view for each component, handles MDX compatibility, and ensures all content points users to the correct GitHub repository for authoritative information.

### Core Goals & Scope
**Primary Goals (In Scope)**:
- Standardize on `/docs` directory structure across all component repositories for tree view rendering
- Automatically discover and render complete documentation trees for each component
- Direct users to authoritative source repositories for detailed exploration  
- Maintain predictable, consistent link behavior across all content
- Eliminate complex asset management and content transformation pipelines
- Support both main repository (internal doc links) and component repositories (GitHub links)

**Philosophy**: Surface complete component documentation trees for discovery while keeping users connected to authoritative source repositories. The `/docs` standardization enables automatic tree view rendering of comprehensive documentation for each component.

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

### Repository-Specific Transformation System
```javascript
// Simplified configuration based on repository type
const documentationSources = {
  mainRepository: {
    org: 'llm-d',
    name: 'llm-d',
    branch: 'dev',
    transform: 'transformMainRepo'  // Keep some internal links
  },
  componentRepositories: [
    // All repositories from component-configs.js
    // Each gets identical treatment with transformComponentRepo
    { org: 'llm-d', name: 'llm-d-inference-scheduler', branch: 'main' },
    { org: 'llm-d', name: 'llm-d-kv-cache-manager', branch: 'main' },
    { org: 'llm-d', name: 'llm-d-inference-sim', branch: 'main' },
    { org: 'llm-d', name: 'llm-d-routing-sidecar', branch: 'main' },
    { org: 'llm-d-incubation', name: 'llm-d-infra', branch: 'main' },
    // ... other component repositories
  ]
};

// Simple transformation rules
function getRepoTransform(org, name) {
  if (org === 'llm-d' && name === 'llm-d') {
    return transformMainRepo;    // Architecture overview with some internal links
  }
  return transformComponentRepo; // All other repos: everything points to GitHub
}
```

### Path Mapping Examples
- Architecture Overview: `llm-d/llm-d/README.md` → `/docs/latest/llm-d-architecture/overview`
- Component Main: `llm-d-inference-scheduler/README.md` → `/docs/latest/llm-d-architecture/llm-d-inference-scheduler`
- Component Docs: `llm-d-inference-scheduler/docs/path/file.md` → `/docs/latest/llm-d-architecture/llm-d-inference-scheduler/path/file`
- Start Here: `llm-d-infra/README.md` → `/docs/latest/start-here`
- Start Here Docs: `llm-d-infra/docs/path/file.md` → `/docs/latest/start-here/path/file`
- Versioned: Same pattern but with `/docs/0.2/` prefix for version 0.2

### Simplified GitHub-Direct Asset Strategy

**Direct GitHub Linking**: All images and assets are served directly from GitHub, eliminating complex asset management:

```javascript
// Simple transformation: rewrite all relative image paths to GitHub raw URLs
function transformImages(content, { repoUrl, branch }) {
  return content
    // All relative images → GitHub raw URLs
    .replace(/!\[([^\]]*)\]\((?!http)([^)]+)\)/g, (match, alt, path) => {
      const cleanPath = path.replace(/^\.\//, ''); // Remove leading ./
      return `![${alt}](${repoUrl}/raw/${branch}/${cleanPath})`;
    })
    // Same for HTML img tags
    .replace(/<img([^>]*?)src=["'](?!http)([^"']+)["']([^>]*?)>/g, (match, before, path, after) => {
      const cleanPath = path.replace(/^\.\//, '');
      return `<img${before}src="${repoUrl}/raw/${branch}/${cleanPath}"${after}>`;
    });
}
```

**Benefits of GitHub-Direct Approach**:
- ✅ **No asset fetching or storage** - eliminates build complexity
- ✅ **Always current** - images automatically reflect repository state
- ✅ **No broken links** - GitHub handles asset availability
- ✅ **Zero configuration** - works with any repository image organization
- ✅ **Reduced maintenance** - no asset pipeline to maintain

**Simple Processing**:
- **Source**: `![Diagram](./docs/images/arch.png)`
- **Target**: `![Diagram](https://github.com/org/repo/raw/main/docs/images/arch.png)`

**Repository Flexibility**:
- Images can be stored anywhere in the repository
- No required directory structure (`docs/assets/images/` not mandatory)
- Supports any file organization that makes sense for the component

### Technical Constraints
- Must respect GitHub API rate limits for content fetching
- Work within existing Docusaurus + `docusaurus-plugin-remote-content` architecture
- Maintain backward compatibility with current individual file configurations
- Apply essential MDX compatibility fixes (self-closing tags, angle bracket URLs, JSON strings)
- Support repository-specific transformation rules based on source repository type

## References

- [Docusaurus Plugin Remote Content](https://github.com/rdilweb/docusaurus-plugin-remote-content)
- [GitHub Issue #37 - Pattern Matching Support](https://github.com/rdilweb/docusaurus-plugin-remote-content/issues/37)
- [Docusaurus Versioning Documentation](https://docusaurus.io/docs/versioning)
- [Docusaurus Plugin Development Guide](https://docusaurus.io/docs/api/plugin-methods) 