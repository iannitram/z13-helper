"""One command owner across frontend edits, full applies, and recovery."""
from contextlib import contextmanager
import copy
import json
import threading

import pytest

import host
import profiles
import max_fans_state
import state_authority as authority
from z13ctl_client import Z13CtlClient, Z13CtlError

# z13ctl backend names of the built-ins (no fixed named profiles, issue #179).
BAL = Z13CtlClient.backend_profile_name('Balanced')
TUR = Z13CtlClient.backend_profile_name('Turbo')


REAL_PROBE = authority.named_client


class Daemon(Z13CtlClient):
    def __init__(self):
        self.calls = []
        self.state = {'profile': BAL, 'custom_profiles': {}}
        self.fail = None

    def request(self, cmd, **fields):
        self.calls.append((cmd, fields))
        if cmd == self.fail:
            raise Z13CtlError('injected failure')
        if cmd == 'profile-list':
            return {'ok': True, 'value': json.dumps(list(self.state['custom_profiles'].values()))}
        if cmd == 'get-state':
            return {'ok': True, 'state': copy.deepcopy(self.state)}
        if cmd == 'profile-create':
            self.state['custom_profiles'][fields['set']] = {'name': fields['set']}
        elif cmd == 'profile':
            self.state['profile'] = fields['set']
        elif fields.get('profile'):
            p = self.state['custom_profiles'][fields['profile']]
            field = {'fancurve': 'fan_curve', 'tdp': 'tdp', 'undervolt': 'undervolt'}.get(cmd)
            if field == 'tdp':
                assert 'set' in fields  # required by the real wire protocol
                p[field] = {'pl1_spl': int(fields['set']), 'pl2_sppt': int(fields['pl2']),
                            'fppt': int(fields['pl3'])}
            elif field == 'fan_curve':
                p[field] = {'points': [{'temp': int(t), 'pwm': round(int(pct[:-1]) * 255 / 100)}
                                      for t, pct in (s.split(':') for s in fields['set'].split(','))]}
            elif field == 'undervolt':
                p[field] = {'cpu_co': int(fields['set'])}
            elif cmd.endswith('-reset'):
                field = {'fancurve-reset': 'fan_curve', 'tdp-reset': 'tdp',
                         'undervolt-reset': 'undervolt'}[cmd]
                p.pop(field, None)
        return {'ok': True}


@pytest.fixture
def daemon(monkeypatch, profiles_env):
    d = Daemon()
    profiles.save_store({'version': 1, 'active': 'Balanced', 'profiles': profiles.default_profiles()})
    # Populate initial named settings without changing the requested selection.
    d.ensure_named_profile(profiles.get_profile('Balanced'))
    d.calls.clear()
    monkeypatch.setattr(authority, 'named_client', lambda: d)
    return d


def test_full_apply_mirrors_all_fields_and_selects_only_after_target_is_ready(daemon, fake_host):
    p = profiles.get_profile('Turbo')
    p['tdp'] = {'pl1': 50, 'pl2': 60, 'pl3': 65}
    p['undervolt'] = 0  # zero is a real override, not an absent value
    result = profiles.apply(p, skip_hooks=True)
    assert all(ok for _, ok, _ in result)
    assert daemon.state['profile'] == TUR
    cmds = [cmd for cmd, _ in daemon.calls]
    assert cmds.index('profile-create') < cmds.index('fancurve') < cmds.index('tdp') < cmds.index('undervolt')
    assert daemon.calls[-1] == ('profile', {'set': TUR})
    assert not any(a[1] in ('tdp', 'fancurve', 'undervolt') for a in fake_host.argvs)
    assert all(fields.get('profile') == TUR for cmd, fields in daemon.calls
               if cmd in ('tdp', 'fancurve', 'undervolt'))


def test_mirror_failure_never_selects_partial_target_or_falls_back(daemon, fake_host):
    daemon.fail = 'tdp-reset'
    results = profiles.apply(profiles.get_profile('Turbo'), skip_hooks=True)
    assert any(not ok for _, ok, _ in results)
    assert daemon.state['profile'] == 'performance'
    assert not fake_host.calls
    assert not any(cmd == 'profile' and f['set'] == TUR for cmd, f in daemon.calls)


