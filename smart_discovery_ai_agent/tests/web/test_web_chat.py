from conftest import PASSWORD, new_client, register


def make_conv(c):
    r = c.post("/api/conversations")
    assert r.status_code == 201
    return r.json()["id"]


def send(c, cid, text, **extra):
    return c.post(f"/api/conversations/{cid}/messages", json={"content": text, **extra})


def test_create_conversation_unique_ids_and_defaults(user_client):
    a, b = make_conv(user_client), make_conv(user_client)
    assert a != b
    items = user_client.get("/api/conversations").json()["items"]
    assert {i["id"] for i in items} == {a, b} and all(i["title"] == "New chat" for i in items)


def test_send_message_saves_user_and_real_ai_response_with_cards(user_client):
    cid = make_conv(user_client)
    r = send(user_client, cid, "Find vegetarian restaurants in Baner, Pune")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_message"]["role"] == "user" and body["assistant_message"]["role"] == "assistant"
    payload = body["assistant_message"]["payload"]
    names = [c["name"] for c in payload["cards"]]
    assert "Green Leaf" in names and "Meat Lovers Grill" not in names  # vegetarian conflict removed
    assert payload["mode"] == "rule-based" and "No LLM API key" in payload["fallback_reason"]
    assert payload["source"]["provider"] == "OpenStreetMap"
    msgs = user_client.get(f"/api/conversations/{cid}/messages").json()["items"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["payload"]["cards"][0]["id"].startswith("osm:")  # cards restored from the database


def test_title_generated_from_first_message_only(user_client):
    cid = make_conv(user_client)
    send(user_client, cid, "Find a great cafe in Baner, Pune with wifi please and some more words to make it very long indeed")
    title = user_client.get(f"/api/conversations/{cid}").json()["title"]
    assert title.startswith("Find a great cafe") and title.endswith("…") and len(title) <= 58
    send(user_client, cid, "cheaper")
    assert user_client.get(f"/api/conversations/{cid}").json()["title"] == title


def test_second_conversation_keeps_first_visible_and_ordered_by_update(user_client):
    first = make_conv(user_client)
    send(user_client, first, "Cafe in Baner, Pune")
    second = make_conv(user_client)
    send(user_client, second, "Chinese in Baner, Pune")
    ids = [i["id"] for i in user_client.get("/api/conversations").json()["items"]]
    assert ids == [second, first]
    send(user_client, first, "with wifi")  # bumping the older one moves it to the top
    assert [i["id"] for i in user_client.get("/api/conversations").json()["items"]] == [first, second]


def test_switching_conversations_loads_each_ones_own_messages_in_order(user_client):
    a, b = make_conv(user_client), make_conv(user_client)
    send(user_client, a, "Cafe in Baner, Pune")
    send(user_client, b, "Chinese in Baner, Pune")
    send(user_client, a, "open now")
    msgs_a = user_client.get(f"/api/conversations/{a}/messages").json()["items"]
    msgs_b = user_client.get(f"/api/conversations/{b}/messages").json()["items"]
    assert [m["content"] for m in msgs_a if m["role"] == "user"] == ["Cafe in Baner, Pune", "open now"]
    assert [m["content"] for m in msgs_b if m["role"] == "user"] == ["Chinese in Baner, Pune"]
    assert [m["id"] for m in msgs_a] == sorted(m["id"] for m in msgs_a)


def test_history_survives_logout_login_and_new_browser_session(user_client, app):
    cid = make_conv(user_client)
    send(user_client, cid, "Cafe in Baner, Pune")
    user_client.post("/api/auth/logout")
    assert user_client.get("/api/conversations").status_code == 401
    fresh = new_client(app)  # brand new browser
    assert fresh.post("/api/auth/login", json={"email": "asha@example.com", "password": PASSWORD}).status_code == 200
    assert [i["id"] for i in fresh.get("/api/conversations").json()["items"]] == [cid]
    assert len(fresh.get(f"/api/conversations/{cid}/messages").json()["items"]) == 2


def test_history_survives_app_restart(web_settings, osm_provider):
    from discovery.api import create_app
    app1 = create_app(web_settings, osm=osm_provider)
    c1 = new_client(app1)
    register(c1)
    cid = make_conv(c1)
    send(c1, cid, "Cafe in Baner, Pune")
    app2 = create_app(web_settings, osm=osm_provider)  # "server restart": same database file
    c2 = new_client(app2)
    c2.post("/api/auth/login", json={"email": "asha@example.com", "password": PASSWORD})
    assert len(c2.get(f"/api/conversations/{cid}/messages").json()["items"]) == 2


def test_delete_affects_only_selected_conversation(user_client, app):
    from sqlalchemy import func, select
    from discovery.db import Message
    a, b = make_conv(user_client), make_conv(user_client)
    send(user_client, a, "Cafe in Baner, Pune")
    send(user_client, b, "Chinese in Baner, Pune")
    assert user_client.delete(f"/api/conversations/{a}").status_code == 200
    assert user_client.get(f"/api/conversations/{a}").status_code == 404
    assert len(user_client.get(f"/api/conversations/{b}/messages").json()["items"]) == 2
    with app.state.sf() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == a)) == 0
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == b)) == 2


