"use strict";
const $ = (id) => document.getElementById(id),
  esc = (x) =>
    String(x ?? "—").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
let state = { datasets: [], runs: [] },
  run = null,
  runId = "",
  rows = [],
  cursor = -1,
  selected = null,
  entities = {},
  charts = {},
  busy = false,
  inspectionSerial = 0,
  runEpoch = 0;
const titles = {
  dashboard: "نمای کلی دانشکده",
  experiment: "پیکربندی آزمایش",
  monitor: "پایش زنده تکامل",
  inspector: "کاوش کروموزوم و تقویم",
  results: "نتایج و دریافت‌ها",
};
const statusNames = {
  Idle: "آماده",
  Running: "در حال اجرا",
  Stopping: "در انتظار پایان نسل جاری",
  Completed: "تکمیل‌شده",
  Stopped: "متوقف‌شده",
  Failed: "خطای اجرا",
};
const days = {
  Saturday: "شنبه",
  Sunday: "یکشنبه",
  Monday: "دوشنبه",
  Tuesday: "سه‌شنبه",
  Wednesday: "چهارشنبه",
  Thursday: "پنجشنبه",
  Friday: "جمعه",
};
const fmt = (x, d = 0) =>
  typeof x === "number"
    ? x.toLocaleString("fa-IR", { maximumFractionDigits: d })
    : esc(x);
function notice(message) {
  $("notice").hidden = !message;
  $("notice").textContent = message || "";
}
async function api(path, options = {}) {
  const r = await fetch(path, {
    ...options,
    headers: {
      "X-Local-Token": document.querySelector("meta[name=local-token]").content,
      ...options.headers,
    },
  });
  if (!r.ok) {
    let e = await r.json().catch(() => ({ detail: r.statusText }));
    throw Error(
      typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
    );
  }
  return r.json();
}
async function action(fn) {
  try {
    notice("");
    await fn();
  } catch (e) {
    notice(e.message);
  }
}
function page(name) {
  document.querySelectorAll(".page").forEach((e) => (e.hidden = e.id !== name));
  document
    .querySelectorAll("nav button")
    .forEach((e) => e.classList.toggle("active", e.dataset.page === name));
  $("pageTitle").textContent = titles[name];
  history.replaceState(null, "", "#" + name);
  Object.values(charts).forEach((c) => c.resize());
  window.scrollTo({top:0,behavior:'instant'});
}
document.querySelectorAll("[data-page],[data-go]").forEach(
  (b) =>
    (b.onclick = () => {
      const name = b.dataset.page || b.dataset.go;
      page(name);
      if (name === "inspector") action(generationUI);
    }),
);
$("theme").onclick = () => {
  document.body.classList.toggle("dark");
  localStorage.setItem(
    "theme",
    document.body.classList.contains("dark") ? "dark" : "light",
  );
};
if (localStorage.getItem("theme") === "dark")
  document.body.classList.add("dark");
