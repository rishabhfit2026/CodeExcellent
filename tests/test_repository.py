from codeexcellent.config.settings import load_config
from codeexcellent.core import repository

CONFIG = load_config()


def _make_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "auth.py").write_text("def login(): pass\n")
    (tmp_path / "billing.py").write_text("def charge(): pass\n")
    (tmp_path / "unrelated.py").write_text("def noop(): pass\n")


def test_relevant_files_only_returns_keyword_matches(tmp_path):
    _make_project(tmp_path)
    results = repository.find_relevant_files(str(tmp_path), "Fix the login bug in auth", CONFIG)
    assert "auth.py" in results
    assert "unrelated.py" not in results
    assert "billing.py" not in results


def test_different_queries_return_different_relevant_files(tmp_path):
    _make_project(tmp_path)
    auth_results = repository.find_relevant_files(str(tmp_path), "Fix the login bug in auth", CONFIG)
    billing_results = repository.find_relevant_files(str(tmp_path), "Fix the billing charge amount", CONFIG)
    assert set(auth_results) != set(billing_results)


def test_no_keyword_overlap_returns_empty(tmp_path):
    _make_project(tmp_path)
    results = repository.find_relevant_files(str(tmp_path), "xyzxyz qqqqqq", CONFIG)
    assert results == []


def test_content_scan_finds_matches_path_alone_would_miss(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "token_service.py").write_text("def check_expiration(token):\n    pass\n")
    (tmp_path / "unrelated.py").write_text("def noop(): pass\n")

    results = repository.find_relevant_files(str(tmp_path), "Fix JWT expiration handling", CONFIG)
    assert "token_service.py" in results
    assert "unrelated.py" not in results


def test_dependency_and_test_files_are_pulled_in(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "jwt_handler.py").write_text(
        "from token_service import validate\n\ndef handle_jwt():\n    return validate()\n"
    )
    (tmp_path / "token_service.py").write_text("def validate():\n    pass\n")
    (tmp_path / "unrelated.py").write_text("def noop(): pass\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_jwt_handler.py").write_text("def test_handle_jwt():\n    pass\n")

    results = repository.find_relevant_files(str(tmp_path), "Fix JWT handler expiration bug", CONFIG)
    assert "jwt_handler.py" in results
    assert "token_service.py" in results  # pulled in as a dependency of jwt_handler.py
    assert "tests/test_jwt_handler.py" in results  # pulled in as its test
    assert "unrelated.py" not in results


def test_relevant_files_respects_max_results_plus_dependency_cap(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "auth_handler.py").write_text("from auth_utils import check\n\ndef handle_auth():\n    return check()\n")
    (tmp_path / "auth_utils.py").write_text("def check():\n    pass\n")

    results = repository.find_relevant_files(str(tmp_path), "Fix auth handler bug", CONFIG, max_results=1)
    # 1 primary match + up to max_dependency_files pulled-in extras
    assert "auth_handler.py" in results
    assert len(results) <= 1 + CONFIG["context"]["max_dependency_files"]
