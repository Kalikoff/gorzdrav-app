/* ── Telegram Web App ────────────────────────────────────────────────────── */
const tg = window.Telegram?.WebApp ?? {
  ready: () => {},
  expand: () => {},
  initData: "",
  showAlert: (m) => alert(m),
  showConfirm: (m, cb) => cb(confirm(m)),
};
tg.ready();
tg.expand();

/* Без Telegram (локальная отладка с SKIP_AUTH=true) подставляем тестового юзера */
const INIT_DATA = tg.initData || "user=%7B%22id%22%3A1%7D";

/* ── API ─────────────────────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": INIT_DATA,
    },
    ...opts,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : `Ошибка ${res.status}`);
  }
  return res.json();
}

/* ── Утилиты ─────────────────────────────────────────────────────────────── */
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

function formatDay(iso) {
  const day = new Date(`${iso}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const diff = Math.round((day - today) / 86400000);
  if (diff === 0) return "сегодня";
  if (diff === 1) return "завтра";

  const label = day.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  return `${WEEKDAYS[day.getDay()]}, ${label}`;
}

function formatNearest(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const day = date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return `${day} в ${time}`;
}

function describeWindow(from, to) {
  if (from === "00:00" && to === "23:59") return "любое время";
  if (from === "00:00") return `до ${to}`;
  if (to === "23:59") return `с ${from}`;
  return `с ${from} до ${to}`;
}

function toast(message) {
  document.querySelector(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

function emptyState(icon, text) {
  return `<div class="empty"><div class="empty-icon">${icon}</div>
          <div class="empty-text">${esc(text)}</div></div>`;
}

/* ── Состояние ───────────────────────────────────────────────────────────── */
const PRESETS = [
  { label: "Любое", from: "00:00", to: "23:59" },
  { label: "До 12:00", from: "00:00", to: "12:00" },
  { label: "После 14:00", from: "14:00", to: "23:59" },
  { label: "После 17:00", from: "17:00", to: "23:59" },
];

let view = "subs";
let step = 0;
let subs = [];
let draft = emptyDraft();

function emptyDraft() {
  return {
    district_id: null, district_name: null,
    lpu_id: null, lpu_name: null,
    speciality_id: null, speciality_name: null,
    doctor_id: null, doctor_name: null,
    time_from: "00:00", time_to: "23:59",
  };
}

/* ── Запуск ──────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("#main-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  tg.BackButton?.onClick?.(goBack);
  document.getElementById("ref-find").addEventListener("click", lookupReferral);
  document.getElementById("ref-lastname").addEventListener("keydown", (e) => {
    if (e.key === "Enter") lookupReferral();
  });
  loadSubscriptions();
});

function switchView(next) {
  view = next;
  document.querySelectorAll("#main-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === next);
  });
  document.getElementById("view-subs").classList.toggle("hidden", next !== "subs");
  document.getElementById("view-new").classList.toggle("hidden", next !== "new");
  document.getElementById("view-referral").classList.toggle("hidden", next !== "referral");

  if (next === "new") goStep(step);
  else syncBackButton();

  if (next === "referral") {
    loadWatchedReferrals();
    loadBookings();
  }
}

function syncBackButton() {
  const back = tg.BackButton;
  if (!back?.show) return;
  if (view === "new" && step > 0) back.show();
  else back.hide();
}

function goBack() {
  // На специальность можно попасть из избранного, минуя выбор района.
  if (step === 2 && !draft.district_id) goStep(0);
  else goStep(Math.max(0, step - 1));
}

/* ── Мастер создания подписки ────────────────────────────────────────────── */
const STEP_RENDERERS = [stepDistrict, stepLpu, stepSpeciality, stepDoctor, stepFilter];

async function goStep(index) {
  step = index;
  renderCrumbs();
  syncBackButton();

  try {
    await STEP_RENDERERS[index]();
  } catch (error) {
    document.getElementById("wizard").innerHTML =
      `<div class="card">${emptyState("⚠️", error.message)}</div>`;
  }
}

function renderCrumbs() {
  const box = document.getElementById("crumbs");
  const crumbs = [];

  if (draft.district_name) crumbs.push({ label: draft.district_name, target: 0 });
  if (draft.lpu_name) crumbs.push({ label: draft.lpu_name, target: draft.district_id ? 1 : 0 });
  if (draft.speciality_name) crumbs.push({ label: draft.speciality_name, target: 2 });
  if (step >= 4) crumbs.push({ label: draft.doctor_name || "Любой врач", target: 3 });

  box.innerHTML = "";
  crumbs.forEach((crumb, index) => {
    const el = document.createElement("button");
    el.className = "crumb" + (index === crumbs.length - 1 ? " current" : "");
    el.type = "button";
    el.textContent = crumb.label;
    el.addEventListener("click", () => goStep(crumb.target));
    box.appendChild(el);
  });
}

function showWizardLoading() {
  document.getElementById("wizard").innerHTML = `<div class="card"><div class="loading">Загрузка…</div></div>`;
}

function pickRow(item) {
  const row = document.createElement("button");
  row.className = "pick";
  row.type = "button";

  const badge = item.badge
    ? `<span class="pick-badge${item.badgeMuted ? " muted" : ""}">${esc(item.badge)}</span>`
    : "";

  row.innerHTML = `
    <div class="pick-body">
      <div class="pick-title">${esc(item.title)}</div>
      ${item.sub ? `<div class="pick-sub">${esc(item.sub)}</div>` : ""}
    </div>
    ${badge}
  `;

  if (item.onStar) {
    const star = document.createElement("span");
    star.className = "pick-star";
    star.textContent = item.starred ? "⭐" : "☆";
    star.addEventListener("click", async (event) => {
      event.stopPropagation();
      const next = !item.starred;
      try {
        await item.onStar(next);
        item.starred = next;
        star.textContent = next ? "⭐" : "☆";
      } catch (error) {
        tg.showAlert(error.message);
      }
    });
    row.appendChild(star);
  }

  row.addEventListener("click", () => item.onPick());
  return row;
}

function renderPicker(config) {
  const wizard = document.getElementById("wizard");
  wizard.innerHTML = "";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-header">
      <div>
        <div class="card-title">${esc(config.title)}</div>
        ${config.subtitle ? `<div class="card-subtitle">${esc(config.subtitle)}</div>` : ""}
      </div>
    </div>
  `;

  const search = document.createElement("input");
  search.className = "search";
  search.type = "search";
  search.placeholder = config.searchPlaceholder || "Поиск…";
  card.appendChild(search);

  const body = document.createElement("div");
  card.appendChild(body);
  wizard.appendChild(card);

  const draw = () => {
    const query = search.value.trim().toLowerCase();
    body.innerHTML = "";
    let total = 0;

    config.groups.forEach((group) => {
      const items = group.items.filter(
        (item) => !query || `${item.title} ${item.sub || ""}`.toLowerCase().includes(query)
      );
      if (!items.length) return;
      total += items.length;

      if (group.label) {
        const label = document.createElement("div");
        label.className = "section-label";
        label.textContent = group.label;
        body.appendChild(label);
      }

      const list = document.createElement("div");
      list.className = "picker";
      items.forEach((item) => list.appendChild(pickRow(item)));
      body.appendChild(list);
    });

    if (!total) {
      body.innerHTML = emptyState("🔍", config.emptyText || "Ничего не найдено");
    }
  };

  search.addEventListener("input", draw);
  draw();
}

/* Шаг 1 — район (и быстрый вход через избранное) */
async function stepDistrict() {
  showWizardLoading();
  const [districts, favorites] = await Promise.all([api("/districts"), api("/favorites")]);

  const groups = [];

  if (favorites.length) {
    groups.push({
      label: "Избранные поликлиники",
      items: favorites.map((favorite) => ({
        title: favorite.name,
        starred: true,
        onStar: async () => {
          await api(`/favorites/${encodeURIComponent(favorite.id)}`, { method: "DELETE" });
          toast("Убрано из избранного");
          goStep(0);
        },
        onPick: () => {
          draft.district_id = null;
          draft.district_name = null;
          draft.lpu_id = favorite.id;
          draft.lpu_name = favorite.name;
          goStep(2);
        },
      })),
    });
  }

  groups.push({
    label: favorites.length ? "Все районы" : null,
    items: districts.map((district) => ({
      title: district.name,
      onPick: () => {
        draft.district_id = district.id;
        draft.district_name = district.name;
        draft.lpu_id = null;
        draft.lpu_name = null;
        goStep(1);
      },
    })),
  });

  renderPicker({
    title: "Район",
    subtitle: "Шаг 1 из 5",
    searchPlaceholder: "Поиск района",
    groups,
  });
}

/* Шаг 2 — поликлиника */
async function stepLpu() {
  showWizardLoading();
  const [lpus, favorites] = await Promise.all([
    api(`/districts/${encodeURIComponent(draft.district_id)}/lpus`),
    api("/favorites"),
  ]);
  const favoriteIds = new Set(favorites.map((favorite) => favorite.id));

  renderPicker({
    title: "Поликлиника",
    subtitle: `Шаг 2 из 5 · ${draft.district_name}`,
    searchPlaceholder: "Название или адрес",
    emptyText: "В этом районе ничего не нашлось",
    groups: [{
      items: lpus.map((lpu) => ({
        title: lpu.name,
        sub: lpu.address,
        starred: favoriteIds.has(lpu.id),
        onStar: async (add) => {
          if (add) {
            await api("/favorites", {
              method: "POST",
              body: JSON.stringify({ lpu_id: lpu.id, lpu_name: lpu.name }),
            });
          } else {
            await api(`/favorites/${encodeURIComponent(lpu.id)}`, { method: "DELETE" });
          }
        },
        onPick: () => {
          draft.lpu_id = lpu.id;
          draft.lpu_name = lpu.name;
          draft.speciality_id = null;
          draft.speciality_name = null;
          goStep(2);
        },
      })),
    }],
  });
}

/* Шаг 3 — специальность */
async function stepSpeciality() {
  showWizardLoading();
  const specialities = await api(`/lpus/${encodeURIComponent(draft.lpu_id)}/specialities`);

  renderPicker({
    title: "Специальность",
    subtitle: `Шаг 3 из 5 · ${draft.lpu_name}`,
    searchPlaceholder: "Поиск специальности",
    emptyText: "У этой поликлиники нет расписания",
    groups: [{
      items: specialities.map((speciality) => ({
        title: speciality.name,
        sub: speciality.nearest_date ? `Ближайший приём: ${formatNearest(speciality.nearest_date)}` : "",
        badge: speciality.free_tickets ? `${speciality.free_tickets} 🎫` : "нет",
        badgeMuted: !speciality.free_tickets,
        onPick: () => {
          draft.speciality_id = speciality.id;
          draft.speciality_name = speciality.name;
          draft.doctor_id = null;
          draft.doctor_name = null;
          goStep(3);
        },
      })),
    }],
  });
}

/* Шаг 4 — врач */
async function stepDoctor() {
  showWizardLoading();
  const doctors = await api(
    `/lpus/${encodeURIComponent(draft.lpu_id)}/specialities/${encodeURIComponent(draft.speciality_id)}/doctors`
  );

  const items = [{
    title: "👨‍⚕️ Любой врач",
    sub: "Слежу за всеми врачами этой специальности",
    onPick: () => {
      draft.doctor_id = null;
      draft.doctor_name = null;
      goStep(4);
    },
  }];

  doctors.forEach((doctor) => {
    items.push({
      title: doctor.name,
      sub: doctor.nearest_date ? `Ближайший приём: ${formatNearest(doctor.nearest_date)}` : "Свободных номерков нет",
      badge: doctor.free_tickets ? `${doctor.free_tickets} 🎫` : "нет",
      badgeMuted: !doctor.free_tickets,
      onPick: () => {
        draft.doctor_id = doctor.id;
        draft.doctor_name = doctor.name;
        goStep(4);
      },
    });
  });

  renderPicker({
    title: "Врач",
    subtitle: `Шаг 4 из 5 · ${draft.speciality_name}`,
    searchPlaceholder: "Поиск врача",
    emptyText: "Врачи не найдены",
    groups: [{ items }],
  });
}

/* Шаг 5 — фильтр по времени и создание подписки */
function stepFilter() {
  const wizard = document.getElementById("wizard");
  wizard.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">Удобное время</div>
          <div class="card-subtitle">Шаг 5 из 5 · номерки вне окна бот пропустит</div>
        </div>
      </div>
      ${timeControlsHtml(draft.time_from, draft.time_to)}
      <div class="hint-box" id="filter-summary"></div>
      <div class="row">
        <button class="btn btn-secondary" id="preview-btn">🔍 Проверить</button>
        <button class="btn btn-primary" id="create-btn">Подписаться</button>
      </div>
      <div id="preview-box"></div>
    </div>
  `;

  bindTimeControls(wizard, draft, updateFilterSummary);
  updateFilterSummary();

  document.getElementById("preview-btn").addEventListener("click", previewSlots);
  document.getElementById("create-btn").addEventListener("click", createSubscription);
}

function timeControlsHtml(from, to) {
  const presets = PRESETS.map((preset) => {
    const active = preset.from === from && preset.to === to ? " active" : "";
    return `<button type="button" class="preset${active}" data-from="${preset.from}" data-to="${preset.to}">${preset.label}</button>`;
  }).join("");

  return `
    <div class="presets">${presets}</div>
    <div class="time-row">
      <label>с</label>
      <input type="time" class="time-input" data-role="from" value="${from}" />
      <label>по</label>
      <input type="time" class="time-input" data-role="to" value="${to}" />
    </div>
  `;
}

/** Связывает пресеты и поля времени с объектом-приёмником. */
function bindTimeControls(root, target, onChange) {
  const fromInput = root.querySelector('[data-role="from"]');
  const toInput = root.querySelector('[data-role="to"]');

  const syncPresets = () => {
    root.querySelectorAll(".preset").forEach((preset) => {
      preset.classList.toggle(
        "active",
        preset.dataset.from === target.time_from && preset.dataset.to === target.time_to
      );
    });
  };

  root.querySelectorAll(".preset").forEach((preset) => {
    preset.addEventListener("click", () => {
      target.time_from = preset.dataset.from;
      target.time_to = preset.dataset.to;
      fromInput.value = target.time_from;
      toInput.value = target.time_to;
      syncPresets();
      onChange?.();
    });
  });

  [fromInput, toInput].forEach((input) => {
    input.addEventListener("change", () => {
      if (!input.value) return;
      target[input.dataset.role === "from" ? "time_from" : "time_to"] = input.value;
      syncPresets();
      onChange?.();
    });
  });
}

function updateFilterSummary() {
  const doctor = draft.doctor_name || "любой врач";
  document.getElementById("filter-summary").innerHTML =
    `🏥 ${esc(draft.lpu_name)}<br />🩺 ${esc(draft.speciality_name)} · 👤 ${esc(doctor)}<br />` +
    `⏰ Напишу про номерки на <b>${esc(describeWindow(draft.time_from, draft.time_to))}</b>`;
  document.getElementById("preview-box").innerHTML = "";
}

async function previewSlots() {
  const button = document.getElementById("preview-btn");
  const box = document.getElementById("preview-box");

  button.disabled = true;
  button.textContent = "Ищу…";
  box.innerHTML = `<div class="loading">Смотрю расписание…</div>`;

  try {
    const data = await api("/slots/preview", {
      method: "POST",
      body: JSON.stringify({
        lpu_id: draft.lpu_id,
        speciality_id: draft.speciality_id,
        doctor_id: draft.doctor_id,
        time_from: draft.time_from,
        time_to: draft.time_to,
      }),
    });
    box.innerHTML = `<div class="section-label" style="margin-top:16px">Сейчас свободно</div>${slotsHtml(data)}`;
  } catch (error) {
    box.innerHTML = "";
    tg.showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "🔍 Проверить";
  }
}

async function createSubscription() {
  const button = document.getElementById("create-btn");
  button.disabled = true;
  button.textContent = "Сохраняю…";

  try {
    const result = await api("/subscriptions", {
      method: "POST",
      body: JSON.stringify({
        district_id: draft.district_id,
        district_name: draft.district_name,
        lpu_id: draft.lpu_id,
        lpu_name: draft.lpu_name,
        speciality_id: draft.speciality_id,
        speciality_name: draft.speciality_name,
        doctor_id: draft.doctor_id,
        doctor_name: draft.doctor_name,
        time_from: draft.time_from,
        time_to: draft.time_to,
      }),
    });

    toast(result.created ? "✅ Подписка создана" : "✅ Подписка обновлена");
    draft = emptyDraft();
    step = 0;
    switchView("subs");
    await loadSubscriptions();
  } catch (error) {
    tg.showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Подписаться";
  }
}

/* ── Номерки ─────────────────────────────────────────────────────────────── */
function slotsHtml(data) {
  if (!data.total) {
    return emptyState("🕓", "Свободных номерков в это время нет");
  }

  return data.doctors.map((doctor) => {
    const byDay = new Map();
    doctor.slots.forEach((slot) => {
      if (!byDay.has(slot.date)) byDay.set(slot.date, []);
      byDay.get(slot.date).push(slot.time);
    });

    const days = [...byDay.entries()].map(([date, times]) => `
      <div class="slot-day">
        <div class="slot-date">${esc(formatDay(date))}</div>
        <div class="slot-times">${times.map((time) => `<span class="slot">${esc(time)}</span>`).join("")}</div>
      </div>
    `).join("");

    return `<div class="slot-doc">
      <div class="slot-doc-name">👨‍⚕️ ${esc(doctor.name)}</div>${days}
    </div>`;
  }).join("");
}

/* ── Список подписок ─────────────────────────────────────────────────────── */
async function loadSubscriptions() {
  const box = document.getElementById("subs-list");
  box.innerHTML = `<div class="loading">Загрузка…</div>`;

  try {
    subs = await api("/subscriptions");
  } catch (error) {
    box.innerHTML = `<div class="card">${emptyState("⚠️", error.message)}</div>`;
    return;
  }
  renderSubscriptions();
}

function renderSubscriptions() {
  const box = document.getElementById("subs-list");
  box.innerHTML = "";

  if (!subs.length) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = emptyState("🎫", "Подписок пока нет. Нажмите «Новая», чтобы начать следить за номерками.");
    box.appendChild(card);
    return;
  }

  subs.forEach((sub) => box.appendChild(subscriptionCard(sub)));

  const footer = document.createElement("button");
  footer.className = "btn btn-danger";
  footer.textContent = "🗑 Удалить все подписки";
  footer.addEventListener("click", () => {
    tg.showConfirm("Удалить все подписки?", async (ok) => {
      if (!ok) return;
      try {
        await api("/subscriptions", { method: "DELETE" });
        toast("🗑 Все подписки удалены");
        await loadSubscriptions();
      } catch (error) {
        tg.showAlert(error.message);
      }
    });
  });
  box.appendChild(footer);
}

function subscriptionCard(sub) {
  const card = document.createElement("div");
  card.className = `card sub-card${sub.is_active ? "" : " off"}`;
  card.innerHTML = `
    <div class="sub-head">
      <div class="sub-title">🏥 ${esc(sub.lpu_name)}</div>
      <label class="switch">
        <input type="checkbox" ${sub.is_active ? "checked" : ""} />
        <span></span>
      </label>
    </div>
    <div class="sub-meta">🩺 ${esc(sub.speciality_name)}</div>
    <div class="sub-meta">👤 ${esc(sub.doctor_name || "Любой врач")}</div>
    <div class="pill">⏰ ${esc(sub.time_label)}</div>
    <div class="sub-actions">
      <button class="btn btn-secondary btn-sm" data-act="slots">🎫 Номерки</button>
      <button class="btn btn-secondary btn-sm btn-narrow" data-act="edit">⏰</button>
      <button class="btn btn-danger btn-sm btn-narrow" data-act="delete">🗑</button>
    </div>
    <div class="sub-extra hidden"></div>
  `;

  const extra = card.querySelector(".sub-extra");

  card.querySelector(".switch input").addEventListener("change", async (event) => {
    const active = event.target.checked;
    try {
      await api(`/subscriptions/${sub.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: active }),
      });
      sub.is_active = active;
      card.classList.toggle("off", !active);
      toast(active ? "▶️ Слежу за номерками" : "⏸ Подписка на паузе");
    } catch (error) {
      event.target.checked = !active;
      tg.showAlert(error.message);
    }
  });

  card.querySelector('[data-act="slots"]').addEventListener("click", async (event) => {
    if (extra.dataset.mode === "slots" && !extra.classList.contains("hidden")) {
      extra.classList.add("hidden");
      return;
    }

    const button = event.currentTarget;
    button.disabled = true;
    extra.dataset.mode = "slots";
    extra.classList.remove("hidden");
    extra.innerHTML = `<div class="loading">Смотрю расписание…</div>`;

    try {
      const data = await api(`/subscriptions/${sub.id}/slots`);
      extra.innerHTML = slotsHtml(data);
    } catch (error) {
      extra.innerHTML = emptyState("⚠️", error.message);
    } finally {
      button.disabled = false;
    }
  });

  card.querySelector('[data-act="edit"]').addEventListener("click", () => {
    if (extra.dataset.mode === "edit" && !extra.classList.contains("hidden")) {
      extra.classList.add("hidden");
      return;
    }

    extra.dataset.mode = "edit";
    extra.classList.remove("hidden");
    extra.innerHTML = `
      ${timeControlsHtml(sub.time_from, sub.time_to)}
      <button class="btn btn-primary btn-sm" data-act="save">Сохранить время</button>
    `;

    const pending = { time_from: sub.time_from, time_to: sub.time_to };
    bindTimeControls(extra, pending);

    extra.querySelector('[data-act="save"]').addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Сохраняю…";
      try {
        const updated = await api(`/subscriptions/${sub.id}`, {
          method: "PATCH",
          body: JSON.stringify(pending),
        });
        Object.assign(sub, updated);
        toast(`⏰ Теперь ищу на ${updated.time_label}`);
        renderSubscriptions();
      } catch (error) {
        tg.showAlert(error.message);
        button.disabled = false;
        button.textContent = "Сохранить время";
      }
    });
  });

  card.querySelector('[data-act="delete"]').addEventListener("click", () => {
    tg.showConfirm(`Удалить подписку «${sub.speciality_name}»?`, async (ok) => {
      if (!ok) return;
      try {
        await api(`/subscriptions/${sub.id}`, { method: "DELETE" });
        subs = subs.filter((item) => item.id !== sub.id);
        toast("🗑 Подписка удалена");
        renderSubscriptions();
      } catch (error) {
        tg.showAlert(error.message);
      }
    });
  });

  return card;
}