def test_rename_validation(user_client):
    cid = make_conv(user_client)
    assert user_client.patch(f"/api/conversations/{cid}", json={"title": "Dinner ideas"}).json()["title"] == "Dinner ideas"
    assert user_client.patch(f"/api/conversations/{cid}", json={"title": "   "}).status_code == 422
    assert user_client.patch(f"/api/conversations/{cid}", json={"title": "x" * 500}).status_code == 422


def test_cross_user_access_is_blocked_on_every_endpoint(user_client, second_client):
    cid = make_conv(user_client)
    send(user_client, cid, "Cafe in Baner, Pune")
    assert second_client.get(f"/api/conversations/{cid}").status_code == 404
    assert second_client.get(f"/api/conversations/{cid}/messages").status_code == 404
    assert send(second_client, cid, "hello").status_code == 404
    assert second_client.patch(f"/api/conversations/{cid}", json={"title": "pwned"}).status_code == 404
    assert second_client.delete(f"/api/conversations/{cid}").status_code == 404
    assert second_client.get("/api/conversations").json()["items"] == []
    assert second_client.get("/api/conversations", params={"q": "Baner"}).json()["items"] == []
    # the owner's data is untouched
    assert user_client.get(f"/api/conversations/{cid}").json()["title"] != "pwned"
    assert len(user_client.get(f"/api/conversations/{cid}/messages").json()["items"]) == 2
    assert second_client.get("/api/conversations/not-a-real-id").status_code == 404


def test_search_conversations_by_title_and_message_content(user_client):
    a, b = make_conv(user_client), make_conv(user_client)
    send(user_client, a, "Cafe in Baner, Pune")
    send(user_client, b, "Chinese in Baner, Pune")
    assert [i["id"] for i in user_client.get("/api/conversations", params={"q": "Chinese"}).json()["items"]] == [b]
    assert user_client.get("/api/conversations", params={"q": "zzzz"}).json()["items"] == []
    assert len(user_client.get("/api/conversations", params={"q": "%"}).json()["items"]) == 0  # wildcard is escaped


def test_message_pagination_and_conversation_list_pagination(user_client):
    cid = make_conv(user_client)
    for i in range(4):
        send(user_client, cid, f"Cafe in Baner, Pune {i}")
    page1 = user_client.get(f"/api/conversations/{cid}/messages", params={"limit": 3}).json()
    assert len(page1["items"]) == 3 and page1["has_more"] is True
    page2 = user_client.get(f"/api/conversations/{cid}/messages", params={"limit": 3, "before_id": page1["items"][0]["id"]}).json()
    assert len(page2["items"]) == 3 and page2["items"][-1]["id"] < page1["items"][0]["id"]
    for _ in range(4):
        make_conv(user_client)
    lst = user_client.get("/api/conversations", params={"limit": 2}).json()
    assert len(lst["items"]) == 2 and lst["has_more"] is True


def test_message_validation(user_client):
    cid = make_conv(user_client)
    assert send(user_client, cid, "").status_code == 422
    assert send(user_client, cid, "x" * 501).status_code == 422
    assert send(user_client, cid, "hi", lat=999, lon=0).status_code == 422
    assert user_client.get(f"/api/conversations/{cid}/messages").json()["items"] == []  # nothing saved for rejects


def test_clarifying_question_then_answer_uses_conversation_context(user_client):
    cid = make_conv(user_client)
    first = send(user_client, cid, "Find vegetarian restaurants for 2 under 1500").json()["assistant_message"]
    assert first["payload"]["status"] == "needs_clarification" and "Which area" in first["content"]
    second = send(user_client, cid, "Baner").json()["assistant_message"]
    assert second["payload"]["status"] == "ok"
    prefs = second["payload"]["preferences"]
    assert prefs["diet"] == "vegetarian" and prefs["budget_amount"] == 1500 and prefs["location_text"].startswith("Baner")


def test_provider_outage_is_reported_honestly_and_saved(user_client, fake_osm):
    fake_osm.overpass = "500"
    fake_osm.nominatim = "500"
    cid = make_conv(user_client)
    r = send(user_client, cid, "Cafe in Baner, Pune").json()["assistant_message"]
    assert r["payload"]["status"] == "provider_error" and r["payload"]["cards"] == []
    assert "unavailable" in r["content"].lower()
    assert len(user_client.get(f"/api/conversations/{cid}/messages").json()["items"]) == 2
