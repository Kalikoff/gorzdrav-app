/* Запись по направлению. Общие помощники (api, esc, toast, formatDay,
   timeControlsHtml, bindTimeControls, slotsHtml, emptyState) живут в app.js. */

let referralDraft = null;   // { number, last_name, data, time_from, time_to }

async function lookupReferral() {
  const number = document.getElementById("ref-number").value.trim();
  const lastName = document.getElementById("ref-lastname").value.trim();
  const box = document.getElementById("ref-result");

  if (!/^\d{4,}$/.test(number)) {
    tg.showAlert("Введите номер направления — только цифры");
    return;
  }
  if (!lastName) {
    tg.showAlert("Введите фамилию пациента");
    return;
  }

  const button = document.getElementById("ref-find");
  button.disabled = true;
  button.textContent = "Ищу…";
  box.innerHTML = `<div class="card"><div class="loading">Запрашиваю направление…</div></div>`;

  try {
    const data = await api("/referral/lookup", {
      method: "POST",
      body: JSON.stringify({ number, last_name: lastName }),
    });
    referralDraft = { number, last_name: lastName, data, time_from: "00:00", time_to: "23:59" };
    renderReferral();
  } catch (error) {
    referralDraft = null;
    box.innerHTML = `<div class="card">${emptyState("🔍", error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Найти направление";
  }
}

function renderReferral() {
  const box = document.getElementById("ref-result");
  const { data } = referralDraft;
  box.innerHTML = "";

  const head = document.createElement("div");
  head.className = "card";
  head.innerHTML = `
    <div class="card-header">
      <div>
        <div class="card-title">${esc(data.patient)}</div>
        <div class="card-subtitle">Направление №${esc(referralDraft.number)}</div>
      </div>
    </div>
    <div class="sub-meta">🏥 ${esc(data.lpu_name)}</div>
    ${data.lpu_address ? `<div class="sub-meta">📍 ${esc(data.lpu_address)}</div>` : ""}
    ${data.lpu_phone ? `<div class="sub-meta">☎️ ${esc(data.lpu_phone)}</div>` : ""}
  `;
  box.appendChild(head);

  let total = 0;
  data.specialities.forEach((speciality) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      `<div class="card-header"><div class="card-title">🩺 ${esc(speciality.name)}</div></div>`;

    speciality.doctors.forEach((doctor) => {
      total += doctor.slots.length;

      const block = document.createElement("div");
      block.className = "slot-doc";
      const note = doctor.note ? `<div class="pick-sub">${esc(doctor.note)}</div>` : "";
      block.innerHTML = `<div class="slot-doc-name">👨‍⚕️ ${esc(doctor.name)}</div>${note}`;

      if (!doctor.slots.length) {
        block.insertAdjacentHTML("beforeend",
          `<div class="pick-sub">Свободных номерков нет</div>`);
      } else {
        const byDay = new Map();
        doctor.slots.forEach((slot) => {
          if (!byDay.has(slot.date)) byDay.set(slot.date, []);
          byDay.get(slot.date).push(slot);
        });

        byDay.forEach((slots, date) => {
          const day = document.createElement("div");
          day.className = "slot-day";
          day.innerHTML = `<div class="slot-date">${esc(formatDay(date))}</div>`;

          const row = document.createElement("div");
          row.className = "slot-times";
          slots.forEach((slot) => {
            const chip = document.createElement("button");
            chip.className = "slot slot-btn";
            chip.type = "button";
            chip.textContent = slot.time;
            chip.addEventListener("click", () => confirmBooking(doctor, slot, date));
            row.appendChild(chip);
          });

          day.appendChild(row);
          block.appendChild(day);
        });
      }

      card.appendChild(block);
    });

    box.appendChild(card);
  });

  if (!total) {
    const none = document.createElement("div");
    none.className = "card";
    none.innerHTML = emptyState(
      "🕓",
      "Свободных номерков по направлению сейчас нет. Поставьте его под наблюдение — сообщу, как появятся."
    );
    box.appendChild(none);
  }

  const watch = document.createElement("div");
  watch.className = "card";
  watch.innerHTML = `
    <div class="card-header">
      <div>
        <div class="card-title">Следить за направлением</div>
        <div class="card-subtitle">Сообщу, когда появится подходящий номерок</div>
      </div>
    </div>
    ${timeControlsHtml("00:00", "23:59")}
    <button class="btn btn-secondary" id="ref-watch">🔔 Следить</button>
  `;
  box.appendChild(watch);

  bindTimeControls(watch, referralDraft);
  watch.querySelector("#ref-watch").addEventListener("click", watchReferral);
}

function confirmBooking(doctor, slot, date) {
  tg.showConfirm(
    `Записаться к врачу ${doctor.name}?\n\n${formatDay(date)} в ${slot.time}\n\n` +
    "Запись будет создана в горздраве.",
    async (ok) => {
      if (!ok) return;
      try {
        const result = await api("/referrals/book", {
          method: "POST",
          body: JSON.stringify({
            number: referralDraft.number,
            last_name: referralDraft.last_name,
            appointment_id: slot.appointment_id,
          }),
        });
        toast(`✅ Записаны на ${result.time}`);
        tg.HapticFeedback?.notificationOccurred?.("success");
        await loadBookings();
        await lookupReferral();
      } catch (error) {
        tg.showAlert(error.message);
      }
    }
  );
}

async function watchReferral() {
  const button = document.getElementById("ref-watch");
  button.disabled = true;
  button.textContent = "Сохраняю…";
  try {
    const result = await api("/referrals", {
      method: "POST",
      body: JSON.stringify({
        number: referralDraft.number,
        last_name: referralDraft.last_name,
        time_from: referralDraft.time_from,
        time_to: referralDraft.time_to,
      }),
    });
    toast(result.created ? "🔔 Слежу за направлением" : "🔔 Наблюдение обновлено");
    await loadWatchedReferrals();
  } catch (error) {
    tg.showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "🔔 Следить";
  }
}

/* ── Направления под наблюдением ─────────────────────────────────────────── */

async function loadWatchedReferrals() {
  const box = document.getElementById("ref-watched");
  let items = [];
  try {
    items = await api("/referrals");
  } catch {
    box.innerHTML = "";
    return;
  }

  box.innerHTML = "";
  if (!items.length) return;

  const title = document.createElement("div");
  title.className = "section-label";
  title.textContent = "Под наблюдением";
  box.appendChild(title);

  items.forEach((item) => box.appendChild(watchedReferralCard(item)));
}

function watchedReferralCard(item) {
  const card = document.createElement("div");
  card.className = `card sub-card${item.is_active ? "" : " off"}`;
  card.style.marginBottom = "12px";
  card.innerHTML = `
    <div class="sub-head">
      <div class="sub-title">📄 №${esc(item.number)}</div>
      <label class="switch">
        <input type="checkbox" ${item.is_active ? "checked" : ""} />
        <span></span>
      </label>
    </div>
    ${item.patient_name ? `<div class="sub-meta">👤 ${esc(item.patient_name)}</div>` : ""}
    ${item.lpu_name ? `<div class="sub-meta">🏥 ${esc(item.lpu_name)}</div>` : ""}
    ${item.speciality_name ? `<div class="sub-meta">🩺 ${esc(item.speciality_name)}</div>` : ""}
    <div class="pill">⏰ ${esc(item.time_label)}</div>
    <div class="sub-actions">
      <button class="btn btn-secondary btn-sm" data-act="slots">🎫 Номерки</button>
      <button class="btn btn-danger btn-sm btn-narrow" data-act="delete">🗑</button>
    </div>
    <div class="sub-extra hidden"></div>
  `;

  const extra = card.querySelector(".sub-extra");

  card.querySelector(".switch input").addEventListener("change", async (event) => {
    const active = event.target.checked;
    try {
      await api(`/referrals/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: active }),
      });
      card.classList.toggle("off", !active);
      toast(active ? "▶️ Слежу за направлением" : "⏸ Наблюдение на паузе");
    } catch (error) {
      event.target.checked = !active;
      tg.showAlert(error.message);
    }
  });

  card.querySelector('[data-act="slots"]').addEventListener("click", async (event) => {
    if (!extra.classList.contains("hidden")) {
      extra.classList.add("hidden");
      return;
    }
    const button = event.currentTarget;
    button.disabled = true;
    extra.classList.remove("hidden");
    extra.innerHTML = `<div class="loading">Смотрю направление…</div>`;
    try {
      extra.innerHTML = slotsHtml(await api(`/referrals/${item.id}/slots`));
    } catch (error) {
      extra.innerHTML = emptyState("⚠️", error.message);
    } finally {
      button.disabled = false;
    }
  });

  card.querySelector('[data-act="delete"]').addEventListener("click", () => {
    tg.showConfirm(`Перестать следить за направлением №${item.number}?`, async (ok) => {
      if (!ok) return;
      try {
        await api(`/referrals/${item.id}`, { method: "DELETE" });
        toast("🗑 Наблюдение снято");
        await loadWatchedReferrals();
      } catch (error) {
        tg.showAlert(error.message);
      }
    });
  });

  return card;
}

