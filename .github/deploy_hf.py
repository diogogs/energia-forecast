"""Deploy the read-only API to a Hugging Face Docker Space.

Creates the Space if needed, injects the DATABASE_URL_RO secret, writes a Space README with the
required Docker frontmatter (kept out of the GitHub README), and uploads only what the image
needs (Dockerfile, pyproject, uv.lock, src). Run by .github/workflows/deploy-hf.yml.
"""

from __future__ import annotations

import os
import sys

from huggingface_hub import HfApi

_README = """---
title: Energia Forecast API
emoji: ⚡
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# energia-forecast API

Read-only serving API for Portuguese electricity demand and MIBEL day-ahead price forecasts.
Source and docs: https://github.com/diogogs/energia-forecast — see `/docs` for the OpenAPI UI.
"""


def main() -> None:
    token = os.environ.get("HF_TOKEN")
    space = os.environ.get("HF_SPACE")  # "username/space-name"
    if not token or not space:
        sys.exit("HF_TOKEN and HF_SPACE must be set")

    api = HfApi(token=token)
    api.create_repo(repo_id=space, repo_type="space", space_sdk="docker", exist_ok=True)

    db_ro = os.environ.get("DATABASE_URL_RO")
    if db_ro:
        api.add_space_secret(repo_id=space, key="DATABASE_URL_RO", value=db_ro)

    api.upload_file(
        path_or_fileobj=_README.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=space,
        repo_type="space",
    )
    for f in ("Dockerfile", "pyproject.toml", "uv.lock"):
        api.upload_file(path_or_fileobj=f, path_in_repo=f, repo_id=space, repo_type="space")
    api.upload_folder(
        folder_path="src",
        path_in_repo="src",
        repo_id=space,
        repo_type="space",
        ignore_patterns=["**/__pycache__/**", "**/*.pyc"],
    )
    print(f"deployed to https://huggingface.co/spaces/{space}")


if __name__ == "__main__":
    main()
