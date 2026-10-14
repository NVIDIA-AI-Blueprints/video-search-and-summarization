# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'run_embedding_comparison.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_required_cli_and_defaults():
    with pytest.raises(SystemExit):
        runner.parser().parse_args([])
    args = runner.parser().parse_args(['--config', 'c.json', '--out', 'run'])
    assert not args.resume and not args.dry_run and args.log_level == 'INFO'


def test_resume_requires_dependencies_and_unchanged_artifacts(tmp_path):
    (tmp_path / 'bundle.json').write_text('{}')
    record = {'status': 'complete', 'dependency_fingerprint': 'one', 'artifacts': runner.artifact_hashes(tmp_path)}
    assert runner.reusable(record, 'one', tmp_path)
    assert not runner.reusable(record, 'two', tmp_path)
    (tmp_path / 'bundle.json').write_text('{"changed": true}')
    assert not runner.reusable(record, 'one', tmp_path)


def test_relative_config_paths(tmp_path):
    (tmp_path / 'dataset').mkdir()
    (tmp_path / 'scripts').mkdir()
    for name in ('embed_cosmos.py', 'score.py', 'events.py'):
        (tmp_path / 'scripts' / name).write_text('# original script placeholder\n')
    (tmp_path / 'clips.txt').write_text('a.mp4\n')
    c = dict(data='dataset', video_list='clips.txt', scripts_dir='scripts', es_index='videos', rt_embed_model='deployed', reference_model='reference', reference_revision='a' * 40)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(c))
    assert runner.load_config(config)['data'] == str(tmp_path / 'dataset')


def test_commands_forward_defaults_and_network_configuration(tmp_path):
    c = dict(data='dataset', video_list='clips.txt', scripts_dir='scripts', es_index='videos', rt_embed_model='deployed', reference_model='reference', reference_revision='a' * 40)
    commands = dict(runner.commands(c, tmp_path, tmp_path / 'deployment.json'))
    assert commands['ingestion'][commands['ingestion'].index('--complete-retries') + 1] == '3'
    assert commands['vss'][commands['vss'].index('--wait-timeout') + 1] == '1200'
    assert commands['reference'][commands['reference'].index('--num-frames') + 1] == '8'
    assert '--deployment-config' in commands['ingestion'] and '--deployment-config' in commands['vss']


def test_dry_run_never_preflights_or_uploads(tmp_path, monkeypatch):
    import sys
    import types
    (tmp_path / 'dataset').mkdir()
    (tmp_path / 'scripts').mkdir()
    for name in ('embed_cosmos.py', 'score.py', 'events.py'):
        (tmp_path / 'scripts' / name).write_text('# original script placeholder\n')
    (tmp_path / 'clips.txt').write_text('a.mp4\n')
    c = dict(data='dataset', video_list='clips.txt', scripts_dir='scripts', es_index='videos', rt_embed_model='deployed', reference_model='reference', reference_revision='a' * 40)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(c))
    calls = []
    monkeypatch.setitem(sys.modules, 'comparison_common', types.SimpleNamespace(script_hashes=runner.artifact_hashes, deployment_document=lambda p: {}, load_deployment=lambda p: {'agent': 'https://example/api'}, preflight=lambda *a: pytest.fail('network preflight in dry run')))
    monkeypatch.setattr(runner, 'preflight_external', lambda c: None)
    monkeypatch.setattr(runner.subprocess, 'run', lambda command, **kwargs: calls.append(command))
    runner.run(runner.parser().parse_args(['--config', str(config), '--out', str(tmp_path / 'out'), '--dry-run']))
    assert len(calls) == 1 and 'prepare_subset.py' in calls[0][1]
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('name', ['prepare_subset', 'ingest_subset', 'collect_vss_embeddings', 'generate_reference_embeddings', 'evaluate_and_report', 'run_embedding_comparison'])
def test_all_cli_help(name):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(runner.ROOT / (name + '.py')), '--help'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--log-level' in result.stdout
    assert '--out' in result.stdout


@pytest.mark.parametrize('name,expected', [
    ('prepare_subset', {'log_level': 'INFO'}),
    ('ingest_subset', {'upload_timestamp': '2025-01-01T00:00:00', 'complete_retries': 3, 'complete_backoff': 5, 'resume': False, 'deployment_config': None, 'auth_config': None, 'ca_bundle': None}),
    ('collect_vss_embeddings', {'wait_timeout': 1200, 'poll_interval': 10, 'vector_field': 'llm.visionEmbeddings.vector', 'sensor_field': 'sensor.id.keyword', 'resume': False}),
    ('generate_reference_embeddings', {'num_frames': 8, 'video_batch': 2, 'text_batch': 64}),
    ('evaluate_and_report', {'log_level': 'INFO'}),
    ('run_embedding_comparison', {'resume': False, 'dry_run': False}),
])
def test_documented_parser_defaults(name, expected, monkeypatch):
    monkeypatch.syspath_prepend(str(runner.ROOT))
    import importlib
    module = importlib.import_module(name)
    parser_factory = getattr(module, 'parser', None) or module.build_parser
    p = parser_factory()
    required = []
    for action in p._actions:
        if action.required:
            required.extend([action.option_strings[0], 'placeholder'])
    values = vars(p.parse_args(required))
    for key, value in expected.items():
        assert values[key] == value
    assert values['log_level'] == 'INFO'


