#!/usr/bin/env python3
"""
Update NeMo Guardrails and NAT configuration files.

Copies the environment-specific template config files (config.yml.cloud or
config.yml.k8s) to the active config.yml files.

Usage:
    python update_config.py cloud   # Copies config.yml.cloud → config.yml (NVIDIA cloud API endpoints)
    python update_config.py k8s     # Copies config.yml.k8s → config.yml (NIM services in the k8s cluster)

Config File Locations:
    - guardrails_config/config.yml
    - src/nat_simple_web_query/configs/config.yml
"""

import argparse
import shutil
from pathlib import Path

CONFIG_TYPES = ["cloud", "k8s"]


def copy_config_template(config_type):
    """Copy config template files to active config.yml based on config type."""
    if config_type not in CONFIG_TYPES:
        print(f"❌ Error: Invalid config type: {config_type}. Must be one of: {', '.join(CONFIG_TYPES)}")
        exit(1)

    # Define config directories
    guardrails_dir = Path("guardrails_config")
    nat_dir = Path("src/nat_simple_web_query/configs")

    # Copy template files to config.yml
    for config_dir in [guardrails_dir, nat_dir]:
        source_file = config_dir / f"config.yml.{config_type}"
        dest_file = config_dir / "config.yml"

        if not source_file.exists():
            print(f"❌ Error: {source_file} not found")
            exit(1)

        shutil.copy2(source_file, dest_file)
        print(f"✓ Copied {source_file} → {dest_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Activate config files for a deployment profile")
    parser.add_argument(
        "config_type",
        choices=CONFIG_TYPES,
        help="Config type: 'cloud' for NVIDIA cloud API endpoints, 'k8s' for in-cluster NIM services"
    )

    args = parser.parse_args()
    print(f"Updating config files for config type: {args.config_type}")
    copy_config_template(args.config_type)
