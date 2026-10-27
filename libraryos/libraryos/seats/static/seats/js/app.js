/* LibraryOS front end.
 *
 * Seat data lives in the Django database. This script renders it, sends every
 * action to the JSON API (see seats/urls.py) and re-renders from the fresh
 * state the server returns. It also polls the server so several browsers stay in sync.
 */

const CONFIG = JSON.parse(document.getElementById('app-config').textContent);
const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').content;

let state = JSON.parse(document.getElementById('initial-state').textContent);
let ZONES = state.zones;
let seats = state.seats;
let USERS = state.users;

let currentRole = state.role;
let selectedProfSeats = [];
let targetStudentSeatToBook = null;
let studentZoneFilter = 'all';
let lastNotifId = Math.max(0, ...((state.notifications || {}).items || []).map(n => n.id));

/* ------------------------------------------------------------------ helpers */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function seatUrl(name, seatId) {
  return CONFIG.urls[name].replace(CONFIG.seatPlaceholder, encodeURIComponent(seatId));
}

function findSeat(seatId) {
  return seats.find(s => s.id === seatId);
}

function resUrl(name, id) {
  return CONFIG.urls[name].replace(CONFIG.idPlaceholder, encodeURIComponent(id));
}

// the student's one pending / accepted / checked-in reservation, if any
function currentStudentBooking() {
  return (state.myReservations || []).find(r => r.role === 'student');
}

// the server clock, so countdowns do not depend on the browser clock being right
let clockOffset = Date.parse(CONFIG.serverTime) - Date.now();
function serverNow() { return Date.now() + clockOffset; }

