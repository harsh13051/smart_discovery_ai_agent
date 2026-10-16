import dataclasses
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from discovery.api import create_app
from discovery.llm import AnthropicClient
from discovery.providers.cache import MemoryCache
from discovery.providers.osm import OSMProvider
from discovery.settings import AppSettings
from helpers import NOMINATIM, OVERPASS_URL, OVERPASS_URL_2, FakeAnthropic, FakeOSM

PASSWORD = "dinner2026x"


@pytest.fixture
def fake_osm():
    return FakeOSM()


@pytest.fixture
def web_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", osm_contact="tests@example.com",
                       overpass_urls=(OVERPASS_URL, OVERPASS_URL_2), nominatim_url=NOMINATIM, static_dir=tmp_path / "none")


@pytest.fixture
def osm_provider(web_settings, fake_osm):
    client = httpx.Client(transport=httpx.MockTransport(fake_osm.handler))
    return OSMProvider(web_settings, MemoryCache(), client=client, sleep=lambda s: None, attempts=2)


@pytest.fixture
def app(web_settings, osm_provider):
    return create_app(web_settings, osm=osm_provider)


def new_client(app) -> TestClient:
    c = TestClient(app)
    token = c.get("/api/auth/csrf").json()["csrf_token"]
    c.headers["X-CSRF-Token"] = token
    return c


@pytest.fixture
def client(app):
    return new_client(app)


def register(c: TestClient, email="asha@example.com", name="Asha", password=PASSWORD):
    r = c.post("/api/auth/register", json={"name": name, "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["user"]


@pytest.fixture
def user_client(client):
    register(client)
    return client


@pytest.fixture
def second_client(app):
    c = new_client(app)
    register(c, email="ben@example.com", name="Ben")
    return c


def llm_client(script):
    fake = FakeAnthropic(script)
    client = AnthropicClient("sk-test", "claude-test", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    return client, fake


@pytest.fixture
def app_with_llm(web_settings, osm_provider):
    def make(script):
        client, fake = llm_client(script)
        return create_app(dataclasses.replace(web_settings, anthropic_api_key="sk-test", llm_model="claude-test"),
                          osm=osm_provider, llm_client=client), fake
    return make
