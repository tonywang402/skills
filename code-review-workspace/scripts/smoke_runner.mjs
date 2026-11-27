#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
};

function usage() {
  console.log('Usage: node smoke_runner.mjs <path-to-review.html>');
}

function findOnPath(names) {
  const pathDirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const name of names) {
    for (const dir of pathDirs) {
      const candidate = path.join(dir, name);
      try {
        fs.accessSync(candidate, fs.constants.X_OK);
        return candidate;
      } catch {}
    }
  }
  return null;
}

function resolvePlaywright(cachePwDir) {
  const require = createRequire(import.meta.url);
  const searchPaths = [process.cwd()];
  if (process.env.NODE_PATH) {
    searchPaths.push(...process.env.NODE_PATH.split(path.delimiter).filter(Boolean));
  }
  try {
    const globalRoot = execFileSync('npm', ['root', '-g'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (globalRoot) searchPaths.push(globalRoot);
  } catch {}
  searchPaths.push(path.join(cachePwDir, 'node_modules'));

  for (const pkg of ['playwright', 'playwright-core']) {
    try {
      const resolved = require.resolve(pkg, { paths: searchPaths });
      const idx = resolved.lastIndexOf(`${path.sep}node_modules${path.sep}`);
      const pwDir = idx >= 0 ? resolved.slice(0, idx) : path.dirname(path.dirname(resolved));
      return { mod: require(resolved), source: resolved, pwDir };
    } catch {}
  }

  fs.mkdirSync(cachePwDir, { recursive: true });
  const pkgJsonPath = path.join(cachePwDir, 'package.json');
  if (!fs.existsSync(pkgJsonPath)) {
    fs.writeFileSync(pkgJsonPath, JSON.stringify({ name: 'crw-playwright-cache', private: true }, null, 2) + '\n');
  }
  console.error(`Installing playwright into persistent cache: ${cachePwDir}`);
  execFileSync('npm', ['install', '--prefix', cachePwDir, '--no-save', '--no-audit', '--no-fund', 'playwright'], {
    stdio: 'inherit',
  });
  const resolved = require.resolve('playwright', { paths: [path.join(cachePwDir, 'node_modules')] });
  return { mod: require(resolved), source: resolved, pwDir: cachePwDir };
}

async function launchBrowser(chromium, pwDir) {
  try {
    const bundled = chromium.executablePath();
    if (bundled && fs.existsSync(bundled)) {
      return { browser: await chromium.launch({ headless: true }), via: `playwright-cached (${bundled})` };
    }
  } catch {}

  for (const channel of ['chrome', 'msedge']) {
    try {
      return { browser: await chromium.launch({ headless: true, channel }), via: `channel:${channel}` };
    } catch {}
  }

  const systemCandidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    findOnPath(['google-chrome', 'chromium', 'chromium-browser']),
  ].filter(Boolean);

  for (const executablePath of systemCandidates) {
    if (!fs.existsSync(executablePath)) continue;
    try {
      return { browser: await chromium.launch({ headless: true, executablePath }), via: `system:${executablePath}` };
    } catch {}
  }

  const cliBin = path.join(pwDir, 'node_modules', '.bin', process.platform === 'win32' ? 'playwright.cmd' : 'playwright');
  console.error('No cached Playwright browser or system Chrome found; installing Playwright Chromium once...');
  if (fs.existsSync(cliBin)) {
    execFileSync(cliBin, ['install', 'chromium'], { stdio: 'inherit' });
  } else {
    execFileSync('npx', ['--yes', 'playwright', 'install', 'chromium'], { stdio: 'inherit' });
  }
  return { browser: await chromium.launch({ headless: true }), via: 'playwright-installed' };
}

function startServer(rootDir) {
  const realRoot = fs.realpathSync(rootDir);
  const server = http.createServer((req, res) => {
    try {
      const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
      const relPath = urlPath.replace(/^\/+/, '');
      const candidate = path.resolve(realRoot, relPath);
      if (candidate !== realRoot && !candidate.startsWith(realRoot + path.sep)) {
        res.writeHead(403);
        res.end('Forbidden');
        return;
      }
      if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }
      const ext = path.extname(candidate).toLowerCase();
      res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
      fs.createReadStream(candidate).pipe(res);
    } catch {
      res.writeHead(500);
      res.end('Internal error');
    }
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolve({ server, port: address.port });
    });
  });
}

