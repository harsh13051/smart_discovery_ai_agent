/* Table Scout single-page app. All untrusted text (restaurant names, tags, messages) is inserted with
   textContent / DOM APIs only - never innerHTML. */
(() => {
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const NS = 'http://www.w3.org/2000/svg';
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function icon(path, size = 18) {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', size); s.setAttribute('height', size);
  s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
  s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(NS, 'path'); p.setAttribute('d', path); s.append(p); return s;
}
const ICONS = {
  heart: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z',
  plus: 'M12 5v14M5 12h14', pencil: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z', trash: 'M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14',
  menu: 'M3 6h18M3 12h18M3 18h18', send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z', pin: 'M12 21s-7-6.1-7-11a7 7 0 0 1 14 0c0 4.9-7 11-7 11zM12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4',
};
const fmtDate = (iso) => {
  const d = new Date(iso), now = new Date(), days = Math.floor((now - d) / 864e5);
  if (days < 1 && d.getDate() === now.getDate()) return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (days < 7) return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
};
const ago = (iso) => {
  if (!iso) return null;
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (m < 2) return 'just now'; if (m < 90) return `${m} min ago`;
  const hr = Math.round(m / 60); if (hr < 36) return `${hr} h ago`; return `${Math.round(hr / 24)} days ago`;
};
const safeUrl = (u) => {
  if (!u) return null;
  try { const url = new URL(/^[a-z]+:/i.test(u) ? u : 'https://' + u); return /^https?:$/.test(url.protocol) ? url.href : null; }
  catch { return null; }
};
const nice = (s) => String(s).replace(/_/g, ' ');
const store = {
  get: (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const session = {
  get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* ignore */ } },
  del: (k) => { try { sessionStorage.removeItem(k); } catch { /* ignore */ } },
};

// ---------------------------------------------------------------------- api
class ApiError extends Error { constructor(status, message, fields) { super(message); this.status = status; this.fields = fields || {}; } }
const cookie = (n) => document.cookie.split('; ').find((c) => c.startsWith(n + '='))?.split('=')[1];
async function api(method, path, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (method !== 'GET') { opts.headers['X-CSRF-Token'] = cookie('csrf_token') || ''; }
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opts); } catch { throw new ApiError(0, 'Network error — check your connection and try again.'); }
  let data = null; try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    let msg = data?.detail ?? data?.message ?? `Request failed (${res.status})`;
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg).join('; ');
    if (res.status === 401 && state.user && !path.startsWith('/api/auth/')) { state.user = null; renderTopbar(); go('#/login'); }
    throw new ApiError(res.status, typeof msg === 'string' ? msg : 'Something went wrong.', data?.fields);
  }
  return data;
}
const state = { user: null, status: null, favs: new Set(), geo: null };
let compareIds = store.get('compare_ids', []);

function toast(msg, kind) {
  const t = h('div', { class: 'toast' + (kind === 'err' ? ' err' : ''), role: kind === 'err' ? 'alert' : null }, msg);
  $('#toasts').append(t); setTimeout(() => t.remove(), kind === 'err' ? 6000 : 3200);
}

// ----------------------------------------------------------------- modals
function modal(build) {
  const dlg = h('dialog', { 'aria-modal': 'true' });
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.append(h('div', { class: 'inner' }, build(close)));
  dlg.addEventListener('cancel', () => dlg.remove());
  document.body.append(dlg); dlg.showModal(); return dlg;
}
const confirmBox = (title, text, okLabel) => new Promise((resolve) => {
  modal((close) => [h('h3', {}, title), h('p', { class: 'muted' }, text),
    h('div', { class: 'actions' }, h('span', { class: 'grow' }),
      h('button', { class: 'btn', onclick: () => { close(); resolve(false); } }, 'Cancel'),
      h('button', { class: 'btn danger', onclick: () => { close(); resolve(true); } }, okLabel))]);
});
const askText = (title, value) => new Promise((resolve) => {
  modal((close) => {
    const input = h('input', { value, maxlength: 120, 'aria-label': title });
    const done = (v) => { close(); resolve(v); };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(input.value.trim() || null); });
    setTimeout(() => { input.focus(); input.select(); }, 30);
    return [h('h3', {}, title), input, h('div', { class: 'actions' }, h('span', { class: 'grow' }),
      h('button', { class: 'btn', onclick: () => done(null) }, 'Cancel'),
      h('button', { class: 'btn primary', onclick: () => done(input.value.trim() || null) }, 'Save'))];
  });
});

