from fastapi.testclient import TestClient
from app.main import app
from app.storage import users, documents, refresh_tokens

client = TestClient(app)


def reset_storage():
    users.clear()
    documents.clear()
    refresh_tokens.clear()


def test_register_success():
    reset_storage()

    response = client.post("/auth/register", json={
        "username": "alice",
        "email": "alice@test.com",
        "password": "123456"
    })

    assert response.status_code == 200
    assert response.json()["message"] == "Registered successfully"
    assert "alice" in users
    assert "id" in users["alice"]
    assert users["alice"]["username"] == "alice"
    assert users["alice"]["email"] == "alice@test.com"
    assert users["alice"]["password"] != "123456"


def test_register_duplicate_username():
    reset_storage()

    client.post("/auth/register", json={
        "username": "alice",
        "email": "alice@test.com",
        "password": "123456"
    })

    response = client.post("/auth/register", json={
        "username": "alice",
        "email": "alice2@test.com",
        "password": "abcdef"
    })

    assert response.status_code == 400
    assert response.json()["detail"] == "Username already exists"


def test_login_success():
    reset_storage()

    client.post("/auth/register", json={
        "username": "alice",
        "email": "alice@test.com",
        "password": "123456"
    })

    response = client.post("/auth/login", data={
        "username": "alice",
        "password": "123456"
    })

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


def test_login_wrong_password():
    reset_storage()

    client.post("/auth/register", json={
        "username": "alice",
        "email": "alice@test.com",
        "password": "123456"
    })

    response = client.post("/auth/login", data={
        "username": "alice",
        "password": "wrongpassword"
    })

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_refresh_success():
    reset_storage()

    client.post("/auth/register", json={
        "username": "alice",
        "email": "alice@test.com",
        "password": "123456"
    })

    login_response = client.post("/auth/login", data={
        "username": "alice",
        "password": "123456"
    })

    refresh_token = login_response.json()["refresh_token"]

    refresh_response = client.post("/auth/refresh", json={
        "refresh_token": refresh_token
    })

    assert refresh_response.status_code == 200
    assert "access_token" in refresh_response.json()