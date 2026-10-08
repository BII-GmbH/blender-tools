#!/usr/bin/env bash

mkdir -p fbx

for laz_file in las_files/*.las; do
    [ -e "$laz_file" ] || continue

    echo "Processing: $laz_file"

    blender \
        -b point_cloud_template.blend \
        -P las_to_blend.py \
        -- "$laz_file" \
        --output-dir fbx
done
