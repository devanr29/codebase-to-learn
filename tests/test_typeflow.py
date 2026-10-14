"""Type-aware call resolution (``codemap/enrich/typeflow.py``) and how it folds
into the call graph (``enrich/engine.py``): each call site is resolved to the
symbol(s) it can reach, ruled external (a builtin / third-party target), or left
unknown -- and an unknown site never changes codemap's own edges."""

from __future__ import annotations

import networkx as nx
import pytest

from codemap.enrich import engine, typeflow
from codemap.impact import AMBIGUOUS, EXTRACTED, INFERRED
from codemap.languages.registry import spec_for_path
from codemap.parsing import parse_source


def _analyze(files: dict[str, str]) -> list[typeflow.Site]:
    blobs = {p: s.encode() for p, s in files.items()}
    file_rows, sym_rows = [], []
    for path, src in blobs.items():
        spec = spec_for_path(path)
        file_rows.append({"path": path, "lang": spec.name})
        for s in parse_source(path, src, spec).symbols:
            sym_rows.append({"key": s.key, "name": s.name, "file": path,
                             "start_line": s.start_line, "end_line": s.end_line})
    return typeflow.analyze(file_rows, sym_rows, blobs.get).sites


def _site(sites, src: str, name: str, nth: int = 0) -> typeflow.Site:
    hits = [s for s in sites if s.src == src and s.name == name]
    assert len(hits) > nth, f"no call to {name} from {src}: {[(s.src, s.name) for s in sites]}"
    return hits[nth]


def _resolved(sites, src, name, nth=0) -> set[str]:
    s = _site(sites, src, name, nth)
    assert s.outcome == typeflow.RESOLVED, s
    return set(s.targets)


# --------------------------------------------------------------------------- python

PY_REPO = '''\
class Repo:
    def get(self, k):
        return k

    def save(self, x):
        pass


class CachedRepo(Repo):
    def get(self, k):
        return super().get(k)
'''

PY_SERVICE = '''\
from dataclasses import dataclass, field
from typing import Optional

import requests

from .repo import Repo, CachedRepo
from app import repo as repo_mod


@dataclass
class Item:
    name: str

    def label(self) -> str:
        return self.name


class Service:
    items: list[Item] = field(default_factory=list)

    def __init__(self, repo: Repo, backup: Optional[CachedRepo] = None):
        self.repo = repo
        self.backup = backup
        self.session = requests.Session()
        self.cache = {}

    @classmethod
    def build(cls) -> "Service":
        return cls(Repo())

    @property
    def primary(self) -> Repo:
        return self.repo

    def run(self):
        self.repo.get(1)
        self.backup.get(2)
        self.cache.get(3)
        self.session.get("x")
        self.primary.save(4)
        r = repo_mod.Repo()
        r.save(5)
        for it in self.items:
            it.label()
        def inner():
            self.repo.save(6)
        inner()
        made = self.make()
        made.make()
        return len(self.items)

    def make(self):
        return Service(Repo())


def get(k):
    return k
'''


@pytest.fixture(scope="module")
def py_sites():
    return _analyze({"app/repo.py": PY_REPO, "app/service.py": PY_SERVICE})


RUN = "app/service.py::Service.run"
REPO_GET, CACHED_GET = "app/repo.py::Repo.get", "app/repo.py::CachedRepo.get"
REPO_SAVE = "app/repo.py::Repo.save"


def test_a_field_typed_by_a_constructor_parameter_resolves_with_its_overrides(py_sites):
    # `self.repo: Repo` can hold a CachedRepo at runtime, so both are reachable
    assert _resolved(py_sites, RUN, "get", 0) == {REPO_GET, CACHED_GET}
    assert _site(py_sites, RUN, "get", 0).strategy == "type"


def test_optional_unwraps_and_a_subclass_type_narrows_the_dispatch(py_sites):
    assert _resolved(py_sites, RUN, "get", 1) == {CACHED_GET}


