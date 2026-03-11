# ReliAPI Release Process

## Prerequisites

### Required Secrets

Configure these secrets in GitHub repository settings:

| Secret | Description |
|--------|-------------|
| `NPM_TOKEN` | NPM automation token for `@kikuai/reliapi` package |
| `PYPI_TOKEN` | PyPI API token for `reliapi-sdk` package |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |

### Local Setup

```bash
# Install dependencies
make install

# Verify everything works
make verify
```

## Release Process

### 1. Automated Release (Recommended)

```bash
# For bug fixes
make release-patch

# For new features
make release-minor

# For breaking changes
make release-major
```

This will:
1. Run tests
2. Bump version in all files
3. Regenerate OpenAPI spec
4. Generate SDKs and Postman collection
5. Create git commit and tag
6. Push to origin

CI will then automatically:
1. Run full test suite
2. Build and push Docker image
3. Publish NPM package
4. Publish PyPI package
5. Create GitHub Release

### 2. Manual Release

If you need more control:

```bash
# 1. Bump version manually
python scripts/bump_version.py patch  # or minor/major

# 2. Generate artifacts
make openapi
make sdk
make postman

# 3. Verify
make verify

# 4. Commit and tag
git add -A
git commit -m "chore(release): v1.0.1"
git tag -a v1.0.1 -m "Release v1.0.1"

# 5. Push
git push origin main
git push origin v1.0.1
```

## Version Files

Version is tracked in these files (all updated by `bump_version.py`):

- `product.yaml` - Source of truth
- `pyproject.toml` - Root package
- `reliapi/__init__.py` - Package version
- `cli/pyproject.toml` - CLI package
- `sdk/js/package.json` - JS SDK (after generation)
- `sdk/python/pyproject.toml` - Python SDK (after generation)
- `action/package.json` - GitHub Action
- `CHANGELOG.md` - Changelog entry

## CI Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `ci.yml` | Push/PR to main | Lint, test, build |
| `publish-npm.yml` | Tag v* | Publish to NPM |
| `publish-pypi.yml` | Tag v* | Publish to PyPI |
| `publish-docker.yml` | Tag v* | Push to Docker Hub |
| `release-assets.yml` | Tag v* | Create GitHub Release |

## Rollback

If a release has issues:

```bash
# 1. Delete the tag locally and remotely
git tag -d v1.0.1
git push origin :refs/tags/v1.0.1

# 2. Revert the commit
git revert HEAD
git push origin main

# 3. Unpublish packages (if needed)
# NPM: npm unpublish @kikuai/reliapi@1.0.1
# PyPI: Cannot unpublish, create new patch version
# Docker: docker rmi kikuai/reliapi:1.0.1
```

## Troubleshooting

### NPM Publish Fails

1. Check `NPM_TOKEN` is valid
2. Verify package name is correct
3. Check if version already exists

### PyPI Publish Fails

1. Check `PYPI_TOKEN` is valid
2. Verify package name is correct
3. Check if version already exists

### Docker Push Fails

1. Check `DOCKERHUB_TOKEN` is valid
2. Verify image name matches

## Security Checklist

Before each release:

- [ ] No secrets in committed code
- [ ] No private URLs/IPs exposed
- [ ] Dependencies are up to date
- [ ] Security patches applied
- [ ] Leak detection passes
