START = "/api/v1/onboarding/start"
BODY = {"prospect_name": "Juan Perez", "document_id": "1712345678", "product": "cuenta_ahorros"}


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_start_onboarding(client):
    response = client.post(START, json=BODY)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "APPROVED"
    assert data["decision"]["decision"] == "APPROVED"
    assert len(data["required_documents"]) == 3
    assert "Juan Perez" in data["customer_message"]


def test_start_validates_body(client):
    response = client.post(START, json={"prospect_name": "Juan Perez"})
    assert response.status_code == 422


def test_get_state_includes_audit(client):
    session_id = client.post(START, json=BODY).json()["session_id"]
    state = client.get(f"/api/v1/onboarding/{session_id}").json()
    assert state["session_id"] == session_id
    assert {c["tool"] for c in state["tool_calls"]} >= {"verify_identity", "check_risk_lists"}
    assert state["events"][0]["type"] == "onboarding_received"


def test_unknown_session_returns_404(client):
    assert client.get("/api/v1/onboarding/no-existe").status_code == 404
    response = client.post("/api/v1/onboarding/no-existe/messages", json={"message": "hola"})
    assert response.status_code == 404


def test_conversational_flow(client):
    body = {"prospect_name": "Maria Lopez", "document_id": "0923456789", "product": "cuenta_ahorros"}
    started = client.post(START, json=body).json()
    assert started["status"] == "PENDING_REVIEW"
    assert started["mitigations"][0]["code"] == "BIOMETRIC_REVERIFICATION"

    response = client.post(
        f"/api/v1/onboarding/{started['session_id']}/messages",
        json={"message": "Ya hice la prueba de vida", "liveness_token": "LIVENESS-OK"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"


def test_review_flow(client):
    body = {"prospect_name": "Pedro Gomez", "document_id": "1705555555", "product": "tarjeta_credito"}
    session_id = client.post(START, json=body).json()["session_id"]
    review = {"decision": "reject", "reviewer": "oficial.cumplimiento", "notes": "Coincidencia confirmada"}

    response = client.post(f"/api/v1/onboarding/{session_id}/review", json=review)
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"

    # Una sesión ya resuelta no se puede volver a revisar.
    assert client.post(f"/api/v1/onboarding/{session_id}/review", json=review).status_code == 409
