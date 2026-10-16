import threading
from concurrent.futures import ThreadPoolExecutor

from test_api import create_kb, register


def test_concurrent_turn_and_global_admission_are_rejected_before_second_generation(client, monkeypatch):
    from app.services import answering
    entered, resume = threading.Event(), threading.Event()
    original = answering.answer_question
    calls = []
    def blocked(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert resume.wait(10)
        return original(*args, **kwargs)
    owner, other = register(client), register(client, 'other@example.com')
    kb = create_kb(client, owner)
    initial = client.post('/api/chat', headers=owner, json={'question': '初始化', 'knowledge_base_ids': [kb]}).json()
    monkeypatch.setattr(answering, 'answer_question', blocked)
    monkeypatch.setattr(client.app.state.settings, 'global_active_chats', 1)
    body = {'question': '第一条问题', 'conversation_id': initial['conversation_id'], 'request_id': 'concurrent-turn-1'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/chat', headers=owner, json=body)
        try:
            assert entered.wait(5)
            busy = client.post('/api/chat', headers=owner, json=body)
            assert busy.status_code == 409
            global_busy = client.post('/api/chat', headers=other, json={'question': '另一账户问题'})
            assert global_busy.status_code == 429
        finally:
            resume.set()
        assert future.result(timeout=10).status_code == 200
    assert len(calls) == 1
    history = client.get('/api/conversations/' + initial['conversation_id'], headers=owner).json()['messages']
    assert [message['role'] for message in history] == ['user', 'assistant', 'user', 'assistant']
