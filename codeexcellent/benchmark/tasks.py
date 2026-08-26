"""The representative benchmark suite: 16 tasks spanning trivial through
very_hard, each with a small fixture repo so a --live run has something
concrete to act on.

## Validation philosophy (phases 1-5 of the audit this file was rewritten
under)

`expected_behavior` is set for every task -- a human-readable statement of
what correct completion looks like, useful even without an automated
`validate`.

`validate(root) -> (passed, message)` is a programmatic, BEHAVIORAL check
where one is reliable: it imports the fixture module in-process and calls
its functions, asserting on real return values -- not on whether specific
source text appears. A task changing is not the same as a task succeeding.
The two exceptions are narrow and deliberate:
  - `trivial_typo`: the artifact IS prose (a README), so text comparison is
    the correct and only sensible check, not a weak proxy for one.
  - `very_hard_cross_module_redesign`: "orders.py no longer reaches into
    inventory.STOCK directly" is itself a claim about source structure, not
    runtime behavior, so a precise static check for that exact anti-pattern
    is the right tool, paired with a behavioral check that functionality is
    preserved.

Every validator was verified against three synthetic states before being
trusted (see tests/test_benchmark_validators.py): the untouched baseline
(must FAIL), a hand-written correct fix (must PASS), and, where a distinct
wrong implementation is meaningful, an incorrect fix (must FAIL).

Running the baseline check surfaced a real, non-obvious bug class: for
"preserve behavior while restructuring" tasks, a validator that ONLY checks
behavior preservation trivially PASSES a complete no-op, because unchanged
code obviously preserves its own behavior. `hard_refactor_service` and
`very_hard_architecture_migration` were fixed by pairing the behavioral
check with a minimal structural signal that something actually changed
(more than one function now exists; a new .py file now exists). For
`very_hard_auth_migration`, no such signal was available without inventing
an arbitrary keyword requirement (checking the string "oauth" appears
somewhere), which is exactly the superficial text-matching this audit
exists to move away from -- so that task has NO validator at all, not even
a partial one, despite an earlier draft having one that (incorrectly)
seemed to work.

Two tasks deliberately have NO validator, with the reason documented at
their definition, rather than a validator that would produce unreliable
false positives/negatives:
  - `hard_change_data_flow`: "batch processing" doesn't specify a contract
    (does `handle` start receiving lists? does a new function appear?) --
    any validator I write would false-negative on a differently-shaped but
    correct implementation.
  - `very_hard_auth_migration`: see above -- even its one seemingly-objective
    half (JWT backward compatibility) turned out not to discriminate "did
    nothing" from "did the task."

`hard_refactor_service` and `very_hard_architecture_migration` validate an
objective requirement (behavior preservation) plus a minimal, low-false-
positive-risk structural signal that a real change happened -- not the
FULL request (e.g. "split into validation/pricing/persistence" or "into
models.py/routes.py/services.py specifically" are style judgments no
black-box test can verify without over-constraining implementation choice).

## Fixture/task changes made during this audit (not orchestration changes,
just benchmark-data corrections)

- `easy_validation`'s original fixture (`"@" in email`) already returned
  False for an empty string by accident, so "reject empty strings" was
  already true without any fix -- the task was untestable as originally
  written. Rewritten so the fixture has a genuine bug matching the request.
- `hard_add_auth`'s original fixture took an unstructured `request` param
  with no defined header contract, so no behavioral test could call it with
  "a valid X-API-Key" at all. Rewritten with an explicit `headers: dict`
  parameter and a `VALID_API_KEY` constant, which is what makes real
  request/response validation (Phase 2's "verify an API response") possible
  instead of a substring check on the word "X-API-Key" -- which an agent
  could satisfy with just a comment, never touching enforcement.
- `medium_add_endpoint` reclassified trivial->easy: it and `easy_helper` are
  both "add one new function returning a fixed dict/value" -- essentially
  identical mechanical complexity that had landed in different bands. This
  is a correction of a specific, demonstrated inconsistency, not a
  rebalancing pass (see phase 8 of the audit).
- Added `easy_cli_flag`: the previous 15 tasks had no CLI-change task
  despite that being one of the requested task-type categories (bug fix,
  feature, refactor, test creation, API change, CLI change, config change,
  data transform, security change, multi-file change were all otherwise
  represented). One task, not several, per "identify gaps, don't invent
  fake complexity."
- `very_hard_cross_module_redesign`'s `inventory.adjust()` didn't return
  the new stock level, which meant even a genuinely clean interface-based
  `place_order` was forced to read `inventory.STOCK[sku]` directly just to
  report the result back -- the task as originally fixture'd could not
  actually be completed without tripping the "no longer reaches into
  inventory.STOCK directly" check. Found by phase-4 testing (my own
  hand-written "correct" implementation failed validation). Fixed by
  having `adjust()` return the new value.
"""
from __future__ import annotations

import contextlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_PYTEST_TIMEOUT_SECONDS = 30


@dataclass
class BenchmarkTask:
    id: str
    category: str  # trivial/easy/medium/hard/very_hard
    request: str
    fixture: Callable[[Path], None]
    expected_behavior: str
    validate: Callable[[Path], tuple[bool, str]] | None = None


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _read(root: Path, name: str) -> str:
    return (root / name).read_text(errors="ignore")


@contextlib.contextmanager
def _isolated_imports(root: Path):
    """Yields an `import_(name)` function that loads `<root>/<name>.py` as a
    module, usable by validators that need to call real functions rather
    than pattern-match source text.

    `root` is added to sys.path for the duration so a fixture's own
    intra-repo imports resolve (e.g. very_hard_cross_module_redesign's
    orders.py doing `import inventory`); every module loaded through this
    is removed from sys.modules again on exit. Without that cleanup,
    Python's import cache (keyed by module name only) would let a *later*
    task's same-named fixture module (e.g. another task's own "api.py")
    silently reuse an *earlier* task's cached module -- a real cross-task
    contamination risk, since validators run in-process rather than in a
    subprocess like Claude's own edits do.
    """
    root_str = str(root)
    added_path = root_str not in sys.path
    if added_path:
        sys.path.insert(0, root_str)
    loaded: list[str] = []

    def import_(name: str):
        sys.modules.pop(name, None)
        import importlib.util

        spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loaded.append(name)
        spec.loader.exec_module(module)
        return module

    try:
        yield import_
    finally:
        for name in loaded:
            sys.modules.pop(name, None)
        if added_path:
            try:
                sys.path.remove(root_str)
            except ValueError:
                pass


# --- trivial ---------------------------------------------------------------

def _validate_trivial_rename(root: Path) -> tuple[bool, str]:
    # Behavioral, not textual: calls the real function rather than grepping
    # for "nam" (which would false-positive on "name" itself via substring).
    with _isolated_imports(root) as import_:
        try:
            module = import_("greet")
        except Exception as exc:
            return False, f"greet.py failed to import: {exc!r}"
        if not hasattr(module, "greet"):
            return False, "greet() no longer exists"
        try:
            result = module.greet("World")
        except TypeError as exc:
            return False, f"greet() could not be called with a single positional argument: {exc!r}"
        if result != "Hello, World":
            return False, f"greet('World') returned {result!r}, expected 'Hello, World'"
        import inspect

        params = list(inspect.signature(module.greet).parameters)
        if params and params[0] == "nam":
            return False, "the parameter is still named 'nam'"
    return True, "greet() renamed and behaves correctly"


def _validate_trivial_typo(root: Path) -> tuple[bool, str]:
    # The artifact is prose (a README), so text comparison is the correct
    # tool here, not a proxy for behavior there isn't any of.
    content = _read(root, "README.md")
    if "Wecome" in content:
        return False, "typo 'Wecome' still present"
    if "Welcome" not in content:
        return False, "expected corrected word 'Welcome' not found"
    return True, "typo fixed"


