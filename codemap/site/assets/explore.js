/* Codegraph Explorer — frozen runtime. No libraries, no build step.
   Everything variable comes from the inlined JSON in #codemap-data. Do NOT
   regenerate this file from Python. All DOM is built with textContent / real
   nodes — no string HTML is ever assigned to the document. */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("codemap-data").textContent);
  var APP = document.getElementById("app");
  var SVGNS = "http://www.w3.org/2000/svg";
  var SVG_TAGS = { svg: 1, g: 1, path: 1, circle: 1, text: 1, ellipse: 1, line: 1, rect: 1 };

  // ---- tiny DOM helper -------------------------------------------------
  function el(tag, attrs, kids) {
    var n = SVG_TAGS[tag]
      ? document.createElementNS(SVGNS, tag)
      : document.createElement(tag);
    attrs = attrs || {};
    for (var k in attrs) {
      if (attrs[k] == null) continue;
      if (k === "text") n.textContent = attrs[k];
      else if (k === "on") for (var e in attrs[k]) n.addEventListener(e, attrs[k][e]);
      else if (k === "class") n.setAttribute("class", attrs[k]);
      else n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) {
      if (c == null || c === false) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  // split "text with `code` spans" into an array of text nodes / <code> elements
  function codeify(text) {
    var out = [];
    String(text == null ? "" : text).split(/`([^`]+)`/).forEach(function (part, i) {
      if (part === "") return;
      out.push(i % 2 ? el("code", { text: part }) : document.createTextNode(part));
    });
    return out;
  }

  // ---- graph indices ------------------------------------------------
  var N = DATA.nodes || [];
  var E = DATA.edges || [];
  var FILES = DATA.files || [];
  var outAdj = N.map(function () { return []; });
  var inAdj = N.map(function () { return []; });
  E.forEach(function (e) { outAdj[e.s].push(e.t); inAdj[e.t].push(e.s); });
  var keyToI = {};
  N.forEach(function (n) { keyToI[n.key] = n.i; });
  var fileByPath = {};
  FILES.forEach(function (f) { fileByPath[f.path] = f; });
  var incidentEdges = N.map(function () { return []; });
  E.forEach(function (e, i) { incidentEdges[e.s].push(i); incidentEdges[e.t].push(i); });

  function bfs(start, adj, maxHops) {
    var dist = new Map([[start, 0]]);
    var frontier = [start];
    for (var h = 1; h <= maxHops; h++) {
      var next = [];
      for (var i = 0; i < frontier.length; i++) {
        var nb = adj[frontier[i]] || [];
        for (var j = 0; j < nb.length; j++)
          if (!dist.has(nb[j])) { dist.set(nb[j], h); next.push(nb[j]); }
      }
      frontier = next;
      if (!next.length) break;
    }
    dist.delete(start);
    return dist;
  }
  function reachSet(start, adj) {
    var seen = new Set([start]);
    var stack = [start];
    while (stack.length) {
      var nb = adj[stack.pop()] || [];
      for (var i = 0; i < nb.length; i++)
        if (!seen.has(nb[i])) { seen.add(nb[i]); stack.push(nb[i]); }
    }
    seen.delete(start);
    return seen;
  }

  // ---- organic edge geometry (ported from the design's support script) --
  function rnd(s) { var x = Math.sin(s * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
  function bez(a, c1, c2, b, t) {
    var u = 1 - t;
    return {
      x: u * u * u * a.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * b.x,
      y: u * u * u * a.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * b.y,
    };
  }
  function dendrite(a, b, seed, opt) {
    opt = opt || {};
    var bow = opt.bow == null ? 0.34 : opt.bow;
    var dx = b.x - a.x, dy = b.y - a.y;
    var len = Math.hypot(dx, dy) || 1;
    var px = -dy / len, py = dx / len;
    var sway = (rnd(seed) - 0.5) * len * 0.22 + (opt.sway || 0);
    var c1 = { x: a.x + dx * bow + px * sway, y: a.y + dy * bow + py * sway };
    var c2 = { x: b.x - dx * bow + px * sway * 0.6, y: b.y - dy * bow + py * sway * 0.6 };
    var d = "M " + a.x + " " + a.y + " C " + c1.x + " " + c1.y + " " + c2.x + " " + c2.y +
            " " + b.x + " " + b.y;
    function stub(t, i) {
      var p = bez(a, c1, c2, b, t);
      var q = bez(a, c1, c2, b, Math.min(0.999, t + 0.05));
      var tx = q.x - p.x, ty = q.y - p.y, tl = Math.hypot(tx, ty) || 1;
      var sgn = rnd(seed * 3 + i) > 0.5 ? 1 : -1;
      var L = 8 + rnd(seed * 7 + i) * 15;
      var ex = p.x + (tx / tl) * L * 0.55 + (-ty / tl) * L * sgn;
      var ey = p.y + (ty / tl) * L * 0.55 + (tx / tl) * L * sgn;
      return "M " + p.x + " " + p.y + " Q " +
        (p.x + (-ty / tl) * L * 0.5 * sgn) + " " + (p.y + (tx / tl) * L * 0.5 * sgn) +
        " " + ex + " " + ey;
    }
    return { d: d, b1: stub(0.3, 1), b2: stub(0.62, 2) };
  }
  function hashSeed(str) {
    var h = 0;
    for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
    return Math.abs(h % 100000) + 1;
  }

  var VBW = 1040, VBH = 700;
  var NODE_STYLE = {
    entry: { r: 12, fill: "#232532", stroke: "#d2cefd", halo: 26, hc: "rgba(145,132,217,.14)" },
    focus: { r: 18, fill: "#4a4180", stroke: "#d2cefd", halo: 40, hc: "rgba(145,132,217,.20)" },
    hot:   { r: 10, fill: "#3b3466", stroke: "#b5abfc", halo: 20, hc: "rgba(145,132,217,.12)" },
    file:  { r: 8,  fill: "#242736", stroke: "#595d6c", halo: 0,  hc: "rgba(0,0,0,0)" },
    node:  { r: 7,  fill: "#232532", stroke: "#75798c", halo: 0,  hc: "rgba(0,0,0,0)" },
    dead:  { r: 6,  fill: "#1a1c28", stroke: "#3f424d", halo: 0,  hc: "rgba(0,0,0,0)" },
    ext:   { r: 5,  fill: "#1f2130", stroke: "#595d6c", halo: 0,  hc: "rgba(0,0,0,0)" },
  };

  // ---- app state ---------------------------------------------------
  var state = {
    tab: "graph",
    grain: 2,
    hops: 2,
    focus: null,
    fileScope: null,
    showDead: true,
    highlight: null,
    view: { x: 0, y: 0, k: 1 },
    touched: null,
    module: null,
  };

  // ---- routing ---------------------------------------------------
  function parseHash() {
    var h = (location.hash || "#/graph").replace(/^#/, "");
    var parts = h.split("/").filter(Boolean);
    return { tab: parts[0] || "graph", arg: decodeURIComponent(parts.slice(1).join("/") || "") };
  }
  function go(tab, arg) {
    location.hash = "#/" + tab + (arg ? "/" + encodeURIComponent(arg) : "");
  }
  window.addEventListener("hashchange", route);

  function route() {
    var r = parseHash();
    state.tab = ["graph", "learn", "timeline"].indexOf(r.tab) >= 0 ? r.tab : "graph";
    if (state.tab === "graph") {
      if (r.arg && keyToI[r.arg] != null) { state.focus = keyToI[r.arg]; state.fileScope = null; }
      else if (!r.arg) state.focus = null;
    } else if (state.tab === "learn") {
      state.module = r.arg ||
        (DATA.learn && DATA.learn.modules[0] && DATA.learn.modules[0].id) || "orientation";
    }
    render();
  }

  // ---- topbar ---------------------------------------------------
  function topbar() {
    var s = DATA.stats || {};
    var tabs = [
      ["graph", "ph ph-graph", "Graph"],
      ["learn", "ph ph-graduation-cap", "Learn"],
      ["timeline", "ph ph-git-commit", "Timeline"],
    ].map(function (t) {
      return el("button", {
        class: "tab" + (state.tab === t[0] ? " active" : ""),
        on: { click: function () { go(t[0]); } },
      }, [el("i", { class: t[1] }), t[2]]);
    });
    return el("div", { class: "topbar" }, [
      el("div", { class: "brand" }, [
        el("i", { class: "ph ph-graph" }), el("span", { text: "codegraph" }),
        el("span", { class: "ver", text: (DATA.generator || "").replace("codemap ", "v") }),
      ]),
      el("div", { class: "tabs" }, tabs),
      el("div", { class: "spacer" }),
      stat("files", s.files), stat("symbols", s.symbols),
      stat("edges", s.edges), stat("cycles", s.cycles),
    ]);
  }
  function stat(label, v) {
    return el("div", { class: "stat" }, [label, el("b", { text: v == null ? "0" : String(v) })]);
  }
  function staleBanner() {
    if (!DATA.behind || DATA.commit === DATA.head) return null;
    return el("div", { class: "stale-banner" }, [
      el("i", { class: "ph ph-warning" }),
      el("span", {}, [
        "Built from ", el("code", { text: DATA.commit_short }), " — HEAD is now ",
        el("code", { text: DATA.head_short }),
        " (" + DATA.behind + " commit" + (DATA.behind === 1 ? "" : "s") + " ahead). Run ",
        el("code", { text: "codemap explore" }), ".",
      ]),
    ]);
  }

  // ---- source tree (left rail) -----------------------------------
  function buildTree() {
    var root = { name: "", dirs: {}, files: [] };
    FILES.forEach(function (f) {
      var segs = f.path.split("/");
      var cur = root;
      for (var i = 0; i < segs.length - 1; i++) {
        cur.dirs[segs[i]] = cur.dirs[segs[i]] || { name: segs[i], dirs: {}, files: [] };
        cur = cur.dirs[segs[i]];
      }
      cur.files.push(f);
    });
    return root;
  }
  var TREE = buildTree();
  var treeOpen = {};

  function renderTreeNode(node, prefix, depth, frag) {
    Object.keys(node.dirs).sort().forEach(function (dn) {
      var p = prefix + dn + "/";
      if (!(p in treeOpen)) treeOpen[p] = depth < 1;
      frag.appendChild(el("div", {
        class: "trow", style: "padding-left:" + (6 + depth * 11) + "px",
        on: { click: function () { treeOpen[p] = !treeOpen[p]; renderRail(); } },
      }, [
        el("i", { class: treeOpen[p] ? "ph ph-folder-open" : "ph ph-folder" }),
        el("span", { class: "tname", text: dn }),
      ]));
      var kids = el("div", { class: "tchildren" + (treeOpen[p] ? " open" : "") });
      renderTreeNode(node.dirs[dn], p, depth + 1, kids);
      frag.appendChild(kids);
    });
    node.files.slice().sort(function (a, b) { return a.path < b.path ? -1 : 1; }).forEach(function (f) {
      var fileSel = state.fileScope === f.fi ||
        (state.focus != null && N[state.focus] && N[state.focus].file === f.path);
      frag.appendChild(el("div", {
        class: "trow" + (fileSel ? " sel" : ""),
        style: "padding-left:" + (6 + depth * 11) + "px",
        on: { click: function () {
          treeOpen["f:" + f.path] = !treeOpen["f:" + f.path];
          state.fileScope = f.fi;
          if (f.symbols.length) go("graph", N[f.symbols[0]].key);
          else renderRail();
        } },
      }, [
        el("i", { class: f.lang === "python" ? "ph ph-file-py" : "ph ph-file-ts" }),
        el("span", { class: "tname", text: f.path.split("/").pop() }),
        el("span", { class: "tcount", text: f.symbols.length || "" }),
      ]));
      var open = treeOpen["f:" + f.path];
      var kids = el("div", { class: "tchildren" + (open ? " open" : "") });
      f.symbols.forEach(function (si) {
        var n = N[si];
        kids.appendChild(el("div", {
          class: "trow" + (state.focus === si ? " sel" : "") +
            (n.fan_in === 0 && n.entry.length === 0 ? " dim" : ""),
          style: "padding-left:" + (6 + (depth + 1) * 11 + 6) + "px",
          on: { click: function (ev) { ev.stopPropagation(); go("graph", n.key); } },
        }, [
          el("span", { class: "tkind", text: n.kind.slice(0, 4) }),
          el("span", { class: "tname", text: n.name }),
          el("span", { class: "tcount", text: n.fan_in || "" }),
        ]));
      });
      frag.appendChild(kids);
    });
  }

  var railEl;
  function renderRail() {
    if (!railEl) return;
    var tree = railEl.querySelector(".tree");
    clear(tree);
    tree.appendChild(el("div", { class: "tree-kicker", text: "SOURCE" }));
    var frag = document.createDocumentFragment();
    renderTreeNode(TREE, "", 0, frag);
    tree.appendChild(frag);
  }
  function rail() {
    var s = DATA.stats || {};
    railEl = el("div", { class: "rail" }, [
      el("div", { class: "rail-search", on: { click: openPalette } }, [
        el("i", { class: "ph ph-magnifying-glass" }),
        el("input", { placeholder: "Find symbol…", readonly: "readonly" }),
        el("kbd", { text: navigator.platform.indexOf("Mac") >= 0 ? "⌘K" : "Ctrl K" }),
      ]),
      el("div", { class: "tree" }),
      el("div", { class: "rail-foot" }, [
        kv("Files indexed", s.files),
        kv("Symbols", s.symbols + (DATA.degraded ? " / " + DATA.degraded.total : "")),
        kv("Call edges", s.edges),
        kv("Unreachable", s.unreachable, true),
      ]),
    ]);
    renderRail();
    return railEl;
  }
  function kv(label, v, warn) {
    return el("div", { class: "kv" + (warn ? " warn" : "") }, [
      el("span", { text: label }), el("b", { text: v == null ? "0" : String(v) }),
    ]);
  }

  // ---- node → navigation target -----------------------------
  // A placed node whose `i` is a string (module / file / external) is not a
  // symbol, so it can't be focused directly. Give it an `act()` that drills to
  // something real instead, so every node on the canvas responds to a click.
  function fileTopSymbol(f) {
    var best = null;
    (f.symbols || []).forEach(function (si) {
      if (best == null || N[si].fan_in > N[best].fan_in) best = si;
    });
    return best == null ? null : N[best].key;
  }
  function fileAct(f) {
    return function () {
      state.fileScope = f.fi;
      var key = fileTopSymbol(f);
      if (key) go("graph", key);
      else { state.grain = 3; render(); }
    };
  }

  // ---- graph layout -------------------------------------------
  function layoutOverview() {
    var grain = state.grain, mods = DATA.modules || [];
    var placed = [], links = [], lobes = [];
    var M = Math.max(mods.length, 1);
    var cx = VBW / 2, cy = VBH / 2;
    var ringRx = VBW * 0.34, ringRy = VBH * 0.30;
    var centers = {};
    mods.forEach(function (m, mi) {
      var ang = (mi / M) * Math.PI * 2 - Math.PI / 2;
      var mx = M === 1 ? cx : cx + Math.cos(ang) * ringRx;
      var my = M === 1 ? cy : cy + Math.sin(ang) * ringRy;
      var span = Math.sqrt(m.files.length + 1);
      centers[m.name] = { x: mx, y: my, rx: 70 + span * 26, ry: 54 + span * 20 };
      lobes.push({ x: mx, y: my, rx: centers[m.name].rx, ry: centers[m.name].ry, name: m.name });
    });

    if (grain <= 1) {
      mods.forEach(function (m, mi) {
        var c = centers[m.name];
        placed.push({ i: "mod:" + m.name, x: c.x, y: c.y, style: NODE_STYLE.hot,
          label: m.name + "  (" + m.symbol_count + ")", kind: "mod",
          act: function () { state.grain = 2; state.view = { x: 0, y: 0, k: 1 }; render(); } });
      });
      var seen = {};
      (DATA.file_edges || []).forEach(function (fe) {
        var a = FILES[fe.s].module, b = FILES[fe.t].module;
        if (a === b || seen[a + " " + b]) return;
        seen[a + " " + b] = 1;
        links.push({ a: centers[a], b: centers[b], seed: hashSeed(a + b), hot: true });
      });
      return { placed: placed, links: links, lobes: lobes };
    }

    var perModule = {};
    var items = grain === 2
      ? FILES.map(function (f) {
          return { key: "file:" + f.path, mod: f.module, label: f.path.split("/").pop(),
            weight: f.symbols.reduce(function (a, si) { return a + N[si].fan_in; }, 0), ref: f, kind: "file" };
        })
      : N.map(function (n) {
          return { key: n.key, mod: n.module, label: n.name, weight: n.fan_in + n.churn * 2,
            ref: n, kind: "sym", idx: n.i };
        });
    items.forEach(function (it) { (perModule[it.mod] || (perModule[it.mod] = [])).push(it); });

    var pos = {};
    Object.keys(perModule).forEach(function (mn) {
      var arr = perModule[mn].sort(function (a, b) { return b.weight - a.weight; });
      if (grain === 3 && arr.length > 42) arr = arr.slice(0, 42);
      var c = centers[mn] || { x: cx, y: cy, rx: 120, ry: 90 };
      arr.forEach(function (it, k) {
        var a = (k / Math.max(arr.length, 1)) * Math.PI * 2 + rnd(hashSeed(it.key)) * 0.9;
        var rr = 0.34 + rnd(hashSeed(it.key) * 2) * 0.5;
        var x = c.x + Math.cos(a) * c.rx * rr, y = c.y + Math.sin(a) * c.ry * rr;
        pos[it.key] = { x: x, y: y };
        var dead = it.kind === "sym" && it.ref.fan_in === 0 && it.ref.entry.length === 0;
        if (dead && !state.showDead) return;
        var style = it.kind === "file" ? NODE_STYLE.file
          : it.ref.entry.length ? NODE_STYLE.entry
          : dead ? NODE_STYLE.dead
          : it.ref.fan_in >= 6 ? NODE_STYLE.hot : NODE_STYLE.node;
        placed.push({ i: it.idx == null ? it.key : it.idx, x: x, y: y, style: style,
          label: it.label, kind: it.kind, dead: dead,
          act: it.kind === "file" ? fileAct(it.ref) : null });
      });
    });

    if (grain === 2) {
      (DATA.file_edges || []).forEach(function (fe) {
        var a = pos["file:" + FILES[fe.s].path], b = pos["file:" + FILES[fe.t].path];
        if (a && b) links.push({ a: a, b: b, seed: fe.s * 131 + fe.t,
          hot: FILES[fe.s].module !== FILES[fe.t].module });
      });
    } else {
      var cap = 0;
      E.forEach(function (e) {
        var a = pos[N[e.s].key], b = pos[N[e.t].key];
        if (!a || !b) return;
        if (e.tier === 1 && N[e.s].fan_in < 3 && N[e.t].fan_in < 3 && cap++ > 220) return;
        links.push({ a: a, b: b, seed: e.s * 131 + e.t, hot: e.tier === 2, dyn: e.namebased });
      });
    }
    return { placed: placed, links: links, lobes: lobes };
  }

  function layoutFocus() {
    var f = state.focus;
    var soma = { x: VBW / 2, y: VBH / 2 };
    var placed = [{ i: f, x: soma.x, y: soma.y, style: NODE_STYLE.focus, label: N[f].name, kind: "focus" }];
    var links = [];
    function ring(dist, side) {
      var byHop = {};
      Array.from(dist.entries()).forEach(function (p) { (byHop[p[1]] = byHop[p[1]] || []).push(p[0]); });
      Object.keys(byHop).forEach(function (hk) {
        var h = +hk, group = byHop[hk];
        group.forEach(function (idx, k) {
          var t = group.length === 1 ? 0.5 : k / (group.length - 1);
          var ang = (side === "in" ? Math.PI : 0) + (t - 0.5) * 1.7;
          var r = 120 + h * 92 + (rnd(hashSeed(N[idx].key)) - 0.5) * 34;
          var x = soma.x + Math.cos(ang) * r * 0.98, y = soma.y + Math.sin(ang) * r * 0.72;
          var style = N[idx].entry.length ? NODE_STYLE.entry
            : N[idx].fan_in >= 6 ? NODE_STYLE.hot : NODE_STYLE.node;
          placed.push({ i: idx, x: x, y: y, style: style, label: N[idx].name,
            kind: side === "in" ? "caller" : "callee" });
          links.push({ a: side === "in" ? { x: x, y: y } : soma,
            b: side === "in" ? soma : { x: x, y: y },
            seed: hashSeed(N[idx].key), hot: h === 1, thin: side === "in" });
        });
      });
    }
    ring(bfs(f, inAdj, state.hops), "in");
    ring(bfs(f, outAdj, state.hops), "out");

    var file = fileByPath[N[f].file];
    if (file && file.imports.length) {
      file.imports.slice(0, 6).forEach(function (dep, k) {
        var ang = -0.6 + (k / Math.max(file.imports.length - 1, 1)) * 1.2;
        var x = soma.x + Math.cos(ang) * 430 * 0.98, y = soma.y + Math.sin(ang) * 430 * 0.72;
        placed.push({ i: "ext:" + dep, x: x, y: y, style: NODE_STYLE.ext, label: dep, kind: "ext" });
        links.push({ a: soma, b: { x: x, y: y }, seed: hashSeed(dep), dyn: true });
      });
    }
    return { placed: placed, links: links, lobes: [] };
  }

  function renderGraphSVG() {
    var lay = state.focus != null ? layoutFocus() : layoutOverview();
    var g = el("g", { class: "pz",
      transform: "translate(" + state.view.x + "," + state.view.y + ") scale(" + state.view.k + ")" });

    lay.lobes.forEach(function (lo) {
      g.appendChild(el("ellipse", { cx: lo.x, cy: lo.y, rx: lo.rx, ry: lo.ry,
        fill: "rgba(147,151,171,.035)", stroke: "rgba(147,151,171,.18)", "stroke-width": 1 }));
      g.appendChild(el("text", { x: lo.x, y: lo.y - lo.ry - 6, "text-anchor": "middle",
        "font-size": 12, "font-weight": 600, "letter-spacing": ".08em", fill: "#75798c",
        text: lo.name.toUpperCase() }));
    });

    var hlSet = null;
    if (state.highlight != null) {
      hlSet = new Set();
      (incidentEdges[state.highlight] || []).forEach(function (ei) { hlSet.add(E[ei].s + " " + E[ei].t); });
    }
    var edgeG = el("g", { fill: "none", "stroke-linecap": "round" });
    lay.links.forEach(function (lk) {
      if (!lk.a || !lk.b) return;
      var d = dendrite(lk.a, lk.b, lk.seed, { bow: lk.hot ? 0.42 : 0.3 });
      var col = lk.dyn ? "var(--edge-dyn)" : lk.hot ? "var(--edge-hot)" : "var(--edge-neutral)";
      var w = lk.hot ? 1.6 : lk.thin ? 1 : 1.1;
      var op = hlSet ? 0.1 : lk.dyn ? 0.5 : 0.8;
      var grp = el("g");
      if (lk.hot && !hlSet)
        grp.appendChild(el("path", { d: d.d, stroke: "rgba(145,132,217,.28)", "stroke-width": 7, opacity: 0.5 }));
      grp.appendChild(el("path", { d: d.d, stroke: col, "stroke-width": w, opacity: op }));
      grp.appendChild(el("path", { d: d.b1, stroke: col, "stroke-width": 0.7, opacity: op * 0.6 }));
      grp.appendChild(el("path", { d: d.b2, stroke: col, "stroke-width": 0.6, opacity: op * 0.4 }));
      edgeG.appendChild(grp);
    });
    g.appendChild(edgeG);

    var nodeG = el("g");
    lay.placed.forEach(function (p) {
      var st = p.style;
      var wrap = el("g", { class: "gnode" });
      if (st.halo) wrap.appendChild(el("circle", { cx: p.x, cy: p.y, r: st.halo, fill: st.hc }));
      wrap.appendChild(el("circle", { class: "body", cx: p.x, cy: p.y, r: st.r,
        fill: st.fill, stroke: st.stroke, "stroke-width": 1.4 }));
      wrap.appendChild(el("text", { x: p.x, y: p.y + st.r + 12, "text-anchor": "middle",
        "font-size": p.kind === "focus" ? 12 : 10.5,
        fill: p.kind === "focus" ? "#f5f4ff" : p.dead ? "#595d6c" : "#b2b6ca", text: p.label }));
      var act = p.act || (typeof p.i === "number"
        ? function () { go("graph", N[p.i].key); } : null);
      wrap.style.cursor = act ? "pointer" : "default";
      if (act)
        wrap.addEventListener("click", function (ev) { ev.stopPropagation(); act(); });
      if (typeof p.i === "number") {
        wrap.addEventListener("mouseenter", function () { state.highlight = p.i; paintGraph(); });
        wrap.addEventListener("mouseleave", function () { state.highlight = null; paintGraph(); });
      }
      nodeG.appendChild(wrap);
    });
    g.appendChild(nodeG);
    return g;
  }

  var canvasEl, svgEl;
  function paintGraph() {
    if (!svgEl) return;
    clear(svgEl);
    svgEl.appendChild(renderGraphSVG());
  }

  function stage() {
    var n = state.focus != null ? N[state.focus] : null;
    var crumb = el("div", { class: "crumb" });
    if (n) {
      n.file.split("/").forEach(function (s, i, a) {
        if (i) crumb.appendChild(el("i", { class: "ph ph-caret-right" }));
        crumb.appendChild(el("span", { class: i === a.length - 1 ? "cur" : "", text: s }));
      });
      crumb.appendChild(el("i", { class: "ph ph-caret-right" }));
      crumb.appendChild(el("span", { class: "cur", text: n.name + "()" }));
    } else {
      crumb.appendChild(el("span", { class: "cur", text: "all modules" }));
    }

    var grainSeg = el("div", { class: "seg" },
      ["Package", "Module", "File", "Function"].map(function (label, gi) {
        return el("button", { class: state.grain === gi ? "on" : "",
          on: { click: function () { state.grain = gi; state.view = { x: 0, y: 0, k: 1 }; render(); } } },
          [label]);
      }));

    var hop = el("div", { class: "hopwrap" }, [
      "hops",
      el("input", { type: "range", min: "1", max: "4", value: String(state.hops),
        on: { input: function (e) { state.hops = +e.target.value; paintGraph(); updateCap(); } } }),
      el("b", { class: "mono", text: String(state.hops) }),
    ]);

    var deadPill = el("button", { class: "pill" + (state.showDead ? " on" : ""),
      on: { click: function () { state.showDead = !state.showDead; render(); } } },
      [el("i", { class: "ph ph-eye-slash" }), "Unreachable"]);
    var resetPill = el("button", { class: "pill",
      on: { click: function () { state.focus = null; state.fileScope = null; state.touched = null; go("graph"); } } },
      [el("i", { class: "ph ph-arrow-counter-clockwise" }), "Whole graph"]);

    var bar = el("div", { class: "stage-bar" }, [
      crumb, grainSeg, state.focus != null ? hop : null,
      el("div", { class: "spacer" }),
      state.focus != null ? resetPill : deadPill,
    ]);

    canvasEl = el("div", { class: "canvas" });
    svgEl = el("svg", { viewBox: "0 0 " + VBW + " " + VBH, preserveAspectRatio: "xMidYMid meet" });
    canvasEl.appendChild(svgEl);
    canvasEl.appendChild(legend());
    canvasEl.appendChild(zoombox());
    wireCanvas();
    paintGraph();
    return el("div", { class: "stage" }, [bar, canvasEl]);
  }
  function updateCap() {
    var cap = canvasEl && canvasEl.querySelector(".zoombox .cap");
    if (cap) cap.textContent = state.focus != null
      ? "depth " + state.hops + " · neuron view"
      : ["package", "module", "file", "function"][state.grain] + " grain · " +
        DATA.stats.symbols + " nodes";
  }
  function legend() {
    return el("div", { class: "legend" }, [
      el("div", { class: "lk", text: "EDGE" }),
      lrow("var(--edge-hot)", "same-file call (tier 2)"),
      lrow("var(--edge-neutral)", "cross-file, name-based (tier 1)"),
      lrow("var(--edge-dyn)", "import / external"),
      el("div", { class: "row", style: "margin-top:4px;color:#75798c;font-size:10px",
        text: "module-level calls are not shown" }),
    ]);
  }
  function lrow(c, t) {
    return el("div", { class: "row" }, [el("span", { class: "sw", style: "background:" + c }), t]);
  }
  function zoombox() {
    var box = el("div", { class: "zoombox" }, [
      el("div", { class: "btns" }, [
        zbtn("ph ph-plus", function () { zoomBy(1.2); }),
        zbtn("ph ph-minus", function () { zoomBy(1 / 1.2); }),
        zbtn("ph ph-crosshair", function () { state.view = { x: 0, y: 0, k: 1 }; paintGraph(); }),
      ]),
      el("div", { class: "cap" }),
    ]);
    setTimeout(updateCap, 0);
    return box;
  }
  function zbtn(icon, fn) { return el("button", { on: { click: fn } }, [el("i", { class: icon })]); }
  function zoomBy(f) { state.view.k = Math.max(0.3, Math.min(4, state.view.k * f)); paintGraph(); }
  function wireCanvas() {
    var drag = null;
    canvasEl.addEventListener("mousedown", function (e) {
      if (e.target.closest(".gnode")) return;
      drag = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y };
    });
    window.addEventListener("mousemove", function (e) {
      if (!drag) return;
      state.view.x = drag.vx + (e.clientX - drag.x);
      state.view.y = drag.vy + (e.clientY - drag.y);
      paintGraph();
    });
    window.addEventListener("mouseup", function () { drag = null; });
    canvasEl.addEventListener("wheel", function (e) {
      e.preventDefault();
      zoomBy(e.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });
  }

  // ---- inspector --------------------------------------------
  function inspector() {
    var body = el("div", { class: "insp-body" });
    var head = el("div", { class: "insp-head" }, [
      el("i", { class: "ph-fill ph-circle k" }),
      el("span", { text: state.focus != null ? N[state.focus].file : "no selection" }),
    ]);
    var foot = el("div", { class: "insp-foot" });

    if (state.focus == null) {
      body.appendChild(el("div", { class: "idle",
        text: "Pick a symbol in the tree or the graph to inspect its callers, blast radius and source." }));
      var idleDeps = DATA.dependencies || [];
      if (idleDeps.length) body.appendChild(depPanel(idleDeps));
      return el("div", { class: "insp" }, [head, body, foot]);
    }
    var n = N[state.focus];
    var reachIn = reachSet(state.focus, inAdj);
    var filesHit = new Set();
    reachIn.forEach(function (i) { filesHit.add(N[i].file); });
    var totalFiles = DATA.stats.files || 1;

    if (n.explain) {
      var exTerms = n.explain.terms || {};
      var ex = el("div", { class: "insp-explain" }, [
        el("div", { class: "lbl", text: "WHAT THIS DOES" }),
        el("p", {}, termNodes(n.explain.what, exTerms)),
      ]);
      if (n.explain.why)
        ex.appendChild(el("p", { class: "why" }, termNodes(n.explain.why, exTerms)));
      body.appendChild(ex);
    }

    body.appendChild(el("div", { class: "statgrid" }, [
      card("FAN-IN", n.fan_in), card("FAN-OUT", n.fan_out),
      card("CHURN", n.churn), card("TIER", tierOf(n.file)),
    ]));

    body.appendChild(el("div", { class: "blast" }, [
      el("div", { class: "t" }, [el("i", { class: "ph ph-warning" }), "Change impact"]),
      el("p", {}, [
        "Editing this reaches ", el("b", { text: String(reachIn.size) }),
        " caller" + (reachIn.size === 1 ? "" : "s") + " across ",
        el("b", { text: String(filesHit.size) }), " file" + (filesHit.size === 1 ? "" : "s") + ".",
      ]),
      el("div", { class: "bar" }, [el("span", {
        style: "width:" + Math.min(100, Math.round((filesHit.size / totalFiles) * 100)) + "%" })]),
    ]));

    if (n.entry.length) {
      var epsec = el("div", { class: "section" }, [el("div", { class: "lbl", text: "ENTRY POINT" })]);
      n.entry.forEach(function (e) {
        epsec.appendChild(el("div", { class: "rowitem" }, [
          el("span", { class: "rk", text: e.split(":")[0] }),
          el("span", { class: "rn", text: e.split(":").slice(1).join(":") }),
        ]));
      });
      body.appendChild(epsec);
    }

    var path = entryPathTo(state.focus);
    if (path)
      body.appendChild(el("div", { class: "section" }, [
        el("div", { class: "lbl", text: "ON PATH FROM" }),
        el("div", { class: "rowitem", style: "flex-wrap:wrap;white-space:normal", text: path }),
      ]));

    body.appendChild(listSection("DIRECT CALLERS", inAdj[state.focus]));
    body.appendChild(listSection("CALLS OUT TO", outAdj[state.focus]));

    var fi = fileByPath[n.file].fi;
    var importers = (DATA.file_edges || []).filter(function (fe) { return fe.t === fi; })
      .map(function (fe) { return FILES[fe.s].path; });
    if (importers.length) {
      var isec = el("div", { class: "section" }, [el("div", { class: "lbl", text: "FILE IMPORTED BY" })]);
      importers.forEach(function (p) {
        isec.appendChild(el("div", { class: "rowitem" }, [
          el("i", { class: "ph ph-arrow-u-up-left" }), el("span", { class: "rn", text: p }),
        ]));
      });
      body.appendChild(isec);
    }

    var fdeps = fileByPath[n.file].deps || [];
    if (fdeps.length) {
      var dsec = el("div", { class: "section" }, [el("div", { class: "lbl", text: "FILE IMPORTS" })]);
      fdeps.forEach(function (d) { dsec.appendChild(depRow(d)); });
      body.appendChild(dsec);
    }

    if (n.doc)
      body.appendChild(el("div", { class: "section" }, [
        el("div", { class: "lbl", text: "DOCSTRING" }),
        el("p", { style: "margin:0;font-size:12px;color:#b2b6ca", text: n.doc }),
      ]));

    if (n.excerpt)
      body.appendChild(el("div", { class: "section" }, [
        el("div", { class: "lbl", text: n.file + ":" + n.line[0] + "-" + n.line[1] }),
        el("pre", { class: "excerpt", text: n.excerpt }),
      ]));

    foot.appendChild(el("a", { class: "btn primary", href: editorUri(n), text: "Open in editor" }));
    foot.appendChild(el("button", { class: "btn", text: "Copy key",
      on: { click: function () { navigator.clipboard && navigator.clipboard.writeText(n.key); } } }));
    return el("div", { class: "insp" }, [head, body, foot]);
  }
  function card(l, v) {
    return el("div", { class: "statcard" }, [
      el("div", { class: "lbl", text: l }), el("div", { class: "val", text: v == null ? "0" : String(v) }),
    ]);
  }
  // ---- dependencies (inspector panel + per-file "imports") --------------
  function depKindLabel(k) {
    return k === "third_party" ? "third-party" : k === "stdlib" ? "built-in" : "internal";
  }
  function depRow(d) {
    return el("div", { class: "rowitem flat" }, [
      el("span", { class: "rk", text: depKindLabel(d.kind) }),
      el("span", { class: "rn", text: d.name }),
      d.count ? el("span", { class: "rc", text: d.count + "×" }) : null,
    ]);
  }
  function depPanel(deps) {
    var groups = {};
    deps.forEach(function (d) { (groups[d.kind] || (groups[d.kind] = [])).push(d); });
    var sec = el("div", { class: "section" }, [el("div", { class: "lbl", text: "DEPENDENCIES" })]);
    [["third_party", "THIRD-PARTY"], ["stdlib", "BUILT-IN"]].forEach(function (g) {
      var list = groups[g[0]] || [];
      if (!list.length) return;
      sec.appendChild(el("div", { class: "depgroup", text: g[1] + " · " + list.length }));
      list.forEach(function (d) { sec.appendChild(depRow(d)); });
    });
    return sec;
  }
  function tierOf(path) { var f = fileByPath[path]; return f ? "T" + f.tier : "—"; }
  function listSection(label, idxs) {
    var sec = el("div", { class: "section" }, [el("div", { class: "lbl", text: label })]);
    var uniq = Array.from(new Set(idxs || []));
    if (!uniq.length) {
      sec.appendChild(el("div", { class: "rowitem muted", text: "none at this tier" }));
      return sec;
    }
    uniq.slice(0, 14).forEach(function (i) {
      var n = N[i];
      sec.appendChild(el("div", { class: "rowitem",
        on: { click: function () { go("graph", n.key); } } }, [
        el("span", { class: "rk", text: n.kind.slice(0, 4) }),
        el("span", { class: "rn", text: n.qual }),
        el("span", { class: "rc", text: n.file.split("/").pop() }),
      ]));
    });
    if (uniq.length > 14)
      sec.appendChild(el("div", { class: "rowitem muted", text: "+" + (uniq.length - 14) + " more" }));
    return sec;
  }
  function entryPathTo(target) {
    var entries = N.filter(function (n) { return n.entry.length; }).map(function (n) { return n.i; });
    if (!entries.length) return null;
    var best = null;
    entries.forEach(function (st) {
      var prev = new Map([[st, -1]]);
      var q = [st];
      while (q.length) {
        var u = q.shift();
        if (u === target) break;
        (outAdj[u] || []).forEach(function (v) { if (!prev.has(v)) { prev.set(v, u); q.push(v); } });
      }
      if (!prev.has(target)) return;
      var chain = [], c = target;
      while (c !== -1) { chain.unshift(c); c = prev.get(c); }
      if (!best || chain.length < best.length) best = chain;
    });
    if (!best) return null;
    var label = (N[best[0]].entry[0] || "").split(":").slice(1).join(":") || N[best[0]].name;
    return label + " -> " + best.map(function (i) { return N[i].name; }).join(" -> ");
  }
  function editorUri(n) {
    var root = (DATA.root || "").replace(/\\/g, "/");
    if (root && !/^\//.test(root)) root = "/" + root;
    return "vscode://file" + root + "/" + n.file + ":" + n.line[0];
  }

  // ---- palette --------------------------------------------
  var paletteOpen = false;
  function openPalette() {
    if (paletteOpen) return;
    paletteOpen = true;
    var sel = 0, matches = N.slice(0, 60);
    var input = el("input", { placeholder: "symbol name or file…", spellcheck: "false" });
    var list = el("ul");
    var back = el("div", { class: "palette-back",
      on: { click: function (e) { if (e.target === back) closeP(); } } },
      [el("div", { class: "palette" }, [input, list])]);
    function closeP() { paletteOpen = false; document.body.removeChild(back); }
    function refresh() {
      var q = input.value.toLowerCase().trim();
      matches = (q ? N.filter(function (n) {
        return (n.qual + " " + n.file).toLowerCase().indexOf(q) >= 0;
      }) : N).slice(0, 60);
      sel = 0;
      clear(list);
      matches.forEach(function (n, i) {
        list.appendChild(el("li", { class: i === sel ? "on" : "",
          on: { click: function () { pick(n); } } }, [
          el("span", { class: "rk", text: n.kind.slice(0, 4) }),
          el("span", { text: n.qual }),
          el("span", { class: "pth", text: n.file }),
        ]));
      });
    }
    function pick(n) { closeP(); go("graph", n.key); }
    function mark() {
      Array.prototype.forEach.call(list.children, function (li, i) {
        li.className = i === sel ? "on" : "";
        if (i === sel) li.scrollIntoView({ block: "nearest" });
      });
    }
    input.addEventListener("input", refresh);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeP();
      else if (e.key === "ArrowDown") { sel = Math.min(sel + 1, matches.length - 1); mark(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); mark(); }
      else if (e.key === "Enter" && matches[sel]) pick(matches[sel]);
    });
    document.body.appendChild(back);
    refresh();
    input.focus();
  }
  window.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  });

  // ---- timeline tab -------------------------------------
  function timelineTab() {
    var wrap = el("div", { class: "tl-wrap" });
    (DATA.timeline || []).forEach(function (c) {
      var touched = state.touched && c.touched.some(function (i) { return state.touched.has(i); });
      var bars = el("div", { class: "tl-bars" });
      [["s", c.counts.structural, "structural"], ["b", c.counts.behavioral, "behavioral"],
       ["c", c.counts.cosmetic, "cosmetic"]].forEach(function (p) {
        if (!p[1]) return;
        bars.appendChild(el("span", { class: "tl-bar " + p[0], style: "width:" + (8 + p[1] * 3) + "px" }));
        bars.appendChild(el("span", { text: p[1] + " " + p[2] }));
      });
      var kids = [
        el("div", { class: "tl-top" }, [
          el("span", { class: "tl-sha", text: c.short }),
          el("span", { class: "tl-date", text: c.date }),
          el("span", { class: "badge src" + (c.intent.source === "inferred" ? " inferred" : ""),
            text: c.intent.source.replace("_", " ") }),
        ]),
        el("div", { class: "tl-subj", text: c.subject || "(no message)" }),
        c.intent.text && c.intent.source !== "inferred"
          ? el("div", { class: "muted", style: "font-size:12px", text: c.intent.text }) : null,
        bars,
        c.headline ? el("div", { class: "tl-line" }, [el("b", { text: "Headline: " })].concat(codeify(c.headline))) : null,
        c.impact ? el("div", { class: "tl-line" }, [el("b", { text: "Impact: " }), c.impact]) : null,
        c.on_path ? el("div", { class: "tl-line" }, [el("b", { text: "On path from: " }), c.on_path]) : null,
        c.new_deps.length ? el("div", { class: "tl-line" },
          [el("b", { text: "New dependency: " })].concat(codeify(c.new_deps.map(function (d) { return "`" + d + "`"; }).join(", ")))) : null,
        c.read_first ? el("div", { class: "tl-link", text: "Read this first: " + c.read_first,
          on: { click: function () {
            state.touched = new Set(c.touched);
            if (c.headline_node != null) go("graph", N[c.headline_node].key);
            else go("graph");
          } } }) : null,
      ];
      wrap.appendChild(el("div", { class: "tl-item" + (touched ? " touch" : "") }, kids));
    });
    return el("div", { class: "tl" }, [wrap]);
  }

  // ---- learn tab -------------------------------------
  function orientationCourse() {
    var mods = (DATA.modules || []).map(function (m) {
      return { title: m.name, count: m.symbol_count, files: m.files.length };
    });
    var hot = N.slice().sort(function (a, b) {
      return (b.fan_in + b.churn * 2) - (a.fan_in + a.churn * 2);
    }).slice(0, 8);
    var sections = [
      { heading: "Where execution enters", kind: "entries", items: DATA.entry_points || [] },
      { heading: "The modules", kind: "modules", items: mods },
      { heading: "Read these first", kind: "hot", items: hot },
    ];
    if ((DATA.dependencies || []).length)
      sections.push({ heading: "The outside world", kind: "deps", items: DATA.dependencies });
    return { title: "Orientation", sections: sections };
  }
  function learnTab() {
    var course = DATA.learn;
    var navItems = [], content;
    if (course && course.modules && course.modules.length) {
      course.modules.forEach(function (m, i) { navItems.push({ id: m.id, n: i + 1, title: m.title }); });
      if (!course.modules.some(function (m) { return m.id === state.module; }))
        state.module = course.modules[0].id;
      var mod = course.modules.filter(function (m) { return m.id === state.module; })[0];
      content = renderModule(mod, course.modules.indexOf(mod) + 1);
    } else {
      navItems.push({ id: "orientation", n: "·", title: "Orientation" });
      content = renderOrientation(orientationCourse());
    }
    var nav = el("div", { class: "learn-nav" },
      [el("div", { class: "lk", text: "MODULES" })].concat(navItems.map(function (it) {
        return el("div", { class: "mlink" + (state.module === it.id ? " active" : ""),
          on: { click: function () { go("learn", it.id); } } },
          [el("span", { class: "n", text: it.n }), it.title]);
      })));
    return el("div", { class: "learn", style: "display:flex" }, [nav,
      el("div", { class: "learn-body" }, [el("div", { class: "learn-inner" }, [content])])]);
  }
  function renderOrientation(o) {
    var wrap = el("div", {}, [
      el("div", { class: "module-num", text: "00" }),
      el("div", { class: "module-title", text: "Orientation" }),
      el("div", { class: "module-sub",
        text: "Generated from the graph. Run the codebase-to-course skill for a full walkthrough with quizzes." }),
    ]);
    o.sections.forEach(function (sec) {
      var s = el("div", { class: "screen" }, [el("h3", { text: sec.heading })]);
      if (sec.kind === "entries") {
        if (!sec.items.length) s.appendChild(el("p", { text: "No entry points detected." }));
        var steps = el("div", { class: "steps" });
        sec.items.forEach(function (e, i) {
          steps.appendChild(el("div", { class: "step" }, [
            el("div", { class: "sn", text: i + 1 }),
            el("div", {}, [
              el("p", {}, [el("b", { text: "[" + e.kind + "] " }), e.detail]),
              e.node != null ? el("span", { class: "sf", text: N[e.node].qual,
                on: { click: function () { go("graph", N[e.node].key); } } }) : null,
            ]),
          ]));
        });
        s.appendChild(steps);
      } else if (sec.kind === "modules") {
        var pc = el("div", { class: "pcards" });
        sec.items.forEach(function (m) {
          pc.appendChild(el("div", { class: "pcard" }, [
            el("div", { class: "pt", text: m.title }),
            el("p", { text: m.files + " file" + (m.files === 1 ? "" : "s") + " · " + m.count + " symbols" }),
          ]));
        });
        s.appendChild(pc);
      } else if (sec.kind === "deps") {
        var dg = el("div", { class: "pcards" });
        sec.items.forEach(function (d) {
          dg.appendChild(el("div", { class: "pcard" }, [
            el("div", { class: "pt", text: d.name }),
            el("p", { text: depKindLabel(d.kind) + " · imported by " + d.count +
              " file" + (d.count === 1 ? "" : "s") }),
          ]));
        });
        s.appendChild(dg);
      } else {
        var steps2 = el("div", { class: "steps" });
        sec.items.forEach(function (n, i) {
          steps2.appendChild(el("div", { class: "step" }, [
            el("div", { class: "sn", text: i + 1 }),
            el("div", {}, [
              el("p", {}, [el("span", { class: "sf", text: n.qual }),
                " — " + n.fan_in + " callers, " + n.churn + " non-cosmetic changes"]),
              el("span", { class: "sf", text: n.file,
                on: { click: function () { go("graph", n.key); } } }),
            ]),
          ]));
        });
        s.appendChild(steps2);
      }
      wrap.appendChild(s);
    });
    return wrap;
  }
  function renderModule(mod, num) {
    var glossary = mod.glossary || {};
    var wrap = el("div", {}, [
      el("div", { class: "module-num", text: String(num).padStart(2, "0") }),
      el("div", { class: "module-title", text: mod.title }),
      mod.summary ? el("div", { class: "module-sub", text: mod.summary }) : null,
      mod.metaphor ? el("div", { class: "metaphor", text: mod.metaphor }) : null,
    ]);
    (mod.screens || []).forEach(function (sc) {
      var s = el("div", { class: "screen" });
      if (sc.heading) s.appendChild(el("h3", { text: sc.heading }));
      if (sc.body) s.appendChild(el("p", {}, termNodes(sc.body, glossary)));
      if (sc.translation)
        s.appendChild(el("div", { class: "xlate" }, [
          el("div", { class: "code" }, [
            el("div", { class: "lbl", text: "CODE" }),
            el("pre", { text: sc.translation.code }),
          ]),
          el("div", { class: "en" }, [el("div", { class: "lbl", text: "PLAIN ENGLISH" })].concat(
            (sc.translation.lines || []).map(function (l) { return el("p", { text: l }); }))),
        ]));
      if (sc.callout)
        s.appendChild(el("div", { class: "callout accent" }, [
          el("i", { class: "ph ph-lightbulb" }),
          el("div", {}, [
            sc.callout.title ? el("div", { class: "ct", text: sc.callout.title }) : null,
            el("p", { text: sc.callout.text }),
          ]),
        ]));
      if (sc.nodes && sc.nodes.length) {
        var refs = el("div", { class: "steps" });
        sc.nodes.forEach(function (ni) {
          if (N[ni]) refs.appendChild(el("div", { class: "step" }, [
            el("div", { class: "sn", text: "→" }),
            el("div", {}, [el("span", { class: "sf", text: N[ni].qual + "  (" + N[ni].file + ")",
              on: { click: function () { go("graph", N[ni].key); } } })]),
          ]));
        });
        s.appendChild(refs);
      }
      wrap.appendChild(s);
    });
    if (mod.quiz && mod.quiz.length) wrap.appendChild(renderQuiz(mod.quiz));
    return wrap;
  }
  // split body text on glossary terms, returning an array of text nodes / .term spans
  function termNodes(text, glossary) {
    var terms = Object.keys(glossary).filter(Boolean);
    if (!terms.length) return [document.createTextNode(text)];
    var re = new RegExp("\\b(" + terms.map(function (t) {
      return t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("|") + ")\\b");
    var out = [], rest = String(text), m;
    while ((m = rest.match(re))) {
      if (m.index > 0) out.push(document.createTextNode(rest.slice(0, m.index)));
      out.push(el("span", { class: "term", "data-def": glossary[m[1]] || "", text: m[1] }));
      rest = rest.slice(m.index + m[1].length);
    }
    if (rest) out.push(document.createTextNode(rest));
    return out;
  }
  function renderQuiz(qs) {
    var box = el("div", { class: "quiz" }, [el("div", { class: "qh", text: "CHECK YOURSELF" })]);
    qs.forEach(function (q) {
      var picked = null;
      var fb = el("div", { class: "qfb" });
      var block = el("div", { class: "qblock" }, [el("div", { class: "qtext", text: q.q })]);
      (q.options || []).forEach(function (opt, oi) {
        block.appendChild(el("button", { class: "qopt", on: { click: function () {
          if (picked != null) return;
          picked = oi;
          Array.prototype.forEach.call(block.querySelectorAll(".qopt"), function (b, bi) {
            if (bi === q.answer) b.classList.add("correct");
            if (bi === oi) b.classList.add("sel");
            if (bi === oi && oi !== q.answer) b.classList.add("wrong");
          });
          var ok = oi === q.answer;
          clear(fb);
          fb.className = "qfb show " + (ok ? "ok" : "no");
          fb.appendChild(el("b", { text: ok ? "Exactly. " : "Not quite. " }));
          fb.appendChild(document.createTextNode(ok ? (q.right || "") : (q.wrong || "")));
        } } }, [el("span", { class: "dot" }), opt]));
      });
      block.appendChild(fb);
      box.appendChild(block);
    });
    return box;
  }

  // ---- glossary tooltip (fixed to body so overflow:hidden can't clip it) --
  var activeTip = null;
  function positionTip(term, tip) {
    var r = term.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(window.innerWidth - tip.offsetWidth - 8, r.left)) + "px";
    var above = r.top - tip.offsetHeight - 10;
    tip.style.top = (above < 8 ? r.bottom + 10 : above) + "px";
  }
  document.addEventListener("mouseover", function (e) {
    var t = e.target.closest && e.target.closest(".term");
    if (!t) return;
    if (activeTip) activeTip.remove();
    var tip = el("div", { class: "term-tooltip", text: t.getAttribute("data-def") });
    document.body.appendChild(tip);
    positionTip(t, tip);
    requestAnimationFrame(function () { tip.classList.add("visible"); });
    activeTip = tip;
  });
  document.addEventListener("mouseout", function (e) {
    var t = e.target.closest && e.target.closest(".term");
    if (t && activeTip) { activeTip.remove(); activeTip = null; }
  });

  // ---- render ------------------------------------------
  function render() {
    clear(APP);
    var frag = document.createDocumentFragment();
    frag.appendChild(topbar());
    var banner = staleBanner();
    if (banner) frag.appendChild(banner);
    if (state.tab === "graph")
      frag.appendChild(el("div", { class: "view" }, [rail(), stage(), inspector()]));
    else if (state.tab === "timeline")
      frag.appendChild(el("div", { class: "view" }, [timelineTab()]));
    else
      frag.appendChild(el("div", { class: "view" }, [learnTab()]));
    APP.appendChild(frag);
    updateCap();
  }

  route();
})();
