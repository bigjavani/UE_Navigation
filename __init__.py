# SPDX-License-Identifier: GPL-3.0-or-later
"""Unreal Engine viewport navigation for Blender.

Recreates UE's editor viewport navigation scheme:

* RMB hold + mouse ......... look around (free look)
* RMB + W/S/A/D / arrows ... fly (WASD)
* RMB + E/Q / PgUp / PgDn .. up / down in world space
* RMB + R/F ................ up / down in camera space
* RMB + Z/C ................ raise / lower field of view
* RMB + scroll ............. fly speed
* LMB while RMB held ....... pan
* F ........................ focus selection (sets orbit pivot)
* Alt + LMB drag ........... orbit around pivot
* Alt + RMB drag ........... dolly toward pivot
* Alt + MMB drag / MMB ....  track / pan
* Scroll (no buttons) ...... dolly zoom (Blender default, matches UE)
* RMB click ................ context menu (unchanged)
"""

bl_info = {
    "name": "Unreal Engine Navigation",
    "author": "UE Nav",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport",
    "description": "Unreal Engine style viewport navigation: RMB+WASD fly, "
                   "Alt+LMB orbit, Alt+RMB dolly, Alt+MMB track, F focus, "
                   "speed on RMB+wheel",
    "doc_url": "",
    "category": "3D View",
}

import bpy

from . import core, keymaps, operators, preferences

_modules = (preferences, operators)

_install_pending = False


_install_tries = 0


def _keymap_ready():
    """True once the user '3D View' keymap carries the factory defaults."""
    kc = bpy.context.window_manager.keyconfigs.user
    km = kc.keymaps.get("3D View") if kc else None
    return km is not None and len(km.keymap_items) >= 10


def _delayed_install():
    """Perform keymap surgery once the user keyconfig is fully loaded.

    When the add-on is auto-enabled during Blender's startup, the user
    keyconfig has not been materialized yet (mode-keymap defaults and the
    previously saved item states load later), so installing immediately
    would miss items and fail to heal a prefs file that was saved while
    the add-on was enabled.  This retry loop waits (up to ~30 s) until the
    factory defaults are present, then installs."""
    global _install_pending, _install_tries
    if not bpy.app.background and not _keymap_ready():
        if _install_tries < 60:
            _install_tries += 1
            return 0.5  # retry
        print("[ue_navigation] keymap defaults never loaded - "
              "installing anyway (use Repair if navigation behaves oddly)")
    _install_pending = False
    try:
        keymaps.install()
    except Exception:
        import traceback
        traceback.print_exc()
    return None


def register():
    global _install_pending
    for m in _modules:
        m.register()
    core.PIVOT["valid"] = False
    core.ACTIVE.clear()
    if bpy.app.background:
        # Background mode has no real keymap defaults and no timer loop;
        # tests rely on the keymap being present right after register().
        keymaps.install()
    else:
        _install_pending = True
        bpy.app.timers.register(_delayed_install, first_interval=0.5)
    core.hud_register()


def unregister():
    global _install_pending
    # End any still-running modal operators gracefully.
    for key in list(core.ACTIVE.keys()):
        op = core.ACTIVE.get(key)
        try:
            context = bpy.context
            if op is not None and hasattr(op, "_timer") and op._timer is not None:
                context.window_manager.event_timer_remove(op._timer)
                op._timer = None
        except Exception:
            pass
        core.ACTIVE.pop(key, None)
    core.hud_unregister()
    if _install_pending:
        # Disabled before the deferred install ran: nothing was disabled by
        # us this session, but re-activate any managed items that a prefs
        # file saved while the add-on was enabled left disabled.
        _install_pending = False
        try:
            bpy.app.timers.unregister(_delayed_install)
        except Exception:
            pass
        try:
            keymaps.heal_keymaps(verbose=False)
        except Exception:
            pass
    else:
        keymaps.uninstall()
    for m in reversed(_modules):
        m.unregister()
