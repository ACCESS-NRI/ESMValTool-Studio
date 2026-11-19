const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync(require('node:path').join(__dirname, '../web/app.js'), 'utf8');
const functions = app.slice(app.indexOf('const recipeFileTypes ='), app.indexOf('async function parseCurrent()'));
function setup() {
  const elements = Object.fromEntries(['saveFile', 'saveRecipeAs', 'dirtyMark', 'documentName', 'yamlFileName', 'fileInput'].map(id => [id, {hidden: false, click() {this.clicked = true;}}]));
  const notices = [];
  const state = {yaml: 'datasets: []\n', name: 'library/recipe.yml', dirty: true, recipeVersion: 1};
  let persisted = 0;
  const downloads = [];
  const context = vm.createContext({
    state, window: {}, $: id => elements[id], toast: (...args) => notices.push(args),
    persistDraft: () => persisted++, canReplaceRecipe: () => true,
    setRecipe(yaml, name) {Object.assign(state, {yaml, name, dirty: false, fileHandle: null, recipeVersion: state.recipeVersion + 1});},
    document: {body: {append() {}}, createElement() {return {click() {downloads.push(this.download);}, remove() {}};}},
    Blob, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}}, setTimeout() {},
  });
  vm.runInContext(functions, context);
  return {state, elements, notices, context, downloads, persisted: () => persisted};
}
function fileHandle(name = 'saved.yml', hooks = {}) {
  const writes = [];
  return {name, writes, async createWritable() {
    if (hooks.open) await hooks.open();
    return {async write(source) {writes.push(source); if (hooks.write) await hooks.write();}, async close() {if (hooks.close) await hooks.close();}, async abort() {if (hooks.abort) hooks.abort();}};
  }};
}

test('save chooses a destination once, writes exact YAML and updates it on later saves', async () => {
  const h = setup(); const handle = fileHandle(); let picks = 0;
  h.context.window.showSaveFilePicker = async options => {picks++; assert.equal(options.suggestedName, 'recipe.yml'); return handle;};
  await h.context.saveRecipe();
  assert.equal(h.state.fileHandle, handle);
  assert.equal(h.state.name, 'saved.yml');
  assert.equal(h.state.dirty, false);
  assert.equal(h.elements.dirtyMark.hidden, true);
  h.state.yaml = '# edited\ndatasets: []\n'; h.state.dirty = true;
  await h.context.saveRecipe();
  assert.equal(picks, 1);
  assert.deepEqual(handle.writes, ['datasets: []\n', '# edited\ndatasets: []\n']);
  assert.equal(h.persisted(), 2);
});

test('Save as writes a new file and leaves the original untouched', async () => {
  const h = setup(); const original = fileHandle('original.yml'); const copy = fileHandle('copy.yml');
  h.state.fileHandle = original; h.context.window.showSaveFilePicker = async () => copy;
  await h.context.saveRecipe(true);
  assert.equal(original.writes.length, 0);
  assert.equal(copy.writes.length, 1);
  assert.equal(h.state.fileHandle, copy);
});

test('cancel and failed writes preserve unsaved edits, draft and destination', async () => {
  for (const failure of ['cancel', 'open', 'write', 'close']) {
    const h = setup(); const original = fileHandle('original.yml'); h.state.fileHandle = original;
    let aborted = false;
    const hooks = {[failure]: () => {throw new Error('Disk unavailable');}, abort: () => {aborted = true;}};
    h.context.window.showSaveFilePicker = async () => {
      if (failure === 'cancel') throw Object.assign(new Error('Cancelled'), {name: 'AbortError'});
      return fileHandle('copy.yml', hooks);
    };
    await h.context.saveRecipe(true);
    assert.equal(h.state.dirty, true, failure);
    assert.equal(h.state.fileHandle, original, failure);
    assert.equal(h.elements.dirtyMark.hidden, false, failure);
    assert.equal(h.persisted(), 0, failure);
    assert.equal(h.elements.saveFile.disabled, false, failure);
    assert.equal(h.notices.length, failure === 'cancel' ? 0 : 1, failure);
    assert.equal(aborted, ['write', 'close'].includes(failure), failure);
  }
});

test('edits made during a save remain dirty after the original snapshot is saved', async () => {
  const h = setup();
  const handle = fileHandle('saved.yml', {write: () => {h.state.yaml = 'newer edits';}});
  h.context.window.showSaveFilePicker = async () => handle;
  await h.context.saveRecipe();
  assert.deepEqual(handle.writes, ['datasets: []\n']);
  assert.equal(h.state.dirty, true);
  assert.equal(h.elements.dirtyMark.hidden, false);
  assert.match(h.notices[0][0], /Newer edits/);
});

test('switching recipes during saving does not assign the old handle or clear new edits', async () => {
  const h = setup();
  h.context.window.showSaveFilePicker = async () => fileHandle('old.yml', {write: () => {h.state.recipeVersion++; h.state.name = 'new.yml';}});
  await h.context.saveRecipe();
  assert.equal(h.state.name, 'new.yml');
  assert.equal(h.state.fileHandle, undefined);
  assert.equal(h.state.dirty, true);
  assert.equal(h.persisted(), 0);
});

test('concurrent save requests do not open multiple file pickers', async () => {
  const h = setup(); let finish; let picks = 0;
  h.context.window.showSaveFilePicker = () => {picks++; return new Promise(resolve => {finish = resolve;});};
  const saving = h.context.saveRecipe();
  await h.context.saveRecipe();
  assert.equal(picks, 1);
  assert.equal(h.elements.saveFile.disabled, true);
  finish(fileHandle()); await saving;
  assert.equal(h.elements.saveFile.disabled, false);
});

test('unsupported browsers download a copy and retain the unsaved recovery draft', async () => {
  const h = setup(); await h.context.saveRecipe();
  assert.deepEqual(h.downloads, ['recipe.yml']);
  assert.equal(h.state.dirty, true);
  assert.equal(h.persisted(), 0);
});

test('opening a local file remembers its handle for Save; fallback uses the file input', async () => {
  const h = setup(); const handle = fileHandle('opened.yml');
  handle.getFile = async () => ({name: handle.name, text: async () => 'documentation: {}\n'});
  h.context.window.showOpenFilePicker = async () => [handle];
  await h.context.openRecipeFile();
  assert.equal(h.state.name, 'opened.yml');
  assert.equal(h.state.fileHandle, handle);
  h.state.yaml += 'datasets: []\n';
  await h.context.saveRecipe();
  assert.deepEqual(handle.writes, ['documentation: {}\ndatasets: []\n']);
  delete h.context.window.showOpenFilePicker;
  await h.context.openRecipeFile();
  assert.equal(h.elements.fileInput.clicked, true);
});
