const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const variableName = (item) => item.short_name || item.name;
const variableGroup = (item) => item.name !== variableName(item) ? `Group ${item.name}` : '';
const variableListName = (item) => variableGroup(item) ? `${variableName(item)} (${item.name})` : variableName(item);
const realmNames = { atmos: 'Atmosphere', atmosChem: 'Atmospheric chemistry', land: 'Land', ocean: 'Ocean', ocnBgchem: 'Ocean biogeochemistry', seaIce: 'Sea ice' };
const realmLabel = (realm) => realmNames[realm] || realm;
const state = { yaml: '', summary: null, name: '', libraryInfo: null, dirty: false, selected: null, jobs: [], selectedJob: null, parseTimer: null, draftTimer: null, figureUrl: null, figureSource: null, figureRequest: 0, catalogue: null, builderProfile: '', builderBrick: '', builderEditing: false, isNewRecipe: false, recipePreviewTimer: null, recipePreviewVersion: 0, configFiles: [], configSummary: null, configVersion: 0 };
let accessToken = sessionStorage.getItem('esmval-gui-access-token') || '';
const draftKey = 'esmval-gui-recipe-draft';
function persistDraft() {
  clearTimeout(state.draftTimer);
  if (!state.dirty || !state.yaml) { sessionStorage.removeItem(draftKey); return; }
  state.draftTimer = setTimeout(() => {
    try { sessionStorage.setItem(draftKey, JSON.stringify({yaml: state.yaml, name: state.name, savedAt: Date.now()})); }
    catch (_) { toast('Draft recovery is unavailable in this browser session.', true); }
  }, 500);
}
function canReplaceRecipe() {
  return !state.dirty || window.confirm('This recipe has edits that have not been downloaded. Replace it and discard those edits?');
}
window.addEventListener('beforeunload', (event) => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
});

function showAuth(message = '') {
  accessToken = '';
  sessionStorage.removeItem('esmval-gui-access-token');
  document.querySelector('.shell').inert = true;
  $('authError').textContent = message;
  $('authGate').hidden = false;
  $('authTokenInput').value = '';
  $('authTokenInput').focus();
}

