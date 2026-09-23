"""Exercise the installed tracker hook contract with real shell scripts."""
import json
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'workflow-tracker/scripts'


def test_session_start_preserves_chain_and_reinjects_instructions(tmp_path):
    state = tmp_path / '.step-status'
    state.mkdir()
    (state / 'current').write_text('fix')
    (state / 'fix.state').write_text('active\tdiagnose\t\nplanned\tverify\t\n')
    for source in ('startup', 'resume', 'compact', 'clear'):
        result = subprocess.run(['bash', str(SCRIPTS / 'hook_session_start.sh')],
                                input=json.dumps({'cwd': str(tmp_path), 'source': source}),
                                text=True, capture_output=True, check=True)
        assert (state / 'fix.state').exists()
        assert 'diagnose' in result.stdout
        assert 'clear` when finished' not in result.stdout
