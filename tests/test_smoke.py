from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, root_path="/docker_demo")


def test_healthz_ok():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_messages_requires_auth():
    response = client.post("/messages", json={"content": "hej"})
    assert response.status_code == 401
