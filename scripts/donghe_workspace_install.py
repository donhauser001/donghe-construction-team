"""Install one macOS URL handler; no per-project daemon or login agent."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import uuid


def install_helper(package):
    if not (package / 'runtime/python/bin/python3').is_file():
        raise ValueError('install the complete package before installing the reader helper')
    root = Path.home() / '.donghe' / 'reader'
    root.mkdir(parents=True, exist_ok=True)
    app = root / '东合阅读助手.app'
    # AppleScript handles only URLs; the Python dispatcher validates registered IDs.
    executable = str(package / 'runtime/python/bin/python3')
    script = str(package / 'scripts/workspace_entry.py')
    import shlex
    command = shlex.quote(executable) + ' -I -B ' + shlex.quote(script) + ' url --url '
    source = 'on open location incomingURL\n do shell script ' + json.dumps(command, ensure_ascii=False) + ' & quoted form of incomingURL\nend open location\n'
    with tempfile.TemporaryDirectory(prefix='donghe-reader-', dir=root) as temporary:
        temporary = Path(temporary)
        source_path = temporary / 'handler.applescript'
        source_path.write_text(source)
        prepared = temporary / app.name
        subprocess.run(['/usr/bin/osacompile', '-o', str(prepared), str(source_path)], check=True, capture_output=True)
        info = prepared / 'Contents/Info.plist'
        data = plistlib.loads(info.read_bytes())
        data.update({'CFBundleIdentifier': 'com.donghe.workspace-reader', 'LSUIElement': True,
                     'CFBundleURLTypes': [{'CFBundleURLName': 'Donghe project reader', 'CFBundleURLSchemes': ['donghe']}]})
        info.write_bytes(plistlib.dumps(data))
        backup = root / ('reader-app-backup-' + uuid.uuid4().hex)
        if app.exists():
            os.replace(app, backup)
        try:
            os.replace(prepared, app)
            subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister',
                            '-f', str(app)], check=True, capture_output=True)
        except BaseException:
            if app.exists():
                os.replace(app, root / ('reader-app-failed-' + uuid.uuid4().hex))
            if backup.exists():
                os.replace(backup, app)
            raise
    return {'helper': str(app), 'scheme': 'donghe', 'backgroundAtLogin': False}
