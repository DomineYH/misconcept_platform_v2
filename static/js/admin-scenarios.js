const searchInput = document.getElementById('search-input');
const filterPublication = document.getElementById('filter-publication');
const filterStatus = document.getElementById('filter-status');
function filterScenarios() {
  document.querySelectorAll('.scenario-card').forEach(card => {
    const matches = card.dataset.title.toLowerCase().includes(searchInput.value.toLowerCase())
      && (!filterPublication.value || card.dataset.publication === filterPublication.value)
      && (!filterStatus.value || card.classList.contains('inactive') === (filterStatus.value === 'inactive'));
    card.style.display = matches ? '' : 'none';
  });
}
searchInput.addEventListener('input', filterScenarios);
filterPublication.addEventListener('change', filterScenarios);
filterStatus.addEventListener('change', filterScenarios);
document.querySelectorAll('.delete-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    if (!window.confirm(`"${btn.dataset.title}" 시나리오를 삭제하시겠습니까?\n관련 세션 ${btn.dataset.sessionCount}개도 함께 숨겨집니다.`)) return;
    btn.disabled = true;
    try {
      const token = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
      const response = await fetch(`/admin/scenarios/${btn.dataset.id}/delete`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'x-csrf-token': token ? token[1] : ''},
        body: JSON.stringify({expected_version: Number(btn.dataset.version)})
      });
      if (response.ok) window.location.reload();
      else window.alert(response.status === 409 ? '다른 관리자가 변경했습니다. 새로고침 후 확인하세요.' : '삭제에 실패했습니다.');
    } catch {
      window.alert('삭제에 실패했습니다.');
    } finally {
      btn.disabled = false;
    }
  });
});
