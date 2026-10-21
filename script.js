
"use strict";

const $ = id => document.getElementById(id);

const esc = x => String(x ?? "").replace(
  /[&<>"']/g,
  c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[c])
);

const pretty = x => JSON.stringify(x, null, 2);

let cases = [];
let serial = 1;
let latest = null;
let activeFilter = "all";

const heading = {
  overview: [
    "Quality, with a point of view.",
    "A smarter place to test APIs and find the truth in every response."
  ],
  studio: [
    "The Test Studio.",
    "Design meaningful checks, not just requests."
  ],
  results: [
    "The Evidence Room.",
    "Every assertion, finding, and recommendation in view."
  ],
  history: [
    "Your testing timeline.",
    "Trace what changed across releases."
  ],
  methods: [
    "AI that explains itself.",
    "Classical algorithms, fully transparent decisions."
  ]
};

const ruleTypes = [
  ["status", "Expected HTTP status"],
  ["latency", "Max latency (ms)"],
  ["exists", "Field exists"],
  ["equals", "Field equals"],
  ["type", "Field type"],
  ["nonempty", "Response nonempty"],
  ["unique", "Unique array values"],
  ["range", "Numeric min/max"],
  ["schema", "JSON Schema"]
];


function toast(s) {
  $("toast").textContent = s;
  $("toast").style.display = "block";

  clearTimeout(toast.timer);

  toast.timer = setTimeout(
    () => $("toast").style.display = "none",
    4000
  );
}


async function api(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify(data)
        }
  );

  let result;

  try {
    result = await response.json();
  } catch {
    throw Error("Invalid server response");
  }

  if (!response.ok) {
    throw Error(result.error || "Request failed");
  }

  return result;
}


function go(page) {
  document.querySelectorAll(".page").forEach(x => {
    x.classList.toggle(
      "hidden",
      x.id !== page
    );
  });

  document.querySelectorAll(".nav").forEach(x => {
    x.classList.toggle(
      "active",
      x.dataset.page === page
    );
  });

  $("page-title").textContent = heading[page][0];
  $("page-sub").textContent = heading[page][1];
  $("breadcrumb").textContent = page.toUpperCase();

  if (page === "history") {
    loadHistory();
  }

  if (page === "overview") {
    loadChart();
  }

  window.scrollTo({
    top: 0,
    behavior: "smooth"
  });
}


function fresh() {
  return {
    id: serial++,
    name: "New API check",
    method: "GET",
    endpoint: "/demo/users",
    body: "{}",
    headers: "{}",
    compare: false,
    rules: [
      {type: "status", value: 200},
      {type: "latency", value: 2000}
    ]
  };
}


function sample() {
  cases = [
    {
      id: serial++,
      name: "Healthy user directory",
      method: "GET",
      endpoint: "/demo/users",
      body: "{}",
      headers: "{}",
      compare: false,
      rules: [
        {type: "status", value: 200},
        {type: "nonempty", path: "$", value: true},
        {type: "unique", path: "$", value: "id"}
      ]
    },
    {
      id: serial++,
      name: "Intentional duplicate defect",
      method: "GET",
      endpoint: "/demo/broken",
      body: "{}",
      headers: "{}",
      compare: false,
      rules: [
        {type: "status", value: 200},
        {type: "unique", path: "$", value: "id"},
        {
          type: "schema",
          path: "$",
          value: {
            type: "array",
            items: {
              type: "object",
              required: ["id", "name"],
              properties: {
                name: {type: "string"}
              }
            }
          }
        }
      ]
    },
    {
      id: serial++,
      name: "Create a user",
      method: "POST",
      endpoint: "/demo/users",
      body: pretty({
        name: "Ari",
        email: "ari@example.org"
      }),
      headers: "{}",
      compare: false,
      rules: [
        {type: "status", value: 201},
        {type: "exists", path: "id", value: true}
      ]
    },
    {
      id: serial++,
      name: "Verify inserted user",
      method: "GET",
      endpoint: "/demo/users/3",
      body: "{}",
      headers: "{}",
      compare: false,
      rules: [
        {type: "status", value: 200},
        {
          type: "equals",
          path: "name",
          value: "Ari"
        }
      ]
    }
  ];

  draw();
}


