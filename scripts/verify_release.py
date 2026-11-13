"""Inspect a finished release ZIP without running its application."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import plistlib
import struct
import zipfile


def verify(path):
    path=Path(path);expected_demo=Path(__file__).resolve().parents[1]/'demo'
    with zipfile.ZipFile(path) as archive:
        damaged=archive.testzip()
        if damaged:raise ValueError(f'ZIP integrity error: {damaged}')
        names=archive.namelist()
        for name in names:
            parts=PurePosixPath(name).parts
            if not parts or parts[0]!='namiyomi' or '..' in parts or name.startswith('/'):
                raise ValueError(f'Unexpected archive path: {name}')
            lower=name.lower()
            if any(word in lower for word in ('.ds_store','__macosx','libreadline','libhistory','.recovery.json','.jsonl','.p12','.pfx','.pem')):
                raise ValueError(f'Unexpected private/development component: {name}')
        ab1s={name for name in names if name.lower().endswith('.ab1')}
        expected={f'namiyomi/demo/Demo{n:02}_{direction}.ab1' for n in (1,2) for direction in ('F','R')}
        if ab1s!=expected:raise ValueError('The archive must contain only the four synthetic demo AB1s.')
        for name in expected:
            if archive.read(name)!=(expected_demo/PurePosixPath(name).name).read_bytes():
                raise ValueError(f'Demo bytes do not match the synthetic source: {name}')
        project=json.loads(archive.read('namiyomi/demo/Demo.json'))
        if any(Path(row['path']).is_absolute() or ':' in row['path'] for row in project['reads']):
            raise ValueError('Demo source paths must be relative.')
        for name in ('START_HERE.txt','LICENSE.txt','CITATION.cff','CITATION.txt'):
            archive.getinfo('namiyomi/'+name)
        if 'namiyomi/namiyomi.exe' in names:
            executable=archive.read('namiyomi/namiyomi.exe')
            if executable[:2]!=b'MZ':raise ValueError('Invalid Windows executable.')
            offset=struct.unpack_from('<I',executable,0x3c)[0]
            if executable[offset:offset+4]!=b'PE\0\0' or struct.unpack_from('<H',executable,offset+4)[0]!=0x8664:
                raise ValueError('Expected a Windows x86-64 executable.')
            platform='Windows x64';notices='namiyomi/ThirdPartyNotices/'
            archive.getinfo('namiyomi/_internal/python312.dll')
            if b'Microsoft' not in archive.read(notices+'python-runtime/LICENSE.txt'):
                raise ValueError('The full Windows Python/CRT license is missing.')
        else:
            root='namiyomi/namiyomi.app/Contents/'
            info=plistlib.loads(archive.read(root+'Info.plist'))
            if info.get('CFBundleIdentifier')!='local.yoneoka.namiyomi' or info.get('CFBundleShortVersionString')!='0.2.0':
                raise ValueError('Unexpected macOS application identity/version.')
            executable=archive.read(root+'MacOS/namiyomi')
            if executable[:4]!=b'\xcf\xfa\xed\xfe' or struct.unpack_from('<I',executable,4)[0]!=0x100000c:
                raise ValueError('Expected a macOS arm64 executable.')
            platform='macOS arm64';notices=root+'Resources/ThirdPartyNotices/'
        for notice in ('Python-LICENSE.txt','Tcl-license.terms','Tk-license.terms','TkDND-license.terms',
                       'GCC-COPYING3.txt','GCC-RUNTIME-EXCEPTION.txt','OpenSSL-LICENSE.txt'):
            if not archive.read(notices+'upstream/'+notice):raise ValueError('Empty runtime notice.')
        archive.getinfo(notices+'manifest.json')
    return {'file':path.name,'platform':platform,'bytes':path.stat().st_size,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'status':'passed'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path)
    print(json.dumps(verify(parser.parse_args().archive),indent=2))
