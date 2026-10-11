"""Express routes as entry points: a route declared on a router in one file,
mounted under a prefix in another and handled by a function in a third. The
indexer stores each file's route facts and ``_resolve_server_routes`` ties the
full path to the handler's symbol once the whole snapshot is in — so a backend
handler is a Simulate scenario even when no screen calls it."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codemap import config, db, indexer
from codemap.site import brief

SERVER = """import express from 'express'
import orderRoutes from './routes/orderRoutes.js'
const userRoutes = require('./routes/userRoutes')

const app = express()
app.use('/api/orders', orderRoutes)
app.use('/api/users', userRoutes)
app.get('/health', (req, res) => res.send('ok'))
app.listen(5000)
"""
ORDER_ROUTES = """import express from 'express'
const router = express.Router()
import { addOrderItems, getOrderById } from '../controllers/orderController.js'
import protect from '../middleware/auth.js'

router.route('/').post(protect, addOrderItems)
router.route('/:id').get(protect, getOrderById)

export default router
"""
ORDER_CONTROLLER = """import asyncHandler from 'express-async-handler'

const addOrderItems = asyncHandler(async (req, res) => {
  res.status(201).json({})
})

const getOrderById = asyncHandler(async (req, res) => {
  res.json({})
})

export { addOrderItems, getOrderById }
"""
USER_ROUTES = """const { Router } = require('express')
const users = require('../controllers/userController')
const router = Router()
router.get('/', users.listUsers)
module.exports = router
"""
USER_CONTROLLER = """function listUsers(req, res) {
  res.json([])
}
module.exports = { listUsers }
"""
AUTH = "export default function protect(req, res, next) { next() }\n"

FILES = {
    "backend/server.js": SERVER,
    "backend/routes/orderRoutes.js": ORDER_ROUTES,
    "backend/routes/userRoutes.js": USER_ROUTES,
    "backend/controllers/orderController.js": ORDER_CONTROLLER,
    "backend/controllers/userController.js": USER_CONTROLLER,
    "backend/middleware/auth.js": AUTH,
}

EXPECTED = {
    ("POST /api/orders", "backend/controllers/orderController.js::addOrderItems", "backend/routes/orderRoutes.js"),
    ("GET /api/orders/:id", "backend/controllers/orderController.js::getOrderById", "backend/routes/orderRoutes.js"),
    ("GET /api/users", "backend/controllers/userController.js::listUsers", "backend/routes/userRoutes.js"),
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _write(root, FILES)
    _commit(root, "init")
    return root


def _scan(root: Path, dbname: str = "index.db"):
    cfg = config.load(root)
    conn = db.connect(root / ".codemap" / dbname)
    db.migrate(conn)
    indexer.scan(conn, cfg)
    return cfg, conn


def _routes(conn, sha: str = indexer.WORKTREE_SHA) -> set[tuple[str, str, str]]:
    out = set()
    for r in conn.execute(
        "SELECT e.detail, s.key FROM entry_points e JOIN symbols s ON s.id = e.symbol_id "
        "WHERE e.commit_sha = ? AND e.kind = 'route'",
        (sha,),
    ):
        route, _, declared = r["detail"].partition(indexer._DECLARED_IN)
        out.add((route, r["key"], declared))
    return out


def test_mounted_routes_resolve_to_their_handlers(tmp_path):
    root = _repo(tmp_path)
    _cfg, conn = _scan(root)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    # the inline /health handler has no symbol of its own: not an entry point
    assert _routes(conn) == EXPECTED
    assert _routes(conn, head) == EXPECTED


def test_changing_only_the_mount_prefix_moves_the_route(tmp_path):
    root = _repo(tmp_path)
    _scan(root)
    _write(root, {"backend/server.js": SERVER.replace("'/api/orders'", "'/api/v2/orders'")})
    _commit(root, "v2 orders")
    _cfg, conn = _scan(root)
    want = {(r.replace("/api/orders", "/api/v2/orders"), k, f) for r, k, f in EXPECTED}
    assert _routes(conn) == want


def test_removing_a_route_file_drops_its_entry_points(tmp_path):
    root = _repo(tmp_path)
    _scan(root)
    (root / "backend/routes/userRoutes.js").unlink()
    _write(root, {"backend/server.js": SERVER.replace("app.use('/api/users', userRoutes)\n", "")})
    _commit(root, "drop users")
    _cfg, conn = _scan(root)
    assert _routes(conn) == {r for r in EXPECTED if not r[0].startswith("GET /api/users")}


def test_incremental_equals_from_scratch(tmp_path):
    root = _repo(tmp_path)
    _scan(root, "inc.db")
    _write(root, {"backend/controllers/orderController.js": ORDER_CONTROLLER + "// touched\n"})
    _commit(root, "touch controller")
    _c1, inc = _scan(root, "inc.db")
    _c2, fresh = _scan(root, "fresh.db")

    def every(conn):
        return sorted(
            (r["commit_sha"], r["key"] or "", r["kind"], r["detail"])
            for r in conn.execute(
                "SELECT e.commit_sha, s.key, e.kind, e.detail FROM entry_points e "
                "LEFT JOIN symbols s ON s.id = e.symbol_id"
            )
        )

    assert every(inc) == every(fresh)
    assert _routes(inc) == _routes(fresh) == EXPECTED


def test_routes_become_scenario_candidates(tmp_path):
    from codemap.site import model

    root = _repo(tmp_path)
    cfg, conn = _scan(root)
    data = model.build(conn, cfg)
    cands = brief._scenario_candidates(data)
    got = {(c["key"], c["group"]) for c in cands if c["kind"] == "route"}
    assert got == {(k, "A request comes in") for _r, k, _f in EXPECTED}