function ruleHTML(rule, i) {
  const value = typeof rule.value === "string"
    ? rule.value
    : JSON.stringify(rule.value);

  return `
    <div class="rule-row" data-rule-index="${i}">
      <select data-rule-key="type">
        ${ruleTypes.map(([key, label]) => `
          <option
            value="${key}"
            ${rule.type === key ? "selected" : ""}
          >
            ${esc(label)}
          </option>
        `).join("")}
      </select>

      <input
        data-rule-key="path"
        placeholder="JSON path e.g. 0.name"
        value="${esc(rule.path || "")}"
      >

      <input
        data-rule-key="value"
        placeholder="Expected value / JSON"
        value="${esc(value)}"
      >

      <button
        class="btn btn-danger btn-small"
        data-action="remove-rule"
        data-index="${i}"
      >
        ✕
      </button>
    </div>
  `;
}


function draw() {
  $("case-count").textContent =
    `${cases.length} TEST CASE${cases.length === 1 ? "" : "S"}`;

  $("cases").innerHTML = cases.length
    ? cases.map((c, i) => `
      <article class="case" data-id="${c.id}">
        <div class="case-head">
          <strong>
            <span class="chip">
              CASE ${String(i + 1).padStart(2, "0")}
            </span>
            ${esc(c.name)}
          </strong>

          <div class="case-tools">
            <button
              class="btn btn-soft btn-small"
              data-action="suggest"
            >
              ✦ Suggest rules
            </button>

            <button
              class="btn btn-small"
              data-action="clone"
            >
              Duplicate
            </button>

            <button
              class="btn btn-danger btn-small"
              data-action="delete"
            >
              Delete
            </button>
          </div>
        </div>

        <div class="field">
          <label>TEST NAME</label>
          <input
            data-field="name"
            value="${esc(c.name)}"
          >
        </div>

        <div class="form-grid">
          <div class="field">
            <label>METHOD</label>
            <select data-field="method">
              ${["GET", "POST", "PUT", "PATCH", "DELETE"]
                .map(m => `
                  <option
                    ${c.method === m ? "selected" : ""}
                  >
                    ${m}
                  </option>
                `).join("")}
            </select>
          </div>

          <div class="field">
            <label>ENDPOINT — DEMO PATH OR HTTPS URL</label>
            <input
              data-field="endpoint"
              value="${esc(c.endpoint)}"
            >
          </div>
        </div>

        <div class="form-grid">
          <div class="field">
            <label>HEADERS JSON</label>
            <textarea
              data-field="headers"
              spellcheck="false"
            >${esc(c.headers)}</textarea>
          </div>

          <div class="field">
            <label>BODY JSON</label>
            <textarea
              data-field="body"
              spellcheck="false"
            >${esc(c.body)}</textarea>
          </div>
        </div>

        <h4>
          Assertions
          <span class="chip">
            ${c.rules.length} RULES
          </span>
        </h4>

        ${c.rules.map(ruleHTML).join("")}

        <div class="case-footer">
          <label>
            <input
              type="checkbox"
              data-field="compare"
              ${c.compare ? "checked" : ""}
            >
            Compare saved contract
          </label>

          <button
            class="btn btn-soft btn-small"
            data-action="add-rule"
          >
            + Add assertion
          </button>
        </div>
      </article>
    `).join("")
    : `
      <div class="placeholder">
        No cases here yet. Add one, generate a suite,
        or load samples.
      </div>
    `;
}


function parseInput(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}