def _validate_trivial_constant(root: Path) -> tuple[bool, str]:
    # Imports and reads the real attribute value rather than grepping, so it
    # also verifies the "must NOT change" half (TIMEOUT_SECONDS untouched) --
    # a text-substring check for "MAX_RETRIES = 5" alone can't see that.
    with _isolated_imports(root) as import_:
        try:
            module = import_("config")
        except Exception as exc:
            return False, f"config.py failed to import: {exc!r}"
        if getattr(module, "MAX_RETRIES", None) != 5:
            return False, f"MAX_RETRIES is {getattr(module, 'MAX_RETRIES', 'MISSING')!r}, expected 5"
        if getattr(module, "TIMEOUT_SECONDS", None) != 30:
            return False, f"TIMEOUT_SECONDS changed unexpectedly to {getattr(module, 'TIMEOUT_SECONDS', 'MISSING')!r} (should stay 30)"
    return True, "MAX_RETRIES updated correctly; TIMEOUT_SECONDS untouched"


# --- easy --------------------------------------------------------------

def _validate_easy_validation(root: Path) -> tuple[bool, str]:
    with _isolated_imports(root) as import_:
        try:
            module = import_("validators")
        except Exception as exc:
            return False, f"validators.py failed to import: {exc!r}"
        if not hasattr(module, "validate_email"):
            return False, "validate_email no longer exists"
        try:
            empty_result = module.validate_email("")
        except Exception:
            empty_result = False  # raising on invalid input is an acceptable rejection
        if empty_result:
            return False, "validate_email('') is still truthy -- empty strings are not rejected"
        if not module.validate_email("user@example.com"):
            return False, "validate_email('user@example.com') should still pass"
        if module.validate_email("not-an-email"):
            return False, "validate_email('not-an-email') should still be rejected (pre-existing behavior)"
    return True, "empty strings rejected; valid/invalid email handling preserved"


def _validate_easy_helper(root: Path) -> tuple[bool, str]:
    with _isolated_imports(root) as import_:
        try:
            module = import_("utils")
        except Exception as exc:
            return False, f"utils.py failed to import: {exc!r}"
        if not hasattr(module, "slugify"):
            return False, "slugify() was not added"
        try:
            result = module.slugify("Hello World")
        except Exception as exc:
            return False, f"slugify('Hello World') raised {exc!r}"
        if result != "hello-world":
            return False, f"slugify('Hello World') returned {result!r}, expected 'hello-world'"
        if module.truncate("hello", 3) != "hel":
            return False, "truncate() behavior was changed (should be untouched)"
    return True, "slugify() added correctly; truncate() untouched"


def _validate_easy_endpoint_response(root: Path) -> tuple[bool, str]:
    with _isolated_imports(root) as import_:
        try:
            module = import_("api")
        except Exception as exc:
            return False, f"api.py failed to import: {exc!r}"
        try:
            result = module.status_endpoint()
        except Exception as exc:
            return False, f"status_endpoint() raised {exc!r}"
        if not isinstance(result, dict) or result.get("status") != "ok":
            return False, f"status_endpoint() no longer returns status='ok': {result!r}"
        if result.get("version") != "1.0":
            return False, f"status_endpoint() is missing version='1.0': {result!r}"
    return True, "status_endpoint() includes version '1.0' alongside the existing status field"


def _validate_medium_add_endpoint(root: Path) -> tuple[bool, str]:
    with _isolated_imports(root) as import_:
        try:
            module = import_("api")
        except Exception as exc:
            return False, f"api.py failed to import: {exc!r}"
        if not hasattr(module, "health_check"):
            return False, "health_check() was not added"
        try:
            result = module.health_check()
        except Exception as exc:
            return False, f"health_check() raised {exc!r}"
        if result != {"status": "ok"}:
            return False, f"health_check() returned {result!r}, expected {{'status': 'ok'}}"
        if module.status_endpoint() != {"status": "ok"}:
            return False, "status_endpoint() behavior was changed unexpectedly"
        if module.users_endpoint() != {"users": []}:
            return False, "users_endpoint() behavior was changed unexpectedly"
    return True, "health_check() added correctly; existing endpoints unchanged"


