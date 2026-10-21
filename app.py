
import copy
import json
import sqlite3
import time
import re
import threading
import ipaddress
import socket
import ssl
import http.client
import os

from jsonschema import Draft202012Validator, exceptions as schema_errors
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DB = ROOT / "ruleguard.db"
MAX_BYTES = 512_000
LOCK = threading.Lock()

USERS = [
    {"id": 1, "name": "Asha", "email": "asha@example.org", "age": 23},
    {"id": 2, "name": "Rio", "email": "rio@example.org", "age": 28},
]

RULES = {
    "status", "latency", "exists", "equals",
    "type", "nonempty", "unique", "range", "schema"
}

WEIGHTS = {
    "critical": 35,
    "high": 18,
    "medium": 8,
    "low": 3
}


def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs("
        "id INTEGER PRIMARY KEY,created TEXT,decision TEXT,"
        "score INTEGER,coverage REAL,document TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS baselines("
        "key TEXT PRIMARY KEY,schema TEXT)"
    )
    conn.commit()
    return conn


def infer(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {
            "type": "array",
            "items": merge([infer(x) for x in value])
        }
    if isinstance(value, dict):
        return {
            "type": "object",
            "required": sorted(value),
            "properties": {
                k: infer(v) for k, v in value.items()
            }
        }
    return {"type": "unknown"}


def merge(shapes):
    if not shapes:
        return {"type": "unknown"}

    kinds = sorted({x["type"] for x in shapes})

    if len(kinds) > 1:
        return {"type": "union", "anyOf": kinds}

    kind = kinds[0]

    if kind == "array":
        return {
            "type": "array",
            "items": merge([x["items"] for x in shapes])
        }

    if kind == "object":
        names = set().union(
            *(set(x["properties"]) for x in shapes)
        )

        required = set(shapes[0]["required"])
        for x in shapes[1:]:
            required.intersection_update(x["required"])

        return {
            "type": "object",
            "required": sorted(required),
            "properties": {
                n: merge([
                    x["properties"][n]
                    for x in shapes
                    if n in x["properties"]
                ])
                for n in sorted(names)
            }
        }

    return {"type": kind}


def changes(old, new, path="$"):
    out = []

    if old.get("type") != new.get("type"):
        return [{
            "path": path,
            "severity": "critical",
            "message": (
                f"Type {old.get('type')} → {new.get('type')}"
            )
        }]

    kind = old.get("type")

    if kind == "union" and old.get("anyOf") != new.get("anyOf"):
        out.append({
            "path": path,
            "severity": "high",
            "message": "Accepted types changed"
        })

    if kind == "array":
        out += changes(
            old["items"],
            new["items"],
            path + "[]"
        )

    if kind == "object":
        a = old["properties"]
        b = new["properties"]

        for key in sorted(set(a) - set(b)):
            out.append({
                "path": path + "." + key,
                "severity": (
                    "critical"
                    if key in old["required"]
                    else "medium"
                ),
                "message": "Field removed"
            })

        for key in sorted(
            set(old["required"]) - set(new["required"])
        ):
            if key in b:
                out.append({
                    "path": path + "." + key,
                    "severity": "high",
                    "message": "Required field became optional"
                })

        for key in sorted(set(b) - set(a)):
            out.append({
                "path": path + "." + key,
                "severity": "low",
                "message": "New field"
            })

        for key in sorted(set(a) & set(b)):
            out += changes(
                a[key],
                b[key],
                path + "." + key
            )

    return out


def lookup(data, path):
    if not path or path == "$":
        return True, data

    current = data

    for token in str(path).removeprefix("$.").split("."):
        if isinstance(current, dict) and token in current:
            current = current[token]

        elif (
            isinstance(current, list)
            and token.isdigit()
            and int(token) < len(current)
        ):
            current = current[int(token)]

        else:
            return False, None

    return True, current


def typename(x):
    return infer(x)["type"]


def validate_schema(data, spec):
    """Validate response using JSON Schema Draft 2020-12."""
    if not isinstance(spec, dict):
        raise ValueError("Schema must be a JSON object")

    Draft202012Validator.check_schema(spec)
    return Draft202012Validator(spec).is_valid(data)


def evaluate(rule, status, data, elapsed):
    typ = rule.get("type")
    path = rule.get("path", "$")
    expected = rule.get("value")

    if typ not in RULES:
        raise ValueError("Unknown assertion: " + str(typ))

    found, actual = lookup(data, path)

    if typ == "status":
        passed = status == int(expected) and status < 500
        actual = status

    elif typ == "latency":
        passed = elapsed <= float(expected)
        actual = round(elapsed, 1)

    elif typ == "exists":
        passed = found
        actual = "present" if found else "missing"

    elif typ == "equals":
        passed = found and actual == expected

    elif typ == "type":
        passed = found and (
            typename(actual) == expected
            or (
                expected == "number"
                and typename(actual) == "integer"
            )
        )
        actual = typename(actual) if found else "missing"

    elif typ == "nonempty":
        passed = found and bool(actual)
        actual = bool(actual) if found else False

    elif typ == "unique":
        passed = (
            found
            and isinstance(actual, list)
            and len(actual) > 0
        )

        if passed:
            ids = [
                lookup(x, str(expected or "id"))
                for x in actual
            ]
            passed = (
                all(ok for ok, _ in ids)
                and len({
                    json.dumps(v, sort_keys=True)
                    for _, v in ids
                }) == len(ids)
            )
        actual = "unique" if passed else "duplicate/missing"

    elif typ == "range":
        passed = (
            found
            and isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and isinstance(expected, dict)
            and expected.get("min", float("-inf"))
            <= actual
            <= expected.get("max", float("inf"))
        )

    elif typ == "schema":
        passed = found and validate_schema(actual, expected)
        actual = "matches" if passed else "mismatch"

    severity = (
        "critical"
        if typ == "status"
        else "medium"
        if typ in ("latency", "nonempty")
        else "high"
    )

    advice = {
        "status": "Check the documented status code and server logs.",
        "latency": "Profile server or network delay.",
        "exists": "Restore the documented required field.",
        "equals": "Investigate response mapping or business logic.",
        "type": "Verify JSON serialization and API contract.",
        "nonempty": "Confirm the endpoint is expected to return content.",
        "unique": "Review record identifiers and duplicate creation.",
        "range": "Validate business constraints and numeric boundaries.",
        "schema": "Inspect changed required fields and field types."
    }[typ]

    return {
        "type": typ,
        "path": path,
        "expected": expected,
        "actual": actual,
        "passed": bool(passed),
        "severity": severity,
        "recommendation": advice
    }


def local_request(method, path, body, users):
    path = urlsplit(path).path.rstrip("/")

    if path == "/demo/users":
        if method == "GET":
            return 200, copy.deepcopy(users)

        if method == "POST":
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("name"), str)
                or not body["name"].strip()
                or not isinstance(body.get("email"), str)
                or "@" not in body["email"]
            ):
                return 400, {
                    "error": "Valid name and email required"
                }

            uid = max(
                (x["id"] for x in users),
                default=0
            ) + 1

            item = {
                "id": uid,
                "name": body["name"],
                "email": body["email"],
                "age": body.get("age", 18)
            }

            users.append(item)
            return 201, copy.deepcopy(item)

    if path.startswith("/demo/users/"):
        text = path.rsplit("/", 1)[-1]

        if not text.isdigit():
            return 400, {"error": "Invalid user ID"}

        user = next(
            (x for x in users if x["id"] == int(text)),
            None
        )

        if user is None:
            return 404, {"error": "User not found"}

        if method == "GET":
            return 200, copy.deepcopy(user)

        if method == "DELETE":
            users.remove(user)
            return 200, {"deleted": int(text)}

        if method in ("PATCH", "PUT"):
            if not isinstance(body, dict):
                return 400, {"error": "Object body required"}

            if method == "PUT" and not all(
                k in body for k in ("name", "email")
            ):
                return 400, {
                    "error": "name and email required"
                }

            if any(
                k not in ("name", "email", "age")
                for k in body
            ):
                return 400, {"error": "Unknown field"}

            user.update(body)
            return 200, copy.deepcopy(user)

    if path == "/demo/broken" and method == "GET":
        return 200, [
            {"id": 1, "name": "Ada"},
            {"id": 1, "name": None},
            {"id": 3}
        ]

    if path == "/demo/error" and method == "GET":
        return 500, {"error": "Service unavailable"}

    if path == "/demo/changed" and method == "GET":
        return 200, [{"id": "1", "full_name": "Ada"}]

    return 404, {"error": "Unknown route"}


