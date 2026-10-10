"""The reviewed records for the registry-write boundary (tests/write_boundary.py).

REVIEWED: every finding of the scan, one entry per occurrence, with the
statement it sits in shown above it. PROTECTED: the reviewed fingerprint of
each region of the critical surface. Change either only after reviewing the
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
    # dogear/publish/checks.py: the hosted checks fetch the served site (read only) and run git
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
    # dogear/publish/cli.py: argv, git and the store constructor (a function named _store); constant getattr defaults; args.store is the CLI's own option
    # import subprocess
    Finding('dogear/publish/cli.py', '', 'import', 'subprocess', '-', '9d97d469cf8d6646', 1),
    # import sys
    Finding('dogear/publish/cli.py', '', 'import', 'sys', '-', 'f7252e3c1dbe1d5e', 1),
    # week = getattr(self.args, 'week', None)
    Finding('dogear/publish/cli.py', '_Report._line', 'dynamic', "getattr(self.args, 'week', None)", 'body[0]', 'c5172ef4221622e4', 1),
    # txns = [out.txn] if getattr(out, 'txn', None) else sorted(set(_TXN_ID.findall(out.message)))
    Finding('dogear/publish/cli.py', '_Report.success', 'dynamic', "getattr(out, 'txn', None)", 'body[1]', 'efbcfbb23d59b115', 1),
    # args.store, args.base_url, args.mode, args.policy, args.dataset, args.affinity = (t['store'], t['base_url'], t['m
    Finding('dogear/publish/cli.py', '_apply_target', 'rebind', 'args.store', 'body[1]/body[4]', '4dee8df7c1b1b979', 1),
    # week = dt.date.fromisoformat(args.week) if getattr(args, 'week', None) else None
    Finding('dogear/publish/cli.py', 'run', 'dynamic', "getattr(args, 'week', None)", 'body[13]', '6bb3055cf79d7bc5', 1),
    # store, served = _store(args.store, args.base_url, fixed=bool(args.target))
    Finding('dogear/publish/cli.py', 'run', 'private-name', '_store(args.store, args.base_url, fixed=bool(args.target))', 'body[9]/body[0]', 'abb0bf06dfdd2073', 1),
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
    Finding('dogear/publish/publisher.py', 'Publisher._quiet_presentation_guard.guard', 'rebind', 'self._presentation_checked', 'body[2]', 'bc6bdb4e589b8422', 1),
    # self.store.allow_once(intended)
    Finding('dogear/publish/publisher.py', 'Publisher._transact', 'write-call', 'self.store.allow_once', 'body[11]/body[3]', 'c340a91b52240e0e', 1),
    # self.store.put(REGISTRY_KEY, new_bytes, content_type='application/json', if_match=expected, if_none_match=expecte
    Finding('dogear/publish/publisher.py', 'Publisher._transact', 'write-call', 'self.store.put', 'body[11]/body[4]/body[0]', '82888df3f5abc82a', 1),
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
    # dogear/publish/r2.py: the R2 transport: one GET and one PUT
    # import http.client
    Finding('dogear/publish/r2.py', '', 'import', 'http.client', '-', '6c915fe19718932a', 1),
    # import socket
    Finding('dogear/publish/r2.py', '', 'import', 'socket', '-', '9df267ed6eef4f61', 1),
    # import ssl
    Finding('dogear/publish/r2.py', '', 'import', 'ssl', '-', 'd58f4a781306aa98', 1),
    # from urllib.parse import quote, urlsplit
    Finding('dogear/publish/r2.py', '', 'import', 'urllib.parse', '-', 'f3d6af1e804d6291', 1),
    # status, headers, data = self._request('GET', key)
    Finding('dogear/publish/r2.py', 'R2Store.get', 'write-call', 'self._request', 'body[0]', '2c9474fd159b344d', 1),
    # status, headers, body = self._request('PUT', key, bytes(data), extra)
    Finding('dogear/publish/r2.py', 'R2Store.put', 'write-call', 'self._request', 'body[3]', '767de7e1197aece9', 1),
    # dogear/publish/staging.py: exceptions' own constructors
    # super().__init__(f'{week}: ' + '; '.join(problems))
    Finding('dogear/publish/staging.py', 'StageRefused.__init__', 'reflection', "super().__init__(f'{week}: ' + '; '.join(problems))", 'body[0]', '943e1394c60cd752', 1),
    # super().__init__(week, problems)
    Finding('dogear/publish/staging.py', 'WeekHeld.__init__', 'reflection', 'super().__init__(week, problems)', 'body[0]', 'f139b8521720e8af', 1),
    # dogear/publish/store.py: the in-memory test store names the operation for its fault hook
    # action = self._fault('put', key, data)
    Finding('dogear/publish/store.py', 'MemoryStore.put', 'write-name', 'put', 'body[1]', 'ecfc3197db42b3ba', 1),
    # dogear/synthetic.py: URL parsing; argv
    # import sys
    Finding('dogear/synthetic.py', '', 'import', 'sys', '-/body[0]', 'f7252e3c1dbe1d5e', 1),
    # from urllib.parse import urlsplit
    Finding('dogear/synthetic.py', '', 'import', 'urllib.parse', '-', '7f6d2de520ba06be', 1),
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

PROTECTED = {
    ('dogear/publish/publisher.py', '<imports>'): '3e162395e5775f37e47ae9558cec01ea4f39535ef7d2927a6c73a44befb8c6f9',
    ('dogear/publish/publisher.py', 'UnguardedRegistryWrite'): '4cbf0b7fc843cc13351051a8c85fc5a6c4cd1dfb7eabe48afb0f185b1f455dec',
    ('dogear/publish/publisher.py', 'is_registry_key'): 'a497c31f375b0a09c973b5556ffb1115e25eddcc01bb4f6c34414ed4a972393f',
    ('dogear/publish/publisher.py', '_GATE_API'): '1b3d0965b5fbf0d5626933b05563ed69496801ed2db1aa2c77bb855945c2d2f0',
    ('dogear/publish/publisher.py', 'RegistryGate'): 'a1bdab7bb76cafe21a948d0e9ac3922d587581fecef48ce9af0694e450da9012',
    ('dogear/publish/publisher.py', 'Publisher.__init__'): 'c2127b1b4f653332dc03352fc3a6cc3c59b222cbef32c3f3aef953b061f35e62',
    ('dogear/publish/publisher.py', 'Publisher._quiet_capability'): 'fc6161c4679e61c0edd9f36af5e540d6ef1ef69a9fa9eac6f9e3a9208b2a62fb',
    ('dogear/publish/publisher.py', 'Publisher._revision_is_quiet'): 'eb07e4c5b7c90992fa5025f1e9d20a73dbbb3ef5f3352c6fd7f5021e368832b6',
    ('dogear/publish/publisher.py', 'Publisher._quiet_presentation_guard'): 'afd91c2a7d3fb4023f2dea03fbf5dbf0aeed1162500590920d08d152b355be39',
    ('dogear/publish/publisher.py', 'Publisher._quiet_shown_without_capability'): 'f006238ae2660e89a7e8e14db96fe4c53c8c75539fd83c628b89c8fafac322a5',
    ('dogear/publish/publisher.py', 'Publisher._all'): 'd4836d66c03b9eb1f87bde0c7bd60d13b58d05c1940e02e89496d92b965d798c',
    ('dogear/publish/publisher.py', 'Publisher._upload'): '9a73b39d2760a398d922d3f8da234bb9d1b0e1d07a6f2b476362f17ec27d8e17',
    ('dogear/publish/publisher.py', 'Publisher._transact'): 'd8468bd6b0f8385b9891ebd67acee0da50d6f4aeef50fa8f72f914fa7f1d6f76',
    ('dogear/publish/publisher.py', 'Publisher._control'): '29203a034dc55c2f6960ee5843b9155b99f45d6691e8e3eb82ee388caec25769',
    ('dogear/publish/registry.py', 'REGISTRY_KEY'): 'c886597a9fa34c41a0243c86cf07d2aba8f4d44c64296698e0d9488ffa1390d4',
    ('dogear/publish/registry.py', 'sha256'): '9d066faf3373480daee0d3eb7525e28d51681c6ef65aa599f9add1df7050199c',
    ('dogear/publish/firmware.py', ''): '2e22a774f2f5049937d5652efc8eff9c3d95daab60d1e8cecc570e89c5e8d1c2',
    ('dogear/publish/drills.py', 'DrillStore.put'): 'b49252910cb59d338c68ffd4ef0a1539e8751db1b1296d3946dd8416d447f39b',
    ('tests/write_boundary.py', ''): '5bda8fd095212431f01c6f751a3ee66500c6f5fdcd9aed7ea0465ad0388b085e',
    ('tests/test_firmware_capability.py', 'RegistryWriteBoundaryTests'): 'f25792b5c437b9641c4caedf02f63b6eea1610eed2e05abfb5b72960b9cadc75',
    ('tests/test_firmware_capability.py', 'SourceReviewGuardTests'): '2fcbc96e927882ebb108e7e274fcac214c9c8da6e9ff7a8d74970e498df8d2ba',
    ('tests/test_firmware_capability.py', 'BYPASS'): '360bf4738a179e6101e83bb258dc1a735e478a18912482e0180f474463bab73e',
    ('tests/test_firmware_capability.py', 'QUALIFIED_BYPASS'): '79a282624e5670903ba58f896ab77ebb03a021b6a3fb9768f2fea351559a3fd9',
    ('tests/test_firmware_capability.py', 'BYPASS_VARIANTS'): '8cc6673364fe095c2f8b83c41850280d97366bd31377ba10c21f86f76bfbae43',
    ('tests/test_firmware_capability.py', 'NESTED_WRITER'): 'ab430d05252c7b86e244c9af7f55d9216fdb440add1bd098cea6e122891c8308',
    ('<surface>', ''): 'e0dda924138280225b1a287c92b18c6e4f93a345608fec130e224adb37e6ba12',
}
