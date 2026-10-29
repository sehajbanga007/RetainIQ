// RetainIQ single-page dashboard
const $app = document.getElementById('app');
const esc = Charts.esc;
const state = { me: null, custQuery: { q: '', risk: '', segment: '', sort: 'priority', page: 1 } };

// ---------- utils
async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(o.body); }
  const r = await fetch(path, o);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.startsWith('/api/auth/login')) { state.me = null; renderLogin(); throw new Error('Not logged in'); }
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}
const inr = v => '₹' + Math.round(v || 0).toLocaleString('en-IN');
const inrShort = v => { v = v || 0; if (v >= 1e7) return '₹' + (v / 1e7).toFixed(2) + ' Cr'; if (v >= 1e5) return '₹' + (v / 1e5).toFixed(1) + ' L'; if (v >= 1e3) return '₹' + (v / 1e3).toFixed(1) + 'k'; return '₹' + Math.round(v); };
const pct = (v, d = 0) => (v == null ? '—' : (v * 100).toFixed(d) + '%');
const riskColor = p => p >= 0.5 ? '#e0483c' : p >= 0.2 ? '#f2b705' : '#1f9d6a';
const initials = n => (n || '?').split(' ').map(x => x[0]).slice(0, 2).join('').toUpperCase();
const fmtDate = s => s ? new Date(s).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const fmtDateTime = s => new Date(s).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
function toast(msg) { const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 2800); }
function probbar(p) { return `<div class="probbar"><div class="track"><div class="fill" style="width:${(p * 100).toFixed(1)}%;background:${riskColor(p)}"></div></div><span>${pct(p)}</span></div>`; }
const tier = t => `<span class="tier ${t}">${t}</span>`;

// ---------- auth
async function renderLogin(mode = 'login') {
  let demo = { accounts: [] };
  try { demo = await api('/api/meta/demo-accounts'); } catch (e) { }
  $app.innerHTML = `
  <div class="auth">
    <div class="auth-left">
      <div class="brand"><div class="brand-mark">${logo()}</div>RetainIQ</div>
      <div>
        <h1>Know which customers are about to leave, why, and how to keep them.</h1>
        <p>A network of AI agents scores every customer's churn risk, explains it, recommends the next best action and builds a shared persona that follows each shopper across every brand on RetainIQ.</p>
        <div class="auth-flow">
          <div><b>Predict</b>Churn probability for the next 90 days</div>
          <div><b>Explain</b>The exact factors behind each score</div>
          <div><b>Retain</b>Personalised actions, tracked to outcome</div>
        </div>
      </div>
      <div style="font-size:12px;color:#7d8ca6">Multi-agent churn prediction system · Sehaj Singh Banga, Pratik Bherwani, Yash Bohra</div>
    </div>
    <div class="auth-right"><div class="auth-card" id="authcard"></div></div>
  </div>`;
  const card = document.getElementById('authcard');
  if (mode === 'login') {
    card.innerHTML = `
      <h2>Sign in to your business</h2>
      <p style="color:var(--muted);margin:0 0 20px">Use a demo brand below or your own account.</p>
      <form id="lf">
        <div class="field"><label class="f" for="em">Work email</label><input id="em" type="email" required autocomplete="username"></div>
        <div class="field"><label class="f" for="pw">Password</label><input id="pw" type="password" required autocomplete="current-password"></div>
        <div class="err" id="er"></div>
        <button class="btn primary" style="width:100%;justify-content:center;padding:10px">Sign in</button>
      </form>
      <p style="margin-top:14px;font-size:13px">New business? <a href="#" id="toreg">Create an account</a></p>
      <div class="demo-list"><div class="sec-title" style="margin-top:0">Demo brands (password ${esc(demo.password || 'demo123')})</div>
        ${demo.accounts.map(a => `<button data-e="${esc(a.email)}"><span class="n">${esc(a.name)}</span><span class="c">${esc(a.category)}</span></button>`).join('')}
      </div>`;
    card.querySelectorAll('[data-e]').forEach(b => b.onclick = () => { document.getElementById('em').value = b.dataset.e; document.getElementById('pw').value = demo.password || 'demo123'; });
    document.getElementById('toreg').onclick = e => { e.preventDefault(); renderLogin('register'); };
    document.getElementById('lf').onsubmit = async e => {
      e.preventDefault();
      try { await api('/api/auth/login', { method: 'POST', body: { email: em.value, password: pw.value } }); boot(); }
      catch (x) { document.getElementById('er').textContent = x.message; }
    };
  } else {
    card.innerHTML = `
      <h2>Create a business account</h2>
      <p style="color:var(--muted);margin:0 0 20px">Join the RetainIQ network, then upload your customer data.</p>
      <form id="rf">
        <div class="field"><label class="f">Business name</label><input id="rn" required></div>
        <div class="field"><label class="f">Category</label><select id="rc">${['Fashion E-commerce', 'Electronics', 'Grocery Delivery', 'OTT Streaming', 'Food Delivery', 'Fitness', 'Other'].map(c => `<option>${c}</option>`).join('')}</select></div>
        <div class="field"><label class="f">Work email</label><input id="re" type="email" required></div>
        <div class="field"><label class="f">Password (6+ characters)</label><input id="rp" type="password" minlength="6" required></div>
        <div class="err" id="er"></div>
        <button class="btn primary" style="width:100%;justify-content:center;padding:10px">Create account</button>
      </form>
      <p style="margin-top:14px;font-size:13px">Already registered? <a href="#" id="tolog">Sign in</a></p>`;
    document.getElementById('tolog').onclick = e => { e.preventDefault(); renderLogin('login'); };
    document.getElementById('rf').onsubmit = async e => {
      e.preventDefault();
      try { await api('/api/auth/register', { method: 'POST', body: { name: rn.value, category: rc.value, email: re.value, password: rp.value } }); location.hash = '#/overview'; boot(); }
      catch (x) { document.getElementById('er').textContent = x.message; }
    };
  }
}

