# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository automatically generates Clash Meta configuration files from subscription feeds. A Python script fetches proxy nodes from subscription URLs, categorizes them by region, and outputs a complete Clash configuration with proxy groups and routing rules.

## Core Architecture

### Build Pipeline
1. **Template** (`test.yaml`): Defines proxy groups, rule providers, and routing rules with empty proxy lists
2. **Build Script** (`scripts/build_clash_config.py`): Fetches subscription feeds, categorizes nodes, and populates proxy groups
3. **Output**:
   - `dist/config.yaml`: Final Clash configuration
   - `providers/all.yaml`: Proxy provider file containing all fetched nodes

### Node Categorization Logic

The script categorizes proxy nodes into groups based on regex patterns matching node names:

- **香港** (Hong Kong): Matches `hk|hong|港|香江|xiangjiang|gp(?!t)|gp\d+`
- **东南亚** (Southeast Asia): Matches `sg|singapore|sea|vn|vietnam|th|thailand|my|malaysia|ph|phil|id|indo|jp|japan|tw|taiwan` and regional keywords
- **欧美** (Europe/Americas): Matches `us|usa|uk|gb|eu|europe|de|fr|nl|ca|america` and regional keywords
- **币安** (Binance): Contains Hong Kong + Singapore nodes (for Binance-specific routing)
- **pikpak**: All nodes except Hong Kong (for PikPak cloud storage)
- **其他** (Other): Nodes not matching any region pattern

### Subscription Fetching

The script includes fallback logic to handle various subscription URL formats:
- Tries multiple API paths (`/api/v1/client/subscribe`, `/api/client/subscribe`)
- Tests different `flag` parameters (`clashmeta`, `clash`, `meta`, etc.)
- Falls back to HTTP if HTTPS fails
- Deduplicates nodes by name across multiple subscriptions

## Development Commands

### Build Configuration
```bash
# Generate Clash config from subscriptions (requires internet)
python scripts/build_clash_config.py

# Use local provider file instead of fetching subscriptions
USE_LOCAL_PROVIDER=1 python scripts/build_clash_config.py
```

### Prerequisites
```bash
# Install Python dependencies
pip install pyyaml requests
```

## GitHub Actions Workflow

The repository uses GitHub Actions (`.github/workflows/clash-config.yml`) to automatically rebuild the configuration:
- **Schedule**: Daily at 15:00 Beijing time (07:00 UTC)
- **Manual trigger**: Via `workflow_dispatch`
- **Auto-commit**: Pushes updated `dist/config.yaml` and `providers/all.yaml` to the repository

## Configuration Customization

### Adding New Proxy Groups

1. Add the group definition to `test.yaml` under `proxy-groups` with empty `proxies: []`
2. Add the group name to `name_sets` dict in `scripts/build_clash_config.py:214`
3. Define regex pattern for node categorization if needed (lines 223-232)
4. Add categorization logic in the node processing loop (lines 234-255)
5. The script will automatically populate the group's proxy list

### Adding Rule Providers

Add rule provider definitions to `test.yaml` under `rule-providers` following this format:
```yaml
rule-providers:
  example:
    type: http
    behavior: classical  # or "domain"
    format: yaml
    interval: 86400
    path: ./rule-set/example.yaml
    url: https://example.com/rule.yaml
```

Then reference in `rules` section: `- RULE-SET,example,ProxyGroupName`

## File Organization

- `test.yaml`: Template configuration with subscriptions, groups, rules
- `scripts/build_clash_config.py`: Main build script
- `dist/config.yaml`: Generated Clash configuration (git-tracked output)
- `providers/all.yaml`: Proxy provider file (git-tracked output)
- `.github/workflows/clash-config.yml`: CI/CD automation
