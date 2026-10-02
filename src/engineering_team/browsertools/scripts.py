"""The one script Accessibility Check runs inside the page (read-only: it changes nothing).

It reports the page language and title, and up to 20 text elements whose colour contrast against
the nearest opaque ancestor background is below WCAG AA (4.5:1, or 3:1 for large text). It cannot
see backgrounds drawn with images or gradients; elements over those are skipped, not guessed.
"""

PAGE_FACTS = """
() => {
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  const background = (el) => {
    let layers = [];
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== 'none') return null;
      const c = parse(style.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 0.99) break; }
    }
    let base = {r: 255, g: 255, b: 255};
    for (const c of layers.reverse()) {
      base = {r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a)};
    }
    return base;
  };
  const seen = new Set();
  const issues = [];
  let scanned = 0;
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (scanned >= 400 || issues.length >= 20) break;
    const text = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').trim();
    if (!text) continue;
    const style = getComputedStyle(el);
    if (style.visibility !== 'visible' || style.display === 'none' || parseFloat(style.opacity) === 0) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    scanned++;
    const fg = parse(style.color);
    const bg = background(el);
    if (!fg || !bg) continue;
    const alpha = fg.a;
    const blended = {r: fg.r * alpha + bg.r * (1 - alpha), g: fg.g * alpha + bg.g * (1 - alpha), b: fg.b * alpha + bg.b * (1 - alpha)};
    const l1 = lum(blended), l2 = lum(bg);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const size = parseFloat(style.fontSize), weight = parseInt(style.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const required = large ? 3 : 4.5;
    const key = hex(blended) + hex(bg) + required;
    if (ratio < required && !seen.has(key)) {
      seen.add(key);
      issues.push({text: text.slice(0, 40), ratio: Math.round(ratio * 10) / 10, required, foreground: hex(blended), background: hex(bg)});
    }
  }
  return {lang: document.documentElement.getAttribute('lang') || '', title: document.title || '', contrast: issues};
}
"""
