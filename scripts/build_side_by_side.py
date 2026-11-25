"""Build a side-by-side copy of the integration under the domain xantechv2.

Usage: python scripts/build_side_by_side.py <repo> <out_dir>
Then copy <out_dir>/custom_components/xantechv2 into HA and restart HA.
"""
import json, pathlib, shutil, subprocess, sys

repo = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
src = repo / 'custom_components' / 'xantech'
rev = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--short', 'HEAD'], text=True).strip()

if out.exists():
    shutil.rmtree(out)
cc = out / 'custom_components' / 'xantechv2'
shutil.copytree(src, cc, ignore=shutil.ignore_patterns('__pycache__'))

def lf(root):
    # the Windows checkout has CRLF; HA runs on Linux
    for f in root.rglob('*'):
        if f.is_file() and f.suffix in ('.py', '.json', '.yaml', '.toml', '.cfg', '.ini'):
            f.write_bytes(f.read_bytes().replace(bytes([13, 10]), bytes([10])))

lf(cc)

def sub(p, old, new, count=1):
    s = p.read_bytes().decode()
    assert s.count(old) == count, (p, old, s.count(old))
    p.write_bytes(s.replace(old, new).encode())

sub(cc / 'const.py', "DOMAIN: Final = 'xantech'", "DOMAIN: Final = 'xantechv2'")
sub(cc / 'services.yaml', 'integration: xantech\n', 'integration: xantechv2\n', count=7)
m = json.loads((cc / 'manifest.json').read_text())
m['domain'] = 'xantechv2'
m['name'] = 'Xantech Multi-Zone Audio v2 (test)'
m['version'] = f"{m['version']}+fork.{rev}"
(cc / 'manifest.json').write_bytes((json.dumps(m, indent=4) + '\n').encode())

# test copy: same tests, pointed at the renamed package
shutil.copytree(repo / 'tests', out / 'tests', ignore=shutil.ignore_patterns('__pycache__'))
for f in (out / 'tests').rglob('*.py'):
    s = f.read_bytes().decode()
    f.write_bytes(s.replace('custom_components.xantech.', 'custom_components.xantechv2.')
                   .replace('custom_components.xantech ', 'custom_components.xantechv2 ').encode())
lf(out / 'tests')
sub(out / 'tests' / 'test_media_player.py', "== 'xantech_test_amp_zone_11'", "== 'xantechv2_test_amp_zone_11'")
for name in ('pyproject.toml', 'setup.cfg', 'pytest.ini'):
    if (repo / name).exists():
        shutil.copy(repo / name, out / name)
lf(out)
print('built', cc, m['version'])
