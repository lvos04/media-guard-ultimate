// WCAG 2.2 contrast checker for the MediaGuard design tokens.
// Usage: node docs/design/tools/contrast.mjs  → prints a Markdown table.
// Tints (status backgrounds) are alpha-composited over the surface they sit on.

const themes = {
  dark: {
    bg: "#0C111B", surface: "#141C29", raised: "#1C2735", border: "#2C3746", borderStrong: "#5A6A80",
    text: "#F0F4FA", text2: "#A4B0C1", text3: "#8592A5",
    accent: "#4F9CF9", success: "#35B587", warning: "#E8A44D", danger: "#E36B71", info: "#8C9DF5",
    onAccent: "#07101F", focus: "#8EC1FF",
  },
  light: {
    bg: "#F4F6FA", surface: "#FFFFFF", raised: "#EEF2F7", border: "#D3DAE4", borderStrong: "#7A889C",
    text: "#0E1624", text2: "#4A5668", text3: "#5D697B",
    accent: "#1D66C9", success: "#167650", warning: "#8F5300", danger: "#BE2F39", info: "#4651C4",
    onAccent: "#FFFFFF", focus: "#1D66C9",
  },
};

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lin = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
const lum = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const mix = (fg, bg, alpha) => fg.map((v, i) => Math.round(v * alpha + bg[i] * (1 - alpha)));
const toHex = (rgb) => "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();

const rows = [];
for (const [name, t] of Object.entries(themes)) {
  const c = Object.fromEntries(Object.entries(t).map(([k, v]) => [k, hex(v)]));
  const tintAlpha = name === "dark" ? 0.12 : 0.10;
  const checks = [
    ["text", "bg", 4.5], ["text", "surface", 4.5], ["text", "raised", 4.5],
    ["text2", "bg", 4.5], ["text2", "surface", 4.5], ["text2", "raised", 4.5],
    ["text3", "bg", 4.5], ["text3", "surface", 4.5], ["text3", "raised", 4.5],
    ["accent", "bg", 4.5], ["accent", "surface", 4.5], ["accent", "raised", 4.5],
    ["success", "surface", 4.5], ["warning", "surface", 4.5], ["danger", "surface", 4.5], ["info", "surface", 4.5],
    ["success", "raised", 4.5], ["warning", "raised", 4.5], ["danger", "raised", 4.5],
    ["onAccent", "accent", 4.5], ["focus", "bg", 3], ["focus", "surface", 3], ["border", "surface", 1], ["borderStrong", "surface", 3], ["borderStrong", "bg", 3],
  ];
  for (const [fg, bg, min] of checks) {
    const r = ratio(c[fg], c[bg]);
    rows.push({ theme: name, pair: `${fg} ${t[fg]} on ${bg} ${t[bg]}`, ratio: r, min, ok: r >= min });
  }
  for (const s of ["accent", "success", "warning", "danger", "info"]) {
    const tint = mix(c[s], c.surface, tintAlpha);
    const r = ratio(c[s], tint);
    rows.push({ theme: name, pair: `${s} text on ${s}-tint ${toHex(tint)} (${Math.round(tintAlpha * 100)}% over surface)`, ratio: r, min: 4.5, ok: r >= 4.5 });
    const r2 = ratio(c.text, tint);
    rows.push({ theme: name, pair: `text on ${s}-tint ${toHex(tint)}`, ratio: r2, min: 4.5, ok: r2 >= 4.5 });
  }
  // Non-text UI components (WCAG 1.4.11, 3:1): status colours used as icons / bars against surface
  for (const s of ["accent", "success", "warning", "danger"]) {
    const r = ratio(c[s], c.surface);
    rows.push({ theme: name, pair: `${s} icon/bar on surface (1.4.11)`, ratio: r, min: 3, ok: r >= 3 });
  }
}

console.log("| Thema | Combinatie | Ratio | Eis | Resultaat |\n|---|---|---|---|---|");
for (const r of rows) {
  console.log(`| ${r.theme} | ${r.pair} | ${r.ratio.toFixed(2)}:1 | ${r.min}:1 | ${r.min === 1 ? "decoratief" : r.ok ? "✓ AA" : "✗"} |`);
}
const failed = rows.filter((r) => r.min > 1 && !r.ok);
if (failed.length) { console.error(`\n${failed.length} combinaties falen`); process.exitCode = 1; }