// ------------------------------------------------------------ restaurant UI
function toggleCompare(id) {
  const i = compareIds.indexOf(id);
  if (i >= 0) compareIds.splice(i, 1);
  else if (compareIds.length >= 4) { toast('You can compare up to 4 restaurants.', 'err'); return false; }
  else compareIds.push(id);
  store.set('compare_ids', compareIds); renderTray(); return true;
}
async function toggleFav(r, btn) {
  const on = state.favs.has(r.id);
  btn.disabled = true;
  try {
    if (on) { await api('DELETE', '/api/favourites?id=' + encodeURIComponent(r.id)); state.favs.delete(r.id); toast('Removed from favourites'); }
    else { await api('POST', '/api/favourites', { restaurant_id: r.id }); state.favs.add(r.id); toast('Saved to favourites'); }
    syncFavButtons(r.id);
  } catch (e) { toast(e.message, 'err'); } finally { btn.disabled = false; }
}
function syncFavButtons(id) {
  document.querySelectorAll(`[data-fav="${CSS.escape(id)}"]`).forEach((b) => {
    const on = state.favs.has(id); b.setAttribute('aria-pressed', on); b.classList.toggle('fav-on', on);
    b.setAttribute('aria-label', on ? 'Remove from favourites' : 'Save to favourites'); b.title = b.getAttribute('aria-label');
  });
}
function openStatus(r) {
  if (r.open_now === true) return h('span', { class: 'tag ok' }, 'Open now');
  if (r.open_now === false) return h('span', { class: 'tag warn' }, 'Closed now');
  return h('span', { class: 'tag' }, r.opening_hours ? 'Hours listed' : 'Hours unavailable');
}
const na = (t) => h('span', { class: 'na' }, t);
function priceFact(r) {
  if (r.cost_per_person == null) return na('Price unavailable');
  return h('span', {}, `≈ ₹${r.cost_per_person}/person `, h('span', { class: 'muted small' }, r.is_demo ? '(sample average)' : '(listed average)'));
}
function ratingFact(r) {
  if (r.rating == null) return na('Rating unavailable');
  return h('span', {}, `★ ${r.rating}`, r.review_count ? ` (${r.review_count} reviews)` : '', r.is_demo ? ' · sample' : '');
}
function restaurantCard(r, opts = {}) {
  const src = safeUrl(r.source_url), web = safeUrl(r.website);
  const amen = Object.entries(r.amenities || {}).filter(([, v]) => v === true).map(([k]) => h('span', { class: 'tag' }, nice(k)));
  const favBtn = h('button', { class: 'btn sm icon-btn', 'data-fav': r.id, onclick: (e) => toggleFav(r, e.currentTarget) }, icon(ICONS.heart));
  const cmpBox = h('input', { type: 'checkbox', checked: compareIds.includes(r.id), id: 'cmp-' + r.id.replace(/\W/g, '_'),
    onchange: (e) => { if (!toggleCompare(r.id)) e.target.checked = false; } });
  const card = h('article', { class: 'card rcard' + (opts.compact ? ' compact' : ''), 'aria-label': r.name },
    h('div', { class: 'rhead' },
      h('div', {},
        h('h3', { class: 'rname' }, h('button', { onclick: () => openDetail(r.id) }, r.name)),
        h('div', { class: 'muted small' }, [r.kind && nice(r.kind), r.address || r.locality].filter(Boolean).join(' · ') || 'Address unavailable')),
      r.score != null && h('div', { class: 'score', title: 'Relevance to your request (not just rating)' }, Math.round(r.score), h('small', {}, 'match'))),
    h('div', { class: 'facts' }, ratingFact(r), priceFact(r), openStatus(r),
      r.distance_km != null && h('span', {}, `${r.distance_km} km away`)),
    h('div', { class: 'facts' }, (r.cuisines || []).length ? r.cuisines.slice(0, 5).map((c) => h('span', { class: 'tag' }, nice(c))) : na('Cuisine not listed'),
      amen.slice(0, 5), r.is_demo && h('span', { class: 'tag demo' }, 'DEMO DATA')),
    (r.reasons || []).length > 0 && h('ul', { class: 'why', 'aria-label': 'Why this matches' }, r.reasons.map((x) => h('li', {}, x))),
    (r.trade_offs || []).length > 0 && h('ul', { class: 'why trade', 'aria-label': 'Trade-offs and unverified details' }, r.trade_offs.map((x) => h('li', {}, x))),
    h('div', { class: 'actions' },
      h('button', { class: 'btn sm', onclick: () => openDetail(r.id) }, 'Details'),
      h('label', { class: 'check small', for: cmpBox.id, style: 'margin:0' }, cmpBox, 'Compare'),
      h('span', { class: 'grow' }), favBtn),
    h('div', { class: 'muted small', style: 'margin-top:8px' },
      `Source: ${r.is_demo ? 'demo data (fictional)' : (r.source_name || 'unknown')}`, r.fetched_at && !r.is_demo && ` · retrieved ${ago(r.fetched_at)}`,
      src && [' · ', h('a', { href: src, target: '_blank', rel: 'noopener noreferrer' }, 'Original listing')],
      web && [' · ', h('a', { href: web, target: '_blank', rel: 'noopener noreferrer' }, 'Website')]));
  syncFavButtonsLater(favBtn, r.id);
  return card;
}
function syncFavButtonsLater(btn, id) { queueMicrotask(() => syncFavButtons(id)); }

async function openDetail(id) {
  const dlg = h('dialog', { 'aria-label': 'Restaurant details' });
  dlg.append(h('div', { class: 'inner' }, h('div', { class: 'skel' })));
  document.body.append(dlg); dlg.showModal();
  const close = () => { dlg.close(); dlg.remove(); };
  dlg.addEventListener('cancel', () => dlg.remove());
  try {
    const { restaurant: r } = await api('GET', '/api/restaurant?id=' + encodeURIComponent(id));
    if (r.is_favourite) state.favs.add(r.id);
    const rows = [];
    const add = (k, v) => v && rows.push(h('dt', {}, k), h('dd', {}, v));
    add('Address', r.address || null); add('Type', r.kind && nice(r.kind));
    add('Cuisine', (r.cuisines || []).map(nice).join(', ') || null);
    add('Diet', [r.vegetarian && `vegetarian: ${r.vegetarian}`, r.vegan && `vegan: ${r.vegan}`, r.jain && `jain: ${r.jain}`].filter(Boolean).join(' · ') || null);
    const am = Object.entries(r.amenities || {}); add('Amenities', am.length ? am.map(([k, v]) => `${nice(k)}: ${v ? 'yes' : 'no'}`).join(' · ') : null);
    add('Opening hours', r.opening_hours ? [r.opening_hours, ' ', openStatus(r)] : null);
    add('Phone', r.phone);
    const web = safeUrl(r.website), menu = safeUrl(r.menu_url);
    add('Website', web && h('a', { href: web, target: '_blank', rel: 'noopener noreferrer' }, web));
    add('Menu', menu && h('a', { href: menu, target: '_blank', rel: 'noopener noreferrer' }, 'Open menu'));
    if (r.lat != null && r.lon != null) add('Map', h('a', { href: `https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lon}#map=18/${r.lat}/${r.lon}`, target: '_blank', rel: 'noopener noreferrer' }, 'View location on OpenStreetMap'));
    const src = safeUrl(r.source_url);
    const favBtn = h('button', { class: 'btn', 'data-fav': r.id, onclick: (e) => toggleFav(r, e.currentTarget) }, icon(ICONS.heart), 'Favourite');
    dlg.replaceChildren(h('div', { class: 'inner' },
      h('div', { class: 'rhead' }, h('h2', {}, r.name), h('button', { class: 'btn sm', onclick: close, 'aria-label': 'Close' }, 'Close')),
      r.is_demo && h('div', { class: 'banner warn' }, 'DEMO DATA — this is a fictional restaurant used to demonstrate the app.'),
      h('div', { class: 'facts' }, ratingFact(r), priceFact(r)),
      h('dl', { class: 'kv' }, rows),
      h('p', { class: 'muted small' }, 'Fields shown as missing were not provided by the data source. This app cannot make reservations or confirm table availability — please contact the restaurant directly.'),
      h('p', { class: 'muted small' }, `Source: ${r.is_demo ? 'demo data' : r.source_name}`, r.fetched_at && !r.is_demo && ` · retrieved ${ago(r.fetched_at)}`,
        src && [' · ', h('a', { href: src, target: '_blank', rel: 'noopener noreferrer' }, 'Original listing')]),
      h('div', { class: 'actions' }, favBtn,
        h('button', { class: 'btn', onclick: () => { if (toggleCompare(r.id)) toast('Added to comparison'); } }, 'Add to compare'))));
    syncFavButtons(r.id);
  } catch (e) {
    dlg.replaceChildren(h('div', { class: 'inner' }, h('div', { class: 'banner err' }, e.message), h('button', { class: 'btn', onclick: close }, 'Close')));
  }
}

