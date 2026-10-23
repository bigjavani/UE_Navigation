# SPDX-License-Identifier: GPL-3.0-or-later
"""Keymap installation / restoration.

The add-on adds its own items to the addon keyconfig ("3D View") and,
when the matching preferences are enabled, temporarily deactivates a
small number of *default* items in the user keyconfig so the UE scheme
takes effect:

* ``view3d.rotate`` on Middle Mouse (Blender's default MMB orbit) - UE
  uses MMB-drag for panning and orbiting only happens with Alt+LMB.
* The RMB-press context-menu items of the 3D-view mode keymaps - UE
  starts fly/look the moment RMB goes down.  The add-on replays the
  exact same menu on a *clean* RMB click (press + release without
  navigation), preserving Blender's context menu behavior.

All takeovers are pattern-based and fully reversible: disabling the
add-on simply re-activates every binding it switched off (heal).
"""

import bpy

from . import core

# (keymap name, space type) of mode keymaps whose RMB menus we manage.
_MENU_FILTER = ("wm.call_menu", "wm.call_menu_pie")


def _kc_addon():
    wm = bpy.context.window_manager
    kc = getattr(wm.keyconfigs, "addon", None)
    return kc


def _kc_user():
    wm = bpy.context.window_manager
    return getattr(wm.keyconfigs, "user", None)


def _kmi_matches_clean_rmb_menu(kmi):
    if kmi.idname not in _MENU_FILTER:
        return False
    if kmi.type != 'RIGHTMOUSE' or kmi.value not in ('PRESS', 'ANY'):
        return False
    if kmi.alt or kmi.ctrl or kmi.shift or kmi.oskey:
        return False
    if kmi.key_modifier != 'NONE':
        return False
    name = getattr(kmi.properties, "name", "") if kmi.properties else ""
    return isinstance(name, str) and name.startswith("VIEW3D_MT")


# Window/screen-level RMB handlers that consume right-press before space
# keymaps are evaluated (Blender 5.x moved the generic region context menu
# here).  Every other editor ships its own RMB binding, so disabling these
# effectively only affects the 3D Viewport - where UE-style fly needs RMB.
_WINDOW_RMB_CONSUMERS = ("screen.area_options", "screen.region_context_menu")


def _kmi_matches_wheel_zoom(kmi):
    # The add-on provides its own wheel dolly; native wheel zoom items are
    # switched off while enabled (healed on disable).  This also guarantees
    # wheel zoom exists when the user keyconfig is degraded.
    return (kmi.idname in ('view3d.zoom', 'view3d.dolly')
            and str(kmi.type) in ('WHEELINMOUSE', 'WHEELOUTMOUSE')
            and not (kmi.alt or kmi.ctrl or kmi.shift or kmi.oskey)
            and kmi.key_modifier == 'NONE')


def _kmi_matches_window_rmb_consumer(kmi):
    return (kmi.idname in _WINDOW_RMB_CONSUMERS
            and kmi.type == 'RIGHTMOUSE'
            and kmi.value in ('PRESS', 'ANY')
            and not (kmi.alt or kmi.ctrl or kmi.shift or kmi.oskey)
            and kmi.key_modifier == 'NONE')


def _kmi_matches_mmb_orbit(kmi):
    if kmi.idname != 'view3d.rotate':
        return False
    if kmi.alt or kmi.ctrl or kmi.shift or kmi.oskey:
        return False
    if kmi.key_modifier != 'NONE':
        return False
    # Blender 4.x uses EVT_TWEAK_M/ANY, Blender 5.x uses MIDDLEMOUSE/CLICK_DRAG.
    if kmi.type in ('MIDDLEMOUSE', 'EVT_TWEAK_M') and kmi.value in ('PRESS', 'ANY', 'CLICK_DRAG'):
        return True
    return False


def _props_dict(kmi):
    props = {}
    if kmi.properties is None:
        return props
    try:
        rna = kmi.properties.rna_type.properties
    except Exception:
        return props
    for identifier in rna.keys():
        if identifier in ("rna_type",):
            continue
        try:
            props[identifier] = getattr(kmi.properties, identifier)
        except Exception:
            pass
    return props


