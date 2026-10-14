"""Python web requests and served routes (``codemap/enrich/pyfacts.py``), and
their pairing -- with each other and across languages -- by ``jsfacts.link``."""

from __future__ import annotations

from codemap.enrich import jsfacts, pyfacts

CLIENT = '''\
import httpx
import requests
from urllib.request import urlopen

BASE = "http://localhost:8000"


def sync_users(user_id, cache):
    requests.get("http://localhost:8000/api/users")
    requests.post(f"{BASE}/api/users/{user_id}/avatar", data=b"")
    with httpx.Client() as client:
        client.request("DELETE", "/api/users/" + str(user_id))
    urlopen("https://example.com/health")
    cache.get("users")                       # not a URL: not a request
    requests.get(f"{BASE}/{user_id}")        # no literal segment: nothing to match
'''


def _requests(src: str):
    return [(r.method, r.url, r.line) for r in pyfacts.extract("app/client.py", src.encode()).requests]


def test_python_requests_are_read_with_their_method_and_url_pattern():
    assert _requests(CLIENT) == [
        ("GET", "/api/users", 9),
        ("POST", "/api/users/:param/avatar", 10),
        ("DELETE", "/api/users/:param", 12),
        ("GET", "/health", 13),
    ]


def test_a_file_without_request_words_is_not_parsed():
    assert _requests("def f():\n    return 1\n") == []


def test_route_entry_points_become_served_routes():
    eps = {
        "api/users.py::list_users": [("route", "GET /api/users")],
        "api/users.py::user": [("route", "GET, POST /api/users/<int:user_id>")],
        "api/ws.py::feed": [("route", "WS /feed")],
        "api/cli.py::main": [("cli", "main")],
        "web/server.js::handler": [("route", "GET /js-owned")],      # jsfacts handles JS routes
    }
    keys = set(eps)
    served = {(s.method, s.path, s.handler) for s in pyfacts.served_from_entry_points(eps, keys)}
    assert served == {
        ("GET", "/api/users", "api/users.py::list_users"),
        ("GET", "/api/users/:param", "api/users.py::user"),
        ("POST", "/api/users/:param", "api/users.py::user"),
    }


def test_a_js_request_pairs_with_a_python_route_and_a_python_request_too():
    front = jsfacts.extract("web/api.ts", b"export function load(id) { return fetch(`/api/users/${id}`); }\n",
                            "typescript")
    back = pyfacts.extract("app/client.py", CLIENT.encode())
    served = pyfacts.served_from_entry_points(
        {"api/users.py::user": [("route", "GET, POST /api/users/<int:user_id>")]}, {"api/users.py::user"})
    spans = [
        jsfacts.SymSpan("web/api.ts::load", "load", "web/api.ts", 1, 1),
        jsfacts.SymSpan("app/client.py::sync_users", "sync_users", "app/client.py", 8, 16),
        jsfacts.SymSpan("api/users.py::user", "user", "api/users.py", 1, 5),
    ]
    linked = jsfacts.link([front, back], spans, served)
    by = {(r.method, r.url): r for r in linked.routes}
    get = by[("GET", "/api/users/:param")]
    assert get.callers == ["web/api.ts::load"] and get.handlers == ["api/users.py::user"]
    # POST /api/users/:param/avatar has one more segment: it does not match the route
    assert ("POST", "/api/users/:param/avatar") in by and by[("POST", "/api/users/:param/avatar")].handlers == []
    assert linked.server_routes == 2 and linked.paired == 1
