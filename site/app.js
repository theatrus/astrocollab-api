/* The reference is rendered from the same OpenAPI document validated in CI. */
'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let contract;
let operations = [];
let toastTimer;
const groupNames = {capabilities:'Discovery', pairing:'Pairing', projects:'Projects & requirements', me:'Your account', participations:'Participation & planning', sync:'Snapshots', changes:'Change feed', submissions:'Submissions & assessment', uploads:'Resumable uploads', jobs:'Background jobs'};

function resolve(value) {
  if (!value?.$ref) return value;
  if (!value.$ref.startsWith('#/')) throw new Error('Unsupported external schema reference');
  return value.$ref.slice(2).split('/').reduce((node, key) => node[key.replace(/~1/g, '/').replace(/~0/g, '~')], contract);
}
function typeName(raw) {
  if (raw.$ref) return raw.$ref.split('/').at(-1);
  if (raw.const !== undefined) return JSON.stringify(raw.const);
  if (raw.type === 'array') return `array<${typeName(raw.items || {})}>`;
  if (raw.oneOf) return 'one of';
  if (raw.anyOf) return 'any of';
  return Array.isArray(raw.type) ? raw.type.join(' | ') : raw.type || 'schema';
}
function badge(method) { return `<span class="method ${method.toLowerCase()}">${escapeHtml(method.toUpperCase())}</span>`; }
function constraints(s) {
  const values = [];
  if (s.format) values.push(`format: ${s.format}`);
  if (s.enum) values.push(`values: ${s.enum.map(x => JSON.stringify(x)).join(', ')}`);
  if (s.const !== undefined) values.push(`constant: ${JSON.stringify(s.const)}`);
  for (const key of ['minimum','maximum','exclusiveMinimum','exclusiveMaximum','minLength','maxLength','minItems','maxItems','pattern','default','uniqueItems','additionalProperties']) {
    if (s[key] !== undefined) values.push(`${key}: ${JSON.stringify(s[key])}`);
  }
  return values.length ? `<div class="schema-constraints">${values.map(escapeHtml).join('<br>')}</div>` : '';
}
function schemaNode(raw, name = 'Body', required = false, depth = 0, chain = []) {
  const s = resolve(raw);
  const recursive = raw.$ref && chain.includes(raw.$ref);
  const nextChain = raw.$ref ? [...chain, raw.$ref] : chain;
  let inside = s.description ? `<p>${escapeHtml(s.description)}</p>` : '';
  inside += constraints(s);
  if (recursive || depth > 12) inside += '<p>See the full schema in the OpenAPI download for this recursive definition.</p>';
  else {
    if (s.properties) inside += Object.entries(s.properties).map(([key, value]) => schemaNode(value, key, s.required?.includes(key), depth + 1, nextChain)).join('');
    if (s.items) inside += schemaNode(s.items, 'Array item', false, depth + 1, nextChain);
    for (const combinator of ['oneOf','anyOf','allOf']) {
      if (s[combinator]) inside += `<div class="schema-group">${escapeHtml(combinator)} — ${combinator === 'allOf' ? 'all constraints apply' : 'allowed alternatives'}</div>` + s[combinator].map((part, i) => schemaNode(part, `${combinator} ${i + 1}`, false, depth + 1, nextChain)).join('');
    }
    if (s.if || s.not || s.then || s.else || s.propertyNames) inside += `<pre class="schema-constraints">${escapeHtml(JSON.stringify(s, null, 2))}</pre>`;
  }
  return `<details class="schema-node" ${depth === 0 ? 'open' : ''}><summary><code>${escapeHtml(name)}</code><span class="schema-type">${escapeHtml(typeName(raw))}</span>${required ? '<span class="required">required</span>' : ''}</summary><div class="schema-body">${inside || '<p>No additional constraints.</p>'}</div></details>`;
}
function codeBlock(value, label, isText = false) {
  const text = isText ? value : JSON.stringify(value, null, 2);
  return `<div class="sample"><div class="sample-head"><span>${escapeHtml(label)}</span><button type="button" data-copy>Copy</button></div><pre><code>${escapeHtml(text)}</code></pre></div>`;
}
function exampleOf(media) {
  if (media?.example !== undefined) return media.example;
  const example = Object.values(media?.examples || {})[0];
  return example?.value;
}
function curlExample(op) {
  const parameters = op.parameters?.map(resolve) || [];
  let url = contract.servers[0].url + op.path;
  for (const p of parameters.filter(p => p.in === 'path')) url = url.replace(`{${p.name}}`, p.schema.format === 'uuid' ? '00000000-0000-4000-8000-000000000001' : '1');
  const query = parameters.filter(p => p.in === 'query' && p.required).map(p => `${p.name}=${p.schema.type === 'integer' ? '1' : 'EXAMPLE_CURSOR'}`);
  if (query.length) url += '?' + query.join('&');
  const lines = [`curl --request ${op.method.toUpperCase()} '${url}'`];
  if (op['x-token-context'] !== 'public') lines.push(`  --header 'Authorization: Bearer YOUR_API_KEY'`);
  for (const p of parameters.filter(p => p.in === 'header' && p.required)) {
    let value = p.example || (p.name === 'Idempotency-Key' ? '00000000-0000-4000-8000-000000000090' : p.name === 'X-Part-SHA256' ? 'SHA256_OF_PART_BYTES' : p.name === 'Content-Length' ? 'ACTUAL_PART_BYTE_COUNT' : p.name === 'If-Match' ? '"ETAG_FROM_LAST_READ"' : 'VALUE');
    lines.push(`  --header '${p.name}: ${value}'`);
  }
  if (parameters.some(p => p.name === 'If-None-Match' && p.schema.const === '*')) lines.push(`  --header 'If-None-Match: *'`);
  const body = op.requestBody?.content;
  if (body?.['application/json']) {
    lines.push("  --header 'Content-Type: application/json'");
    const example = exampleOf(body['application/json']);
    if (example !== undefined) lines.push(`  --data '${JSON.stringify(example, null, 2).replace(/'/g, "'\\''")}'`);
  } else if (body?.['application/octet-stream']) {
    lines.push("  --header 'Content-Type: application/octet-stream'", "  --data-binary '@part.bin'");
  }
  return lines.join(' \\\n');
}
function renderSidebar() {
  const query = $('#endpoint-search').value.trim().toLowerCase();
  const method = $('#method-filter').value;
  const filtered = operations.filter(op => (!method || op.method.toUpperCase() === method) && `${op.method} ${op.path} ${op.summary} ${op.operationId} ${op['x-required-scopes'].join(' ')}`.toLowerCase().includes(query));
  $('#search-count').textContent = `${filtered.length} of ${operations.length} operations`;
  const groups = new Map();
  for (const op of filtered) {
    const group = op.tags[0];
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(op);
  }
  const selected = location.hash.split('/')[1];
  $('#operation-list').innerHTML = [...groups].map(([key, ops]) => `<div class="endpoint-group-title">${escapeHtml(groupNames[key] || key)}</div>${ops.map(op => `<a class="endpoint-nav" href="#operation/${op.operationId}" ${selected === op.operationId ? 'aria-current="page"' : ''}>${badge(op.method)}<span>${escapeHtml(op.summary)}</span></a>`).join('')}`).join('') || '<div class="empty-results">No matching operations. Try a method, path, or “framing”.</div>';
}
function renderReferenceHome() {
  $('#endpoint-content').innerHTML = `<p class="eyebrow">ASTROCOLLAB API / ${escapeHtml(contract.info.version)}</p><h1>API reference</h1><p class="reference-intro">${operations.length} operations. Schemas and examples come from the OpenAPI specification.</p><div class="notice">Draft specification. <code>collab.example</code> is a placeholder. This page does not send API requests.</div><div class="operation-cards">${[
    ['getCapabilities','Service discovery','Find the API root, service capabilities, formats and limits.'],
    ['joinProject','Join a project','Accept the terms and enroll with your API key.'],
    ['checkIn','Automatic framing','Request updated framing using equipment and project progress.'],
    ['createSubmission','Submit calibrated data','Send a manifest, resume verified parts, and receive assessments.'],
    ['createSnapshot','Project sync','Read a snapshot, then apply ordered changes.'],
    ['appendAssessment','Record quality and credit','Record assessments and count each capture once.']
  ].map(([id,title,description]) => `<a class="operation-card" href="#operation/${id}"><p class="eyebrow">${id}</p><h2>${title} ↗</h2><p>${description}</p></a>`).join('')}</div>`;
}
function renderOperation(op) {
  const parameters = (op.parameters || []).map(resolve);
  const context = op['x-token-context'].replaceAll('_', ' ');
  const scopes = op['x-required-scopes'];
  let html = `<p class="eyebrow">${escapeHtml(groupNames[op.tags[0]] || op.tags[0])} / ${escapeHtml(op.operationId)}</p><h1>${escapeHtml(op.summary)}</h1><div class="endpoint-path">${badge(op.method)}<code>${escapeHtml(op.path)}</code></div>${op.description ? `<p class="endpoint-description">${escapeHtml(op.description)}</p>` : ''}<div class="auth-box"><b>Authorization</b> · ${escapeHtml(context)}<p>${scopes.length ? scopes.map(scope => `<span class="scope">${escapeHtml(scope)}</span>`).join('') : 'No default scope required.'}</p>${op['x-scope-rules'] ? `<p>Conditional scope alternatives apply. Each inner list is AND; alternative lists are OR.</p>${codeBlock(op['x-scope-rules'],'SCOPE RULES')}` : ''}<p>The server also checks membership, project and resource ownership. <a class="text-link" href="#guide/authentication">Authentication guide ↗</a></p></div>`;
  if (parameters.length) html += `<section class="reference-section"><h2>Parameters</h2><div class="table-scroll"><table class="parameter-table"><thead><tr><th>NAME / LOCATION</th><th>TYPE / REQUIREMENT</th><th>DETAILS</th></tr></thead><tbody>${parameters.map(p => `<tr><td><code>${escapeHtml(p.name)}</code><small>${escapeHtml(p.in)}</small></td><td>${escapeHtml(typeName(p.schema))}<br><span class="${p.required ? 'required' : 'optional'}">${p.required ? 'required' : 'optional'}</span></td><td>${escapeHtml(p.description || '')}${constraints(p.schema)}</td></tr>`).join('')}</tbody></table></div></section>`;
  const request = op.requestBody;
  if (request) {
    html += '<section class="reference-section"><h2>Request body</h2>';
    if (request.description) html += `<p class="reference-note">${escapeHtml(request.description)}</p>`;
    for (const [mime, media] of Object.entries(request.content)) {
      html += `<p class="reference-note"><code>${escapeHtml(mime)}</code> · ${request.required ? 'Required' : 'Optional'}</p>${schemaNode(media.schema,'Request',request.required)}`;
      const value = exampleOf(media);
      if (value !== undefined) html += codeBlock(value, 'REQUEST EXAMPLE');
    }
    html += '</section>';
  }
  html += `<section class="reference-section"><h2>Request example</h2><p class="reference-note">Replace example IDs, credentials and preconditions before running this command.</p>${codeBlock(curlExample(op),'CURL',true)}</section><section class="reference-section"><h2>Responses</h2><label class="response-select">HTTP status<select id="response-status">${Object.entries(op.responses).map(([status,raw]) => `<option value="${escapeHtml(status)}">${escapeHtml(status)} — ${escapeHtml(resolve(raw).description)}</option>`).join('')}</select></label><div id="response-content"></div></section><p class="reference-note">See the <a href="#guide/protocol">protocol</a> for lifecycle, retry, privacy and astronomy requirements. <a href="./openapi/astrocollab.yaml" download>Download the full contract ↓</a></p>`;
  $('#endpoint-content').innerHTML = html;
  function updateResponse() {
    const status = $('#response-status').value;
    const response = resolve(op.responses[status]);
    let content = `<p class="reference-note">${escapeHtml(response.description)}</p>`;
    if (response.headers) content += `<p class="reference-note">Response headers: ${Object.entries(response.headers).map(([key,s]) => `<code>${escapeHtml(key)}</code> — ${escapeHtml(s.description || typeName(s.schema))}`).join('; ')}</p>`;
    for (const [mime, media] of Object.entries(response.content || {})) {
      content += `<p class="reference-note"><code>${escapeHtml(mime)}</code></p>${schemaNode(media.schema, 'Response')}`;
      const value = exampleOf(media);
      if (value !== undefined) content += codeBlock(value, 'RESPONSE EXAMPLE');
    }
    $('#response-content').innerHTML = content;
  }
  $('#response-status').addEventListener('change',updateResponse);
  updateResponse();
}
function renderGuide(name, anchor) {
  const template = document.getElementById(`guide-${name}`);
  if (!template) { $('#guide-content').innerHTML = '<h1>Guide not found</h1><p><a href="#guide/how-it-works">Read how the API works</a></p>'; $('#guide-toc').innerHTML = ''; return; }
  $('#guide-content').replaceChildren(template.content.cloneNode(true));
  for (const table of $('#guide-content').querySelectorAll('table')) {
    const wrapper = document.createElement('div'); wrapper.className = 'table-scroll'; table.replaceWith(wrapper); wrapper.append(table);
  }
  const headings = [...$('#guide-content').querySelectorAll('h2')];
  $('#guide-toc').innerHTML = `<p class="eyebrow">ON THIS PAGE</p>${headings.map(h => `<a href="#guide/${name}/${h.id}">${escapeHtml(h.textContent)}</a>`).join('')}`;
  $('.guide-sidebar nav').querySelectorAll('a').forEach(a => a.setAttribute('aria-current', a.hash === `#guide/${name}` ? 'page' : 'false'));
  document.title = `${$('#guide-content h1')?.textContent || 'Guide'} — AstroCollab`;
  if (anchor) requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView());
}
function route() {
  const [name = 'overview', item, anchor] = location.hash.slice(1).split('/');
  const view = ['reference','operation'].includes(name) ? 'reference' : name === 'guide' ? 'guide' : 'overview';
  document.querySelectorAll('.view').forEach(el => { el.hidden = el.id !== `${view}-view`; });
  document.querySelectorAll('[data-nav]').forEach(a => a.setAttribute('aria-current', a.dataset.nav === view ? 'page' : 'false'));
  document.title = 'AstroCollab — Collaborative astrophotography API';
  if (view === 'guide') renderGuide(item || 'how-it-works',anchor);
  if (view === 'reference') {
    if (!contract) return;
    renderSidebar();
    const op = operations.find(o => o.operationId === item);
    if (name === 'operation' && !op) $('#endpoint-content').innerHTML = '<div class="error-panel"><h2>Operation not found</h2><a href="#reference">Return to the API reference →</a></div>';
    else if (op) { renderOperation(op); document.title = `${op.summary} — AstroCollab API`; }
    else { renderReferenceHome(); document.title = 'API reference — AstroCollab'; }
    if (window.innerWidth <= 760 && op) {
      $('#operation-list').hidden = true; $('#sidebar-toggle').setAttribute('aria-expanded','false'); $('#sidebar-toggle').textContent = 'Show endpoints';
    }
  }
  if (!anchor) window.scrollTo({top:0,behavior:'instant'});
}
document.addEventListener('click', async event => {
  if (event.target.closest('.skip-link')) { event.preventDefault(); $('#main').focus(); return; }
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  const text = button.closest('.sample').querySelector('code').textContent;
  try {
    await navigator.clipboard.writeText(text);
    $('#toast').textContent = 'Copied to clipboard'; $('#toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 2400);
  } catch {
    const range = document.createRange(); range.selectNodeContents(button.closest('.sample').querySelector('code'));
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    button.textContent = 'Text selected — copy manually';
  }
});
function searchOperations() {
  renderSidebar(); $('#operation-list').hidden = false;
  $('#sidebar-toggle').setAttribute('aria-expanded','true'); $('#sidebar-toggle').textContent = 'Hide endpoints';
}
$('#endpoint-search').addEventListener('input',searchOperations);
$('#method-filter').addEventListener('change',searchOperations);
window.addEventListener('resize', () => {
  if (window.innerWidth > 760) { $('#operation-list').hidden = false; $('#sidebar-toggle').setAttribute('aria-expanded','true'); $('#sidebar-toggle').textContent = 'Hide endpoints'; }
});
$('#sidebar-toggle').addEventListener('click',() => {
  const hidden = !$('#operation-list').hidden; $('#operation-list').hidden = hidden;
  $('#sidebar-toggle').setAttribute('aria-expanded',String(!hidden)); $('#sidebar-toggle').textContent = hidden ? 'Show endpoints' : 'Hide endpoints';
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && !event.ctrlKey && !event.metaKey) {
    event.preventDefault(); if ($('#reference-view').hidden) location.hash = '#reference';
    setTimeout(() => $('#endpoint-search').focus(),0);
  }
});
window.addEventListener('hashchange',route);
route();
fetch('./openapi/astrocollab.json').then(response => {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}).then(data => {
  contract = data;
  operations = Object.entries(contract.paths).flatMap(([path,item]) => Object.entries(item).filter(([method]) => ['get','post','put','patch','delete'].includes(method)).map(([method,op]) => ({...op,path,method})));
  route();
}).catch(error => {
  $('#endpoint-content').innerHTML = `<div class="error-panel"><h1>Reference could not load</h1><p>${escapeHtml(error.message)}. Reload this page or download the complete contract.</p><a href="./openapi/astrocollab.yaml">Download OpenAPI →</a></div>`;
});
