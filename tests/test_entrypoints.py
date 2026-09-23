"""M19: frontend entry-point detection — file-based routing (expo-router,
Next.js app + pages router, Remix/React Router v7 flat routes), the
package.json dependency gate, and registration-based (React Navigation /
app-root) regex detection."""

from __future__ import annotations

from codemap import entrypoints


# --------------------------------------------------------------- frontend_roots


def test_frontend_roots_gated_on_dependency():
    def read(path):
        if path == "mobile/package.json":
            return b'{"dependencies": {"expo-router": "~3.0.0", "react-native": "0.86.0"}}'
        if path == "backend/package.json":
            return b'{"dependencies": {"express": "^4.0.0"}}'
        return None

    roots = entrypoints.frontend_roots(
        ["mobile/package.json", "backend/package.json"], read
    )
    by_dir = {r.dir: r for r in roots}
    assert "mobile" in by_dir
    assert {"expo-router", "react-native"} <= by_dir["mobile"].conventions
    # an Express backend's package.json names no frontend routing convention
    assert "backend" not in by_dir


def test_frontend_roots_root_level_package_json():
    roots = entrypoints.frontend_roots(
        ["package.json"], lambda p: b'{"dependencies": {"next": "14.0.0"}}'
    )
    assert roots == [entrypoints.FrontendRoot(dir="", conventions=frozenset({"next"}))]


def test_frontend_roots_excludes_node_modules_and_malformed_json():
    hits = []

    def read(path):
        hits.append(path)
        if "malformed" in path:
            return b"{not json"
        return b'{"dependencies": {"next": "1.0.0"}}'

    roots = entrypoints.frontend_roots(
        [
            "node_modules/some-lib/package.json",
            "app/malformed/package.json",
            "package.json",
        ],
        read,
    )
    assert [r.dir for r in roots] == [""]
    assert "node_modules/some-lib/package.json" not in hits  # excluded before a read


# --------------------------------------------------------------- from_route_file


def _root(dir_: str, *conventions: str) -> entrypoints.FrontendRoot:
    return entrypoints.FrontendRoot(dir=dir_, conventions=frozenset(conventions))


def test_expo_router_screen_and_layout_and_group_stripping():
    roots = [_root("mobile", "expo-router")]
    assert entrypoints.from_route_file(
        "mobile/src/app/(tabs)/budget/index.tsx", roots
    ) == ("screen", "/budget")
    assert entrypoints.from_route_file(
        "mobile/src/app/(tabs)/budget/_layout.tsx", roots
    ) == ("layout", "/budget")
    assert entrypoints.from_route_file(
        "mobile/src/app/settings.tsx", roots
    ) == ("screen", "/settings")
    assert entrypoints.from_route_file(
        "mobile/src/app/_layout.tsx", roots
    ) == ("layout", "/")


def test_expo_router_dynamic_and_catchall_segments():
    roots = [_root("", "expo-router")]
    assert entrypoints.from_route_file("app/notes/[id].tsx", roots) == ("screen", "/notes/:id")
    assert entrypoints.from_route_file("app/[...rest].tsx", roots) == ("screen", "/*")
    assert entrypoints.from_route_file("app/+not-found.tsx", roots) == ("screen", "/ (not-found)")


def test_next_app_router_special_filenames():
    roots = [_root("", "next")]
    assert entrypoints.from_route_file("app/budget/page.tsx", roots) == ("screen", "/budget")
    assert entrypoints.from_route_file("app/budget/layout.tsx", roots) == ("layout", "/budget")
    assert entrypoints.from_route_file("app/api/users/route.ts", roots) == (
        "route", "ANY /api/users",
    )
    # a non-special file under app/ is not a Next.js page at all, and this
    # project isn't expo-router — so it's not a route either
    assert entrypoints.from_route_file("app/budget/helpers.ts", roots) is None


def test_next_pages_router():
    roots = [_root("", "next")]
    assert entrypoints.from_route_file("pages/about.tsx", roots) == ("screen", "/about")
    assert entrypoints.from_route_file("pages/api/users.ts", roots) == ("route", "ANY /users")
    assert entrypoints.from_route_file("pages/_app.tsx", roots) is None
    assert entrypoints.from_route_file("pages/_document.tsx", roots) is None


