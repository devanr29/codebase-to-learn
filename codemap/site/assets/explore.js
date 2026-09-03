/* Codegraph Explorer — frozen runtime. No libraries, no build step.
   Everything variable comes from the inlined JSON in #codemap-data. Do NOT
   regenerate this file from Python. All DOM is built with textContent / real
   nodes — no string HTML is ever assigned to the document. */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("codemap-data").textContent);
  var APP = document.getElementById("app");
  var SVGNS = "http://www.w3.org/2000/svg";
  var SVG_TAGS = { svg: 1, g: 1, path: 1, circle: 1, text: 1, ellipse: 1, line: 1, rect: 1,
                   animate: 1, animateMotion: 1, defs: 1, pattern: 1, title: 1 };

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
  E.forEach(function (e) { outAdj[e.s].push(e.t); inAdj[e.t].push(e.s); });
  var keyToI = {};
  N.forEach(function (n) { keyToI[n.key] = n.i; });
  var fileByPath = {};
  FILES.forEach(function (f) { fileByPath[f.path] = f; });

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
  // sentinel key for the legend's single "other folders" row, which stands in
  // for every long-tail folder folded into GROUP_OTHER's shared colour — see
  // the folder-filter note above toggleFolder().
  var OTHER_KEY = " other";
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
  var groupOrder = [];   // folders that earned a distinct hue, in legend order
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
      if (i < GROUP_HUES.length) groupOrder.push(k);
    });
  })();
  function colorForPath(p) { return groupColor[dirGroup(p)] || GROUP_OTHER; }
  function colorForNode(n) { return n && n.file ? colorForPath(n.file) : GROUP_OTHER; }
  function moduleColor(name) { return groupColor[name] || GROUP_OTHER; }

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
  var churnMax = Math.max.apply(null, churnOf.concat([1]));

  // Trace roots are ranked by call-subtree size, NOT read from DATA.entry_points
  // — main() dies at `args.func(args)` after two hops (dynamic dispatch the
  // indexer can't follow), so the richly-connected roots are the big test
  // drivers and the cmd_* handlers instead.
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
  var traceRoots = N.map(function (nd) { return { i: nd.i, size: subtreeSize(nd.i, 6) }; })
    .filter(function (r) { return r.size >= 4; })
    .sort(function (a, b) { return b.size - a.size || N[a.i].qual.localeCompare(N[b.i].qual); })
    .slice(0, 14);
  var traceExpand = {};   // "<root>:<depth>" -> true once the "+N more" is opened

  // ---- app state ---------------------------------------------------
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // touch has no hover and no reliable dblclick — a tap-then-wait-200ms
  // "was that a double click" trick just reads as a laggy single tap, and
  // the actual double tap needed to drill in often never registers at all.
  var IS_TOUCH = navigator.maxTouchPoints > 0 || "ontouchstart" in window;
  var state = {
    tab: "graph",
    grain: 1,   // 0 Module, 1 File (default), 2 Function — see GRAINS below
    hops: 2,
    focus: null,
    fileScope: null,
    showDead: true,
    highlight: null,
    view: { x: 0, y: 0, k: 1 },
    touched: null,
    module: null,
    flow: !reduceMotion,
    folders: new Set(),   // legend folder filter — empty = every folder shown
    pin: null,            // a click-pinned node key: spotlight it + its edges
    mapView: "layers",    // Map tab: "layers" | "trace" | "mass"
    traceRoot: null,      // Map/trace: node index of the traced call root
    traceStep: null,      // Map/trace: BFS depth the step wave has reached (null = all)
    hideTests: false,     // Map/layers: drop tests/** and collapse empty bands
    mobileRail: false,    // narrow viewport: source tree shown as an overlay, not a column
    mobileInsp: false,    // narrow viewport: inspector shown as an overlay, not a column
  };

  // ---- routing ---------------------------------------------------
  function parseHash() {
    var h = (location.hash || "#/graph").replace(/^#/, "");
    var parts = h.split("/").filter(Boolean);
    var raw = parts.slice(1).join("/") || "";
    var arg;
    try { arg = decodeURIComponent(raw); } catch (e) { arg = raw; }   // a hand-edited/copied hash can be malformed — never let it kill the route
    return { tab: parts[0] || "graph", arg: arg };
  }
  function go(tab, arg) {
    var next = "#/" + tab + (arg ? "/" + encodeURIComponent(arg) : "");
    // location.hash = same value fires no hashchange, so re-clicking the
    // already-open target (a symbol, the focused file's row, …) would
    // otherwise silently do nothing — route directly instead.
    if (location.hash === next) route();
    else location.hash = next;
  }
  window.addEventListener("hashchange", route);

  function route() {
    var r = parseHash();
    state.tab = ["graph", "learn", "timeline", "map"].indexOf(r.tab) >= 0 ? r.tab : "graph";
    if (state.tab === "graph") {
      if (r.arg && keyToI[r.arg] != null) { state.focus = keyToI[r.arg]; state.fileScope = null; }
      else if (!r.arg) state.focus = null;
    } else if (state.tab === "learn") {
      state.module = r.arg ||
        (DATA.learn && DATA.learn.modules[0] && DATA.learn.modules[0].id) || "orientation";
    } else if (state.tab === "map") {
      var seg = r.arg.split("/");
      if (["layers", "trace", "mass"].indexOf(seg[0]) >= 0) state.mapView = seg[0];
      if (state.mapView === "trace") {
        var key = seg.slice(1).join("/");
        if (key && keyToI[key] != null) { state.traceRoot = keyToI[key]; state.traceStep = null; }
        else if (state.traceRoot == null && traceRoots.length) state.traceRoot = traceRoots[0].i;
      }
    }
    render();
  }

  // ---- topbar ---------------------------------------------------
  function topbar() {
    var s = DATA.stats || {};
    var tabs = [
      ["graph", "ph ph-graph", "Graph"],
      ["map", "ph ph-stack", "Map"],
      ["learn", "ph ph-graduation-cap", "Learn"],
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
    // this visually, a second visible "codegraph" would just be noise.
    return el("header", { class: "topbar" }, [
      el("h1", { class: "sr-only", text: "Codegraph Explorer" }),
      el("div", { class: "brand" }, [
        el("i", { class: "ph ph-graph" }), el("span", { text: "codegraph" }),
        el("span", { class: "ver", text: (DATA.generator || "").replace("codemap ", "v") }),
      ]),
      el("nav", { class: "tabs", "aria-label": "Sections" }, tabs),
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
        el("i", { class: fileIcon(f.lang) }),
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
          el("span", { class: "tkind", text: kindLabel(n.kind) }),
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
    railEl = el("nav", { class: "rail", "aria-label": "Source tree" }, [
      el("button", { class: "mobile-only mobile-close", "aria-label": "Close source tree",
        on: { click: function () { state.mobileRail = false; render(); } } }, [el("i", { class: "ph ph-x" })]),
      // a real (but readonly) <input> here looked like a text field a user
      // could type into — it couldn't, typing and Enter both did nothing.
      // A plain label makes the one actual control unambiguous: this row
      // opens the palette (el() gives it a button role + tab stop below,
      // since it carries an on.click), where the real typing happens.
      el("div", { class: "rail-search", "aria-label": "Find symbol",
        on: { click: openPalette } }, [
        el("i", { class: "ph ph-magnifying-glass" }),
        el("span", { class: "rail-search-ph", text: "Find symbol…" }),
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
  // A folder crumb "opens" that folder: drop any focus and isolate every folder
  // at or below the clicked path prefix. state.folders holds folder KEYS (not
  // colours) — several unrelated long-tail folders can share GROUP_OTHER's one
  // hex, so filtering by colour would light up every folder that happens to
  // share that colour, anywhere in the repo, not just the ones under `prefix`.
  // An ancestor folder with no colour of its own still isolates correctly, by
  // sweeping up every descendant's key.
  function crumbFolder(prefix) {
    return function () {
      var hits = new Set();
      Object.keys(groupColor).forEach(function (k) {
        if (k === prefix || k.indexOf(prefix + "/") === 0) hits.add(k);
      });
      state.folders = hits;
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
          label: m.name + "  (" + m.symbol_count + ")", kind: "mod", group: moduleColor(m.name),
          act: function () { state.grain = 1; state.view = { x: 0, y: 0, k: 1 }; render(); } });
      });
      var seen = {};
      (DATA.file_edges || []).forEach(function (fe) {
        var a = FILES[fe.s].module, b = FILES[fe.t].module;
        if (a === b || seen[a + " " + b]) return;
        seen[a + " " + b] = 1;
        var md = moduleDepth(a);
        links.push({ a: centers[a], b: centers[b], seed: hashSeed(a + b), hot: true,
          sk: "mod:" + a, tk: "mod:" + b,
          grp: moduleColor(a), live: md >= 0, dep: md < 0 ? 0 : md, vol: 8 });
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
        var fk = it.kind === "file" ? dirGroup(it.ref.path) : dirGroup(it.ref.file);
        placed.push({ i: it.idx == null ? it.key : it.idx, x: x, y: y, style: style,
          label: it.label, kind: it.kind, dead: dead,
          group: it.kind === "file" ? colorForPath(it.ref.path) : colorForNode(it.ref), fk: fk,
          act: it.kind === "file" ? fileAct(it.ref) : null });
      });
    });

    if (grain === 1) {
      (DATA.file_edges || []).forEach(function (fe) {
        var a = pos["file:" + FILES[fe.s].path], b = pos["file:" + FILES[fe.t].path];
        if (!a || !b) return;
        var fd = fileFlow[fe.s], tf = FILES[fe.t];
        links.push({ a: a, b: b, seed: fe.s * 131 + fe.t,
          hot: FILES[fe.s].module !== FILES[fe.t].module,
          sk: "file:" + FILES[fe.s].path, tk: "file:" + FILES[fe.t].path,
          grp: colorForPath(FILES[fe.s].path), fk: dirGroup(FILES[fe.s].path),
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
          s: e.s, t: e.t, sk: e.s, tk: e.t, grp: colorForNode(N[e.s]), fk: dirGroup(N[e.s].file),
          live: sd >= 0, dep: sd < 0 ? 0 : sd, vol: N[e.t].fan_in });
      });
    }
    return { placed: placed, links: links, lobes: lobes, capped: capped };
  }

  function layoutFocus() {
    var f = state.focus;
    var soma = { x: VBW / 2, y: VBH / 2 };
    var placed = [{ i: f, x: soma.x, y: soma.y, style: NODE_STYLE.focus, label: N[f].name,
      kind: "focus", group: colorForNode(N[f]), fk: dirGroup(N[f].file) }];
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
            kind: side === "in" ? "caller" : "callee", group: colorForNode(N[idx]),
            fk: dirGroup(N[idx].file) });
          var src = side === "in" ? idx : f, dst = side === "in" ? f : idx;
          var sd = symDepth(src);
          links.push({ a: side === "in" ? { x: x, y: y } : soma,
            b: side === "in" ? soma : { x: x, y: y },
            seed: hashSeed(N[idx].key), hot: h === 1, thin: side === "in",
            s: src, t: dst, sk: src, tk: dst, grp: colorForNode(N[idx]), fk: dirGroup(N[idx].file),
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
    return { placed: placed, links: links, lobes: [] };
  }

  function renderGraphSVG() {
    var lay = state.focus != null ? layoutFocus() : layoutOverview();
    var g = el("g", { class: "pz",
      transform: "translate(" + state.view.x + "," + state.view.y + ") scale(" + state.view.k + ")" });

    lay.lobes.forEach(function (lo) {
      var lc = moduleColor(lo.name), plain = lc === GROUP_OTHER;
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
      var unbudgeted = state.focus != null || state.grain === 0;   // few links here
      flowSet = new Set(liveIdx.slice(0, unbudgeted ? liveIdx.length : FLOW_BUDGET));
      cometSet = new Set(liveIdx.slice(0, unbudgeted ? liveIdx.length : COMET_BUDGET));
      flowN = flowSet.size;
    }

    var edgeG = el("g", { fill: "none", "stroke-linecap": "round" });
    lay.links.forEach(function (lk, li) {
      if (!lk.a || !lk.b) return;
      var d = dendrite(lk.a, lk.b, lk.seed, { bow: lk.hot ? 0.42 : 0.3 });
      var imp = !!lk.imp;
      var col = imp ? IMPORT_EDGE : (lk.grp || GROUP_OTHER);
      var w = imp ? 1.5 : lk.hot ? 2.8 : lk.thin ? 1.9 : 2.2;
      var op = imp ? 0.5 : lk.hot ? 0.92 : 0.76;
      var grp = el("g", { class: "edge" });
      if (!imp)   // soft colour glow: a run of same-folder edges reads as one strand
        grp.appendChild(el("path", { d: d.d, stroke: col, "stroke-width": w + 4,
          opacity: lk.hot ? 0.16 : 0.09 }));
      grp.appendChild(el("path", { d: d.d, stroke: col, "stroke-width": w, opacity: op,
        "stroke-dasharray": imp ? "5 4" : null }));
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
        var pr = el("circle", { class: "emit", cx: p.x, cy: p.y, r: R + 2,
          fill: "none", stroke: ring, "stroke-width": 1.4 });
        var b = (-(rnd(p.i + 1) * 2.4)) + "s";
        pr.appendChild(el("animate", { attributeName: "r", values: (R + 2) + ";" + (R + 17),
          dur: "2.4s", begin: b, repeatCount: "indefinite" }));
        pr.appendChild(el("animate", { attributeName: "opacity", values: "0.55;0",
          dur: "2.4s", begin: b, repeatCount: "indefinite" }));
        wrap.appendChild(pr);
      }
      wrap.appendChild(el("circle", { class: "body", cx: p.x, cy: p.y, r: R,
        fill: st.fill, stroke: ring, "stroke-width": p.kind === "focus" ? 2 : 1.6 }));
      wrap.appendChild(el("text", { x: p.x, y: p.y + R + 12, "text-anchor": "middle",
        "font-size": p.kind === "focus" ? 12 : 10.5,
        fill: p.kind === "focus" ? "#f5f4ff" : p.dead ? "#595d6c" : "#b2b6ca", text: p.label }));
      var act = p.act || (typeof p.i === "number"
        ? function () { go("graph", N[p.i].key); } : null);
      var pinnable = p.i != null && p.kind !== "focus" && p.kind !== "ext";
      wrap.style.cursor = (act || pinnable) ? "pointer" : "default";
      if (pinnable && IS_TOUCH) {
        // no click/dblclick disambiguation on touch: one tap goes straight
        // to the primary action, same as Enter from the keyboard below.
        wrap.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (act) { state.pin = null; act(); } else togglePin(p.i);
        });
      } else if (pinnable) {
        // one click pins a spotlight on this node + its edges; a double click
        // drills in, the old single-click behaviour. 200ms lets dblclick win.
        var clickT = 0;
        wrap.addEventListener("click", function (ev) {
          ev.stopPropagation();
          clearTimeout(clickT);
          clickT = setTimeout(function () { togglePin(p.i); }, 200);
        });
        wrap.addEventListener("dblclick", function (ev) {
          ev.stopPropagation();
          clearTimeout(clickT);
          if (act) { state.pin = null; act(); }
        });
      } else if (act) {
        wrap.addEventListener("click", function (ev) { ev.stopPropagation(); act(); });
      }
      // the click/dblclick split above has no keyboard equivalent — a
      // keyboard user gets straight to the primary action (drill in, same as
      // a double click) on Enter/Space; hovering already pins nothing for a
      // mouse user either, so nothing is lost.
      if (act || pinnable) {
        wrap.setAttribute("tabindex", "0");
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

  // ---- graph paint / pan / zoom ---------------------------------------
  // The scene <g> is built once per structural change (grain, focus, hops,
  // showDead). Pan and zoom only rewrite its `transform` — no relayout, no DOM
  // churn — and hover only nudges opacity. That is what keeps the canvas smooth.
  var canvasEl, svgEl, emptyEl, sceneG, edgeItems = [], nodeItems = [];
  var drag = null, panPend = null, panRaf = 0, settleT = 0, lastFlowN = 0, animPaused = false;
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

  function clampK(k) { return Math.max(0.3, Math.min(4, k)); }
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
  function liveView() {   // pan/wheel: suspend the eased transition for 1:1 tracking
    if (canvasEl) canvasEl.classList.add("dragging");
    freezeAnims();
    clearTimeout(settleT);
    settleT = setTimeout(function () {
      if (canvasEl) canvasEl.classList.remove("dragging");
      thawAnims();
    }, 160);
    applyView();
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
  function applyHighlight() {
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
    // it.grp here is a folder KEY (dirGroup path, or the OTHER_KEY sentinel for
    // the legend's "other folders" row) — never a colour — so two folders that
    // happen to share GROUP_OTHER's hex never light each other up. See the note
    // above crumbFolder().
    var filtering = state.folders && state.folders.size > 0;
    function fdim(fk) {
      if (!filtering) return false;
      if (fk == null) return true;
      if (state.folders.has(fk)) return false;
      return !(state.folders.has(OTHER_KEY) && groupColor[fk] === GROUP_OTHER);
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
  function toggleFolder(key) {
    if (state.folders.has(key)) state.folders.delete(key);
    else state.folders.add(key);
    applyHighlight();
    refreshLegend();
  }
  function clearFolders() {
    state.folders.clear();
    applyHighlight();
    refreshLegend();
  }
  function refreshLegend() {
    var old = canvasEl && canvasEl.querySelector(".legend");
    if (old) old.replaceWith(legend());
  }
  function paintGraph() {
    if (!svgEl) return;
    edgeItems = [];
    nodeItems = [];
    clear(svgEl);
    sceneG = renderGraphSVG();
    svgEl.appendChild(sceneG);
    if (emptyEl) emptyEl.hidden = lastPlacedN > 0;
    applyView();
    applyHighlight();
  }

  // window-level drag listeners: bound once, not per render
  window.addEventListener("mousemove", function (e) {
    if (!drag) return;
    panPend = e;
    if (panRaf) return;
    panRaf = requestAnimationFrame(function () {
      panRaf = 0;
      drag.moved = true;
      state.view.x = drag.vx + (panPend.clientX - drag.x);
      state.view.y = drag.vy + (panPend.clientY - drag.y);
      liveView();
    });
  });
  window.addEventListener("mouseup", function () {
    if (!drag && !animPaused) return;
    lastPanMoved = !!(drag && drag.moved);
    drag = null;
    // covers both a finished drag and a bare click (which armed no settle timer)
    clearTimeout(settleT);
    settleT = setTimeout(function () {
      if (canvasEl) canvasEl.classList.remove("dragging");
      thawAnims();
    }, 120);
  });

  function stage() {
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
          class: last ? "cur seg" : "seg", text: s,
          on: last
            ? (fobj ? { click: fileAct(fobj) } : null)
            : { click: crumbFolder(segs.slice(0, i + 1).join("/")) },
        }));
      });
      crumb.appendChild(el("i", { class: "ph ph-caret-right" }));
      crumb.appendChild(el("span", { class: "cur seg fn", text: n.name + "()",
        on: { click: function () { state.view = { x: 0, y: 0, k: 1 }; applyView(); } } }));
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
    var grainSeg = el("div", { class: "seg" },
      ["Module", "File", "Function"].map(function (label, gi) {
        return el("button", { class: state.grain === gi ? "on" : "",
          on: { click: function () { state.grain = gi; state.view = { x: 0, y: 0, k: 1 }; render(); } } },
          [label]);
      }));

    // hops slider + flow pill repaint the graph in-place (paintGraph(), not the
    // full render()) so a drag/click stays cheap — but that means their own
    // controls must be kept in sync by hand, not left for a rerender to fix.
    var hopReadout = el("b", { class: "mono", text: String(state.hops) });
    var hop = el("div", { class: "hopwrap" }, [
      "hops",
      el("input", { type: "range", min: "1", max: "4", value: String(state.hops),
        on: { input: function (e) {
          state.hops = +e.target.value;
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

    var bar = el("div", { class: "stage-bar" }, [
      filesPill, crumbWrap, grainSeg, state.focus != null ? hop : null,
      el("div", { class: "spacer" }),
      detailsPill, flowPill,
      state.focus != null ? resetPill : deadPill,
    ]);

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
    } else {
      // lastPlacedN is what layoutOverview() actually placed — NOT
      // DATA.stats.symbols, which stays fixed at the repo's total symbol
      // count no matter the grain (2 module lobes, or 45 files, or a
      // per-module-capped set of functions all reported the same number).
      base = ["module", "file", "function"][state.grain] + " grain · " +
        lastPlacedN + " node" + (lastPlacedN === 1 ? "" : "s");
      if (lastPlacedCapped) base += " (capped per module)";
    }
    cap.textContent = state.flow && lastFlowN ? base + " · " + lastFlowN + " flows" : base;
  }
  function legend() {
    var box = el("div", { class: "legend" }, [el("div", { class: "lk", text: "FOLDERS" })]);
    var sel = state.folders;
    // each folder row is a filter toggle — click one to isolate it, the rest of
    // the graph drops to a wash (not hidden). Empty selection = show everything.
    // `key` is a folder path (or OTHER_KEY for the catch-all row below) — the
    // filter is matched on that key, never on `hex`, which the "other folders"
    // row shares with every long-tail folder in the repo.
    function folderRow(key, hex, label) {
      var on = sel.has(key);
      return el("div", {
        class: "row folder" + (on ? " on" : "") + (sel.size && !on ? " off" : ""),
        title: "click to isolate this folder",
        on: { click: function () { toggleFolder(key); } },
      }, [
        el("span", { class: "sw dot", style: "background:" + hex }),
        el("span", { class: "gname", text: label }),
      ]);
    }
    groupOrder.forEach(function (k) {
      box.appendChild(folderRow(k, groupColor[k], k === "(root)" ? "· repo root" : k));
    });
    if (Object.keys(groupColor).length > groupOrder.length)
      box.appendChild(folderRow(OTHER_KEY, GROUP_OTHER, "other folders"));
    if (sel.size)
      box.appendChild(el("div", { class: "row reset", on: { click: clearFolders } },
        [el("span", { class: "sw" }), "show all folders"]));
    box.appendChild(el("div", { class: "lk", style: "margin-top:9px", text: "EDGE" }));
    box.appendChild(el("div", { class: "row" }, [
      el("span", { class: "sw", style: "background:var(--color-neutral-300)" }),
      "call · thicker = same file",
    ]));
    box.appendChild(el("div", { class: "row" }, [el("span", { class: "sw dash" }), "import / external"]));
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
        zbtn("ph ph-crosshair", "Reset view", function () { state.view = { x: 0, y: 0, k: 1 }; applyView(); }),
      ]),
      el("div", { class: "cap" }),
    ]);
    setTimeout(updateCap, 0);
    return box;
  }
  function zbtn(icon, label, fn) {
    return el("button", { "aria-label": label, on: { click: fn } }, [el("i", { class: icon })]);
  }
  function wireCanvas() {
    canvasEl.addEventListener("mousedown", function (e) {
      if (e.target.closest(".gnode")) return;
      e.preventDefault();
      drag = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y };
      canvasEl.classList.add("dragging");
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
      liveView();
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
      return el("aside", { class: "insp", "aria-label": "Symbol details" }, [head, body, foot]);
    }
    var n = N[state.focus];
    var reachIn = reachSet(state.focus, inAdj);
    var filesHit = new Set();
    reachIn.forEach(function (i) { filesHit.add(N[i].file); });
    var totalFiles = (DATA.stats && DATA.stats.files) || 1;

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

    if (n.excerpt)
      body.appendChild(el("div", { class: "section" }, [
        el("div", { class: "lbl", text: n.file + ":" + n.line[0] + "-" + n.line[1] }),
        el("pre", { class: "excerpt", text: n.excerpt }),
      ]));

    foot.appendChild(el("a", { class: "btn primary", href: editorUri(n), text: "Open in editor" }));
    var copyBtn = el("button", { class: "btn", text: "Copy key" });
    copyBtn.addEventListener("click", function () { copyText(n.key, copyBtn); });
    foot.appendChild(copyBtn);
    return el("aside", { class: "insp", "aria-label": "Symbol details" }, [head, body, foot]);
  }
  function card(l, v) {
    return el("div", { class: "statcard" }, [
      el("div", { class: "lbl", text: l }), el("div", { class: "val", text: v == null ? "0" : String(v) }),
    ]);
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
    var covered = syms.reduce(function (s, m) { return s + (m.line[1] - m.line[0] + 1); }, 0);
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
  function listSection(label, idxs) {
    var sec = el("div", { class: "section" }, [el("div", { class: "lbl", text: label })]);
    var uniq = Array.from(new Set(idxs || []));
    if (!uniq.length) {
      sec.appendChild(el("div", { class: "rowitem muted", text: "none at this tier" }));
      return sec;
    }
    uniq.slice(0, 14).forEach(function (i) {
      var n = N[i];
      sec.appendChild(el("div", { class: "rowitem is-link",
        on: { click: function () { go("graph", n.key); } } }, [
        el("span", { class: "rk", text: kindLabel(n.kind) }),
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
    // encodeURI, not encodeURIComponent: it leaves "/" and the drive-letter
    // ":" alone while still escaping spaces — real in a repo path like
    // "…/My Project/…", which would otherwise truncate the vscode:// URI.
    return encodeURI("vscode://file" + root + "/" + n.file + ":" + n.line[0]);
  }

  // ---- palette --------------------------------------------
  var paletteOpen = false;
  function openPalette() {
    if (paletteOpen) return;
    paletteOpen = true;
    var restoreFocus = document.activeElement;
    var sel = 0, matches = N.slice(0, 60);
    var input = el("input", { placeholder: "symbol name or file…", spellcheck: "false",
      "aria-label": "Find symbol", role: "combobox", "aria-expanded": "true" });
    var list = el("ul");
    var empty = el("li", { class: "palette-empty", text: "No matching symbols", hidden: true });
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
    function refresh() {
      var q = input.value.toLowerCase().trim();
      matches = (q ? N.filter(function (n) {
        return (n.qual + " " + n.file).toLowerCase().indexOf(q) >= 0;
      }) : N).slice(0, 60);
      sel = 0;
      clear(list);
      if (!matches.length) { list.appendChild(empty); empty.hidden = false; return; }
      matches.forEach(function (n, i) {
        list.appendChild(el("li", { class: i === sel ? "on" : "",
          on: { click: function () { pick(n); } } }, [
          el("span", { class: "rk", text: kindLabel(n.kind) }),
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
      wrap.appendChild(el("div", { class: "tl-item" + (touched ? " touch" : "") }, kids));
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
    // matches "tests/…" AND "pkg/tests/…" — a top-level-only check missed any
    // nested tests dir, which stayed in the cake with "hide tests" turned on.
    var visible = FILES.filter(function (f) {
      return !(hide && /(^|\/)tests\//.test(f.path));
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
        if (w > 34)
          gEl.appendChild(el("text", { x: x + w / 2, y: y + h / 2 + 1, "text-anchor": "middle",
            "dominant-baseline": "middle", class: "cake-name",
            text: fitText(b.cyc ? "cycle · " + b.files.length : b.label, w - 10) }));
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
        on: { click: function () { state.hideTests = !state.hideTests; render(); } } },
        [el("i", { class: "ph ph-flask" }), "Hide tests"]),
    ];
    var legend = el("div", { class: "map-legend" }, [
      el("div", { class: "lk", text: "LAYER CAKE" }),
      lgRow(el("span", { class: "sw", style: "width:26px;height:12px;border-radius:3px;" +
        "background:" + hexA(GROUP_HUES[0], 0.24) + ";border:1px solid " + GROUP_HUES[0] }),
        "block = file · width = lines of code · fill = folder"),
      lgRow(el("span", { class: "sw", style: "width:14px;height:12px;border-radius:3px;" +
        "background:transparent;border:2px solid " + GROUP_HUES[0] }), "↺ merged = import cycle"),
      lgRow(el("span", { class: "sw", style: "border:0" }), "L0 imports nothing in-repo · hover a block for its imports"),
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
      (outAdj[u] || []).forEach(function (v) {
        if (!depth.has(v)) { depth.set(v, du + 1); order.push(v); }
      });
    }
    var cols = [];
    depth.forEach(function (d, i) { (cols[d] = cols[d] || []).push(i); });
    var maxD = cols.length - 1;
    var thin = order.length < 8;   // frontier died early — dynamic dispatch ahead

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
    var W = LEFT * 2 + (maxD + 1) * COLW + (thin ? 220 : 0);
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

    if (thin) {
      var lastLine = null;
      (N[root].excerpt || "").split("\n").forEach(function (ln) {
        if (/\(/.test(ln) && !/^\s*(def|class|@)/.test(ln)) lastLine = ln.trim();
      });
      var bx = LEFT + (maxD + 1) * COLW, by = TOP + maxRows * ROWH / 2 - 34;
      var dg = el("g", { class: "trace-dispatch" });
      dg.appendChild(el("rect", { x: bx, y: by, width: 200, height: 68, rx: 8,
        fill: "var(--color-surface-2)", stroke: "var(--color-accent-700)" }));
      dg.appendChild(el("text", { x: bx + 12, y: by + 20, class: "trace-nm", text: "⚡ dynamic dispatch" }));
      dg.appendChild(el("text", { x: bx + 12, y: by + 38, class: "trace-fl",
        text: "the indexer can't follow this" }));
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
        var f = fc.item.f, dead = fileAllDead(f.fi), cw = Math.max(0, fc.w - GAP);
        var cell = el("g", { class: "mass-cell",
          on: { click: function () { fileAct(f)(); } } }, [
          el("title", { text: f.path.split("/").pop() + " · " + (f.loc || 0) + " loc · " +
            (churnOf[f.fi] || 0) + " commits" + (dead ? " · every function unreachable" : "") }),
        ]);
        cell.appendChild(el("rect", { x: fc.x, y: fc.y, width: cw,
          height: Math.max(0, fc.h - GAP), rx: 3, fill: churnFill(f.fi) }));
        if (dead)
          cell.appendChild(el("rect", { x: fc.x, y: fc.y, width: cw,
            height: Math.max(0, fc.h - GAP), rx: 3, fill: "url(#cm-hatch)" }));
        if (cw > 40 && fc.h > 20) {
          cell.appendChild(el("text", { x: fc.x + 5, y: fc.y + 13, class: "mass-nm",
            text: fitText(f.path.split("/").pop(), cw - 8, 5.8) }));
          if (fc.h > 34)
            cell.appendChild(el("text", { x: fc.x + 5, y: fc.y + 25, class: "mass-sub",
              text: (f.loc || 0) + " loc" + (dead ? " · dead" : "") }));
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
      out.push(el("span", { class: "term", tabindex: "0", "data-def": glossary[m[1]] || "",
        "aria-label": m[1] + ": " + (glossary[m[1]] || ""), text: m[1] }));
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
      function reset() {
        picked = null;
        Array.prototype.forEach.call(block.querySelectorAll(".qopt"), function (b) {
          b.classList.remove("correct", "sel", "wrong");
        });
        clear(fb);
        fb.className = "qfb";
      }
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
          fb.appendChild(el("button", { class: "qretry", text: "Try again",
            on: { click: reset } }));
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
  function render() {
    clear(APP);
    // model.build() returns {empty:true} with none of the usual keys (no
    // stats/nodes/files/…) when the DB has no index yet. The CLI already
    // refuses to render this file in that case, but a hand-built or
    // hand-edited payload can still reach the browser this way — show a real
    // message instead of a blank canvas.
    if (DATA.empty) {
      APP.appendChild(el("div", { class: "empty-index" }, [
        el("i", { class: "ph ph-database" }),
        el("p", { text: "No index yet — run `codemap scan` first, then `codemap explore`." }),
      ]));
      return;
    }
    var frag = document.createDocumentFragment();
    frag.appendChild(topbar());
    var banner = staleBanner();
    if (banner) frag.appendChild(banner);
    if (state.tab === "graph") {
      var railNode = rail(), inspNode = inspector();
      railNode.classList.toggle("open", state.mobileRail);
      inspNode.classList.toggle("open", state.mobileInsp);
      var mobileOpen = state.mobileRail || state.mobileInsp;
      var backdrop = el("div", { class: "mobile-backdrop" + (mobileOpen ? " show" : ""),
        on: { click: function () { state.mobileRail = false; state.mobileInsp = false; render(); } } });
      frag.appendChild(el("main", { class: "view" }, [railNode, stage(), inspNode, backdrop]));
    } else if (state.tab === "map")
      frag.appendChild(el("main", { class: "view" }, [mapTab()]));
    else if (state.tab === "timeline")
      frag.appendChild(el("main", { class: "view" }, [timelineTab()]));
    else
      frag.appendChild(el("main", { class: "view" }, [learnTab()]));
    APP.appendChild(frag);
    updateCap();
  }

  route();
})();
