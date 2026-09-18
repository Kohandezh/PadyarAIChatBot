# Video Recording

Record a browser session as WebM, for a demo, a PR, or proof that a fix works.

This is a `playwright-cli` feature and its scripting api is JavaScript, because the code
runs in the CLI's driver process. It is for showing work, not for tests. Do not add video
recording to anything in `tests/e2e/`.

## Basic recording

```bash
playwright-cli open
playwright-cli video-start demo.webm

playwright-cli video-chapter "چت" --description="Asking the assistant a question" --duration=2000

playwright-cli goto http://127.0.0.1:8000
playwright-cli snapshot
playwright-cli fill "#user-input" "ساعت کاری نمایشگاه چیست؟"
playwright-cli click "#send-btn"

playwright-cli video-stop
```

## Recording a whole scripted run

For anything you will actually show someone, write the script to a file and run it once.
That lets you pace the typing, pause between steps, and annotate.

1. Work the scenario through the CLI first and note every locator you used.
2. Write the script.
3. `playwright-cli run-code --filename=your-script.js`

Overlays are `pointer-events: none`, so a sticky overlay never blocks a click.

```js
async (page) => {
  await page.screencast.start({
    path: "chat-demo.webm",
    size: { width: 1280, height: 800 },
  });
  await page.goto("http://127.0.0.1:8000");

  await page.screencast.showChapter("Asking a question", {
    description: "The visitor types in Persian and the assistant answers.",
    duration: 2000,
  });

  await page.locator("#user-input")
    .pressSequentially("ساعت کاری نمایشگاه چیست؟", { delay: 60 });
  await page.locator("#send-btn").click();
  await page.waitForTimeout(1500);

  await page.screencast.showChapter("Light mode", {
    description: "A per-visitor preference, stored in this browser only.",
    duration: 2000,
  });

  await page.locator("#menu-toggle").click();
  await page.locator("#theme-btn").click();
  await page.waitForTimeout(1500);

  // A sticky annotation that stays while you keep interacting.
  const annotation = await page.screencast.showOverlay(`
    <div style="position: absolute; top: 8px; left: 8px;
      padding: 6px 12px; background: rgba(0,0,0,0.7);
      border-radius: 8px; font-size: 13px; color: white;">
      body.light-mode
    </div>
  `);
  await page.waitForTimeout(1500);
  await annotation.dispose();

  // Highlight a specific element and label it.
  const bounds = await page.locator("#send-btn").boundingBox();
  await page.screencast.showOverlay(
    `<div style="position: absolute;
        top: ${bounds.y}px; left: ${bounds.x}px;
        width: ${bounds.width}px; height: ${bounds.height}px;
        border: 2px solid red;"></div>`,
    { duration: 2000 }
  );

  await page.screencast.stop();
};
```

## Overlay api

| Method | Use |
|---|---|
| `page.screencast.showChapter(title, { description?, duration?, styleSheet? })` | Full-screen chapter card with a blurred backdrop, for section transitions |
| `page.screencast.showOverlay(html, { duration? })` | Custom HTML overlay, for callouts and highlights |
| `disposable.dispose()` | Remove a sticky overlay added without a duration |
| `page.screencast.hideOverlays()` / `showOverlays()` | Hide or show all overlays |

## Notes for this app

- The page is RTL. Put an overlay on the **left** when you want it out of the way of the
  drawer, which sits at the physical right on desktop.
- Chapter titles and descriptions are yours to write. Persian in the chapter card reads
  naturally next to a Persian UI, but keep it short. A card nobody can read in 2 seconds
  is a card nobody reads.
- The video tab plays an avatar clip and can start muted. If the recording is meant to
  show sound working, click the sound control first, because autoplay only allows muted.
- Use `pressSequentially` with a delay for typing. An instant `fill` looks like a glitch.

## Naming and cleanup

```bash
playwright-cli video-start /tmp/recordings/light-mode-2026-09-18.webm
```

Write recordings to `/tmp` or the session scratchpad. Do not commit `.webm` files. Attach
them to a PR or an issue instead.

## Video or trace

| | Video | Trace |
|---|---|---|
| Output | WebM file | Trace file, opened in the Trace Viewer |
| Shows | Visual recording | DOM snapshots, network, console, actions |
| Use | Demos, documentation, PR evidence | Debugging |
| Size | Larger | Smaller |

Recording adds overhead, and large recordings use significant disk space.
