"""Shared profile/hardware command authority for every frontend and recovery.

z13ctl owns live state. This adapter serializes app transactions across processes,
mirrors named performance settings, and preserves the bounded legacy CLI path.
Only this module hands hardware commands to the host seam.
"""
from contextlib import contextmanager
import functools
import json
import logging
import os
import threading

import host
from z13ctl_client import Z13CtlClient, Z13CtlError
from z13ctl_profiles import profile_target_supported

logger = logging.getLogger(__name__)

_MUTEX = threading.RLock()
_local = threading.local()


@contextmanager
def transaction():
    # profiles supplies the existing hardened, reentrant inter-process lock.
    import profiles
    with _MUTEX, profiles.file_lock(profiles.STORE_PATH + '.hardware', strict=True):
        yield


def serialized(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with transaction():
            return fn(*args, **kwargs)
    return wrapped


def named_client():
    """Read-only protocol probe; only an absent/old daemon permits CLI fallback.

    profile-list and the profile target field were added together upstream.
    A malformed successful response is a broken new daemon, not a legacy one.
    """
    client = Z13CtlClient(request_timeout=host.Z13CTL_WRITE_TIMEOUT_S)
    try:
        listing = client.profile_list()
    except (ConnectionError, FileNotFoundError):
        return None
    except Z13CtlError as exc:
        if 'unknown command' in str(exc).lower():
            return None
        raise
    if not profile_target_supported(listing):
        raise Z13CtlError('z13ctl profile-list returned invalid JSON profile rows')
    return client


def run(argv, timeout=30, *, as_deck=False, input=None):
    """One bounded hardware command, with explicit named targets when supported.

    CLI reads and non-hardware commands retain host.run's exact contract.
    Never retry a failed socket write through the CLI: it may have applied.
    """
    argv = list(argv)
    hardware = os.path.basename(argv[0]) == 'z13ctl'
    write = hardware and ('--set' in argv or '--reset' in argv or
                          'brightness' in argv or 'apply' in argv or 'off' in argv)
    if not write:
        return host.run(argv, timeout=timeout, as_deck=as_deck, input=input)
    with transaction():
        try:
            client = named_client()
            # Profile activation and non-performance settings still use the CLI
            # parser, which talks to the same daemon. Thermal edits must carry
            # an explicit target so they never land on default `custom`.
            cmd = argv[1]
            if client is not None and cmd in ('tdp', 'fancurve', 'undervolt'):
                import profiles
                fields = {}
                if '--profile' in argv:
                    fields['profile'] = argv[argv.index('--profile') + 1]
                else:
                    state = client.get_state()
                    bound = getattr(_local, 'profile', None)
                    if bound is None:
                        profiles.sync_active_from_backend(state)
                    elif (state.get('profile') not in (client.mapped_profile_name(bound), bound['base'])):
                        raise ValueError('Not applied: active profile changed; refresh the control')
                    p = bound or profiles.get_profile(profiles.active_name())
                    if p is None:
                        raise Z13CtlError('No active profile for hardware edit')
                    fields['profile'] = client.mapped_profile_name(p)
                    # Populate a new target before the first live field edit.
                    if fields['profile'] not in (state.get('custom_profiles') or {}):
                        client.ensure_named_profile(p)
                if '--reset' in argv:
                    cmd += '-reset'
                else:
                    fields['set'] = argv[argv.index('--set') + 1]
                for flag in ('pl1', 'pl2', 'pl3'):
                    if '--' + flag in argv:
                        fields[flag] = argv[argv.index('--' + flag) + 1]
                if '--force' in argv:
                    fields['force'] = True
                reply = client.request(cmd, **fields)
                # Editing an inactive target is save-only upstream. Select it
                # only if it still belongs to the current app profile.
                if '--profile' not in argv:
                    after = client.get_state().get('profile')
                    if after != fields['profile']:
                        if after != state.get('profile'):
                            raise ValueError('Not applied: active profile changed; target saved only')
                        client.request('profile', set=fields['profile'])
                return host.Result(0, reply.get('value', ''), False)
            return host.run(argv, timeout=timeout, as_deck=as_deck, input=input)
        except TimeoutError:
            return host.Result(124, 'z13ctl write timed out after 20 s (kernel asus-wmi hang?)', True)
        except (OSError, Z13CtlError, ValueError) as exc:
            return host.Result(1, str(exc), False)


def _read_saved_brightness():
    paths = [
        os.path.expanduser("~/.config/z13-helper/config.json"),
        os.path.expanduser("~/homebrew/settings/z13-helper/state.json"),
        "/home/deck/homebrew/settings/z13-helper/state.json",
    ]
    try:
        import sys
        if "decky" in sys.modules:
            d = getattr(sys.modules["decky"], "DECKY_PLUGIN_SETTINGS_DIR", None)
            if d:
                paths.insert(0, os.path.join(d, "state.json"))
    except Exception:
        pass

    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                v = data.get("brightness")
                if v is not None:
                    return v
        except Exception:
            pass
    return None


@serialized
def edit(profile, field, *, devices=None):
    """Store an edit; push it live only when the profile is active."""
    import profiles
    client = named_client()
    if client is not None:
        profiles.sync_active_from_backend(client.get_state())
    current = profiles.get_profile(profile['name'])
    before_value = None
    if current is not None:
        raw = current.get(field)
        before_value = dict(raw) if isinstance(raw, dict) else raw
        # A slider owns one field, never the other values in its stale widget copy.
        current[field] = profile.get(field)
        profile = current
    profiles.upsert_profile(profile)
    if profile['name'] != profiles.active_name():
        # Inactive profile: the edit is stored through this owner; live
        # hardware is untouched until the profile is applied.
        return mirror_profile(profile)
    previous = getattr(_local, 'profile', None)
    _local.profile = profile
    try:
        return _push_field(profile, field, devices=devices, before=before_value)
    finally:
        _local.profile = previous


@serialized
def apply_profile(name, **kw):
    """Apply a stored profile as one transaction (GUI switch and re-apply)."""
    import profiles
    return profiles.apply_by_name(name, **kw)


def _push_field(profile, field, *, devices=None, before=None):
    import profiles
    from hardware_controller import HardwareController
    # Use the same full ordered apply for clearing overrides and base changes.
    # Targeted writes avoid a fan dip on ordinary slider releases.
    c = HardwareController()
    value = profile.get(field)

    def _record_change(ok):
        if field not in ("tdp", "battery_limit", "brightness"):
            return
        try:
            import timeline
            if field == "tdp":
                summary = (f"TDP {value['pl1']}/{value['pl2']}/{value['pl3']} W"
                           if (isinstance(value, dict) and "pl1" in value and "pl2" in value and "pl3" in value)
                           else f"TDP {value}" if value else "TDP off")
            elif field == "battery_limit":
                summary = f"Charge limit {value}%"
            elif field == "brightness":
                summary = f"Brightness {value}"
            else:
                summary = f"{field} {value}"

            b_val = before
            if field == "brightness" and b_val is None:
                b_val = _read_saved_brightness()

            before_dict = {field: b_val}
            after_dict = {field: value} if ok else before_dict

            timeline.record_setting_change(
                kind=field,
                summary=summary,
                before=before_dict,
                after=after_dict,
                ok=ok,
                path=None,
                defer=True,   # never write the timeline inside a hardware transaction
            )
        except Exception as e:
            logger.warning("timeline recording failed for %s: %s", field, e)

    if field == 'tdp' and value:
        try:
            result = c.apply_tdp(value)
        except Exception:
            _record_change(ok=False)
            raise
        _record_change(ok=True)
    elif field == 'fan_curve' and value:
        import max_fans_state
        if max_fans_state.is_enabled():
            max_fans_state.record_profile_curve(value)
            return 'Saved; Max Fans remains ON'
        result = c.set_fancurve(value)
    elif field == 'undervolt' and value is not None:
        result = c.set_undervolt(value)
    elif field == 'battery_limit' and value is not None:
        try:
            result = c.set_battery_limit(value)
        except Exception:
            _record_change(ok=False)
            raise
        _record_change(ok=True)
    elif field == 'epp' and value is not None:
        result = c.set_epp(value)
    elif field == 'brightness' and value:
        try:
            failed = c.set_brightness(value, devices=devices)
        except Exception:
            _record_change(ok=False)
            raise
        _record_change(ok=not bool(failed))
        result = failed
    else:
        results = profiles.apply(profile, previous=profile, skip_hooks=True)
        if any(ok for step, ok, _ in results if step == "profile"):
            profiles.set_active(profile["name"])
        return results
    return result


@serialized
def set_max_fans(enabled):
    import profiles
    import max_fans_state
    from hardware_controller import HardwareController
    c = HardwareController()
    if enabled:
        info = c.get_fancurve()
        pts = info.get('points') or []
        if len(pts) == 8 and not all(pwm >= 255 for _, pwm, _ in pts):
            max_fans_state.save(saved_curve=[[int(t), int(pwm)] for t, pwm, _ in pts])
        c.set_fans_max()
    else:
        kind, saved = max_fans_state.restore_plan()
        p = profiles.get_profile(profiles.active_name()) or {}
        if kind == 'curve':
            c.set_fancurve_pwm(saved)
        elif kind == 'unknown' and p.get('fan_curve'):
            c.set_fancurve(p['fan_curve'])
        else:
            c.reset_fancurve()
    max_fans_state.save(enabled=bool(enabled))
    return 'Max Fans ' + ('ON' if enabled else 'OFF')


@serialized
def set_field(field, value, expected_profile=None, *, devices=None):
    """Frontend edit entry point, validated against fresh backend selection."""
    import profiles
    client = named_client()
    if client is not None:
        profiles.sync_active_from_backend(client.get_state())
    name = profiles.active_name()
    if expected_profile and name != expected_profile:
        raise ValueError('Not applied: active profile changed; refresh the control')
    p = profiles.get_profile(name)
    if p is None:
        raise ValueError('No active profile')
    p[field] = value
    return edit(p, field, devices=devices)


@serialized
def set_lighting(name, device, setting, brightness):
    import profiles
    from hardware_controller import HardwareController
    client = named_client()
    if client is not None:
        profiles.sync_active_from_backend(client.get_state())
    if name != profiles.active_name():
        raise ValueError('Not applied: active profile changed; refresh the control')
    profiles.set_profile_lighting(name, device, setting, brightness=brightness)
    if setting is None:
        return 'Profile override cleared'
    return HardwareController().set_lighting(device, setting, brightness)


@serialized
def set_screen_backlight(path, raw):
    """Write the sysfs display backlight under the shared hardware lock.

    Display state, not z13ctl state, but every GUI hardware write goes through
    this owner so it cannot interleave with a profile transaction.
    """
    res = host.run(["tee", os.path.join(path, "brightness")], timeout=10, input=f"{raw}\n")
    if res[0] != 0:
        raise RuntimeError(res[1])
    return raw


@serialized
def set_platform_profile(profile, *, z13ctl=None, timeout=None):
    """Switch the firmware platform profile under the shared hardware lock."""
    z13ctl = z13ctl or os.path.expanduser('~/.local/bin/z13ctl')
    timeout = host.Z13CTL_WRITE_TIMEOUT_S if timeout is None else timeout
    return run([z13ctl, 'profile', '--set', profile], timeout=timeout)


@serialized
def recover_performance():
    """Reapply the current named performance state, without hooks or lighting.

    None selects the older watchdog sequence. Named recovery keeps its target
    throughout the base rewrite instead of accidentally selecting a built-in.
    """
    import profiles
    import max_fans_state
    client = named_client()
    if client is None:
        return None
    try:
        profiles.sync_active_from_backend(client.get_state())
        p = profiles.get_profile(profiles.active_name())
        if p is None:
            raise ValueError('No active profile for fan recovery')
        p = dict(p)
        if max_fans_state.is_enabled():
            p['fan_curve'] = [[t, 100] for t in profiles.MAX_FAN_TEMPS]
        client.activate_app_profile(p)
        results = profiles.apply_epp(p)
        failed = [msg for _, ok, msg in results if not ok]
        return 'Fans: named profile re-applied' + ('; ' + '; '.join(failed) if failed else '')
    except (OSError, RuntimeError, ValueError) as exc:
        return f'Fan recovery failed: {exc}'


@serialized
def snapshot(client):
    """Read/reconcile only after any in-flight profile transaction has finished."""
    import profiles
    state = client.get_state()
    # The daemon names a custom profile but does not report its firmware base.
    # Read this cached metadata only on events; never read live PPT values.
    result = host.run(['cat', '/sys/firmware/acpi/platform_profile'], timeout=5, as_deck=True)
    if result.rc == 0 and result.out.strip() in profiles.BASES:
        state['platform_profile'] = result.out.strip()
    profiles.sync_active_from_backend(state)
    return state


@serialized
def mirror_profile(profile):
    """Mirror a saved inactive definition; active edits use the ordered apply."""
    import profiles
    client = named_client()
    if client is None:
        return 'Saved for legacy backend'
    target = client.mapped_profile_name(profile)
    if client.get_state().get('profile') == target:
        return 'Active profile settings already applied'
    client.ensure_named_profile(profile)
    # Persist backend identity so renaming an app profile cannot orphan it.
    current = profiles.get_profile(profile['name'])
    if current is not None:
        current['z13ctl_profile'] = target
        profiles.upsert_profile(current)
    return 'Saved to z13ctl named profile'


@serialized
def delete_named_profile(profile):
    client = named_client()
    if client is not None:
        client.request('profile-delete', set=client.mapped_profile_name(profile))
    return 'Named profile removed'