function logo() { return `<svg viewBox="0 0 18 18"><path d="M2 13l5-5 3.5 3.5L16 5" stroke="#0f1d33" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`; }

// ---------- shell
const NAV = [
  ['overview', 'Overview'], ['customers', 'Customers at risk'], ['network', 'Persona network'],
  ['actions', 'Retention actions'],
];

function shell(active, title, sub, body, actions = '') {
  $app.innerHTML = `
  <div class="shell">
    <aside class="side">
      <div class="brand"><div class="brand-mark">${logo()}</div>RetainIQ</div>
      <nav class="nav">${NAV.map(([k, l]) => `<a href="#/${k}" class="${k === active ? 'active' : ''}"><span class="dot"></span>${l}</a>`).join('')}</nav>
      <div class="side-foot">
        <div class="who">${esc(state.me.name)}</div>
        <div>${esc(state.me.category)}</div>
        ${state.me.live && state.me.live.running ? `<div class="live"><span></span>Live · ${fmtDate(state.me.as_of)}</div>` : `<div>Data as of ${fmtDate(state.me.as_of)}</div>`}<div>Scores updated ${state.me.last_scored ? fmtDateTime(state.me.last_scored) : '—'}</div>
        <button id="logout">Sign out</button>
      </div>
    </aside>
    <main class="main">
      <div class="top"><div><h1>${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ''}</div><div class="top-actions">${actions}</div></div>
      <div class="content" id="content">${body}</div>
    </main>
  </div>`;
  document.getElementById('logout').onclick = async () => { await api('/api/auth/logout', { method: 'POST' }); state.me = null; renderLogin(); };
}
const loading = () => `<div class="empty"><div class="spin" style="margin:0 auto 10px;color:var(--muted)"></div>Loading…</div>`;

