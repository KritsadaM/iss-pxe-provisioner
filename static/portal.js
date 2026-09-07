let token = '';
const el = id => document.getElementById(id);
async function api(path, data) {
  const response = await fetch('/api/' + path, {method: data ? 'POST' : 'GET',
    headers: {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    body: data ? JSON.stringify(data) : undefined});
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.error || 'Request failed (' + response.status + ')');
  return value;
}
function message(text) { el('message').textContent = text; }
async function refresh() {
  if (!token) return;
  try {
    const rows = await api('devices'); el('devices').replaceChildren();
    if (!rows.length) el('devices').textContent = 'No deployments yet. Configure your first VM.';
    rows.forEach(row => {
      const card = document.createElement('article'); card.className = 'device';
      const title = document.createElement('strong'); title.textContent = row.hostname;
      const state = document.createElement('p'); state.className = 'state'; state.textContent = row.state;
      const detail = document.createElement('p'); detail.textContent = row.mac + ' · Ubuntu ' + row.release + ' · ' + row.disk;
      card.append(title, document.createElement('br'), state, detail);
      if (['armed','booting','installing','first_boot'].includes(row.state)) {
        const cancel = document.createElement('button'); cancel.textContent = 'Revoke deployment';
        cancel.onclick = async () => {try { const result = await api('cancel', {mac: row.mac}); message(result.note); await refresh(); } catch (e) {message(e.message);} };
        card.append(cancel);
      }
      el('devices').append(card);
    });
  } catch(e) { message(e.message); }
}
el('login').onsubmit = async event => {
  event.preventDefault(); token = new FormData(event.target).get('token');
  try {
    const data = await api('catalog'); el('releases').replaceChildren();
    Object.entries(data.images).forEach(([version]) => el('releases').add(new Option('Ubuntu Desktop ' + version, version)));
    el('packages').replaceChildren(new Option('OS only — skip ISS3', ''));
    Object.entries(data.packages).forEach(([key, value]) => el('packages').add(new Option('ISS3 ' + value.version + ' · ' + value.releases.join(', '), key)));
    el('fields').disabled = false;
    el('catalog').textContent = Object.keys(data.images).length + ' images ready · ' + data.base_url;
    message('Console unlocked. Import assets before arming a deployment.'); await refresh();
  } catch(e) {message(e.message);}
};
el('deploy').onsubmit = async event => {
  event.preventDefault();
  try { const result = await api('deploy', Object.fromEntries(new FormData(event.target)));
    event.target.elements.password.value = ''; message('Armed ' + result.mac + '. Start PXE boot on this VM.'); await refresh();
  } catch(e) {message(e.message);}
};
el('mode').onchange = () => {el('static-fields').hidden = el('mode').value !== 'static';};
el('refresh').onclick = refresh;
setInterval(refresh, 5000);
