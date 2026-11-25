"""Copy the DAX88 network client from a pyxantech checkout into the integration.

Usage: python scripts/bundle_network_client.py <path-to-pyxantech-checkout>

The integration bundles pyxantech/dax88_network.py (it only needs the standard library)
until a pyxantech release ships it. Keep the two identical: edit the library, then run this.
"""
import pathlib
import subprocess
import sys

lib = pathlib.Path(sys.argv[1])
here = pathlib.Path(__file__).resolve().parents[1]
rev = subprocess.check_output(
    ['git', '-C', str(lib), 'rev-parse', '--short', 'HEAD'], text=True
).strip()
source = (lib / 'pyxantech' / 'dax88_network.py').read_bytes().replace(b'\r\n', b'\n')
# pyxantech is MIT-licensed: a copy carries its copyright and permission notice
notice = (lib / 'LICENSE').read_text(encoding='utf-8').replace('\r\n', '\n').strip()
header = (
    f'# Bundled copy of pyxantech/dax88_network.py from the pyxantech fork (commit {rev}).\n'
    '# Keep it identical to the library; replace with `from pyxantech.dax88_network import ...`\n'
    '# once a pyxantech release ships it. It only uses the Python standard library.\n'
    '#\n'
    '# pyxantech is distributed under this licence:\n'
    '#\n'
    + ''.join(f'# {line}'.rstrip() + '\n' for line in notice.split('\n'))
).encode()
target = here / 'custom_components' / 'xantech' / 'dax88_network.py'
target.write_bytes(header + source)
print(f'bundled {target} from pyxantech {rev}')
