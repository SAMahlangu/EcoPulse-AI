"""Check application text for invalid UTF-8 and common encoding corruption.

Run from any directory: python diagnostics/check_text_encoding.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.tsx', '.ts', '.js', '.css', '.html', '.py', '.sql', '.md', '.json', '.txt'}
CORRUPTION = re.compile(r'\u00c2[\u0080-\u00bf]|\u00c3[\u0080-\u00bf]|\u00e2\u20ac|\u00e2\u0153|\ufffd')


def main() -> int:
    files = [ROOT / 'README.md']
    for folder in ('frontend/src', 'backend/app', 'backend/tests', 'database', 'docs'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.suffix in EXTENSIONS)
    failures = []
    for path in files:
        try:
            text = path.read_text(encoding='utf-8-sig')
        except UnicodeError:
            failures.append(f'{path.relative_to(ROOT)}: invalid UTF-8')
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if CORRUPTION.search(line):
                failures.append(f'{path.relative_to(ROOT)}:{number}: corrupted text')
    if failures:
        print('\n'.join(failures))
        return 1
    print(f'Text encoding check passed ({len(files)} files).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