def _new_drag_item(km, idname, mouse, **mods):
    """Create a mouse drag item across Blender 4.x (EVT_TWEAK_*) and
    5.x (CLICK_DRAG) keymap systems."""
    letter = {'LEFTMOUSE': 'L', 'MIDDLEMOUSE': 'M', 'RIGHTMOUSE': 'R'}[mouse]
    try:
        return km.keymap_items.new(idname, mouse, 'CLICK_DRAG', **mods)
    except TypeError:
        pass
    except RuntimeError:
        pass
    return km.keymap_items.new(idname, 'EVT_TWEAK_' + letter, 'ANY', **mods)


addon_keymaps = []       # [(keymap, [items...])]
_menu_props_blacklist = {"rna_type"}


def install():
    uninstall_items()
    prefs = core.get_prefs()
    # Heal leftovers first (e.g. preferences saved while the add-on was
    # enabled, or a failed restore from an earlier version), so the disable
    # pass below records a consistent set.
    heal_keymaps(verbose=False)
    kc = _kc_user()
    if kc is None:
        kc = _kc_addon()
    if kc is None:
        return
    km = kc.keymaps.get("3D View")
    if km is None:
        # NEVER create a "3D View" keymap in the user keyconfig: a keymap
        # created before Blender materialized the factory defaults would be
        # saved into preferences and shadow the real defaults forever
        # (this is what permanently killed the RMB menu in old versions).
        # The deferred installer retries until the real keymap exists.
        if kc is _kc_user() and bpy.app.background is False:
            print("[ue_navigation] '3D View' keymap not found - deferring")
            return
        km = kc.keymaps.new(name="3D View", space_type='VIEW_3D', region_type='WINDOW')
    # Remove leftovers from a previous crashed session first (user keyconfig
    # items are saved with preferences, so a crash while enabled would leave
    # our bindings behind).  Also drop legacy JSON backups - the stateless
    # heal made them obsolete.
    for sweep_km in kc.keymaps:
        if sweep_km.name == "3D View":
            _remove_own_items(sweep_km)
    core.backup_delete()
    items = []
    # RMB press -> UE fly/look (fires immediately, like UE).
    items.append(km.keymap_items.new("view3d.unreal_fly", 'RIGHTMOUSE', 'PRESS'))
    # Alt+LMB drag -> orbit, Alt+RMB drag -> dolly, Alt+MMB drag -> track.
    items.append(_new_drag_item(km, "view3d.unreal_orbit", 'LEFTMOUSE', alt=True))
    items.append(_new_drag_item(km, "view3d.unreal_dolly", 'RIGHTMOUSE', alt=True))
    items.append(_new_drag_item(km, "view3d.unreal_pan", 'MIDDLEMOUSE', alt=True))
    # Plain MMB drag -> pan (UE). (Requires deactivating default MMB orbit.)
    if prefs is None or prefs.mmb_pan:
        items.append(_new_drag_item(km, "view3d.unreal_pan", 'MIDDLEMOUSE'))
    # Optional: UE perspective LMB-drag crash-cam (off by default; Blender 5
    # reserves LMB-drag for its own select/move tooling).
    if prefs is not None and prefs.crash_cam:
        items.append(_new_drag_item(km, "view3d.unreal_crashcam", 'LEFTMOUSE'))
    # F -> focus (any mode; edit-mode 'F' shortcuts live in earlier mode
    # keymaps and keep working).
    if prefs is None or prefs.focus_key:
        items.append(km.keymap_items.new("view3d.unreal_focus", 'F', 'PRESS'))
    # Plain wheel -> guaranteed UE-style dolly (the native binding may be
    # missing from degraded user keyconfigs).
    kmi_in = km.keymap_items.new("view3d.unreal_wheel_zoom", 'WHEELINMOUSE', 'PRESS')
    kmi_in.properties.delta = -1
    kmi_out = km.keymap_items.new("view3d.unreal_wheel_zoom", 'WHEELOUTMOUSE', 'PRESS')
    kmi_out.properties.delta = 1
    items.extend((kmi_in, kmi_out))
    addon_keymaps.append((km, items))
    _install_user_keymap_changes(prefs)


