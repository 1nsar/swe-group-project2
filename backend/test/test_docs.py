from fastapi.testclient import TestClient
from app.main import app
from app.storage import users, documents, refresh_tokens

client = TestClient(app)


def reset_storage():
    users.clear()
    documents.clear()
    refresh_tokens.clear()


def register_user(username, email, password):
    return client.post("/auth/register", json={
        "username": username,
        "email": email,
        "password": password
    })


def login_user(username, password):
    return client.post("/auth/login", data={
        "username": username,
        "password": password
    })


def auth_headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}


def test_create_and_list_document():
    reset_storage()

    register_user("alice", "alice@test.com", "123456")
    login_response = login_user("alice", "123456")
    access_token = login_response.json()["access_token"]

    create_response = client.post(
        "/documents",
        json={"title": "Doc 1", "content": "Hello"},
        headers=auth_headers(access_token)
    )

    assert create_response.status_code == 200
    created_doc = create_response.json()
    assert created_doc["title"] == "Doc 1"
    assert created_doc["content"] == "Hello"
    assert created_doc["owner"] == "alice"

    list_response = client.get(
        "/documents",
        headers=auth_headers(access_token)
    )

    assert list_response.status_code == 200
    docs = list_response.json()
    assert len(docs) == 1
    assert docs[0]["title"] == "Doc 1"


def test_update_document_and_version_history():
    reset_storage()

    register_user("alice", "alice@test.com", "123456")
    login_response = login_user("alice", "123456")
    access_token = login_response.json()["access_token"]

    create_response = client.post(
        "/documents",
        json={"title": "Doc 1", "content": "Old content"},
        headers=auth_headers(access_token)
    )
    doc_id = create_response.json()["id"]

    update_response = client.put(
        f"/documents/{doc_id}",
        json={"content": "New content"},
        headers=auth_headers(access_token)
    )

    assert update_response.status_code == 200
    updated_doc = update_response.json()
    assert updated_doc["content"] == "New content"
    assert len(updated_doc["versions"]) == 1
    assert updated_doc["versions"][0]["content"] == "Old content"


def test_restore_document_version():
    reset_storage()

    register_user("alice", "alice@test.com", "123456")
    login_response = login_user("alice", "123456")
    access_token = login_response.json()["access_token"]

    create_response = client.post(
        "/documents",
        json={"title": "Doc 1", "content": "Version 1"},
        headers=auth_headers(access_token)
    )
    doc_id = create_response.json()["id"]

    client.put(
        f"/documents/{doc_id}",
        json={"content": "Version 2"},
        headers=auth_headers(access_token)
    )

    restore_response = client.post(
        f"/documents/{doc_id}/restore/0",
        headers=auth_headers(access_token)
    )

    assert restore_response.status_code == 200
    restored_doc = restore_response.json()
    assert restored_doc["content"] == "Version 1"


def test_viewer_can_read_but_cannot_edit():
    reset_storage()

    register_user("alice", "alice@test.com", "123456")
    register_user("bob", "bob@test.com", "123456")

    alice_login = login_user("alice", "123456")
    bob_login = login_user("bob", "123456")

    alice_token = alice_login.json()["access_token"]
    bob_token = bob_login.json()["access_token"]

    create_response = client.post(
        "/documents",
        json={"title": "Shared Doc", "content": "Read only text"},
        headers=auth_headers(alice_token)
    )
    doc_id = create_response.json()["id"]

    share_response = client.post(
        f"/documents/{doc_id}/share",
        json={"username": "bob", "role": "viewer"},
        headers=auth_headers(alice_token)
    )

    assert share_response.status_code == 200

    read_response = client.get(
        f"/documents/{doc_id}",
        headers=auth_headers(bob_token)
    )
    assert read_response.status_code == 200

    edit_response = client.put(
        f"/documents/{doc_id}",
        json={"content": "Bob tries to edit"},
        headers=auth_headers(bob_token)
    )

    assert edit_response.status_code == 403
    assert edit_response.json()["detail"] == "No edit permission"