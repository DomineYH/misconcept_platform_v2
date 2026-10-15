const searchInput = document.getElementById('search-input');
const filterPublication = document.getElementById('filter-publication');
const filterStatus = document.getElementById('filter-status');
function filterScenarios() {
  let visible = 0;
  document.querySelectorAll('.scenario-card').forEach(card => {
    const matches = card.dataset.title.toLowerCase().includes(searchInput.value.toLowerCase())
      && (!filterPublication.value || card.dataset.publication === filterPublication.value)
      && (!filterStatus.value || card.classList.contains('inactive') === (filterStatus.value === 'inactive'));
    card.style.display = matches ? '' : 'none';
    if (matches) visible++;
  });
  document.getElementById('scenario-filter-empty').hidden = visible > 0 || document.querySelectorAll('.scenario-card').length === 0;
}
searchInput.addEventListener('input', filterScenarios);
filterPublication.addEventListener('change', filterScenarios);
filterStatus.addEventListener('change', filterScenarios);
document.querySelectorAll('.delete-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    if (!window.confirm(uiLanguage.t('admin.scenario_delete_confirm', {title:btn.dataset.title, count:btn.dataset.sessionCount}))) return;
    btn.disabled = true;
    try {
      const token = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
      const response = await fetch(`/admin/scenarios/${btn.dataset.id}/delete`, {
        method: 'POST',
        headers: uiLanguage.headers(`/admin/scenarios/${btn.dataset.id}/delete`, {'Content-Type': 'application/json', 'x-csrf-token': token ? token[1] : ''}),
        body: JSON.stringify({expected_version: Number(btn.dataset.version)})
      });
      if (response.ok) {
        const previous = document.querySelector('#scenarios-pagination [rel="prev"]');
        if (previous && document.querySelectorAll('.scenario-card').length === 1) {
          window.location.assign(previous.href);
        } else window.location.reload();
      } else window.alert(response.status === 409 ? uiLanguage.t('admin.another_administrator_made_changes_reload_and_check_again') : uiLanguage.t('admin.deletion_failed'));
    } catch {
      window.alert(uiLanguage.t('admin.deletion_failed'));
    } finally {
      btn.disabled = false;
    }
  });
});

uiLanguage.registerGuard(() => ({dirty:Boolean(searchInput.value || filterPublication.value || filterStatus.value), busy:[...document.querySelectorAll('.delete-btn')].some(button => button.disabled)}));
