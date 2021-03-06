import argparse
import logging
import shutil
import sys
from datetime import datetime as dt

import pandas as pd

import powergenome
from powergenome.generators import GeneratorClusters
from powergenome.load_profiles import load_curves
from powergenome.params import DATA_PATHS
from powergenome.transmission import agg_transmission_constraints, transmission_line_distance
from powergenome.util import init_pudl_connection, load_settings, get_git_hash

if not sys.warnoptions:
    import warnings
    warnings.simplefilter("ignore")


def parse_command_line(argv):
    """
    Parse command line arguments. See the -h option.

    :param argv: arguments on the command line must include caller file name.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-sf",
        "--settings_file",
        dest="settings_file",
        type=str,
        default="pudl_data_extraction.yml",
        help="Specify a YAML settings file.",
    )
    parser.add_argument(
        "-rf",
        "--results_folder",
        dest="results_folder",
        type=str,
        default=dt.now().strftime("%Y-%m-%d %H.%M.%S"),
        help="Specify the results subfolder to write output",
    )
    parser.add_argument(
        "-a",
        "--all_units",
        dest="all_units",
        type=bool,
        default=True,
        help="Save information on all individual units (boolean)",
    )
    arguments = parser.parse_args(argv[1:])
    return arguments


def main():

    args = parse_command_line(sys.argv)

    out_folder = DATA_PATHS["results"] / args.results_folder
    out_folder.mkdir(exist_ok=True)

    # Create a logger to output any messages we might have...
    logger = logging.getLogger(powergenome.__name__)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        # More extensive test-like formatter...
        "%(asctime)s [%(levelname)8s] %(name)s:%(lineno)s %(message)s",
        # This is the datetime format string.
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    filehandler = logging.FileHandler(out_folder / "log.txt")
    filehandler.setFormatter(formatter)
    logger.addHandler(filehandler)

    git_hash = get_git_hash()
    logger.info(f'Current git hash is {git_hash}')

    logger.info("Reading settings file")
    settings = load_settings(path=args.settings_file)

    # Copy the settings file to results folder
    shutil.copy(args.settings_file, out_folder)

    logger.info("Initiating PUDL connections")
    pudl_engine, pudl_out = init_pudl_connection(freq="YS")

    # Make sure everything in model_regions is either an aggregate region
    # or an IPM region. Will need to change this once we start using non-IPM
    # regions.
    ipm_regions = pd.read_sql_table("regions_entity_epaipm", pudl_engine)["region_id_epaipm"]
    all_valid_regions = ipm_regions.tolist() + list(settings["region_aggregations"])
    good_regions = [region in all_valid_regions for region in settings["model_regions"]]

    # assert all(good_regions), logger.warning(
    #     "One or more model regions is not valid. Check to make sure all"
    #     "regions are either in IPM or region_aggregations in the settings YAML file."
    # )
    if not all(good_regions):
        logger.warning(
            "One or more model regions is not valid. Check to make sure all regions "
            "are either in IPM or region_aggregations in the settings YAML file."
        )

    # Sort zones in the settings to make sure they are correctly sorted everywhere.
    settings["model_regions"] = sorted(settings["model_regions"])
    zones = settings["model_regions"]
    logger.info(f"Sorted zones are {', '.join(zones)}")
    zone_num_map = {
        zone: f"{number + 1}" for zone, number in zip(zones, range(len(zones)))
    }

    gc = GeneratorClusters(
        pudl_engine=pudl_engine, pudl_out=pudl_out, settings=settings
    )
    gen_clusters = gc.create_region_technology_clusters()
    gen_clusters["zone"] = gen_clusters.index.get_level_values("region").map(
        zone_num_map
    )

    load = load_curves(pudl_engine=pudl_engine, settings=settings)
    load.columns = "Load_MW_z" + load.columns.map(zone_num_map)

    transmission = agg_transmission_constraints(
        pudl_engine=pudl_engine, settings=settings
    )
    transmission = transmission.pipe(
        transmission_line_distance,
        ipm_shapefile=gc.model_regions_gdf,
        settings=settings,
        units='mile'
    )

    logger.info("Write GenX input files")
    gen_clusters.to_csv(
        out_folder / f"generator_clusters_{args.results_folder}.csv",
        float_format="%.3f",
    )
    if args.all_units is True:
        gc.all_units.to_csv(
            out_folder / f"all_units_{args.results_folder}.csv"
        )

    load.astype(int).to_csv(out_folder / f"load_curves_{args.results_folder}.csv")
    transmission.to_csv(
        out_folder / f"transmission_constraints_{args.results_folder}.csv",
        float_format="%.1f",
    )


if __name__ == "__main__":
    main()
