"""A visitor roster entry without a name is indistinguishable from the next one.

The form on the leads page is an input and a button, not a <form>, so nothing
in HTML stops an empty submit — an operator tapping «افزودن» twice used to
mint a real personal link for «بی‌نام». The client guards it now, but the
client is not the contract: the endpoint must refuse the name it cannot
display.
"""
import pytest


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name_is_refused(client, name):
    res = client.post("/admin/api/leads/visitors", json={"name": name})
    assert res.status_code == 400, res.text


def test_real_name_still_creates_the_link(client):
    res = client.post("/admin/api/leads/visitors", json={"name": "سینا"})
    assert res.status_code == 200, res.text
    body = res.json()
    # What the operator hands over at the booth: a link and its QR.
    assert body["link"].startswith("http")
    assert "<svg" in body["qr"]
