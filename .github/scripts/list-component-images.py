#!/usr/bin/env python3
"""List the container images referenced by the centralized image components.

Walks every kustomization.yaml under
guides/recipes/modelserver/components/images and prints one fully-qualified
`newName:newTag` reference per line (deduplicated, sorted). The verify-component-images
workflow feeds this list to `docker buildx imagetools inspect` to confirm each
reference resolves to a real manifest.

Usage:
    python .github/scripts/list-component-images.py [IMAGES_DIR]
"""

import pathlib
import sys

import yaml

DEFAULT_DIR = "guides/recipes/modelserver/components/images"


def collect_images(images_dir: pathlib.Path) -> list[str]:
    refs: set[str] = set()
    for path in sorted(images_dir.rglob("kustomization.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for img in doc.get("images", []) or []:
            new_name = img.get("newName")
            if not new_name:
                # A component that only retags (newTag on the original name) or
                # an entry without an override is not something we can resolve.
                continue
            new_tag = img.get("newTag")
            digest = img.get("digest")
            if digest:
                ref = f"{new_name}@{digest}"
            elif new_tag is not None:
                ref = f"{new_name}:{new_tag}"
            else:
                ref = new_name
            refs.add(ref)
    return sorted(refs)


def main() -> int:
    images_dir = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR)
    if not images_dir.is_dir():
        print(f"error: {images_dir} is not a directory", file=sys.stderr)
        return 2
    for ref in collect_images(images_dir):
        print(ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