function sync(e) {
  const root = e.target.closest(".case");
  if (!root) return;

  const c = cases.find(
    x => x.id === Number(root.dataset.id)
  );

  if (!c) return;

  if (e.target.dataset.field) {
    const k = e.target.dataset.field;

    c[k] = k === "compare"
      ? e.target.checked
      : e.target.value;
  }

  const rr = e.target.closest(".rule-row");

  if (rr && e.target.dataset.ruleKey) {
    const r = c.rules[
      Number(rr.dataset.ruleIndex)
    ];

    const k = e.target.dataset.ruleKey;

    r[k] = k === "value"
      ? parseInput(e.target.value)
      : e.target.value;
  }
}


function parseJSON(value, label) {
  try {
    return JSON.parse(value || "{}");
  } catch {
    throw Error("Invalid JSON in " + label);
  }
}


async function editor(e) {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;

  const root = btn.closest(".case");
  if (!root) return;

  const c = cases.find(
    x => x.id === Number(root.dataset.id)
  );

  if (!c) return;

  const a = btn.dataset.action;

  if (a === "delete") {
    cases = cases.filter(x => x !== c);
  }

  if (a === "clone") {
    const item = structuredClone(c);
    item.id = serial++;
    item.name += " (copy)";
    cases.push(item);
  }

  if (a === "add-rule") {
    c.rules.push({
      type: "status",
      value: 200
    });
  }

  if (a === "remove-rule") {
    c.rules.splice(
      Number(btn.dataset.index),
      1
    );
  }

  if (a === "suggest") {
    btn.disabled = true;

    try {
      const res = await api("/api/suggest", {
        method: c.method,
        endpoint: c.endpoint,
        body: parseJSON(c.body, c.name),
        headers: parseJSON(c.headers, c.name)
      });

      c.rules = res.rules;
      toast(res.note);

    } catch (err) {
      toast(err.message);

    } finally {
      btn.disabled = false;
    }
  }

  draw();
}


function adopt(items) {
  if (!Array.isArray(items) || !items.length) {
    throw Error("No operations available");
  }

  cases = items.map(x => ({
    id: serial++,
    name: x.name || "Generated test",
    method: x.method || "GET",
    endpoint: x.endpoint || "/demo/users",
    body: pretty(x.body ?? {}),
    headers: "{}",
    compare: false,
    rules: x.rules || []
  }));

  draw();
  go("studio");

  toast(`${cases.length} test cases ready for review`);
}


function badge(s) {
  return `
    <span class="badge ${esc(s).replace(/\s+/g, "")}">
      ${esc(s)}
    </span>
  `;
}


async function run() {
  if (!cases.length) {
    return toast("Add at least one case");
  }

  const btn = $("run");
  btn.disabled = true;
  btn.textContent = "Running checks...";

  try {
    const prepared = cases.map(c => ({
      name: c.name,
      method: c.method,
      endpoint: c.endpoint,
      body: parseJSON(c.body, c.name + " body"),
      headers: parseJSON(c.headers, c.name + " headers"),
      compare: c.compare,
      rules: c.rules
    }));

    latest = await api("/api/run", {
      cases: prepared,
      allowExternalWrites: $("allow-writes").checked
    });

    update(latest);
    render();
    await loadChart();

    go("results");
    toast("Suite completed: " + latest.decision);

  } catch (err) {
    toast(err.message);

  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Run suite";
  }
}


function update(r) {
  $("score").textContent = r.score === null
    ? "Not scored"
    : `${r.score}/100`;

  $("coverage").textContent = `${r.coverage}%`;
  $("findings").textContent = r.failed;
  $("decision").textContent = r.decision;

  $("donut-number").textContent =
    r.score === null ? "—" : r.score;

  $("donut").style.background = `
    conic-gradient(
      var(--coral) ${r.score ?? 0}%,
      #efe9e3 ${r.score ?? 0}%
    )
  `;

  $("verdict").textContent = {
    READY: (
      "All configured checks passed. " +
      "Confirm requirement coverage before release."
    ),
    BLOCK: (
      "Critical failures detected. Review the evidence."
    ),
    REVIEW: (
      "Noncritical findings require attention."
    ),
    "NOT TESTED": (
      "Some cases have no assertions. " +
      "Quality score withheld."
    )
  }[r.decision] || "";

  const findings = r.results.flatMap(
    c => c.issues.map(x =>
      `${c.name}: ${x.message}` +
      (x.path ? ` (${x.path})` : "")
    )
  );

  $("insights").innerHTML = findings.length
    ? findings.map(x => `
        <div class="finding">${esc(x)}</div>
      `).join("")
    : `
      <div class="placeholder">
        No failures in the configured assertions.
      </div>
    `;
}


