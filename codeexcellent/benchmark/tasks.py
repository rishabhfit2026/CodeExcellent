"""The representative benchmark suite from section 20: three tasks at each
of trivial/easy/medium/hard/very_hard, each with a small fixture repo so a
--live run has something concrete to act on. Purely descriptive data --
no execution logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class BenchmarkTask:
    id: str
    category: str  # trivial/easy/medium/hard/very_hard
    request: str
    fixture: Callable[[Path], None]


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


ALL_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        "trivial_rename", "trivial",
        "Rename the parameter nam to name in greet.py",
        lambda r: _write(r, "greet.py", "def greet(nam):\n    return \"Hello, \" + nam\n"),
    ),
    BenchmarkTask(
        "trivial_typo", "trivial",
        "Fix the typo 'Wecome' (should be 'Welcome') in README.md",
        lambda r: _write(r, "README.md", "# Wecome to the project\n\nThis is a sample project.\n"),
    ),
    BenchmarkTask(
        "trivial_constant", "trivial",
        "Change the MAX_RETRIES constant in config.py from 3 to 5",
        lambda r: _write(r, "config.py", "MAX_RETRIES = 3\nTIMEOUT_SECONDS = 30\n"),
    ),
    BenchmarkTask(
        "easy_validation", "easy",
        "Add input validation to validate_email in validators.py to reject empty strings",
        lambda r: _write(r, "validators.py", "def validate_email(email):\n    return \"@\" in email\n"),
    ),
    BenchmarkTask(
        "easy_helper", "easy",
        "Add a small helper function slugify(text) to utils.py that lowercases text and replaces spaces with hyphens",
        lambda r: _write(r, "utils.py", "def truncate(text, length):\n    return text[:length]\n"),
    ),
    BenchmarkTask(
        "easy_endpoint_response", "easy",
        "Modify the /status endpoint in api.py to also include a 'version' field set to '1.0' in its response",
        lambda r: _write(r, "api.py", "def status_endpoint():\n    return {\"status\": \"ok\"}\n"),
    ),
    BenchmarkTask(
        "medium_add_endpoint", "medium",
        "Add a new health_check function to api.py that returns {'status': 'ok'} for a GET /health endpoint",
        lambda r: _write(r, "api.py", "def status_endpoint():\n    return {\"status\": \"ok\"}\n\n\ndef users_endpoint():\n    return {\"users\": []}\n"),
    ),
    BenchmarkTask(
        "medium_db_field", "medium",
        "Add an 'email' field to the User model in models.py",
        lambda r: _write(r, "models.py", "class User:\n    def __init__(self, id, name):\n        self.id = id\n        self.name = name\n"),
    ),
    BenchmarkTask(
        "medium_add_tests", "medium",
        "Add unit tests for the functions in calculator.py, placed in tests/test_calculator.py",
        lambda r: _write(r, "calculator.py", "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"),
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
    ),
    BenchmarkTask(
        "hard_add_auth", "hard",
        "Add basic API key authentication to api.py so all routes require a valid X-API-Key header",
        lambda r: _write(r, "api.py", "def status_endpoint(request):\n    return {\"status\": \"ok\"}\n\n\ndef users_endpoint(request):\n    return {\"users\": []}\n"),
    ),
    BenchmarkTask(
        "hard_change_data_flow", "hard",
        "Change the data pipeline in pipeline.py so records are processed in batches of 100 instead of one at a time",
        lambda r: _write(r, "pipeline.py", "def process(records):\n    for record in records:\n        handle(record)\n\n\ndef handle(record):\n    pass\n"),
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
    ),
]