// ---------- overview
async function viewOverview() {
  shell('overview', 'Overview', 'Churn risk across your customer base', loading());
  const d = await api('/api/dashboard');
  const k = d.kpis;
  if (!k.active) {
    document.getElementById('content').innerHTML = `<div class="panel empty"><h2>No scored customers yet</h2><p>Connect your store to RetainIQ. Customer scores appear here automatically as your shoppers buy and use your app.</p></div>`;
    return;
  }
  const f = d.factors || {};
  const hist = d.history || [];
  document.getElementById('content').innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="l">Active customers</div><div class="v">${k.active.toLocaleString('en-IN')}</div><div class="s">of ${k.total_customers.toLocaleString('en-IN')} ever acquired</div></div>
      <div class="kpi alert"><div class="l">High churn risk</div><div class="v">${k.high}</div><div class="s">${k.medium} medium · ${k.critical} critical (high value)</div></div>
      <div class="kpi"><div class="l">Revenue at risk (12 mo)</div><div class="v">${inrShort(k.revenue_at_risk)}</div><div class="s">of ${inrShort(k.total_value)} expected value</div></div>
      <div class="kpi"><div class="l">Average churn probability</div><div class="v">${pct(k.avg_churn, 1)}</div><div class="s">next 90 days</div></div>
      <div class="kpi"><div class="l">Retention success</div><div class="v">${k.retention_rate == null ? '—' : pct(k.retention_rate)}</div><div class="s">${k.retained} saved · ${k.actions_open} actions open</div></div>
    </div>
    <div class="grid g-62">
      <div class="panel">
        <div class="panel-head"><div><h2>Customers who need attention first</h2><p class="hint">Ranked by priority: churn risk combined with customer value</p></div><a href="#/customers">View all</a></div>
        <table><thead><tr><th>Customer</th><th>Churn risk</th><th>Main reason</th><th>Next best action</th><th class="num">At risk</th></tr></thead>
        <tbody>${d.top.map(r => `<tr class="click" data-id="${r.person_id}"><td><div class="cell-name">${esc(r.name)}</div><div class="cell-sub">${esc(r.priority)}</div></td><td>${probbar(r.churn_prob)}</td><td>${esc(r.top_reason)}</td><td>${esc(r.actions[0] ? r.actions[0].action : '')}</td><td class="num">${inr(r.revenue_at_risk)}</td></tr>`).join('') || '<tr><td colspan=5 class="empty">No at-risk customers right now</td></tr>'}</tbody></table>
      </div>
      <div class="panel">
        <h2>Risk distribution</h2><p class="hint">Number of customers by predicted churn probability</p>
        ${Charts.columns(d.distribution, ['0-10', '10-20', '20-30', '30-40', '40-50', '50-60', '60-70', '70-80', '80-90', '90+'], { colors: d.distribution.map((_, i) => riskColor(i / 10 + 0.05)), height: 210, showValues: true, cap: true, label: 'Risk distribution' })}
        <div class="chart-legend"><span><i style="background:#1f9d6a"></i>Low</span><span><i style="background:#f2b705"></i>Medium</span><span><i style="background:#e0483c"></i>High</span><span>x-axis: churn probability %</span></div>
        <div class="sec-title" style="margin-top:18px">Customers and revenue at risk by tier</div>
        <table><thead><tr><th>Tier</th><th class="num">Customers</th><th class="num">Revenue at risk</th></tr></thead><tbody>
        ${['High', 'Medium', 'Low'].map(t => `<tr><td>${tier(t)}</td><td class="num">${(d.tiers[t] || { c: 0 }).c}</td><td class="num">${inr((d.tiers[t] || { rar: 0 }).rar)}</td></tr>`).join('')}</tbody></table>
        <p class="hint" style="margin-top:10px">High = 50%+ chance of leaving in 90 days, Medium = 20–50%.</p>
      </div>
    </div>
    <div class="grid g3" style="margin-top:18px">
      <div class="panel"><h2>What drives churn here</h2><p class="hint">Factor Identification Agent: share of influence on predictions</p>
        ${Charts.hbars((f.importance || []).slice(0, 7).map(i => ({ label: i.label, value: i.importance })), { fmt: v => pct(v) })}</div>
      <div class="panel"><h2>Patterns that predict leaving</h2><p class="hint">Churn rate vs everyone else, last full quarter</p>
        <table><tbody>${(f.patterns || []).filter(p => p.lift >= 1.05).slice(0, 6).map(p => `<tr><td>${esc(p.pattern)}<div class="cell-sub">${p.customers} customers · ${pct(p.churn_rate, 1)} churned</div></td><td class="num"><b>${p.lift}x</b></td></tr>`).join('')}</tbody></table></div>
      <div class="panel"><h2>Risk by segment</h2><p class="hint">Behavior Analysis Agent segments, average churn probability</p>
        ${Charts.hbars(d.segment_risk.map(s => ({ label: `${s.segment} (${s.c})`, value: s.p })), { fmt: v => pct(v, 1) })}</div>
    </div>
    <div class="grid g2" style="margin-top:18px">
      <div class="panel"><h2>Engagement and trend</h2><p class="hint">How actively customers use your app, and which way it is moving</p>
        <div class="grid g2">${Charts.hbars(['High', 'Medium', 'Low', 'Dormant'].map(x => ({ label: x, value: d.engagement[x] || 0 })))}
        ${Charts.hbars(['Growing', 'Stable', 'Declining', 'Declining sharply'].map(x => ({ label: x, value: d.trend[x] || 0, color: x.startsWith('Declining') ? '#e0483c' : '' })))}</div></div>
      <div class="panel"><h2>Average churn risk over time</h2><p class="hint">One point per agent run date${hist.length < 2 ? ' — more points appear as you run the agents on new data' : ''}</p>
        ${hist.length ? Charts.line(hist.map(h => h.p), hist.map(h => h.d.slice(5)), { fmt: v => pct(v, 1), label: 'Average churn over time' }) : '<div class="empty">No history yet</div>'}
        <div class="cell-sub">Model v${d.model ? d.model.id : '—'} (${d.model ? esc(d.model.algorithm) : '—'}) · last run ${d.last_run ? fmtDate(d.last_run.finished_at) : '—'} · ${k.shared_with_network} of your customers also shop with other brands in the network</div></div>
    </div>`;
  document.querySelectorAll('tr[data-id]').forEach(tr => tr.onclick = () => location.hash = '#/customer/' + tr.dataset.id);
}

// ---------- customers
async function viewCustomers() {
  const q = state.custQuery;
  shell('customers', 'Customers at risk', 'Every active customer, scored by the agents', loading(),
    `<a class="btn" href="/api/export/customers.csv">Export CSV</a>`);
  const params = new URLSearchParams({ ...q, per: 25 });
  const d = await api('/api/customers?' + params);
  const pages = Math.max(1, Math.ceil(d.total / d.per));
  document.getElementById('content').innerHTML = `
    <div class="panel">
      <div class="filters">
        <input type="search" id="fq" placeholder="Search name, email or phone" value="${esc(q.q)}">
        <select id="fr"><option value="">All risk levels</option>${['High', 'Medium', 'Low'].map(x => `<option ${q.risk === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
        <select id="fs"><option value="">All segments</option>${d.segments.map(x => `<option ${q.segment === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
        <select id="fo">${[['priority', 'Sort: priority'], ['risk', 'Sort: churn risk'], ['rar', 'Sort: revenue at risk'], ['value', 'Sort: customer value'], ['name', 'Sort: name']].map(([v, l]) => `<option value="${v}" ${q.sort === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span style="color:var(--muted);margin-left:auto">${d.total.toLocaleString('en-IN')} customers</span>
      </div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>Customer</th><th>Risk</th><th>Churn probability</th><th>Main reason</th><th>Behaviour</th><th>Next best action</th><th class="num">12-mo value</th><th class="num">At risk</th></tr></thead>
        <tbody>${d.rows.map(r => `<tr class="click" data-id="${r.person_id}">
          <td><div class="cell-name">${esc(r.name)}</div><div class="cell-sub">${esc(r.email || '')}</div></td>
          <td>${tier(r.risk_tier)}<div class="cell-sub">${esc(r.priority)}</div></td>
          <td>${probbar(r.churn_prob)}</td>
          <td>${esc(r.top_reason)}</td>
          <td>${esc(r.engagement)} engagement<div class="cell-sub">${esc(r.trend)} · ${esc(r.segment)}</div></td>
          <td>${esc(r.next_action || '')}</td>
          <td class="num">${inr(r.clv)}</td><td class="num">${inr(r.revenue_at_risk)}</td></tr>`).join('') || '<tr><td colspan=8 class="empty">No customers match these filters</td></tr>'}</tbody>
      </table></div>
      <div class="pager"><span>Page ${d.page} of ${pages}</span><span>
        <button class="btn small" id="pp" ${d.page <= 1 ? 'disabled' : ''}>Previous</button>
        <button class="btn small" id="pn" ${d.page >= pages ? 'disabled' : ''}>Next</button></span></div>
    </div>`;
  let t;
  fq.oninput = () => { clearTimeout(t); t = setTimeout(() => { q.q = fq.value; q.page = 1; viewCustomers().then(() => { const i = document.getElementById('fq'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 350); };
  fr.onchange = () => { q.risk = fr.value; q.page = 1; viewCustomers(); };
  fs.onchange = () => { q.segment = fs.value; q.page = 1; viewCustomers(); };
  fo.onchange = () => { q.sort = fo.value; q.page = 1; viewCustomers(); };
  pp.onclick = () => { q.page--; viewCustomers(); };
  pn.onclick = () => { q.page++; viewCustomers(); };
  document.querySelectorAll('tr[data-id]').forEach(tr => tr.onclick = () => location.hash = '#/customer/' + tr.dataset.id);
}

// ---------- explanation waterfall (signature visual)
function waterfall(score, base) {
  const ex = score.explanation || [];
  const shown = ex.slice(0, 7);
  const rest = ex.slice(7).reduce((a, e) => a + e.impact, 0);
  const final = score.churn_prob * 100;
  const sumShown = shown.reduce((a, e) => a + e.impact, 0);
  const start = final - sumShown - rest; // baseline
  let cur = start;
  const pos = v => Math.max(0, Math.min(100, v));
  let rows = `<div class="wf-row"><div>Average customer in the network</div><div class="lane"><div class="seg base" style="left:0;width:${pos(start)}%"></div></div><div class="d">${start.toFixed(1)}%</div></div>`;
  for (const e of shown) {
    const a = cur, b = cur + e.impact; cur = b;
    const l = pos(Math.min(a, b)), w = Math.max(0.6, pos(Math.max(a, b)) - l);
    rows += `<div class="wf-row"><div>${esc(e.reason)}<div class="cell-sub">${esc(e.label)}</div></div><div class="lane"><div class="seg ${e.impact > 0 ? 'up' : 'down'}" style="left:${l}%;width:${w}%"></div></div><div class="d ${e.impact > 0 ? 'up' : 'down'}">${e.impact > 0 ? '+' : ''}${e.impact.toFixed(1)}</div></div>`;
  }
  if (Math.abs(rest) >= 0.1) {
    const a = cur, b = cur + rest; cur = b;
    const l = pos(Math.min(a, b)), w = Math.max(0.6, pos(Math.max(a, b)) - l);
    rows += `<div class="wf-row"><div>Other smaller factors</div><div class="lane"><div class="seg ${rest > 0 ? 'up' : 'down'}" style="left:${l}%;width:${w}%;opacity:.5"></div></div><div class="d">${rest > 0 ? '+' : ''}${rest.toFixed(1)}</div></div>`;
  }
  rows += `<div class="wf-row total"><div>This customer's churn probability</div><div class="lane"><div class="seg final" style="left:0;width:${pos(final)}%"></div></div><div class="d">${final.toFixed(1)}%</div></div>`;
  return `<div class="wf">${rows}</div><div class="chart-legend"><span><i style="background:#e0483c"></i>Raises risk</span><span><i style="background:#1f9d6a"></i>Lowers risk</span><span>Values are percentage points of churn probability</span></div>`;
}

// ---------- persona block (used on customer page and network page)
function personaBlock(p, opts = {}) {
  if (!p) return '<div class="empty">No persona available yet</div>';
  p = { interests: [], next_interests: [], likely_to_leave: [], brands: [], traits: [], price_sensitivity: { level: '—' }, ...p };
  const brands = p.brands.map(b => `<div class="b"><div><b>${esc(b.business)}</b> <span class="cell-sub">${esc(b.category)} · since ${fmtDate(b.since)} · ${esc(b.plan)}</span></div>
     <div>${b.status === 'Active' ? probbar(b.churn_prob) : '<span class="tag">Left</span>'}</div></div>`).join('');
  return `
    <div class="persona-head"><div class="avatar">${initials(p.name)}</div>
      <div><div class="arche">${esc(p.archetype)}</div><div class="cell-sub">${esc(p.name)} · ${esc(p.city || '')}${p.age ? ' · ' + p.age + ' yrs' : ''}</div></div></div>
    <div>${p.traits.map(t => `<span class="tag blue">${esc(t)}</span>`).join('')}</div>
    <div class="stat-pairs">
      <div><div class="l">Spend band</div><div class="v">${esc(p.spend_band)}</div></div>
      <div><div class="l">Loyalty score</div><div class="v">${Math.round(p.loyalty_score)}/100</div></div>
      <div><div class="l">Churn tendency</div><div class="v">${Math.round(p.churn_tendency)}%</div></div>
      <div><div class="l">Price sensitivity</div><div class="v">${esc(p.price_sensitivity.level)}</div></div>
    </div>
    ${opts.identity ? `<div class="sec-title">Identity</div><div>${esc(p.email || '')} · ${esc(p.phone || '')}<div class="cell-sub">${esc(p.address || '')}</div></div>` : ''}
    <div class="sec-title">Top interests across the network</div>
    ${p.interests.length ? Charts.hbars(p.interests.map(i => ({ label: `${i.category} (${i.business})${i.trend !== 'Steady' ? ' · ' + i.trend : ''}`, value: i.score })), { fmt: v => v.toFixed(0) + '%' }) : '<div class="cell-sub">No purchases yet</div>'}
    <div class="sec-title">Likely to buy next</div>
    ${p.next_interests.length ? p.next_interests.map(n => `<div style="margin-bottom:6px"><b>${esc(n.category)}</b><div class="cell-sub">${esc(n.reason)}</div></div>`).join('') : '<div class="cell-sub">Not enough signal yet</div>'}
    <div class="sec-title">Likely to leave</div>
    ${p.likely_to_leave.length ? p.likely_to_leave.map(l => `<span class="tag" style="background:var(--red-soft);color:#a8261c">${esc(l.business)} · ${pct(l.churn_prob)}</span>`).join('') : '<div class="cell-sub">No brand relationship at high risk</div>'}
    <div class="sec-title">Brands in the network (${p.n_brands})</div>
    <div class="brand-list">${brands}</div>
    <div class="cell-sub" style="margin-top:10px">Network lifetime spend ${inr(p.lifetime_spend)} · most active on ${esc(p.most_active_on || '—')} · updated ${fmtDate(p.updated_at)}</div>`;
}

// ---------- customer detail
async function viewCustomer(id) {
  shell('customers', 'Customer', '<a href="#/customers">Back to customers</a>', loading());
  const d = await api('/api/customers/' + id);
  const s = d.score, p = d.person;
  const series = d.series;
  document.querySelector('.top h1').textContent = p.name;
  const c = document.getElementById('content');
  if (!s) {
    c.innerHTML = `<div class="notice">This customer has had no orders or app visits for over 30 days, so they are counted as churned and are not scored.</div><div class="panel">${personaBlock(d.persona, { identity: true })}</div>`;
    return;
  }
  c.innerHTML = `
    <div class="panel" style="margin-bottom:18px">
      <div class="hero-cust">
        <div>
          <h2>${esc(p.name)} ${tier(s.risk_tier)}</h2>
          <div class="meta-line">${esc(p.email || '')} · ${esc(p.phone || '')} · ${esc(p.city || '')} · customer since ${fmtDate(d.relationship.joined_at)} · ${esc(d.relationship.plan)} plan</div>
          <div style="margin-top:10px"><span class="tag">${esc(s.priority)}</span><span class="tag">${esc(s.segment)}</span><span class="tag">${esc(s.engagement)} engagement</span><span class="tag">${esc(s.trend)}</span></div>
        </div>
        <div style="text-align:right"><div class="cell-sub">Chance of leaving in 90 days</div><div class="bigprob" style="color:${riskColor(s.churn_prob)}">${pct(s.churn_prob)}</div>
          <div class="cell-sub">12-month value ${inr(s.clv)} · at risk ${inr(s.revenue_at_risk)}</div></div>
      </div>
    </div>
    <div class="grid g-62">
      <div class="panel"><h2>Why this score</h2><p class="hint">Explainability Agent: how each factor moved this customer away from the average</p>${waterfall(s)}</div>
      <div class="panel"><h2>Recommended actions</h2><p class="hint">Retention Strategy Agent, ranked by expected impact</p>
        ${s.actions.map((a, i) => `<div class="action"><div><div class="a">${esc(a.action)}<span class="imp ${a.expected_impact}">${a.expected_impact} impact</span></div>
          <div class="w">Because: ${esc(a.why)}</div><div class="o">${esc(a.offer)}</div><div class="cell-sub">Channel: ${esc(a.channel)}</div></div>
          <div><button class="btn small primary" data-act="${i}">Send</button></div></div>`).join('')}
      </div>
    </div>
    <div class="grid g2" style="margin-top:18px">
      <div class="panel"><h2>Behaviour over the last 12 months</h2><p class="hint">Bars: monthly spend · Line: app visits</p>
        ${Charts.combo(series.map(x => x.spend), series.map(x => x.visits), series.map(x => x.month), { fmtBar: v => inrShort(v), label: 'Monthly behaviour' })}
        <div class="stat-pairs">
          <div><div class="l">Lifetime spend</div><div class="v">${inr(s.features.lifetime_spend)}</div></div>
          <div><div class="l">Orders (lifetime)</div><div class="v">${s.features.lifetime_orders}</div></div>
          <div><div class="l">Last purchase</div><div class="v">${Math.round(s.features.recency_days)}d ago</div></div>
          <div><div class="l">Last app visit</div><div class="v">${Math.round(s.features.session_recency_days)}d ago</div></div>
        </div>
        <div class="grid g2">
          <div><div class="sec-title">Recent orders</div><table><tbody>${d.orders.slice(0, 6).map(o => `<tr><td>${fmtDate(o.order_date)}<div class="cell-sub">${esc(o.category)}${o.discount_used ? ' · discount' : ''}${o.returned ? ' · returned' : ''}</div></td><td class="num">${inr(o.amount)}</td></tr>`).join('') || '<tr><td class="cell-sub">No orders</td></tr>'}</tbody></table></div>
          <div><div class="sec-title">Support tickets</div><table><tbody>${d.tickets.slice(0, 6).map(t => `<tr><td>${esc(t.issue)}<div class="cell-sub">${fmtDate(t.created_at)} · ${t.resolved ? 'resolved' : '<b style="color:var(--red)">unresolved</b>'}${t.csat ? ' · CSAT ' + t.csat + '/5' : ''}</div></td></tr>`).join('') || '<tr><td class="cell-sub">No tickets</td></tr>'}</tbody></table></div>
        </div>
      </div>
      <div class="panel"><div class="panel-head"><div><h2>Network persona</h2><p class="hint">Persona Agent: built from this person's activity across every brand on RetainIQ</p></div></div>${personaBlock(d.persona)}</div>
    </div>
    <div class="panel" style="margin-top:18px"><h2>Action history and outcome</h2><p class="hint">Mark the outcome so the system learns which actions work (continuous feedback)</p>
      ${d.actions.length ? `<table><thead><tr><th>Action</th><th>Channel</th><th>Sent</th><th>Risk when sent</th><th>Status</th><th></th></tr></thead><tbody>${d.actions.map(a => `<tr><td>${esc(a.action)}</td><td>${esc(a.channel || '')}</td><td>${fmtDate(a.created_at)}</td><td>${a.churn_prob_at_action != null ? pct(a.churn_prob_at_action) : '—'}</td><td>${esc(a.status)}</td>
        <td class="num">${a.status === 'Sent' || a.status === 'Planned' ? `<button class="btn small" data-out="${a.id}" data-s="Retained">Retained</button> <button class="btn small" data-out="${a.id}" data-s="Churned">Churned</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<div class="cell-sub">No actions sent yet. Use "Send" on a recommended action above.</div>'}
    </div>`;
  c.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
    const a = s.actions[+b.dataset.act];
    await api(`/api/customers/${id}/actions`, { method: 'POST', body: { action: a.action, channel: a.channel, note: a.offer } });
    toast(`"${a.action}" sent to ${p.name}`); viewCustomer(id);
  });
  c.querySelectorAll('[data-out]').forEach(b => b.onclick = async () => {
    await api('/api/actions/' + b.dataset.out, { method: 'PATCH', body: { status: b.dataset.s } });
    toast('Outcome saved: ' + b.dataset.s); viewCustomer(id);
  });
}

// ---------- persona network
async function viewNetwork(pid) {
  shell('network', 'Persona network', 'One profile per shopper, shared across every brand on RetainIQ', loading(),
    `<a class="btn" href="/checkout-demo" target="_blank">Open one-tap checkout demo</a>`);
  const d = await api('/api/network/stats');
  const ar = Object.entries(d.archetypes).sort((a, b) => b[1] - a[1]);
  document.getElementById('content').innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="l">People in the network</div><div class="v">${d.persons.toLocaleString('en-IN')}</div></div>
      <div class="kpi"><div class="l">Shop with 2+ brands</div><div class="v">${d.multi_brand.toLocaleString('en-IN')}</div><div class="s">${pct(d.multi_brand / d.persons)} of people</div></div>
      <div class="kpi"><div class="l">Brands in the network</div><div class="v">${d.businesses.length}</div></div>
      <div class="kpi"><div class="l">Your persona lookups</div><div class="v">${d.lookups}</div></div>
      <div class="kpi"><div class="l">Most common persona</div><div class="v" style="font-size:19px;margin-top:8px">${esc(ar[0] ? ar[0][0] : '—')}</div></div>
    </div>
    <div class="grid g-26">
      <div>
        <div class="panel"><h2>Look up a person</h2><p class="hint">Search by name, email or phone across the whole network</p>
          <input type="search" id="ns" placeholder="e.g. 98765… or riya@gmail.com" style="width:100%">
          <div id="nres" style="margin-top:10px"></div></div>
        <div class="panel" style="margin-top:18px"><h2>Your customers also shop at</h2><p class="hint">Overlap with other brands in the network</p>
          ${Charts.hbars(d.overlap.map(o => ({ label: o.name, value: o.c })))}</div>
        <div class="panel" style="margin-top:18px"><h2>Persona types</h2><p class="hint">Archetypes assigned by the Persona Agent</p>
          ${Charts.hbars(ar.map(([k, v]) => ({ label: k, value: v })))}</div>
      </div>
      <div class="panel" id="pview"><div class="empty">Search for a person to see their full network persona.</div></div>
    </div>`;
  const show = async id => {
    pview.innerHTML = loading();
    try {
      const r = await api('/api/network/persona/' + id);
      const p = r.persona;
      if (!p) throw new Error('No persona found for this person yet');
      pview.innerHTML = `<div class="panel-head"><h2>${esc(p.name)}</h2>${p.is_your_customer ? `<a href="#/customer/${p.person_id}">Open as your customer</a>` : '<span class="tag">Not yet your customer</span>'}</div>
        ${p.with_you ? `<div class="notice">With you: ${tier(p.with_you.risk_tier)} ${pct(p.with_you.churn_prob)} churn risk — ${esc(p.with_you.top_reason)}</div>` : ''}
        ${personaBlock(p, { identity: true })}`;
    } catch (e) { pview.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };
  let t;
  ns.oninput = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const r = await api('/api/network/search?q=' + encodeURIComponent(ns.value));
      nres.innerHTML = r.rows.map(x => `<div class="b" style="padding:7px 0;border-bottom:1px solid #edf0f4;cursor:pointer" data-p="${x.id}"><b>${esc(x.name)}</b><div class="cell-sub">${esc(x.email || '')} · ${esc(x.phone || '')} · ${esc(x.city || '')}</div></div>`).join('') || (ns.value.length > 1 ? '<div class="cell-sub">No one found</div>' : '');
      nres.querySelectorAll('[data-p]').forEach(el => el.onclick = () => show(el.dataset.p));
    }, 300);
  };
  if (pid) show(pid);
}

// ---------- actions
async function viewActions() {
  shell('actions', 'Retention actions', 'Every action sent and its outcome', loading());
  const d = await api('/api/actions');
  document.getElementById('content').innerHTML = `
    <div class="grid g-26">
      <div class="panel"><h2>What works</h2><p class="hint">Outcomes by action type — used to improve future recommendations</p>
        ${d.by_action.length ? `<table><thead><tr><th>Action</th><th class="num">Sent</th><th class="num">Saved</th><th class="num">Lost</th><th class="num">Success</th></tr></thead><tbody>${d.by_action.map(a => `<tr><td>${esc(a.action)}</td><td class="num">${a.total}</td><td class="num">${a.retained}</td><td class="num">${a.churned}</td><td class="num">${a.retained + a.churned ? pct(a.retained / (a.retained + a.churned)) : '—'}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No actions yet</div>'}</div>
      <div class="panel"><h2>Action log</h2><p class="hint">Mark outcomes once you know whether the customer stayed</p>
        ${d.rows.length ? `<table><thead><tr><th>Customer</th><th>Action</th><th>Sent</th><th>Risk then → now</th><th>Status</th><th></th></tr></thead><tbody>${d.rows.map(a => `<tr><td><a href="#/customer/${a.person_id}">${esc(a.name)}</a></td><td>${esc(a.action)}<div class="cell-sub">${esc(a.note || '')}</div></td><td>${fmtDate(a.created_at)}</td>
          <td>${a.churn_prob_at_action != null ? pct(a.churn_prob_at_action) : '—'} → ${a.current_prob != null ? pct(a.current_prob) : 'inactive'}</td><td>${esc(a.status)}</td>
          <td class="num">${a.status === 'Sent' || a.status === 'Planned' ? `<button class="btn small" data-out="${a.id}" data-s="Retained">Retained</button> <button class="btn small" data-out="${a.id}" data-s="Churned">Churned</button>` : ''}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">No actions sent yet. Open a customer and send a recommended action.<br><br><a class="btn" href="#/customers">Go to customers</a></div>'}</div>
    </div>`;
  document.querySelectorAll('[data-out]').forEach(b => b.onclick = async () => {
    await api('/api/actions/' + b.dataset.out, { method: 'PATCH', body: { status: b.dataset.s } }); toast('Outcome saved'); viewActions();
  });
}

// ---------- router
async function route(keepScroll) {
  if (!state.me) return;
  const h = location.hash.replace(/^#\/?/, '') || 'overview';
  const [page, arg] = h.split('/');
  try {
    if (page === 'customers') await viewCustomers();
    else if (page === 'customer') await viewCustomer(arg);
    else if (page === 'network') await viewNetwork(arg);
    else if (page === 'actions') await viewActions();
    else await viewOverview();
    if (!keepScroll) window.scrollTo(0, 0);
  } catch (e) {
    if (e.message !== 'Not logged in') { const c = document.getElementById('content'); if (c) c.innerHTML = `<div class="panel empty">${esc(e.message)}</div>`; }
  }
}
window.addEventListener('hashchange', () => route());

// live refresh: when the agents finish a new run, refresh list pages
setInterval(async () => {
  if (!state.me) return;
  try {
    const m = await api('/api/auth/me');
    const changed = m.business.last_scored !== state.me.last_scored;
    state.me = m.business;
    if (changed) {
      const page = (location.hash.replace(/^#\/?/, '') || 'overview').split('/')[0];
      if (['overview', 'customers', 'actions'].includes(page)) { await route(true); toast('Scores updated with the latest activity'); }
      else { const f = document.querySelector('.side-foot'); if (f) toast('New scores available'); }
    }
  } catch (e) { }
}, 20000);

async function boot() {
  try { const m = await api('/api/auth/me'); state.me = m.business; route(); }
  catch (e) { renderLogin(); }
}
boot();
