"""Phase 4 of the benchmarking audit: for every benchmark task with a
validate() function, prove it actually distinguishes correct from
incorrect, rather than trusting it just because it happens to pass a
correct implementation once.

For each validated task:
  1. The untouched baseline fixture MUST fail validation (if it didn't,
     the "task" would already be complete before any work was done).
  2. A hand-written correct implementation MUST pass.
  3. Where a distinct, meaningfully-wrong implementation exists, it MUST
     fail (guards against a validator too weak to catch real mistakes).

These tests exercise `task.fixture` and `task.validate` directly -- no
Claude, no CodeExcellent orchestration, no subprocess beyond what a
validator itself needs (e.g. medium_add_tests genuinely runs pytest).
"""
from codeexcellent.benchmark.tasks import ALL_TASKS


def _task(task_id: str):
    for task in ALL_TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)


def _apply(root, name: str, content: str) -> None:
    (root / name).write_text(content)


# --- trivial_rename ---------------------------------------------------------

def test_trivial_rename_baseline_fails(tmp_path):
    task = _task("trivial_rename")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_trivial_rename_correct_passes(tmp_path):
    task = _task("trivial_rename")
    task.fixture(tmp_path)
    _apply(tmp_path, "greet.py", 'def greet(name):\n    return "Hello, " + name\n')
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_trivial_rename_incorrect_fails(tmp_path):
    task = _task("trivial_rename")
    task.fixture(tmp_path)
    # Renamed the parameter but changed the greeting text -- a real mistake
    # a text-substring validator would have missed entirely.
    _apply(tmp_path, "greet.py", 'def greet(name):\n    return "Hi, " + name\n')
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- trivial_typo ------------------------------------------------------------

def test_trivial_typo_baseline_fails(tmp_path):
    task = _task("trivial_typo")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_trivial_typo_correct_passes(tmp_path):
    task = _task("trivial_typo")
    task.fixture(tmp_path)
    _apply(tmp_path, "README.md", "# Welcome to the project\n\nThis is a sample project.\n")
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_trivial_typo_incorrect_fails(tmp_path):
    task = _task("trivial_typo")
    task.fixture(tmp_path)
    _apply(tmp_path, "README.md", "# Welcom to the project\n\nThis is a sample project.\n")  # different typo
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- trivial_constant --------------------------------------------------------

def test_trivial_constant_baseline_fails(tmp_path):
    task = _task("trivial_constant")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_trivial_constant_correct_passes(tmp_path):
    task = _task("trivial_constant")
    task.fixture(tmp_path)
    _apply(tmp_path, "config.py", "MAX_RETRIES = 5\nTIMEOUT_SECONDS = 30\n")
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_trivial_constant_incorrect_fails_when_it_changes_what_must_not_change(tmp_path):
    task = _task("trivial_constant")
    task.fixture(tmp_path)
    _apply(tmp_path, "config.py", "MAX_RETRIES = 5\nTIMEOUT_SECONDS = 99\n")  # touched the untouchable
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- easy_validation ----------------------------------------------------

def test_easy_validation_baseline_fails(tmp_path):
    task = _task("easy_validation")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_easy_validation_correct_passes(tmp_path):
    task = _task("easy_validation")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "validators.py",
        "def validate_email(email):\n    if not email:\n        return False\n    return \"@\" in email\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_easy_validation_incorrect_fails(tmp_path):
    task = _task("easy_validation")
    task.fixture(tmp_path)
    # Rejects empty strings, but also breaks valid emails -- overcorrected.
    _apply(tmp_path, "validators.py", "def validate_email(email):\n    return False\n")
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- easy_helper ---------------------------------------------------------

