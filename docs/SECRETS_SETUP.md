# ReliAPI Secrets Setup Guide

## GitHub Repository Secrets

Configure these secrets in your GitHub repository settings (Settings > Secrets and variables > Actions).

### Required Secrets

#### NPM_TOKEN

For publishing the JavaScript SDK to NPM.

1. Go to https://www.npmjs.com/settings/~/tokens
2. Click "Generate New Token"
3. Select "Automation" type
4. Copy the token
5. Add as `NPM_TOKEN` secret in GitHub

#### PYPI_TOKEN

For publishing the Python SDK to PyPI.

1. Go to https://pypi.org/manage/account/
2. Click "Add API token"
3. Set scope to "Entire account" or specific project
4. Copy the token (starts with `pypi-`)
5. Add as `PYPI_TOKEN` secret in GitHub

#### DOCKERHUB_USERNAME and DOCKERHUB_TOKEN

For pushing Docker images to Docker Hub.

1. Go to https://hub.docker.com/settings/security
2. Click "New Access Token"
3. Give it a description and appropriate permissions
4. Copy the token
5. Add as `DOCKERHUB_TOKEN` secret in GitHub
6. Add your username as `DOCKERHUB_USERNAME`

## Local Development Secrets

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

### Required

```
REDIS_URL=redis://localhost:6379/0
```

### Optional

```
# API Authentication
RELIAPI_API_KEY=your-api-key

# LLM Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
MISTRAL_API_KEY=...

# RapidAPI
RAPIDAPI_API_KEY=...
RAPIDAPI_WEBHOOK_SECRET=...
```

## Security Best Practices

1. **Never commit secrets** - Always use environment variables or secrets management
2. **Rotate tokens regularly** - Set calendar reminders to rotate tokens
3. **Use minimal permissions** - Only grant the permissions needed
4. **Monitor usage** - Check NPM/PyPI/Docker Hub for unauthorized publishes
5. **Review before release** - Always verify no secrets in the release

## Token Permissions Reference

| Token | Required Permissions |
|-------|---------------------|
| NPM_TOKEN | Automation (publish packages) |
| PYPI_TOKEN | Upload packages |
| DOCKERHUB_TOKEN | Read, Write, Delete (repository access) |

## Troubleshooting

### "Permission denied" in CI

- Check token hasn't expired
- Verify token has correct permissions
- Ensure secret name matches workflow reference

### "Package already exists"

- Version already published (cannot overwrite)
- Increment version and try again

### "Repository not found" for release assets

- Verify repository name in GitHub workflow settings is correct
