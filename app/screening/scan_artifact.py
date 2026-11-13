"""Lossless transfer of an owned, atomically published scan between processes."""
from __future__ import annotations

import errno
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

PROTOCOL = 'niuone-scan-archive-v1'


def child_metadata(env: Mapping[str, str], now: str) -> dict[str, Any]:
    if env.get('NIUONE_SCAN_TRANSPORT') != PROTOCOL or not env.get('DASHBOARD_B1_JOB_ID'):
        return {}
    result = {'job_id': env['DASHBOARD_B1_JOB_ID'], 'running': False, 'error': '',
              'cooldown_remaining_seconds': 0, 'schedule_triggered_at': now,
              'schedule_run_kind': env.get('NIUONE_SCAN_RUN_KIND') or 'manual'}
    if env.get('NIUONE_SCAN_SLOT'):
        result['schedule_slot'] = env['NIUONE_SCAN_SLOT']
    return result


def _identity(stat: os.stat_result) -> tuple[int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class ArchivedScan(dict):
    """Transient identity is kept outside the serialized payload."""
    path: Path
    version: tuple[int, int, int, int]

    def publish(self, target: Path) -> None:
        tmp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
        try:
            try:
                os.link(self.path, tmp)
                if _identity(tmp.stat()) != self.version:
                    raise ValueError('scan archive changed after validation')
            except OSError as exc:
                if exc.errno not in {errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.ENOSYS, errno.EMLINK}:
                    raise
                with self.path.open('rb') as src, tmp.open('xb') as dst:
                    if _identity(os.fstat(src.fileno())) != self.version:
                        raise ValueError('scan archive changed before copy')
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                    if _identity(os.fstat(src.fileno())) != self.version:
                        raise ValueError('scan archive changed during copy')
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)


def write_managed_scan(payload: Mapping[str, Any], archive: Path, targets: tuple[Path, ...]) -> None:
    """Publish all aliases from the exact inode written by this scan."""
    tmp = archive.with_name(f'.{archive.name}.{uuid.uuid4().hex}.tmp')
    try:
        scan = ArchivedScan()
        with tmp.open('x', encoding='utf-8') as out:
            json.dump(payload, out, ensure_ascii=False, separators=(',', ':'))
            out.write('\n')
            out.flush()
            scan.version = _identity(os.fstat(out.fileno()))
        tmp.replace(archive)
        scan.path = archive
        for target in targets:
            scan.publish(target)
    finally:
        tmp.unlink(missing_ok=True)


def load_owned_scan(root: Path, ref: dict, job_id: str, slot: str, kind: str) -> ArchivedScan:
    stamp = str(ref.get('generated_at') or '')
    parsed = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S')
    if stamp != parsed.strftime('%Y-%m-%d %H:%M:%S') or ref.get('job_id') != job_id:
        raise ValueError('invalid scan archive reference')
    path = root / stamp[:10] / (stamp.replace(' ', '_').replace(':', '-') + '.json')
    with path.open('rb') as src:
        version = _identity(os.fstat(src.fileno()))
        payload = json.load(src)
        if _identity(os.fstat(src.fileno())) != version:
            raise ValueError('scan archive changed during read')
    if (not isinstance(payload, dict) or payload.get('job_id') != job_id
            or payload.get('generated_at') != stamp or (payload.get('schedule_slot') or '') != slot
            or payload.get('schedule_run_kind') != kind or payload.get('running') is not False
            or payload.get('error') != '' or not isinstance(payload.get('trade_items'), list)
            or not isinstance(payload.get('items'), list) or not isinstance(payload.get('candidates'), list)
            or payload.get('count') != len(payload['items']) or payload.get('trade_count') != len(payload['trade_items'])):
        raise ValueError('scan archive identity or candidate counts do not match')
    result = ArchivedScan(payload)
    result.path, result.version = path, version
    return result