def _validate_easy_cli_flag(root: Path) -> tuple[bool, str]:
    # argparse's default error handling writes usage/error text to STDERR
    # and calls sys.exit() -- redirecting only stdout would let that leak
    # into whatever process is running this validator (and it derives its
    # displayed program name from OUR sys.argv[0], not the fixture's, which
    # is confusing besides). Both streams are captured here.
    import io

    with _isolated_imports(root) as import_:
        try:
            module = import_("cli")
        except Exception as exc:
            return False, f"cli.py failed to import: {exc!r}"
        if not hasattr(module, "main"):
            return False, "main() no longer exists"

        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                module.main(["World"])
        except SystemExit:
            pass
        except Exception as exc:
            return False, f"main(['World']) raised {exc!r}"
        base_output = out.getvalue()
        if "Hello, World" not in base_output:
            return False, f"main(['World']) no longer prints the greeting (got: {base_output!r})"
        if "Verbose mode enabled" in base_output:
            return False, "verbose output appeared even without --verbose"

        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                module.main(["World", "--verbose"])
        except SystemExit:
            pass
        except Exception as exc:
            return False, f"main(['World', '--verbose']) raised {exc!r}"
        verbose_output = out.getvalue()
        if "Verbose mode enabled" not in verbose_output:
            return False, f"--verbose did not enable verbose output (got: {verbose_output!r})"
        if "Hello, World" not in verbose_output:
            return False, "--verbose broke the normal greeting output"
    return True, "--verbose flag added without changing default behavior"


# --- medium ------------------------------------------------------------

def _validate_medium_db_field(root: Path) -> tuple[bool, str]:
    import inspect

    with _isolated_imports(root) as import_:
        try:
            module = import_("models")
        except Exception as exc:
            return False, f"models.py failed to import: {exc!r}"
        User = getattr(module, "User", None)
        if User is None:
            return False, "User class no longer exists"
        sig = inspect.signature(User.__init__)
        if "email" not in sig.parameters:
            return False, "User.__init__ has no 'email' parameter"
        try:
            user = User(1, "Alice", email="alice@example.com")
        except TypeError:
            try:
                user = User(1, "Alice", "alice@example.com")
            except TypeError as exc:
                return False, f"could not construct a User with an email: {exc!r}"
        if getattr(user, "email", None) != "alice@example.com":
            return False, "User.email was not stored correctly"
        if user.id != 1 or user.name != "Alice":
            return False, "adding email broke the existing id/name fields"
    return True, "User accepts and stores an email field; id/name preserved"


def _validate_medium_add_tests(root: Path) -> tuple[bool, str]:
    test_path = root / "tests" / "test_calculator.py"
    if not test_path.exists():
        return False, "tests/test_calculator.py was not created"

    content = test_path.read_text(errors="ignore")
    if not re.search(r"\badd\b", content) or not re.search(r"\bsubtract\b", content):
        return False, "the test file doesn't appear to exercise both add and subtract"

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q"],
            cwd=root, capture_output=True, text=True, timeout=_PYTEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, "running the new tests timed out"
    except FileNotFoundError:
        return False, "pytest is not available to run the new tests"

    output = result.stdout + result.stderr
    if "no tests ran" in output.lower() or "collected 0 items" in output.lower():
        return False, "no tests were actually collected"
    if result.returncode != 0:
        return False, f"pytest failed:\n{output[-800:]}"
    return True, "tests/test_calculator.py exists and passes"


# --- hard ----------------------------------------------------------------

