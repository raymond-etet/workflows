# Repository Guidelines

## Project Structure & Module Organization
- `test.yaml`: Template Clash Meta config defining groups, rule providers, and subscriptions to fetch.
- `scripts/`: Python tooling; `scripts/build_clash_config.py` reads `test.yaml`, pulls subscriptions, classifies nodes, and writes outputs.
- `providers/all.yaml`: Generated proxy-provider file; consumed by the final config.
- `dist/config.yaml`: Generated Clash config ready for clients.
- `.github/workflows/clash-config.yml`: CI job that rebuilds and auto-commits the generated files on schedule or manual dispatch.

## Build, Test, and Development Commands
- `pip install pyyaml requests` – install runtime deps for the build script (Python 3.11+ recommended).
- `python scripts/build_clash_config.py` – fetch subscriptions, categorize nodes, and regenerate `dist/config.yaml` and `providers/all.yaml`.
- `USE_LOCAL_PROVIDER=1 python scripts/build_clash_config.py` – skip network fetch; reuse existing `providers/all.yaml`.
- `python -m compileall scripts` (optional) – quick syntax sanity check.

## Coding Style & Naming Conventions
- Python: follow PEP 8, 4-space indents; prefer clear helper functions (see `load_yaml`, `fetch_proxies`).
- Keep regex-based grouping logic readable and commented when adding new patterns in `build_clash_config.py`.
- YAML: align indentation with two spaces; keep group names consistent with the template (e.g., `香港`, `东南亚`, `欧美`).

## Testing Guidelines
- No formal test suite; validate by running the build script and confirming it succeeds and prints the generated paths.
- Spot-check outputs: ensure `dist/config.yaml` loads in your Clash client and that proxy groups are populated as expected.
- When changing grouping logic, verify representative node names fall into the intended groups (Hong Kong/Southeast Asia/Europe-Americas/Other).

## Commit & Pull Request Guidelines
- Recent history mixes short messages; prefer the existing `type: subject` style used by CI (`chore: build clash config`) and keep subjects under ~60 chars.
- PRs: describe why the config or logic changed, list key commands run (`python scripts/build_clash_config.py`), and note whether outputs were regenerated.
- Link related issues if any; include screenshots only when they clarify client behavior or config diffs.

## Security & Configuration Tips
- Treat subscription URLs and tokens in `test.yaml` as sensitive; avoid sharing logs containing them.
- When adding new rule-provider URLs, favor HTTPS mirrors and verify availability to keep CI green.