function metric(label, value, sub = "") {
  return `<div class="metric"><span>${esc(label)}</span><strong>${value}</strong><small>${esc(sub)}</small></div>`;
}
function table(headers, data) {
  return `<table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${data.join("")}</tbody></table>`;
}
function sortable(container) {
  container.querySelectorAll("th").forEach(
    (th, i) =>
      (th.onclick = () => {
        const body = container.querySelector("tbody");
        const descending = th.dataset.desc !== "true";
        th.dataset.desc = descending;
        [...body.rows]
          .sort((a, b) => {
            const av = a.cells[i].dataset.sort ?? a.cells[i].textContent,
              bv = b.cells[i].dataset.sort ?? b.cells[i].textContent;
            return (
              (av !== "" && bv !== "" && !isNaN(av) && !isNaN(bv)
                ? Number(av) - Number(bv)
                : av.localeCompare(bv)) * (descending ? -1 : 1)
            );
          })
          .forEach((r) => body.append(r));
      }),
  );
}
function datasetUI() {
  const ds = state.datasets.find((d) => d.id === $("datasetSelect").value);
  if (!ds) return;
  $("datasetName").textContent = ds.name;
  $("datasetStatus").textContent = ds.valid
    ? "ورودی از نظر ساختاری معتبر"
    : "ورودی نامعتبر";
  $("datasetCards").innerHTML = Object.entries({
    students: "دانشجو",
    professors: "استاد",
    courses: "درس",
    rooms: "کلاس فیزیکی",
    requests: "درخواست انتخاب واحد",
  })
    .map(([k, v]) => metric(v, fmt(ds.counts[k])))
    .join("");
  $("validation").innerHTML =
    `<b class="${ds.valid ? "good" : "bad"}">${ds.valid ? "آمادهٔ اجرا" : "اجرای الگوریتم مجاز نیست"}</b><p>SHA-256: <code>${esc(ds.sha256)}</code></p><h4>خطاها</h4><pre>${esc(JSON.stringify(ds.errors, null, 2))}</pre><h4>هشدارهای ورودی</h4><pre>${esc(JSON.stringify(ds.warnings, null, 2))}</pre><details><summary>Diagnostics حل‌پذیری و منابع</summary><pre>${esc(JSON.stringify(ds.diagnostics || [], null, 2))}</pre></details>`;
  $("start").disabled = !ds.valid || !!state.active_run_id;
}
async function refreshState(chooseDataset) {
  state = await api("/api/state");
  let prev = chooseDataset || $("datasetSelect").value;
  $("datasetSelect").innerHTML = state.datasets
    .map(
      (d) =>
        `<option value="${d.id}">${esc(d.name)} · ${d.valid ? "معتبر" : "نامعتبر"}</option>`,
    )
    .join("");
  if (prev) $("datasetSelect").value = prev;
  datasetUI();
  $("runSelect").innerHTML =
    '<option value="">انتخاب آزمایش</option>' +
    state.runs
      .map(
        (r) =>
          `<option value="${r.id}">${esc(r.dataset_name)} · ${r.id.slice(0, 6)} · ${statusNames[r.status]}</option>`,
      )
      .join("");
  $("runSelect").value = runId;
  $("history").innerHTML = state.runs.length
    ? table(
        [
          "اجرا / داده",
          "وضعیت",
          "Seed",
          "نسل",
          "Hard",
          "Soft",
          "زمان / دلیل توقف",
        ],
        state.runs.map(
          (r) =>
            `<tr data-run="${r.id}"><td><button class="quiet">${r.id.slice(0, 8)} · ${esc(r.dataset_name)}</button><small>${esc(r.created)}</small></td><td>${statusNames[r.status]}</td><td>${fmt(r.settings.random_seed)}</td><td>${fmt(r.completed_generations)}</td><td>${fmt(r.best_overall?.hard_penalty)}</td><td>${fmt(r.best_overall?.soft_score, 5)}</td><td>${fmt(r.elapsed_seconds, 1)}s · ${esc(r.stop_reasons.join(", "))}</td></tr>`,
        ),
      )
    : '<p class="empty">هنوز آزمایشی اجرا نشده است.</p>';
  $("history")
    .querySelectorAll("[data-run]")
    .forEach((e) => (e.onclick = () => action(() => selectRun(e.dataset.run))));
  sortable($("history"));
}
$("datasetSelect").onchange = datasetUI;
for (const label of ["A", "B"])
  $("example" + label).onclick = () =>
    action(async () => {
      const ds = await api("/api/datasets/examples/" + label, {
        method: "POST",
      });
      await refreshState(ds.id);
    });
$("uploadButton").onclick = () =>
  action(async () => {
    const file = $("upload").files[0];
    if (!file) throw Error("فایل xlsx را انتخاب کنید.");
    const body = new FormData();
    body.append("file", file);
    const ds = await api("/api/datasets/upload", { method: "POST", body });
    await refreshState(ds.id);
  });
$("settings").onsubmit = (e) => {
  e.preventDefault();
  action(async () => {
    const settings = {};
    for (const el of e.target.elements) {
      if (!el.name) continue;
      settings[el.name] =
        el.type === "checkbox"
          ? el.checked
          : el.value === ""
            ? null
            : Number(el.value);
    }
    const result = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: $("datasetSelect").value, settings }),
    });
    await refreshState();
    await selectRun(result.id);
    page("monitor");
  });
};
$("stop").onclick = () =>
  action(async () => {
    if (runId) {
      run = await api("/api/runs/" + runId + "/stop", { method: "POST" });
      renderRun();
    }
  });
$("runSelect").onchange = () =>
  action(() =>
    $("runSelect").value ? selectRun($("runSelect").value) : Promise.resolve(),
  );
