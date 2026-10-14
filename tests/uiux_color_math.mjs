// Serialized with each measurement so computed styles stay in the browser context.
export function colorMath() {
  const rgba = value => value.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0];
  const over = (fg, bg) => fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
  const background = el => {
    if (!el) return [255, 255, 255];
    return over(rgba(getComputedStyle(el).backgroundColor), background(el.parentElement));
  };
  const luminance = rgb => rgb.map(v => {v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;})
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
  return {rgba, over, background, luminance, contrast};
}