def safe_public_url(url):
    if not isinstance(url, str) or len(url) > 1500:
        raise ValueError("Invalid URL")

    p = urlsplit(url)

    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise ValueError(
            "Only public HTTPS on port 443 is allowed"
        )

    host = p.hostname.rstrip(".")

    if not host or not re.fullmatch(
        r"[A-Za-z0-9.:-]+",
        host
    ):
        raise ValueError("Invalid host")

    info = socket.getaddrinfo(
        host,
        443,
        type=socket.SOCK_STREAM
    )

    ips = list({row[4][0] for row in info})

    if not ips or not all(
        ipaddress.ip_address(x).is_global
        for x in ips
    ):
        raise ValueError(
            "Private/reserved network blocked"
        )

    return p, host, ips[0]


def public_request(method, url, headers, body, allow_writes):
    if method != "GET":
        allowed = {
            x.strip().lower()
            for x in os.getenv(
                "RULEGUARD_WRITE_HOSTS", ""
            ).split(",")
            if x.strip()
        }

        target = urlsplit(url).hostname

        if (
            not allow_writes
            or not target
            or target.lower() not in allowed
        ):
            raise ValueError(
                "External write blocked: enable checkbox "
                "AND set RULEGUARD_WRITE_HOSTS "
                "to an authorized host"
            )

    p, host, ip = safe_public_url(url)

    class Pinned(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection(
                (ip, 443),
                timeout=8
            )
            self.sock = self._context.wrap_socket(
                raw,
                server_hostname=host
            )

    clean = {
        "Accept": "application/json",
        "User-Agent": "RuleGuard-Atelier/1.0"
    }

    for key, value in (headers or {}).items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,50}", key)
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError("Invalid header")

        if key.lower() in (
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "proxy-authorization"
        ):
            raise ValueError("Reserved header")

        clean[key] = value

    raw_body = None

    if method not in ("GET", "DELETE") and body is not None:
        raw_body = json.dumps(body).encode("utf-8")
        clean.setdefault(
            "Content-Type",
            "application/json"
        )

    conn = Pinned(
        host,
        timeout=8,
        context=ssl.create_default_context()
    )

    path = urlunsplit((
        "", "", p.path or "/", p.query, ""
    ))

    try:
        conn.request(
            method,
            path,
            body=raw_body,
            headers=clean
        )

        result = conn.getresponse()
        status = result.status
        raw = result.read(MAX_BYTES + 1)

    finally:
        conn.close()

    if len(raw) > MAX_BYTES:
        raise ValueError("Response exceeds 500 KB")

    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        data = None

    return status, data