def _validate_hard_refactor_service(root: Path) -> tuple[bool, str]:
    # Checks the objective requirement (process_order(order) must keep
    # returning the same result for the same input) PLUS a minimal
    # structural signal that some decomposition actually happened (more
    # than the original single function now exists at module level).
    # Behavior preservation alone isn't sufficient: an untouched file
    # trivially "preserves" its own behavior, so a validator that only
    # checked that would pass a complete no-op -- caught during phase-4
    # baseline verification, see tests/test_benchmark_validators.py. This
    # doesn't verify the SPECIFIC split (validation/pricing/persistence)
    # the task suggests -- that's a style judgment -- only that a split
    # happened at all.
    import inspect

    with _isolated_imports(root) as import_:
        try:
            module = import_("order_service")
        except Exception as exc:
            return False, f"order_service.py failed to import: {exc!r}"
        if not hasattr(module, "process_order"):
            return False, "process_order no longer exists"

        function_count = sum(
            1 for _, obj in inspect.getmembers(module, inspect.isfunction)
            if obj.__module__ == module.__name__
        )
        if function_count <= 1:
            return False, "process_order was not decomposed -- only one function still exists in the module"

        cases = [
            ({"items": [{"price": 10, "qty": 2}]}, 20),
            ({"items": [{"price": 10, "qty": 2}], "coupon": True}, 18.0),
        ]
        for order, expected in cases:
            try:
                result = module.process_order(order)
            except Exception as exc:
                return False, f"process_order({order!r}) raised {exc!r}"
            if result != expected:
                return False, f"process_order({order!r}) returned {result!r}, expected {expected!r}"

        try:
            module.process_order({"items": []})
            return False, "process_order({'items': []}) should raise ValueError but didn't"
        except ValueError:
            pass
        except Exception as exc:
            return False, f"process_order({{'items': []}}) raised {exc!r}, expected ValueError"
    return True, "process_order behavior preserved across the refactor"


def _validate_hard_add_auth(root: Path) -> tuple[bool, str]:
    with _isolated_imports(root) as import_:
        try:
            module = import_("api")
        except Exception as exc:
            return False, f"api.py failed to import: {exc!r}"
        valid_key = getattr(module, "VALID_API_KEY", "secret-key-123")
        ok_headers = {"X-API-Key": valid_key}
        bad_header_variants = [{}, {"X-API-Key": "wrong-key"}]

        for name, expected_ok in (("status_endpoint", {"status": "ok"}), ("users_endpoint", {"users": []})):
            fn = getattr(module, name, None)
            if fn is None:
                return False, f"{name} no longer exists"
            try:
                ok_result = fn(ok_headers)
            except Exception as exc:
                return False, f"{name} raised {exc!r} even with a valid X-API-Key"
            if ok_result != expected_ok:
                return False, f"{name} with a valid key returned {ok_result!r}, expected {expected_ok!r}"

            for bad_headers in bad_header_variants:
                try:
                    bad_result = fn(bad_headers)
                except Exception:
                    continue  # raising is an acceptable way to reject
                if bad_result == expected_ok:
                    return False, f"{name} returned the normal response without a valid X-API-Key ({bad_headers!r})"
    return True, "endpoints require a valid X-API-Key and behave normally when one is provided"


# hard_change_data_flow has no validate(): see module docstring.


# --- very_hard -------------------------------------------------------------

def _validate_very_hard_architecture_migration(root: Path) -> tuple[bool, str]:
    # Behavior preservation is checked without assuming anything about the
    # new internal module layout -- only that app.py's PUBLIC entry points
    # (the stable contract named in expected_behavior) still work. That
    # alone isn't sufficient, though: an untouched app.py trivially
    # "preserves" its own behavior, so a no-op would otherwise pass (caught
    # during phase-4 baseline verification). A migration into a package
    # necessarily means at least one new .py file besides app.py, so that's
    # checked as a minimal, low-risk-of-false-positive structural signal
    # that something actually happened.
    other_py_files = [p for p in root.glob("*.py") if p.name != "app.py"]
    if not other_py_files:
        return False, "no additional .py files exist -- app.py was not split into a package"

    with _isolated_imports(root) as import_:
        try:
            module = import_("app")
        except Exception as exc:
            return False, f"app.py failed to import: {exc!r}"
        if not hasattr(module, "route_users") or not hasattr(module, "service_greet"):
            return False, "app.py no longer exposes route_users/service_greet"
        try:
            users = module.route_users()
        except Exception as exc:
            return False, f"route_users() raised {exc!r}"
        if not users or not hasattr(users[0], "name"):
            return False, "route_users() no longer returns the expected User-like objects"
        try:
            greeting = module.service_greet(users[0])
        except Exception as exc:
            return False, f"service_greet() raised {exc!r}"
        if "Hello" not in greeting:
            return False, f"service_greet() behavior changed unexpectedly: {greeting!r}"
    return True, "app.py's public entry points still behave as before"


