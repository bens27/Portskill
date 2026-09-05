/*! Portskill Iterate Mode v1 — pick / pulse / working set / A/B / persist / AI Agent */
(function () {
  "use strict";

  var DEFAULTS = {
    "--paper": "#f3f4f1",
    "--panel": "#ffffff",
    "--ink": "#15181d",
    "--muted": "#5d6572",
    "--line": "#dde0da",
    "--cobalt": "#2743d6",
    "--green": "#188a5e",
    "--amber": "#b97303",
    "--red": "#bf3b3b",
    "--violet": "#6c46c8"
  };

  var KNOBS = Object.keys(DEFAULTS);
  var state = {
    on: false,
    picking: true,
    preview: "working", // "current" | "working"
    working: {},
    baseline: {},
    selected: null,
    selectedLabel: ""
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function copyDefaults() {
    var out = {};
    KNOBS.forEach(function (k) { out[k] = DEFAULTS[k]; });
    return out;
  }

  function readComputedTokens() {
    var cs = getComputedStyle(document.documentElement);
    var out = {};
    KNOBS.forEach(function (k) {
      var v = (cs.getPropertyValue(k) || "").trim();
      out[k] = v || DEFAULTS[k];
    });
    return out;
  }

  function applyVars(map) {
    var root = document.documentElement;
    KNOBS.forEach(function (k) {
      if (map[k]) root.style.setProperty(k, map[k]);
    });
  }

  function clearInlineVars() {
    var root = document.documentElement;
    KNOBS.forEach(function (k) { root.style.removeProperty(k); });
  }

  function applyPreview() {
    if (state.preview === "working") {
      applyVars(state.working);
    } else {
      clearInlineVars();
      applyVars(state.baseline);
    }
  }

  function jsonFetch(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "content-type": "application/json" }, opts.headers || {});
    return fetch(url, opts).then(function (res) {
      return res.json().then(function (body) {
        return { ok: res.ok, status: res.status, body: body };
      }).catch(function () {
        return { ok: res.ok, status: res.status, body: { ok: false, message: "invalid JSON" } };
      });
    });
  }

  function setStatus(el, text, kind) {
    if (!el) return;
    el.textContent = text || "";
    el.className = "pr-it-status" + (kind ? " " + kind : "");
  }

  function isIterateChrome(el) {
    if (!el || !el.closest) return true;
    return !!(el.closest("#pr-iterate-panel") || el.closest("#pr-iterate-launcher"));
  }

  function pickable(el) {
    if (!el) return null;
    if (el.nodeType !== 1) el = el.parentElement;
    if (!el || !el.closest) return null;
    if (isIterateChrome(el)) return null;
    if (!el.closest(".app")) return null;
    var tagged = el.closest("[data-iterate]");
    if (tagged && !isIterateChrome(tagged) && tagged.closest(".app")) return tagged;
    var cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      if (isIterateChrome(cur)) return null;
      if (!cur.closest(".app")) break;
      if (cur.classList && cur.classList.contains("app")) break;
      var tag = (cur.tagName || "").toUpperCase();
      if (/^(BUTTON|A|INPUT|SELECT|TEXTAREA|LABEL|H1|H2|H3|H4|H5|NAV|HEADER|ASIDE|MAIN|SECTION|ARTICLE|LI|UL|OL|TABLE|TR|TD|TH|P|IMG|SVG|FORM|FIELDSET)$/.test(tag)) {
        return cur;
      }
      var cls = typeof cur.className === "string" ? cur.className.trim() : "";
      if (cur.id || cls) return cur;
      cur = cur.parentElement;
    }
    return el.closest(".app *") ? el : null;
  }

  function clearHover() {
    $all(".pr-iterate-hover").forEach(function (n) { n.classList.remove("pr-iterate-hover"); });
  }

  function onPointerMove(ev) {
    if (!state.on || !state.picking) return;
    if (isIterateChrome(ev.target)) { clearHover(); return; }
    var el = pickable(ev.target);
    clearHover();
    if (el && el !== state.selected) el.classList.add("pr-iterate-hover");
  }

  function labelFor(el) {
    if (!el) return "";
    var di = el.getAttribute && el.getAttribute("data-iterate");
    if (di) return di;
    if (el.className && typeof el.className === "string") {
      var cls = el.className.trim().split(/\s+/).slice(0, 3).join(".");
      if (cls) return el.tagName.toLowerCase() + "." + cls;
    }
    return el.tagName ? el.tagName.toLowerCase() : "node";
  }

  function clearPulse() {
    $all(".pr-iterate-pulse").forEach(function (n) { n.classList.remove("pr-iterate-pulse"); });
    clearHover();
  }

  function pulse(el) {
    clearPulse();
    if (el) el.classList.add("pr-iterate-pulse");
  }

  function renderKnobs() {
    var host = $("#pr-it-knobs");
    if (!host) return;
    host.innerHTML = "";
    KNOBS.forEach(function (k) {
      var row = document.createElement("div");
      row.className = "pr-it-knob";
      var lab = document.createElement("label");
      lab.textContent = k;
      var color = document.createElement("input");
      color.type = "color";
      var val = state.working[k] || DEFAULTS[k];
      try { color.value = toHex(val); } catch (e) { color.value = "#000000"; }
      var text = document.createElement("input");
      text.type = "text";
      text.value = val;
      function commit(v) {
        state.working[k] = v;
        if (state.preview === "working") applyVars(state.working);
        text.value = v;
        try { color.value = toHex(v); } catch (e) {}
        persistWorkingSetRemote();
      }
      color.addEventListener("input", function () { commit(color.value); });
      text.addEventListener("change", function () { commit(text.value.trim()); });
      row.appendChild(lab);
      row.appendChild(color);
      row.appendChild(text);
      host.appendChild(row);
    });
  }

  function toHex(v) {
    v = String(v || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(v)) {
      return ("#" + v[1] + v[1] + v[2] + v[2] + v[3] + v[3]).toLowerCase();
    }
    if (/^#fff$/i.test(v) || /^white$/i.test(v)) return "#ffffff";
    var m = v.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
    if (m) {
      return "#" + [m[1], m[2], m[3]].map(function (n) {
        var h = Number(n).toString(16);
        return h.length === 1 ? "0" + h : h;
      }).join("");
    }
    return DEFAULTS["--cobalt"];
  }

  var workingSaveTimer = null;
  function persistWorkingSetRemote() {
    clearTimeout(workingSaveTimer);
    workingSaveTimer = setTimeout(function () {
      jsonFetch("/iterate/working-set", {
        method: "POST",
        body: JSON.stringify({ workingSet: state.working, preview: state.preview, selected: state.selectedLabel })
      }).catch(function () {});
    }, 200);
  }

  function showTab(name) {
    $all(".pr-it-tab").forEach(function (t) {
      t.setAttribute("aria-selected", t.getAttribute("data-tab") === name ? "true" : "false");
    });
    $all(".pr-it-pane").forEach(function (p) {
      p.hidden = p.getAttribute("data-pane") !== name;
    });
    if (name === "collab") loadCollab();
  }

  function renderMessages(list) {
    var host = $("#pr-it-msgs");
    if (!host) return;
    host.innerHTML = "";
    (list || []).slice(-40).forEach(function (m) {
      var div = document.createElement("div");
      div.className = "pr-it-msg";
      var meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = (m.role || "?") + " · " + (m.at || m.id || "");
      var text = document.createElement("div");
      text.textContent = m.text || "";
      div.appendChild(meta);
      div.appendChild(text);
      host.appendChild(div);
    });
    host.scrollTop = host.scrollHeight;
  }

  function loadCollab() {
    jsonFetch("/iterate/collab/messages").then(function (r) {
      renderMessages((r.body && r.body.messages) || []);
    }).catch(function () {});
  }

  function sendCollab() {
    var ta = $("#pr-it-composer");
    var status = $("#pr-it-collab-status");
    var text = (ta && ta.value || "").trim();
    if (!text) { setStatus(status, "Type a note first", "err"); return; }
    jsonFetch("/iterate/collab/send", {
      method: "POST",
      body: JSON.stringify({ text: text, role: "ben" })
    }).then(function (r) {
      if (!r.ok || !(r.body && r.body.ok)) {
        setStatus(status, (r.body && r.body.message) || ("send failed " + r.status), "err");
        return;
      }
      if (ta) ta.value = "";
      setStatus(status, "Sent", "ok");
      renderMessages((r.body && r.body.messages) || []);
    }).catch(function (e) {
      setStatus(status, String(e), "err");
    });
  }

  function doPersist() {
    var status = $("#pr-it-persist-status");
    setStatus(status, "Writing…");
    jsonFetch("/iterate/persist", {
      method: "POST",
      body: JSON.stringify({ workingSet: state.working })
    }).then(function (r) {
      if (!r.ok || !(r.body && r.body.ok)) {
        setStatus(status, (r.body && r.body.message) || ("persist failed " + r.status), "err");
        return;
      }
      setStatus(status, "Wrote " + ((r.body && r.body.path) || "iterate-tokens.css"), "ok");
      state.baseline = Object.assign({}, state.working);
    }).catch(function (e) {
      setStatus(status, String(e), "err");
    });
  }

  function buildPanel() {
    if ($("#pr-iterate-panel")) return;
    var panel = document.createElement("div");
    panel.id = "pr-iterate-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Iterate Mode");
    panel.innerHTML =
      '<div class="pr-it-head"><strong>Iterate Mode</strong>' +
      '<button type="button" class="pr-it-btn" id="pr-it-close" title="Close">×</button></div>' +
      '<div class="pr-it-tabs" role="tablist">' +
      '<button type="button" class="pr-it-tab" data-tab="working" aria-selected="true">Working set</button>' +
      '<button type="button" class="pr-it-tab" data-tab="ab" aria-selected="false">A/B</button>' +
      '<button type="button" class="pr-it-tab" data-tab="collab" aria-selected="false">AI Agent</button>' +
      '</div>' +
      '<div class="pr-it-body">' +
      '<div class="pr-it-pane" data-pane="working">' +
      '<p class="pr-it-pick-hint">Hover to preview, click to select almost any element in the app. Interactive controls still work; Alt+click picks without firing them.</p>' +
      '<div class="pr-it-selected" id="pr-it-selected">Nothing selected</div>' +
      '<div id="pr-it-knobs"></div>' +
      '<div class="pr-it-row">' +
      '<button type="button" class="pr-it-btn primary" id="pr-it-persist">Persist</button>' +
      '<button type="button" class="pr-it-btn" id="pr-it-reset">Reset knobs</button>' +
      '</div>' +
      '<div class="pr-it-status" id="pr-it-persist-status"></div>' +
      '</div>' +
      '<div class="pr-it-pane" data-pane="ab" hidden>' +
      '<div class="pr-it-ab">' +
      '<button type="button" class="pr-it-btn" id="pr-it-ab-current" aria-pressed="false">Current</button>' +
      '<button type="button" class="pr-it-btn" id="pr-it-ab-working" aria-pressed="true">Working set</button>' +
      '</div>' +
      '<p class="pr-it-pick-hint">Toggle preview between persisted/current tokens and the unsaved working set.</p>' +
      '</div>' +
      '<div class="pr-it-pane" data-pane="collab" hidden>' +
      '<div class="pr-it-composer">' +
      '<textarea id="pr-it-composer" placeholder="Note for AI Agent / Jeeves…"></textarea>' +
      '<button type="button" class="pr-it-btn primary" id="pr-it-send">Send</button>' +
      '</div>' +
      '<div class="pr-it-status" id="pr-it-collab-status"></div>' +
      '<div class="pr-it-msgs" id="pr-it-msgs"></div>' +
      '</div>' +
      '</div>';
    document.body.appendChild(panel);

    $all(".pr-it-tab", panel).forEach(function (tab) {
      tab.addEventListener("click", function () { showTab(tab.getAttribute("data-tab")); });
    });
    $("#pr-it-close").addEventListener("click", function () { setOn(false); });
    $("#pr-it-persist").addEventListener("click", doPersist);
    $("#pr-it-reset").addEventListener("click", function () {
      state.working = Object.assign({}, state.baseline);
      renderKnobs();
      applyPreview();
      persistWorkingSetRemote();
    });
    $("#pr-it-ab-current").addEventListener("click", function () {
      state.preview = "current";
      $("#pr-it-ab-current").setAttribute("aria-pressed", "true");
      $("#pr-it-ab-working").setAttribute("aria-pressed", "false");
      applyPreview();
      persistWorkingSetRemote();
    });
    $("#pr-it-ab-working").addEventListener("click", function () {
      state.preview = "working";
      $("#pr-it-ab-current").setAttribute("aria-pressed", "false");
      $("#pr-it-ab-working").setAttribute("aria-pressed", "true");
      applyPreview();
      persistWorkingSetRemote();
    });
    $("#pr-it-send").addEventListener("click", sendCollab);
  }

  function setOn(on) {
    state.on = !!on;
    document.body.classList.toggle("pr-iterate-on", state.on);
    var btn = $("#pr-iterate-launcher");
    if (btn) btn.setAttribute("aria-pressed", state.on ? "true" : "false");
    if (state.on) {
      buildPanel();
      renderKnobs();
      applyPreview();
      showTab("working");
    } else {
      clearPulse();
      // Keep working vars if preview working; leave applied so Persist path still visible
    }
  }

  function onClickCapture(ev) {
    if (!state.on || !state.picking) return;
    if (isIterateChrome(ev.target)) return;
    // Allow normal Start/Stop/actions when user is not intending pick:
    // if they click a control with data-pr-action / switch, do NOT preventDefault —
    // only pulse-select when the click is on decorative/chrome targets OR Alt is held.
    var actionish = ev.target.closest && ev.target.closest("[data-pr-action], .pr-switch input, a[href], button.pr-btn, input, select, textarea, label.pr-switch");
    var el = pickable(ev.target);
    if (!el) return;
    // If it's an interactive control and Alt not held, let the app handle it (no block)
    if (actionish && !ev.altKey) {
      // still soft-pulse the chrome around it without blocking
      var soft = el.closest("[data-iterate]") || el;
      state.selected = soft;
      state.selectedLabel = labelFor(soft);
      pulse(soft);
      var sel = $("#pr-it-selected");
      if (sel) sel.textContent = "Selected: " + state.selectedLabel + " (Alt+click to pick without acting)";
      return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    state.selected = el;
    state.selectedLabel = labelFor(el);
    pulse(el);
    var sel2 = $("#pr-it-selected");
    if (sel2) sel2.textContent = "Selected: " + state.selectedLabel;
    persistWorkingSetRemote();
  }

  function ensureLauncher() {
    if ($("#pr-iterate-launcher")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "pr-iterate-launcher";
    btn.setAttribute("aria-pressed", "false");
    btn.title = "Toggle Iterate Mode";
    btn.textContent = "Iterate";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      setOn(!state.on);
    });
    document.body.appendChild(btn);
  }

  function tagChrome() {
    var map = [
      [".brand", "sidebar-brand"],
      [".topbar", "topbar"],
      [".pr-toolbar", "toolbar"],
      [".sidebar", "sidebar"],
      [".eyebrow", "eyebrow"],
      [".pr-stats", "stats"],
      [".main", "main"],
      [".content", "content"],
      [".view-head", "view-head"],
      [".pr-panel", "panel"],
      [".pr-subpanel", "subpanel"],
      [".pr-env-rail", "env-rail"],
      [":root", "root-tokens"]
    ];
    map.forEach(function (pair) {
      $all(pair[0]).forEach(function (n) {
        if (!n.getAttribute("data-iterate")) n.setAttribute("data-iterate", pair[1]);
      });
    });
    $all(".pr-switch").forEach(function (n, i) {
      if (!n.getAttribute("data-iterate")) n.setAttribute("data-iterate", "pr-switch-" + i);
    });
    $all(".badge").forEach(function (n, i) {
      if (!n.getAttribute("data-iterate")) n.setAttribute("data-iterate", "badge-" + i);
    });
    $all(".pr-range").forEach(function (n, i) {
      if (!n.getAttribute("data-iterate")) n.setAttribute("data-iterate", "range-" + i);
    });
    $all(".stat").forEach(function (n, i) {
      if (!n.getAttribute("data-iterate")) n.setAttribute("data-iterate", "stat-" + i);
    });
    $all(".pr-project").forEach(function (n, i) {
      if (!n.getAttribute("data-iterate")) n.setAttribute("data-iterate", "project-" + i);
    });
  }

  function bootFromServer() {
    return jsonFetch("/iterate/state").then(function (r) {
      var body = r.body || {};
      var ws = body.workingSet || body.working_set || {};
      if (ws && typeof ws === "object") {
        KNOBS.forEach(function (k) {
          if (typeof ws[k] === "string" && ws[k]) state.working[k] = ws[k];
        });
      }
      if (body.preview === "current" || body.preview === "working") state.preview = body.preview;
    }).catch(function () {});
  }

  function init() {
    state.baseline = readComputedTokens();
    state.working = Object.assign({}, state.baseline);
    ensureLauncher();
    tagChrome();
    document.addEventListener("click", onClickCapture, true);
    document.addEventListener("pointermove", onPointerMove, true);
    bootFromServer().then(function () {
      // ready
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