function sourceNote(source) {
  if (!source || !source.provider) return null;
  return h('div', { class: 'muted small' }, `Data: ${source.provider}`, source.location && ` · ${String(source.location).split(',').slice(0, 2).join(',')}`,
    source.fetched_at && ` · retrieved ${ago(source.fetched_at)}`, source.from_cache && ' (cached)');
}
const skeletons = (n = 3) => Array.from({ length: n }, () => h('div', { class: 'skel', 'aria-hidden': 'true' }));

// -------------------------------------------------------------- top bar/tray
function renderTopbar() {
  const bar = $('#topbar'); const cur = (location.hash.replace(/^#\//, '').split('/')[0]) || '';
  const link = (href, label, key) => h('a', { href, 'aria-current': cur === key ? 'page' : null }, label);
  bar.replaceChildren(
    h('a', { class: 'brand', href: '#/' }, h('span', { class: 'logo' }, (() => { const s = icon('M12 3v8M8 3v5a4 4 0 0 0 8 0V3M12 11v10', 18); s.style.color = '#fff'; return s; })()), 'Table Scout'),
    h('nav', { class: 'nav', 'aria-label': 'Main' }, state.user ? [link('#/chat', 'Chat', 'chat'),
      link('#/favourites', 'Favourites', 'favourites'), link('#/compare', compareIds.length ? `Compare (${compareIds.length})` : 'Compare', 'compare')] : []),
    h('div', { class: 'user-box' }, state.user
      ? [h('a', { href: '#/profile', class: 'name muted', style: 'text-decoration:none' }, state.user.name),
         h('button', { class: 'btn sm', onclick: logout }, 'Log out')]
      : [h('a', { class: 'btn sm', href: '#/login' }, 'Log in'), h('a', { class: 'btn sm primary', href: '#/register' }, 'Sign up')]));
}
function renderTray() {
  renderTopbar();
  const t = $('#compare-tray');
  if (!compareIds.length || !state.user) { t.hidden = true; return; }
  t.hidden = false;
  t.replaceChildren(h('span', {}, `${compareIds.length} selected to compare`),
    h('a', { class: 'btn sm', href: '#/compare' }, 'Compare'),
    h('button', { class: 'btn sm', onclick: () => { compareIds = []; store.set('compare_ids', []); renderTray(); document.querySelectorAll('input[id^=cmp-]').forEach((c) => (c.checked = false)); } }, 'Clear'));
}
async function logout() {
  try { await api('POST', '/api/auth/logout'); } catch { /* still clear local state */ }
  state.user = null; state.favs = new Set(); session.del('pendingMsg');
  renderTopbar(); renderTray(); go('#/'); toast('Logged out');
}

// ------------------------------------------------------------------- router
const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };
const main = () => $('#main');
const PROTECTED = ['discover', 'chat', 'favourites', 'compare', 'profile'];
async function render() {
  const [route, arg] = (location.hash.replace(/^#\//, '') || '').split('/');
  renderTopbar(); document.body.classList.toggle('in-chat', route === 'chat');
  if (PROTECTED.includes(route) && !state.user) { session.set('next', location.hash); return go('#/login'); }
  if ((route === 'login' || route === 'register' || route === '' || route === 'discover') && state.user) return go('#/chat');
  main().replaceChildren(); main().className = '';
  const views = { '': landing, login: () => authView('login'), register: () => authView('register'), discover, chat: () => chat(arg),
    favourites, compare, profile };
  (views[route] || notFound)();
  document.title = 'Table Scout'; if (route !== 'chat') window.scrollTo(0, 0);
}
function notFound() { main().append(h('div', { class: 'page empty' }, h('h3', {}, 'Page not found'), h('a', { class: 'btn', href: '#/' }, 'Go home'))); }

// ------------------------------------------------------------------ landing
function landing() {
  const input = h('input', { type: 'text', placeholder: 'e.g. Vegetarian dinner for two in Baner under ₹1,000', 'aria-label': 'Describe the restaurant you want', maxlength: 500 });
  const pick = { loc: null, budget: null, cuisine: null };
  const groups = [
    ['Popular Pune areas', 'loc', ['Baner', 'Koregaon Park', 'Kothrud', 'Hinjewadi', 'Viman Nagar']],
    ['Budget', 'budget', ['under ₹500 per person', 'under ₹1,000 total for two', 'under ₹2,000 total for four']],
    ['Cuisine', 'cuisine', ['North Indian', 'Italian', 'Café', 'Chinese', 'South Indian', 'Vegetarian']],
  ];
  const compose = () => {
    const parts = ['Find', pick.cuisine ? pick.cuisine + (pick.cuisine === 'Vegetarian' ? ' restaurants' : ' food') : 'a good restaurant'];
    if (pick.loc) parts.push('in ' + pick.loc + ', Pune'); if (pick.budget) parts.push(pick.budget);
    input.value = parts.join(' ');
  };
  const chipRows = groups.map(([label, key, opts]) => [h('div', { class: 'chip-label' }, label),
    h('div', { class: 'chips', role: 'group', 'aria-label': label }, opts.map((o) => {
      const b = h('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', onclick: () => {
        pick[key] = pick[key] === o ? null : o;
        b.parentElement.querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', c === b && pick[key] ? 'true' : 'false')); compose(); } }, o);
      return b; }))]);
  const submit = async (e) => {
    e.preventDefault(); const text = input.value.trim(); if (!text) { input.focus(); return; }
    if (!state.user) { session.set('pendingAsk', text); toast('Create a free account or log in to search.'); return go('#/register'); }
    await startChat(text);
  };
  main().append(h('div', { class: 'page' },
    h('section', { class: 'hero' },
      h('h1', {}, 'Tell us what you’re craving. We’ll find where to eat.'),
      h('p', { class: 'lead' }, 'An AI agent searches real restaurant data, applies your budget, diet and preferences, and explains why each place made the list — including what it couldn’t verify.'),
      h('form', { class: 'card ask', onsubmit: submit }, input, h('button', { class: 'btn primary', type: 'submit' }, icon(ICONS.send, 16), 'Search')),
      chipRows),
    h('section', { 'aria-labelledby': 'how' }, h('h2', { id: 'how', style: 'margin-top:40px' }, 'How the assistant works'),
      h('div', { class: 'steps' }, [
        ['1. Understands', 'A language model reads your request and extracts location, budget (per person or group), cuisine and diet.'],
        ['2. Calls tools', 'It runs real search and filter tools against live OpenStreetMap data — no hard-coded lists.'],
        ['3. Ranks transparently', 'Ordinary code scores relevance to what you asked for, not just ratings, and flags anything unverified.'],
        ['4. Explains', 'You get the reasons and trade-offs for each pick, with a link to the original listing.'],
      ].map(([t, d]) => h('div', { class: 'card step' }, h('h3', {}, t), h('p', { class: 'muted', style: 'margin:0' }, d))))),
    h('p', { class: 'note' }, 'Restaurant data comes from OpenStreetMap contributors. OpenStreetMap does not include prices or ratings, so budget and rating requests can only be verified when a listing provides them; otherwise results are labelled unverified. Estimated costs are never a guarantee of your final bill. This app cannot make reservations.')));
}
async function startChat(text) {
  try { const c = await api('POST', '/api/conversations'); session.set('pendingMsg', JSON.stringify({ id: c.id, text })); go('#/chat/' + c.id); }
  catch (e) { toast(e.message, 'err'); }
}

// --------------------------------------------------------------- auth views
function authView(mode) {
  const reg = mode === 'register';
  const err = {}; const banner = h('div', { role: 'alert' });
  const f = (name, label, type, extra = {}) => { err[name] = h('div', { class: 'err', id: `${mode}-${name}-err` });
    return h('div', { class: 'field' }, h('label', { for: `${mode}-${name}` }, label), h('input', { id: `${mode}-${name}`, name, type, required: true, 'aria-describedby': `${mode}-${name}-err`, ...extra }), err[name]); };
  const btn = h('button', { class: 'btn primary', type: 'submit', style: 'width:100%' }, reg ? 'Create account' : 'Log in');
  const form = h('form', { novalidate: true, onsubmit: async (e) => {
    e.preventDefault(); Object.values(err).forEach((x) => (x.textContent = '')); banner.replaceChildren();
    const data = Object.fromEntries(new FormData(form)); btn.disabled = true;
    try {
      const res = await api('POST', `/api/auth/${mode}`, data); state.user = res.user; await loadFavs(); renderTray();
      toast(reg ? 'Welcome aboard!' : 'Welcome back!');
      const ask = session.get('pendingAsk'); if (ask) { session.del('pendingAsk'); return startChat(ask); }
      const next = session.get('next'); session.del('next'); go(next || '#/chat');
    } catch (ex) {
      for (const [k, v] of Object.entries(ex.fields || {})) if (err[k]) err[k].textContent = v;
      banner.replaceChildren(h('div', { class: 'banner err' }, ex.message));
    } finally { btn.disabled = false; }
  } }, banner, reg && f('name', 'Name', 'text', { autocomplete: 'name', maxlength: 80 }),
    f('email', 'Email', 'email', { autocomplete: 'email' }),
    f('password', 'Password', 'password', { autocomplete: reg ? 'new-password' : 'current-password' }),
    reg && h('p', { class: 'muted small' }, 'At least 8 characters, mixing letters and numbers.'), btn);
  // show/hide password toggle
  const pw = $('input[type=password]', form);
  const eye = h('button', { type: 'button', class: 'pw-toggle', 'aria-label': 'Show password', 'aria-pressed': 'false', onclick: () => {
    const show = pw.type === 'password'; pw.type = show ? 'text' : 'password';
    eye.textContent = show ? 'Hide' : 'Show'; eye.setAttribute('aria-pressed', String(show)); eye.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); } }, 'Show');
  pw.parentElement.classList.add('pw-wrap'); pw.after(eye);
  const perks = [['Real restaurant data', 'Searches live OpenStreetMap listings. Nothing is made up, and missing details are labelled.'],
    ['Budget-aware', 'Per-person or group budgets, diet and amenity filters, with every pick explained.'],
    ['Your history, saved', 'Conversations and favourites are kept in your account across devices.']];
  main().append(h('div', { class: 'auth-shell' },
    h('aside', { class: 'auth-brand', 'aria-hidden': 'false' },
      h('a', { class: 'brand light', href: '#/' }, h('span', { class: 'logo' }, icon('M12 3v8M8 3v5a4 4 0 0 0 8 0V3M12 11v10', 18)), 'Table Scout'),
      h('h1', {}, 'Find the right table, without the guesswork.'),
      h('ul', { class: 'perks' }, perks.map(([t, d]) => h('li', {}, h('b', {}, t), h('span', {}, d))))),
    h('section', { class: 'auth-panel' },
      h('div', { class: 'seg', role: 'tablist', 'aria-label': 'Account' },
        h('a', { href: '#/login', role: 'tab', 'aria-selected': String(!reg) }, 'Log in'),
        h('a', { href: '#/register', role: 'tab', 'aria-selected': String(reg) }, 'Sign up')),
      h('h2', {}, reg ? 'Create your account' : 'Welcome back'),
      h('p', { class: 'muted' }, reg ? 'Save chats and favourites and get personalised defaults.' : 'Log in to continue your conversations.'),
      form,
      h('p', { class: 'muted small', style: 'margin-top:16px' }, reg ? 'Already registered? ' : 'New here? ',
        h('a', { href: reg ? '#/login' : '#/register' }, reg ? 'Log in' : 'Create an account')))));
  setTimeout(() => $('input', form)?.focus(), 30);
}
async function loadFavs() { try { const r = await api('GET', '/api/favourites'); state.favs = new Set(r.items.map((i) => i.restaurant_id)); } catch { /* non-fatal */ } }

// ----------------------------------------------------------------- discover
const CUISINES = ['north_indian', 'south_indian', 'chinese', 'italian', 'cafe', 'mughlai', 'biryani', 'japanese', 'continental', 'street_food', 'desserts'];
const AMEN = ['wifi', 'outdoor_seating', 'parking', 'wheelchair', 'takeaway', 'delivery', 'air_conditioning'];
function discover() {
  const saved = store.get('discover_form', {});
  const sel = new Set(saved.cuisines || []), amSel = new Set(saved.amenities || []);
  const loc = h('input', { id: 'f-loc', placeholder: 'e.g. Baner, Pune', value: saved.location || '', maxlength: 200 });
  const geoNote = h('div', { class: 'muted small', 'aria-live': 'polite' });
  const geoBtn = h('button', { type: 'button', class: 'btn sm', onclick: () => {
    if (!navigator.geolocation) return toast('Location is not supported by this browser.', 'err');
    geoBtn.disabled = true;
    navigator.geolocation.getCurrentPosition((p) => { state.geo = { lat: p.coords.latitude, lon: p.coords.longitude }; geoNote.textContent = 'Using your current location.'; geoBtn.disabled = false; },
      () => { geoNote.textContent = 'Location permission denied — type a place instead.'; geoBtn.disabled = false; }, { timeout: 10000 });
  } }, icon(ICONS.pin, 14), 'Use my location');
  const budget = h('input', { id: 'f-budget', type: 'number', min: 1, max: 1000000, placeholder: '₹', value: saved.budget || '' });
  const btype = h('select', { id: 'f-btype', 'aria-label': 'Budget type' }, h('option', { value: 'total' }, 'Total (group)'), h('option', { value: 'per_person' }, 'Per person'));
  btype.value = saved.btype || 'total';
  const party = h('input', { id: 'f-party', type: 'number', min: 1, max: 50, placeholder: 'People', value: saved.party || '' });
  const diet = h('select', { id: 'f-diet' }, ['any', 'vegetarian', 'vegan', 'jain'].map((d) => h('option', { value: d }, d === 'any' ? 'No preference' : d)));
  diet.value = saved.diet || state.user?.preferences?.diet || 'any';
  const rating = h('select', { id: 'f-rating' }, h('option', { value: '' }, 'Any'), ['3.5', '4', '4.5'].map((v) => h('option', { value: v }, v + '+')));
  rating.value = saved.rating || '';
  const radius = h('select', { id: 'f-radius' }, ['1', '2.5', '5', '8'].map((v) => h('option', { value: v }, v + ' km'))); radius.value = saved.radius || '2.5';
  const openNow = h('input', { type: 'checkbox', id: 'f-open', checked: !!saved.open });
  const demo = h('input', { type: 'checkbox', id: 'f-demo', checked: !!saved.demo });
  const chips = (set, items, label) => h('div', { class: 'chips', role: 'group', 'aria-label': label }, items.map((c) => {
    const b = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(set.has(c)), onclick: () => { set.has(c) ? set.delete(c) : set.add(c); b.setAttribute('aria-pressed', set.has(c)); } }, nice(c)); return b; }));
  const out = h('section', { class: 'results', 'aria-live': 'polite', 'aria-label': 'Search results' });
  let page = 1, busy = false;
  const run = async (append) => {
    if (busy) return; if (!loc.value.trim() && !state.geo) { loc.focus(); return toast('Enter a location or use your current location.', 'err'); }
    busy = true; submitBtn.disabled = true; if (!append) { page = 1; out.replaceChildren(...skeletons()); }
    const q = new URLSearchParams({ diet: diet.value, budget_type: btype.value, radius_km: radius.value, page, page_size: 10 });
    if (loc.value.trim()) q.set('location', loc.value.trim()); else { q.set('lat', state.geo.lat); q.set('lon', state.geo.lon); }
    if (budget.value) q.set('budget', budget.value); if (party.value) q.set('party', party.value);
    if (sel.size) q.set('cuisines', [...sel].join(',')); if (amSel.size) q.set('amenities', [...amSel].join(','));
    if (rating.value) q.set('min_rating', rating.value); if (openNow.checked) q.set('open_now', 'true'); if (demo.checked) q.set('demo_data', 'true');
    store.set('discover_form', { location: loc.value, budget: budget.value, btype: btype.value, party: party.value, diet: diet.value, rating: rating.value,
      radius: radius.value, open: openNow.checked, demo: demo.checked, cuisines: [...sel], amenities: [...amSel] });
    try {
      const r = await api('GET', '/api/restaurants/search?' + q);
      if (append) out.querySelector('.more')?.remove(); else out.replaceChildren();
      if (!append) {
        out.append(h('div', { class: r.status === 'ok' ? 'banner info' : 'banner warn' }, r.message), ...(r.notes || []).map((n) => h('div', { class: 'banner warn small' }, n)), sourceNote(r.source));
      }
      if (!r.cards.length && !append) out.append(h('div', { class: 'card empty' }, h('h3', {}, 'No matches'), h('p', {}, 'Try a larger radius, fewer filters, or a nearby area.')));
      r.cards.forEach((c) => out.append(restaurantCard(c)));
      if (r.has_more) out.append(h('button', { class: 'btn more', onclick: () => { page += 1; run(true); } }, 'Load more'));
    } catch (e) { if (!append) out.replaceChildren(); out.append(h('div', { class: 'banner err', role: 'alert' }, e.message)); }
    finally { busy = false; submitBtn.disabled = false; }
  };
  const submitBtn = h('button', { class: 'btn primary', type: 'submit', style: 'width:100%' }, 'Search restaurants');
  const form = h('form', { class: 'card filters', novalidate: true, onsubmit: (e) => { e.preventDefault(); run(false); } },
    h('h2', { style: 'font-size:1.15rem' }, 'Find restaurants'),
    h('div', { class: 'field' }, h('label', { for: 'f-loc' }, 'Where?'), loc, h('div', { style: 'margin-top:8px' }, geoBtn), geoNote),
    h('div', { class: 'field' }, h('label', { for: 'f-budget' }, 'Budget'), h('div', { class: 'row' }, budget, btype), h('div', { style: 'margin-top:8px' }, party)),
    h('div', { class: 'field' }, h('label', { for: 'f-diet' }, 'Diet'), diet),
    h('fieldset', {}, h('legend', {}, 'Cuisine'), chips(sel, CUISINES, 'Cuisine')),
    h('fieldset', {}, h('legend', {}, 'Must have'), chips(amSel, AMEN, 'Amenities')),
    h('div', { class: 'row field' }, h('div', {}, h('label', { for: 'f-rating' }, 'Min rating'), rating), h('div', {}, h('label', { for: 'f-radius' }, 'Radius'), radius)),
    h('label', { class: 'check', for: 'f-open' }, openNow, 'Open now (when hours are listed)'),
    state.status?.demo_data_available && h('label', { class: 'check', for: 'f-demo' }, demo, 'Use DEMO data (fictional)'),
    h('div', { style: 'margin-top:14px' }, submitBtn));
  out.append(h('div', { class: 'card empty' }, h('h3', {}, 'Search real restaurants'), h('p', {}, 'Enter a place and your preferences. Prices and ratings appear only when the data source has them.')));
  main().append(h('div', { class: 'page' }, h('div', { class: 'discover' }, form, out)));
}

// --------------------------------------------------------------------- chat
function chat(cid) {
  main().className = 'chat-host';
  const side = h('aside', { class: 'side', 'aria-label': 'Conversations' });
  const thread = h('div', { class: 'thread', tabindex: '0', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation' });
  const titleEl = h('div', { class: 'muted small', style: 'padding:10px clamp(14px,4vw,48px) 0;display:flex;gap:10px;align-items:center' });
  const ta = h('textarea', { rows: 1, placeholder: 'Ask about restaurants, budgets, cuisines…', 'aria-label': 'Message', maxlength: 500 });
  const sendBtn = h('button', { class: 'btn primary', type: 'submit', 'aria-label': 'Send' }, icon(ICONS.send, 16));
  const useGeo = h('input', { type: 'checkbox', id: 'c-geo' }); const useDemo = h('input', { type: 'checkbox', id: 'c-demo' });
  let geo = null, active = cid || null, loadingOlder = false, sending = false, listState = { items: [], hasMore: false, q: '' };

  useGeo.addEventListener('change', () => {
    if (!useGeo.checked) { geo = null; return; }
    if (!navigator.geolocation) { useGeo.checked = false; return toast('Location is not supported by this browser.', 'err'); }
    navigator.geolocation.getCurrentPosition((p) => { geo = { lat: p.coords.latitude, lon: p.coords.longitude }; toast('Using your location for “near me”'); },
      () => { useGeo.checked = false; toast('Location permission denied.', 'err'); }, { timeout: 10000 });
  });
  const form = h('form', { onsubmit: (e) => { e.preventDefault(); send(); } },
    h('div', { class: 'card box' }, ta, sendBtn),
    h('div', { class: 'opts' }, h('label', { for: 'c-geo' }, useGeo, 'Share my location for “near me”'),
      state.status?.demo_data_available && h('label', { for: 'c-demo' }, useDemo, 'Use DEMO data (fictional)'),
      h('span', {}, state.status?.llm?.configured ? `AI model: ${state.status.llm.model}` : 'AI model not configured — rule-based fallback active')));
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; });
  const menuBtn = h('button', { class: 'btn sm menu-btn', 'aria-label': 'Toggle conversation list', onclick: () => side.classList.toggle('open') }, icon(ICONS.menu, 16));
  titleEl.append(menuBtn);
  const titleText = h('span', { style: 'font-weight:600;color:var(--text)' }, ''); titleEl.append(titleText);
  main().replaceChildren(h('div', { class: 'chat' }, side, h('section', { class: 'conv-main' }, titleEl, thread, h('div', { class: 'composer' }, form))));

  // sidebar ---------------------------------------------------------------
  const listEl = h('ul', { class: 'conv-list' });
  const search = h('input', { type: 'search', placeholder: 'Search conversations', 'aria-label': 'Search conversations' });
  let timer; search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => loadList(true), 300); });
  const newBtn = h('button', { class: 'btn primary', onclick: newChat }, icon(ICONS.plus, 16), 'New chat');
  side.append(h('div', { class: 'top' }, newBtn, search), listEl);
  async function newChat() {
    newBtn.disabled = true;
    try { const c = await api('POST', '/api/conversations'); listState.items.unshift(c); side.classList.remove('open'); go('#/chat/' + c.id); }
    catch (e) { toast(e.message, 'err'); } finally { newBtn.disabled = false; }
  }
  function drawList() {
    listEl.replaceChildren();
    if (!listState.items.length) listEl.append(h('li', { class: 'empty small' }, listState.q ? 'No conversations match your search.' : 'No conversations yet. Click “New chat” to start.'));
    for (const c of listState.items) {
      listEl.append(h('li', { class: 'conv', 'aria-current': c.id === active ? 'true' : null },
        h('a', { href: '#/chat/' + c.id, onclick: () => side.classList.remove('open') }, h('span', { class: 't' }, c.title), h('span', { class: 'd' }, fmtDate(c.updated_at))),
        h('span', { class: 'acts' },
          h('button', { class: 'btn ghost icon-btn', 'aria-label': 'Rename conversation ' + c.title, onclick: async () => {
            const t = await askText('Rename conversation', c.title); if (!t) return;
            try { const u = await api('PATCH', '/api/conversations/' + c.id, { title: t }); c.title = u.title; drawList(); if (c.id === active) titleText.textContent = u.title; toast('Renamed'); } catch (e) { toast(e.message, 'err'); } } }, icon(ICONS.pencil, 15)),
          h('button', { class: 'btn ghost icon-btn', 'aria-label': 'Delete conversation ' + c.title, onclick: async () => {
            if (!(await confirmBox('Delete this conversation?', 'This permanently deletes it and its messages. Other conversations are not affected.', 'Delete'))) return;
            try { await api('DELETE', '/api/conversations/' + c.id); listState.items = listState.items.filter((x) => x.id !== c.id); toast('Conversation deleted');
              if (c.id === active) go('#/chat'); else drawList(); } catch (e) { toast(e.message, 'err'); } } }, icon(ICONS.trash, 15)))));
    }
    if (listState.hasMore) listEl.append(h('li', {}, h('button', { class: 'btn sm', style: 'width:100%', onclick: () => loadList(false) }, 'Load more')));
  }
  async function loadList(reset) {
    const q = search.value.trim(); const p = new URLSearchParams({ limit: 25 }); if (q) p.set('q', q);
    if (!reset && listState.items.length) p.set('before', listState.items[listState.items.length - 1].updated_at);
    try { const r = await api('GET', '/api/conversations?' + p);
      listState = { items: reset ? r.items : listState.items.concat(r.items), hasMore: r.has_more, q }; drawList(); }
    catch (e) { listEl.replaceChildren(h('li', { class: 'banner err small' }, e.message)); }
  }

  // messages --------------------------------------------------------------
  function assistantBody(m) {
    const p = m.payload || {}; const body = h('div', {});
    body.append(h('div', { class: 'bubble' }, m.content));
    if (p.alternatives) body.append(h('div', { class: 'banner warn small' }, 'These are closest alternatives, not exact matches.'));
    if (p.cards?.length) body.append(h('div', { class: 'cards' }, p.cards.map((c) => restaurantCard(c, { compact: true }))));
    if (p.comparison) body.append(h('div', { class: 'cards' }, comparisonTable(p.comparison)));
    (p.notes || []).forEach((n) => body.append(h('div', { class: 'muted small', style: 'margin-top:6px' }, 'ℹ ' + n)));
    const sn = sourceNote(p.source); if (sn) body.append(h('div', { style: 'margin-top:6px' }, sn));
    const meta = h('div', { class: 'muted small', style: 'margin-top:6px' },
      h('span', { class: 'tag ' + (p.mode === 'llm' ? 'ok' : 'warn') }, p.mode === 'llm' ? 'AI model answer' : 'Rule-based answer'),
      p.fallback_reason && ` ${p.fallback_reason}`);
    body.append(meta);
    if (p.trace?.length) body.append(h('details', { class: 'trace' }, h('summary', {}, `How this was produced (${p.trace.length} tool calls)`),
      h('p', { class: 'muted small' }, 'Tool calls are ordinary application code (search, filtering, budget maths, ranking). The model only chooses tools and writes the summary.'),
      h('ol', {}, p.trace.map((t) => h('li', {}, h('code', {}, t.tool), ' — ', t.summary || '', t.ok === false && ' (failed)')))));
    return body;
  }
  function msgEl(m) {
    return h('div', { class: 'msg ' + m.role, 'data-id': m.id }, h('div', { class: 'who' }, m.role === 'user' ? 'You' : 'Table Scout'),
      m.role === 'user' ? h('div', { class: 'bubble' }, m.content) : assistantBody(m));
  }
  const scrollEnd = () => { thread.scrollTop = thread.scrollHeight; };
  async function openConversation(id) {
    thread.replaceChildren(...skeletons(2));
    try {
      const [c, msgs] = await Promise.all([api('GET', '/api/conversations/' + id), api('GET', `/api/conversations/${id}/messages?limit=30`)]);
      titleText.textContent = c.title; thread.replaceChildren();
      if (msgs.has_more) thread.append(olderBtn(id));
      if (!msgs.items.length) thread.append(emptyChat());
      msgs.items.forEach((m) => thread.append(msgEl(m))); scrollEnd();
      document.title = c.title + ' · Table Scout';
      const pending = JSON.parse(session.get('pendingMsg') || 'null');
      if (pending && pending.id === id) { session.del('pendingMsg'); ta.value = pending.text; send(); } else ta.focus();
    } catch (e) {
      thread.replaceChildren(h('div', { class: 'empty' }, h('h3', {}, e.status === 404 ? 'Conversation not found' : 'Could not load conversation'), h('p', {}, e.message), h('a', { class: 'btn', href: '#/chat' }, 'Back to chats')));
      titleText.textContent = '';
    }
  }
  const olderBtn = (id) => h('div', { style: 'text-align:center;margin-bottom:16px', class: 'older' }, h('button', { class: 'btn sm', onclick: async (e) => {
    if (loadingOlder) return; loadingOlder = true; const first = thread.querySelector('.msg'); const before = first?.dataset.id; e.currentTarget.disabled = true;
    try { const r = await api('GET', `/api/conversations/${id}/messages?limit=30&before_id=${before}`); const prevH = thread.scrollHeight;
      thread.querySelector('.older').remove(); const frag = document.createDocumentFragment(); r.items.forEach((m) => frag.append(msgEl(m)));
      if (r.has_more) thread.prepend(frag), thread.prepend(olderBtn(id)); else thread.prepend(frag); thread.scrollTop = thread.scrollHeight - prevH; }
    catch (er) { toast(er.message, 'err'); e.currentTarget.disabled = false; } finally { loadingOlder = false; } } }, 'Load earlier messages'));
  const emptyChat = () => h('div', { class: 'empty', id: 'empty-chat' }, h('h3', {}, 'What are you in the mood for?'), h('p', { class: 'muted' }, 'Ask in plain language: place, budget, cuisine, diet, group size. I\u2019ll search real restaurant data, save favourites and compare picks for you right here.'),
    h('div', { class: 'chips', style: 'justify-content:center' }, ['Find a good restaurant in Baner, Pune for dinner under ₹1,000 for two', 'Vegetarian restaurants with outdoor seating in Koregaon Park',
      'Quiet café with Wi-Fi in Kothrud, budget ₹500', 'Romantic restaurant for an anniversary in Viman Nagar', 'Best North Indian near me'].map((s) => h('button', { class: 'chip', onclick: () => { ta.value = s; ta.focus(); } }, s))));

  async function send() {
    const text = ta.value.trim(); if (!text || sending) return;
    if (!active) {
      try { const c = await api('POST', '/api/conversations'); session.set('pendingMsg', JSON.stringify({ id: c.id, text })); go('#/chat/' + c.id); }
      catch (e) { toast(e.message, 'err'); }
      return;
    }
    sending = true; sendBtn.disabled = true; ta.disabled = true; $('#empty-chat')?.remove();
    const userEl = msgEl({ role: 'user', content: text, id: '' }); thread.append(userEl);
    const typing = h('div', { class: 'msg assistant' }, h('div', { class: 'who' }, 'Table Scout'), h('div', { class: 'typing', 'aria-label': 'Assistant is working' }, h('i'), h('i'), h('i')));
    thread.append(typing); scrollEnd(); ta.value = ''; ta.style.height = 'auto';
    try {
      const body = { content: text, use_demo: useDemo.checked }; if (geo) { body.lat = geo.lat; body.lon = geo.lon; }
      const r = await api('POST', `/api/conversations/${active}/messages`, body);
      userEl.replaceWith(msgEl(r.user_message)); typing.replaceWith(msgEl(r.assistant_message)); titleText.textContent = r.conversation.title; document.title = r.conversation.title + ' · Table Scout';
      const idx = listState.items.findIndex((x) => x.id === active); if (idx >= 0) listState.items.splice(idx, 1); listState.items.unshift(r.conversation); drawList();
    } catch (e) { typing.remove(); userEl.remove(); ta.value = text; toast(e.message, 'err'); }
    finally { sending = false; sendBtn.disabled = false; ta.disabled = false; ta.focus(); scrollEnd(); }
  }

  loadList(true).then(async () => {
    if (active) return openConversation(active);
    thread.replaceChildren(emptyChat()); titleText.textContent = ''; ta.focus();
  });
  if (!active) thread.replaceChildren(emptyChat());
}

