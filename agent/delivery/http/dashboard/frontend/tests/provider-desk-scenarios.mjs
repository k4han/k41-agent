import assert from 'node:assert/strict';

export async function verifyProviderDesk({ js, navigate, until, call, snapshot, setAcceptDialog, getDialogs, setSourceFailure, aiConnections, decisionSecrets, writes, getDefaults }) {
  const click = async text => {
    await until(() => js(`Array.from(document.querySelectorAll('button')).some(button => button.textContent.trim() === ${JSON.stringify(text)} && !button.disabled)`), `enabled ${text} button`);
    return js(`Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === ${JSON.stringify(text)} && !button.disabled).click()`);
  };
  const change = async (selector, value) => {
    await until(() => js(`!!document.querySelector(${JSON.stringify(selector)}) && !document.querySelector(${JSON.stringify(selector)}).disabled`), `enabled ${selector}`);
    return js(`(() => { const input = document.querySelector(${JSON.stringify(selector)}); input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); })()`);
  };
  await navigate('/settings/providers');
  await until(() => js('!!document.querySelector("[data-connection-name=team]")'), 'unified connections');
  assert.equal(await js('document.querySelectorAll("aside.sidebar").length'), 1);
  assert.equal(await js('!!document.querySelector("[data-provider-picker]")'), false);
  assert.equal(await js('getComputedStyle(document.querySelector(".provider-desk-search input")).borderTopWidth'), '0px');
  await click('Add connection');
  await until(() => js('!!document.querySelector("[data-provider-id=tavily]")'), 'separate catalog');
  assert.equal(await js('!!document.querySelector("[data-provider-connections]")'), false);
  assert.equal(await js('document.querySelectorAll(".provider-catalog-tile").length'), 20);
  await click('Next');
  await until(() => js('new URLSearchParams(location.search).get("page") === "2"'), 'catalog pagination');
  await click('Previous');
  await js('document.querySelector("[data-provider-id=tavily]").click()');
  await until(() => js('!!document.querySelector("[role=dialog] form")'), 'create dialog');
  await change('form input.input', 'review');
  await change('form input[type=password]', 'fake-key');
  await js('Array.from(document.querySelectorAll(".settings-tabs [role=tab]")).find(tab => tab.textContent.includes("Connections")).click()');
  assert.equal(getDialogs(), 1);
  assert.equal(await js('document.querySelector("form input.input").value'), 'review');
  setAcceptDialog(true);
  await click('Cancel');
  await until(() => js('!document.querySelector("[role=dialog]")'), 'cancel dirty dialog');
  assert.equal(writes.some(write => write.path === '/dashboard-api/web-connections'), false);

  for (const [kind, provider, count] of [['llm', 'google', 2], ['web', 'tavily', 2], ['decision', 'cloudflare', 2], ['decision', 'google', 1], ['web', 'google', 1]]) {
    for (let index = 1; index <= count; index++) {
      await navigate(`/settings/providers?tab=catalog&kind=${kind}`);
      await until(() => js(`!!document.querySelector('[data-provider-id="${provider}"]')`), `${kind} catalog entry`);
      await js(`document.querySelector('[data-provider-id="${provider}"]').click()`);
      const expected = index === 1 ? provider : `${provider}-${index}`;
      await until(() => js(`document.querySelector('form input.input')?.value === '${expected}'`), `${kind} suggested name`);
      await until(() => js('!!document.activeElement.closest("[role=dialog]")'), 'dialog initial focus');
      await change('form input[type=password]', 'saved-secret');
      await js(`(() => { const account = Array.from(document.querySelectorAll('form label')).find(label => label.textContent.includes('Account ID'))?.querySelector('input'); if (account) { account.value = 'account'; account.dispatchEvent(new Event('input', { bubbles: true })); } document.querySelector('form').requestSubmit(); })()`);
      await until(() => js(`location.pathname === '/settings/providers/${kind}/${expected}' && !document.querySelector('[role=dialog]')`), `${kind} saved detail`);
      if (kind === 'llm' && index === 1) {
        await js('document.getElementById("provider-editor-tab-decision").click()');
        await until(() => js('document.body.textContent.includes("No Decision connections saved for this provider")'), 'empty provider Decision selector');
        assert.equal(await js('document.querySelector("[data-provider-decision] select").value'), '');
        await snapshot('decision-empty');
      }
    }
  }

  for (const kind of ['llm', 'decision']) {
    await navigate(`/settings/providers?tab=catalog&kind=${kind}&new=google`);
    await until(() => js('!!document.querySelector("form input[type=password]")'), 'duplicate form');
    await change('form input.input', 'google');
    await change('form input[type=password]', 'duplicate-secret');
    await js('document.querySelector("form").requestSubmit()');
    await until(() => js('document.body.textContent.includes("Provider already exists.")'), 'duplicate name feedback');
    assert.equal(await js('document.querySelector("form input[type=password]").value'), 'duplicate-secret');
    await click('Cancel');
    await until(() => js('!document.querySelector("[role=dialog]")'), 'duplicate draft cancelled');
  }

  await navigate('/settings/providers');
  await until(() => js('document.querySelectorAll(".provider-desk-table tbody tr").length === 8'), 'connection pagination');
  await click('Next');
  await until(() => js('document.querySelectorAll(".provider-desk-table tbody tr").length === 1'), 'next connection page');
  await change('input[type=search][aria-label="Search connections, providers, models"]', 'google');
  await until(() => js('document.querySelectorAll(".provider-desk-table tbody tr").length === 4'), 'cross-kind search');
  assert.equal(await js('document.querySelectorAll("[data-connection-name=google]").length'), 3);
  await change('select[aria-label="Connection type"]', 'decision');
  await until(() => js('document.querySelectorAll(".provider-desk-table tbody tr").length === 1'), 'kind filter');
  await js(`document.querySelector('button[aria-label="Set google as default"]').click()`);
  await until(() => getDefaults().decisionDefault === 'google', 'decision default');
  await until(() => js(`document.querySelector('button[aria-label="Delete google"]').disabled`), 'default deletion guard');

  await navigate('/settings/providers/decision/google');
  await until(() => js('!!document.querySelector("form")'), 'decision detail');
  assert.equal(await js('!!document.querySelector("form input[type=password]")'), false);
  await click('Test Connection');
  await until(() => js('!!document.querySelector(".connection-latency-badge")'), 'stored decision secret verification');
  assert.equal(Object.hasOwn(writes.findLast(write => write.path.endsWith('/verify')).body.fields, 'api_key'), false);
  await js('document.getElementById("decision-editor-tab-decision").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }))');
  assert.equal(await js('document.activeElement.id'), 'decision-editor-tab-connection');
  await js('document.querySelector("form input[type=checkbox]").click()');
  await change('form input[type=password]', 'new-google-key');
  await js('document.getElementById("decision-editor-tab-decision").click()');
  assert.equal(await js('document.querySelector("form input[type=password]").value'), 'new-google-key');
  await js('document.querySelector("form").requestSubmit()');
  await until(() => decisionSecrets.get('google') === 'new-google-key', 'decision secret saved');
  await until(() => js('!document.querySelector(".provider-save-bar")'), 'clean decision draft');

  await navigate('/settings/providers/web/google');
  await until(() => js('!!document.querySelector("form input[type=checkbox]")'), 'web editor');
  assert.equal(await js('!!document.querySelector("form input[type=password]")'), false);
  await js('document.querySelector("form input[type=checkbox]").click()');
  await change('form input[type=password]', 'replacement-web-key');
  assert.equal(await js('getComputedStyle(document.querySelector("form")).overflowY'), 'visible');
  await js('document.querySelector("form").requestSubmit()');
  await until(() => js('!document.querySelector(".provider-save-bar")'), 'web secret saved');
  assert.equal(writes.findLast(write => write.path.endsWith('/web-connections/google')).body.api_key, 'replacement-web-key');
  await change('form input:not([type=password]):not([type=checkbox]):not([disabled])', 'cse-id');
  await js('document.querySelector("form").requestSubmit()');
  await until(() => js('!document.querySelector(".provider-save-bar")'), 'web metadata saved');
  assert.equal(Object.hasOwn(writes.findLast(write => write.path.endsWith('/web-connections/google')).body, 'api_key'), false);

  await navigate('/settings/providers/llm/google-2');
  await until(() => js('!!document.querySelector("#provider-editor-tab-models")'), 'model editor tabs');
  await js(`(() => { const input = Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('Default Model')).querySelector('input'); input.value = 'gemini-test'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('Save changes');
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'model save confirmation');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Save")).click()');
  await until(() => js('!document.querySelector(".dialog-footer") && !document.querySelector(".provider-save-bar")'), 'model saved');
  await js(`(() => { const input = Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('Fetch Active Models')).querySelector('textarea'); input.value = 'manual/model'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('Fetch Active Models');
  await until(() => js(`Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('Fetch Active Models')).querySelector('textarea').value.includes('discovered-model')`), 'model discovery');
  assert.equal(await js(`Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('Fetch Active Models')).querySelector('textarea').value.includes('manual/model')`), true);
  await click('Save changes');
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'discovery save confirmation');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Save")).click()');
  await until(() => js('!document.querySelector(".dialog-footer") && !document.querySelector(".provider-save-bar")'), 'discovered and manual models saved');
  await js('document.getElementById("provider-editor-tab-connection").click()');
  await js(`(() => { const input = Array.from(document.querySelectorAll('.setting-row')).find(row => row.textContent.includes('API Key')).querySelector('input'); input.value = 'draft-key'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  assert.equal(await js('document.getElementById("provider-editor-tab-connection").getAttribute("aria-selected")'), 'true');
  await js('document.getElementById("provider-editor-tab-models").click(); document.getElementById("provider-editor-tab-connection").click()');
  assert.equal(await js('Array.from(document.querySelectorAll(".setting-row")).find(row => row.textContent.includes("API Key")).querySelector("input").value'), 'draft-key');
  await click('Discard draft');
  assert.equal(aiConnections.find(item => item.name === 'google-2').api_key, 'saved-secret');
  await navigate('/settings/providers?tab=llm');
  await until(() => js('!!document.querySelector("[data-connection-name=google-2]")'), 'legacy models filter');
  await js(`document.querySelector('button[aria-label="Set google-2 as default"]').click()`);
  await until(() => getDefaults().aiDefault === 'google-2/gemini-test', 'global model default');

  await navigate('/settings/providers?tab=defaults');
  await until(() => js(`document.querySelector('select[aria-label="Global model target"]')?.value === "google-2"`), 'global defaults page');
  await change('input[aria-label="Global model ID"]', 'manual/nested-model');
  await click('Save selection');
  await until(() => getDefaults().aiDefault === 'google-2/manual/nested-model', 'independent manual global model');
  assert.equal(aiConnections.find(item => item.name === 'google-2').default_model, 'gemini-test');
  await change('select[aria-label="Global model target"]', '@pool/balanced');
  await click('Save selection');
  await until(() => getDefaults().aiDefault === '@pool/balanced', 'pool default');
  await change('select[aria-label="Tavily default connection"]', 'tavily');
  await until(() => getDefaults().defaults.tavily === 'tavily', 'web service default');
  await click('Clear default');
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'clear default confirmation');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Clear Default")).click()');
  await until(() => getDefaults().decisionDefault === '', 'decision fallback');
  await until(() => js('!document.querySelector("[role=dialog]")'), 'cleared default dialog closed');

  setSourceFailure('decision');
  await navigate('/settings/providers');
  await until(() => js('document.body.textContent.includes("Decision source unavailable")'), 'independent source failure');
  assert.equal(await js('!!document.querySelector("[data-connection-kind=web]") && !!document.querySelector("[data-connection-kind=llm]")'), true);
  setSourceFailure('');
  await click('Retry Decision');
  await until(() => js('!!document.querySelector("[data-connection-kind=decision]") && !document.querySelector("[role=alert]")'), 'failed source retried');
  await js('document.documentElement.classList.remove("dark")');
  await snapshot('connections-light');
  await js('document.documentElement.classList.add("dark")');
  await snapshot('connections-dark');
  await js('document.documentElement.classList.remove("dark")');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await navigate('/settings/providers');
  await until(() => js('!!document.querySelector(".provider-desk-table") && !document.body.textContent.includes("Loading provider connections")'), 'mobile connections preview');
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true);
  await snapshot('connections-mobile');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

  await navigate('/settings/providers?tab=catalog');
  await until(() => js('!!document.querySelector(".provider-catalog-tile")'), 'catalog preview');
  await snapshot('catalog-light');
  await navigate('/settings/providers?tab=defaults');
  await until(() => js(`document.querySelector('select[aria-label="Global model target"]')?.value === '@pool/balanced'`), 'pool default survives reload and asynchronous options');
  await snapshot('defaults-light');

  await navigate('/settings/providers?tab=web');
  await until(() => js('!!document.querySelector("[data-connection-name=tavily-2]")'), 'web deletion');
  await js(`document.querySelector('button[aria-label="Delete tavily-2"]').click()`);
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'delete confirmation');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Delete")).click()');
  await until(() => js('!document.querySelector("[data-connection-name=tavily-2]") && !document.querySelector("[role=dialog]")'), 'web deleted');
  assert.equal(writes.findLast(write => write.path.endsWith('/tavily-2')).method, 'DELETE');

  setSourceFailure('decision');
  await navigate('/settings/providers/llm/google-2');
  await until(() => js('!!document.getElementById("provider-editor-tab-decision")'), 'model Decision tab');
  await js('document.getElementById("provider-editor-tab-decision").click()');
  await until(() => js('!!document.querySelector("[data-provider-decision] [role=alert]")'), 'provider Decision load failure');
  assert.equal(await js(`document.querySelector('[aria-label="Provider Decision connection"]').disabled`), true);
  setSourceFailure('');
  await click('Retry Decision');
  await until(() => js('!!document.querySelector("[data-provider-decision] form input[disabled]")'), 'inline Decision editor');
  assert.deepEqual(await js(`Array.from(document.querySelector('[aria-label="Provider Decision connection"]').options).filter(option => option.value).map(option => option.value)`), ['google']);
  assert.equal(await js('document.querySelectorAll("aside.sidebar").length'), 1);
  await js(`(() => { const input = Array.from(document.querySelectorAll('[data-provider-decision] form label')).find(label => label.firstChild.textContent.trim() === 'Model').querySelector('input'); input.value = 'custom-decision-model'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await js('document.getElementById("provider-editor-tab-models").click(); document.getElementById("provider-editor-tab-decision").click()');
  assert.equal(await js(`Array.from(document.querySelectorAll('[data-provider-decision] form label')).find(label => label.firstChild.textContent.trim() === 'Model').querySelector('input').value`), 'custom-decision-model');
  await js(`Array.from(document.querySelectorAll('[data-provider-decision] button')).find(button => button.textContent.trim() === 'Test Connection').click()`);
  await until(() => js('!!document.querySelector("[data-provider-decision] .connection-latency-badge")'), 'inline Decision access test');
  const verifyWrite = writes.findLast(write => write.path === '/dashboard-api/decision-providers/verify');
  assert.equal(verifyWrite.body.name, 'google');
  assert.equal(verifyWrite.body.fields.model, 'custom-decision-model');
  assert.equal(Object.hasOwn(verifyWrite.body.fields, 'api_key'), false);
  setAcceptDialog(false);
  await click('Add Decision connection');
  await until(() => js('document.querySelector("[role=dialog]")?.textContent.includes("Discard Decision Changes")'), 'Decision draft discard confirmation');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Cancel")).click()');
  await until(() => js('!document.querySelector("[role=dialog]")'), 'Decision draft retained');
  assert.equal(await js('document.querySelector("[data-provider-decision] form input[disabled]").value'), 'google');
  await js('document.querySelector("[data-provider-decision] form").requestSubmit()');
  await until(() => js('!document.querySelector("[data-provider-decision] .provider-save-bar")'), 'inline Decision saved');
  const decisionWrite = writes.findLast(write => write.path === '/dashboard-api/decision-providers/google');
  assert.equal(decisionWrite.body.fields.model, 'custom-decision-model');
  assert.equal(Object.hasOwn(decisionWrite.body.fields, 'api_key'), false);
  assert.equal(aiConnections.find(item => item.name === 'google-2').default_model, 'gemini-test');
  await click('Use as Decision default');
  await until(() => getDefaults().decisionDefault === 'google', 'inline Decision default');
  await until(() => js('document.querySelector("[data-provider-decision] .provider-desk-default")?.textContent.includes("Default Decision")'), 'inline default badge');
  await js('document.getElementById("provider-editor-tab-decision").scrollIntoView({ block: "start" })');
  await snapshot('decision-configured');
  await js('document.documentElement.classList.add("dark")');
  await snapshot('decision-dark');
  await js('document.documentElement.classList.remove("dark")');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await js('document.querySelector("[data-provider-decision]").scrollIntoView({ block: "start" })');
  assert.equal(await js('document.documentElement.scrollWidth <= window.innerWidth'), true);
  await snapshot('decision-mobile');
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

  await click('Add Decision connection');
  await until(() => js('!!document.querySelector("[data-provider-decision] form input[type=password]")'), 'inline Decision creation');
  await change('[data-provider-decision] form input.input', 'new');
  await change('[data-provider-decision] form input[type=password]', 'inline-decision-secret');
  await js('document.querySelector("[data-provider-decision] form").requestSubmit()');
  await until(() => js(`document.querySelector('[aria-label="Provider Decision connection"]')?.value === 'new' && !!document.querySelector('[data-provider-decision] form input[disabled]')`), 'created Decision selected');
  assert.equal(await js('location.pathname'), '/settings/providers/llm/google-2');
  assert.equal(decisionSecrets.get('new'), 'inline-decision-secret');
  await change('[aria-label="Provider Decision connection"]', 'google');
  await until(() => js('document.querySelector("[data-provider-decision] form input[disabled]")?.value === "google"'), 'existing Decision selected');
  await change('[aria-label="Provider Decision connection"]', 'new');
  await until(() => js('document.querySelector("[data-provider-decision] form input[disabled]")?.value === "new"'), 'connection named new remains editable');
  await click('Use as Decision default');
  await until(() => getDefaults().decisionDefault === 'new', 'created Decision default');

  await navigate('/settings/providers?tab=defaults');
  await until(() => js(`document.querySelector('[aria-label="Default Decision connection"]')?.value === 'new'`), 'Decision selection survives asynchronous options and reload');
  assert.equal(await js('document.querySelector("[data-decision-default] .provider-decision-summary").textContent.includes("gemini-decision-1")'), true);
  assert.equal(await js('document.querySelector("[data-decision-default] .provider-decision-summary a").getAttribute("href")'), '/settings/providers/decision/new');
  await snapshot('decision-default-selected');
  await change('[aria-label="Default Decision connection"]', 'google');
  await until(() => getDefaults().decisionDefault === 'google', 'Decision selected from Defaults');
  await until(() => js('document.querySelector("[data-decision-default] .provider-decision-summary").textContent.includes("custom-decision-model")'), 'Decision model summary updated');
  await change('[aria-label="Default Decision connection"]', '');
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'fallback selection confirmation');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Cancel")).click()');
  await until(() => js('!document.querySelector("[role=dialog]")'), 'fallback cancellation');
  assert.equal(getDefaults().decisionDefault, 'google');
  assert.equal(await js(`document.querySelector('[aria-label="Default Decision connection"]').value`), 'google');
  await change('[aria-label="Default Decision connection"]', '');
  await until(() => js('!!document.querySelector(".dialog-footer")'), 'fallback confirmed');
  await js('Array.from(document.querySelectorAll(".dialog-footer button")).find(button => button.textContent.includes("Clear Default")).click()');
  await until(() => js(`!document.querySelector('[role=dialog]') && document.querySelector('[aria-label="Default Decision connection"]').value === ''`), 'fallback selection persisted');
  assert.equal(getDefaults().decisionDefault, '');

  aiConnections.push({ name: 'cf-models', type: 'openai_compatible', catalog_id: 'cloudflare-workers-ai', api_key: 'model-only-key', default_model: 'cloudflare-model', models: [], enabled: true },
    { name: 'custom-models', type: 'openai_compatible', catalog_id: 'google', api_key: 'model-only-key', default_model: 'custom-model', models: [], enabled: true });
  await navigate('/settings/providers/llm/cf-models');
  await until(() => js('!!document.getElementById("provider-editor-tab-decision")'), 'Cloudflare model connection');
  await js('document.getElementById("provider-editor-tab-decision").click()');
  await until(() => js(`document.querySelector('[aria-label="Provider Decision connection"]')?.value === 'cloudflare'`), 'Cloudflare Decision connection');
  assert.deepEqual(await js(`Array.from(document.querySelector('[aria-label="Provider Decision connection"]').options).filter(option => option.value).map(option => option.value)`), ['cloudflare', 'cloudflare-2']);
  await navigate('/settings/providers/llm/custom-models');
  await until(() => js('!!document.getElementById("provider-editor-tab-decision")'), 'custom model connection');
  await js('document.getElementById("provider-editor-tab-decision").click()');
  await until(() => js('document.querySelector("[data-provider-decision]")?.textContent.includes("Decision is available through Google and Cloudflare")'), 'unsupported Decision capability');
  assert.equal(await js('!!document.querySelector("[data-provider-decision] select")'), false);
  setAcceptDialog(true);
}