def test_easy_helper_baseline_fails(tmp_path):
    task = _task("easy_helper")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_easy_helper_correct_passes(tmp_path):
    task = _task("easy_helper")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "utils.py",
        "def truncate(text, length):\n    return text[:length]\n\n\n"
        "def slugify(text):\n    return text.lower().replace(\" \", \"-\")\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_easy_helper_incorrect_fails(tmp_path):
    task = _task("easy_helper")
    task.fixture(tmp_path)
    # Forgot to lowercase.
    _apply(
        tmp_path, "utils.py",
        "def truncate(text, length):\n    return text[:length]\n\n\n"
        "def slugify(text):\n    return text.replace(\" \", \"-\")\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- easy_endpoint_response ------------------------------------------------

def test_easy_endpoint_response_baseline_fails(tmp_path):
    task = _task("easy_endpoint_response")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_easy_endpoint_response_correct_passes(tmp_path):
    task = _task("easy_endpoint_response")
    task.fixture(tmp_path)
    _apply(tmp_path, "api.py", 'def status_endpoint():\n    return {"status": "ok", "version": "1.0"}\n')
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_easy_endpoint_response_incorrect_fails(tmp_path):
    task = _task("easy_endpoint_response")
    task.fixture(tmp_path)
    _apply(tmp_path, "api.py", 'def status_endpoint():\n    return {"status": "ok", "version": "2.0"}\n')  # wrong value
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- easy_cli_flag -------------------------------------------------------

_CLI_CORRECT = (
    "import argparse\n\n\n"
    "def build_parser():\n"
    "    parser = argparse.ArgumentParser()\n"
    "    parser.add_argument(\"name\")\n"
    "    parser.add_argument(\"--verbose\", action=\"store_true\")\n"
    "    return parser\n\n\n"
    "def main(argv=None):\n"
    "    args = build_parser().parse_args(argv)\n"
    "    if args.verbose:\n"
    "        print(\"Verbose mode enabled\")\n"
    "    print(f\"Hello, {args.name}\")\n\n\n"
    "if __name__ == \"__main__\":\n"
    "    main()\n"
)

_CLI_INCORRECT_ALWAYS_VERBOSE = (
    "import argparse\n\n\n"
    "def build_parser():\n"
    "    parser = argparse.ArgumentParser()\n"
    "    parser.add_argument(\"name\")\n"
    "    parser.add_argument(\"--verbose\", action=\"store_true\")\n"
    "    return parser\n\n\n"
    "def main(argv=None):\n"
    "    args = build_parser().parse_args(argv)\n"
    "    print(\"Verbose mode enabled\")\n"  # unconditional -- breaks default behavior
    "    print(f\"Hello, {args.name}\")\n\n\n"
    "if __name__ == \"__main__\":\n"
    "    main()\n"
)


def test_easy_cli_flag_baseline_fails(tmp_path):
    task = _task("easy_cli_flag")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_easy_cli_flag_baseline_does_not_leak_argparse_error_to_real_stderr(tmp_path, capsys):
    # Regression: the baseline fixture has no --verbose registered, so
    # calling main(["World", "--verbose"]) makes argparse print a usage/
    # error message and exit. That message must be captured, not leaked to
    # the actual process running the validator (it was, before stderr was
    # also redirected -- confusingly under the *codeexcellent* program name,
    # since unpatched argparse derives it from our own sys.argv[0]).
    task = _task("easy_cli_flag")
    task.fixture(tmp_path)
    task.validate(tmp_path)
    captured = capsys.readouterr()
    assert "unrecognized arguments" not in captured.err
    assert "unrecognized arguments" not in captured.out


def test_easy_cli_flag_correct_passes(tmp_path):
    task = _task("easy_cli_flag")
    task.fixture(tmp_path)
    _apply(tmp_path, "cli.py", _CLI_CORRECT)
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_easy_cli_flag_incorrect_fails(tmp_path):
    task = _task("easy_cli_flag")
    task.fixture(tmp_path)
    _apply(tmp_path, "cli.py", _CLI_INCORRECT_ALWAYS_VERBOSE)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- medium_db_field -------------------------------------------------------

def test_medium_db_field_baseline_fails(tmp_path):
    task = _task("medium_db_field")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_medium_db_field_correct_passes(tmp_path):
    task = _task("medium_db_field")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "models.py",
        "class User:\n"
        "    def __init__(self, id, name, email=None):\n"
        "        self.id = id\n        self.name = name\n        self.email = email\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_medium_db_field_incorrect_fails_when_email_is_accepted_but_not_stored(tmp_path):
    task = _task("medium_db_field")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "models.py",
        "class User:\n"
        "    def __init__(self, id, name, email=None):\n"
        "        self.id = id\n        self.name = name\n",  # email accepted, never assigned
    )
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- medium_add_tests --------------------------------------------------------

