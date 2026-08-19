# Page Object Model (POM)

When e2e tests grow beyond a couple of flows, encapsulate page interactions in Page
Object classes. In Python they're plain classes that take a `Page` and expose locators
and action methods. Put them under `tests/e2e/poms/`.

## Structure

- `BasePage` (`base_page.py`) — shared `goto()` and common locators.
- Feature pages (`login_page.py`, `dataset_page.py`, …) — extend `BasePage` with
  page-specific locators and actions.

## Conventions

### Locators

Define locators in `__init__`. Prefer stable selectors: ids this app exposes
(`#username`, `#password`, `#sec-answer`), `get_by_role(...)`, or `get_by_test_id(...)`.

```python
from playwright.sync_api import Page, Locator


class BasePage:
    BASE = "http://127.0.0.1:8000"

    def __init__(self, page: Page):
        self.page = page

    def goto(self, path: str):
        self.page.goto(f"{self.BASE}{path}")


class LoginPage(BasePage):
    def __init__(self, page: Page):
        super().__init__(page)
        self.username: Locator = page.locator("#username")
        self.password: Locator = page.locator("#password")
        self.sec_answer: Locator = page.locator("#sec-answer")
        self.submit: Locator = page.get_by_role("button", name="ورود به سیستم")

    def open(self):
        self.goto("/secure-panel-inotex/login")

    def login(self, user: str, pw: str, answer: str):
        self.open()
        self.username.fill(user)
        self.password.fill(pw)
        self.sec_answer.fill(answer)
        self.submit.click()
        self.page.wait_for_url("**/secure-panel-inotex**")
```

### Fixtures

Expose page objects as pytest fixtures in `tests/e2e/conftest.py` so tests stay terse:

```python
import pytest
from tests.e2e.poms.login_page import LoginPage


@pytest.fixture
def login_page(page):
    return LoginPage(page)
```

```python
def test_login(login_page):
    login_page.login("admin", "admin", "آبی")
    expect(login_page.page.get_by_text("داشبورد")).to_be_visible()
```

## Creating a new page object

1. Create `tests/e2e/poms/<feature>_page.py`.
2. Extend `BasePage`, define locators in `__init__`, add action methods.
3. Add a fixture in `tests/e2e/conftest.py`.
4. Write tests in `tests/e2e/test_<feature>.py`.

## Discovering selectors

When exploring the UI to build a new page object, run `playwright codegen` against the
running app and copy the locators it generates, or inspect attributes with
`locator.get_attribute(...)` (see [element-attributes.md](element-attributes.md)).
