"""The representative benchmark suite from section 20: three tasks at each
of trivial/easy/medium/hard/very_hard, each with a small fixture repo so a
--live run has something concrete to act on.

Section 8 of the current spec asks for task/repository/expected-behavior/
validation/success-criteria as first-class fields. `expected_behavior` is
set for every task (cheap, and gives a human -- or a future automated
reviewer -- something concrete to check against even without a `validate`
function). `validate` is an optional programmatic success check; it's
filled in for a representative subset (one per difficulty band) rather than
all 15, per "do not generate hundreds of fake benchmark tasks yet -- create
the framework so real ones can be added later." Adding a `validate` to any
remaining task is a self-contained addition; nothing else needs to change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


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


def _validate_trivial_rename(root: Path) -> tuple[bool, str]:
    # \b (word boundary) matters here: a naive "in content" substring check
    # would flag "name" as still containing "nam" and always fail.
    content = _read(root, "greet.py")
    if re.search(r"\bnam\b", content):
        return False, "parameter 'nam' was not renamed"
    if "def greet(name)" not in content:
        return False, "expected signature 'def greet(name)' not found"
    return True, "parameter renamed correctly"


def _validate_trivial_typo(root: Path) -> tuple[bool, str]:
    content = _read(root, "README.md")
    if "Wecome" in content:
        return False, "typo 'Wecome' still present"
    if "Welcome" not in content:
        return False, "expected corrected word 'Welcome' not found"
    return True, "typo fixed"


def _validate_trivial_constant(root: Path) -> tuple[bool, str]:
    # \b after the 5 avoids "MAX_RETRIES = 5" matching "MAX_RETRIES = 50".
    content = _read(root, "config.py")
    if not re.search(r"MAX_RETRIES\s*=\s*5\b", content):
        return False, "MAX_RETRIES was not changed to 5"
    return True, "constant updated correctly"


def _validate_medium_db_field(root: Path) -> tuple[bool, str]:
    content = _read(root, "models.py")
    if "email" not in content:
        return False, "no 'email' field found in models.py"
    return True, "email field present"


def _validate_hard_add_auth(root: Path) -> tuple[bool, str]:
    content = _read(root, "api.py").lower()
    if "x-api-key" not in content:
        return False, "no reference to the X-API-Key header found"
    return True, "API key header handling present"


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
        lambda r: _write(r, "validators.py", "def validate_email(email):\n    return \"@\" in email\n"),
        expected_behavior="validate_email returns False (or raises) for an empty string, and still validates non-empty emails as before.",
    ),
    BenchmarkTask(
        "easy_helper", "easy",
        "Add a small helper function slugify(text) to utils.py that lowercases text and replaces spaces with hyphens",
        lambda r: _write(r, "utils.py", "def truncate(text, length):\n    return text[:length]\n"),
        expected_behavior="A new slugify(text) function exists in utils.py, e.g. slugify('Hello World') == 'hello-world'; truncate is untouched.",
    ),
    BenchmarkTask(
        "easy_endpoint_response", "easy",
        "Modify the /status endpoint in api.py to also include a 'version' field set to '1.0' in its response",
        lambda r: _write(r, "api.py", "def status_endpoint():\n    return {\"status\": \"ok\"}\n"),
        expected_behavior="status_endpoint's returned dict includes 'version': '1.0' in addition to the existing 'status' key.",
    ),
    BenchmarkTask(
        "medium_add_endpoint", "medium",
        "Add a new health_check function to api.py that returns {'status': 'ok'} for a GET /health endpoint",
        lambda r: _write(r, "api.py", "def status_endpoint():\n    return {\"status\": \"ok\"}\n\n\ndef users_endpoint():\n    return {\"users\": []}\n"),
        expected_behavior="A new health_check function exists returning {'status': 'ok'}; existing endpoints are unchanged.",
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
    ),
    BenchmarkTask(
        "hard_add_auth", "hard",
        "Add basic API key authentication to api.py so all routes require a valid X-API-Key header",
        lambda r: _write(r, "api.py", "def status_endpoint(request):\n    return {\"status\": \"ok\"}\n\n\ndef users_endpoint(request):\n    return {\"users\": []}\n"),
        expected_behavior="Requests without a valid X-API-Key header are rejected before reaching the endpoint logic; requests with a valid key succeed as before.",
        validate=_validate_hard_add_auth,
    ),
    BenchmarkTask(
        "hard_change_data_flow", "hard",
        "Change the data pipeline in pipeline.py so records are processed in batches of 100 instead of one at a time",
        lambda r: _write(r, "pipeline.py", "def process(records):\n    for record in records:\n        handle(record)\n\n\ndef handle(record):\n    pass\n"),
        expected_behavior="process(records) groups records into batches of up to 100 before handling them, rather than calling handle() once per record.",
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
        expected_behavior="An OAuth2 flow is introduced alongside the existing JWT verification, so previously issued JWT tokens still verify successfully during the transition.",
    ),
    BenchmarkTask(
        "very_hard_cross_module_redesign", "very_hard",
        "Redesign orders.py and inventory.py to remove the tight coupling between them by introducing a clean interface",
        lambda r: (
            _write(r, "inventory.py", "STOCK = {}\n\n\ndef adjust(sku, delta):\n    STOCK[sku] = STOCK.get(sku, 0) + delta\n"),
            _write(
                r, "orders.py",
                "import inventory\n\n\n"
                "def place_order(sku, qty):\n"
                "    inventory.STOCK[sku] = inventory.STOCK.get(sku, 0) - qty\n"
                "    return inventory.STOCK[sku]\n",
            ),
        ),
        expected_behavior="orders.py no longer reaches into inventory.STOCK directly; it calls a function/interface exposed by inventory.py instead, with equivalent behavior.",
    ),
]
