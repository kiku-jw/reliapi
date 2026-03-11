#!/usr/bin/env python3
"""Bump version across all project files.

Reads current version from product.yaml, bumps it according to semver,
and updates all relevant files (product.yaml, pyproject.toml, package.json, etc.).

Usage:
    python bump_version.py [patch|minor|major]
    python bump_version.py set 1.2.3
"""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent


def load_config() -> dict:
    """Load product.yaml configuration."""
    config_path = PROJECT_ROOT / "product.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    """Save product.yaml configuration."""
    config_path = PROJECT_ROOT / "product.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse semver version string into tuple."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"Invalid version format: {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_version(current: str, bump_type: str) -> str:
    """Bump version according to type."""
    major, minor, patch = parse_version(current)

    if bump_type == "patch":
        patch += 1
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ValueError(f"Invalid bump type: {bump_type}")

    return f"{major}.{minor}.{patch}"


def update_pyproject_toml(version: str, path: Path) -> bool:
    """Update version in pyproject.toml file."""
    if not path.exists():
        return False

    content = path.read_text()
    updated = re.sub(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{version}"',
        content,
        flags=re.MULTILINE,
    )

    if updated != content:
        path.write_text(updated)
        print(f"Updated {path}")
        return True
    return False


def update_package_json(version: str, path: Path) -> bool:
    """Update version in package.json file."""
    if not path.exists():
        return False

    with open(path) as f:
        data = json.load(f)

    if data.get("version") != version:
        data["version"] = version
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Updated {path}")
        return True
    return False


def update_python_files(version: str) -> None:
    """Update version in Python source files."""
    package_init = PROJECT_ROOT / "reliapi" / "__init__.py"
    if package_init.exists():
        content = package_init.read_text()
        updated = re.sub(
            r'__version__\s*=\s*"[^"]+"',
            f'__version__ = "{version}"',
            content,
        )
        if updated != content:
            package_init.write_text(updated)
            print(f"Updated {package_init}")


def update_changelog(version: str) -> None:
    """Update CHANGELOG.md with new version entry."""
    changelog_path = PROJECT_ROOT / "CHANGELOG.md"

    if not changelog_path.exists():
        # Create new changelog
        content = f"""# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [{version}] - {datetime.now(UTC).strftime('%Y-%m-%d')}

### Added
- Initial release

"""
        changelog_path.write_text(content)
        print(f"Created {changelog_path}")
        return

    content = changelog_path.read_text()
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    new_entry = f"""## [{version}] - {date_str}

### Changed
- Version bump

"""

    # Insert after the header section
    if "## [" in content:
        # Find first version entry and insert before it
        content = re.sub(
            r"(## \[)",
            f"{new_entry}\\1",
            content,
            count=1,
        )
    else:
        # Append after header
        content += f"\n{new_entry}"

    changelog_path.write_text(content)
    print(f"Updated {changelog_path}")


def main() -> int:
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: bump_version.py [patch|minor|major]")
        print("       bump_version.py set X.Y.Z")
        return 1

    config = load_config()
    current_version = config["product"]["version"]

    if sys.argv[1] == "set" and len(sys.argv) >= 3:
        new_version = sys.argv[2]
        # Validate version format
        parse_version(new_version)
    elif sys.argv[1] in ("patch", "minor", "major"):
        new_version = bump_version(current_version, sys.argv[1])
    else:
        print(f"Invalid argument: {sys.argv[1]}")
        return 1

    print(f"Bumping version: {current_version} -> {new_version}")

    # Update product.yaml
    config["product"]["version"] = new_version
    save_config(config)
    print("Updated product.yaml")

    # Update root pyproject.toml
    update_pyproject_toml(new_version, PROJECT_ROOT / "pyproject.toml")

    # Update SDK files
    update_pyproject_toml(new_version, PROJECT_ROOT / "sdk" / "python" / "pyproject.toml")
    update_package_json(new_version, PROJECT_ROOT / "sdk" / "js" / "package.json")

    # Update CLI
    update_pyproject_toml(new_version, PROJECT_ROOT / "cli" / "pyproject.toml")

    # Update action
    update_package_json(new_version, PROJECT_ROOT / "action" / "package.json")

    # Update Python source files
    update_python_files(new_version)

    # Update changelog
    update_changelog(new_version)

    print(f"\nVersion bumped to {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
