#!/usr/bin/env python3
"""Remove local account metadata from the sdist and write release checksums."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
EPOCH = int(os.environ.get('SOURCE_DATE_EPOCH', '1780272000'))
for archive in DIST.glob('*.tar.gz'):
    output = io.BytesIO()
    with tarfile.open(archive, 'r:gz') as source:
        with gzip.GzipFile(fileobj=output, mode='wb', mtime=EPOCH, filename='') as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as target:
                for member in source.getmembers():
                    contents = source.extractfile(member) if member.isfile() else None
                    member.uid = member.gid = 0
                    member.uname = member.gname = ''
                    member.mtime = EPOCH
                    member.pax_headers = {}
                    target.addfile(member, contents)
    archive.write_bytes(output.getvalue())
checksums = []
for archive in sorted(DIST.iterdir()):
    if archive.suffix in ('.whl','.apk') or archive.name.endswith('.tar.gz'):
        checksums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name)
(DIST / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
print('Normalized archive owners/timestamps and wrote SHA256SUMS.')
