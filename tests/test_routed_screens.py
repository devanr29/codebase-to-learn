"""A screen registered in one file and defined in another: React Router's
``<Route component={X} />`` (v4/v5) / ``element={<X />}`` (v6+) with X
imported, and a render root wrapped in providers. The indexer records the
registration per router file and ``_resolve_routed_screens`` ties it to X's
symbol after the whole snapshot is in."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codemap import config, db, entrypoints, indexer

PKG = '{"name": "web", "dependencies": {"react": "17", "react-router-dom": "5"}}\n'
APP = """import React from 'react'
import { BrowserRouter as Router, Route } from 'react-router-dom'
import HomeScreen from './screens/HomeScreen'
import { CartScreen } from './screens/CartScreen'
const AdminScreen = React.lazy(() => import('./screens/AdminScreen'))

const App = () => (
  <Router>
    <Route path='/' component={HomeScreen} exact />
    <Route path='/cart/:id?' element={<CartScreen />} />
    <Route path='/admin' component={AdminScreen} />
    <Route path='/missing' component={NotImported} />
  </Router>
)
export default App
"""
INDEX = """import React from 'react'
import ReactDOM from 'react-dom'
import { Provider } from 'react-redux'
import App from './App'
ReactDOM.render(<Provider store={store}><App /></Provider>, document.getElementById('root'))
"""
HOME = "const HomeScreen = () => <h1>Latest</h1>\nexport default HomeScreen\n"
CART = "export const CartScreen = () => <h1>Cart</h1>\n"
ADMIN = "export default function AdminPage() { return null }\n"

FILES = {
    "web/package.json": PKG,
    "web/src/App.js": APP,
    "web/src/index.js": INDEX,
    "web/src/screens/HomeScreen.js": HOME,
    "web/src/screens/CartScreen.js": CART,
    "web/src/screens/AdminScreen.js": ADMIN,
}


def test_v5_component_attribute_is_a_screen():
    hits = entrypoints.from_source("<Route path='/order/:id' component={OrderScreen} />", "javascript")
    assert [(h.kind, h.detail, h.component) for h in hits] == [("screen", "/order/:id", "OrderScreen")]


def test_import_bindings():
    got = entrypoints.import_bindings(APP)
    assert got["HomeScreen"] == ("./screens/HomeScreen", "default")
    assert got["CartScreen"] == ("./screens/CartScreen", "CartScreen")
    assert got["AdminScreen"] == ("./screens/AdminScreen", "default")
    assert got["Router"] == ("react-router-dom", "BrowserRouter")


def test_a_provider_around_the_render_root_is_unwrapped():
    hits = [h for h in entrypoints.from_source(INDEX, "javascript") if h.kind == "main"]
    assert [h.component for h in hits] == ["App"]


# --------------------------------------------------------------------------- indexing


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


def _screens(conn, sha: str = indexer.WORKTREE_SHA) -> set[tuple[str, str, str]]:
    return {
        (r["kind"], r["detail"].split("  ·  ")[0], r["key"])
        for r in conn.execute(
            "SELECT e.kind, e.detail, s.key FROM entry_points e JOIN symbols s ON s.id = e.symbol_id "
            "WHERE e.commit_sha = ? AND e.kind IN ('screen', 'main')",
            (sha,),
        )
    }


EXPECTED = {
    ("screen", "/", "web/src/screens/HomeScreen.js::HomeScreen"),
    ("screen", "/cart/:id?", "web/src/screens/CartScreen.js::CartScreen"),
    ("screen", "/admin", "web/src/screens/AdminScreen.js::AdminPage"),
    ("main", "render root", "web/src/App.js::App"),
}


def test_imported_screens_resolve_in_history_and_worktree(tmp_path):
    root = _repo(tmp_path)
    _cfg, conn = _scan(root)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    assert _screens(conn) == EXPECTED
    assert _screens(conn, head) == EXPECTED


def test_changing_only_the_screen_file_keeps_its_route(tmp_path):
    root = _repo(tmp_path)
    _scan(root)
    _write(root, {"web/src/screens/HomeScreen.js": HOME.replace("Latest", "Newest")})
    _commit(root, "edit home")
    _cfg, conn = _scan(root)
    assert _screens(conn) == EXPECTED


def test_removing_a_route_drops_its_entry_point(tmp_path):
    root = _repo(tmp_path)
    _scan(root)
    _write(root, {"web/src/App.js": APP.replace("    <Route path='/admin' component={AdminScreen} />\n", "")})
    _commit(root, "drop admin")
    _cfg, conn = _scan(root)
    assert _screens(conn) == EXPECTED - {("screen", "/admin", "web/src/screens/AdminScreen.js::AdminPage")}


def test_incremental_equals_from_scratch(tmp_path):
    root = _repo(tmp_path)
    _scan(root, "inc.db")
    _write(root, {"web/src/screens/CartScreen.js": CART + "// touched\n"})
    _commit(root, "touch cart")
    _c1, inc = _scan(root, "inc.db")
    _c2, fresh = _scan(root, "fresh.db")
    assert _all_entry_points(inc) == _all_entry_points(fresh)
    assert _registrations(inc) == _registrations(fresh)
    assert _screens(inc) == _screens(fresh) == EXPECTED


def _all_entry_points(conn) -> list[tuple]:
    return sorted(
        (r["commit_sha"], r["key"] or "", r["kind"], r["detail"])
        for r in conn.execute(
            "SELECT e.commit_sha, s.key, e.kind, e.detail FROM entry_points e "
            "LEFT JOIN symbols s ON s.id = e.symbol_id"
        )
    )


def _registrations(conn) -> list[tuple]:
    return sorted(
        (r["commit_sha"], r["path"], r["detail"], r["local_name"], r["imported"], r["source"])
        for r in conn.execute(
            "SELECT rr.*, f.path FROM route_registrations rr JOIN files f ON f.id = rr.file_id"
        )
    )