def recommendations(status, data):
    rules = [
        {"type": "status", "value": 200},
        {"type": "latency", "value": 2000}
    ]

    if data is not None:
        rules.append({
            "type": "nonempty",
            "path": "$",
            "value": True
        })

    if (
        isinstance(data, list)
        and data
        and all(isinstance(x, dict) for x in data)
    ):
        common = set.intersection(
            *(set(x) for x in data)
        )

        if "id" in common:
            rules.append({
                "type": "unique",
                "path": "$",
                "value": "id"
            })

        for key in sorted(common)[:3]:
            rules.append({
                "type": "exists",
                "path": "0." + key,
                "value": True
            })

    elif isinstance(data, dict):
        for key in list(data)[:4]:
            rules.append({
                "type": "exists",
                "path": key,
                "value": True
            })

    return rules


def generate_cases(payload):
    endpoint = payload.get("endpoint", "/demo/users")
    method = str(payload.get("method", "GET")).upper()
    schema = payload.get("schema", {})

    cases = [{
        "name": "Happy path: expected response",
        "method": method,
        "endpoint": endpoint,
        "body": payload.get("body", {}),
        "rules": [
            {"type": "status", "value": 200},
            {"type": "latency", "value": 2000}
        ]
    }]

    if isinstance(schema, dict) and schema:
        cases[0]["rules"].append({
            "type": "schema",
            "path": "$",
            "value": schema
        })

    if endpoint.startswith("/demo/users") and method == "GET":
        cases[0]["rules"].append({
            "type": "nonempty",
            "path": "$",
            "value": True
        })

        if endpoint == "/demo/users":
            cases[0]["rules"].append({
                "type": "unique",
                "path": "$",
                "value": "id"
            })

        cases.append({
            "name": "Negative: unknown resource",
            "method": "GET",
            "endpoint": "/demo/users/9999",
            "body": {},
            "rules": [
                {"type": "status", "value": 404},
                {
                    "type": "exists",
                    "path": "error",
                    "value": True
                }
            ]
        })

        cases.append({
            "name": "Defect discovery: duplicate identifiers",
            "method": "GET",
            "endpoint": "/demo/broken",
            "body": {},
            "rules": [
                {"type": "status", "value": 200},
                {"type": "unique", "path": "$", "value": "id"},
                {
                    "type": "schema",
                    "path": "$",
                    "value": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "name"]
                        }
                    }
                }
            ]
        })

    if endpoint == "/demo/users" and method == "POST":
        cases[0]["rules"][0]["value"] = 201

        cases[0]["body"] = {
            "name": "Ari",
            "email": "ari@example.org",
            "age": 18
        }

        cases.append({
            "name": "Boundary: empty name rejected",
            "method": "POST",
            "endpoint": endpoint,
            "body": {
                "name": "",
                "email": "ari@example.org"
            },
            "rules": [
                {"type": "status", "value": 400},
                {"type": "exists", "path": "error", "value": True}
            ]
        })

        cases.append({
            "name": "Equivalence: invalid email rejected",
            "method": "POST",
            "endpoint": endpoint,
            "body": {
                "name": "Ari",
                "email": "invalid"
            },
            "rules": [
                {"type": "status", "value": 400}
            ]
        })

    return cases[:12]