# very_hard_auth_migration has no validate(): an earlier draft of this
# validator checked only JWT backward compatibility (the one part of the
# task with a fixed, objective contract -- "OAuth2 is introduced" has no
# fixed shape to check without a real OAuth provider, which a sandboxed
# fixture can't have). But phase-4 baseline verification showed that check
# is trivially satisfied by a complete no-op too: an untouched auth.py
# obviously still verifies its own pre-existing JWTs. Unlike
# hard_refactor_service / very_hard_architecture_migration, there's no
# available structural signal here that "some migration happened" without
# inventing an arbitrary keyword requirement (e.g. checking for the string
# "oauth" in the source), which would be exactly the superficial,
# gameable text-matching this audit was set up to move away from. A
# validator that cannot distinguish "did nothing" from "did the task" is
# worse than no validator, so this task is left unvalidated.


def _validate_very_hard_cross_module_redesign(root: Path) -> tuple[bool, str]:
    # Hybrid: "no longer reaches into inventory.STOCK directly" is itself a
    # claim about source structure, so a precise check for that exact
    # pattern is the right tool (not a superficial text match -- it's the
    # literal definition of the requested change), paired with a behavioral
    # check that stock adjustment still works correctly.
    orders_src = _read(root, "orders.py")
    if re.search(r"inventory\s*\.\s*STOCK", orders_src):
        return False, "orders.py still reaches into inventory.STOCK directly"

    with _isolated_imports(root) as import_:
        try:
            inventory = import_("inventory")
            orders = import_("orders")  # if orders.py does `import inventory`, reuses the same instance
        except Exception as exc:
            return False, f"orders.py/inventory.py failed to import: {exc!r}"
        if not hasattr(orders, "place_order"):
            return False, "place_order no longer exists"

        inventory.STOCK["sku-1"] = 10
        try:
            result = orders.place_order("sku-1", 3)
        except Exception as exc:
            return False, f"place_order() raised {exc!r}"
        if inventory.STOCK.get("sku-1") != 7:
            return False, f"placing an order did not correctly decrement stock (STOCK={inventory.STOCK!r})"
        if result != 7:
            return False, f"place_order() returned {result!r}, expected the new stock level 7"
    return True, "orders.py no longer touches inventory.STOCK directly; stock adjustment behavior is preserved"


