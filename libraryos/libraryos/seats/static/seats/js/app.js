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

function currentStudentBooking() {
  return seats.find(s =>
    s.role === 'student' && s.mine && (s.status === 'reserved' || s.status === 'occupied')
  );
}

function applyState(newState) {
  state = newState;
  ZONES = newState.zones;
  seats = newState.seats;
  USERS = newState.users;
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
    const res = await fetch(CONFIG.urls.state, { credentials: 'same-origin' });
    const data = await res.json();
    if (data.ok) {
      applyState(data.state);
      renderAllViews();
    }
  } catch (err) { /* offline: keep showing the last known state */ }
}

/* --------------------------------------------------------------------- init */
document.addEventListener('DOMContentLoaded', () => {
  updateTimeDisplay();
  setInterval(updateTimeDisplay, 1000);
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
    ['overview', 'seatmap', 'reservations', 'analytics'].forEach(t => {
      document.getElementById(`admin-panel-${t}`).classList.toggle('hidden', t !== tabName);
      const btn = document.getElementById(`admin-tab-${t}`);
      btn.className = t === tabName
        ? "pb-3 tab-active flex items-center space-x-2 whitespace-nowrap"
        : "pb-3 text-slate-500 hover:text-slate-700 flex items-center space-x-2 whitespace-nowrap";
    });
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
  renderAdminZoneSummaryBars();
  renderAdminSeatMap();
  renderAdminReservationsTable();
  renderProfSeatMap();
  renderProfSessions();
  renderStudentSeatMap(studentZoneFilter);
  renderStudentActiveBookings();
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
  if (noShows.length === 0) {
    container.innerHTML = `
      <div class="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-900 flex items-start space-x-2.5">
        <i class="fa-solid fa-circle-check text-emerald-600 text-base mt-0.5"></i>
        <div><span class="font-bold">All clear:</span>
          <p class="text-emerald-800 mt-0.5">No expired reservations are waiting for release.</p></div>
      </div>`;
    return;
  }
  container.innerHTML = noShows.map(s => `
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
  const reservedOrOccupied = seats.filter(s => s.status !== 'available');

  if (reservedOrOccupied.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="p-4 text-center text-slate-400">No active reservations recorded.</td></tr>`;
    return;
  }

  reservedOrOccupied.forEach(s => {
    let badge = '<span class="bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full text-[10px] font-bold">ACTIVE</span>';
    if (s.status === 'reserved') badge = '<span class="bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full text-[10px] font-bold">UPCOMING</span>';
    if (s.noShow) badge = '<span class="bg-rose-100 text-rose-800 px-2 py-0.5 rounded-full text-[10px] font-bold">EXPIRED</span>';
    if (s.status === 'maintenance') badge = '<span class="bg-slate-200 text-slate-800 px-2 py-0.5 rounded-full text-[10px] font-bold">BLOCKED</span>';

    tbody.innerHTML += `
      <tr class="hover:bg-slate-50 border-b border-slate-100">
        <td class="p-3 font-mono font-bold text-slate-600">#RES-${esc(s.id)}</td>
        <td class="p-3 font-bold text-navy-900">${esc(s.id)} <span class="text-slate-400 font-normal">(${esc(s.zone)})</span></td>
        <td class="p-3 font-medium text-slate-800">${esc(s.user || 'N/A')}</td>
        <td class="p-3 uppercase text-[10px] font-bold text-slate-500">${esc(s.role || 'System')}</td>
        <td class="p-3 text-slate-600">${esc(s.time || 'N/A')}</td>
        <td class="p-3">${badge}</td>
        <td class="p-3 text-right space-x-1">
          <button onclick="forceReleaseSeat('${esc(s.id)}')" class="bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 px-2 py-1 rounded text-[10px] font-semibold">Force Release</button>
          <button onclick="toggleMaintenance('${esc(s.id)}')" class="bg-slate-100 text-slate-700 hover:bg-slate-200 border border-slate-200 px-2 py-1 rounded text-[10px] font-semibold">${s.status === 'maintenance' ? 'Unblock' : 'Block'}</button>
        </td>
      </tr>
    `;
  });
  filterAdminReservations();   // keep any search/filter the admin typed
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
  const container = document.getElementById('prof-seatmap-grid');
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
  document.getElementById('prof-selected-count').innerText = selectedProfSeats.length;
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
  const profSeats = seats.filter(s => s.role === 'professor' && s.mine);
  document.getElementById('prof-session-count').innerText = profSeats.length;

  if (profSeats.length === 0) {
    container.innerHTML = `
      <div class="bg-white p-8 rounded-xl border border-slate-200 text-center space-y-2">
        <i class="fa-solid fa-folder-open text-slate-300 text-3xl"></i>
        <p class="text-xs text-slate-500">You currently have no active faculty seat reservations.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = profSeats.map(s => `
    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex items-center justify-between">
      <div class="space-y-1">
        <div class="flex items-center space-x-2">
          <span class="bg-royal-600 text-white font-mono font-bold text-xs px-2 py-0.5 rounded">${esc(s.id)}</span>
          <span class="font-bold text-navy-900 text-xs">${esc(s.zoneName)}</span>
          <span class="bg-emerald-100 text-emerald-800 text-[10px] font-bold px-2 py-0.5 rounded-full">ACTIVE SESSION</span>
        </div>
        <p class="text-xs text-slate-500">${esc(s.time)}</p>
      </div>
      <button onclick="forceReleaseSeat('${esc(s.id)}')" class="bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100 px-3 py-1.5 rounded-lg text-xs font-semibold">Release Seat</button>
    </div>
  `).join('');
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
    document.getElementById('single-rule-current-text').innerText = `Seat ${existing.id} • ${existing.zoneName} (${existing.time})`;
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
    const res = await callApi(seatUrl('release', existing.id));
    if (!res || !res.ok) return;
  }
  closeModal('modal-single-rule');
  openStudentBookingModal(targetStudentSeatToBook);
}

function renderStudentActiveBookings() {
  const container = document.getElementById('student-active-booking-container');
  const studentBooking = currentStudentBooking();

  document.getElementById('student-booking-badge').innerText = studentBooking ? '1' : '0';

  if (!studentBooking) {
    container.innerHTML = `
      <div class="bg-white p-8 rounded-xl border border-slate-200 text-center space-y-3">
        <i class="fa-solid fa-ticket text-slate-300 text-4xl"></i>
        <p class="text-sm font-semibold text-slate-700">No Active Seat Booking Found</p>
        <p class="text-xs text-slate-500 max-w-sm mx-auto">You do not have any active or upcoming reservations. Select a seat on the floorplan to book.</p>
        <button onclick="switchTab('student', 'find')" class="bg-royal-600 text-white text-xs font-bold px-4 py-2 rounded-xl hover:bg-royal-700">Browse Available Seats</button>
      </div>
    `;
    return;
  }

  const isOccupied = studentBooking.status === 'occupied';
  container.innerHTML = `
    <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-5">
      <div class="flex flex-wrap justify-between items-start border-b border-slate-100 pb-4 gap-2">
        <div>
          <span class="${isOccupied ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'} text-xs font-bold px-2.5 py-1 rounded-full uppercase tracking-wider">
            ${isOccupied ? 'ACTIVE SESSION' : 'RESERVED - PENDING CHECK-IN'}
          </span>
          <h3 class="text-2xl font-bold text-navy-900 mt-2">Seat ${esc(studentBooking.id)}</h3>
          <p class="text-xs text-slate-500">${esc(studentBooking.zoneName)}</p>
        </div>
        <div class="text-right">
          <span class="text-xs text-slate-400 block">Session Time:</span>
          <span class="text-sm font-bold text-slate-700">${esc(studentBooking.time)}</span>
        </div>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
        <div class="bg-slate-50 p-3 rounded-xl border border-slate-100">
          <span class="text-slate-400 block">Desk Features:</span>
          <span class="font-semibold text-slate-700">${esc(studentBooking.features)}</span>
        </div>
        <div class="bg-slate-50 p-3 rounded-xl border border-slate-100">
          <span class="text-slate-400 block">Check-In Window:</span>
          <span class="font-semibold text-emerald-600">15 min grace period</span>
        </div>
        <div class="bg-slate-50 p-3 rounded-xl border border-slate-100">
          <span class="text-slate-400 block">Automatic Release:</span>
          <span class="font-semibold text-slate-700">Enforced by Sensor</span>
        </div>
      </div>
      <div class="flex flex-wrap gap-3 pt-2">
        ${!isOccupied ? `
          <button onclick="openModal('modal-qr-checkin')" class="flex-1 bg-emerald-600 text-white hover:bg-emerald-700 font-bold py-2.5 rounded-xl text-xs flex items-center justify-center space-x-1.5 shadow-sm">
            <i class="fa-solid fa-qrcode"></i>
            <span>Scan Desk QR to Check-In</span>
          </button>
        ` : ''}
        <button onclick="forceReleaseSeat('${esc(studentBooking.id)}')" class="flex-1 bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100 font-bold py-2.5 rounded-xl text-xs flex items-center justify-center space-x-1.5">
          <i class="fa-solid fa-circle-xmark"></i>
          <span>${isOccupied ? 'End Session & Check-Out' : 'Cancel Reservation'}</span>
        </button>
      </div>
    </div>
  `;
}

async function executeQRCheckIn() {
  const booking = currentStudentBooking();
  if (!booking) {
    closeModal('modal-qr-checkin');
    return;
  }
  const res = await callApi(seatUrl('checkIn', booking.id));
  if (res && res.ok) closeModal('modal-qr-checkin');
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
  if (s.status === 'available' || s.status === 'maintenance') {
    toggleMaintenance(seatId);
  } else {
    forceReleaseSeat(seatId);
  }
}

/* ------------------------------------------------------------ modals & toast */
function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
}

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  let bg = 'bg-navy-900 text-white';
  if (type === 'success') bg = 'bg-emerald-600 text-white';
  if (type === 'warning') bg = 'bg-amber-500 text-white';
  if (type === 'info') bg = 'bg-royal-600 text-white';

  toast.className = `${bg} px-4 py-3 rounded-xl shadow-xl text-xs font-semibold flex items-center space-x-2 border border-white/20 transition-all transform translate-y-2 pointer-events-auto`;
  const icon = document.createElement('i');
  icon.className = 'fa-solid fa-circle-info';
  const text = document.createElement('span');
  text.textContent = message;          // textContent: never interpret server/user text as HTML
  toast.append(icon, text);
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}
