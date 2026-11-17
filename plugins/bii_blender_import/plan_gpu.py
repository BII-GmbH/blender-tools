"""GPU memory for plan images.

The viewport keeps every plan image it shows as a GPU texture: 4 bytes per
pixel, plus a third for the mipmaps. A plan at 16384 x 11600 px takes about
1 GB. When the GPU memory runs out, Blender (5.x with Vulkan) crashes while
drawing instead of reporting it. So the imports check first how much GPU
memory is left, and import only what fits.

How much is left: NVIDIA GPUs report their free memory (nvidia-smi). Other
GPUs only their total memory (Windows registry), from which the plans the
scene already shows and a reserve for the rest of the scene are taken off.
Without either, a fixed budget is assumed.
"""

import math
import subprocess
import sys

import bpy

RESERVE_MB = 300  # left free for framebuffers, a larger window, other programs
SCENE_MB = 1024  # assumed for the rest of the scene when only the total memory is known
UNKNOWN_TOTAL_MB = 4096  # assumed total when the GPU memory cannot be read
MIB = 1.048576  # nvidia-smi counts MiB; here MB are 10^6 bytes, as in the panel
TEXTURE_LIMITS = {'CLAMP_8192': 8192, 'CLAMP_4096': 4096, 'CLAMP_2048': 2048,
                  'CLAMP_1024': 1024, 'CLAMP_512': 512, 'CLAMP_256': 256, 'CLAMP_128': 128}


def texture_limit():
    """Preferences > Viewport > Texture Limit Size in pixels, or None."""
    try:
        return TEXTURE_LIMITS.get(bpy.context.preferences.system.gl_texture_limit)
    except AttributeError:
        return None


def texture_megabytes(width, height):
    """GPU memory of an image of width x height px: RGBA 8 bit with mipmaps."""
    limit = texture_limit()
    if limit and max(width, height) > limit:
        factor = limit / max(width, height)
        width, height = width * factor, height * factor
    return width * height * 4 * 4 / 3 / 1e6


def _renderer():
    try:
        import gpu
        return gpu.platform.renderer_get().lower()
    except Exception:  # background mode has no GPU context
        return ""


def _matching(rows, renderer):
    """The row (name, ...) of the GPU Blender draws with, else the first one."""
    for row in rows:
        if row[0].lower() and row[0].lower() in renderer:
            return row
    return rows[0] if rows else None


def _nvidia():
    """(free MB, total MB) of the NVIDIA GPU Blender uses, or None."""
    cmd = ["nvidia-smi", "--query-gpu=name,memory.free,memory.total",
           "--format=csv,noheader,nounits"]
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: no console flash
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            rows.append((parts[0], float(parts[1]) * MIB, float(parts[2]) * MIB))
        except (IndexError, ValueError):
            continue
    row = _matching(rows, _renderer())
    return (row[1], row[2]) if row else None


def _windows_total():
    """Total MB of the GPU Blender uses, from the Windows registry, or None."""
    if sys.platform != "win32":
        return None
    import winreg
    path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    rows = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return None
    with root:
        for i in range(64):
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, name) as key:
                    desc = winreg.QueryValueEx(key, "DriverDesc")[0]
                    try:
                        size = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                    except OSError:
                        size = winreg.QueryValueEx(key, "HardwareInformation.MemorySize")[0]
                        if isinstance(size, bytes):
                            size = int.from_bytes(size[:4], "little")
            except (OSError, TypeError):
                continue
            if isinstance(size, int) and size > 0:
                rows.append((str(desc), size / 1e6))
    row = _matching(rows, _renderer())
    return row[1] if row else None


def shown_plan_megabytes(exclude=()):
    """GPU memory of the images of the plans the viewport can show (visible plans)."""
    seen, total = set(), 0.0
    for obj in bpy.context.view_layer.objects:
        pg = getattr(obj, "bii_plan", None)
        if pg is None or not pg.is_plan or pg.image is None or obj in exclude:
            continue
        if pg.image.name in seen or not obj.visible_get():
            continue
        seen.add(pg.image.name)
        total += texture_megabytes(*pg.image.size)
    return total


def budget():
    """(MB of GPU memory new plan images may take, how it was found).

    Conservative: with the free memory known, the plans that are visible but
    not drawn yet (no texture yet) still count, since they get one as soon
    as the viewport shows them.
    """
    if bpy.app.background:  # no viewport, no textures
        return math.inf, "no viewport"
    plans = shown_plan_megabytes()
    nvidia = _nvidia()
    if nvidia is not None:
        free, total = nvidia
        return (min(free - RESERVE_MB, total - RESERVE_MB - SCENE_MB - plans),
                f"{free:.0f} of {total:.0f} MB free on the GPU")
    total = _windows_total()
    if total:
        return (total - RESERVE_MB - SCENE_MB - plans,
                f"GPU with {total:.0f} MB, {plans:.0f} MB for the plans shown now")
    return (UNKNOWN_TOTAL_MB - RESERVE_MB - SCENE_MB - plans,
            f"GPU memory unknown, {UNKNOWN_TOTAL_MB} MB assumed")


def fitting_resolution(width, height, megabytes, resolutions):
    """The largest long side from resolutions (descending) whose image fits, or None."""
    for long_side in resolutions:
        factor = min(1.0, long_side / max(width, height))
        if texture_megabytes(width * factor, height * factor) <= megabytes:
            return long_side
    return None


def advice():
    return ("Choose a lower Resolution, lower the resolution of other plans (Change "
            "Resolution), or hide plans you do not need")
