"""Real pipes with a fake local protocol process; never starts Codex or a solver."""

import json
import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from spiceowl.codex_bridge import BridgeError, CodexBridge
from spiceowl.codex_bridge.bridge import MAX_RESULT_BYTES, MAX_TOOL_ARGUMENT_BYTES
from spiceowl.domain.common import DomainError


FAKE_PROCESS = r'''
import json, os, pathlib, sys
root=pathlib.Path.cwd()
fixture=json.loads((root/'fixture.json').read_text())
scenario=fixture.get('scenario','normal')
record=root/'wire.jsonl'
pending_responses=set()
def send(value):
    sys.stdout.write(json.dumps(value)+'\n');sys.stdout.flush()
def reply(message,result):send({'id':message['id'],'result':result})
def notice(method,params):send({'method':method,'params':params})
def complete():
    notice('item/agentMessage/delta',{'threadId':'thread-1','turnId':'turn-1','itemId':'text-1','delta':'verified answer'})
    notice('turn/completed',{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}})
def request(method,params,rid='server-1'):
    send({'id':rid,'method':method,'params':{'threadId':'thread-1','turnId':'turn-1',**params}})
for line in sys.stdin:
    message=json.loads(line)
    with record.open('a') as out:out.write(json.dumps(message)+'\n')
    method=message.get('method')
    if method=='initialize':reply(message,{'userAgent':'fake-codex'})
    elif method=='initialized':pass
    elif method=='config/read':reply(message,{'config':{'approval_policy':fixture.get('policy','on-request'),
        'approvals_reviewer':'user','mcp_servers':{'private_server':{'secret':'do not persist'} }}})
    elif method=='thread/start':
        reply(message,{'thread':{'id':'thread-1'},'model':'fake-model','approvalPolicy':fixture.get('thread_policy','on-request'),
            'approvalsReviewer':'user','sandbox':{'type':'readOnly'}})
    elif method=='turn/interrupt':
        reply(message,{})
        notice('turn/completed',{'threadId':'thread-1','turn':{'id':'turn-1','status':'interrupted'}})
    elif method=='turn/start':
        if scenario=='exit_on_turn':os._exit(0)
        if scenario=='oversized':sys.stdout.write('x'*(2*1024*1024+1));sys.stdout.flush();os._exit(0)
        if scenario=='reject_turn':send({'id':message['id'],'error':{'code':-32602,'message':'rejected'}});continue
        if scenario!='early_complete':reply(message,{'turn':{'id':'turn-1','status':'inProgress'}})
        notice('turn/started',{'threadId':'thread-1','turn':{'id':'turn-1','status':'inProgress'}})
        if scenario in ('normal','early_complete'):
            complete()
            if scenario=='early_complete':reply(message,{'turn':{'id':'turn-1','status':'inProgress'}})
        elif scenario in ('tool','duplicate_tool','changed_tool','tool_then_exit','tool_cross_turn','denial_followup','premature_terminal'):
            params={'tool':fixture.get('tool','inspect_circuit'),'callId':'call-1','arguments':fixture.get('arguments',{'value':7})}
            if scenario=='tool_cross_turn':params['turnId']='other-turn'
            request('item/tool/call',params)
            if scenario=='duplicate_tool':request('item/tool/call',params,'server-duplicate')
            if scenario=='changed_tool':request('item/tool/call',{**params,'arguments':{'value':8}},'server-changed')
            if scenario=='premature_terminal':complete()
        elif scenario in ('native','native_changed','native_no_accept'):
            params={'itemId':'command-1','command':'echo synthetic','cwd':str(root),'reason':'fake approval'}
            if scenario=='native_no_accept':params['availableDecisions']=['decline','cancel']
            request('item/commandExecution/requestApproval',params)
            if scenario=='native_changed':request('item/commandExecution/requestApproval',{**params,'command':'changed command'})
        elif scenario in ('two_tools','two_tools_premature','two_tools_with_read','two_native'):
            pending_responses={'server-1','server-2'}
            if scenario=='two_native':
                for i in (1,2):
                    request('item/commandExecution/requestApproval',{'itemId':'command-'+str(i),
                        'command':'echo synthetic-'+str(i),'cwd':str(root)},'server-'+str(i))
            else:
                for i in (1,2):
                    request('item/tool/call',{'tool':'inspect_circuit','callId':'call-'+str(i),
                        'arguments':{'value':6+i}},'server-'+str(i))
                if scenario=='two_tools_with_read':
                    pending_responses.add('server-read')
                    request('item/tool/call',{'tool':'inspect_circuit','callId':'call-read','arguments':{'value':9}},'server-read')
        elif scenario=='permissions':request('item/permissions/requestApproval',{'itemId':'permission-1','permissions':{'network':{'enabled':True}}})
        elif scenario=='auth':request('account/chatgptAuthTokens/refresh',{'token':'sensitive-fixture-token'})
        elif scenario=='foreign_notification':
            notice('item/agentMessage/delta',{'threadId':'other-thread','turnId':'other-turn','delta':'wrong text'})
            complete()
    elif method is None and message.get('id','').startswith('server-'):
        if scenario=='tool_then_exit':os._exit(0)
        if scenario in ('two_tools','two_tools_premature','two_tools_with_read','two_native'):
            pending_responses.discard(message['id'])
            if scenario=='two_tools_premature' or not pending_responses:complete()
            else:notice('turn/started',{'threadId':'thread-1','turn':{'id':'turn-1','status':'inProgress'}})
            continue
        if scenario=='denial_followup' and message['id']=='server-1':
            request('item/tool/call',{'tool':'inspect_circuit','callId':'call-2','arguments':{'value':9}},'server-2')
            continue
        if scenario not in ('changed_tool','native_changed'):
            complete()
'''


class CodexBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        if os.name == 'nt':
            script = self.root / 'fake_codex.py'
            script.write_text(FAKE_PROCESS, encoding='utf-8')
            self.exe = self.root / 'fake-codex.cmd'
            self.exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
        else:
            self.exe = self.root / 'fake-codex'
            self.exe.write_text('#!' + sys.executable + '\n' + FAKE_PROCESS)
            self.exe.chmod(0o700)
        self.bridges = []
        self.dispatched = []

    def tearDown(self):
        for bridge in reversed(self.bridges):
            bridge.close()
        self.temp.cleanup()

    def bridge(self, scenario='normal', *, policy='read', dispatch=None, mode='standalone', definitions=None, **fixture):
        directory = self.root / ('session-' + str(len(self.bridges)))
        if mode == 'standalone':
            directory.mkdir()
            (directory / 'fixture.json').write_text(json.dumps({'scenario': scenario, **fixture}))
        def default_dispatch(name, arguments, *, context):
            self.dispatched.append((name, arguments, context))
            return {'value': arguments['value']}
        result = CodexBridge(directory, mode=mode, executable=self.exe, request_timeout=3,
            dispatch_tool=dispatch or default_dispatch, tool_policy=policy if callable(policy) else lambda name, args: policy,
            tool_definitions=definitions or [{'name':'inspect_circuit','description':'Read a synthetic circuit.',
                'inputSchema':{'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}}])
        self.bridges.append(result)
        return result

    def ready(self, bridge):
        bridge.connect({})
        return bridge.create_thread()['thread_id']

    def wait(self, callback):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = callback()
            if result:
                return result
            time.sleep(.01)
        self.fail('Fake protocol process did not reach the expected state')

    def wire(self, bridge):
        path = bridge.directory / 'wire.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_construction_and_read_apis_never_start_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('must not start')):
            bridge = self.bridge()
            self.assertFalse(bridge.status()['connected'])
            self.assertEqual([], bridge.events()['events'])

    def test_embedded_active_methods_fail_without_files_or_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('must not start')):
            for mode in ('embedded', 'embedded_in_circuitgit'):
                bridge = self.bridge(mode=mode)
                self.assertEqual('embedded_in_circuitgit', bridge.status()['mode'])
                for action in (lambda: bridge.connect({}), bridge.create_thread,
                               lambda: bridge.send_message('thread-1','hello','key-1'),
                               lambda: bridge.decide('approval-1','approve'), bridge.disconnect):
                    with self.assertRaises(BridgeError) as caught: action()
                    self.assertEqual('codex_embedded_mode', caught.exception.code)
                self.assertFalse(bridge.directory.exists())

    def test_credentials_and_command_overrides_are_rejected(self):
        bridge = self.bridge()
        with patch('subprocess.Popen', side_effect=AssertionError('must not start')):
            for profile in ({'credentials':{}},{'executable':'shell'},{'env':{}},{'profile':'bad name'}):
                with self.assertRaises(BridgeError):bridge.connect(profile)

    def test_official_handshake_restricted_thread_and_stream(self):
        bridge = self.bridge()
        thread = self.ready(bridge)
        bridge.send_message(thread,'hello','request-1')
        self.wait(lambda: bridge.status()['state']=='complete')
        wire = self.wire(bridge)
        self.assertEqual(['initialize','initialized','config/read','thread/start','turn/start'],[m.get('method') for m in wire])
        self.assertTrue(wire[0]['params']['capabilities']['experimentalApi'])
        thread_params = wire[3]['params']
        self.assertEqual('read-only',thread_params['sandbox'])
        self.assertNotIn('approvalPolicy',thread_params)
        self.assertFalse(thread_params['config']['features.shell_tool'])
        self.assertFalse(thread_params['config']['mcp_servers.private_server.enabled'])
        self.assertEqual('function',thread_params['dynamicTools'][0]['type'])
        delta=[e for e in bridge.events()['events'] if e['method']=='item/agentMessage/delta'][0]
        self.assertEqual('verified answer',delta['params']['delta'])
        self.assertEqual('request-1',delta['request_key'])
        user=[e for e in bridge.events()['events'] if e['method']=='bridge/user_message'][0]
        self.assertEqual('hello',user['params']['text'])
        self.assertEqual('request-1',user['request_key'])
        self.assertNotIn('do not persist',(bridge.directory/'codex-bridge.json').read_text())

    def test_never_approval_profile_is_refused(self):
        bridge = self.bridge(policy='never')
        # policy here configures tool policy; fixture uses the fake profile separately.
        (bridge.directory/'fixture.json').write_text(json.dumps({'policy':'never'}))
        with self.assertRaises(BridgeError) as caught:bridge.connect({})
        self.assertEqual('codex_manual_approval_unavailable',caught.exception.code)
        self.assertFalse(bridge.status()['connected'])

    def test_thread_policy_is_verified_before_any_turn(self):
        bridge=self.bridge(thread_policy='never');bridge.connect({})
        with self.assertRaises(BridgeError):bridge.create_thread()
        self.assertTrue(bridge.status()['recovery_required'])
        self.assertFalse(any(m.get('method')=='turn/start' for m in self.wire(bridge)))

    def test_terminal_before_start_reply_is_not_overwritten(self):
        bridge=self.bridge('early_complete');thread=self.ready(bridge)
        receipt=bridge.send_message(thread,'hello','request-1')
        self.assertEqual('completed',receipt['state'])
        self.assertEqual('complete',bridge.status()['state'])

    def test_request_key_returns_terminal_receipt_without_replay(self):
        bridge=self.bridge();thread=self.ready(bridge)
        bridge.send_message(thread,'hello','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        receipt=bridge.send_message(thread,'hello','request-1')
        self.assertEqual('completed',receipt['state'])
        self.assertEqual(1,sum(m.get('method')=='turn/start' for m in self.wire(bridge)))
        with self.assertRaises(BridgeError) as caught:bridge.send_message(thread,'changed','request-1')
        self.assertEqual('codex_key_conflict',caught.exception.code)

    def test_unknown_dispatch_survives_restart_and_blocks_changed_key(self):
        bridge=self.bridge('exit_on_turn');thread=self.ready(bridge)
        with self.assertRaises(BridgeError):bridge.send_message(thread,'hello','request-1')
        self.assertTrue(bridge.status()['recovery_required'])
        self.assertEqual('request-1',bridge.status()['request_key'])
        with self.assertRaises(BridgeError):bridge.send_message(thread,'hello','request-2')
        bridge.close()
        restored=CodexBridge(bridge.directory,executable=self.exe)
        self.bridges.append(restored)
        self.assertTrue(restored.status()['recovery_required'])
        with patch('subprocess.Popen',side_effect=AssertionError('must not reconnect')):
            with self.assertRaises(BridgeError):restored.connect({})
        self.assertEqual(1,sum(m.get('method')=='turn/start' for m in self.wire(bridge)))

    def test_turn_identity_is_durable_before_rpc_send(self):
        bridge=self.bridge('idle');thread=self.ready(bridge)
        original=bridge.rpc.send
        def inspect(message):
            if message.get('method')=='turn/start':
                saved=json.loads((bridge.directory/'codex-bridge.json').read_text())
                self.assertEqual('starting',saved['requests']['request-1']['state'])
            return original(message)
        bridge.rpc.send=inspect
        bridge.send_message(thread,'hello','request-1')

    def test_read_tool_dispatch_exact_context_once(self):
        bridge=self.bridge('duplicate_tool');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual(1,len(self.dispatched))
        name,args,context=self.dispatched[0]
        self.assertEqual({'value':7},args)
        self.assertEqual({'thread_id':'thread-1','turn_id':'turn-1','request_key':'request-1','tool_call_id':'call-1'},context)

    def test_tool_waits_for_human_and_dispatches_frozen_arguments(self):
        bridge=self.bridge('tool',policy='approval');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        self.assertEqual([],self.dispatched)
        self.assertEqual('awaiting_approval',bridge.status()['state'])
        self.assertTrue(pending['can_approve'])
        pending['params']['arguments']['value']=999
        bridge.decide(pending['approval_id'],'approve')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual({'value':7},self.dispatched[0][1])
        with self.assertRaises(BridgeError):bridge.decide(pending['approval_id'],'approve')

    def test_human_denial_never_calls_dispatcher(self):
        bridge=self.bridge('tool',policy='approval');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        bridge.decide(pending['approval_id'],'deny')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([],self.dispatched)
        answer=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]
        self.assertFalse(answer['result']['success'])

    def test_native_approval_is_exact_human_decision(self):
        bridge=self.bridge('native');thread=self.ready(bridge)
        bridge.send_message(thread,'native','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        self.assertFalse(any(m.get('id')=='server-1' for m in self.wire(bridge)))
        self.assertEqual('codex',pending['kind'])
        bridge.decide(pending['approval_id'],'approve')
        self.wait(lambda:bridge.status()['state']=='complete')
        answer=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]
        self.assertEqual({'decision':'accept'},answer['result'])

    def test_native_available_decisions_are_enforced(self):
        bridge=self.bridge('native_no_accept');thread=self.ready(bridge)
        bridge.send_message(thread,'native','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        self.assertFalse(pending['can_approve'])
        with self.assertRaises(BridgeError):bridge.decide(pending['approval_id'],'approve')
        bridge.decide(pending['approval_id'],'deny')
        self.wait(lambda:bridge.status()['state']=='complete')

    def test_permissions_cannot_gain_session_access(self):
        bridge=self.bridge('permissions');thread=self.ready(bridge)
        bridge.send_message(thread,'permission','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        self.assertFalse(pending['can_approve'])
        bridge.decide(pending['approval_id'],'deny')
        self.wait(lambda:bridge.status()['state']=='complete')
        answer=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]
        self.assertEqual({'permissions':{},'scope':'turn'},answer['result'])

    def test_changed_approval_identity_blocks_progress(self):
        bridge=self.bridge('native_changed');thread=self.ready(bridge)
        bridge.send_message(thread,'native','request-1')
        self.wait(lambda:bridge.status()['recovery_required'])
        pending=bridge.status()['pending_approvals'][0]
        self.assertEqual('unknown',pending['status'])
        with self.assertRaises(BridgeError):bridge.decide(pending['approval_id'],'approve')

    def test_wrong_turn_tool_and_foreign_notifications_are_rejected(self):
        bridge=self.bridge('tool_cross_turn');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([],self.dispatched)
        self.assertIn('error',[m for m in self.wire(bridge) if m.get('id')=='server-1'][0])
        other=self.bridge('foreign_notification');thread=self.ready(other)
        other.send_message(thread,'hello','request-1')
        self.wait(lambda:other.status()['state']=='complete')
        self.assertNotIn('wrong text',json.dumps(other.events()))

    def test_unadvertised_tool_is_never_called(self):
        bridge=self.bridge('tool',tool='approve_everything');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([],self.dispatched)

    def test_human_operations_cannot_be_registered_as_tools(self):
        for name in ('approve','spiceowl_approval_resolve','codex_decide','read_credentials'):
            with self.assertRaises(BridgeError):
                CodexBridge(self.root/name,tool_definitions=[{'name':name,'description':'bad','inputSchema':{'type':'object'}}])

    def test_disconnect_invalidates_pending_approval_without_execution(self):
        bridge=self.bridge('tool',policy='approval');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        bridge.disconnect()
        self.assertTrue(bridge.status()['recovery_required'])
        with self.assertRaises(BridgeError):bridge.decide(pending['approval_id'],'approve')
        self.assertEqual([],self.dispatched)

    def test_oversized_protocol_line_preserves_unknown_identity(self):
        bridge=self.bridge('oversized');thread=self.ready(bridge)
        with self.assertRaises(BridgeError):bridge.send_message(thread,'hello','request-1')
        self.assertTrue(bridge.status()['recovery_required'])

    def test_full_tool_result_retains_every_sample(self):
        samples=list(range(4096))
        bridge=self.bridge('tool',dispatch=lambda name,args,context:{'samples':samples})
        thread=self.ready(bridge);bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        answer=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]['result']
        self.assertEqual(samples,json.loads(answer['contentItems'][0]['text'])['samples'])
        event=[e for e in bridge.events()['events'] if e['method']=='tool/result'][0]
        self.assertTrue(event['params']['preview_only'])

    def test_large_tool_result_explicitly_reports_missing_output(self):
        bridge=self.bridge('tool',dispatch=lambda name,args,context:{'data':'x'*(MAX_RESULT_BYTES+1)})
        thread=self.ready(bridge);bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        answer=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]['result']
        result=json.loads(answer['contentItems'][0]['text'])
        self.assertFalse(answer['success'])
        self.assertTrue(result['execution_completed'])
        self.assertFalse(result['result_available'])
        self.assertEqual('codex_tool_output_too_large',result['error']['code'])

    def test_approval_detail_is_full_while_preview_is_labelled(self):
        arguments={'value':7,'source':'x'*30000}
        bridge=self.bridge('tool',policy='approval',arguments=arguments);thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        self.assertTrue(pending['preview_only'])
        self.assertFalse(pending['can_approve'])
        detail=bridge.approval_details(pending['approval_id'])
        self.assertFalse(detail['preview_only'])
        self.assertTrue(detail['can_approve'])
        self.assertEqual(arguments,detail['params']['arguments'])

    def test_auth_request_does_not_surface_or_persist_tokens(self):
        bridge=self.bridge('auth');thread=self.ready(bridge)
        bridge.send_message(thread,'hello','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertNotIn('sensitive-fixture-token',(bridge.directory/'codex-bridge.json').read_text())
        self.assertEqual([],bridge.status()['pending_approvals'])

    def test_single_owner_lock_and_private_journal(self):
        bridge=self.bridge();self.ready(bridge)
        with self.assertRaises(BridgeError) as caught:CodexBridge(bridge.directory)
        self.assertEqual('codex_session_busy',caught.exception.code)
        if os.name != 'nt':
            self.assertEqual(0o600,os.stat(bridge.directory/'codex-bridge.json').st_mode & 0o777)

    def test_bounded_events_have_explicit_gap_and_no_pagination_skip(self):
        bridge=self.bridge()
        with bridge.lock:
            for i in range(1003):bridge._event('test/event',{'i':i})
        first=bridge.events(0,200)
        self.assertTrue(first['truncated'])
        second=bridge.events(first['cursor'],200)
        self.assertEqual(first['cursor']+1,second['events'][0]['id'])
        self.assertEqual(1003,second['latest_cursor'])

    def test_human_denial_blocks_later_tools_in_same_turn(self):
        bridge=self.bridge('denial_followup',policy='approval');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        pending=self.wait(lambda:bridge.status()['pending_approvals'])[0]
        bridge.decide(pending['approval_id'],'deny')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([],self.dispatched)
        self.assertIn('error',[m for m in self.wire(bridge) if m.get('id')=='server-2'][0])

    def test_premature_terminal_cannot_hide_unfinished_tool(self):
        release=threading.Event()
        def dispatch(name,args,context):
            release.wait(5)
            return {'value':7}
        bridge=self.bridge('premature_terminal',dispatch=dispatch);thread=self.ready(bridge)
        try:
            bridge.send_message(thread,'inspect','request-1')
            self.wait(lambda:bridge.status()['recovery_required'])
            self.assertEqual('unknown',bridge.status()['state'])
        finally:
            release.set()

    def test_dispatch_exception_is_unknown_without_retry(self):
        count=[]
        def dispatch(name,args,context):
            count.append(context['tool_call_id'])
            raise RuntimeError('possibly after side effect')
        bridge=self.bridge('tool',dispatch=dispatch);thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['recovery_required'])
        with self.assertRaises(BridgeError):bridge.send_message(thread,'inspect','request-2')
        self.assertEqual(['call-1'],count)

    def test_disconnect_after_tool_reply_preserves_call_without_replay(self):
        bridge=self.bridge('tool_then_exit');thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['recovery_required'])
        self.assertEqual(1,len(self.dispatched))
        saved=json.loads((bridge.directory/'codex-bridge.json').read_text())
        self.assertEqual('complete',next(iter(saved['calls'].values()))['state'])
        with self.assertRaises(BridgeError):bridge.send_message(thread,'inspect','request-2')

    def test_known_rpc_rejection_has_a_non_replayed_receipt(self):
        bridge=self.bridge('reject_turn');thread=self.ready(bridge)
        with self.assertRaises(BridgeError) as caught:bridge.send_message(thread,'hello','request-1')
        self.assertEqual('codex_rpc_rejected',caught.exception.code)
        self.assertEqual('rejected',bridge.send_message(thread,'hello','request-1')['state'])
        self.assertFalse(bridge.status()['recovery_required'])

    def test_typed_read_only_validation_error_can_be_corrected(self):
        def dispatch(name,args,context):
            raise DomainError('Exact source is required.', 'invalid_tool_arguments')
        bridge=self.bridge('tool',dispatch=dispatch);thread=self.ready(bridge)
        bridge.send_message(thread,'inspect','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        answer=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]['result']
        self.assertFalse(answer['success'])
        self.assertEqual('invalid_tool_arguments',json.loads(answer['contentItems'][0]['text'])['error']['code'])
        self.assertFalse(bridge.status()['recovery_required'])

    def test_full_precision_4096_samples_and_times_reach_read_tool_unchanged(self):
        arguments={'samples':[math.sin(2*math.pi*37*i/4096)+0.0000123456789*math.cos(i) for i in range(4096)],
                   'time_s':[1.23456789101112e-7+i/1.000000123e9 for i in range(4096)]}
        raw=json.dumps(arguments,sort_keys=True,separators=(',',':')).encode()
        self.assertGreater(len(raw),64*1024)
        self.assertLess(len(raw),MAX_TOOL_ARGUMENT_BYTES)
        expected_hash=hashlib.sha256(raw).hexdigest()
        received=[]
        def dispatch(name,args,context):
            received.append(args)
            return {'sample_count':len(args['samples']),'timestamp_count':len(args['time_s']),
                'input_sha256':hashlib.sha256(json.dumps(args,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        schema={'type':'object','properties':{name:{'type':'array','items':{'type':'number'},'maxItems':4096}
                for name in ('samples','time_s')},'required':['samples','time_s'],'additionalProperties':False}
        definitions=[{'name':'inspect_circuit','description':'Read the full synthetic sampled waveform.','inputSchema':schema}]
        bridge=self.bridge('tool',arguments=arguments,dispatch=dispatch,definitions=definitions)
        thread=self.ready(bridge);bridge.send_message(thread,'analyze exact samples','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([arguments],received)
        reply=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]['result']
        self.assertTrue(reply['success'])
        self.assertEqual({'sample_count':4096,'timestamp_count':4096,'input_sha256':expected_hash},
                         json.loads(reply['contentItems'][0]['text']))

    def test_read_arguments_above_one_mib_are_explicitly_rejected_without_dispatch(self):
        bridge=self.bridge('tool',arguments={'value':7,'data':'x'*(MAX_TOOL_ARGUMENT_BYTES+1)})
        thread=self.ready(bridge);bridge.send_message(thread,'oversized input','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([],self.dispatched)
        reply=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]['result']
        self.assertFalse(reply['success'])
        self.assertEqual({'error':'codex_tool_arguments_too_large'},json.loads(reply['contentItems'][0]['text']))

    def test_approval_keeps_full_payload_limit_at_64_kib(self):
        bridge=self.bridge('tool',policy='approval',arguments={'value':7,'source':'x'*(64*1024)})
        thread=self.ready(bridge);bridge.send_message(thread,'oversized approval','request-1')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual([],self.dispatched)
        self.assertEqual([],bridge.status()['pending_approvals'])
        reply=[m for m in self.wire(bridge) if m.get('id')=='server-1'][0]['result']
        self.assertFalse(reply['success'])
        self.assertEqual({'error':'codex_approval_payload_limit'},json.loads(reply['contentItems'][0]['text']))

    def test_two_pending_tool_approvals_remain_actionable_in_either_order(self):
        for order in ((1,2),(2,1)):
            with self.subTest(order=order):
                start=len(self.dispatched)
                bridge=self.bridge('two_tools',policy='approval');thread=self.ready(bridge)
                bridge.send_message(thread,'two reviewed actions','request-1')
                self.wait(lambda:len(bridge.status()['pending_approvals'])==2)
                cards={a['params']['callId']:a for a in bridge.status()['pending_approvals']}
                bridge.decide(cards['call-'+str(order[0])]['approval_id'],'approve')
                remaining=bridge.status()['pending_approvals']
                self.assertEqual('awaiting_approval',bridge.status()['state'])
                self.assertEqual(1,len(remaining))
                self.assertTrue(remaining[0]['can_approve'])
                self.assertTrue(remaining[0]['can_deny'])
                bridge.decide(remaining[0]['approval_id'],'approve')
                self.wait(lambda:bridge.status()['state']=='complete')
                self.assertEqual(['call-'+str(i) for i in order],
                                 [row[2]['tool_call_id'] for row in self.dispatched[start:]])
                replies=[m['id'] for m in self.wire(bridge) if m.get('id') in ('server-1','server-2')]
                self.assertEqual(['server-1','server-2'],sorted(replies))

    def test_two_tool_approval_and_denial_orders_do_not_strand_a_card(self):
        for order in ((1,2),(2,1)):
            for first_decision in ('approve','deny'):
                with self.subTest(order=order,first_decision=first_decision):
                    start=len(self.dispatched)
                    bridge=self.bridge('two_tools',policy='approval');thread=self.ready(bridge)
                    bridge.send_message(thread,'two reviewed actions','request-1')
                    self.wait(lambda:len(bridge.status()['pending_approvals'])==2)
                    cards={a['params']['callId']:a for a in bridge.status()['pending_approvals']}
                    bridge.decide(cards['call-'+str(order[0])]['approval_id'],first_decision)
                    self.assertEqual('awaiting_approval',bridge.status()['state'])
                    remaining=bridge.status()['pending_approvals']
                    self.assertEqual(1,len(remaining))
                    self.assertTrue(remaining[0]['can_deny'])
                    self.assertEqual(first_decision=='approve',remaining[0]['can_approve'])
                    if first_decision=='deny':
                        with self.assertRaises(BridgeError) as caught:
                            bridge.decide(remaining[0]['approval_id'],'approve')
                        self.assertEqual('codex_approval_unsupported',caught.exception.code)
                        self.assertEqual('pending',bridge.status()['pending_approvals'][0]['status'])
                    bridge.decide(remaining[0]['approval_id'],'deny')
                    self.wait(lambda:bridge.status()['state']=='complete')
                    expected=['call-'+str(order[0])] if first_decision=='approve' else []
                    self.assertEqual(expected,[row[2]['tool_call_id'] for row in self.dispatched[start:]])
                    replies=[m['id'] for m in self.wire(bridge) if m.get('id') in ('server-1','server-2')]
                    self.assertEqual(['server-1','server-2'],sorted(replies))

    def test_read_tool_completion_and_duplicate_started_preserve_pending_approvals(self):
        policy=lambda name,args:'read' if args['value']==9 else 'approval'
        bridge=self.bridge('two_tools_with_read',policy=policy);thread=self.ready(bridge)
        bridge.send_message(thread,'read and two reviewed actions','request-1')
        self.wait(lambda:len(bridge.status()['pending_approvals'])==2)
        self.wait(lambda:any(m.get('id')=='server-read' for m in self.wire(bridge)))
        self.assertEqual('awaiting_approval',bridge.status()['state'])
        self.assertEqual(['call-read'],[row[2]['tool_call_id'] for row in self.dispatched])
        for card in bridge.status()['pending_approvals']:
            bridge.decide(card['approval_id'],'approve')
        self.wait(lambda:bridge.status()['state']=='complete')
        self.assertEqual(['call-1','call-2','call-read'],sorted(row[2]['tool_call_id'] for row in self.dispatched))

    def test_premature_terminal_cannot_hide_second_pending_approval(self):
        bridge=self.bridge('two_tools_premature',policy='approval');thread=self.ready(bridge)
        bridge.send_message(thread,'two reviewed actions','request-1')
        self.wait(lambda:len(bridge.status()['pending_approvals'])==2)
        cards={a['params']['callId']:a for a in bridge.status()['pending_approvals']}
        try:
            bridge.decide(cards['call-1']['approval_id'],'approve')
        except BridgeError as exc:
            self.assertEqual('codex_outcome_unknown',exc.code)
        self.wait(lambda:bridge.status()['recovery_required'])
        self.assertEqual('unknown',bridge.status()['state'])
        self.assertEqual(['call-1'],[row[2]['tool_call_id'] for row in self.dispatched])
        remaining=next(a for a in bridge.status()['pending_approvals'] if a['params']['callId']=='call-2')
        self.assertEqual('unknown',remaining['status'])
        self.assertFalse(remaining['can_approve'])
        with self.assertRaises(BridgeError):bridge.decide(remaining['approval_id'],'approve')

    def test_two_native_approvals_keep_remaining_denial_available(self):
        bridge=self.bridge('two_native');thread=self.ready(bridge)
        bridge.send_message(thread,'two native requests','request-1')
        self.wait(lambda:len(bridge.status()['pending_approvals'])==2)
        first,second=bridge.status()['pending_approvals']
        bridge.decide(first['approval_id'],'deny')
        self.assertEqual('awaiting_approval',bridge.status()['state'])
        remaining=bridge.status()['pending_approvals'][0]
        self.assertFalse(remaining['can_approve'])
        self.assertTrue(remaining['can_deny'])
        bridge.decide(second['approval_id'],'deny')
        self.wait(lambda:bridge.status()['state']=='complete')
        replies=[m['result']['decision'] for m in self.wire(bridge) if m.get('id') in ('server-1','server-2')]
        self.assertEqual(['decline','decline'],replies)
        self.assertEqual([],self.dispatched)

    def test_interrupt_stops_active_turn_clears_pending_approvals_and_allows_next_message(self):
        bridge=self.bridge('two_tools',policy='approval');thread=self.ready(bridge)
        bridge.send_message(thread,'two reviewed actions','request-1')
        self.wait(lambda:len(bridge.status()['pending_approvals'])==2)
        res=bridge.interrupt()
        self.assertTrue(res['interrupted'])
        self.assertEqual('interrupted',res['state'])
        self.assertFalse(res['recovery_required'])
        self.assertEqual([],bridge.status()['pending_approvals'])
        self.assertEqual([],self.dispatched)
        wire=self.wire(bridge)
        self.assertTrue(any(m.get('method')=='turn/interrupt' for m in wire))
        self.assertEqual(['server-1','server-2'],sorted(m['id'] for m in wire if m.get('id') in ('server-1','server-2')))
        # Idempotent when already stopped
        again=bridge.interrupt()
        self.assertFalse(again['interrupted'])


if __name__ == '__main__':
    unittest.main()
