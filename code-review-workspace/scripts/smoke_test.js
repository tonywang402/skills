// In-page smoke test for a rendered code-review workspace (no dependencies).
// Load this file into the page (e.g. Playwright `page.addScriptTag({ path })`, or evaluate its text), then run
//   await window.__crwSmoke('seed')    -> reload the page and load this file again
//   await window.__crwSmoke('verify')
//   await window.__crwSmoke('layout')  -> once at 1440x900 and once at 390x844
//   await window.__crwSmoke('cleanup') -> restores this review's localStorage keys to their pre-test values
// Every phase resolves to { phase, ok, failures, checks }; `checks` names each verified behavior.
(() => {
  const SNAPSHOT = '__crwSmokeSnapshot';
  const BODY = 'crw smoke test comment';
  const head = document.body.dataset.reviewCommit;
  const KEYS = [`code-review-comments:${head}`, `code-review-language:${head}`];
  const CJK_CHAR = /[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/;
  const hasChinese = text => CJK_CHAR.test(text.replace(/`[^`]*`/g, '')); // `code` and `paths` are never translated
  const NOT_TRANSLATED = '.diff-table, .diff-file-header, .file-path, .review-thread, .language-switch, .review-comments-note';
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const stored = () => {
    try { return JSON.parse(localStorage.getItem(KEYS[0]) || 'null')?.comments || []; } catch { return []; }
  };
  const reveal = element => {
    for (let d = element.closest('details'); d; d = d.parentElement?.closest('details')) d.open = true;
  };

  function chineseLeftInEnglish() {
    const found = [];
    const walker = document.createTreeWalker($('.shell'), NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement.closest(NOT_TRANSLATED) && hasChinese(node.textContent)) found.push(node.textContent.trim().slice(0, 50));
    }
    return found;
  }

  async function seed() {
    const checks = {};
    const failures = [];
    const check = (name, passed, message) => { checks[name] = passed; if (!passed) failures.push(message); };
    if (!sessionStorage.getItem(SNAPSHOT)) {
      sessionStorage.setItem(SNAPSHOT, JSON.stringify(KEYS.map(key => [key, localStorage.getItem(key)])));
    }
    const files = $$('#file-groups li.file');
    const paths = files.map(li => $('.diff-file-header a', li).textContent.trim());
    if (!files.length) return { phase: 'seed', ok: false, failures: ['no files rendered'], checks };
    check('filesUnique', new Set(paths).size === paths.length, 'a file is rendered more than once');
    const guides = typeof FILE_DIFF_GUIDES === 'object' ? FILE_DIFF_GUIDES : {};
    const noGuide = paths.filter(path => !guides[path]?.units?.length);
    check('panelsComplete', !noGuide.length, `files without panel units: ${noGuide.slice(0, 5).join(', ')}`);

    $('[data-language="en"]').click();
    const residue = chineseLeftInEnglish();
    check('englishComplete', !residue.length, `Chinese text left in English mode: ${residue.slice(0, 5).join(' | ')}`);
    const first = files[0];
    reveal($('.file-summary', first));
    $('.ask-diff-button', first).click();
    await wait(300);
    const panel = $('#ask-diff-panel');
    check('askPanelOpens', panel.classList.contains('open') && $$('.ask-unit', panel).length > 0, 'Ask panel did not open with units');
    check('askPanelEnglish', !$$('.ask-unit-content', panel).some(p => hasChinese(p.textContent)), 'English Ask panel shows Chinese text');
    $('[data-language="zh"]').click();
    await wait(100);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await wait(300);
    check('askPanelEscape', !panel.classList.contains('open'), 'Escape did not close the Ask panel');

    const details = $('.file-diff', first);
    details.open = true;
    const viewed = $('.viewed-button', first);
    viewed.click();
    const collapsed = viewed.getAttribute('aria-pressed') === 'true' && !details.open;
    viewed.click();
    check('viewedToggle', collapsed && viewed.getAttribute('aria-pressed') === 'false' && details.open, 'Viewed did not collapse/expand the file');

    const add = $('#file-groups .review-add-button');
    if (!add) return { phase: 'seed', ok: false, failures: [...failures, 'no commentable diff line'], checks };
    reveal(add);
    add.click();
    await wait(50);
    const textarea = $('#review-comment-draft');
    if (!textarea) return { phase: 'seed', ok: false, failures: [...failures, 'comment editor did not open'], checks };
    textarea.value = BODY;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    $('[data-review-action="save-draft"]').click();
    await wait(50);
    check('commentSaved', stored().some(comment => comment.body === BODY), 'comment was not saved to localStorage');
    return { phase: 'seed', ok: !failures.length, failures, checks, next: 'reload, load this file again, run verify' };
  }

  async function verify() {
    const checks = {};
    const failures = [];
    const check = (name, passed, message) => { checks[name] = passed; if (!passed) failures.push(message); };
    const record = stored().find(comment => comment.body === BODY);
    if (!record) return { phase: 'verify', ok: false, failures: ['smoke comment missing after reload (run seed first)'], checks };
    check('commentRestored', $$('.review-thread.saved').some(thread => thread.textContent.includes(BODY)), 'comment not restored inline after reload');
    const markdown = exportMarkdown();
    check('copyAllMarkdown', markdown.includes(BODY) && markdown.includes(record.filePath) && markdown.includes(record.code[0]),
      'Copy all markdown lacks the comment, file path or code');
    // Capture the Copy all payload instead of overwriting the user's system clipboard.
    let copied = null;
    const clipboard = navigator.clipboard;
    const originalExec = document.execCommand;
    if (clipboard) Object.defineProperty(clipboard, 'writeText', { configurable: true, value: async text => { copied = text; } });
    document.execCommand = command => command === 'copy';
    try {
      $('#review-copy-all').click();
      await wait(400);
    } finally {
      if (clipboard) delete clipboard.writeText;
      document.execCommand = originalExec;
    }
    check('copyAllButton', !(clipboard && window.isSecureContext) || copied === markdown, 'Copy all did not hand the exported Markdown to the clipboard');
    const id = CSS.escape(record.id);
    $('#review-comments-panel').open = true;
    $(`#review-comments-list [data-review-action="locate"][data-comment-id="${id}"]`)?.click();
    await wait(100);
    check('locate', Boolean($('.diff-row.review-located')), 'Locate did not highlight the commented line');
    const originalConfirm = window.confirm;
    window.confirm = () => true;
    try {
      $(`#review-comments-list [data-review-action="delete"][data-comment-id="${id}"]`)?.click();
    } finally {
      window.confirm = originalConfirm;
    }
    check('delete', !stored().some(comment => comment.body === BODY), 'Delete did not remove the comment');
    return { phase: 'verify', ok: !failures.length, failures, checks };
  }

  async function layout() {
    $$('#file-groups details').forEach(d => { d.open = false; });
    let widest = null;
    let longest = 0;
    $$('#file-groups li.file').forEach(li => {
      const length = $$('.diff-code', li).reduce((max, cell) => Math.max(max, cell.textContent.length), 0);
      if (length > longest) { longest = length; widest = li; }
    });
    if (widest) reveal($('.file-summary', widest));
    await wait(150);
    const width = document.documentElement.clientWidth;
    const overflow = document.documentElement.scrollWidth - width;
    const outside = $$('body *').filter(element => {
      if (element.closest('.diff-scroll, .ask-panel') || getComputedStyle(element).position === 'fixed') return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.right > width + 1;
    });
    const outsideSet = new Set(outside);
    const offenders = outside.filter(element => ![...element.children].some(child => outsideSet.has(child)))
      .slice(0, 5).map(element => `${element.tagName.toLowerCase()}${element.className ? `.${String(element.className).trim().split(/\s+/).join('.')}` : ''} "${element.textContent.trim().slice(0, 40)}"`);
    $$('#file-groups details').forEach(d => { d.open = false; });
    const failures = [];
    if (overflow > 1) failures.push(`page scrolls horizontally by ${overflow}px`);
    if (offenders.length) failures.push(`elements extend past the viewport (clipped or overflowing): ${offenders.join(', ')}`);
    return { phase: 'layout', viewport: `${innerWidth}x${innerHeight}`, ok: !failures.length, failures };
  }

  function cleanup() {
    const snapshot = JSON.parse(sessionStorage.getItem(SNAPSHOT) || 'null');
    if (!snapshot) return { phase: 'cleanup', ok: false, failures: ['no snapshot in this tab; nothing restored'] };
    // The page writes the language key on every load; absent and 'zh' mean the same, so leave no residue.
    snapshot.forEach(([key, value]) => (value === null || (key === KEYS[1] && value === 'zh')
      ? localStorage.removeItem(key) : localStorage.setItem(key, value)));
    sessionStorage.removeItem(SNAPSHOT);
    return { phase: 'cleanup', ok: true, failures: [], restored: snapshot.map(([key]) => key) };
  }

  const phases = { seed, verify, layout, cleanup };
  window.__crwSmoke = async phase => {
    if (!phases[phase]) return { phase, ok: false, failures: [`unknown phase "${phase}"`] };
    try {
      return await phases[phase]();
    } catch (error) {
      return { phase, ok: false, failures: [`exception: ${error.message}`] };
    }
  };
})();
