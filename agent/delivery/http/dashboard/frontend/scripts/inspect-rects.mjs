import { spawn } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_ROOT = path.resolve(__dirname, '..');
const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const tokensCss = fs.readFileSync(path.join(FRONTEND_ROOT, 'src/styles/tokens.css'), 'utf8');
const shellCss = fs.readFileSync(path.join(FRONTEND_ROOT, 'src/styles/shell.css'), 'utf8');
const chatCss = fs.readFileSync(path.join(FRONTEND_ROOT, 'src/styles/chat.css'), 'utf8');
const responsiveCss = fs.readFileSync(path.join(FRONTEND_ROOT, 'src/styles/responsive.css'), 'utf8');

const targetWidth = parseInt(process.argv[2] || '375', 10);
const targetHeight = targetWidth <= 390 ? 844 : targetWidth <= 414 ? 896 : 800;

const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=${targetWidth}, initial-scale=1.0, viewport-fit=cover" />
  <style>
    ${tokensCss}
    ${shellCss}
    ${chatCss}
    ${responsiveCss}
    body { margin: 0; padding: 0; }
  </style>
</head>
<body>
  <div class="app-layout">
    <main class="main">
      <div class="content">
        <div class="chat-shell chat-shell-resizable workspace-closed">
          <div class="chat-panel">
            <div class="transcript" id="transcript">
              
              <div class="message user" id="userMsg">
                <div class="message-bubble" id="userBubble">
                  <div class="message-text">ok</div>
                  <div class="message-actions" id="userActions">
                    <button class="message-action-btn" id="copyBtn">C</button>
                    <button class="message-action-btn" id="editBtn">E</button>
                    <div class="message-branch-switcher" id="branchSwitcher">
                      <button class="message-action-btn" id="prevBranch">&lt;</button>
                      <span class="message-branch-count" id="branchCount">1/2</span>
                      <button class="message-action-btn" id="nextBranch">&gt;</button>
                    </div>
                  </div>
                </div>
              </div>

              <div class="message assistant" id="asstMsg">
                <div class="message-bubble">
                  <p>Assistant response.</p>
                </div>
              </div>

            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</body>
</html>`;

const tmpPath = path.join(FRONTEND_ROOT, `temp_inspect_${targetWidth}.html`);
fs.writeFileSync(tmpPath, html, 'utf8');

const cdpPort = 9333 + (targetWidth % 100);
const chromeProc = spawn(CHROME_PATH, [
  '--headless=new',
  `--remote-debugging-port=${cdpPort}`,
  '--disable-gpu',
  '--no-first-run',
  `--window-size=${targetWidth},${targetHeight}`,
  `file:///${tmpPath.replace(/\\/g, '/')}`,
]);

async function main() {
  await new Promise((r) => setTimeout(r, 600));

  const targets = await new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${cdpPort}/json`, (res) => {
      let d = '';
      res.on('data', (c) => (d += c));
      res.on('end', () => resolve(JSON.parse(d)));
    }).on('error', reject);
  });

  const page = targets.find((t) => t.type === 'page') || targets[0];
  const ws = new WebSocket(page.webSocketDebuggerUrl);

  await new Promise((resolve) => (ws.onopen = resolve));

  const send = (method, params = {}) =>
    new Promise((resolve) => {
      const id = Math.floor(Math.random() * 10000);
      const handler = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.id === id) {
          ws.removeEventListener('message', handler);
          resolve(msg.result);
        }
      };
      ws.addEventListener('message', handler);
      ws.send(JSON.stringify({ id, method, params }));
    });

  await send('Emulation.setDeviceMetricsOverride', {
    width: targetWidth,
    height: targetHeight,
    deviceScaleFactor: 2,
    mobile: targetWidth <= 768,
  });

  await send('Page.enable');
  await send('Page.navigate', { url: `file:///${tmpPath.replace(/\\/g, '/')}` });
  await new Promise((r) => setTimeout(r, 600));

  const result = await send('Runtime.evaluate', {
    expression: `
      (() => {
        const rect = (id) => {
          const el = document.getElementById(id);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const s = window.getComputedStyle(el);
          return {
            id,
            tag: el.tagName,
            left: Math.round(r.left),
            right: Math.round(r.right),
            top: Math.round(r.top),
            bottom: Math.round(r.bottom),
            width: Math.round(r.width),
            height: Math.round(r.height),
            position: s.position,
            maxWidth: s.maxWidth,
          };
        };

        return {
          viewport: { innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth },
          userMsg: rect('userMsg'),
          userBubble: rect('userBubble'),
          userActions: rect('userActions'),
          copyBtn: rect('copyBtn'),
          editBtn: rect('editBtn'),
          branchSwitcher: rect('branchSwitcher'),
          prevBranch: rect('prevBranch'),
          branchCount: rect('branchCount'),
          nextBranch: rect('nextBranch'),
          asstMsg: rect('asstMsg'),
        };
      })()
    `,
    returnByValue: true,
  });

  console.log('LAYOUT INSPECTION REPORT:');
  console.log(JSON.stringify(result.result.value, null, 2));

  ws.close();
  chromeProc.kill();
  try {
    fs.unlinkSync(tmpPath);
  } catch (e) {}
}

main().catch(console.error);
