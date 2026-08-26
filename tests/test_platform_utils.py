import sys
from unittest.mock import patch

from codeexcellent.core.platform_utils import python_executable, resolve_executable


def test_resolve_executable_returns_full_path_when_found():
    with patch("shutil.which", return_value="/usr/bin/git"):
        assert resolve_executable("git") == "/usr/bin/git"


def test_resolve_executable_falls_back_to_bare_name_when_not_found():
    with patch("shutil.which", return_value=None):
        assert resolve_executable("nonexistent-tool") == "nonexistent-tool"


def test_python_executable_is_the_running_interpreter():
    assert python_executable() == sys.executable
