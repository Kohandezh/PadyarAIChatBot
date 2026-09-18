# Running Custom Playwright Code from the CLI

`run-code` executes arbitrary Playwright code for scenarios the plain CLI commands do not
cover.

**It takes JavaScript.** The code runs inside `playwright-cli`'s own driver process, so
that is the language of this command, not a sign that the repo uses node. Your tests stay
Python. Each section below gives the Python equivalent where you would need one in a
test.

## Syntax

```bash
playwright-cli run-code "async page => {
  // Playwright code here. page.context() reaches the browser context.
}"
```

Or from a file:

```bash
playwright-cli run-code --filename=./my-script.js
```

The code must be a single function expression. It is wrapped in `(...)` and evaluated.
`import`, `export` and `require` are not supported.

## Colour scheme, for light/dark work

```bash
playwright-cli run-code "async page => { await page.emulateMedia({ colorScheme: 'light' }); }"
playwright-cli run-code "async page => { await page.emulateMedia({ colorScheme: 'dark' }); }"
playwright-cli run-code "async page => { await page.emulateMedia({ reducedMotion: 'reduce' }); }"
playwright-cli run-code "async page => { await page.emulateMedia({ media: 'print' }); }"
```

This matters here. The chat boot script in `themes/base/partials/index.html` reads
`prefers-color-scheme` when nothing is stored in `localStorage`. See the `dark-mode`
skill.

Python:

```python
await page.emulate_media(color_scheme="light")
# or set it when the context is created, before the first paint:
context = await browser.new_context(color_scheme="light")
```

The context form is the one you usually want, because the boot script runs on first paint
and an emulate call after `goto` is too late.

## Viewport and device

```bash
playwright-cli resize 390 844          # plain CLI command, no run-code needed
```

Python:

```python
context = await browser.new_context(viewport={"width": 390, "height": 844})
```

Check narrow width. The drawer stops being an overlay at 992px and becomes a sidebar
(`static/chat/base.css`), so the layout is genuinely different above and below it.

## Permissions

The chat has a microphone button (`#mic-btn`) that posts to `/api/transcribe`.

```bash
playwright-cli run-code "async page => {
  await page.context().grantPermissions(['microphone']);
}"
```

Python:

```python
context = await browser.new_context(permissions=["microphone"])
# or later:
await context.grant_permissions(["microphone"])
```

## Wait strategies

```bash
playwright-cli run-code "async page => { await page.waitForLoadState('networkidle'); }"
playwright-cli run-code "async page => { await page.locator('#loading-bubble').waitFor({ state: 'hidden' }); }"
playwright-cli run-code "async page => { await page.waitForFunction(() => typeof initChat === 'function'); }"
```

Python:

```python
await page.wait_for_load_state("networkidle")
await page.locator("#loading-bubble").wait_for(state="hidden")
await page.wait_for_function("typeof renderOptions === 'function'")
```

Wait on a condition, never on a sleep.

## Page information

```bash
playwright-cli run-code "async page => { return await page.title(); }"
playwright-cli run-code "async page => { return page.url(); }"
playwright-cli run-code "async page => { return await page.content(); }"
playwright-cli run-code "async page => { return page.viewportSize(); }"
```

Python: `await page.title()`, `page.url`, `await page.content()`, `page.viewport_size`.

## Reading state out of the page

```bash
playwright-cli run-code "async page => {
  return await page.evaluate(() => ({
    lang: document.documentElement.lang,
    dir: document.documentElement.dir,
    bodyClass: document.body.className,
    bubbles: document.querySelectorAll('.message.bot .bubble').length,
    light: localStorage.getItem('inotex-light-mode'),
  }));
}"
```

Python: the same string, through `await page.evaluate(...)`.

## Frames and downloads

```bash
playwright-cli run-code "async page => {
  const frame = page.locator('iframe#my-iframe').contentFrame();
  await frame.locator('button').click();
}"

playwright-cli run-code "async page => {
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download' }).click();
  const download = await downloadPromise;
  await download.saveAs('./downloaded-file.csv');
  return download.suggestedFilename();
}"
```

Downloads are worth exercising in the admin panel. Several admin screens export CSV or a
database dump.

Python:

```python
async with page.expect_download() as info:
    await page.click("#export-btn")
download = await info.value
await download.save_as("/tmp/export.csv")
```

## Error handling

```bash
playwright-cli run-code "async page => {
  try {
    await page.getByRole('button', { name: 'ارسال' }).click({ timeout: 1000 });
    return 'clicked';
  } catch (e) {
    return 'element not found';
  }
}"
```

## A full flow: admin login

```bash
playwright-cli run-code "async page => {
  await page.goto('http://127.0.0.1:8000/secure-panel-admin/login');
  await page.locator('#username').fill('admin');
  await page.locator('#password').fill('...');
  await page.locator('#sec-answer').fill('...');   // the form's third field
  await page.getByRole('button', { name: 'ورود به سیستم' }).click();
  await page.waitForURL('**/secure-panel-admin');
  await page.context().storageState({ path: 'admin-auth.json' });
  return 'ok';
}"
```

Never put a real password in a committed file or a saved storage-state file. See
`references/storage-state.md`.