def test_resume_skips_completed_upload_and_invalidates_changed_scripts(tmp_path, monkeypatch):
    import sys
    import types
    (tmp_path / 'dataset').mkdir()
    (tmp_path / 'scripts').mkdir()
    for name in ('embed_cosmos.py', 'score.py', 'events.py'):
        (tmp_path / 'scripts' / name).write_text('# pinned\n')
    (tmp_path / 'clips.txt').write_text('clip.mp4\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(data='dataset', video_list='clips.txt', scripts_dir='scripts', es_index='videos', rt_embed_model='deployed', reference_model='reference', reference_revision='a' * 40)))
    monkeypatch.setitem(sys.modules, 'comparison_common', types.SimpleNamespace(script_hashes=runner.artifact_hashes, deployment_document=lambda p: {}, load_deployment=lambda p: {}, preflight=lambda *a, **kw: None))
    monkeypatch.setattr(runner, 'preflight_external', lambda c: None)
    executed = []

    def stage(command, **kwargs):
        executed.append(Path(command[1]).stem)
        output = Path(command[command.index('--out') + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'result.json').write_text('{}')
        if 'prepare_subset' in executed[-1]:
            (output / 'selection.json').write_text(json.dumps({'run_id': 'run', 'subset_fingerprint': 'subset'}))

    monkeypatch.setattr(runner.subprocess, 'run', stage)
    argv = ['--config', str(config), '--out', str(tmp_path / 'run')]
    runner.run(runner.parser().parse_args(argv))
    assert len(executed) == 5
    executed.clear()
    runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == []
    (tmp_path / 'scripts' / 'embed_cosmos.py').write_text('# changed revision\n')
    runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == ['generate_reference_embeddings', 'evaluate_and_report']
    executed.clear()
    values = json.loads(config.read_text())
    values['rt_embed_model'] = 'new-deployed-model'
    config.write_text(json.dumps(values))
    runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == ['collect_vss_embeddings', 'evaluate_and_report']
    executed.clear()
    values['reference_model'] = 'new-reference-model'
    config.write_text(json.dumps(values))
    runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == ['generate_reference_embeddings', 'evaluate_and_report']


@pytest.mark.parametrize('missing', ['model', 'es_index'])
def test_preflight_failure_prevents_upload(tmp_path, monkeypatch, missing):
    import sys
    import types
    (tmp_path / 'dataset').mkdir()
    (tmp_path / 'scripts').mkdir()
    for name in ('embed_cosmos.py', 'score.py', 'events.py'):
        (tmp_path / 'scripts' / name).write_text('# pinned')
    (tmp_path / 'clips.txt').write_text('a.mp4')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(data='dataset', video_list='clips.txt', scripts_dir='scripts', es_index='videos', rt_embed_model='deployed', reference_model='reference', reference_revision='a' * 40)))

    def unavailable(*args, **kwargs):
        assert kwargs['model'] == 'deployed' and kwargs['es_index'] == 'videos'
        raise ValueError(f'unavailable {missing}')

    monkeypatch.setitem(sys.modules, 'comparison_common', types.SimpleNamespace(script_hashes=runner.artifact_hashes, deployment_document=lambda p: {}, load_deployment=lambda p: {}, preflight=unavailable))
    monkeypatch.setattr(runner, 'preflight_external', lambda c: None)
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **kw: pytest.fail('stage started before successful preflight'))
    with pytest.raises(ValueError, match=f'unavailable {missing}'):
        runner.run(runner.parser().parse_args(['--config', str(config), '--out', str(tmp_path / 'run')]))
    assert json.loads((tmp_path / 'run' / 'run.json').read_text())['preflight_error'] == 'ValueError'