function render() {
  if (!latest) return;

  const r = latest;
  $("report-summary").className = "finding";

  $("report-summary").textContent =
    `RUN #${r.id || "—"} · ` +
    `${r.caseCount} cases · ` +
    `${r.passed} passed assertions · ` +
    `${r.failed} findings · ` +
    `Coverage ${r.coverage}% · ` +
    `Quality ${
      r.score === null ? "Not scored" : r.score + "/100"
    } · ${r.decision}`;

  const selected = r.results
    .map((test, i) => ({test, i}))
    .filter(({test}) =>
      activeFilter === "all" ||
      (
        activeFilter === "problems"
          ? test.decision !== "READY"
          : test.decision === "READY"
      )
    );

  $("result-list").innerHTML = selected.length
    ? selected.map(({test, i}) => `
      <details class="result">
        <summary>
          <div>
            <b>${esc(test.name)}</b>
            <small>
              ${esc(test.method)}
              ${esc(test.endpoint)}
              · HTTP ${test.status}
              · ${test.latency}ms
            </small>
          </div>

          <div>
            ${badge(test.decision)}
            &nbsp;
            ${
              test.score === null
                ? "Not scored"
                : test.score + "/100"
            }
            ▾
          </div>
        </summary>

        <div class="result-body">
          <h4>Rules & evidence</h4>

          ${test.checks.map(c => `
            <div class="check-line">
              <div>
                <b>${esc(c.type)}</b> · ${esc(c.path)}

                <small>
                  Expected: ${esc(pretty(c.expected))}
                  | Actual: ${esc(pretty(c.actual))}
                </small>

                ${c.passed ? "" : `
                  <small style="color:var(--red)">
                    ${esc(c.recommendation)}
                  </small>
                `}
              </div>

              ${badge(c.passed ? "READY" : "BLOCK")}
            </div>
          `).join("") || `
            <div class="finding">
              No assertions configured — NOT TESTED.
            </div>
          `}

          <h4>Findings & recommended actions</h4>

          ${test.issues.map(x => `
            <div class="finding">
              <b>${esc(x.severity.toUpperCase())}</b>
              · ${esc(x.message)}
            </div>
          `).join("") || `
            <p>No issues raised by the configured checks.</p>
          `}

          <h4>Actual response</h4>
          <pre class="code">${esc(pretty(test.data))}</pre>

          <button
            class="btn btn-soft"
            data-baseline="${i}"
          >
            ◇ Save response contract baseline
          </button>
        </div>
      </details>
    `).join("")
    : `
      <div class="placeholder">
        No tests in this filter.
      </div>
    `;
}


async function saveBaseline(i) {
  if (!latest) return;

  const c = latest.results[i];

  if (
    c.decision !== "READY" ||
    c.status < 200 ||
    c.status >= 300
  ) {
    toast(
      "Only passing 2xx responses can become " +
      "new contract baselines."
    );
    return;
  }

  if (!confirm(
    `Save/overwrite response schema for ` +
    `${c.method} ${c.endpoint}?`
  )) {
    return;
  }

  try {
    await api("/api/baseline", {
      key: c.method + " " + c.endpoint,
      schema: c.schema
    });

    toast(
      "Baseline saved. Enable Compare in the " +
      "corresponding case to detect changes."
    );

  } catch (e) {
    toast(e.message);
  }
}