def openapi_cases(spec):
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get("paths"), dict)
    ):
        raise ValueError(
            "Paste a valid OpenAPI JSON document with paths"
        )

    cases = []

    for path, ops in spec["paths"].items():
        if not isinstance(ops, dict):
            continue

        for method, operation in ops.items():
            if (
                method.lower()
                not in ("get", "post", "put", "patch", "delete")
                or not isinstance(operation, dict)
            ):
                continue

            responses = operation.get("responses", {})

            positive = next(
                (
                    code for code in responses
                    if str(code).isdigit()
                    and 200 <= int(code) < 300
                ),
                None
            )

            if positive is None:
                continue

            rules = [{
                "type": "status",
                "value": int(positive)
            }]

            desc = responses[positive]

            if isinstance(desc, dict):
                media = desc.get(
                    "content", {}
                ).get("application/json", {})

                schema = (
                    media.get("schema")
                    if isinstance(media, dict)
                    else None
                )

                if (
                    isinstance(schema, dict)
                    and "$ref" not in json.dumps(schema)
                ):
                    rules.append({
                        "type": "schema",
                        "path": "$",
                        "value": schema
                    })

            cases.append({
                "name": f"{method.upper()} {path}",
                "method": method.upper(),
                "endpoint": path,
                "body": {},
                "rules": rules,
                "note": (
                    "OpenAPI draft: resolve base URL, "
                    "path parameters, auth and request body "
                    "before running."
                )
            })

            if len(cases) >= 20:
                return cases

    return cases


