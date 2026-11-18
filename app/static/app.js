"use strict";
const $ = (id) => document.getElementById(id);
const state = { origin: null, destination: null, receiptBase: null };
const GRADE_TEXT = {
  NORMAL: "Within the normal range",
  CHECK_NEEDED: "Needs checking",
  CLEAR_EXCESS: "Clearly above the normal range",
  UNSUPPORTED: "Cannot be checked automatically",
};
const won = (n) => (n || n === 0 ? n.toLocaleString("en-US") + " KRW" : "–");

fetch("/api/health").then((r) => r.json()).then((h) => { if (h.demo) $("demo-banner").hidden = false; }).catch(() => {});

function show(id) { $(id).hidden = false; }
function status(id, text, isErr) {
  const el = $(id); el.textContent = text; el.hidden = !text; el.classList.toggle("err", !!isErr);
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body.error && body.error.message) || res.statusText);
  return body;
}

// ---------- 1. 업로드 ----------
$("image").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  $("upload-label").textContent = file.name;
  status("upload-status", "Reading your receipt…");
  const fd = new FormData(); fd.append("image", file);
  try {
    const data = await api("/api/extract", { method: "POST", body: fd });
    fillReceipt(data.receipt, data.low_confidence_fields);
    status("upload-status", "Done. Please check the fields below.");
  } catch (e) {
    status("upload-status", "Could not read the receipt (" + e.message + "). You can enter it manually.", true);
    fillReceipt({}, []);
  }
});
$("skip-upload").addEventListener("click", () => fillReceipt({ taxi_type: "medium" }, []));

function toLocalInput(iso) {
  if (!iso) return "";
  // 서버는 +09:00 ISO를 준다. 입력칸에는 KST 벽시계 시각을 그대로 보여준다.
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? m[1] + "T" + m[2] : "";
}

function fillReceipt(r, low) {
  state.receiptBase = r;
  const form = $("step-confirm");
  for (const label of form.querySelectorAll("label[data-f]")) {
    const f = label.dataset.f;
    const el = label.querySelector("input,select");
    let v = r[f];
    if (f === "ride_start" || f === "ride_end") v = toLocalInput(v);
    else if (typeof v === "boolean") v = String(v);
    el.value = v === null || v === undefined ? (f === "taxi_type" ? "unknown" : "") : v;
    label.classList.toggle("low", low.includes(f) || (v === "" && ["total_fare", "ride_start", "ride_end"].includes(f)));
    el.addEventListener("input", () => label.classList.remove("low"), { once: true });
  }
  show("step-confirm"); show("step-trip");
  updateJudgeBtn();
}

function readReceipt() {
  const q = (n) => $("step-confirm").querySelector(`[name="${n}"]`).value;
  const int = (n) => (q(n) === "" ? null : parseInt(q(n), 10));
  const bool = (n) => (q(n) === "" ? null : q(n) === "true");
  const dt = (n) => (q(n) === "" ? null : q(n) + ":00+09:00");
  return {
    total_fare: int("total_fare"), meter_fare: int("meter_fare"), toll_fee: int("toll_fee"),
    ride_start: dt("ride_start"), ride_end: dt("ride_end"),
    night_surcharge: bool("night_surcharge"), out_of_city_surcharge: bool("out_of_city_surcharge"),
    distance_km: (state.receiptBase && state.receiptBase.distance_km) || null,
    vehicle_no: q("vehicle_no") || null, taxi_type: q("taxi_type") || "unknown",
    confidence: {},
  };
}

// ---------- 3. 장소 검색 ----------
function placeSearch(prefix, key) {
  const input = $(prefix + "-q"), list = $(prefix + "-results"), chosen = $(prefix + "-chosen");
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    state[key] = null; chosen.hidden = true; updateJudgeBtn();
    const q = input.value.trim();
    if (q.length < 2) { list.innerHTML = ""; return; }
    timer = setTimeout(async () => {
      try {
        const { places } = await api("/api/places?q=" + encodeURIComponent(q));
        list.innerHTML = "";
        if (!places.length) list.innerHTML = "<li>No results</li>";
        for (const p of places) {
          const li = document.createElement("li");
          li.innerHTML = "<span></span><small></small>";
          li.firstChild.textContent = p.name;
          li.lastChild.textContent = p.address;
          li.addEventListener("click", () => {
            state[key] = p; list.innerHTML = ""; input.value = p.name;
            chosen.textContent = "✓ " + p.name + (p.address ? " · " + p.address : "");
            chosen.hidden = false; updateJudgeBtn();
          });
          list.appendChild(li);
        }
      } catch (e) {
        list.innerHTML = ""; const li = document.createElement("li");
        li.textContent = "Search failed: " + e.message; list.appendChild(li);
      }
    }, 300);
  });
}
placeSearch("origin", "origin");
placeSearch("dest", "destination");

function updateJudgeBtn() { $("judge-btn").disabled = !(state.origin && state.destination && state.receiptBase); }

// ---------- 4. 판정 ----------
$("judge-btn").addEventListener("click", async () => {
  status("judge-status", "Checking the route and fare…");
  $("judge-btn").disabled = true;
  try {
    const data = await api("/api/judge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        receipt: readReceipt(), origin: state.origin, destination: state.destination,
        meter_used: !$("meter-not-used").checked, hailed_via_app: $("via-app").checked,
        user_lang: $("lang").value,
      }),
    });
    renderResult(data);
    status("judge-status", "");
  } catch (e) {
    status("judge-status", "Check failed: " + e.message, true);
  } finally { updateJudgeBtn(); }
});

function renderResult(d) {
  const v = d.verdict;
  const g = $("grade"); g.className = "grade " + v.grade; g.textContent = GRADE_TEXT[v.grade];
  $("r-charged").textContent = won(v.charged_won);
  $("r-upper").textContent = v.upper_bound_won ? won(v.upper_bound_won) : "–";
  $("r-ratio").textContent = v.upper_bound_won ? v.ratio + "×" : "–";
  $("explanation").textContent = d.explanation.text;
  $("report").textContent = d.report_ko;
  const ch = d.report_channels || [];
  $("channels").hidden = !ch.length;
  $("channel-list").innerHTML = "";
  for (const c of ch) {
    const li = document.createElement("li"); const a = document.createElement("a");
    a.href = c.url; a.textContent = c.label; a.target = "_blank"; a.rel = "noopener";
    li.appendChild(a); $("channel-list").appendChild(li);
  }
  show("step-result");
  $("step-result").scrollIntoView({ behavior: "smooth" });
}

$("copy-btn").addEventListener("click", async () => {
  const text = $("report").textContent;
  try { await navigator.clipboard.writeText(text); status("copy-status", "Copied."); }
  catch {
    const r = document.createRange(); r.selectNodeContents($("report"));
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
    status("copy-status", "Text selected — use your phone's Copy.");
  }
});
$("restart").addEventListener("click", () => location.reload());
