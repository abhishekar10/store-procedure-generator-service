"""Writes the service's OpenAPI spec to docs/openapi.json, the checked-in
copy that tests/test_contract.py pins the API against.

    python -m app.openapi_export
"""

import json

from app.config import REPO_ROOT

SPEC_PATH = REPO_ROOT / "docs" / "openapi.json"


def render() -> str:
    from app.main import app

    return json.dumps(app.openapi(), indent=2) + "\n"


if __name__ == "__main__":
    SPEC_PATH.parent.mkdir(exist_ok=True)
    SPEC_PATH.write_text(render())
    print(f"Wrote {SPEC_PATH}")
