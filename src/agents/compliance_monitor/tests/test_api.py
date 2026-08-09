"""HTTP wiring + auth for the compliance routers."""


def test_precheck_requires_api_key(client):
    resp = client.post("/api/v1/precheck", json={"channel": "sms", "contact_id": "lead_1"})
    assert resp.status_code == 401


def test_precheck_roundtrip(client, api_headers):
    client.put("/api/v1/consent", headers=api_headers,
               json={"lead_id": "lead_1", "channel": "sms", "opted_in": True})
    resp = client.post("/api/v1/precheck", headers=api_headers,
                       json={"channel": "sms", "contact_id": "lead_1"})
    assert resp.status_code == 200 and resp.json()["decision"] == "allow"


def test_fairness_endpoint(client, api_headers):
    resp = client.post("/api/v1/fairness/evaluate", headers=api_headers, json={
        "sub_agent": "lead_assignment", "cohort_dimension": "cohort",
        "cohorts": {"a": {"selected": 42, "total": 100}, "b": {"selected": 31, "total": 100}}})
    assert resp.status_code == 200 and resp.json()["passes"] is False


def test_rule_workflow_over_http(client, api_headers):
    proposed = client.post("/api/v1/rules/changes", headers=api_headers, json={
        "rule_id": "r1", "domain": "marketing", "definition": {"x": 1},
        "change_reason": "test", "changed_by": "alice"}).json()
    # Proposer approving own change → 409.
    same = client.post(f"/api/v1/rules/changes/{proposed['change_id']}/approve",
                       headers=api_headers, json={"approved_by": "alice"})
    assert same.status_code == 409
    client.post(f"/api/v1/rules/changes/{proposed['change_id']}/approve",
                headers=api_headers, json={"approved_by": "bob"})
    deployed = client.post(f"/api/v1/rules/changes/{proposed['change_id']}/deploy",
                           headers=api_headers)
    assert deployed.status_code == 200
    assert deployed.json()["production"]["stage"] == "production"


def test_dashboard_and_verify(client):
    assert client.get("/api/v1/dashboard").json()["overall"] == "green"
    assert client.get("/api/v1/audit/verify").json()["verified"] is True


def test_case_status_is_validated(client, api_headers):
    client.post("/api/v1/activity", headers=api_headers, json={
        "event_type": "decision", "actor_type": "engine", "engine": "lead-scoring",
        "entity_id": "l1"})
    cases = client.get("/api/v1/cases", headers=api_headers).json()["cases"]
    assert cases
    case_id = cases[0]["case_id"]
    bad = client.patch(f"/api/v1/cases/{case_id}", headers=api_headers,
                       json={"status": "invalid"})
    assert bad.status_code == 422
    ok = client.patch(f"/api/v1/cases/{case_id}", headers=api_headers,
                      json={"status": "investigating"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "investigating"
