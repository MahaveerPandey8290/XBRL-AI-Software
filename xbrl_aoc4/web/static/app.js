// Autosave every field as soon as it changes.
function postJSON(url, data) {
  return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)})
    .then(r => r.json().then(j => { if (!r.ok) throw new Error(j.error || r.statusText); return j; }));
}

document.addEventListener('change', function (ev) {
  const el = ev.target;
  if (!el.dataset || !el.dataset.eid) return;
  const fid = document.body.dataset.fid;
  const payload = {eid: el.dataset.eid, pk: el.dataset.pk, value: el.value};
  if (el.dataset.tid) { payload.tid = el.dataset.tid; payload.col = el.dataset.col; }
  postJSON('/api/f/' + fid + '/value', payload)
    .then(() => { el.classList.remove('saveerr'); el.classList.add('saved'); setTimeout(() => el.classList.remove('saved'), 900); })
    .catch(() => el.classList.add('saveerr'));
});

function addColumn(tid) {
  const fid = document.body.dataset.fid;
  const dims = {};
  document.querySelectorAll('[data-axis]').forEach(s => { dims[s.dataset.axis] = s.value; });
  postJSON('/api/f/' + fid + '/table/' + tid + '/add', {dims: dims})
    .then(() => location.reload())
    .catch(e => alertBox(e.message));
}

function removeColumn(tid, col) {
  if (!confirmBox('Remove this column and its values?')) return;
  const fid = document.body.dataset.fid;
  postJSON('/api/f/' + fid + '/table/' + tid + '/remove', {col: col}).then(() => location.reload());
}

function alertBox(msg) {
  const d = document.getElementById('msg');
  if (d) { d.textContent = msg; d.style.display = 'block'; } else { console.log(msg); }
}
function confirmBox(msg) { return window.confirm(msg); }

function loadDoc(sel, target) {
  const fid = document.body.dataset.fid;
  if (!sel.value) return;
  fetch('/api/f/' + fid + '/doc/' + encodeURIComponent(sel.value)).then(r => r.json()).then(j => {
    const ta = document.getElementById(target);
    ta.value = j.html;
    ta.dispatchEvent(new Event('change', {bubbles: true}));
    sel.value = '';
  });
}
