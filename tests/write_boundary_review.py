"""The reviewed records for the registry-write boundary (tests/write_boundary.py).

REVIEWED: every finding of the scan, one entry per occurrence, with the
statement it sits in shown above it. NOT_CRITICAL: the surface's dependencies
left off it, with why. PROTECTED: the reviewed fingerprint of each region of
the critical surface. Change any of them only after reviewing the
code the failing test names: these lists are the review, not a formality."""

from __future__ import annotations

try:
    from tests.write_boundary import Finding
except ImportError:
    from write_boundary import Finding

REVIEWED = {
    # dogear/cli.py: the CLI reads argv and exits
    # import sys
    Finding('dogear/cli.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # dogear/dataset.py: URL parsing; an exception's own constructor
    # from urllib.parse import urlsplit
    Finding('dogear/dataset.py', '', 'import', 'urllib.parse', '-', '7f6d2de520ba06be', 1),
    # super().__init__(f'{len(problems)} dataset problem(s):\n ' + '\n '.join(problems))
    Finding('dogear/dataset.py', 'DatasetError.__init__', 'reflection', "super().__init__(f'{len(problems)} dataset problem(s):\\n  ' + '\\n  '.join(problems))", 'body[0]', 'e4b672f693b011bb', 1),
    # dogear/publish/checks.py: the hosted checks fetch the served site (read only) and run git; urllib.error and urllib.request both bind urllib
    # import socket
    Finding('dogear/publish/checks.py', '', 'import', 'socket', '-', '9df267ed6eef4f61', 1),
    # import ssl
    Finding('dogear/publish/checks.py', '', 'import', 'ssl', '-', 'd58f4a781306aa98', 1),
    # import subprocess
    Finding('dogear/publish/checks.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # import urllib.error
    Finding('dogear/publish/checks.py', '', 'import', 'urllib.error', '-', '532ccfe0e99dc9f7', 1),
    # import urllib.request
    Finding('dogear/publish/checks.py', '', 'import', 'urllib.request', '-', '6e0f76526c6d4c47', 1),
    # import urllib.request
    Finding('dogear/publish/checks.py', '', 'rebinding', 'urllib (import)', '-', '6e0f76526c6d4c47', 1),
    # dogear/publish/cli.py: argv, git and the store constructor (a function named _store); constant getattr defaults; args.store is the CLI's own option; the one Publisher and R2 store, in the checked handoff (_configure, _store)
    # from .publisher import Aborted, NeedsHuman, NotReady, Publisher, PublishedUnrecorded, PublishedUnverified
    Finding('dogear/publish/cli.py', '', 'construct', 'Publisher', '-', '396ca90a510d01c4', 1),
    # from .r2 import R2Store
    Finding('dogear/publish/cli.py', '', 'construct', 'R2Store', '-', 'bd4a357eb72312cd', 1),
    # import subprocess
    Finding('dogear/publish/cli.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # import sys
    Finding('dogear/publish/cli.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # week = getattr(self.args, 'week', None)
    Finding('dogear/publish/cli.py', '_Report._line', 'dynamic', "getattr(self.args, 'week', None)", 'body[0]', 'c5172ef4221622e4', 1),
    # txns = [out.txn] if getattr(out, 'txn', None) else sorted(set(_TXN_ID.findall(out.message)))
    Finding('dogear/publish/cli.py', '_Report.success', 'dynamic', "getattr(out, 'txn', None)", 'body[2]', 'efbcfbb23d59b115', 1),
    # args.store, args.base_url, args.mode, args.policy, args.dataset, args.affinity = (t['store'], t['base_url'], t['m
    Finding('dogear/publish/cli.py', '_apply_target', 'rebind', 'args.store', 'body[1]/body[4]', '4dee8df7c1b1b979', 1),
    # return (Publisher(store, PubData(args.pubdata, committer, saved_reader), StageInputs(args.dataset, args.affinity
    # (the saved-state reader: read-only git fetch/show of the publication data, for the Friday proof read-back)
    Finding('dogear/publish/cli.py', '_configure', 'construct', 'Publisher(store, PubData(args.pubdata, committer, saved_reader), StageInputs(args.dataset, args.affinity, args.base_url, _generator()), policy, checks, clock, mode=args.mode, assets=assets, served=served, separation=separation)', 'body[15]', '7b865c7cc220f771', 1),
    # store, served = _store(args.store, args.base_url, fixed=bool(args.target))
    Finding('dogear/publish/cli.py', '_configure', 'private-name', '_store(args.store, args.base_url, fixed=bool(args.target))', 'body[11]/body[0]', 'abb0bf06dfdd2073', 1),
    # return (R2Store.from_env(spec[3:], allow_endpoint_override=not fixed), http_served(base_url))
    Finding('dogear/publish/cli.py', '_store', 'construct', 'R2Store', 'body[1]/body[0]', 'ca9923f6405045c7', 1),
    # week = dt.date.fromisoformat(args.week) if getattr(args, 'week', None) else None
    Finding('dogear/publish/cli.py', 'run', 'dynamic', "getattr(args, 'week', None)", 'body[4]', '6bb3055cf79d7bc5', 1),
    # dogear/publish/drills.py: test mode: the drill store wraps the store and simulates a concurrent writer
    # self.store, self.drill, self.fired = (store, drill, False)
    Finding('dogear/publish/drills.py', 'DrillStore.__init__', 'rebind', 'self.store', 'body[1]', 'f37b1fafd793f53a', 1),
    # return self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[0]/body[0]', '94f6c183e74372c8', 1),
    # self.store.put(REGISTRY_KEY, current.data + b'\n', content_type=content_type)
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[2]/body[1]/body[0]', '956c5bc46a5db7cf', 1),
    # return self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[2]/body[2]', '94f6c183e74372c8', 1),
    # etag = self.store.put(key, data, content_type=content_type, if_match=if_match, if_none_match=if_none_match)
    Finding('dogear/publish/drills.py', 'DrillStore.put', 'write-call', 'self.store.put', 'body[4]', '0a8a6e4e79031251', 1),
    # dogear/publish/pubdata.py: git, for the publication data checkout
    # import subprocess
    Finding('dogear/publish/pubdata.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # dogear/publish/publisher.py: the gate itself (the only code that touches the raw store), the guarded registry write, artifact uploads through the gate, and the guard's record
    # _GATE_API = frozenset({'get', 'put', 'allow_once'})
    Finding('dogear/publish/publisher.py', '', 'write-name', 'allow_once', '_GATE_API', '1b3d0965b5fbf0d5', 1),
    # _GATE_API = frozenset({'get', 'put', 'allow_once'})
    Finding('dogear/publish/publisher.py', '', 'write-name', 'put', '_GATE_API', '1b3d0965b5fbf0d5', 1),
    # self._presentation_checked: Optional[str] = None
    Finding('dogear/publish/publisher.py', 'Publisher.__init__', 'rebind', 'self._presentation_checked', 'body[10]', 'd89e997de7d3ed82', 1),
    # self.store = RegistryGate(store)
    Finding('dogear/publish/publisher.py', 'Publisher.__init__', 'rebind', 'self.store', 'body[4]', '63c05214d94e2828', 1),
    # self._presentation_checked = None
    Finding('dogear/publish/publisher.py', 'Publisher._quiet_presentation_guard.guard', 'rebind', 'self._presentation_checked', 'body[0]', 'b733f06f8cca7380', 1),
    # self._presentation_checked = intended
    # (the hold-only branch, PUBLISHING §4b: only when _hold_only_guard proves these exact bytes change nothing visible)
    Finding('dogear/publish/publisher.py', 'Publisher._quiet_presentation_guard.guard', 'rebind', 'self._presentation_checked', 'body[1]/body[0]', 'bc6bdb4e589b8422', 1),
    # self._presentation_checked = intended
    Finding('dogear/publish/publisher.py', 'Publisher._quiet_presentation_guard.guard', 'rebind', 'self._presentation_checked', 'body[3]', 'bc6bdb4e589b8422', 1),
    # self.store.allow_once(intended)
    Finding('dogear/publish/publisher.py', 'Publisher._transact', 'write-call', 'self.store.allow_once', 'body[12]/body[3]', 'c340a91b52240e0e', 1),
    # self.store.put(REGISTRY_KEY, new_bytes, content_type='application/json', if_match=expected, if_none_match=expecte
    Finding('dogear/publish/publisher.py', 'Publisher._transact', 'write-call', 'self.store.put', 'body[12]/body[4]/body[0]', '82888df3f5abc82a', 1),
    # self.store.put(key, data, content_type=content_type)
    Finding('dogear/publish/publisher.py', 'Publisher._upload', 'write-call', 'self.store.put', 'body[0]/body[0]/body[2]/body[0]', '9e1dbe0a23c58d26', 1),
    # __slots__ = ('_store', '_allowed')
    Finding('dogear/publish/publisher.py', 'RegistryGate', 'private-name', '_allowed', '__slots__', '64ad30d49d2a3749', 1),
    # __slots__ = ('_store', '_allowed')
    Finding('dogear/publish/publisher.py', 'RegistryGate', 'private-name', '_store', '__slots__', '64ad30d49d2a3749', 1),
    # __slots__ = ('_store', '_allowed')
    Finding('dogear/publish/publisher.py', 'RegistryGate', 'reflection', '__slots__', '__slots__', '64ad30d49d2a3749', 1),
    # return object.__getattribute__(self, name)
    Finding('dogear/publish/publisher.py', 'RegistryGate.__getattribute__', 'reflection', 'object.__getattribute__(self, name)', 'body[1]', '26f41b0de4151247', 1),
    # object.__setattr__(self, '_allowed', None)
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'private-name', '_allowed', 'body[1]', '630312ef05d18ba6', 1),
    # object.__setattr__(self, '_store', store)
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'private-name', '_store', 'body[0]', '05d53cea0b8065b1', 1),
    # object.__setattr__(self, '_allowed', None)
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'reflection', "object.__setattr__(self, '_allowed', None)", 'body[1]', '630312ef05d18ba6', 1),
    # object.__setattr__(self, '_store', store)
    Finding('dogear/publish/publisher.py', 'RegistryGate.__init__', 'reflection', "object.__setattr__(self, '_store', store)", 'body[0]', '05d53cea0b8065b1', 1),
    # object.__setattr__(self, '_allowed', registry_sha)
    Finding('dogear/publish/publisher.py', 'RegistryGate.allow_once', 'private-name', '_allowed', 'body[0]', '16fdb4c5f8504eb1', 1),
    # object.__setattr__(self, '_allowed', registry_sha)
    Finding('dogear/publish/publisher.py', 'RegistryGate.allow_once', 'reflection', "object.__setattr__(self, '_allowed', registry_sha)", 'body[0]', '16fdb4c5f8504eb1', 1),
    # return object.__getattribute__(self, '_store').get(key)
    Finding('dogear/publish/publisher.py', 'RegistryGate.get', 'private-name', '_store', 'body[0]', '2d142db8b34d24dd', 1),
    # return object.__getattribute__(self, '_store').get(key)
    Finding('dogear/publish/publisher.py', 'RegistryGate.get', 'reflection', "object.__getattribute__(self, '_store')", 'body[0]', '2d142db8b34d24dd', 1),
    # allowed = object.__getattribute__(self, '_allowed')
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'private-name', '_allowed', 'body[0]/body[0]', '8714483e247ba201', 1),
    # object.__setattr__(self, '_allowed', None)
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'private-name', '_allowed', 'body[0]/body[1]', '630312ef05d18ba6', 1),
    # return object.__getattribute__(self, '_store').put(key, data, **kwargs)
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'private-name', '_store', 'body[1]', '0ff1beed22fbad5c', 1),
    # allowed = object.__getattribute__(self, '_allowed')
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'reflection', "object.__getattribute__(self, '_allowed')", 'body[0]/body[0]', '8714483e247ba201', 1),
    # return object.__getattribute__(self, '_store').put(key, data, **kwargs)
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'reflection', "object.__getattribute__(self, '_store')", 'body[1]', '0ff1beed22fbad5c', 1),
    # object.__setattr__(self, '_allowed', None)
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'reflection', "object.__setattr__(self, '_allowed', None)", 'body[0]/body[1]', '630312ef05d18ba6', 1),
    # return object.__getattribute__(self, '_store').put(key, data, **kwargs)
    Finding('dogear/publish/publisher.py', 'RegistryGate.put', 'write-call', "object.__getattribute__(self, '_store').put", 'body[1]', '0ff1beed22fbad5c', 1),
    # dogear/publish/r2.py: the R2 transport: one GET and one PUT; from_env names its own class
    # import http.client
    Finding('dogear/publish/r2.py', '', 'import', 'http.client', '-', '6c915fe19718932a', 1),
    # import socket
    Finding('dogear/publish/r2.py', '', 'import', 'socket', '-', '9df267ed6eef4f61', 1),
    # import ssl
    Finding('dogear/publish/r2.py', '', 'import', 'ssl', '-', 'd58f4a781306aa98', 1),
    # from urllib.parse import quote, urlsplit
    Finding('dogear/publish/r2.py', '', 'import', 'urllib.parse', '-', 'f3d6af1e804d6291', 1),
    # @classmethod def from_env(cls, bucket: str, environ: Optional[dict]=None, *, allow_endpoint_override: bool=False,
    Finding('dogear/publish/r2.py', 'R2Store.from_env', 'construct', 'R2Store', '', '9e7cff8e8f1c78d4', 1),
    # status, headers, data = self._request('GET', key)
    Finding('dogear/publish/r2.py', 'R2Store.get', 'write-call', 'self._request', 'body[0]', '2c9474fd159b344d', 1),
    # status, headers, body = self._request('PUT', key, bytes(data), extra)
    Finding('dogear/publish/r2.py', 'R2Store.put', 'write-call', 'self._request', 'body[3]', '767de7e1197aece9', 1),
    # dogear/publish/staging.py: exceptions' own constructors, and the page parser's
    # super().__init__(f'{week}: ' + '; '.join(problems))
    Finding('dogear/publish/staging.py', 'StageRefused.__init__', 'reflection', "super().__init__(f'{week}: ' + '; '.join(problems))", 'body[0]', '943e1394c60cd752', 1),
    # super().__init__(week, problems)
    Finding('dogear/publish/staging.py', 'WeekHeld.__init__', 'reflection', 'super().__init__(week, problems)', 'body[0]', 'f139b8521720e8af', 1),
    # super().__init__(convert_charrefs=True)
    Finding('dogear/publish/staging.py', '_PageValues.__init__', 'reflection', 'super().__init__(convert_charrefs=True)', 'body[0]', '2cb1a00e4a7ad3c5', 1),
    # dogear/publish/store.py: the in-memory test store names the operation for its fault hook
    # action = self._fault('put', key, data)
    Finding('dogear/publish/store.py', 'MemoryStore.put', 'write-name', 'put', 'body[1]', 'ecfc3197db42b3ba', 1),
    # dogear/synthetic.py: URL parsing; argv, imported again inside its __main__ block
    # import sys
    Finding('dogear/synthetic.py', '', 'import', 'sys', '-/body[0]', 'f7252e3c1dbe1d5e', 1),
    # from urllib.parse import urlsplit
    Finding('dogear/synthetic.py', '', 'import', 'urllib.parse', '-', '7f6d2de520ba06be', 1),
    # import sys
    Finding('dogear/synthetic.py', '', 'rebinding', 'sys (import)', '-/body[0]', 'f7252e3c1dbe1d5e', 1),
    # dogear/web.py: builds its font list (_FONTS) in a module-level loop
    # _FONTS += [('Newsreader', f'newsreader-latin-wght-{_style}', _style, _FONTS[0][3]), ('Newsreader', f'newsreader-l
    Finding('dogear/web.py', '', 'rebinding', '_FONTS (augassign)', '-/body[0]', 'e32384c8837729b8', 1),
    # for _style in ('normal', 'italic'):
    Finding('dogear/web.py', '', 'rebinding', '_style (for)', '-', 'dee1b3ad76a31592', 1),
    # tools/chain_check.py: the chain check opens a TLS connection (read only); a constant hasattr
    # import socket
    Finding('tools/chain_check.py', '', 'import', 'socket', '-', '9df267ed6eef4f61', 1),
    # import ssl
    Finding('tools/chain_check.py', '', 'import', 'ssl', '-', 'd58f4a781306aa98', 1),
    # import sys
    Finding('tools/chain_check.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # chain = tls.get_verified_chain() if hasattr(tls, 'get_verified_chain') else []
    Finding('tools/chain_check.py', 'check', 'dynamic', "hasattr(tls, 'get_verified_chain')", 'body[4]/body[0]/body[1]', '774930b0be8ab593', 1),
    # tools/editorial_gate.py: runs the editorial checks in a child python
    # import subprocess
    Finding('tools/editorial_gate.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # import sys
    Finding('tools/editorial_gate.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # checks = [('editorial tests', [sys.executable, '-m', 'unittest', 'discover', '-s', str(editorial / 'tests')], edi
    Finding('tools/editorial_gate.py', 'main', 'reflection', 'sys.executable', 'body[8]', '8b178c12833c4d39', 1),
    # checks = [('editorial tests', [sys.executable, '-m', 'unittest', 'discover', '-s', str(editorial / 'tests')], edi
    Finding('tools/editorial_gate.py', 'main', 'reflection', 'sys.executable', 'body[8]', '8b178c12833c4d39', 2),
    # checks = [('editorial tests', [sys.executable, '-m', 'unittest', 'discover', '-s', str(editorial / 'tests')], edi
    Finding('tools/editorial_gate.py', 'main', 'reflection', 'sys.executable', 'body[8]', '8b178c12833c4d39', 3),
    # checks = [('editorial tests', [sys.executable, '-m', 'unittest', 'discover', '-s', str(editorial / 'tests')], edi
    Finding('tools/editorial_gate.py', 'main', 'reflection', 'sys.executable', 'body[8]', '8b178c12833c4d39', 4),
    # tools/firmware_contract.py: the sync reads GitHub (GET only) and the firmware clone with git
    # import subprocess
    Finding('tools/firmware_contract.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # import sys
    Finding('tools/firmware_contract.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # import urllib.request
    Finding('tools/firmware_contract.py', '', 'import', 'urllib.request', '-', '6e0f76526c6d4c47', 1),
    # tools/route_vectors.py: an in-memory store for the route test vectors
    # import sys
    Finding('tools/route_vectors.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # store.put(key, body.encode('utf-8'), content_type='application/octet-stream')
    Finding('tools/route_vectors.py', 'build', 'write-call', 'store.put', 'body[2]/body[1]/body[0]', 'b4fd343a82dd2940', 1),
    # tools/run_publish.py: runs the publisher CLI in a child python
    # import subprocess
    Finding('tools/run_publish.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # import sys
    Finding('tools/run_publish.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # argv = [sys.executable, '-m', 'dogear.cli', 'publish', '--target', target, '--pubdata', pubdata]
    Finding('tools/run_publish.py', 'build_argv', 'reflection', 'sys.executable', 'body[5]', '3d81489ac3daacdf', 1),
    # tools/staging_fixture.py: argv
    # import sys
    Finding('tools/staging_fixture.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
}

# Package names the surface uses that are not on it, each reviewed as unable to
# change what a write makes visible, eligibility, quiet presentation or the capability.
NOT_CRITICAL = {
    ('dogear/cli.py', 'DEFAULT_AFFINITY'):
        'used only to refuse --affinity alongside --target; TARGETS (protected) fixes the paths',
    ('dogear/cli.py', 'DEFAULT_DATASET'):
        'used only to refuse --dataset alongside --target; TARGETS (protected) fixes the paths',
    ('dogear/publish/cli.py', 'FONTS'):
        'the font files uploaded beside the web page: no check, record or selection reads them',
    ('dogear/publish/publisher.py', 'Guard'):
        'the guard type alias, for annotations only',
    ('dogear/publish/staging.py', 'FitChecker'):
        'a checker type alias, for annotations only',
    ('dogear/publish/staging.py', 'LinkChecker'):
        'a checker type alias, for annotations only',
}

PROTECTED = {
    ('dogear/publish/publisher.py', '<imports>'): '3e162395e5775f37e47ae9558cec01ea4f39535ef7d2927a6c73a44befb8c6f9',
    ('dogear/publish/publisher.py', '<bindings>'): 'f43142901c9db276ccd0cfdef3e9b9aeba40ac34efc5fe5d4fcc87e7cf2df89d',
    ('dogear/publish/publisher.py', 'UnguardedRegistryWrite'): '4cbf0b7fc843cc13351051a8c85fc5a6c4cd1dfb7eabe48afb0f185b1f455dec',
    ('dogear/publish/publisher.py', 'is_registry_key'): 'a497c31f375b0a09c973b5556ffb1115e25eddcc01bb4f6c34414ed4a972393f',
    ('dogear/publish/publisher.py', '_GATE_API'): '1b3d0965b5fbf0d5626933b05563ed69496801ed2db1aa2c77bb855945c2d2f0',
    ('dogear/publish/publisher.py', 'RegistryGate'): 'a1bdab7bb76cafe21a948d0e9ac3922d587581fecef48ce9af0694e450da9012',
    ('dogear/publish/publisher.py', 'Publisher.__init__'): 'c2127b1b4f653332dc03352fc3a6cc3c59b222cbef32c3f3aef953b061f35e62',
    ('dogear/publish/publisher.py', 'Publisher._quiet_capability'): 'fc6161c4679e61c0edd9f36af5e540d6ef1ef69a9fa9eac6f9e3a9208b2a62fb',
    ('dogear/publish/publisher.py', 'Publisher._revision_is_quiet'): 'eb07e4c5b7c90992fa5025f1e9d20a73dbbb3ef5f3352c6fd7f5021e368832b6',
    ('dogear/publish/publisher.py', 'Publisher._quiet_presentation_guard'): '62072863b34e5a77b0938e4239a1576e90a7b9a578eb4de2a5f2bea66aee82b6',
    ('dogear/publish/publisher.py', 'Publisher._quiet_shown_without_capability'): 'f006238ae2660e89a7e8e14db96fe4c53c8c75539fd83c628b89c8fafac322a5',
    ('dogear/publish/publisher.py', 'Publisher._all'): 'd4836d66c03b9eb1f87bde0c7bd60d13b58d05c1940e02e89496d92b965d798c',
    ('dogear/publish/publisher.py', 'Publisher._upload'): '9a73b39d2760a398d922d3f8da234bb9d1b0e1d07a6f2b476362f17ec27d8e17',
    ('dogear/publish/publisher.py', 'Publisher._transact'): '5dd65dfc5df0094980d396e7a427637c32c4afa3bf697c32c9d736894a5bd3a5',
    ('dogear/publish/publisher.py', 'Publisher._control'): '29203a034dc55c2f6960ee5843b9155b99f45d6691e8e3eb82ee388caec25769',
    ('dogear/publish/publisher.py', 'Outcome'): '92c9697a41c53711fdfe5772995f9bdaa9b36a90ee9db12955c1018d759285e6',
    ('dogear/publish/publisher.py', 'PublishedUnverified'): 'e1d35ecc5e03eda7c87c1e89bfba342b93afc13c2b32915d72b0a30ded0d56b5',
    ('dogear/publish/publisher.py', 'PublishedUnrecorded'): '706853fb00cb8cdf121697b557e8bd7a9dba55675de4af11be9cfb136a4ebfee',
    ('dogear/publish/publisher.py', 'Publisher._registry_state'): '4f891753b1ee358ed1b67427fa3d1c7fac70e49bb1b6899fb0fce07aa9c26f34',
    ('dogear/publish/publisher.py', 'Publisher._committed'): 'feb2322f529c295bb7c1a9d0f92b836c5307d36283ca2b8f9e60345df4c50bc0',
    ('dogear/publish/publisher.py', 'Publisher._stop'): '63c40d33326ab6b63bd75b0901d8669f008b1917907d7c74b42609f6b8d93333',
    ('dogear/publish/publisher.py', 'Publisher._unresolved'): 'c12a101c0f092c8db45da053f97a2f252ef2a8225cce31447cf76c0395ba53a6',
    ('dogear/publish/publisher.py', 'Publisher._carries'): 'ddb87a75a69a259e654397a0c7752cf71994207efcae89beef6e02e6ddf7ac14',
    ('dogear/publish/publisher.py', 'Publisher._finalize'): '38103cbe71124366af8f681d3bb979a7ffcf7a53140cb5681bba5f2ca7d350f5',
    ('dogear/publish/publisher.py', 'Publisher._abandon'): '2468b0d129f25c54d44a68bb3b66dcec1c4551c12fb88f8d1284b5225eb8d5cb',
    ('dogear/publish/publisher.py', 'Publisher._settle_pending'): '4eb53db4246f5ebbb9e7e746b0498a3f648e0854c46957243db06568a350e767',
    ('dogear/publish/publisher.py', 'Publisher.reconcile'): 'bd92a79a3596ab39e96c9cd26ebd8bf9860232e16919982d5e1c62f2bb2fb9b7',
    ('dogear/publish/publisher.py', 'Publisher.abandon'): '56eacd2ebe8c296438a08ef2efb87e5056543046789cdb3ad2d9f38908291a96',
    ('dogear/publish/publisher.py', 'Publisher._history_entry'): 'fa2901d9fc6685052cf6021282528ca99158cd9e1eefd75a75ece5476b15c170',
    ('dogear/publish/publisher.py', 'verification_targets'): '6c2587d12739970f7f5105ebd8d25b441edd6ffbd0a459ddde0e2972b80fc058',
    ('dogear/publish/publisher.py', '_expected_targets_problem'): '0a1d57f80a4dad1ffcf34c6640657624acefd5f6f6948540717eaeb031e3287f',
    ('dogear/publish/publisher.py', 'Publisher._outstanding'): 'bc5ccf37b2b89978b7d360d34de305e739ccb15f25acf6b29bd7073d9590989d',
    ('dogear/publish/publisher.py', 'Publisher._verify_outstanding'): 'be468038abc81d21812bc424661f903db68e089ef7ac006c0db320339db78952',
    ('dogear/publish/publisher.py', 'Publisher._check_target'): '835a76a0c9d03ce98f083a04c825abc8abb6294bf75f4df039fdbf526ca78aab',
    ('dogear/publish/publisher.py', 'Publisher._save_quietly'): '35c0b4203ec71bbfbfcd349d89650392a2495111b3ebc0a22f046d02669d1dee',
    ('dogear/publish/publisher.py', 'HoldNotProven'): '78cf4390efaa303a50e88dba4487da1dfb4c6d480a52ef73477842b38970cdd0',
    ('dogear/publish/publisher.py', '_hold_only_guard'): '4afde189eda209e7e21e0e0709f2b5986efee373953fc0869c5556469ce2d04e',
    ('dogear/publish/publisher.py', 'expected_verify'): 'ae47688dcc6dec01e72bb563f98e647b7646e9ba26b48174a73023b7e15739bf',
    ('dogear/publish/publisher.py', 'Publisher._committed_hold'): 'b6cddcf15f170c287108c2e9b7fd3ebbcd4dac795eb6d96dee1116acf5bbb673',
    ('dogear/publish/publisher.py', 'Publisher.hold'): '3d8dcd973fcdeb8fd1fcf2375a02b737c0982fa9a29e04b00d55449e7196a0d6',
    ('dogear/publish/publisher.py', 'Publisher._hold'): 'db7e05572f03b9ce4b8c1a2a5e8b48087d2af02cb54d1cd457965e18e6899cf9',
    ('dogear/publish/publisher.py', 'AUTO_HOLD_CODES'): '0ae5bf4ce49593514b3ae586dbab4efd5aef39311a79de898ef8a0f3a800ae6e',
    ('dogear/publish/publisher.py', 'STAGE_CODES'): 'f8492a9d1f29a01b3611500f862d35e4a78fd461d833f793b119cfc448cd6419',
    ('dogear/publish/publisher.py', 'HOLD_CODES'): '0d4e5181afe78edfba239622fe92509911932109515be3e0455470fc43287ea0',
    ('dogear/publish/publisher.py', 'Publisher.stage'): 'c9c0c8c4fdb2cd22f8dc4ab8d58014f50676af149d2c9a94c78491c149b47040',
    ('dogear/publish/publisher.py', 'Publisher.stage_next'): '43264e3e1e5b67d85a629ab5db982077001c74cfaa8f124cd99408db46551646',
    ('dogear/publish/publisher.py', 'Publisher._saved_proof_problem'): '8fd980998998ff039dc7a902a763d5268eb272dafa1ec9d5055bdbf94b836f4d',
    ('dogear/publish/publisher.py', 'Publisher._stage_failed'): '39094c81c62170eee639df85ff00cc71e54837bc3aa21de1ec4222257c8f3edb',
    ('dogear/publish/publisher.py', 'Publisher._proof_problem'): '5c12e4057319ab68fca455eabcfdd0e76dd078aa7620d6dab3b4e15f5d0a3d83',
    ('dogear/publish/publisher.py', 'Publisher._persisted_proof'): 'd9c80e6cf41a54a463a21bb530db768e6a256c5666b3dd0d7087859304479287',
    ('dogear/publish/publisher.py', '_UNREADABLE_PROOF'): 'e1e702839998409cdbbe3718d35ede1e795162816bacb2a69fcebfa5da49f082',
    ('dogear/publish/publisher.py', 'shown_revisions'): 'fa792f643fa86ea6362e0db57cf3d1e2c8e2a9c7f7a4138fbf8b079f07fe1b9c',
    ('dogear/publish/publisher.py', 'Publisher._read_registry'): 'b83bef519f6b2a3427004b873da8cac9c29d563fd0a7a964633a5a2bdb432188',
    ('dogear/publish/publisher.py', 'Publisher._dataset_text'): '84cac62afb8cc2c0f3b8d777e587d716e19244ca14d389633c041403b82d45c2',
    ('dogear/publish/publisher.py', 'Publisher._week_guard'): 'ade2c10c26c856a5775ed9dfddfc120cb38f3fdf652c82c47e93b3c7c14718f1',
    ('dogear/publish/publisher.py', 'Publisher._separation_problem'): 'bc26cc80eb70fedeb0694af75e26638b690d31dbf66fb5c416b1fac659ee53a7',
    ('dogear/publish/publisher.py', 'Publisher._eligibility_guard'): '4870e55b3f4bf3889e88cb13bd2338da9879268a9349875ef495a3c801d72984',
    ('dogear/publish/publisher.py', 'Publisher._visible_revisions_guard'): 'ad848dd4960c1d6fdf5c8736e44d4bb5d62e1c8fbe20721c52834ebfb94c9289',
    ('dogear/publish/publisher.py', 'Publisher.promote'): '58e9d7a0d37b258c2460ce36973fc8a5984ce5ec232d116b20a0620ddb5cc994',
    ('dogear/publish/publisher.py', 'Publisher.launch'): '356f6c6606024e9d769f53a8cd173a81166cbcdb3bbe7ce0a6b1d3e07bdca899',
    ('dogear/publish/publisher.py', 'Publisher.correct'): 'cdcf63f7d30301ca124eda5e12b7f5312229f3cde4efcc00e82fccb2489e77db',
    ('dogear/publish/publisher.py', 'Publisher.rollback'): '69e2921915b86728d3601e99c36152db4833845a87e4346faca57d462f7ca3ed',
    ('dogear/publish/publisher.py', 'Publisher.restore'): 'b38bd7d8d9297d8c26d34e8bce20178171d63356d3c152167112855152786c86',
    ('dogear/publish/publisher.py', 'Publisher.resume'): '8acb4571713561599398ca7b1304b11b7575e0cc1e4841f30f60f06c06af77d8',
    ('dogear/publish/publisher.py', 'Publisher._ready'): 'e225ffa79d5ae347859c172469587ac38118618f28a302e71dcc170396892a5c',
    ('dogear/publish/publisher.py', 'NotReady'): '2589fc30f206a08ffe18f5af589289b33bf0f42647a8dec4790c463a5b85580b',
    ('dogear/publish/publisher.py', 'Aborted'): '02d293d15be0b52ded912625f5a11fb9eb23953e9b0677f33539851a98b7a12b',
    ('dogear/publish/publisher.py', 'NeedsHuman'): '760266d640f3a442efe6428c899842b016aa02b6c3c13b20c47fd26738aa5506',
    ('dogear/publish/policy.py', ''): '718e0d4cfd76b64912c7549317258dd1949fbf9e20f3fc97b54f6a7ec0351204',
    ('dogear/publish/publisher.py', 'rollover_week'): '2b5a2373e68c4532f29e82459888373b1ae419fa79ba7f18b5b4365000999580',
    ('dogear/publish/publisher.py', 'ROLLOVER'): '429d8755d3bca6d7f435d8027d79c2e95084e33f3982031612cc827ab31ab599',
    ('dogear/publish/registry.py', ''): 'b78c343f27443d4f57ec45dd25d4613f7307d679d6a81ea91272c484584bf9b2',
    ('dogear/publish/eligibility.py', ''): '0975d75936707113959f78362a6fc4820f2856baeedad8287a7f6871c01d31f5',
    ('dogear/week.py', ''): '4087294013e6a65914a3387e8607b2efa5331f1ddfd0926b6f538b720eecc9b9',
    ('dogear/dataset.py', ''): 'cefed2cff624963c45d876a166ef6e7f346f7dc6107bd39bfe90a719d6035b48',
    ('dogear/affinity.py', ''): 'f12f6045be75567e9492b6bdd1551e019326ddf894a49eefec0bc1ffddbb4688',
    ('dogear/publish/edition.py', ''): '06c4032e3c90292c94b7a176725df4089dd718707a8c9623ae2f0daafee9dfe3',
    ('dogear/synthetic.py', ''): '9298499ee140b9ba1e456a910dd3b265bd88165489ff3fa9dc29c896be944fe3',
    ('dogear/quiet.py', ''): '75727c0a54d4e9958e0d67937250a4476b30d979d2d2592689534044bb99128d',
    ('dogear/select.py', ''): '6b5d1449d6b7d092a0c5d820e501f538beefd5534f832a584c4d7f8f14a93b53',
    ('dogear/issue.py', ''): '040916b6d699de39bc02811be8369ae9e4ab3d8c6233b1e1e417bcd057e523a0',
    ('dogear/web.py', ''): 'f9ff5edbc695f3d7c2cc147811cfd94233f1e895084c9acc4ca49a0cbd99b1f0',
    ('dogear/publish/checks.py', ''): '5b6f5ff4a9125ead387bbcc9d6921501de650408b3fff4c50272c39e79e748df',
    ('dogear/publish/routes.py', ''): 'a25019c047bd898289762d45dfd6782fb803894894697125f1809c2f95a5c15d',
    ('mock/render_dogear.py', ''): '5763eaffee3691e999a26db8c39baf23b32b18481e5563283f0bd088e49a4355',
    ('dogear/publish/publisher.py', 'Publisher._quiet_source'): 'dd0bbf2bea4fb7c527adfc28f3c4d6f1ff4b1c8f59ccab5a9a0af53be0e37ed5',
    ('dogear/publish/quiet_history.py', ''): 'cb19b7a56af8ad4398a104be5559393bc2937b41f1ef7905768573ffbd4a50c5',
    ('dogear/publish/pubdata.py', ''): 'c6a9a50656bfb257bd792992d212efc92efcf841804014d8eb3404a0fa640409',
    ('dogear/publish/staging.py', '<bindings>'): '52108442ac9b724b2b6d8e11f8c1a25dfa587b540765884ef76de92ee586803f',
    ('dogear/publish/publisher.py', 'Publisher._stage'): '7efdc9c380eacbb35839614be878edfb703ac3710b79a227f5d123080710bf82',
    ('dogear/publish/publisher.py', 'Publisher._uploads'): '4c387adafce21110a9cc13fe14f1f04ed51e976f668533bc109a362baa6fb14b',
    ('dogear/publish/publisher.py', 'Publisher._history'): '69731475d3e16c36c4701f5a14a3395ebce86ab9d86b76443e9b73df94c33c6b',
    ('dogear/publish/publisher.py', 'Publisher._write_staged'): '6003e83628719d859fae046a6ea1396d10138ee4cb1d18188b0b1cf438c9d688',
    ('dogear/publish/staging.py', 'stage'): 'f429852bee3e9ba3c83df233f2a9229ded997cd04d2a26ae167f1eaf93cc0a4c',
    ('dogear/publish/staging.py', 'freeze'): '5742b77284efa26df947ba5a16a0a513f093b43eb28955d4db427cb876faad65',
    ('dogear/publish/staging.py', 'generate'): '0fa0a6c37b066ab25f7b634c2c17bf6b954720859c4e11a5ac766c789e9afda1',
    ('dogear/publish/staging.py', 'quiet_occurrence'): '8004991e9284a1b8ec0ee64de256e6ea449d40ac70e0217bd3a02d29bb4f4c6c',
    ('dogear/publish/staging.py', 'run_checks'): 'a3f0318e54778f970602229f2feb80b0a56bda0842f41fab9edce04263d26782',
    ('dogear/publish/staging.py', 'external_links'): '39eb925f90c0b8b3b3b66c9660d4716e0a0305334a43df174912a00baf358acf',
    ('dogear/publish/staging.py', 'LinkReport'): '47839a3f5ce299745612442cc5583ae100a8e094d18dac9ad3ee10a9f692921e',
    ('dogear/publish/staging.py', 'shown_link_problems'): '367eb568b0a65093eb04d873792fff17fc8310a5a94c4add0047b6a245a675bb',
    ('dogear/publish/staging.py', '_URL'): 'cd5edee01a0cc1188815ec624b3c6227f1646f039d29456e88cf791ba542cdac',
    ('dogear/publish/staging.py', '_PageValues'): '0170118b0d7df5e4b588177758713e1ecb30108dbd3b7637a63345fe70a80897',
    ('dogear/publish/staging.py', '_RELATIVE'): '5874780ca51f8844fc57313c5395fce4eef72c33800832c8b3ec30755f36aef9',
    ('dogear/publish/staging.py', '_LINK_ATTRS'): 'fad4bb10b7f8ec5d00533ecbea12ffa99a4130e5ac7a2af749920d28d6ac1c92',
    ('dogear/publish/staging.py', '_REFUSED_TAGS'): 'dfb19b2d8f44c8554a7a42ebd4ff07ad835a8efe76eacfd0695bd8ab79186387',
    ('dogear/publish/staging.py', '_read'): '3d77c69b98b962c578b8afa82a853ef3fb7a8ab2cd6a2b0a3ee0c5aed70b445a',
    ('dogear/publish/staging.py', 'verify_staged'): '95c078cfacb816dcd2e302a599e90cfa55ffc955036303e3eaacc7f4d775e210',
    ('dogear/publish/staging.py', 'Staged'): '4081b879038ffc371a42d35df3d375481c13c372735108194a869311098ef21a',
    ('dogear/publish/staging.py', 'StageInputs'): '6066bcd07918e93a1d0cffcfdb1376c3054660ea6f4df871edab6090f31b5052',
    ('dogear/publish/staging.py', 'Frozen'): 'fe8d7e1580d849f9f8f7ad1baa04b2864e19f31e338748a6f51421cef212e161',
    ('dogear/publish/staging.py', 'StageRefused'): '94d20dd9c1b87bbe4b076832bad84f0e0879aacb36e0014637d5ddbfcc74147e',
    ('dogear/publish/staging.py', 'WeekHeld'): 'a87f31c649a9a2ee6df7e32897bf2fba746fc4c8339d7b73e08f3dc0327528f3',
    ('dogear/publish/staging.py', '_raw_records'): '3fe95fee58a999945baaada1c8d790ee682282765b9821a1fed0547098f07d65',
    ('dogear/publish/staging.py', 'artifact_problems'): '30cac53a4f94a8682993503302525cf3fda58ad456bee3cc7b4071ab47a9d2ff',
    ('dogear/publish/staging.py', 'device_limit_problems'): '26d60361eab60aa269c7639635dd89ccb538386d8354bb9005debc1a72ca48ac',
    ('dogear/publish/staging.py', 'ALLOWED_CTA'): 'a64133778baa898e3f883fe95c4c4cfe458faf0a8fbf5d6b7b2cad83988f3fd3',
    ('dogear/publish/staging.py', 'MAX_ENTRIES'): '45c16c6719db770c285141661468ab4f445940b967a4db96193a0ff4372c9bf3',
    ('dogear/publish/staging.py', 'provenance_bytes'): 'f83f0dcbbcdcd282554984a9a1b22dc26ed2aece59fba13df93c91ef74f6a12c',
    ('dogear/publish/staging.py', 'Checks'): '35b07399afe917c0a41b38de46c569e41cda53f451ce941d828cd1d74bd099a1',
    ('dogear/publish/firmware.py', ''): '85084c2954763f62957c332b6ea489f91cc368c4e9084f79a0e8222ae05e90c2',
    ('dogear/publish/r2.py', ''): '4afa501dcb1fc56454c277eabf009e51b4d11993d6c30759e7cdaacb62f28aa2',
    ('dogear/publish/store.py', ''): 'ff1b9479259d27e55d008db7d74feca7a30ec040f111fcccece0c22c8257c233',
    ('dogear/publish/drills.py', ''): '6330a81c0a1cf4477c0ee057ef5ece5c909ef77364ac36dbbf0858958b3f6e51',
    ('dogear/publish/cli.py', '<bindings>'): '96f8a2608e0f399254426afb94950a59c797b8f3dd1a3201dc7e9d620acaede7',
    ('dogear/publish/cli.py', 'ROOT'): '54984a8c5ee3afca40c43588fccd6a196361c5bd0f3a143eae021f3097225f2c',
    ('dogear/publish/cli.py', 'PRODUCTION_HOST'): '91b9ff873fafee6cb0a26c3db0393c8f8e28db71ead754690447802631cb2296',
    ('dogear/publish/cli.py', 'TARGETS'): '81c0748349f6c031e716872c6aefe5dc0964a22617cd9e6fe9f8487f7b4fd7c5',
    ('dogear/publish/cli.py', '_generator'): '7b4fa4abaca8a36190ce3c984a51e45c16a49f0509c576f90ce2da4b6d108fe0',
    ('dogear/publish/cli.py', '_store'): 'ee595279707e3991523fd044b0ad279b9fd10d5eee412603d9635adc238676ef',
    ('dogear/publish/cli.py', '_apply_target'): '0b8c75dafb4a622648786dec51a3b6b06f871f5ef1cbcdf7c1fc6f6af3928627',
    ('dogear/publish/cli.py', '_pairing_problem'): '6e3d2c9f0db2558ce01e86fe65975bcaaceff5b533763f4c0100b4c8beed51e8',
    ('dogear/publish/cli.py', '_configure'): '27af5df741ba796c962cc7d4b5ced1a6d851776b8720e9ec56abd38d6a864f88',
    ('tests/write_boundary.py', ''): 'ac518fcd2719ed812f349bb3b9195320d479261ac407eda0d54cb91a484ba079',
    ('tests/test_firmware_capability.py', 'RegistryWriteBoundaryTests'): 'c4a0b639040ff82ba7ca910f93bdc8a3cb0cf449f3e3ece6de7377789bcda876',
    ('tests/test_firmware_capability.py', 'SourceReviewGuardTests'): 'f081a01b5abf841fd514636ad2c522a530d2dc59b1e72575a65a61ffa59c9da7',
    ('tests/test_firmware_capability.py', 'BindingAndSurfaceTests'): 'b30dc384ffcda76b0d2c1a37f1b5ed0f8d55ee1458d149dc8e96f65c5ecc1b0d',
    ('tests/test_firmware_capability.py', 'NESTED_REBINDING'): 'e645e614c14c3c79ea97acd4705b469cdaf13d4dab50971519b4924689cf0b63',
    ('tests/test_firmware_capability.py', 'LAMBDA_REBINDING'): 'bb7893ac6cc38e35e83e6e16adfb2b8ad8811b44dc01153a0d899299ad571492',
    ('tests/test_firmware_capability.py', 'CliHandoffTests'): '2db490a4454289face410dca4a08057a94f89bb07efead88aebeedf6e0d1bdd2',
    ('tests/test_firmware_capability.py', 'QuietVerificationSurfaceTests'): '42fb997cb831a730230a7eb9bc0079a402406a6b0bea6639a5f54d27cc2e44aa',
    ('tests/test_firmware_capability.py', 'PublicationInvariantTests'): '78927ea9351e52d8f9c64960940814228e42e8297121b91a9e31c3655e2bb9cb',
    ('tests/test_firmware_capability.py', 'BYPASS'): '360bf4738a179e6101e83bb258dc1a735e478a18912482e0180f474463bab73e',
    ('tests/test_firmware_capability.py', 'QUALIFIED_BYPASS'): '79a282624e5670903ba58f896ab77ebb03a021b6a3fb9768f2fea351559a3fd9',
    ('tests/test_firmware_capability.py', 'BYPASS_VARIANTS'): '8cc6673364fe095c2f8b83c41850280d97366bd31377ba10c21f86f76bfbae43',
    ('tests/test_firmware_capability.py', 'NESTED_WRITER'): 'ab430d05252c7b86e244c9af7f55d9216fdb440add1bd098cea6e122891c8308',
    ('<surface>', ''): 'ce24a1d9120fafebda9b8041e99026b39d69e222b43eacb2923c7be099315e59',
}
