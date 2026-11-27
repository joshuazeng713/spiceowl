"""Operator-bound capabilities for the standalone window; no second Runner."""
import hashlib
import importlib
import json
from pathlib import Path
import sys

from spiceowl.domain.common import DomainError
from spiceowl.simulation_tools import SimulationToolDispatcher, _validate

OWNER_OPERATIONS = frozenset('''projects read sim_plan sim_preflight sim_submit sim_status
    sim_results request_status sim_runtime sim_saved_run sim_execute
    onboarding_netlist onboarding_ssh_hosts onboarding_session onboarding_probe
    onboarding_discover_pdk onboarding_veriloga_headers onboarding_pdk_scopes
    onboarding_select_pdk_scope onboarding_prepare onboarding_run onboarding_submission
    onboarding_inspect onboarding_waveforms_catalog onboarding_waveforms_mapping
    onboarding_waveforms_window onboarding_source onboarding_agent_tb_review
    onboarding_agent_tb onboarding_agent_tb_draft onboarding_agent_tb_prepare
    onboarding_analysis'''.split())


class CircuitGitOwner:
    """Typed calls to an explicitly installed/selected CircuitGit client SDK.

Service owns its existing config/journal and approval requests. No resolve,
approve, generic HTTP, shell, publication or canonical-source edit is exported.
"""
    def __init__(self, client_config, sdk_path=None):
        if sdk_path is not None:
            path = Path(sdk_path).expanduser()
            if not path.is_absolute() or not (path / 'ic_gateway' / 'service.py').is_file():
                raise DomainError('Select the explicit installed CircuitGit SDK parent directory.', 'owner_sdk_unavailable')
            # Only operator startup configuration controls import provenance.
            existing = sys.modules.get('ic_gateway.service')
            if existing and not Path(existing.__file__).resolve().is_relative_to(path.resolve()):
                raise DomainError('A different CircuitGit SDK is already loaded.', 'owner_sdk_conflict')
            sys.path.insert(0, str(path.resolve()))
        try:
            service = importlib.import_module('ic_gateway.service')
            contracts = importlib.import_module('ic_gateway.contracts')
        except ImportError:
            raise DomainError('Install or explicitly select the reviewed CircuitGit SDK.', 'owner_sdk_unavailable') from None
        try:
            self.service = service.Service(service.load_config(client_config))
            self.failure = service.Failure
            definitions = contracts.tools()
        except service.Failure as exc:
            raise DomainError('CircuitGit rejected the operator client configuration: ' + exc.code, 'owner_configuration_invalid') from None
        self.contracts = {row['name'].removeprefix('ic_'): row for row in definitions
                          if row['name'].removeprefix('ic_') in OWNER_OPERATIONS}
        self.operation_names = ['catalog', *sorted(self.contracts)]
        self.read_operations = {'catalog'} | (set(getattr(contracts, 'READ_ONLY', ())) & set(self.contracts))

    def __call__(self, payload, *, context):
        operation, arguments = payload['operation'], payload['arguments']
        if operation == 'catalog':
            if arguments:
                raise DomainError('Owner catalog takes empty arguments.', 'invalid_tool_arguments')
            return {'schema': 'spiceowl.owner-tool-catalog.v1', 'owner': 'circuitgit',
                'tools': list(self.contracts.values()), 'automatic_approval': False,
                'review': 'Use the existing gateway human review page; model calls cannot resolve requests.'}
        if operation not in self.contracts:
            raise DomainError('This owner operation is not exposed to the Agent.', 'tool_unavailable')
        _validate(arguments, self.contracts[operation]['inputSchema'])
        try:
            return self.service.call(operation, arguments)
        except self.failure as exc:
            # Keep safe review/reconciliation identity; never return config/key paths.
            allowed = {'review_url', 'request_id', 'fingerprint', 'state', 'status', 'operation',
                       'intake_id', 'plan_id', 'run_id', 'job_id', 'submission_key', 'revision'}
            details = {key: value for key, value in (exc.details or {}).items() if key in allowed}
            return {'ok': False, 'error': {'code': exc.code,
                'message': 'The execution owner returned ' + exc.code + '. Preserve the original request; use its human review page or status tool.'},
                'details': details, 'automatic_approval': False, 'replayed': False}