def test_builtin_and_third_party_receivers_are_external(py_sites):
    assert _site(py_sites, RUN, "get", 2).outcome == typeflow.EXTERNAL     # dict.get
    assert _site(py_sites, RUN, "get", 3).outcome == typeflow.EXTERNAL     # requests.Session().get
    assert _site(py_sites, RUN, "len").outcome == typeflow.EXTERNAL        # a builtin


def test_properties_module_aliases_loops_and_closures(py_sites):
    assert _resolved(py_sites, RUN, "save", 0) == {REPO_SAVE}              # self.primary -> Repo
    assert _resolved(py_sites, RUN, "Repo") == {"app/repo.py::Repo"}       # repo_mod.Repo()
    assert _resolved(py_sites, RUN, "save", 1) == {REPO_SAVE}              # r = repo_mod.Repo()
    assert _resolved(py_sites, RUN, "label") == {"app/service.py::Item.label"}   # for it in list[Item]
    assert _resolved(py_sites, "app/service.py::Service.run.inner", "save") == {REPO_SAVE}
    assert _resolved(py_sites, RUN, "inner") == {"app/service.py::Service.run.inner"}


def test_return_types_are_inferred_from_agreeing_returns(py_sites):
    assert _resolved(py_sites, RUN, "make", 0) == {"app/service.py::Service.make"}
    assert _resolved(py_sites, RUN, "make", 1) == {"app/service.py::Service.make"}   # made = self.make()


def test_super_and_classmethod_cls(py_sites):
    assert _resolved(py_sites, CACHED_GET, "get") == {REPO_GET}
    assert _site(py_sites, CACHED_GET, "get").strategy == "super"
    assert _resolved(py_sites, "app/service.py::Service.build", "cls") == {"app/service.py::Service"}


def _one(files, src, name):
    return _site(_analyze(files), src, name)


def test_a_variable_bound_to_two_types_is_unknown():
    s = _one({"m.py": '''\
class A:
    def go(self): pass
class B:
    def go(self): pass
def f(flag):
    x = A()
    if flag:
        x = B()
    x.go()
'''}, "m.py::f", "go")
    assert s.outcome == typeflow.UNKNOWN


def test_none_does_not_spoil_a_binding():
    s = _one({"m.py": '''\
class A:
    def go(self): pass
def f():
    x = None
    x = A()
    x.go()
'''}, "m.py::f", "go")
    assert s.targets == ("m.py::A.go",)


def test_a_typevar_or_alias_annotation_is_not_a_type():
    s = _one({"m.py": '''\
from typing import TypeVar
T = TypeVar("T")
class A:
    def go(self): pass
def f(x: T):
    x.go()
'''}, "m.py::f", "go")
    assert s.outcome == typeflow.UNKNOWN


def test_a_protocol_or_an_abstract_stub_never_resolves_to_the_stub():
    sites = _analyze({"m.py": '''\
from abc import ABC, abstractmethod
from typing import Protocol
class Saver(Protocol):
    def save(self) -> None: ...
class Base(ABC):
    @abstractmethod
    def load(self): ...
class Impl(Base):
    def load(self): return 1
class Lone(ABC):
    def go(self):
        raise NotImplementedError
def f(s: Saver, b: Base, l: Lone):
    s.save()
    b.load()
    l.go()
'''})
    assert _site(sites, "m.py::f", "save").outcome == typeflow.UNKNOWN     # structural: implementers unknown
    assert _resolved(sites, "m.py::f", "load") == {"m.py::Impl.load"}      # only the override can run
    assert _site(sites, "m.py::f", "go").outcome == typeflow.UNKNOWN       # a stub with no override


