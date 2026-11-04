"""Collect dependency notices into a freshly built app, before signing/archiving."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys


def collect(app):
    app=Path(app).resolve()
    if app.suffix=='.app' and (app/'Contents/Frameworks').is_dir():
        runtime=app/'Contents/Frameworks';output=app/'Contents/Resources/ThirdPartyNotices'
    elif (app/'namiyomi.exe').is_file() and (app/'_internal').is_dir():
        runtime=app/'_internal';output=app/'ThirdPartyNotices'
    else:raise ValueError('Expected a built macOS .app or Windows PyInstaller application folder.')
    output.mkdir(parents=True,exist_ok=True)
    manifest=[]
    if (app/'namiyomi.exe').is_file():
        # Windows Python appends binary-runtime redistribution terms (including
        # the Microsoft CRT) to its installed LICENSE. The source LICENSE alone
        # does not contain those conditions.
        installed_license=Path(sys.base_prefix)/'LICENSE.txt'
        if not installed_license.is_file() or 'Microsoft' not in installed_license.read_text(encoding='utf-8'):
            raise ValueError('The full Windows Python runtime LICENSE, including CRT terms, is required.')
        target=output/'python-runtime'/'LICENSE.txt';target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(installed_license,target)
        manifest.append({'package':'python-windows-runtime','version':sys.version.split()[0],
                         'notices':['python-runtime/LICENSE.txt']})
    for name in ('biopython','numpy','tkinterdnd2','pyinstaller','PyYAML'):
        try:dist=metadata.distribution(name)
        except metadata.PackageNotFoundError:continue
        copied=[]
        for entry in dist.files or []:
            if not any(word in str(entry).lower() for word in ('license','copying','notice')):continue
            source=Path(dist.locate_file(entry))
            if not source.is_file():continue
            # Distribution metadata may contain ../ prefixes. Never copy outside
            # this newly built notice directory, even when a wheel lists them.
            relative=Path(*[part for part in Path(str(entry)).parts if part not in ('.','..','/','\\') and ':' not in part])
            target=output/name/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            copied.append(str(target.relative_to(output)))
        manifest.append({'package':name,'version':dist.version,'notices':copied})
    prefix=Path(sys.prefix)
    package_cache=prefix.parents[1]/'pkgs'
    bundled={p.name for p in runtime.rglob('*') if p.suffix.lower() in ('.so','.dylib','.dll','.pyd')}
    for row in sorted((prefix/'conda-meta').glob('*.json')):
        data=json.loads(row.read_text());name=data['name']
        if name in ('biopython','numpy'):continue  # Installed Python metadata above is authoritative.
        if not any(Path(f).name in bundled for f in data.get('files',[])):continue
        package_info=package_cache/f'{name}-{data["version"]}-{data["build"]}'/'info'
        source=package_info/'licenses'
        target=output/f'conda-{name}'
        if source.is_dir():
            shutil.copytree(source,target,dirs_exist_ok=True,copy_function=shutil.copyfile)
        else:
            for notice in (package_info/'recipe').rglob('*'):
                if notice.is_file() and any(word in notice.name.lower() for word in ('license','copying','exception','notice')):
                    destination=target/notice.relative_to(package_info/'recipe')
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(notice,destination)
        if not target.exists():continue
        manifest.append({'package':name,'version':data['version'],'license':data.get('license',''),
                         'notices':str(target.relative_to(output))})
    # Wheel metadata does not carry the Python/Tcl runtime's own license. Keep
    # upstream terms, plus GCC runtime exception terms for Conda/NumPy runtimes.
    upstream=Path(__file__).resolve().parent/'third_party_licenses'
    required=('Python-LICENSE.txt','Tcl-license.terms','Tk-license.terms','TkDND-license.terms',
              'GCC-COPYING3.txt','GCC-RUNTIME-EXCEPTION.txt','OpenSSL-LICENSE.txt')
    for name in required:
        source=upstream/name
        if not source.is_file():raise ValueError(f'Missing upstream runtime notice: {name}')
        destination=output/'upstream'/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
    manifest.append({'package':'upstream-runtime-terms','notices':[f'upstream/{name}' for name in required]})
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'Collected notices for {len(manifest)} bundled packages: {output}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('app');args=parser.parse_args()
    collect(args.app)