/* ── Мои записи ──────────────────────────────────────────────────────────── */

async function loadBookings() {
  const box = document.getElementById("ref-bookings");
  let items = [];
  try {
    items = await api("/bookings");
  } catch {
    box.innerHTML = "";
    return;
  }

  box.innerHTML = "";
  if (!items.length) return;

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<div class="card-header"><div class="card-title">📌 Мои записи</div></div>`;

  items.forEach((booking) => {
    const when = new Date(booking.visit_start);
    const dateStr = when.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    const timeStr = when.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

    const row = document.createElement("div");
    row.className = "history-item";
    row.innerHTML = `
      <div class="h-date">
        ${esc(booking.doctor_name || "Врач")}
        <div class="h-date-sub">${esc(dateStr)}, ${esc(timeStr)} · ${esc(booking.lpu_name || "")}</div>
      </div>
    `;

    if (booking.status !== "active") {
      row.insertAdjacentHTML("beforeend", `<span class="pick-badge muted">отменена</span>`);
      row.style.opacity = ".55";
    } else {
      const button = document.createElement("button");
      button.className = "btn-icon";
      button.textContent = "✖️";
      button.addEventListener("click", () => cancelBooking(booking));
      row.appendChild(button);
    }

    card.appendChild(row);
  });

  box.appendChild(card);
}

function cancelBooking(booking) {
  const when = new Date(booking.visit_start).toLocaleString("ru-RU", {
    day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
  });
  tg.showConfirm(
    `Отменить запись к врачу ${booking.doctor_name}?\n\n${when}`,
    async (ok) => {
      if (!ok) return;
      try {
        await api(`/bookings/${booking.id}/cancel`, { method: "POST" });
        toast("Запись отменена");
        await loadBookings();
      } catch (error) {
        tg.showAlert(error.message);
      }
    }
  );
}
