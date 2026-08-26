from codeexcellent.config.settings import load_config
from codeexcellent.core import context
from codeexcellent.core.models import RepoContext

CONFIG = load_config()


def test_context_respects_total_byte_cap(tmp_path):
    big_file = tmp_path / "big.py"
    big_file.write_text("x = 1\n" * 10000)

    repo = RepoContext(
        root=str(tmp_path), project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=1, relevant_files=[], repo_complexity=0.0,
    )

    small_config = {"context": {"max_files": 5, "max_bytes_per_file": 1000, "max_total_bytes": 1500}}
    bundle = context.build(repo, ["big.py"], small_config)

    assert bundle.total_bytes <= 1500


def test_context_skips_missing_files(tmp_path):
    repo = RepoContext(
        root=str(tmp_path), project_types=[], languages=[], frameworks=[], entry_points=[],
        config_files=[], test_dirs=[], has_git=False, git_branch=None, git_dirty_files=[],
        file_count=0, relevant_files=[], repo_complexity=0.0,
    )
    bundle = context.build(repo, ["does_not_exist.py"], CONFIG)
    assert bundle.files == {}


def test_render_prompt_context_includes_summary_and_files(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("print('hi')")
    repo = RepoContext(
        root=str(tmp_path), project_types=["python"], languages=["python"], frameworks=[],
        entry_points=[], config_files=[], test_dirs=[], has_git=False, git_branch=None,
        git_dirty_files=[], file_count=1, relevant_files=[], repo_complexity=0.0,
    )
    bundle = context.build(repo, ["a.py"], CONFIG)
    rendered = context.render_prompt_context(bundle)
    assert "python" in rendered.lower()
    assert "a.py" in rendered
