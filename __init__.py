# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name" : "BII Blender Tools",
    "author" : "bii",
    "description" : "",
    "blender" : (2, 80, 0),
    "version" : (0, 0, 3),
    "location" : "",
    "warning" : "",
    "category" : "BII Tools"
}

from . import close_mesh_holes
from . import bii_functions_panel
from . import bulk_assign_ifc_class
from . import bulk_material_dropdown
from . import add_ifc_property
from . import clean_reduce_ifc
from . import upgrade_to_IFC4
from . import export_rail_asset

def register():
    close_mesh_holes.register()
    bii_functions_panel.register()
    bulk_assign_ifc_class.register()
    bulk_material_dropdown.register()
    add_ifc_property.register()
    clean_reduce_ifc.register()
    # upgrade_to_IFC4.register()
    export_rail_asset.register()

def unregister():
    close_mesh_holes.unregister()
    bii_functions_panel.unregister()
    bulk_assign_ifc_class.unregister()
    bulk_material_dropdown.unregister()
    add_ifc_property.unregister()
    clean_reduce_ifc.unregister()
    # upgrade_to_IFC4.unregister()
    export_rail_asset.unregister()

if __name__ == "__main__":
    register()