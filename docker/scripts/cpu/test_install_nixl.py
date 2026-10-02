from pathlib import Path

import install_nixl
import pytest


def test_find_nixl_wheel_uses_version_order(tmp_path: Path) -> None:
    for name in (
        "nixl-0.9.0-cp312-cp312-linux_x86_64.whl",
        "nixl-0.10.0-cp312-cp312-linux_x86_64.whl",
        "nixl-0.6.1-cp312-cp312-linux_x86_64.whl",
    ):
        (tmp_path / name).touch()

    selected = install_nixl.find_nixl_wheel_in_cache(str(tmp_path))

    assert Path(selected).name == "nixl-0.10.0-cp312-cp312-linux_x86_64.whl"


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        (("0.10.0rc1", "0.10.0"), "0.10.0"),
        (("0.9.0", "0.10.0.dev1"), "0.10.0.dev1"),
        (("0.10.0", "0.10.0.post1"), "0.10.0.post1"),
    ],
)
def test_find_nixl_wheel_uses_pep440_order(tmp_path, versions, expected):
    for version in versions:
        (tmp_path / f"nixl-{version}-cp312-cp312-linux_x86_64.whl").touch()

    selected = install_nixl.find_nixl_wheel_in_cache(str(tmp_path))

    assert Path(selected).name == f"nixl-{expected}-cp312-cp312-linux_x86_64.whl"


def test_find_nixl_wheel_ignores_unrelated_or_invalid_wheels(tmp_path):
    for name in ("nixl-invalid.whl", "nixl_extra-99.0-py3-none-any.whl"):
        (tmp_path / name).touch()

    assert install_nixl.find_nixl_wheel_in_cache(str(tmp_path)) is None