function initCharts() {
  if (!window.Chart) {
    notice(
      "کتابخانهٔ نمودار محلی بارگذاری نشد؛ جدول نسل‌ها و API همچنان دادهٔ واقعی دارند.",
    );
    return;
  }
  const specs = {
    bestHard: ["best_hard_penalty", "#087f8c"],
    meanHard: ["mean_hard_penalty", "#547ca3"],
    bestSoft: ["best_soft_score", "#2a9b80"],
    meanSoft: ["mean_soft_score", "#8a70a8"],
    diversity: ["unique_chromosomes", "#c19045"],
    cost: ["cumulative_fitness_evaluation_count", "#087f8c"],
  };
  for (const [id, [key, color]] of Object.entries(specs)) {
    charts[id]?.destroy();
    const datasets = [
      {
        label: key,
        data: [],
        borderColor: color,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0,
        parsing: false,
      },
    ];
    if (id === "cost")
      datasets.push({
        label: "Runtime seconds",
        data: [],
        borderColor: "#ca9554",
        yAxisID: "runtime",
        borderWidth: 2,
        pointRadius: 0,
        parsing: false,
      });
    charts[id] = new Chart($(id), {
      type: "line",
      data: { datasets },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        normalized: true,
        scales: {
          x: { type: "linear", ticks: { maxTicksLimit: 6 } },
          y: { beginAtZero: true },
          ...(id === "cost"
            ? {
                runtime: {
                  type: "linear",
                  position: "right",
                  grid: { drawOnChartArea: false },
                },
              }
            : {}),
        },
        plugins: { legend: { display: id === "cost" } },
      },
    });
    charts[id].metricKey = key;
  }
}
function appendCharts(incoming) {
  for (const [id, c] of Object.entries(charts)) {
    c.data.datasets[0].data.push(
      ...incoming.map((r) => ({ x: r.generation_index, y: r[c.metricKey] })),
    );
    if (id === "cost")
      c.data.datasets[1].data.push(
        ...incoming.map((r) => ({
          x: r.generation_index,
          y: r.cumulative_elapsed_seconds,
        })),
      );
    c.update("none");
  }
}
async function selectRun(id, navigate = true) {
  inspectionSerial++;
  runEpoch++;
  busy = false;
  runId = id;
  run = state.runs.find((r) => r.id === id) || null;
  cursor = -1;
  rows = [];
  selected = null;
  entities = {};
  $("population").innerHTML = "";
  $("snapshotMetrics").innerHTML = "";
  $("calendar").textContent = "یک کروموزوم انتخاب کنید.";
  initCharts();
  $("runSelect").value = id;
  await poll(true);
  entities = await api(`/api/runs/${id}/entities`);
  entityOptions();
  if (navigate) page("monitor");
}
function bestCard(label, best, selection) {
  return `<article class="panel"><span class="eyebrow">${esc(label)}</span>${best ? `<h2 class="${best.feasible ? "good" : "bad"}">Hard ${fmt(best.hard_penalty)}</h2><p>Soft ${fmt(best.soft_score, 6)} · پوشش ${fmt(best.request_coverage * 100, 2)}٪</p><small>${best.feasible ? "معتبر" : "نامعتبر · دارای نقض سخت"}</small><p><code>${esc(best.fingerprint.slice(0, 16))}</code></p><button class="quiet" data-inspect="${selection}">بررسی این جواب ←</button>` : '<p class="empty">هنوز جواب معتبری وجود ندارد.</p>'}</article>`;
}
function bestButtons(container) {
  container.querySelectorAll("[data-inspect]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          await inspect(b.dataset.inspect);
          page("inspector");
        })),
  );
}
function renderRun() {
  if (!run) return;
  const last = rows.at(-1);
  $("status").textContent = statusNames[run.status] || run.status;
  const runOption = [...$("runSelect").options].find((o) => o.value === run.id);
  if (runOption)
    runOption.textContent =
      run.dataset_name +
      " · " +
      run.id.slice(0, 6) +
      " · " +
      statusNames[run.status];
  $("status").classList.toggle("invalid", run.status === "Failed");
  $("stop").disabled = !["Running", "Stopping"].includes(run.status);
  $("start").disabled =
    ["Running", "Stopping"].includes(run.status) ||
    !state.datasets.find((d) => d.id === $("datasetSelect").value)?.valid;
  $("stop").textContent =
    run.status === "Stopping"
      ? "پایان نسل جاری و ذخیرهٔ نتیجه…"
      : "درخواست توقف ایمن";
  $("generationProgress").style.width = last
    ? `${run.settings.max_generations ? Math.min(100, (last.generation_index / run.settings.max_generations) * 100) : 100}%`
    : "0%";
  $("liveMetrics").innerHTML = last
    ? [
        metric(
          "نسل کامل / حداکثر",
          fmt(last.generation_index),
          "/ " + run.settings.max_generations,
        ),
        metric("بهترین Hard جمعیت", fmt(last.best_hard_penalty)),
        metric("Soft فرد برتر", fmt(last.best_soft_score, 5)),
        metric("جمعیت معتبر", fmt(last.feasible_count)),
        metric(
          "کروموزوم یکتا",
          fmt(last.unique_chromosomes),
          "/ " + last.population_size,
        ),
        metric("ارزیابی تجمعی", fmt(last.cumulative_fitness_evaluation_count)),
        metric("زمان تجمعی (ثانیه)", fmt(last.cumulative_elapsed_seconds, 1)),
        metric("نسل‌های رکود", fmt(run.stagnant_generations)),
      ].join("")
    : '<div class="empty">در حال ساخت جمعیت اولیه؛ نسل صفر هنوز کامل نشده است.</div>';
  const cards =
    bestCard("بهترین جمعیت کنونی", run.current_best, "best") +
    bestCard("بهترین مشاهده‌شدهٔ کل اجرا", run.best_overall, "best_overall") +
    bestCard("بهترین جواب معتبر", run.best_feasible, "best_feasible");
  $("bests").innerHTML = cards;
  $("resultBests").innerHTML = cards;
  bestButtons($("bests"));
  bestButtons($("resultBests"));
  $("dashboardRun").innerHTML = last
    ? `<h2>نسل ${fmt(last.generation_index)} · Hard ${fmt(last.best_hard_penalty)}</h2><p>${statusNames[run.status]} · ${fmt(last.cumulative_fitness_evaluation_count)} ارزیابی واقعی</p>`
    : "در انتظار پایان ساخت جمعیت اولیه";
  $("resultSummary").innerHTML =
    `<p>وضعیت: <b>${statusNames[run.status]}</b> · دلیل توقف: <code>${esc(run.stop_reasons.join(", ") || "—")}</code></p><p>نسل کامل: ${fmt(run.completed_generations)} · ارزیابی کل: ${fmt(run.fitness_calls ?? last?.cumulative_fitness_evaluation_count)} · زمان: ${fmt(run.elapsed_seconds ?? last?.cumulative_elapsed_seconds, 1)} ثانیه</p><p class="bad">${esc(run.error || (!run.best_feasible ? "هنوز جواب معتبر Hard=0 پیدا نشده است." : ""))}</p>`;
  $("resultConfig").textContent = JSON.stringify(
    {
      dataset: run.dataset_name,
      sha256: run.dataset_sha256,
      context_id: run.context_id,
      settings: run.settings,
    },
    null,
    2,
  );
  $("reportDownload").href = `/api/runs/${runId}/download/report`;
  for (const id of ["generationSelect", "operatorGeneration"]) {
    const old = $(id).value;
    $(id).innerHTML = rows
      .map(
        (r) =>
          `<option value="${r.generation_index}">نسل ${fmt(r.generation_index)}</option>`,
      )
      .join("");
    if (old !== "" && rows.some((r) => String(r.generation_index) === old))
      $(id).value = old;
    else if (id === "operatorGeneration" && last)
      $(id).value = last.generation_index;
  }
  renderOperators();
}
async function poll(force = false) {
  if (!runId || (busy && !force)) return;
  if (!force && run && ['Completed','Stopped','Failed'].includes(run.status) && cursor >= run.completed_generations) return;
  busy = true;
  const id = runId,
    epoch = runEpoch, previousStatus = run?.status;
  try {
    let more = true;
    while (more) {
      const data = await api(
        `/api/runs/${id}/events?after=${cursor}&limit=100`,
      );
      if (id !== runId || epoch !== runEpoch) return;
      run = data.run;
      rows.push(...data.events);
      cursor = data.next;
      appendCharts(data.events);
      more = data.events.length === 100;
    }
    renderRun();
    if (previousStatus !== run.status && ['Completed','Stopped','Failed'].includes(run.status)) await refreshState();
  } finally {
    if (epoch === runEpoch) busy = false;
  }
}
function renderOperators() {
  const r = rows.find(
    (r) => r.generation_index === Number($("operatorGeneration").value),
  );
  if (!r) return;
  const o = r.operators,
    s = run.settings;
  const groups = [
    [
      "Elitism",
      {
        "Elite تنظیم‌شده": s.elite_count,
        "حفظ بهترین عضو": r.best_preserved ? "بله" : "خیر",
        "Eliteهای واقعی": r.elites,
      },
    ],
    [
      "Tournament Selection",
      {
        اندازه: s.tournament_size,
        "Self-mating": o.self_matings,
        "نرخ Self-mating": r.self_mating_rate,
      },
    ],
    [
      "Course-Based Crossover",
      {
        احتمال: s.crossover_probability,
        اعمال‌شده: o.crossover_applied,
        "کپی والد": o.parent_cloned,
      },
    ],
    [
      "Mutation",
      {
        اعمال‌شده: o.mutation_applied,
        "رد دروازه": o.mutation_skipped,
        ناموفق: o.mutation_failed,
        غیرقابل‌اعمال: o.mutation_not_applicable,
        عملیات: o.mutation_operations,
      },
    ],
    [
      "Repair",
      {
        بهبودیافته: o.repair_improved,
        "بدون تغییر": o.repair_unchanged,
        غیرفعال: o.repair_disabled,
        "ارزیابی کاندیدا": r.candidate_repair_evaluation_count,
        "حدود واقعی": s.repair_settings,
      },
    ],
    [
      "کیفیت فرزندان",
      {
        "بهتر از هر دو": o.better_than_both_parents,
        "بدتر از هر دو": o.worse_than_both_parents,
        "میان والدها / مساوی": o.between_parents,
        "یکسان با والد": o.identical_to_a_parent,
      },
    ],
  ];
  $("operators").innerHTML =
    '<div class="operator-grid">' +
    groups
      .map(
        ([name, v]) =>
          `<dl><dt>${esc(name)}</dt>${Object.entries(v)
            .map(
              ([k, x]) =>
                `<dd>${esc(k)}: ${typeof x === "object" ? `<pre>${esc(JSON.stringify(x, null, 2))}</pre>` : esc(x)}</dd>`,
            )
            .join("")}</dl>`,
      )
      .join("") +
    "</div>";
}
$("operatorGeneration").onchange = renderOperators;
async function generationUI() {
  if (!runId || !rows.length) return;
  const id = runId,
    g = Number($("generationSelect").value);
  const data = await api(`/api/runs/${id}/generations/${g}`);
  if (id !== runId) return;
  $("retentionNotice").textContent = data.full_population_available
    ? "جمعیت کامل این نسل قابل بررسی است؛ ستون‌ها قابل مرتب‌سازی هستند."
    : "جمعیت کامل این نسل طبق سیاست نگهداری حذف شده؛ فقط بهترین کروموزوم همان نسل موجود است.";
  $("persistGeneration").disabled = !data.full_population_available;
  $("population").innerHTML = table(
    [
      "شماره",
      "Fingerprint",
      "Hard",
      "Soft",
      "اعتبار",
      "پوشش",
      "تخصیص",
      "گروه فعال",
      "Elite",
    ],
    data.chromosomes.map(
      (c) =>
        `<tr data-index="${data.full_population_available ? c.index : "best"}"><td>${fmt(c.index)}</td><td><button class="quiet">${esc(c.fingerprint.slice(0, 14))}</button></td><td data-sort="${c.hard_penalty}">${fmt(c.hard_penalty)}</td><td data-sort="${c.soft_score}">${fmt(c.soft_score, 6)}</td><td class="${c.feasible ? "good" : "bad"}">${c.feasible ? "معتبر" : "نامعتبر"}</td><td data-sort="${c.request_coverage}">${fmt(c.request_coverage * 100, 2)}٪</td><td>${fmt(c.assigned)}</td><td>${fmt(c.active_sections)}</td><td>${c.elite ? "بله" : "—"}</td></tr>`,
    ),
  );
  $("population")
    .querySelectorAll("[data-index]")
    .forEach((e) => (e.onclick = () => action(() => inspect(e.dataset.index))));
  sortable($("population"));
}
$("generationSelect").onchange = () => action(generationUI);
$("persistGeneration").onclick = () =>
  action(async () => {
    await api(
      `/api/runs/${runId}/generations/${$("generationSelect").value}/persist`,
      { method: "POST" },
    );
    notice("جمعیت کامل این نسل روی دیسک حفظ شد.");
  });