def test_an_annotation_the_code_contradicts_is_not_trusted():
    # found on codemap itself: `-> Phase` that sometimes returns a duck-typed
    # _NullPhase (not a subclass). Trusting the annotation dropped a true edge.
    sites = _analyze({"m.py": '''\
class Phase:
    def advance(self): pass
class _NullPhase:
    def advance(self): pass
NO_PHASE = _NullPhase()
class Reporter:
    def __init__(self, silent: bool):
        self.silent = silent
        self.current: Phase = NO_PHASE
    def phase(self) -> Phase:
        if self.silent:
            return NO_PHASE
        return Phase()
    def honest(self) -> Phase:
        return Phase()
def f(r: Reporter):
    r.phase().advance()
    r.current.advance()
    r.honest().advance()
'''})
    assert _site(sites, "m.py::f", "advance", 0).outcome == typeflow.UNKNOWN
    assert _site(sites, "m.py::f", "advance", 1).outcome == typeflow.UNKNOWN
    assert _resolved(sites, "m.py::f", "advance", 2) == {"m.py::Phase.advance"}


def test_an_unbound_bare_name_is_unknown_unless_it_is_a_builtin():
    sites = _analyze({"m.py": '''\
def f():
    helper()
    print(1)
    open("x").read()
'''})
    assert _site(sites, "m.py::f", "helper").outcome == typeflow.UNKNOWN
    assert _site(sites, "m.py::f", "print").outcome == typeflow.EXTERNAL
    assert _site(sites, "m.py::f", "read").outcome == typeflow.EXTERNAL


def test_a_star_import_keeps_bare_names_unknown():
    s = _one({"m.py": "from lib import *\ndef f():\n    print(1)\n"}, "m.py::f", "print")
    assert s.outcome == typeflow.UNKNOWN


def test_package_init_re_exports_are_followed():
    sites = _analyze({
        "pkg/__init__.py": "from .impl import Engine\n",
        "pkg/impl.py": "class Engine:\n    def start(self): pass\n",
        "main.py": "from pkg import Engine\ndef run():\n    Engine().start()\n",
    })
    assert _resolved(sites, "main.py::run", "start") == {"pkg/impl.py::Engine.start"}
    assert _resolved(sites, "main.py::run", "Engine") == {"pkg/impl.py::Engine"}
    assert _site(sites, "main.py::run", "Engine").strategy == "import"


# --------------------------------------------------------------------------- typescript

TS_REPO = "export class Repo {\n  get(k: string) { return k; }\n  save() {}\n}\n"
TS_INDEX = "export * from './repo';\nexport { fmt as format } from './util';\n"
TS_UTIL = "export function fmt(x: string) { return x; }\nexport default class Clock { now() { return 1; } }\n"
TS_API = '''\
import { Repo, format } from './lib';
import Clock from './lib/util';
import * as util from './lib/util';

interface Store { get(k: string): string; }

export class Api {
  constructor(private readonly repo: Repo, private store: Store) {}

  load = async () => { this.repo.get('a'); };

  async fetchRepo(): Promise<Repo> { return this.repo; }

  async go() {
    const r = await this.fetchRepo();
    r.save();
    format('x');
    util.fmt('y');
    new Clock().now();
    this.store.get('k');
    (this.lookup() as Repo).save();
    console.log('done');
    [1, 2].map((n) => n);
  }

  lookup(): unknown { return null; }
}
'''


@pytest.fixture(scope="module")
def ts_sites():
    return _analyze({"web/lib/repo.ts": TS_REPO, "web/lib/index.ts": TS_INDEX,
                     "web/lib/util.ts": TS_UTIL, "web/api.ts": TS_API})


GO = "web/api.ts::Api.go"


def test_ts_constructor_parameter_properties_and_class_field_arrows(ts_sites):
    # the arrow in `load = async () => ...` is not a symbol; its calls belong to the class
    assert _resolved(ts_sites, "web/api.ts::Api", "get") == {"web/lib/repo.ts::Repo.get"}


