from test_api import create_kb, register, upload, wait_job


def test_restricted_kb_grants_and_revocation_apply_before_retrieval(client):
    owner, member = register(client), register(client, 'member@example.com')
    org = client.post('/api/organizations', headers=owner, json={'name': '研发组织'}).json()['id']
    assert client.put(f'/api/organizations/{org}/members', headers=owner,
        json={'email': 'member@example.com', 'role': 'editor'}).status_code == 200
    response = client.post('/api/knowledge-bases', headers=owner,
        json={'name': '受限产品资料', 'organization_id': org, 'visibility': 'restricted'})
    kb = response.json()['id']
    result = upload(client, owner, kb, '产品机密额度500元。'.encode())
    wait_job(client, owner, result['job_id'])
    assert client.get('/api/knowledge-bases', headers=member).json() == []
    assert client.post('/api/chat', headers=member,
        json={'question': '产品机密额度', 'knowledge_base_ids': [kb]}).status_code == 404
    assert client.put(f'/api/knowledge-bases/{kb}/members', headers=owner,
        json={'email': 'member@example.com', 'role': 'reader'}).status_code == 200
    assert client.get('/api/knowledge-bases', headers=member).json()[0]['role'] == 'reader'
    assert client.delete('/api/documents/' + result['document']['id'], headers=member).status_code == 403
    answer = client.post('/api/chat', headers=member,
        json={'question': '产品机密额度', 'knowledge_base_ids': [kb]}).json()
    assert '500' in answer['answer']
    member_id = client.get('/api/auth/me', headers=member).json()['id']
    assert client.delete(f'/api/knowledge-bases/{kb}/members/{member_id}', headers=owner).status_code == 204
    # The login remains valid, but cache/history and fresh vector queries lose the shared source.
    assert client.get('/api/auth/me', headers=member).status_code == 200
    assert client.get('/api/knowledge-bases', headers=member).json() == []
    history = client.get('/api/conversations/' + answer['conversation_id'], headers=member).json()
    assert '500' not in history['messages'][1]['content'] and not history['messages'][1]['citations']
    empty = client.post('/api/chat', headers=member, json={'question': '产品机密额度'}).json()
    assert empty['citations'] == []


def test_personal_kb_cannot_be_given_organization_grants(client):
    owner = register(client)
    kb = create_kb(client, owner)
    assert client.put(f'/api/knowledge-bases/{kb}/members', headers=owner,
        json={'email': 'owner@example.com', 'role': 'admin'}).status_code == 422
