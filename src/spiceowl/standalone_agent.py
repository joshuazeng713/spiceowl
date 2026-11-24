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
                                circuitgit_client_config=None, circuitgit_sdk_path=None):
    from spiceowl.read_api import open_read_views
    handlers = {
        'spiceowl_probe_host': lambda args, **_: controller.hosts.probe(args['host_id']),
        'spiceowl_scan_resources': lambda args, **_: controller.hosts.scan(args['host_ids']),
        'spiceowl_recommend_hosts': lambda args, **_: controller.hosts.recommend(args['requirements'], args['host_ids']),
    }
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