function fmtStamp(iso) {
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function fmtClock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function applyState(newState) {
  state = newState;
  ZONES = newState.zones;
  seats = newState.seats;
  USERS = newState.users;
  handleNewNotifications();
  // drop professor selections that were taken by someone else in the meantime
  selectedProfSeats = selectedProfSeats.filter(id => {
    const s = findSeat(id);
    return s && s.status === 'available';
  });
}

async function callApi(url, payload = {}) {
  let res;
  let data = {};
  try {
    res = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    showToast('Could not reach the server. Please try again.', 'warning');
    return null;
  }
  try { data = await res.json(); } catch (err) { /* non-JSON error page */ }

  if (!res.ok || !data.ok) {
    showToast(data.error || `Request failed (${res.status}). Try reloading the page.`, 'warning');
    await refreshState();
    return { ok: false, ...data };
  }
  applyState(data.state);
  renderAllViews();
  if (data.message) showToast(data.message, data.level || 'info');
  return data;
}

async function refreshState() {
  try {
    const res = await fetch(CONFIG.urls.live, { credentials: 'same-origin', cache: 'no-store' });
    if (res.status === 401) { window.location.reload(); return; }   // signed out elsewhere
    const data = await res.json();
    if (data.serverTime) clockOffset = Date.parse(data.serverTime) - Date.now();
    if (data.ok && JSON.stringify(data.state) !== JSON.stringify(state)) {
      applyState(data.state);
      renderAllViews();
    }
    refreshLiveReport();
  } catch (err) { /* offline: keep showing the last known state */ }
}

/* --------------------------------------------------------------------- init */
document.addEventListener('DOMContentLoaded', () => {
  updateTimeDisplay();
  setInterval(updateTimeDisplay, 1000);
  setInterval(tickCountdowns, 1000);
  setRoleUI(currentRole);
  setInterval(() => { if (!document.hidden) refreshState(); }, CONFIG.pollMs);
});

function updateTimeDisplay() {
  const now = new Date();
  document.getElementById('current-date-display').innerText = now.toLocaleDateString('en-US', {
    weekday: 'long', month: 'short', day: 'numeric', year: 'numeric'
  });
  document.getElementById('current-time-display').innerText = now.toLocaleTimeString('en-US', {
    hour12: false
  });
}

/* ------------------------------------------------------------ roles & tabs */
function setRoleUI(role) {
  currentRole = role;
  const u = USERS[role];
  document.getElementById('user-name').innerText = u.name;
  document.getElementById('user-role-badge').innerText = u.roleText;
  document.getElementById('user-avatar').innerText = u.avatar;
  document.getElementById('user-subtitle').innerText = u.sub;

  document.getElementById('view-admin').classList.toggle('hidden', role !== 'admin');
  document.getElementById('view-professor').classList.toggle('hidden', role !== 'professor');
  document.getElementById('view-student').classList.toggle('hidden', role !== 'student');

  renderAllViews();
}

function switchTab(role, tabName) {
  if (role === 'admin') {
    ['overview', 'seatmap', 'requests', 'reservations', 'reserve', 'analytics'].forEach(t => {
      document.getElementById(`admin-panel-${t}`).classList.toggle('hidden', t !== tabName);
      const btn = document.getElementById(`admin-tab-${t}`);
      btn.className = t === tabName
        ? "pb-3 tab-active flex items-center space-x-2 whitespace-nowrap"
        : "pb-3 text-slate-500 hover:text-slate-700 flex items-center space-x-2 whitespace-nowrap";
    });
    if (tabName === 'analytics' && !reportLoaded) loadDateReport();
  } else if (role === 'prof') {
    ['reserve', 'sessions'].forEach(t => {
      document.getElementById(`prof-panel-${t}`).classList.toggle('hidden', t !== tabName);
      const btn = document.getElementById(`prof-tab-${t}`);
      btn.className = t === tabName
        ? "pb-3 tab-active flex items-center space-x-2"
        : "pb-3 text-slate-500 hover:text-slate-700 flex items-center space-x-2";
    });
  } else if (role === 'student') {
    ['find', 'bookings'].forEach(t => {
      document.getElementById(`student-panel-${t}`).classList.toggle('hidden', t !== tabName);
      const btn = document.getElementById(`student-tab-${t}`);
      btn.className = t === tabName
        ? "pb-3 tab-active flex items-center space-x-2"
        : "pb-3 text-slate-500 hover:text-slate-700 flex items-center space-x-2";
    });
  }
}

/* ---------------------------------------------------------------- rendering */
function renderAllViews() {
  updateKPIs();
  renderAdminAlerts();
  renderAdminRequests();
  renderAdminZoneSummaryBars();
  renderAdminSeatMap();
  renderAdminReservationsTable();
  renderAdminAnalytics();
  renderProfSeatMap();
  renderProfSessions();
  renderStudentSeatMap(studentZoneFilter);
  renderStudentActiveBookings();
  renderNotifications();
  tickCountdowns();
}

function updateKPIs() {
  const total = seats.length;
  const avail = seats.filter(s => s.status === 'available').length;
  const occ = seats.filter(s => s.status === 'occupied').length;
  const res = seats.filter(s => s.status === 'reserved').length;
  const pct = n => total ? Math.round((n / total) * 100) : 0;

  const globalAvail = document.getElementById('global-available-count');
  const globalTotal = document.getElementById('global-total-count');
  if (globalAvail) globalAvail.innerText = avail;
  if (globalTotal) globalTotal.innerText = total;
  document.getElementById('admin-kpi-available').innerText = avail;
  document.getElementById('admin-kpi-occupied').innerText = occ;
  document.getElementById('admin-kpi-reserved').innerText = res;
  document.getElementById('admin-kpi-avail-percent').innerText = `${pct(avail)}% Free Capacity`;
  document.getElementById('admin-kpi-occ-percent').innerText = `${pct(occ)}% Real-time Occupancy`;
}

function renderAdminAlerts() {
  const container = document.getElementById('admin-alerts');
  const noShows = seats.filter(s => s.noShow);
  const hogged = seats.filter(s => s.unattended).map(s => `
    <div class="p-3 bg-sky-50 border border-sky-200 rounded-lg text-sky-900 flex items-start space-x-2.5">
      <i class="fa-solid fa-bag-shopping text-sky-600 text-base mt-0.5"></i>
      <div>
        <span class="font-bold">Unattended Belongings (${esc(s.id)}):</span>
        <p class="text-sky-800 mt-0.5">Items are on this seat with nobody present. Please check the seat.</p>
      </div>
    </div>`).join('');
  if (noShows.length === 0 && hogged) {
    container.innerHTML = hogged;
    return;
  }
  if (noShows.length === 0) {
    container.innerHTML = `
      <div class="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-900 flex items-start space-x-2.5">
        <i class="fa-solid fa-circle-check text-emerald-600 text-base mt-0.5"></i>
        <div><span class="font-bold">All clear:</span>
          <p class="text-emerald-800 mt-0.5">No expired reservations are waiting for release.</p></div>
      </div>`;
    return;
  }
  container.innerHTML = hogged + noShows.map(s => `
    <div class="p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-900 flex items-start space-x-2.5">
      <i class="fa-solid fa-triangle-exclamation text-amber-600 text-base mt-0.5"></i>
      <div>
        <span class="font-bold">No-Show Alert (${esc(s.zone)}-${esc(s.number)}):</span>
        <p class="text-amber-800 mt-0.5">${esc(s.user)}'s reservation expired without check-in. Auto-release ready.</p>
        <button onclick="forceReleaseSeat('${esc(s.id)}')" class="mt-2 bg-amber-600 text-white px-2.5 py-1 rounded text-[11px] font-semibold hover:bg-amber-700">Release Seat ${esc(s.id)}</button>
      </div>
    </div>`).join('');
}

function renderAdminZoneSummaryBars() {
  const container = document.getElementById('admin-zone-summary-bars');
  container.innerHTML = '';
  ZONES.forEach(z => {
    const zSeats = seats.filter(s => s.zone === z.id);
    const freeCount = zSeats.filter(s => s.status === 'available').length;
    const occCount = zSeats.length - freeCount;
    const pctFree = zSeats.length ? Math.round((freeCount / zSeats.length) * 100) : 0;

    container.innerHTML += `
      <div class="space-y-1">
        <div class="flex justify-between text-xs">
          <span class="font-bold text-slate-700">${esc(z.name)}</span>
          <span class="text-slate-500">${freeCount} available / ${occCount} in use</span>
        </div>
        <div class="w-full bg-slate-100 h-3 rounded-full overflow-hidden flex border border-slate-200">
          <div class="bg-emerald-500 h-full" style="width: ${pctFree}%" title="Available"></div>
          <div class="bg-rose-500 h-full" style="width: ${100 - pctFree}%" title="Occupied/Reserved"></div>
        </div>
      </div>
    `;
  });
}

function renderAdminSeatMap() {
  const container = document.getElementById('admin-seatmap-grid');
  container.innerHTML = '';
  ZONES.forEach(z => {
    const zSeats = seats.filter(s => s.zone === z.id);
    let seatsHTML = '';
    zSeats.forEach(s => {
      let colorClass = 'bg-emerald-50 border-emerald-300 text-emerald-800 hover:bg-emerald-100';
      if (s.status === 'occupied') colorClass = 'bg-rose-500 border-rose-600 text-white';
      if (s.status === 'reserved') colorClass = 'bg-amber-400 border-amber-500 text-navy-900 font-bold';
      if (s.status === 'maintenance') colorClass = 'bg-slate-400 border-slate-500 text-white';
      if (s.status === 'pending') colorClass = 'bg-amber-50 border-amber-400 border-dashed text-amber-800 font-bold';

      seatsHTML += `
        <button onclick="openAdminSeatOverride('${esc(s.id)}')" class="seat-btn p-2 text-center rounded-lg border text-xs font-mono font-bold transition-all shadow-sm ${colorClass}" title="${esc(s.id)}:${esc(s.status)}">
          ${esc(s.id)}
        </button>
      `;
    });

    container.innerHTML += `
      <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-3">
        <div class="flex justify-between items-center border-b border-slate-100 pb-2">
          <h4 class="font-bold text-xs text-navy-900">${esc(z.name)}</h4>
          <span class="text-[11px] text-slate-500 font-medium">${zSeats.filter(s => s.status === 'available').length}/${z.total} Available</span>
        </div>
        <div class="grid grid-cols-6 gap-2">
          ${seatsHTML}
        </div>
      </div>
    `;
  });
}

function renderAdminReservationsTable() {
  const tbody = document.getElementById('admin-reservations-tbody');
  tbody.innerHTML = '';
  const rows = seats.filter(s => s.status !== 'available');

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="p-4 text-center text-slate-400">No active reservations recorded.</td></tr>`;
    return;
  }

  const BADGES = {
    pending: ['PENDING', 'bg-sky-100 text-sky-800'],
    reserved: ['UPCOMING', 'bg-amber-100 text-amber-800'],
    occupied: ['ACTIVE', 'bg-emerald-100 text-emerald-800'],
    maintenance: ['BLOCKED', 'bg-slate-200 text-slate-800'],
  };
  const btn = (label, onclick, cls) =>
    `<button onclick="${onclick}" class="${cls} px-2 py-1 rounded text-[10px] font-semibold">${label}</button>`;
  const GREEN = 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border border-emerald-200';
  const RED = 'bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200';
  const GREY = 'bg-slate-100 text-slate-700 hover:bg-slate-200 border border-slate-200';

  rows.forEach(s => {
    const [label, cls] = BADGES[s.status] || ['—', 'bg-slate-100 text-slate-700'];
    let actions = '';
    if (s.status === 'pending') {
      actions = btn('Accept', `acceptReservation(${s.reservationId})`, GREEN)
        + btn('Reject', `rejectReservation(${s.reservationId})`, RED)
        + btn('Block', `toggleMaintenance('${esc(s.id)}')`, GREY);
    } else if (s.status === 'maintenance') {
      actions = btn('Unblock', `toggleMaintenance('${esc(s.id)}')`, GREY);
    } else {
      actions = btn('Force Release', `forceReleaseSeat('${esc(s.id)}')`, RED)
        + btn('Block', `toggleMaintenance('${esc(s.id)}')`, GREY);
    }
    tbody.innerHTML += `
      <tr class="hover:bg-slate-50 border-b border-slate-100">
        <td class="p-3 font-mono font-bold text-slate-600">${s.reservationId ? `#RES-${s.reservationId}` : `#${esc(s.id)}`}</td>
        <td class="p-3 font-bold text-navy-900">${esc(s.id)} <span class="text-slate-400 font-normal">(${esc(s.zone)})</span></td>
        <td class="p-3 font-medium text-slate-800">${esc(s.user || 'N/A')}</td>
        <td class="p-3 uppercase text-[10px] font-bold text-slate-500">${esc(s.role || 'System')}</td>
        <td class="p-3 text-slate-600">${esc(s.time || 'N/A')}</td>
        <td class="p-3"><span class="${cls} px-2 py-0.5 rounded-full text-[10px] font-bold">${label}</span></td>
        <td class="p-3 text-right space-x-1 whitespace-nowrap">${actions}</td>
      </tr>
    `;
  });
  filterAdminReservations();   // keep any search/filter the admin typed
}

/* --------------------------------------------------------- analytics report */
function anGet(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj);
}

function anBars(items, valueOf, colour) {
  return items.map(i => `
    <div class="space-y-1">
      <div class="flex justify-between text-xs">
        <span class="font-semibold text-slate-700">${esc(i.label)}</span>
        <span class="text-slate-500">${esc(i.detail)}</span>
      </div>
      <div class="h-2.5 bg-slate-100 rounded-full overflow-hidden">
        <div class="h-full ${i.colour || colour} transition-all duration-500" style="width: ${Math.min(100, valueOf(i))}%"></div>
      </div>
    </div>`).join('');
}

function renderAdminAnalytics() {
  const a = state.analytics;
  if (!a) return;

  document.querySelectorAll('[data-an]').forEach(el => {
    const v = anGet(a, el.dataset.an);
    if (v !== undefined) el.textContent = v;
  });
  document.querySelectorAll('[data-an-bar]').forEach(el => {
    const v = anGet(a, el.dataset.anBar);
    if (v !== undefined) el.style.width = `${Math.min(100, v)}%`;
  });

  document.getElementById('an-zones').innerHTML = a.zones.length ? a.zones.map(z => `
    <div class="space-y-1">
      <div class="flex justify-between text-xs">
        <span class="font-semibold text-slate-700">${esc(z.name)}</span>
        <span class="text-slate-500">${z.occupied.count} occupied (${z.occupied.pct}%) &bull; ${z.reserved.count} reserved (${z.reserved.pct}%) &bull; ${z.utilization}% used</span>
      </div>
      <div class="h-2.5 bg-slate-100 rounded-full overflow-hidden flex">
        <div class="h-full bg-rose-500 transition-all duration-500" style="width: ${z.occupied.pct}%"></div>
        <div class="h-full bg-amber-400 transition-all duration-500" style="width: ${z.reserved.pct}%"></div>
      </div>
    </div>`).join('') : '<p class="text-xs text-slate-400">No zones yet.</p>';

  const st = a.reservations.status;
  document.getElementById('an-status-rows').innerHTML = anBars([
    { label: 'Awaiting approval', detail: `${st.awaitingApproval.count} • ${st.awaitingApproval.pct}%`, v: st.awaitingApproval.pct, colour: 'bg-sky-400' },
    { label: 'Accepted, waiting for check-in', detail: `${st.pending.count} • ${st.pending.pct}%`, v: st.pending.pct, colour: 'bg-amber-400' },
    { label: 'In session', detail: `${st.inSession.count} • ${st.inSession.pct}%`, v: st.inSession.pct, colour: 'bg-emerald-500' },
    { label: 'Completed (checked in)', detail: `${st.completed.count} • ${st.completed.pct}%`, v: st.completed.pct, colour: 'bg-royal-500' },
    { label: 'Cancelled before check-in', detail: `${st.cancelled.count} • ${st.cancelled.pct}%`, v: st.cancelled.pct, colour: 'bg-slate-400' },
    { label: 'Expired (no-show)', detail: `${st.expired.count} • ${st.expired.pct}%`, v: st.expired.pct, colour: 'bg-rose-500' },
    { label: 'Rejected', detail: `${st.rejected.count} • ${st.rejected.pct}%`, v: st.rejected.pct, colour: 'bg-slate-300' },
  ], i => i.v);

  document.getElementById('an-roles').innerHTML = anBars(
    a.roles.map(r => ({ label: r.label, detail: `${r.count} • ${r.pct}%`, v: r.pct })),
    i => i.v, 'bg-royal-500');

  document.getElementById('an-top-seats').innerHTML = a.topSeats.length ? a.topSeats.map(t => `
    <tr>
      <td class="p-3 font-bold text-navy-900">${esc(t.seat)} <span class="text-slate-400 font-normal">(${esc(t.zone)})</span></td>
      <td class="p-3">${t.count}</td>
      <td class="p-3">${t.pct}%</td>
      <td class="p-3 text-slate-600">${Math.floor(t.minutes / 60)}h ${t.minutes % 60}m</td>
    </tr>`).join('') : '<tr><td colspan="4" class="p-4 text-center text-slate-400">No sessions recorded yet.</td></tr>';

  const maxHour = Math.max(1, ...a.hours.reservations, ...a.hours.occupancy);
  const bar = (n, cls) => `<div class="flex-1 ${cls} rounded-t" style="height: ${Math.round(n / maxHour * 100)}%"></div>`;
  document.getElementById('an-hours').innerHTML = a.hours.reservations.map((n, h) => `
    <div class="flex-1 h-full flex items-end gap-px" title="${String(h).padStart(2, '0')}:00 — ${n} reservations, ${a.hours.occupancy[h]} check-ins/walk-ins">
      ${bar(n, 'bg-amber-400')}${bar(a.hours.occupancy[h], 'bg-royal-500')}
    </div>`).join('');
  const peakText = peaks => peaks.length ? peaks.map(p => `${p.label} (${p.count})`).join(', ') : 'no data yet';
  document.getElementById('an-peak-res').textContent = peakText(a.hours.peakReservations);
  document.getElementById('an-peak-occ').textContent = peakText(a.hours.peakOccupancy);
}

function filterAdminReservations() {
  const query = document.getElementById('admin-search-input').value.toLowerCase();
  const filter = document.getElementById('admin-status-filter').value;
  const rows = document.querySelectorAll('#admin-reservations-tbody tr');

  rows.forEach(row => {
    const text = row.innerText.toLowerCase();
    const matchesQuery = text.includes(query);
    const matchesFilter = filter === 'all' || text.includes(filter);
    row.style.display = (matchesQuery && matchesFilter) ? '' : 'none';
  });
}

/* --------------------------------------------------------------- professor */
function renderProfSeatMap() {
  // professors and admins share the multi-select reservation grid
  const isAdmin = currentRole === 'admin';
  const container = document.getElementById(isAdmin ? 'admin-reserve-grid' : 'prof-seatmap-grid');
  container.innerHTML = '';
  ZONES.forEach(z => {
    const zSeats = seats.filter(s => s.zone === z.id);
    let seatsHTML = '';
    zSeats.forEach(s => {
      const isSelected = selectedProfSeats.includes(s.id);
      let colorClass = 'bg-emerald-50 border-emerald-300 text-emerald-800 hover:bg-emerald-100';
      if (s.status !== 'available') {
        colorClass = 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed';
      }
      if (isSelected) {
        colorClass = 'bg-royal-600 border-royal-700 text-white font-bold ring-2 ring-royal-300';
      }

      seatsHTML += `
        <button onclick="toggleProfSeatSelection('${esc(s.id)}')" ${s.status !== 'available' ? 'disabled' : ''} class="seat-btn p-2 text-center rounded-lg border text-xs font-mono font-bold transition-all ${colorClass}">
          ${esc(s.id)}
        </button>
      `;
    });

    container.innerHTML += `
      <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-3">
        <div class="flex justify-between items-center border-b border-slate-100 pb-2">
          <h4 class="font-bold text-xs text-navy-900">${esc(z.name)}</h4>
          <span class="text-[11px] text-slate-500 font-medium">${zSeats.filter(s => s.status === 'available').length}/${z.total} Available</span>
        </div>
        <div class="grid grid-cols-6 gap-2">
          ${seatsHTML}
        </div>
      </div>
    `;
  });
  document.getElementById(isAdmin ? 'admin-selected-count' : 'prof-selected-count').innerText = selectedProfSeats.length;
}

function toggleProfSeatSelection(seatId) {
  const idx = selectedProfSeats.indexOf(seatId);
  if (idx > -1) selectedProfSeats.splice(idx, 1);
  else selectedProfSeats.push(seatId);
  renderProfSeatMap();
}

function clearProfSelections() {
  selectedProfSeats = [];
  renderProfSeatMap();
}

function openBatchModal() {
  if (selectedProfSeats.length === 0) {
    showToast('Please select at least one available seat first.', 'warning');
    return;
  }
  document.getElementById('prof-batch-count-text').innerText = selectedProfSeats.length;
  const container = document.getElementById('prof-batch-seats-list');
  container.innerHTML = selectedProfSeats.map(s => `<span class="bg-royal-100 text-royal-800 text-xs font-bold px-2 py-0.5 rounded border border-royal-200">${esc(s)}</span>`).join('');
  openModal('modal-prof-batch');
}

async function confirmProfBatchBooking() {
  const titleInput = document.getElementById('prof-session-title-input');
  const res = await callApi(CONFIG.urls.batchReserve, {
    seat_ids: selectedProfSeats,
    title: titleInput.value.trim(),
    duration: parseInt(document.getElementById('prof-session-duration').value, 10),
    start_hour: new Date().getHours(),
  });
  if (res && res.ok) {
    selectedProfSeats = [];
    titleInput.value = '';
    closeModal('modal-prof-batch');
    renderAllViews();
  }
}

function renderProfSessions() {
  const container = document.getElementById('prof-sessions-container');
  if (currentRole !== 'professor') return;
  const mine = (state.myReservations || []).filter(r => r.role === 'professor');
  document.getElementById('prof-session-count').innerText = mine.length;

  if (mine.length === 0) {
    container.innerHTML = `
      <div class="bg-white p-8 rounded-xl border border-slate-200 text-center space-y-2">
        <i class="fa-solid fa-folder-open text-slate-300 text-3xl"></i>
        <p class="text-xs text-slate-500">You currently have no faculty seat requests or reservations.</p>
      </div>
    `;
    return;
  }
  container.innerHTML = mine.map(reservationCardHTML).join('');
}

/* ----------------------------------------------------------------- student */
function renderStudentSeatMap(filterZone) {
  const container = document.getElementById('student-seatmap-grid');
  container.innerHTML = '';
  const zonesToDisplay = filterZone === 'all' ? ZONES : ZONES.filter(z => z.id === filterZone);

  zonesToDisplay.forEach(z => {
    const zSeats = seats.filter(s => s.zone === z.id);
    let seatsHTML = '';
    zSeats.forEach(s => {
      let colorClass = 'bg-emerald-50 border-emerald-300 text-emerald-800 hover:bg-emerald-100 cursor-pointer';
      let clickFn = `handleStudentSeatClick('${esc(s.id)}')`;

      if (s.status === 'occupied') {
        colorClass = 'bg-rose-50 border-rose-200 text-rose-400 cursor-not-allowed';
        clickFn = `showToast('Seat ${esc(s.id)} is currently occupied by another student.', 'warning')`;
      } else if (s.status === 'pending') {
        colorClass = 'bg-amber-50 border-amber-300 border-dashed text-amber-700 cursor-not-allowed';
        clickFn = `showToast('${s.mine ? 'Your request for' : 'A request for'} seat ${esc(s.id)} is waiting for admin approval.', 'warning')`;
      } else if (s.status === 'reserved') {
        colorClass = 'bg-amber-100 border-amber-300 text-amber-800 cursor-not-allowed';
        clickFn = `showToast('Seat ${esc(s.id)} is reserved.', 'warning')`;
      } else if (s.status === 'maintenance') {
        colorClass = 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed';
        clickFn = `showToast('Seat ${esc(s.id)} is under maintenance.', 'warning')`;
      }

      seatsHTML += `
        <button onclick="${clickFn}" class="seat-btn p-2 text-center rounded-lg border text-xs font-mono font-bold transition-all ${colorClass}">
          ${esc(s.id)}
        </button>
      `;
    });

    container.innerHTML += `
      <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-3">
        <div class="flex justify-between items-center border-b border-slate-100 pb-2">
          <h4 class="font-bold text-xs text-navy-900">${esc(z.name)}</h4>
          <span class="text-[11px] text-slate-500 font-medium">${zSeats.filter(s => s.status === 'available').length}/${z.total} Free</span>
        </div>
        <div class="grid grid-cols-6 gap-2">
          ${seatsHTML}
        </div>
      </div>
    `;
  });
}

function filterStudentZone(zoneId) {
  studentZoneFilter = zoneId;
  ['all', ...ZONES.map(z => z.id)].forEach(z => {
    const btn = document.getElementById(`zone-filter-${z}`);
    if (!btn) return;
    btn.className = z === zoneId
      ? "px-3 py-1.5 rounded-lg font-semibold bg-royal-600 text-white"
      : "px-3 py-1.5 rounded-lg font-semibold bg-slate-100 text-slate-600 hover:bg-slate-200";
  });
  renderStudentSeatMap(zoneId);
}

function handleStudentSeatClick(seatId) {
  const existing = currentStudentBooking();
  if (existing) {
    targetStudentSeatToBook = seatId;
    document.getElementById('single-rule-current-text').innerText = `Seat ${existing.seat} • ${existing.zoneName} (${existing.time})`;
    openModal('modal-single-rule');
    return;
  }
  openStudentBookingModal(seatId);
}

function openStudentBookingModal(seatId) {
  const s = findSeat(seatId);
  if (!s) return;
  targetStudentSeatToBook = seatId;
  document.getElementById('modal-seat-title').innerText = `Seat ${s.id}`;
  document.getElementById('modal-seat-zone').innerText = s.zoneName;
  document.getElementById('modal-seat-features').innerText = s.features;
  openModal('modal-student-reserve');
}

async function confirmStudentBooking() {
  if (!targetStudentSeatToBook) return;
  const res = await callApi(seatUrl('reserve', targetStudentSeatToBook), {
    duration: parseInt(document.getElementById('modal-time-duration').value, 10),
    start_hour: new Date().getHours(),
  });
  closeModal('modal-student-reserve');
  if (res && res.ok) {
    targetStudentSeatToBook = null;
    switchTab('student', 'bookings');
  }
}

async function cancelStudentCurrentAndBookNew() {
  const existing = currentStudentBooking();
  if (existing) {
    const res = await callApi(resUrl('cancel', existing.id));
    if (!res || !res.ok) return;
  }
  closeModal('modal-single-rule');
  openStudentBookingModal(targetStudentSeatToBook);
}

function renderStudentActiveBookings() {
  const container = document.getElementById('student-active-booking-container');
  const booking = currentStudentBooking();

  document.getElementById('student-booking-badge').innerText = booking ? '1' : '0';

  if (!booking) {
    container.innerHTML = `
      <div class="bg-white p-8 rounded-xl border border-slate-200 text-center space-y-3">
        <i class="fa-solid fa-ticket text-slate-300 text-4xl"></i>
        <p class="text-sm font-semibold text-slate-700">No Active Seat Booking Found</p>
        <p class="text-xs text-slate-500 max-w-sm mx-auto">You do not have any pending, active or upcoming reservations. Select a seat on the floorplan to request one.</p>
        <button onclick="switchTab('student', 'find')" class="bg-royal-600 text-white text-xs font-bold px-4 py-2 rounded-xl hover:bg-royal-700">Browse Available Seats</button>
      </div>
    `;
    return;
  }
  container.innerHTML = reservationCardHTML(booking);
}

/* One reservation, as shown to a student or professor: status, confirmation code, grace countdown. */
const RES_BADGES = {
  pending: ['PENDING APPROVAL', 'bg-amber-100 text-amber-800'],
  reserved: ['ACCEPTED - CHECK IN NOW', 'bg-sky-100 text-sky-800'],
  active: ['CHECKED IN', 'bg-emerald-100 text-emerald-800'],
};

function reservationCardHTML(r) {
  const [label, badgeCls] = RES_BADGES[r.status] || [String(r.status).toUpperCase(), 'bg-slate-100 text-slate-700'];

  let codeBlock = '';
  if (r.code && r.status !== 'active') {
    const hint = r.status === 'reserved'
      ? 'Show this code to the library admin to confirm your Time-In.'
      : 'Keep this code. You will show it to the admin once your request is accepted.';
    codeBlock = `
      <div class="bg-navy-900 text-white rounded-xl p-4 text-center space-y-1">
        <p class="text-[11px] uppercase tracking-wider text-sky-200">Your confirmation code</p>
        <p class="font-mono text-3xl font-bold tracking-widest select-all">${esc(r.code)}</p>
        <p class="text-[11px] text-sky-100/80">${hint}</p>
      </div>`;
  }

  let note = '';
  if (r.status === 'pending') {
    note = `<div class="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs text-amber-900">
      <i class="fa-solid fa-hourglass-half mr-1"></i>Waiting for the admin to accept your request. The 15-minute check-in window starts when it is accepted.</div>`;
  } else if (r.status === 'reserved' && r.graceExpiresAt) {
    note = `<div class="bg-sky-50 border border-sky-200 rounded-xl p-3 text-xs text-sky-900 flex flex-wrap items-center justify-between gap-2">
      <span><i class="fa-solid fa-stopwatch mr-1"></i>Check in with the admin before the grace period ends, or the seat is released.</span>
      <span class="text-sm">Time left: ${countdownHTML(r.graceExpiresAt)}</span></div>`;
  } else if (r.status === 'active' && r.checkedInAt) {
    note = `<div class="bg-emerald-50 border border-emerald-200 rounded-xl p-3 text-xs text-emerald-900">
      <i class="fa-solid fa-circle-check mr-1"></i>Time-in confirmed at ${esc(fmtClock(r.checkedInAt))}. Enjoy your session!</div>`;
  }

  const cancelLabel = r.status === 'active' ? 'End Session & Check-Out' : r.status === 'pending' ? 'Cancel Request' : 'Cancel Reservation';
  return `
    <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4">
      <div class="flex flex-wrap justify-between items-start border-b border-slate-100 pb-4 gap-2">
        <div>
          <span class="${badgeCls} text-xs font-bold px-2.5 py-1 rounded-full uppercase tracking-wider">${label}</span>
          <h3 class="text-2xl font-bold text-navy-900 mt-2">Seat ${esc(r.seat)}</h3>
          <p class="text-xs text-slate-500">${esc(r.zoneName)}</p>
        </div>
        <div class="text-right">
          <span class="text-xs text-slate-400 block">Session Time:</span>
          <span class="text-sm font-bold text-slate-700">${esc(r.time)}</span>
          <span class="text-[11px] text-slate-400 block">Requested ${esc(fmtStamp(r.requestedAt))}</span>
        </div>
      </div>
      ${codeBlock}
      ${note}
      <button onclick="cancelReservation(${r.id})" class="w-full bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100 font-bold py-2.5 rounded-xl text-xs flex items-center justify-center space-x-1.5">
        <i class="fa-solid fa-circle-xmark"></i><span>${cancelLabel}</span>
      </button>
    </div>
  `;
}

function countdownHTML(expiresAt) {
  return `<span class="countdown font-mono font-bold" data-expires="${esc(expiresAt)}">--:--</span>`;
}

let expiryRefreshAt = 0;
function tickCountdowns() {
  document.querySelectorAll('.countdown[data-expires]').forEach(el => {
    const left = Date.parse(el.dataset.expires) - serverNow();
    if (left <= 0) {
      el.textContent = '00:00';
      el.className = 'countdown font-mono font-bold text-rose-600';
      if (Date.now() - expiryRefreshAt > 3000) { expiryRefreshAt = Date.now(); refreshState(); }
      return;
    }
    const m = Math.floor(left / 60000);
    const sec = Math.floor((left % 60000) / 1000);
    el.textContent = `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
    el.className = 'countdown font-mono font-bold ' + (left < 120000 ? 'text-rose-600' : left < 300000 ? 'text-amber-600' : 'text-emerald-600');
  });
}

