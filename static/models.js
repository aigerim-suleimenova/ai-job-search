// Поиск по списку моделей и прогресс скачивания. Один файл на страницу
// «Модель» и на знакомство при первом запуске.
function filterModels() {
  const q = document.getElementById('q').value.toLowerCase().trim();
  const onlyFits = document.getElementById('onlyfits').checked;
  let shown = 0;
  document.querySelectorAll('.model-row').forEach(row => {
    const matchQ = !q || row.dataset.name.includes(q);
    const matchF = !onlyFits || ['yes', 'tight'].includes(row.dataset.fits);
    const ok = matchQ && matchF;
    row.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  document.getElementById('count').textContent = shown + ' / ' + document.querySelectorAll('.model-row').length;
}

async function pollPull() {
  const card = document.getElementById('pull-card');
  if (!card) return;
  try {
    const d = await (await fetch('/models/pull_status')).json();
    if (!d.model || (d.done && !d.error)) {
      if (card.hidden === false && d.done) location.reload();
      card.hidden = true;
      return;
    }
    card.hidden = false;
    document.getElementById('pull-model').textContent = d.model;
    document.getElementById('pull-bar').style.width = (d.percent || 0) + '%';
    document.getElementById('pull-status').textContent =
      d.error ? d.error : (d.status || '') + (d.percent ? ' — ' + d.percent + '%' : '');
  } catch (e) { /* сервер перезапускается — просто ждём */ }
}

filterModels();
setInterval(pollPull, 2000);
pollPull();