// ---------------------------------------------------------- comparison view
const CMP_ROWS = [['Type', (r) => r.kind && nice(r.kind)], ['Cuisine', (r) => (r.cuisines || []).map(nice).join(', ')],
  ['Address', (r) => r.address], ['Estimated cost', (r) => (r.cost_per_person != null ? `₹${r.cost_per_person}/person${r.is_demo ? ' (sample)' : ''}` : null)],
  ['Rating', (r) => (r.rating != null ? `${r.rating}/5${r.review_count ? ` (${r.review_count})` : ''}` : null)],
  ['Vegetarian', (r) => r.vegetarian], ['Vegan', (r) => r.vegan], ['Distance', (r) => (r.distance_km != null ? r.distance_km + ' km' : null)],
  ['Open now', (r) => (r.open_now === true ? 'Yes' : r.open_now === false ? 'No' : null)], ['Opening hours', (r) => r.opening_hours],
  ['Amenities', (r) => Object.entries(r.amenities || {}).filter(([, v]) => v).map(([k]) => nice(k)).join(', ')],
  ['Data source', (r) => (r.is_demo ? 'DEMO data' : r.source_name)], ['Data retrieved', (r) => (r.is_demo ? null : ago(r.fetched_at))]];
function comparisonTable(c, onRemove) {
  const rows = c.restaurants;
  return h('div', { class: 'card', style: 'padding:6px 10px' }, h('div', { class: 'table-wrap' }, h('table', { class: 'cmp' },
    h('thead', {}, h('tr', {}, h('th', {}, ''), rows.map((r) => h('th', { scope: 'col' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); openDetail(r.id); } }, r.name),
      onRemove && h('div', {}, h('button', { class: 'btn sm ghost', onclick: () => onRemove(r.id) }, 'Remove')))))),
    h('tbody', {}, CMP_ROWS.map(([label, fn]) => h('tr', {}, h('th', { scope: 'row' }, label), rows.map((r) => { const v = fn(r); return h('td', {}, v ? v : h('span', { class: 'muted' }, 'unavailable')); })))))),
    c.differences?.length > 0 && h('div', { style: 'padding:6px 10px 12px' }, h('h3', {}, 'Key differences'), h('ul', {}, c.differences.map((d) => h('li', {}, d))),
      h('p', { class: 'muted small' }, 'Differences are computed from the data above; missing values are never filled in.')));
}
async function compare() {
  const box = h('div', {}); main().append(h('div', { class: 'page' }, h('h2', {}, 'Compare restaurants'), box));
  const draw = async () => {
    if (compareIds.length < 2) { box.replaceChildren(h('div', { class: 'card empty' }, h('h3', {}, 'Pick at least two restaurants'), h('p', {}, 'Tick “Compare” on restaurant cards in Discover or Chat.'), h('a', { class: 'btn primary', href: '#/discover' }, 'Find restaurants'))); return; }
    box.replaceChildren(...skeletons(1));
    try { const c = await api('POST', '/api/compare', { ids: compareIds });
      box.replaceChildren(comparisonTable(c, (id) => { compareIds = compareIds.filter((x) => x !== id); store.set('compare_ids', compareIds); renderTray(); draw(); }));
      if (c.missing_ids?.length) box.prepend(h('div', { class: 'banner warn' }, `${c.missing_ids.length} selected restaurant(s) could not be found and were skipped.`)); }
    catch (e) { box.replaceChildren(h('div', { class: 'banner err' }, e.message)); }
  };
  draw();
}