async function api(path, options = {}) {
  const response = await fetch('/api' + path, { ...options, headers: { 'Content-Type': 'application/json', ...(accessToken ? {'X-ESMVal-Token': accessToken} : {}), ...options.headers } });
  const body = await response.json();
  if (response.status === 401) showAuth('The token is missing or no longer valid. Enter the token from the server terminal.');
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body));
  return body;
}
function post(path, body) { return api(path, { method: 'POST', body: JSON.stringify(body) }); }
const esgfProjects = ['CMIP6', 'CMIP5', 'CMIP3', 'CMIP7', 'obs4MIPs'];
function esgfSearchMarkup() {
  return `<div class="esgf-search"><div class="eyebrow">FIND ON ESGF</div><div class="esgf-search-controls"><select class="esgf-project" aria-label="ESGF project">${esgfProjects.map((project) => `<option value="${project}">${project}</option>`).join('')}</select><input class="esgf-query" aria-label="Search model or dataset" placeholder="Model name, e.g. ACCESS" autocomplete="off"><button class="button subtle small-button esgf-go" type="button">Search</button></div><div class="esgf-filters"><input class="esgf-experiment" aria-label="Filter ESGF experiment" placeholder="Experiment" autocomplete="off"><input class="esgf-variable" aria-label="Filter ESGF variable" placeholder="Variable" autocomplete="off"><input class="esgf-ensemble" aria-label="Filter ESGF ensemble" placeholder="Ensemble" autocomplete="off"></div><p class="esgf-status hint" role="status">Search ESGF to fill the dataset fields, or enter them manually. Refine by experiment or variable if needed.</p><div class="esgf-results" role="listbox" aria-label="ESGF datasets"></div></div>`;
}
function attachEsgfSearch(form, fields, onApplied = () => {}) {
  const panel = form.querySelector('.esgf-search');
  const query = panel.querySelector('.esgf-query');
  const project = panel.querySelector('.esgf-project');
  const experiment = panel.querySelector('.esgf-experiment');
  const variable = panel.querySelector('.esgf-variable');
  const ensemble = panel.querySelector('.esgf-ensemble');
  const status = panel.querySelector('.esgf-status');
  const results = panel.querySelector('.esgf-results');
  const button = panel.querySelector('.esgf-go');
  let timer; let sequence = 0;
  const search = async () => {
    const term = query.value.trim();
    if (term.length < 2) { status.textContent = 'Enter at least two characters from a model name.'; results.replaceChildren(); return; }
    const current = ++sequence;
    button.disabled = true; status.textContent = 'Searching ESGF…'; results.replaceChildren();
    try {
      const data = await api(`/esgf/datasets?${new URLSearchParams({query: term, project: project.value, experiment: experiment.value.trim(), variable: variable.value.trim(), ensemble: ensemble.value.trim()})}`);
      if (current !== sequence || !form.isConnected) return;
      status.textContent = data.results.length ? `${data.results.length} matching records${data.total > data.results.length ? ` from ${data.total} ESGF hits; refine your search for more` : ''}. Choose one to fill the fields.` : 'No matching datasets found. Try a longer model name or enter facets manually.';
      results.innerHTML = data.results.map((item, index) => `<button type="button" class="esgf-result" data-esgf-index="${index}" role="option"><strong>${escapeHtml(item.dataset)}</strong><span>${escapeHtml([item.exp, item.ensemble, item.grid].filter(Boolean).join(' · ') || item.project)}</span><small>${escapeHtml([item.mip, item.variable, item.version && `v${item.version}`].filter(Boolean).join(' · '))}</small></button>`).join('');
      results.querySelectorAll('[data-esgf-index]').forEach((choice) => choice.addEventListener('click', () => {
        const item = data.results[Number(choice.dataset.esgfIndex)];
        for (const [facet, selector] of Object.entries(fields)) {
          const input = form.querySelector(selector);
          if (input && item[facet] !== undefined) input.value = item[facet];
        }
        status.textContent = `Selected ${item.dataset}. Review the facets before adding it; ESGF availability does not confirm files on Gadi.`;
        results.replaceChildren(); onApplied(item);
      }));
    } catch (error) {
      if (current === sequence && form.isConnected) status.textContent = error.message;
    } finally { if (current === sequence && form.isConnected) button.disabled = false; }
  };
  const queueSearch = () => {
    clearTimeout(timer); sequence++; button.disabled = false; results.replaceChildren();
    if (query.value.trim().length >= 2) timer = setTimeout(search, 500);
    else status.textContent = 'Enter at least two characters from a model name.';
  };
  button.addEventListener('click', () => { clearTimeout(timer); search(); });
  query.addEventListener('input', queueSearch);
  experiment.addEventListener('input', queueSearch);
  variable.addEventListener('input', queueSearch);
  ensemble.addEventListener('input', queueSearch);
  query.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); clearTimeout(timer); search(); } });
  project.addEventListener('change', () => { if (query.value.trim().length >= 2) search(); });
}
function attachScriptPicker(container, pathInput) {
  const picker = document.createElement('details');
  picker.className = 'script-picker';
  picker.innerHTML = '<summary>Browse recipe diagnostic scripts</summary><input type="search" aria-label="Search diagnostic scripts" placeholder="Search by script or purpose"><div class="script-results"></div>';
  container.append(picker);
  const input = picker.querySelector('input');
  const results = picker.querySelector('.script-results');
  let timer; let sequence = 0;
  async function search() {
    const current = ++sequence;
    results.textContent = 'Searching scripts…';
    try {
      const found = await api('/scripts?' + new URLSearchParams({query: input.value.trim()}));
      if (!picker.isConnected || current !== sequence) return;
      results.innerHTML = found.scripts.map((item) => `<button type="button" class="script-result" data-path="${escapeHtml(item.path)}"><strong>${escapeHtml(item.path)}</strong><small>${escapeHtml(item.summary || `Used in ${item.used_by} recipe definition${item.used_by === 1 ? '' : 's'}.`)}</small></button>`).join('') || '<p class="hint">No matching local scripts. You can enter a path manually.</p>';
      if (found.total > found.scripts.length) results.insertAdjacentHTML('beforeend', `<p class="hint">Showing ${found.scripts.length} of ${found.total}; refine the search.</p>`);
      results.querySelectorAll('[data-path]').forEach((button) => button.addEventListener('click', () => {
        pathInput.value = button.dataset.path; pathInput.dispatchEvent(new Event('input', {bubbles: true})); picker.open = false;
      }));
    } catch (error) { if (current === sequence) results.textContent = error.message; }
  }
  picker.addEventListener('toggle', () => { if (picker.open) search(); });
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); clearTimeout(timer); search(); } });
}
function toast(message, error = false) {
  const node = $('toast'); node.textContent = message; node.classList.toggle('error', error); node.classList.add('show');
  clearTimeout(node.timer); node.timer = setTimeout(() => node.classList.remove('show'), 4200);
}
function setTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
  ['pipeline', 'builder', 'yaml', 'config', 'runs'].forEach((tab) => $(tab + 'Tab').classList.toggle('active', tab === name));
  if (name === 'runs') refreshJobs();
  if (name === 'builder') renderBuilder();
}
function configOrigin(summary, path) {
  return summary.origins.find((item) => JSON.stringify(item.path) === JSON.stringify(path))?.file || '';
}
function configSourceBadge(summary, path) {
  const source = configOrigin(summary, path);
  return source ? `<span class="config-source" title="Defined in ${escapeHtml(source)}">${escapeHtml(source.split('/').pop())}</span>` : '';
}
function configValue(value, key) {
  if (/(password|token|secret|credential|api[_-]?key|private[_-]?key)/i.test(key)) return '<span class="config-redacted">•••••• (hidden)</span>';
  if (value === null) return '<code>null</code>';
  if (Array.isArray(value)) return `<code>${escapeHtml(JSON.stringify(value))}</code>`;
  return `<code>${escapeHtml(String(value))}</code>`;
}
function configTree(value, path, summary) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return `<div class="config-tree">${Object.entries(value).map(([key, child]) => {
      const next = [...path, key];
      if (/(password|token|secret|credential|api[_-]?key|private[_-]?key)/i.test(key)) return `<div class="config-leaf"><span class="config-leaf-key">${escapeHtml(key)}</span><span class="config-leaf-value"><span class="config-redacted">•••••• (hidden)</span></span>${configSourceBadge(summary, next)}</div>`;
      if (child && typeof child === 'object' && !Array.isArray(child)) {
        return `<details class="config-branch"><summary><strong>${escapeHtml(key)}</strong><span class="config-child-count">${Object.keys(child).length} settings</span></summary>${configTree(child, next, summary)}</details>`;
      }
      return `<div class="config-leaf"><span class="config-leaf-key">${escapeHtml(key)}</span><span class="config-leaf-value">${configValue(child, key)}</span>${configSourceBadge(summary, next)}</div>`;
    }).join('')}</div>`;
  }
  return `<div class="config-leaf">${configValue(value, path.at(-1) || '')}${configSourceBadge(summary, path)}</div>`;
}
function renderConfig() {
  const files = state.configFiles;
  $('configFileList').innerHTML = files.length ? `<div class="config-section-label">MERGE ORDER <span>low → high priority</span></div>${files.map((file, index) => `<div class="config-file"><span class="config-file-index">${index + 1}</span><div class="config-file-name" title="${escapeHtml(file.name)}"><strong>${escapeHtml(file.name)}</strong><small>${index === files.length - 1 ? 'Highest priority' : `${Math.round(file.content.length / 1024)} KB`}</small></div><div class="config-file-actions"><button data-config-action="up" data-index="${index}" type="button" aria-label="Move ${escapeHtml(file.name)} earlier" ${index === 0 ? 'disabled' : ''}>↑</button><button data-config-action="down" data-index="${index}" type="button" aria-label="Move ${escapeHtml(file.name)} later" ${index === files.length - 1 ? 'disabled' : ''}>↓</button><button data-config-action="remove" data-index="${index}" type="button" aria-label="Remove ${escapeHtml(file.name)}">×</button></div></div>`).join('')}` : '<div class="config-empty">Open one or more YAML files, a local folder, or a configuration directory on Gadi.</div>';
  const summary = state.configSummary;
  if (!summary) { ['configOverview','configSources','configSettings','configOverrides'].forEach((id) => $(id).replaceChildren()); return; }
  const count = summary.counts;
  $('configOverview').innerHTML = `<div class="config-stats"><div><strong>${count.files}</strong><span>files</span></div><div><strong>${count.sections}</strong><span>sections</span></div><div><strong>${count.projects}</strong><span>projects</span></div><div><strong>${count.data_sources}</strong><span>data sources</span></div></div>`;
  const projects = summary.effective.projects;
  $('configSources').innerHTML = projects && typeof projects === 'object' && !Array.isArray(projects) && Object.keys(projects).length ? `<div class="config-section-label">DATA LOCATIONS</div><div class="config-projects">${Object.entries(projects).map(([project, settings]) => {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return '';
    const data = settings.data && typeof settings.data === 'object' && !Array.isArray(settings.data) ? settings.data : {};
    return `<section class="config-project"><h3>${escapeHtml(project)} <small>${Object.keys(data).length} sources</small></h3>${Object.entries(data).map(([source, values]) => `<details class="config-data-source"><summary>${escapeHtml(source)}</summary>${configTree(values, ['projects', project, 'data', source], summary)}</details>`).join('') || '<p class="config-muted">No data sources defined.</p>'}${Object.keys(settings).filter((key) => key !== 'data').length ? `<details class="config-project-other"><summary>Other ${escapeHtml(project)} settings</summary>${configTree(Object.fromEntries(Object.entries(settings).filter(([key]) => key !== 'data')), ['projects', project], summary)}</details>` : ''}</section>`;
  }).join('')}</div>` : '';
  const other = Object.fromEntries(Object.entries(summary.effective).filter(([key]) => key !== 'projects'));
  $('configSettings').innerHTML = `<div class="config-section-label">MERGED SETTINGS</div><div class="config-settings-card">${Object.keys(other).length ? configTree(other, [], summary) : '<p class="config-muted">No other settings in these files.</p>'}</div>`;
  $('configOverrides').innerHTML = summary.overrides.length ? `<details class="config-overrides"><summary>${summary.overrides.length} overridden setting${summary.overrides.length === 1 ? '' : 's'}</summary>${summary.overrides.map((item) => `<div><code>${escapeHtml(item.path.join('.'))}</code><span>${escapeHtml(item.previous.split('/').pop())} → ${escapeHtml(item.file.split('/').pop())}</span></div>`).join('')}</details>` : '';
}
async function inspectConfig() {
  const version = ++state.configVersion;
  state.configSummary = null;
  renderConfig();
  if (!state.configFiles.length) { $('configStatus').textContent = ''; return; }
  $('configStatus').textContent = 'Merging configuration files…';
  try {
    const result = await post('/config/inspect', {files: state.configFiles});
    if (version !== state.configVersion) return;
    state.configSummary = result;
    $('configStatus').textContent = `Showing ${result.files.length} selected file${result.files.length === 1 ? '' : 's'} in priority order.`;
  } catch (error) { if (version === state.configVersion) $('configStatus').textContent = error.message; }
  if (version === state.configVersion) renderConfig();
}
async function addConfigFiles(files) {
  const incoming = [...files].filter((file) => /\.ya?ml$/i.test(file.name)).sort((a,b) => a.name.localeCompare(b.name, 'en'));
  if (!incoming.length) { $('configStatus').textContent = 'Choose .yml or .yaml files.'; return; }
  const names = new Set(state.configFiles.map((file) => file.name));
  const additions = [];
  try {
    for (const file of incoming) {
      if (file.size > 1_000_000) throw new Error(`${file.name}: file exceeds the 1 MB limit.`);
      let name = file.webkitRelativePath || file.name;
      if (names.has(name)) { let number = 2; while (names.has(`${name} (${number})`)) number++; name = `${name} (${number})`; }
      names.add(name);
      additions.push({name, content: await file.text()});
    }
    if (state.configFiles.length + additions.length > 50 || [...state.configFiles, ...additions].reduce((total, file) => total + file.content.length, 0) > 3_000_000) throw new Error('Choose at most 50 files with no more than 3 MB of YAML in total.');
    state.configFiles.push(...additions); await inspectConfig();
  } catch (error) { $('configStatus').textContent = error.message; }
}
function setYamlFeedback(message = '', kind = '') {
  const feedback = $('yamlFeedback');
  feedback.textContent = message;
  feedback.className = `yaml-feedback${kind ? ` ${kind}` : ''}`;
  feedback.hidden = !message;
}
function updateYamlFeedback(summary) {
  const messages = summary?.messages || [];
  setYamlFeedback(messages.length ? messages.join('\n') : '', messages.length ? 'issue' : '');
}
function setRecipe(yaml, name, dirty = false, libraryInfo = null, isNewRecipe = false) {
  state.libraryRequest = (state.libraryRequest || 0) + 1;
  state.yaml = yaml; state.name = name; state.libraryInfo = libraryInfo; state.dirty = dirty; state.selected = null; state.builderProfile = ''; state.builderBrick = ''; state.isNewRecipe = isNewRecipe;
  $('yamlEditor').value = yaml; $('documentName').textContent = name; $('yamlFileName').textContent = name.split('/').pop() || 'recipe.yml'; $('dirtyMark').hidden = !dirty;
  persistDraft();
  setYamlFeedback(); $('recipeGuide').hidden = true;
  parseCurrent(); setTab('pipeline');
}
async function parseCurrent() {
  if (!state.yaml.trim()) {
    state.summary = null; state.selected = null;
    $('validationStatus').textContent = 'Empty YAML'; $('validationStatus').classList.add('invalid');
    $('overview').innerHTML = ''; $('messages').innerHTML = '';
    $('pipeline').innerHTML = '<div class="empty-state">Add recipe YAML to see its structure.</div>';
    $('recipeTitle').textContent = state.name || 'Recipe'; $('recipeDescription').textContent = 'Add recipe YAML to begin.';
    $('recipeStory').hidden = true;
    $('recipeGuide').hidden = true;
    $('builderSteps').innerHTML = '<p class="empty">Add recipe YAML to edit preprocessors.</p>';
    setYamlFeedback('Add recipe YAML to begin.', 'issue');
    return;
  }
  const source = state.yaml;
  try {
    const summary = await post('/parse', { yaml: source });
    if (state.yaml !== source) return;
    state.summary = summary; renderSummary(); updateYamlFeedback(summary);
    $('validationStatus').textContent = summary.messages.length ? `${summary.messages.length} structure issue${summary.messages.length === 1 ? '' : 's'}` : 'YAML structure checked';
    $('validationStatus').classList.toggle('invalid', !!summary.messages.length);
  } catch (err) {
    if (state.yaml !== source) return;
    state.summary = null; $('validationStatus').textContent = 'YAML error'; $('validationStatus').classList.add('invalid');
    setYamlFeedback(err.message, 'error');
    $('messages').innerHTML = `<div class="message error-message">${escapeHtml(err.message)}</div>`;
    $('pipeline').innerHTML = '<div class="empty-state">Fix the YAML error to see the pipeline.</div>';
    $('overview').innerHTML = '';
    $('recipeTitle').textContent = state.name || 'Recipe'; $('recipeDescription').textContent = 'Fix the YAML error to see the recipe.';
    $('recipeStory').hidden = true;
    $('recipeGuide').hidden = true;
    $('builderSteps').innerHTML = '<p class="empty">Fix the YAML to edit preprocessors.</p>';
  }
}
function renderSummary() {
  const summary = state.summary; if (!summary) return;
  $('recipeTitle').textContent = summary.title;
  $('recipeDescription').textContent = summary.description || 'ESMValTool recipe';
  renderRecipeStory();
  renderRecipeGuide();
  $('overview').innerHTML = [
    ['Datasets', summary.counts.datasets, '◫'],
    ['Named profiles', summary.counts.profiles, '▤'],
    ['Variables', summary.counts.variables, '◌'],
    ['Diagnostics', summary.counts.diagnostics, '◇'],
  ].map(([label, count, icon]) => `<div class="stat"><span class="stat-icon">${icon}</span><div><strong>${count}</strong><span>${label}</span></div></div>`).join('');
  const datasets = summary.graph.datasets.map((item) => `<button class="pipeline-item dataset" data-kind="dataset" data-id="${escapeHtml(item.id)}"><div class="item-icon">◫</div><div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail || item.scope)}${item.definition_count > 1 ? ` · ${item.definition_count} inputs` : ''}</small></div><span class="item-arrow">›</span></button>`).join('') || '<p class="empty">No explicit datasets</p>';
  const profiles = pipelineProfiles().map((item) => `<button class="pipeline-item profile" data-kind="profile" data-id="${escapeHtml(item.id)}"><div class="item-icon">▤</div><div><strong>${escapeHtml(item.label)}</strong><small>${item.default ? 'ESMValCore defaults' : `${item.steps.length} steps${item.custom_order ? ' · custom order' : ''}`}</small></div><span class="item-arrow">›</span></button>`).join('') || '<p class="empty">No preprocessing profiles</p>';
  const variables = summary.graph.variables.map((item) => `<button class="pipeline-item variable" data-kind="variable" data-id="${escapeHtml(item.id)}" title="${escapeHtml(`${variableName(item)}${variableGroup(item) ? ` · ${variableGroup(item)}` : ''} · ${item.diagnostic} · ${item.profile}`)}"><div class="item-icon">◌</div><div><strong>${escapeHtml(variableName(item))}</strong><small>${escapeHtml(variableGroup(item) ? `${variableGroup(item)} · ${item.diagnostic}` : `${item.diagnostic} · ${item.profile}`)}</small></div><span class="item-arrow">›</span></button>`).join('') || '<p class="empty">No variables</p>';
  const diagnostics = summary.graph.diagnostics.map((item) => `<button class="pipeline-item diagnostic" data-kind="diagnostic" data-id="${escapeHtml(item.id)}"><div class="item-icon">◇</div><div><strong>${escapeHtml(item.label)}</strong><small>${item.variables.length} variables · ${item.scripts.length} scripts</small></div><span class="item-arrow">›</span></button>`).join('') || '<p class="empty">No diagnostics</p>';
  const heading = (number, label, kind) => `<div class="column-title"><div><span>${number}</span> ${label}</div><button class="column-add" data-create-kind="${kind}" type="button" title="Add ${kind === 'profile' ? 'preprocessor' : kind}" aria-label="Add ${kind === 'profile' ? 'preprocessor' : kind}">+</button></div>`;
  $('pipeline').innerHTML = `<svg id="pipelineConnections" class="pipeline-connections" aria-hidden="true"></svg><div class="pipeline-column">${heading('01', 'DATASETS', 'dataset')}${datasets}</div><div class="pipeline-column">${heading('02', 'PREPROCESSORS', 'profile')}${profiles}</div><div class="pipeline-column">${heading('03', 'VARIABLES', 'variable')}${variables}</div><div class="pipeline-column">${heading('04', 'DIAGNOSTICS', 'diagnostic')}${diagnostics}</div>`;
  $('pipeline').querySelectorAll('button[data-kind]').forEach((button) => button.addEventListener('click', () => selectItem(button.dataset.kind, button.dataset.id)));
  $('pipeline').querySelectorAll('[data-create-kind]').forEach((button) => button.addEventListener('click', () => mountCreateForm(button.dataset.createKind)));
  $('messages').innerHTML = summary.messages.map((message) => `<div class="message error-message">${escapeHtml(message)}</div>`).join('');
  if (state.selected) selectItem(state.selected.kind, state.selected.id);
  else updateConnections();
  renderBuilder();
}
function renderRecipeGuide() {
  const guide = $('recipeGuide');
  guide.hidden = !state.isNewRecipe;
  if (!state.isNewRecipe) return;
  const hasScript = state.summary.graph.diagnostics.some((diagnostic) => diagnostic.scripts.some((script) => script.path));
  const hasProfile = state.summary.graph.profiles.length > 0;
  const unassigned = state.summary.graph.variables.find((variable) => variable.profile === 'default');
  const profileAction = hasProfile && unassigned ? '<button type="button" data-guide="assign">Assign preprocessor →</button>'
    : `<button type="button" data-guide="profile">${hasProfile ? 'Edit preprocessor →' : '+ Preprocessor'}</button>`;
  guide.innerHTML = `<div class="guide-head"><div><div class="eyebrow">RECIPE BUILDER</div><h2>Continue building</h2><p>Your first dataset, variable and diagnostic are ready. Add processing and any missing inputs, then download the YAML.</p></div><span class="guide-progress">${hasScript ? 'Script added' : 'Preprocessing only'}</span></div><div class="guide-actions"><button type="button" data-guide="dataset">+ Dataset</button>${profileAction}<button type="button" data-guide="variable">+ Variable</button><button type="button" data-guide="diagnostic">+ Diagnostic</button>${hasScript ? '' : '<button type="button" data-guide="script">Add script →</button>'}</div>`;
  guide.querySelectorAll('[data-guide]').forEach((button) => button.addEventListener('click', () => {
    const action = button.dataset.guide;
    if (action === 'profile') { setTab('builder'); if (!hasProfile) $('newProfile').click(); else $('builderProfile').focus(); }
    else if (action === 'assign') { setTab('pipeline'); selectItem('variable', unassigned.id); $('variableProfile')?.focus(); }
    else if (action === 'script') {
      const diagnostic = state.summary.graph.diagnostics[0];
      if (diagnostic) { selectItem('diagnostic', diagnostic.id); $('scriptPath')?.focus(); }
    } else mountCreateForm(action);
  }));
}
function renderRecipeStory() {
  const summary = state.summary;
  const doc = summary.documentation || {};
  const info = state.libraryInfo || {};
  const people = (items) => (items || []).map((person) => person.orcid
    ? `<a href="${escapeHtml(person.orcid)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(person.institute)}">${escapeHtml(person.name)}</a>`
    : `<span title="${escapeHtml(person.institute)}">${escapeHtml(person.name)}</span>`).join(', ');
  const authors = people(doc.authors);
  const maintainers = people(doc.maintainers);
  const references = doc.references || [];
  const refs = references.map((ref) => `<li>${ref.url
    ? `<a href="${escapeHtml(ref.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ref.title)} ↗</a>`
    : escapeHtml(ref.title)}</li>`).join('');
  const projects = (doc.projects || []).map((project) => `<span title="${escapeHtml(project.id)}">${escapeHtml(project.name)}</span>`).join(' · ');
  const meta = [
    info.source_path ? `<div class="story-meta-row"><dt>Source</dt><dd><code>${escapeHtml(info.source_path)}</code>${info.source_url ? ` <a href="${escapeHtml(info.source_url)}" target="_blank" rel="noopener noreferrer">View source ↗</a>` : ''}</dd></div>` : '',
    authors ? `<div class="story-meta-row"><dt>Authors</dt><dd>${authors}</dd></div>` : '',
    maintainers ? `<div class="story-meta-row"><dt>Maintainers</dt><dd>${maintainers}</dd></div>` : '',
    projects ? `<div class="story-meta-row"><dt>Projects</dt><dd>${projects}</dd></div>` : '',
  ].filter(Boolean).join('');
  const links = info.docs_url ? `<a class="story-link" href="${escapeHtml(info.docs_url)}" target="_blank" rel="noopener noreferrer">Recipe documentation ↗</a>` : '';
  const normalize = (text) => String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const overview = normalize(info.overview) && !normalize(summary.description).includes(normalize(info.overview))
    ? `<div class="story-overview"><strong>From the documentation</strong><p>${escapeHtml(info.overview)}</p></div>` : '';
  const figure = info.figure ? `<figure class="story-figure">${info.docs_url ? `<a href="${escapeHtml(info.docs_url)}" target="_blank" rel="noopener noreferrer" title="View figure in documentation">` : ''}<img alt="${escapeHtml(info.figure.caption || 'Example figure from ESMValTool documentation')}" loading="lazy">${info.docs_url ? '</a>' : ''}<figcaption><span class="figure-source">${info.figure.scope === 'documentation' ? 'SHARED DOCUMENTATION EXAMPLE' : 'DOCUMENTED EXAMPLE OUTPUT'}</span>${escapeHtml(info.figure.caption || 'Example figure from ESMValTool documentation')}</figcaption></figure>` : '';
  const story = $('recipeStory');
  story.hidden = false;
  story.innerHTML = `<div class="story-main"><div class="story-eyebrow">ABOUT THIS RECIPE</div><p class="story-description">${escapeHtml(summary.description || 'No description provided in this recipe.')}</p>${overview}${meta ? `<dl class="story-meta">${meta}</dl>` : ''}${refs ? `<details class="story-references" ${references.length <= 3 ? 'open' : ''}><summary>References <span>${references.length}</span></summary><ul>${refs}</ul></details>` : ''}${links ? `<div class="story-links">${links}</div>` : ''}</div>${figure}`;
  const source = info.figure?.url || null;
  if (source !== state.figureSource) {
    state.figureRequest++;
    if (state.figureUrl) URL.revokeObjectURL(state.figureUrl);
    state.figureUrl = null; state.figureSource = source;
  }
  if (source) {
    const image = story.querySelector('img');
    if (state.figureUrl) image.src = state.figureUrl;
    else loadRecipeFigure(source, image, ++state.figureRequest);
  }
}
async function loadRecipeFigure(path, image, request) {
  if (!path.startsWith('/api/docs-figure/')) return;
  try {
    const response = await fetch(path, {headers: {'X-ESMVal-Token': accessToken}});
    if (response.status === 401) { showAuth('The token is no longer valid. Enter the token from the server terminal.'); return; }
    if (!response.ok) throw new Error('Figure unavailable');
    const url = URL.createObjectURL(await response.blob());
    if (request !== state.figureRequest || state.figureSource !== path) { URL.revokeObjectURL(url); return; }
    state.figureUrl = url;
    const current = $('recipeStory').querySelector('img');
    if (current) current.src = url;
  } catch (_) { if (image.isConnected) image.closest('figure')?.remove(); }
}
function pipelineProfiles() {
  const profiles = state.summary?.graph.profiles || [];
  return state.summary?.graph.variables.some((variable) => variable.profile === 'default') && !profiles.some((profile) => profile.id === 'default')
    ? [{id: 'default', label: 'Default', steps: [], custom_order: false, default: true}, ...profiles]
    : profiles;
}
function activeVariables(kind, id) {
  const variables = state.summary?.graph.variables || [];
  if (kind === 'variable') return variables.filter((variable) => variable.id === id);
  if (kind === 'profile') return variables.filter((variable) => variable.profile === id);
  if (kind === 'diagnostic') return variables.filter((variable) => variable.diagnostic === id);
  if (kind === 'dataset') return variables.filter((variable) => variable.dataset_ids?.includes(id));
  return [];
}
function updateConnections() {
  const pipeline = $('pipeline');
  const layer = $('pipelineConnections'); if (!layer) return;
  const selected = state.selected;
  const variables = selected ? activeVariables(selected.kind, selected.id) : [];
  const active = new Set();
  const edges = new Map();
  const key = (kind, id) => `${kind}:${id}`;
  const edge = (fromKind, fromId, toKind, toId) => edges.set(`${key(fromKind, fromId)}|${key(toKind, toId)}`, [key(fromKind, fromId), key(toKind, toId)]);
  for (const variable of variables) {
    active.add(key('variable', variable.id));
    active.add(key('profile', variable.profile));
    active.add(key('diagnostic', variable.diagnostic));
    const datasetIds = selected.kind === 'dataset' ? [selected.id] : (variable.dataset_ids || []);
    for (const datasetId of datasetIds) {
      active.add(key('dataset', datasetId));
      edge('dataset', datasetId, 'profile', variable.profile);
    }
    edge('profile', variable.profile, 'variable', variable.id);
    edge('variable', variable.id, 'diagnostic', variable.diagnostic);
  }
  if (selected) active.add(key(selected.kind, selected.id));
  const items = new Map([...pipeline.querySelectorAll('.pipeline-item[data-kind]')].map((element) => [key(element.dataset.kind, element.dataset.id), element]));
  for (const [itemKey, element] of items) {
    element.classList.toggle('selected', !!selected && itemKey === key(selected.kind, selected.id));
    element.classList.toggle('related', !!selected && active.has(itemKey) && itemKey !== key(selected.kind, selected.id));
    element.classList.toggle('dimmed', !!selected && !active.has(itemKey));
  }
  const width = pipeline.scrollWidth; const height = pipeline.scrollHeight;
  layer.setAttribute('viewBox', `0 0 ${width} ${height}`);
  layer.style.width = `${width}px`; layer.style.height = `${height}px`;
  const root = pipeline.getBoundingClientRect();
  const point = (element, side) => {
    const rect = element.getBoundingClientRect();
    return {x: (side === 'right' ? rect.right : rect.left) - root.left + pipeline.scrollLeft,
            y: rect.top + rect.height / 2 - root.top + pipeline.scrollTop};
  };
  const paths = [];
  for (const [fromKey, toKey] of edges.values()) {
    const from = items.get(fromKey); const to = items.get(toKey);
    if (!from || !to) continue;
    const a = point(from, 'right'); const b = point(to, 'left');
    const bend = Math.max(12, (b.x - a.x) * 0.45);
    paths.push(`<path d="M ${a.x} ${a.y} C ${a.x + bend} ${a.y}, ${b.x - bend} ${b.y}, ${b.x} ${b.y}"/>`);
  }
  layer.innerHTML = paths.join('');
}
function nodePath(kind, item, editor) {
  if (kind === 'dataset') return item.occurrences[Number(editor.querySelector('#nodeOccurrence').value)].path;
  if (kind === 'variable') return ['diagnostics', item.diagnostic, 'variables', item.name];
  if (kind === 'diagnostic') return ['diagnostics', item.id];
  return ['preprocessors', item.id];
}
function nodeSelection(kind, summary, path) {
  if (kind === 'dataset') {
    const item = summary.graph.datasets.find((dataset) => dataset.occurrences.some((occurrence) => JSON.stringify(occurrence.path) === JSON.stringify(path)));
    return item ? {kind, id: item.id} : null;
  }
  return {kind, id: kind === 'variable' ? `${path[1]}/${path[3]}` : path[path.length - 1]};
}
async function loadNodeEditor(kind, item, editor) {
  const path = nodePath(kind, item, editor);
  const textarea = editor.querySelector('#nodeDefinition');
  const saveFields = editor.querySelector('#saveNodeFields');
  const saveYaml = editor.querySelector('#saveNodeYaml');
  const status = editor.querySelector('#nodeEditStatus');
  textarea.value = ''; textarea.disabled = true; saveFields.disabled = true; saveYaml.disabled = true;
  editor.querySelector('#nodeFields').innerHTML = '<p class="hint">Loading settings…</p>';
  status.textContent = 'Loading definition…';
  try {
    const result = await post('/node/read', {yaml: state.yaml, kind, path});
    if (!editor.isConnected || state.selected?.kind !== kind || state.selected?.id !== item.id
        || JSON.stringify(nodePath(kind, item, editor)) !== JSON.stringify(path)) return;
    textarea.value = result.definition; textarea.disabled = false; saveFields.disabled = false; saveYaml.disabled = false;
    if (editor.querySelector('#nodeName')) editor.querySelector('#nodeName').value = result.name;
    const settings = result.settings && typeof result.settings === 'object' && !Array.isArray(result.settings) ? result.settings : {};
    const scalarFields = Object.entries(settings).filter(([key, value]) => key !== 'preprocessor' && (value === null || typeof value !== 'object'));
    const nestedFields = Object.entries(settings).filter(([, value]) => value !== null && typeof value === 'object').map(([key]) => key);
    editor.querySelector('#nodeFields').innerHTML = scalarFields.map(([key, value]) => {
      const raw = value === null ? 'null' : String(value);
      const control = raw.includes('\n') || raw.length > 90
        ? `<textarea data-field="${escapeHtml(key)}" rows="3">${escapeHtml(raw)}</textarea>`
        : `<input data-field="${escapeHtml(key)}" value="${escapeHtml(raw)}">`;
      return `<div class="node-field-row"><label class="field">${escapeHtml(key)}${control}</label><button class="node-remove" data-remove-field="${escapeHtml(key)}" type="button" title="Remove ${escapeHtml(key)}" aria-label="Remove ${escapeHtml(key)}">×</button></div>`;
    }).join('') || '<p class="hint">No simple settings. Use the builder or advanced YAML for nested settings.</p>';
    if (nestedFields.length) editor.querySelector('#nodeFields').insertAdjacentHTML('beforeend', `<p class="hint">${escapeHtml(nestedFields.join(', '))}: edit in Advanced YAML.</p>`);
    editor.querySelectorAll('[data-field]').forEach((input) => { input.dataset.originalValue = input.value; });
    editor.querySelectorAll('[data-remove-field]').forEach((button) => button.addEventListener('click', () => {
      const row = button.closest('.node-field-row');
      row.classList.toggle('removed');
      button.textContent = row.classList.contains('removed') ? '↶' : '×';
      button.setAttribute('aria-label', `${row.classList.contains('removed') ? 'Restore' : 'Remove'} ${button.dataset.removeField}`);
    }));
    status.textContent = '';
  } catch (err) { if (editor.isConnected) status.textContent = err.message; }
}
function mountNodeEditor(kind, item) {
  if (kind === 'profile' && item.default) {
    $('inspectorContent').querySelector('.inspector-title').insertAdjacentHTML('afterend', '<div class="inspector-section"><div class="eyebrow">EDIT PROFILE</div><p class="hint">Default preprocessing is built into ESMValCore. Create a named profile to configure steps.</p><button id="createProfileFromDefault" class="button primary small-button">Open preprocessor builder →</button></div>');
    $('createProfileFromDefault').addEventListener('click', () => setTab('builder'));
    return;
  }
  const label = {dataset: 'Dataset', variable: 'Variable', profile: 'Preprocessor', diagnostic: 'Diagnostic'}[kind];
  const occurrences = kind === 'dataset' ? item.occurrences || [] : [];
  const choices = occurrences.length > 1 ? `<label class="field">Definition<select id="nodeOccurrence">${occurrences.map((occurrence, index) => `<option value="${index}">${escapeHtml(`${index + 1}. ${occurrence.scope}${occurrence.summary ? ` · ${occurrence.summary}` : ''}`)}</option>`).join('')}</select></label>` : (occurrences.length ? `<p class="hint">Defined in ${escapeHtml(occurrences[0].scope)}</p>` : '');
  const name = kind === 'dataset' ? '' : `<label class="field">${kind === 'variable' ? 'Variable group' : kind === 'profile' ? 'Profile name' : 'Diagnostic name'}<input id="nodeName" value="${escapeHtml(item.name || item.id)}"></label>`;
  const profileBuilder = kind === 'profile' ? '<button id="editorBuilder" class="button subtle small-button">Edit steps in builder →</button>' : '';
  const variableHint = kind === 'variable' ? '<p class="hint">If scripts use this group name, update those references after renaming it.</p>' : '';
  $('inspectorContent').querySelector('.inspector-title').insertAdjacentHTML('afterend', `<div id="nodeEditor" class="inspector-section node-editor"><div class="eyebrow">EDIT ${label.toUpperCase()}</div>${profileBuilder}${choices}${name}${variableHint}<div id="nodeFields"></div><details class="node-advanced"><summary>Advanced YAML</summary><p class="hint">Edit nested settings or the complete definition here.</p><label class="field">Definition (YAML)<textarea id="nodeDefinition" rows="10" spellcheck="false" aria-label="${label} YAML definition"></textarea></label><button id="saveNodeYaml" class="button subtle small-button">Save YAML definition</button></details><div class="node-editor-actions"><button id="saveNodeFields" class="button primary small-button">Save ${label.toLowerCase()}</button><button id="resetNode" class="button ghost small-button">Reset</button></div><details class="node-add"><summary>Add setting</summary><label class="field">Name<input id="newNodeField" placeholder="e.g. start_year"></label><label class="field">Value<input id="newNodeValue" placeholder="e.g. 2000"></label></details><p id="nodeEditStatus" class="hint" role="status"></p></div>`);
  const editor = $('nodeEditor');
  editor.querySelector('#editorBuilder')?.addEventListener('click', () => { state.builderProfile = item.id; state.builderBrick = ''; setTab('builder'); });
  editor.querySelector('#nodeOccurrence')?.addEventListener('change', () => loadNodeEditor(kind, item, editor));
  editor.querySelector('#resetNode').addEventListener('click', () => loadNodeEditor(kind, item, editor));
  const save = async (endpoint, payload, button) => {
    button.disabled = true;
    try {
      const result = await post(endpoint, {yaml: state.yaml, kind, path: nodePath(kind, item, editor),
        name: editor.querySelector('#nodeName')?.value, ...payload});
      state.selected = nodeSelection(kind, result.summary, result.path);
      applyEdit(result); toast(`${label} updated in recipe.`);
    } catch (err) { editor.querySelector('#nodeEditStatus').textContent = err.message; toast(err.message, true); }
    finally { if (button.isConnected) button.disabled = false; }
  };
  editor.querySelector('#saveNodeFields').addEventListener('click', () => {
    const removed = [...editor.querySelectorAll('.node-field-row.removed [data-remove-field]')].map((button) => button.dataset.removeField);
    const fields = Object.fromEntries([...editor.querySelectorAll('.node-field-row:not(.removed) [data-field]')]
      .filter((input) => input.value !== input.dataset.originalValue).map((input) => [input.dataset.field, input.value]));
    const newName = editor.querySelector('#newNodeField').value.trim();
    if (newName) fields[newName] = editor.querySelector('#newNodeValue').value;
    save('/node/edit-fields', {fields, removed}, editor.querySelector('#saveNodeFields'));
  });
  editor.querySelector('#saveNodeYaml').addEventListener('click', () => save('/node/edit',
    {definition: editor.querySelector('#nodeDefinition').value}, editor.querySelector('#saveNodeYaml')));
  loadNodeEditor(kind, item, editor);
}
function mountCreateForm(kind) {
  if (!state.summary) return toast('Open a valid recipe first.', true);
  const previous = state.selected;
  const diagnostics = state.summary.graph.diagnostics;
  const variables = state.summary.graph.variables;
  const profiles = state.summary.graph.profiles;
  const selectedVariable = previous?.kind === 'variable' ? variables.find((item) => item.id === previous.id) : null;
  const selectedDiagnostic = selectedVariable?.diagnostic || (previous?.kind === 'diagnostic' ? previous.id : diagnostics[0]?.id || '');
  const diagnosticOptions = diagnostics.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selectedDiagnostic ? 'selected' : ''}>${escapeHtml(item.label)}</option>`).join('');
  const label = {dataset: 'Dataset', profile: 'Preprocessor', variable: 'Variable', diagnostic: 'Diagnostic'}[kind];
  if (!label) return;
  state.selected = null;
  updateConnections();
  const name = `<label class="field">${kind === 'dataset' ? 'Dataset name' : kind === 'variable' ? 'Variable group' : `${label} name`}<input id="createName" required maxlength="120" placeholder="${kind === 'dataset' ? 'e.g. ACCESS-ESM1-5' : kind === 'variable' ? 'e.g. tas' : kind === 'diagnostic' ? 'e.g. temperature_maps' : 'e.g. annual_mean'}"></label>`;
  const details = kind === 'dataset' ? `${esgfSearchMarkup()}<label class="field">Project<input id="createProject" placeholder="e.g. CMIP6"></label><label class="field">Experiment <span class="optional">optional</span><input id="createExp" placeholder="e.g. historical"></label><label class="field">Ensemble <span class="optional">optional</span><input id="createEnsemble" placeholder="e.g. r1i1p1f1"></label><label class="field">Grid <span class="optional">optional</span><input id="createGrid" placeholder="e.g. gn"></label><label class="field">MIP table <span class="optional">optional</span><input id="createMip" placeholder="e.g. Amon"></label><label class="field">Used by<select id="createScope"><option value="recipe" ${previous?.kind !== 'diagnostic' && !selectedVariable ? 'selected' : ''}>Whole recipe</option><option value="diagnostic" ${previous?.kind === 'diagnostic' ? 'selected' : ''}>One diagnostic</option><option value="variable" ${selectedVariable ? 'selected' : ''}>One variable</option></select></label><div id="createTarget"><label class="field" id="createDiagnosticField">Diagnostic<select id="createDiagnostic">${diagnosticOptions}</select></label><label class="field" id="createVariableField">Variable<select id="createVariable"></select></label></div><p class="hint">The scope controls which variables use this dataset.</p>`
    : kind === 'variable' ? `<label class="field">Diagnostic<select id="createDiagnostic">${diagnosticOptions}</select></label><label class="field">Climate variable <span class="optional">short_name</span><input id="createShortName" placeholder="e.g. tas (defaults to group name)"></label><label class="field">Preprocessor<select id="createProfile"><option value="default">Default</option>${profiles.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join('')}</select></label><p class="hint">The group name is the YAML key. Set short_name when the group name differs from the climate variable.</p>${diagnostics.length ? '' : '<button id="createPrerequisite" class="button subtle small-button" type="button">Add a diagnostic first →</button>'}`
    : kind === 'diagnostic' ? `<p class="hint">A diagnostic holds variables and scripts. You can add variables after creating it.</p><label class="field">Script name <span class="optional">optional</span><input id="createScriptName" placeholder="e.g. plot"></label><label class="field">Script path <span class="optional">optional</span><input id="createScriptPath" placeholder="e.g. examples/diagnostic.py"></label>`
    : '<p class="hint">Create a named profile, then add documented ESMValCore bricks in the preprocessor builder.</p>';
  $('inspectorContent').innerHTML = `<div class="inspector-title"><span class="inspector-icon">+</span><h3>Add ${label.toLowerCase()}</h3><p>New recipe component</p></div><form id="createNodeForm" class="node-editor create-node-form"><div class="eyebrow">${label.toUpperCase()}</div>${name}${details}<div class="node-editor-actions"><button id="saveNewNode" class="button primary small-button" type="submit">Add ${label.toLowerCase()}</button><button id="cancelNewNode" class="button ghost small-button" type="button">Cancel</button></div><p id="createStatus" class="hint" role="status"></p></form>`;
  const form = $('createNodeForm');
  if (kind === 'dataset') attachEsgfSearch(form, {dataset: '#createName', project: '#createProject', exp: '#createExp', ensemble: '#createEnsemble', grid: '#createGrid', mip: '#createMip'});
  const value = (id) => form.querySelector('#' + id)?.value.trim() || '';
  const updateTargets = () => {
    if (kind !== 'dataset') return;
    const scope = value('createScope');
    form.querySelector('#createTarget').hidden = scope === 'recipe';
    form.querySelector('#createDiagnosticField').hidden = scope === 'recipe';
    form.querySelector('#createVariableField').hidden = scope !== 'variable';
    const available = variables.filter((item) => item.diagnostic === value('createDiagnostic'));
    const select = form.querySelector('#createVariable');
    const previousValue = select.value || selectedVariable?.name;
    select.innerHTML = available.map((item) => `<option value="${escapeHtml(item.name)}" ${item.name === previousValue ? 'selected' : ''}>${escapeHtml(variableListName(item))}</option>`).join('');
    const blocked = (scope === 'diagnostic' && !diagnostics.length) || (scope === 'variable' && !available.length);
    form.querySelector('#saveNewNode').disabled = blocked;
    form.querySelector('#createStatus').textContent = blocked ? (scope === 'variable' ? 'Add a variable to this diagnostic first.' : 'Add a diagnostic first.') : '';
  };
  form.querySelector('#createScope')?.addEventListener('change', updateTargets);
  form.querySelector('#createDiagnostic')?.addEventListener('change', updateTargets);
  if (kind === 'variable' && !diagnostics.length) {
    form.querySelector('#saveNewNode').disabled = true;
    form.querySelector('#createStatus').textContent = 'Add a diagnostic first.';
    form.querySelector('#createPrerequisite').addEventListener('click', () => mountCreateForm('diagnostic'));
  }
  updateTargets();
  form.querySelector('#cancelNewNode').addEventListener('click', () => { $('inspectorContent').innerHTML = '<div class="empty-state small">Select a component or use + to add one.</div>'; });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('#saveNewNode');
    button.disabled = true;
    const payload = {yaml: state.yaml, kind, name: value('createName'), diagnostic: value('createDiagnostic'),
      variable: value('createVariable'), scope: value('createScope') || 'recipe', project: value('createProject'),
      exp: value('createExp'), ensemble: value('createEnsemble'), grid: value('createGrid'), mip: value('createMip'),
      short_name: value('createShortName'), profile: value('createProfile') || 'default',
      script_name: value('createScriptName'), script_path: value('createScriptPath')};
    try {
      const result = await post('/node/create', payload);
      state.selected = nodeSelection(kind, result.summary, result.path);
      applyEdit(result);
      toast(`${label} added to recipe.`);
    } catch (err) { form.querySelector('#createStatus').textContent = err.message; button.disabled = false; }
  });
  form.querySelector('#createName').focus();
}
function mountScriptCreator(item) {
  const existing = new Set(item.scripts.map((script) => script.name));
  let suggested = 'plot'; let number = 2;
  while (existing.has(suggested)) suggested = `plot_${number++}`;
  $('nodeEditor').insertAdjacentHTML('afterend', `<form id="scriptCreate" class="inspector-section script-create"><div class="eyebrow">ADD SCRIPT</div><label class="field">Name<input id="scriptName" required value="${suggested}" placeholder="e.g. plot"></label><label class="field">Script path<input id="scriptPath" required placeholder="e.g. examples/diagnostic.py"></label><button class="button subtle small-button" type="submit">Add script</button><p id="scriptCreateStatus" class="hint" role="status"></p></form>`);
  attachScriptPicker($('scriptCreate'), $('scriptPath'));
  $('scriptCreate').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try {
      const result = await post('/script/create', {yaml: state.yaml, diagnostic: item.id,
        name: form.querySelector('#scriptName').value.trim(), path: form.querySelector('#scriptPath').value.trim()});
      state.selected = {kind: 'diagnostic', id: item.id};
      applyEdit(result); toast('Script added to diagnostic.');
    } catch (err) { form.querySelector('#scriptCreateStatus').textContent = err.message; button.disabled = false; }
  });
}
let scriptViewerRequest = 0;
let scriptViewerSource = '';
let scriptViewerPreviousFocus = null;
function closeScriptViewer() {
  scriptViewerRequest++;
  $('scriptModal').hidden = true;
  scriptViewerPreviousFocus?.focus();
}
async function openScriptViewer(script) {
  const request = ++scriptViewerRequest;
  scriptViewerPreviousFocus = document.activeElement;
  scriptViewerSource = '';
  $('scriptViewerTitle').textContent = script.name;
  $('scriptViewerPath').textContent = script.path;
  $('scriptViewerStatus').textContent = 'Loading source…';
  $('scriptViewerStatus').hidden = false;
  $('scriptViewerCode').hidden = true;
  $('scriptViewerCode').replaceChildren();
  $('scriptViewerMeta').textContent = '';
  $('copyScriptSource').hidden = true;
  $('scriptModal').hidden = false;
  $('closeScriptViewer').focus();
  try {
    const result = await api('/scripts/source?path=' + encodeURIComponent(script.path));
    if (request !== scriptViewerRequest) return;
    scriptViewerSource = result.source;
    const lines = result.source.endsWith('\n') ? result.source.slice(0, -1).split('\n') : result.source.split('\n');
    $('scriptViewerCode').innerHTML = lines.map((line) => `<span class="script-code-line">${escapeHtml(line) || ' '}</span>`).join('');
    $('scriptViewerCode').hidden = false;
    $('scriptViewerStatus').hidden = true;
    $('scriptViewerMeta').textContent = `${result.language} · ${result.line_count} lines · local ESMValTool source`;
    $('copyScriptSource').hidden = false;
  } catch (error) {
    if (request !== scriptViewerRequest) return;
    $('scriptViewerStatus').textContent = `${error.message} Check that the matching ESMValTool scripts are installed locally.`;
  }
}
function selectItem(kind, id) {
  state.selected = { kind, id };
  updateConnections();
  const items = kind === 'profile' ? pipelineProfiles() : state.summary.graph[kind === 'dataset' ? 'datasets' : kind === 'variable' ? 'variables' : 'diagnostics'];
  const item = items.find((x) => x.id === id);
  if (!item) return;
  if (kind === 'dataset') {
    const linked = activeVariables(kind, id);
    $('inspectorContent').innerHTML = `<div class="inspector-title"><span class="inspector-icon">◫</span><h3>${escapeHtml(item.label)}</h3><p>Dataset group · ${item.definition_count} input definition${item.definition_count === 1 ? '' : 's'}</p></div><div class="inspector-section"><div class="eyebrow">USED BY</div>${linked.map((variable) => `<div class="detail-row"><strong>${escapeHtml(variableListName(variable))}</strong><span>${escapeHtml(variable.diagnostic)}</span></div>`).join('') || '<p class="empty">No direct variable uses this dataset.</p>'}</div><div class="inspector-section"><div class="eyebrow">DEFINED IN</div><p class="hint">${escapeHtml(item.scopes.join(', '))}${item.definition_count > item.scopes.length ? ', and other variable scopes' : ''}</p></div><div class="inspector-section"><div class="eyebrow">EXAMPLE INPUT SETTINGS</div>${Object.entries(item.settings).map(([name, value]) => `<div class="detail-row"><strong>${escapeHtml(name)}</strong><span>${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : value)}</span></div>`).join('')}</div>`;
    $('inspectorContent').querySelector('.inspector-section:last-child').remove();
    mountNodeEditor(kind, item);
    return;
  }
  if (kind === 'variable') {
    const options = [...new Set(['default', ...state.summary.graph.profiles.map((profile) => profile.id)])];
    $('inspectorContent').innerHTML = `<div class="inspector-title"><span class="inspector-icon">◌</span><h3>${escapeHtml(variableName(item))}</h3><p>${escapeHtml(variableGroup(item) ? `${variableGroup(item)} · Diagnostic ${item.diagnostic}` : `Variable · Diagnostic ${item.diagnostic}`)}</p></div><div class="inspector-section"><div class="eyebrow">PREPROCESSOR</div><label class="field">Profile<select id="variableProfile">${options.map((name) => `<option value="${escapeHtml(name)}" ${name === item.profile ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('')}</select></label><p class="hint">This variable uses the selected profile before its diagnostic scripts run.</p><button id="saveVariableProfile" class="button primary small-button">Apply profile</button></div>`;
    $('saveVariableProfile').addEventListener('click', async () => {
      try { const result = await post('/variable/preprocessor', {yaml: state.yaml, diagnostic: item.diagnostic, variable: item.name, profile: $('variableProfile').value}); applyEdit(result); toast('Variable preprocessor updated.'); }
      catch (err) { toast(err.message, true); }
    });
    mountNodeEditor(kind, item);
    return;
  }
  if (kind === 'diagnostic') {
    const scriptRows = item.scripts.map((script, index) => `<div class="detail-stack diagnostic-script"><strong>${escapeHtml(script.name)}</strong><code>${escapeHtml(script.path || 'No script path')}</code>${script.path ? `<button class="button subtle small-button view-script" type="button" data-script-index="${index}">View source ↗</button>` : ''}</div>`).join('') || '<p class="empty">No scripts</p>';
    const variableRows = item.variables.map((variable) => `<div class="detail-row"><strong>${escapeHtml(variableListName(variable))}</strong><span>${escapeHtml(variable.profile)}</span></div>`).join('') || '<p class="empty">No variables</p>';
    $('inspectorContent').innerHTML = `<div class="inspector-title"><span class="inspector-icon">◇</span><h3>${escapeHtml(item.label)}</h3><p>Diagnostic · ${item.variables.length} variables</p></div>
      <div class="inspector-section"><div class="eyebrow">SCRIPTS</div>${scriptRows}</div>
      <div class="inspector-section"><div class="eyebrow">VARIABLES</div>${variableRows}</div>
      ${item.ancestors.length ? `<div class="inspector-section"><div class="eyebrow">ANCESTORS</div><p>${escapeHtml(item.ancestors.join(', '))}</p></div>` : ''}
      <p class="hint">Select a variable in the pipeline to assign its preprocessor.</p>`;
    $('inspectorContent').querySelectorAll('.view-script').forEach((button) => {
      button.addEventListener('click', () => openScriptViewer(item.scripts[Number(button.dataset.scriptIndex)]));
    });
    mountNodeEditor(kind, item);
    mountScriptCreator(item);
    return;
  }
  $('inspectorContent').innerHTML = `<div class="inspector-title"><span class="inspector-icon">▤</span><h3>${escapeHtml(item.label)}</h3><p>${item.default ? 'Default preprocessing' : `Preprocessor profile · ${item.custom_order ? 'custom' : 'ESMValCore'} order`}</p></div><div class="inspector-section"><div class="eyebrow">VARIABLES USING THIS PROFILE</div>${activeVariables('profile', item.id).map((variable) => `<div class="detail-row"><strong>${escapeHtml(variableListName(variable))}</strong><span>${escapeHtml(variable.diagnostic)}</span></div>`).join('') || '<p class="empty">No variables</p>'}</div>${item.default ? '<p class="hint">Variables without a named profile use ESMValCore defaults.</p>' : `<div class="inspector-section"><div class="eyebrow">STEPS</div><div class="step-list">${item.steps.map((step, index) => `<div class="step"><span class="step-index">${String(index + 1).padStart(2, '0')}</span><span>${escapeHtml(step.name)}</span></div>`).join('') || '<p class="empty">No steps</p>'}</div></div><div class="inspector-section"><button id="openBuilder" class="button primary">Open builder →</button><p class="hint">Search documented ESMValCore bricks and configure their parameters.</p></div>`}`;
  $('openBuilder')?.addEventListener('click', () => { state.builderProfile = item.id; state.builderBrick = ''; setTab('builder'); });
  mountNodeEditor(kind, item);
}
function applyEdit(result) {
  state.yaml = result.yaml; state.summary = result.summary; state.dirty = true;
  persistDraft();
  $('yamlEditor').value = result.yaml; $('dirtyMark').hidden = false; renderSummary(); updateYamlFeedback(result.summary);
  $('validationStatus').textContent = result.summary.messages.length ? `${result.summary.messages.length} structure issue${result.summary.messages.length === 1 ? '' : 's'}` : 'YAML structure checked';
  $('validationStatus').classList.toggle('invalid', !!result.summary.messages.length);
}
function renderBuilder() {
  const profiles = state.summary?.graph.profiles || [];
  if (!profiles.some((item) => item.id === state.builderProfile)) state.builderProfile = profiles[0]?.id || '';
  $('builderProfile').innerHTML = profiles.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === state.builderProfile ? 'selected' : ''}>${escapeHtml(item.label)}</option>`).join('') || '<option value="">No profiles yet</option>';
  const profile = profiles.find((item) => item.id === state.builderProfile);
  $('customOrder').checked = !!profile?.custom_order;
  $('customOrder').disabled = !profile;
  $('builderOrderNote').textContent = profile?.custom_order ? 'Steps run in the order shown in YAML.' : 'ESMValCore applies its standard step order.';
  const order = state.catalogue?.order || [];
  const steps = profile ? [...profile.steps] : [];
  if (profile && !profile.custom_order) steps.sort((a, b) => (order.indexOf(a.name) < 0 ? 999 : order.indexOf(a.name)) - (order.indexOf(b.name) < 0 ? 999 : order.indexOf(b.name)));
  $('builderStepCount').textContent = profile ? `${steps.length} configured` : '';
  $('builderSteps').innerHTML = profile ? (steps.map((step, index) => `<div class="builder-step-row"><button class="builder-step ${state.builderBrick === step.name && state.builderEditing ? 'active' : ''} ${step.parameters === false ? 'disabled-step' : ''}" data-step="${escapeHtml(step.name)}"><span class="step-index">${String(index + 1).padStart(2, '0')}</span><strong>${escapeHtml(step.name)}</strong>${step.parameters === false ? '<em>Off</em>' : ''}<span>›</span></button>${profile.custom_order ? `<div class="move-controls"><button data-move="-1" data-step="${escapeHtml(step.name)}" aria-label="Move ${escapeHtml(step.name)} up" ${index === 0 ? 'disabled' : ''}>↑</button><button data-move="1" data-step="${escapeHtml(step.name)}" aria-label="Move ${escapeHtml(step.name)} down" ${index === steps.length - 1 ? 'disabled' : ''}>↓</button></div>` : ''}</div>`).join('') || '<p class="empty">No steps yet. Add a brick below.</p>') : '<p class="empty">Create a profile to begin building.</p>';
  $('builderSteps').querySelectorAll('.builder-step').forEach((button) => button.addEventListener('click', () => { state.builderBrick = button.dataset.step; state.builderEditing = true; renderBuilder(); }));
  $('builderSteps').querySelectorAll('[data-move]').forEach((button) => button.addEventListener('click', async () => {
    try { const result = await post('/profile/move', { yaml: state.yaml, profile: state.builderProfile, step: button.dataset.step, direction: Number(button.dataset.move) }); applyEdit(result); }
    catch (err) { toast(err.message, true); }
  }));
  renderBrickList(); renderBrickDetails();
}
function renderBrickList() {
  const query = $('brickSearch').value.toLowerCase().trim();
  const bricks = state.catalogue?.bricks || [];
  const filtered = bricks.filter((brick) => `${brick.name} ${brick.category} ${brick.summary}`.toLowerCase().includes(query));
  $('brickList').innerHTML = filtered.map((brick) => `<button class="brick-entry ${state.builderBrick === brick.name && !state.builderEditing ? 'active' : ''}" data-brick="${escapeHtml(brick.name)}"><span><strong>${escapeHtml(brick.name)}</strong><small>${escapeHtml(brick.category)}</small></span><span>+</span></button>`).join('') || '<p class="empty">No matching bricks.</p>';
  $('brickList').querySelectorAll('[data-brick]').forEach((button) => button.addEventListener('click', () => {
    state.builderBrick = button.dataset.brick;
    state.builderEditing = !!state.summary?.graph.profiles.find((profile) => profile.id === state.builderProfile)?.steps.find((step) => step.name === state.builderBrick);
    renderBuilder();
  }));
}
function inputValue(value) { return value === undefined || value === null ? '' : typeof value === 'string' ? value : JSON.stringify(value); }
function renderBrickDetails() {
  const brick = state.catalogue?.bricks.find((item) => item.name === state.builderBrick);
  const profile = state.summary?.graph.profiles.find((item) => item.id === state.builderProfile);
  const existing = profile?.steps.find((step) => step.name === state.builderBrick);
  if (!brick) {
    $('brickDetails').innerHTML = state.catalogue ? '<div class="empty-state">Select a brick to see its documentation and parameters.</div>' : '<div class="empty-state">Loading ESMValCore bricks…</div>';
    return;
  }
  const values = state.builderEditing && existing && typeof existing.parameters === 'object' && existing.parameters ? existing.parameters : {};
  const known = new Set(brick.parameters.map((parameter) => parameter.name));
  const extras = Object.fromEntries(Object.entries(values).filter(([name]) => !known.has(name)));
  $('brickDetails').innerHTML = `<div class="brick-detail-head"><span class="brick-category">${escapeHtml(brick.category)}</span><h3>${escapeHtml(brick.name)}</h3><p>${escapeHtml(brick.summary)}</p>${existing?.parameters === false ? '<p class="brick-disabled-note">This step is disabled in the recipe. Add its required parameters to enable it.</p>' : ''}</div><div class="brick-param-list"><div class="builder-subhead">PARAMETERS</div>${brick.parameters.map((parameter) => `<label class="brick-param"><span><strong>${escapeHtml(parameter.name)}</strong>${parameter.required ? '<em>required</em>' : `<em>default ${escapeHtml(inputValue(parameter.default))}</em>`}</span><small>${escapeHtml(parameter.description || parameter.type || 'Optional processing setting.')}</small><textarea data-param="${escapeHtml(parameter.name)}" rows="1" placeholder="${escapeHtml(parameter.required ? 'Enter a value' : inputValue(parameter.default))}">${escapeHtml(inputValue(values[parameter.name]))}</textarea></label>`).join('') || '<p class="empty">This brick has no recipe parameters.</p>'}<label class="brick-param"><span><strong>Additional parameters</strong><em>optional YAML</em></span><small>For advanced options accepted by this brick.</small><textarea id="brickExtras" rows="3" placeholder="key: value">${escapeHtml(Object.keys(extras).length ? JSON.stringify(extras, null, 2) : '')}</textarea></label></div><details class="brick-doc"><summary>Full ESMValCore documentation</summary><pre>${escapeHtml(brick.documentation)}</pre></details><div class="brick-actions"><button id="saveBrick" class="button primary" ${profile ? '' : 'disabled'}>${existing?.parameters === false ? 'Enable step' : existing && state.builderEditing ? 'Update step' : 'Add step'}</button>${existing && state.builderEditing ? `<button id="${existing.parameters === false ? 'removeBrick' : 'disableBrick'}" class="button ghost">${existing.parameters === false ? 'Remove' : 'Disable'}</button>${existing.parameters === false ? '' : '<button id="removeBrick" class="button ghost">Remove</button>'}` : ''}</div>`;
  $('saveBrick').addEventListener('click', saveBrick);
  $('removeBrick')?.addEventListener('click', removeBrick);
  $('disableBrick')?.addEventListener('click', disableBrick);
}
async function saveBrick() {
  if (!state.builderProfile || !state.builderBrick) return toast('Create a profile first.', true);
  const fields = Object.fromEntries([...$('brickDetails').querySelectorAll('[data-param]')].map((element) => [element.dataset.param, element.value]));
  try {
    const result = await post('/profile/edit-fields', { yaml: state.yaml, profile: state.builderProfile, step: state.builderBrick, fields, extra: $('brickExtras').value });
    state.builderEditing = true; applyEdit(result); toast('Preprocessor step saved.');
  } catch (err) { toast(err.message, true); }
}
async function removeBrick() {
  try { const result = await post('/profile/edit', { yaml: state.yaml, profile: state.builderProfile, step: state.builderBrick, remove: true }); state.builderEditing = false; applyEdit(result); toast('Step removed.'); }
  catch (err) { toast(err.message, true); }
}
async function disableBrick() {
  try { const result = await post('/profile/edit', { yaml: state.yaml, profile: state.builderProfile, step: state.builderBrick, parameters: 'false' }); applyEdit(result); toast('Step disabled.'); }
  catch (err) { toast(err.message, true); }
}
async function loadCatalogue() {
  try { state.catalogue = await api('/preprocessors'); renderBuilder(); }
  catch (err) { $('brickDetails').innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`; }
}
async function loadLibrary() {
  try {
    const result = await api('/recipes'); state.library = result.recipes;
    $('libraryStatus').textContent = result.root ? 'Local ESMValTool collection' : 'Set ESMVAL_GUI_RECIPE_ROOT to show recipes';
    renderRealmFilter();
    renderLibrary();
    const initial = result.recipes.find((x) => x.path === 'examples/recipe_python.yml') || result.recipes[0];
    if (initial && !state.yaml) await openLibraryRecipe(initial.path);
  } catch (err) { $('recipeList').innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`; }
}
function renderRealmFilter() {
  const counts = new Map(); let unspecified = 0;
  for (const item of state.library || []) {
    if (!item.realms?.length) unspecified++;
    for (const realm of item.realms || []) counts.set(realm, (counts.get(realm) || 0) + 1);
  }
  const options = [...counts].sort(([a], [b]) => realmLabel(a).localeCompare(realmLabel(b)));
  $('realmFilter').innerHTML = `<option value="">All realms (${state.library.length})</option>${options.map(([realm, count]) => `<option value="${escapeHtml(realm)}">${escapeHtml(realmLabel(realm))} (${count})</option>`).join('')}<option value="__unspecified__">Realm unspecified (${unspecified})</option>`;
}
function renderLibrary() {
  const query = $('recipeSearch').value.toLowerCase().trim();
  const realm = $('realmFilter').value;
  const matching = (state.library || []).filter((item) => item.path.toLowerCase().includes(query) && (!realm || (realm === '__unspecified__' ? !item.realms?.length : item.realms?.includes(realm))));
  $('recipeCount').textContent = matching.length;
  $('recipeList').innerHTML = matching.map((item) => `<button class="recipe-entry ${state.name === item.path ? 'active' : ''}" data-path="${escapeHtml(item.path)}"><span class="recipe-entry-name">${escapeHtml(item.name.replace(/^recipe_/, '').replace(/\.yml$/, '').replaceAll('_', ' '))}</span><span class="recipe-entry-group" title="${escapeHtml(item.group)}">${escapeHtml(item.group === '.' ? 'Root' : item.group)}</span><span class="recipe-entry-realm">${escapeHtml(item.realms?.length ? item.realms.map(realmLabel).join(' · ') : 'Realm unspecified')}</span></button>`).join('') || '<p class="empty">No recipes found in this realm.</p>';
  $('recipeList').querySelectorAll('.recipe-entry').forEach((button) => button.addEventListener('click', () => openLibraryRecipe(button.dataset.path)));
}
async function openLibraryRecipe(path) {
  if (state.name === path && state.yaml) return;
  if (!canReplaceRecipe()) return;
  const current = state.yaml;
  const request = (state.libraryRequest = (state.libraryRequest || 0) + 1);
  try {
    const result = await api('/recipes/' + path.split('/').map(encodeURIComponent).join('/'));
    if (request !== state.libraryRequest || (state.yaml !== current && !canReplaceRecipe())) return;
    setRecipe(result.yaml, path, false, result.library_info); renderLibrary();
  }
  catch (err) { toast(err.message, true); }
}
function settings() {
  const ids = ['host','project','queue','walltime','ncpus','memory_gb','jobfs_gb','storage','work_dir','esmvaltool_command','setup_command','config_dir','config_file'];
  const value = Object.fromEntries(ids.map((id) => [id, $(id).value.trim()]));
  ['ncpus','memory_gb','jobfs_gb'].forEach((id) => value[id] = Number(value[id]));
  localStorage.setItem('esmval-gui-settings', JSON.stringify(value));
  return value;
}
function restoreSettings() {
  try { const saved = JSON.parse(localStorage.getItem('esmval-gui-settings') || '{}'); Object.entries(saved).forEach(([id, value]) => { if ($(id)) $(id).value = value; }); } catch (_) {}
}
async function probeRemote() {
  $('probeResult').textContent = 'Connecting…';
  try {
    const s = settings(); const result = await post('/remote/probe', { host: s.host, esmvaltool_command: s.esmvaltool_command, setup_command: s.setup_command });
    if (s.esmvaltool_command === 'esmvaltool' && result.esmvaltool_path) $('esmvaltool_command').value = result.esmvaltool_path;
    if (!s.project && result.project) $('project').value = result.project;
    if (!s.config_dir && !s.config_file && result.config_file) $('config_file').value = result.config_file;
    if (!s.work_dir && result.project && result.user) $('work_dir').value = `/scratch/${result.project}/${result.user}/esmval-gui-jobs`;
    if (!s.storage && result.project) {
      const executableProject = (result.esmvaltool_path || s.esmvaltool_command).match(/^\/g\/data\/([A-Za-z0-9_-]+)\//)?.[1];
      const paths = [`scratch/${result.project}`, ...(executableProject ? [`gdata/${executableProject}`] : []), ...(result.storage_paths || [])];
      $('storage').value = [...new Set(paths)].join('+');
    }
    $('probeResult').textContent = result.pbs === 'yes' ? (result.esmvaltool_path ? 'PBS and ESMValTool ready' : 'PBS ready · ESMValTool executable not found') : 'PBS unavailable';
    $('connectionBadge').textContent = result.pbs === 'yes' ? 'Gadi connected' : 'SSH connected';
    $('connectionBadge').classList.add('connected'); settings();
  } catch (err) { $('probeResult').textContent = 'Connection failed'; toast(err.message, true); }
}
async function previewScript() {
  try { const result = await post('/remote/preview', { yaml: state.yaml, settings: settings() }); $('pbsScript').textContent = result.script; document.querySelector('.script-preview').open = true; return true; }
  catch (err) { toast(err.message, true); return false; }
}
function reviewSignature() { return JSON.stringify({yaml: state.yaml, settings: settings()}); }
function clearRunReview() {
  state.reviewSignature = null;
  $('runReview').hidden = true;
  $('runReview').replaceChildren();
  $('submitButton').textContent = 'Review & check ↗';
}
async function submitJob() {
  const signature = reviewSignature();
  const button = $('submitButton');
  if (state.reviewSignature !== signature) {
    clearRunReview(); button.disabled = true; button.textContent = 'Checking Gadi…';
    try {
      if (!await previewScript()) return;
      const result = await post('/remote/preflight', {yaml: state.yaml, settings: settings()});
      if (reviewSignature() !== signature) return;
      $('runReview').hidden = false;
      $('runReview').innerHTML = `<strong>${escapeHtml(result.summary.title)} · ${result.ready ? 'Ready for your review' : 'Fix blocking checks'}</strong><p>${escapeHtml(`${$('project').value} · ${$('queue').value} · ${$('walltime').value} · ${$('ncpus').value} CPU · ${$('memory_gb').value} GB`)}</p>${result.checks.map((check) => `<div class="review-check ${escapeHtml(check.level)}"><span>${check.level === 'ok' ? '✓' : check.level === 'error' ? '×' : '!'}</span><b>${escapeHtml(check.label)}</b><span>${escapeHtml(check.detail)}</span></div>`).join('')}`;
      if (result.ready) state.reviewSignature = signature;
    } catch (err) { toast(err.message, true); }
    finally { button.disabled = false; button.textContent = state.reviewSignature === signature ? 'Queue job ↗' : 'Review & check ↗'; }
    return;
  }
  button.disabled = true; button.textContent = 'Submitting…';
  try {
    const result = await post('/remote/submit', {yaml: state.yaml, settings: settings()});
    $('runModal').hidden = true; clearRunReview(); toast('Submitted PBS job ' + result.job_id); setTab('runs');
  } catch (err) { toast(err.message, true); }
  finally { button.disabled = false; if (state.reviewSignature === signature) button.textContent = 'Queue job ↗'; }
}
async function refreshJobs() {
  try {
    const result = await api('/jobs'); state.jobs = result.jobs;
    $('jobsList').innerHTML = result.jobs.map((job) => `<button class="job-entry" data-id="${escapeHtml(job.id)}"><span class="job-icon">↗</span><span><strong>${escapeHtml(job.title || job.job_id)}</strong><small>${escapeHtml(job.job_id)} · ${new Date(job.submitted_at).toLocaleString()}${job.cancelled ? ' · Cancelled' : ''}</small></span><span class="job-queue">${escapeHtml(job.queue)}</span></button>`).join('') || '<p class="empty">No jobs submitted yet.</p>';
    $('jobsList').querySelectorAll('.job-entry').forEach((button) => button.addEventListener('click', () => selectJob(button.dataset.id)));
    if (state.selectedJob) selectJob(state.selectedJob);
  } catch (err) { toast(err.message, true); }
}
async function selectJob(id) {
  state.selectedJob = id; $('jobDetails').hidden = false;
  const record = state.jobs.find((job) => job.id === id); $('selectedJobTitle').textContent = 'Job ' + (record?.job_id || id);
  try {
    const [status, logs] = await Promise.all([api('/jobs/' + id), api('/jobs/' + id + '/logs')]);
    $('selectedJobState').textContent = `${status.state}${status.exit_status !== null && status.exit_status !== undefined ? ' · exit ' + status.exit_status : ''} · ${status.remote_dir}`;
    $('cancelJob').hidden = !['queued', 'running', 'held'].includes(status.state);
    $('stdoutLog').textContent = logs.stdout || 'No output yet.'; $('stderrLog').textContent = logs.stderr || 'No errors.';
  } catch (err) { $('selectedJobState').textContent = err.message; }
}
document.querySelectorAll('.tab').forEach((button) => button.addEventListener('click', () => setTab(button.dataset.tab)));
const newRecipeForm = $('newRecipeForm');
attachScriptPicker(newRecipeForm.querySelector('.recipe-form-section:last-of-type'), newRecipeForm.elements.script_path);
newRecipeForm.querySelector('.esgf-wizard-search').innerHTML = esgfSearchMarkup();
attachEsgfSearch(newRecipeForm, {dataset: '[name="dataset"]', project: '[name="project"]', exp: '[name="exp"]', ensemble: '[name="ensemble"]', grid: '[name="grid"]'}, (item) => {
  if (item.mip && item.project !== 'CMIP7') newRecipeForm.elements.mip.value = item.mip;
  if (item.variable) newRecipeForm.elements.variable.value = item.variable;
  scheduleNewRecipePreview();
});
const newRecipeValues = () => Object.fromEntries(new FormData(newRecipeForm).entries());
function closeNewRecipeBuilder() { $('newRecipeModal').hidden = true; clearTimeout(state.recipePreviewTimer); state.recipePreviewVersion++; }
function scheduleNewRecipePreview() {
  clearTimeout(state.recipePreviewTimer);
  const version = ++state.recipePreviewVersion;
  if (!newRecipeForm.checkValidity()) {
    $('newRecipePreview').textContent = 'Fill the required fields to preview the recipe.';
    $('newRecipeStatus').textContent = '';
    return;
  }
  state.recipePreviewTimer = setTimeout(async () => {
    try {
      const result = await post('/recipe/new', newRecipeValues());
      if (version !== state.recipePreviewVersion || $('newRecipeModal').hidden) return;
      $('newRecipePreview').textContent = result.yaml;
      $('newRecipeStatus').textContent = '';
    } catch (err) {
      if (version !== state.recipePreviewVersion || $('newRecipeModal').hidden) return;
      $('newRecipePreview').textContent = 'Preview unavailable until the fields are valid.';
      $('newRecipeStatus').textContent = err.message;
    }
  }, 300);
}
$('newRecipeButton').addEventListener('click', () => { $('newRecipeModal').hidden = false; newRecipeForm.elements.title.focus(); scheduleNewRecipePreview(); });
$('closeNewRecipe').addEventListener('click', closeNewRecipeBuilder);
$('cancelNewRecipe').addEventListener('click', closeNewRecipeBuilder);
$('newRecipeModal').addEventListener('click', (event) => { if (event.target === $('newRecipeModal')) closeNewRecipeBuilder(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('newRecipeModal').hidden) closeNewRecipeBuilder(); });
newRecipeForm.addEventListener('input', (event) => {
  if (event.target.name === 'filename') newRecipeForm.elements.filename.dataset.manual = 'true';
  if (event.target.name === 'title' && newRecipeForm.elements.filename.dataset.manual !== 'true') {
    const slug = event.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    newRecipeForm.elements.filename.value = slug ? `recipe_${slug}.yml` : '';
  }
  scheduleNewRecipePreview();
});
newRecipeForm.addEventListener('change', scheduleNewRecipePreview);
newRecipeForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('createRecipe'); button.disabled = true;
  try {
    const result = await post('/recipe/new', newRecipeValues());
    if (!canReplaceRecipe()) return;
    closeNewRecipeBuilder();
    setRecipe(result.yaml, result.name, true, null, true);
    newRecipeForm.reset(); delete newRecipeForm.elements.filename.dataset.manual;
    $('newRecipePreview').textContent = 'Fill the required fields to preview the recipe.';
    toast('Recipe created. Continue in the pipeline.');
  } catch (err) { $('newRecipeStatus').textContent = err.message; }
  finally { button.disabled = false; }
});
$('editYamlButton').addEventListener('click', () => { setTab('yaml'); $('yamlEditor').focus({ preventScroll: true }); });
$('viewPipeline').addEventListener('click', () => setTab('pipeline'));
$('builderProfile').addEventListener('change', (event) => { state.builderProfile = event.target.value; state.builderBrick = ''; renderBuilder(); });
$('brickSearch').addEventListener('input', renderBrickList);
$('newProfile').addEventListener('click', () => {
  if (!state.yaml) return toast('Open or create a recipe first.', true);
  $('profileCreateForm').hidden = false; $('profileCreateName').focus();
});
$('cancelProfileCreate').addEventListener('click', () => { $('profileCreateForm').hidden = true; $('profileCreateStatus').textContent = ''; });
$('profileCreateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const profile = $('profileCreateName').value.trim();
  const button = $('saveProfileCreate'); button.disabled = true;
  try {
    const result = await post('/profile/create', {yaml: state.yaml, profile});
    state.builderProfile = profile; state.builderBrick = '';
    applyEdit(result);
    $('profileCreateForm').hidden = true; $('profileCreateForm').reset(); $('profileCreateStatus').textContent = '';
    toast('Profile created. Add a preprocessor brick, then assign the profile to a variable.');
  } catch (err) { $('profileCreateStatus').textContent = err.message; }
  finally { button.disabled = false; }
});
$('customOrder').addEventListener('change', async (event) => {
  try { const result = await post('/profile/order', { yaml: state.yaml, profile: state.builderProfile, custom_order: event.target.checked }); applyEdit(result); }
  catch (err) { event.target.checked = !event.target.checked; toast(err.message, true); }
});
$('recipeSearch').addEventListener('input', renderLibrary);
$('realmFilter').addEventListener('change', renderLibrary);
$('yamlEditor').addEventListener('input', () => { state.yaml = $('yamlEditor').value; state.dirty = true; $('dirtyMark').hidden = false; persistDraft(); clearTimeout(state.parseTimer); state.parseTimer = setTimeout(parseCurrent, 400); });
$('yamlEditor').addEventListener('keydown', (event) => {
  if (event.key === 'Tab' && !event.shiftKey) {
    event.preventDefault();
    event.target.setRangeText('  ', event.target.selectionStart, event.target.selectionEnd, 'end');
    event.target.dispatchEvent(new Event('input', { bubbles: true }));
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault(); $('saveFile').click();
  }
});
$('validateButton').addEventListener('click', async () => { await parseCurrent(); toast(!state.summary ? 'Fix the YAML before checking structure.' : state.summary.messages.length ? 'Recipe structure has issues.' : 'YAML structure checked. Gadi data and runtime compatibility still need review.', !state.summary || !!state.summary.messages.length); });
$('openFile').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', async () => { const file = $('fileInput').files[0]; $('fileInput').value = ''; if (file && canReplaceRecipe()) setRecipe(await file.text(), file.name); });
$('configOpenFiles').addEventListener('click', () => $('configFileInput').click());
$('configOpenFolder').addEventListener('click', () => $('configFolderInput').click());
for (const id of ['configFileInput', 'configFolderInput']) $(id).addEventListener('change', async (event) => { await addConfigFiles(event.target.files); event.target.value = ''; });
$('configClear').addEventListener('click', () => { state.configFiles = []; inspectConfig(); });
$('configFileList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-config-action]');
  if (!button) return;
  const index = Number(button.dataset.index);
  if (button.dataset.configAction === 'remove') state.configFiles.splice(index, 1);
  else {
    const other = index + (button.dataset.configAction === 'up' ? -1 : 1);
    if (other < 0 || other >= state.configFiles.length) return;
    [state.configFiles[index], state.configFiles[other]] = [state.configFiles[other], state.configFiles[index]];
  }
  inspectConfig();
});
$('configRemoteForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('configLoadRemote'); button.disabled = true;
  $('configStatus').textContent = 'Reading YAML files from Gadi…';
  try {
    const result = await post('/config/remote', {host: $('configHost').value.trim(), path: $('configPath').value.trim()});
    if (state.configFiles.length + result.files.length > 50) throw new Error('Choose at most 50 configuration files.');
    const names = new Set(state.configFiles.map((file) => file.name));
    for (const file of result.files) {
      let name = file.name;
      if (names.has(name)) { let number = 2; while (names.has(`${name} (${number})`)) number++; name = `${name} (${number})`; }
      names.add(name); state.configFiles.push({name, content: file.content});
    }
    await inspectConfig();
  } catch (error) { $('configStatus').textContent = error.message; }
  finally { button.disabled = false; }
});
$('saveFile').addEventListener('click', () => { if (!state.yaml) return toast('Open a recipe first.', true); const blob = new Blob([state.yaml], { type: 'text/yaml' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = state.name.split('/').pop() || 'recipe.yml'; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); state.dirty = false; $('dirtyMark').hidden = true; persistDraft(); });
$('runButton').addEventListener('click', () => { if (!state.yaml) return toast('Open a recipe first.', true); clearRunReview(); $('runModal').hidden = false; });
$('closeScriptViewer').addEventListener('click', closeScriptViewer);
$('scriptModal').addEventListener('click', (event) => { if (event.target === $('scriptModal')) closeScriptViewer(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('scriptModal').hidden) closeScriptViewer(); });
$('copyScriptSource').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(scriptViewerSource); toast('Script source copied.'); }
  catch (_) { toast('Could not copy script source.', true); }
});
$('closeModal').addEventListener('click', () => $('runModal').hidden = true);
$('runModal').addEventListener('click', (event) => { if (event.target === $('runModal')) $('runModal').hidden = true; });
$('runModal').addEventListener('input', (event) => { if (event.target.matches('input,select,textarea')) clearRunReview(); });
$('runModal').addEventListener('change', (event) => { if (event.target.matches('input,select,textarea')) clearRunReview(); });
$('probeButton').addEventListener('click', probeRemote); $('previewButton').addEventListener('click', previewScript); $('submitButton').addEventListener('click', submitJob);
$('refreshJobs').addEventListener('click', refreshJobs); $('refreshLog').addEventListener('click', () => { if (state.selectedJob) selectJob(state.selectedJob); });
$('cancelJob').addEventListener('click', async () => {
  if (!state.selectedJob || !window.confirm('Cancel this PBS job on Gadi?')) return;
  try { await post('/jobs/' + state.selectedJob + '/cancel', {}); toast('Job cancelled.'); await refreshJobs(); }
  catch (err) { toast(err.message, true); }
});
let connectionFrame = 0;
function scheduleConnections() {
  if (connectionFrame) return;
  connectionFrame = requestAnimationFrame(() => { connectionFrame = 0; updateConnections(); });
}
$('pipeline').addEventListener('scroll', scheduleConnections);
window.addEventListener('resize', scheduleConnections);
new ResizeObserver(scheduleConnections).observe($('pipeline'));
function startStudio() {
  $('authGate').hidden = true;
  document.querySelector('.shell').inert = false;
  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
    if (draft?.yaml && window.confirm(`Recover the edited draft of ${draft.name || 'recipe.yml'} from this tab?`)) setRecipe(draft.yaml, draft.name || 'recipe.yml', true, null, true);
    else sessionStorage.removeItem(draftKey);
  } catch (_) { sessionStorage.removeItem(draftKey); }
  loadCatalogue(); loadLibrary();
}
$('authForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  accessToken = $('authTokenInput').value.trim();
  $('authSubmit').disabled = true;
  $('authError').textContent = '';
  try {
    await api('/health');
    sessionStorage.setItem('esmval-gui-access-token', accessToken);
    $('authTokenInput').value = '';
    startStudio();
  } catch (error) {
    $('authError').textContent = error.message === 'GUI access token required' ? 'That token was not accepted.' : error.message;
  } finally { $('authSubmit').disabled = false; }
});
restoreSettings();
if (accessToken) api('/health').then(startStudio).catch((error) => showAuth(error.message));
else showAuth();
