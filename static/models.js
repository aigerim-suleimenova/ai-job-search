// Searching the model list and the download progress. One file for the "Model"
// page and for the first-launch introduction.
const SHOW_ALL_KEY = 'aijs-models-show-unsupported';

function filterModels() {
  const q = document.getElementById('q').value.toLowerCase().trim();
  const box = document.getElementById('showall');
  const showAll = box.checked;
  try { localStorage.setItem(SHOW_ALL_KEY, showAll ? '1' : ''); } catch (e) { /* private mode */ }

  const rows = document.querySelectorAll('.model-row');
  let shown = 0, hiddenByMemory = 0;
  rows.forEach(row => {
    const matchQ = !q || row.dataset.name.includes(q);
    const fits = ['yes', 'tight'].includes(row.dataset.fits);
    const ok = matchQ && (showAll || fits);
    row.style.display = ok ? '' : 'none';
    if (ok) shown++;
    // "nothing found" because of a hidden filter is the worst kind of empty list,
    // so we count how many models matching the query we have kept back
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
  } catch (e) { /* the server is restarting — we simply wait */ }
}

// The box's state survives a page reload: after "Download" the page refreshes,
// and otherwise the filter choice was quietly lost.
try {
  const box = document.getElementById('showall');
  if (box) box.checked = !!localStorage.getItem(SHOW_ALL_KEY);
} catch (e) { /* private mode */ }

filterModels();

// The browser jumps to the anchor as soon as the page is parsed, while the list
// is filtered afterwards: rows hide, the height changes, and the place you wanted
// slides out of view. So we return to it ourselves once the list has settled.
if (location.hash) {
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target) target.scrollIntoView({ block: 'center' });
}

setInterval(pollPull, 2000);
pollPull();
