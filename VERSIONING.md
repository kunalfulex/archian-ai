# Versioning Guide

Archian AI uses [Semantic Versioning](https://semver.org/) (SemVer) for version management.

## Version Format

`MAJOR.MINOR.PATCH` (e.g., `1.0.0`, `1.1.0`, `2.0.0`)

- **MAJOR** (1.0.0 → 2.0.0): Breaking changes
- **MINOR** (1.0.0 → 1.1.0): New features, backward compatible
- **PATCH** (1.0.0 → 1.0.1): Bug fixes, small changes

## Current Version

Check version in:
- **Backend**: `backend/version.py` (`__version__`)
- **Frontend**: `package.json` (`version` field)

## Creating a New Version

When you're ready to release a new version:

### Step 1: Update Version Files

**Backend:**
```bash
# Edit backend/version.py
# Change: __version__ = "0.1.0" → __version__ = "0.1.1"
```

**Frontend:**
```bash
# Edit package.json
# Change: "version": "0.1.0" → "version": "0.1.1"
```

### Step 2: Commit Version Changes

```bash
git add backend/version.py package.json
git commit -m "Bump version to 0.1.1"
```

### Step 3: Create Git Tag

**Backend:**
```bash
cd edison-ai
git tag -a v0.1.1 -m "Release version 0.1.1"
git push origin v0.1.1
```

**Frontend:**
```bash
cd edisonai-frontend
git tag -a v0.1.1 -m "Release version 0.1.1"
git push origin v0.1.1
```

### Step 4: (Optional) Create GitHub Release

- Go to GitHub → Releases → "Draft a new release"
- Select the tag (e.g., `v0.1.1`)
- Add release notes
- Publish

## Rollback to Previous Version

If you need to rollback to a previous working version:

```bash
# See all version tags
git tag -l "v*"

# Checkout previous version
git checkout v0.1.0

# Or create a hotfix branch from previous version
git checkout -b hotfix/rollback-to-v0.1.0 v0.1.0
```

## Version Sync

**Important**: Always keep frontend and backend versions in sync when releasing together.

Both repos should use the same version number for releases.

## Quick Reference

```bash
# List all versions
git tag -l "v*"

# View version details
git show v0.1.0

# Compare versions
git diff v0.1.0 v0.1.1

# Deploy specific version
git checkout v0.1.0
```

## Questions?

- When should I bump version? Before merging to `main` or when deploying to production
- Which version to use? Start with `0.1.0`, bump as you make changes
- Version in dev? You can keep dev branch at a higher version than production

