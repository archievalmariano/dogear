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
    Finding('dogear/publish/cli.py', '_Report.success', 'dynamic', "getattr(out, 'txn', None)", 'body[1]', 'efbcfbb23d59b115', 1),
    # args.store, args.base_url, args.mode, args.policy, args.dataset, args.affinity = (t['store'], t['base_url'], t['m
    Finding('dogear/publish/cli.py', '_apply_target', 'rebind', 'args.store', 'body[1]/body[4]', '4dee8df7c1b1b979', 1),
    # return (Publisher(store, PubData(args.pubdata, committer), StageInputs(args.dataset, args.affinity, args.base_url
    Finding('dogear/publish/cli.py', '_configure', 'construct', 'Publisher(store, PubData(args.pubdata, committer), StageInputs(args.dataset, args.affinity, args.base_url, _generator()), policy, checks, clock, mode=args.mode, assets=assets, served=served, separation=separation)', 'body[14]', '3155b05fe9febba8', 1),
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
    ('dogear/affinity.py', 'Affinity'):
        'the preference levels (see parse_affinity)',
    ('dogear/affinity.py', 'EMPTY'):
        'no preferences (see parse_affinity)',
    ('dogear/affinity.py', 'parse_affinity'):
        "parses the owner's preference file into levels: a scoring input. The protected selector turns a level into its own bounded lift (AFFINITY_LIFT) under its own bars, and an unknown level fails selection",
    ('dogear/cli.py', 'DEFAULT_AFFINITY'):
        'used only to refuse --affinity alongside --target; TARGETS fixes the paths',
    ('dogear/cli.py', 'DEFAULT_DATASET'):
        'used only to refuse --dataset alongside --target; TARGETS fixes the paths',
    ('dogear/publish/cli.py', 'FONTS'):
        'the font files uploaded beside the web page',
    ('dogear/publish/publisher.py', 'Guard'):
        'the guard type alias, for annotations only',
    ('dogear/publish/publisher.py', 'Publisher._history'):
        'the published weeks the selector scores against (its recent-repeat penalty): a scoring input. It cannot admit an excluded quiet item (exclusions come from the protected quiet source and are re-checked on the bytes), and it decides no publication, verification or currentness',
    ('dogear/publish/publisher.py', 'Publisher._write_staged'):
        "saves Friday's staged files for promotion; verify_staged (protected) trusts none of them: inputs, provenance, regeneration, eligibility, edition, quiet rules and every check are redone",
    ('dogear/publish/staging.py', 'FitChecker'):
        'a checker type alias, for annotations only',
    ('dogear/publish/staging.py', 'LinkChecker'):
        'a checker type alias, for annotations only',
    ('dogear/publish/staging.py', '_read'):
        "reads the input files to freeze; any record it returned that is not the dataset's own fails the write-time eligibility guard, which re-reads the dataset itself and compares approved fingerprints",
    ('dogear/web.py', 'render_web'):
        "the browser page's markup and wording: presentation. Every outside link it shows must be one the link check was given or the record's own source (shown_link_problems, protected); the device fit concerns the device issue (issue.py, protected), not this page",
}