ALL_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        "trivial_rename", "trivial",
        "Rename the parameter nam to name in greet.py",
        lambda r: _write(r, "greet.py", "def greet(nam):\n    return \"Hello, \" + nam\n"),
        expected_behavior="The parameter is renamed from nam to name everywhere it's used in greet.py; behavior is unchanged.",
        validate=_validate_trivial_rename,
    ),
    BenchmarkTask(
        "trivial_typo", "trivial",
        "Fix the typo 'Wecome' (should be 'Welcome') in README.md",
        lambda r: _write(r, "README.md", "# Wecome to the project\n\nThis is a sample project.\n"),
        expected_behavior="'Wecome' is corrected to 'Welcome'; no other content changes.",
        validate=_validate_trivial_typo,
    ),
    BenchmarkTask(
        "trivial_constant", "trivial",
        "Change the MAX_RETRIES constant in config.py from 3 to 5",
        lambda r: _write(r, "config.py", "MAX_RETRIES = 3\nTIMEOUT_SECONDS = 30\n"),
        expected_behavior="MAX_RETRIES is set to 5; TIMEOUT_SECONDS and everything else is untouched.",
        validate=_validate_trivial_constant,
    ),
    BenchmarkTask(
        "easy_validation", "easy",
        "Add input validation to validate_email in validators.py to reject empty strings",
        lambda r: _write(
            r, "validators.py",
            "def validate_email(email):\n"
            "    if not email:\n"
            "        return True  # BUG: empty strings are incorrectly accepted\n"
            "    return \"@\" in email\n",
        ),
        expected_behavior="validate_email returns False (or raises) for an empty string, and still validates non-empty emails as before.",
        validate=_validate_easy_validation,
    ),
    BenchmarkTask(
        "easy_helper", "easy",
        "Add a small helper function slugify(text) to utils.py that lowercases text and replaces spaces with hyphens",
        lambda r: _write(r, "utils.py", "def truncate(text, length):\n    return text[:length]\n"),
        expected_behavior="A new slugify(text) function exists in utils.py, e.g. slugify('Hello World') == 'hello-world'; truncate is untouched.",
        validate=_validate_easy_helper,
    ),
    BenchmarkTask(
        "easy_endpoint_response", "easy",
        "Modify the /status endpoint in api.py to also include a 'version' field set to '1.0' in its response",
        lambda r: _write(r, "api.py", "def status_endpoint():\n    return {\"status\": \"ok\"}\n"),
        expected_behavior="status_endpoint's returned dict includes 'version': '1.0' in addition to the existing 'status' key.",
        validate=_validate_easy_endpoint_response,
    ),
    BenchmarkTask(
        "easy_cli_flag", "easy",
        "Add a --verbose flag to cli.py. When passed, main() should additionally print 'Verbose mode enabled' before the greeting.",
        lambda r: _write(
            r, "cli.py",
            "import argparse\n\n\n"
            "def build_parser():\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument(\"name\")\n"
            "    return parser\n\n\n"
            "def main(argv=None):\n"
            "    args = build_parser().parse_args(argv)\n"
            "    print(f\"Hello, {args.name}\")\n\n\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n",
        ),
        expected_behavior="cli.py gains a --verbose flag; with it, main() prints 'Verbose mode enabled' in addition to the existing greeting; without it, output is unchanged.",
        validate=_validate_easy_cli_flag,
    ),
    BenchmarkTask(
        "medium_add_endpoint", "easy",
        "Add a new health_check function to api.py that returns {'status': 'ok'} for a GET /health endpoint",
        lambda r: _write(r, "api.py", "def status_endpoint():\n    return {\"status\": \"ok\"}\n\n\ndef users_endpoint():\n    return {\"users\": []}\n"),
        expected_behavior="A new health_check function exists returning {'status': 'ok'}; existing endpoints are unchanged.",
        validate=_validate_medium_add_endpoint,
    ),
    BenchmarkTask(
        "medium_db_field", "medium",
        "Add an 'email' field to the User model in models.py",
        lambda r: _write(r, "models.py", "class User:\n    def __init__(self, id, name):\n        self.id = id\n        self.name = name\n"),
        expected_behavior="User accepts and stores an email attribute in addition to id and name.",
        validate=_validate_medium_db_field,
    ),
    BenchmarkTask(
        "medium_add_tests", "medium",
        "Add unit tests for the functions in calculator.py, placed in tests/test_calculator.py",
        lambda r: _write(r, "calculator.py", "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"),
        expected_behavior="tests/test_calculator.py exists with passing tests covering add and subtract.",
        validate=_validate_medium_add_tests,
    ),
    BenchmarkTask(
        "hard_refactor_service", "hard",
        "Refactor process_order in order_service.py to separate validation, pricing, and persistence into smaller functions",
        lambda r: _write(
            r, "order_service.py",
            "def process_order(order):\n"
            "    if not order.get('items'):\n        raise ValueError('empty order')\n"
            "    total = sum(i['price'] * i['qty'] for i in order['items'])\n"
            "    if order.get('coupon'):\n        total *= 0.9\n"
            "    print(f'saving order total={total}')\n"
            "    return total\n",
        ),
        expected_behavior="process_order's logic is split into smaller, separately named functions (validation/pricing/persistence); process_order(order) still returns the same total for the same input.",
        validate=_validate_hard_refactor_service,
    ),
    BenchmarkTask(
        "hard_add_auth", "hard",
        "Add basic API key authentication to api.py: status_endpoint(headers) and users_endpoint(headers) should only return their normal "
        "response when headers contains 'X-API-Key' set to the value of VALID_API_KEY; otherwise they should reject the request "
        "(raise or return something other than the normal response) instead of the normal response.",
        lambda r: _write(
            r, "api.py",
            "VALID_API_KEY = \"secret-key-123\"\n\n\n"
            "def status_endpoint(headers):\n    return {\"status\": \"ok\"}\n\n\n"
            "def users_endpoint(headers):\n    return {\"users\": []}\n",
        ),
        expected_behavior="Requests without a valid X-API-Key header are rejected before reaching the endpoint logic; requests with a valid key succeed as before.",
        validate=_validate_hard_add_auth,
    ),
    BenchmarkTask(
        "hard_change_data_flow", "hard",
        "Change the data pipeline in pipeline.py so records are processed in batches of 100 instead of one at a time",
        lambda r: _write(r, "pipeline.py", "def process(records):\n    for record in records:\n        handle(record)\n\n\ndef handle(record):\n    pass\n"),
        expected_behavior="process(records) groups records into batches of up to 100 before handling them, rather than calling handle() once per record. "
        "No validate(): the task doesn't specify whether handle() should start receiving lists, a new function should appear, or something else -- "
        "any validator would risk false-negatives on a differently-shaped but correct implementation. See module docstring.",
    ),
    BenchmarkTask(
        "very_hard_architecture_migration", "very_hard",
        "Migrate app.py from a single-file script into a package with separate modules for routes, models, and services, preserving behavior",
        lambda r: _write(
            r, "app.py",
            "class User:\n    def __init__(self, id, name):\n        self.id = id\n        self.name = name\n\n\n"
            "def route_users():\n    return [User(1, 'a')]\n\n\n"
            "def service_greet(user):\n    return f'Hello {user.name}'\n",
        ),
        expected_behavior="User/routes/services move into separate modules (e.g. models.py, routes.py, services.py) with the same behavior as before; app.py's public entry points still work.",
        validate=_validate_very_hard_architecture_migration,
    ),
    BenchmarkTask(
        "very_hard_auth_migration", "very_hard",
        "Migrate authentication in auth.py from JWT to OAuth2, preserving backward compatibility with existing JWT tokens during a transition period",
        lambda r: _write(
            r, "auth.py",
            "import hashlib\n\n\n"
            "def issue_jwt(user_id):\n    return hashlib.sha256(str(user_id).encode()).hexdigest()\n\n\n"
            "def verify_jwt(token, user_id):\n    return token == issue_jwt(user_id)\n",
        ),
        expected_behavior="An OAuth2 flow is introduced alongside the existing JWT verification, so previously issued JWT tokens still verify successfully during the transition. "
        "No validate(): even the objective backward-compatibility half is trivially satisfied by a no-op (an untouched file still verifies its own "
        "pre-existing JWTs) -- see module docstring.",
    ),
    BenchmarkTask(
        "very_hard_cross_module_redesign", "very_hard",
        "Redesign orders.py and inventory.py to remove the tight coupling between them by introducing a clean interface",
        lambda r: (
            _write(r, "inventory.py", "STOCK = {}\n\n\ndef adjust(sku, delta):\n    STOCK[sku] = STOCK.get(sku, 0) + delta\n    return STOCK[sku]\n"),
            _write(
                r, "orders.py",
                "import inventory\n\n\n"
                "def place_order(sku, qty):\n"
                "    inventory.STOCK[sku] = inventory.STOCK.get(sku, 0) - qty\n"
                "    return inventory.STOCK[sku]\n",
            ),
        ),
        expected_behavior="orders.py no longer reaches into inventory.STOCK directly; it calls a function/interface exposed by inventory.py instead, with equivalent behavior.",
        validate=_validate_very_hard_cross_module_redesign,
    ),
]
