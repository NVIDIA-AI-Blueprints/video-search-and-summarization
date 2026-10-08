"""Temporary read-only replay of a retained task; never rewrites its verdict."""
import argparse
import importlib.util
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True, type=Path)
args = parser.parse_args()
root = Path('/tmp/skill-eval/results/_viewer')
trials = [p for job in root.glob('*__37767605818__*') for p in job.glob('step-11*/agent/trajectory.json')]
if len(trials) != 1:
    raise RuntimeError('Expected exactly one retained step-11 trajectory')
trajectory = trials[0]
module_path = Path(__file__).parent / 'verifiers/generic_judge.py'
spec = importlib.util.spec_from_file_location('replay_judge', module_path)
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)
task = json.loads((Path(__file__).parents[2] / 'skills/operations/vss-ask-video/evals/base_profile_video_understanding.json').read_text())['expects'][10]
instruction = (trajectory.parent / 'instruction.txt').read_text()
data = json.loads(trajectory.read_text())
user_text = '\n'.join(row.get('message', '') for row in data['steps'] if row.get('source') == 'user' and isinstance(row.get('message'), str))
evidence = {
    'run': 37767605818, 'step': 11,
    'instruction_contains_selected_query': task['query'] in instruction,
    'trajectory_user_contains_selected_query': task['query'] in user_text,
    'trajectory_user_contains_unrelated_alerts': 'alerts profile' in user_text,
}
if not evidence['instruction_contains_selected_query'] or not evidence['trajectory_user_contains_selected_query']:
    raise RuntimeError('Retained task and selected query do not match')
evidence['replay'] = judge._run_checks(task['checks'], str(trajectory), 180, query=task['query'], step=11)
args.out.write_text(json.dumps(judge._scrub_tree(evidence), indent=2) + '\n')
