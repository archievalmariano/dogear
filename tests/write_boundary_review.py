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
    # dogear/synthetic.py: URL parsing; argv, imported again inside its __main__ block
    # import sys
    Finding('dogear/synthetic.py', '', 'import', 'sys', '-/body[0]', 'f7252e3c1dbe1d5e', 1),
    # from urllib.parse import urlsplit
    Finding('dogear/synthetic.py', '', 'import', 'urllib.parse', '-', '7f6d2de520ba06be', 1),
    # import sys
    Finding('dogear/synthetic.py', '', 'rebinding', 'sys (import)', '-/body[0]', 'f7252e3c1dbe1d5e', 1),
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
    ('dogear/publish/pubdata.py', 'PubData'):
        'the publication-data store (pending, history): never the registry',
    ('dogear/publish/pubdata.py', 'SaveFailed'):
        'a publication-data error type: a change can only make a run fail',
    ('dogear/publish/publisher.py', 'Guard'):
        'the guard type alias, for annotations only',
    ('dogear/publish/publisher.py', 'Outcome'):
        'the result a run returns; no write depends on it',
    ('dogear/publish/publisher.py', 'Publisher._abandon'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/publisher.py', 'Publisher._committed'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/publisher.py', 'Publisher._history'):
        'builds the candidate revision or its history line; every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/publisher.py', 'Publisher._history_entry'):
        'builds the candidate revision or its history line; every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/publisher.py', 'Publisher._quiet_source'):
        'builds the candidate revision or its history line; every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/publisher.py', 'Publisher._registry_state'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/publisher.py', 'Publisher._stage'):
        'builds the candidate revision or its history line; every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/publisher.py', 'Publisher._stop'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/publisher.py', 'Publisher._unresolved'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/publisher.py', 'Publisher._uploads'):
        'builds the candidate revision or its history line; every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/publisher.py', 'Publisher._write_staged'):
        'builds the candidate revision or its history line; every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/publisher.py', 'Publisher.reconcile'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/publisher.py', 'verification_targets'):
        'after the write or instead of it (settles, records, verifies); only _transact writes the registry, which the scan holds',
    ('dogear/publish/routes.py', 'fetch'):
        'the default fetcher for checking the served site after the write',
    ('dogear/publish/staging.py', 'FitChecker'):
        'a checker type alias, for annotations only',
    ('dogear/publish/staging.py', 'LinkChecker'):
        'a checker type alias, for annotations only',
    ('dogear/publish/staging.py', 'StageInputs'):
        'carries the input paths the guards read afresh',
    ('dogear/publish/staging.py', 'Staged'):
        'carries the staged bytes the guards re-check',
    ('dogear/publish/staging.py', 'WeekHeld'):
        'a staging refusal type: a change can only make a run fail',
    ('dogear/publish/staging.py', 'verify_staged'):
        'checks the staged bytes reproduce from their inputs (the whole generator); every write re-checks those bytes (eligibility, separation, edition invariant, quiet presentation)',
    ('dogear/publish/store.py', 'PreconditionFailed'):
        'a storage error type: a change can only make a write fail',
    ('dogear/publish/store.py', 'StoreError'):
        'a storage error type: a change can only make a read or write fail',
}

