"""Create verifiable portable/source archives without including local work files."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pollen import __version__

MANIFEST = '文件清单.json'


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_directory(folder):
    manifest = json.loads((folder/MANIFEST).read_text(encoding='utf-8'))
    expected = {entry['path']:entry for entry in manifest['files']}
    actual = {p.relative_to(folder).as_posix():p for p in folder.rglob('*') if p.is_file() and p.name!=MANIFEST}
    if set(expected)!=set(actual):
        raise ValueError('文件清单不匹配：存在缺失或多余文件')
    for name,path in actual.items():
        if path.stat().st_size!=expected[name]['bytes'] or file_hash(path)!=expected[name]['sha256']:
            raise ValueError('文件校验失败：'+name)
    if manifest['version']!=__version__:
        raise ValueError('版本不匹配')
    print(f'完整性检查通过：{len(actual)} 个文件，版本 {manifest["version"]}')


def write_zip(target,entries):
    with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.zip',delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path,name in entries:
                archive.write(path,name)
        with zipfile.ZipFile(temporary) as archive:
            failed = archive.testzip()
            if failed:
                raise ValueError('压缩文件校验失败：'+failed)
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='正式版便携包、源码包与文件完整性检查')
    parser.add_argument('--extract-check',type=Path,help='将发布包解压到新的空文件夹并核对文件哈希')
    parser.add_argument('--verify-extracted',type=Path,help='仅核对已经解压的软件文件夹')
    args = parser.parse_args()
    release_root = ROOT/'dist'/f'v{__version__}'
    app = release_root/'PollenStudio'
    portable = release_root/f'PollenStudio-{__version__}-Windows-x64.zip'
    if args.verify_extracted:
        verify_directory(args.verify_extracted.resolve())
        return
    if args.extract_check:
        target = args.extract_check.resolve()
        if target.exists() and any(target.iterdir()):
            raise ValueError('解压验收目标须为新的空文件夹，不覆盖已有内容')
        target.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(portable) as archive:
            for info in archive.infolist():
                destination = (target/info.filename).resolve()
                if not destination.is_relative_to(target):
                    raise ValueError('压缩包存在越界路径')
            archive.extractall(target)
        verify_directory(target/'PollenStudio')
        print('解压验收目录：'+str(target/'PollenStudio'))
        return
    required = ['PollenStudio.exe','_internal','欢迎使用.html','模板/孢粉计数模板.xlsx','模板/孢粉百分比模板.xlsx',
                '使用指南/快速入门.html','使用指南/使用说明.html','使用指南/版本说明.html']
    for name in required:
        if not (app/name).exists():
            raise FileNotFoundError('正式发布内容缺失：'+name)
    files = sorted(p for p in app.rglob('*') if p.is_file() and p.name!=MANIFEST)
    manifest = {'product':'孢粉分析工作台','version':__version__,'platform':'Windows x64',
        'files':[{'path':p.relative_to(app).as_posix(),'bytes':p.stat().st_size,'sha256':file_hash(p)} for p in files]}
    (app/MANIFEST).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    verify_directory(app)
    write_zip(portable,[(p,'PollenStudio/'+p.relative_to(app).as_posix()) for p in sorted(app.rglob('*')) if p.is_file()])
    source = release_root/f'PollenStudio-{__version__}-source.zip'
    top_files = ['main.py','README.md','requirements.txt','build.ps1','release.py','.gitignore']
    sources = [ROOT/name for name in top_files]
    for folder in ('pollen','tests','tools','assets','使用指南'):
        sources.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    write_zip(source,[(p,f'PollenStudio-{__version__}-source/'+p.relative_to(ROOT).as_posix()) for p in sorted(sources)])
    (release_root/'SHA256SUMS.txt').write_text(''.join(file_hash(p)+'  '+p.name+'\n' for p in (portable,source)),encoding='utf-8')
    for path in (portable,source):
        print(f'已生成 {path.name}（{path.stat().st_size/1024/1024:.1f} MB）')


if __name__=='__main__':
    main()
