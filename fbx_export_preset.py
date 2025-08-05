import bpy
import os

def register():
    # Path to the user's presets folder
    preset_dir = bpy.utils.user_resource('SCRIPTS', path="presets/operator/export_scene.fbx", create=True)
    preset_path = os.path.join(preset_dir, "🏗️_dProB_defaults.py")

    if os.path.isfile(preset_path):
        return

    # Contents of the preset
    preset_contents = '''\
import bpy
op = bpy.context.active_operator

op.use_custom_props = True
op.path_mode = 'COPY'
op.embed_textures = True
'''

    # Write the preset file
    with open(preset_path, 'w') as f:
        f.write(preset_contents)