/* ------------------------------------------------------- shared seat actions */
async function forceReleaseSeat(seatId) {
  await callApi(seatUrl('release', seatId));
}

async function toggleMaintenance(seatId) {
  await callApi(seatUrl('maintenance', seatId));
}

function openAdminSeatOverride(seatId) {
  const s = findSeat(seatId);
  if (!s) return;
  if (s.status === 'pending') {
    switchTab('admin', 'requests');          // a waiting request: accept or reject it there
  } else if (s.status === 'available' || s.status === 'maintenance') {
    toggleMaintenance(seatId);
  } else {
    forceReleaseSeat(seatId);
  }
}

async function acceptReservation(id) {
  await callApi(resUrl('accept', id));
}

async function rejectReservation(id) {
  await callApi(resUrl('reject', id));
}

async function cancelReservation(id) {
  await callApi(resUrl('cancel', id));
}

async function verifyCode() {
  const input = document.getElementById('verify-code-input');
  const res = await callApi(CONFIG.urls.verify, { code: input.value });
  if (res && res.ok) input.value = '';
}

/* ------------------------------------------------------------ modals & toast */
function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
}

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}

function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  let bg = 'bg-navy-900 text-white';
  if (type === 'success') bg = 'bg-emerald-600 text-white';
  if (type === 'warning') bg = 'bg-amber-500 text-white';
  if (type === 'info') bg = 'bg-royal-600 text-white';

  toast.className = `${bg} px-4 py-3 rounded-xl shadow-xl text-xs font-semibold flex items-center space-x-2 border border-white/20 transition-all transform translate-y-2 pointer-events-auto max-w-sm`;
  const icon = document.createElement('i');
  icon.className = 'fa-solid fa-circle-info';
  const text = document.createElement('span');
  text.textContent = message;          // textContent: never interpret server/user text as HTML
  toast.append(icon, text);
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

