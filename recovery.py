"""Local crash snapshots. Never save over the user's document or source AB1s."""
from __future__ import annotations
import json
import os
import time
import uuid
import sys
from pathlib import Path

from core import Project, atomic_text
from platform_support import application_support_directory, windows_process_alive


class RecoveryStore:
    def __init__(self, directory=None):
        default=application_support_directory()/'Recovery'
        self.directory=Path(directory or os.environ.get('NAMIYOMI_RECOVERY_DIR',default)).resolve()
        self.path=self.directory/f'{uuid.uuid4().hex}.recovery.json'
        self.last_revision=None

    @staticmethod
    def process_alive(pid):
        if type(pid) is not int or pid<=0:return False
        if sys.platform=='win32':return windows_process_alive(pid)
        try:os.kill(pid,0)
        except ProcessLookupError:return False
        except PermissionError:return True
        return True

    def candidates(self):
        """Do not offer snapshots belonging to another running app window."""
        result=[]
        for path in self.directory.glob('*.recovery.json'):
            if path==self.path:continue
            try:
                metadata=json.loads(path.read_text(encoding='utf-8'))['recovery']
                if not self.process_alive(metadata.get('pid')):result.append((metadata.get('timestamp',0),path,metadata))
            except (OSError,ValueError,KeyError,TypeError):continue
        return sorted(result,key=lambda item:item[0],reverse=True)

    def write(self, project, revision):
        if not project.dirty or self.last_revision==revision:return False
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        payload=project.payload(self.directory)
        payload['recovery']=dict(pid=os.getpid(),timestamp=time.time(),
            original_path=str(project.path) if project.path else None)
        # mkstemp in atomic_text produces a private (0600) file.
        atomic_text(self.path,json.dumps(payload,ensure_ascii=False,indent=2))
        self.last_revision=revision
        return True

    def load(self, path):
        path=Path(path)
        metadata=json.loads(path.read_text(encoding='utf-8')).get('recovery')
        if not isinstance(metadata,dict):raise ValueError('This is not a recovery snapshot.')
        project=Project.load(path)  # validates project and immutable AB1 checksums
        original=metadata.get('original_path')
        project.path=Path(original) if original else None
        project.dirty=True
        return project

    def adopt(self, path, project, revision):
        # Write and verify the replacement before removing the orphan snapshot.
        self.last_revision=None
        self.write(project,revision)
        if Path(path).resolve()!=self.path.resolve():Path(path).unlink()

    def clear(self):
        self.path.unlink(missing_ok=True)
        self.last_revision=None
