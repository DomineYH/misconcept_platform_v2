import assert from 'node:assert/strict';
import {colorMath} from './uiux_color_math.mjs';

export default async function check(page) {
  const known = await page.evaluate(`(() => {
    const {rgba, background, contrast} = (${colorMath})();
    const parent = document.createElement('div'), child = document.createElement('div');
    parent.style.backgroundColor = 'rgb(0, 0, 0)';
    child.style.backgroundColor = 'rgba(255, 255, 255, 0.5)';
    parent.append(child); document.body.append(parent);
    const composed = background(child);
    parent.remove();
    return {parsed:rgba('rgba(255, 255, 255, 0.5)'), composed,
      blackWhite:contrast([0,0,0],[255,255,255]), grayWhite:contrast([128,128,128],[255,255,255])};
  })()`);
  assert.deepEqual(known.parsed,[255,255,255,.5]);
  assert.deepEqual(known.composed,[127.5,127.5,127.5]);
  assert.equal(known.blackWhite,21);
  assert(Math.abs(known.grayWhite - 3.9494396480491156) < 1e-9);
  return {pageErrors:[]};
}