async function inspect(selection) {
  if (!runId) return;
  const serial = ++inspectionSerial,
    id = runId;
  let generation = Number(
    $("generationSelect").value || rows.at(-1)?.generation_index || 0,
  );
  if (
    selection === "best" &&
    document.querySelector("#monitor:not([hidden]),#results:not([hidden])")
  )
    generation = rows.at(-1)?.generation_index || 0;
  const query = new URLSearchParams({ generation, selection });
  const data = await api(`/api/runs/${id}/snapshot?${query}`);
  if (serial !== inspectionSerial || id !== runId) return;
  if (['best_overall','best_feasible','final_population_best'].includes(selection)) {
    generation = data.snapshot.observed_generation ?? generation;
    query.set('generation',generation);
    if ([...$('generationSelect').options].some(o=>Number(o.value)===generation)) {
      $('generationSelect').value=generation;
      await generationUI();
    }
  }
  if (serial !== inspectionSerial || id !== runId) return;
  selected = {
    id,
    generation,
    selection,
    query: query.toString(),
    snapshot: data.snapshot,
  };
  $("fingerprint").textContent = data.summary.fingerprint;
  $("selectedGeneration").textContent =
    "کروموزوم منتخب · نسل " + (data.snapshot.observed_generation ?? generation);
  const s = data.summary;
  $("snapshotMetrics").innerHTML = [
    metric("HardPenalty", fmt(s.hard_penalty)),
    metric("SoftScore", fmt(s.soft_score, 6)),
    metric("پوشش درخواست‌ها", fmt(s.request_coverage * 100, 2) + "٪"),
    metric("تخصیص / بی‌پاسخ", fmt(s.assigned), "/ " + s.unassigned),
    metric(
      "گروه فعال / غیرفعال",
      fmt(s.active_sections),
      "/ " + s.inactive_sections,
    ),
  ].join("");
  $("violations").innerHTML =
    "<h3>نقض محدودیت‌های سخت</h3>" +
    Object.entries(data.snapshot.hard_evaluation.counts_by_type)
      .map(
        ([type, count]) =>
          `<details><summary class="${count ? "bad" : "good"}">${esc(type)} · مجموع شدت ${fmt(count)}</summary><pre>${esc(
            JSON.stringify(
              data.snapshot.hard_evaluation.violations.filter(
                (v) => v.constraint_type === type,
              ),
              null,
              2,
            ),
          )}</pre></details>`,
      )
      .join("");
  $("exportWarning").textContent = s.feasible
    ? "برنامهٔ منتخب از نظر محدودیت‌های سخت معتبر است."
    : "برنامه پیشنهادی نامعتبر — دارای نقض محدودیت‌های سخت";
  $("exportWarning").className = s.feasible ? "good" : "bad";
  $("downloadXlsx").href = `/api/runs/${id}/download/xlsx?${query}`;
  $("downloadJson").href = `/api/runs/${id}/download/json?${query}`;
  await timetable();
}
function entityOptions() {
  const role = $("role").value,
    term = $("entitySearch").value.toLowerCase(),
    prev = $("entity").value;
  $("entity").innerHTML = (entities[role] || [])
    .filter((e) => (e.id + " " + e.name).toLowerCase().includes(term))
    .map(
      (e) =>
        `<option value="${esc(e.id)}">${esc(e.id + " · " + e.name)}</option>`,
    )
    .join("");
  if ([...$("entity").options].some((o) => o.value === prev))
    $("entity").value = prev;
}
$("role").onchange = () =>
  action(async () => {
    entityOptions();
    await timetable();
  });