def _resolve_library_host(controller, host_id=None):
    hosts = controller.hosts.list() if hasattr(controller, 'hosts') else []
    if not hosts:
        raise DomainError('No registered SSH hosts are available. Connect at least one EDA server first.', 'host_unavailable')
    if host_id:
        for h in hosts:
            if h.get('host_id') == host_id:
                return h
        available = ', '.join(str(h.get('host_id')) for h in hosts[:12])
        raise DomainError(f'Host {host_id!r} is not registered. Available hosts: {available}.', 'host_not_found')
    verified = [h for h in hosts if (h.get('last_probe') or {}).get('identity', {}).get('verified')]
    return verified[0] if verified else hosts[0]


def _strip_rnl_preview(text, limit=12000):
    lines = []
    skip = False
    depth = 0
    for line in str(text or '').splitlines():
        if not skip and line.lstrip().startswith('//') and '@RNL' in line:
            skip = True
            depth = line.count('{') - line.count('}')
            if depth <= 0:
                skip = False
            continue
        if skip:
            depth += line.count('{') - line.count('}')
            if depth <= 0:
                skip = False
            continue
        lines.append(line)
    clean = '\n'.join(lines).strip()
    return clean[:limit] + ('\n... (truncated)' if len(clean) > limit else '')


def _apply_netlist_edits(netlist_text: str, edits: list) -> str:
    if not isinstance(netlist_text, str):
        raise DomainError('Active netlist text must be a string.', 'invalid_tool_arguments')
    if not isinstance(edits, list) or len(edits) > 32:
        raise DomainError('netlist_edits must be a list of at most 32 edit objects.', 'invalid_tool_arguments')
    updated = netlist_text
    for item in edits:
        if not isinstance(item, dict):
            raise DomainError('Each netlist_edits item must be an object with find and replace.', 'invalid_tool_arguments')
        if set(item) - {'find', 'replace', 'replace_all'}:
            raise DomainError('netlist_edits items accept only find, replace, and optional replace_all.', 'invalid_tool_arguments')
        find = item.get('find')
        replace = item.get('replace')
        replace_all = item.get('replace_all', False)
        if not isinstance(find, str) or not find or len(find) > 8192:
            raise DomainError('Each netlist_edits item requires a non-empty find string.', 'invalid_tool_arguments')
        if not isinstance(replace, str) or len(replace) > 16384:
            raise DomainError('Each netlist_edits item requires a replace string.', 'invalid_tool_arguments')
        if type(replace_all) is not bool:
            raise DomainError('netlist_edits replace_all must be a boolean.', 'invalid_tool_arguments')
        count = updated.count(find)
        if count == 0:
            raise DomainError('netlist_edits target was not found in the active netlist.', 'invalid_tool_arguments')
        if count > 1 and not replace_all:
            raise DomainError(
                'netlist_edits target matched multiple locations; add more context or set replace_all=true.',
                'invalid_tool_arguments',
            )
        updated = updated.replace(find, replace) if replace_all else updated.replace(find, replace, 1)
    return updated


