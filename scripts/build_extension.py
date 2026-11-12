"""Build a Blender extension into zips.

Downloads the wheels listed in the extension's blender_manifest.toml from
PyPI (they are not in git), then runs Blender's extension validate and build.
With "platforms" in the manifest, it builds one zip per platform.

    python scripts/build_extension.py plugins/bii_blender_tools
    python scripts/build_extension.py plugins/bii_blender_tools --blender path/to/blender --output dist

Prints the paths of the built zips, one per line, at the end. Needs Python
3.11+ with pip, and Blender 4.2+ (--blender, the BLENDER environment
variable, or "blender" on PATH).
"""

import argparse
import os
import subprocess
import sys
import tomllib


def download_wheel(wheel_path):
    """Download one wheel from PyPI by its file name, unless it is there."""
    if os.path.isfile(wheel_path):
        return
    filename = os.path.basename(wheel_path)
    # {name}-{version}-{python tag}-{abi tag}-{platform tag}.whl
    name, version, python_tag, abi_tag, platform_tag = filename[:-4].split("-")
    # "py3-none" wheels fit any Python 3 (3.11 is the oldest in Blender 4.2+);
    # compressed platform tags ("manylinux_2_27_x86_64.manylinux_2_28_x86_64")
    # list several platforms.
    python_version = "3.11" if python_tag.startswith("py") else python_tag.removeprefix("cp")
    abi = [] if abi_tag == "none" else ["--abi", abi_tag]
    platforms = [arg for tag in platform_tag.split(".") for arg in ("--platform", tag)]
    print(f"Downloading {filename}", flush=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", f"{name}=={version}",
         "--no-deps", "--only-binary=:all:", "--dest", os.path.dirname(wheel_path),
         "--python-version", python_version, "--implementation", "cp",
         *abi, *platforms,
         "--disable-pip-version-check", "--quiet"],
        check=True)
    if not os.path.isfile(wheel_path):
        sys.exit(f"pip did not download {filename}; check the name in blender_manifest.toml")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source", help="extension folder with blender_manifest.toml")
    p.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    p.add_argument("--output", default="dist", help="folder for the zips (default: dist)")
    a = p.parse_args()

    source = os.path.abspath(a.source)
    with open(os.path.join(source, "blender_manifest.toml"), "rb") as f:
        manifest = tomllib.load(f)
    for wheel in manifest.get("wheels", []):
        download_wheel(os.path.normpath(os.path.join(source, wheel)))

    output = os.path.abspath(a.output)
    os.makedirs(output, exist_ok=True)
    before = set(os.listdir(output))
    subprocess.run([a.blender, "--factory-startup", "--command", "extension", "validate", source],
                   check=True)
    # One zip per platform when the manifest lists platforms (extensions with
    # wheels), otherwise one zip for all.
    split = ["--split-platforms"] if manifest.get("platforms") else []
    subprocess.run([a.blender, "--factory-startup", "--command", "extension", "build",
                    "--source-dir", source, "--output-dir", output, *split],
                   check=True)

    prefix = f"{manifest['id']}-{manifest['version']}"
    built = sorted(f for f in os.listdir(output)
                   if f.startswith(prefix) and f.endswith(".zip") and f not in before)
    if not built:  # an earlier build of the same version is overwritten
        built = sorted(f for f in os.listdir(output) if f.startswith(prefix) and f.endswith(".zip"))
    for f in built:
        print(os.path.join(output, f))


if __name__ == "__main__":
    main()