def execute(payload):
    cases = payload.get("cases")

    if (
        not isinstance(cases, list)
        or not 1 <= len(cases) <= 20
    ):
        raise ValueError("Provide 1–20 cases")

    allow_writes = payload.get("allowExternalWrites") is True
    users = copy.deepcopy(USERS)
    results = []
    total_risk = 0

    conn = db()

    try:
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError("Invalid test case")

            method = str(case.get("method", "GET")).upper()
            url = case.get("endpoint", "/demo/users")
            rules = case.get("rules", [])
            headers = case.get("headers", {})
            body = case.get("body")

            if (
                method not in (
                    "GET", "POST", "PUT", "PATCH", "DELETE"
                )
                or not isinstance(url, str)
            ):
                raise ValueError("Invalid operation")

            if (
                not isinstance(rules, list)
                or len(rules) > 35
                or not all(isinstance(x, dict) for x in rules)
            ):
                raise ValueError("Invalid assertions")

            start = time.perf_counter()
            transport_error = None

            try:
                if url.startswith("/demo/"):
                    status, data = local_request(
                        method,
                        url,
                        body,
                        users
                    )

                elif url.startswith("https://"):
                    status, data = public_request(
                        method,
                        url,
                        headers,
                        body,
                        allow_writes
                    )

                else:
                    raise ValueError(
                        "Use /demo/ or https:// public endpoint"
                    )

            except (
                OSError,
                ValueError,
                TimeoutError,
                ssl.SSLError,
                http.client.HTTPException
            ) as exc:
                status, data = 0, None
                transport_error = (
                    f"{type(exc).__name__}: {exc}"
                )

            ms = (time.perf_counter() - start) * 1000

            checks = (
                []
                if transport_error
                else [
                    evaluate(r, status, data, ms)
                    for r in rules
                ]
            )

            issues = [
                {
                    "severity": r["severity"],
                    "message": r["recommendation"],
                    "source": r["type"]
                }
                for r in checks
                if not r["passed"]
            ]

            if transport_error:
                issues.append({
                    "severity": "critical",
                    "message": transport_error,
                    "source": "transport"
                })

            else:
                expected_status = any(
                    r["type"] == "status"
                    and r["passed"]
                    for r in checks
                )

                if (
                    status >= 500
                    or (
                        status >= 300
                        and not expected_status
                    )
                ):
                    if not any(
                        r["type"] == "status"
                        and not r["passed"]
                        for r in checks
                    ):
                        issues.append({
                            "severity": "critical",
                            "message": (
                                "Unexpected HTTP response "
                                "or server failure"
                            ),
                            "source": "http-policy"
                        })

                if (
                    data is None
                    and status not in (204, 205, 304)
                ):
                    issues.append({
                        "severity": "high",
                        "message": "Response is not valid JSON",
                        "source": "response-format"
                    })

            key = method + " " + url

            baseline = conn.execute(
                """
                SELECT schema
                FROM baselines
                WHERE key=?
                """,
                (key,)
            ).fetchone()

            differences = (
                changes(
                    json.loads(baseline[0]),
                    infer(data)
                )
                if baseline and case.get("compare")
                else []
            )

            issues += [
                {
                    "severity": c["severity"],
                    "message": c["message"],
                    "source": "contract",
                    "path": c["path"]
                }
                for c in differences
            ]

            risk = min(
                100,
                sum(
                    WEIGHTS[x["severity"]]
                    for x in issues
                )
            )

            total_risk = min(
                100,
                total_risk + risk
            )

            no_rules = len(rules) == 0

            critical = any(
                x["severity"] == "critical"
                for x in issues
            )

            decision = (
                "BLOCK"
                if critical or risk >= 40
                else "NOT TESTED"
                if no_rules
                else "REVIEW"
                if risk
                else "READY"
            )

            results.append({
                "name": str(case.get("name", "Untitled"))[:120],
                "method": method,
                "endpoint": url,
                "status": status,
                "latency": round(ms, 2),
                "data": data,
                "schema": infer(data),
                "checks": checks,
                "issues": issues,
                "decision": decision,
                "score": None if no_rules else 100 - risk,
                "assertionCount": len(rules)
            })

        untested = sum(
            r["assertionCount"] == 0
            for r in results
        )

        overall = (
            "BLOCK"
            if any(
                r["decision"] == "BLOCK"
                for r in results
            )
            else "NOT TESTED"
            if untested
            else "REVIEW"
            if any(
                r["decision"] == "REVIEW"
                for r in results
            )
            else "READY"
        )

        coverage = round(
            (len(results) - untested)
            / len(results) * 100,
            1
        )

        score = None if untested else 100 - total_risk

        passed_assertions = sum(
            sum(c["passed"] for c in r["checks"])
            for r in results
        )

        executed_assertions = sum(
            len(r["checks"])
            for r in results
        )

        report = {
            "created": datetime.now(
                timezone.utc
            ).isoformat(),
            "decision": overall,
            "score": score,
            "coverage": coverage,
            "caseCount": len(results),
            "passed": passed_assertions,
            "failed": sum(
                len(r["issues"])
                for r in results
            ),
            "results": results,
            "assertionPassRate": round(
                100 * passed_assertions
                / max(1, executed_assertions),
                1
            )
        }

        with LOCK:
            cur = conn.execute(
                """
                INSERT INTO runs(
                    created, decision, score, coverage, document
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report["created"],
                    overall,
                    score,
                    coverage,
                    json.dumps(report)
                )
            )

            conn.commit()
            report["id"] = cur.lastrowid

        return report

    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):

    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode()

        self.send_response(code)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(raw))
        )
        self.send_header(
            "Cache-Control",
            "no-store"
        )
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path

        if path in (
            "/api/history",
            "/api/latest",
            "/api/baselines"
        ):
            conn = db()

            try:
                if path == "/api/history":
                    rows = conn.execute(
                        """
                        SELECT
                            id, created, decision, score, coverage
                        FROM runs
                        ORDER BY id DESC
                        LIMIT 25
                        """
                    ).fetchall()

                    return self.send_json([
                        dict(zip(
                            (
                                "id",
                                "created",
                                "decision",
                                "score",
                                "coverage"
                            ),
                            r
                        ))
                        for r in rows
                    ])

                if path == "/api/latest":
                    row = conn.execute(
                        """
                        SELECT document, id
                        FROM runs
                        ORDER BY id DESC
                        LIMIT 1
                        """
                    ).fetchone()

                    return self.send_json(
                        {
                            **json.loads(row[0]),
                            "id": row[1]
                        }
                        if row else None
                    )

                rows = conn.execute(
                    """
                    SELECT key
                    FROM baselines
                    ORDER BY key
                    """
                ).fetchall()

                return self.send_json([
                    r[0] for r in rows
                ])

            finally:
                conn.close()

        name = {
            "/": "index.html",
            "/index.html": "index.html",
            "/style.css": "style.css",
            "/script.js": "script.js"
        }.get(path)

        if not name:
            return self.send_json(
                {"error": "Not found"},
                404
            )

        file = ROOT / name

        if not file.exists():
            return self.send_json(
                {"error": "Missing file: " + name},
                500
            )

        raw = file.read_bytes()

        mime = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "text/javascript"
        }[file.suffix]

        self.send_response(200)
        self.send_header(
            "Content-Type",
            mime + "; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(raw))
        )
        self.send_header(
            "Cache-Control",
            "no-store"
        )
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )

            if not 0 < length < 400000:
                raise ValueError("Invalid request size")

            payload = json.loads(
                self.rfile.read(length)
            )

            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")

            path = urlsplit(self.path).path

            if path == "/api/run":
                return self.send_json(execute(payload))

            if path == "/api/generate":
                return self.send_json({
                    "cases": generate_cases(payload)
                })

            if path == "/api/openapi":
                return self.send_json({
                    "cases": openapi_cases(
                        payload.get("spec")
                    )
                })

            if path == "/api/suggest":
                method = str(
                    payload.get("method", "GET")
                ).upper()

                url = payload.get(
                    "endpoint", "/demo/users"
                )

                if url.startswith("/demo/"):
                    status, data = local_request(
                        method,
                        url,
                        payload.get("body"),
                        copy.deepcopy(USERS)
                    )

                elif (
                    method == "GET"
                    and url.startswith("https://")
                ):
                    status, data = public_request(
                        "GET",
                        url,
                        payload.get("headers"),
                        None,
                        False
                    )

                else:
                    raise ValueError(
                        "Suggestions support demo endpoints "
                        "or external GET only"
                    )

                return self.send_json({
                    "rules": recommendations(status, data),
                    "note": (
                        "Heuristics from observed data are "
                        "suggestions; verify against requirements."
                    )
                })

            if path == "/api/baseline":
                key = payload.get("key")
                schema = payload.get("schema")

                if (
                    not isinstance(key, str)
                    or not isinstance(schema, dict)
                    or len(key) > 1600
                ):
                    raise ValueError("Invalid baseline")

                conn = db()

                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO baselines
                        (key, schema)
                        VALUES (?, ?)
                        """,
                        (key, json.dumps(schema))
                    )
                    conn.commit()

                finally:
                    conn.close()

                return self.send_json({"saved": True})

            return self.send_json(
                {"error": "Unknown route"},
                404
            )

        except (
            ValueError,
            TypeError,
            KeyError,
            OSError,
            ssl.SSLError,
            http.client.HTTPException,
            schema_errors.SchemaError
        ) as err:
            self.send_json(
                {"error": str(err)},
                400
            )

        except Exception as err:
            self.send_json({
                "error": (
                    "Internal error: "
                    + type(err).__name__
                )
            }, 500)


if __name__ == "__main__":
    db().close()

    print(
        "RuleGuard Atelier → http://127.0.0.1:8000"
    )

    ThreadingHTTPServer(
        ("127.0.0.1", 8000),
        Handler
    ).serve_forever()