PROTECTED = {
    ('dogear/publish/publisher.py', '<imports>'): '3e162395e5775f37e47ae9558cec01ea4f39535ef7d2927a6c73a44befb8c6f9',
    ('dogear/publish/publisher.py', '<bindings>'): '0c74ee0af90bc76718813be81fe062cd1de35e31ae0018cf24db341f647eb869',
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
    ('dogear/publish/publisher.py', 'shown_revisions'): 'fa792f643fa86ea6362e0db57cf3d1e2c8e2a9c7f7a4138fbf8b079f07fe1b9c',
    ('dogear/publish/publisher.py', 'Publisher._read_registry'): 'b83bef519f6b2a3427004b873da8cac9c29d563fd0a7a964633a5a2bdb432188',
    ('dogear/publish/publisher.py', 'Publisher._dataset_text'): '84cac62afb8cc2c0f3b8d777e587d716e19244ca14d389633c041403b82d45c2',
    ('dogear/publish/publisher.py', 'Publisher._week_guard'): 'ade2c10c26c856a5775ed9dfddfc120cb38f3fdf652c82c47e93b3c7c14718f1',
    ('dogear/publish/publisher.py', 'Publisher._separation_problem'): 'bc26cc80eb70fedeb0694af75e26638b690d31dbf66fb5c416b1fac659ee53a7',
    ('dogear/publish/publisher.py', 'Publisher._eligibility_guard'): '4870e55b3f4bf3889e88cb13bd2338da9879268a9349875ef495a3c801d72984',
    ('dogear/publish/publisher.py', 'Publisher._visible_revisions_guard'): 'ad848dd4960c1d6fdf5c8736e44d4bb5d62e1c8fbe20721c52834ebfb94c9289',
    ('dogear/publish/publisher.py', 'Publisher.promote'): '59b59665f9cc50920e1095086b5d123888cea3b430f26775d3472b126171c49f',
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
    ('dogear/publish/registry.py', ''): 'e73ff75f9a56dc449bdd3d5ee6738baf43bf784f7610eaa7e2e3b05af2867d8a',
    ('dogear/publish/eligibility.py', ''): '0975d75936707113959f78362a6fc4820f2856baeedad8287a7f6871c01d31f5',
    ('dogear/week.py', ''): '4087294013e6a65914a3387e8607b2efa5331f1ddfd0926b6f538b720eecc9b9',
    ('dogear/dataset.py', ''): '59c2852e0ca0e622025570cfb9056985374a3ceadea530eb9cc9ea97253287ce',
    ('dogear/affinity.py', '<bindings>'): '1bbb970d77d7db74158136629fa5710f5364b58a82934f762eec29bd51483d9a',
    ('dogear/affinity.py', 'normalize'): '85d6ec7a6892838d43926beaaea76461490e2cfe33f8c6122a93f70616a1bc53',
    ('dogear/publish/edition.py', ''): '6291d0eec22b3d6dd8d8ba29489fa5dcc8cd058ae08da379957d7863032e94df',
    ('dogear/synthetic.py', ''): '9298499ee140b9ba1e456a910dd3b265bd88165489ff3fa9dc29c896be944fe3',
    ('dogear/quiet.py', ''): '75727c0a54d4e9958e0d67937250a4476b30d979d2d2592689534044bb99128d',
    ('dogear/select.py', '<bindings>'): '1e198475c0e62fa36bd5b804d8664b2c11647aaca5880dbaa7ea040249d99c28',
    ('dogear/select.py', 'MAX_ENTRIES'): '12fd26d16c206798524113c83fc115d1dee613533f561980c6b2d5b7ee5ee65d',
    ('dogear/publish/quiet_history.py', '<bindings>'): '5c83e0fa73fc926ce91b73d527298ba8f72a992d05bbdf6676bd81fd5c47c21a',
    ('dogear/publish/quiet_history.py', 'CONTENT_OPS'): 'cf51cc3a0319ecfeb5bd355aacdeafbbea416e499a8fde283c6b25f81a98d549',
    ('dogear/publish/quiet_history.py', 'content_lines'): 'd2b7c86f9572a2529ed01ffaa2377ab8d8960a83a00ea5602abc02e34cb48c3f',
    ('dogear/publish/quiet_history.py', 'line_problems'): '9298167c97d459d81f8f13b4ead9db10ef9261f8360288d6cc4dceb1a0e86aae',
    ('dogear/publish/staging.py', '<bindings>'): 'c37a1acfd7b887a73ebe29d553916944c8ceb775f003d902d9099875d31d8831',
    ('dogear/publish/staging.py', 'provenance_bytes'): 'f83f0dcbbcdcd282554984a9a1b22dc26ed2aece59fba13df93c91ef74f6a12c',
    ('dogear/publish/staging.py', 'Checks'): '35b07399afe917c0a41b38de46c569e41cda53f451ce941d828cd1d74bd099a1',
    ('dogear/publish/firmware.py', ''): '2e22a774f2f5049937d5652efc8eff9c3d95daab60d1e8cecc570e89c5e8d1c2',
    ('dogear/publish/drills.py', '<bindings>'): '00d55b6696810dda2510388e0d1706d03e731532f2e2df7589db2b63069c9946',
    ('dogear/publish/drills.py', 'DrillStore.put'): 'b49252910cb59d338c68ffd4ef0a1539e8751db1b1296d3946dd8416d447f39b',
    ('tests/write_boundary.py', ''): '059565d415dc622578a9f5e45bb5c602b99f8d370ef2bada37753ea1bfa518fd',
    ('tests/test_firmware_capability.py', 'RegistryWriteBoundaryTests'): 'c4a0b639040ff82ba7ca910f93bdc8a3cb0cf449f3e3ece6de7377789bcda876',
    ('tests/test_firmware_capability.py', 'SourceReviewGuardTests'): 'f081a01b5abf841fd514636ad2c522a530d2dc59b1e72575a65a61ffa59c9da7',
    ('tests/test_firmware_capability.py', 'BindingAndSurfaceTests'): '5f2987938974d4d4e1a5353e7415a79ca00e58d105734e20a6ec09a6fe894268',
    ('tests/test_firmware_capability.py', 'NESTED_REBINDING'): 'e645e614c14c3c79ea97acd4705b469cdaf13d4dab50971519b4924689cf0b63',
    ('tests/test_firmware_capability.py', 'BYPASS'): '360bf4738a179e6101e83bb258dc1a735e478a18912482e0180f474463bab73e',
    ('tests/test_firmware_capability.py', 'QUALIFIED_BYPASS'): '79a282624e5670903ba58f896ab77ebb03a021b6a3fb9768f2fea351559a3fd9',
    ('tests/test_firmware_capability.py', 'BYPASS_VARIANTS'): '8cc6673364fe095c2f8b83c41850280d97366bd31377ba10c21f86f76bfbae43',
    ('tests/test_firmware_capability.py', 'NESTED_WRITER'): 'ab430d05252c7b86e244c9af7f55d9216fdb440add1bd098cea6e122891c8308',
    ('<surface>', ''): '911415b83ae3cfd08fcb9476893c835b8523213fd107f5ae634995aca55843bd',
}
