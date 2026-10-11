"""codemap's own JS/TS facts (``enrich/jsfacts.py``): the REA-derived request,
storage and config rules, the gaps codemap fills on top (URL patterns, route
chains, mounts across imports, pairing), and the payload they land in."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codemap import config, db, indexer
from codemap.enrich import jsfacts
from codemap.parsing import parse_source
from codemap.languages.registry import spec_for_path
from codemap.site import model


def _facts(src: str, path: str = "a.js") -> jsfacts.FileFacts:
    return jsfacts.extract(path, src.encode())


# --------------------------------------------------------------------------- extraction (REA rules)


def test_requests_keep_literal_urls_and_turn_substitutions_into_params():
    ff = _facts("""
        axios.get(`/api/products/${id}/reviews?sort=${s}`)
        axios.post('/api/orders', order, config)
        fetch('/api/x', { method: 'DELETE' })
        fetch(`${API}/users`)
        http.put('https://shop.example.com/api/items/' + id)
    """)
    got = [(q.method, q.url) for q in ff.requests]
    assert got == [
        ("GET", "/api/products/:param/reviews"),
        ("POST", "/api/orders"),
        ("DELETE", "/api/x"),
        ("GET", "/users"),
        ("PUT", "/api/items/:param"),
    ]


def test_a_get_that_is_not_a_url_is_not_a_request():
    # REA keeps any literal for most methods; a link needs it to look like one
    ff = _facts("""
        cache.get('user')
        new Map().get('/looks/like/a/path')
        req.headers.get('/x')
        settings.post('name')
    """)
    assert ff.requests == []


def test_storage_kinds_keys_and_operations():
    ff = _facts("""
        localStorage.setItem('cart', JSON.stringify(c))
        window.sessionStorage.getItem('tab')
        localStorage.length
        indexedDB.open('shop', 2)
        caches.open('v1')
        const db = new Database('app.sqlite')
    """)
    assert [(s.kind, s.key, s.op) for s in ff.storage] == [
        ("local-storage", "cart", "setItem"),
        ("session-storage", "tab", "getItem"),
        ("indexed-db", "shop", "open"),
        ("cache-storage", "v1", "open"),
        ("sqlite", "app.sqlite", "open"),
    ]


def test_config_reads_with_their_defaults():
    ff = _facts("""
        const PORT = process.env.PORT || 5000
        const mode = process.env['MODE'] ?? 'dev'
        const key = process.env.SECRET
        const flag = process.argv[2]
        const notIndex = process.argv.length
        const vite = import.meta.env.VITE_API
    """)
    got = [(c.kind, c.key, c.default) for c in ff.config]
    assert got == [
        ("environment", "PORT", "5000"),
        ("environment", "MODE", "dev"),
        ("environment", "SECRET", None),
        ("argv", "2", None),
        ("vite-env", "VITE_API", None),
    ]


# --------------------------------------------------------------------------- extraction (codemap's additions)


def test_server_routes_are_routes_not_requests():
    ff = _facts("""
        import express from 'express'
        const router = express.Router()
        router.route('/').post(protect, addItem).get(protect, admin, listAll)
        router.get('/top', topOnes)
        router.route('/:id').get(getOne)
        export default router
    """)
    assert ff.requests == []
    assert [(r.method, r.path, r.handler, r.middleware) for r in ff.routes] == [
        ("GET", "/", "listAll", ["protect", "admin"]),
        ("POST", "/", "addItem", ["protect"]),
        ("GET", "/top", "topOnes", []),
        ("GET", "/:id", "getOne", []),
    ]
    assert ff.default_export == "router"


def test_mounts_and_axios_instances():
    ff = _facts("""
        const express = require('express')
        const users = require('./routes/users')
        const app = express()
        app.use('/api/users', users)
        app.use('/api/admin', require('./routes/admin'))
        const api = axios.create({ baseURL: '/api/v2' })
        api.get('/things')
    """)
    assert [(m.receiver, m.prefix, m.target) for m in ff.mounts] == [
        ("app", "/api/users", "users"),
        ("app", "/api/admin", "require:./routes/admin"),
    ]
    assert ff.instances == {"api": "/api/v2"}
    assert [(q.url, q.base) for q in ff.requests] == [("/things", "api")]


def test_a_file_that_is_not_js_or_does_not_parse_gives_nothing():
    assert jsfacts.extract("x.py", b"requests.get('/x')").requests == []
    assert jsfacts.extract("x.js", b"\x00\xff((((").requests == []


# --------------------------------------------------------------------------- linking


SERVER = """
import express from 'express'
import orderRoutes from './routes/orderRoutes.js'
const app = express()
app.use('/api/orders', orderRoutes)
app.get('/health', (req, res) => res.send('ok'))
"""
ROUTES = """
import express from 'express'
import { addOrder, getOrder, myOrders } from '../controllers/orders.js'
const router = express.Router()
router.route('/').post(addOrder)
router.route('/mine').get(myOrders)
router.route('/:id').get(getOrder)
export default router
"""
CONTROLLERS = """
import asyncHandler from 'express-async-handler'
export const addOrder = asyncHandler(async (req, res) => { res.json({}) })
export const getOrder = asyncHandler(async (req, res) => { res.json({}) })
export const myOrders = asyncHandler(async (req, res) => { res.json([]) })
"""
CLIENT = """
import axios from 'axios'
export const createOrder = (o) => async (dispatch) => { await axios.post('/api/orders', o) }
export const loadOrder = (id) => async (dispatch) => { await axios.get(`/api/orders/${id}`) }
export const loadMine = () => async (dispatch) => { await axios.get('/api/orders/mine') }
export const ping = () => fetch('/health')
"""
FILES = {
    "server/server.js": SERVER,
    "server/routes/orderRoutes.js": ROUTES,
    "server/controllers/orders.js": CONTROLLERS,
    "web/src/actions/orders.js": CLIENT,
}


def _link(files: dict[str, str]) -> jsfacts.Linked:
    facts, spans = [], []
    for path, src in files.items():
        b = src.encode()
        facts.append(jsfacts.extract(path, b))
        pf = parse_source(path, b, spec_for_path(path))
        spans += [jsfacts.SymSpan(s.key, s.name, path, s.start_line, s.end_line) for s in pf.symbols]
    return jsfacts.link(facts, spans)


def test_a_request_is_tied_to_the_handler_through_the_mount_prefix():
    linked = _link(FILES)
    by = {(r.method, r.url): r for r in linked.routes}
    post = by[("POST", "/api/orders")]
    assert post.callers == ["web/src/actions/orders.js::createOrder"]
    assert post.handlers == ["server/controllers/orders.js::addOrder"]
    assert by[("GET", "/api/orders/:id")].handlers == ["server/controllers/orders.js::getOrder"]
    assert by[("GET", "/api/orders/:id")].callers == ["web/src/actions/orders.js::loadOrder"]
    assert linked.requests == 4 and linked.paired == 4


def test_a_literal_segment_beats_a_parameter():
    by = {(r.method, r.url): r for r in _link(FILES).routes}
    mine = by[("GET", "/api/orders/mine")]
    assert mine.callers == ["web/src/actions/orders.js::loadMine"]
    assert mine.handlers == ["server/controllers/orders.js::myOrders"]
    # /mine must not also be claimed by /:id
    assert "web/src/actions/orders.js::loadMine" not in by[("GET", "/api/orders/:id")].callers


def test_an_inline_handler_still_pairs_but_has_no_symbol():
    by = {(r.method, r.url): r for r in _link(FILES).routes}
    health = by[("GET", "/health")]
    assert health.callers == ["web/src/actions/orders.js::ping"]
    assert health.handlers == []


def test_a_request_to_a_route_this_repo_does_not_serve_stays_unpaired():
    linked = _link({"web/a.js": "export const f = () => axios.get('/api/elsewhere')"})
    assert linked.paired == 0
    assert [(r.url, r.callers, r.handlers) for r in linked.routes] == [("/api/elsewhere", ["web/a.js::f"], [])]


def test_wrapped_functions_are_symbols():
    pf = parse_source("c.js", CONTROLLERS.encode(), spec_for_path("c.js"))
    assert [s.name for s in pf.symbols] == ["addOrder", "getOrder", "myOrders"]
    t = parse_source("t.js", b"const id = setTimeout(() => go(), 5)", spec_for_path("t.js"))
    assert t.symbols == []


# --------------------------------------------------------------------------- model / brief


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def _repo(tmp_path: Path, files: dict[str, str]):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    cfg = config.load(root)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg)
    return cfg, conn


def test_model_carries_native_route_links_and_facts(tmp_path):
    files = dict(FILES)
    files["web/src/store.js"] = "export const saved = () => localStorage.getItem('cart')\n"
    files["server/config.js"] = "export const port = () => process.env.PORT || 5000\n"
    cfg, conn = _repo(tmp_path, files)
    data = model.build(conn, cfg)
    key = {n["key"]: i for i, n in enumerate(data["nodes"])}
    routes = {(r["method"], r["url"]): r for r in data["routes"]}
    post = routes[("POST", "/api/orders")]
    assert post["callers"] == [key["web/src/actions/orders.js::createOrder"]]
    assert post["handlers"] == [key["server/controllers/orders.js::addOrder"]]
    assert data["facts"]["paired"] == 4
    assert {(c["key"], c["default"]) for c in data["facts"]["config"]} == {("PORT", "5000")}
    store = next(s for s in data["architecture"]["stores"] if s["id"] == "browser-storage")
    assert store["keys"] == ["cart"] and store["via"] == ["localStorage"]


def test_native_off_adds_nothing(tmp_path):
    cfg, conn = _repo(tmp_path, FILES)
    cfg.engine.native = "off"
    data = model.build(conn, cfg)
    assert "facts" not in data
    assert "routes" not in data