// --------------------------------------------------------------- favourites
async function favourites() {
  const box = h('div', { class: 'results' }); main().append(h('div', { class: 'page' }, h('h2', {}, 'Favourites'), box)); box.append(...skeletons(2));
  try {
    const r = await api('GET', '/api/favourites'); box.replaceChildren();
    if (!r.items.length) return box.append(h('div', { class: 'card empty' }, h('h3', {}, 'No favourites yet'), h('p', {}, 'Tap the heart on any restaurant to save it here.'), h('a', { class: 'btn primary', href: '#/discover' }, 'Discover restaurants')));
    r.items.forEach((f) => { if (!f.restaurant) return; const c = restaurantCard(f.restaurant); state.favs.add(f.restaurant_id);
      c.prepend(h('div', { class: 'small ' + (f.stale ? 'banner warn' : 'muted') }, `Saved ${ago(f.saved_at)}${f.stale ? ' — this information may be out of date; open the original listing to check.' : ''}`)); box.append(c); });
    r.items.forEach((f) => syncFavButtons(f.restaurant_id));
  } catch (e) { box.replaceChildren(h('div', { class: 'banner err' }, e.message)); }
}

// ------------------------------------------------------------------ profile
function profile() {
  const u = state.user, p = u.preferences || {};
  const name = h('input', { id: 'p-name', value: u.name, maxlength: 80 });
  const loc = h('input', { id: 'p-loc', value: p.default_location || '', placeholder: 'e.g. Baner, Pune', maxlength: 120 });
  const diet = h('select', { id: 'p-diet' }, ['any', 'vegetarian', 'vegan', 'jain'].map((d) => h('option', { value: d }, d === 'any' ? 'No preference' : d))); diet.value = p.diet || 'any';
  const bud = h('input', { id: 'p-bud', type: 'number', min: 1, value: p.budget_per_person || '', placeholder: '₹ per person' });
  const msg = h('div', { role: 'status' });
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault(); msg.replaceChildren();
    try { const r = await api('PUT', '/api/profile', { name: name.value, default_location: loc.value, diet: diet.value, budget_per_person: bud.value ? Number(bud.value) : null });
      state.user = r.user; renderTopbar(); msg.replaceChildren(h('div', { class: 'banner info' }, 'Saved.')); toast('Profile saved'); }
    catch (ex) { msg.replaceChildren(h('div', { class: 'banner err' }, ex.message)); } } },
    h('div', { class: 'field' }, h('label', { for: 'p-name' }, 'Name'), name),
    h('div', { class: 'field' }, h('label', {}, 'Email'), h('input', { value: u.email, disabled: true, 'aria-label': 'Email' })),
    h('div', { class: 'field' }, h('label', { for: 'p-loc' }, 'Default location'), loc),
    h('div', { class: 'field' }, h('label', { for: 'p-diet' }, 'Dietary preference'), diet),
    h('div', { class: 'field' }, h('label', { for: 'p-bud' }, 'Typical budget per person'), bud),
    h('button', { class: 'btn primary', type: 'submit' }, 'Save preferences'), msg);
  main().append(h('div', { class: 'page' }, h('div', { class: 'card auth', style: 'max-width:520px;margin:0' }, h('h2', {}, 'Profile & preferences'),
    h('p', { class: 'muted small' }, 'Used as defaults by the assistant when your request doesn’t say otherwise.'), form)));
}

// --------------------------------------------------------------------- boot
(async function boot() {
  try { await api('GET', '/api/auth/csrf'); } catch { /* retried implicitly by next request */ }
  try { state.status = await api('GET', '/api/status'); } catch { /* optional */ }
  try { const r = await api('GET', '/api/auth/me'); state.user = r.user; await loadFavs(); } catch { state.user = null; }
  const syncBar = () => document.documentElement.style.setProperty('--topbar-h', $('#topbar').offsetHeight + 'px');
  new ResizeObserver(syncBar).observe($('#topbar'));
  window.addEventListener('hashchange', render); renderTray(); render(); syncBar();
})();
})();