/* ------------------------------------------------- admin: requests & time-in */
function renderAdminRequests() {
  const pending = state.pendingRequests || [];
  const awaiting = state.awaiting || [];
  const badge = document.getElementById('admin-tab-requests-badge');
  if (badge) {
    badge.textContent = pending.length;
    badge.classList.toggle('hidden', pending.length === 0);
  }
  setText('req-pending-count', pending.length);
  setText('req-awaiting-count', awaiting.length);

  const pendingBox = document.getElementById('req-pending-list');
  if (!pendingBox) return;
  pendingBox.innerHTML = pending.length ? pending.map(r => `
    <div class="p-3 flex flex-wrap items-center justify-between gap-3">
      <div class="text-xs space-y-0.5">
        <div><span class="font-mono font-bold text-navy-900">${esc(r.seat)}</span>
          <span class="text-slate-400">(${esc(r.zone)})</span>
          <span class="ml-1 text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-sky-100 text-sky-800">${esc(r.role)}</span></div>
        <div class="font-medium text-slate-800">${esc(r.user)}${r.title ? ` &bull; ${esc(r.title)}` : ''}</div>
        <div class="text-slate-500">${esc(r.time)} &bull; requested ${esc(fmtStamp(r.requestedAt))}</div>
      </div>
      <div class="flex space-x-2">
        <button onclick="acceptReservation(${r.id})" class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold">Accept</button>
        <button onclick="rejectReservation(${r.id})" class="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold">Reject</button>
      </div>
    </div>`).join('') : '<p class="p-4 text-xs text-slate-500">No requests are waiting for approval.</p>';

  const awaitingBox = document.getElementById('req-awaiting-list');
  awaitingBox.innerHTML = awaiting.length ? awaiting.map(r => `
    <div class="p-3 flex flex-wrap items-center justify-between gap-3">
      <div class="text-xs space-y-0.5">
        <div><span class="font-mono font-bold text-navy-900">${esc(r.seat)}</span>
          <span class="text-slate-400">(${esc(r.zone)})</span>
          <span class="ml-1 text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-sky-100 text-sky-800">${esc(r.role)}</span></div>
        <div class="font-medium text-slate-800">${esc(r.user)}</div>
        <div class="text-slate-500">Accepted ${esc(fmtClock(r.acceptedAt))} &bull; ${countdownHTML(r.graceExpiresAt)} left to check in</div>
      </div>
      <button onclick="forceReleaseSeat('${esc(r.seat)}')" class="px-3 py-1.5 rounded-lg bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 text-xs font-semibold">Force Release</button>
    </div>`).join('') : '<p class="p-4 text-xs text-slate-500">Nobody is waiting to check in.</p>';
}