async function loadChart() {
  try {
    const items = (
      await api("/api/history")
    ).slice(0, 10).reverse();

    $("chart").innerHTML = items.length
      ? items.map(r => `
        <div class="bar-item">
          <b>${r.score === null ? "—" : r.score}</b>

          <div
            class="bar-body"
            style="height:${Math.max(
              3,
              r.score ?? 0
            )}%"
          ></div>

          <small>#${r.id}</small>
        </div>
      `).join("")
      : `
        <div class="placeholder">
          Execute a suite to begin your quality history.
        </div>
      `;

  } catch (e) {
    toast(e.message);
  }
}


async function loadHistory() {
  try {
    const items = await api("/api/history");

    $("history-rows").innerHTML = items.map(r => `
      <tr>
        <td>#${r.id}</td>

        <td>
          ${esc(
            r.created.slice(0, 19).replace("T", " ")
          )}
        </td>

        <td>
          ${
            r.score === null
              ? "Not scored"
              : r.score + "/100"
          }
        </td>

        <td>${r.coverage}%</td>
        <td>${badge(r.decision)}</td>
      </tr>
    `).join("") || `
      <tr>
        <td colspan="5">No runs yet.</td>
      </tr>
    `;

  } catch (e) {
    toast(e.message);
  }
}


function download() {
  if (!latest) {
    return toast("Run a test suite first");
  }

  const blob = new Blob(
    [pretty(latest)],
    {type: "application/json"}
  );

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");

  a.href = url;
  a.download =
    `ruleguard-report-${latest.id || "current"}.json`;

  document.body.append(a);
  a.click();
  a.remove();

  setTimeout(
    () => URL.revokeObjectURL(url),
    1200
  );
}


document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".nav").forEach(x => {
    x.onclick = () => go(x.dataset.page);
  });

  document.querySelectorAll("[data-go]").forEach(x => {
    x.onclick = () => go(x.dataset.go);
  });

  $("top-new").onclick = () => {
    cases.push(fresh());
    draw();
    go("studio");
  };

  $("demo-action").onclick = () => {
    sample();
    go("studio");
  };

  $("sample").onclick = sample;

  $("add").onclick = () => {
    cases.push(fresh());
    draw();
  };

  $("clear").onclick = () => {
    if (confirm("Clear all draft cases?")) {
      cases = [];
      draw();
    }
  };

  $("run").onclick = run;

  $("cases").addEventListener("input", sync);
  $("cases").addEventListener("change", sync);
  $("cases").addEventListener("click", editor);

  $("generate").onclick = async () => {
    try {
      const r = await api("/api/generate", {
        method: $("gen-method").value,
        endpoint: $("gen-endpoint").value
      });

      adopt(r.cases);

    } catch (e) {
      toast(e.message);
    }
  };

  $("openapi-toggle").onclick = () => {
    $("openapi-box").classList.toggle("hidden");
  };

  $("openapi-import").onclick = async () => {
    try {
      const r = await api("/api/openapi", {
        spec: JSON.parse($("openapi-json").value)
      });

      adopt(r.cases);

    } catch (e) {
      toast(e.message);
    }
  };

  document.querySelectorAll(".filter").forEach(x => {
    x.onclick = () => {
      activeFilter = x.dataset.filter;

      document.querySelectorAll(".filter").forEach(y => {
        y.classList.toggle(
          "active",
          x === y
        );
      });

      render();
    };
  });

  $("result-list").addEventListener("click", e => {
    const x = e.target.closest("[data-baseline]");

    if (x) {
      saveBaseline(Number(x.dataset.baseline));
    }
  });

  $("refresh").onclick = loadHistory;
  $("download").onclick = download;

  sample();
  go("overview");

  api("/api/latest")
    .then(r => {
      if (r) {
        latest = r;
        update(r);
        render();
      }
    })
    .catch(e => toast(e.message));

  loadChart();
});