def test_app_dir_dispatch_prefers_next_special_names_over_expo_router():
    """Both conventions can be active on the same repo (a project migrating
    frameworks, or a monorepo). Next's own special filenames must win over
    the expo-router catch-all so `page.tsx` isn't mis-read as a generic
    screen named "/budget/page"."""
    roots = [_root("", "next", "expo-router")]
    assert entrypoints.from_route_file("app/budget/page.tsx", roots) == ("screen", "/budget")
    # a non-special file still falls through to the expo-router reading
    assert entrypoints.from_route_file("app/budget/custom.tsx", roots) == (
        "screen", "/budget/custom",
    )


def test_remix_flat_routes():
    roots = [_root("", "remix")]
    # `_index` (underscore) is Remix's own index-route marker; a literal
    # "index" segment (no underscore) is just an ordinary path segment.
    assert entrypoints.from_route_file("app/routes/budget._index.tsx", roots) == ("screen", "/budget")
    assert entrypoints.from_route_file("app/routes/budget.$id.tsx", roots) == ("screen", "/budget/:id")
    assert entrypoints.from_route_file("app/routes/_index.tsx", roots) == ("screen", "/")
    assert entrypoints.from_route_file("app/routes/files.$.tsx", roots) == ("screen", "/files/*")


def test_backend_app_directory_is_not_mistaken_for_a_router():
    """The whole point of gating on package.json: an `app/` (or `src/app/`)
    directory is also a common backend folder name (e.g. a Java/Spring-style
    layout), and must not produce a scenario just because a .tsx-adjacent
    extension happens to live there."""
    roots = [_root("backend", "react-dom")]  # `react-dom` alone signals nothing routable
    assert entrypoints.from_route_file("backend/src/app/routes.ts", roots) is None


def test_no_matching_root_is_none():
    assert entrypoints.from_route_file("mobile/src/app/index.tsx", []) is None


def test_non_frontend_extension_is_none():
    roots = [_root("", "expo-router")]
    assert entrypoints.from_route_file("app/index.css", roots) is None


# --------------------------------------------------------------------- from_source


def test_react_navigation_screen_registration():
    src = '<Stack.Screen name="Budget" component={BudgetOverviewScreen} />'
    hits = entrypoints.from_source(src, "tsx")
    assert len(hits) == 1
    # `detail` prefers the route's display name ("Budget"); `component` is
    # the symbol name to resolve, independent of what the route is called.
    assert hits[0] == entrypoints.SourceHit("screen", "Budget", "BudgetOverviewScreen")


def test_react_navigation_screen_name_only():
    src = '<Tab.Screen name="Home">{() => <HomeScreen />}</Tab.Screen>'
    hits = entrypoints.from_source(src, "tsx")
    assert hits[0].detail == "Home"
    assert hits[0].component is None  # no `component={...}` to resolve


def test_app_registry_and_register_root_component():
    src1 = 'AppRegistry.registerComponent(appName, () => App);'
    assert entrypoints.from_source(src1, "tsx")[0] == entrypoints.SourceHit(
        "main", "AppRegistry.registerComponent", "App"
    )
    src2 = "registerRootComponent(App);"
    assert entrypoints.from_source(src2, "tsx")[0] == entrypoints.SourceHit(
        "main", "registerRootComponent", "App"
    )


def test_render_root_jsx_and_call_forms():
    src1 = "ReactDOM.render(<App />, document.getElementById('root'));"
    assert entrypoints.from_source(src1, "tsx")[0].component == "App"
    src2 = "createRoot(rootEl).render(App());"
    hits = entrypoints.from_source(src2, "tsx")
    assert any(h.component == "App" for h in hits)


def test_from_source_ignored_for_non_js_languages():
    assert entrypoints.from_source("<Stack.Screen name=\"x\" component={X}/>", "python") == []


def test_react_router_jsx_route_table():
    src = """
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/professional" element={<Professional />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
"""
    hits = entrypoints.from_source(src, "tsx")
    by_path = {h.detail: h.component for h in hits if h.kind == "screen"}
    assert by_path == {"/": "Home", "/professional": "Professional", "*": "NotFound"}


