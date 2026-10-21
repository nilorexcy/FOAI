
import importlib.util
import tempfile
import json
import threading
import urllib.request

from pathlib import Path
from http.server import ThreadingHTTPServer

spec = importlib.util.spec_from_file_location(
    "atelier",
    Path(__file__).with_name("app.py")
)

a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

a.DB = Path(tempfile.mkdtemp()) / "test.db"


def suite(*cases):
    return a.execute({"cases": list(cases)})


def check():
    good = suite({
        "endpoint": "/demo/users",
        "rules": [
            {"type": "status", "value": 200},
            {"type": "unique", "value": "id"}
        ]
    })

    assert good["decision"] == "READY"
    assert good["score"] == 100
    assert good["coverage"] == 100

    broken = suite({
        "endpoint": "/demo/broken",
        "rules": [
            {"type": "status", "value": 200},
            {"type": "unique", "value": "id"}
        ]
    })

    assert broken["decision"] == "REVIEW"
    assert broken["failed"] == 1

    error = suite({
        "endpoint": "/demo/error",
        "rules": [
            {"type": "status", "value": 500}
        ]
    })

    assert error["decision"] == "BLOCK"
    assert error["results"][0]["checks"][0]["passed"] is False

    negative = suite({
        "endpoint": "/demo/users/999",
        "rules": [
            {"type": "status", "value": 404},
            {"type": "exists", "path": "error"}
        ]
    })

    assert negative["decision"] == "READY"

    empty = suite({
        "endpoint": "/demo/users",
        "rules": []
    })

    assert empty["decision"] == "NOT TESTED"
    assert empty["score"] is None
    assert empty["coverage"] == 0

    cases = a.generate_cases({
        "method": "POST",
        "endpoint": "/demo/users"
    })

    assert len(cases) == 3
    assert suite(*cases)["decision"] == "READY"

    schema = a.infer([
        {"id": 1, "name": "A"},
        {"id": "2"}
    ])

    assert (
        schema["items"]["properties"]["id"]["type"]
        == "union"
    )

    assert "name" not in schema["items"]["required"]

    assert a.changes(
        a.infer({"id": 1}),
        a.infer({"id": "a"})
    )

    assert a.openapi_cases({
        "paths": {
            "/demo/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK"
                        }
                    }
                }
            }
        }
    })

    with a.db() as con:
        con.execute(
            """
            INSERT INTO baselines(key, schema)
            VALUES (?, ?)
            """,
            (
                "GET /demo/users",
                json.dumps(a.infer(a.USERS))
            )
        )

    contract = suite({
        "endpoint": "/demo/users",
        "compare": True,
        "rules": [
            {"type": "status", "value": 200}
        ]
    })

    assert contract["decision"] == "READY"

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        a.Handler
    )

    threading.Thread(
        target=server.serve_forever,
        daemon=True
    ).start()

    try:
        base = (
            f"http://127.0.0.1:{server.server_port}"
        )

        for route in [
            "/",
            "/style.css",
            "/script.js",
            "/api/history",
            "/api/latest"
        ]:
            with urllib.request.urlopen(
                base + route,
                timeout=3
            ) as response:
                assert response.status == 200

        def post(route, payload):
            request = urllib.request.Request(
                base + route,
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json"
                }
            )

            with urllib.request.urlopen(
                request,
                timeout=3
            ) as response:
                return json.load(response)

        result = post(
            "/api/run",
            {
                "cases": [{
                    "endpoint": "/demo/users",
                    "rules": [
                        {"type": "status", "value": 200}
                    ]
                }]
            }
        )

        assert result["decision"] == "READY"

        generated = post(
            "/api/generate",
            {
                "method": "GET",
                "endpoint": "/demo/users"
            }
        )

        assert len(generated["cases"]) >= 2

    finally:
        server.shutdown()
        server.server_close()

    print(
        "PASS — rule engine, negative tests, "
        "boundaries, schemas, OpenAPI, "
        "persistence, local HTTP"
    )


def extra_regressions():
    # JSON Schema: enum
    assert a.validate_schema(
        "good",
        {"enum": ["good"]}
    )

    assert not a.validate_schema(
        "bad",
        {"enum": ["good"]}
    )

    # JSON Schema: const
    assert not a.validate_schema(
        7,
        {"const": 10}
    )

    # Required property
    assert not a.validate_schema(
        {"x": 1},
        {
            "type": "object",
            "required": ["y"]
        }
    )

    # One untested failed API + one tested healthy API
    mixed = suite(
        {
            "endpoint": "/demo/error",
            "rules": []
        },
        {
            "endpoint": "/demo/users",
            "rules": [
                {"type": "status", "value": 200}
            ]
        }
    )

    assert mixed["coverage"] == 50.0
    assert mixed["score"] is None
    assert mixed["decision"] == "BLOCK"

    # Transport error must not stop later cases
    isolated = suite(
        {
            "endpoint": "https://127.0.0.1/nope",
            "rules": [
                {"type": "status", "value": 200}
            ]
        },
        {
            "endpoint": "/demo/users",
            "rules": [
                {"type": "status", "value": 200}
            ]
        }
    )

    assert isolated["caseCount"] == 2

    assert (
        isolated["results"][0]["decision"]
        == "BLOCK"
    )

    assert (
        isolated["results"][1]["decision"]
        == "READY"
    )

    assert (
        isolated["results"][0]["issues"][0]["source"]
        == "transport"
    )

    print(
        "PASS: schema keywords, accurate coverage, "
        "per-case transport isolation"
    )


if __name__ == "__main__":
    check()
    extra_regressions()