/* ------------------------------------------------------------ notifications */
const NOTIF_DOT = { success: 'bg-emerald-500', warning: 'bg-amber-500', info: 'bg-royal-500' };

function handleNewNotifications() {
  const items = (state.notifications || {}).items || [];
  items.filter(n => n.id > lastNotifId && !n.read).reverse()
    .forEach(n => showToast(n.message, n.kind, 7000));
  lastNotifId = Math.max(lastNotifId, ...items.map(n => n.id));
}

function renderNotifications() {
  const data = state.notifications || { unread: 0, items: [] };
  const badge = document.getElementById('notif-badge');
  if (!badge) return;
  badge.textContent = data.unread > 99 ? '99+' : data.unread;
  badge.classList.toggle('hidden', data.unread === 0);
  document.getElementById('notif-list').innerHTML = data.items.length ? data.items.map(n => `
    <button onclick="markNotificationsRead([${n.id}])" class="w-full text-left px-4 py-3 flex items-start space-x-3 hover:bg-slate-50 ${n.read ? 'opacity-60' : ''}">
      <span class="mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${n.read ? 'bg-slate-300' : (NOTIF_DOT[n.kind] || NOTIF_DOT.info)}"></span>
      <span class="min-w-0">
        <span class="block text-xs text-slate-800 ${n.read ? '' : 'font-semibold'}">${esc(n.message)}</span>
        <span class="block text-[10px] text-slate-400 mt-0.5">${esc(fmtStamp(n.at))}</span>
      </span>
    </button>`).join('') : '<p class="px-4 py-6 text-xs text-center text-slate-400">No notifications yet.</p>';
}

