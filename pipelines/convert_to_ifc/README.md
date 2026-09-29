# Blender -> IFC Converter

This pipeline converts a .blend file to a .ifc file. It retains object names, scene hierachy via groups and materials.

## How to run

```bash
blender -b your_file.blend -P blend_to_ifc.py
```

This generates a your_file.ifc file.

### Example

There is a ```ifc_test.blend``` file to showcase the abilities of this pipeline. Feel free to test it first with that file.

## Disclaimer
The module ```ifcopenshell``` needs to be installed within the bundled Blender's Python version. 
You **can** add it via the [bonsai](https://extensions.blender.org/add-ons/bonsai/) add-on. 
But it can also be installed directly into the bundled version via pip!