def test_stale_frontend_edit_is_stored_but_never_applied_to_another_profile(daemon, fake_host):
    old = profiles.get_profile('Balanced')
    old['tdp'] = {'pl1': 40, 'pl2': 50, 'pl3': 55}
    daemon.state['profile'] = 'performance'
    authority.edit(old, 'tdp')   # no "active profile changed" error: Balanced is inactive now
    assert profiles.active_name() == 'Turbo'
    assert profiles.get_profile('Balanced')['tdp']['pl1'] == 40
    assert not fake_host.calls
    assert not any(cmd == 'profile' for cmd, _ in daemon.calls)


def test_inactive_profile_edit_persists_and_does_not_raise(daemon, fake_host):
    """Regression: save(profile, field) on a non-active profile must keep the edit."""
    p = profiles.get_profile('Turbo')
    p['tdp'] = {'pl1': 45, 'pl2': 55, 'pl3': 60}
    authority.edit(p, 'tdp')
    assert profiles.get_profile('Turbo')['tdp']['pl1'] == 45
    assert profiles.active_name() == 'Balanced'
    assert daemon.state['profile'] == BAL
    assert not fake_host.calls
    # Only the edited profile's own named target is written; live selection never moves.
    assert not any(cmd == 'profile' for cmd, _ in daemon.calls)
    assert all(f.get('profile') == TUR for cmd, f in daemon.calls
               if cmd in ('tdp', 'fancurve', 'undervolt'))


def test_live_slider_edit_targets_named_profile_without_default_custom(daemon, fake_host):
    authority.set_field('tdp', {'pl1': 40, 'pl2': 50, 'pl3': 60}, 'Balanced')
    edits = [fields for cmd, fields in daemon.calls if cmd == 'tdp']
    assert edits == [{'profile': BAL, 'set': '40', 'pl2': '50', 'pl3': '60'}]
    assert profiles.get_profile('Balanced')['tdp']['pl1'] == 40
    assert not fake_host.calls


def test_cli_changes_are_reflected_without_hardware_writes(daemon, fake_host):
    daemon.state['custom_profiles'][BAL]['tdp'] = {'pl1_spl': 35, 'pl2_sppt': 45, 'fppt': 55}
    daemon.state['battery_limit'] = 85
    daemon.state['lighting'] = {'brightness': 1}
    profiles.sync_active_from_backend(daemon.state)
    p = profiles.get_profile('Balanced')
    assert p['tdp']['pl1'] == 35
    assert p['battery_limit'] == 85
    assert p['brightness'] == 'low'
    assert not daemon.calls
    assert not fake_host.calls


def test_max_fans_uses_the_same_target_and_only_publishes_confirmed_success(daemon, fake_host):
    daemon.fail = 'fancurve'
    with pytest.raises(RuntimeError):
        authority.set_max_fans(True)
    assert not max_fans_state.is_enabled()
    assert any(f.get('profile') == BAL for c, f in daemon.calls if c == 'fancurve')


@pytest.mark.parametrize('reply', ['garbage', '{}', '[{}]'])
def test_broken_new_daemon_cannot_enable_legacy_fallback(monkeypatch, reply):
    class Broken:
        def profile_list(self):
            return reply
    monkeypatch.setattr(authority, 'Z13CtlClient', lambda **_: Broken())
    with pytest.raises(Z13CtlError, match='invalid JSON'):
        REAL_PROBE()


def test_old_daemon_uses_compatibility_path_before_any_targeted_write(monkeypatch):
    class Old:
        def profile_list(self):
            raise Z13CtlError('unknown command: profile-list')
    monkeypatch.setattr(authority, 'Z13CtlClient', lambda **_: Old())
    assert REAL_PROBE() is None


def test_named_watchdog_recovery_preserves_custom_selection_and_skips_hooks(daemon, fake_host):
    p = profiles.get_profile('Balanced')
    p.update(name='Travel', builtin=False, hooks={'enter': 'must-not-run', 'exit': 'must-not-run'})
    profiles.upsert_profile(p)
    daemon.activate_app_profile(p)
    profiles.set_active('Travel')
    daemon.calls.clear()
    assert 're-applied' in authority.recover_performance()
    assert daemon.state['profile'] == daemon.mapped_profile_name(p)
    assert not fake_host.calls


def test_hardware_lock_failure_refuses_mutation(monkeypatch, fake_host):
    def fail(_):
        raise PermissionError('no lock permission')
    monkeypatch.setattr(profiles, '_open_lock', fail)
    with pytest.raises(PermissionError):
        authority.run(['z13ctl', 'profile', '--set', 'balanced'])
    assert not fake_host.calls


