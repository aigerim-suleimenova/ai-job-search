// Поиск по списку моделей и прогресс скачивания. Один файл на страницу
// «Модель» и на знакомство при первом запуске.
const SHOW_ALL_KEY = 'aijs-models-show-unsupported';

function filterModels() {
  const q = document.getElementById('q').value.toLowerCase().trim();
  const box = document.getElementById('showall');
  const showAll = box.checked;
  try { localStorage.setItem(SHOW_ALL_KEY, showAll ? '1' : ''); } catch (e) { /* приватный режим */ }

  const rows = document.querySelectorAll('.model-row');
  let shown = 0, hiddenByMemory = 0;
  rows.forEach(row => {
    const matchQ = !q || row.dataset.name.includes(q);
    const fits = ['yes', 'tight'].includes(row.dataset.fits);
    const ok = matchQ && (showAll || fits);
    row.style.display = ok ? '' : 'none';
    if (ok) shown++;
    // «ничего не найдено» из-за скрытого фильтра — худший вид пустого списка,
    // поэтому считаем, сколько подходящих под запрос моделей мы утаили
    else if (matchQ && !fits) hiddenByMemory++;
  });

  const count = document.getElementById('count');
  const note = hiddenByMemory
    ? ' · ' + (count.dataset.hiddenNote || '{n}').replace('{n}', hiddenByMemory)
    : '';
  count.textContent = shown + ' / ' + rows.length + note;
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

// Состояние галочки переживает перезагрузку страницы: после «Скачать» она
// обновляется, и выбор фильтра иначе молча терялся.
try {
  const box = document.getElementById('showall');
  if (box) box.checked = !!localStorage.getItem(SHOW_ALL_KEY);
} catch (e) { /* приватный режим */ }

filterModels();

// Браузер прыгает к якорю сразу после разбора страницы, а список фильтруется
// уже потом: строки прячутся, высота меняется, и нужное место уезжает из виду.
// Поэтому возвращаемся к нему сами, когда список принял окончательный вид.
if (location.hash) {
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target) target.scrollIntoView({ block: 'center' });
}

setInterval(pollPull, 2000);
pollPull();