def test_medium_add_tests_baseline_fails(tmp_path):
    task = _task("medium_add_tests")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_medium_add_tests_correct_passes(tmp_path):
    task = _task("medium_add_tests")
    task.fixture(tmp_path)
    (tmp_path / "tests").mkdir()
    _apply(
        tmp_path, "tests/test_calculator.py",
        "import sys\nsys.path.insert(0, '..')\nfrom calculator import add, subtract\n\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_subtract():\n    assert subtract(5, 3) == 2\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_medium_add_tests_incorrect_fails_on_a_wrong_assertion(tmp_path):
    task = _task("medium_add_tests")
    task.fixture(tmp_path)
    (tmp_path / "tests").mkdir()
    _apply(
        tmp_path, "tests/test_calculator.py",
        "import sys\nsys.path.insert(0, '..')\nfrom calculator import add, subtract\n\n\n"
        "def test_add():\n    assert add(2, 3) == 999\n\n\n"  # wrong expected value
        "def test_subtract():\n    assert subtract(5, 3) == 2\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_medium_add_tests_incorrect_fails_on_an_empty_test_file(tmp_path):
    task = _task("medium_add_tests")
    task.fixture(tmp_path)
    (tmp_path / "tests").mkdir()
    _apply(tmp_path, "tests/test_calculator.py", "# TODO: write tests for add and subtract\n")
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- hard_refactor_service ---------------------------------------------------

_REFACTOR_CORRECT = (
    "def validate(order):\n"
    "    if not order.get('items'):\n        raise ValueError('empty order')\n\n\n"
    "def price(order):\n"
    "    total = sum(i['price'] * i['qty'] for i in order['items'])\n"
    "    if order.get('coupon'):\n        total *= 0.9\n"
    "    return total\n\n\n"
    "def process_order(order):\n"
    "    validate(order)\n"
    "    total = price(order)\n"
    "    print(f'saving order total={total}')\n"
    "    return total\n"
)

_REFACTOR_INCORRECT_WRONG_MATH = (
    "def validate(order):\n"
    "    if not order.get('items'):\n        raise ValueError('empty order')\n\n\n"
    "def price(order):\n"
    "    total = sum(i['price'] * i['qty'] for i in order['items'])\n"
    "    return total\n\n\n"  # dropped the coupon discount entirely
    "def process_order(order):\n"
    "    validate(order)\n"
    "    total = price(order)\n"
    "    print(f'saving order total={total}')\n"
    "    return total\n"
)


def test_hard_refactor_service_baseline_fails(tmp_path):
    # The untouched original is a single function -- this is the exact
    # no-op-passes bug class found during phase 4 and fixed before this
    # test suite was written; asserting it here locks that fix in.
    task = _task("hard_refactor_service")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_hard_refactor_service_correct_passes(tmp_path):
    task = _task("hard_refactor_service")
    task.fixture(tmp_path)
    _apply(tmp_path, "order_service.py", _REFACTOR_CORRECT)
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_hard_refactor_service_incorrect_fails(tmp_path):
    task = _task("hard_refactor_service")
    task.fixture(tmp_path)
    _apply(tmp_path, "order_service.py", _REFACTOR_INCORRECT_WRONG_MATH)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- hard_add_auth -------------------------------------------------------

_AUTH_CORRECT = (
    "VALID_API_KEY = \"secret-key-123\"\n\n\n"
    "def _authorized(headers):\n    return headers.get(\"X-API-Key\") == VALID_API_KEY\n\n\n"
    "def status_endpoint(headers):\n"
    "    if not _authorized(headers):\n        raise PermissionError(\"unauthorized\")\n"
    "    return {\"status\": \"ok\"}\n\n\n"
    "def users_endpoint(headers):\n"
    "    if not _authorized(headers):\n        raise PermissionError(\"unauthorized\")\n"
    "    return {\"users\": []}\n"
)

_AUTH_INCORRECT_COMMENT_ONLY = (
    "VALID_API_KEY = \"secret-key-123\"\n\n\n"
    "# Requires a valid X-API-Key header for authentication.\n"
    "def status_endpoint(headers):\n    return {\"status\": \"ok\"}\n\n\n"
    "def users_endpoint(headers):\n    return {\"users\": []}\n"
)


def test_hard_add_auth_baseline_fails(tmp_path):
    task = _task("hard_add_auth")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_hard_add_auth_correct_passes(tmp_path):
    task = _task("hard_add_auth")
    task.fixture(tmp_path)
    _apply(tmp_path, "api.py", _AUTH_CORRECT)
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_hard_add_auth_incorrect_fails_on_comment_only_fix(tmp_path):
    # The exact false-positive risk that made the original substring-based
    # validator unreliable: a comment mentioning the header name, with zero
    # actual enforcement.
    task = _task("hard_add_auth")
    task.fixture(tmp_path)
    _apply(tmp_path, "api.py", _AUTH_INCORRECT_COMMENT_ONLY)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- very_hard_architecture_migration ---------------------------------------

def test_very_hard_architecture_migration_baseline_fails(tmp_path):
    # Same no-op-passes bug class as hard_refactor_service: an untouched
    # app.py trivially "preserves" its own behavior.
    task = _task("very_hard_architecture_migration")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_very_hard_architecture_migration_correct_passes(tmp_path):
    task = _task("very_hard_architecture_migration")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "models.py",
        "class User:\n    def __init__(self, id, name):\n        self.id = id\n        self.name = name\n",
    )
    _apply(
        tmp_path, "app.py",
        "from models import User\n\n\n"
        "def route_users():\n    return [User(1, 'a')]\n\n\n"
        "def service_greet(user):\n    return f'Hello {user.name}'\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_very_hard_architecture_migration_incorrect_fails_when_entry_points_break(tmp_path):
    task = _task("very_hard_architecture_migration")
    task.fixture(tmp_path)
    _apply(tmp_path, "models.py", "class User:\n    def __init__(self, id, name):\n        self.id = id\n        self.name = name\n")
    # Split into a new file, but app.py no longer exposes route_users.
    _apply(tmp_path, "app.py", "from models import User\n\n\ndef service_greet(user):\n    return f'Hello {user.name}'\n")
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


# --- very_hard_cross_module_redesign -----------------------------------------

_CROSS_MODULE_CORRECT = (
    "import inventory\n\n\n"
    "def place_order(sku, qty):\n"
    "    return inventory.adjust(sku, -qty)\n"
)


def test_very_hard_cross_module_redesign_baseline_fails(tmp_path):
    task = _task("very_hard_cross_module_redesign")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_very_hard_cross_module_redesign_correct_passes(tmp_path):
    task = _task("very_hard_cross_module_redesign")
    task.fixture(tmp_path)
    _apply(tmp_path, "orders.py", _CROSS_MODULE_CORRECT)
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_very_hard_cross_module_redesign_incorrect_fails_when_still_coupled(tmp_path):
    task = _task("very_hard_cross_module_redesign")
    task.fixture(tmp_path)
    # Reformatted but still reaches into inventory.STOCK directly.
    _apply(
        tmp_path, "orders.py",
        "import inventory\n\n\ndef place_order(sku, qty):\n    inventory.STOCK[sku] = inventory.STOCK.get(sku, 0) - qty\n    return inventory.STOCK[sku]\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_very_hard_cross_module_redesign_accepts_an_inventory_that_dropped_its_own_stock_dict(tmp_path):
    # Regression: a live benchmark run found a real Claude implementation
    # that restructured inventory.py's internals (no module-level STOCK
    # dict at all -- e.g. a class-based store) while still exposing
    # adjust() as instructed. That's a legitimate "clean interface" -- the
    # task only requires orders.py not to reach into STOCK, never that
    # inventory.py keep STOCK as its own representation. The old validator,
    # which read inventory.STOCK directly, crashed with AttributeError on
    # this -- indistinguishable from a genuine failure.
    task = _task("very_hard_cross_module_redesign")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "inventory.py",
        "class _Store:\n"
        "    def __init__(self):\n        self._levels = {}\n\n"
        "    def adjust(self, sku, delta):\n"
        "        self._levels[sku] = self._levels.get(sku, 0) + delta\n"
        "        return self._levels[sku]\n\n\n"
        "_store = _Store()\n\n\n"
        "def adjust(sku, delta):\n    return _store.adjust(sku, delta)\n",
    )
    _apply(tmp_path, "orders.py", _CROSS_MODULE_CORRECT)
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


# --- tasks that deliberately have no validator ------------------------------

def test_undocumented_tasks_have_no_validator_and_it_is_explained_why():
    for task_id in ("hard_change_data_flow", "very_hard_auth_migration"):
        task = _task(task_id)
        assert task.validate is None
        assert "no validate" in task.expected_behavior.lower() or "not independently checked" in task.expected_behavior.lower()


def test_medium_add_endpoint_was_reclassified_to_easy():
    # Same complexity class as easy_helper: "add one new function returning
    # a fixed value" -- a genuine labeling inconsistency, not a rebalancing
    # pass (see the difficulty-audit section of the module docstring).
    task = _task("medium_add_endpoint")
    assert task.category == "easy"


def test_medium_add_endpoint_baseline_fails(tmp_path):
    task = _task("medium_add_endpoint")
    task.fixture(tmp_path)
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg


def test_medium_add_endpoint_correct_passes(tmp_path):
    task = _task("medium_add_endpoint")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "api.py",
        "def status_endpoint():\n    return {\"status\": \"ok\"}\n\n\n"
        "def users_endpoint():\n    return {\"users\": []}\n\n\n"
        "def health_check():\n    return {\"status\": \"ok\"}\n",
    )
    passed, msg = task.validate(tmp_path)
    assert passed is True, msg


def test_medium_add_endpoint_incorrect_fails_on_wrong_return_value(tmp_path):
    task = _task("medium_add_endpoint")
    task.fixture(tmp_path)
    _apply(
        tmp_path, "api.py",
        "def status_endpoint():\n    return {\"status\": \"ok\"}\n\n\n"
        "def users_endpoint():\n    return {\"users\": []}\n\n\n"
        "def health_check():\n    return {\"status\": \"healthy\"}\n",  # wrong value
    )
    passed, msg = task.validate(tmp_path)
    assert passed is False, msg
