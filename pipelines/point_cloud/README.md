# Point Cloud Converter

This automation converts .las files into .blend scenes by using a geometry node to convert the point cloud to a mesh.

## How to run
1. [Blender](https://www.blender.org/download/) has to be installed (5.x)
2. add the .las file to this directory
3. in terminal call:
```bash
blender -b point_cloud_template.blend -P las_to_blend.py -- your_file.las
```

This should create a .blend file with the name of the .las file that contains the point cloud converted to a mesh.

## Disclaimer

[Point Cloud I/O](https://extensions.blender.org/add-ons/point-cloud-io/) needs to be installed!