// Three fixture-only structures share the real controls and their in-memory input.
const names = {A:'기존 계약 우선', B:'위아래 맥락', C:'접는 맥락'};
const bar = document.createElement('nav');
bar.id = 'prototype-switcher';
bar.setAttribute('aria-label', '프로토타입 배치');
bar.innerHTML = '<button type="button" aria-label="이전 배치">←</button><output></output><button type="button" aria-label="다음 배치">→</button>';
document.body.append(bar);
const state = document.createElement('p');
state.id = 'prototype-state';
state.textContent = 'LOCAL FIXTURE · 합성 자료 · 쓰기 차단';
document.querySelector('main').prepend(state);
const disclosures = [];
for (const [selector, label] of [['.scenario-preview','공개 미리보기'], ['#conversation-dialogue','기준 대화 이력']]) {
  const content = document.querySelector(selector);
  if (!content) continue;
  const details = document.createElement('details');
  details.className = 'prototype-context';
  const summary = document.createElement('summary');
  summary.textContent = label;
  content.before(details);
  details.append(summary, content);
  disclosures.push(details);
}
function select(variant) {
  document.documentElement.dataset.variant = variant;
  const url = new URL(location.href);
  url.searchParams.set('variant', variant);
  history.replaceState(null, '', url);
  bar.querySelector('output').textContent = `${variant} · ${names[variant]}`;
  for (const details of disclosures) details.open = variant !== 'C';
}
function cycle(offset) {
  const keys = Object.keys(names);
  select(keys[(keys.indexOf(document.documentElement.dataset.variant) + offset + 3) % 3]);
}
bar.firstElementChild.onclick = () => cycle(-1);
bar.lastElementChild.onclick = () => cycle(1);
document.addEventListener('keydown', event => {
  // Legacy comparison tabs own arrows; text editing and modified shortcuts also win.
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey ||
      event.target.closest('input, textarea, select, [contenteditable], [role="tablist"]')) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault();
    cycle(event.key === 'ArrowLeft' ? -1 : 1);
  }
});
select(new URL(location.href).searchParams.get('variant'));
if (new URL(location.href).searchParams.has('comparison')) {
  document.getElementById('open-comparison')?.click();
  const {renderComparisonResult} = await import('/static/js/comparison-results.js');
  for (const panel of document.querySelectorAll('[data-comparison-panel]')) {
    renderComparisonResult(panel.lastElementChild, {status:'completed',
      text:'학생봇: 분모가 더 크면 조각도 크다고 생각했어요.\n\n같은 전체인지 그림으로 다시 확인해 볼게요.\n\n'.repeat(4),
      model_config:{model_id:'합성-긴모델이름-분수수업-비교검토'}}, 'student',
      Object.fromEntries(['stop','retry','adopt','apply'].map(key => [key, () => {
        state.textContent = '합성 결과 · 채택/적용은 실행하지 않음';
      }])), false);
  }
}
