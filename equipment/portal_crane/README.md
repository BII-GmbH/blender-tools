# Equipment - Portal Crane

Generates a portal crane model (.fbx), based on a config, that can be animated in dprob/etc. 
Calls Blender headless and injects the config values into a geometry node, further takes care of child<->parent setup.

## How to run

The ```config.json``` file defines the aspects of the portal crane: 
```json
{
  "Dimensions": {
    "Length": 45.0,
    "Width": 8.0,
    "Height": 15.0
  },
  "Offset": {
    "Top Construction Length": 10.0,
    "Legs Width": 12.0,
    "Middle Beam Height": -3.0
  },
  "Optional": {
    "Base Beam Thickness": 0.2,
    "Top Beam Thickness": 0.15,
    "Beam Profile Resolution": 8
  }
}
```
1. Configure the json to your liking.
2. Call ```blender -b portal_cran_v.1.2.blend -P p_crane_config.py```.
3. ```portal_crane.fbx``` will be created.


## Example Output

```terminaloutput
blender -b portal_cran_v.1.2.blend -P p_crane_config.py 
Blender 5.2.2 LTS (hash d13f752e3b9c built 2026-09-15 01:34:58)
00:01.207  blend            | Read blend: "/home/brandnerkasper/Documents/Python/portal crane/portal_cran_v.1.2.blend"
Active object Portal_Crane with geo node Portal-Cran-Configurator
Setting value: Length with value: 25.0.
Setting value: Width with value: 5.0.
Setting value: Height with value: 8.0.
Setting value: Top Construction Length with value: 8.0.
Setting value: Legs Width with value: 2.0.
Setting value: Middle Beam Height with value: -1.0.
Setting value: Base Beam Thickness with value: 0.2.
Setting value: Top Beam Thickness with value: 0.1.
Setting value: Beam Profile Resolution with value: 8.

FBX export starting... '/home/brandnerkasper/Documents/Python/portal crane/portal_crane.fbx'
export finished in 0.0207 sec.

Blender quit
```

