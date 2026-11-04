"""按插件包方式加载源码。"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("maiweave", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules["maiweave"] = package
spec.loader.exec_module(package)


@pytest.fixture(autouse=True)
def runtime_directory(tmp_path, monkeypatch):
    """每项测试使用独立的 MaiBot 运行目录。"""
    monkeypatch.chdir(tmp_path)
