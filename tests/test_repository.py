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
