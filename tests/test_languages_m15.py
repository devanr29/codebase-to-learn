"""M15 language breadth: Go, Rust, Java, C#, Ruby, PHP, C, C++.

Each of these is registered at tier 1 (T1 capture only, no per-language
resolver) — see languages/registry.py. One golden-key case per language,
in the style of test_parsing.py, plus classify_import coverage for the
per-language import syntax each of them needed (indexer.classify_import
otherwise falls back to Python-style parsing, which is wrong for all of
these — see indexer.py).
"""

from __future__ import annotations

import pytest

from codemap.indexer import classify_import
from codemap.languages.registry import spec_for_path
from codemap.parsing import parse_source
from codemap.resolve import _kind_of


def _parse(path: str, src: bytes):
    spec = spec_for_path(path)
    assert spec is not None, f"no registered language for {path}"
    return spec, parse_source(path, src, spec)


GO_SRC = b'''package main

import (
\t"fmt"
\t"myrepo/internal/util"
)

type Store struct {
\tdata map[string]string
}

type Reader interface {
\tRead() string
}

func (s *Store) Put(key string, value string) bool {
\treturn save(key, value)
}

func Load(path string) string {
\tx := read(path)
\tfmt.Println(x)
\treturn x
}

func read(path string) string {
\treturn path
}
'''

RUST_SRC = b'''use std::io;
use serde::Serialize;

struct Store {
    data: String,
}

trait Runner {
    fn run(&self);
}

impl Store {
    fn put(&self, key: &str, value: &str) -> bool {
        save(key, value)
    }
}

fn load(path: &str) -> String {
    read(path).to_string()
}

fn read(path: &str) -> String {
    path.to_string()
}
'''

JAVA_SRC = b'''import java.util.List;
import com.example.Helper;

public class Store {
    public boolean put(String key, String value) {
        return save(key, value);
    }
}

interface Reader {
    String read(String path);
}

class Loader {
    public String load(String path) {
        return read(path);
    }

    public String read(String path) {
        return path;
    }
}
'''

CSHARP_SRC = b'''using System;
using MyApp.Services;

namespace App {
  public class Store {
    public bool Put(string key, string value) {
      return Save(key, value);
    }
  }

  interface IReader {
    string Read(string path);
  }

  class Loader {
    public string Load(string path) {
      return Read(path);
    }

    public string Read(string path) {
      return path;
    }
  }
}
'''

RUBY_SRC = b'''require "json"
require_relative "./util"

class Store
  def put(key, value)
    save(key, value)
  end
end

def load(path)
  read(path)
end

def read(path)
  path
end
'''

PHP_SRC = b'''<?php
use App\\Models\\User;

class Store {
    public function put($key, $value) {
        return $this->save($key, $value);
    }
}

interface Reader {
    public function read($path);
}

function load($path) {
    return read($path);
}

function read($path) {
    return $path;
}
'''

C_SRC = b'''#include <stdio.h>
#include "local.h"

int add(int a, int b) {
    return helper(a, b);
}

int helper(int a) {
    return a;
}
'''

CPP_SRC = b'''#include <string>
#include "store.h"

class Store {
public:
    bool put(std::string key, std::string value) {
        return save(key, value);
    }
};

int helper(int a) {
    return a;
}
'''


def test_go_golden():
    spec, pf = _parse("a.go", GO_SRC)
    assert pf.ok
    assert spec.tier == 1
    keys = {s.key for s in pf.symbols}
    assert keys == {
        "a.go::Store", "a.go::Reader", "a.go::Put", "a.go::Load", "a.go::read",
    }
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.go::Store"].kind == "class"
    assert by_key["a.go::Reader"].kind == "interface"
    assert by_key["a.go::Put"].kind == "method"          # receiver-based, flat name
    assert by_key["a.go::Load"].signature == "Load(path string) -> string"
    refs = {r.target_name for r in pf.refs}
    assert {"save", "read", "Println"} <= refs
    assert [i.raw for i in pf.imports] == ['"fmt"', '"myrepo/internal/util"']


def test_rust_golden():
    spec, pf = _parse("a.rs", RUST_SRC)
    assert pf.ok
    keys = {s.key for s in pf.symbols}
    assert keys == {"a.rs::Store", "a.rs::Runner", "a.rs::put", "a.rs::load", "a.rs::read"}
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.rs::Store"].kind == "class"
    assert by_key["a.rs::Runner"].kind == "interface"
    assert by_key["a.rs::put"].kind == "function"  # impl-nested, flat (documented simplification)
    refs = {r.target_name for r in pf.refs}
    assert {"save", "read", "to_string"} <= refs
    assert [i.raw for i in pf.imports] == ["use std::io;", "use serde::Serialize;"]