$("entitySearch").oninput = () => {
  entityOptions();
  action(timetable);
};
$("entity").onchange = () => action(timetable);
async function timetable() {
  if (!selected || !$("entity").value) return;
  const capture = selected,
    entity = $("entity").value,
    role = $("role").value;
  const data = await api(
    `/api/runs/${capture.id}/timetable?${capture.query}&role=${role}&entity_id=${encodeURIComponent(entity)}`,
  );
  if (
    selected !== capture ||
    $("entity").value !== entity ||
    $("role").value !== role
  )
    return;
  $("entityDetails").innerHTML =
    '<div class="entity-info">' +
    Object.entries(data.detail)
      .filter(([key]) => key !== "requests")
      .map(
        ([k, v]) =>
          `<span>${esc({ credits: "واحد دریافتی", min_credits: "حداقل واحد", max_credits: "حداکثر واحد", unassigned: "درخواست بی‌پاسخ", teaching_minutes: "دقیقه تدریس", teaching_hours: "ساعت تدریس", max_hours: "حداکثر ساعت", capacity: "ظرفیت", room_type: "نوع فضا", equipment: "تجهیزات", requests: "درخواست‌ها" }[k] || k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`,
      )
      .join("") +
    "</div>";
  if (data.detail.requests) {
    $("entityDetails").innerHTML += '<div class="table-wrap">' + table(
      ['درس','نام درس','گروه','واحد','وضعیت'],
      data.detail.requests.map(r => `<tr><td>${esc(r.course_id)}</td><td>${esc(r.course_name)}</td><td>${esc(r.section_id)}</td><td>${fmt(r.credits)}</td><td>${r.section_id ? 'تخصیص یافته' : 'بی‌پاسخ'}</td></tr>`)) + '</div>';
  }
  const events = data.events;
  let start = events.length
      ? Math.floor(Math.min(...events.map((e) => e.start_minute)) / 60) * 60
      : 480,
    end = events.length
      ? Math.ceil(Math.max(...events.map((e) => e.end_minute)) / 60) * 60
      : 1080;
  if (end <= start) end = start + 60;
  const height = end - start;
  $("calendar").classList.remove("empty");
  $("calendar").innerHTML = data.days
    .map((day) => {
      const indices = events
        .map((e, i) => i)
        .filter((i) => events[i].day === day)
        .sort(
          (a, b) => events[a].start_minute - events[b].start_minute || a - b,
        );
      let groups = [],
        group = [],
        until = -1;
      for (const i of indices) {
        if (events[i].start_minute >= until && group.length) {
          groups.push(group);
          group = [];
          until = -1;
        }
        group.push(i);
        until = Math.max(until, events[i].end_minute);
      }
      if (group.length) groups.push(group);
      const layout = {};
      for (const list of groups) {
        let lanes = [];
        for (const i of list) {
          let lane = lanes.findIndex((end) => end <= events[i].start_minute);
          if (lane < 0) lane = lanes.length;
          lanes[lane] = events[i].end_minute;
          layout[i] = { lane, count: 0 };
        }
        for (const i of list) layout[i].count = lanes.length;
      }
      return `<div class="day-column"><div class="day-title">${esc(days[day] || day)}</div><div class="day-body" style="height:${height}px">${Array.from({ length: Math.ceil(height / 60) }, (_, h) => `<span class="time-label" style="top:${h * 60}px">${String(Math.floor((start + h * 60) / 60)).padStart(2, "0")}:00</span>`).join("")}${indices
        .map((i) => {
          const e = events[i],
            l = layout[i];
          return `<button class="calendar-event ${e.violations.length || e.overlaps.length ? "conflict" : ""}" data-event="${i}" style="top:${e.start_minute - start}px;height:${e.end_minute - e.start_minute}px;right:${(l.lane / l.count) * 100}%;width:${100 / l.count}%"><b>${esc(e.course_name)}</b><br><span dir="ltr">${e.start}–${e.end}</span><br>${esc(e.section_id)}<br>${esc(e.professor_name)} · ${esc(e.room_id)}</button>`;
        })
        .join("")}</div></div>`;
    })
    .join("");
  $("eventDetails").hidden = true;
  $("calendar")
    .querySelectorAll("[data-event]")
    .forEach(
      (b) =>
        (b.onclick = () => {
          $("eventDetails").hidden = false;
          $("eventDetails").innerHTML =
            "<h3>جزئیات جلسه و نقض‌های مرتبط</h3><pre>" +
            esc(JSON.stringify(events[Number(b.dataset.event)], null, 2)) +
            "</pre>";
        }),
    );
}
async function boot() {
  const initialPage = location.hash.slice(1);
  initCharts();
  await refreshState();
  const id = state.active_run_id || state.runs[0]?.id;
  if (id) {
    await selectRun(id, false);
    await generationUI();
  }
  page(titles[initialPage] ? initialPage : "dashboard");
  setInterval(
    () =>
      action(async () => {
        const old = rows.length;
        await poll();
        if (rows.length !== old && $("generationSelect").options.length === 1)
          await generationUI();
      }),
    1500,
  );
}
action(boot);