_OWN_ID_PREFIX = "view3d.unreal_"


def _remove_own_items(km):
    """Drop any of our keymap items left over from a previous session."""
    for kmi in list(km.keymap_items):
        if kmi.idname.startswith(_OWN_ID_PREFIX):
            try:
                km.keymap_items.remove(kmi)
            except Exception:
                pass


def _pattern_matches(kmi):
    """True for every item this add-on manages (disables while enabled)."""
    return (_kmi_matches_mmb_orbit(kmi)
            or _kmi_matches_clean_rmb_menu(kmi)
            or _kmi_matches_window_rmb_consumer(kmi)
            or _kmi_matches_wheel_zoom(kmi))


def heal_keymaps(verbose=True):
    """Re-activate every managed item that is currently inactive.

    Covers states where the user preferences were saved while the add-on
    was enabled (items persisted as disabled), or an old add-on version
    failed to restore.  Called automatically on install; also exposed as
    the explicit "Repair default keymaps" command."""
    kc_user = _kc_user()
    if kc_user is None:
        return 0
    healed = 0
    for km in kc_user.keymaps:
        for kmi in km.keymap_items:
            if _pattern_matches(kmi) and not kmi.active:
                kmi.active = True
                healed += 1
    if healed and verbose:
        print("[ue_navigation] repaired %d default keymap item(s)" % healed)
    return healed


def _install_user_keymap_changes(prefs):
    """Stateless takeover: capture the mode context menus for replay, then
    switch off every conflicting default binding by pattern.  There is no
    bookkeeping to lose - disabling is undone by simply re-activating every
    pattern match (heal_keymaps) when the add-on is disabled."""
    core.MENU_REPLAY.clear()
    kc_user = _kc_user()
    if kc_user is None:
        # Background mode / factory: no user keymap defaults to patch.
        return
    for km in kc_user.keymaps:
        for kmi in km.keymap_items:
            disable = False
            if (prefs is None or prefs.mmb_pan) and _kmi_matches_mmb_orbit(kmi):
                disable = True
            if _kmi_matches_clean_rmb_menu(kmi):
                core.MENU_REPLAY.append({
                    "keymap": km.name,
                    "idname": kmi.idname,
                    "menu": getattr(kmi.properties, "name", ""),
                    "props": _props_dict(kmi),
                })
                disable = True
            if _kmi_matches_window_rmb_consumer(kmi):
                # Replayed on clean RMB clicks via screen.region_context_menu.
                disable = True
            if _kmi_matches_wheel_zoom(kmi):
                # Our own wheel dolly replaces the native one while enabled.
                disable = True
            if disable:
                kmi.active = False


def _restore_user_keymap_changes():
    """Re-activate every binding the add-on switched off (pattern-based,
    nothing bookkept - immune to module reloads and prefs reloads)."""
    heal_keymaps(verbose=False)
    core.MENU_REPLAY.clear()


def _matches_descriptor(kmi, desc):
    return (kmi.idname == desc["idname"]
            and str(kmi.type) == desc["type"]
            and str(kmi.value) == desc["value"]
            and bool(kmi.alt) == desc["alt"]
            and bool(kmi.ctrl) == desc["ctrl"]
            and bool(kmi.shift) == desc["shift"]
            and bool(kmi.oskey) == desc["oskey"])


def uninstall_items():
    """Remove our keymap items.  Stored references can go stale when
    Blender reloads the keyconfig between install and uninstall, so a
    fresh pattern sweep over the (possibly recreated) keymap follows."""
    for km, items in addon_keymaps:
        for kmi in items:
            try:
                km.keymap_items.remove(kmi)
            except Exception:
                pass
    addon_keymaps.clear()
    for kc in (_kc_user(), _kc_addon()):
        if kc is None:
            continue
        for km in kc.keymaps:
            if km.name == "3D View":
                _remove_own_items(km)


def uninstall():
    uninstall_items()
    _restore_user_keymap_changes()


def refresh():
    """Called when a keymap-affecting preference changes."""
    try:
        uninstall()
        install()
    except Exception:
        pass
