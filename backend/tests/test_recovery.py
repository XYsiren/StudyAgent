from pathlib import Path

import pytest
from test_api import create_kb, register, upload, wait_job


def test_coordinated_backup_restore_and_overwrite_guards(client, tmp_path):
    from app.db import Database
    from app.maintenance import backup, restore, verify_backup
    from app.models import Document, User
    from sqlalchemy import select
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '报销额度500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    source = client.app.state.settings
    if source.database_url.startswith('postgresql'):
        pytest.skip('PostgreSQL full backup/restore is exercised by scripts/validate-infrastructure.py')
    client.app.state.worker_stop.set()
    client.app.state.worker_thread.join(timeout=10)
    # Quiesce the local fixture's worker and close its single-process vector client.
    from app.services import retrieval
    if retrieval.client.cache_info().currsize:
        retrieval.client().close()
        retrieval.client.cache_clear()
    folder = tmp_path / 'coordinated-backup'
    with pytest.raises(ValueError, match='Stop ALL'):
        backup(source, folder)
    report = backup(source, folder, quiesced=True)
    assert report['files'] > 1 and verify_backup(folder)['files']
    target_root = tmp_path / 'restored'
    target = source.model_copy(update={'data_dir': target_root,
        'database_url': 'sqlite:///' + (target_root / 'restored.db').as_posix()})
    result = restore(target, folder, quiesced=True)
    assert result['restored']
    database = Database(target)
    with database.session() as db:
        doc = db.get(Document, uploaded['document']['id'])
        assert Path(doc.stored_path).is_relative_to(target_root) and '500' in Path(doc.stored_path).read_text(encoding='utf-8')
        assert db.scalar(select(User)).email == 'owner@example.com'
    database.engine.dispose()
    with pytest.raises(ValueError, match='must be empty'):
        restore(target, folder, quiesced=True)
    upload_path = next((folder / 'uploads').iterdir())
    upload_path.write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='integrity'):
        verify_backup(folder)