async function run() {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help') || args.includes('-h')) {
    usage();
    process.exit(args.length ? 0 : 2);
  }
  const htmlPath = path.resolve(args[0]);
  if (!fs.existsSync(htmlPath) || !fs.statSync(htmlPath).isFile()) {
    console.error(`ERROR: HTML file not found: ${htmlPath}`);
    process.exit(1);
  }
  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const smokeJsPath = path.join(scriptDir, 'smoke_test.js');
  if (!fs.existsSync(smokeJsPath)) {
    console.error(`ERROR: smoke_test.js not found next to smoke_runner.mjs: ${smokeJsPath}`);
    process.exit(1);
  }

  const cacheBase = process.env.CRW_CACHE_DIR || path.join(os.homedir(), '.cache', 'code-review-workspace');
  const pwCacheDir = path.join(cacheBase, 'playwright');
  const { mod: playwright, source, pwDir } = resolvePlaywright(pwCacheDir);
  const { server, port } = await startServer(path.dirname(htmlPath));
  const url = `http://127.0.0.1:${port}/${encodeURIComponent(path.basename(htmlPath))}`;

  let browser;
  const pageErrors = [];
  const results = [];
  let seeded = false;

  try {
    const launched = await launchBrowser(playwright.chromium, pwDir);
    browser = launched.browser;
    console.log(`Playwright: ${source}`);
    console.log(`Browser   : ${launched.via}`);
    console.log(`Target    : ${url}`);

    const context = await browser.newContext();
    const page = await context.newPage();
    page.on('pageerror', err => pageErrors.push(err.message || String(err)));

    const execPhase = async (phase, viewport) => {
      if (viewport) await page.setViewportSize(viewport);
      const res = await page.evaluate(p => window.__crwSmoke(p), phase);
      results.push({ phase, viewport: viewport ? `${viewport.width}x${viewport.height}` : null, ...res });
      const tag = viewport ? `${phase} ${viewport.width}x${viewport.height}` : phase;
      console.log(`${tag}: ${JSON.stringify(res)}`);
      return res;
    };

    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.addScriptTag({ path: smokeJsPath });
    const seedRes = await execPhase('seed');
    seeded = true;
    if (!seedRes.ok) throw new Error(`seed failed: ${seedRes.failures.join('; ')}`);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.addScriptTag({ path: smokeJsPath });
    const editRes = await execPhase('edit');
    if (!editRes.ok) throw new Error(`edit failed: ${editRes.failures.join('; ')}`);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.addScriptTag({ path: smokeJsPath });
    const verifyRes = await execPhase('verify');
    if (!verifyRes.ok) throw new Error(`verify failed: ${verifyRes.failures.join('; ')}`);

    const desktopRes = await execPhase('layout', { width: 1440, height: 900 });
    if (!desktopRes.ok) throw new Error(`layout 1440x900 failed: ${desktopRes.failures.join('; ')}`);

    const mobileRes = await execPhase('layout', { width: 390, height: 844 });
    if (!mobileRes.ok) throw new Error(`layout 390x844 failed: ${mobileRes.failures.join('; ')}`);
  } finally {
    if (browser && seeded) {
      try {
        const [page] = browser.contexts()[0]?.pages() || [];
        if (page) {
          const hasHelper = await page.evaluate(() => typeof window.__crwSmoke === 'function');
          if (!hasHelper) await page.addScriptTag({ path: smokeJsPath });
          const cleanupRes = await page.evaluate(() => window.__crwSmoke?.('cleanup'));
          if (cleanupRes) {
            results.push({ phase: 'cleanup', ...cleanupRes });
            console.log(`cleanup: ${JSON.stringify(cleanupRes)}`);
          }
        }
      } catch {}
    }
    if (browser) {
      try { await browser.close(); } catch {}
    }
    server.close();
  }

  if (pageErrors.length) {
    console.error(`Page errors: ${pageErrors.join(' | ')}`);
    process.exit(1);
  }
  const failed = results.filter(r => !r.ok);
  if (failed.length) {
    process.exit(1);
  }
}

run().catch(err => {
  console.error(`ERROR: ${err.message || err}`);
  process.exit(1);
});
