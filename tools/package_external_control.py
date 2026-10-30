#!/usr/bin/env python3
"""Package the locally built SDL binary as a separate self-contained macOS app."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def package(destination):
    destination = Path(destination).resolve()
    app = destination / 'Brogue External Control.app'
    if app.exists():
        raise SystemExit(f'Refusing to overwrite {app}; choose an empty destination.')
    mac = app / 'Contents/MacOS'
    resources = app / 'Contents/Resources'
    frameworks = app / 'Contents/Frameworks'
    for directory in (mac, resources, frameworks):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'bin/brogue', mac / 'brogue')
    shutil.copytree(ROOT / 'bin/assets', resources / 'assets')
    shutil.copy2(ROOT / 'macos/Brogue.icns', resources / 'Brogue.icns')
    shutil.copy2(ROOT / 'bin/keymap.txt', resources / 'keymap.txt')
    with (ROOT / 'macos/Info.plist').open('rb') as source:
        info = plistlib.load(source)
    info.update(CFBundleIdentifier='jp.toyoshi.BrogueExternalControl',
                CFBundleExecutable='launch', CFBundleName='Brogue External Control',
                CFBundleDisplayName='Brogue External Control')
    with (app / 'Contents/Info.plist').open('wb') as output:
        plistlib.dump(info, output)
    launcher = mac / 'launch'
    launcher.write_text('''#!/bin/sh
set -eu
mac=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$mac/../../.." && pwd)
mkdir -p "$root/Session"
cd "$root/Session"
exec "$mac/brogue" --data-dir "$mac/../Resources" --control-dir "/tmp/brogue-control-$(id -u)" "$@" >> "$root/launch.log" 2>&1
''')
    launcher.chmod(0o755)
    copied = {}

    def bundle(binary, source):
        source = source.resolve()
        lines = run('otool', '-L', str(source)).splitlines()[1:]
        load_commands = run('otool', '-l', str(source)).splitlines()
        rpaths = []
        for index, line in enumerate(load_commands):
            if line.strip() == 'cmd LC_RPATH':
                rpaths.append(load_commands[index + 2].strip().split(' (offset', 1)[0][5:])
        for line in lines:
            original = line.strip().split(' (', 1)[0]
            if original.startswith(('/usr/lib/', '/System/')):
                continue
            if original.startswith('@rpath/'):
                tail = original[len('@rpath/'):]
                candidates = [source.parent / tail]
                for rpath in rpaths:
                    rpath = rpath.replace('@loader_path', str(source.parent))
                    candidates.append(Path(rpath) / tail)
                resolved = next((p.resolve() for p in candidates if p.exists()), None)
                if resolved is None:
                    raise RuntimeError(f'Cannot resolve {original} in {source}')
            elif original.startswith('@loader_path/'):
                resolved = (source.parent / original[len('@loader_path/'):]).resolve()
            elif original.startswith('/'):
                resolved = Path(original).resolve()
            else:
                raise RuntimeError(f'Unsupported dependency {original} in {source}')
            if resolved == source:  # dylib's own install ID
                continue
            name = Path(original).name
            target = frameworks / name
            if name not in copied:
                copied[name] = resolved
                shutil.copy2(resolved, target)
                target.chmod(0o755)
                run('install_name_tool', '-id', '@rpath/' + name, str(target))
                bundle(target, resolved)
            elif copied[name] != resolved:
                raise RuntimeError(f'Conflicting library names: {name}')
            prefix = '@executable_path/../Frameworks/' if binary == mac / 'brogue' else '@loader_path/'
            run('install_name_tool', '-change', original, prefix + name, str(binary))

    bundle(mac / 'brogue', ROOT / 'bin/brogue')
    # Homebrew may supply sdl2-compat, which dlopens SDL3 instead of linking it.
    sdl2 = frameworks / 'libSDL2-2.0.0.dylib'
    if sdl2.exists() and b'Failed loading SDL3 library' in sdl2.read_bytes():
        config = shutil.which('sdl2-config')
        source = Path(config).parent.parent / 'lib/libSDL3.dylib' if config else Path('/missing')
        if not source.exists():
            raise RuntimeError('sdl2-compat requires an installed SDL3 library')
        target = frameworks / 'libSDL3.dylib'
        shutil.copy2(source, target)
        target.chmod(0o755)
        run('install_name_tool', '-id', '@rpath/libSDL3.dylib', str(target))
        bundle(target, source)
    for library in frameworks.iterdir():
        run('codesign', '--force', '--sign', '-', str(library))
    run('codesign', '--force', '--sign', '-', str(mac / 'brogue'))
    run('codesign', '--force', '--sign', '-', str(app))
    shutil.copy2(ROOT / 'tools/external_control.py', destination / 'external_control.py')
    shutil.copy2(ROOT / 'EXTERNAL_CONTROL.md', destination / 'EXTERNAL_CONTROL.md')
    shutil.copy2(ROOT / 'LICENSE.txt', destination / 'LICENSE.txt')
    print(app)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    package(parser.parse_args().destination)