def test_ts_await_unwraps_a_promise_return_type(ts_sites):
    assert _resolved(ts_sites, GO, "fetchRepo") == {"web/api.ts::Api.fetchRepo"}
    assert _resolved(ts_sites, GO, "save", 0) == {"web/lib/repo.ts::Repo.save"}


def test_ts_barrels_default_and_namespace_imports(ts_sites):
    assert _resolved(ts_sites, GO, "format") == {"web/lib/util.ts::fmt"}     # export { fmt as format } from
    assert _resolved(ts_sites, GO, "fmt") == {"web/lib/util.ts::fmt"}        # import * as util
    assert _resolved(ts_sites, GO, "Clock") == {"web/lib/util.ts::Clock"}    # new <default export>
    assert _site(ts_sites, GO, "Clock").strategy == "new"
    assert _resolved(ts_sites, GO, "now") == {"web/lib/util.ts::Clock.now"}


def test_ts_casts_interfaces_and_globals(ts_sites):
    assert _resolved(ts_sites, GO, "save", 1) == {"web/lib/repo.ts::Repo.save"}   # (x as Repo).save()
    assert _site(ts_sites, GO, "get").outcome == typeflow.UNKNOWN                # an interface: who implements it?
    assert _site(ts_sites, GO, "log").outcome == typeflow.EXTERNAL
    assert _site(ts_sites, GO, "map").outcome == typeflow.EXTERNAL


# --------------------------------------------------------------------------- merging


def _site_of(src, name, outcome, *targets, line=1, strategy="type"):
    return typeflow.Site(src, name, line, outcome, tuple(targets), strategy)


def _graph():
    g = nx.DiGraph()
    g.add_nodes_from(["a.py::f", "b.py::A.get", "c.py::B.get", "d.py::C.get", "a.py::g"])
    g.add_edge("a.py::f", "b.py::A.get", confidence=AMBIGUOUS)
    g.add_edge("a.py::f", "c.py::B.get", confidence=AMBIGUOUS)
    g.add_edge("a.py::f", "d.py::C.get", confidence=INFERRED)
    return g


NAMES = {"a.py::f": "f", "b.py::A.get": "get", "c.py::B.get": "get", "d.py::C.get": "get", "a.py::g": "g"}


def test_merge_upgrades_the_resolved_guess_and_drops_the_others():
    g = _graph()
    changes = engine.merge(g, [_site_of("a.py::f", "get", typeflow.RESOLVED, "b.py::A.get", line=7)], NAMES)
    assert changes == {"added": 0, "upgraded": 1, "dropped": 1}
    assert g.edges["a.py::f", "b.py::A.get"] == {"confidence": INFERRED, "via": "types",
                                                 "type_strategy": "type", "line": 7}
    assert not g.has_edge("a.py::f", "c.py::B.get")
    assert g.edges["a.py::f", "d.py::C.get"]["confidence"] == INFERRED   # a confident edge is never touched


def test_merge_keeps_every_guess_while_one_call_to_the_name_is_unknown():
    g = _graph()
    changes = engine.merge(g, [_site_of("a.py::f", "get", typeflow.RESOLVED, "b.py::A.get"),
                               _site_of("a.py::f", "get", typeflow.UNKNOWN)], NAMES)
    assert changes == {"added": 0, "upgraded": 1, "dropped": 0}
    assert g.has_edge("a.py::f", "c.py::B.get")


def test_merge_drops_guesses_for_an_external_call_and_adds_missing_links():
    g = _graph()
    changes = engine.merge(g, [_site_of("a.py::f", "get", typeflow.EXTERNAL),
                               _site_of("a.py::f", "g", typeflow.RESOLVED, "a.py::g", strategy="local")], NAMES)
    assert changes == {"added": 1, "upgraded": 0, "dropped": 2}
    assert g.edges["a.py::f", "a.py::g"]["confidence"] == EXTRACTED       # same file
    assert g.has_edge("a.py::f", "d.py::C.get")
