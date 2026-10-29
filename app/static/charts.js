// Tiny dependency-free SVG chart helpers
const Charts = (() => {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function columns(values, labels, opts = {}) {
    const W = opts.width || 520, H = opts.height || 200, pl = 36, pb = 26, pt = 10, pr = 6;
    let max = Math.max(1, ...values) * 1.1;
    const sorted = [...values].sort((a, b) => b - a);
    if (opts.cap && sorted[0] > sorted[1] * 4) max = Math.max(1, sorted[1] * 2.2);
    const n = values.length, bw = (W - pl - pr) / n;
    const colors = opts.colors || values.map(() => opts.color || '#2a4166');
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(opts.label || 'chart')}">`;
    for (let i = 0; i <= 4; i++) {
      const v = max * i / 4, y = H - pb - (H - pb - pt) * i / 4;
      s += `<line x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}" stroke="#edf0f4"/>`;
      s += `<text x="${pl - 6}" y="${y + 4}" font-size="10" text-anchor="end" fill="#5d6b82">${opts.fmt ? opts.fmt(v) : Math.round(v)}</text>`;
    }
    values.forEach((v, i) => {
      const over = v > max, h = (H - pb - pt) * Math.min(v, max) / max, x = pl + i * bw + bw * 0.15, y = H - pb - h;
      s += `<rect x="${x}" y="${y}" width="${bw * 0.7}" height="${Math.max(h, 0)}" rx="3" fill="${colors[i]}"><title>${esc(labels[i])}: ${v}</title></rect>`;
      if (over) s += `<path d="M${x - 2} ${y + 14} l${bw * 0.74} -6 v5 l-${bw * 0.74} 6z" fill="#fff"/>`;
      if (opts.showValues && v > 0) s += `<text x="${x + bw * 0.35}" y="${y - 4}" font-size="10" text-anchor="middle" fill="#16233a">${v}</text>`;
      s += `<text x="${x + bw * 0.35}" y="${H - 8}" font-size="10" text-anchor="middle" fill="#5d6b82">${esc(labels[i])}</text>`;
    });
    return s + '</svg>';
  }

  // bars + line overlay with two axes (e.g. monthly spend bars + app visits line)
  function combo(bars, line, labels, opts = {}) {
    const W = opts.width || 560, H = opts.height || 210, pl = 46, pr = 34, pb = 26, pt = 12;
    const bmax = Math.max(1, ...bars) * 1.15, lmax = Math.max(1, ...line) * 1.15;
    const n = bars.length, bw = (W - pl - pr) / n;
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(opts.label || 'chart')}">`;
    for (let i = 0; i <= 4; i++) {
      const y = H - pb - (H - pb - pt) * i / 4;
      s += `<line x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}" stroke="#edf0f4"/>`;
      s += `<text x="${pl - 6}" y="${y + 4}" font-size="10" text-anchor="end" fill="#5d6b82">${opts.fmtBar ? opts.fmtBar(bmax * i / 4) : Math.round(bmax * i / 4)}</text>`;
      s += `<text x="${W - pr + 6}" y="${y + 4}" font-size="10" fill="#1f7fe0">${Math.round(lmax * i / 4)}</text>`;
    }
    bars.forEach((v, i) => {
      const h = (H - pb - pt) * v / bmax, x = pl + i * bw + bw * 0.18;
      s += `<rect x="${x}" y="${H - pb - h}" width="${bw * 0.64}" height="${h}" rx="3" fill="#c7d2e2"><title>${esc(labels[i])}: ${opts.fmtBar ? opts.fmtBar(v) : v}</title></rect>`;
      s += `<text x="${pl + i * bw + bw / 2}" y="${H - 8}" font-size="10" text-anchor="middle" fill="#5d6b82">${esc(labels[i])}</text>`;
    });
    const pts = line.map((v, i) => `${pl + i * bw + bw / 2},${H - pb - (H - pb - pt) * v / lmax}`);
    s += `<polyline points="${pts.join(' ')}" fill="none" stroke="#1f7fe0" stroke-width="2.2" stroke-linejoin="round"/>`;
    line.forEach((v, i) => { const [x, y] = pts[i].split(','); s += `<circle cx="${x}" cy="${y}" r="3" fill="#1f7fe0"><title>${esc(labels[i])}: ${v} visits</title></circle>`; });
    return s + '</svg>';
  }

  function line(values, labels, opts = {}) {
    const W = opts.width || 520, H = opts.height || 160, pl = 40, pr = 10, pb = 24, pt = 10;
    const max = opts.max || Math.max(0.01, ...values) * 1.2;
    const n = values.length;
    const x = i => n === 1 ? (pl + W - pr) / 2 : pl + (W - pl - pr) * i / (n - 1);
    const y = v => H - pb - (H - pb - pt) * v / max;
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(opts.label || 'trend')}">`;
    for (let i = 0; i <= 3; i++) {
      const v = max * i / 3;
      s += `<line x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}" stroke="#edf0f4"/><text x="${pl - 6}" y="${y(v) + 4}" font-size="10" text-anchor="end" fill="#5d6b82">${opts.fmt ? opts.fmt(v) : v.toFixed(2)}</text>`;
    }
    const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    s += `<polyline points="${pts}" fill="none" stroke="${opts.color || '#e0483c'}" stroke-width="2.2"/>`;
    values.forEach((v, i) => {
      s += `<circle cx="${x(i)}" cy="${y(v)}" r="3.5" fill="${opts.color || '#e0483c'}"><title>${esc(labels[i])}: ${opts.fmt ? opts.fmt(v) : v}</title></circle>`;
      if (n <= 8 || i % Math.ceil(n / 8) === 0) s += `<text x="${x(i)}" y="${H - 6}" font-size="10" text-anchor="middle" fill="#5d6b82">${esc(labels[i])}</text>`;
    });
    return s + '</svg>';
  }

  function hbars(items, opts = {}) {
    const max = Math.max(1e-9, ...items.map(i => i.value));
    return `<div class="hbars">${items.map(i => `<div class="row"><div>${esc(i.label)}</div><div class="t"><div style="width:${(i.value / max * 100).toFixed(1)}%;background:${i.color || ''}"></div></div><div class="n">${opts.fmt ? opts.fmt(i.value) : i.value}</div></div>`).join('')}</div>`;
  }

  return { columns, combo, line, hbars, esc };
})();
