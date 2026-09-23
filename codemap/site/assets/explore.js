/* codemap Explorer — frozen runtime. No libraries, no build step.
   Everything variable comes from the inlined JSON in #codemap-data. Do NOT
   regenerate this file from Python. All DOM is built with textContent / real
   nodes — no string HTML is ever assigned to the document. */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("codemap-data").textContent);
  var APP = document.getElementById("app");
  var SVGNS = "http://www.w3.org/2000/svg";
  var SVG_TAGS = { svg: 1, g: 1, path: 1, circle: 1, text: 1, tspan: 1, ellipse: 1, line: 1, rect: 1,
                   animate: 1, animateMotion: 1, defs: 1, pattern: 1, clipPath: 1, title: 1 };

  // ---- tiny DOM helper -------------------------------------------------
  // tags that are already both focusable and operable from the keyboard by
  // default — everything else that gets an `on.click` below needs help.
  var NATIVE_INTERACTIVE = { button: 1, a: 1, input: 1, select: 1, textarea: 1 };
  function el(tag, attrs, kids) {
    var n = SVG_TAGS[tag]
      ? document.createElementNS(SVGNS, tag)
      : document.createElement(tag);
    attrs = attrs || {};
    // The whole app is built from plain divs/spans/g's wired up with
    // `on: { click: fn }` — none of that is reachable or operable from a
    // keyboard by default. Rather than hand-add role/tabindex/keydown at
    // every one of the ~30 call sites, give every click-handling element
    // that isn't already natively interactive the same treatment here, once:
    // a button role, a tab stop, and Enter/Space wired to the same handler.
    // A caller that needs different semantics just passes its own role or
    // tabindex and this step no-ops for it.
    if (attrs.on && attrs.on.click && !NATIVE_INTERACTIVE[tag] &&
        attrs.role == null && attrs.tabindex == null) {
      var click = attrs.on.click;
      var extraOn = {};
      for (var ek in attrs.on) extraOn[ek] = attrs.on[ek];
      extraOn.keydown = function (ev) {
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
          ev.preventDefault();
          click(ev);
        }
      };
      var extra = {};
      for (var ak in attrs) extra[ak] = attrs[ak];
      extra.role = "button";
      extra.tabindex = "0";
      extra.on = extraOn;
      attrs = extra;
    }
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

  // navigator.clipboard needs a secure context — file:// (the normal way this
  // page is opened) is NOT one in Chrome, so `navigator.clipboard` is simply
  // undefined there. execCommand("copy") on an offscreen textarea still works
  // on file://. Either way, flash `btn`'s own label so a click always has a
  // visible outcome instead of silently doing nothing.
  function copyText(text, btn) {
    var label = btn.textContent;
    function flash(ok) {
      btn.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(function () { btn.textContent = label; }, 1200);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { flash(true); }, function () { flash(false); });
      return;
    }
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    flash(ok);
  }

  // A viewer preference that should survive a reload — but this page can be
  // opened from file://, a private window, or with site data blocked, where
  // localStorage throws on first touch rather than just being empty.
  function lsGet(key, fallback) {
    try {
      var v = localStorage.getItem("codemap." + key);
      return v == null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
  }
  function lsSet(key, value) {
    try { localStorage.setItem("codemap." + key, JSON.stringify(value)); } catch (e) { /* unavailable */ }
  }

  // small uppercase kind badge (tree rows, caller/callee lists, entry points).
  // A blind slice(0, 4) reads fine for "function"/"method" but turns "class"
  // into "clas" — spell out the short ones instead of truncating them.
  var KIND_LABEL = { function: "func", method: "meth", class: "class" };
  function kindLabel(k) { return KIND_LABEL[k] || String(k || "").slice(0, 4); }

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
  // confidence-filtered twin of each: an AMBIGUOUS edge (no import evidence,
  // matched by name alone) is a real edge for the canvas and Simulate's
  // "might be it" narration, but never for a *count* presented as fact —
  // blast radius, the entry path and "Trace back to entry" all walk these.
  var outAdjSure = N.map(function () { return []; });
  var inAdjSure = N.map(function () { return []; });
  // links only the optional codebase-memory engine found (`via: "cbm"`), by "s:t"
  var viaEdge = {};
  E.forEach(function (e) {
    outAdj[e.s].push(e.t); inAdj[e.t].push(e.s);
    if (e.confidence !== "AMBIGUOUS") { outAdjSure[e.s].push(e.t); inAdjSure[e.t].push(e.s); }
    if (e.via) viaEdge[e.s + ":" + e.t] = e;
  });
  // per-caller calls, in call-site order — the Graph/Map views only need "is
  // there an edge", but Simulate (Lane 1) needs "in what order does this
  // frame make its calls", which only the line number on each edge carries.
  var outCalls = N.map(function () { return []; });
  E.forEach(function (e) { outCalls[e.s].push({ t: e.t, line: e.line || 0, conf: e.confidence }); });
  outCalls.forEach(function (list) { list.sort(function (a, b) { return a.line - b.line; }); });
  var keyToI = {};
  N.forEach(function (n) { keyToI[n.key] = n.i; });
  var fileByPath = {};
  FILES.forEach(function (f) { fileByPath[f.path] = f; });

  // ---- embedded sources ---------------------------------------------------
  // DATA.sources = {file index: whole file text} for the files that fit the
  // [explore] max_source_bytes budget (model.py). The payload leaves out the
  // per-symbol `excerpt` of those files, so it's rebuilt here — capped exactly
  // as the Python side caps it, so Simulate and the rest see the same snippets.
  var SRC = DATA.sources || {};
  var SNIP_LINES = DATA.snippet_lines || 40;
  var srcLinesCache = {};
  function fileLines(fi) {
    if (!(fi in srcLinesCache)) {
      var t = SRC[fi];
      if (t == null) srcLinesCache[fi] = null;
      else {
        var a = t.split("\n");
        if (a.length && a[a.length - 1] === "") a.pop();
        srcLinesCache[fi] = a;
      }
    }
    return srcLinesCache[fi];
  }
  // a symbol's complete source (any length) — or its capped excerpt when its
  // file wasn't embedded, or null when neither is known
  function srcOf(n) {
    var f = fileByPath[n.file], lines = f ? fileLines(f.fi) : null;
    if (lines) return lines.slice(Math.max(n.line[0] - 1, 0), n.line[1]).join("\n");
    return n.excerpt || null;
  }
  N.forEach(function (n) {
    if (n.excerpt == null && n.line[1] - n.line[0] + 1 <= SNIP_LINES) {
      var f = fileByPath[n.file];
      if (f && fileLines(f.fi)) n.excerpt = srcOf(n);
    }
  });
  // how many files the search box's "isn't embedded" caveat can honestly
  // point to — on a repo where everything fits the budget, naming
  // max_source_bytes as the reason for a miss would just be wrong.
  var unembeddedFileCount = FILES.reduce(function (n, f) { return n + (fileLines(f.fi) ? 0 : 1); }, 0);

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
  // Everything reachable from `start` along `adj`, capped at `maxHops` —
  // built on the same hop-limited bfs() above rather than an unbounded DFS
  // stack walk, so "Editing this reaches N callers" means "within the depth
  // impact analysis actually explores" (DATA.impact_depth, the same bound
  // `codemap`'s own impact.analyze() uses), not "everything transitively
  // reachable, however many hops away" — on a real repo that unbounded walk
  // is how a single edge into a busy symbol turned into "419 callers".
  function reachSet(start, adj, maxHops) {
    return new Set(bfs(start, adj, maxHops == null ? (DATA.impact_depth || 3) : maxHops).keys());
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
  // a packet: glow + core, moved along the edge by SMIL (paced = constant speed).
  // animateMotion writes only a transform on this tiny <g> — no re-rasterisation.
  function comet(pathD, col, dur, begin, small) {
    var gc = el("g", { class: "pkt" });
    gc.appendChild(el("circle", { r: small ? 4 : 5.5, fill: col, opacity: 0.16 }));
    gc.appendChild(el("circle", { r: small ? 1.6 : 2.2, fill: "#f5f4ff", opacity: 0.95 }));
    gc.appendChild(el("animateMotion", { dur: dur + "s", begin: begin + "s",
      repeatCount: "indefinite", path: pathD }));
    return gc;
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

  // language string (codemap/languages/registry.py's LanguageSpec.name) -> a
  // real Phosphor "file-*" icon; anything the indexer supports but Phosphor
  // has no dedicated glyph for (go, java, php, ruby, …) falls back to the
  // generic file-code glyph rather than a wrong specific one.
  var LANG_ICON = {
    python: "ph-file-py", javascript: "ph-file-js", typescript: "ph-file-ts",
    tsx: "ph-file-ts", rust: "ph-file-rs", c: "ph-file-c", cpp: "ph-file-cpp",
    csharp: "ph-file-c-sharp",
  };
  function fileIcon(lang) { return "ph " + (LANG_ICON[lang] || "ph-file-code"); }

  // ---- folder colour system ---------------------------------------------
  // Eight hues in a fixed, CVD-safe order (the data-viz "dark" categorical
  // ramp, validated against this canvas surface). Each folder is assigned one
  // by size; the 9th folder onward folds into a neutral "other". Colour only
  // reinforces the spatial lobe grouping — the lobe outline, its label and the
  // legend all carry the same information, so identity is never colour-alone.
  var GROUP_HUES = ["#3987e5", "#d95926", "#199e70", "#c98500",
                    "#d55181", "#008300", "#9085e9", "#e66767"];
  var GROUP_OTHER = "#8a8fa3";
  var IMPORT_EDGE = "#8a8fa3";
  function dirGroup(path) {
    var i = String(path == null ? "" : path).lastIndexOf("/");
    return i < 0 ? "(root)" : path.slice(0, i);
  }
  function hexA(hex, a) {
    var h = hex.replace("#", "");
    return "rgba(" + parseInt(h.slice(0, 2), 16) + "," + parseInt(h.slice(2, 4), 16) +
      "," + parseInt(h.slice(4, 6), 16) + "," + a + ")";
  }
  var groupColor = {};   // every folder present -> hex (distinct hue or GROUP_OTHER)
  (function () {
    var weight = {};
    FILES.forEach(function (f) {
      var k = dirGroup(f.path);
      weight[k] = (weight[k] || 0) + (f.symbols.length || 1);
    });
    Object.keys(weight).sort(function (a, b) {
      return weight[b] - weight[a] || (a < b ? -1 : 1);
    }).forEach(function (k, i) {
      groupColor[k] = i < GROUP_HUES.length ? GROUP_HUES[i] : GROUP_OTHER;
    });
  })();
  function colorForPath(p) { return groupColor[dirGroup(p)] || GROUP_OTHER; }
  function colorForNode(n) { return n && n.file ? colorForPath(n.file) : GROUP_OTHER; }
  function moduleColor(name) { return groupColor[name] || GROUP_OTHER; }

  // ---- graph grouping: Folder | Layer ---------------------------------------
  // The Graph legend can colour and filter by architecture layer instead of by
  // folder. Layers come from DATA.architecture (the Architecture tab's
  // inferred placement), so they are a best guess — the legend says so. Only
  // colours and the filter key change; the layout is the same (it is seeded by
  // node key, not by colour), so switching is a repaint, never a relayout.
  var LAYER_IDS = ["entry", "views", "api", "logic", "data", "shared", "tests", "unplaced"];
  var LAYER_LABEL = { entry: "Routes & entry", views: "Views & UI", api: "API", logic: "Logic",
                      data: "Data", shared: "Shared code", tests: "Tests", unplaced: "Unplaced" };
  var layerByFi = null;
  function layerOfFile(f) {
    if (!layerByFi) {
      layerByFi = {};
      ((DATA.architecture || {}).components || []).forEach(function (c) {
        (c.files || []).concat(c.extra_files || []).forEach(function (fi) { layerByFi[fi] = c.layer; });
      });
    }
    return (f && layerByFi[f.fi]) || "unplaced";
  }
  function hasLayers() { return !!((DATA.architecture || {}).components || []).length; }
  function layerHue(id) { return id === "unplaced" ? GROUP_OTHER : (A_HUE[id] || GROUP_OTHER); }
  function layerCounts() {
    var c = {};
    FILES.forEach(function (f) { var l = layerOfFile(f); c[l] = (c[l] || 0) + 1; });
    return c;
  }
  // the graph's own colour / filter-key lookups: folder mode = the plain folder
  // system above, layer mode = the file's layer. `gKey` is what state.folders
  // holds and what applyHighlight() compares (a folder path, or "layer:<id>").
  function gKey(path) {
    return state.groupBy === "layer" ? "layer:" + layerOfFile(fileByPath[path]) : dirGroup(path);
  }
  function gColorForPath(p) {
    return state.groupBy === "layer" ? layerHue(layerOfFile(fileByPath[p])) : colorForPath(p);
  }
  function gColorForNode(n) { return n && n.file ? gColorForPath(n.file) : GROUP_OTHER; }
  var modLayerCache = {};
  function gModuleColor(name) {
    if (state.groupBy !== "layer") return moduleColor(name);
    if (!(name in modLayerCache)) {
      // a module cloud has no single layer: take the one most of its symbols sit in
      var w = {}, best = null;
      FILES.forEach(function (f) {
        if (f.module !== name) return;
        var l = layerOfFile(f);
        w[l] = (w[l] || 0) + (f.symbols.length || 1);
      });
      Object.keys(w).forEach(function (l) { if (best === null || w[l] > w[best]) best = l; });
      modLayerCache[name] = best || "unplaced";
    }
    return layerHue(modLayerCache[name]);
  }
  // a lobe is a folder outline, so in layer mode it stays neutral and lets the
  // layer colours on the nodes and edges carry the grouping
  function gLobeColor(name) { return state.groupBy === "layer" ? GROUP_OTHER : moduleColor(name); }

  // ---- traffic model --------------------------------------------------
  // No runtime profile exists, so "traffic" is derived by BFS outward from the
  // real entry points. Two passes: one over the call graph (symbol grain) and
  // one over the file-import graph (file / module grain, and as a fallback for
  // symbols the sparse name-based call graph never links). Depth = hops from the
  // nearest entry, -1 = never reached. Reachable edges carry a flowing "current";
  // the busiest also carry comet packets. Unreachable code visibly carries
  // nothing — the animation is signal, not decoration.
  var FLOW_BUDGET = 220;   // max edges given the marching-dash current (a paint prop)
  var COMET_BUDGET = 90;   // max edges given a moving packet (cheap: a transform)
  var PKT_SPEED = 165;     // viewBox units / second a packet travels
  var CASCADE = 0.55;      // seconds of stagger per BFS hop, so a wave radiates out

  function bfsDepths(n, seeds, adj) {
    var depth = new Int32Array(n);
    for (var i = 0; i < n; i++) depth[i] = -1;
    var frontier = [];
    seeds.forEach(function (s) { if (depth[s] < 0) { depth[s] = 0; frontier.push(s); } });
    for (var d = 1; frontier.length; d++) {
      var next = [];
      for (var k = 0; k < frontier.length; k++) {
        var nb = adj[frontier[k]] || [];
        for (var j = 0; j < nb.length; j++)
          if (depth[nb[j]] < 0) { depth[nb[j]] = d; next.push(nb[j]); }
      }
      frontier = next;
    }
    return depth;
  }

  var ENTRY_SEEDS = [];
  N.forEach(function (nd) { if (nd.entry && nd.entry.length) ENTRY_SEEDS.push(nd.i); });
  if (!ENTRY_SEEDS.length)            // no detected entry: call-graph roots
    N.forEach(function (nd) { if (nd.fan_in === 0 && nd.fan_out > 0) ENTRY_SEEDS.push(nd.i); });
  if (!ENTRY_SEEDS.length)            // still nothing (a pure cycle): busiest few
    ENTRY_SEEDS = N.slice().sort(function (a, b) { return b.fan_out - a.fan_out; })
      .slice(0, 8).map(function (nd) { return nd.i; });
  var entrySet = new Set(ENTRY_SEEDS);
  var flowDepth = bfsDepths(N.length, ENTRY_SEEDS, outAdj);

  // file-import graph: edge s -> t means "s imports t", i.e. execution can pass
  // from s into t — same caller->callee direction as the call graph.
  var fileAdj = FILES.map(function () { return []; });
  var fileImported = new Set();
  (DATA.file_edges || []).forEach(function (fe) { fileAdj[fe.s].push(fe.t); fileImported.add(fe.t); });
  // Seeds = files holding an entry-point symbol PLUS import-graph roots (nothing
  // imports them: a script you run, a test module the runner collects). Both are
  // places a traversal legitimately begins, so e.g. tests/ lights up as its own
  // source rather than going dark.
  var fileSeeds = [];
  FILES.forEach(function (f, fi) {
    if (!fileImported.has(fi) ||
        (f.symbols || []).some(function (si) { return N[si].entry && N[si].entry.length; }))
      fileSeeds.push(fi);
  });
  var fileFlow = bfsDepths(FILES.length, fileSeeds, fileAdj);
  var fileFlowByPath = {};
  FILES.forEach(function (f, fi) { fileFlowByPath[f.path] = fileFlow[fi]; });

  // symbol reachability, call graph first, file-import graph as the fallback
  function symDepth(i) {
    if (flowDepth[i] >= 0) return flowDepth[i];
    var fd = fileFlowByPath[N[i].file];
    return fd == null ? -1 : fd;
  }
  function moduleDepth(name) {        // grain <=1: min reached file depth in the module
    var best = -1;
    FILES.forEach(function (f, fi) {
      if (f.module !== name) return;
      var d = fileFlow[fi];
      if (d >= 0 && (best < 0 || d < best)) best = d;
    });
    return best;
  }

  // ---- map-tab derivations ------------------------------------------
  // The Graph tab answers "what calls what". These power a second tab that
  // answers three questions the call graph alone never shows: what SHAPE is this
  // system (layer cake), what happens when it RUNS (run trace), and where does
  // the code sit and keep moving (mass map). All from the payload already inlined
  // — no Python change. `fileAdj` (s imports t) is reused from the traffic model.

  var fileImpBy = FILES.map(function () { return []; });   // reverse: who imports me
  fileAdj.forEach(function (outs, s) {
    outs.forEach(function (t) { fileImpBy[t].push(s); });
  });

  // Strongly-connected components of the file-import graph (iterative Tarjan —
  // recursion could blow the stack on a big repo). An SCC with >1 member is an
  // import cycle, drawn later as one merged block.
  function fileSCCs() {
    var n = FILES.length, idx = 0;
    var num = new Array(n).fill(-1), low = new Array(n).fill(0);
    var onStk = new Array(n).fill(false), stk = [];
    var comp = new Array(n).fill(-1), comps = [];
    for (var s0 = 0; s0 < n; s0++) {
      if (num[s0] >= 0) continue;
      var work = [[s0, 0]];
      while (work.length) {
        var fr = work[work.length - 1], v = fr[0];
        if (fr[1] === 0) { num[v] = low[v] = idx++; stk.push(v); onStk[v] = true; }
        if (fr[1] < fileAdj[v].length) {
          var w = fileAdj[v][fr[1]++];
          if (num[w] < 0) work.push([w, 0]);
          else if (onStk[w]) low[v] = Math.min(low[v], num[w]);
        } else {
          if (low[v] === num[v]) {
            var c = [], x;
            do { x = stk.pop(); onStk[x] = false; comp[x] = comps.length; c.push(x); }
            while (x !== v);
            comps.push(c);
          }
          work.pop();
          if (work.length) {
            var p = work[work.length - 1][0];
            low[p] = Math.min(low[p], low[v]);
          }
        }
      }
    }
    return { comp: comp, comps: comps };
  }
  var _scc = fileSCCs();
  var fileComp = _scc.comp;              // fi -> component id
  var sccMembers = _scc.comps;           // component id -> [fi, ...]
  var compAdj = sccMembers.map(function () { return new Set(); });
  fileAdj.forEach(function (outs, s) {
    outs.forEach(function (t) {
      if (fileComp[s] !== fileComp[t]) compAdj[fileComp[s]].add(fileComp[t]);
    });
  });
  // longest-path layer over the condensed DAG: 0 = imports nothing in-repo
  var compLayer = new Array(sccMembers.length).fill(-1);
  (function () {
    function depthOf(c) {
      if (compLayer[c] >= 0) return compLayer[c];
      compLayer[c] = 0;                  // guard against any stray re-entry
      var d = 0;
      compAdj[c].forEach(function (t) { d = Math.max(d, 1 + depthOf(t)); });
      return (compLayer[c] = d);
    }
    for (var c = 0; c < sccMembers.length; c++) depthOf(c);
  })();
  function fileLayer(fi) { return compLayer[fileComp[fi]]; }

  // per-file churn (Σ non-cosmetic changes) + "every function unreachable" flag
  var churnOf = FILES.map(function () { return 0; });
  var funcOf = FILES.map(function () { return 0; });
  var deadOf = FILES.map(function () { return 0; });
  N.forEach(function (nd) {
    var f = fileByPath[nd.file];
    if (!f) return;
    churnOf[f.fi] += nd.churn || 0;
    if (nd.kind === "function" || nd.kind === "method") {
      funcOf[f.fi]++;
      if (nd.fan_in === 0 && !(nd.entry && nd.entry.length)) deadOf[f.fi]++;
    }
  });
  function fileAllDead(fi) { return funcOf[fi] > 0 && deadOf[fi] === funcOf[fi]; }
  // matches "tests/…" AND "pkg/tests/…" — a top-level-only check misses any
  // nested tests dir. Shared by Map's "Hide tests" pill and anywhere else
  // "unreachable" needs to mean something (a test file's own functions are
  // never called by production code either — that's not the interesting kind
  // of dead code, so it shouldn't read as a warning).
  function isTestPath(path) { return /(^|\/)tests\//.test(path); }
  var churnMax = Math.max.apply(null, churnOf.concat([1]));

  // Candidacy is still call-subtree size, not "is this a DATA.entry_points
  // entry" — main() dies at `args.func(args)` after two hops (dynamic
  // dispatch the indexer can't follow), so the richly-connected roots are
  // the big test drivers and the cmd_* handlers instead. entry_points only
  // breaks ties in the ranking below (real entries over tests), not the cut.
  function subtreeSize(i, cap) {
    var seen = new Set([i]), frontier = [i];
    for (var d = 0; d < cap && frontier.length; d++) {
      var nx = [];
      frontier.forEach(function (u) {
        (outAdj[u] || []).forEach(function (v) {
          if (!seen.has(v)) { seen.add(v); nx.push(v); }
        });
      });
      frontier = nx;
    }
    return seen.size - 1;
  }
  // real entry points (routes, main guards, jobs, …) belong at the top of the
  // Run-trace root picker; a test file's own top-level functions have huge
  // subtrees too (they call into everything they exercise) and used to
  // outrank the actual entry points the picker exists to surface.
  var TRACE_ENTRY_NODES = new Set((DATA.entry_points || [])
    .map(function (ep) { return ep.node; }).filter(function (i) { return i != null; }));
  var traceRoots = N.map(function (nd) { return { i: nd.i, size: subtreeSize(nd.i, 6) }; })
    .filter(function (r) { return r.size >= 4; })
    .sort(function (a, b) {
      var an = N[a.i], bn = N[b.i];
      var aEntry = TRACE_ENTRY_NODES.has(a.i), bEntry = TRACE_ENTRY_NODES.has(b.i);
      if (aEntry !== bEntry) return aEntry ? -1 : 1;
      var aTest = isTestPath(an.file), bTest = isTestPath(bn.file);
      if (aTest !== bTest) return aTest ? 1 : -1;
      return b.size - a.size || an.qual.localeCompare(bn.qual);
    })
    .slice(0, 14);
  var traceExpand = {};   // "<root>:<depth>" -> true once the "+N more" is opened

  // ---- app state ---------------------------------------------------
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var flowPref = lsGet("flow", null);   // explicit viewer choice, else the size-based default below decides
  var state = {
    tab: "learn",   // land on Orientation, not the graph — "read the map before the words" (route() sets this from the hash before first render anyway)
    grain: lsGet("grain", 1),   // 0 Module, 1 File (default), 2 Function — see GRAINS below; remembers the viewer's last pick
    hops: lsGet("hops", 2),     // remembers the viewer's last pick
    focus: null,
    fileScope: null,
    showDead: true,
    highlight: null,
    view: { x: 0, y: 0, k: 1 },
    touched: null,
    module: null,
    pkg: null,      // Packages: current package/module slug
    flow: flowPref != null ? flowPref : !reduceMotion,
    folders: new Set(),   // legend filter — empty = everything shown; folder paths, or "layer:<id>" in layer mode
    groupBy: "folder",    // Graph: colour + filter by "folder" or by inferred architecture "layer"
    srcOpen: false,       // Graph: the source viewer slide-over is open on the focused symbol
    srcTargetLine: null,  // Graph: one-shot line to scroll the source viewer to, e.g. from a file-text search hit — consumed by syncSrc() and cleared right after, else it defaults to the focused symbol's own first line
    pin: null,            // a click-pinned node key: spotlight it + its edges
    mapView: "layers",    // Map tab: "layers" | "trace" | "mass"
    traceRoot: null,      // Map/trace: node index of the traced call root
    traceStep: null,      // Map/trace: BFS depth the step wave has reached (null = all)
    hideTests: false,     // Map/layers: drop tests/** and collapse empty bands
    archSel: null,        // Architecture: selected item key (component id, or store:/service:/actor:<id>)
    archShowTests: false, // Architecture: include the tests side panel
    mobileRail: false,    // narrow viewport: source tree shown as an overlay, not a column
    mobileInsp: false,    // narrow viewport: inspector shown as an overlay, not a column
    folderScope: "",      // legend: folder path currently drilled into ("" = repo root)
    legendCollapsed: lsGet("legendCollapsed", true), // legend: collapsed to just its header bar by default; remembers the viewer's choice
    legendPos: null,      // legend: {left, top} px within the canvas once dragged, else default corner
    simScenario: null,    // Simulate: current scenario id
    simStep: 0,           // Simulate: current step index into that scenario's normalized steps
    simPlaying: false,    // Simulate: transport is auto-advancing
    simSpeed: 1,          // Simulate: playback speed multiplier — one of SIM_SPEEDS below
    simCollapsed: { stage: false, flow: false, log: false, source: false },  // Simulate: per-pane hide toggle
    simLayout: { leftW: null, stageH: null, logH: null },  // Simulate: px overrides once a splitter is dragged, else CSS default
    simSolo: null,        // Simulate: pane key ("stage"/"flow"/"log"/"source") shown full-bleed, else null — dbl-click a pane header
    routeNote: null,      // set by route() when the hash named a tab/arg that didn't resolve — cleared on the next successful route
    timelineSha: null,    // Timeline: commit sha deep-linked via "#/timeline/<sha>", scrolled to and highlighted
  };
  // Traffic (the animated call-flow overlay) is the single biggest cost on a
  // dense graph — animating 150+ SMIL/CSS edges on a big repo's default view
  // is what drops a real build to single-digit fps. Default it off past that
  // point instead of unconditionally on; a small repo still gets it for free.
  // Measured against the actual default view (File grain, whole graph), not
  // a raw edge count that ignores this layout's own de-dup/capping. Only
  // applies when the viewer hasn't already picked one way or the other.
  if (flowPref == null && state.flow && layoutOverview().links.length > 150) state.flow = false;

  // ---- routing ---------------------------------------------------
  var TAB_LABEL = { graph: "the Graph", arch: "Architecture", map: "Map", sim: "Simulate",
    learn: "Learn", libs: "Packages", timeline: "Timeline" };
  function parseHash() {
    var h = (location.hash || "#/learn").replace(/^#/, "");
    var parts = h.split("/").filter(Boolean);
    var raw = parts.slice(1).join("/") || "";
    var arg;
    try { arg = decodeURIComponent(raw); } catch (e) { arg = raw; }   // a hand-edited/copied hash can be malformed — never let it kill the route
    return { tab: parts[0] || "learn", arg: arg };   // "#/" and bare "#" land on Learn, same as no hash at all
  }
  // The hash that actually matches what's on screen right now — used to
  // correct the URL bar (history.replaceState) when the hash we were just
  // asked to show didn't resolve to anything real. Mirrors go()'s own
  // encodeURIComponent/"/src"-suffix rules so the two never disagree.
  function canonicalHash() {
    var t = state.tab;
    if (t === "graph") {
      var arg = state.focus != null ? N[state.focus].key : "";
      var h = "#/graph" + (arg ? "/" + encodeURIComponent(arg) : "");
      if (arg && state.srcOpen) h += "/src";
      return h;
    }
    if (t === "learn") return "#/learn" + (state.module && state.module !== ORIENTATION_ID ? "/" + encodeURIComponent(state.module) : "");
    if (t === "arch") return "#/arch" + (state.archSel ? "/" + encodeURIComponent(state.archSel) : "");
    if (t === "libs") return "#/libs" + (state.pkg ? "/" + encodeURIComponent(state.pkg) : "");
    if (t === "map") {
      var seg = state.mapView;
      if (state.mapView === "trace" && state.traceRoot != null) seg += "/" + encodeURIComponent(N[state.traceRoot].key);
      return "#/map/" + seg;
    }
    if (t === "sim") return "#/sim" + (state.simScenario ? "/" + encodeURIComponent(state.simScenario) : "");
    if (t === "timeline") return "#/timeline" + (state.timelineSha ? "/" + encodeURIComponent(state.timelineSha) : "");
    return "#/" + t;
  }
  function go(tab, arg) {
    var next = "#/" + tab + (arg ? "/" + encodeURIComponent(arg) : "");
    if (tab === "graph" && arg && state.srcOpen) next += "/src";   // keep the viewer open while moving between symbols
    // location.hash = same value fires no hashchange, so re-clicking the
    // already-open target (a symbol, the focused file's row, …) would
    // otherwise silently do nothing — route directly instead.
    if (location.hash === next) route();
    else location.hash = next;
  }
  window.addEventListener("hashchange", route);

  function route() {
    if (state.simPlaying) simPause();   // a real navigation (incl. our own scenario-switch
                                         // `go()`) always means "stop the timer" — in-tab
                                         // step/scrub never calls route(), see simSetStep().
    var prevTab = state.tab;            // read before route() below overwrites it — see
                                         // updateGraphFocus()'s fast path at the bottom
    var r = parseHash();
    // Set below whenever the hash names a tab or an argument that doesn't
    // actually resolve to anything real — the URL then gets corrected
    // (history.replaceState) to whatever ends up on screen, with a small
    // note, instead of silently showing a fallback under a wrong-looking URL.
    var badLink = null;
    var tabKnown = ["graph", "arch", "learn", "libs", "timeline", "map", "sim"].indexOf(r.tab) >= 0;
    if (r.tab && !tabKnown) badLink = "tab “" + r.tab + "”";
    state.tab = tabKnown ? r.tab : "learn";
    if (state.tab === "graph") {
      requestFit();   // a route change means a new layout — fit state.view to it
      // "#/graph/<key>/src" = that symbol with the source viewer open
      var gArg = r.arg;
      state.srcOpen = false;
      if (gArg && keyToI[gArg] == null && /\/src$/.test(gArg) && keyToI[gArg.slice(0, -4)] != null) {
        gArg = gArg.slice(0, -4);
        state.srcOpen = true;
      }
      if (gArg && keyToI[gArg] != null) { state.focus = keyToI[gArg]; state.fileScope = null; }
      else if (!gArg) state.focus = null;
      else { state.focus = null; badLink = badLink || "symbol “" + gArg + "”"; }
    } else if (state.tab === "learn") {
      // #/learn/<page-id>; no arg lands on Orientation, not the first folder —
      // "nobody understands a codebase entirely" is a better first thing to
      // read than whichever folder happened to sort first.
      //
      // Back-compat + slug-collision rule: before the Packages split, a bare
      // #/learn/<slug> always meant a library/module slug. Now it can mean the
      // orientation id, "parts", the category-map id, or a folder path — and a
      // folder path can collide with an old package slug (e.g. "frontend" is
      // both a folder and, on Packages, an internal-module entry). Folder wins
      // on Learn, package wins on Packages: only redirect to Packages when the
      // arg resolves to NEITHER a real Learn page NOR a folder, but DOES
      // resolve to a real Packages slug. Anything that resolves to neither
      // falls back to the orientation id, exactly like an empty arg always has.
      var learnArg = r.arg;
      if (!learnArg || learnArg === ORIENTATION_ID) {
        state.module = ORIENTATION_ID;
      } else if (learnArg === "parts" || learnArg === CATEGORY_MAP_ID || FOLDER_BY_PATH[learnArg]) {
        state.module = learnArg;
      } else if (LIB_BY_SLUG[learnArg]) {
        state.tab = "libs";
        state.pkg = learnArg;
      } else {
        state.module = ORIENTATION_ID;
        badLink = badLink || "page “" + learnArg + "”";
      }
    } else if (state.tab === "arch") {
      // #/arch/<item key>; a deep link to a test component turns the tests panel on
      state.archSel = r.arg || null;
      if (state.archSel && !archItem(state.archSel)) {
        badLink = badLink || "component “" + state.archSel + "”";
        state.archSel = null;
      }
      if (state.archSel && A_COMP[state.archSel] && A_COMP[state.archSel].layer === "tests") state.archShowTests = true;
    } else if (state.tab === "libs") {
      state.pkg = r.arg || null;   // null = the package index (first entry / a landing state)
      if (state.pkg && !LIB_BY_SLUG[state.pkg]) {
        badLink = badLink || "package “" + state.pkg + "”";
        state.pkg = null;
      }
    } else if (state.tab === "map") {
      var seg = r.arg.split("/");
      if (["layers", "trace", "mass"].indexOf(seg[0]) >= 0) state.mapView = seg[0];
      else if (!r.arg) state.mapView = "layers";   // "#/map" with no arg always lands on Layers (M14 — Map Back)
      else badLink = badLink || "map view “" + seg[0] + "”";
      if (state.mapView === "trace") {
        var key = seg.slice(1).join("/");
        if (key && keyToI[key] != null) { state.traceRoot = keyToI[key]; state.traceStep = null; }
        else if (!key) { if (traceRoots.length) { state.traceRoot = traceRoots[0].i; state.traceStep = null; } }   // "#/map/trace" with no key resets to the default root, even if one was already picked
        else {
          badLink = badLink || "trace root “" + key + "”";
          if (state.traceRoot == null && traceRoots.length) state.traceRoot = traceRoots[0].i;
        }
      }
    } else if (state.tab === "sim") {
      // "#/sim/<scenario-id>/<step>" — the id itself may contain "/" (e.g. a
      // symbol key), so split off the trailing numeric step, not the first "/".
      var simParts = r.arg.split("/");
      var lastPart = simParts[simParts.length - 1];
      var hasStep = simParts.length > 1 && /^\d+$/.test(lastPart);
      var scId = hasStep ? simParts.slice(0, -1).join("/") : r.arg;
      // ensureScenario (not a bare simById lookup) so a "derive:"/"origin:"
      // deep link materializes on arrival even when its target was never one
      // of the handful pre-seeded into the rail — this is what makes both
      // Map ▸ "Simulate ▶" and the inspector's "Trace back to entry ▶" work
      // for a symbol outside the top few busiest in the repo.
      if (scId && ensureScenario(scId)) {
        state.simScenario = scId;
      } else {
        if (scId) badLink = badLink || "scenario “" + scId + "”";
        if (!state.simScenario || !simById[state.simScenario]) {
          // simStartScenario() picks the authored curriculum's lowest-`order`
          // hero scenario (see its own comment); a bare SIM_SCENARIOS[0] is
          // just insertion order and, with an authored curriculum, usually
          // isn't the same scenario at all.
          var startSc = simStartScenario() || SIM_SCENARIOS[0];
          state.simScenario = startSc ? startSc.id : null;
        }
      }
      state.simStep = hasStep ? parseInt(lastPart, 10) : 0;
      state.simPlaying = false;
    } else if (state.tab === "timeline") {
      state.timelineSha = r.arg || null;
    }
    state.routeNote = badLink ? ("Link not found (" + badLink + ") — showing " + (TAB_LABEL[state.tab] || state.tab) + ".") : null;
    if (badLink) {
      var canon = canonicalHash();
      if (location.hash !== canon) history.replaceState(null, "", canon);
    }
    // railEl/canvasEl/svgEl only exist once the graph tab has actually
    // rendered at least once — false on first load and on every return trip
    // from another tab, so those still get the full render().
    if (state.tab === "graph" && prevTab === "graph" && railEl && canvasEl && svgEl)
      updateGraphFocus();
    else render();
  }

  // ---- topbar ---------------------------------------------------
  function topbar() {
    var s = DATA.stats || {};
    var tabs = [
      ["graph", "ph ph-graph", "Graph"],
      ["arch", "ph ph-tree-structure", "Architecture"],
      ["map", "ph ph-stack", "Map"],
      ["sim", "ph ph-play-circle", "Simulate"],
      ["learn", "ph ph-graduation-cap", "Learn"],
      ["libs", "ph ph-package", "Packages"],
      ["timeline", "ph ph-git-commit", "Timeline"],
    ].map(function (t) {
      var active = state.tab === t[0];
      // the label collapses to icon-only under ~400px (see explore.css) —
      // aria-label keeps the button's accessible name whether or not the
      // text is actually painted.
      return el("button", {
        class: "tab" + (active ? " active" : ""), "aria-current": active ? "page" : null,
        "aria-label": t[2],
        on: { click: function () { go(t[0]); } },
      }, [el("i", { class: t[1] }), el("span", { class: "tab-label", text: t[2] })]);
    });
    // <header>/<nav>/<h1> — the app was pure div soup with no landmarks and no
    // heading at all, so a screen reader had nothing to jump to or announce.
    // The h1 is visually hidden: the brand mark next to it already carries
    // this visually, a second visible "codemap" would just be noise.
    return el("header", { class: "topbar" }, [
      el("h1", { class: "sr-only", text: "codemap Explorer" }),
      el("div", { class: "brand" }, [
        el("i", { class: "ph ph-graph" }), el("span", { text: "codemap" }),
        el("span", { class: "ver", text: (DATA.generator || "").replace("codemap ", "v") }),
        engineBadge(),
      ]),
      el("nav", { class: "tabs", "aria-label": "Sections" }, tabs),
      el("div", { class: "spacer" }),
      stat("files", s.files), stat("symbols", s.symbols),
      stat("edges", s.edges), stat("cycles", s.cycles),
    ]);
  }
  // shown only when a codebase-memory-mcp index of this repo was merged into
  // the call graph (model.py adds `engine` then, and only then)
  function engineBadge() {
    var en = DATA.engine;
    if (!en) return null;
    var bits = [];
    if (en.added) bits.push(en.added + " call link" + (en.added === 1 ? "" : "s") + " added");
    if (en.upgraded) bits.push(en.upgraded + " guess" + (en.upgraded === 1 ? "" : "es") + " confirmed");
    if (en.dropped) bits.push(en.dropped + " wrong guess" + (en.dropped === 1 ? "" : "es") + " removed");
    return el("span", { class: "engine-badge", text: "+ codebase-memory", title:
      "Call links here were checked against a codebase-memory-mcp index of this repo" +
      (bits.length ? ": " + bits.join(", ") + "." : ".") +
      (en.stale_files ? " " + en.stale_files + " file" + (en.stale_files === 1 ? "" : "s") +
        " changed since it was indexed and were skipped." : "") });
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
  // Shown once, right after route() corrects a hash that didn't resolve to
  // anything real (see canonicalHash()) — cleared as soon as any later route
  // succeeds, so it never lingers once the viewer navigates on their own.
  function linkNoteBanner() {
    if (!state.routeNote) return null;
    return el("div", { class: "stale-banner link-note" }, [
      el("i", { class: "ph ph-warning" }),
      el("span", { text: state.routeNote }),
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
  // walks TREE by path segments — used by the graph legend to look up "what
  // folders live directly inside the folder currently drilled into", so the
  // legend can mirror the real directory tree instead of a flat repo-wide list.
  function treeNodeAt(path) {
    if (!path) return TREE;
    var segs = path.split("/"), cur = TREE;
    for (var i = 0; i < segs.length; i++) {
      cur = cur.dirs[segs[i]];
      if (!cur) return null;
    }
    return cur;
  }
  function parentFolder(path) {
    var i = path.lastIndexOf("/");
    return i < 0 ? "" : path.slice(0, i);
  }

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
      var hasSyms = f.symbols.length > 0;
      frag.appendChild(el("div", {
        class: "trow" + (fileSel ? " sel" : ""),
        style: "padding-left:" + (6 + depth * 11) + "px",
        title: hasSyms
          ? f.symbols.length + " symbol" + (f.symbols.length === 1 ? "" : "s")
          : "no functions or classes captured here",
        on: { click: function () {
          state.fileScope = f.fi;
          // the highest fan-in symbol, not just whichever happened to parse
          // first — same ranking the graph canvas itself uses for a file node.
          if (hasSyms) { fileAct(f)(); return; }
          // nothing to focus: still give the click something real to do
          // instead of silently no-op'ing, via the inline note below.
          treeOpen["f:" + f.path] = !treeOpen["f:" + f.path];
          renderRail();
        } },
      }, [
        el("i", { class: fileIcon(f.lang) }),
        el("span", { class: "tname", text: f.path.split("/").pop() }),
        el("span", { class: "tcount tcount-file", text: f.symbols.length || "" }),
      ]));
      var open = treeOpen["f:" + f.path];
      var kids = el("div", { class: "tchildren" + (open ? " open" : "") });
      if (!hasSyms)
        kids.appendChild(el("div", { class: "trow-note",
          style: "padding-left:" + (6 + (depth + 1) * 11 + 6) + "px",
          text: "no functions or classes captured here" }));
      f.symbols.forEach(function (si) {
        var n = N[si];
        var isFocused = state.focus === si;
        kids.appendChild(el("div", {
          class: "trow" + (isFocused ? " sel" : "") +
            (n.fan_in === 0 && n.entry.length === 0 ? " dim" : ""),
          style: "padding-left:" + (6 + (depth + 1) * 11 + 6) + "px",
          title: n.fan_in + " caller" + (n.fan_in === 1 ? "" : "s"),
          "data-rail-focus": isFocused ? "1" : null,
          on: { click: function (ev) { ev.stopPropagation(); go("graph", n.key); } },
        }, [
          el("span", { class: "tkind", text: kindLabel(n.kind) }),
          el("span", { class: "tname", text: n.name }),
          el("span", { class: "tcount", text: n.fan_in || "" }),
        ]));
      });
      frag.appendChild(kids);
    });
  }

  var railEl;
  var lastRailScrollFocus;   // only scroll on the render right after a focus
                             // change — not on every incidental re-render
                             // (an unrelated folder toggle shouldn't yank the
                             // viewer's scroll position back to it)
  function renderRail() {
    if (!railEl) return;
    var tree = railEl.querySelector(".tree");
    clear(tree);
    tree.appendChild(el("div", { class: "tree-kicker", text: "SOURCE" }));
    // The tree only opens a folder lazily, as the viewer clicks into it — a
    // symbol reached any other way (search, a graph node, a caller/callee
    // row, a deep link) would otherwise sit behind folders still collapsed,
    // with nothing on screen showing where it actually lives.
    if (state.focus != null) {
      var focusedFile = N[state.focus].file;
      var parts = focusedFile.split("/"), prefix = "";
      for (var i = 0; i < parts.length - 1; i++) {
        prefix += parts[i] + "/";
        treeOpen[prefix] = true;
      }
      treeOpen["f:" + focusedFile] = true;
    }
    var frag = document.createDocumentFragment();
    renderTreeNode(TREE, "", 0, frag);
    tree.appendChild(frag);
    if (lastRailScrollFocus !== state.focus) {
      var focusedRow = tree.querySelector("[data-rail-focus]");
      if (focusedRow && focusedRow.scrollIntoView) focusedRow.scrollIntoView({ block: "nearest" });
      lastRailScrollFocus = state.focus;
    }
  }
  function rail() {
    var s = DATA.stats || {};
    railEl = el("nav", { class: "rail", "aria-label": "Source tree" }, [
      el("button", { class: "mobile-only mobile-close", "aria-label": "Close source tree",
        on: { click: function () { state.mobileRail = false; render(); } } }, [el("i", { class: "ph ph-x" })]),
      // a real (but readonly) <input> here looked like a text field a user
      // could type into — it couldn't, typing and Enter both did nothing.
      // A plain label makes the one actual control unambiguous: this row
      // opens the palette (el() gives it a button role + tab stop below,
      // since it carries an on.click), where the real typing happens.
      el("div", { class: "rail-search", "aria-label": "Find anything you saw on screen",
        on: { click: openPalette } }, [
        el("i", { class: "ph ph-magnifying-glass" }),
        el("span", { class: "rail-search-ph", text: "Find anything on screen…" }),
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
      else { state.grain = 2; render(); }   // no symbols to focus: fall back to Function grain
    };
  }
  // Isolates every folder at or below `prefix`: state.folders holds folder
  // KEYS (not colours) — several unrelated long-tail folders can share
  // GROUP_OTHER's one hex, so filtering by colour would light up every folder
  // that happens to share that colour, anywhere in the repo, not just the
  // ones under `prefix`. An ancestor folder with no colour of its own still
  // isolates correctly, by sweeping up every descendant's key. `prefix === ""`
  // (repo root) intentionally matches nothing, i.e. clears the filter.
  // state.folderScope records `prefix` itself, verbatim, so the legend (see
  // legend() / openFolder()) knows which folder it's currently browsing.
  function isolateFolder(prefix) {
    state.groupBy = "folder";   // folder keys mean nothing in layer mode
    var hits = new Set();
    Object.keys(groupColor).forEach(function (k) {
      if (k === prefix || k.indexOf(prefix + "/") === 0) hits.add(k);
    });
    state.folders = hits;
    state.folderScope = prefix;
  }
  // A folder crumb "opens" that folder: drop any focus and isolate its
  // subtree. Reached only from an already-focused symbol's breadcrumb, so —
  // unlike the legend's own openFolder() below — it deliberately backs out of
  // that focus rather than layering the filter on top of it.
  function crumbFolder(prefix) {
    return function () {
      isolateFolder(prefix);
      state.focus = null;
      state.fileScope = null;
      state.pin = null;
      if (state.grain < 1) state.grain = 1;   // jump out of the Module cloud so files/symbols are visible
      go("graph");
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

    if (grain === 0) {
      mods.forEach(function (m, mi) {
        var c = centers[m.name];
        placed.push({ i: "mod:" + m.name, x: c.x, y: c.y, style: NODE_STYLE.hot,
          label: m.name + "  (" + m.symbol_count + ")", kind: "mod", group: gModuleColor(m.name),
          act: function () { state.grain = 1; requestFit(); render(); } });
      });
      var seen = {};
      (DATA.file_edges || []).forEach(function (fe) {
        var a = FILES[fe.s].module, b = FILES[fe.t].module;
        if (a === b || seen[a + " " + b]) return;
        seen[a + " " + b] = 1;
        var md = moduleDepth(a);
        links.push({ a: centers[a], b: centers[b], seed: hashSeed(a + b), hot: true,
          sk: "mod:" + a, tk: "mod:" + b,
          grp: gModuleColor(a), live: md >= 0, dep: md < 0 ? 0 : md, vol: 8 });
      });
      return { placed: placed, links: links, lobes: lobes, capped: false };
    }

    var perModule = {};
    var items = grain === 1
      ? FILES.map(function (f) {
          return { key: "file:" + f.path, mod: f.module, label: f.path.split("/").pop(),
            weight: f.symbols.reduce(function (a, si) { return a + N[si].fan_in; }, 0), ref: f, kind: "file" };
        })
      : N.map(function (n) {
          return { key: n.key, mod: n.module, label: n.name, weight: n.fan_in + n.churn * 2,
            ref: n, kind: "sym", idx: n.i };
        });
    items.forEach(function (it) { (perModule[it.mod] || (perModule[it.mod] = [])).push(it); });

    var pos = {}, capped = false;
    Object.keys(perModule).forEach(function (mn) {
      var arr = perModule[mn].sort(function (a, b) { return b.weight - a.weight; });
      if (grain === 2 && arr.length > 42) { arr = arr.slice(0, 42); capped = true; }
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
        var fk = it.kind === "file" ? gKey(it.ref.path) : gKey(it.ref.file);
        placed.push({ i: it.idx == null ? it.key : it.idx, x: x, y: y, style: style,
          label: it.label, kind: it.kind, dead: dead,
          group: it.kind === "file" ? gColorForPath(it.ref.path) : gColorForNode(it.ref), fk: fk,
          act: it.kind === "file" ? fileAct(it.ref) : null });
      });
    });

    if (grain === 1) {
      // unlike grain 2 just below, this had no budget at all — a repo with a
      // few thousand file_edges (dense same-module imports, not unusual)
      // meant that many <path> elements (4 each, via dendrite()) built on
      // every landing on/reset to this default view. Same style of cap:
      // cross-module edges are the architecturally interesting ones (and
      // typically the minority), so those stay uncapped; same-module edges
      // — already visually grouped by their shared lobe — give way first.
      var fcap = 0;
      (DATA.file_edges || []).forEach(function (fe) {
        var a = pos["file:" + FILES[fe.s].path], b = pos["file:" + FILES[fe.t].path];
        if (!a || !b) return;
        var hot = FILES[fe.s].module !== FILES[fe.t].module;
        if (!hot && fcap++ > 220) { capped = true; return; }
        var fd = fileFlow[fe.s], tf = FILES[fe.t];
        links.push({ a: a, b: b, seed: fe.s * 131 + fe.t, hot: hot,
          sk: "file:" + FILES[fe.s].path, tk: "file:" + FILES[fe.t].path,
          grp: gColorForPath(FILES[fe.s].path), fk: gKey(FILES[fe.s].path),
          live: fd >= 0, dep: fd < 0 ? 0 : fd,
          vol: (tf.symbols || []).reduce(function (s, si) { return s + N[si].fan_in; }, 0) });
      });
    } else {
      var cap = 0;
      E.forEach(function (e) {
        var a = pos[N[e.s].key], b = pos[N[e.t].key];
        if (!a || !b) return;
        if (e.tier === 1 && N[e.s].fan_in < 3 && N[e.t].fan_in < 3 && cap++ > 220) return;
        var sd = symDepth(e.s);
        links.push({ a: a, b: b, seed: e.s * 131 + e.t, hot: e.tier === 2,
          guess: e.confidence === "AMBIGUOUS",
          s: e.s, t: e.t, sk: e.s, tk: e.t, grp: gColorForNode(N[e.s]), fk: gKey(N[e.s].file),
          live: sd >= 0, dep: sd < 0 ? 0 : sd, vol: N[e.t].fan_in });
      });
    }
    return { placed: placed, links: links, lobes: lobes, capped: capped };
  }

  var RING_HOP_BUDGET = 60;   // see layoutFocus() — caps each hop-ring, not the whole graph
  function layoutFocus() {
    var f = state.focus;
    var soma = { x: VBW / 2, y: VBH / 2 };
    var placed = [{ i: f, x: soma.x, y: soma.y, style: NODE_STYLE.focus, label: N[f].name,
      kind: "focus", group: gColorForNode(N[f]), fk: gKey(N[f].file) }];
    var links = [];
    var capped = false;
    function ring(dist, side) {
      var byHop = {};
      Array.from(dist.entries()).forEach(function (p) { (byHop[p[1]] = byHop[p[1]] || []).push(p[0]); });
      Object.keys(byHop).forEach(function (hk) {
        var h = +hk, group = byHop[hk];
        // a hop's frontier is unbounded BFS breadth — a widely-used helper
        // (a logger, a base class method) can have hundreds to thousands of
        // callers/callees at hop 1-2 on a large repo, which used to mean
        // that many full <g><circle><text> nodes (each with 4-6 listeners)
        // built on one click. Show the most load-bearing ones (same ranking
        // the overview's per-module cap already uses: highest fan-in first)
        // and note the rest via updateCap(), the same way the overview does.
        if (group.length > RING_HOP_BUDGET) {
          group = group.slice().sort(function (a, b) { return N[b].fan_in - N[a].fan_in; })
            .slice(0, RING_HOP_BUDGET);
          capped = true;
        }
        group.forEach(function (idx, k) {
          var t = group.length === 1 ? 0.5 : k / (group.length - 1);
          var ang = (side === "in" ? Math.PI : 0) + (t - 0.5) * 1.7;
          var r = 120 + h * 92 + (rnd(hashSeed(N[idx].key)) - 0.5) * 34;
          var x = soma.x + Math.cos(ang) * r * 0.98, y = soma.y + Math.sin(ang) * r * 0.72;
          var style = N[idx].entry.length ? NODE_STYLE.entry
            : N[idx].fan_in >= 6 ? NODE_STYLE.hot : NODE_STYLE.node;
          placed.push({ i: idx, x: x, y: y, style: style, label: N[idx].name,
            kind: side === "in" ? "caller" : "callee", group: gColorForNode(N[idx]),
            fk: gKey(N[idx].file), hop: h });
          var src = side === "in" ? idx : f, dst = side === "in" ? f : idx;
          var sd = symDepth(src);
          links.push({ a: side === "in" ? { x: x, y: y } : soma,
            b: side === "in" ? soma : { x: x, y: y },
            seed: hashSeed(N[idx].key), hot: h === 1, thin: side === "in",
            s: src, t: dst, sk: src, tk: dst, grp: gColorForNode(N[idx]), fk: gKey(N[idx].file),
            live: sd >= 0, dep: sd < 0 ? 0 : sd, vol: N[dst].fan_in });
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
        placed.push({ i: "ext:" + dep, x: x, y: y, style: NODE_STYLE.ext, label: dep,
          kind: "ext", group: IMPORT_EDGE });
        var sd = symDepth(f);
        links.push({ a: soma, b: { x: x, y: y }, seed: hashSeed(dep), imp: true,
          sk: f, tk: "ext:" + dep,
          live: sd >= 0, dep: sd < 0 ? 0 : sd, vol: 0 });
      });
    }
    return { placed: placed, links: links, lobes: [], capped: capped };
  }

  function renderGraphSVG() {
    var lay = state.focus != null ? layoutFocus() : layoutOverview();
    lastLay = lay;
    if (pendingFit) { fitView(lay); pendingFit = false; }
    var g = el("g", { class: "pz",
      transform: "translate(" + state.view.x + "," + state.view.y + ") scale(" + state.view.k + ")" });

    lay.lobes.forEach(function (lo) {
      var lc = gLobeColor(lo.name), plain = lc === GROUP_OTHER;
      g.appendChild(el("ellipse", { cx: lo.x, cy: lo.y, rx: lo.rx, ry: lo.ry,
        fill: plain ? "rgba(147,151,171,.035)" : hexA(lc, 0.055),
        stroke: plain ? "rgba(147,151,171,.18)" : hexA(lc, 0.34), "stroke-width": 1.2 }));
      g.appendChild(el("text", { x: lo.x, y: lo.y - lo.ry - 6, "text-anchor": "middle",
        "font-size": 12, "font-weight": 600, "letter-spacing": ".08em",
        fill: plain ? "#75798c" : lc, text: lo.name.toUpperCase() }));
    });

    // pick which live edges get motion, cheapest first. Depth-ascending keeps the
    // animated set as intact chains radiating from the entry points, not scatter.
    var animate = state.flow;   // default honours prefers-reduced-motion; the pill overrides
    var flowSet = null, cometSet = null, flowN = 0;
    if (animate) {
      var liveIdx = [];
      lay.links.forEach(function (lk, i) { if (lk.live && lk.a && lk.b) liveIdx.push(i); });
      liveIdx.sort(function (x, y) {
        var lx = lay.links[x], ly = lay.links[y];
        return (lx.dep - ly.dep) || (ly.vol - lx.vol);
      });
      // The budget used to skip the neuron view and Module grain on the
      // assumption there are always few links there — a busy entry point's
      // neuron view, or a repo with a lot of cross-module traffic, breaks
      // that assumption and re-creates the same fps drop the budget exists
      // to prevent. Apply it everywhere.
      flowSet = new Set(liveIdx.slice(0, FLOW_BUDGET));
      cometSet = new Set(liveIdx.slice(0, COMET_BUDGET));
      flowN = flowSet.size;
    }

    var edgeG = el("g", { fill: "none", "stroke-linecap": "round" });
    lay.links.forEach(function (lk, li) {
      if (!lk.a || !lk.b) return;
      var d = dendrite(lk.a, lk.b, lk.seed, { bow: lk.hot ? 0.42 : 0.3 });
      var imp = !!lk.imp;
      var guess = !!lk.guess;
      var col = imp ? IMPORT_EDGE : (lk.grp || GROUP_OTHER);
      var w = imp ? 1.5 : lk.hot ? 2.8 : lk.thin ? 1.9 : 2.2;
      var op = imp ? 0.5 : guess ? 0.5 : lk.hot ? 0.92 : 0.76;
      var grp = el("g", { class: "edge" + (guess ? " edge-guess" : "") });
      if (!imp)   // soft colour glow: a run of same-folder edges reads as one strand
        grp.appendChild(el("path", { d: d.d, stroke: col, "stroke-width": w + 4,
          opacity: lk.hot ? 0.16 : 0.09 }));
      // dashed = a name-only guess, same as an import edge's dash (see the
      // legend) — no import connects the two files, so this might not be a
      // real call at all.
      grp.appendChild(el("path", { d: d.d, stroke: col, "stroke-width": w, opacity: op,
        "stroke-dasharray": (imp || guess) ? "5 4" : null }));
      grp.appendChild(el("path", { d: d.b1, stroke: col, "stroke-width": w * 0.38, opacity: op * 0.55 }));
      grp.appendChild(el("path", { d: d.b2, stroke: col, "stroke-width": w * 0.32, opacity: op * 0.38 }));

      if (animate && flowSet.has(li)) {   // the "current": one shared CSS keyframe
        var fp = el("path", { class: "flow", d: d.d, stroke: col,
          "stroke-width": Math.max(1.3, w * 0.6), opacity: Math.min(0.95, op + 0.12) });
        fp.style.animationDelay = (lk.dep * CASCADE + rnd(lk.seed) * 0.5) + "s";
        grp.appendChild(fp);
      }
      if (animate && cometSet.has(li)) {   // discrete packets on the hottest paths
        var dx = lk.b.x - lk.a.x, dy = lk.b.y - lk.a.y;
        var dur = Math.max(0.9, Math.min(4.2, Math.hypot(dx, dy) * 1.15 / PKT_SPEED));
        var begin = lk.dep * CASCADE;
        grp.appendChild(comet(d.d, col, dur, begin, false));
        if (lk.vol >= 6) grp.appendChild(comet(d.d, col, dur, begin + dur / 2, true));
      }

      edgeItems.push({ el: grp, grp: lk.fk || null,
        s: lk.sk == null ? -1 : lk.sk, t: lk.tk == null ? -1 : lk.tk });
      edgeG.appendChild(grp);
    });
    g.appendChild(edgeG);
    lastFlowN = flowN;
    lastPlacedN = lay.placed.length;
    lastPlacedCapped = !!lay.capped;

    // node size encodes received traffic: how many packets land on it per cycle
    // (a live in-edge counts 1, a high-volume one 2). Nodes traffic never reaches
    // keep their base size — big = does a lot of work in this codebase.
    var recv = {};
    if (animate)
      lay.links.forEach(function (lk) {
        if (!lk.live || lk.imp || lk.tk == null) return;
        recv[lk.tk] = (recv[lk.tk] || 0) + 1 + (lk.vol >= 6 ? 1 : 0);
      });

    var nodeG = el("g");
    lay.placed.forEach(function (p) {
      var st = p.style;
      var ring = p.group || st.stroke;
      var rr = recv[p.i] || 0;                                     // traffic landing here
      var R = st.r + (rr > 0 ? Math.min(13, Math.sqrt(rr) * 2.6) : 0);
      var wrap = el("g", { class: "gnode" });
      if (st.halo) wrap.appendChild(el("circle", { cx: p.x, cy: p.y,
        r: Math.max(st.halo, R + 12), fill: st.hc }));
      if (animate && typeof p.i === "number" && entrySet.has(p.i)) {   // where packets are born
        // a CSS transform/opacity keyframe (compositor-only) — this used to be
        // two SMIL <animate> children re-tessellating r/opacity every frame,
        // on every entry point on screen at once.
        var pr = el("circle", { class: "emit", cx: p.x, cy: p.y, r: R + 2,
          fill: "none", stroke: ring, "stroke-width": 1.4 });
        pr.style.animationDelay = (-(rnd(p.i + 1) * 2.4)) + "s";
        pr.style.setProperty("--emit-scale", ((R + 17) / (R + 2)).toFixed(3));
        wrap.appendChild(pr);
      }
      wrap.appendChild(el("circle", { class: "body", cx: p.x, cy: p.y, r: R,
        fill: st.fill, stroke: ring, "stroke-width": p.kind === "focus" ? 2 : 1.6 }));
      // a native tooltip on the whole node — the one thing a label-culling
      // pass (below) can't take away, so a culled label's name is still one
      // hover away, not just gone.
      wrap.appendChild(el("title", { text: p.label }));
      var labelY = p.y + R + 12;
      var labelEl = el("text", { x: p.x, y: labelY, "text-anchor": "middle",
        "font-size": p.kind === "focus" ? 12 : 10.5,
        fill: p.kind === "focus" ? "#f5f4ff" : p.dead ? "#595d6c" : "#b2b6ca", text: p.label });
      wrap.appendChild(labelEl);
      labelItems.push({ el: labelEl, p: p, y: labelY });
      var act = p.act || (typeof p.i === "number"
        ? function () { go("graph", N[p.i].key); } : null);
      var pinnable = p.i != null && p.kind !== "focus" && p.kind !== "ext";
      wrap.style.cursor = (act || pinnable) ? "pointer" : "default";
      if (pinnable) {
        // one click drills in — the same primary action touch and Enter
        // already ran. Shift+click pins a spotlight on this node + its
        // edges instead, without navigating.
        wrap.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (ev.shiftKey) { togglePin(p.i); return; }
          if (act) { state.pin = null; act(); } else togglePin(p.i);
        });
      } else if (act) {
        wrap.addEventListener("click", function (ev) { ev.stopPropagation(); act(); });
      }
      // Shift+click has no keyboard equivalent — Enter/Space always drills
      // in, same as a plain click. Pinning from the keyboard isn't lost:
      // focus itself already spotlights the node (see the focus/blur
      // listeners below).
      //
      // tabindex="-1", not "0": these used to sit in the natural Tab order —
      // one stop per node, so reaching the inspector past a few hundred of
      // them on a real repo took hundreds of presses. The rail (already
      // fully keyboard-reachable — el()'s own a11y wrapper covers every
      // .trow) is the real keyboard path through this same tree; a node
      // stays individually operable (role, label, Enter/Space) for a mouse
      // or touch user, or anything that focuses it programmatically, it's
      // just not a Tab stop of its own.
      if (act || pinnable) {
        wrap.setAttribute("tabindex", "-1");
        wrap.setAttribute("role", "button");
        wrap.setAttribute("aria-label", p.label);
        wrap.addEventListener("keydown", function (ev) {
          if (ev.key !== "Enter" && ev.key !== " " && ev.key !== "Spacebar") return;
          ev.preventDefault();
          ev.stopPropagation();
          if (act) { state.pin = null; act(); } else { togglePin(p.i); }
        });
        wrap.addEventListener("focus", function () { setHighlight(p.i); });
        wrap.addEventListener("blur", function () { setHighlight(null); });
      }
      nodeItems.push({ el: wrap, key: p.i, grp: p.fk || null });
      wrap.addEventListener("mouseenter", function () { setHighlight(p.i); });
      wrap.addEventListener("mouseleave", function () { setHighlight(null); });
      nodeG.appendChild(wrap);
    });
    g.appendChild(nodeG);
    return g;
  }

  // Node labels have no collision handling of their own — on a dense cluster
  // (a busy hub's neuron view, a big module at File/Function grain) they used
  // to just overlap into an unreadable smear. A greedy pass instead keeps the
  // most important label at every point of overlap and hides the rest —
  // never removes the node itself, and a culled label is always one hover (or
  // its aria-label / <title>, for keyboard and touch) away.
  function labelImportance(p) {
    if (p.kind === "focus") return Infinity;
    var fanIn = typeof p.i === "number" && N[p.i] ? N[p.i].fan_in : 0;
    var hop = p.hop || 0;   // only the neuron view's rings set this
    return -hop * 1e6 + fanIn;   // lower hop always outranks any fan-in gap
  }
  function cullLabels() {
    var CHAR_W = 6.2, HALF_H = 6;
    var ranked = labelItems.slice().sort(function (a, b) {
      return labelImportance(b.p) - labelImportance(a.p);
    });
    var kept = [];
    ranked.forEach(function (it) {
      var w = ((it.p.label || "").length * CHAR_W) / 2;
      var box = { x0: it.p.x - w, x1: it.p.x + w, y0: it.y - HALF_H, y1: it.y + HALF_H };
      var hit = kept.some(function (k) {
        return box.x0 < k.x1 && box.x1 > k.x0 && box.y0 < k.y1 && box.y1 > k.y0;
      });
      if (hit) it.el.classList.add("label-hidden");
      else { it.el.classList.remove("label-hidden"); kept.push(box); }
    });
  }

  // ---- graph paint / pan / zoom ---------------------------------------
  // The scene <g> is built once per structural change (grain, focus, hops,
  // showDead). Pan and zoom only rewrite its `transform` — no relayout, no DOM
  // churn — and hover only nudges opacity. That is what keeps the canvas smooth.
  var canvasEl, svgEl, emptyEl, sceneG, edgeItems = [], nodeItems = [], labelItems = [];
  var drag = null, panPend = null, panRaf = 0, zoomRaf = 0, settleT = 0, lastFlowN = 0, animPaused = false;
  var lastPlacedN = 0, lastPlacedCapped = false;   // how many nodes the last layout actually placed
  var lastPanMoved = false;   // did the last mouseup end a real pan (vs a bare click)

  // Freeze every animation for the duration of a gesture. stroke-dashoffset is a
  // main-thread paint prop; re-tessellating dashes along 200+ beziers every frame
  // would undo the transform-only pan/zoom. So during a drag nothing animates.
  function freezeAnims() {
    if (animPaused) return;
    animPaused = true;
    if (svgEl && svgEl.pauseAnimations) svgEl.pauseAnimations();
  }
  function thawAnims() {
    if (!animPaused) return;
    animPaused = false;
    if (svgEl && svgEl.unpauseAnimations) svgEl.unpauseAnimations();
  }
  // Unlike switching app tabs (which detaches the canvas entirely, so its
  // animations already cost nothing), the *browser* tab going to the
  // background leaves the DOM attached — SMIL and CSS animations keep
  // running there unless paused explicitly. bound once; safe to call
  // whether or not the Graph tab happens to be the active app section.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { freezeAnims(); if (canvasEl) canvasEl.classList.add("tab-hidden"); }
    else { thawAnims(); if (canvasEl) canvasEl.classList.remove("tab-hidden"); }
  });

  function clampK(k) { return Math.max(0.3, Math.min(4, k)); }
  var lastLay = null, pendingFit = true;   // true = the next paint fits state.view to it
  function requestFit() { pendingFit = true; }
  // The legend (bottom-right) and the zoom box (bottom-left) float over the
  // canvas — a fit that ignored them could centre content right behind
  // either one. Both are already in the DOM by the time a layout paints, so
  // their real rendered size is measured and converted to viewBox units with
  // the same scale the svg's own viewBox mapping uses (see vbPoint()).
  function reservedMargins() {
    var m = { top: 30, left: 30, right: 30, bottom: 30 };
    if (!svgEl) return m;
    var r = svgEl.getBoundingClientRect();
    var s = Math.min(r.width / VBW, r.height / VBH) || 1;
    var legendEl = canvasEl && canvasEl.querySelector(".legend");
    var zEl = canvasEl && canvasEl.querySelector(".zoombox");
    if (legendEl) {
      var lr = legendEl.getBoundingClientRect();
      m.right = Math.max(m.right, lr.width / s + 18);
      m.bottom = Math.max(m.bottom, lr.height / s + 18);
    }
    if (zEl) {
      var zr = zEl.getBoundingClientRect();
      m.left = Math.max(m.left, zr.width / s + 18);
      m.bottom = Math.max(m.bottom, zr.height / s + 18);
    }
    return m;
  }
  // Fits state.view to whatever a layout actually placed, instead of the old
  // fixed identity transform — a big repo's overview, or a high-hop neuron
  // view, routinely spilled outside the 1040x700 viewBox at k=1.
  function fitView(lay) {
    var pts = (lay && lay.placed) || [];
    if (!pts.length) { state.view = { x: 0, y: 0, k: 1 }; return; }
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    pts.forEach(function (p) {
      var rad = (p.style && p.style.r) || 8;
      var top = p.y - rad - 14, bot = p.y + rad + 22;   // label sits below the node
      var lft = p.x - rad - 26, rgt = p.x + rad + 26;   // and needs side room too
      if (lft < minX) minX = lft;
      if (rgt > maxX) maxX = rgt;
      if (top < minY) minY = top;
      if (bot > maxY) maxY = bot;
    });
    var mg = reservedMargins();
    var availW = Math.max(160, VBW - mg.left - mg.right);
    var availH = Math.max(160, VBH - mg.top - mg.bottom);
    var bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
    var k = clampK(Math.min(availW / bw, availH / bh, 1.6));
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    state.view = { x: mg.left + availW / 2 - cx * k, y: mg.top + availH / 2 - cy * k, k: k };
  }
  function applyView() {
    if (sceneG) sceneG.setAttribute("transform",
      "translate(" + state.view.x + "," + state.view.y + ") scale(" + state.view.k + ")");
    updateCap();
  }
  // zoom about a point in viewBox space, holding it fixed on screen
  function zoomToward(px, py, f) {
    var nk = clampK(state.view.k * f);
    f = nk / state.view.k;
    state.view.x = px - f * (px - state.view.x);
    state.view.y = py - f * (py - state.view.y);
    state.view.k = nk;
  }
  function zoomAt(f) { zoomToward(VBW / 2, VBH / 2, f); applyView(); }
  function vbPoint(e) {
    var r = svgEl.getBoundingClientRect();
    var s = Math.min(r.width / VBW, r.height / VBH) || 1;
    return { x: (e.clientX - r.left - (r.width - VBW * s) / 2) / s,
             y: (e.clientY - r.top - (r.height - VBH * s) / 2) / s };
  }
  // pan/wheel: suspend the eased transition for 1:1 tracking. `skipApply`
  // lets a caller that's already driving the visual another way (the pan
  // CSS-transform proxy above; the rAF-coalesced wheel handler below) skip
  // the immediate `.pz` attribute write this would otherwise force.
  function liveView(skipApply) {
    if (canvasEl) canvasEl.classList.add("dragging");
    freezeAnims();
    clearTimeout(settleT);
    settleT = setTimeout(function () {
      if (canvasEl) canvasEl.classList.remove("dragging");
      thawAnims();
      cullLabels();   // a settled zoom level is worth a fresh declutter pass
    }, 160);
    if (!skipApply) applyView();
  }
  function setHighlight(i) {
    if (state.highlight === i) return;
    state.highlight = i;
    applyHighlight();
  }
  function togglePin(key) {
    state.pin = state.pin === key ? null : key;
    applyHighlight();
  }
  // One pass sets the final opacity of every node and edge from two lenses that
  // stack: the folder filter (legend clicks) washes out whole folders; the
  // spotlight (a hovered or click-pinned node) washes out everything not touching
  // that one node. Neither rebuilds the scene — like pan/zoom this only nudges
  // style.opacity on nodes/edges already in the DOM.
  //
  // Coalesced to one pass per animation frame: a fast mouse sweep across a
  // dense graph fires mouseenter/mouseleave far faster than that, and every
  // call before was a full pass over every node and edge on screen. Nothing
  // downstream reads the result synchronously, so every caller just fires
  // this and moves on — safe to defer.
  var highlightRaf = 0;
  function applyHighlight() {
    if (highlightRaf) return;
    highlightRaf = requestAnimationFrame(function () { highlightRaf = 0; applyHighlightNow(); });
  }
  function applyHighlightNow() {
    var h = state.highlight != null ? state.highlight : state.pin;
    if (h != null) {
      var known = new Set(nodeItems.map(function (it) { return it.key; }));
      if (!known.has(h)) { if (state.pin === h) state.pin = null; h = null; }   // stale after relayout
    }
    var near = null;
    if (h != null) {
      near = new Set([h]);
      edgeItems.forEach(function (it) {
        if (it.s === h) near.add(it.t);
        else if (it.t === h) near.add(it.s);
      });
    }
    // it.grp here is a folder KEY (a dirGroup path) — never a colour — so two
    // folders that happen to share GROUP_OTHER's hex never light each other
    // up just because a click matched one of them. See the note above
    // isolateFolder().
    var filtering = state.folders && state.folders.size > 0;
    function fdim(fk) {
      if (!filtering) return false;
      if (fk == null) return true;
      return !state.folders.has(fk);
    }
    edgeItems.forEach(function (it) {
      var o = 1;
      if (fdim(it.grp)) o = 0.05;
      if (h != null && !(it.s === h || it.t === h)) o = Math.min(o, 0.06);
      it.el.style.opacity = o === 1 ? "" : String(o);
    });
    nodeItems.forEach(function (it) {
      var o = 1;
      if (fdim(it.grp)) o = 0.12;
      if (h != null && !near.has(it.key)) o = Math.min(o, 0.22);
      it.el.style.opacity = o === 1 ? "" : String(o);
      it.el.classList.toggle("pinned", state.pin != null && it.key === state.pin);
    });
  }
  // Legend-driven folder browsing is a cheap lens change (opacity only, via
  // applyHighlight()) — it must not disturb whatever symbol is currently
  // focused/pinned, so unlike crumbFolder() it never touches state.focus and
  // never triggers a full go()/render(). prefix === "" isolates nothing,
  // i.e. resets to "show every folder" — clearFolders() is just that case.
  function openFolder(prefix) {
    isolateFolder(prefix);
    applyHighlight();
    refreshLegend();
  }
  function clearFolders() { openFolder(""); }
  function refreshLegend() {
    var old = canvasEl && canvasEl.querySelector(".legend");
    if (old) old.replaceWith(legend());
  }
  function paintGraph() {
    if (!svgEl) return;
    edgeItems = [];
    nodeItems = [];
    labelItems = [];
    clear(svgEl);
    sceneG = renderGraphSVG();
    svgEl.appendChild(sceneG);
    if (emptyEl) emptyEl.hidden = lastPlacedN > 0;
    applyView();
    applyHighlight();
    cullLabels();
  }

  // window-level drag listeners: bound once, not per render
  window.addEventListener("mousemove", function (e) {
    if (!drag) return;
    panPend = e;
    if (panRaf) return;
    panRaf = requestAnimationFrame(function () {
      panRaf = 0;
      drag.moved = true;
      // Move the already-painted <svg> with a CSS transform (compositor-only,
      // pure translate needs no transform-origin) instead of rewriting the
      // `.pz` group's SVG transform attribute on every frame — that attribute
      // write is what forced a repaint of every one of the (up to hundreds
      // of) dendrite paths on each mousemove. state.view.x/y and the real
      // `.pz` attribute are committed once, on mouseup.
      var dxPx = panPend.clientX - drag.x, dyPx = panPend.clientY - drag.y;
      if (svgEl) svgEl.style.transform = "translate(" + dxPx + "px," + dyPx + "px)";
      liveView(true);
    });
  });
  window.addEventListener("mouseup", function () {
    if (!drag && !animPaused) return;
    lastPanMoved = !!(drag && drag.moved);
    if (drag && drag.moved && svgEl) {
      svgEl.style.transform = "";
      svgEl.style.willChange = "";
      // fold the committed pixel delta into state.view, in viewBox units —
      // dividing by the viewBox's own render scale is the pan unit fix:
      // screen pixels were previously added directly as if they were
      // already viewBox units, so a drag tracked the cursor at the wrong rate
      // whenever the canvas wasn't rendered at exactly 1 viewBox unit / px.
      var r = svgEl.getBoundingClientRect();
      var s = Math.min(r.width / VBW, r.height / VBH) || 1;
      state.view.x = drag.vx + (panPend.clientX - drag.x) / s;
      state.view.y = drag.vy + (panPend.clientY - drag.y) / s;
      applyView();
    }
    drag = null;
    // covers both a finished drag and a bare click (which armed no settle timer)
    clearTimeout(settleT);
    settleT = setTimeout(function () {
      if (canvasEl) canvasEl.classList.remove("dragging");
      thawAnims();
      cullLabels();
    }, 120);
  });

  // Everything the stage bar shows (crumb, hop slider, dead/reset/details
  // pills) is a pure function of state.focus/state.grain/state.flow/etc, so
  // it can be rebuilt on its own and swapped in — see updateGraphFocus().
  function stageBar() {
    var n = state.focus != null ? N[state.focus] : null;
    var crumb = el("div", { class: "crumb" });
    if (n) {
      // every crumb segment is a live control: a folder isolates its subtree,
      // the filename scopes to that file, the trailing symbol re-centres the view.
      var segs = n.file.split("/");
      var fobj = fileByPath[n.file];
      segs.forEach(function (s, i) {
        if (i) crumb.appendChild(el("i", { class: "ph ph-caret-right" }));
        var last = i === segs.length - 1;
        crumb.appendChild(el("span", {
          class: last ? "cur crumb-part" : "crumb-part", text: s,
          on: last
            ? (fobj ? { click: fileAct(fobj) } : null)
            : { click: crumbFolder(segs.slice(0, i + 1).join("/")) },
        }));
      });
      crumb.appendChild(el("i", { class: "ph ph-caret-right" }));
      crumb.appendChild(el("span", { class: "cur crumb-part fn", text: n.name + "()",
        on: { click: function () { fitView(lastLay); applyView(); } } }));
    } else {
      crumb.appendChild(el("span", { class: "cur", text: "all modules" }));
    }
    // No persistent scrollbar. The wheel scrolls the path while the pointer is
    // over it, and a caret on each side nudges it. Carets + the edge fade only
    // appear when the path overflows, and each caret dims at its limit.
    function crumbNav(icon, dir) {
      return el("button", { class: "crumb-nav " + (dir < 0 ? "l" : "r"),
        "aria-label": dir < 0 ? "scroll path left" : "scroll path right",
        on: { click: function () {
          crumb.scrollBy({ left: dir * Math.max(90, crumb.clientWidth * 0.6), behavior: "smooth" });
        } } }, [el("i", { class: icon })]);
    }
    var crumbWrap = el("div", { class: "crumbwrap" }, [
      crumbNav("ph ph-caret-left", -1), crumb, crumbNav("ph ph-caret-right", 1),
    ]);
    function syncCrumbNav() {
      var over = crumb.scrollWidth - crumb.clientWidth;
      crumbWrap.classList.toggle("scrollable", over > 1);
      crumbWrap.classList.toggle("at-start", crumb.scrollLeft <= 0);
      crumbWrap.classList.toggle("at-end", crumb.scrollLeft >= over - 1);
    }
    crumb.addEventListener("wheel", function (e) {
      if (crumb.scrollWidth <= crumb.clientWidth) return;
      e.preventDefault();
      crumb.scrollLeft += e.deltaY || e.deltaX;
    }, { passive: false });
    crumb.addEventListener("scroll", syncCrumbNav);
    setTimeout(function () {
      syncCrumbNav();                       // carets claim their width first…
      crumb.scrollLeft = crumb.scrollWidth; // …then kick to the selected end
      syncCrumbNav();
    }, 0);

    // "Package" used to be a separate 4th grain, but layoutOverview() rendered
    // it identically to "Module" (both were `grain <= 1`) — a control that lied
    // about having two distinct views. Three real grains now.
    var GRAIN_ICON = ["ph-cube", "ph-file", "ph-function"];
    var grainSeg = el("div", { class: "seg" },
      ["Module", "File", "Function"].map(function (label, gi) {
        // the label collapses to icon-only under ~1180px (see explore.css) —
        // aria-label keeps the button's accessible name either way.
        return el("button", { class: state.grain === gi ? "on" : "", "aria-label": label,
          on: { click: function () {
            // the neuron view (a focused symbol) ignores grain entirely, so
            // clicking Module/File/Function while focused used to do nothing
            // visible — clear the focus first so the click always lands on
            // the overview it names.
            state.grain = gi; requestFit();
            lsSet("grain", gi);
            if (state.focus != null) go("graph"); else render();
          } } },
          [el("i", { class: "ph " + GRAIN_ICON[gi] }), el("span", { class: "seg-label", text: label })]);
      }));

    // hops slider + flow pill repaint the graph in-place (paintGraph(), not the
    // full render()) so a drag/click stays cheap — but that means their own
    // controls must be kept in sync by hand, not left for a rerender to fix.
    var hopReadout = el("b", { class: "mono", text: String(state.hops) });
    var hop = el("div", { class: "hopwrap" }, [
      "hops",
      el("input", { type: "range", min: "1", max: "4", value: String(state.hops), "aria-label": "Call hops",
        on: { input: function (e) {
          state.hops = +e.target.value;
          lsSet("hops", state.hops);
          hopReadout.textContent = String(state.hops);
          paintGraph(); updateCap();
        } } }),
      hopReadout,
    ]);

    var deadPill = el("button", { class: "pill" + (state.showDead ? " on" : ""),
      on: { click: function () { state.showDead = !state.showDead; render(); } } },
      [el("i", { class: "ph ph-eye-slash" }), "Unreachable"]);
    var resetPill = el("button", { class: "pill",
      on: { click: function () {
        state.focus = null; state.fileScope = null; state.touched = null; state.pin = null;
        go("graph");
      } } },
      [el("i", { class: "ph ph-arrow-counter-clockwise" }), "Whole graph"]);
    var flowPill = el("button", { class: "pill" + (state.flow ? " on" : ""),
      on: { click: function () {
        state.flow = !state.flow;
        lsSet("flow", state.flow);
        flowPill.classList.toggle("on", state.flow);
        paintGraph(); updateCap(); refreshLegend();
      } } },
      [el("i", { class: "ph ph-broadcast" }), "Traffic"]);

    // below 900px the rail and inspector become off-canvas overlays (see
    // .rail/.insp in the stylesheet) instead of just vanishing — these two
    // pills are their only entry point there, so they're the one thing in
    // the bar that's hidden again above 900px, where rail/inspector are
    // always-visible columns and need no toggle.
    var filesPill = el("button", { class: "pill mobile-only",
      on: { click: function () { state.mobileRail = true; render(); } } },
      [el("i", { class: "ph ph-list" }), "Files"]);
    var detailsPill = state.focus != null ? el("button", { class: "pill mobile-only",
      on: { click: function () { state.mobileInsp = true; render(); } } },
      [el("i", { class: "ph ph-info" }), "Details"]) : null;

    // showDead only ever filters *symbol* nodes (see layoutOverview()'s own
    // `it.kind === "sym"` guard) — at Module or File grain, every item on
    // screen is a module/file node, so toggling it did nothing visible.
    var showDeadPill = state.focus == null && state.grain === 2 ? deadPill : null;
    return el("div", { class: "stage-bar" }, [
      filesPill, crumbWrap, grainSeg, state.focus != null ? hop : null,
      el("div", { class: "spacer" }),
      detailsPill, flowPill,
      state.focus != null ? resetPill : showDeadPill,
    ]);
  }
  function stage() {
    var bar = stageBar();
    canvasEl = el("div", { class: "canvas" });
    svgEl = el("svg", { viewBox: "0 0 " + VBW + " " + VBH, preserveAspectRatio: "xMidYMid meet" });
    emptyEl = el("div", { class: "empty", role: "status",
      text: "Nothing to show at this grain. Turn on Unreachable, or pick a different grain.", hidden: true });
    canvasEl.appendChild(svgEl);
    canvasEl.appendChild(emptyEl);
    canvasEl.appendChild(legend());
    canvasEl.appendChild(zoombox());
    wireCanvas();
    paintGraph();
    return el("div", { class: "stage" }, [bar, canvasEl]);
  }
  function updateCap() {
    var cap = canvasEl && canvasEl.querySelector(".zoombox .cap");
    if (!cap) return;
    var base;
    if (state.focus != null) {
      base = "depth " + state.hops + " · neuron view";
      if (lastPlacedCapped) base += " (capped per hop, highest fan-in first)";
    } else {
      // lastPlacedN is what layoutOverview() actually placed — NOT
      // DATA.stats.symbols, which stays fixed at the repo's total symbol
      // count no matter the grain (2 module lobes, or 45 files, or a
      // per-module-capped set of functions all reported the same number).
      base = ["module", "file", "function"][state.grain] + " grain · " +
        lastPlacedN + " node" + (lastPlacedN === 1 ? "" : "s");
      if (lastPlacedCapped) base += " (capped)";
    }
    cap.textContent = state.flow && lastFlowN ? base + " · " + lastFlowN + " flows" : base;
  }
  // Drags the legend panel by its grip handle, repositioning it with an
  // explicit left/top (replacing the default right/bottom CSS anchor) and
  // clamping it inside the canvas. Position survives across rebuilds via
  // state.legendPos, the same way collapse/scope survive via their own
  // state fields — the legend node itself is thrown away and rebuilt on
  // almost every interaction (refreshLegend(), every render()), so nothing
  // can live on the DOM node itself.
  function wireLegendDrag(grip, box) {
    if (state.legendPos) {
      box.style.left = state.legendPos.left + "px";
      box.style.top = state.legendPos.top + "px";
      box.style.right = "auto";
      box.style.bottom = "auto";
    }
    // stopPropagation on mousedown (not just the pointerdown below) belt-
    // and-braces against canvasEl's own mousedown-driven pan-drag picking up
    // this same gesture — pointerdown's preventDefault suppresses the
    // synthesized compatibility mouse events on most browsers, but not all.
    grip.addEventListener("mousedown", function (e) { e.stopPropagation(); });
    var drag = null;
    grip.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      var r = box.getBoundingClientRect(), cr = canvasEl.getBoundingClientRect();
      drag = { x: e.clientX, y: e.clientY, left: r.left - cr.left, top: r.top - cr.top,
        cw: cr.width, ch: cr.height, bw: r.width, bh: r.height };
      box.classList.add("dragging");
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* unsupported: drag still works while the pointer stays over grip */ }
      e.preventDefault();
    });
    grip.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var nl = Math.max(4, Math.min(drag.left + (e.clientX - drag.x), drag.cw - drag.bw - 4));
      var nt = Math.max(4, Math.min(drag.top + (e.clientY - drag.y), drag.ch - drag.bh - 4));
      box.style.left = nl + "px";
      box.style.top = nt + "px";
      box.style.right = "auto";
      box.style.bottom = "auto";
      state.legendPos = { left: nl, top: nt };
    });
    function endDrag(e) {
      if (!drag) return;
      drag = null;
      box.classList.remove("dragging");
      try { grip.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
    }
    grip.addEventListener("pointerup", endDrag);
    grip.addEventListener("pointercancel", endDrag);
  }
  // Folder | Layer: recolours the same graph (see "graph grouping" above)
  function setGroupBy(mode) {
    if (state.groupBy === mode) return;
    state.groupBy = mode;
    state.folders = new Set();
    state.folderScope = "";
    paintGraph();
    refreshLegend();
  }
  function groupSwitch() {
    function opt(mode, label) {
      var on = state.groupBy === mode;
      return el("button", { class: on ? "on" : "", "aria-pressed": on ? "true" : "false", text: label,
        on: { click: function () { setGroupBy(mode); } } });
    }
    return el("div", { class: "legend-seg", role: "group", "aria-label": "Colour the graph by" },
      [opt("folder", "Folder"), opt("layer", "Layer")]);
  }
  // clicking a layer isolates it (transparent, not hidden — same rule as folders);
  // clicking the only isolated layer again shows everything
  function toggleLayer(id) {
    var key = "layer:" + id;
    state.folders = state.folders.size === 1 && state.folders.has(key) ? new Set() : new Set([key]);
    applyHighlight();
    refreshLegend();
  }
  function legendLayers(box) {
    var counts = layerCounts();
    LAYER_IDS.forEach(function (id) {
      if (!counts[id]) return;
      var on = state.folders.has("layer:" + id);
      box.appendChild(el("div", { class: "row nav" + (on ? " on" : ""),
        title: "isolate " + LAYER_LABEL[id] + " — " + counts[id] + " file" + (counts[id] === 1 ? "" : "s"),
        on: { click: function () { toggleLayer(id); } } }, [
        el("span", { class: "sw dot", style: "background:" + layerHue(id) }),
        el("span", { class: "gname", text: LAYER_LABEL[id] }),
        el("span", { class: "cnt", text: String(counts[id]) }),
      ]));
    });
    if (state.folders.size)
      box.appendChild(el("div", { class: "row nav", on: { click: function () { state.folders = new Set(); applyHighlight(); refreshLegend(); } } },
        [el("i", { class: "ph ph-x" }), el("span", { class: "gname", text: "show all layers" })]));
    box.appendChild(el("div", { class: "row note" }, [el("span", {}, ["inferred from names and imports · ",
      el("a", { href: "#/arch", text: "see Architecture" })])]));
  }
  function legend() {
    var scope = state.folderScope;
    var box = el("div", { class: "legend" + (state.legendCollapsed ? " collapsed" : "") });
    // rows inside the legend already do the right thing on click (drill in,
    // jump the path, collapse, …) — none of that should also count as
    // "clicked the canvas backdrop", which unpins whatever node is pinned.
    box.addEventListener("click", function (e) { e.stopPropagation(); });
    var grip = el("i", { class: "ph ph-dots-six-vertical legend-grip", "aria-hidden": "true", title: "Drag to move" });
    box.appendChild(el("div", { class: "legend-head" }, [
      grip,
      el("span", { class: "lk", text: state.groupBy === "layer" ? "LAYERS" : "FOLDERS" }),
      el("div", { class: "spacer" }),
      el("button", { class: "legend-toggle", "aria-label": state.legendCollapsed ? "Show legend" : "Hide legend",
        on: { click: function () {
          state.legendCollapsed = !state.legendCollapsed;
          lsSet("legendCollapsed", state.legendCollapsed);
          refreshLegend();
        } } },
        [el("i", { class: state.legendCollapsed ? "ph ph-caret-up" : "ph ph-caret-down" })]),
    ]));
    wireLegendDrag(grip, box);
    if (state.legendCollapsed) return box;
    if (hasLayers()) box.appendChild(groupSwitch());

    if (state.groupBy === "layer") legendLayers(box);
    else {
      // Dynamic, drilldown FOLDERS section: rather than one flat, repo-wide
      // list, it mirrors TREE at whatever folder is currently "open" — root by
      // default, or wherever a folder crumb / a row below last pointed. Picking
      // a row both narrows this list to that folder's own children *and*
      // isolates it in the graph (openFolder() does both, via state.folders).
      if (scope) {
        var segs = scope.split("/");
        var pathRow = el("div", { class: "legend-path" }, [
          el("span", { class: "crumb-part", text: "root", on: { click: function () { openFolder(""); } } }),
        ]);
        segs.forEach(function (s, i) {
          pathRow.appendChild(el("i", { class: "ph ph-caret-right" }));
          var last = i === segs.length - 1;
          var upto = segs.slice(0, i + 1).join("/");
          var attrs = { class: last ? "crumb-part cur" : "crumb-part", text: s };
          if (!last) attrs.on = { click: function () { openFolder(upto); } };
          pathRow.appendChild(el("span", attrs));
        });
        box.appendChild(pathRow);
      }
      var node = treeNodeAt(scope) || TREE;
      var childNames = Object.keys(node.dirs || {}).sort();
      if (childNames.length) {
        childNames.forEach(function (name) {
          var childPath = scope ? scope + "/" + name : name;
          // only a folder that directly holds files has one true colour in the
          // graph itself (see colorForPath) — a pass-through folder that holds
          // only subfolders gets a plain glyph rather than a made-up swatch.
          var hex = Object.prototype.hasOwnProperty.call(groupColor, childPath) ? groupColor[childPath] : null;
          box.appendChild(el("div", {
            class: "row nav", title: "open " + childPath,
            on: { click: function () { openFolder(childPath); } },
          }, [
            hex ? el("span", { class: "sw dot", style: "background:" + hex }) : el("i", { class: "ph ph-folder" }),
            el("span", { class: "gname", text: name }),
          ]));
        });
      } else {
        box.appendChild(el("div", { class: "row note", text: "no subfolders here" }));
        node.files.slice().sort(function (a, b) { return a.path < b.path ? -1 : 1; }).forEach(function (f) {
          box.appendChild(el("div", { class: "row nav", title: f.path, on: { click: fileAct(f) } }, [
            el("i", { class: fileIcon(f.lang) }),
            el("span", { class: "gname", text: f.path.split("/").pop() }),
          ]));
        });
      }
    }
    box.appendChild(el("div", { class: "lk", style: "margin-top:9px", text: "EDGE" }));
    box.appendChild(el("div", { class: "row" }, [
      el("span", { class: "sw", style: "background:var(--color-neutral-300)" }),
      "call · thicker = same file",
    ]));
    box.appendChild(el("div", { class: "row" }, [el("span", { class: "sw dash" }), "import / external"]));
    box.appendChild(el("div", { class: "row" }, [el("span", { class: "sw dash" }),
      "guessed call — name matched, no import proof (Function view)"]));
    if (state.flow) {
      box.appendChild(el("div", { class: "lk", style: "margin-top:9px", text: "TRAFFIC" }));
      box.appendChild(el("div", { class: "row" }, [
        el("span", { class: "sw flowsw" }), "flow · entry → callee",
      ]));
      box.appendChild(el("div", { class: "row" }, [
        el("span", { class: "sw dot", style: "background:#f5f4ff" }), "packet · more = higher fan-in",
      ]));
      box.appendChild(el("div", { class: "row" }, [
        el("span", { class: "sw dot",
          style: "background:var(--color-neutral-400);width:13px;height:13px" }),
        "bigger node = more traffic handled",
      ]));
      box.appendChild(el("div", { class: "row" }, [
        el("span", { class: "sw", style: "background:var(--color-neutral-800)" }),
        "no motion = unreachable",
      ]));
    }
    return box;
  }
  function zoombox() {
    var box = el("div", { class: "zoombox" }, [
      el("div", { class: "btns" }, [
        zbtn("ph ph-plus", "Zoom in", function () { zoomAt(1.25); }),
        zbtn("ph ph-minus", "Zoom out", function () { zoomAt(1 / 1.25); }),
        zbtn("ph ph-crosshair", "Reset view", function () { fitView(lastLay); applyView(); }),
      ]),
      el("div", { class: "cap" }),
    ]);
    setTimeout(updateCap, 0);
    return box;
  }
  function zbtn(icon, label, fn) {
    return el("button", { "aria-label": label, on: { click: fn } }, [el("i", { class: icon })]);
  }
  // Re-fit whenever the canvas box itself changes size (a window resize, the
  // rail/inspector toggling on narrow viewports, …) — the observer is bound
  // once; each wireCanvas() call below just points it at the current
  // canvasEl. No relayout, just the same cheap transform-only fit a manual
  // "Reset view" click runs.
  var canvasResizeRaf = 0;
  var canvasRO = window.ResizeObserver ? new ResizeObserver(function () {
    if (canvasResizeRaf) return;
    canvasResizeRaf = requestAnimationFrame(function () {
      canvasResizeRaf = 0;
      if (lastLay) { fitView(lastLay); applyView(); }
    });
  }) : null;
  function wireCanvas() {
    if (canvasRO) canvasRO.observe(canvasEl);
    canvasEl.addEventListener("mousedown", function (e) {
      if (e.target.closest(".gnode")) return;
      e.preventDefault();
      drag = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y };
      canvasEl.classList.add("dragging");
      if (svgEl) svgEl.style.willChange = "transform";
      freezeAnims();
    });
    canvasEl.addEventListener("click", function (e) {   // click the backdrop = unpin
      if (e.target.closest(".gnode") || lastPanMoved) return;
      if (state.pin != null) { state.pin = null; applyHighlight(); }
    });
    canvasEl.addEventListener("wheel", function (e) {
      // ctrlKey (or metaKey on some browsers/trackpads) on a wheel event is
      // how a pinch gesture — including the OS/browser's own accessibility
      // zoom — is reported. Capturing that unconditionally as "zoom the
      // graph" would silently swallow a low-vision user's page-zoom gesture
      // any time their pointer happened to be over the canvas; only a plain
      // wheel/two-finger-pan is this widget's own zoom-the-graph gesture.
      if (e.ctrlKey || e.metaKey) return;
      e.preventDefault();
      var p = vbPoint(e);
      zoomToward(p.x, p.y, e.deltaY < 0 ? 1.12 : 1 / 1.12);
      liveView(true);
      // a trackpad's smooth wheel can fire well past 60Hz — coalesce the
      // `.pz` attribute write (the expensive part) to once per paint instead
      // of once per event; the cheap state.view math above still runs live.
      if (!zoomRaf) zoomRaf = requestAnimationFrame(function () { zoomRaf = 0; applyView(); });
    }, { passive: false });
  }

  // ---- inspector --------------------------------------------
  function inspector() {
    var body = el("div", { class: "insp-body" });
    var head = el("div", { class: "insp-head" }, [
      el("i", { class: "ph-fill ph-circle k" }),
      el("span", { text: state.focus != null ? N[state.focus].file : "no selection" }),
      el("div", { class: "spacer" }),
      el("button", { class: "mobile-only mobile-close", "aria-label": "Close details",
        on: { click: function () { state.mobileInsp = false; render(); } } }, [el("i", { class: "ph ph-x" })]),
    ]);
    var foot = el("div", { class: "insp-foot" });

    if (state.focus == null) {
      body.appendChild(el("div", { class: "idle",
        text: "Pick a symbol in the tree or the graph to inspect its callers, blast radius and source." }));
      var idleDeps = DATA.dependencies || [];
      if (idleDeps.length) body.appendChild(depPanel(idleDeps));
      return el("aside", { class: "insp", id: "insp-panel", tabindex: "-1", "aria-label": "Symbol details" }, [head, body, foot]);
    }
    var n = N[state.focus];
    var reachIn = reachSet(state.focus, inAdjSure);
    var filesHit = new Set();
    reachIn.forEach(function (i) { filesHit.add(N[i].file); });
    var totalFiles = (DATA.stats && DATA.stats.files) || 1;

    if (n.explain) {
      // a symbol's own explain.terms shadows the global glossary for this card —
      // it never merges with GLOSS. what/why share one `seen` so the same term
      // isn't underlined twice back-to-back in the same two-paragraph card.
      var exTerms = n.explain.terms || {};
      var exSeen = {};
      var ex = el("div", { class: "insp-explain" }, [
        el("div", { class: "lbl", text: "WHAT THIS DOES" }),
        el("p", {}, proseNodes(n.explain.what, exTerms, exSeen)),
      ]);
      if (n.explain.why)
        ex.appendChild(el("p", { class: "why" }, proseNodes(n.explain.why, exTerms, exSeen)));
      body.appendChild(ex);
    }

    body.appendChild(el("div", { class: "statgrid" }, [
      card("FAN-IN", n.fan_in, n.fan_in_guess), card("FAN-OUT", n.fan_out, n.fan_out_guess),
      card("CHURN", n.churn), card("TIER", tierOf(n.file)),
    ]));

    var strip = anatomyStrip(n);
    if (strip) body.appendChild(strip);

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

    if (n.history) body.appendChild(historyPanel(n.history));

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

    var chain = shortestEntryChain(state.focus);
    if (chain) {
      var pathSec = el("div", { class: "section" }, [
        el("div", { class: "lbl", text: "ON PATH FROM" }),
        el("div", { class: "rowitem", style: "flex-wrap:wrap;white-space:normal", text: chainLabel(chain) }),
      ]);
      // "I see this — what made it?" — the same chain above, played instead of
      // just read. Hidden when this symbol *is* the entry point (chain of one:
      // there's nothing to walk back through).
      if (chain.length > 1)
        pathSec.appendChild(el("div", { class: "orient-link", style: "margin-top:8px",
          on: { click: function () { go("sim", "origin:" + n.key + "/0"); } } }, [
          el("div", { class: "orient-link-t" }, [el("i", { class: "ph ph-play-circle" }), " Trace back to entry ▶"]),
          el("div", { class: "orient-link-s", text: "watch this exact path get here, call by call, in Simulate" }),
        ]));
      body.appendChild(pathSec);
    }

    body.appendChild(listSection("DIRECT CALLERS", inAdj[state.focus]));
    body.appendChild(listSection("CALLS OUT TO", outAdj[state.focus]));

    var fi = fileByPath[n.file] ? fileByPath[n.file].fi : null;
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

    var fdeps = (fileByPath[n.file] || {}).deps || [];
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

    var preview = codePreview(n);
    if (preview) body.appendChild(preview);

    if (hasSource(n)) {
      var viewBtn = el("button", { class: "btn primary", text: "View source" });
      viewBtn.addEventListener("click", openSource);
      foot.appendChild(viewBtn);
    }
    foot.appendChild(el("a", { class: "btn" + (hasSource(n) ? "" : " primary"), href: editorUri(n), text: "Open in editor" }));
    var copyBtn = el("button", { class: "btn", text: "Copy key" });
    copyBtn.addEventListener("click", function () { copyText(n.key, copyBtn); });
    foot.appendChild(copyBtn);
    return el("aside", { class: "insp", id: "insp-panel", tabindex: "-1", "aria-label": "Symbol details" }, [head, body, foot]);
  }
  function card(l, v, guess) {
    var kids = [
      el("div", { class: "lbl", text: l }), el("div", { class: "val", text: v == null ? "0" : String(v) }),
    ];
    // a repo-wide name guess (no import evidence, no self/class match) is
    // never folded into the number above it — shown separately so it can't
    // be mistaken for a confident count, same reasoning as the dashed edges
    // on the canvas (see the legend).
    if (guess) kids.push(el("div", { class: "muted", text: "+" + guess + " guessed", title:
      "Also referenced by name only, with no import connecting the two files — shown, not counted." }));
    return el("div", { class: "statcard" }, kids);
  }
  // "Read the git history when a line makes no sense — someone wrote a reason
  // down" (teacher's principle 6). codemap captures intent at commit time
  // rather than guessing it after the fact, so this can show the *stated*
  // reason, not a plausible-sounding reconstruction — and says so plainly
  // when nothing was stated.
  function historyPanel(h) {
    var sec = el("div", { class: "section insp-history" }, [el("div", { class: "lbl", text: "HISTORY" })]);
    sec.appendChild(el("p", { style: "margin:0 0 4px", text:
      "Changed in " + h.commits + " commit" + (h.commits === 1 ? "" : "s") +
      (h.last_short ? " — most recently " + h.last_short + "." : ".") }));
    if (h.intent_text) {
      sec.appendChild(el("p", { class: "insp-history-why", text:
        (h.intent_source === "inferred" ? "Likely: " : "") + h.intent_text }));
    } else {
      sec.appendChild(el("p", { class: "insp-history-why muted", text:
        "No reason was recorded for that change — that's normal, not a gap in the tool." }));
    }
    return sec;
  }
  // A proportional map of the focused symbol's file: every symbol as a block at
  // its true line span, the unfilled gaps being module-level code (imports,
  // constants). Fill = kind; brightness = fan-in. Bridges the map to the source.
  var ANATOMY_KIND = { function: "#9085e9", method: "#3987e5", class: "#c98500" };
  function anatomyStrip(n) {
    var f = fileByPath[n.file];
    if (!f || !f.loc || !f.symbols.length) return null;
    var H = 190, Wd = 30, loc = f.loc;
    var syms = f.symbols.map(function (i) { return N[i]; })
      .sort(function (a, b) { return a.line[0] - b.line[0]; });
    // union of line ranges, not a plain sum — syms includes every nested
    // symbol too (a class's own span already contains its methods'), so
    // summing used to double-count and could read over 100%.
    var covered = 0, coveredEnd = -1;
    syms.forEach(function (m) {
      var s = m.line[0], e = m.line[1];
      if (s > coveredEnd) { covered += e - s + 1; coveredEnd = e; }
      else if (e > coveredEnd) { covered += e - coveredEnd; coveredEnd = e; }
    });
    var svg = el("svg", { class: "anatomy", width: Wd, height: H, viewBox: "0 0 " + Wd + " " + H });
    svg.appendChild(el("rect", { x: 0, y: 0, width: Wd, height: H, rx: 3,
      fill: "var(--color-neutral-900)" }));
    syms.forEach(function (m) {
      var y = (m.line[0] - 1) / loc * H;
      var h = Math.max(1.5, (m.line[1] - m.line[0] + 1) / loc * H);
      var base = ANATOMY_KIND[m.kind] || "#75798c";
      var a = 0.32 + Math.min(0.6, m.fan_in / 12 * 0.6);
      var isFocus = m.i === n.i;
      svg.appendChild(el("rect", { x: 0, y: y, width: Wd, height: h,
        fill: hexA(base, isFocus ? 1 : a),
        stroke: isFocus ? "#f5f4ff" : "none", "stroke-width": isFocus ? 1.4 : 0,
        on: { click: function () { go("graph", m.key); } } }));
    });
    var dots = Object.keys(ANATOMY_KIND).map(function (k) {
      return el("span", { class: "akey" }, [
        el("span", { class: "sw dot", style: "background:" + ANATOMY_KIND[k] }), k,
      ]);
    });
    return el("div", { class: "section anatomy-sec" }, [
      el("div", { class: "lbl", text: "FILE ANATOMY" }),
      el("div", { class: "anatomy-wrap" }, [
        svg,
        el("div", { class: "anatomy-meta" }, [
          el("p", {}, [el("b", { text: String(syms.length) }), " symbols span ",
            el("b", { text: Math.round(covered / loc * 100) + "%" }), " of ",
            el("b", { text: loc + " lines" }), ". Gaps are module-level code."]),
          el("div", { class: "anatomy-keys" }, dots),
          el("div", { class: "trace-link", text: "Trace calls from here →",
            on: { click: function () { go("map", "trace/" + n.key); } } }),
        ]),
      ]),
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
  // "<focus symbol>:<section label>" -> true once that section's "+N more"
  // has been opened — same idea as traceExpand above, scoped per symbol so
  // expanding one caller/callee list doesn't leave every other symbol you
  // later focus looking pre-expanded.
  var listExpand = {};
  function listSection(label, idxs) {
    var key = state.focus + ":" + label;
    // "Being lost is the normal state of reading unfamiliar code" — say so
    // plainly here instead of the old bare "none at this tier", which reads
    // like a limitation of the tool rather than a normal shape a codebase
    // takes (dynamic dispatch, a plugin registry, a caller outside the repo).
    var isCallers = label === "DIRECT CALLERS";
    function build() {
      var sec = el("div", { class: "section" }, [el("div", { class: "lbl", text: label })]);
      var uniq = Array.from(new Set(idxs || []));
      if (!uniq.length) {
        sec.appendChild(el("p", { class: "lib-missing", text: isCallers
          ? "I can't see who calls this — it may be wired up dynamically (a dispatch table, a plugin registry, code outside this repo). That's normal, not a gap in the graph."
          : "I can't see what this calls out to — it may happen dynamically. That's normal, not a gap in the graph." }));
        return sec;
      }
      var open = listExpand[key];
      var take = open ? uniq : uniq.slice(0, 14);
      take.forEach(function (i) {
        var n = N[i];
        var via = viaEdge[isCallers ? i + ":" + state.focus : state.focus + ":" + i];
        sec.appendChild(el("div", { class: "rowitem is-link",
          on: { click: function () { go("graph", n.key); } } }, [
          el("span", { class: "rk", text: kindLabel(n.kind) }),
          el("span", { class: "rn", text: n.qual }),
          via ? el("span", { class: "cbm-tag", text: "cbm", title:
            "Found by codebase-memory-mcp (" + (via.engine_strategy || "resolved call") +
            (via.engine_score != null ? ", score " + via.engine_score : "") +
            "); codemap's own analysis had no link here." }) : null,
          el("span", { class: "rc", text: n.file.split("/").pop() }),
        ]));
      });
      // rebuilt in place (not a full render()) so opening the list doesn't
      // reset the inspector's scroll position the way navigating away and
      // back would.
      if (uniq.length > 14 && !open)
        sec.appendChild(el("div", { class: "rowitem is-link muted",
          on: { click: function () { listExpand[key] = true; sec.replaceWith(build()); } } },
          ["+" + (uniq.length - 14) + " more"]));
      return sec;
    }
    return build();
  }
  // Shortest chain, forward edges only, from *some* entry point down to
  // `target` — the shortest across every entry, BFS'd independently since the
  // graph is unweighted. One BFS feeds both the inspector's "ON PATH FROM"
  // label (chainLabel() below) and a reverse scenario (originScenario(),
  // further down): "I see this — what made it?" is that same chain, played
  // instead of just read. Confident edges only (outAdjSure) — this is what
  // "Trace back to entry ▶" then *plays*, so a guessed edge here used to be
  // able to walk a whole false call chain and present it as the real path.
  function shortestEntryChain(target) {
    var entries = N.filter(function (n) { return n.entry.length; }).map(function (n) { return n.i; });
    if (!entries.length) return null;
    var best = null;
    entries.forEach(function (st) {
      var prev = new Map([[st, -1]]);
      var q = [st];
      while (q.length) {
        var u = q.shift();
        if (u === target) break;
        (outAdjSure[u] || []).forEach(function (v) { if (!prev.has(v)) { prev.set(v, u); q.push(v); } });
      }
      if (!prev.has(target)) return;
      var chain = [], c = target;
      while (c !== -1) { chain.unshift(c); c = prev.get(c); }
      if (!best || chain.length < best.length) best = chain;
    });
    return best;
  }
  function chainLabel(chain) {
    var label = (N[chain[0]].entry[0] || "").split(":").slice(1).join(":") || N[chain[0]].name;
    return label + " -> " + chain.map(function (i) { return N[i].name; }).join(" -> ");
  }
  function editorUri(n) {
    var root = (DATA.root || "").replace(/\\/g, "/");
    if (root && !/^\//.test(root)) root = "/" + root;
    // encodeURI, not encodeURIComponent: it leaves "/" and the drive-letter
    // ":" alone while still escaping spaces — real in a repo path like
    // "…/My Project/…", which would otherwise truncate the vscode:// URI.
    return encodeURI("vscode://file" + root + "/" + n.file + ":" + n.line[0]);
  }


  // ---- source viewer --------------------------------------------------------
  // A slide-over on the Graph stage: the focused symbol's whole file, line
  // numbered and highlighted, with the symbol's range marked. Definitions and
  // call sites inside it link back into the graph — the reason to read code
  // here rather than in an editor. Built from DATA.sources; a file that missed
  // the embed budget shows the symbol's own excerpt instead. The highlighter is
  // deliberately small (comments, strings, numbers, keywords, names) and local:
  // no CDN script, so the page still works offline.
  //
  // Every piece of source text reaches the DOM through escHtml() (a whole file
  // is thousands of rows — building them as nodes is the slow path), and the
  // only other markup is fixed strings and numeric symbol indices.
  function kwSet(words) {
    var o = {};
    words.split(" ").forEach(function (w) { o[w] = 1; });
    return o;
  }
  var HL_KW = {
    py: kwSet("and as assert async await break case class continue def del elif else except finally for from " +
      "global if import in is lambda match nonlocal not or pass raise return try while with yield self cls " +
      "True False None"),
    js: kwSet("abstract as async await break case catch class const continue debugger declare default delete do " +
      "else enum export extends false finally for from function get if implements import in instanceof " +
      "interface let namespace new null of private protected public readonly return set static super switch " +
      "this throw true try type typeof undefined var void while with yield"),
    c: kwSet("abstract as async await bool break case catch char class const continue default defer delete do " +
      "double else enum export extends extern false final finally float fn for func go goto if impl import " +
      "in include instanceof int interface let long loop match mod mut namespace new nil null override package " +
      "private protected pub public readonly ref return self short signed static struct super switch this " +
      "throw trait true try type typedef typeof union unsafe unsigned use using var virtual void volatile " +
      "where while yield"),
    rb: kwSet("alias and begin break case class def defined do else elsif end ensure false for if in module " +
      "next nil not or redo rescue retry return self super then true undef unless until when while yield " +
      "require require_relative attr_accessor attr_reader attr_writer"),
  };
  var HL_SLASH = { line: ["//"], block: ["/*", "*/"], kw: "c" };
  var HL_SPEC = {
    python: { line: ["#"], tri: true, kw: "py" },
    ruby: { line: ["#"], kw: "rb" },
    php: { line: ["//", "#"], block: ["/*", "*/"], kw: "c" },
    javascript: { line: ["//"], block: ["/*", "*/"], tpl: true, kw: "js" },
    typescript: { line: ["//"], block: ["/*", "*/"], tpl: true, kw: "js" },
    tsx: { line: ["//"], block: ["/*", "*/"], tpl: true, kw: "js" },
    go: { line: ["//"], block: ["/*", "*/"], tpl: true, kw: "c" },
  };
  var HL_DEF_WORD = kwSet("def class function fn func struct enum interface trait type impl module namespace");
  // sticky: String.match() with these reads from `lastIndex`, i.e. "does a token start exactly here"
  var HL_RE_ID = /[A-Za-z_$][\w$]*/y;
  var HL_RE_NUM = /0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
  var HL_BS = String.fromCharCode(92);
  function escHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // one html string per source line. A block comment / triple-quoted / template
  // string that is still open at the end of a line carries into the next one.
  function highlightLines(lines, lang) {
    var spec = HL_SPEC[lang] || HL_SLASH, kw = HL_KW[spec.kw];
    var carry = null, out = [];
    lines.forEach(function (s) {
      var h = "", pl = "", i = 0, n = s.length, afterDef = false;
      function flush() { if (pl) { h += escHtml(pl); pl = ""; } }
      function tok(cls, t) { flush(); h += '<span class="' + cls + '">' + escHtml(t) + "</span>"; }
      if (carry) {
        var j0 = s.indexOf(carry.end);
        if (j0 < 0) { out.push(s ? '<span class="' + carry.cls + '">' + escHtml(s) + "</span>" : ""); return; }
        tok(carry.cls, s.slice(0, j0 + carry.end.length));
        i = j0 + carry.end.length;
        carry = null;
      }
      while (i < n) {
        var c = s.charAt(i), m, k;
        var isLine = false;
        for (k = 0; k < spec.line.length; k++) if (s.startsWith(spec.line[k], i)) { isLine = true; break; }
        if (isLine) { tok("c", s.slice(i)); i = n; break; }
        if (spec.block && s.startsWith(spec.block[0], i)) {
          var eb = s.indexOf(spec.block[1], i + spec.block[0].length);
          if (eb < 0) { tok("c", s.slice(i)); carry = { end: spec.block[1], cls: "c" }; i = n; }
          else { tok("c", s.slice(i, eb + spec.block[1].length)); i = eb + spec.block[1].length; }
          continue;
        }
        if (spec.tri && (s.startsWith('"""', i) || s.startsWith("'''", i))) {
          var q3 = s.substr(i, 3), e3 = s.indexOf(q3, i + 3);
          if (e3 < 0) { tok("s", s.slice(i)); carry = { end: q3, cls: "s" }; i = n; }
          else { tok("s", s.slice(i, e3 + 3)); i = e3 + 3; }
          continue;
        }
        if (c === '"' || c === "'" || (c === "`" && spec.tpl)) {
          var j = i + 1;
          while (j < n && s.charAt(j) !== c) j += s.charAt(j) === HL_BS ? 2 : 1;
          if (j >= n && c === "`") { tok("s", s.slice(i)); carry = { end: "`", cls: "s" }; i = n; }
          else { tok("s", s.slice(i, j + 1)); i = Math.min(j + 1, n); }
          continue;
        }
        if (c >= "0" && c <= "9" && !/[\w$]/.test(i ? s.charAt(i - 1) : " ")) {
          HL_RE_NUM.lastIndex = i;
          m = s.match(HL_RE_NUM);
          if (m) { tok("n", m[0]); i += m[0].length; afterDef = false; continue; }
        }
        if (c === "@" && /[A-Za-z_]/.test(s.charAt(i + 1))) {
          HL_RE_ID.lastIndex = i + 1;
          m = s.match(HL_RE_ID);
          if (m) { tok("d", "@" + m[0]); i += 1 + m[0].length; afterDef = false; continue; }
        }
        if (/[A-Za-z_$]/.test(c)) {
          HL_RE_ID.lastIndex = i;
          m = s.match(HL_RE_ID);
          var w = m[0];
          if (kw[w]) { tok("k", w); afterDef = !!HL_DEF_WORD[w]; }
          else if (afterDef) { tok("f", w); afterDef = false; }
          else if (s.charAt(i + w.length) === "(") { tok("m", w); }
          else if (/^[A-Z]/.test(w)) { tok("t", w); }
          else pl += w;
          i += w.length;
          continue;
        }
        if (c !== " " && c !== "\t") afterDef = false;
        pl += c;
        i++;
      }
      flush();
      out.push(h);
    });
    return out;
  }
  var hlCache = {};   // file index -> highlighted html per line (whole files only)
  function fileHtml(f) {
    if (!hlCache[f.fi]) hlCache[f.fi] = highlightLines(fileLines(f.fi), f.lang);
    return hlCache[f.fi];
  }
  function hasSource(n) {
    var f = fileByPath[n.file];
    return !!((f && fileLines(f.fi)) || n.excerpt);
  }
  function symSpan(m) { return m.line[1] - m.line[0]; }
  // parse an html string (already escaped, see above) into `host`
  function setHtml(host, html) {
    host.textContent = "";
    host.appendChild(document.createRange().createContextualFragment(html));
  }

  // the inspector's short preview of a symbol: numbered + highlighted, first
  // PREVIEW_LINES lines, with a way into the full viewer
  var PREVIEW_LINES = 30;
  function codePreview(n) {
    var text = srcOf(n);
    if (!text) return null;
    var f = fileByPath[n.file], lines = text.split("\n");
    var shown = lines.slice(0, PREVIEW_LINES);
    var html = highlightLines(shown, f ? f.lang : "");
    var pre = el("pre", { class: "excerpt code" });
    setHtml(pre, html.map(function (h, i) {
      return '<span class="no">' + (n.line[0] + i) + "</span>" + h;
    }).join("\n"));
    var kids = [
      el("div", { class: "lbl", text: n.file + ":" + n.line[0] + "-" + n.line[1] }),
      pre,
    ];
    if (lines.length > shown.length)
      kids.push(el("div", { class: "src-more",
        text: "+" + (lines.length - shown.length) + " more lines — view source",
        on: { click: function () { openSource(); } } }));
    return el("div", { class: "section" }, kids);
  }

  function openSource() {
    if (state.focus == null || !hasSource(N[state.focus])) return;
    state.srcOpen = true;
    history.replaceState(null, "", "#/graph/" + encodeURIComponent(N[state.focus].key) + "/src");
    syncSrc();
  }
  function closeSource() {
    state.srcOpen = false;
    if (state.focus != null)
      history.replaceState(null, "", "#/graph/" + encodeURIComponent(N[state.focus].key));
    syncSrc();
  }
  // One Esc key, one job at a time — closes whichever of these is actually
  // open, nearest/most-modal first, and never more than one per press. The
  // palette has its own capture-phase handler (below) that always runs
  // first regardless of registration order, since capture always precedes
  // bubble; this covers everything Esc should close once the palette isn't
  // the thing on top.
  window.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || paletteOpen) return;
    if (state.srcOpen && state.tab === "graph") { closeSource(); return; }
    if (state.mobileRail || state.mobileInsp) {
      state.mobileRail = false; state.mobileInsp = false; render(); return;
    }
    if (activeTip) { hideTip(); return; }
    if (state.pin != null) { state.pin = null; applyHighlight(); }
  });

  function srcViewer(n) {
    var f = fileByPath[n.file] || null;
    var whole = !!(f && fileLines(f.fi));
    var first = whole ? 1 : n.line[0];
    var htmls = whole ? fileHtml(f)
      : n.excerpt ? highlightLines(n.excerpt.split("\n"), f ? f.lang : "") : null;
    var syms = whole ? f.symbols.map(function (i) { return N[i]; }) : [n];

    var head = el("div", { class: "src-head" }, [
      el("i", { class: fileIcon(f ? f.lang : "") }),
      el("div", { class: "src-title" }, [
        el("span", { class: "src-path", text: n.file }),
        el("span", { class: "src-sub", text: n.qual + " · lines " + n.line[0] + "–" + n.line[1] +
          (f && f.loc ? " · " + f.loc + " lines in file" : "") }),
      ]),
      el("div", { class: "spacer" }),
      el("a", { class: "btn", href: editorUri(n), text: "Open in editor" }),
      el("button", { class: "src-close", "aria-label": "Close source", title: "Close (Esc)",
        on: { click: closeSource } }, [el("i", { class: "ph ph-x" })]),
    ]);
    var kids = [head];
    if (!whole && htmls)
      kids.push(el("div", { class: "src-note",
        text: "The whole file isn't embedded (over the max_source_bytes budget, or minified) — this is the symbol's own excerpt." }));

    var body = el("div", { class: "srcbody", tabindex: "0", role: "region", "aria-label": "Source of " + n.file });
    if (!htmls) {
      body.appendChild(el("div", { class: "src-empty",
        text: "No source is embedded for this symbol. Use Open in editor to read it." }));
      kids.push(el("div", { class: "srcwrap" }, [body]));
      return el("aside", { class: "srcpane", "aria-label": "Source viewer" }, kids);
    }

    // where a definition starts / a call happens, by absolute line
    var defAt = {}, callsAt = {};
    syms.forEach(function (m) {
      var l = m.line[0];
      if (!(l in defAt) || symSpan(m) < symSpan(N[defAt[l]])) defAt[l] = m.i;
      (outCalls[m.i] || []).forEach(function (c) {
        if (!c.line) return;
        var a = callsAt[c.line] || (callsAt[c.line] = []);
        if (a.indexOf(c.t) < 0) a.push(c.t);
      });
    });
    var rows = [];
    for (var k = 0; k < htmls.length; k++) {
      var no = first + k;
      var cls = "sl" + (no >= n.line[0] && no <= n.line[1] ? " in" : "") + (no === n.line[0] ? " top" : "");
      var mk = defAt[no] != null
        ? '<button class="dm' + (defAt[no] === n.i ? " on" : "") + '" data-s="' + defAt[no] +
          '" title="Show in the graph" aria-label="Show in the graph"></button>' : "";
      var chips = "";
      (callsAt[no] || []).slice(0, 3).forEach(function (t) {
        chips += '<button class="cl" data-s="' + t + '" title="Go to ' + escHtml(N[t].qual) + '">' +
          escHtml(N[t].name) + " &#8599;</button>";
      });
      rows.push('<div class="' + cls + '" data-l="' + no + '"><span class="mk">' + mk + '</span><span class="no">' +
        no + '</span><span class="cd">' + (htmls[k] || " ") + chips + "</span></div>");
    }
    setHtml(body, rows.join(""));
    body.addEventListener("click", function (e) {
      var t = e.target.closest && e.target.closest("[data-s]");
      if (t) go("graph", N[+t.getAttribute("data-s")].key);
    });

    var wrapKids = [body];
    if (whole) {
      // a strip of the file: every symbol at its true position (same idea as the
      // inspector's anatomy), a viewport marker, click to jump
      var total = htmls.length;
      var map = el("div", { class: "srcmap", "aria-hidden": "true", title: "Click to jump" });
      syms.forEach(function (m) {
        map.appendChild(el("i", { class: "sm" + (m.i === n.i ? " on" : ""),
          style: "top:" + ((m.line[0] - 1) / total * 100) + "%;height:" +
            Math.max(0.5, (m.line[1] - m.line[0] + 1) / total * 100) + "%;background:" +
            (ANATOMY_KIND[m.kind] || "#75798c") }));
      });
      var view = el("b", { class: "sm-view" });
      map.appendChild(view);
      var place = function () {
        var h = body.scrollHeight || 1;
        view.style.top = (body.scrollTop / h * 100) + "%";
        view.style.height = Math.min(100, body.clientHeight / h * 100) + "%";
      };
      body.addEventListener("scroll", place);
      map.addEventListener("click", function (e) {
        var r = map.getBoundingClientRect();
        body.scrollTop = (e.clientY - r.top) / r.height * body.scrollHeight - body.clientHeight / 2;
      });
      wrapKids.push(map);
    }
    kids.push(el("div", { class: "srcwrap" }, wrapKids));
    return el("aside", { class: "srcpane", "aria-label": "Source viewer" }, kids);
  }

  // Mount / refresh / remove the pane so it always matches state.srcOpen and the
  // focused symbol. Called after every graph render and focus change.
  function syncSrc() {
    var stageEl = canvasEl && canvasEl.closest(".stage");
    if (!stageEl) return;
    var old = stageEl.querySelector(".srcpane");
    var want = state.srcOpen && state.tab === "graph" && state.focus != null && hasSource(N[state.focus]);
    stageEl.classList.toggle("src-open", !!want);
    if (!want) {
      if (old) old.remove();
      return;
    }
    var n = N[state.focus], pane = srcViewer(n);
    if (old) old.replaceWith(pane);
    else stageEl.appendChild(pane);
    // one-shot: a file-text search hit wants the exact matched line, not the
    // anchor symbol's own first line — consumed here so it never leaks into
    // a later, unrelated open of the same pane.
    var targetLine = state.srcTargetLine != null ? state.srcTargetLine : n.line[0];
    state.srcTargetLine = null;
    var body = pane.querySelector(".srcbody"), top = pane.querySelector('.sl[data-l="' + targetLine + '"]');
    if (body && top) body.scrollTop = Math.max(0, top.offsetTop - body.clientHeight * 0.22);
    if (body) body.dispatchEvent(new Event("scroll"));
  }

  // ---- palette --------------------------------------------
  var paletteOpen = false;
  // symbols matching `q`: by name / file / docstring first (in file order), then
  // by text inside their code. The full symbol text is lower-cased once per
  // symbol — this runs on every keystroke.
  var lcSrc = {};
  function srcLower(n) {
    if (!(n.i in lcSrc)) { var s = srcOf(n); lcSrc[n.i] = s ? s.toLowerCase() : ""; }
    return lcSrc[n.i];
  }
  function searchNodes(q) {
    var named = [], inText = [];
    N.forEach(function (n) {
      if ((n.qual + " " + n.file).toLowerCase().indexOf(q) >= 0
        || (n.doc && n.doc.toLowerCase().indexOf(q) >= 0)) named.push(n);
      else if (srcLower(n).indexOf(q) >= 0) inText.push(n);
    });
    // a hit inside code: the innermost symbol first, not the class or long function around it
    inText.sort(function (a, b) { return symSpan(a) - symSpan(b); });
    return named.concat(inText);
  }
  // Every embedded file's lines that fall OUTSIDE any symbol's own range —
  // imports, module-level constants, a docstring at the top, config files
  // with no captured symbols at all. searchNodes() above only ever looks
  // inside a symbol's own source, so this text used to be unsearchable even
  // though it's sitting right there, embedded, on screen. Computed once per
  // file and cached, like srcLower() above.
  var fileGapCache = {};
  function fileGapLines(f) {
    if (!(f.fi in fileGapCache)) {
      var lines = fileLines(f.fi), out = [];
      if (lines) {
        var covered = new Array(lines.length + 1);
        f.symbols.forEach(function (si) {
          var n = N[si];
          for (var ln = Math.max(1, n.line[0]); ln <= Math.min(lines.length, n.line[1]); ln++) covered[ln] = true;
        });
        for (var i = 1; i <= lines.length; i++) {
          var text = lines[i - 1];
          if (!covered[i] && text.trim()) out.push({ line: i, text: text.trim() });
        }
      }
      fileGapCache[f.fi] = out;
    }
    return fileGapCache[f.fi];
  }
  // one hit per matching file (the first gap line it matches on) — a big
  // config file matching on every line would otherwise flood the results.
  function searchFiles(q) {
    var hits = [];
    FILES.forEach(function (f) {
      var gaps = fileGapLines(f);
      for (var i = 0; i < gaps.length; i++) {
        if (gaps[i].text.toLowerCase().indexOf(q) >= 0) {
          hits.push({ kind: "file", file: f, line: gaps[i].line, text: gaps[i].text });
          break;
        }
      }
    });
    return hits;
  }
  // the symbol whose own range is closest to `line` — used to anchor the
  // (symbol-centric) source viewer on a gap-line hit that belongs to no
  // symbol itself.
  function nearestSymbolTo(f, line) {
    var best = null, bestD = Infinity;
    (f.symbols || []).forEach(function (si) {
      var n = N[si];
      var d = line < n.line[0] ? n.line[0] - line : line > n.line[1] ? line - n.line[1] : 0;
      if (d < bestD) { bestD = d; best = si; }
    });
    return best;
  }
  function openPalette() {
    if (paletteOpen) return;
    paletteOpen = true;
    var restoreFocus = document.activeElement;
    var sel = 0, matches = N.slice(0, 60);
    var input = el("input", { placeholder: "symbol, file, or text you saw on screen…", spellcheck: "false",
      "aria-label": "Find anything you saw on screen", role: "combobox", "aria-expanded": "true" });
    var list = el("ul");
    // "text on a button exists somewhere in the files — that's your entry
    // point into anything" (teacher's principle 6). This box only ever
    // searches what's actually in the page (whole files up to [explore]
    // max_source_bytes, capped excerpts past that), so a real miss still
    // deserves a plain explanation rather than reading as "that text doesn't exist".
    var missText = "No match in any symbol name, file, docstring, or the source in this page";
    missText += unembeddedFileCount > 0
      ? " — it may be in one of the " + unembeddedFileCount + " file" +
        (unembeddedFileCount === 1 ? "" : "s") + " too large to embed (see max_source_bytes)."
      : ".";
    var empty = el("li", { class: "palette-empty", text: missText, hidden: true });
    var back = el("div", { class: "palette-back", role: "dialog", "aria-modal": "true",
      on: { click: function (e) { if (e.target === back) closeP(); } } },
      [el("div", { class: "palette" }, [input, list])]);
    // one focusable control (the input) — Escape and Tab are handled on
    // `document` in the capture phase so they work no matter where focus
    // actually is, and closing always gives focus back to whatever opened
    // the palette (the ⌘K shortcut can fire from anywhere on the page).
    function onDocKeydown(e) {
      if (e.key === "Escape") { e.preventDefault(); closeP(); }
      else if (e.key === "Tab") { e.preventDefault(); input.focus(); }
    }
    function closeP() {
      paletteOpen = false;
      document.removeEventListener("keydown", onDocKeydown, true);
      document.body.removeChild(back);
      if (restoreFocus && typeof restoreFocus.focus === "function") restoreFocus.focus();
    }
    // the first line in the doc or the symbol's source that contains the query
    // — matched text you saw on screen, not just a symbol you already know
    // the name of. This is "what's in the page", not "the whole repo" (the
    // empty state above says so).
    function findMatchLine(n, q) {
      if (n.doc && n.doc.toLowerCase().indexOf(q) >= 0)
        return n.doc.trim().split("\n")[0];
      var src = srcOf(n);
      if (src) {
        var lines = src.split("\n");
        for (var i = 0; i < lines.length; i++)
          if (lines[i].toLowerCase().indexOf(q) >= 0) return lines[i].trim();
      }
      return null;
    }
    function refresh() {
      var q = input.value.toLowerCase().trim();
      // symbol matches first (named, then in-text), file-level text last —
      // a file whose match is inside a symbol already surfaced that symbol.
      matches = (q ? searchNodes(q).concat(searchFiles(q)) : N).slice(0, 60);
      sel = 0;
      clear(list);
      if (!matches.length) { list.appendChild(empty); empty.hidden = false; return; }
      matches.forEach(function (hit, i) {
        var isFile = hit.kind === "file";
        var row = isFile
          ? el("div", { class: "palette-row" }, [
              el("span", { class: "rk", text: "file" }),
              el("span", { text: hit.file.path.split("/").pop() }),
              el("span", { class: "pth", text: hit.file.path }),
            ])
          : el("div", { class: "palette-row" }, [
              el("span", { class: "rk", text: kindLabel(hit.kind) }),
              el("span", { text: hit.qual }),
              el("span", { class: "pth", text: hit.file }),
            ]);
        var kids = [row];
        if (isFile) {
          kids.push(el("div", { class: "palette-hit",
            text: hit.text.length > 90 ? hit.text.slice(0, 90) + "…" : hit.text }));
        } else {
          var nameHit = (hit.qual + " " + hit.file).toLowerCase().indexOf(q) >= 0;
          if (q && !nameHit) {
            var line = findMatchLine(hit, q);
            if (line) kids.push(el("div", { class: "palette-hit",
              text: line.length > 90 ? line.slice(0, 90) + "…" : line }));
          }
        }
        list.appendChild(el("li", { class: i === sel ? "on" : "",
          on: { click: function () { pick(hit); } } }, kids));
      });
    }
    function pick(hit) {
      closeP();
      if (hit.kind !== "file") { go("graph", hit.key); return; }
      // anchor the (symbol-centric) source viewer on whichever symbol sits
      // closest to the matched line, then scroll straight to that line —
      // the file itself may have no symbols at all, so this can fail.
      var si = nearestSymbolTo(hit.file, hit.line);
      if (si == null) { state.fileScope = hit.file.fi; go("graph"); return; }
      state.srcTargetLine = hit.line;
      go("graph", N[si].key + "/src");
    }
    function mark() {
      Array.prototype.forEach.call(list.children, function (li, i) {
        li.className = i === sel ? "on" : "";
        if (i === sel) li.scrollIntoView({ block: "nearest" });
      });
    }
    input.addEventListener("input", refresh);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, matches.length - 1); mark(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); mark(); }
      else if (e.key === "Enter" && matches[sel]) pick(matches[sel]);
    });
    document.addEventListener("keydown", onDocKeydown, true);
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
      // "#/timeline/<sha>" deep link — accept either the full sha or the
      // short form shown on the card, since that's what a viewer would
      // actually copy.
      var deepLinked = state.timelineSha && (c.sha === state.timelineSha || c.short === state.timelineSha);
      var bars = el("div", { class: "tl-bars" });
      [["s", c.counts.structural, "structural"], ["b", c.counts.behavioral, "behavioral"],
       ["c", c.counts.cosmetic, "cosmetic"]].forEach(function (p) {
        if (!p[1]) return;
        // clamp so one outsized commit can't push the bar past the 780px
        // .tl-wrap column — the label text next to it already carries the
        // exact count, the bar only needs to convey relative scale.
        bars.appendChild(el("span", { class: "tl-bar " + p[0],
          style: "width:" + Math.min(220, 8 + p[1] * 3) + "px" }));
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
      wrap.appendChild(el("div", { id: "tl-" + c.sha,
        class: "tl-item" + (touched ? " touch" : "") + (deepLinked ? " hl" : "") }, kids));
    });
    if (!(DATA.timeline || []).length)
      wrap.appendChild(el("div", { class: "tl-empty", role: "status",
        text: "No non-cosmetic commits yet — the timeline fills in as the repo changes." }));
    return el("div", { class: "tl" }, [wrap]);
  }

  // ---- map tab: structure views ----------------------------------------
  // Three static, laid-out-once views (no pan/zoom, they scroll). Each builder
  // returns { controls, content, legend }; mapTab() frames them.
  function lerpHex(a, b, t) {
    t = Math.max(0, Math.min(1, t));
    function ch(hex, o) { return parseInt(hex.substr(o, 2), 16); }
    var r = Math.round(ch(a, 1) + (ch(b, 1) - ch(a, 1)) * t);
    var g = Math.round(ch(a, 3) + (ch(b, 3) - ch(a, 3)) * t);
    var bl = Math.round(ch(a, 5) + (ch(b, 5) - ch(a, 5)) * t);
    return "rgb(" + r + "," + g + "," + bl + ")";
  }
  var CHURN_LO = "#322c4d", CHURN_HI = "#b5abfc";   // one-hue sequential ramp
  var churnLogMax = Math.log1p(churnMax);
  function churnFill(fi) {
    // log scale: per-file churn is long-tailed (one outlier at churnMax), so a
    // linear ramp would crush every ordinary file into the dark end.
    return lerpHex(CHURN_LO, CHURN_HI, Math.log1p(churnOf[fi] || 0) / churnLogMax);
  }
  // trim an SVG label to a pixel width (mono ≈ 6.6px/char at our sizes)
  function fitText(s, w, cpx) {
    var max = Math.max(1, Math.floor(w / (cpx || 6.6)));
    return s.length <= max ? s : s.slice(0, Math.max(1, max - 1)) + "…";
  }
  // Same char-width estimate as fitText, but wraps onto up to 2 lines instead
  // of ellipsis-truncating to 1 — an authored architecture title shouldn't
  // get cut off just because the box is a fixed width. Only truly extreme
  // input still ellipses, on the second line, as a last resort.
  function wrapTitle(s, w, cpx) {
    var max = Math.max(1, Math.floor(w / (cpx || 6.6)));
    if (s.length <= max) return [s];
    var breakAt = s.lastIndexOf(" ", max);
    if (breakAt < Math.floor(max * 0.4)) breakAt = max;   // no good word break nearby — hard-split
    var line1 = s.slice(0, breakAt).trim();
    var rest = s.slice(breakAt).trim();
    var line2 = rest.length <= max ? rest : rest.slice(0, Math.max(1, max - 1)) + "…";
    return [line1, line2];
  }

  // squarified treemap (Bruls/Huizing/van Wijk), compact recursion
  function squarify(data, x0, y0, w0, h0) {
    var res = [];
    var items = data.filter(function (d) { return d.value > 0; })
      .sort(function (a, b) { return b.value - a.value; });
    var sum = items.reduce(function (s, d) { return s + d.value; }, 0);
    if (sum <= 0 || w0 <= 0 || h0 <= 0) return res;
    place(items.map(function (d) { return { d: d, v: d.value / sum * w0 * h0 }; }), x0, y0, w0, h0);
    function ratio(row, side) {
      var s = row.reduce(function (a, r) { return a + r.v; }, 0);
      var mx = Math.max.apply(null, row.map(function (r) { return r.v; }));
      var mn = Math.min.apply(null, row.map(function (r) { return r.v; }));
      var t = s / side;
      return Math.max((t * t) / mn, mx / (t * t));
    }
    function place(vals, x, y, w, h) {
      if (!vals.length) return;
      var side = Math.min(w, h), row = [], rest = vals.slice(), best = Infinity;
      while (rest.length) {
        var cand = row.concat([rest[0]]);
        var wr = ratio(cand, side);
        if (row.length && wr > best) break;
        row = cand; rest.shift(); best = wr;
      }
      var rs = row.reduce(function (a, r) { return a + r.v; }, 0);
      if (w >= h) {
        var rw = rs / h, oy = y;
        row.forEach(function (r) { var rh = r.v / rs * h; res.push({ item: r.d, x: x, y: oy, w: rw, h: rh }); oy += rh; });
        place(rest, x + rw, y, w - rw, h);
      } else {
        var rh2 = rs / w, ox = x;
        row.forEach(function (r) { var rw2 = r.v / rs * w; res.push({ item: r.d, x: ox, y: y, w: rw2, h: rh2 }); ox += rw2; });
        place(rest, x, y + rh2, w, h - rh2);
      }
    }
    return res;
  }

  function mapTab() {
    var built = state.mapView === "trace" ? runTrace()
      : state.mapView === "mass" ? massMap()
      : layerCake();
    var seg = el("div", { class: "seg" },
      [["layers", "Layers"], ["trace", "Run trace"], ["mass", "Mass"]].map(function (v) {
        return el("button", { class: state.mapView === v[0] ? "on" : "",
          on: { click: function () { go("map", v[0]); } } }, [v[1]]);
      }));
    var bar = el("div", { class: "map-bar" },
      [seg].concat(built.controls || [], [el("div", { class: "spacer" })]));
    return el("div", { class: "map" }, [
      bar,
      el("div", { class: "map-body" }, [
        el("div", { class: "map-canvas" }, [built.content]),
        built.legend || null,
      ]),
    ]);
  }

  // ── 1. Layer cake ── "what shape is this system" ─────────────────────
  function layerCake() {
    var hide = state.hideTests;
    var visible = FILES.filter(function (f) {
      return !(hide && isTestPath(f.path));
    });
    var vis = {};
    visible.forEach(function (f) { vis[f.fi] = true; });

    // one block per file, or one merged block per import cycle with >1 shown file
    var byComp = {};
    visible.forEach(function (f) { (byComp[fileComp[f.fi]] = byComp[fileComp[f.fi]] || []).push(f); });
    var blocks = [];
    Object.keys(byComp).forEach(function (cid) {
      var fs = byComp[cid].slice().sort(function (a, b) { return b.loc - a.loc; });
      var loc = fs.reduce(function (s, f) { return s + (f.loc || 0); }, 0);
      var cyc = sccMembers[cid].length > 1;
      blocks.push({
        id: "b" + cid, files: fs, loc: loc, layer: compLayer[cid], cyc: cyc && fs.length > 1,
        hue: colorForPath(fs[0].path),
        label: cyc && fs.length > 1 ? fs.length + " files in a cycle" : fs[0].path.split("/").pop(),
      });
    });

    var layers = [];
    blocks.forEach(function (b) { if (layers.indexOf(b.layer) < 0) layers.push(b.layer); });
    layers.sort(function (a, b) { return b - a; });   // highest layer at the top

    var PAD = 16, ROWH = 78, GAP = 4, MINW = 48, LBLW = 34;
    var band = {};
    var maxBandLoc = 1;
    layers.forEach(function (L) {
      band[L] = blocks.filter(function (b) { return b.layer === L; })
        .sort(function (a, b) { return b.loc - a.loc; });
      var s = band[L].reduce(function (a, b) { return a + b.loc; }, 0);
      if (s > maxBandLoc) maxBandLoc = s;
    });
    var avail = 980 - LBLW - PAD * 2;
    var pxPerLoc = Math.min(0.6, avail / maxBandLoc);
    var innerW = layers.reduce(function (mx, L) {
      var w = band[L].reduce(function (a, b) { return a + Math.max(MINW, b.loc * pxPerLoc) + GAP; }, 0);
      return Math.max(mx, w);
    }, 0);
    var W = LBLW + PAD * 2 + innerW;
    var H = PAD * 2 + layers.length * ROWH;

    var pos = {};   // block id -> {cx, cy}
    var svg = el("svg", { class: "cake", width: W, height: H, viewBox: "0 0 " + W + " " + H });
    var edgeLayer = el("g", { class: "cake-edges", fill: "none" });
    svg.appendChild(edgeLayer);

    layers.forEach(function (L, row) {
      var y = PAD + row * ROWH;
      svg.appendChild(el("text", { x: LBLW - 8, y: y + ROWH / 2, "text-anchor": "end",
        "dominant-baseline": "middle", class: "cake-lnum", text: "L" + L }));
      var x = LBLW + PAD;
      band[L].forEach(function (b) {
        var w = Math.max(MINW, b.loc * pxPerLoc), h = ROWH - 22;
        pos[b.id] = { cx: x + w / 2, cy: y + h / 2, x: x, y: y, w: w, h: h };
        var titleTxt = b.cyc
          ? "import cycle: " + b.files.map(function (f) { return f.path.split("/").pop(); }).join(" ⇄ ")
          : b.files[0].path + " · " + b.loc + " loc";
        var gEl = el("g", { class: "cake-block" + (b.cyc ? " cyc" : ""),
          on: {
            mouseenter: function () { drawCakeEdges(b); },
            mouseleave: function () { clear(edgeLayer); },
            click: function () { fileAct(b.files[0])(); },
          } }, [el("title", { text: titleTxt })]);
        gEl.appendChild(el("rect", { x: x, y: y, width: w, height: h, rx: 5,
          fill: hexA(b.hue, b.cyc ? 0.16 : 0.24), stroke: b.hue,
          "stroke-width": b.cyc ? 2 : 1.1 }));
        if (b.cyc)
          gEl.appendChild(el("text", { x: x + 7, y: y + 15, class: "cake-cyc", text: "↺" }));
        var cakeLbl = fitText(b.cyc ? "cycle · " + b.files.length : b.label, w - 10);
        // a block too narrow to fit at least ~6 real characters just shows a
        // 1-2 letter fragment ("f…") that reads as noise, not a name — skip
        // painting it and rely on the <title> (already on gEl) for hover/tap.
        if (w > 34 && cakeLbl.replace(/…$/, "").length >= 6)
          gEl.appendChild(el("text", { x: x + w / 2, y: y + h / 2 + 1, "text-anchor": "middle",
            "dominant-baseline": "middle", class: "cake-name", text: cakeLbl }));
        if (w > 78)
          gEl.appendChild(el("text", { x: x + w / 2, y: y + h + 12, "text-anchor": "middle",
            class: "cake-loc", text: b.loc + " loc" }));
        svg.appendChild(gEl);
        x += w + GAP;
      });
    });

    function drawCakeEdges(b) {
      clear(edgeLayer);
      var here = pos[b.id];
      if (!here) return;
      var seen = {};
      b.files.forEach(function (f) {
        fileAdj[f.fi].forEach(function (t) { if (vis[t]) seen[blockIdOf(t)] = "down"; });
        fileImpBy[f.fi].forEach(function (s) { if (vis[s]) seen[blockIdOf(s)] = "up"; });
      });
      Object.keys(seen).forEach(function (bid) {
        if (bid === b.id || !pos[bid]) return;
        var o = pos[bid];
        var y1 = seen[bid] === "down" ? here.y + here.h : here.y;
        var y2 = seen[bid] === "down" ? o.y : o.y + o.h;
        var my = (y1 + y2) / 2;
        edgeLayer.appendChild(el("path", {
          d: "M " + here.cx + " " + y1 + " C " + here.cx + " " + my + " " +
             o.cx + " " + my + " " + o.cx + " " + y2,
          stroke: b.hue, "stroke-width": 1.4, opacity: 0.7 }));
      });
    }
    function blockIdOf(fi) { return "b" + fileComp[fi]; }

    var controls = [
      el("button", { class: "pill" + (state.hideTests ? " on" : ""),
        title: "tests/** — the health inspector: checks the kitchen, doesn't do the cooking",
        on: { click: function () { state.hideTests = !state.hideTests; render(); } } },
        [el("i", { class: "ph ph-flask" }), "Hide tests"]),
    ];
    // The restaurant (teacher's principle 2, reused as the house metaphor —
    // see content-philosophy.md): a menu is what the outside world can ask
    // for, a waiter carries the order back but doesn't cook, the kitchen does
    // the actual work, the fridge/pantry is what survives after everyone goes
    // home. This diagram's own axis — longest-path import depth — lines up
    // with it almost exactly: L0 depends on nothing else here (the pantry),
    // the top layer is called by almost nothing and calls into everything
    // below it (the waiter), and the cooking happens in the layers between.
    var legend = el("div", { class: "map-legend" }, [
      el("div", { class: "lk", text: "LAYER CAKE" }),
      lgRow(el("span", { class: "sw", style: "width:26px;height:12px;border-radius:3px;" +
        "background:" + hexA(GROUP_HUES[0], 0.24) + ";border:1px solid " + GROUP_HUES[0] }),
        "block = file · width = lines of code · fill = folder"),
      lgRow(el("span", { class: "sw", style: "width:14px;height:12px;border-radius:3px;" +
        "background:transparent;border:2px solid " + GROUP_HUES[0] }), "↺ merged = import cycle"),
      lgRow(el("span", { class: "sw", style: "border:0" }), "L0 imports nothing in-repo · hover a block for its imports"),
      lgRow(el("span", { class: "sw", style: "border:0" }),
        "the restaurant: bottom = the pantry & fridge (little calls out, lots call in) · top = the waiter (calls everything, called by little) · the kitchen is between"),
    ]);
    return { controls: controls, content: svg, legend: legend };
  }

  // ── 2. Run trace ── "what happens when this runs" ───────────────────
  function runTrace() {
    var root = state.traceRoot != null ? state.traceRoot
      : (traceRoots.length ? traceRoots[0].i : null);
    if (root == null)
      return { content: el("div", { class: "map-empty", text: "No call graph to trace." }) };

    var depth = new Map([[root, 0]]);
    var order = [root], qi = 0;
    while (qi < order.length) {
      var u = order[qi++], du = depth.get(u);
      // confident edges only (outAdjSure) — an AMBIGUOUS one used to be able
      // to walk this trace straight from production code into an unrelated
      // same-named method, even a test file's.
      (outAdjSure[u] || []).forEach(function (v) {
        if (!depth.has(v)) { depth.set(v, du + 1); order.push(v); }
      });
    }
    var cols = [];
    depth.forEach(function (d, i) { (cols[d] = cols[d] || []).push(i); });
    var maxD = cols.length - 1;
    // a short trace on its own means nothing — a small repo or a shallow
    // root looks identical. Only call it out when a real leaf in the trace
    // shows an actual dynamic-dispatch pattern in its own source.
    var dynLeaf = null;
    for (var li = 0; li < order.length && !dynLeaf; li++) {
      var leafU = order[li];
      if ((outAdjSure[leafU] || []).length) continue;
      if (classifyUnresolvedCalls(N[leafU]) === "dynamic") dynLeaf = N[leafU];
    }

    var BUDGET = 12, COLW = 208, ROWH = 44, TOP = 30, LEFT = 24;
    var shown = {}, colShown = [];
    cols.forEach(function (arr, d) {
      var ranked = arr.slice().sort(function (a, b) { return N[b].fan_in - N[a].fan_in; });
      var open = traceExpand[root + ":" + d];
      var take = open || ranked.length <= BUDGET ? ranked : ranked.slice(0, BUDGET);
      take.forEach(function (i) { shown[i] = true; });
      colShown[d] = { list: take, hidden: (open ? 0 : Math.max(0, ranked.length - take.length)) };
    });
    var maxRows = colShown.reduce(function (m, c) { return Math.max(m, c.list.length + (c.hidden ? 1 : 0)); }, 1);
    var W = LEFT * 2 + (maxD + 1) * COLW + (dynLeaf ? 220 : 0);
    var Hh = TOP * 2 + maxRows * ROWH;
    var yOf = {}, xOf = {};
    colShown.forEach(function (c, d) {
      var n = c.list.length + (c.hidden ? 1 : 0);
      var y0 = TOP + (maxRows - n) * ROWH / 2;
      c.list.forEach(function (i, k) { yOf[i] = y0 + k * ROWH + ROWH / 2; xOf[i] = LEFT + d * COLW; });
      c._chipY = y0 + c.list.length * ROWH + ROWH / 2;
      c._x = LEFT + d * COLW;
    });
    var step = state.traceStep;
    var svg = el("svg", { class: "trace", width: W, height: Hh, viewBox: "0 0 " + W + " " + Hh });

    var eG = el("g", { fill: "none" });
    svg.appendChild(eG);
    order.forEach(function (u) {
      if (!shown[u]) return;
      (outAdj[u] || []).forEach(function (v) {
        if (!shown[v] || depth.get(v) !== depth.get(u) + 1) return;
        var dim = step != null && depth.get(v) > step;
        var x1 = xOf[u] + 30, x2 = xOf[v] - 8, mx = (x1 + x2) / 2;
        eG.appendChild(el("path", {
          d: "M " + x1 + " " + yOf[u] + " C " + mx + " " + yOf[u] + " " + mx + " " + yOf[v] +
             " " + x2 + " " + yOf[v],
          stroke: colorForNode(N[u]), "stroke-width": 1.3, opacity: dim ? 0.1 : 0.4 }));
      });
    });

    colShown.forEach(function (c, d) {
      svg.appendChild(el("text", { x: c._x + 12, y: 16, class: "trace-dnum",
        text: d === 0 ? "root" : "depth " + d }));
      c.list.forEach(function (i) {
        var n = N[i], dim = step != null && d > step;
        var r = 7 + Math.min(6, Math.sqrt(n.fan_in));
        var gEl = el("g", { class: "trace-st" + (dim ? " dim" : ""),
          on: { click: function () { go("graph", n.key); } } },
          [el("title", { text: n.qual + " · " + n.fan_in + " callers" })]);
        gEl.appendChild(el("circle", { cx: xOf[i], cy: yOf[i], r: r,
          fill: hexA(colorForNode(n), 0.9), stroke: colorForNode(n), "stroke-width": 1.4 }));
        gEl.appendChild(el("text", { x: xOf[i] + r + 8, y: yOf[i] - 3, class: "trace-nm",
          text: fitText(n.name, COLW - r - 20, 6.4) }));
        gEl.appendChild(el("text", { x: xOf[i] + r + 8, y: yOf[i] + 9, class: "trace-fl",
          text: fitText(n.file.split("/").pop(), COLW - r - 20, 5.4) }));
        svg.appendChild(gEl);
      });
      if (c.hidden) {
        var cy = c._chipY;
        var chip = el("g", { class: "trace-more",
          on: { click: function () { traceExpand[root + ":" + d] = true; render(); } } });
        chip.appendChild(el("rect", { x: c._x + 2, y: cy - 12, width: 150, height: 24, rx: 12,
          fill: "var(--color-neutral-900)", stroke: "var(--color-neutral-800)" }));
        chip.appendChild(el("text", { x: c._x + 12, y: cy + 4, class: "trace-nm",
          text: "+ " + c.hidden + " more calls" }));
        svg.appendChild(chip);
      }
    });

    if (dynLeaf) {
      var lastLine = null;
      (srcOf(dynLeaf) || "").split("\n").forEach(function (ln) {
        if (SIM_DYN_RE.test(ln)) lastLine = ln.trim();
      });
      var bx = LEFT + (maxD + 1) * COLW, by = TOP + maxRows * ROWH / 2 - 34;
      var dg = el("g", { class: "trace-dispatch" });
      dg.appendChild(el("rect", { x: bx, y: by, width: 200, height: 68, rx: 8,
        fill: "var(--color-surface-2)", stroke: "var(--color-accent-700)" }));
      dg.appendChild(el("text", { x: bx + 12, y: by + 20, class: "trace-nm", text: "⚡ dynamic dispatch" }));
      dg.appendChild(el("text", { x: bx + 12, y: by + 38, class: "trace-fl",
        text: dynLeaf.name + " — the indexer can't follow this" }));
      if (lastLine)
        dg.appendChild(el("text", { x: bx + 12, y: by + 55, class: "trace-code",
          text: lastLine.length > 26 ? lastLine.slice(0, 25) + "…" : lastLine }));
      svg.appendChild(dg);
    }

    var rootList = traceRoots.slice();
    if (!rootList.some(function (r) { return r.i === root; }))
      rootList.unshift({ i: root, size: order.length - 1 });   // a hand-picked root not in the top ranks
    var picker = el("select", { class: "map-select",
      on: { change: function (e) { go("map", "trace/" + e.target.value); } } },
      rootList.map(function (r) {
        return el("option", { value: N[r.i].key, selected: r.i === root ? "selected" : null,
          text: N[r.i].qual + "  (" + r.size + " reached)" });
      }));
    var stepLabel = step == null ? "all" : step + " / " + maxD;
    var controls = [
      picker,
      el("button", { class: "pill", on: { click: function () {
        go("sim", "derive:" + N[root].key + "/0");
      } } }, [el("i", { class: "ph ph-play-circle" }), "Simulate ▶"]),
      el("div", { class: "stepper" }, [
        el("button", { "aria-label": "step back",
          on: { click: function () {
            state.traceStep = step == null ? Math.max(0, maxD - 1) : Math.max(0, step - 1);
            render();
          } } }, [el("i", { class: "ph ph-caret-left" })]),
        el("b", { class: "mono", text: stepLabel }),
        el("button", { "aria-label": "step forward",
          on: { click: function () {
            state.traceStep = step == null ? 0 : Math.min(maxD, step + 1);
            if (state.traceStep >= maxD) state.traceStep = null;
            render();
          } } }, [el("i", { class: "ph ph-caret-right" })]),
      ]),
    ];
    var legend = el("div", { class: "map-legend" }, [
      el("div", { class: "lk", text: "RUN TRACE" }),
      lgRow(el("span", { class: "sw", style: "border:0" }), "column = calls N hops from the root"),
      lgRow(el("span", { class: "sw dot", style: "background:var(--color-neutral-400);width:13px;height:13px" }),
        "bigger dot = more callers (fan-in)"),
      lgRow(el("span", { class: "sw", style: "border:0" }), "◀ ▶ walks the cascade one hop at a time"),
      lgRow(el("span", { class: "sw", style: "border:0" }), "⚡ = a call resolved at runtime, not statically"),
    ]);
    return { controls: controls, content: svg, legend: legend };
  }

  // ── 3. Mass map ── "where is the code, and what keeps moving" ────────
  function massMap() {
    var W = 1000, Hh = 660, GAP = 3, LBL = 15;
    var groups = {};
    FILES.forEach(function (f) { (groups[dirGroup(f.path)] = groups[dirGroup(f.path)] || []).push(f); });
    var gData = Object.keys(groups).map(function (k) {
      return { key: k, files: groups[k],
        value: groups[k].reduce(function (s, f) { return s + (f.loc || 0); }, 0) };
    });
    var svg = el("svg", { class: "mass", width: W, height: Hh, viewBox: "0 0 " + W + " " + Hh });
    var defs = el("defs", {}, [
      (function () {
        var p = el("pattern", { id: "cm-hatch", width: 6, height: 6,
          patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
        p.appendChild(el("rect", { width: 6, height: 6, fill: "transparent" }));
        p.appendChild(el("line", { x1: 0, y1: 0, x2: 0, y2: 6,
          stroke: "var(--color-neutral-500)", "stroke-width": 1.4 }));
        return p;
      })(),
    ]);
    svg.appendChild(defs);
    var hiddenZero = 0;

    squarify(gData, 4, 4, W - 8, Hh - 8).forEach(function (gc) {
      var grp = gc.item;
      svg.appendChild(el("rect", { x: gc.x, y: gc.y, width: gc.w, height: gc.h, rx: 4,
        fill: "none", stroke: hexA(colorForPath(grp.files[0].path), 0.5), "stroke-width": 1 }));
      svg.appendChild(el("text", { x: gc.x + 6, y: gc.y + 11, class: "mass-grp",
        fill: colorForPath(grp.files[0].path),
        text: fitText(grp.key === "(root)" ? "· root" : grp.key, gc.w - 12, 5.6) }));
      var inner = squarify(grp.files.map(function (f) { return { value: f.loc || 0, f: f }; }),
        gc.x + GAP, gc.y + LBL, Math.max(0, gc.w - GAP * 2), Math.max(0, gc.h - LBL - GAP));
      hiddenZero += grp.files.filter(function (f) { return !(f.loc > 0); }).length;
      inner.forEach(function (fc) {
        var f = fc.item.f, dead = fileAllDead(f.fi) && !isTestPath(f.path),
            cw = Math.max(0, fc.w - GAP), ch = Math.max(0, fc.h - GAP);
        var cell = el("g", { class: "mass-cell",
          on: { click: function () { fileAct(f)(); } } }, [
          el("title", { text: f.path.split("/").pop() + " · " + (f.loc || 0) + " loc · " +
            (churnOf[f.fi] || 0) + " commits" + (dead ? " · every function unreachable" : "") }),
        ]);
        cell.appendChild(el("rect", { x: fc.x, y: fc.y, width: cw, height: ch, rx: 3, fill: churnFill(f.fi) }));
        if (dead)
          cell.appendChild(el("rect", { x: fc.x, y: fc.y, width: cw, height: ch, rx: 3, fill: "url(#cm-hatch)" }));
        if (cw > 40 && fc.h > 20) {
          // fitText already keeps each line inside its own estimated-width
          // budget, but a clipPath is a hard backstop against the sub-label
          // ("N loc · dead") still bleeding past the cell's real edge on a
          // narrow squarify slice, where the char-width estimate can be off
          // by a pixel or two — same idea as the Architecture boxes below.
          var clipId = "mc-" + f.fi;
          defs.appendChild(el("clipPath", { id: clipId }, [
            el("rect", { x: fc.x, y: fc.y, width: cw, height: ch }),
          ]));
          var labels = el("g", { "clip-path": "url(#" + clipId + ")" });
          labels.appendChild(el("text", { x: fc.x + 5, y: fc.y + 13, class: "mass-nm",
            text: fitText(f.path.split("/").pop(), cw - 8, 5.8) }));
          if (fc.h > 34)
            labels.appendChild(el("text", { x: fc.x + 5, y: fc.y + 25, class: "mass-sub",
              text: fitText((f.loc || 0) + " loc" + (dead ? " · dead" : ""), cw - 8, 5.6) }));
          cell.appendChild(labels);
        }
        svg.appendChild(cell);
      });
    });

    var ramp = el("span", { class: "sw",
      style: "width:60px;height:10px;border-radius:2px;background:linear-gradient(90deg," +
        CHURN_LO + "," + CHURN_HI + ")" });
    var legend = el("div", { class: "map-legend" }, [
      el("div", { class: "lk", text: "MASS MAP" }),
      lgRow(el("span", { class: "sw", style: "border:0" }), "area = lines of code · grouped by folder"),
      lgRow(ramp, "fill = commits touching it (low → high)"),
      lgRow(el("span", { class: "sw", style: "width:16px;height:12px;background:var(--color-surface-2);" +
        "background-image:repeating-linear-gradient(45deg,var(--color-neutral-500) 0 1px,transparent 1px 4px)" }),
        "hatched = every function unreachable"),
    ]);
    // area is proportional to LOC, so a 0-line file (a stub, an __init__.py,
    // a barrel export) squarifies to zero area and just isn't drawn — say so
    // rather than let it silently vanish with no trace anywhere in the view.
    if (hiddenZero)
      legend.appendChild(lgRow(el("span", { class: "sw", style: "border:0" }),
        hiddenZero + " empty file" + (hiddenZero === 1 ? "" : "s") + " (0 loc) not shown"));
    return { content: svg, legend: legend };
  }

  function lgRow(sw, txt) { return el("div", { class: "row" }, [sw, txt]); }

  // ---- architecture tab ---------------------------------------------
  // The hand-drawn architecture diagram most READMEs never get: layers stacked
  // top to bottom (routes & entry → views / API → logic → data), each a band of
  // component boxes labelled with the tech they're built on, data stores as
  // cylinders, outside services and whoever drives the app as clouds, and a
  // side panel of shared code with dashed links into the layers that use it.
  // Every placement comes from DATA.architecture (codemap/site/architecture.py
  // scores each file and ships the evidence) — nothing is guessed here, this
  // section only lays it out once per render and wires hover/click. Like Map it
  // scrolls instead of pan/zooming; hover only rewrites classes and repaints
  // one small highlight layer, never the scene.
  var ARCH = DATA.architecture || {};
  var A_COMPS = ARCH.components || [];
  var A_STORES = ARCH.stores || [];
  var A_SERVICES = ARCH.services || [];
  var A_ACTORS = ARCH.actors || [];
  var A_LINKS = ARCH.links || [];
  // web requests between parts (only when a codebase-memory index was merged in):
  // kept apart from A_LINKS, which are layer-ordered imports
  var A_REQS = ARCH.requests || [];
  var A_COMP = {}, A_LAYER = {};
  A_COMPS.forEach(function (c) { A_COMP[c.id] = c; });
  (ARCH.layers || []).forEach(function (l) { A_LAYER[l.id] = l; });
  // hues from GROUP_HUES (already CVD-checked on this canvas), in CKAN's order
  // of routes pink → views green → API amber → logic blue → models orange. The
  // band title always repeats the layer in words, so colour is never the only cue.
  var A_HUE = { entry: "#d55181", views: "#199e70", api: "#c98500", logic: "#3987e5",
                data: "#d95926", shared: GROUP_OTHER, tests: GROUP_OTHER };
  var A_SERVICE_HUE = "#9085e9", A_ACTOR_HUE = "#b2b6ca", A_UP_HUE = "#e66767";
  // A_BOX_H has room for a 2-line title + the subtitle line below it — every
  // box gets this height (the row/band layout below shares one height per
  // row), so a long authored title never has to ellipsis down to one line;
  // a short title just leaves a little breathing room under it instead.
  var A_BOX_W = 140, A_BOX_H = 60, A_GAP = 12, A_BAND_PAD = 14, A_BAND_HEAD = 32,
      A_ROW_GAP = 52, A_MAIN_W = 700, A_SIDE_W = 184, A_SIDE_GAP = 54, A_PAD = 20,
      A_CLOUD_W = 150, A_CLOUD_H = 64, A_CYL_W = 118, A_CYL_H = 66, A_UP_MAX = 12, A_REQ_MAX = 6,
      A_SEAM = 18, A_SEAM_WIDE = 46;
  var EP_BY_NODE = {};
  (DATA.entry_points || []).forEach(function (ep) {
    if (ep.node != null) (EP_BY_NODE[ep.node] = EP_BY_NODE[ep.node] || []).push(ep);
  });

  function archFindById(list, id) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }
  // item keys: a component's own id ("<layer>:<path>"), or store:/service:/actor:<id>
  function archItem(key) {
    if (!key) return null;
    if (A_COMP[key]) return { kind: "comp", obj: A_COMP[key] };
    var i = key.indexOf(":"), kind = key.slice(0, i), id = key.slice(i + 1);
    var list = kind === "store" ? A_STORES : kind === "service" ? A_SERVICES : kind === "actor" ? A_ACTORS : null;
    var obj = list && archFindById(list, id);
    return obj ? { kind: kind, obj: obj } : null;
  }
  function archRelated(key) {
    var rel = new Set([key]);
    var it = archItem(key);
    if (!it) return rel;
    if (it.kind === "comp") {
      A_LINKS.forEach(function (l) {
        if (l.s === key) rel.add(l.t);
        if (l.t === key) rel.add(l.s);
      });
      (it.obj.stores || []).forEach(function (s) { rel.add("store:" + s); });
      (it.obj.services || []).forEach(function (s) { rel.add("service:" + s); });
      A_ACTORS.forEach(function (a) { if (a.components.indexOf(key) >= 0) rel.add("actor:" + a.id); });
    } else {
      (it.obj.components || []).forEach(function (c) { rel.add(c); });
    }
    return rel;
  }
  function archHueOf(key) {
    var it = archItem(key);
    if (!it) return GROUP_OTHER;
    if (it.kind === "comp") return A_HUE[it.obj.layer] || GROUP_OTHER;
    return it.kind === "store" ? A_HUE.data : it.kind === "service" ? A_SERVICE_HUE : A_ACTOR_HUE;
  }
  function archLight(hex) { return lerpHex(hex, "#ffffff", 0.35); }

  // ── shapes ──
  function archCloudPath(x, y, w, h) {
    var b = y + h * 0.84;
    return "M " + (x + w * 0.2) + " " + b +
      " C " + (x - w * 0.03) + " " + b + " " + (x - w * 0.02) + " " + (y + h * 0.4) + " " + (x + w * 0.2) + " " + (y + h * 0.44) +
      " C " + (x + w * 0.18) + " " + (y + h * 0.06) + " " + (x + w * 0.5) + " " + (y - h * 0.04) + " " + (x + w * 0.58) + " " + (y + h * 0.24) +
      " C " + (x + w * 0.72) + " " + (y + h * 0.0) + " " + (x + w * 1.0) + " " + (y + h * 0.16) + " " + (x + w * 0.86) + " " + (y + h * 0.46) +
      " C " + (x + w * 1.05) + " " + (y + h * 0.5) + " " + (x + w * 1.03) + " " + b + " " + (x + w * 0.8) + " " + b + " Z";
  }
  function archCylBody(x, y, w, h, ry) {
    return "M " + x + " " + (y + ry) + " L " + x + " " + (y + h - ry) +
      " A " + (w / 2) + " " + ry + " 0 0 0 " + (x + w) + " " + (y + h - ry) +
      " L " + (x + w) + " " + (y + ry) + " Z";
  }
  // a CKAN-style hollow block arrow on the vertical, tip at y2
  function archBlockArrow(x, y1, y2) {
    var dir = y2 > y1 ? 1 : -1, sw = 5, hw = 11, yh = y2 - 11 * dir;
    return "M " + (x - sw) + " " + y1 + " L " + (x - sw) + " " + yh + " L " + (x - hw) + " " + yh +
      " L " + x + " " + y2 + " L " + (x + hw) + " " + yh + " L " + (x + sw) + " " + yh +
      " L " + (x + sw) + " " + y1 + " Z";
  }
  function archArrowPair(x, yTop, yBot, title) {
    var g = el("g", { class: "arch-arrow" }, title ? [el("title", { text: title })] : []);
    g.appendChild(el("path", { d: archBlockArrow(x - 12, yTop, yBot) }));   // calls in
    g.appendChild(el("path", { d: archBlockArrow(x + 12, yBot, yTop) }));   // results back
    return g;
  }
  // the same block arrow laid on its side, tip at x2
  function archBlockArrowH(y, x1, x2) {
    var dir = x2 > x1 ? 1 : -1, sw = 5, hw = 11, xh = x2 - 11 * dir;
    return "M " + x1 + " " + (y - sw) + " L " + xh + " " + (y - sw) + " L " + xh + " " + (y - hw) +
      " L " + x2 + " " + y + " L " + xh + " " + (y + hw) + " L " + xh + " " + (y + sw) +
      " L " + x1 + " " + (y + sw) + " Z";
  }
  function archArrowPairH(y, xa, xb, title) {
    var g = el("g", { class: "arch-arrow" }, title ? [el("title", { text: title })] : []);
    g.appendChild(el("path", { d: archBlockArrowH(y - 12, xa, xb) }));   // calls across
    g.appendChild(el("path", { d: archBlockArrowH(y + 12, xb, xa) }));   // results back
    return g;
  }
  // An arrow stands for a whole pair of layers, so its tooltip leads with the layers
  // and then names one real pair of parts as a concrete example of what crosses.
  function archTip(head, pairs) {
    var s = pairs.slice().sort(function (a, b) {
      return b.n - a.n || (a.s < b.s ? -1 : a.s > b.s ? 1 : 0) || (a.t < b.t ? -1 : a.t > b.t ? 1 : 0);
    });
    var ex = A_COMP[s[0].s].title + " → " + A_COMP[s[0].t].title;
    if (s.length === 1) return head + "\n" + ex;
    return head + "\n" + (s[1].n === s[0].n ? "for example: " : "heaviest: ") + ex + " · " + s[0].n;
  }

  function archTab() {
    if (!A_COMPS.length)
      return el("div", { class: "map" }, [el("div", { class: "map-empty", text: "Nothing indexed to draw yet." })]);
    if (state.archSel && !archItem(state.archSel)) state.archSel = null;   // stale deep link after a re-scan

    var showTests = state.archShowTests;
    function visible(key) {
      var c = A_COMP[key];
      return !c || c.layer !== "tests" || showTests;
    }
    var byLayer = {};
    A_COMPS.forEach(function (c) {
      if (visible(c.id)) (byLayer[c.layer] = byLayer[c.layer] || []).push(c);
    });

    // Imports between two layers of the same rank. architecture.py tags them `same`
    // because Views and API share a rank, and they sit side by side in one row, so
    // they cannot be drawn down a row gap: they cross the seam between the bands.
    // Worked out before layout because it decides how wide that seam has to be.
    var sameBands = {};      // "<srcLayer>|<tgtLayer>" -> {n, pairs: [link]}
    A_LINKS.forEach(function (l) {
      if (l.dir !== "same" || !A_COMP[l.s] || !A_COMP[l.t] || !visible(l.s) || !visible(l.t)) return;
      var ls = A_COMP[l.s].layer, lt = A_COMP[l.t].layer;
      if (ls === lt) return;                       // inside one band: hover already shows it
      var k = ls + "|" + lt, e = sameBands[k] || (sameBands[k] = { n: 0, pairs: [] });
      e.n += l.n;
      e.pairs.push(l);
    });

    var pos = {};      // item key -> {x, y, w, h, cx, cy}
    var rowOf = {};    // component id -> index into rows (main-column band rows only)
    var rows = [];     // {kind, top, bottom, bands: [{layer, x, w}]}
    var sideComps = (byLayer.shared || []).concat(byLayer.tests || []);
    var hasSide = sideComps.length > 0;
    var x0 = A_PAD + (hasSide ? A_SIDE_W + A_SIDE_GAP : 0);
    var y = A_PAD;

    // ── layout: actors row ──
    if (A_ACTORS.length) {
      var aw = A_ACTORS.length * A_CLOUD_W + (A_ACTORS.length - 1) * 28;
      var ax = x0 + (A_MAIN_W - aw) / 2;
      A_ACTORS.forEach(function (a, k) {
        var x = ax + k * (A_CLOUD_W + 28);
        pos["actor:" + a.id] = { x: x, y: y, w: A_CLOUD_W, h: A_CLOUD_H, cx: x + A_CLOUD_W / 2, cy: y + A_CLOUD_H / 2 };
      });
      rows.push({ kind: "actors", top: y, bottom: y + A_CLOUD_H * 0.84 });
      y += A_CLOUD_H + A_ROW_GAP - 10;
    }

    // ── layout: band rows ──
    function flowRows(n, itemW, innerW) {
      var per = Math.max(1, Math.floor((innerW + A_GAP) / (itemW + A_GAP)));
      return { per: per, rows: Math.ceil(n / per) };
    }
    function place(keys, itemW, itemH, bx, bw, top) {
      var fr = flowRows(keys.length, itemW, bw - A_BAND_PAD * 2);
      keys.forEach(function (key, k) {
        var r = Math.floor(k / fr.per), inRow = Math.min(fr.per, keys.length - r * fr.per);
        var rowW = inRow * itemW + (inRow - 1) * A_GAP;
        var x = bx + (bw - rowW) / 2 + (k - r * fr.per) * (itemW + A_GAP);
        var yy = top + r * (itemH + A_GAP);
        pos[key] = { x: x, y: yy, w: itemW, h: itemH, cx: x + itemW / 2, cy: yy + itemH / 2 };
      });
      return fr.rows ? fr.rows * (itemH + A_GAP) - A_GAP : 0;
    }
    var storesShown = A_STORES.slice();
    [["entry"], ["views", "api"], ["logic"], ["data"]].forEach(function (spec) {
      var present = spec.filter(function (l) {
        return (byLayer[l] || []).length || (l === "data" && storesShown.length);
      });
      if (!present.length) return;
      // two bands share this row (Views beside API). Their seam is normally 18px,
      // far too narrow to hold an arrow; when imports cross it, open it up.
      var seam = A_SEAM;
      if (present.length === 2 &&
          (sameBands[present[0] + "|" + present[1]] || sameBands[present[1] + "|" + present[0]]))
        seam = A_SEAM_WIDE;
      var widths;
      if (present.length === 1) widths = [A_MAIN_W];
      else {
        var na = (byLayer[present[0]] || []).length, nb = (byLayer[present[1]] || []).length;
        var share = Math.max(0.38, Math.min(0.62, na / (na + nb)));
        widths = [(A_MAIN_W - seam) * share, (A_MAIN_W - seam) * (1 - share)];
      }
      var rowIdx = rows.length, bands = [], rowH = 0, bx = x0;
      present.forEach(function (layer, k) {
        var bw = widths[k];
        var comps = (byLayer[layer] || []).map(function (c) { rowOf[c.id] = rowIdx; return c.id; });
        var top = y + A_BAND_HEAD;
        var storeKeys = layer === "data" ? storesShown.map(function (s) { return "store:" + s.id; }) : [];
        var oneRowW = comps.length * (A_BOX_W + A_GAP) + storeKeys.length * (A_CYL_W + A_GAP) - A_GAP;
        if (storeKeys.length && oneRowW <= bw - A_BAND_PAD * 2) {
          // CKAN's Models band: boxes and database cylinders side by side on one row
          var rx = bx + (bw - oneRowW) / 2;
          comps.forEach(function (key) {
            var yy = top + (A_CYL_H - A_BOX_H) / 2;
            pos[key] = { x: rx, y: yy, w: A_BOX_W, h: A_BOX_H, cx: rx + A_BOX_W / 2, cy: yy + A_BOX_H / 2 };
            rx += A_BOX_W + A_GAP;
          });
          storeKeys.forEach(function (key) {
            pos[key] = { x: rx, y: top, w: A_CYL_W, h: A_CYL_H, cx: rx + A_CYL_W / 2, cy: top + A_CYL_H / 2 };
            rx += A_CYL_W + A_GAP;
          });
          rowH = Math.max(rowH, A_BAND_HEAD + A_CYL_H + A_BAND_PAD);
          bands.push({ layer: layer, x: bx, w: bw });
          bx += bw + seam;
          return;
        }
        var boxesH = place(comps, A_BOX_W, A_BOX_H, bx, bw, top);
        var h = A_BAND_HEAD + boxesH;
        if (storeKeys.length) {
          var sTop = top + boxesH + (boxesH ? 16 : 4);
          var cylH = place(storeKeys, A_CYL_W, A_CYL_H, bx, bw, sTop);
          h += (boxesH ? 16 : 4) + cylH;
        }
        h += A_BAND_PAD;
        rowH = Math.max(rowH, h);
        bands.push({ layer: layer, x: bx, w: bw });
        bx += bw + seam;
      });
      rows.push({ kind: "band", top: y, bottom: y + rowH, bands: bands, seam: seam });
      y += rowH + A_ROW_GAP;
    });

    // ── layout: outside services ──
    var servicesTop = null;
    if (A_SERVICES.length) {
      servicesTop = y + 14;
      var perS = Math.max(1, Math.floor((A_MAIN_W + 18) / (A_CLOUD_W + 18)));
      A_SERVICES.forEach(function (s, k) {
        var r = Math.floor(k / perS), inRow = Math.min(perS, A_SERVICES.length - r * perS);
        var rowW = inRow * A_CLOUD_W + (inRow - 1) * 18;
        var x = x0 + (A_MAIN_W - rowW) / 2 + (k - r * perS) * (A_CLOUD_W + 18);
        var yy = servicesTop + r * (A_CLOUD_H + 12);
        pos["service:" + s.id] = { x: x, y: yy, w: A_CLOUD_W, h: A_CLOUD_H, cx: x + A_CLOUD_W / 2, cy: yy + A_CLOUD_H / 2 };
      });
      var sRows = Math.ceil(A_SERVICES.length / perS);
      rows.push({ kind: "services", top: servicesTop, bottom: servicesTop + sRows * (A_CLOUD_H + 12) });
      y = servicesTop + sRows * (A_CLOUD_H + 12) + A_ROW_GAP;
    }
    var mainBottom = y - A_ROW_GAP;

    // ── layout: side panel ──
    var side = null;
    if (hasSide) {
      var sTop0 = rows.length && rows[0].kind === "actors" && rows[1] ? rows[1].top : A_PAD;
      var sy = sTop0 + 14, sections = [];
      [["shared", byLayer.shared || []], ["tests", byLayer.tests || []]].forEach(function (pair) {
        if (!pair[1].length) return;
        sections.push({ layer: pair[0], y: sy });
        sy += 26;
        pair[1].forEach(function (c) {
          var bx2 = A_PAD + 12, bw2 = A_SIDE_W - 24;
          pos[c.id] = { x: bx2, y: sy, w: bw2, h: A_BOX_H, cx: bx2 + bw2 / 2, cy: sy + A_BOX_H / 2 };
          sy += A_BOX_H + 10;
        });
        sy += 10;
      });
      side = { top: sTop0, bottom: sy, sections: sections };
    }

    // ── links that don't fit the neighbour-to-neighbour picture ──
    var bandAgg = {}, skipAgg = {}, ups = [], sideLayers = {};
    A_LINKS.forEach(function (l) {
      if (!visible(l.s) || !visible(l.t) || !pos[l.s] || !pos[l.t]) return;
      if (l.dir === "side") {
        var other = A_COMP[l.s].layer === "shared" || A_COMP[l.s].layer === "tests" ? l.t : l.s;
        var ol = A_COMP[other].layer;
        if (ol !== "shared" && ol !== "tests") sideLayers[ol] = (sideLayers[ol] || 0) + l.n;
      } else if (l.dir === "up") {
        ups.push(l);
      } else if (l.dir === "down") {
        var rs = rowOf[l.s], rt = rowOf[l.t], sl = A_COMP[l.s].layer, tl = A_COMP[l.t].layer;
        if (rt === rs + 1) {
          // one entry per pair of *bands*: an arrow across a row gap can only honestly
          // say "this layer calls that layer", never name a single box
          var bk = rs + "|" + sl + "|" + tl;
          var be = bandAgg[bk] || (bandAgg[bk] = { r: rs, sl: sl, tl: tl, n: 0, pairs: [] });
          be.n += l.n;
          be.pairs.push(l);
        } else if (rt > rs + 1) {
          var sk = rs + "|" + rt;
          var se = skipAgg[sk] || (skipAgg[sk] = { n: 0, from: {}, to: {}, pairs: [] });
          se.n += l.n;
          se.from[sl] = (se.from[sl] || 0) + l.n;     // which layers it really leaves from...
          se.to[tl] = (se.to[tl] || 0) + l.n;         // ...and lands in, not a guess from band order
          se.pairs.push(l);
        }
      }
    });
    var skipKeys = Object.keys(skipAgg).sort();
    var farActors = A_ACTORS.filter(function (a) {
      return a.components.every(function (cid) { return rowOf[cid] == null || rowOf[cid] > 1; }) &&
        a.components.some(function (cid) { return rowOf[cid] != null; });
    }).length;
    var gutterN = skipKeys.length + farActors;
    var W = x0 + A_MAIN_W + (gutterN ? 22 + gutterN * 10 : 0) + A_PAD;
    var H = Math.max(mainBottom, side ? side.bottom : 0) + A_PAD;

    var svg = el("svg", { class: "arch", width: W, height: H, viewBox: "0 0 " + W + " " + H,
      role: "img", "aria-label": "Architecture diagram: " + (ARCH.stack || []).join(", ") });
    var gBands = el("g"), gArrows = el("g", { class: "arch-arrows" }), gHi = el("g", { class: "arch-hi", fill: "none" }),
        gItems = el("g");
    svg.appendChild(gBands); svg.appendChild(gArrows); svg.appendChild(gHi); svg.appendChild(gItems);

    function layerTitle(id) { return (A_LAYER[id] && A_LAYER[id].title) || id; }
    // the band a layer occupies in a given row, or null (the actors and services rows have none)
    function bandIn(rowIdx, layer) {
      var bs = (rows[rowIdx] || {}).bands || [];
      for (var i = 0; i < bs.length; i++) if (bs[i].layer === layer) return bs[i];
      return null;
    }
    function layerRank(m) {
      return Object.keys(m).sort(function (a, b) { return m[b] - m[a] || (a < b ? -1 : 1); });
    }
    function clampIn(x, b, pad) { return Math.max(b.x + pad, Math.min(b.x + b.w - pad, x)); }

    // ── bands ──
    rows.forEach(function (row) {
      if (row.kind === "services") {
        gBands.appendChild(el("text", { x: x0, y: row.top - 6, class: "arch-lk", text: "OUTSIDE SERVICES" }));
        return;
      }
      if (row.kind !== "band") return;
      row.bands.forEach(function (b) {
        var hue = A_HUE[b.layer] || GROUP_OTHER, n = (byLayer[b.layer] || []).length;
        gBands.appendChild(el("rect", { x: b.x, y: row.top, width: b.w, height: row.bottom - row.top, rx: 10,
          fill: hexA(hue, 0.09), stroke: hexA(hue, 0.5), "stroke-width": 1.2 }));
        gBands.appendChild(el("text", { x: b.x + 14, y: row.top + 21, class: "arch-lt", fill: archLight(hue),
          text: fitText(layerTitle(b.layer), b.w - 90, 8.2) }, [el("title", { text: (A_LAYER[b.layer] || {}).body || "" })]));
        if (n)
          gBands.appendChild(el("text", { x: b.x + b.w - 14, y: row.top + 20, "text-anchor": "end", class: "arch-lc",
            text: n + (n === 1 ? " part" : " parts") }));
      });
    });
    if (side) {
      gBands.appendChild(el("rect", { x: A_PAD, y: side.top, width: A_SIDE_W, height: side.bottom - side.top, rx: 10,
        fill: hexA(GROUP_OTHER, 0.07), stroke: hexA(GROUP_OTHER, 0.4), "stroke-width": 1.2 }));
      side.sections.forEach(function (s) {
        gBands.appendChild(el("text", { x: A_PAD + 12, y: s.y + 12, class: "arch-lt small", fill: archLight(GROUP_OTHER),
          text: fitText(layerTitle(s.layer), A_SIDE_W - 24, 7.2) }, [el("title", { text: (A_LAYER[s.layer] || {}).body || "" })]));
      });
    }

    // ── neighbour arrows: actors → first band, band → next band ──
    var gutterK = skipKeys.length, routed = 0;
    A_ACTORS.forEach(function (a) {
      var p = pos["actor:" + a.id], best = null;
      a.components.forEach(function (cid) { if (rowOf[cid] != null && (best == null || rowOf[cid] < best)) best = rowOf[cid]; });
      if (!p || best == null) return;
      // the band that actually holds its components in that row, not just the leftmost one
      var heat = {};
      a.components.forEach(function (cid) {
        if (rowOf[cid] === best) heat[A_COMP[cid].layer] = (heat[A_COMP[cid].layer] || 0) + 1;
      });
      var tb = bandIn(best, layerRank(heat)[0]) || rows[best].bands[0];
      var title = a.label + " → " + layerTitle(tb.layer) + " · " + a.entries + " entry point" + (a.entries === 1 ? "" : "s");
      if (best === 1) {
        gArrows.appendChild(archArrowPair(clampIn(p.cx, tb, 30), p.y + p.h * 0.84 + 4, rows[best].top - 4, title));
        return;
      }
      // the layer it drives isn't the next one down: go around the bands, not through them
      var gx = x0 + A_MAIN_W + 14 + (gutterK++) * 10, yb = rows[best].top + 26;
      var kids = [el("title", { text: title })];
      if (tb.x + tb.w >= x0 + A_MAIN_W - 1) {
        // its band is the rightmost in the row: come in at that edge
        kids.push(el("path", { d: "M " + (p.x + p.w * 0.97) + " " + p.cy + " H " + gx + " V " + yb + " H " + (x0 + A_MAIN_W + 6) }));
        kids.push(el("path", { class: "head", d: "M " + (x0 + A_MAIN_W + 1) + " " + yb + " l 7 -4 v 8 z" }));
      } else {
        // otherwise run back over the top of the row and drop into its own band
        var ya = rows[best].top - 30 - (routed++) * 7, tx = tb.x + tb.w / 2;
        kids.push(el("path", { d: "M " + (p.x + p.w * 0.97) + " " + p.cy + " H " + gx + " V " + ya + " H " + tx + " V " + (rows[best].top - 10) }));
        kids.push(el("path", { class: "head", d: "M " + tx + " " + (rows[best].top - 4) + " l -4 -7 h 8 z" }));
      }
      gArrows.appendChild(el("g", { class: "arch-skip actor" }, kids));
    });
    // Band → next band, one pair of block arrows per pair of *bands*. It spans a row gap,
    // so it can only honestly say "this layer calls that layer": it is centred on the
    // overlap of the two bands and titled with the two layers. Box-to-box detail is on
    // hover, where paint() draws a real curve to each box. (It used to sit at the midpoint
    // of two *boxes* and name them, which on a split row put it in the seam between
    // Views and API, touching neither box.) No cap and no collision rule are needed: the
    // pairs in one gap sit over disjoint band overlaps.
    Object.keys(bandAgg).forEach(function (k) {
      var e = bandAgg[k], sb = bandIn(e.r, e.sl), tb = bandIn(e.r + 1, e.tl);
      if (!sb || !tb) return;
      var lo = Math.max(sb.x, tb.x), hi = Math.min(sb.x + sb.w, tb.x + tb.w);
      var x = clampIn(hi - lo >= 60 ? (lo + hi) / 2 : tb.x + tb.w / 2, tb, 26);
      gArrows.appendChild(archArrowPair(x, rows[e.r].bottom + 4, rows[e.r + 1].top - 4,
        archTip(layerTitle(e.sl) + " → " + layerTitle(e.tl) + " · " + e.n + " import" + (e.n === 1 ? "" : "s"), e.pairs)));
    });
    // Views and API sit at the same depth, so their imports run sideways across the seam
    rows.forEach(function (row) {
      if (row.kind !== "band" || !row.bands[1] || row.seam < A_SEAM_WIDE) return;
      var a = row.bands[0], b = row.bands[1];
      var fw = sameBands[a.layer + "|" + b.layer], rv = sameBands[b.layer + "|" + a.layer];
      var flip = !fw || (rv && rv.n > fw.n), e = flip ? rv : fw, back = flip ? fw : rv;
      if (!e) return;
      var sl = flip ? b.layer : a.layer, tl = flip ? a.layer : b.layer, xl = a.x + a.w + 4, xr = b.x - 4;
      gArrows.appendChild(archArrowPairH(row.top + A_BAND_HEAD + A_BOX_H / 2, flip ? xr : xl, flip ? xl : xr,
        archTip(layerTitle(sl) + " → " + layerTitle(tl) + " · " + e.n + " import" + (e.n === 1 ? "" : "s") +
          (back ? " (+" + back.n + " the other way)" : ""), e.pairs)));
    });

    // ── skip-a-layer connectors down the right gutter ──
    skipKeys.forEach(function (k, idx) {
      var parts = k.split("|"), ra = rows[+parts[0]], rb = rows[+parts[1]], e = skipAgg[k];
      var gx = x0 + A_MAIN_W + 14 + idx * 10;
      var ya = (ra.top + ra.bottom) / 2, yb = (rb.top + rb.bottom) / 2 + idx * 4;
      var fromL = layerRank(e.from), toL = layerRank(e.to);
      // leave from the band the imports really come from: its right edge if it is the
      // rightmost band, otherwise the gap just under its row
      var sb = bandIn(+parts[0], fromL[0]) || ra.bands[ra.bands.length - 1];
      var start = sb.x + sb.w >= x0 + A_MAIN_W - 1
        ? "M " + (x0 + A_MAIN_W) + " " + ya + " H " + gx
        : "M " + (sb.x + sb.w - 12) + " " + ra.bottom + " V " + (ra.bottom + 8) + " H " + gx;
      gArrows.appendChild(el("g", { class: "arch-skip" }, [
        el("title", { text: archTip(e.n + " import" + (e.n === 1 ? "" : "s") + " skip a layer: " +
          fromL.map(layerTitle).join(", ") + " → " + toL.map(layerTitle).join(", "), e.pairs) }),
        el("path", { d: start + " V " + yb + " H " + (x0 + A_MAIN_W + 6) }),
        el("path", { class: "head", d: "M " + (x0 + A_MAIN_W + 1) + " " + yb + " l 7 -4 v 8 z" }),
      ]));
    });

    // ── side panel's dashed fan into the layers it serves ──
    if (side) {
      var axs = A_PAD + A_SIDE_W, ays = (side.top + side.bottom) / 2;
      rows.forEach(function (row) {
        if (row.kind !== "band") return;
        // each band that leans on shared code gets its own titled line; a second band in
        // the same row (API beside Views) draws to its own left edge
        row.bands.forEach(function (b, k) {
          var n = sideLayers[b.layer];
          if (!n) return;
          gArrows.appendChild(el("path", { class: "arch-side-line",
            d: "M " + axs + " " + ays + " L " + b.x + " " + (k === 0 ? (row.top + row.bottom) / 2 : row.top + 16) }, [
            el("title", { text: layerTitle(b.layer) + " ⇄ shared code · " + n + " import" + (n === 1 ? "" : "s") })]));
        });
      });
    }

    // ── services hang off the bottom of the diagram ──
    A_SERVICES.forEach(function (s) {
      var p = pos["service:" + s.id];
      var lastBand = null;
      rows.forEach(function (r) { if (r.kind === "band") lastBand = r; });
      if (!p || !lastBand || !s.components.length) return;
      gArrows.appendChild(el("path", { class: "arch-svc-line", d: "M " + p.cx + " " + (lastBand.bottom + 4) + " V " + (p.y + 4) }));
    });

    // ── wrong-way imports, drawn on top of everything neighbourly ──
    ups.slice(0, A_UP_MAX).forEach(function (l) {
      var a = pos[l.s], b = pos[l.t];
      var y1 = a.y, y2 = b.y + b.h, mx = (a.cx + b.cx) / 2, my = (y1 + y2) / 2;
      gArrows.appendChild(el("g", { class: "arch-up" }, [
        el("title", { text: A_COMP[l.s].title + " (" + layerTitle(A_COMP[l.s].layer) + ") imports " + A_COMP[l.t].title +
          " (" + layerTitle(A_COMP[l.t].layer) + ") — a lower layer reaching up" }),
        el("path", { d: "M " + a.cx + " " + y1 + " C " + a.cx + " " + (y1 - 40) + " " + b.cx + " " + (y2 + 40) + " " + b.cx + " " + y2 }),
        el("circle", { cx: mx, cy: my, r: 7 }),
        el("text", { x: mx, y: my + 3.5, "text-anchor": "middle", text: "!" }),
      ]));
    });

    // ── web requests: a screen calling a route another part serves ──
    // No import joins the two, so nothing above draws it. A dashed connector from
    // the calling part to the one serving the route, labelled with the route.
    A_REQS.filter(function (q) { return visible(q.s) && visible(q.t) && pos[q.s] && pos[q.t]; })
      .slice(0, A_REQ_MAX).forEach(function (q) {
        var a = pos[q.s], b = pos[q.t], d, hx, hy, head, lx, ly;
        if (b.y > a.y + a.h - 1 || b.y + b.h < a.y + 1) {          // one row above the other
          var down = b.y > a.y, y1 = down ? a.y + a.h : a.y, y2 = down ? b.y : b.y + b.h, dy = (y2 - y1) * 0.45;
          d = "M " + a.cx + " " + y1 + " C " + a.cx + " " + (y1 + dy) + " " + b.cx + " " + (y2 - dy) + " " + b.cx + " " + y2;
          hx = b.cx; hy = y2; head = down ? "l -4 -7 h 8 z" : "l -4 7 h 8 z";
          lx = (a.cx + b.cx) / 2; ly = (y1 + y2) / 2 - 4;
        } else {                                   // side by side in one row: arc over the box tops
          var rise = 22;                           // (the gap between them is far too narrow to draw in)
          d = "M " + a.cx + " " + a.y + " C " + a.cx + " " + (a.y - rise) + " " + b.cx + " " + (b.y - rise) + " " + b.cx + " " + b.y;
          hx = b.cx; hy = b.y; head = "l -4 -7 h 8 z";
          lx = (a.cx + b.cx) / 2; ly = Math.min(a.y, b.y) - rise * 0.75 - 3;
        }
        var tip = q.n + " web request" + (q.n === 1 ? "" : "s") + " from " + A_COMP[q.s].title + " to " +
          A_COMP[q.t].title + ":\n" + q.routes.join("\n");
        gArrows.appendChild(el("g", { class: "arch-req" }, [
          el("title", { text: tip }),
          el("path", { d: d }),
          el("path", { class: "head", d: "M " + hx + " " + hy + " " + head }),
          el("text", { x: lx, y: ly, "text-anchor": "middle",
            text: fitText(q.routes[0], 170, 5.9) + (q.n > 1 ? "  +" + (q.n - 1) : "") }),
        ]));
      });

    // ── items ──
    var itemEls = {}, itemBase = {};
    function wire(key, g, base) {
      itemEls[key] = g;
      itemBase[key] = base;
      g.addEventListener("mouseenter", function () { paint(key); });
      g.addEventListener("mouseleave", function () { paint(null); });
      g.addEventListener("focus", function () { paint(key); });
      g.addEventListener("blur", function () { paint(null); });
      gItems.appendChild(g);
    }
    function select(key) { return function () { go("arch", key); }; }

    A_COMPS.forEach(function (c) {
      var p = pos[c.id];
      if (!p || !visible(c.id)) return;
      var hue = A_HUE[c.layer] || GROUP_OTHER;
      var weak = c.confidence === "weak" && !c.authored;
      var sub = c.tech.length
        ? c.tech.slice(0, 3).map(function (t) { return t.label; }).join(" · ")
        : (c.kind === "file" ? "" : c.files.length + (c.files.length === 1 ? " file · " : " files · ")) + c.loc + " loc";
      var base = "arch-item arch-box" + (weak ? " weak" : "");
      var g = el("g", { class: base, "aria-label": c.title + ", " + layerTitle(c.layer),
        on: { click: select(c.id) } }, [
        el("title", { text: c.path + "\n" + c.evidence.join("\n") }),
        el("rect", { x: p.x, y: p.y, width: p.w, height: p.h, rx: 6, class: "frame", stroke: hue }),
        el("rect", { x: p.x, y: p.y, width: 4, height: p.h, rx: 2, fill: hue }),
        el("text", { x: p.x + 13, y: p.y + 18, class: "arch-bt" },
          wrapTitle(c.title, p.w - 20, 6.6).map(function (ln, li) {
            return el("tspan", { x: p.x + 13, dy: li === 0 ? 0 : 13 }, [ln]);
          })),
        el("text", { x: p.x + 13, y: p.y + p.h - 10, class: "arch-bs", fill: c.tech.length ? archLight(hue) : null,
          text: fitText(sub, p.w - 20, 5.9) }),
      ]);
      if (c.entries.length)
        g.appendChild(el("text", { x: p.x + p.w - 8, y: p.y + 14, "text-anchor": "end", class: "arch-badge",
          text: "▸" + c.entries.length }));
      wire(c.id, g, base);
    });
    A_STORES.forEach(function (s) {
      var key = "store:" + s.id, p = pos[key];
      if (!p) return;
      var ry = 9, hue = A_HUE.data;
      var base = "arch-item arch-cyl" + (s.components.length ? "" : " weak");
      var g = el("g", { class: base, "aria-label": s.label + ", data store", on: { click: select(key) } }, [
        el("title", { text: s.label + (s.kind ? " · " + s.kind : "") +
          (s.via.length ? "\nvia " + s.via.join(", ") : "") +
          (s.declared_in.length ? "\ndeclared in " + s.declared_in.join(", ") : "") }),
        el("path", { class: "frame", d: archCylBody(p.x, p.y, p.w, p.h, ry), stroke: hue }),
        el("ellipse", { class: "frame", cx: p.cx, cy: p.y + ry, rx: p.w / 2, ry: ry, stroke: hue }),
        el("text", { x: p.cx, y: p.y + p.h / 2 + 6, "text-anchor": "middle", class: "arch-bt",
          text: fitText(s.label, p.w - 14, 6.6) }),
        el("text", { x: p.cx, y: p.y + p.h / 2 + 20, "text-anchor": "middle", class: "arch-bs",
          text: fitText(s.kind || "store", p.w - 14, 5.9) }),
      ]);
      wire(key, g, base);
    });
    function cloudItem(key, p, hue, label, sub, aria, dashed) {
      var base = "arch-item arch-cloud" + (dashed ? " weak" : "");
      var g = el("g", { class: base, "aria-label": aria, on: { click: select(key) } }, [
        el("title", { text: label + (sub ? " · " + sub : "") }),
        el("path", { class: "frame", d: archCloudPath(p.x, p.y, p.w, p.h), stroke: hue }),
        el("text", { x: p.cx, y: p.y + p.h * 0.52, "text-anchor": "middle", class: "arch-bt",
          text: fitText(label, p.w - 36, 6.6) }),
        el("text", { x: p.cx, y: p.y + p.h * 0.52 + 13, "text-anchor": "middle", class: "arch-bs",
          text: fitText(sub || "", p.w - 40, 5.9) }),
      ]);
      wire(key, g, base);
    }
    A_ACTORS.forEach(function (a) {
      var key = "actor:" + a.id;
      if (pos[key]) cloudItem(key, pos[key], A_ACTOR_HUE, a.label,
        a.entries + " entry point" + (a.entries === 1 ? "" : "s"), a.label + ", drives the app");
    });
    A_SERVICES.forEach(function (s) {
      var key = "service:" + s.id;
      if (pos[key]) cloudItem(key, pos[key], A_SERVICE_HUE, s.label, s.kind, s.label + ", outside service",
        !s.components.length);
    });

    // ── hover / selection: classes + one highlight layer, never a re-layout ──
    function paint(hoverKey) {
      clear(gHi);
      var focus = hoverKey || state.archSel;
      var rel = focus && pos[focus] ? archRelated(focus) : null;
      Object.keys(itemEls).forEach(function (k) {
        var cls = itemBase[k];
        if (rel && !rel.has(k)) cls += " dim";
        if (k === state.archSel) cls += " sel";
        itemEls[k].setAttribute("class", cls);
      });
      gArrows.setAttribute("class", "arch-arrows" + (rel ? " dim" : ""));
      if (!rel) return;
      var a = pos[focus], hue = archHueOf(focus);
      rel.forEach(function (k) {
        if (k === focus || !pos[k]) return;
        var b = pos[k], d;
        if (Math.abs(a.cy - b.cy) < 4) {   // same row: arc over the top
          var top = Math.min(a.y, b.y) - 18;
          d = "M " + a.cx + " " + a.y + " C " + a.cx + " " + top + " " + b.cx + " " + top + " " + b.cx + " " + b.y;
        } else if (b.x >= a.x + a.w || b.x + b.w <= a.x) {
          if (b.cy > a.cy) {
            d = "M " + a.cx + " " + (a.y + a.h) + " C " + a.cx + " " + (a.y + a.h + 40) + " " + b.cx + " " + (b.y - 40) + " " + b.cx + " " + b.y;
          } else {
            d = "M " + a.cx + " " + a.y + " C " + a.cx + " " + (a.y - 40) + " " + b.cx + " " + (b.y + b.h + 40) + " " + b.cx + " " + (b.y + b.h);
          }
        } else if (b.cy > a.cy) {
          d = "M " + a.cx + " " + (a.y + a.h) + " L " + b.cx + " " + b.y;
        } else {
          d = "M " + a.cx + " " + a.y + " L " + b.cx + " " + (b.y + b.h);
        }
        // a side-panel box sits beside the main column: leave from its right edge instead
        if (a.x + a.w <= x0 - A_SIDE_GAP + 1 && b.x >= x0)
          d = "M " + (a.x + a.w) + " " + a.cy + " C " + (a.x + a.w + 60) + " " + a.cy + " " + (b.x - 60) + " " + b.cy + " " + b.x + " " + b.cy;
        else if (b.x + b.w <= x0 - A_SIDE_GAP + 1 && a.x >= x0)
          d = "M " + a.x + " " + a.cy + " C " + (a.x - 60) + " " + a.cy + " " + (b.x + b.w + 60) + " " + b.cy + " " + (b.x + b.w) + " " + b.cy;
        gHi.appendChild(el("path", { d: d, stroke: hue }));
      });
    }
    paint(null);

    var controls = [
      el("div", { class: "arch-stack", title: "The stack, as far as the imports and manifests show it" },
        (ARCH.stack || []).map(function (s) { return el("span", { class: "chip", text: s }); })),
      el("div", { class: "spacer" }),
      el("button", { class: "pill" + (showTests ? " on" : ""),
        title: "Tests check the app; they're not part of it — shown in the side panel when on",
        on: { click: function () { state.archShowTests = !state.archShowTests; render(); } } },
        [el("i", { class: "ph ph-flask" }), "Show tests"]),
    ];
    var canvas = el("div", { class: "map-canvas" }, [svg]);
    var mount = el("div", { class: "map arch" }, [
      el("div", { class: "map-bar" }, controls),
      el("div", { class: "arch-body" }, [el("div", { class: "arch-stage" }, [canvas]), archInspector()]),
    ]);
    // once laid out: on a phone (the SVG doesn't shrink there, it scrolls) open on
    // the main column rather than the side panel, and bring a deep-linked
    // selection into view
    setTimeout(function () {
      var svgW = svg.getBoundingClientRect().width, sc = svgW / W;
      if (hasSide && canvas.clientWidth < svgW) canvas.scrollLeft = Math.max(0, x0 * sc - 12);
      if (state.archSel && pos[state.archSel])
        canvas.scrollTop = Math.max(0, pos[state.archSel].y * sc - canvas.clientHeight / 3);
    }, 0);
    return mount;
  }

  // ── the right-hand panel: overview, or the selected item's details ──
  function archInspector() {
    var key = state.archSel, it = archItem(key);
    var head, body = [];
    function sec(label, kids) {
      return el("div", { class: "section" }, [el("div", { class: "lbl", text: label })].concat(kids));
    }
    function row(rk, rn, rc, onClick, title) {
      return el("div", { class: "rowitem " + (onClick ? "is-link" : "flat"), title: title || null,
        on: onClick ? { click: onClick } : null }, [
        rk ? el("span", { class: "rk", text: rk }) : null,
        el("span", { class: "rn", text: rn }),
        rc != null ? el("span", { class: "rc", text: String(rc) }) : null,
      ]);
    }
    function compRow(cid, rc, warn) {
      var c = A_COMP[cid];
      if (!c) return null;
      return row(warn ? "⚠ up" : ((A_LAYER[c.layer] || {}).title || c.layer).split(" ")[0].toLowerCase(),
        c.title, rc, function () { go("arch", cid); },
        warn ? "a lower layer importing a higher one" : c.path);
    }
    function techChip(label, pkg) {
      var slug = pkg ? libSlug(pkg) : null, linkable = slug && LIB_BY_SLUG[slug];
      return el("span", { class: "chip" + (linkable ? " is-link" : ""), text: label,
        title: linkable ? "Open " + pkg + " in Packages" : null,
        on: linkable ? { click: function () { go("libs", slug); } } : null });
    }
    function note(kids) { return el("p", { class: "arch-note" }, kids); }
    var foot = null;

    if (!it) {
      head = [el("span", { class: "k", text: "ARCHITECTURE" }), el("span", { text: "Overview" })];
      body.push(el("div", { class: "insp-explain" }, [
        el("div", { class: "lbl", text: ARCH.summary ? "WHAT THIS IS" : "BUILT WITH" }),
        el("p", {}, ARCH.summary ? codeify(ARCH.summary) : [(ARCH.stack || []).join(" · ") || "no recognised stack"]),
      ]));
      body.push(sec("LAYERS", (ARCH.layers || []).filter(function (l) { return l.components.length; })
        .map(function (l) {
          return row(null, l.title, l.components.length, function () { go("arch", l.components[0]); }, l.body);
        })));
      var ups = A_LINKS.filter(function (l) { return l.dir === "up"; });
      if (ups.length)
        body.push(sec("WRONG-WAY IMPORTS · " + ups.length, [
          note(["A lower layer importing a higher one — often a shortcut, sometimes a real cycle. Worth a second look."]),
        ].concat(ups.slice(0, 20).map(function (l) {
          return row("⚠", A_COMP[l.s].title + " → " + A_COMP[l.t].title, l.n, function () { go("arch", l.s); });
        }))));
      if ((ARCH.declared_only || []).length)
        body.push(sec("DECLARED, NEVER IMPORTED", [note(["Listed in a manifest, but no indexed file imports it."])]
          .concat(ARCH.declared_only.map(function (d) { return row(null, d.label, d.source.split("/").pop(), null, d.source); }))));
      body.push(sec("HOW TO READ THIS", [
        archLegendRow("box", "a part of the app — a folder, or one file in a mixed folder"),
        archLegendRow("weak", "dashed = a best guess; click it to see why"),
        archLegendRow("cyl", "a data store the code talks to"),
        archLegendRow("cloud", "who drives the app, or an outside service"),
        archLegendRow("arrows", "one layer calls into another and gets results back"),
        archLegendRow("up", "a lower layer importing a higher one"),
        archLegendRow("dash", "shared code used by that layer"),
      ].concat(A_REQS.length ? [archLegendRow("req", "a web request: one part calls a route another part serves")] : [])));
      body.push(note(["Hover a box to light up what it talks to; click it for why it's in that layer."]));
      if (!ARCH.authored)
        body.push(note(["Every placement here is derived from folder names, entry points and imports. The ",
          el("code", { text: "codebase-to-course" }), " skill can name and correct them in ",
          el("code", { text: ".codemap/architecture.json" }), "."]));
    } else if (it.kind === "comp") {
      var c = it.obj, L = A_LAYER[c.layer] || {};
      head = [el("span", { class: "k", text: (L.title || c.layer).toUpperCase() }), el("span", { class: "arch-ht", text: c.title })];
      if (c.body)
        body.push(el("div", { class: "insp-explain" }, [el("div", { class: "lbl", text: "WHAT THIS IS" }), el("p", {}, codeify(c.body))]));
      body.push(el("div", { class: "arch-path" }, [el("code", { text: c.path }),
        el("span", { text: c.files.length + (c.files.length === 1 ? " file · " : " files · ") + c.loc + " loc" })]));
      var why = c.evidence.map(function (t) { return row(null, t, null); });
      if (!c.authored && c.confidence === "weak")
        why.push(note(["A best guess — nothing here names its layer clearly. Correct it in ",
          el("code", { text: ".codemap/architecture.json" }), "."]));
      body.push(sec("WHY IT'S HERE", why));
      if (c.tech.length)
        body.push(sec("BUILT WITH", [el("div", { class: "arch-chips" },
          c.tech.map(function (t) { return techChip(t.label, t.package); }))]));
      var outs = A_LINKS.filter(function (l) { return l.s === c.id; }).sort(function (a, b) { return b.n - a.n; });
      var ins = A_LINKS.filter(function (l) { return l.t === c.id; }).sort(function (a, b) { return b.n - a.n; });
      if (outs.length)
        body.push(sec("TALKS TO", outs.map(function (l) { return compRow(l.t, l.n, l.dir === "up"); })));
      if (ins.length)
        body.push(sec("USED BY", ins.map(function (l) { return compRow(l.s, l.n, l.dir === "up"); })));
      // requests over the web: no import joins these, so they aren't in TALKS TO / USED BY
      function reqRow(q, otherId) {
        var o = A_COMP[otherId];
        return o ? row("web", o.title, q.routes[0] + (q.n > 1 ? " +" + (q.n - 1) : ""),
          function () { go("arch", otherId); }, q.routes.join("\n")) : null;
      }
      var reqOut = A_REQS.filter(function (q) { return q.s === c.id; });
      var reqIn = A_REQS.filter(function (q) { return q.t === c.id; });
      if (reqOut.length) body.push(sec("SENDS WEB REQUESTS TO", reqOut.map(function (q) { return reqRow(q, q.t); })));
      if (reqIn.length) body.push(sec("SERVES WEB REQUESTS FROM", reqIn.map(function (q) { return reqRow(q, q.s); })));
      var reach = (c.stores || []).map(function (s) {
        var st = archFindById(A_STORES, s);
        return st ? row("store", st.label, st.kind, function () { go("arch", "store:" + s); }) : null;
      }).concat((c.services || []).map(function (s) {
        var sv = archFindById(A_SERVICES, s);
        return sv ? row("service", sv.label, sv.kind, function () { go("arch", "service:" + s); }) : null;
      }));
      if (reach.some(Boolean)) body.push(sec("DATA & SERVICES", reach));
      if (c.entries.length)
        body.push(sec("ENTRY POINTS", c.entries.map(function (ni) {
          var n = N[ni], ep = (EP_BY_NODE[ni] || [])[0] || {};
          return el("div", { class: "rowitem is-link", on: { click: function () { go("graph", n.key); } } }, [
            el("span", { class: "rk", text: ep.kind || "entry" }),
            el("span", { class: "rn", text: ep.detail || n.qual, title: n.qual }),
            el("button", { class: "arch-sim", title: "Simulate what happens from here", "aria-label": "Simulate " + n.qual,
              on: { click: function (ev) { ev.stopPropagation(); go("sim", "derive:" + n.key + "/0"); } } },
              [el("i", { class: "ph ph-play" })]),
          ]);
        })));
      body.push(sec("FILES", c.files.slice(0, 40).map(function (fi) {
        var f = FILES[fi];
        return row(null, c.files.length === 1 ? f.path : f.path.slice(c.path.length + 1) || f.path, f.loc + " loc",
          fileAct(f), "Open " + f.path + " in the Graph");
      }).concat(c.files.length > 40 ? [row(null, "+ " + (c.files.length - 40) + " more", null)] : [])));
      var folder = c.kind === "folder" ? c.path : dirGroup(c.path);
      if (FOLDER_BY_PATH[folder])
        foot = el("div", { class: "insp-foot" }, [
          el("button", { class: "btn", on: { click: function () { go("learn", folder); } } },
            ["Read about " + (folder === "(root)" ? "the project root" : folder + "/") + " in Learn"]),
        ]);
    } else {
      var o = it.obj;
      var kindLabel = it.kind === "store" ? "DATA STORE" : it.kind === "service" ? "OUTSIDE SERVICE" : "WHO DRIVES IT";
      head = [el("span", { class: "k", text: kindLabel }), el("span", { class: "arch-ht", text: o.label })];
      if (o.body)
        body.push(el("div", { class: "insp-explain" }, [el("div", { class: "lbl", text: "WHAT THIS IS" }), el("p", {}, codeify(o.body))]));
      if (it.kind === "actor") {
        body.push(note([o.entries + " entry point" + (o.entries === 1 ? "" : "s") +
          " — the places execution starts when " + o.label.toLowerCase() + " does something."]));
        var eps = [];
        o.components.forEach(function (cid) { (A_COMP[cid] ? A_COMP[cid].entries : []).forEach(function (ni) { eps.push(ni); }); });
        body.push(sec("ENTRY POINTS", eps.slice(0, 40).map(function (ni) {
          var ep = (EP_BY_NODE[ni] || [])[0] || {};
          return row(ep.kind || "entry", ep.detail || N[ni].qual, null, function () { go("graph", N[ni].key); }, N[ni].qual);
        })));
      } else {
        if (o.kind) body.push(el("div", { class: "arch-path" }, [el("span", { text: o.kind })]));
        if ((o.via || []).length)
          body.push(sec("HOW THE CODE REACHES IT", [el("div", { class: "arch-chips" },
            o.via.map(function (pkg) { return techChip(pkg, pkg); }))]));
        if ((o.declared_in || []).length)
          body.push(sec("DECLARED IN", o.declared_in.map(function (p) { return row(null, p, null); })));
        if (!o.components.length)
          body.push(note(["Declared, but no indexed file imports a client for it — it may be reached through an ORM, a URL in config, or a service outside this repo."]));
      }
      if (o.components.length)
        body.push(sec("USED BY", o.components.map(function (cid) { return compRow(cid, null, false); })));
    }

    var closeBtn = it ? el("button", { class: "arch-close", "aria-label": "Back to the overview",
      on: { click: function () { go("arch"); } } }, [el("i", { class: "ph ph-x" })]) : null;
    return el("aside", { class: "arch-insp" + (it ? " has-sel" : ""), "aria-label": "Architecture details" }, [
      el("div", { class: "insp-head" }, head.concat([el("span", { class: "spacer" }), closeBtn])),
      el("div", { class: "insp-body" }, body),
      foot,
    ]);
  }
  function archLegendRow(kind, text) {
    var s = el("svg", { width: 30, height: 20, viewBox: "0 0 30 20", class: "arch-sw", "aria-hidden": "true" });
    var hue = A_HUE.logic;
    if (kind === "box" || kind === "weak") {
      s.appendChild(el("rect", { x: 1.5, y: 3, width: 27, height: 14, rx: 3, class: "frame", stroke: hue,
        "stroke-dasharray": kind === "weak" ? "3 2" : null }));
      s.appendChild(el("rect", { x: 1.5, y: 3, width: 3, height: 14, rx: 1.5, fill: hue }));
    } else if (kind === "cyl") {
      s.appendChild(el("path", { class: "frame", d: archCylBody(7, 2, 16, 16, 3.5), stroke: A_HUE.data }));
      s.appendChild(el("ellipse", { class: "frame", cx: 15, cy: 5.5, rx: 8, ry: 3.5, stroke: A_HUE.data }));
    } else if (kind === "cloud") {
      s.appendChild(el("path", { class: "frame", d: archCloudPath(3, 3, 24, 15), stroke: A_SERVICE_HUE }));
    } else if (kind === "arrows") {
      var g = archArrowPair(15, 2, 18);
      g.setAttribute("transform", "translate(15 10) scale(0.55) translate(-15 -10)");
      s.appendChild(g);
    } else if (kind === "up") {
      s.appendChild(el("g", { class: "arch-up" }, [el("path", { d: "M 4 17 C 4 6 26 14 26 3" })]));
    } else if (kind === "dash") {
      s.appendChild(el("path", { class: "arch-side-line", d: "M 2 10 L 28 10" }));
    } else if (kind === "req") {
      s.appendChild(el("g", { class: "arch-req" }, [el("path", { d: "M 2 10 L 22 10" }),
        el("path", { class: "head", d: "M 28 10 l -7 -4 v 8 z" })]));
    }
    return el("div", { class: "rowitem flat" }, [s, el("span", { class: "rn", text: text })]);
  }

  // ---- simulate tab -------------------------------------
  // Three lanes feed the same normalized step shape (references/scenarios-schema.md):
  //   ⚡ derived  — computed right here, from the call graph + each edge's call-site
  //                    line (model.py attaches it; see `outCalls` above). Works on any
  //                    repo, authors nothing, and is honest that it's a guess: branches
  //                    and loops are shown as "could happen", never resolved either way.
  //   ✏ authored — DATA.sim.scenarios with source "authored", written by the
  //                    codebase-to-course skill into .codemap/scenarios.json.
  //   ⏺ recorded — DATA.sim.scenarios with source "recorded", produced by a real
  //                    `codemap trace` run (codemap/tracer.py) — real branches taken,
  //                    real loop counts, real output, real timing.
  // Every scenario ends up as {id, title, trigger, source, root, steps}. `steps` is
  // normalized lazily into an array where each entry already carries its own call-stack
  // snapshot (normalizeSteps) — paintSimStep(i) is a pure function of i, so scrubbing
  // is exact and instant and a deep link always renders the same frame.

  function excerptLineAt(n, line) {
    var src = srcOf(n);
    if (!src || !line || !n.line) return null;
    var idx = line - n.line[0];
    var lines = src.split("\n");
    return idx >= 0 && idx < lines.length ? lines[idx] : null;
  }
  function condFor(callerN, line) {
    var code = excerptLineAt(callerN, line) || "";
    if (/^\s*(for|while)\b/.test(code))
      return { kind: /^\s*for\b/.test(code) ? "for" : "while", text: "repeats — this can run more than once" };
    if (/^\s*(if|elif)\b/.test(code)) return { kind: "if", text: "only when this branch is taken" };
    if (/^\s*try\b/.test(code)) return { kind: "try", text: "only if nothing above this raises" };
    return null;
  }
  // A dead end with zero resolved calls is either genuine dynamic dispatch
  // (the target is looked up at runtime — getattr, a dict/list lookup then
  // immediately called, a callback stashed on an attribute like the CLI's
  // own `args.func(args)`) or just a leaf that only calls code outside this
  // repo (stdlib, a third-party client, …) — two different, both honest,
  // messages. Any line with a bare `(` used to count as "maybe dynamic
  // dispatch", which fired on almost every real function.
  var SIM_DYN_RE = /\bgetattr\s*\(|\]\s*\(|\.(?:func|fn|handler|callback|command|action|cb)\s*\(/;
  function classifyUnresolvedCalls(n) {
    var src = srcOf(n);
    if (!src) return null;
    var sawCall = false, sawDynamic = false;
    src.split("\n").forEach(function (ln) {
      if (/^\s*(def|class|@)/.test(ln)) return;
      if (SIM_DYN_RE.test(ln)) { sawDynamic = true; sawCall = true; return; }
      if (/\(/.test(ln)) sawCall = true;
    });
    if (sawDynamic) return "dynamic";
    if (sawCall) return "external";
    return null;
  }

  // HTTP routes (only when a codebase-memory index was merged in; model.py adds
  // `routes` then): routeOut[i] = the routes symbol i calls that some handler
  // in this repo serves. The call graph has no edge across that hop — the
  // request leaves this code and comes back in as a different entry point —
  // so a derived tree would otherwise stop at the API client call.
  var routeOut = N.map(function () { return []; });
  (DATA.routes || []).forEach(function (r) {
    if (!(r.handlers || []).length) return;
    (r.callers || []).forEach(function (c) { if (routeOut[c]) routeOut[c].push(r); });
  });
  function routeLabel(r) { return r.method && r.method !== "ANY" ? r.method + " " + r.url : r.url; }

  var SIM_BUDGET = 90, SIM_DEPTH_CAP = 7;
  function deriveSteps(rootI) {
    var steps = [], onStack = {};
    function visit(i, fromI, line, conf, depth, route) {
      if (steps.length >= SIM_BUDGET) { steps.truncated = true; return; }
      var n = N[i];
      var recursive = !!onStack[i];
      var cond = fromI != null && !route ? condFor(N[fromI], line) : null;
      var user, code;
      if (route) {
        user = "The server receives the request — this is where the backend code runs.";
        code = routeLabel(route) + " arrives at " + n.name + ".";
      } else if (fromI == null) {
        user = "You run this — nothing is on screen yet.";
        code = n.qual + " starts.";
      } else {
        code = N[fromI].name + " calls " + n.name + (line ? " at line " + line : "") + ".";
        user = depth > 2
          ? "Still waiting — several calls deep now, inside " + n.name + "."
          : "Still nothing on screen — execution just moved into " + n.name + ".";
      }
      var step = { t: "call", node: i, from: fromI, line: line || null, cond: cond, conf: conf || null,
        user: user, code: code };
      if (route) step.route = routeLabel(route);
      steps.push(step);
      if (recursive) {
        steps.push({ t: "note", node: i, user: "", code: n.name + " calls itself — folded here to keep the trace readable." });
      } else if (depth >= SIM_DEPTH_CAP) {
        steps.push({ t: "note", node: i, user: "", code: "trace depth limit reached here." });
      } else {
        // AMBIGUOUS calls (no import evidence, matched by name alone) never
        // get walked into here — a derived scenario used to be able to dive
        // straight into an unrelated same-named method (even a test file's)
        // and narrate it as if it were the real call.
        var calls = (outCalls[i] || []).filter(function (c) { return c.conf !== "AMBIGUOUS"; }).slice(0, 8);
        if (!calls.length) {
          var unresolvedKind = classifyUnresolvedCalls(n);
          if (unresolvedKind === "dynamic")
            steps.push({ t: "note", node: i, user: "", code: "⚡ dynamic dispatch — the indexer can't follow this call." });
          else if (unresolvedKind === "external")
            steps.push({ t: "note", node: i, user: "", code: "end of the line — only calls code outside this repo." });
        }
        onStack[i] = true;
        calls.forEach(function (c) { visit(c.t, i, c.line, c.conf, depth + 1); });
        (routeOut[i] || []).forEach(function (r) {
          r.handlers.slice(0, 2).forEach(function (h) {
            if (h === i || onStack[h]) return;
            steps.push({ t: "note", node: i, route: routeLabel(r),
              user: "The request leaves this code and travels over the network to the server.",
              code: n.name + " sends " + routeLabel(r) + " — the server's router hands it to " + N[h].name + "." });
            visit(h, i, null, null, depth + 1, r);
          });
        });
        onStack[i] = false;
      }
      steps.push({ t: "return", node: i, user: "", code: n.name + " finishes and returns to its caller." });
    }
    visit(rootI, null, null, null, 1);
    return steps;
  }

  var derivedCache = {};
  function derivedScenario(rootI) {
    var id = "derive:" + N[rootI].key;
    if (!derivedCache[id])
      // no `surface` here on purpose — resolveSurface() infers one from the
      // root node's entry-point kind / file / excerpt instead of defaulting
      // every derived scenario to a fake terminal.
      derivedCache[id] = { id: id, title: N[rootI].qual + " runs", source: "derived", root: rootI,
        trigger: { text: N[rootI].qual } };
    return derivedCache[id];
  }

  function edgeInfo(fromI, toI) {
    var calls = outCalls[fromI] || [];
    for (var k = 0; k < calls.length; k++) if (calls[k].t === toI) return calls[k];
    return null;
  }
  // "I see this — what made it?" (teacher's principles 5+6, run backwards): the
  // shortest real chain from *some* entry point down to a symbol, replayed as
  // a scenario instead of just read as the inspector's "ON PATH FROM" line.
  // This is control flow, not data flow — it shows what ran to reach this
  // line, never which value produced a number. Reverse fans out far faster
  // than forward (one symbol in this repo has 40+ callers) — shortestEntryChain()
  // already resolves that to a single path, so there's no branching factor to
  // cap the way deriveSteps() caps outCalls; a step's AMBIGUOUS badge (below,
  // same field the Graph tab shows per edge) is the honest flag that a hop
  // picked one of several same-named targets.
  function deriveOrigins(targetI) {
    var chain = shortestEntryChain(targetI);
    if (!chain || chain.length < 2) return null;   // already an entry point, or unreachable from one
    var steps = [];
    chain.forEach(function (i, d) {
      var fromI = d > 0 ? chain[d - 1] : null, n = N[i];
      var edge = fromI != null ? edgeInfo(fromI, i) : null;
      var user, code;
      if (fromI == null) {
        var epLabel = (n.entry[0] || "").split(":").slice(1).join(":") || n.qual;
        user = "This is where the app actually starts: " + epLabel + ".";
        code = n.qual + " is an entry point — nothing in this repo calls it; something outside does.";
      } else {
        code = N[fromI].name + " calls " + n.name + (edge && edge.line ? " at line " + edge.line : "") + ".";
        user = d === chain.length - 1
          ? "This call is what actually reaches the symbol you started from."
          : "One step closer — execution is now inside " + n.name + ".";
      }
      steps.push({ t: "call", node: i, from: fromI, line: (edge && edge.line) || null,
        conf: (edge && edge.conf) || null, user: user, code: code });
    });
    for (var d2 = chain.length - 1; d2 >= 0; d2--)
      steps.push({ t: "return", node: chain[d2], user: "",
        code: N[chain[d2]].name + " finishes and returns to its caller." });
    return steps;
  }
  var originCache = {};
  function originScenario(targetI) {
    var id = "origin:" + N[targetI].key;
    if (originCache[id]) return originCache[id];
    var steps = deriveOrigins(targetI);
    if (!steps) return null;
    return (originCache[id] = { id: id, title: "How " + N[targetI].qual + " gets reached",
      source: "derived", trigger: { text: N[targetI].qual }, _raw: steps });
  }
  var SIM_SCENARIOS = [];
  var SIM_AUTHORED = (DATA.sim && DATA.sim.scenarios || []);
  SIM_AUTHORED.forEach(function (s) {
    // `steps: []` from simulate.py is a root-only curriculum entry — leave _raw
    // null so scenarioSteps() derives the tree from `root` client-side.
    s._raw = (s.steps && s.steps.length) ? s.steps : null;
    SIM_SCENARIOS.push(s);
  });
  // Lane 1 (⚡ derived) fills the rail only when the skill authored nothing.
  // With a curriculum present the rail *is* the curriculum; derive:<key> stays
  // reachable on demand via ensureScenario (deep links, Map ▸ "Simulate ▶").
  if (!SIM_AUTHORED.length)
    traceRoots.slice(0, 6).forEach(function (r) { SIM_SCENARIOS.push(derivedScenario(r.i)); });
  var simById = {};
  SIM_SCENARIOS.forEach(function (s) { simById[s.id] = s; });
  function ensureScenario(id) {
    if (simById[id]) return simById[id];
    if (id && id.indexOf("derive:") === 0) {
      var idx = keyToI[id.slice(7)];
      if (idx != null) {
        var sc = derivedScenario(idx);
        if (!simById[sc.id]) { simById[sc.id] = sc; SIM_SCENARIOS.push(sc); }
        return sc;
      }
    }
    if (id && id.indexOf("origin:") === 0) {
      var oi = keyToI[id.slice(7)];
      if (oi != null) {
        var osc = originScenario(oi);
        if (osc && !simById[osc.id]) { simById[osc.id] = osc; SIM_SCENARIOS.push(osc); }
        return osc;
      }
    }
    return null;
  }

  // ── rail grouping: a curriculum, ordered by how the app runs ──────────
  // `group` is the section header, `order` the workflow position (ascending);
  // with neither, fall back to BFS depth from the entry seeds so even an
  // un-authored repo reads roughly in call order.
  var SIM_GROUP_CAP = 8;                 // rows shown per group before "+N more"
  var simGroupOpen = {};                 // group name -> explicit open/closed
  var simGroupExpand = {};               // group name -> "+N more" opened
  var simRailFilter = "";
  function simGroupOf(sc) {
    return sc.group || (sc.source === "derived" ? "Auto-derived" : "Scenarios");
  }
  function simOrderOf(sc) {
    if (typeof sc.order === "number") return sc.order;
    var d = sc.root != null && flowDepth[sc.root] != null && flowDepth[sc.root] >= 0 ? flowDepth[sc.root] : 500;
    return 1000 + d;
  }
  function simSorted() {
    return SIM_SCENARIOS.slice().sort(function (a, b) {
      return simOrderOf(a) - simOrderOf(b) ||
        simGroupOf(a).localeCompare(simGroupOf(b)) ||
        (a.title || "").localeCompare(b.title || "");
    });
  }

  // A recorded (Lane 3) step carries no narration and no explicit `from` —
  // a real run has facts, not prose. Both are filled in here from the stack
  // itself so a trace with zero authoring still gets a readable player: a
  // drawable edge (`from` = whoever was on top of the stack), and a plain-
  // fact sentence in the same voice the derived lane already uses.
  function defaultNarration(st, callerI) {
    var n = N[st.node];
    if (st.t === "call") {
      if (callerI == null) return { user: "You run this — nothing is on screen yet.", code: n.qual + " starts." };
      var caller = N[callerI];
      return {
        user: "Still nothing on screen — execution just moved into " + n.name + ".",
        code: (caller ? caller.name : "the caller") + " calls " + n.name + (st.line ? " at line " + st.line : "") + ".",
      };
    }
    if (st.t === "return") return { user: "", code: n.name + " finishes and returns to its caller." };
    if (st.t === "emit") return { user: "", code: n.name + " produces output." };
    return { user: "", code: "" };
  }
  function normalizeSteps(raw) {
    var stack = [], out = [], prevTs = null;
    var libsSeen = {};   // library name -> shown once already this scenario
    raw.forEach(function (st) {
      var callerBefore = stack.length ? stack[stack.length - 1] : null;
      var from = st.from != null ? st.from : (st.t === "call" ? callerBefore : null);
      if (st.t === "call") stack.push(st.node);
      var snap = stack.slice();
      var dur = st.t === "note" ? 650 : st.t === "emit" ? 380 : 520;
      if (typeof st.ts === "number") {
        dur = prevTs == null ? 200 : Math.max(90, Math.min(2200, st.ts - prevTs));
        prevTs = st.ts;
      }
      var hasText = (st.user && st.user.trim()) || (st.code && st.code.trim());
      var fallback = hasText ? null : defaultNarration(st, from);
      // show a library's blurb inline the first time a call touches it — after
      // that the learner already saw it, and repeating it every step would
      // turn a 15-40 step thread into a glossary (see item 6, plan doc)
      var libs = null;
      if (st.t === "call") {
        var fresh = (libByNode[st.node] || []).filter(function (nm) { return !libsSeen[nm]; });
        if (fresh.length) {
          fresh.forEach(function (nm) { libsSeen[nm] = true; });
          libs = fresh.map(libFor);
        }
      }
      out.push({
        t: st.t, node: st.node, from: from,
        line: st.line || null, cond: st.cond || null, conf: st.conf || null,
        user: (st.user && st.user.trim()) || (fallback ? fallback.user : ""),
        code: (st.code && st.code.trim()) || (fallback ? fallback.code : ""),
        emit: st.emit || null, args: st.args || null,
        depth: snap.length, stack: snap, dur: dur, libs: libs,
      });
      if (st.t === "return" && stack.length) stack.pop();
    });
    return out;
  }
  function scenarioSteps(sc) {
    if (!sc._norm) {
      if (!sc._raw) {
        sc._raw = deriveSteps(sc.root);
        if (sc._raw.truncated) sc.truncated = true;
      }
      sc._norm = normalizeSteps(sc._raw);
    }
    return sc._norm;
  }
  function simCurrent() { return state.simScenario ? simById[state.simScenario] : null; }
  function stepReached(steps, i, node) {
    for (var k = 0; k <= i; k++) if (steps[k].node === node) return true;
    return false;
  }

  // layout is computed once per scenario, not per step — nodes never move while playing.
  function layoutSim(steps) {
    var order = [], seen = {}, depthOf = {};
    steps.forEach(function (st) {
      if (st.t === "call" && !seen[st.node]) { seen[st.node] = true; order.push(st.node); depthOf[st.node] = st.depth; }
    });
    var COLW = 190, ROWH = 46, TOP = 26, LEFT = 20;
    var cols = {};
    order.forEach(function (ni) { (cols[depthOf[ni]] = cols[depthOf[ni]] || []).push(ni); });
    var depths = Object.keys(cols).map(Number).sort(function (a, b) { return a - b; });
    var pos = {};
    depths.forEach(function (d) {
      cols[d].forEach(function (ni, k) { pos[ni] = { x: LEFT + (d - 1) * COLW, y: TOP + k * ROWH }; });
    });
    var maxRows = Math.max.apply(null, depths.map(function (d) { return cols[d].length; }).concat([1]));
    return {
      pos: pos, order: order,
      W: LEFT * 2 + Math.max(1, depths.length) * COLW,
      H: TOP * 2 + maxRows * ROWH,
    };
  }

  // one arrowhead marker per distinct folder hue in play, so a static edge
  // (no animation, e.g. right after a scrub) still shows which way a call
  // goes — `orient="auto-start-reverse"` points it along the path's own
  // tangent, so it's correct regardless of which side of its caller a
  // node's column ends up on (a symbol called from more than one depth can
  // legitimately sit to the LEFT of a later caller — the arrow, not screen
  // position, is what's authoritative).
  function arrowMarkerId(hex) { return "sim-arrow-" + hex.replace("#", ""); }
  function buildArrowMarker(id, hex) {
    var m = el("marker", { id: id, viewBox: "0 0 10 10", refX: "8.4", refY: "5",
      markerWidth: "6.5", markerHeight: "6.5", orient: "auto-start-reverse" });
    m.appendChild(el("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: hex }));
    return m;
  }
  // our dendrite() paths are always one cubic segment, "M ax ay C c1x c1y
  // c2x c2y bx by" — swapping the endpoint and reversing the control-point
  // order retraces the identical visual curve backwards. Used so a RETURN's
  // token travels the same edge back to the caller instead of re-animating
  // forward along it (animateMotion always walks a path start->end).
  var CUBIC_D_RE = /^M\s+([-\d.]+)\s+([-\d.]+)\s+C\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)$/;
  function reverseCubicD(d) {
    var m = d.match(CUBIC_D_RE);
    if (!m) return d;
    return "M " + m[7] + " " + m[8] + " C " + m[5] + " " + m[6] + " " + m[3] + " " + m[4] + " " + m[1] + " " + m[2];
  }
  function buildSimFlow(steps, layout) {
    var svg = el("svg", { class: "sim-flow", width: layout.W, height: layout.H,
      viewBox: "0 0 " + layout.W + " " + layout.H });
    var defs = el("defs", {});
    var edgeLayer = el("g", { class: "sim-edges", fill: "none" });
    var nodeLayer = el("g", { class: "sim-nodes" });
    svg.appendChild(defs); svg.appendChild(edgeLayer); svg.appendChild(nodeLayer);
    var edgeEls = {}, nodeEls = {}, seenEdge = {}, seenMarker = {};
    steps.forEach(function (st) {
      if (st.t !== "call" || st.from == null) return;
      var key = st.from + ">" + st.node;
      if (seenEdge[key]) return;
      seenEdge[key] = true;
      var a = layout.pos[st.from], b = layout.pos[st.node];
      if (!a || !b) return;
      var hue = colorForNode(N[st.node]);
      var markerId = arrowMarkerId(hue);
      if (!seenMarker[markerId]) { seenMarker[markerId] = true; defs.appendChild(buildArrowMarker(markerId, hue)); }
      var d = dendrite({ x: a.x + 78, y: a.y + 14 }, { x: b.x, y: b.y + 14 }, hashSeed(key), { bow: 0.26 });
      var p = el("path", { class: "sim-edge", d: d.d, stroke: hue, "stroke-width": 1.4,
        "marker-end": "url(#" + markerId + ")" });
      edgeLayer.appendChild(p);
      edgeEls[key] = p;
    });
    layout.order.forEach(function (ni) {
      var p = layout.pos[ni], n = N[ni];
      var g = el("g", { class: "sim-node pending", transform: "translate(" + p.x + "," + p.y + ")",
        on: { click: function () { go("graph", n.key); } } }, [el("title", { text: n.qual })]);
      g.appendChild(el("circle", { class: "sim-dot", cx: 8, cy: 14, r: 8,
        fill: hexA(colorForNode(n), 0.85), stroke: colorForNode(n), "stroke-width": 1.4 }));
      g.appendChild(el("text", { x: 22, y: 11, class: "sim-lbl", text: fitText(n.name, 150, 6.2) }));
      g.appendChild(el("text", { x: 22, y: 23, class: "sim-sub", text: fitText(n.file.split("/").pop(), 150, 5.2) }));
      nodeLayer.appendChild(g);
      nodeEls[ni] = g;
    });
    return { svg: svg, nodeEls: nodeEls, edgeEls: edgeEls };
  }

  // Trace log — replaces the old call-stack snapshot. Every step 0..end is a
  // row, built once at mount; paintTraceLog() only moves the `.cur` marker and
  // scrolls it into view, so the whole run stays scrollable and every past row
  // stays clickable to jump back to. The breadcrumb strip up top keeps the
  // live call-stack readout the snapshot pane used to give.
  var SIM_LOG_GLYPH = { call: "→", return: "←", emit: "»", note: "·", branch: "◇" };
  function traceLogCrumb(stack) {
    if (!stack || !stack.length) return "· idle ·";
    return stack.map(function (ni) { return N[ni].name; }).join("  ›  ");
  }
  function buildTraceLog(m) {
    var crumbText = traceLogCrumb(m.steps[0] && m.steps[0].stack);
    var crumb = el("div", { class: "sim-log-crumb", title: crumbText, text: crumbText });
    var scroll = el("div", { class: "sim-log-scroll" });
    m.logRows = m.steps.map(function (st, k) {
      var n = N[st.node];
      var isOut = st.t === "emit" && st.emit;
      var row = el("div", { class: "sim-log-row t-" + st.t,
        style: "padding-left:" + (8 + Math.max(0, (st.depth || 1) - 1) * 12) + "px",
        on: { click: function () { simSetStep(k); } } }, [
        el("span", { class: "sim-log-i", text: String(k) }),
        el("span", { class: "sim-log-glyph", text: SIM_LOG_GLYPH[st.t] || "·" }),
        el("span", { class: "sim-log-name", text: n.name }),
        el("span", { class: isOut ? "sim-log-out" : "sim-log-code",
          title: isOut ? st.emit.text : (st.code || ""),
          text: isOut ? st.emit.text : (st.code || "") }),
      ]);
      scroll.appendChild(row);
      return row;
    });
    m.logCrumb = crumb;
    m.logScrollEl = scroll;
    m.curLogRow = null;
    return el("div", { class: "sim-loglist" }, [crumb, scroll]);
  }
  // keep the current row visible *within the log's own scroller* — never
  // Element.scrollIntoView(), which walks up and scrolls .sim-pane-body /
  // .sim-body too, dragging the pinned breadcrumb and the whole Simulate
  // body along with it.
  function scrollLogRowIntoView(scroll, row) {
    if (!scroll || !row) return;   // .sim-log-scroll is position:relative, so offsetTop is scroller-local
    var pad = 8, top = row.offsetTop, bot = top + row.offsetHeight;
    if (top < scroll.scrollTop + pad) scroll.scrollTop = Math.max(0, top - pad);
    else if (bot > scroll.scrollTop + scroll.clientHeight - pad)
      scroll.scrollTop = bot - scroll.clientHeight + pad;
  }
  function paintTraceLog(m, i) {
    if (!m.logRows) return;
    if (m.curLogRow) m.curLogRow.classList.remove("cur");
    var row = m.logRows[i];
    // update the crumb first — it can reflow (a deeper stack is a longer line),
    // and scrollLogRowIntoView must measure the log scroller *after* that
    var crumb = traceLogCrumb(m.steps[i] && m.steps[i].stack);
    m.logCrumb.textContent = crumb;
    m.logCrumb.title = crumb;
    if (row) { row.classList.add("cur"); scrollLogRowIntoView(m.logScrollEl, row); }
    m.curLogRow = row || null;
  }

  function sourcePane(n, activeLine) {
    var box = el("div", { class: "sim-source" });
    box.appendChild(el("div", { class: "sim-source-hd" }, [
      el("i", { class: fileIcon(fileByPath[n.file] ? fileByPath[n.file].lang : "") }),
      el("span", { text: n.file }),
    ]));
    var src = srcOf(n);
    if (!src) {
      box.appendChild(el("div", { class: "sim-source-empty", text: "source not captured for this symbol" }));
      return box;
    }
    var body = el("div", { class: "sim-source-body" });
    src.split("\n").forEach(function (ln, i) {
      var lineNo = n.line[0] + i;
      body.appendChild(el("div", { class: "sim-line" + (lineNo === activeLine ? " active" : "") }, [
        el("span", { class: "sim-lineno", text: String(lineNo) }),
        el("span", { class: "sim-code", text: ln }),
      ]));
    });
    box.appendChild(body);
    return box;
  }

  function narrationPane(st) {
    var box = el("div", { class: "sim-narr", "aria-live": "polite" });
    // ph-user-fill isn't a real Phosphor icon (the filled variant is a
    // separate style class, not a name suffix — see ph-fill ph-circle
    // just above) so this always rendered a blank glyph, network or not.
    box.appendChild(el("div", { class: "sim-narr-row user" }, [el("i", { class: "ph-fill ph-user" }), st.user || "—"]));
    box.appendChild(el("div", { class: "sim-narr-row code" }, [el("i", { class: "ph ph-gear-fine" }), st.code || "—"]));
    if (st.cond)
      box.appendChild(el("div", { class: "sim-narr-cond" },
        [(st.cond.kind === "for" || st.cond.kind === "while" ? "↻ " : st.cond.kind === "try" ? "⚠ " : "◇ ") + st.cond.text]));
    if (st.conf === "AMBIGUOUS")
      box.appendChild(el("div", { class: "sim-narr-cond", text: "◇ one of several same-named targets — shown as the most likely" }));
    if (st.t === "call" && st.from != null && st.line != null && N[st.from]) {
      var callerN = N[st.from], callLine = st.line;
      box.appendChild(el("div", { class: "sim-narr-callsite",
        on: { click: function () {
          state.srcTargetLine = callLine;
          go("graph", callerN.key + "/src");
        } } },
        [el("i", { class: "ph ph-arrow-square-out" }),
          "called from " + callerN.name + ", line " + callLine]));
    }
    (st.libs || []).forEach(function (lib) {
      var blurb = lib.here || lib.general;
      box.appendChild(el("div", { class: "sim-narr-lib",
        on: { click: function () { go("libs", lib.slug); } } },
        [el("i", { class: "ph ph-package" }),
          el("b", { text: lib.name }), " — " + (blurb || "open in Learn ▸")]));
    });
    return box;
  }
  // A step's narration box height varies with how much text/cond/libs it
  // carries; sizing it per-step made the whole panes grid visibly resize as
  // you scrubbed. Instead, size it once per scenario to its tallest step
  // (capped — a pathological step with five library blurbs shouldn't blow
  // the layout up either), and let anything past that scroll internally.
  function estimateNarrHeight(st) {
    var rows = 2;   // the user + code rows are always rendered, even as "—"
    if (st.cond) rows++;
    if (st.conf === "AMBIGUOUS") rows++;
    var libN = (st.libs || []).length;
    rows += libN;
    var h = 22 + rows * 20 + libN * 6;   // box padding + ~1 line/row, libs add their own border+padding
    if ((st.user || "").length > 60) h += 19;   // a long sentence typically wraps to a 2nd line
    if ((st.code || "").length > 60) h += 19;
    return h;
  }
  function narrBoxHeight(steps) {
    var max = 0;
    steps.forEach(function (st) { max = Math.max(max, estimateNarrHeight(st)); });
    return Math.max(56, Math.min(160, max));
  }

  // ── adaptive Stage surface ──────────────────────────────────────────
  // Which of the seven Stage renderers below a scenario gets is resolved
  // once per scenario (cached on sc._surface — same pattern as sc._raw/
  // sc._norm) rather than trusting `trigger.surface` alone: an authored
  // scenario that set it always wins, but a *derived* scenario never sets
  // it (derivedScenario() leaves it out on purpose), so most scenarios
  // reach this ladder. Evidence, in order: an explicit trigger surface, the
  // majority surface actually emitted, the root symbol's entry-point kind
  // (route/task/cli/... — already computed server-side by entrypoints.py
  // and carried on every node as `entry`), then a few content heuristics
  // over that node's file path / excerpt. `terminal` is the final fallback,
  // matching today's behaviour for anything unrecognised.
  var SIM_ENTRY_SURFACE = { task: "job", cli: "terminal", script: "terminal", main: "terminal", docker: "terminal" };
  var SIM_UI_FILE_RE = /\.(tsx|jsx|vue|svelte)$/i;
  var SIM_UI_PATH_RE = /(^|\/)(components?|pages?|views?|ui|frontend)(\/|$)/i;
  // deliberately NOT a bare `<Word>` sniff — that also matches a C `#include
  // <stdio.h>`, a docstring placeholder like `<path>`, or `<redacted>`. Require
  // either a real closing tag or an attribute (`=`) alongside the angle
  // brackets, so only actual markup trips it.
  var SIM_UI_EXCERPT_RE = /<\/[A-Za-z]|<[A-Za-z][\w.-]*\s+[\w-]+=|className=|useState\(|styled\./;
  var SIM_DB_EXCERPT_RE = /\b(SELECT|INSERT INTO|UPDATE|DELETE FROM|BEGIN|COMMIT)\b|\bcursor\.|\.execute\(|session\.query/i;
  var SIM_DB_PATH_RE = /(^|\/)(db|repository|dao|models?|queries|store)(\/|$)/i;
  var SIM_JOB_EXCERPT_RE = /threading\.Thread|asyncio\.create_task|\.delay\(|\.apply_async\(/;
  var SIM_JOB_NAME_RE = /(worker|task|job|queue|celery|cron)/i;
  // deliberately narrow: only text that's actual evidence of an HTML
  // response (a template render call, or a literal <html> tag), not any
  // `.render(` (React components call that too) or a bare `redirect(` (an
  // API redirects just as often as a page does)
  var SIM_HTML_RE = /render_template|HTMLResponse|res\.render|<html/i;
  var SIM_WRITE_RE = /open\([^)]*["']w|\.write\(|\.write_text\(|to_csv|json\.dump/;
  function simSurfaceFromContent(n) {
    if (!n) return null;
    var file = n.file || "", excerpt = srcOf(n) || "", name = n.name || "";
    if (SIM_UI_FILE_RE.test(file) || SIM_UI_PATH_RE.test(file) || SIM_UI_EXCERPT_RE.test(excerpt)) return "ui";
    if (SIM_DB_EXCERPT_RE.test(excerpt) || SIM_DB_PATH_RE.test(file)) return "db";
    if (SIM_JOB_EXCERPT_RE.test(excerpt) || SIM_JOB_NAME_RE.test(file) || SIM_JOB_NAME_RE.test(name)) return "job";
    if (SIM_WRITE_RE.test(excerpt)) return "file";
    return null;
  }
  // A route defaults to "api" — most routes in a codebase worth simulating
  // are JSON endpoints — and only reads as "browser" (a rendered page) when
  // the source actually shows evidence of an HTML response.
  function simSurfaceForRoute(n) {
    var entry = (n.entry && n.entry[0]) || "";
    var detail = entry.split(":").slice(1).join(":");     // "POST /tools/refresh-data/"
    var path = detail.replace(/^\S+\s+/, "");              // strip the leading method
    if (/^\/api(\/|$)/i.test(path)) return "api";
    if (SIM_HTML_RE.test(srcOf(n) || "")) return "browser";
    return "api";
  }
  function simSurfaceFromEntry(n) {
    if (!n || !n.entry || !n.entry.length) return null;
    for (var idx = 0; idx < n.entry.length; idx++) {
      var kind = n.entry[idx].split(":")[0];
      if (kind === "route" || kind === "controller") return simSurfaceForRoute(n);
      if (SIM_ENTRY_SURFACE[kind]) return SIM_ENTRY_SURFACE[kind];
    }
    return null;
  }
  function simMajorityEmitSurface(steps) {
    var counts = {}, best = null, bestN = 0;
    steps.forEach(function (st) {
      if (st.emit && st.emit.surface) {
        var s = st.emit.surface;
        counts[s] = (counts[s] || 0) + 1;
        if (counts[s] > bestN) { best = s; bestN = counts[s]; }
      }
    });
    return best;
  }
  function simRootNode(sc, steps) {
    if (sc.root != null && N[sc.root]) return N[sc.root];
    if (steps.length && N[steps[0].node]) return N[steps[0].node];
    return null;
  }
  function resolveSurface(sc, steps) {
    if (sc._surface) return sc._surface;
    var surface = (sc.trigger && sc.trigger.surface) || simMajorityEmitSurface(steps);
    if (!surface) {
      var n = simRootNode(sc, steps);
      surface = simSurfaceFromEntry(n) || simSurfaceFromContent(n) || "terminal";
    }
    sc._surface = surface;
    return surface;
  }
  // An emit with no `surface` of its own (the common case — scenarios.py no
  // longer coerces one) belongs to whatever the scenario resolved to.
  function stepEmitSurface(st, fallback) {
    return st.emit ? (st.emit.surface || fallback) : null;
  }
  function prettyMaybeJson(text) {
    var t = text.trim();
    if (t[0] === "{" || t[0] === "[") {
      try { return JSON.stringify(JSON.parse(t), null, 2); } catch (e) { /* not JSON — show as-is */ }
    }
    return text;
  }

  // steps use a 1-based depth (root call = 1); "done" = the last step, back
  // at the root frame, and not still entering a call.
  function simDoneAt(steps, i) {
    return i === steps.length - 1 && steps[i].depth <= 1 && steps[i].t !== "call";
  }
  // the last `return <expr>` in the root's own source — a plain-text stand-in
  // for "what this call actually produced" when no authored/recorded emit
  // says so explicitly.
  function summarizeReturn(n) {
    var src = n && srcOf(n);
    if (!src) return null;
    var last = null;
    src.split("\n").forEach(function (ln) {
      var m = ln.match(/^\s*return\b\s*(.*)$/);
      if (m) last = m[1].replace(/[;]+\s*$/, "").trim();
    });
    if (!last) return null;
    return last.length > 60 ? last.slice(0, 59) + "…" : last;
  }
  function simFinishedText(n) {
    var summary = summarizeReturn(n);
    return summary ? "200 · returned " + summary : "200 · done";
  }

  function terminalStage(sc, steps, i, surface) {
    var box = el("div", { class: "sim-term" });
    box.appendChild(el("div", { class: "sim-term-hd" }, [
      el("span", { class: "sim-term-dot r" }), el("span", { class: "sim-term-dot y" }), el("span", { class: "sim-term-dot g" }),
    ]));
    var body = el("div", { class: "sim-term-body" });
    body.appendChild(el("div", { class: "sim-term-cmd", text: "$ " + ((sc.trigger && sc.trigger.text) || sc.title) }));
    for (var k = 0; k <= i; k++) {
      var em = steps[k].emit;
      if (em && stepEmitSurface(steps[k], surface) === "terminal")
        body.appendChild(el("div", { class: "sim-term-line" + (em.stream === "stderr" ? " err" : ""), text: em.text }));
    }
    if (!simDoneAt(steps, i)) body.appendChild(el("span", { class: "sim-term-cursor" }));
    box.appendChild(body);
    return box;
  }
  function browserStage(sc, steps, i, surface) {
    var box = el("div", { class: "sim-browser" });
    var urlEl = el("span", { class: "sim-browser-url", text: (sc.trigger && sc.trigger.text) || "" });
    box.appendChild(el("div", { class: "sim-browser-bar" }, [el("span", { class: "sim-browser-dot" }), urlEl]));
    var page = el("div", { class: "sim-browser-page" });
    var lines = [];
    for (var k = 0; k <= i; k++) {
      var st = steps[k], em = st.emit;
      if (em && stepEmitSurface(st, surface) === "browser") {
        lines.push(em.text);
        // a narrated redirect/navigation updates the URL bar so the page
        // reflects where the browser actually ended up, not just where it started
        var navigated = em.text.match(/(?:redirect(?:ed|s|ing)?|navigat\w*)\s+to\s+(\S+)/i);
        if (navigated) urlEl.textContent = navigated[1].replace(/[.,;:]+$/, "");
      }
    }
    if (!lines.length) {
      if (simDoneAt(steps, i)) {
        page.appendChild(el("div", { class: "sim-browser-line", text: simFinishedText(N[steps[i].node]) }));
      } else {
        var waiting = N[steps[i].node];
        page.appendChild(el("div", { class: "sim-browser-spinner" }));
        page.appendChild(el("div", { class: "sim-browser-wait",
          text: "waiting on the server" + (waiting ? " — " + waiting.name + " is still running" : "") }));
      }
    } else {
      lines.forEach(function (text) { page.appendChild(el("div", { class: "sim-browser-line", text: text })); });
    }
    box.appendChild(page);
    return box;
  }
  function apiStage(sc, steps, i, surface) {
    var box = el("div", { class: "sim-api" });
    var triggerText = (sc.trigger && sc.trigger.text) || "";
    var m = triggerText.match(/^([A-Z]+)\s+(\S+)/);
    var method = m ? m[1] : "", path = m ? m[2] : triggerText;
    box.appendChild(el("div", { class: "sim-api-head" }, [
      method ? el("span", { class: "sim-api-method sim-api-method-" + method.toLowerCase(), text: method }) : null,
      el("span", { class: "sim-api-path", text: path || "…" }),
    ]));
    var msgs = [];
    for (var k = 0; k <= i; k++) {
      var em = steps[k].emit;
      if (em && stepEmitSurface(steps[k], surface) === "api") msgs.push(em.text);
    }
    if (!msgs.length) {
      box.appendChild(el("div", { class: "sim-api-card" }, [el("div", { class: "sim-api-lbl", text: "REQUEST" }),
        el("div", { class: "sim-api-body", text: triggerText || "…" })]));
      if (simDoneAt(steps, i)) {
        var doneText = simFinishedText(N[steps[i].node]);
        var doneLbl = el("div", { class: "sim-api-lbl" }, ["RESPONSE",
          el("span", { class: "sim-api-status sim-api-status-2xx", text: "200" })]);
        box.appendChild(el("div", { class: "sim-api-card" }, [doneLbl, el("div", { class: "sim-api-body", text: doneText })]));
      } else {
        box.appendChild(el("div", { class: "sim-api-card sim-api-pending" }, [el("div", { class: "sim-api-lbl", text: "RESPONSE" }),
          el("div", { class: "sim-api-body", text: "waiting…" })]));
      }
    } else {
      msgs.forEach(function (text, idx) {
        var status = text.match(/\b([1-5])\d\d\b/);
        var lbl = el("div", { class: "sim-api-lbl" }, [idx === 0 ? "REQUEST" : idx === 1 ? "RESPONSE" : "RESPONSE #" + idx]);
        if (status) lbl.appendChild(el("span", { class: "sim-api-status sim-api-status-" + status[1] + "xx", text: status[0] }));
        box.appendChild(el("div", { class: "sim-api-card" }, [lbl, el("div", { class: "sim-api-body", text: prettyMaybeJson(text) })]));
      });
    }
    return box;
  }
  function uiStage(sc, steps, i, surface) {
    var box = el("div", { class: "sim-ui" });
    var root = simRootNode(sc, steps);
    box.appendChild(el("div", { class: "sim-ui-frame-hd", text: root ? (root.name || root.qual) : "UI" }));
    var tree = el("div", { class: "sim-ui-tree" });
    var mounted = {}, activeNode = steps[i].node;
    for (var k = 0; k <= i; k++) {
      var st = steps[k];
      if (st.t !== "call" || mounted[st.node]) continue;
      mounted[st.node] = true;
      var n = N[st.node];
      if (!n) continue;
      var depth = Math.max(0, (st.depth || 1) - 1);
      tree.appendChild(el("div", {
        class: "sim-ui-chip" + (st.node === activeNode ? " active" : " mounted"),
        style: "margin-left:" + (depth * 16) + "px",
      }, [el("i", { class: "ph ph-caret-right" }), el("span", { text: n.name })]));
    }
    box.appendChild(tree);
    var blocks = el("div", { class: "sim-ui-content" });
    for (var j = 0; j <= i; j++) {
      var em = steps[j].emit;
      if (em && stepEmitSurface(steps[j], surface) === "ui") blocks.appendChild(el("div", { class: "sim-ui-block", text: em.text }));
    }
    if (blocks.childNodes.length) box.appendChild(blocks);
    box.appendChild(el("div", { class: "sim-ui-footer",
      text: simDoneAt(steps, i) ? "mounted" : "mounting · step " + (i + 1) + " of " + steps.length }));
    return box;
  }
  function jobStage(sc, steps, i, surface) {
    var box = el("div", { class: "sim-job" });
    var st = steps[i];
    var done = simDoneAt(steps, i);
    var state = i === 0 ? "queued" : (done ? "done" : "running");
    box.appendChild(el("div", { class: "sim-job-hd" }, [
      el("span", { class: "sim-job-pill sim-job-pill-" + state, text: state }),
      el("span", { class: "sim-job-title", text: (sc.trigger && sc.trigger.text) || sc.title }),
    ]));
    var log = el("div", { class: "sim-job-log" }), n = 0;
    for (var k = 0; k <= i; k++) {
      var em = steps[k].emit;
      if (em && stepEmitSurface(steps[k], surface) === "job") { log.appendChild(el("div", { class: "sim-job-line", text: em.text })); n++; }
    }
    if (!n) log.appendChild(el("div", { class: "sim-job-line pending", text: "working…" }));
    box.appendChild(log);
    box.appendChild(el("div", { class: "sim-job-footer", text: n + " log line" + (n === 1 ? "" : "s") + " · step " + (i + 1) + " of " + steps.length }));
    return box;
  }
  function dbStage(sc, steps, i, surface) {
    var box = el("div", { class: "sim-db" });
    var list = el("div", { class: "sim-db-list" }), count = 0;
    for (var k = 0; k <= i; k++) {
      var em = steps[k].emit;
      if (!(em && stepEmitSurface(steps[k], surface) === "db")) continue;
      var text = em.text.trim();
      if (/^(BEGIN|COMMIT|ROLLBACK)\b/i.test(text)) list.appendChild(el("div", { class: "sim-db-marker", text: "● " + text.toUpperCase() }));
      else { list.appendChild(el("div", { class: "sim-db-stmt", text: text })); count++; }
    }
    if (!list.childNodes.length)
      list.appendChild(el("div", { class: "sim-db-stmt pending",
        text: simDoneAt(steps, i) ? "done" : "waiting on the database…" }));
    box.appendChild(list);
    box.appendChild(el("div", { class: "sim-db-footer", text: count + " statement" + (count === 1 ? "" : "s") }));
    return box;
  }
  function fileStage(sc, steps, i, surface) {
    var rows = [];
    for (var k = 0; k <= i; k++) {
      var em = steps[k].emit;
      if (em && stepEmitSurface(steps[k], surface) === "file") rows.push(em.text);
    }
    if (!rows.length)
      return el("div", { class: "sim-file sim-file-empty" }, [
        el("i", { class: "ph ph-file-text sim-file-icon" }),
        el("div", { class: "sim-file-name", text: simDoneAt(steps, i) ? "done" : "…" }),
      ]);
    var box = el("div", { class: "sim-file" });
    rows.forEach(function (text) {
      var bytes = text.match(/[\d,._]*\d\s*(?:bytes|KB|MB|GB)\b/i);
      box.appendChild(el("div", { class: "sim-file-row" }, [
        el("i", { class: "ph ph-file-text" }),
        el("div", { class: "sim-file-row-body" }, [
          el("div", { class: "sim-file-name", text: text }),
          bytes ? el("div", { class: "sim-file-bytes", text: bytes[0] }) : null,
        ]),
      ]));
    });
    return box;
  }
  function stageFor(sc, steps, i) {
    var surface = resolveSurface(sc, steps);
    if (surface === "browser") return browserStage(sc, steps, i, surface);
    if (surface === "api") return apiStage(sc, steps, i, surface);
    if (surface === "ui") return uiStage(sc, steps, i, surface);
    if (surface === "job") return jobStage(sc, steps, i, surface);
    if (surface === "db") return dbStage(sc, steps, i, surface);
    if (surface === "file") return fileStage(sc, steps, i, surface);
    return terminalStage(sc, steps, i, surface);
  }

  var SIM_LANE_ICON = { derived: "ph-lightning", authored: "ph-pencil-simple", recorded: "ph-record" };
  var simRailListEl = null;   // the scrolling middle of the rail — rebuilt in place
                              // on filter / group toggle so the search field keeps focus
  function simRailRow(sc) {
    var active = state.simScenario === sc.id;
    var text = [el("span", { class: "sim-scenario-title", title: sc.title, text: sc.title })];
    if (sc.summary) text.push(el("span", { class: "sim-scenario-sub", text: sc.summary }));
    return el("div", { class: "sim-scenario" + (active ? " active" : ""),
      on: { click: function () { go("sim", sc.id + "/0"); } } }, [
      el("i", { class: "ph " + (SIM_LANE_ICON[sc.source] || "ph-lightning") }),
      el("span", { class: "sim-scenario-text" }, text),
    ]);
  }
  function renderSimRailList() {
    if (!simRailListEl) return;
    clear(simRailListEl);
    var q = simRailFilter.trim().toLowerCase();
    var groups = [], byName = {};
    simSorted().forEach(function (sc) {
      var g = simGroupOf(sc);
      if (!byName[g]) { byName[g] = { name: g, items: [] }; groups.push(byName[g]); }
      byName[g].items.push(sc);
    });
    var any = false;
    groups.forEach(function (grp, gi) {
      var items = q ? grp.items.filter(function (sc) {
        return (sc.title + " " + (sc.summary || "") + " " + grp.name).toLowerCase().indexOf(q) >= 0;
      }) : grp.items;
      if (!items.length) return;
      any = true;
      var activeHere = items.some(function (sc) { return sc.id === state.simScenario; });
      var open = q ? true
        : (simGroupOpen[grp.name] != null ? simGroupOpen[grp.name] : (activeHere || gi === 0));
      simRailListEl.appendChild(el("div", { class: "sim-rail-group" + (open ? " open" : ""),
        on: { click: function () { simGroupOpen[grp.name] = !open; renderSimRailList(); } } }, [
        el("i", { class: "ph " + (open ? "ph-caret-down" : "ph-caret-right") }),
        el("span", { class: "sim-rail-group-name", text: grp.name }),
        el("span", { class: "sim-rail-group-n", text: String(items.length) }),
      ]));
      if (!open) return;
      var cap = (q || simGroupExpand[grp.name]) ? items.length : Math.min(items.length, SIM_GROUP_CAP);
      items.slice(0, cap).forEach(function (sc) { simRailListEl.appendChild(simRailRow(sc)); });
      if (cap < items.length)
        simRailListEl.appendChild(el("div", { class: "sim-rail-more",
          on: { click: function () { simGroupExpand[grp.name] = true; renderSimRailList(); } } },
          ["+ " + (items.length - cap) + " more"]));
    });
    if (!any)
      simRailListEl.appendChild(el("div", { class: "sim-rail-empty",
        text: q ? "No scenario matches “" + simRailFilter + "”." : "No scenarios." }));
  }
  // "One thread followed end to end beats a week of reading files at random"
  // (teacher's principle 5). A curriculum of 40+ scenarios invites browsing
  // over depth, so whichever scenario sorts first (lowest `order`, the
  // convention the skill gives its single hero scenario — see SKILL.md) gets
  // pinned above the rail as the one thing worth actually finishing.
  function simStartScenario() {
    var sorted = simSorted();
    return sorted.length > 1 ? sorted[0] : null;
  }
  function simStartCallout() {
    var sc = simStartScenario();
    if (!sc) return null;
    var here = sc.id === state.simScenario;
    // already on it: a click here used to still navigate to "#/sim/<id>"
    // (no step suffix), silently resetting you back to step 0 mid-run
    return el("div", { class: "sim-start" + (here ? " here" : ""),
      on: { click: function () { if (!here) go("sim", sc.id); } } }, [
      el("i", { class: "ph ph-flag" }),
      el("div", {}, [
        el("div", { class: "sim-start-lbl", text: here ? "You're on it" : "If you follow only one, follow this one" }),
        el("div", { class: "sim-start-title", text: sc.title }),
      ]),
    ]);
  }
  function simRail() {
    var total = SIM_SCENARIOS.length;
    var box = el("div", { class: "sim-rail" },
      [el("div", { class: "lk", text: "SCENARIOS · " + total })]);
    var start = simStartCallout();
    if (start) box.appendChild(start);
    if (total > 12)
      box.appendChild(el("input", { class: "sim-rail-search", type: "search",
        placeholder: "Filter " + total + " scenarios…", value: simRailFilter,
        on: { input: function (e) { simRailFilter = e.target.value; renderSimRailList(); } } }));
    simRailListEl = el("div", { class: "sim-rail-list" });
    box.appendChild(simRailListEl);
    renderSimRailList();
    box.appendChild(el("div", { class: "sim-legend" }, [
      el("div", { class: "row" }, [el("i", { class: "ph ph-lightning" }), "derived — computed from the call graph, not a real run"]),
      el("div", { class: "row" }, [el("i", { class: "ph ph-pencil-simple" }), "authored — written for this course"]),
      el("div", { class: "row" }, [el("i", { class: "ph ph-record" })].concat(codeify("recorded — a real `codemap trace` run"))),
    ]));
    return box;
  }

  var SIM_SPEEDS = [0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];
  function fmtSpeed(v) { return v < 1 ? v.toFixed(2).replace(/0+$/, "").replace(/\.$/, "") : String(v); }
  function buildTransport(m) {
    var restartBtn = el("button", { class: "sim-tbtn", "aria-label": "restart",
      on: { click: function () { simSetStep(0); } } }, [el("i", { class: "ph ph-skip-back" })]);
    var prevBtn = el("button", { class: "sim-tbtn", "aria-label": "step back",
      on: { click: function () { simSetStep(state.simStep - 1); } } }, [el("i", { class: "ph ph-caret-left" })]);
    var playIcon = el("i", { class: "ph ph-play" });
    var playBtn = el("button", { class: "sim-tbtn play", "aria-label": "play",
      on: { click: function () { state.simPlaying ? simPause() : simPlay(); } } }, [playIcon]);
    var nextBtn = el("button", { class: "sim-tbtn", "aria-label": "step forward",
      on: { click: function () { simSetStep(state.simStep + 1); } } }, [el("i", { class: "ph ph-caret-right" })]);
    var endBtn = el("button", { class: "sim-tbtn", "aria-label": "go to end",
      on: { click: function () { simSetStep(m.steps.length - 1); } } }, [el("i", { class: "ph ph-skip-forward" })]);
    // a plain <input type=range> is left alone every repaint (see syncTransport) — replacing
    // it mid-drag would drop the browser's pointer capture and break scrubbing after 1px.
    var scrub = el("input", { type: "range", class: "sim-scrub", min: "0", max: String(m.steps.length - 1),
      value: "0", "aria-label": "playback position",
      on: { input: function (e) { simSetStep(parseInt(e.target.value, 10)); } } });
    var counter = el("b", { class: "mono sim-counter", text: "1 / " + m.steps.length });
    // a draggable slider, not fixed stops — 1x still read as "fast" to
    // watch a real trace unfold, so the range runs well under 1x too.
    var si = SIM_SPEEDS.indexOf(state.simSpeed);
    if (si < 0) { si = SIM_SPEEDS.indexOf(1); state.simSpeed = 1; }
    var speedLabel = el("b", { class: "mono sim-speed-label", text: fmtSpeed(state.simSpeed) + "×" });
    var speedSlider = el("input", { type: "range", class: "sim-speed-slider",
      min: "0", max: String(SIM_SPEEDS.length - 1), step: "1", value: String(si),
      "aria-label": "playback speed", "aria-valuetext": fmtSpeed(state.simSpeed) + "×",
      on: { input: function (e) {
        state.simSpeed = SIM_SPEEDS[parseInt(e.target.value, 10)];
        speedLabel.textContent = fmtSpeed(state.simSpeed) + "×";
        speedSlider.setAttribute("aria-valuetext", fmtSpeed(state.simSpeed) + "×");
      } } });
    var speedBox = el("div", { class: "sim-speed" }, [
      el("i", { class: "ph ph-gauge", "aria-hidden": "true" }),
      speedSlider,
      speedLabel,
    ]);
    var box = el("div", { class: "sim-transport" }, [
      restartBtn, prevBtn, playBtn, nextBtn, endBtn, scrub, counter, speedBox,
    ]);
    return { box: box, restartBtn: restartBtn, prevBtn: prevBtn, nextBtn: nextBtn, endBtn: endBtn,
      playIcon: playIcon, scrub: scrub, counter: counter };
  }
  function syncTransport(m) {
    var t = m.transport, i = state.simStep, n = m.steps.length;
    t.scrub.value = String(i);
    t.counter.textContent = (i + 1) + " / " + n;
    t.playIcon.setAttribute("class", "ph " + (state.simPlaying ? "ph-pause" : "ph-play"));
    t.prevBtn.disabled = t.restartBtn.disabled = i === 0;
    t.nextBtn.disabled = t.endBtn.disabled = i >= n - 1;
  }

  function findParentEdgeKey(st) {
    var idx = st.stack.indexOf(st.node);
    return idx > 0 ? st.stack[idx - 1] + ">" + st.node : null;
  }
  function fireCallToken(m, st) {
    var isReturn = st.t === "return";
    var key = st.t === "call" && st.from != null ? st.from + ">" + st.node
      : isReturn ? findParentEdgeKey(st) : null;
    var pathEl = key && m.flow.edgeEls[key];
    if (!pathEl) return;
    // the static edge's `d` always runs caller->callee (that's what its own
    // arrowhead points along); a RETURN needs to travel it the other way,
    // back to the caller — animateMotion has no "reverse" flag, so walk an
    // explicitly reversed copy of the same curve instead of the original.
    var d = pathEl.getAttribute("d");
    if (isReturn) d = reverseCubicD(d);
    var g = comet(d, colorForNode(N[st.node]), 0.5, 0, true);
    m.flow.svg.appendChild(g);
    setTimeout(function () { if (g.parentNode) g.parentNode.removeChild(g); }, 650);
  }

  // each of the 4 content panes gets a header (title + hide toggle) and sits in
  // one of two flex columns; a drag handle between the two panes in a column
  // resizes them (one pane's height, its sibling fills the rest), and one more
  // between the columns resizes the whole left/right split — same pointer-
  // capture technique as the legend's drag (wireLegendDrag), sized/clamped
  // instead of positioned. Sizes and collapsed state live in state.sim* so
  // they survive a scenario switch, matching how the legend's own position does.
  var SIM_PANE_TITLE = { stage: "STAGE", flow: "FLOW", log: "TRACE LOG", source: "SOURCE" };
  function simPaneToggle(key) {
    var collapsed = state.simCollapsed[key];
    return el("button", { class: "sim-pane-toggle",
      "aria-label": (collapsed ? "Show " : "Hide ") + SIM_PANE_TITLE[key],
      on: { click: function () { state.simCollapsed[key] = !state.simCollapsed[key]; applyPaneChrome(); } } },
      [el("i", { class: "ph " + (collapsed ? "ph-caret-down" : "ph-caret-up") })]);
  }
  // ⤢ maximise: same effect as double-clicking the pane header — the pane fills
  // the panes area and the other three (plus every splitter) hide. A second
  // press, another double-click, or Esc restores the four-pane layout.
  function simPaneSoloBtn(key) {
    var on = state.simSolo === key;
    return el("button", { class: "sim-pane-solo", "aria-pressed": on ? "true" : "false",
      title: "Double-click the header to maximise",
      "aria-label": (on ? "Restore " : "Maximise ") + SIM_PANE_TITLE[key] + " pane",
      on: { click: function () { toggleSimSolo(key); } } },
      [el("i", { class: "ph " + (on ? "ph-corners-in" : "ph-corners-out") })]);
  }
  function simPaneWrap(key, bodyEl) {
    var hd = el("div", { class: "sim-pane-hd",
      on: { dblclick: function (ev) { if (!ev.target.closest("button")) toggleSimSolo(key); } } }, [
      el("span", { class: "sim-pane-title", text: SIM_PANE_TITLE[key] }),
      simPaneSoloBtn(key),
      simPaneToggle(key),
    ]);
    var wrap = el("div", { class: "sim-pane sim-pane-" + key +
      (state.simCollapsed[key] ? " collapsed" : "") + (state.simSolo === key ? " solo" : "") },
      [hd, el("div", { class: "sim-pane-body" }, [bodyEl])]);
    return { wrap: wrap, hd: hd };
  }
  function applyPaneChrome() {
    var m = simMount;
    if (!m || !m.paneWraps) return;
    Object.keys(m.paneWraps).forEach(function (key) {
      var pw = m.paneWraps[key], collapsed = state.simCollapsed[key];
      pw.wrap.classList.toggle("collapsed", collapsed);
      var btn = pw.hd.querySelector(".sim-pane-toggle"), icon = btn && btn.querySelector("i");
      if (btn) btn.setAttribute("aria-label", (collapsed ? "Show " : "Hide ") + SIM_PANE_TITLE[key]);
      if (icon) icon.setAttribute("class", "ph " + (collapsed ? "ph-caret-down" : "ph-caret-up"));
    });
  }
  // Solo: one pane full-bleed. `data-solo` on .sim-panes + `.solo` on the pane
  // + `.has-solo` on its column drive the CSS; sizes/collapsed state are left
  // untouched so restoring is exact. Persisted in state.simSolo, like collapse.
  function toggleSimSolo(key) {
    state.simSolo = state.simSolo === key ? null : key;
    if (state.simSolo && state.simCollapsed[key]) {
      state.simCollapsed[key] = false;   // can't focus a pane that's collapsed to its header
      applyPaneChrome();
    }
    applySimSolo();
  }
  function applySimSolo() {
    var m = simMount;
    if (!m || !m.paneWraps || !m.panesRow) return;
    var solo = state.simSolo;
    if (solo && !m.paneWraps[solo]) solo = state.simSolo = null;
    if (solo) m.panesRow.setAttribute("data-solo", solo);
    else m.panesRow.removeAttribute("data-solo");
    [m.leftCol, m.rightCol].forEach(function (col) {
      if (col) col.classList.toggle("has-solo", !!solo && col.contains(m.paneWraps[solo].wrap));
    });
    Object.keys(m.paneWraps).forEach(function (key) {
      var pw = m.paneWraps[key], on = key === solo;
      pw.wrap.classList.toggle("solo", on);
      var btn = pw.hd.querySelector(".sim-pane-solo"), icon = btn && btn.querySelector("i");
      if (btn) {
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.setAttribute("aria-label", (on ? "Restore " : "Maximise ") + SIM_PANE_TITLE[key] + " pane");
      }
      if (icon) icon.setAttribute("class", "ph " + (on ? "ph-corners-in" : "ph-corners-out"));
    });
  }
  function wireVSplitter(grip, leftEl, rowEl) {
    var drag = null;
    grip.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      var lr = leftEl.getBoundingClientRect(), rr = rowEl.getBoundingClientRect();
      drag = { x: e.clientX, startW: lr.width, rowW: rr.width };
      grip.classList.add("dragging");
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* drag still works while over the grip */ }
      e.preventDefault();
    });
    grip.addEventListener("pointermove", function (e) {
      if (!drag) return;
      // right column's own floor is 280px + the 9px grip itself
      var w = Math.max(260, Math.min(drag.rowW - 289, drag.startW + (e.clientX - drag.x)));
      leftEl.style.flex = "0 0 " + w + "px";
      state.simLayout.leftW = w;
    });
    function end(e) { if (!drag) return; drag = null; grip.classList.remove("dragging");
      try { grip.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ } }
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
  }
  function wireHSplitter(grip, topEl, colEl, stateKey) {
    var drag = null;
    grip.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      var tr = topEl.getBoundingClientRect(), cr = colEl.getBoundingClientRect();
      drag = { y: e.clientY, startH: tr.height, colH: cr.height };
      grip.classList.add("dragging");
      try { grip.setPointerCapture(e.pointerId); } catch (err) { /* drag still works while over the grip */ }
      e.preventDefault();
    });
    grip.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var h = Math.max(70, Math.min(drag.colH - 70 - 9, drag.startH + (e.clientY - drag.y)));
      topEl.style.flex = "0 0 " + h + "px";
      state.simLayout[stateKey] = h;
    });
    function end(e) { if (!drag) return; drag = null; grip.classList.remove("dragging");
      try { grip.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ } }
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
  }

  // keep the active node visible *within the flow pane's own scroller* —
  // same reasoning as scrollLogRowIntoView: Element.scrollIntoView() would
  // walk up and drag .sim-pane-body / .sim-body along with it too.
  function scrollFlowNodeIntoView(m, ni) {
    var pos = m.layout.pos[ni];
    var scroller = m.paneWraps.flow.wrap.querySelector(".sim-pane-body");
    if (!pos || !scroller) return;
    var cx = pos.x + 8, cy = pos.y + 14;   // the node dot's own center (see buildSimFlow)
    var sw = scroller.clientWidth, sh = scroller.clientHeight;
    if (!sw || !sh) return;
    var visible = cx >= scroller.scrollLeft && cx <= scroller.scrollLeft + sw &&
      cy >= scroller.scrollTop && cy <= scroller.scrollTop + sh;
    if (visible) return;
    scroller.scrollTo({
      left: Math.max(0, Math.min(scroller.scrollWidth - sw, cx - sw / 2)),
      top: Math.max(0, Math.min(scroller.scrollHeight - sh, cy - sh / 2)),
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }

  var simMount = null;   // the currently mounted scenario's live DOM refs — rebuilt by
                          // simTab() on every scenario switch, mutated in place by every
                          // step change so play/scrub never tears down (and never re-lays-out) the DOM.
  function paintSimStep(i, animate) {
    var m = simMount;
    if (!m) return;
    i = Math.max(0, Math.min(m.steps.length - 1, i));
    state.simStep = i;
    var st = m.steps[i], n = N[st.node];

    m.layout.order.forEach(function (ni) {
      var g = m.flow.nodeEls[ni];
      if (!g) return;
      var cls = "sim-node";
      if (ni === st.node) cls += " active";
      else if (st.stack.indexOf(ni) >= 0) cls += " onstack";
      else if (stepReached(m.steps, i, ni)) cls += " done";
      else cls += " pending";
      g.setAttribute("class", cls);
    });
    Object.keys(m.flow.edgeEls).forEach(function (key) {
      var to = +key.split(">")[1];
      m.flow.edgeEls[key].setAttribute("class", "sim-edge" + (stepReached(m.steps, i, to) ? " lit" : ""));
    });
    if (animate && !reduceMotion) fireCallToken(m, st);
    scrollFlowNodeIntoView(m, st.node);

    paintTraceLog(m, i);

    clear(m.sourceBox);
    // st.line, when set, is the call-site line in the CALLER's file, not a
    // line inside n's own body — highlighting n's own first line is the
    // honest "where execution is" marker; the call site itself surfaces as
    // its own "called from X, line N" link in the narration pane instead.
    m.sourceBox.appendChild(sourcePane(n, n.line ? n.line[0] : null));

    clear(m.narrBox);
    m.narrBox.appendChild(narrationPane(st));

    clear(m.stageBox);
    var stageInner = stageFor(m.sc, m.steps, i);
    m.stageBox.appendChild(stageInner);
    var scroller = stageInner.querySelector(".sim-term-body");
    if (scroller) scroller.scrollTop = scroller.scrollHeight;

    syncTransport(m);
    history.replaceState(null, "", "#/sim/" + encodeURIComponent(m.sc.id) + "/" + i);
  }
  function simSetStep(i) { if (simMount) paintSimStep(i, true); }
  var simTimer = null;
  function simPlay() {
    if (!simMount || state.simPlaying) return;
    // pressing Play right after a run finished restarts it — the old
    // behaviour just silently re-paused on the same, already-last step
    if (state.simStep >= simMount.steps.length - 1) simSetStep(0);
    state.simPlaying = true;
    syncTransport(simMount);
    simTick();
  }
  function simPause() {
    state.simPlaying = false;
    if (simTimer) { clearTimeout(simTimer); simTimer = null; }
    if (simMount) syncTransport(simMount);
  }
  function simTick() {
    if (!state.simPlaying || !simMount) return;
    var steps = simMount.steps, i = state.simStep;
    if (i >= steps.length - 1) { simPause(); return; }
    var dur = Math.max(100, steps[i].dur || 500) / state.simSpeed;
    simTimer = setTimeout(function () {
      if (!state.simPlaying) return;
      simSetStep(state.simStep + 1);
      simTick();
    }, dur);
  }
  document.addEventListener("keydown", function (e) {
    if (state.tab !== "sim" || !simMount) return;
    if (e.key === "Escape" && state.simSolo) { e.preventDefault(); state.simSolo = null; applySimSolo(); return; }
    var tag = (e.target && e.target.tagName || "").toLowerCase();
    // a focused control handles its own keys — Space on a transport button
    // would otherwise both click it and toggle play (double-action)
    if (tag === "input" || tag === "textarea" || tag === "button" || tag === "select") return;
    if (e.key === " ") { e.preventDefault(); state.simPlaying ? simPause() : simPlay(); }
    else if (e.key === "ArrowRight" || e.key === ".") { e.preventDefault(); simSetStep(state.simStep + 1); }
    else if (e.key === "ArrowLeft" || e.key === ",") { e.preventDefault(); simSetStep(state.simStep - 1); }
    else if (e.key === "Home") { e.preventDefault(); simSetStep(0); }
    else if (e.key === "End") { e.preventDefault(); simSetStep(simMount.steps.length - 1); }
  });

  function simTab() {
    var sc = simCurrent();
    if (!sc)
      return el("div", { class: "sim-empty" }, [
        el("i", { class: "ph ph-play-circle" }),
        el("p", {}, codeify("No scenarios yet — open a busy symbol in Map ▸ Run trace and press " +
          "“Simulate ▶”, write .codemap/scenarios.json, or run `codemap trace`.")),
      ]);
    var steps = scenarioSteps(sc);
    var i = Math.max(0, Math.min(steps.length - 1, state.simStep));
    state.simStep = i;
    var layout = layoutSim(steps);
    var flow = buildSimFlow(steps, layout);

    var stageBox = el("div", { class: "sim-stagebody" });
    var flowBody = el("div", { class: "sim-flowbody" }, [flow.svg]);
    var logBox = el("div", { class: "sim-logbody" });
    var sourceBox = el("div", { class: "sim-sourcebody" });
    var narrBox = el("div", { class: "sim-narrbox", style: "height:" + narrBoxHeight(steps) + "px" });
    var transportBox = el("div", { class: "sim-transportbox" });

    var stagePane = simPaneWrap("stage", stageBox);
    var flowPane = simPaneWrap("flow", flowBody);
    var logPane_ = simPaneWrap("log", logBox);
    var sourcePane_ = simPaneWrap("source", sourceBox);

    // reapply any sizes dragged earlier this session (mirrors wireLegendDrag
    // reapplying state.legendPos on every rebuild)
    var L = state.simLayout;
    if (L.stageH != null) stagePane.wrap.style.flex = "0 0 " + L.stageH + "px";
    if (L.logH != null) logPane_.wrap.style.flex = "0 0 " + L.logH + "px";

    var hSplitLeft = el("div", { class: "sim-hsplit", "aria-hidden": "true" });
    var hSplitRight = el("div", { class: "sim-hsplit", "aria-hidden": "true" });
    var leftCol = el("div", { class: "sim-col sim-col-left" }, [stagePane.wrap, hSplitLeft, flowPane.wrap]);
    var rightCol = el("div", { class: "sim-col sim-col-right" }, [logPane_.wrap, hSplitRight, sourcePane_.wrap]);
    if (L.leftW != null) leftCol.style.flex = "0 0 " + L.leftW + "px";
    var vSplit = el("div", { class: "sim-vsplit", "aria-hidden": "true" });
    var panesRow = el("div", { class: "sim-panes" }, [leftCol, vSplit, rightCol]);
    wireHSplitter(hSplitLeft, stagePane.wrap, leftCol, "stageH");
    wireHSplitter(hSplitRight, logPane_.wrap, rightCol, "logH");
    wireVSplitter(vSplit, leftCol, panesRow);

    var notes = [];
    if (sc.crashed) notes.push(el("div", { class: "sim-crash" }, [el("i", { class: "ph ph-warning" }), "the recorded run raised: " + sc.crashed]));
    if (sc.truncated) notes.push(el("div", { class: "sim-note" }, ["showing the first " + SIM_BUDGET + " calls"]));
    if (sc.source === "derived")
      notes.push(el("div", { class: "sim-note" },
        codeify("⚡ This is a prediction from the code's structure, not something that actually ran — " +
         "branches and loops are shown as possibilities, not the choices a real run would make. " +
         "Want the real thing? `codemap trace --name \"" + sc.title + "\" -- <command>` records an " +
         "actual run of this and replays it here, call by call.")));

    simMount = { sc: sc, steps: steps, layout: layout, flow: flow,
      stageBox: stageBox, logBox: logBox, sourceBox: sourceBox, narrBox: narrBox,
      leftCol: leftCol, rightCol: rightCol, panesRow: panesRow,
      paneWraps: { stage: stagePane, flow: flowPane, log: logPane_, source: sourcePane_ } };
    logBox.appendChild(buildTraceLog(simMount));   // built once; paintTraceLog re-marks the current row
    simMount.transport = buildTransport(simMount);
    transportBox.appendChild(simMount.transport.box);

    paintSimStep(i, false);
    applySimSolo();   // re-apply a solo pane picked before this scenario switch

    return el("div", { class: "sim" }, [
      simRail(),
      el("div", { class: "sim-body" }, notes.concat([panesRow, narrBox, transportBox])),
    ]);
  }

  // ---- packages tab: library / module reference ---------------------
  // For every external package the code imports — and every top-level module of
  // the repo itself — what it does *in general* plus how *this* codebase uses
  // it. The bundled table below covers the common ecosystem; anything it misses
  // (and every repo module) is filled by .codemap/libraries.json, authored by
  // the codebase-to-course skill. DATA.learn is still emitted but no longer
  // rendered here. (This used to be the Learn tab's whole content; it moved to
  // its own "Packages" tab so Learn could become the project walkthrough —
  // see the "learn tab: the walkthrough" section further down.)
  var LIB_BLURB = {
    // — python standard library —
    os: "Operating-system bridge — file paths, environment variables, processes.",
    sys: "Interpreter internals — argv, stdin/stdout, the import path, exit codes.",
    re: "Regular expressions — match, search and substitute text by pattern.",
    json: "Read and write JSON text to and from Python dicts and lists.",
    pathlib: "Object-oriented file paths — join, glob, read and write files.",
    subprocess: "Run external commands as child processes and capture their output.",
    datetime: "Dates, times, durations and timezone-aware timestamps.",
    collections: "Extra container types — Counter, defaultdict, deque, namedtuple.",
    itertools: "Lazy iterator building blocks — chain, groupby, product, combinations.",
    functools: "Tools for functions — caching (lru_cache), partial, reduce, wraps.",
    typing: "Type hints — Optional, Union, generics; no runtime effect on its own.",
    dataclasses: "Generate __init__/__repr__/__eq__ for plain data-holding classes.",
    enum: "Named constant sets — Enum, IntEnum, auto().",
    abc: "Abstract base classes — declare interfaces subclasses must implement.",
    asyncio: "Async/await event loop for concurrent I/O without threads.",
    sqlite3: "Built-in client for SQLite, a serverless file-based SQL database.",
    logging: "Structured application logging with levels, handlers and formatters.",
    argparse: "Parse command-line arguments into a typed namespace, with --help.",
    hashlib: "Cryptographic hashes — sha256, md5, blake2 — of bytes.",
    tomllib: "Parse TOML config files (read-only; Python 3.11+).",
    io: "In-memory streams — StringIO, BytesIO — and the base stream classes.",
    csv: "Read and write comma-separated-value tables.",
    math: "Floating-point math — sqrt, floor, trig, constants.",
    random: "Pseudo-random numbers, choices, shuffles and samples.",
    time: "Clock access, sleeping, and Unix-timestamp arithmetic.",
    contextlib: "Helpers for `with` blocks — contextmanager, suppress, ExitStack.",
    tempfile: "Create temporary files and directories that clean themselves up.",
    shutil: "High-level file operations — copy, move, rmtree, disk usage.",
    glob: "Expand shell-style wildcard patterns into lists of paths.",
    warnings: "Emit and filter non-fatal warnings.",
    traceback: "Format and print exception stack traces.",
    inspect: "Read live objects — signatures, source, the call stack.",
    importlib: "Import modules by name at runtime; reload them.",
    threading: "Run code on OS threads; locks, events, thread-local storage.",
    multiprocessing: "Run code in separate processes to use multiple CPU cores.",
    uuid: "Generate universally-unique identifiers (UUID1/UUID4).",
    base64: "Encode and decode binary data as ASCII text.",
    copy: "Shallow and deep copies of arbitrary objects.",
    textwrap: "Wrap, fill, indent and dedent blocks of text.",
    urllib: "URL parsing and basic HTTP requests from the standard library.",
    http: "Standard-library HTTP client and server primitives.",
    socket: "Low-level TCP/UDP network sockets.",
    unittest: "The standard-library test framework — TestCase, assertions, mocks.",
    // — third-party: python —
    networkx: "Graphs as data — build nodes and edges, then run algorithms (paths, cycles, centrality).",
    tree_sitter: "Incremental parser that turns source code into a concrete syntax tree.",
    pathspec: "Match paths against .gitignore-style pattern lists.",
    anthropic: "Official client for Anthropic's Claude API.",
    openai: "Official client for OpenAI's API.",
    pytest: "The de-facto Python test framework — plain asserts, fixtures, parametrize.",
    requests: "Synchronous HTTP client — get/post with a friendly API.",
    httpx: "HTTP client with sync and async APIs and HTTP/2.",
    aiohttp: "Async HTTP client and server built on asyncio.",
    flask: "Minimal WSGI web framework — routes as decorated functions.",
    django: "Batteries-included web framework — ORM, admin, auth, templating.",
    fastapi: "Async web framework that derives validation and docs from type hints.",
    starlette: "The lightweight ASGI toolkit FastAPI is built on.",
    pydantic: "Data validation and settings from Python type annotations.",
    sqlalchemy: "SQL toolkit and ORM — model tables as classes, build queries in Python.",
    alembic: "Schema migrations for SQLAlchemy.",
    pandas: "DataFrames — labelled tabular data with fast filtering, grouping, joins.",
    numpy: "N-dimensional numeric arrays and vectorized math.",
    scipy: "Scientific computing on top of NumPy — optimize, stats, signal, linalg.",
    click: "Build command-line interfaces from decorated functions.",
    rich: "Render colored text, tables, progress bars and tracebacks in the terminal.",
    typer: "Build CLIs from type hints, powered by Click.",
    jinja2: "Text templating — HTML and more — with a sandboxed expression language.",
    boto3: "AWS SDK for Python.",
    redis: "Client for Redis, an in-memory key-value data store.",
    celery: "Distributed task queue — run background jobs across workers.",
    uvicorn: "ASGI web server used to run FastAPI/Starlette apps.",
    gunicorn: "Production WSGI server that manages worker processes.",
    // — third-party: javascript / frontend —
    react: "Build UIs from composable components; re-render from state.",
    "react-dom": "Render React component trees into the browser DOM.",
    vue: "Progressive UI framework — reactive state, single-file components.",
    svelte: "UI compiler — components compile to small vanilla-JS DOM updates.",
    next: "React meta-framework — file-based routing, server rendering, API routes.",
    express: "Minimal Node.js HTTP framework — middleware and routes.",
    axios: "Promise-based HTTP client for the browser and Node.",
    lodash: "Utility belt — data manipulation helpers for arrays, objects, functions.",
    zod: "TypeScript-first schema validation with inferred types.",
    tailwindcss: "Utility-first CSS — compose designs from small class names.",
    vite: "Fast dev server and build tool for web apps.",
    jest: "JavaScript test framework — describe/it, mocks, snapshots.",
    vitest: "Vite-native test runner with a Jest-compatible API.",
    d3: "Low-level data-visualization toolkit — bind data to SVG/DOM.",
    three: "3D graphics in the browser via WebGL.",
  };
  function libBlurb(name) {
    var k = String(name).toLowerCase();
    return LIB_BLURB[k] || LIB_BLURB[k.replace(/-/g, "_")] || LIB_BLURB[k.replace(/_/g, "-")] || null;
  }
  function libSlug(name) {
    return String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "x";
  }
  var LIB_AUTHORED = (DATA.libraries && DATA.libraries.items) || {};
  // global tooltip glossary — filtered server-side to terms that actually occur
  // in authored prose (see codemap/site/glossary.py::build). A symbol's own
  // explain.terms (the Graph inspector) shadows this per-symbol; the two never merge.
  var GLOSS = (DATA.glossary && DATA.glossary.terms) || {};

  // ---- Simulate step -> Learn blurb (dependency cross-link) --------------
  // libraries.json's `see` list already maps a library to the call-site keys
  // where it matters — invert that once into node -> [library name, ...] so a
  // scenario step can show the Learn-tab blurb inline instead of sending the
  // learner to a different tab mid-thread. `see` is exact (authored); a node
  // it doesn't cover falls back to that file's third-party imports, but only
  // for a library that actually has something to say (authored or bundled) —
  // an undescribed dependency would just be noise here.
  var libByNode = {};
  function pushLibForNode(i, name) {
    var list = libByNode[i] || (libByNode[i] = []);
    if (list.indexOf(name) < 0) list.push(name);
  }
  Object.keys(LIB_AUTHORED).forEach(function (name) {
    (LIB_AUTHORED[name].see || []).forEach(function (k) {
      var i = keyToI[k];
      if (i != null) pushLibForNode(i, name);
    });
  });
  function libHasBlurb(name) {
    var a = LIB_AUTHORED[name];
    return !!((a && (a.general || a.here)) || libBlurb(name));
  }
  N.forEach(function (n) {
    if (libByNode[n.i] && libByNode[n.i].length) return;  // an authored `see` already claimed this node
    ((fileByPath[n.file] || {}).deps || []).forEach(function (d) {
      if (d.kind === "third_party" && libHasBlurb(d.name)) pushLibForNode(n.i, d.name);
    });
  });
  function libFor(name) {
    var a = LIB_AUTHORED[name] || {};
    return { name: name, slug: libSlug(name), general: a.general || libBlurb(name) || null, here: a.here || null };
  }

  var LIB_SECTIONS = [
    { key: "third_party", label: "Third-party" },
    { key: "stdlib", label: "Standard library" },
    { key: "module", label: "This repo's modules" },
  ];
  var LIB_ENTRIES = [];
  (DATA.dependencies || []).forEach(function (d) {
    if (d.kind !== "third_party" && d.kind !== "stdlib") return;
    LIB_ENTRIES.push({ name: d.name, section: d.kind, kind: depKindLabel(d.kind),
      slug: libSlug(d.name), scope: "external",
      importers: d.importers || [], count: d.count || (d.importers || []).length });
  });
  (DATA.modules || []).forEach(function (m) {
    LIB_ENTRIES.push({ name: m.name, section: "module", kind: "internal module",
      slug: libSlug(m.name), scope: "internal",
      files: (m.files || []).length, symbols: m.symbol_count || 0 });
  });
  var LIB_BY_SLUG = {};
  LIB_ENTRIES.forEach(function (e) { if (!LIB_BY_SLUG[e.slug]) LIB_BY_SLUG[e.slug] = e; });
  function libDescribed(e) { return !!(LIB_AUTHORED[e.name] || libBlurb(e.name)); }

  // filter text for the Packages nav — module-level like simRailFilter, so
  // typing survives renderPkgNavList() repainting just the list below it
  var pkgNavFilter = "";
  var pkgNavListEl;
  // a slug no real library/module can ever produce (libSlug never emits a
  // leading underscore) and no real folder path can ever collide with —
  // the Learn tab's "Start here" landing-screen page id. See
  // renderOrientation() / learnTab() (the Learn walkthrough, not Packages).
  var ORIENTATION_ID = "__start__";
  function renderPkgNavList(cur) {
    if (!pkgNavListEl) return;
    clear(pkgNavListEl);
    var q = pkgNavFilter.trim().toLowerCase();
    var any = false;
    LIB_SECTIONS.forEach(function (sec) {
      var items = LIB_ENTRIES.filter(function (e) { return e.section === sec.key; });
      if (q) items = items.filter(function (e) {
        // e.kind is the on-row label ("built-in", "third-party", "internal
        // module") — include the section's own name too, so typing "stdlib"
        // or "standard" (the section is titled "Standard library") finds
        // every built-in even though neither word appears in "built-in".
        return (e.name + " " + e.kind + " " + sec.key + " " + sec.label).toLowerCase().indexOf(q) >= 0;
      });
      if (!items.length) return;
      any = true;
      pkgNavListEl.appendChild(el("div", { class: "lib-navsec", text: sec.label + " · " + items.length }));
      items.forEach(function (e) {
        pkgNavListEl.appendChild(el("div", { class: "mlink" + (e.slug === cur.slug ? " active" : ""),
          title: libDescribed(e) ? e.name : e.name + " — no description yet",
          on: { click: function () { go("libs", e.slug); } } },
          [libDescribed(e) ? null : el("span", { class: "lib-dot", text: "○ " }), e.name]));
      });
    });
    if (!any)
      pkgNavListEl.appendChild(el("div", { class: "sim-rail-empty",
        text: "No library matches “" + pkgNavFilter + "”." }));
  }
  // ── Orientation — the Learn tab's actual landing screen ──────────────────
  // Everything below is copy, not computation: it reuses LIB_ENTRIES (already
  // built above), DATA.modules, and go() to link into tabs that already exist.
  // Four teaching moves this repeats on purpose (see the plan doc — teacher's
  // principles 1, 3, 5, 6, 7): nobody understands a codebase entirely; what
  // exists vs. what happens are two different pictures; one thread followed
  // end to end beats a week of random reading; and the four ways to actually
  // move around one (search on-screen text, jump to a definition, read the
  // history, run it and watch the order).
  function orientLink(tab, arg, label, sub) {
    return el("div", { class: "orient-link", on: { click: function () { go(tab, arg); } } }, [
      el("div", { class: "orient-link-t", text: label }),
      sub ? el("div", { class: "orient-link-s", text: sub }) : null,
    ]);
  }
  function renderOrientation() {
    var pageSeen = {};   // one dedupe set for every glossary term shown on this page
    var wrap = el("div", {}, [
      el("div", { class: "module-title", text: "Start here" }),
    ]);

    // an authored walkthrough.json intro means there's real, project-specific
    // copy to open with — "what this actually is" beats the generic framing
    // paragraph below as the very first thing a reader sees.
    if (WT.intro && WT.intro.what) {
      var projectIntro = el("div", { class: "screen" }, [el("p", {}, proseNodes(WT.intro.what, GLOSS, pageSeen))]);
      if (WT.intro.sides && Object.keys(WT.intro.sides).length)
        projectIntro.appendChild(orientLink("learn", "parts", "Start with the parts of this app →"));
      wrap.appendChild(projectIntro);
    }

    var intro = el("div", { class: "screen" });
    intro.appendChild(el("p", { text:
      "Nobody understands a codebase entirely — not even the people who wrote it. " +
      "The goal here isn't to memorize this one. It's to learn how to find your way " +
      "around it. Being lost is the normal state of reading unfamiliar code — the only " +
      "difference experience buys you is knowing which direction to walk." }));
    wrap.appendChild(intro);

    var exists = el("div", { class: "screen orient-split" }, [
      el("h3", { text: "Two different pictures" }),
      el("p", { text: "A codebase is a floor plan — files sitting still on disk, describing what " +
        "exists. Running it is someone walking through that building. The floor plan " +
        "never moves; the walk does. Keep both pictures in your head." }),
      el("div", { class: "orient-cols" }, [
        el("div", { class: "orient-col" }, [
          el("div", { class: "orient-col-h", text: "What exists — the floor plan" }),
          orientLink("graph", null, "Graph", "every file and function, and what calls what"),
          orientLink("arch", null, "Architecture", "the layers, what each is built with, and what it talks to"),
          orientLink("map", null, "Map", "import depth, one call chain, and where the code sits"),
          orientLink("learn", null, "Learn", "what this project is and how its parts fit together"),
          orientLink("libs", null, "Packages", "what every dependency and module actually does"),
        ]),
        el("div", { class: "orient-col" }, [
          el("div", { class: "orient-col-h", text: "What happens — the walk" }),
          orientLink("sim", null, "Simulate", "watch one thing the app does, call by call"),
          orientLink("timeline", null, "Timeline", "every commit, and what it actually changed"),
        ]),
      ]),
    ]);
    wrap.appendChild(exists);

    var moves = el("div", { class: "screen" }, [
      el("h3", { text: "Four ways to move around" }),
      el("div", { class: "orient-move", on: { click: openPalette } },
        [el("i", { class: "ph ph-magnifying-glass" }), el("div", {}, [
          el("b", { text: "Search for text you saw on screen." }),
          " A label, an error, a log line — it's typed somewhere in the files. That's your way in.",
        ])]),
      el("div", { class: "orient-move", on: { click: function () { go("graph"); } } },
        [el("i", { class: "ph ph-crosshair" }), el("div", {}, [
          el("b", { text: "Jump to where a name is defined," }),
          " not just where it's used — click any symbol in the Graph tab.",
        ])]),
      el("div", { class: "orient-move", on: { click: function () { go("graph"); } } },
        [el("i", { class: "ph ph-git-commit" }), el("div", {}, [
          el("b", { text: "Read the history when a line makes no sense." }),
          " Focus it in the Graph tab — the inspector shows when it last changed and, when someone recorded it, why.",
        ])]),
      el("div", { class: "orient-move", on: { click: function () { go("sim"); } } },
        [el("i", { class: "ph ph-play" }), el("div", {}, [
          el("b", { text: "Run it and watch the order things happen in." }),
        ].concat(codeify(" The Simulate tab predicts that from the code; `codemap trace` records the real thing.")))]),
    ]);
    wrap.appendChild(moves);

    // "read the map before the words" — a quick tour of the repo's own
    // top-level folders. This used to read LIB_ENTRIES/LIB_AUTHORED (Packages
    // data); now that Learn owns the walkthrough, it reads DATA.folders (the
    // derived folder tree) + any authored purpose from .codemap/walkthrough.json.
    var topFolders = (DATA.folders || []).filter(function (f) { return f.depth === 1; });
    if (topFolders.length) {
      var tour = el("div", { class: "screen" }, [
        el("h3", { text: "Read the map before the words" }),
        el("p", { text: "Folder names carry more information per second of reading than any " +
          "single file does. Before opening anything, here's what this repo's own top-level " +
          "folders are for:" }),
      ]);
      var list = el("div", { class: "steps" });
      topFolders.forEach(function (f) {
        var wtFolder = (WT.folders && WT.folders[f.path]) || {};
        var blurb = wtFolder.purpose || ((f.deps && f.deps.length)
          ? "Uses: " + f.deps.slice(0, 6).map(function (d) { return d.name; }).join(", ") + "."
          : null);
        list.appendChild(el("div", { class: "step orient-mod",
          on: { click: function () { go("learn", f.path); } } }, [
          el("div", { class: "sn", text: "→" }),
          el("div", {}, [
            el("span", { class: "sf", text: f.name + "/" }),
            el("span", { class: "orient-mod-n", text: "  " + f.total_files + " file" + (f.total_files === 1 ? "" : "s") }),
            blurb ? el("p", { class: "orient-mod-blurb" }, proseNodes(blurb, GLOSS, pageSeen)) : null,
          ]),
        ]));
      });
      tour.appendChild(list);
      wrap.appendChild(tour);
    }

    return wrap;
  }

  function packagesTab() {
    if (!LIB_ENTRIES.length)
      return el("div", { class: "learn", style: "display:flex" }, [
        el("div", { class: "learn-body" }, [el("div", { class: "learn-inner" }, [
          el("div", { class: "module-title", text: "Nothing to describe" }),
          el("p", { class: "module-sub", text: "This graph imports no external packages and has no modules." }),
        ])])]);
    // Packages is pure reference — no orientation screen of its own. No/unknown
    // slug just falls back to the first entry, same as the pre-split Learn tab did.
    var cur = LIB_BY_SLUG[state.pkg] || LIB_ENTRIES[0];
    state.pkg = cur.slug;
    var nav = el("div", { class: "learn-nav" }, [el("div", { class: "lk", text: "LIBRARIES · " + LIB_ENTRIES.length })]);
    // matches the Simulate rail's own threshold for when a flat list is long
    // enough that scanning it beats typing a filter — same idiom, same number.
    if (LIB_ENTRIES.length > 12)
      nav.appendChild(el("input", { class: "sim-rail-search", type: "search",
        placeholder: "Filter " + LIB_ENTRIES.length + " libraries…", value: pkgNavFilter,
        on: { input: function (e) { pkgNavFilter = e.target.value; renderPkgNavList(cur); } } }));
    pkgNavListEl = el("div", { class: "learn-nav-list" });
    nav.appendChild(pkgNavListEl);
    renderPkgNavList(cur);
    return el("div", { class: "learn", style: "display:flex" }, [nav,
      el("div", { class: "learn-body" }, [el("div", { class: "learn-inner" },
        [renderLibEntry(cur)])])]);
  }
  function libImportingFiles(e) {
    if (e.scope === "internal") {
      var mod = (DATA.modules || []).filter(function (m) { return m.name === e.name; })[0];
      return (mod ? mod.files : []).map(function (fi) { return FILES[fi] && FILES[fi].path; }).filter(Boolean);
    }
    return e.importers || [];
  }
  function libDerivedSee(e) {
    if (e.scope === "internal") {
      var eps = (DATA.entry_points || []).filter(function (ep) {
        return ep.node != null && N[ep.node].module === e.name;
      }).map(function (ep) { return N[ep.node].key; });
      if (eps.length) return eps.slice(0, 6);
    }
    var paths = {};
    libImportingFiles(e).forEach(function (p) { paths[p] = 1; });
    return N.filter(function (n) { return paths[n.file]; })
      .sort(function (a, b) { return (b.fan_out - a.fan_out) || (b.fan_in - a.fan_in); })
      .slice(0, 6).map(function (n) { return n.key; });
  }
  function libFileLinks(prefix, files) {
    var p = el("p", {}, [document.createTextNode(prefix)]);
    files.slice(0, 12).forEach(function (path, i) {
      if (i) p.appendChild(document.createTextNode(", "));
      p.appendChild(el("span", { class: "sf", on: { click: (function (pp) { return function () {
        var f = fileByPath[pp];
        if (f && f.symbols && f.symbols.length) go("graph", N[f.symbols[0]].key);
      }; })(path) }, text: path }));
    });
    if (files.length > 12) p.appendChild(document.createTextNode(" +" + (files.length - 12) + " more"));
    return p;
  }
  function renderLibEntry(e) {
    var pageSeen = {};   // one dedupe set for every glossary term shown on this page —
                          // glossary.py scans libraries.json's general/here into DATA.glossary
                          // too ("the Packages tab deserves tooltips too"), so wire it in here.
    var authored = LIB_AUTHORED[e.name] || {};
    var general = authored.general || libBlurb(e.name);
    var wrap = el("div", {}, [
      el("div", { class: "module-title", text: e.name }),
      el("div", { class: "module-sub", text: e.scope === "internal"
        ? "internal module · " + e.files + " file" + (e.files === 1 ? "" : "s") + " · " + e.symbols + " symbols"
        : e.kind + " · imported by " + e.count + " file" + (e.count === 1 ? "" : "s") }),
    ]);

    var g = el("div", { class: "screen" }, [el("h3", { text: "In general" })]);
    g.appendChild(general
      ? el("p", {}, proseNodes(general, GLOSS, pageSeen))
      : el("p", { class: "lib-missing" }, codeify("No description bundled for “" + e.name +
          "”. Add a `general` line to .codemap/libraries.json.")));
    wrap.appendChild(g);

    var h = el("div", { class: "screen" }, [el("h3", { text: "In this codebase" })]);
    if (authored.here) {
      h.appendChild(el("p", {}, proseNodes(authored.here, GLOSS, pageSeen)));
    } else {
      var files = libImportingFiles(e);
      if (files.length)
        h.appendChild(libFileLinks(e.scope === "internal" ? "Files: " : "Imported by ", files));
      else
        h.appendChild(el("p", { class: "lib-missing", text: "No import sites recorded in the graph." }));
      h.appendChild(el("p", { class: "lib-hint" }, codeify(e.scope === "internal"
        ? "Add a `here` line to .codemap/libraries.json describing what this module is responsible for."
        : "Add a `here` line to .codemap/libraries.json describing the job this package does here.")));
    }
    wrap.appendChild(h);

    var seeKeys = ((authored.see && authored.see.length) ? authored.see : libDerivedSee(e))
      .filter(function (k) { return keyToI[k] != null; });
    if (seeKeys.length) {
      var steps = el("div", { class: "steps" });
      seeKeys.forEach(function (k) {
        var n = N[keyToI[k]];
        steps.appendChild(el("div", { class: "step" }, [
          el("div", { class: "sn", text: "→" }),
          el("div", {}, [el("span", { class: "sf", text: n.qual + "  (" + n.file + ")",
            on: { click: function () { go("graph", n.key); } } })]),
        ]));
      });
      wrap.appendChild(el("div", { class: "screen" }, [el("h3", { text: "See in the graph" }), steps]));
    }
    return wrap;
  }

  // ---- learn tab: the walkthrough -----------------------------------
  // Learn's actual content: a plain-language intro to the project, a category
  // map grouping references by topic (authored once, reused everywhere a
  // folder needs to cite the same reference), and one page per surviving
  // folder in DATA.folders. All of it is optional — an unauthored repo still
  // gets a full walkthrough built purely from the derived folder tree.
  var WT = DATA.walkthrough || {};
  var FOLDER_BY_PATH = {};
  (DATA.folders || []).forEach(function (f) { FOLDER_BY_PATH[f.path] = f; });
  // a page id no real folder path can ever produce (folders.py never emits a
  // path wrapped in double underscores) — the "what this app is made of"
  // category-map screen. Deliberately NOT the bare string "map": that collides
  // with state.mapView / the Map tab's own vocabulary, and nothing stops a
  // real repo from having a folder literally named "map".
  var CATEGORY_MAP_ID = "__categories__";

  // Resolve one `see` key (from walkthrough.json's categories/folders/seam) to
  // how it should render — reused for the category map, the seam, and every
  // folder's own references. Three shapes, tried in order:
  //   1. a real symbol key (data.nodes[].key)     -> clickable, jumps to Graph
  //   2. a path that IS an indexed file (FILES[]) -> clickable, jumps to that
  //                                                   file's first symbol
  //   3. anything else (e.g. a .css file — codemap doesn't parse CSS, so it
  //      has no node and no FILES[] entry either)  -> plain inert text, never
  //                                                   dropped; this is what
  //                                                   makes an unindexed file
  //                                                   referenceable at all.
  function resolveSeeKey(k) {
    try {
      if (keyToI[k] != null) return { kind: "node", node: N[keyToI[k]] };
      if (fileByPath[k]) return { kind: "file", file: fileByPath[k] };
    } catch (e) { /* fall through to plain text below */ }
    return { kind: "text", text: k };
  }
  // resolveSeeKey() -> one clickable/inert span, same click idiom libFileLinks
  // already uses (jump to the file's first symbol; nothing to jump to -> plain).
  function seeKeyEl(k) {
    var r = resolveSeeKey(k);
    if (r.kind === "node")
      return el("span", { class: "sf", text: r.node.qual + "  (" + r.node.file + ")",
        on: { click: function () { go("graph", r.node.key); } } });
    if (r.kind === "file") {
      if (r.file.symbols && r.file.symbols.length)
        return el("span", { class: "sf",
          on: { click: function () { go("graph", N[r.file.symbols[0]].key); } }, text: r.file.path });
      return el("span", { class: "sf inert", text: r.file.path });
    }
    return el("span", { class: "sf inert", text: r.text });
  }
  // A folder page's "Start reading here" (walkthrough.json's read_first) is a
  // stronger pointer than an ordinary `see` reference — it means "open the
  // actual file and start at the top", not "jump to this symbol's inspector
  // card". Same key resolution as seeKeyEl, but always opens the source
  // viewer at line 1 of the file, anchored on the file's top (highest
  // fan-in) symbol when given a bare file path rather than a specific one.
  function readFirstEl(k) {
    var r = resolveSeeKey(k);
    var key = r.kind === "node" ? r.node.key : r.kind === "file" ? fileTopSymbol(r.file) : null;
    if (!key) return seeKeyEl(k);   // nothing to anchor a source viewer on — same inert fallback as any other `see` key
    var label = r.kind === "node" ? r.node.qual + "  (" + r.node.file + ")" : r.file.path;
    return el("span", { class: "sf", text: label, on: { click: function () {
      state.srcTargetLine = 1;
      go("graph", key + "/src");
    } } });
  }
  // render a normalized see-list ([{group, keys}] — see walkthrough.py's
  // _norm_see) as one or more labelled step lists. `group: null` (the flat-
  // array form) renders with no sub-heading.
  function renderSeeGroups(groups) {
    var wrap = el("div", {});
    (groups || []).forEach(function (g) {
      if (g.group) wrap.appendChild(el("div", { class: "orient-col-h", text: g.group }));
      var steps = el("div", { class: "steps" });
      (g.keys || []).forEach(function (k) {
        steps.appendChild(el("div", { class: "step" }, [
          el("div", { class: "sn", text: "→" }),
          el("div", {}, [seeKeyEl(k)]),
        ]));
      });
      wrap.appendChild(steps);
    });
    return wrap;
  }
  // the file/module path a `see` key ultimately refers to, for the "does this
  // reference fall under this folder" test below — resolved, not the raw key,
  // since a category's `see` entries are usually symbol keys (`path::symbol`),
  // not bare paths.
  function seeKeyPath(k) {
    var r = resolveSeeKey(k);
    return r.kind === "node" ? r.node.file : r.kind === "file" ? r.file.path : k;
  }
  function keyUnderFolder(k, folderPath) {
    var p = seeKeyPath(k);
    if (folderPath === "(root)") return p.indexOf("/") < 0;   // a root-level file has no "/" at all
    return p === folderPath || p.indexOf(folderPath + "/") === 0;
  }
  // A folder that reuses the shared taxonomy (walkthrough.folders[path].categories,
  // a list of category ids) instead of authoring its own `see` — the "derive,
  // never repeat" rule: pull out just the groups (and, within a group, just the
  // keys) that actually resolve under this folder's path, labelled by category.
  function folderCategoryGroups(folder, wtFolder) {
    var out = [];
    var cats = WT.categories || [];
    (wtFolder.categories || []).forEach(function (catId) {
      var cat = null, want = String(catId).trim().toLowerCase();
      // by id, or by title: walkthrough-schema.md has always told authors to write the
      // title, and the id is a slug of it nobody sees, so an id-only match dropped them
      for (var i = 0; i < cats.length; i++)
        if (cats[i].id === catId || String(cats[i].title).trim().toLowerCase() === want) { cat = cats[i]; break; }
      if (!cat) return;
      (cat.groups || []).forEach(function (g) {
        var matched = [];
        (g.see || []).forEach(function (se) {
          var keys = (se.keys || []).filter(function (k) { return keyUnderFolder(k, folder.path); });
          if (keys.length) matched.push({ group: se.group, keys: keys });
        });
        if (matched.length) out.push({ title: cat.title + " — " + g.title, groups: matched });
      });
    });
    return out;
  }

  var walkNavFilter = "";
  var walkNavListEl;
  // Learn's rail: the three fixed screens (only the ones that actually have
  // something to show), then every surviving folder, indented by depth.
  // DATA.folders is already sorted path-ascending with "(root)" first, and a
  // parent path is always a string-prefix of its children, so it's already in
  // parent-before-child order — no re-sort needed here.
  function renderWalkthroughNavList(curId) {
    if (!walkNavListEl) return;
    clear(walkNavListEl);
    walkNavListEl.appendChild(el("div", { class: "mlink orientation-link" + (curId === ORIENTATION_ID ? " active" : ""),
      on: { click: function () { go("learn"); } } },
      [el("i", { class: "ph ph-compass" }), "Start here"]));
    if (WT.intro && WT.intro.sides && Object.keys(WT.intro.sides).length)
      walkNavListEl.appendChild(el("div", { class: "mlink" + (curId === "parts" ? " active" : ""),
        on: { click: function () { go("learn", "parts"); } } }, ["The parts of this app"]));
    if (WT.categories && WT.categories.length)
      walkNavListEl.appendChild(el("div", { class: "mlink" + (curId === CATEGORY_MAP_ID ? " active" : ""),
        on: { click: function () { go("learn", CATEGORY_MAP_ID); } } }, ["What this app is made of"]));

    var folders = DATA.folders || [];
    if (!folders.length) return;
    walkNavListEl.appendChild(el("div", { class: "lib-navsec", text: "FOLDERS · " + folders.length }));
    var q = walkNavFilter.trim().toLowerCase();
    var shown = q ? folders.filter(function (f) {
      return f.path.toLowerCase().indexOf(q) >= 0 || f.name.toLowerCase().indexOf(q) >= 0;
    }) : folders;
    if (!shown.length) {
      walkNavListEl.appendChild(el("div", { class: "sim-rail-empty",
        text: "No folder matches “" + walkNavFilter + "”." }));
      return;
    }
    shown.forEach(function (f) {
      // a filtered list mixes folders from anywhere in the tree, so the
      // usual depth-indent (which implies "nested under the row above") would
      // actively mislead, and two folders that share a last segment
      // ("components/budget" vs. "services/budget") both just read as
      // "budget" — show the full path instead, unindented, while filtering.
      walkNavListEl.appendChild(el("div", {
        class: "mlink" + (curId === f.path ? " active" : ""),
        style: "padding-left:" + (q ? 9 : 9 + Math.max(0, f.depth - 1) * 14) + "px",
        title: f.reach === "orphan" ? "nothing imports this — check its page" : null,
        on: { click: function () { go("learn", f.path); } },
      }, [f.reach === "orphan" ? el("span", { class: "lib-dot", text: "○ " }) : null,
          q ? f.path : f.name]));
    });
  }

  function renderParts() {
    var pageSeen = {};   // one dedupe set for every glossary term shown on this page
    var intro = WT.intro || {};
    var wrap = el("div", {}, [
      el("div", { class: "module-title", text: "The parts of this app" }),
    ]);
    if (intro.what)
      wrap.appendChild(el("div", { class: "screen" }, [el("p", {}, proseNodes(intro.what, GLOSS, pageSeen))]));

    var sides = intro.sides || {};
    Object.keys(sides).forEach(function (key) {
      var side = sides[key];
      var title = side.title || (key.charAt(0).toUpperCase() + key.slice(1));
      var screen = el("div", { class: "screen orient-split" }, [el("h3", {}, proseNodes(title, GLOSS, pageSeen))]);
      if (side.body) screen.appendChild(el("p", {}, proseNodes(side.body, GLOSS, pageSeen)));
      if (side.root && FOLDER_BY_PATH[side.root])
        screen.appendChild(orientLink("learn", side.root, "Open " + FOLDER_BY_PATH[side.root].name));
      wrap.appendChild(screen);
    });

    var seam = intro.seam;
    if (seam && (seam.note || (seam.see && seam.see.length))) {
      var seamScreen = el("div", { class: "screen" }, [el("h3", { text: "Where they meet" })]);
      if (seam.note) seamScreen.appendChild(el("p", {}, proseNodes(seam.note, GLOSS, pageSeen)));
      if (seam.see && seam.see.length) seamScreen.appendChild(renderSeeGroups(seam.see));
      wrap.appendChild(seamScreen);
    }
    return wrap;
  }

  function renderCategoryMap() {
    var pageSeen = {};   // one dedupe set for every glossary term shown on this page
    var cats = WT.categories || [];
    var wrap = el("div", {}, [
      el("div", { class: "module-title", text: "What this app is made of" }),
    ]);
    cats.forEach(function (cat) {
      var screen = el("div", { class: "screen" }, [el("h3", { text: cat.title })]);
      if (cat.body) screen.appendChild(el("p", {}, proseNodes(cat.body, GLOSS, pageSeen)));
      (cat.groups || []).forEach(function (g) {
        screen.appendChild(el("div", { class: "orient-col-h" }, proseNodes(g.title, GLOSS, pageSeen)));
        screen.appendChild(renderSeeGroups(g.see));
      });
      wrap.appendChild(screen);
    });
    return wrap;
  }

  function renderFolderEntry(folder) {
    var pageSeen = {};   // one dedupe set for every glossary term shown on this page
    var wtFolder = (WT.folders && WT.folders[folder.path]) || {};
    // folder.file_count is direct files only; folder.total_files also counts
    // every subfolder folded under this page (a folder with sub-pages of its
    // own still lists them separately in the rail, so both numbers are real
    // — this used to show file_count alone, which disagreed with the bigger
    // total Orientation quotes for the very same folder.
    var directNote = folder.file_count !== folder.total_files
      ? " (" + folder.file_count + " directly in this folder)" : "";
    var wrap = el("div", {}, [
      el("div", { class: "module-title", text: folder.name }),
      el("div", { class: "module-sub", text: folder.total_files + " file" + (folder.total_files === 1 ? "" : "s") +
        directNote + " · " + folder.symbol_count + " symbol" + (folder.symbol_count === 1 ? "" : "s") }),
    ]);

    var purposeScreen = el("div", { class: "screen" }, [el("h3", { text: "What this is for" })]);
    if (wtFolder.purpose) {
      purposeScreen.appendChild(el("p", {}, proseNodes(wtFolder.purpose, GLOSS, pageSeen)));
    } else {
      var depNames = (folder.deps || []).map(function (d) { return d.name; });
      var sentence = folder.total_files + " file" + (folder.total_files === 1 ? "" : "s") + directNote +
        (folder.langs && folder.langs.length ? " (" + folder.langs.join(", ") + ")" : "") +
        (depNames.length ? ", using: " + depNames.slice(0, 8).join(", ") + (depNames.length > 8 ? "…" : "") : "") + ".";
      purposeScreen.appendChild(el("p", { class: "lib-missing" }, proseNodes(sentence, GLOSS, pageSeen)));
    }
    wrap.appendChild(purposeScreen);

    if (wtFolder.read_first)
      wrap.appendChild(el("div", { class: "screen" }, [
        el("h3", { text: "Start reading here" }),
        el("p", {}, [readFirstEl(wtFolder.read_first)]),
      ]));

    // an authored note always wins over the raw derived orphan hedge; never both.
    var noteText = wtFolder.note || (folder.reach === "orphan" ? folder.orphan_reason : null);
    if (noteText) wrap.appendChild(el("p", { class: "lib-hint" }, proseNodes(noteText, GLOSS, pageSeen)));

    var refScreen = el("div", { class: "screen" }, [el("h3", { text: "See in the graph" })]);
    var haveRefs = false;
    if (wtFolder.see && wtFolder.see.length) {
      refScreen.appendChild(renderSeeGroups(wtFolder.see));
      haveRefs = true;
    } else if (wtFolder.categories && wtFolder.categories.length) {
      var catGroups = folderCategoryGroups(folder, wtFolder);
      catGroups.forEach(function (cg) {
        refScreen.appendChild(el("div", { class: "orient-col-h" }, proseNodes(cg.title, GLOSS, pageSeen)));
        refScreen.appendChild(renderSeeGroups(cg.groups));
      });
      haveRefs = catGroups.length > 0;
    }
    if (!haveRefs) {
      var filePaths = (folder.files || []).map(function (fi) { return FILES[fi] && FILES[fi].path; }).filter(Boolean);
      if (filePaths.length) { refScreen.appendChild(libFileLinks("Files: ", filePaths)); haveRefs = true; }
    }
    if (haveRefs) wrap.appendChild(refScreen);

    return wrap;
  }

  function learnTab() {
    var id = state.module;
    var body;
    if (id === ORIENTATION_ID || !id) {
      body = renderOrientation();
    } else if (FOLDER_BY_PATH[id]) {
      // a real folder always wins over a reserved page-id string — same
      // "folder wins" precedent as the #/learn/<pkg-slug> redirect rule in
      // route(). CATEGORY_MAP_ID is a double-underscore sentinel no real
      // folder path could ever produce, so this only actually changes
      // behavior for a repo with a top-level folder literally named "parts";
      // checking both this way keeps the dispatch consistent either way.
      body = renderFolderEntry(FOLDER_BY_PATH[id]);
    } else if (id === "parts" && WT.intro && WT.intro.sides && Object.keys(WT.intro.sides).length) {
      body = renderParts();
    } else if (id === CATEGORY_MAP_ID && WT.categories && WT.categories.length) {
      body = renderCategoryMap();
    } else {
      body = renderOrientation();   // unknown/unavailable id -> don't crash, land somewhere sane
    }

    var nav = el("div", { class: "learn-nav" }, [el("div", { class: "lk", text: "WALKTHROUGH" })]);
    var folders = DATA.folders || [];
    // matches the Simulate rail's/Packages nav's own threshold for when a flat
    // list is long enough that scanning it beats typing a filter.
    if (folders.length > 12)
      nav.appendChild(el("input", { class: "sim-rail-search", type: "search",
        placeholder: "Filter " + folders.length + " folders…", value: walkNavFilter,
        on: { input: function (e) { walkNavFilter = e.target.value; renderWalkthroughNavList(id); } } }));
    walkNavListEl = el("div", { class: "learn-nav-list" });
    nav.appendChild(walkNavListEl);
    renderWalkthroughNavList(id);

    return el("div", { class: "learn", style: "display:flex" }, [nav,
      el("div", { class: "learn-body" }, [el("div", { class: "learn-inner" }, [body])])]);
  }

  // Split body text on glossary terms, returning an array of text nodes / .term
  // spans. `seen` (optional, an object the caller keeps across every termNodes/
  // proseNodes call on one rendered page) dedupes ACROSS calls — a term already
  // underlined once anywhere on the page is left as plain text everywhere else on
  // it. WITHIN one call, at most one term gets wrapped: the loop below skips past
  // any match already in `seen` looking for a fresh one, then stops as soon as it
  // wraps one — it does not keep hunting for every remaining occurrence in the
  // string (the earlier bug: the old while-loop re-matched after every slice, so
  // one call could wrap the same term half a dozen times over a whole paragraph).
  function termNodes(text, glossary, seen) {
    seen = seen || {};
    var str = String(text == null ? "" : text);
    try {
      // longest-first: "environment variable" must be tried before "environment"
      // or "variable" ever get the chance to claim part of that phrase first —
      // Object.keys() is insertion order, which is not length order.
      var terms = Object.keys(glossary).filter(Boolean)
        .sort(function (a, b) { return b.length - a.length; });
      if (!terms.length) return [document.createTextNode(str)];
      // (?<![.\/@-]) / (?![.\/@-]) on top of \b: a plain word boundary treats
      // ".", "/", "@" and "-" as boundaries too, so "state" inside a file
      // path ("src/state/reducer.js"), a package name ("react-router-dom" —
      // "dom"), or a handle ("user@state.gov") used to light up as a glossary
      // term even though it's not a word there at all.
      var re = new RegExp("(?<![.\\/@-])\\b(" + terms.map(function (t) {
        return t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }).join("|") + ")\\b(?![.\\/@-])");
      var out = [], rest = str, m, wrapped = false;
      while (!wrapped) {
        m = rest.match(re);
        if (!m) break;
        var term = m[1];
        if (seen[term]) {
          // already shown elsewhere on this page — keep this occurrence plain,
          // slide past it, and keep looking for a fresh term later in the string
          var skipTo = m.index + term.length;
          out.push(document.createTextNode(rest.slice(0, skipTo)));
          rest = rest.slice(skipTo);
          continue;
        }
        seen[term] = true;
        if (m.index > 0) out.push(document.createTextNode(rest.slice(0, m.index)));
        out.push(el("span", { class: "term", tabindex: "0", "data-def": glossary[term] || "",
          "aria-label": term + ": " + (glossary[term] || ""), text: term }));
        rest = rest.slice(m.index + term.length);
        wrapped = true;   // one wrap per call, by design — see the comment above
      }
      if (rest) out.push(document.createTextNode(rest));
      return out.length ? out : [document.createTextNode(str)];
    } catch (e) {
      // a malformed alternation (or anything else here) must never blank the
      // whole page — render() has no other error boundary, so this one matters.
      return [document.createTextNode(str)];
    }
  }
  // termNodes() plus codeify()'s backtick-fenced `code span` splitting: prose
  // is codeified first, then term-matched only on the resulting plain-text
  // pieces, leaving every <code> node untouched. This is what makes backticking
  // a real identifier (the skill's own habit, per content-philosophy.md) actually
  // suppress a false tooltip match inside it, e.g. a backticked `App.state` never
  // lights up "state" even though the raw text contains that exact substring.
  function proseNodes(text, glossary, seen) {
    seen = seen || {};
    var out = [];
    codeify(text).forEach(function (node) {
      if (node.nodeType === 3) termNodes(node.nodeValue, glossary, seen).forEach(function (n) { out.push(n); });
      else out.push(node);
    });
    return out;
  }
  // ---- glossary tooltip (fixed to body so overflow:hidden can't clip it) --
  var activeTip = null;
  function positionTip(term, tip) {
    var r = term.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(window.innerWidth - tip.offsetWidth - 8, r.left)) + "px";
    var above = r.top - tip.offsetHeight - 10;
    tip.style.top = (above < 8 ? r.bottom + 10 : above) + "px";
  }
  function showTip(t) {
    if (activeTip) activeTip.remove();
    var tip = el("div", { class: "term-tooltip", text: t.getAttribute("data-def") });
    document.body.appendChild(tip);
    positionTip(t, tip);
    requestAnimationFrame(function () { tip.classList.add("visible"); });
    activeTip = tip;
  }
  function hideTip() { if (activeTip) { activeTip.remove(); activeTip = null; } }
  document.addEventListener("mouseover", function (e) {
    var t = e.target.closest && e.target.closest(".term");
    if (t) showTip(t);
  });
  document.addEventListener("mouseout", function (e) {
    if (e.target.closest && e.target.closest(".term")) hideTip();
  });
  // Tab to a term to see its definition the same way hovering does — the
  // tooltip was mouseover/mouseout-only before, unreachable without a mouse.
  document.addEventListener("focusin", function (e) {
    var t = e.target.closest && e.target.closest(".term");
    if (t) showTip(t);
  });
  document.addEventListener("focusout", function (e) {
    if (e.target.closest && e.target.closest(".term")) hideTip();
  });

  // ---- render ------------------------------------------
  // render() tears down and rebuilds the whole page every time it's called —
  // and it's called from all over (every pill/button/toggle that mutates
  // state and wants a clean repaint, not just route()) — so any rail the
  // viewer had scrolled down used to silently jump back to the top on the
  // next unrelated click. Wrap the real renderer so every call site gets
  // scroll preservation for free, without having to know about it.
  var SCROLL_RAILS = [
    { list: ".sim-rail-list", active: ".sim-scenario.active" },
    { list: ".learn-nav-list", active: ".mlink.active" },   // shared by Learn and Packages — only one is ever in the DOM at once
    { list: ".tl", active: null },
  ];
  function preserveScroll(fn) {
    // a fresh "#/timeline/<sha>" deep link already drives its own scroll (see
    // renderNow()'s timeline branch) — let it, instead of snapping back to
    // wherever the pane happened to be scrolled before the link was followed.
    var rails = state.tab === "timeline" && state.timelineSha
      ? SCROLL_RAILS.filter(function (r) { return r.list !== ".tl"; })
      : SCROLL_RAILS;
    var saved = rails.map(function (r) {
      var node = document.querySelector(r.list);
      return node ? node.scrollTop : null;
    });
    fn();
    rails.forEach(function (r, i) {
      var node = document.querySelector(r.list);
      if (!node) return;
      if (saved[i] != null) node.scrollTop = saved[i];
      else if (r.active) {
        var act = node.querySelector(r.active);
        if (act) act.scrollIntoView({ block: "nearest" });
      }
    });
  }
  function render() { preserveScroll(renderNow); }
  function renderNow() {
    clear(APP);
    // model.build() returns {empty:true} with none of the usual keys (no
    // stats/nodes/files/…) when the DB has no index yet. The CLI already
    // refuses to render this file in that case, but a hand-built or
    // hand-edited payload can still reach the browser this way — show a real
    // message instead of a blank canvas.
    if (DATA.empty) {
      APP.appendChild(el("div", { class: "empty-index" }, [
        el("i", { class: "ph ph-database" }),
        el("p", {}, codeify("No index yet — run `codemap scan` first, then `codemap explore`.")),
      ]));
      return;
    }
    var frag = document.createDocumentFragment();
    frag.appendChild(topbar());
    var banner = staleBanner();
    if (banner) frag.appendChild(banner);
    var noteBanner = linkNoteBanner();
    if (noteBanner) frag.appendChild(noteBanner);
    if (state.tab === "graph") {
      var railNode = rail(), inspNode = inspector();
      railNode.classList.toggle("open", state.mobileRail);
      inspNode.classList.toggle("open", state.mobileInsp);
      var mobileOpen = state.mobileRail || state.mobileInsp;
      // el() auto-grants this div a button role + tab stop (it has on.click),
      // but with no text of its own that left it an unlabeled "button" for a
      // screen reader — give it a name, same as the rail's own close button.
      var backdrop = el("div", { class: "mobile-backdrop" + (mobileOpen ? " show" : ""),
        "aria-label": "Close panel",
        on: { click: function () { state.mobileRail = false; state.mobileInsp = false; render(); } } });
      // hidden until focused (Tab from the topbar lands here first) — with
      // graph nodes out of the tab order and the rail's tree potentially
      // dozens of rows deep, this is the fast way past both straight to the
      // inspector, not a replacement for either.
      var skipLink = el("a", { class: "skip-link", href: "#insp-panel", text: "Skip to inspector",
        on: { click: function (e) {
          e.preventDefault();   // href is a real fragment id, but this app owns
                                 // the hash for its own routing — never let a
                                 // plain navigation touch location.hash
          var t = document.getElementById("insp-panel");
          if (t) t.focus();
        } } });
      frag.appendChild(el("main", { class: "view" }, [skipLink, railNode, stage(), inspNode, backdrop]));
    } else if (state.tab === "arch")
      frag.appendChild(el("main", { class: "view" }, [archTab()]));
    else if (state.tab === "map")
      frag.appendChild(el("main", { class: "view" }, [mapTab()]));
    else if (state.tab === "sim")
      frag.appendChild(el("main", { class: "view" }, [simTab()]));
    else if (state.tab === "timeline")
      frag.appendChild(el("main", { class: "view" }, [timelineTab()]));
    else if (state.tab === "libs")
      frag.appendChild(el("main", { class: "view" }, [packagesTab()]));
    else
      frag.appendChild(el("main", { class: "view" }, [learnTab()]));
    APP.appendChild(frag);
    updateCap();
    syncSrc();
    if (state.tab === "timeline" && state.timelineSha) {
      var tlCard = document.querySelector(".tl-item.hl");   // see timelineTab() — matches full or short sha
      if (tlCard) tlCard.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
    }
  }

  // Cheap path for "still on the Graph tab, just picked a different symbol
  // (or went back to the whole graph)" — by a wide margin the single most
  // frequent navigation in this app (every node click, rail row, caller/
  // callee row and palette pick all funnel through go("graph", key) into
  // route()). render() tears down and rebuilds *everything* — topbar, the
  // rail's search box and footer, the whole stage bar, the canvas (incl.
  // re-wiring its pan/zoom listeners) and the inspector — for a change that
  // only ever affects three things: the canvas contents, the rail's
  // selected-row highlight, and the inspector panel. Repaint just those.
  // route() only takes this branch once the graph tab has actually rendered
  // at least once (railEl/canvasEl/svgEl all get set by rail()/stage()), so
  // the very first load, and any return trip from another tab, still gets
  // the full render().
  function updateGraphFocus() {
    // route() may have just set/cleared state.routeNote (e.g. a bad symbol
    // key on an otherwise graph-to-graph navigation) — this fast path skips
    // the full render() that would normally repaint the banner, so patch it
    // in place the same way the stage bar and inspector are patched below.
    var oldNote = document.querySelector(".link-note");
    var freshNote = linkNoteBanner();
    if (oldNote && freshNote) oldNote.replaceWith(freshNote);
    else if (oldNote && !freshNote) oldNote.remove();
    else if (!oldNote && freshNote) {
      var topbarEl = document.querySelector(".topbar");
      if (topbarEl) topbarEl.insertAdjacentElement("afterend", freshNote);
    }
    var stageEl = canvasEl.closest(".stage");
    var oldBar = stageEl && stageEl.querySelector(".stage-bar");
    if (oldBar) oldBar.replaceWith(stageBar());
    paintGraph();
    updateCap();
    renderRail();
    var freshInsp = inspector();
    freshInsp.classList.toggle("open", state.mobileInsp);
    var oldInsp = document.querySelector(".insp");
    if (oldInsp) oldInsp.replaceWith(freshInsp);
    syncSrc();
  }

  route();
})();