def test_react_router_data_router_object_form():
    src = """
const router = createBrowserRouter([
  { path: "/", element: <Home /> },
  { path: "/about", element: <About /> },
]);
"""
    hits = entrypoints.from_source(src, "tsx")
    by_path = {h.detail: h.component for h in hits}
    assert by_path == {"/": "Home", "/about": "About"}


# ------------------------------------------------------------- from_decorators


def test_flask_route_reads_methods_instead_of_always_saying_any():
    assert entrypoints.from_decorators('@api_bp.route("/chat", methods=["POST"])', "python") == (
        "route", "POST /chat",
    )
    # methods=[...] with several verbs, de-duplicated and order-preserved
    assert entrypoints.from_decorators(
        '@api_bp.route("/x", methods=["GET", "POST", "GET"])', "python"
    ) == ("route", "GET, POST /x")
    # no methods= at all: Flask's own default is GET, not "ANY"
    assert entrypoints.from_decorators('@app.route("/plain")', "python") == ("route", "GET /plain")
    # .get()/.post() shorthand still reads the verb straight from the decorator
    assert entrypoints.from_decorators('@app.post("/create")', "python") == ("route", "POST /create")


def test_flask_route_gets_its_blueprint_prefix():
    assert entrypoints.from_decorators(
        '@api_bp.route("/chat", methods=["POST"])', "python", {"api_bp": "/api"}
    ) == ("route", "POST /api/chat")
    # no matching blueprint registration -> no prefix, unchanged
    assert entrypoints.from_decorators(
        '@api_bp.route("/chat")', "python", {}
    ) == ("route", "GET /chat")
    # a blueprint registered with no url_prefix at all -> unchanged
    assert entrypoints.from_decorators(
        '@api_bp.route("/chat")', "python", {"api_bp": ""}
    ) == ("route", "GET /chat")


def test_blueprint_prefixes_reads_register_blueprint_calls():
    files = {
        "app.py": b'app.register_blueprint(api_bp, url_prefix="/api")\n'
                  b'app.register_blueprint(budget_bp, url_prefix="/api/budget")\n'
                  b"app.register_blueprint(admin.bp)\n",
        "api.py": b'@api_bp.route("/chat")\ndef chat(): pass\n',
    }
    prefixes = entrypoints.blueprint_prefixes(list(files), files.get)
    assert prefixes == {"api_bp": "/api", "budget_bp": "/api/budget", "bp": ""}


def test_a_get_and_a_post_on_the_same_path_are_distinct_routes():
    """The apparent-duplicate-row bug: two different handler functions, each
    decorated with its own methods=, used to both collapse to "ANY /events"
    and look like the same route twice."""
    assert entrypoints.from_decorators(
        '@app.route("/events", methods=["GET"])', "python"
    ) == ("route", "GET /events")
    assert entrypoints.from_decorators(
        '@app.route("/events", methods=["POST"])', "python"
    ) == ("route", "POST /events")


def test_scheduled_job_decorator_is_a_task():
    assert entrypoints.from_decorators("@scheduled_job('interval', minutes=5)", "python") == (
        "task", "@scheduled_job('interval', minutes=5)",
    )


def test_scheduler_symbol_ranges_finds_plain_registration_calls():
    src = b"def register_jobs(sched):\n    sched.add_job(sync, 'interval', minutes=5)\n"
    spans = entrypoints.scheduler_symbol_ranges(src)
    assert len(spans) == 1
    start, end = spans[0]
    assert src[start:end] == b".add_job("
    # BackgroundScheduler()/BlockingScheduler()/schedule.every( all count too
    assert len(entrypoints.scheduler_symbol_ranges(b"s = BackgroundScheduler()\n")) == 1
    assert len(entrypoints.scheduler_symbol_ranges(b"schedule.every(10).minutes.do(job)\n")) == 1
    assert entrypoints.scheduler_symbol_ranges(b"def f():\n    return 1\n") == []


def test_render_root_unwraps_strict_mode_to_find_the_real_app():
    src = """
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
"""
    hits = entrypoints.from_source(src, "tsx")
    assert any(h.component == "App" for h in hits)
