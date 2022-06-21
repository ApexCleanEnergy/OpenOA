from ctypes import _SimpleCData
import io
import os
import json
import itertools

from dateutil.parser import parse

from operational_analysis.types import timeseries_table

from .asset import AssetData
from .reanalysis import ReanalysisData

import pandas as pd



class PlantData(object):
    """ Data object for operational wind plant data.

    This class holds references to all tables associated with a wind plant. The tables are grouped by type:
        - PlantData.scada
        - PlantData.meter
        - PlantData.tower
        - PlantData.status
        - PlantData.curtail
        - PlantData.asset
        - PlantData.reanalysis

    Each table must have columns following the following convention:
        -

    The PlantData object can serialize all of these structures and reload them
    them from the cache as needed.

    The underlying datastructure is a TimeseriesTable, which is agnostic to the underlying
    engine and can be implemented with Pandas, Spark, or Dask (for instance).

    Individual plants will extend this object with their own
    prepare() and other methods.
    """

    def __init__(self, path, name, engine="pandas", toolkit=["pruf_analysis"], schema=None):
        """
        Create a plant data object without loading any data.

        Args:
            path(string): path where data should be read/written
            name(string): uniqiue name for this plant in case there's multiple plant's data in the directory
            engine(string): backend engine - pandas, spark or dask
            toolkit(list): the _tool_classes attribute defines a list of toolkit modules that can be loaded

        Returns:
            New object
        """
        if not schema:
            dir = os.path.dirname(os.path.abspath(__file__))
            schema = dir + "/plant_schema.json"
        with open(schema) as schema_file:
            self._schema = json.load(schema_file)

        self._scada = timeseries_table.TimeseriesTable.factory(engine)
        self._meter = timeseries_table.TimeseriesTable.factory(engine)
        self._tower = timeseries_table.TimeseriesTable.factory(engine)
        self._status = timeseries_table.TimeseriesTable.factory(engine)
        self._curtail = timeseries_table.TimeseriesTable.factory(engine)
        self._asset = AssetData(engine)
        self._reanalysis = ReanalysisData(engine)
        self._name = name
        self._path = path
        self._engine = engine

        self._version = 1

        self._status_labels = ["full", "unavailable"]

        self._tables = [
            "_scada",
            "_meter",
            "_status",
            "_tower",
            "_asset",
            "_curtail",
            "_reanalysis",
        ]

    def amend_std(self, dfname, new_fields):
        """
        Amend a dataframe standard with new or changed fields. Consider running ensure_columns afterward to
        automatically create the new required columns if they don't exist.

        Args:
            dfname (string): one of scada, status, curtail, etc.
            new_fields (dict): set of new fields and types in the same format as _scada_std to be added/changed in
            the std

        Returns:
            New data field standard
        """

        k = "_%s_std" % (dfname,)
        setattr(
            self, k, dict(itertools.chain(iter(getattr(self, k).items()), iter(new_fields.items())))
        )

    def get_time_range(self):
        """Get time range as tuple

        Returns:
            (tuple):
                start_time(datetime): start time
                stop_time(datetime): stop time
        """
        return (self._start_time, self._stop_time)

    def set_time_range(self, start_time, stop_time):
        """Set time range given two unparsed timestamp strings

        Args:
            start_time(string): start time
            stop_time(string): stop time

        Returns:
            (None)
        """
        self._start_time = parse(start_time)
        self._stop_time = parse(stop_time)

    def save(self, path=None):
        """Save out the project and all JSON serializeable attributes to a file path.

        Args:
            path(string): Location of new directory into which plant will be saved. The directory should not
            already exist. Defaults to self._path

        Returns:
            (None)
        """
        if path is None:
            raise RuntimeError("Path not specified.")

        os.mkdir(path)

        meta_dict = {}
        for ca, ci in self.__dict__.items():
            if ca in self._tables:
                ci.save(path, ca)
            elif ca in ["_start_time", "_stop_time"]:
                meta_dict[ca] = str(ci)
            else:
                meta_dict[ca] = ci

        with io.open(os.path.join(path, "metadata.json"), "w", encoding="utf-8") as outfile:
            outfile.write(str(json.dumps(meta_dict, ensure_ascii=False)))

    def load(self, path=None):
        """Load this project and all associated data from a file path

        Args:
            path(string): Location of plant data directory. Defaults to self._path

        Returns:
            (None)
        """
        if not path:
            path = self._path

        for df in self._tables:
            getattr(self, df).load(path, df)

        meta_path = os.path.join(path, "metadata.json")
        if os.path.exists(meta_path):
            with io.open(os.path.join(path, "metadata.json"), "r") as infile:
                meta_dict = json.load(infile)
                for ca, ci in meta_dict.items():
                    if ca in ["_start_time", "_stop_time"]:
                        ci = parse(ci)
                    setattr(self, ca, ci)

    def ensure_columns(self):
        """@deprecated Ensure all dataframes contain necessary columns and format as needed"""
        raise NotImplementedError("ensure_columns has been deprecated. Use plant.validate instead.")

    def validate(self, schema=None):

        """Validate this plant data object against its schema. Returns True if valid, Rasies an exception if not valid."""

        if not schema:
            schema = self._schema

        for field in schema["fields"]:
            if field["type"] == "timeseries":
                attr = "_{}".format(field["name"])
                if not getattr(self, attr).is_empty():
                    getattr(self, attr).validate(field)

        return True

    def merge_asset_metadata(self):
        """Merge metadata from the asset table into the scada and tower tables"""
        if not (self._scada.is_empty()) and (len(self._asset.turbine_ids()) > 0):
            self._scada.pandas_merge(
                self._asset.df,
                [
                    "latitude",
                    "longitude",
                    "rated_power_kw",
                    "id",
                    "nearest_turbine_id",
                    "nearest_tower_id",
                ],
                "left",
                on="id",
            )
        if not (self._tower.is_empty()) and (len(self._asset.tower_ids()) > 0):
            self._tower.pandas_merge(
                self._asset.df,
                [
                    "latitude",
                    "longitude",
                    "rated_power_kw",
                    "id",
                    "nearest_turbine_id",
                    "nearest_tower_id",
                ],
                "left",
                on="id",
            )

    def prepare(self):
        """Prepare this object for use by loading data and doing essential preprocessing."""
        self.ensure_columns()
        if not ((self._scada.is_empty()) or (self._tower.is_empty())):
            self._asset.prepare(self._scada.unique("id"), self._tower.unique("id"))
        self.merge_asset_metadata()

    @property
    def scada(self):
        return self._scada

    @property
    def meter(self):
        return self._meter

    @property
    def tower(self):
        return self._tower

    @property
    def reanalysis(self):
        return self._reanalysis

    @property
    def status(self):
        return self._status

    @property
    def asset(self):
        return self._asset

    @property
    def curtail(self):
        return self._curtail
        
    @classmethod
    def from_entr(cls, thrift_server_host="localhost",
                       thrift_server_port=10000,
                       database="entr_warehouse",
                       wind_plant="La Haute Borne",
                       aggregation="",
                       date_range=None,
                       reanalysis_products=None,
                       conn=None):
        """
        from_entr

        Load a PlantData object from data in an entr_warehouse.

        Args:
            thrift_server_host(str): URL of the Apache Thrift server
            thrift_server_port(int): Port of the Apache Thrift server
            database(str): Name of the Hive database
            wind_plant(str): Name of the wind plant you'd like to load
            reanalysis_products(list[str]): Reanalysis products to load from the warehouse.
            aggregation: Not yet implemented
            date_range: Not yet implemented

        Returns:
            plant(PlantData): An OpenOA PlantData object.
        """
        import operational_analysis.toolkits.entr as entr
        plant = cls(database, wind_plant) ## Passing in database as the path and wind_plant as the name for now.
            
        plant.name = wind_plant

        conn = entr.get_connection(thrift_server_host, thrift_server_port)

        entr.load_metadata(conn, plant)
        entr.load_asset(conn, plant)
        entr.load_scada(conn, plant)
        entr.load_curtailment(conn, plant)
        entr.load_meter(conn, plant)
        entr.load_reanalysis(conn, plant, reanalysis_products)

        return plant
        # plant = cls(database, wind_plant) ## Passing in database as the path and wind_plant as the name for now.

        # if conn is None:
        #     from pyhive import hive
        #     conn = hive.Connection(host=thrift_server_host, port=thrift_server_port)

        # ## Plant Metadata
        # metadata_query = f"""
        # SELECT
        #     plant_id,
        #     plant_name,
        #     latitude,
        #     longitude,
        #     plant_capacity,
        #     number_of_turbines,
        #     turbine_capacity
        # FROM
        #     entr_warehouse.dim_asset_plant
        # WHERE
        #     plant_name = "{wind_plant}";
        # """
        # metadata = pd.read_sql(metadata_query, conn)
        # assert len(metadata)<2, f"Multiple plants matching name {wind_plant}"
        # assert len(metadata)>0, f"No plant matching name {wind_plant}"

        # plant.latitude = metadata["latitude"][0]
        # plant.longitude = metadata["longitude"][0]
        # plant.capacity = metadata["plant_capacity"][0]

        # ## Asset Table
        # asset_query = f"""
        # SELECT
        #     plant_id,
        #     wind_turbine_id,
        #     wind_turbine_name,
        #     latitude,
        #     longitude,
        #     elevation,
        #     hub_height,
        #     rotor_diameter,
        #     rated_power,
        #     manufacturer,
        #     model
        # FROM
        #     entr_warehouse.dim_asset_wind_turbine
        # WHERE
        #     plant_id = {metadata['plant_id'][0]};
        # """
        # #plant._asset = pyspark.sql(asset_query).to_pandas()
        # plant._asset = pd.read_sql(asset_query, conn)

        # ## Scada Table
        # scada_query = f"""
        # SELECT
        #     entr_warehouse.rpt_openoa_wtg_scada.wind_turbine_name,
        #     date_time,
        #     `WROT.BlPthAngVal`,
        #     `WTUR.W`,
        #     `WMET.HorWdSpd`,
        #     `WMET.HorWdDirRel`,
        #     `WMET.EnvTmp`,
        #     `WNAC.Dir`,
        #     `WMET.HorWdDir`
        # FROM
        #     entr_warehouse.rpt_openoa_wtg_scada
        #     LEFT JOIN
        #         entr_warehouse.dim_asset_wind_turbine
        #     ON entr_warehouse.rpt_openoa_wtg_scada.wind_turbine_name = entr_warehouse.dim_asset_wind_turbine.wind_turbine_name
        # WHERE
        #     plant_id = {metadata['plant_id'][0]};
        # """
        # plant.scada.df = pd.read_sql(scada_query, conn)

        # conn.close()

        # return plant



    @classmethod
    def from_pandas(cls, scada, meter, status, tower, asset, curtail, reanalysis):
        """
        from_pandas

        Create a PlantData object from a collection of Pandas data frames.

        Args:
            scada:
            meter:
            status:
            tower:
            asset:
            curtail:
            reanalysis:

        Returns:
            plant(PlantData): An OpenOA PlantData object.
        """
        plant = cls()

        plant.scada.df = scada
        plant.meter.df = meter
        plant.status.df = status
        plant.tower.df = tower
        plant.asset.df = asset
        plant.curtail.df = curtail
        plant.reanalysis.df = reanalysis

        plant.validate()