function toggleNotifications(event) {
  event.stopPropagation();
  document.getElementById('notif-dropdown').classList.toggle('hidden');
}

async function markNotificationsRead(ids) {
  await callApi(CONFIG.urls.notificationsRead, ids ? { ids } : {});
}

document.addEventListener('click', event => {
  const dropdown = document.getElementById('notif-dropdown');
  if (dropdown && !event.target.closest('#notif-wrap')) dropdown.classList.add('hidden');
});

/* ------------------------------------------------- admin: report for a date */
let reportRange = { start: CONFIG.serverDate, end: CONFIG.serverDate };
let reportLoaded = false;
let reportIncludesToday = false;

function shiftDate(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function setReportPreset(preset) {
  const today = CONFIG.serverDate;
  if (preset === 'today') reportRange = { start: today, end: today };
  if (preset === 'yesterday') reportRange = { start: shiftDate(today, -1), end: shiftDate(today, -1) };
  if (preset === 'week') reportRange = { start: shiftDate(today, -6), end: today };
  document.getElementById('rep-start').value = reportRange.start;
  document.getElementById('rep-end').value = reportRange.end;
  loadDateReport();
}

function onReportDatesChanged() {
  const start = document.getElementById('rep-start').value;
  if (!start) return;
  reportRange = { start, end: document.getElementById('rep-end').value || start };
  loadDateReport();
}

function reportQuery() {
  return new URLSearchParams(reportRange).toString();
}

async function loadDateReport() {
  try {
    const res = await fetch(`${CONFIG.urls.analytics}?${reportQuery()}`, { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json();
    if (!data.ok) {
      showToast(data.error || 'Could not load the report.', 'warning');
      return;
    }
    reportLoaded = true;
    reportIncludesToday = data.report.includesToday;
    renderDateReport(data.report);
  } catch (err) { /* offline: keep the last report */ }
}

// a report that includes today changes as the day goes on, so keep it fresh while it is on screen
function refreshLiveReport() {
  const panel = document.getElementById('admin-panel-analytics');
  if (currentRole === 'admin' && reportLoaded && reportIncludesToday && panel && !panel.classList.contains('hidden')) {
    loadDateReport();
  }
}

function exportDateReport() {
  window.location.href = `${CONFIG.urls.dateReportCsv}?${reportQuery()}`;
}

function renderDateReport(rep) {
  document.getElementById('rep-start').value = rep.start;
  document.getElementById('rep-end').value = rep.end;
  setText('rep-range-label', rep.start === rep.end ? rep.start : `${rep.start} to ${rep.end}`);
  document.getElementById('rep-live-note').classList.toggle('hidden', !rep.includesToday);

  document.querySelectorAll('[data-rp]').forEach(el => {
    const v = anGet(rep, el.dataset.rp);
    if (v !== undefined) el.textContent = v;
  });
  document.querySelectorAll('[data-rp-bar]').forEach(el => {
    const v = anGet(rep, el.dataset.rpBar);
    if (v !== undefined) el.style.width = `${Math.min(100, v)}%`;
  });

  document.getElementById('rep-roles').innerHTML = anBars(
    rep.roles.map(r => ({ label: r.label, detail: `${r.count} • ${r.pct}%`, v: r.pct })),
    i => i.v, 'bg-royal-500');

  document.getElementById('rep-top-seats').innerHTML = rep.topSeats.length ? rep.topSeats.map(t => `
    <tr>
      <td class="p-3 font-bold text-navy-900">${esc(t.seat)} <span class="text-slate-400 font-normal">(${esc(t.zone)})</span></td>
      <td class="p-3">${t.count}</td>
      <td class="p-3">${t.pct}%</td>
      <td class="p-3 text-slate-600">${Math.floor(t.minutes / 60)}h ${t.minutes % 60}m</td>
    </tr>`).join('') : '<tr><td colspan="4" class="p-4 text-center text-slate-400">No seat sessions on this date.</td></tr>';
}