def build_standalone_dispatcher(controller, *, live_client=None, pdk_lut_path=None,
                                circuitgit_client_config=None, circuitgit_sdk_path=None,
                                direct_manager=None, handoff_manager=None,
                                ui_patch_sink=None, ssh_dir=None,
                                active_source_box=None):
    from spiceowl.read_api import open_read_views
    ssh_cfg = (Path(ssh_dir) / 'config') if ssh_dir else (Path.home() / '.ssh' / 'config')

    def library_browse(args, **_):
        from spiceowl.onboarding.ssh_import import browse_remote_netlists, find_remote_library_targets
        host = _resolve_library_host(controller, args.get('host_id'))
        ssh_target = host.get('ssh_target') or host.get('host_id')
        ssh_hostname = host.get('ssh_hostname')
        op = args.get('operation') or ('find' if args.get('query') else 'browse')
        if op == 'find':
            query = str(args.get('query') or '').strip()
            if not query:
                raise DomainError('operation="find" requires a non-empty query string.', 'invalid_tool_arguments')
            res = find_remote_library_targets(
                ssh_target,
                ssh_cfg if ssh_cfg.is_file() else None,
                query=query,
                path=args.get('path') or '~',
                max_depth=int(args.get('max_depth') or 5),
                ssh_hostname=ssh_hostname,
            )
            return {'host_id': host['host_id'], **res}
        res = browse_remote_netlists(
            ssh_target,
            ssh_cfg if ssh_cfg.is_file() else None,
            path=args.get('path') or '~',
            ssh_hostname=ssh_hostname,
        )
        return {'host_id': host['host_id'], **res}

    source_box = active_source_box if isinstance(active_source_box, dict) else {}

    def library_open(args, **_):
        from spiceowl.onboarding.ssh_import import export_remote_schematic, fetch_remote_netlist
        host = _resolve_library_host(controller, args.get('host_id'))
        ssh_target = host.get('ssh_target') or host.get('host_id')
        ssh_hostname = host.get('ssh_hostname')
        mode = args['mode']
        remote_path = args['remote_path']
        if mode == 'schematic':
            res = export_remote_schematic(
                ssh_target,
                ssh_cfg if ssh_cfg.is_file() else None,
                path=remote_path,
                include_maestro=bool(args.get('include_maestro', True)),
                preserve_rnl=bool(args.get('preserve_rnl', True)),
                maestro_view=args.get('maestro_view'),
                cds_lib=args.get('cds_lib'),
                ssh_hostname=ssh_hostname,
            )
        else:
            res = fetch_remote_netlist(
                ssh_target,
                ssh_cfg if ssh_cfg.is_file() else None,
                path=remote_path,
                include_dependencies=True,
                ssh_hostname=ssh_hostname,
            )
        source_box.clear()
        source_box.update({
            'host_id': host['host_id'],
            'netlist_text': res.get('text') or '',
            'support_files': dict(res.get('support_files') or {}),
            'project_name': res.get('lib_name') or res.get('cell_name') or None,
            'library_name': res.get('lib_name') or None,
            'cell_name': res.get('cell_name') or None,
            'view_name': res.get('view_name') or None,
            'source_origin': 'ssh',
        })
        revision = None
        if callable(ui_patch_sink):
            revision = ui_patch_sink('library_open', {
                'host_id': host['host_id'],
                'mode': mode,
                'payload': res,
            })
        summary = {
            'loaded_in_ui': revision is not None,
            'revision': revision,
            'host_id': host['host_id'],
            'mode': mode,
            'file_name': res.get('file_name'),
            'remote_path': res.get('remote_path'),
            'lib_name': res.get('lib_name'),
            'cell_name': res.get('cell_name'),
            'view_name': res.get('view_name'),
            'bytes': len((res.get('text') or '').encode('utf-8')),
            'support_files': sorted((res.get('support_files') or {}).keys())[:32],
            'maestro_applied': bool(res.get('maestro_applied')),
            'maestro_summary': (
                {
                    'view_name': (res.get('maestro_config') or {}).get('view_name'),
                    'analyses': (res.get('maestro_config') or {}).get('analyses') or [],
                    'variables': (res.get('maestro_config') or {}).get('variables') or {},
                    'corners': [c.get('name') for c in ((res.get('maestro_config') or {}).get('corners') or []) if c.get('enabled')],
                    'model_files_count': len((res.get('maestro_config') or {}).get('model_files') or []),
                } if isinstance(res.get('maestro_config'), dict) else None
            ),
            'rnl_applied': bool(res.get('rnl_applied')),
            'rnl_stats': res.get('rnl_stats'),
            'warnings': res.get('warnings') or [],
            'netlist_preview': _strip_rnl_preview(res.get('text') or ''),
        }
        return summary

    def ui_configure(args, **_):
        if not args:
            raise DomainError('Provide at least one UI option to configure.', 'invalid_tool_arguments')
        if 'host_id' in args:
            _resolve_library_host(controller, args['host_id'])
        patch_args = dict(args)
        if isinstance(args.get('netlist_text'), str) and args['netlist_text'].strip():
            source_box['netlist_text'] = args['netlist_text']
        if 'netlist_edits' in args:
            edits = patch_args.pop('netlist_edits')
            if edits:
                base_text = source_box.get('netlist_text')
                if not isinstance(base_text, str) or not base_text.strip():
                    raise DomainError(
                        'netlist_edits requires an active netlist (open a schematic/netlist or set netlist_text first).',
                        'invalid_tool_arguments',
                    )
                updated_text = _apply_netlist_edits(base_text, edits)
                source_box['netlist_text'] = updated_text
                patch_args['netlist_text'] = updated_text
        if isinstance(args.get('append_netlist'), str) and args['append_netlist'].strip():
            if source_box.get('netlist_text'):
                combined = source_box['netlist_text'].rstrip() + '\n\n' + args['append_netlist'].strip() + '\n'
                source_box['netlist_text'] = combined
                if 'netlist_text' in patch_args:
                    patch_args['netlist_text'] = combined
                    patch_args.pop('append_netlist', None)
        revision = None
        if callable(ui_patch_sink):
            revision = ui_patch_sink('ui_configure', patch_args)
        return {
            'applied': True,
            'revision': revision,
            'changed_fields': sorted(patch_args.keys()),
        }

    handlers = {
        'spiceowl_probe_host': lambda args, **_: controller.hosts.probe(args['host_id']),
        'spiceowl_scan_resources': lambda args, **_: controller.hosts.scan(args['host_ids']),
        'spiceowl_recommend_hosts': lambda args, **_: controller.hosts.recommend(args['requirements'], args['host_ids']),
        'spiceowl_library_browse': library_browse,
        'spiceowl_library_open': library_open,
        'spiceowl_ui_configure': ui_configure,
    }
    policy_override = None
    if direct_manager is not None:
        from spiceowl.batch import DirectBatchManager
        from spiceowl.monitoring.log_stream import IncrementalLogWatcher
        from spiceowl.watch_handoff import WatchHandoffManager, evaluate_autonomy_decision
        batch_manager = DirectBatchManager(direct_manager.data_dir, direct_manager)
        watch_mgr = handoff_manager or WatchHandoffManager(direct_manager.data_dir)
        log_watcher = IncrementalLogWatcher(direct_manager.data_dir / 'log_cursors')

        def _overnight_policy_override(name, arguments):
            policy = watch_mgr.get_policy()
            if not policy.get('enabled'):
                return None
            if name == 'spiceowl_scan_resources':
                return 'read'
            if name == 'spiceowl_direct_control' and isinstance(arguments, dict):
                op = arguments.get('operation')
                params = arguments.get('arguments') if isinstance(arguments.get('arguments'), dict) else {}
                parent_run = None
                parent_id = params.get('parent_run_id') or params.get('root_run_id')
                if isinstance(parent_id, str) and parent_id:
                    try:
                        parent_run = direct_manager.read(parent_id)
                    except DomainError:
                        parent_run = None
                resub_used = watch_mgr.count_agent_resubmissions(parent_id if isinstance(parent_id, str) else None)
                verdict = evaluate_autonomy_decision(
                    policy, operation=str(op or ''), arguments=params,
                    parent_run=parent_run, resubmissions_used=resub_used,
                )
                return 'read' if verdict['allowed'] else 'approval'
            return None

        policy_override = _overnight_policy_override

        def direct_read(args, **_):
            op, params = args['operation'], args['arguments']
            try:
                if op == 'list':
                    if params:
                        raise DomainError('list takes empty arguments.', 'invalid_tool_arguments')
                    return {'runs': direct_manager.list(batch_map=batch_manager.run_batch_map())}
                if op == 'inspect':
                    return direct_manager.read(params['run_id'])
                if op == 'inputs':
                    return direct_manager.read_inputs(params['run_id'])
                if op == 'lookup':
                    return direct_manager.lookup_submission(params['key'])
                if op == 'report':
                    return direct_manager.report(params['run_id'])
                if op == 'waveform_index':
                    return direct_manager.waveform_index(params['run_id'], allow_remote=False)
                if op == 'waveform_query':
                    return direct_manager.waveform_query(
                        params['run_id'], params['dataset'], params['signals'],
                        x_min=params.get('x_min'), x_max=params.get('x_max'),
                        max_points=params.get('max_points', 2000),
                        mode=params.get('mode', 'auto'),
                        allow_remote=False,
                    )
                if op == 'waveform_cycles':
                    return direct_manager.waveform_cycles(
                        params['run_id'], params['dataset'], params['signals'],
                        period=params.get('period'), clock_signal=params.get('clock_signal'),
                        threshold=params.get('threshold'), edge=params.get('edge', 'rising'),
                        t_start=params.get('t_start'), t_end=params.get('t_end'),
                        worst_metric=params.get('worst_metric', 'peak_to_peak'),
                        worst_signal=params.get('worst_signal'),
                        cycle_index=params.get('cycle_index'),
                        context_cycles=params.get('context_cycles', 0),
                        overlay_cycles=params.get('overlay_cycles', 10),
                        max_points_per_cycle=params.get('max_points_per_cycle', 2000),
                        allow_remote=False,
                    )
                if op == 'waveform_measure':
                    return direct_manager.waveform_measure(
                        params['run_id'], params['dataset'], params['signal'],
                        x_min=params.get('x_min'), x_max=params.get('x_max'),
                        tol_pct=float(params.get('tol_pct', 2.0)),
                        allow_remote=False,
                    )
                if op == 'recovery_reports':
                    return {'reports': direct_manager.list_recovery_reports(query=params.get('q'), outcome=params.get('outcome'))}
                if op == 'log_tail':
                    run_dir = direct_manager.path(params['run_id'])
                    log_file = run_dir / 'collected' / 'psf' / 'spectre.out'
                    if not log_file.is_file():
                        log_file = run_dir / 'collected' / 'console.log'
                    if not log_file.is_file():
                        return {'run_id': params['run_id'], 'events': [], 'rotated': False, 'note': 'No collected local log file yet.'}
                    return log_watcher.poll(params['run_id'], log_file)
                if op == 'watch_events':
                    return {'events': watch_mgr.list_events(state=params.get('state'), limit=int(params.get('limit', 30)))}
                if op == 'watch_policy':
                    return watch_mgr.get_policy()
                if op == 'batch_list':
                    if params:
                        raise DomainError('batch_list takes empty arguments.', 'invalid_tool_arguments')
                    return {'batches': batch_manager.list()}
                if op == 'batch_inspect':
                    return batch_manager.build_summary(params['batch_id'])
                if op == 'batch_report':
                    return batch_manager.report(params['batch_id'])
                if op == 'skills':
                    if params:
                        raise DomainError('skills takes empty arguments.', 'invalid_tool_arguments')
                    from spiceowl.skills import list_skills
                    return list_skills()
                if op == 'skill':
                    if set(params) != {'name'}:
                        raise DomainError('skill requires only name.', 'invalid_tool_arguments')
                    from spiceowl.skills import get_skill
                    return get_skill(params['name'])
                raise DomainError('Unknown direct read operation.', 'invalid_tool_arguments')
            except (KeyError, TypeError, ValueError) as exc:
                raise DomainError(f'Invalid arguments for direct_read {op}: {exc}', 'invalid_tool_arguments') from None

        def _normalize_prepare_params(params, context, *, op_name='prepare', allow_recheck=False):
            import inspect as _inspect
            import re as _re
            from datetime import datetime, timezone

            prep_params = dict(params)
            recheck_resources = False
            if allow_recheck and 'recheck_resources' in prep_params:
                raw_recheck = prep_params.pop('recheck_resources')
                if type(raw_recheck) is not bool:
                    raise DomainError('recheck_resources must be a boolean.', 'invalid_tool_arguments')
                recheck_resources = raw_recheck

            # Convert convenient flat resource arguments into limits dict
            flat_lim_keys = ('cpus', 'memory_gib', 'wall_seconds', 'license_queue_seconds', 'enforce_rlimit_as')
            if any(k in prep_params for k in flat_lim_keys):
                lim = dict(prep_params.get('limits') or {}) if isinstance(prep_params.get('limits'), dict) else {}
                if 'cpus' in prep_params and 'cpu_count' not in lim:
                    raw_cpus = prep_params.pop('cpus')
                    if type(raw_cpus) is bool:
                        raise ValueError('cpus must be an integer')
                    lim['cpu_count'] = int(raw_cpus)
                else:
                    prep_params.pop('cpus', None)
                if 'memory_gib' in prep_params and 'memory_bytes' not in lim:
                    raw_mem = prep_params.pop('memory_gib')
                    if type(raw_mem) is bool:
                        raise ValueError('memory_gib must be numeric')
                    lim['memory_bytes'] = int(round(float(raw_mem) * 2**30))
                else:
                    prep_params.pop('memory_gib', None)
                for lk in ('wall_seconds', 'license_queue_seconds', 'enforce_rlimit_as'):
                    if lk in prep_params:
                        val = prep_params.pop(lk)
                        if lk not in lim:
                            lim[lk] = val
                prep_params['limits'] = lim

            netlist_edits = prep_params.pop('netlist_edits', None)
            append_netlist = prep_params.pop('append_netlist', None)

            # Reuse active netlist & support files opened via spiceowl_library_open / workbench if omitted
            if not prep_params.get('netlist_text') and source_box.get('netlist_text'):
                prep_params['netlist_text'] = source_box['netlist_text']
                if not prep_params.get('support_files') and source_box.get('support_files'):
                    prep_params['support_files'] = dict(source_box['support_files'])
                for mk in ('project_name', 'library_name', 'cell_name', 'view_name', 'source_origin'):
                    if not prep_params.get(mk) and source_box.get(mk):
                        prep_params[mk] = source_box[mk]

            if not isinstance(prep_params.get('netlist_text'), str) or not prep_params['netlist_text'].strip():
                raise DomainError(
                    f'{op_name} requires netlist_text (or call spiceowl_library_open first to load a schematic/netlist).',
                    'invalid_tool_arguments',
                )
            if netlist_edits is not None:
                prep_params['netlist_text'] = _apply_netlist_edits(prep_params['netlist_text'], netlist_edits)
            if append_netlist is not None:
                if not isinstance(append_netlist, str):
                    raise DomainError('append_netlist must be a string.', 'invalid_tool_arguments')
                if append_netlist.strip():
                    prep_params['netlist_text'] = prep_params['netlist_text'].rstrip() + '\n\n' + append_netlist.strip() + '\n'
            if not prep_params['netlist_text'].strip():
                raise DomainError(f'{op_name} requires a non-empty netlist_text.', 'invalid_tool_arguments')

            if not prep_params.get('host_id') and not prep_params.get('host_ids'):
                raise DomainError('Specify host_id (or host_ids) for the target server.', 'invalid_tool_arguments')

            if not prep_params.get('run_key'):
                stem = _re.sub(r'[^A-Za-z0-9_-]', '_', str(prep_params.get('cell_name') or 'sim')).strip('_-')[:40] or 'sim'
                if not stem[0].isalnum():
                    stem = 'sim-' + stem
                stamp = datetime.now(timezone.utc).strftime('%H%M%S')
                base_key = f'{stem}-{stamp}'
                cand = base_key
                suffix = 2
                while direct_manager.path(cand).exists():
                    cand = f'{base_key}-{suffix}'
                    suffix += 1
                prep_params['run_key'] = cand

            if not prep_params.get('submission_group_id') and context.get('turn_id'):
                prep_params['submission_group_id'] = f"turn:{context['turn_id']}"
            if not prep_params.get('source_origin'):
                prep_params['source_origin'] = 'agent'

            valid_sig = set(_inspect.signature(direct_manager.prepare).parameters)
            unknown = set(prep_params) - valid_sig
            if unknown:
                raise DomainError(f'Unsupported {op_name} arguments: {", ".join(sorted(unknown))}.', 'invalid_tool_arguments')
            return prep_params, recheck_resources

        def direct_control(args, *, context):
            op, params = args['operation'], args['arguments']
            try:
                if op == 'prepare':
                    prep_params, _ = _normalize_prepare_params(params, context, op_name='prepare', allow_recheck=False)
                    return direct_manager.prepare(**prep_params)
                if op == 'submit':
                    prep_params, recheck_resources = _normalize_prepare_params(params, context, op_name='submit', allow_recheck=True)
                    result = direct_manager.submit(recheck_resources=recheck_resources, **prep_params)
                    if callable(ui_patch_sink):
                        ui_patch_sink('direct_run_submitted', {
                            'run_id': result.get('run_id'),
                            'host_id': result.get('host_id'),
                            'state': result.get('state'),
                            'simulator': result.get('simulator'),
                        })
                    return result
                if op == 'stage_and_launch':
                    unknown = set(params) - {'run_id', 'recheck_resources'}
                    if unknown:
                        raise DomainError(f'Unsupported stage_and_launch arguments: {", ".join(sorted(unknown))}.', 'invalid_tool_arguments')
                    result = direct_manager.stage_and_launch(
                        params['run_id'],
                        recheck_resources=bool(params.get('recheck_resources', False)),
                    )
                    if callable(ui_patch_sink):
                        ui_patch_sink('direct_run_submitted', {
                            'run_id': result.get('run_id'),
                            'host_id': result.get('host_id'),
                            'state': result.get('state'),
                            'simulator': result.get('simulator'),
                        })
                    return result
                if op == 'stage':
                    return direct_manager.stage(params['run_id'])
                if op == 'launch':
                    return direct_manager.launch(params['run_id'], recheck_resources=params.get('recheck_resources'))
                if op == 'status':
                    return direct_manager.status(params['run_id'])
                if op == 'cancel':
                    action_key = params.get('action_key')
                    if action_key is None and all(context.get(field) for field in ('request_key', 'thread_id', 'turn_id', 'tool_call_id')):
                        identity = {field: context[field] for field in ('request_key', 'thread_id', 'turn_id', 'tool_call_id')}
                        action_key = 'cancel-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
                    return direct_manager.cancel(params['run_id'], action_key=action_key)
                if op == 'collect':
                    return direct_manager.collect(params['run_id'])
                if op == 'delete':
                    return direct_manager.delete(params['run_id'])
                if op == 'watch_record_decision':
                    return watch_mgr.record_agent_decision(
                        params['event_id'],
                        outcome=params['outcome'],
                        rationale=params['rationale'],
                        resource_assessment=params.get('resource_assessment'),
                        config_changes=params.get('config_changes'),
                        new_run_id=params.get('new_run_id'),
                        needs_human_review=bool(params.get('needs_human_review', False)),
                    )
                if op == 'batch_prepare':
                    return batch_manager.prepare(**params)
                if op == 'batch_dispatch':
                    return batch_manager.dispatch_next(
                        params['batch_id'], limit=params.get('limit'),
                        recheck_resources=params.get('recheck_resources'),
                    )
                if op == 'batch_refresh':
                    return batch_manager.refresh(
                        params['batch_id'],
                        auto_collect=params.get('auto_collect', True),
                        dispatch_more=params.get('dispatch_more', False),
                        recheck_resources=params.get('recheck_resources'),
                    )
                if op == 'batch_cancel':
                    return batch_manager.cancel(params['batch_id'])
                raise DomainError('Unknown direct control operation.', 'invalid_tool_arguments')
            except (KeyError, TypeError, ValueError) as exc:
                raise DomainError(f'Invalid arguments for direct_control {op}: {exc}', 'invalid_tool_arguments') from None

        handlers['spiceowl_direct_read'] = direct_read
        handlers['spiceowl_direct_control'] = direct_control
    if pdk_lut_path is not None:
        fixed_path = Path(pdk_lut_path).expanduser().absolute()
        def lookup(args, **_):
            from spiceowl.onboarding.pdk_lut import PdkLut
            # One read-only connection per calling worker; no model path selection.
            with PdkLut(fixed_path, read_only=True) as cache:
                return getattr(cache, args['operation'])(**args['arguments'])
        handlers['spiceowl_pdk_lookup'] = lookup
    if circuitgit_client_config is not None:
        handlers['spiceowl_onboarding'] = CircuitGitOwner(circuitgit_client_config, circuitgit_sdk_path)
    elif circuitgit_sdk_path is not None:
        raise DomainError('A selected CircuitGit SDK requires an explicit client configuration.', 'owner_configuration_invalid')
    if live_client is not None:
        handlers['spiceowl_runtime'] = lambda args, **_: live_client.runtime()
        handlers['spiceowl_lookup_submission'] = lambda args, **_: live_client.lookup_submission(args['key'])
        def live(args, *, context):
            operation = args['operation']
            key = None
            if operation in {'run', 'submit', 'collect'}:
                if not all(context.get(field) for field in ('request_key', 'thread_id', 'turn_id', 'tool_call_id')):
                    raise DomainError('A persisted Agent tool identity is required.', 'submission_identity_missing')
                identity = {field: context[field] for field in ('request_key', 'thread_id', 'turn_id', 'tool_call_id')}
                # A registered workflow has exactly one submission key per
                # plan. Preserve LiveSimulationClient's experiment-PLAN_ID
                # rule; only direct submit/collect use a tool-call identity.
                if operation != 'run':
                    key = 'codex-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            return live_client.execute(operation, args['arguments'], idempotency_key=key)
        handlers['spiceowl_live_control'] = live
    return SimulationToolDispatcher(
        mode='standalone',
        read_views=open_read_views(controller.store.directory),
        owner_handlers=handlers,
        policy_override=policy_override,
    )
