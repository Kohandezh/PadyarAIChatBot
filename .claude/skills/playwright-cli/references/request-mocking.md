# Request Mocking (`page.route`)

Intercept, mock, modify, and block network requests with `page.route(...)` in Python.
This is useful for e2e tests that should not hit the real OpenAI/GapGPT proxy — you can
stub the chatbot's AI-fallback responses, or simulate failures, without touching the
backend.

> Note: this app's two-tier pipeline calls GapGPT **server-side**, not from the browser,
> so `page.route` only intercepts requests the *browser* makes (e.g. `/chat`, static
> assets, `/theme.css`). To stub the upstream GapGPT call itself, do it in the backend /
> API tests, not here. `page.route` is the right tool for faking the app's own JSON
> endpoints as seen by the page.

## Basic stubs

```python
def test_chat_with_stubbed_response(page):
    # Return a canned JSON body for the chat endpoint
    page.route(
        "**/chat",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"reply": "پاسخ آزمایشی", "video_url": null}',
        ),
    )
    page.goto("http://127.0.0.1:8000/")
    # ... drive the chat UI; it now receives the stubbed reply
```

```python
# Block images to speed up a test
page.route("**/*.{png,jpg,jpeg,webp}", lambda route: route.abort())

# Fail a request to test the UI's error handling
page.route("**/chat", lambda route: route.abort("failed"))
```

## Remove a route

```python
page.unroute("**/chat")
```

## URL patterns

```
**/chat                 - exact path match
**/api/*/details        - wildcard segment
**/*.{png,jpg,jpeg}     - file extensions
**/theme.css            - the dynamic branding CSS endpoint
```

## Conditional response based on the request

`route.request` exposes the incoming request; branch on it:

```python
def handler(route):
    req = route.request
    body = req.post_data_json or {}
    if body.get("message") == "سلام":
        route.fulfill(status=200, content_type="application/json",
                      body='{"reply": "سلام! چطور می‌تونم کمک کنم؟"}')
    else:
        route.fulfill(status=200, content_type="application/json",
                      body='{"reply": "متوجه نشدم"}')

page.route("**/chat", handler)
```

## Modify a real response

Fetch the real response, tweak it, then fulfill:

```python
def handler(route):
    resp = route.fetch()          # let the request hit the server
    data = resp.json()
    data["reply"] = data["reply"] + " (تغییر یافته در تست)"
    route.fulfill(response=resp, json=data)

page.route("**/chat", handler)
```

## Simulate network failures

```python
# error codes: "failed", "timedout", "connectionrefused", "connectionreset", ...
page.route("**/chat", lambda route: route.abort("timedout"))
```

## Delayed response

```python
import time

def slow(route):
    time.sleep(3)
    route.fulfill(status=200, content_type="application/json", body='{"reply": "..."}')

page.route("**/chat", slow)
```