def test_inactive_custom_profile_is_mirrored_without_changing_live_selection(daemon, fake_host):
    p = profiles.get_profile('Balanced')
    p.update(name='Travel', builtin=False)
    profiles.upsert_profile(p)
    assert 'Saved' in authority.mirror_profile(p)
    saved = profiles.get_profile('Travel')
    assert saved['z13ctl_profile'] in daemon.state['custom_profiles']
    assert daemon.state['profile'] == BAL
    assert not any(cmd == 'profile' for cmd, _ in daemon.calls)
    assert not fake_host.calls


def test_stale_widget_cannot_overwrite_unrelated_backend_fields(daemon, fake_host):
    old = profiles.get_profile('Balanced')
    old['tdp'] = {'pl1': 40, 'pl2': 50, 'pl3': 60}
    daemon.state['custom_profiles'][BAL]['undervolt'] = {'cpu_co': -5}
    authority.edit(old, 'tdp')
    assert profiles.get_profile('Balanced')['undervolt'] == -5


def test_profile_apply_and_queued_slider_share_one_transaction(daemon, fake_host):
    entered, release, started, done = (threading.Event() for _ in range(4))
    original = daemon.request
    def request(cmd, **fields):
        if cmd == 'profile' and fields.get('set') == 'performance':
            entered.set()
            assert release.wait(3)
        return original(cmd, **fields)
    daemon.request = request
    errors = []
    def apply():
        try:
            authority.apply_profile('Turbo', skip_hooks=True)
        except Exception as exc:
            errors.append(exc)
    def slider():
        started.set()
        try:
            authority.set_field('battery_limit', 85, 'Balanced')
        except ValueError as exc:
            errors.append(exc)
        finally:
            done.set()
    first = threading.Thread(target=apply)
    second = threading.Thread(target=slider)
    first.start()
    try:
        assert entered.wait(1)
        second.start()
        assert started.wait(1)
        assert not done.wait(0.05)
    finally:
        release.set()
        first.join(3)
        if second.ident:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert len(errors) == 1 and 'active profile changed' in str(errors[0])
    assert profiles.active_name() == 'Turbo'
    assert profiles.get_profile('Turbo').get('battery_limit') != 85


def test_rebuilding_a_lower_tdp_profile_clears_its_old_limit_before_curve(daemon, fake_host):
    original = daemon.request
    daemon.state['custom_profiles'][BAL]['tdp'] = {'pl1_spl': 80, 'pl2_sppt': 90, 'fppt': 93}
    def request(cmd, **fields):
        if cmd == 'fancurve':
            old = daemon.state['custom_profiles'][fields['profile']].get('tdp')
            if old and old['pl1_spl'] > 75:
                raise Z13CtlError('curve below old saved TDP safety floor')
        return original(cmd, **fields)
    daemon.request = request
    p = profiles.get_profile('Balanced')
    p['tdp'] = {'pl1': 40, 'pl2': 50, 'pl3': 60}
    results = profiles.apply(p, skip_hooks=True)
    assert all(ok for _, ok, _ in results), results
    assert daemon.state['custom_profiles'][BAL]['tdp']['pl1_spl'] == 40
    assert daemon.state['profile'] == BAL


def test_backend_base_snapshot_preserves_an_empty_app_profile_alias(daemon, fake_host):
    p = {'name': 'Travel', 'builtin': False, 'base': 'balanced', 'fan_curve': None,
         'tdp': None, 'undervolt': None, 'hooks': {'enter': '', 'exit': ''}}
    profiles.upsert_profile(p)
    daemon.ensure_named_profile(p)
    daemon.state['profile'] = 'balanced'  # upstream cannot activate empty custom profiles
    profiles.set_active('Travel')
    profiles.sync_active_from_backend(daemon.state)
    assert profiles.active_name() == 'Travel'
    assert not fake_host.calls


def test_per_device_brightness_takes_precedence_over_the_global_default(daemon, fake_host):
    daemon.state['lighting'] = {'brightness': 3}
    daemon.state['devices'] = {'keyboard': {'brightness': 1}}
    profiles.sync_active_from_backend(daemon.state)
    assert profiles.get_profile('Balanced')['brightness'] == 'low'
    assert not fake_host.calls


def test_platform_profile_switch_holds_the_shared_hardware_lock(fake_host, monkeypatch):
    held = []
    real = authority.transaction

    @contextmanager
    def watching():
        with real():
            held.append(True)
            yield

    monkeypatch.setattr(authority, 'transaction', watching)
    result = authority.set_platform_profile('balanced', z13ctl='z13ctl')
    assert result.rc == 0
    assert held
    assert fake_host.argvs == [['z13ctl', 'profile', '--set', 'balanced']]
