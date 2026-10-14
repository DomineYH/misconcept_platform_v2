// Fixture-only: all writes are simulated rejection; inputs remain available.
const realFetch = window.fetch.bind(window);
window.fetch = async (input, options = {}) => {
  const url = new URL(input instanceof Request ? input.url : input, location.href);
  const method = options.method || (input instanceof Request ? input.method : 'GET');
  if (method.toUpperCase() !== 'GET') {
    document.getElementById('prototype-state').textContent = '합성 자료 · 쓰기 요청 차단 · 입력 보존';
    return new Response(JSON.stringify({detail:{code:'prototype_only', current_version:1}}),
      {status:409, headers:{'Content-Type':'application/json'}});
  }
  if (url.pathname.startsWith('/admin/comparison')) {
    const snapshot = document.getElementById('comparison-conversation-data');
    const value = url.pathname.endsWith('/prototype') && snapshot
      ? JSON.parse(snapshot.textContent).snapshot : {items:[], next_cursor:null};
    return new Response(JSON.stringify(value), {headers:{'Content-Type':'application/json'}});
  }
  return realFetch(input, options);
};
document.addEventListener('submit', event => event.preventDefault(), true);
document.addEventListener('htmx:beforeRequest', event => event.preventDefault());