PROTECTED = {
    ('dogear/publish/publisher.py', '<imports>'): '3e162395e5775f37e47ae9558cec01ea4f39535ef7d2927a6c73a44befb8c6f9',
    ('dogear/publish/publisher.py', '<bindings>'): '76450af37acd75bcd5b45249a05d4e3adb88681c6dab721654605f1d9cc50f71',
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
    ('dogear/publish/publisher.py', 'Publisher._transact'): 'ed83ebfabeb8f6c32239834a71fb2716987a1c9b17eb1a854c1329a2548b8948',
    ('dogear/publish/publisher.py', 'Publisher._control'): '29203a034dc55c2f6960ee5843b9155b99f45d6691e8e3eb82ee388caec25769',
    ('dogear/publish/publisher.py', 'Outcome'): 'c68ec0b4e91bd553433b283e0dd0d9f8b7316336b6128ccc60c5769f6d4d90a6',
    ('dogear/publish/publisher.py', 'PublishedUnverified'): 'e1d35ecc5e03eda7c87c1e89bfba342b93afc13c2b32915d72b0a30ded0d56b5',
    ('dogear/publish/publisher.py', 'PublishedUnrecorded'): '706853fb00cb8cdf121697b557e8bd7a9dba55675de4af11be9cfb136a4ebfee',
    ('dogear/publish/publisher.py', 'Publisher._registry_state'): '4f891753b1ee358ed1b67427fa3d1c7fac70e49bb1b6899fb0fce07aa9c26f34',
    ('dogear/publish/publisher.py', 'Publisher._committed'): '2316b119552d2b1664d70d358d454f2e1862bf20afe9814f5c0bfdc47e3259fb',
    ('dogear/publish/publisher.py', 'Publisher._stop'): '63c40d33326ab6b63bd75b0901d8669f008b1917907d7c74b42609f6b8d93333',
    ('dogear/publish/publisher.py', 'Publisher._unresolved'): 'c12a101c0f092c8db45da053f97a2f252ef2a8225cce31447cf76c0395ba53a6',
    ('dogear/publish/publisher.py', 'Publisher._carries'): 'ddb87a75a69a259e654397a0c7752cf71994207efcae89beef6e02e6ddf7ac14',
    ('dogear/publish/publisher.py', 'Publisher._finalize'): '538dfe8f75c9df995522df9e8314c8f6a6b5986c1a349bbceb9bc8bab062f989',
    ('dogear/publish/publisher.py', 'Publisher._abandon'): '2468b0d129f25c54d44a68bb3b66dcec1c4551c12fb88f8d1284b5225eb8d5cb',
    ('dogear/publish/publisher.py', 'Publisher._settle_pending'): '4eb53db4246f5ebbb9e7e746b0498a3f648e0854c46957243db06568a350e767',
    ('dogear/publish/publisher.py', 'Publisher.reconcile'): 'bd92a79a3596ab39e96c9cd26ebd8bf9860232e16919982d5e1c62f2bb2fb9b7',
    ('dogear/publish/publisher.py', 'Publisher.abandon'): '56eacd2ebe8c296438a08ef2efb87e5056543046789cdb3ad2d9f38908291a96',
    ('dogear/publish/publisher.py', 'Publisher._history_entry'): 'fa2901d9fc6685052cf6021282528ca99158cd9e1eefd75a75ece5476b15c170',
    ('dogear/publish/publisher.py', 'verification_targets'): '6c2587d12739970f7f5105ebd8d25b441edd6ffbd0a459ddde0e2972b80fc058',
    ('dogear/publish/publisher.py', '_expected_targets_problem'): 'e44bc227d6660fae42f837a23d7ed4955aca3c3458044122e61c0ef7199a2167',
    ('dogear/publish/publisher.py', 'Publisher._outstanding'): '18fb280ff0f8e68b6f3f33047c01b55065438a1828e52470948313df141d0172',
    ('dogear/publish/publisher.py', 'Publisher._verify_outstanding'): 'be468038abc81d21812bc424661f903db68e089ef7ac006c0db320339db78952',
    ('dogear/publish/publisher.py', 'Publisher._check_target'): '835a76a0c9d03ce98f083a04c825abc8abb6294bf75f4df039fdbf526ca78aab',
    ('dogear/publish/publisher.py', 'Publisher._save_quietly'): '35c0b4203ec71bbfbfcd349d89650392a2495111b3ebc0a22f046d02669d1dee',
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
    ('dogear/publish/edition.py', ''): '06c4032e3c90292c94b7a176725df4089dd718707a8c9623ae2f0daafee9dfe3',
    ('dogear/synthetic.py', ''): '9298499ee140b9ba1e456a910dd3b265bd88165489ff3fa9dc29c896be944fe3',
    ('dogear/quiet.py', ''): '75727c0a54d4e9958e0d67937250a4476b30d979d2d2592689534044bb99128d',
    ('dogear/select.py', ''): '6b5d1449d6b7d092a0c5d820e501f538beefd5534f832a584c4d7f8f14a93b53',
    ('dogear/issue.py', ''): 'b7fe3de28ccd6537f83ac05d4482ae061e5680c087d594e15b17732969d8cee3',
    ('dogear/publish/checks.py', ''): '5b6f5ff4a9125ead387bbcc9d6921501de650408b3fff4c50272c39e79e748df',
    ('dogear/publish/routes.py', ''): 'a25019c047bd898289762d45dfd6782fb803894894697125f1809c2f95a5c15d',
    ('mock/render_dogear.py', ''): '0fa736f6645b2b19b9356e21a06195dc0dac0c708f088216cd27d92eb50389a7',
    ('dogear/publish/publisher.py', 'Publisher._quiet_source'): 'dd0bbf2bea4fb7c527adfc28f3c4d6f1ff4b1c8f59ccab5a9a0af53be0e37ed5',
    ('dogear/publish/quiet_history.py', ''): 'cb19b7a56af8ad4398a104be5559393bc2937b41f1ef7905768573ffbd4a50c5',
    ('dogear/publish/pubdata.py', ''): '841baee953da25a57de288f6190fb734e99d028cc42e4dcc108dad0dd36597da',
    ('dogear/publish/staging.py', '<bindings>'): '9423a2b8a415536dbe7c6e36f695c248c1eb2d58a09d8fd79019bab8e5439f5d',
    ('dogear/publish/publisher.py', 'Publisher._stage'): '7efdc9c380eacbb35839614be878edfb703ac3710b79a227f5d123080710bf82',
    ('dogear/publish/publisher.py', 'Publisher._uploads'): '4c387adafce21110a9cc13fe14f1f04ed51e976f668533bc109a362baa6fb14b',
    ('dogear/publish/staging.py', 'stage'): 'c64da939580e581d396ca59446476daaaebd5fc8740255a3c5ce2f661d80e726',
    ('dogear/publish/staging.py', 'freeze'): '5742b77284efa26df947ba5a16a0a513f093b43eb28955d4db427cb876faad65',
    ('dogear/publish/staging.py', 'generate'): '0fa0a6c37b066ab25f7b634c2c17bf6b954720859c4e11a5ac766c789e9afda1',
    ('dogear/publish/staging.py', 'quiet_occurrence'): '8004991e9284a1b8ec0ee64de256e6ea449d40ac70e0217bd3a02d29bb4f4c6c',
    ('dogear/publish/staging.py', 'run_checks'): 'a3f0318e54778f970602229f2feb80b0a56bda0842f41fab9edce04263d26782',
    ('dogear/publish/staging.py', 'external_links'): 'b181ddc66e762be6f54380223783d9408603dd3222e8e34e35d7b22eed93eb5f',
    ('dogear/publish/staging.py', 'LinkReport'): '47839a3f5ce299745612442cc5583ae100a8e094d18dac9ad3ee10a9f692921e',
    ('dogear/publish/staging.py', 'shown_link_problems'): '9cf893cbdb865d97d887f753a755126e27d7a444a5ecd405158a2d0bb19f7336',
    ('dogear/publish/staging.py', '_HREF'): '4300ae5056dff4a3d0283b121be62038f2e5407308ea94255057c42630c79a8c',
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
    ('dogear/publish/firmware.py', ''): '2e22a774f2f5049937d5652efc8eff9c3d95daab60d1e8cecc570e89c5e8d1c2',
    ('dogear/publish/r2.py', ''): '4afa501dcb1fc56454c277eabf009e51b4d11993d6c30759e7cdaacb62f28aa2',
    ('dogear/publish/store.py', ''): 'ff1b9479259d27e55d008db7d74feca7a30ec040f111fcccece0c22c8257c233',
    ('dogear/publish/drills.py', ''): '6330a81c0a1cf4477c0ee057ef5ece5c909ef77364ac36dbbf0858958b3f6e51',
    ('dogear/publish/cli.py', '<bindings>'): '9fa3b6420068d935df3b0cbc3ed4bfe6998e20b1a043c67cb19be6a256bc6f91',
    ('dogear/publish/cli.py', 'ROOT'): '54984a8c5ee3afca40c43588fccd6a196361c5bd0f3a143eae021f3097225f2c',
    ('dogear/publish/cli.py', 'PRODUCTION_HOST'): '91b9ff873fafee6cb0a26c3db0393c8f8e28db71ead754690447802631cb2296',
    ('dogear/publish/cli.py', 'TARGETS'): '81c0748349f6c031e716872c6aefe5dc0964a22617cd9e6fe9f8487f7b4fd7c5',
    ('dogear/publish/cli.py', '_generator'): '7b4fa4abaca8a36190ce3c984a51e45c16a49f0509c576f90ce2da4b6d108fe0',
    ('dogear/publish/cli.py', '_store'): 'ee595279707e3991523fd044b0ad279b9fd10d5eee412603d9635adc238676ef',
    ('dogear/publish/cli.py', '_apply_target'): '0b8c75dafb4a622648786dec51a3b6b06f871f5ef1cbcdf7c1fc6f6af3928627',
    ('dogear/publish/cli.py', '_pairing_problem'): '6e3d2c9f0db2558ce01e86fe65975bcaaceff5b533763f4c0100b4c8beed51e8',
    ('dogear/publish/cli.py', '_configure'): '2c40824205e389361f335fdea80a5523d64290ddca46d9ac517266c898d45786',
    ('tests/write_boundary.py', ''): 'f49014baa93f90cd6470d682f22e4e440a336745951b217410d20d3475f734e5',
    ('tests/test_firmware_capability.py', 'RegistryWriteBoundaryTests'): 'c4a0b639040ff82ba7ca910f93bdc8a3cb0cf449f3e3ece6de7377789bcda876',
    ('tests/test_firmware_capability.py', 'SourceReviewGuardTests'): 'f081a01b5abf841fd514636ad2c522a530d2dc59b1e72575a65a61ffa59c9da7',
    ('tests/test_firmware_capability.py', 'BindingAndSurfaceTests'): 'b30dc384ffcda76b0d2c1a37f1b5ed0f8d55ee1458d149dc8e96f65c5ecc1b0d',
    ('tests/test_firmware_capability.py', 'NESTED_REBINDING'): 'e645e614c14c3c79ea97acd4705b469cdaf13d4dab50971519b4924689cf0b63',
    ('tests/test_firmware_capability.py', 'LAMBDA_REBINDING'): 'bb7893ac6cc38e35e83e6e16adfb2b8ad8811b44dc01153a0d899299ad571492',
    ('tests/test_firmware_capability.py', 'CliHandoffTests'): '2db490a4454289face410dca4a08057a94f89bb07efead88aebeedf6e0d1bdd2',
    ('tests/test_firmware_capability.py', 'QuietVerificationSurfaceTests'): '42fb997cb831a730230a7eb9bc0079a402406a6b0bea6639a5f54d27cc2e44aa',
    ('tests/test_firmware_capability.py', 'PublicationInvariantTests'): '48f8c296efccd9e1d822d97df1b76e9cf121bad6bac76556f6b9faa064ddfd74',
    ('tests/test_firmware_capability.py', 'BYPASS'): '360bf4738a179e6101e83bb258dc1a735e478a18912482e0180f474463bab73e',
    ('tests/test_firmware_capability.py', 'QUALIFIED_BYPASS'): '79a282624e5670903ba58f896ab77ebb03a021b6a3fb9768f2fea351559a3fd9',
    ('tests/test_firmware_capability.py', 'BYPASS_VARIANTS'): '8cc6673364fe095c2f8b83c41850280d97366bd31377ba10c21f86f76bfbae43',
    ('tests/test_firmware_capability.py', 'NESTED_WRITER'): 'ab430d05252c7b86e244c9af7f55d9216fdb440add1bd098cea6e122891c8308',
    ('<surface>', ''): 'fa9d46379850008eddcff57c1316bc3dffaceaf77a108f227a3eac73bb28b0d8',
}
