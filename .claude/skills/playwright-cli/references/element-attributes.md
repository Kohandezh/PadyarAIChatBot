# Inspecting Element Attributes

When you need an element's `id`, `class`, `data-*` attribute, or a computed style that isn't obvious from the page, read it directly off the locator in Python.

## Examples

```python
from playwright.sync_api import Page

def inspect(page: Page):
    el = page.locator("#login-form button[type=submit]")

    # get the element's id
    print(el.get_attribute("id"))

    # get all CSS classes
    print(el.get_attribute("class"))

    # get a specific attribute
    print(el.get_attribute("data-testid"))
    print(el.get_attribute("aria-label"))

    # get a computed style property (runs JS in the page)
    print(el.evaluate("e => getComputedStyle(e).display"))

    # full inner text / value
    print(el.inner_text())
    print(page.locator("#username").input_value())
```

`evaluate("e => ...")` receives the matched element as `e`, so you can read any DOM
property. Use `evaluate_all("els => els.map(e => e.id)")` to read across many matches.