def test_java_golden():
    spec, pf = _parse("a.java", JAVA_SRC)
    assert pf.ok
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.java::Store"].kind == "class"
    assert by_key["a.java::Reader"].kind == "interface"
    assert by_key["a.java::Store.put"].kind == "method"
    assert by_key["a.java::Loader.read"].kind == "method"
    refs = {r.target_name for r in pf.refs}
    assert {"save", "read"} <= refs
    assert [i.raw for i in pf.imports] == ["import java.util.List;", "import com.example.Helper;"]


def test_csharp_golden():
    spec, pf = _parse("a.cs", CSHARP_SRC)
    assert pf.ok
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.cs::Store"].kind == "class"
    assert by_key["a.cs::IReader"].kind == "interface"
    assert by_key["a.cs::Store.Put"].kind == "method"
    refs = {r.target_name for r in pf.refs}
    assert {"Save", "Read"} <= refs
    assert [i.raw for i in pf.imports] == ["using System;", "using MyApp.Services;"]


def test_ruby_golden():
    spec, pf = _parse("a.rb", RUBY_SRC)
    assert pf.ok
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.rb::Store"].kind == "class"
    assert by_key["a.rb::Store.put"].kind == "method"
    assert by_key["a.rb::load"].kind == "function"
    refs = {r.target_name for r in pf.refs}
    assert {"save", "read"} <= refs
    assert [i.raw for i in pf.imports] == ['require "json"', 'require_relative "./util"']


def test_php_golden():
    spec, pf = _parse("a.php", PHP_SRC)
    assert pf.ok
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.php::Store"].kind == "class"
    assert by_key["a.php::Reader"].kind == "interface"
    assert by_key["a.php::Store.put"].kind == "method"
    assert by_key["a.php::load"].kind == "function"
    refs = {r.target_name for r in pf.refs}
    assert {"save", "read"} <= refs
    assert [i.raw for i in pf.imports] == ["use App\\Models\\User;"]


def test_c_golden():
    spec, pf = _parse("a.c", C_SRC)
    assert pf.ok
    keys = {s.key for s in pf.symbols}
    assert keys == {"a.c::add", "a.c::helper"}
    refs = {r.target_name for r in pf.refs}
    assert refs == {"helper"}
    assert [i.raw for i in pf.imports] == ['#include <stdio.h>', '#include "local.h"']


def test_cpp_golden():
    spec, pf = _parse("a.cpp", CPP_SRC)
    assert pf.ok
    by_key = {s.key: s for s in pf.symbols}
    assert by_key["a.cpp::Store"].kind == "class"
    assert by_key["a.cpp::Store.put"].kind == "method"  # nested -> promoted, like Python/TS
    assert by_key["a.cpp::helper"].kind == "function"
    refs = {r.target_name for r in pf.refs}
    assert refs == {"save"}
    assert [i.raw for i in pf.imports] == ['#include <string>', '#include "store.h"']


@pytest.mark.parametrize(
    "raw,lang,internal,expect_mod,expect_external,expect_kind",
    [
        ('"fmt"', "go", set(), "fmt", False, "stdlib"),
        ('"github.com/foo/bar"', "go", set(), "github.com/foo/bar", True, "third_party"),
        ('"myrepo/internal/util"', "go", {"util"}, "myrepo/internal/util", False, "internal"),
        ("use std::io;", "rust", set(), "std::io", False, "stdlib"),
        ("use crate::util::helper;", "rust", set(), "crate::util::helper", False, "internal"),
        ("use serde::Serialize;", "rust", set(), "serde::Serialize", True, "third_party"),
        ("import java.util.List;", "java", set(), "java.util.List", False, "stdlib"),
        ("import com.example.Foo;", "java", set(), "com.example.Foo", True, "third_party"),
        ("using System;", "csharp", set(), "System", False, "stdlib"),
        ("using MyApp.Services;", "csharp", {"MyApp"}, "MyApp.Services", False, "internal"),
        ('require "json"', "ruby", set(), "json", False, "stdlib"),
        ('require "rails"', "ruby", set(), "rails", True, "third_party"),
        ('require_relative "./util"', "ruby", set(), "./util", False, "internal"),
        ("use App\\Models\\User;", "php", {"App"}, "App\\Models\\User", False, "internal"),
        ("use Vendor\\Package;", "php", set(), "Vendor\\Package", True, "third_party"),
        ("#include <stdio.h>", "c", set(), "stdio.h", False, "internal"),
        ('#include "local.h"', "c", set(), "local.h", False, "internal"),
    ],
)
def test_classify_import_per_language(raw, lang, internal, expect_mod, expect_external, expect_kind):
    mod, external = classify_import(raw, lang, internal)
    assert mod == expect_mod
    assert external is expect_external
    assert _kind_of(mod, lang, external, internal) == expect_kind
