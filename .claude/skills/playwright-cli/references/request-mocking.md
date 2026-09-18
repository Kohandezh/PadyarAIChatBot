# Request Mocking

Intercept, mock, modify and block network requests.

Two contexts, and they use different languages:

- **`playwright-cli`** for exploring by hand. Its `route` and `run-code` take JavaScript,
  because they run in the CLI's driver process.
- **A test in `tests/e2e/`**, which is Python and uses `page.route`.

## In a Python test (this is the one that ships)

Every browser test here fulfils **all** traffic itself, so nothing reaches a network. The
pattern is one `handle` coroutine registered with `page.route`:

```python
async def handle(route, request):
    path = request.url[len(ORIGIN):].split("?")[0] or "/"
    if path == "/":
        return await route.fulfill(status=200, content_type="text/html", body=html)
    if path == "/api/suggestions":
        return await route.fulfill(status=200,
                                   content_type="application/json",
                                   body=json.dumps(SUGGESTIONS_STUB))
    disk = _disk_path(path)
    if disk is not None:
        ctype = mimetypes.guess_type(disk.name)[0] or "application/octet-stream"
        return await route.fulfill(status=200, content_type=ctype,
                                   body=disk.read_bytes())
    return await route.fulfill(status=404, content_type="text/plain", body="")


await page.route(f"{ORIGIN}/**", handle)
```

See `references/page-objects.md` for the surrounding fixture.

### Reading what the page sent

This is how you assert that the app called the right endpoint with the right body,
without a database:

```python
seen = []


async def handle(route, request):
    if request.url.endswith("/chat"):
        seen.append(request.post_data_json)
        return await route.fulfill(status=200,
                                   content_type="application/json",
                                   body=json.dumps({"response": "..."}))
    ...

assert len(seen) == 1
assert seen[0]["message"] == HELD_QUESTION
assert "name" not in seen[0]       # the client must not send identity
```

### Forcing an error path

```python
# 401 from /chat must reopen sign-up, not show an error
return await route.fulfill(status=401, content_type="application/json",
                           body=json.dumps({"detail": "unauthorized"}))

# a dead network
return await route.abort("internetdisconnected")
```

### Stubbing the endpoints this app calls

Common ones a chat page hits: `/chat`, `/api/suggestions`, `/api/auth/session`,
`/api/auth/otp/request`, `/api/auth/otp/verify`, `/api/auth/profile`,
`/api/registration/options`, `/api/transcribe`.

Keep the stub honest. `SUGGESTIONS_STUB` in `tests/e2e/test_chat_localisation.py` carries
titles only, because `/api/suggestions` serves chip labels and nothing else. A stub that
still carried ids and video paths would let the page pass a test against data the real
server no longer sends.

## From the CLI

```bash
playwright-cli route "**/*.jpg" --status=404
playwright-cli route "**/api/suggestions" --body='[{"title":"ساعت کاری"}]' --content-type=application/json
playwright-cli route "**/api/data" --body='{"ok":true}' --header="X-Custom: value"
playwright-cli route "**/*" --remove-header=cookie,authorization
playwright-cli route-list
playwright-cli unroute "**/*.jpg"
playwright-cli unroute
```

### URL patterns

```
**/api/suggestions     Exact path match
**/api/*/details       Wildcard in path
**/*.{png,jpg,jpeg}    Match file extensions
**/search?q=*          Match query parameters
```

### Advanced, via run-code (JavaScript)

Conditional response based on the request body:

```bash
playwright-cli run-code "async page => {
  await page.route('**/chat', route => {
    const body = route.request().postDataJSON();
    if (body.message.includes('ساعت')) {
      route.fulfill({ body: JSON.stringify({ response: 'از ۹ تا ۱۷' }) });
    } else {
      route.fulfill({ status: 401, body: JSON.stringify({ detail: 'unauthorized' }) });
    }
  });
}"
```

Modify a real response:

```bash
playwright-cli run-code "async page => {
  await page.route('**/api/auth/session', async route => {
    const response = await route.fetch();
    const json = await response.json();
    json.signed_in = true;
    await route.fulfill({ response, json });
  });
}"
```

Simulate a network failure:

```bash
playwright-cli run-code "async page => {
  await page.route('**/chat', route => route.abort('internetdisconnected'));
}"
```

Options: `connectionrefused`, `timedout`, `connectionreset`, `internetdisconnected`.

Delay a response, to see the loading state:

```bash
playwright-cli run-code "async page => {
  await page.route('**/chat', async route => {
    await new Promise(r => setTimeout(r, 3000));
    route.fulfill({ body: JSON.stringify({ response: '...' }) });
  });
}"
```

A slow response is worth exercising by hand. The loading bubble is a real part of the UI
(`#loading-bubble`) and several past bugs lived in how it appears and disappears.