@pytest.fixture
def completed_run(tmp_path, monkeypatch):
    import sys
    import types
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    (dataset / 'media.mp4').write_bytes(b'original media')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ('embed_cosmos.py', 'score.py', 'events.py', 'preprocessing.py'):
        (scripts / name).write_text('# pinned\n')
    (tmp_path / 'clips.txt').write_text('media.mp4\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(data='dataset', video_list='clips.txt', scripts_dir='scripts', es_index='videos', rt_embed_model='deployed', reference_model='reference', reference_revision='a' * 40)))
    helper_spec = importlib.util.spec_from_file_location('runner_test_common', runner.ROOT / 'comparison_common.py')
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    monkeypatch.setitem(sys.modules, 'comparison_common', types.SimpleNamespace(
        script_hashes=helper.script_hashes, deployment_document=lambda p: {}, load_deployment=lambda p: {},
        preflight=lambda *a, **kw: None))
    monkeypatch.setattr(runner, 'preflight_external', lambda c: None)
    executed = []

    def stage(command, **kwargs):
        name = Path(command[1]).stem
        executed.append(name)
        output = Path(command[command.index('--out') + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'result.json').write_text('{}')
        if name == 'prepare_subset':
            # Regenerating a subset assigns a new identity, even when bytes match.
            import uuid
            (output / 'selection.json').write_text(json.dumps({'run_id': str(uuid.uuid4()), 'subset_fingerprint': 'subset'}))

    monkeypatch.setattr(runner.subprocess, 'run', stage)
    out = tmp_path / 'run'
    argv = ['--config', str(config), '--out', str(out)]
    runner.run(runner.parser().parse_args(argv))
    executed.clear()
    return config, out, argv, executed


def test_nonresume_rejection_preserves_complete_run(completed_run):
    _, out, argv, executed = completed_run
    before = runner.artifact_hashes(out)
    with pytest.raises(ValueError, match='resume|fresh'):
        runner.run(runner.parser().parse_args(argv))
    assert executed == []
    assert runner.artifact_hashes(out) == before


@pytest.mark.parametrize('change', ['dataset', 'subset_artifact', 'upload_timestamp'])
def test_incompatible_resume_preserves_recovery_inputs(completed_run, change):
    config, out, argv, executed = completed_run
    if change == 'dataset':
        (config.parent / 'dataset' / 'media.mp4').write_bytes(b'new media')
    elif change == 'subset_artifact':
        (out / 'subset' / 'selection.json').write_text('{"corrupted": true}')
    else:
        values = json.loads(config.read_text())
        values['upload_timestamp'] = '2026-01-01T00:00:00'
        config.write_text(json.dumps(values))
    before = runner.artifact_hashes(out)
    with pytest.raises(ValueError, match='recovery|fresh'):
        runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == []
    assert runner.artifact_hashes(out) == before


@pytest.mark.parametrize('relationship', ['inside', 'same', 'ancestor'])
def test_output_dataset_overlap_is_rejected_before_writes(completed_run, relationship):
    config, _, _, executed = completed_run
    dataset = config.parent / 'dataset'
    out = {'inside': dataset / 'run', 'same': dataset, 'ancestor': config.parent}[relationship]
    before = runner.artifact_hashes(config.parent)
    with pytest.raises(ValueError, match='overlap|dataset'):
        runner.run(runner.parser().parse_args(['--config', str(config), '--out', str(out), '--resume']))
    assert executed == []
    assert runner.artifact_hashes(config.parent) == before


def test_supporting_reference_script_change_invalidates_reference_without_upload(completed_run):
    config, _, argv, executed = completed_run
    (config.parent / 'scripts' / 'preprocessing.py').write_text('# new preprocessing\n')
    runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == ['generate_reference_embeddings', 'evaluate_and_report']


def test_script_cache_changes_do_not_invalidate_resume(completed_run):
    config, _, argv, executed = completed_run
    scripts = config.parent / 'scripts'
    for dirname in ('__pycache__', '.pytest_cache'):
        cache = scripts / dirname
        cache.mkdir()
        (cache / 'generated').write_text('transient')
    (scripts / 'helper.pyc').write_bytes(b'bytecode')
    runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == []


def test_missing_manifest_preserves_orphaned_recovery_state(completed_run):
    _, out, argv, executed = completed_run
    (out / 'run.json').unlink()
    before = runner.artifact_hashes(out)
    with pytest.raises(ValueError, match='recovery|fresh'):
        runner.run(runner.parser().parse_args(argv + ['--resume']))
    assert executed == []
    assert runner.artifact_hashes(out) == before


@pytest.mark.parametrize('relationship', ['inside', 'same', 'ancestor'])
def test_output_scripts_overlap_is_rejected_before_writes(completed_run, relationship):
    config, _, _, executed = completed_run
    scripts = config.parent / 'scripts'
    if relationship == 'ancestor':
        nested = scripts / 'nested'
        nested.mkdir()
        for path in list(scripts.iterdir()):
            if path != nested:
                path.rename(nested / path.name)
        values = json.loads(config.read_text())
        values['scripts_dir'] = 'scripts/nested'
        config.write_text(json.dumps(values))
        scripts = nested
    out = {'inside': scripts / 'run', 'same': scripts, 'ancestor': scripts.parent}[relationship]
    before = runner.artifact_hashes(config.parent)
    with pytest.raises(ValueError, match='overlap|scripts'):
        runner.run(runner.parser().parse_args(['--config', str(config), '--out', str(out), '--resume']))
    assert executed == []
    assert runner.artifact_hashes(config.parent) == before
