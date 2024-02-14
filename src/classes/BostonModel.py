from pathlib import Path
from typing import Union
from ..utils.constants import LOCAL_CRS_ORIGIN_GEO, LOCAL_CRS_ORIGIN_STATE
from ..utils.obj_utils import create_ground, get_mi_dict
from ..utils.geo_utils import gdf2localcrs
from ..utils.utils import print_eta

import mitsuba as mi
import open3d as o3d
import geopandas as gpd
import shapely as shp
import time

class BostonModel:
    def __init__(
        self,
        dataset_dir: Union[Path, str] = Path("dataset"),
    ) -> None:
        # Initialize the model from existing local directories
        if isinstance(dataset_dir, Path):
            self.dataset_dir = dataset_dir
        else:
            self.dataset_dir = Path(dataset_dir)

        self.mesh_dir = dataset_dir.joinpath("meshes")

        self.tiles_dict = self._enumerate_scenes()
        self.tile_names = list(self.tiles_dict.keys())

        self.origin_epsg4326 = LOCAL_CRS_ORIGIN_GEO
        self.origin_epsg2249 = LOCAL_CRS_ORIGIN_STATE

        self.flat = (
            True  # for now, we don't support ground elevation different from zero
        )

    # def _check_in_model_dir(self):

    def _enumerate_scenes(self) -> dict:
        scenes_dict = {}
        for scene_file_path in self.dataset_dir.iterdir():
            if (scene_file_path.is_file()) and (scene_file_path.suffix == ".geojson") and ("tileinfo" not in scene_file_path.stem):
                scene_name = scene_file_path.stem

                mi_scene_path = scene_file_path.resolve().with_suffix(".xml")
                tile_info_path = scene_file_path.resolve().with_name(
                    f"{scene_name}_tileinfo.geojson"
                )
                # scene = mi.load_file(str(mi_scene_path))
                # tile_info = gpd.read_file(tile_scene_file)
                scenes_dict[scene_name] = {
                    "tileinfo_path": tile_info_path,
                    "geo_scene_path": scene_file_path,
                    "mi_scene_path": mi_scene_path,
                    # "models": [model.id() for model in scene.shapes()],
                }

        n_scenes = len(scenes_dict)
        self.n_tiles = n_scenes
        print(f"{n_scenes} scenes imported.")

        return scenes_dict

    def convert_to_ascii(self, out_mesh_dir: Union[str, Path] = None):
        if not out_mesh_dir:
            out_mesh_dir = self.mesh_dir
        if not out_mesh_dir.is_dir():
            out_mesh_dir.mkdir(parents=True, exist_ok=True)

        for model_in_path in self.mesh_dir.iterdir():
            model_in = o3d.io.read_triangle_mesh(str(model_in_path))

            model_out_path = out_mesh_dir.joinpath(model_in_path.stem + "_ascii.ply")
            o3d.io.write_triangle_mesh(str(model_out_path), model_in, write_ascii=True)

    def generate_simplified_dataset(
        self,
        scene_in_name: str,
        out_dir: Union[str, Path],
        precision=0.5,
        smooth=False,
        n_smooth_iterations=1,
    ):
        if scene_in_name not in self.tile_names:
            raise KeyError(
                f"scene_name must correspond to one of the scenes name in {self.dataset_dir}: {self.tile_names}\nInstead: {scene_in_name}"
            )
        scene_in_dict = self.tiles_dict[scene_in_name]

        if self.dataset_dir == out_dir:
            raise FileExistsError(
                "Specify a different folder for the simplified dataset.\nOverwriting the original dataset is not supported."
            )

        out_mesh_dir = out_dir.joinpath("meshes")
        if not out_mesh_dir.is_dir():
            out_mesh_dir.mkdir(exist_ok=True, parents=True)

        n_triangles_in = 0
        n_vert_in = 0
        n_triangles_out = 0
        n_vert_out = 0
        for model_name in scene_in_dict["models"]:
            model_in_path = self.mesh_dir.joinpath(model_name + ".ply")
            model_in = o3d.io.read_triangle_mesh(str(model_in_path.resolve()))
            n_triangles_in = n_triangles_in + len(model_in.triangles)
            n_vert_in = n_vert_in + len(model_in.vertices)

            if smooth:
                model_in = model_in.filter_smooth_simple(
                    number_of_iterations=n_smooth_iterations
                )

            model_out = model_in.simplify_vertex_clustering(voxel_size=precision)
            n_triangles_out = n_triangles_out + len(model_out.triangles)
            n_vert_out = n_vert_out + len(model_out.vertices)

            model_out_path = out_mesh_dir.joinpath(model_name + ".ply")
            o3d.io.write_triangle_mesh(str(model_out_path.resolve()), model_out)

        # copy the scene file
        scene_in_path = self.dataset_dir.joinpath(scene_in_name + ".xml")

        scene_out_path = out_dir.joinpath(scene_in_path.name)
        scene_out_path.write_text(scene_in_path.read_text())

        scene_out_path = out_dir.joinpath(scene_in_path.with_suffix(".geojson").name)
        scene_out_path.write_text(scene_in_path.with_suffix(".geojson").read_text())

        print(f"Input scene has {n_vert_in} vertices and {n_triangles_in} triangles.")
        print(
            f"Output scene has {n_vert_out} vertices and {n_triangles_out} triangles."
        )
        print(f"Saved in {scene_out_path}")

    def generate_scene_from_model_gdf(self, model_gdf, scene_center, scene_name) -> None:
        out_scene_path = self.dataset_dir
        meshes_path = self.mesh_dir

        scene_dict_mi = {
            "type": "scene",
            "integrator": {
                "type": "path",
            },
            "light": {"type": "constant"},
            "mat-itu_brick": {
                "type": "twosided",
                "bsdf": {
                    "type": "diffuse",
                    "reflectance": {
                        "type": "rgb",
                        "value": [0.401968, 0.111874, 0.086764],
                    },
                },
            },
            "mat-itu_concrete": {
                "type": "twosided",
                "bsdf": {
                    "type": "diffuse",
                    "reflectance": {
                        "type": "rgb",
                        "value": [0.539479, 0.539479, 0.539480],
                    },
                },
            },
            "mat-itu_medium_dry_ground": {
                "type": "twosided",
                "bsdf": {
                    "type": "diffuse",
                    "reflectance": {
                        "type": "rgb",
                        "value": [65 / 255, 60 / 255, 60 / 255],
                    },
                },
            },
        }
        print(f"Converting GDF to local CRS...({model_gdf.shape})")
        t0 = time.time()
        model_gdf = gdf2localcrs(model_gdf)
        t1 = time.time()
        print(f"Done in {t1-t0} s.")
        scene_bounds = model_gdf.total_bounds
        scene_size = scene_bounds[2]-scene_bounds[0]
        print(f"Scene size is {scene_size}x{scene_size} m.")

        # load frame obj and use it as (flat) ground
        frame_name = f"{scene_name}_Frame"
        base_rect_path = meshes_path.joinpath("rectangle.ply")
        frame_material = "mat-itu_medium_dry_ground"
        out_model_dict, _ = create_ground(
            frame_material,
            0,
            0,
            0,
            scene_size/2+100,  # slightly increase the boundary
            base_rect_path,
            meshes_path.joinpath(frame_name + ".ply"),
            self.dataset_dir,
        )

        model_dict = out_model_dict.copy()

        scene_dict_mi["ground"] = model_dict

        model_gdf["center"] = model_gdf.centroid
        n_tri_list = []
        n_models_tile = model_gdf.shape[0]
        times = []
        for row_idx,model_row in model_gdf.iterrows():
            t0 = time.time()
            model_name = model_row["Model_ID"].values[0]
            model_mesh_path = meshes_path.joinpath(model_name).with_suffix(".ply")

            model_struct_type = model_row["StructType"].values[0]
            if model_struct_type == "Wall":
                model_material = "mat-itu_brick"
            else:
                model_material = "mat-itu_concrete"

            center_x, center_y = model_row["center"].values[0]
            out_model_dict, n_tri = get_mi_dict(
                model_mesh_path, center_x, center_y, 0, out_scene_path, model_material
            )
            n_tri_list.append(n_tri)

            # add model to the tile scene
            tile_model_dict = out_model_dict.copy()
            tile_model_dict["to_world"] = tile_model_dict[
                "to_world"
            ] @ mi.ScalarTransform4f.translate([-scene_center[0], -scene_center[1], 0])
            scene_dict_mi[model_name] = tile_model_dict

            t1 = time.time()

            print_eta(t0, t1, times, row_idx, n_models_tile)

        tile_n_tri = sum(n_tri_list)

        output_tile_scene_path = self.out_dataset_dir.joinpath(scene_name + ".xml")
        mi.xml.dict_to_xml(scene_dict_mi, str(output_tile_scene_path.resolve()))
        model_gdf.to_file(
            output_tile_scene_path.with_suffix(".geojson"), driver="GeoJSON"
        )

        output_tile_info_path = self.out_dataset_dir.joinpath(
            scene_name + "_tileinfo" + ".geojson"
        )
        tile_info = gpd.GeoDataFrame(geometry=shp.Box(**model_gdf.total_bounds))
        tile_info["Centr_X_m"] = scene_center[0]
        tile_info["Centr_Y_m"] = scene_center[1]
        tile_info["n_models"] = n_models_tile
        tile_info["n_triangles"] = tile_n_tri
        tile_info.to_file(output_tile_info_path, driver="GeoJSON")

        print(
            f"Scene {scene_name} imported. There were {n_models_tile} models ({tile_n_tri} triangles)."
        )
