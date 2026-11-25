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


def build_standalone_dispatcher(controller, *, live_client=None, pdk_lut_path=None,
                                circuitgit_client_config=None, circuitgit_sdk_path=None,
                                direct_manager=None):
    from spiceowl.read_api import open_read_views
    handlers = {
        'spiceowl_probe_host': lambda args, **_: controller.hosts.probe(args['host_id']),
        'spiceowl_scan_resources': lambda args, **_: controller.hosts.scan(args['host_ids']),
        'spiceowl_recommend_hosts': lambda args, **_: controller.hosts.recommend(args['requirements'], args['host_ids']),
    }
    if direct_manager is not None:
        def direct_read(args, **_):
            op, params = args['operation'], args['arguments']
            if op == 'list':
                if params:
                    raise DomainError('list takes empty arguments.', 'invalid_tool_arguments')
                return {'runs': direct_manager.list()}
            if op == 'inspect':
                return direct_manager.read(params['run_id'])
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
            raise DomainError('Unknown direct read operation.', 'invalid_tool_arguments')

        def direct_control(args, *, context):
            op, params = args['operation'], args['arguments']
            if op == 'prepare':
                return direct_manager.prepare(**params)
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
            raise DomainError('Unknown direct control operation.', 'invalid_tool_arguments')

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
    return SimulationToolDispatcher(mode='standalone', read_views=open_read_views(controller.store.directory), owner_handlers=handlers)
