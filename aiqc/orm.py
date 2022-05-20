"""
Low-Level API
└── Documentation = https://aiqc.readthedocs.io/en/latest/notebooks/api_low_level.html

This is an Object-Relational Model (ORM) for the SQLite database that keeps track of the workflow.
It acts like an persistent, object-oriented API for machine learning.

There is a circular depedency between: 
1: Model(BaseModel)
2: db.create_tables(Models)
3: BaseModel(db).

	In an attempt to fix this, I split db.create_tables(Models) into a separate file, which allowed me 
	to put the Models in separate files. However, this introduced a problem where the Models couldn't 
	be reloaded without restarting the kernel and rerunning setup(), which isn't very user-friendly.
	I tried all kinds of importlib.reload() to fix this new problem, but decided to move on.
	See also: github.com/coleifer/peewee/issues/856
"""
# --- Local modules ---
from .utils.config import app_dir, timezone_now
from .plots import Plot
from . import utils
# --- Python modules ---
from os import path, remove, makedirs
from sys import modules
from io import BytesIO
from random import randint, sample
from itertools import product
from gzip import open as gzopen
from requests import get as requests_get
from validators import url as val_url
from pprint import pformat
from scipy.stats import mode
from h5py import File as h5_File
from pickle import dump, load
from importlib import reload as importlib_reload
from textwrap import dedent
from re import sub
# math and statistics will not let you import specific modules (ceil, floor, prod)
import math
import statistics
import datetime as dt
# --- External utils ---
from tqdm import tqdm #progress bar.
from natsort import natsorted #file sorting.
# ORM.
from playhouse.sqlite_ext import SqliteExtDatabase, JSONField
from playhouse.signals import Model, pre_save
from peewee import CharField, IntegerField, BlobField, BooleanField, \
	DateTimeField, ForeignKeyField
from playhouse.fields import PickleField
# ETL.
import pandas as pd
import numpy as np
from PIL import Image as Imaje
# Preprocessing & metrics.
import sklearn #mandatory to import submodules separately.
from sklearn.model_selection import train_test_split, StratifiedKFold, KFold
# Deep learning.
from tensorflow.keras.models import load_model
from torch import load as torch_load
from torch import save as torch_save
from torch import long, FloatTensor


def get_path_db():
	from .utils.config import get_config
	aiqc_config = get_config()
	if (aiqc_config is None):
		pass# get_config() will print a null condition.
	else:
		db_path = aiqc_config['db_path']
		return db_path


def get_db():
	"""
	The `BaseModel` of the ORM calls this function. 
	"""
	path = get_path_db()
	if (path is None):
		print("\n=> Info - Cannot fetch database yet because it has not been configured.\n")
	else:
		"""
		Write Ahead Lock (WAL) mode supports concurrent writes, but not NFS (AWS EFS)
		└── <sqlite.org/wal.html>
		Default is Journal mode and it is set in `pragmas`
		└── <docs.peewee-orm.com/en/latest/peewee/database.html>
		"""
		db = SqliteExtDatabase(path)
		return db


def create_db():
	# Future: Could let the user specify their own db name, for import tutorials. Could check if passed as an argument to create_config?
	db_path = get_path_db()
	db_exists = path.exists(db_path)
	if db_exists:
		print(f"\n=> Skipping database file creation as a database file already exists at path:\n{db_path}\n")
		db = get_db()
	else:
		# Create sqlite file for db.
		try:
			db = get_db()
			# This `reload` is needed after I refactored Models into orm.py
			importlib_reload(modules[__name__])
		except:
			print(
				f"=> Yikes - failed to create database file at path:\n{db_path}\n\n" \
				f"===================================\n"
			)
			raise
		print(f"\n=> Success - created database file at path:\n{db_path}\n")	
	
	# Create tables inside db.
	tables = db.get_tables()
	table_count = len(tables)
	if (table_count > 0):
		print(f"\n=> Info - skipping table creation as the following tables already exist.{tables}\n")
	else:
		db.create_tables([
			File, Dataset,
			Label, Feature, 
			Splitset, Featureset, Foldset, Fold, 
			Interpolaterset, LabelInterpolater, FeatureInterpolater,
			Encoderset, LabelCoder, FeatureCoder, 
			Window, FeatureShaper,
			Algorithm, Hyperparamset, Hyperparamcombo,
			Queue, Jobset, Job, Predictor, Prediction,
			FittedEncoderset, FittedLabelCoder,
		])
		tables = db.get_tables()
		table_count = len(tables)
		if table_count > 0:
			print("\n💾  Success - created all database tables.  💾\n")
		else:
			print(
				f"=> Yikes - failed to create tables.\n" \
				f"Please see README file section titled: 'Deleting & Recreating the Database'\n"
			)


def destroy_db(confirm:bool=False, rebuild:bool=False):
	if (confirm==True):
		db_path = get_path_db()
		db_exists = path.exists(db_path)
		if db_exists:
			try:
				remove(db_path)
			except:
				print(
					f"=> Yikes - failed to delete database file at path:\n{db_path}\n\n" \
					f"===================================\n"
				)
				raise
			print(f"\n=> Success - deleted database file at path:\n{db_path}\n")
		else:
			print(f"\n=> Info - there is no file to delete at path:\n{db_path}\n")
		importlib_reload(modules[__name__])

		if (rebuild==True):
			create_db()
	else:
		print("\n=> Info - skipping destruction because `confirm` arg not set to boolean `True`.\n")


#==================================================
# MODEL CLASSES
#==================================================
class BaseModel(Model):
	"""
	- Runs when the package is imported. http://docs.peewee-orm.com/en/latest/peewee/models.html
	- ORM: by inheritting the BaseModel class, each Model class does not have to set Meta.
	- nullable attributes are enabled by inclusion in each Model's method args.
	"""
	time_created = DateTimeField()
	time_updated = DateTimeField()
	name = CharField(null=True)
	version = IntegerField(null=True)
	description = CharField(null=True)
	
	class Meta:
		database = get_db()
	
	# Fetch as human-readable timestamps
	def created_at(self):
		return self.time_created.strftime('%Y%b%d_%H:%M:%S')
	def updated_at(self):
		return self.time_updated.strftime('%Y%b%d_%H:%M:%S')


@pre_save(sender=BaseModel)
def add_timestamps(model_class, instance, created):
	"""Arguments are `signals` defaults"""
	if (created==True):
		instance.time_created = timezone_now()
	instance.time_updated = timezone_now()


@pre_save(sender=BaseModel)
def increment_version(model_class, instance, created):
	"""Rules about new versions"""
	# Minimize performance impact of this signal when creating a one thousand file dataset
	klass = instance.__class__.__name__
	if (klass!="File"):
		instance, latest_match = utils.wrangle.match_name(instance, created)
		###
		print(latest_match)
		# print(latest_match.name)
		if (latest_match is not None):
			if (klass=="Dataset"):
				if (latest_match.dataset_type!=instance.dataset_type):
					msg = f"Yikes - New Dataset type <{instance.dataset_type}> must match the type of the most recent version <{latest_match.dataset_type}>."
					raise Exception(msg)
			elif(klass=="Splitset"):
				new_label = instance.label.id
				if (new_label is not None):
					old_label = latest_match.label.id
					if (new_label!=old_label):
						msg = f"Yikes - New Splitset label <{new_label}> must match old Splitset label <{old_label}> in order to use the same name"
						raise Exception(msg)
				new_features = instance.get_features()
				old_features = latest_match.get_features()
				new_features != old_features
				msg = f"Yikes - New Splitset features <{new_features}> must match old Splitset features <{old_features}> exactly in order to use the same name"




class Dataset(BaseModel):
	"""
	The sub-classes are not 1-1 tables. They simply provide namespacing for functions
	to avoid functions riddled with if statements about dataset_type and null parameters.
	"""
	dataset_index = IntegerField()
	dataset_type = CharField() #tabular, image, sequence, graph, audio.
	file_count = IntegerField() # used in Dataset.Sequence, but for Dataset.Image this really represents the number of seq datasets.
	source_path = CharField(null=True)
	#http://docs.peewee-orm.com/en/latest/peewee/models.html#self-referential-foreign-keys
	dataset = ForeignKeyField('self', deferrable='INITIALLY DEFERRED', null=True, backref='datasets')


	def to_pandas(id:int, columns:list=None, samples:list=None):
		dataset = Dataset.get_by_id(id)
		columns = utils.wrangle.listify(columns)
		samples = utils.wrangle.listify(samples)

		if (dataset.dataset_type == 'tabular'):
			df = Dataset.Tabular.to_pandas(id=dataset.id, columns=columns, samples=samples)
		elif (dataset.dataset_type == 'sequence'):
			df = Dataset.Sequence.to_pandas(id=dataset.id, columns=columns, samples=samples)
		elif (dataset.dataset_type == 'image'):
			df = Dataset.Image.to_pandas(id=dataset.id, columns=columns, samples=samples)
		return df


	def to_numpy(id:int, columns:list=None, samples:list=None):
		dataset = Dataset.get_by_id(id)
		columns = utils.wrangle.listify(columns)
		samples = utils.wrangle.listify(samples)

		if (dataset.dataset_type == 'tabular'):
			arr = Dataset.Tabular.to_numpy(id=id, columns=columns, samples=samples)
		elif (dataset.dataset_type == 'sequence'):
			arr = Dataset.Sequence.to_numpy(id=id, columns=columns, samples=samples)
		elif (dataset.dataset_type == 'image'):
			arr = Dataset.Image.to_numpy(id=id, columns=columns, samples=samples)
		return arr


	def to_pillow(id:int, samples:list=None):
		samples = utils.wrangle.listify(samples)
		dataset = Dataset.get_by_id(id)
		if (dataset.dataset_type == 'tabular'):
			raise Exception("\nYikes - Only `Dataset.Image` and `Dataset.Sequence` support `to_pillow()`\n")
		elif (dataset.dataset_type == 'image'):
			image = Dataset.Image.to_pillow(id=id, samples=samples)
		elif (dataset.dataset_type == 'sequence'):
			if (samples is not None):
				raise Exception("\nYikes - `Dataset.Sequence.to_pillow()` does not support a `samples` argument.\n")
			image = Dataset.Sequence.to_pillow(id=id)
		return image


	def get_main_file(id:int):
		dataset = Dataset.get_by_id(id)
		if (dataset.dataset_type != 'image'):
			file = File.select().join(Dataset).where(
				Dataset.id==id, File.file_index==0
			)[0]
		elif (dataset.dataset_type == 'image'):
			file = dataset.datasets[0].get_main_file()#Recursion.
		return file


	class Tabular():
		"""
		- Does not inherit the Dataset class e.g. `class Tabular(Dataset):`
		  because then ORM would make a separate table for it.
		- It is just a collection of methods and default variables.
		"""
		dataset_index = 0
		dataset_type = 'tabular'
		file_count = 1
		file_index = 0

		def from_path(
			file_path:str
			, source_file_format:str
			, name:str = None
			, description:str = None
			, dtype:object = None
			, column_names:list = None
			, skip_header_rows:object = 'infer'
			, ingest:bool = True
		):
			column_names = utils.wrangle.listify(column_names)

			accepted_formats = ['csv', 'tsv', 'parquet']
			if (source_file_format not in accepted_formats):
				raise Exception(f"\nYikes - Available file formats include csv, tsv, and parquet.\nYour file format: {source_file_format}\n")

			if (not path.exists(file_path)):
				raise Exception(f"\nYikes - The path you provided does not exist according to `path.exists(file_path)`:\n{file_path}\n")

			if (not path.isfile(file_path)):
				raise Exception(dedent(
					f"Yikes - The path you provided is a directory according to `path.isfile(file_path)`:" \
					f"{file_path}" \
					f"But `dataset_type=='tabular'` only supports a single file, not an entire directory.`"
				))

			# Use the raw, not absolute path for the name.
			if (name is None):
				name = file_path

			source_path = path.abspath(file_path)

			dataset = Dataset.create(
				dataset_type = Dataset.Tabular.dataset_type
				, dataset_index = Dataset.Tabular.dataset_index
				, file_count = Dataset.Tabular.file_count
				, source_path = source_path
				, name = name
				, description = description
			)

			try:
				File.from_path(
					path = file_path
					, source_file_format = source_file_format
					, dtype = dtype
					, column_names = column_names
					, skip_header_rows = skip_header_rows
					, ingest = ingest
					, dataset_id = dataset.id
				)
			except:
				dataset.delete_instance() # Orphaned.
				raise

			return dataset

		
		def from_pandas(
			dataframe:object
			, name:str = None
			, description:str = None
			, dtype:object = None
			, column_names:list = None
		):
			column_names = utils.wrangle.listify(column_names)

			if (type(dataframe).__name__ != 'DataFrame'):
				raise Exception("\nYikes - The `dataframe` you provided is not `type(dataframe).__name__ == 'DataFrame'`\n")

			dataset = Dataset.create(
				dataset_type = Dataset.Tabular.dataset_type
				, dataset_index = Dataset.Tabular.dataset_index
				, file_count = Dataset.Tabular.file_count
				, name = name
				, description = description
				, source_path = None
			)

			try:
				File.from_pandas(
					dataframe = dataframe
					, dtype = dtype
					, column_names = column_names
					, dataset_id = dataset.id
				)
			except:
				dataset.delete_instance() # Orphaned.
				raise 
			return dataset


		def from_numpy(
			ndarray:object
			, name:str = None
			, description:str = None
			, dtype:object = None
			, column_names:list = None
			, _dataset_index:int = None
		):
			column_names = utils.wrangle.listify(column_names)
			utils.wrangle.arr_validate(ndarray)

			dimensions = len(ndarray.shape)
			if (dimensions > 2) or (dimensions < 1):
				raise Exception(dedent(f"""
				Yikes - Tabular Datasets only support 1D and 2D arrays.
				Your array dimensions had <{dimensions}> dimensions.
				"""))
			
			dataset = Dataset.create(
				dataset_type = Dataset.Tabular.dataset_type
				, dataset_index = Dataset.Tabular.dataset_index
				, file_count = Dataset.Tabular.file_count
				, name = name
				, description = description
				, source_path = None
			)
			try:
				File.from_numpy(
					ndarray = ndarray
					, dtype = dtype
					, column_names = column_names
					, dataset_id = dataset.id
				)
			except:
				dataset.delete_instance() # Orphaned.
				raise 
			return dataset
		

		def parse_data(
			df_arr_path:object, dtype:object=None, name:str=None, description:str=None, column_names:list=None
		):
			"""Determine how `df_arr_path` should be handled by the low-level API"""
			d = df_arr_path
			data_type = str(type(d))
			if (data_type == "<class 'pandas.core.frame.DataFrame'>"):
				dataset = Dataset.Tabular.from_pandas(
					dataframe=d, dtype=dtype, name=name, description=description
				)
			elif (data_type == "<class 'numpy.ndarray'>"):
				dataset = Dataset.Tabular.from_numpy(
					ndarray=d, dtype=dtype, name=name, description=description, column_names=column_names
				)
			elif (data_type == "<class 'str'>"):
				if ('.csv' in d):
					source_file_format='csv'
				elif ('.tsv' in d):
					source_file_format='tsv'
				elif ('.parquet' in d):
					source_file_format='parquet'
				else:
					raise Exception(dedent("""
					Yikes - None of the following file extensions were found in the path you provided:
					'.csv', '.tsv', '.parquet'
					"""))
				dataset = Dataset.Tabular.from_path(
					file_path = d
					, source_file_format = source_file_format
					, dtype = dtype
					, name=name
					, description=description
				)
			else:
				raise Exception("\nYikes - The `dataFrame_or_filePath` is neither a string nor a Pandas dataframe.\n")
			return dataset


		def to_pandas(id:int, columns:list=None, samples:list=None):
			file = Dataset.get_main_file(id)#`id` belongs to dataset, not file
			columns = utils.wrangle.listify(columns)
			samples = utils.wrangle.listify(samples)
			df = File.to_pandas(id=file.id, samples=samples, columns=columns)
			return df


		def to_numpy(id:int, columns:list=None, samples:list=None):
			dataset = Dataset.get_by_id(id)
			columns = utils.wrangle.listify(columns)
			samples = utils.wrangle.listify(samples)
			# This calls the method above. It does not need `.Tabular`
			df = dataset.to_pandas(columns=columns, samples=samples)
			ndarray = df.to_numpy()
			return ndarray

	
	class Sequence():
		dataset_type = 'sequence'
		dataset_index = 0

		def from_numpy(
			ndarray3D_or_npyPath:object
			, name:str = None
			, description:str = None
			, dtype:object = None
			, column_names:list = None
			, ingest:bool = True
			, _disable:bool = False #used by Dataset.Image
			, _source_path:str = None #used by Dataset.Image
			, _dataset_index:int = None #used by Dataset.Image
			, _dataset:object = None #used by Dataset.Image
		):
			"""Both `ingest=False` and `_source_path=None` is possible"""
			if ((ingest==False) and (isinstance(dtype, dict))):
				raise Exception("\nYikes - If `ingest==False` then `dtype` must be either a str or a single NumPy-based type.\n")
			# Fetch array from .npy if it is not an in-memory array.
			if (str(ndarray3D_or_npyPath.__class__) != "<class 'numpy.ndarray'>"):
				if (not isinstance(ndarray3D_or_npyPath, str)):
					raise Exception("\nYikes - If `ndarray3D_or_npyPath` is not an array then it must be a string-based path.\n")
				if (not path.exists(ndarray3D_or_npyPath)):
					raise Exception("\nYikes - The path you provided does not exist according to `path.exists(ndarray3D_or_npyPath)`\n")
				if (not path.isfile(ndarray3D_or_npyPath)):
					raise Exception("\nYikes - The path you provided is not a file according to `path.isfile(ndarray3D_or_npyPath)`\n")
				if (_source_path is not None):
					# Path or url to 3D image.
					source_path = _source_path
				elif (_source_path is None):
					source_path = ndarray3D_or_npyPath
				if (not source_path.lower().endswith(".npy")):
					raise Exception("\nYikes - Path must end with '.npy' or '.NPY'\n")
				try:
					# `allow_pickle=False` prevented it from reading the file.
					ndarray_3D = np.load(file=ndarray3D_or_npyPath)
				except:
					print("\nYikes - Failed to `np.load(file=ndarray3D_or_npyPath)` with your `ndarray3D_or_npyPath`:\n")
					print(f"{ndarray3D_or_npyPath}\n")
					raise
			elif (str(ndarray3D_or_npyPath.__class__) == "<class 'numpy.ndarray'>"):
				ndarray_3D = ndarray3D_or_npyPath 
				if (_source_path is not None):
					# Path or url to 3D image.
					source_path = _source_path
				elif (_source_path is None):
					source_path = None

			column_names = utils.wrangle.listify(column_names)
			utils.wrangle.arr_validate(ndarray_3D)

			if (ndarray_3D.ndim != 3):
				raise Exception(dedent(f"""
				Yikes - Sequence Datasets can only be constructed from 3D arrays.
				Your array dimensions had <{ndarray_3D.ndim}> dimensions.
				Tip: the shape of each internal array must be the same.
				"""))

			if (_dataset_index is not None):
				dataset_index = _dataset_index
			elif (_dataset_index is None):
				dataset_index = Dataset.Sequence.dataset_index
			file_count = len(ndarray_3D)
			dataset = Dataset.create(
				dataset_type = Dataset.Sequence.dataset_type
				, dataset_index = dataset_index
				, file_count = file_count
				, name = name
				, description = description
				, source_path = source_path
				, dataset = _dataset
			)

			try:
				for i, arr in enumerate(tqdm(
					ndarray_3D
					, desc = "⏱️ Ingesting Sequences 🧬"
					, ncols = 85
					, disable = _disable
				)):
					File.from_numpy(
						ndarray = arr
						, dataset_id = dataset.id
						, column_names = column_names
						, dtype = dtype
						, _file_index = i
						, ingest = ingest
					)
			except:
				dataset.delete_instance() # Orphaned.
				raise
			return dataset


		def to_numpy(id:int, columns:list=None, samples:list=None):
			columns, samples = utils.wrangle.listify(columns), utils.wrangle.listify(samples)
			dataset = Dataset.get_by_id(id)
			if (samples is None):
				files = dataset.files
			elif (samples is not None):
				# Here the 'sample' is the entire file. Whereas, in 2D 'sample==row'.
				# So run a query to get those files: `<<` means `in`.
				files = File.select().join(Dataset).where(
					Dataset.id==dataset.id, File.file_index<<samples
				)
			files = list(files)
			# Then call them with the column filter.
			# So don't pass `samples=samples` to the file.
			list_2D = [f.to_numpy(columns=columns) for f in files]
			arr_3D = np.array(list_2D)
			return arr_3D


		def to_pandas(id:int, columns:list=None, samples:list=None):
			columns, samples = utils.wrangle.listify(columns), utils.wrangle.listify(samples)
			dataset = Dataset.get_by_id(id)
			if (samples is None):
				files = dataset.files
			elif (samples is not None):
				# Here the 'sample' is the entire file. Whereas, in 2D 'sample==row'.
				# So run a query to get those files: `<<` means `in`.
				files = File.select().join(Dataset).where(
					Dataset.id==dataset.id, File.file_index<<samples
				)
			files = list(files)
			# Then call them with the column filter.
			# So don't pass `samples=samples` to the file.
			dfs = [file.to_pandas(columns=columns) for file in files]
			return dfs


		def to_pillow(id:int):
			dataset = Dataset.get_by_id(id)
			arr = dataset.to_numpy().astype('uint8')
			if (arr.shape[0]==1):
				arr = arr.reshape(arr.shape[1], arr.shape[2])
				img = Imaje.fromarray(arr, 'L')
			elif (arr.shape[0]==3):
				img = Imaje.fromarray(arr, 'RGB')
			elif (arr.shape[0]==4):
				img = Imaje.fromarray(arr, 'RGBA')
			else:
				raise Exception("\nYikes - Rendering only enabled for images with either 2, 3, or 4 channels.\n")
			return img


	class Image():
		"""Each Image sample is a Dataset.Sequence of 1 or more Files."""
		# PIL supported file formats: https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html
		dataset_type = 'image'
		dataset_index = 0

		def from_folder_pillow(
			folder_path:str
			, ingest:bool = True
			, name:str = None
			, description:str = None
			, dtype:dict = None
			, column_names:list = None
		):
			if (name is None): name = folder_path
			source_path = path.abspath(folder_path)
			file_paths = utils.wrangle.sorted_file_list(source_path)
			file_count = len(file_paths)

			# Validated during `sequences_from_4D`.
			arr_4d = []
			for p in file_paths:
				img = Imaje.open(p)
				arr = np.array(img)
				if (arr.ndim==2):
					arr=np.array([arr])
				arr_4d.append(arr)
			arr_4d = np.array(arr_4d)

			dataset = Dataset.create(
				dataset_type = Dataset.Image.dataset_type
				, dataset_index = Dataset.Image.dataset_index
				, file_count = file_count
				, name = name
				, description = description
				, source_path = source_path
			)
			try:		
				# Intentionally not passing name for versioning
				Dataset.Image.sequences_from_4D(
					dataset = dataset
					, ndarray_4D = arr_4d
					, paths = file_paths
					, dtype = dtype
					, column_names = column_names
					, ingest = ingest
				)
			except:
				dataset.delete_instance()
				raise
			return dataset


		def from_urls_pillow(
			urls:list
			, source_path:str = None # not used anywhere, but doesn't hurt to record.
			, ingest:bool = True
			, name:str = None
			, description:str = None
			, dtype:dict = None
			, column_names:list = None
		):
			urls = utils.wrangle.listify(urls)
			file_count = len(urls)

			# Validated during `sequences_from_4D`.
			arr_4d = []
			for url in urls:
				validation = val_url(url)
				if (validation != True): #`== False` doesn't work.
					raise Exception(f"\nYikes - Invalid url detected within `urls` list:\n'{url}'\n")

				img = Imaje.open(
					requests_get(url, stream=True).raw
				)
				arr = np.array(img)
				if (arr.ndim==2):
					arr=np.array([arr])
				arr_4d.append(arr)
			arr_4d = np.array(arr_4d)

			dataset = Dataset.create(
				dataset_type = Dataset.Image.dataset_type
				, dataset_index = Dataset.Image.dataset_index
				, file_count = file_count
				, name = name
				, description = description
				, source_path = source_path
			)

			try:
				# Intentionally not passing name for versioning
				Dataset.Image.sequences_from_4D(
					dataset = dataset
					, ndarray_4D = arr_4d
					, paths = urls
					, dtype = dtype
					, column_names = column_names
					, ingest = ingest
				)
			except:
				dataset.delete_instance()
				raise
			return dataset


		def from_numpy(
			ndarray4D_or_npyPath:object
			, name:str = None
			, description:str = None
			, dtype:object = None
			, column_names:list = None
			, ingest:bool = True
		):
			# Fetch array from .npy if it is not an in-memory array.
			if (str(ndarray4D_or_npyPath.__class__) != "<class 'numpy.ndarray'>"):
				if (not isinstance(ndarray4D_or_npyPath, str)):
					raise Exception("\nYikes - If `ndarray4D_or_npyPath` is not an array then it must be a string-based path.\n")
				if (not path.exists(ndarray4D_or_npyPath)):
					raise Exception("\nYikes - The path you provided does not exist according to `path.exists(ndarray3D_or_npyPath)`\n")
				if (not path.isfile(ndarray4D_or_npyPath)):
					raise Exception("\nYikes - The path you provided is not a file according to `path.isfile(ndarray3D_or_npyPath)`\n")
				if (not ndarray4D_or_npyPath.lower().endswith('.npy')):
					raise Exception("\nYikes - Path must end with '.npy' or '.NPY'\n")
				source_path = ndarray4D_or_npyPath
				try:
					# `allow_pickle=False` prevented it from reading the file.
					ndarray_4D = np.load(file=ndarray4D_or_npyPath)
				except:
					print("\nYikes - Failed to `np.load(file=ndarray4D_or_npyPath)` with your `ndarray4D_or_npyPath`:\n")
					print(f"{ndarray4D_or_npyPath}\n")
					raise
			elif (str(ndarray4D_or_npyPath.__class__) == "<class 'numpy.ndarray'>"):
				source_path = None
				ndarray_4D = ndarray4D_or_npyPath
				if (ingest==False):
					raise Exception("\nYikes - If provided an in-memory array, then `ingest` cannot be False.\n")

			file_count = ndarray_4D.shape[1]#This is the 3rd dimension
			dataset = Dataset.create(
				dataset_type = Dataset.Image.dataset_type
				, dataset_index = Dataset.Image.dataset_index
				, file_count = file_count
				, name = name
				, description = description
				, source_path = source_path
			)
			try:
				# Intentionally not passing name for versioning
				Dataset.Image.sequences_from_4D(
					dataset = dataset
					, ndarray_4D = ndarray_4D
					, dtype = dtype
					, column_names = column_names
					, ingest = ingest
					, source_path = source_path
				)
			except:
				dataset.delete_instance()
				raise
			return dataset


		def sequences_from_4D(
			dataset:object
			, ndarray_4D:object
			, ingest:bool = True
			, paths:list = None
			, name:str = None
			, description:str = None
			, dtype:object = None
			, column_names:list = None
			, source_path:str = None
		):
			column_names = utils.wrangle.listify(column_names)
			if ((ingest==False) and (isinstance(dtype, dict))):
				raise Exception("\nYikes - If `ingest==False` then `dtype` must be either a str or a single NumPy-based type.\n")
			# Checking that the shape is 4D validates that each internal array is uniformly shaped.
			if (ndarray_4D.ndim!=4):
				raise Exception("\nYikes - Ingestion failed: `ndarray_4D.ndim!=4`. Tip: shapes of each image array must be the same.\n")
			utils.wrangle.arr_validate(ndarray_4D)
			
			if (paths is not None):
				for i, arr in enumerate(tqdm(
					ndarray_4D
					, desc = "🖼️ Ingesting Images 🖼️"
					, ncols = 85
				)):
					Dataset.Sequence.from_numpy(
						ndarray3D_or_npyPath = arr
						, name = name
						, dtype = dtype
						, column_names = column_names
						, ingest = ingest
						, _dataset_index = i
						, _source_path = paths[i]
						, _disable = True
						, _dataset = dataset
					)
			# Creating Sequences is optional for the npy file approach.
			elif (paths is None):
				for i, arr in enumerate(tqdm(
					ndarray_4D
					, desc = "🖼️ Ingesting Images 🖼️"
					, ncols = 85
				)):
					Dataset.Sequence.from_numpy(
						ndarray3D_or_npyPath = arr
						, name = name
						, dtype = dtype
						, column_names = column_names
						, ingest = ingest
						, _dataset_index = i
						, _source_path = source_path
						, _disable = True
						, _dataset = dataset
					)


		def to_numpy(id:int, samples:list=None, columns:list=None):
			samples, columns = utils.wrangle.listify(samples), utils.wrangle.listify(columns)
			# The 3D array is the sample. Some `samples` not passed `to_numpy()`.
			if (samples is not None):
				# ORM was queries were being weird about the self foreign key.
				datasets = [Dataset.get_by_id(s) for s in samples]
			elif (samples is None):
				datasets = list(Dataset.get_by_id(id).datasets)
			arr_4d = np.array([d.to_numpy(columns=columns) for d in datasets])
			return arr_4d


		def to_pandas(id:int, samples:list=None, columns:list=None):
			samples, columns = utils.wrangle.listify(samples), utils.wrangle.listify(columns)
			# The 3D array is the sample. Some `samples` not passed `to_numpy()`.
			if (samples is not None):
				# ORM was queries were being weird about the self foreign key.
				datasets = [Dataset.get_by_id(s) for s in samples]
			elif (samples is None):
				datasets = list(Dataset.get_by_id(id).datasets)
			dfs = [d.to_pandas(columns=columns) for d in datasets]
			return dfs


		def to_pillow(id:int, samples:list=None):
			dataset = Dataset.get_by_id(id)
			datasets = dataset.datasets
			if (samples is not None):
				samples = utils.wrangle.listify(samples)
				datasets = [d for d in datasets if d.dataset_index in samples]
			images = [d.to_pillow() for d in datasets]
			return images




class File(BaseModel):
	"""
	- Due to the fact that different types of Files have different attributes
	  (e.g. File columns=JSON or File.Graph nodes=Blob, edges=Blob), 
	  I am making each file type its own subclass and 1-1 table. This approach 
	  allows for the creation of custom File types.
	- If `blob=None` then isn't persisted therefore fetch from source_path or s3_path.
	- Note that `dtype` does not require every column to be included as a key in the dictionary.
	"""
	file_format = CharField() # png, jpg, parquet.
	file_index = IntegerField() # image, sequence, graph.
	shape = JSONField()
	is_ingested = BooleanField()
	skip_header_rows = PickleField(null=True) #Image does not have.
	source_path = CharField(null=True) # when `from_numpy` or `from_pandas`.
	blob = BlobField(null=True) # when `is_ingested==False`.

	columns = JSONField()
	dtypes = JSONField()

	dataset = ForeignKeyField(Dataset, backref='files')
	

	def from_pandas(
		dataframe:object
		, dataset_id:int
		, dtype:object = None # Accepts a single str for the entire df, but utlimate it gets saved as one dtype per column.
		, column_names:list = None
		, source_path:str = None # passed in via from_file, but not from_numpy.
		, ingest:bool = True # from_file() method overwrites this.
		, file_format:str = 'parquet' # from_file() method overwrites this.
		, skip_header_rows:int = 'infer'
		, _file_index:int = 0 # Dataset.Sequence overwrites this.
	):
		column_names = utils.wrangle.listify(column_names)
		utils.wrangle.df_validate(dataframe, column_names)

		# We need this metadata whether ingested or not.
		dataframe, columns, shape, dtype = utils.wrangle.df_set_metadata(
			dataframe=dataframe, column_names=column_names, dtype=dtype
		)
		if (ingest==True):
			blob = utils.wrangle.df_to_compressed_parquet_bytes(dataframe)
		elif (ingest==False):
			blob = None

		dataset = Dataset.get_by_id(dataset_id)

		file = File.create(
			blob = blob
			, file_format = file_format
			, file_index = _file_index
			, shape = shape
			, source_path = source_path
			, skip_header_rows = skip_header_rows
			, is_ingested = ingest
			, columns = columns
			, dtypes = dtype
			, dataset = dataset
		)
		return file


	def from_numpy(
		ndarray:object
		, dataset_id:int
		, column_names:list = None
		, dtype:object = None #Or single string.
		, _file_index:int = 0
		, ingest:bool = True
	):
		column_names = utils.wrangle.listify(column_names)
		"""
		Only supporting homogenous arrays because structured arrays are a pain
		when it comes time to convert them to dataframes. It complained about
		setting an index, scalar types, and dimensionality... yikes.
		
		Homogenous arrays keep dtype in `arr.dtype==dtype('int64')`
		Structured arrays keep column names in `arr.dtype.names==('ID', 'Ring')`
		Per column dtypes dtypes from structured array <https://stackoverflow.com/a/65224410/5739514>
		"""
		utils.wrangle.arr_validate(ndarray)
		"""
		column_names and dict-based dtype will be handled by our `from_pandas()` method.
		`pd.DataFrame` method only accepts a single dtype str, or infers if None.
		"""
		df = pd.DataFrame(data=ndarray)
		file = File.from_pandas(
			dataframe = df
			, dataset_id = dataset_id
			, dtype = dtype
			# Setting `column_names` will not overwrite the first row of homogenous array:
			, column_names = column_names
			, _file_index = _file_index
			, ingest = ingest
		)
		return file


	def from_path(
		path:str
		, source_file_format:str
		, dataset_id:int
		, dtype:object = None
		, column_names:list = None
		, skip_header_rows:object = 'infer'
		, ingest:bool = True
	):
		column_names = utils.wrangle.listify(column_names)
		df = utils.wrangle.path_to_df(
			file_path = path
			, source_file_format = source_file_format
			, column_names = column_names
			, skip_header_rows = skip_header_rows
		)

		file = File.from_pandas(
			dataframe = df
			, dataset_id = dataset_id
			, dtype = dtype
			, column_names = None # See docstring above.
			, source_path = path
			, file_format = source_file_format
			, skip_header_rows = skip_header_rows
			, ingest = ingest
		)
		return file


	def to_pandas(id:int, columns:list=None, samples:list=None):
		"""
		This function could be optimized to read columns and rows selectively
		rather than dropping them after the fact.
		https://stackoverflow.com/questions/64050609/pyarrow-read-parquet-via-column-index-or-order
		"""
		file = File.get_by_id(id)
		columns = utils.wrangle.listify(columns)
		samples = utils.wrangle.listify(samples)
		
		f_dtypes = file.dtypes
		f_cols = file.columns

		if (file.is_ingested==False):
			# future: check if `query_fetcher` defined.
			df = utils.wrangle.path_to_df(
				file_path = file.source_path
				, source_file_format = file.file_format
				, column_names = columns
				, skip_header_rows = file.skip_header_rows
			)
		elif (file.is_ingested==True):
			df = pd.read_parquet(
				BytesIO(file.blob)
				, columns=columns
			)

		# Ensures columns are rearranged to be in the correct order.
		if ((columns is not None) and (df.columns.to_list() != columns)):
			df = df.filter(columns)
		# Specific rows.
		if (samples is not None):
			df = df.loc[samples]
		
		# Accepts dict{'column_name':'dtype_str'} or a single str.
		if (isinstance(f_dtypes, dict)):
			if (columns is None):
				columns = f_cols
			# Prunes out the excluded columns from the dtype dict.
			df_dtype_cols = list(f_dtypes.keys())
			for col in df_dtype_cols:
				if (col not in columns):
					del f_dtypes[col]
		elif (isinstance(f_dtypes, str)):
			pass #dtype just gets applied as-is.
		df = df.astype(f_dtypes)
		return df


	def to_numpy(id:int, columns:list=None, samples:list=None):
		"""
		This is the only place where to_numpy() relies on to_pandas(). 
		It does so because pandas is good with the parquet and dtypes.
		"""
		columns = utils.wrangle.listify(columns)
		samples = utils.wrangle.listify(samples)
		file = File.get_by_id(id)
		f_dataset = file.dataset
		# Handles when Dataset.Sequence is stored as a single .npy file
		if ((f_dataset.dataset_type=='sequence') and (file.is_ingested==False)):
			# Subsetting a File via `samples` is irrelevant here because the entire File is 1 sample.
			# Subset the columns:
			if (columns is not None):
				col_indices = utils.wrangle.colIndices_from_colNames(
					column_names = file.columns
					, desired_cols = columns
				)
			dtype = list(file.dtypes.values())[0] #`ingest==False` only allows singular dtype.

			source_path = file.dataset.source_path
			if (source_path.lower().endswith('.npy')):
				# The .npy file represents the entire dataset so we'll lazy load and grab a slice.			
				arr = np.load(source_path)
			else:
				# Handled by PIL. Check if it is a url or not. 
				if (val_url(source_path)):
					arr = np.array(
						Imaje.open(
							requests_get(source_path, stream=True).raw
					))
				else:
					arr = np.array(Imaje.open(source_path))
			
			if (arr.ndim==2):
				# grayscale image.
				if (columns is not None):
					arr = arr[:,col_indices].astype(dtype)
				else:
					arr = arr.astype(dtype)
			elif (arr.ndim==3):
				if (columns is not None):
					# 1st accessor[] gets the 2D. 2nd accessor[] the cols.
					arr = arr[file.file_index][:,col_indices].astype(dtype)
				else:
					arr = arr[file.file_index].astype(dtype)
			elif (arr.ndim==4):
				if (columns is not None):
					# 1st accessor[] gets the 3D. 2nd accessor[] the 2D. 3rd [] the cols.
					arr = arr[f_dataset.dataset_index][file.file_index][:,col_indices].astype(dtype)
				else:
					arr = arr[f_dataset.dataset_index][file.file_index].astype(dtype)

		else:
			df = File.to_pandas(id=id, columns=columns, samples=samples)
			arr = df.to_numpy()
		return arr


class Label(BaseModel):
	"""
	- Label accepts multiple columns in case it is already OneHotEncoded (e.g. tensors).
	- At this point, we assume that the Label is always a tabular dataset.
	"""
	columns = JSONField()
	column_count = IntegerField()
	unique_classes = JSONField(null=True) # For categoricals and binaries. None for continuous.
	
	dataset = ForeignKeyField(Dataset, backref='labels')
	
	def from_dataset(dataset_id:int, columns:list=None):
		d = Dataset.get_by_id(dataset_id)
		columns = utils.wrangle.listify(columns)

		if (d.dataset_type!='tabular'):
			raise Exception(dedent(f"""
			Yikes - Labels can only be created from `dataset_type='tabular'.
			But you provided `dataset_type`: <{d.dataset_type}>
			"""))
		d_cols = Dataset.get_main_file(dataset_id).columns
		
		if (columns is None):
			# Handy for sequence and image pipelines
			columns = d_cols
		elif (columns is not None):
			# Check that the user-provided columns exist.
			all_cols_found = all(c in d_cols for c in columns)
			if (not all_cols_found):
				raise Exception("\nYikes - You specified `columns` that do not exist in the Dataset.\n")

		# Check for duplicates of this label that already exist.
		cols_aplha = sorted(columns)
		d_labels = d.labels
		count = d_labels.count()
		if (count > 0):
			for l in d_labels:
				l_id = str(l.id)
				l_cols = l.columns
				l_cols_alpha = sorted(l_cols)
				if (cols_aplha == l_cols_alpha):
					raise Exception(f"\nYikes - This Dataset already has Label <id:{l_id}> with the same columns.\nCannot create duplicate.\n")

		"""
		- When multiple columns are provided, they must be OHE.
		- Figure out column count because classification_binary and associated 
		metrics can't be run on > 2 columns.
		- Negative values do not alter type of numpy int64 and float64 arrays.
		"""
		label_df = Dataset.to_pandas(id=dataset_id, columns=columns)
		column_count = len(columns)
		if (column_count > 1):
			unique_values = []
			for c in columns:
				uniques = label_df[c].unique()
				unique_values.append(uniques)
				if (len(uniques) == 1):
					print(
						f"Warning - There is only 1 unique value for this label column.\n" \
						f"Unique value: <{uniques[0]}>\n" \
						f"Label column: <{c}>\n"
					)
			flat_uniques = np.concatenate(unique_values).ravel()
			all_uniques = np.unique(flat_uniques).tolist()

			for i in all_uniques:
				if (
					((i == 0) or (i == 1)) 
					or 
					((i == 0.) or (i == 1.))
				):
					pass
				else:
					raise Exception(dedent(f"""
					Yikes - When multiple columns are provided, they must be One Hot Encoded:
					Unique values of your columns were neither (0,1) or (0.,1.) or (0.0,1.0).
					The columns you provided contained these unique values: {all_uniques}
					"""))
			unique_classes = all_uniques
			del label_df

			# Now check if each row in the labels is truly OHE.
			label_arr = Dataset.to_numpy(id=dataset_id, columns=columns)
			for i, arr in enumerate(label_arr):
				if 1 in arr:
					arr = list(arr)
					arr.remove(1)
					if 1 in arr:
						raise Exception(dedent(f"""
						Yikes - Label row <{i}> is supposed to be an OHE row,
						but it contains multiple hot columns where value is 1.
						"""))
				else:
					raise Exception(dedent(f"""
					Yikes - Label row <{i}> is supposed to be an OHE row,
					but it contains no hot columns where value is 1.
					"""))

		elif (column_count==1):
			# At this point, `label_df` is a single column df that needs to fecthed as a Series.
			col = columns[0]
			label_series = label_df[col]
			label_dtype = label_series.dtype
			if (np.issubdtype(label_dtype, np.floating)):
				unique_classes = None
			else:
				unique_classes = label_series.unique().tolist()
				class_count = len(unique_classes)

				if (
					(np.issubdtype(label_dtype, np.signedinteger))
					or
					(np.issubdtype(label_dtype, np.unsignedinteger))
				):
					if (class_count >= 5):
						print(
							f"Tip - Detected  `unique_classes >= {class_count}` for an integer Label." \
							f"If this Label is not meant to be categorical, then we recommend you convert to a float-based dtype." \
							f"Although you'll still be able to bin these integers when it comes time to make a Splitset."
						)
				if (class_count == 1):
					print(
						f"Tip - Only detected 1 unique label class. Should have 2 or more unique classes." \
						f"Your Label's only class was: <{unique_classes[0]}>."
					)

		label = Label.create(
			dataset = d
			, columns = columns
			, column_count = column_count
			, unique_classes = unique_classes
		)
		return label


	def to_pandas(id:int, samples:list=None):
		label = Label.get_by_id(id)
		dataset = label.dataset
		columns = label.columns
		samples = utils.wrangle.listify(samples)
		df = Dataset.to_pandas(id=dataset.id, columns=columns, samples=samples)
		return df


	def to_numpy(id:int, samples:list=None):
		label = Label.get_by_id(id)
		dataset = label.dataset
		columns = label.columns
		samples = utils.wrangle.listify(samples)
		arr = Dataset.to_numpy(id=dataset.id, columns=columns, samples=samples)
		return arr


	def get_dtypes(id:int):
		label = Label.get_by_id(id)

		dataset = label.dataset
		l_cols = label.columns
		tabular_dtype = Dataset.get_main_file(dataset.id).dtypes

		label_dtypes = {}
		for key,value in tabular_dtype.items():
			for col in l_cols:         
				if (col == key):
					label_dtypes[col] = value
					# Exit `col` loop early becuase matching `col` found.
					break
		return label_dtypes


	def preprocess(
		id:int
		, labelinterpolater_id:str = 'latest'
		#, imputerset_id:str='latest'
		#, outlierset_id:str='latest'
		, labelcoder_id:str = 'latest'
		, samples:dict = None#Used by interpolation to process separately. Not used to selectively filter samples. If you need that, just fetch via index from returned array.
		, _samples_train:list = None#Used during job.run()
		, _library:str = None#Used during job.run() and during infer()
		, _job:object = None#Used during job.run() and during infer()
		, _fitted_label:object = None#Used during infer()
	):
		label = Label.get_by_id(id)
		label_array = label.to_numpy()
		if (_job is not None):
			fold = _job.fold

		# Interpolate
		if ((labelinterpolater_id!='skip') and (label.labelinterpolaters.count()>0)):
			if (labelinterpolater_id=='latest'):
				labelinterpolater = label.labelinterpolaters[-1]
			elif isinstance(labelinterpolater_id, int):
				labelinterpolater = LabelInterpolater.get_by_id(labelinterpolater_id)
			else:
				raise Exception(f"\nYikes - Unexpected value <{labelinterpolater_id}> for `labelinterpolater_id` argument.\n")

			labelinterpolater = label.labelinterpolaters[-1]
			label_array = labelinterpolater.interpolate(array=label_array, samples=samples)
		
		#Encode
		# During inference the old labelcoder may be used so we have to nest the count.
		if (labelcoder_id!='skip'):
			if (_fitted_label is not None):
				if (_fitted_label.labelcoders.count()>0):
					labelcoder, fitted_encoders = Predictor.get_fitted_labelcoder(
						job=_job, label=_fitted_label
					)

					label_array = utils.encoding.encoder_transform_labels(
						arr_labels=label_array,
						fitted_encoders=fitted_encoders, labelcoder=labelcoder
					)

			elif (label.labelcoders.count()>0):
				if (labelcoder_id=='latest'):
					labelcoder = label.labelcoders[-1]
				elif (isinstance(labelinterpolater_id, int)):
					labelcoder = LabelCoder.get_by_id(labelcoder_id)
				else:
					raise Exception(f"\nYikes - Unexpected value <{labelcoder_id}> for `labelcoder_id` argument.\n")

				if ((_job is None) or (_samples_train is None)):
					raise Exception("Yikes - both `job_id` and `key_train` must be defined in order to use `labelcoder`")

				fitted_encoders = utils.encoding.encoder_fit_labels(
					arr_labels=label_array, samples_train=_samples_train,
					labelcoder=labelcoder
				)

				if (fold is not None):
					queue = _job.queue
					jobs = [j for j in queue.jobs if j.fold==fold]
					for j in jobs:
						if (j.fittedlabelcoders.count()==0):
							FittedLabelCoder.create(fitted_encoders=fitted_encoders, job=j, labelcoder=labelcoder)
				# Unfolded jobs will all have the same fits.
				elif (fold is None):
					jobs = list(_job.queue.jobs)
					for j in jobs:
						if (j.fittedlabelcoders.count()==0):
							FittedLabelCoder.create(fitted_encoders=fitted_encoders, job=j, labelcoder=labelcoder)

				label_array = utils.encoding.encoder_transform_labels(
					arr_labels=label_array,
					fitted_encoders=fitted_encoders, labelcoder=labelcoder
				)
			elif (label.labelcoders.count()==0):
				pass

		if (_library == 'pytorch'):
			label_array = FloatTensor(label_array)
		return label_array


class Feature(BaseModel):
	"""
	- Remember, a Feature is just a record of the columns being used.
	- Decided not to go w subclasses of Unsupervised and Supervised because that would complicate the SDK for the user,
	  and it essentially forked every downstream model into two subclasses.
	- PCA components vary across features. When different columns are used those columns have different component values.
	"""
	columns = JSONField(null=True)
	columns_excluded = JSONField(null=True)
	dataset = ForeignKeyField(Dataset, backref='features')


	def from_dataset(
		dataset_id:int
		, include_columns:list=None
		, exclude_columns:list=None
		#Future: runPCA #,run_pca:boolean=False # triggers PCA analysis of all columns
	):
		#As we get further away from the `Dataset.<Types>` they need less isolation.
		dataset = Dataset.get_by_id(dataset_id)
		include_columns = utils.wrangle.listify(include_columns)
		exclude_columns = utils.wrangle.listify(exclude_columns)
		d_cols = Dataset.get_main_file(dataset_id).columns
		
		if ((include_columns is not None) and (exclude_columns is not None)):
			raise Exception("\nYikes - You can set either `include_columns` or `exclude_columns`, but not both.\n")

		if (include_columns is not None):
			# check columns exist
			all_cols_found = all(col in d_cols for col in include_columns)
			if (not all_cols_found):
				raise Exception("\nYikes - You specified `include_columns` that do not exist in the Dataset.\n")
			# inclusion
			columns = include_columns
			# exclusion
			columns_excluded = d_cols
			for col in include_columns:
				columns_excluded.remove(col)

		elif (exclude_columns is not None):
			all_cols_found = all(col in d_cols for col in exclude_columns)
			if (not all_cols_found):
				raise Exception("\nYikes - You specified `exclude_columns` that do not exist in the Dataset.\n")
			# exclusion
			columns_excluded = exclude_columns
			# inclusion
			columns = d_cols
			for col in exclude_columns:
				columns.remove(col)
			if (not columns):
				raise Exception("\nYikes - You cannot exclude every column in the Dataset. For there will be nothing to analyze.\n")
		else:
			columns = d_cols
			columns_excluded = None

		"""
		- Check that this Dataset does not already have a Feature that is exactly the same.
		- There are less entries in `excluded_columns` so maybe it's faster to compare that.
		"""
		if columns_excluded is not None:
			cols_aplha = sorted(columns_excluded)
		else:
			cols_aplha = None
		d_features = dataset.features
		count = d_features.count()
		if (count > 0):
			for f in d_features:
				f_id = str(f.id)
				f_cols = f.columns_excluded
				if (f_cols is not None):
					f_cols_alpha = sorted(f_cols)
				else:
					f_cols_alpha = None
				if (cols_aplha == f_cols_alpha):
					raise Exception(dedent(f"""
					Yikes - This Dataset already has Feature <id:{f_id}> with the same columns.
					Cannot create duplicate.
					"""))

		feature = Feature.create(
			dataset = dataset
			, columns = columns
			, columns_excluded = columns_excluded
		)
		return feature


	def to_pandas(id:int, samples:list=None, columns:list=None):
		samples = utils.wrangle.listify(samples)
		columns = utils.wrangle.listify(columns)
		df = Feature.get_feature(
			id = id
			, numpy_or_pandas = 'pandas'
			, samples = samples
			, columns = columns
		)
		return df


	def to_numpy(id:int, samples:list=None, columns:list=None):
		samples = utils.wrangle.listify(samples)
		columns = utils.wrangle.listify(columns)
		arr = Feature.get_feature(
			id = id
			, numpy_or_pandas = 'numpy'
			, samples = samples
			, columns = columns
		)
		return arr


	def get_feature(
		id:int
		, numpy_or_pandas:str
		, samples:list = None
		, columns:list = None
	):
		feature = Feature.get_by_id(id)
		samples = utils.wrangle.listify(samples)
		columns = utils.wrangle.listify(columns)
		f_cols = feature.columns

		if (columns is not None):
			for c in columns:
				if c not in f_cols:
					raise Exception("\nYikes - Cannot fetch column '{c}' because it is not in `Feature.columns`.\n")
			f_cols = columns    

		dataset_id = feature.dataset.id

		if (numpy_or_pandas == 'numpy'):
			f_data = Dataset.to_numpy(
				id = dataset_id
				, columns = f_cols
				, samples = samples
			)
		elif (numpy_or_pandas == 'pandas'):
			f_data = Dataset.to_pandas(
				id = dataset_id
				, columns = f_cols
				, samples = samples
			)
		return f_data


	def get_dtypes(
		id:int
	):
		feature = Feature.get_by_id(id)
		f_cols = feature.columns
		tabular_dtype = feature.dataset.get_main_file().dtypes

		feature_dtypes = {}
		for key,value in tabular_dtype.items():
			for col in f_cols:         
				if (col == key):
					feature_dtypes[col] = value
					# Exit `col` loop early becuase matching `col` found.
					break
		return feature_dtypes


	def preprocess(
		id:int
		, supervision:str = 'supervised'
		, interpolaterset_id:str = 'latest'
		#, imputerset_id:str='latest'
		#, outlierset_id:str='latest'
		, encoderset_id:str = 'latest'
		, window_id:str = 'latest'
		, featureshaper_id:str = 'latest'
		, samples:dict = None#Used by Interpolaterset to process separately. Not used to selectively filter samples. If you need that, just fetch via index from returned array.
		, _samples_train:list = None#Used during job.run()
		, _library:str = None#Used during job.run() and during infer()
		, _job:object = None#Used during job.run() and during infer()
		, _fitted_feature:object = None#Used during infer()
	):
		#As more optional preprocessers were added, we were calling a lot of code everywhere features were fetched.
		feature = Feature.get_by_id(id)
		feature_array = feature.to_numpy()
		if (_job is not None):
			fold = _job.fold

		# Although this is not the first preprocess, other preprocesses need to know the window ranges.
		if ((window_id!='skip') and (feature.windows.count()>0)):
			if (window_id=='latest'):
				window = feature.windows[-1]
			elif isinstance(window_id, int):
				window = Window.get_by_id(window_id)
			else:
				raise Exception(f"\nYikes - Unexpected value <{window_id}> for `window_id` argument.\n")

			# During encoding we'll need the raw rows, not window indices.
			if ((_samples_train is not None) and (_job is not None) and (_fitted_feature is None)):
				samples_unshifted = window.samples_unshifted
				train_windows = [samples_unshifted[idx] for idx in _samples_train]
				# We need all of the rows from all of the windows. But only the unique ones. 
				windows_flat = [item for sublist in train_windows for item in sublist]
				_samples_train = list(set(windows_flat))
		else:
			window = None

		# --- Interpolate ---
		if ((interpolaterset_id!='skip') and (feature.interpolatersets.count()>0)):
			if (interpolaterset_id=='latest'):
				ip = feature.interpolatersets[-1]
			elif isinstance(interpolaterset_id, int):
				ip = Interpolaterset.get_by_id(interpolaterset_id)
			else:
				raise Exception(f"\nYikes - Unexpected value <{interpolaterset_id}> for `interpolaterset_id` argument.\n")

			feature_array = ip.interpolate(array=feature_array, samples=samples, window=window)

		# --- Impute ---
		# --- Outliers ---

		# --- Encode ---
		# During inference the old encoderset may be used so we have to nest the count.
		if (encoderset_id!='skip'):
			if (_fitted_feature is not None):
				if (_fitted_feature.encodersets.count()>0):
					encoderset, fitted_encoders = Predictor.get_fitted_encoderset(
						job=_job, feature=_fitted_feature
					)
					feature_array = utils.encoding.encoderset_transform_features(
						arr_features=feature_array,
						fitted_encoders=fitted_encoders, encoderset=encoderset
					)

			elif (feature.encodersets.count()>0):
				if (encoderset_id=='latest'):
					encoderset = feature.encodersets[-1]
				elif (isinstance(encoderset_id, int)):
					encoderset = Encoderset.get_by_id(encoderset_id)
				else:
					raise Exception(f"\nYikes - Unexpected value <{encoderset_id}> for `encoderset_id` argument.\n")

				if ((_job is None) or (_samples_train is None)):
					raise Exception("Yikes - both `job_id` and `key_train` must be defined in order to use `encoderset`")

				# This takes the entire array because it handles all features and splits.
				fitted_encoders = utils.encoding.encoderset_fit_features(
					arr_features=feature_array, samples_train=_samples_train,
					encoderset=encoderset
				)

				feature_array = utils.encoding.encoderset_transform_features(
					arr_features=feature_array,
					fitted_encoders=fitted_encoders, encoderset=encoderset
				)
				# Record the `fit` for decoding predictions via `inverse_transform`.
				if (fold is not None):
					queue = _job.queue
					jobs = [j for j in queue.jobs if j.fold==fold]
					for j in jobs:
						if (j.fittedencodersets.count()==0):
							FittedEncoderset.create(fitted_encoders=fitted_encoders, job=j, encoderset=encoderset)
				# Unfolded jobs will all have the same fits.
				elif (fold is None):
					jobs = list(_job.queue.jobs)
					for j in jobs:
						if (j.fittedencodersets.count()==0):
							FittedEncoderset.create(fitted_encoders=fitted_encoders, job=j, encoderset=encoderset)
			elif (feature.encodersets.count()==0):
				pass

		# --- Window ---
		if ((window_id!='skip') and (feature.windows.count()>0)):
			# Window object is fetched above because the other features need it. 
			features_ndim = feature_array.ndim

			"""
			- *Need to do the label first in order to access the unwindowed `arr_features`.*
			- During pure inference, there may be no shifted samples.
			- The window grouping adds an extra dimension to the data.
			"""
			if (window.samples_shifted is not None):
				# ndim 2 and 3 are grouping rows of 2D tables.
				if ((features_ndim==2) or (features_ndim==4)):
					label_array = np.array([feature_array[w] for w in window.samples_shifted])
				elif (features_ndim==3):
					label_array = []
					for i, sample in enumerate(feature_array):
						label_array.append(
							[feature_array[i][w] for w in window.samples_shifted]
						)
					label_array = np.array(label_array)
				# whereas ndim 4 is grouping entire images.
				elif (features_ndim==4):
					label_array = []
					for window in window.samples_shifted:
						window_arr = [feature_array[sample] for sample in window]
						label_array.append(window_arr)
					label_array = np.array(label_array)

			elif (window.samples_shifted is None):
				label_array = None

			# Unshifted features.
			if ((features_ndim==2) or (features_ndim==4)):
				feature_array = np.array([feature_array[w] for w in window.samples_unshifted])
			elif (features_ndim==3):
				feature_holder = []
				for i, site in enumerate(feature_array):
					feature_holder.append(
						[feature_array[i][w] for w in window.samples_unshifted]
					)
				feature_array = np.array(feature_holder)
			elif (features_ndim==4):
				feature_holder = []
				for window in window.samples_unshifted:
					window_arr = [feature_array[sample] for sample in window]
					feature_holder.append(window_arr)
				feature_array = np.array(feature_holder)


		if ((featureshaper_id!='skip') and (feature.featureshapers.count()>0)):
			if (featureshaper_id=='latest'):
				featureshaper = feature.featureshapers[-1]
			elif isinstance(featureshaper_id, int):
				featureshaper = FeatureShaper.get_by_id(featureshaper_id)
			else:
				raise Exception(f"\nYikes - Unexpected value <{featureshaper_id}> for `featureshaper_id` argument.\n")

			old_shape = feature_array.shape
			new_shape = []
			for i in featureshaper.reshape_indices:
				if (type(i) == int):
					new_shape.append(old_shape[i])
				elif (type(i) == str):
					new_shape.append(int(i))
				elif (type(i)== tuple):
					indices = [old_shape[idx] for idx in i]
					new_shape.append(math.prod(indices))
			new_shape = tuple(new_shape)

			try:
				feature_array = feature_array.reshape(new_shape)
			except:
				raise Exception(f"\nYikes - Failed to rearrange the feature of shape:<{old_shape}> into new shape:<{new_shape}> based on the `reshape_indices`:<{reshape_indices}> provided. Make sure both shapes have the same multiplication product.\n")
			
			column_position = featureshaper.column_position
			if (old_shape[-1] != new_shape[column_position]):
				raise Exception(f"\nYikes - Reshape succeeded, but expected the last dimension of the old shape:<{old_shape[-1]}> to match the dimension found <{old_shape[column_position]}> in the new shape's `featureshaper.column_position:<{column_position}>.\n")

			# Unsupervised labels.
			if ((window_id!='skip') and (feature.windows.count()>0) and (window.samples_shifted is not None)):
				try:
					label_array = label_array.reshape(new_shape)
				except:
					raise Exception(f"\nYikes - Failed to rearrange the label shape {old_shape} into based on {new_shape} the `reshape_indices` {reshape_indices} provided .\n")


		if (_library == 'pytorch'):
			feature_array = FloatTensor(feature_array)

		if (supervision=='supervised'):
			return feature_array
		elif (supervision=='unsupervised'):
			if (_library == 'pytorch'):
				label_array = FloatTensor(label_array)
			return feature_array, label_array


	def preprocess_remaining_cols(
		id:int
		, existing_preprocs:object #Feature.<preprocessset>.<preprocesses> Queryset.
		, include:bool = True
		, verbose:bool = True
		, dtypes:list = None
		, columns:list = None
	):
		"""
		- Used by the preprocessors to figure out which columns have yet to be assigned preprocessors.
		"""
		feature = Feature.get_by_id(id)
		feature_cols = feature.columns
		feature_dtypes = feature.get_dtypes()

		dtypes = utils.wrangle.listify(dtypes)
		columns = utils.wrangle.listify(columns)

		class_name = existing_preprocs.model.__name__.lower()

		# 1. Figure out which columns have yet to be encoded.
		# Order-wise no need to validate filters if there are no columns left to filter.
		# Remember Feature columns are a subset of the Dataset columns.
		index = existing_preprocs.count()
		if (index==0):
			initial_columns = feature_cols
		elif (index>0):
			# Get the leftover columns from the last one.
			initial_columns = existing_preprocs[-1].leftover_columns
			if (len(initial_columns) == 0):
				raise Exception(f"\nYikes - All features already have {class_name}s associated with them. Cannot add more preprocesses to this set.\n")
		initial_dtypes = {}
		for key,value in feature_dtypes.items():
			for col in initial_columns:
				if (col == key):
					initial_dtypes[col] = value
					# Exit `c` loop early becuase matching `c` found.
					break

		if (verbose == True):
			print(f"\n___/ {class_name}_index: {index} \\_________\n") # Intentionally no trailing `\n`.

		if (dtypes is not None):
			for typ in dtypes:
				if (typ not in set(initial_dtypes.values())):
					raise Exception(dedent(f"""
					Yikes - dtype '{typ}' was not found in remaining dtypes.
					Remove '{typ}' from `dtypes` and try again.
					"""))
		
		if (columns is not None):
			for c in columns:
				if (col not in initial_columns):
					raise Exception(dedent(f"""
					Yikes - Column '{col}' was not found in remaining columns.
					Remove '{col}' from `columns` and try again.
					"""))
		
		# 3a. Figure out which columns the filters apply to.
		if (include==True):
			# Add to this empty list via inclusion.
			matching_columns = []
			
			if ((dtypes is None) and (columns is None)):
				columns = initial_columns

			if (dtypes is not None):
				for typ in dtypes:
					for key,value in initial_dtypes.items():
						if (value == typ):
							matching_columns.append(key)
							# Don't `break`; there can be more than one match.

			if (columns is not None):
				for c in columns:
					# Remember that the dtype has already added some columns.
					if (c not in matching_columns):
						matching_columns.append(c)
					elif (c in matching_columns):
						# We know from validation above that the column existed in initial_columns.
						# Therefore, if it no longer exists it means that dtype_exclude got to it first.
						raise Exception(dedent(f"""
						Yikes - The column '{c}' was already included by `dtypes`, so this column-based filter is not valid.
						Remove '{c}' from `columns` and try again.
						"""))

		elif (include==False):
			# Prune this list via exclusion. `copy`` so we original can be used later.
			matching_columns = initial_columns.copy()

			if (dtypes is not None):
				for typ in dtypes:
					for key,value in initial_dtypes.items():
						if (value == typ):
							matching_columns.remove(key)
							# Don't `break`; there can be more than one match.
			if (columns is not None):
				for c in columns:
					# Remember that the dtype has already pruned some columns.
					if (c in matching_columns):
						matching_columns.remove(c)
					elif (c not in matching_columns):
						# We know from validation above that the column existed in initial_columns.
						# Therefore, if it no longer exists it means that dtype_exclude got to it first.
						raise Exception(dedent(f"""
						Yikes - The column '{c}' was already excluded by `dtypes`,
						so this column-based filter is not valid.
						Remove '{c}' from `dtypes` and try again.
						"""))
			
		if (len(matching_columns) == 0):
			if (include == True):
				inex_str = "inclusion"
			elif (include == False):
				inex_str = "exclusion"
			raise Exception(f"\nYikes - There are no columns left to use after applying the dtype and column {inex_str} filters.\n")

		# 3b. Record the  output.
		leftover_columns =  list(set(initial_columns) - set(matching_columns))
		# This becomes leftover_dtypes.
		for c in matching_columns:
			del initial_dtypes[c]

		original_filter = {
			'include': include
			, 'dtypes': dtypes
			, 'columns': columns
		}
		if (verbose == True):
			print(
				f"=> The column(s) below matched your filter(s) {class_name} filters.\n\n" \
				f"{matching_columns}\n" 
			)
			if (len(leftover_columns) == 0):
				print(
					f"=> Done. All feature column(s) have {class_name}(s) associated with them.\n" \
					f"No more FeatureCoders can be added to this Encoderset.\n"
				)
			elif (len(leftover_columns) > 0):
				print(
					f"=> The remaining column(s) and dtype(s) are available for downstream {class_name}(s):\n" \
					f"{pformat(initial_dtypes)}\n"
				)
		return index, matching_columns, leftover_columns, original_filter, initial_dtypes


	def get_encoded_column_names(id:int):
		"""
		- The order of columns changes during encoding.
		- OHE generates multiple encoded columns from a single column e.g. 'color=green'.
		- `encoded_column_names` proactively lists names from `fit.categories_`
		"""
		feature = Feature.get_by_id(id)
		encodersets = feature.encodersets

		if (encodersets.count()==0):
			all_encoded_column_names = feature.columns
		else:
			encoderset = encodersets[-1]
			featurecoders = encoderset.featurecoders
			num_featurecoders = featurecoders.count()
			if (num_featurecoders==0):
				all_encoded_column_names = feature.columns
			else:
				all_encoded_column_names = []
				for i, fc in enumerate(featurecoders):
					all_encoded_column_names += fc.encoded_column_names
					if ((i+1==num_featurecoders) and (len(fc.leftover_columns)>0)):
						all_encoded_column_names += fc.leftover_columns
		return all_encoded_column_names




class Window(BaseModel):
	"""
	- Although Window could be related to Dataset, they really have nothing to do with Labels, 
	  and they are used a lot when fetching Features.
	"""
	size_window = IntegerField()
	size_shift = IntegerField()
	window_count = IntegerField()#number of windows in the dataset.
	samples_unshifted = JSONField()#underlying sample indices of each window.
	samples_shifted = JSONField(null=True)#underlying sample indices of each window.
	feature = ForeignKeyField(Feature, backref='windows')


	def from_feature(
		feature_id:int
		, size_window:int
		, size_shift:int
		, record_shifted:bool=True
	):
		feature = Feature.get_by_id(feature_id)
		dataset_type = feature.dataset.dataset_type
		
		if (dataset_type=='tabular' or dataset_type=='sequence'):
			# Works for both since it is based on their 2D/ inner 2D dimensions.
			sample_count = feature.dataset.get_main_file().shape['rows']
		elif (dataset_type=='image'):
			# If each seq is an img, think images over time.
			sample_count = feature.dataset.datasets.count()

		if (record_shifted==True):
			if ((size_window < 1) or (size_window > (sample_count - size_shift))):
				raise Exception("\nYikes - Failed: `(size_window < 1) or (size_window > (sample_count - size_shift)`.\n")
			if ((size_shift < 1) or (size_shift > (sample_count - size_window))):
				raise Exception("\nYikes - Failed: `(size_shift < 1) or (size_shift > (sample_count - size_window)`.\n")

			window_count = math.floor((sample_count - size_window) / size_shift)
			prune_shifted_lead = sample_count - ((window_count - 1)*size_shift + size_window)
			prune_unshifted_lead = prune_shifted_lead - size_shift

			samples_shifted = []
			samples_unshifted = []
			file_indices = list(range(sample_count))
			window_indices = list(range(window_count))

			for i in window_indices:
				unshifted_start = prune_unshifted_lead + (i*size_shift)
				unshifted_stop = unshifted_start + size_window
				unshifted_samples = file_indices[unshifted_start:unshifted_stop]
				samples_unshifted.append(unshifted_samples)

				shifted_start = unshifted_start + size_shift
				shifted_stop = shifted_start + size_window
				shifted_samples = file_indices[shifted_start:shifted_stop]
				samples_shifted.append(shifted_samples)

		# This is for pure inference. Just taking as many Windows as you can.
		elif (record_shifted==False):
			window_count = math.floor((sample_count - size_window) / size_shift) + 1
			prune_unshifted_lead = sample_count - ((window_count - 1)*size_shift + size_window)

			samples_unshifted = []
			window_indices = list(range(window_count))
			file_indices = list(range(sample_count))

			for i in window_indices:
				unshifted_start = prune_unshifted_lead + (i*size_shift)
				unshifted_stop = unshifted_start + size_window
				unshifted_samples = file_indices[unshifted_start:unshifted_stop]
				samples_unshifted.append(unshifted_samples)
			samples_shifted = None

		window = Window.create(
			size_window = size_window
			, size_shift = size_shift
			, window_count = window_count
			, samples_unshifted = samples_unshifted
			, samples_shifted = samples_shifted
			, feature_id = feature.id
		)
		return window


class Splitset(BaseModel):
	"""
	- Here the `samples_` attributes contain indices.
	-ToDo: store and visualize distributions of each column in training split, including label.
	-Future: is it useful to specify the size of only test for unsupervised learning?
	"""
	samples = JSONField()
	sizes = JSONField()
	supervision = CharField()
	has_test = BooleanField()
	has_validation = BooleanField()
	bin_count = IntegerField(null=True)
	unsupervised_stratify_col = CharField(null=True)

	label = ForeignKeyField(Label, deferrable='INITIALLY DEFERRED', null=True, backref='splitsets')
	# Featureset is a many-to-many relationship between Splitset and Feature.

	def make(
		feature_ids:list
		, label_id:int = None
		, size_test:float = None
		, size_validation:float = None
		, bin_count:float = None
		, unsupervised_stratify_col:str = None
		, name:str = None
		, description:str = None
	):
		# The first feature_id is used for stratification, so it's best to use Tabular data in this slot.
		# --- Verify splits ---
		if (size_test is not None):
			if ((size_test <= 0.0) or (size_test >= 1.0)):
				raise Exception("\nYikes - `size_test` must be between 0.0 and 1.0\n")
			# Don't handle `has_test` here. Need to check label first.
		
		if ((size_validation is not None) and (size_test is None)):
			raise Exception("\nYikes - you specified a `size_validation` without setting a `size_test`.\n")

		if (size_validation is not None):
			if ((size_validation <= 0.0) or (size_validation >= 1.0)):
				raise Exception("\nYikes - `size_test` must be between 0.0 and 1.0\n")
			sum_test_val = size_validation + size_test
			if sum_test_val >= 1.0:
				raise Exception("\nYikes - Sum of `size_test` + `size_test` must be between 0.0 and 1.0 to leave room for training set.\n")
			"""
			Have to run train_test_split twice do the math to figure out the size of 2nd split.
			Let's say I want {train:0.67, validation:0.13, test:0.20}
			The first test_size is 20% which leaves 80% of the original data to be split into validation and training data.
			(1.0/(1.0-0.20))*0.13 = 0.1625
			"""
			pct_for_2nd_split = (1.0/(1.0-size_test))*size_validation
			has_validation = True
		else:
			has_validation = False

		# --- Verify features ---
		feature_ids = utils.wrangle.listify(feature_ids)
		feature_lengths = []
		for f_id in feature_ids:
			f = Feature.get_by_id(f_id)
			f_dataset = f.dataset
			f_dset_type = f_dataset.dataset_type

			# Determine the number of samples in each feature. Varies based on dset_type and windowing.
			if (
				(f.windows.count()>0)
				and 
				# In 3D sequence, the sample is the patient, not the intra-patient time windows.
				((f_dset_type == 'tabular') or (f_dset_type == 'image'))
			):
				window = f.windows[-1]
				f_length = window.window_count
			else:
				if (f_dset_type == 'tabular'):
					f_length = Dataset.get_main_file(f_dataset.id).shape['rows']
				elif (f_dset_type == 'sequence'):
					f_length = f_dataset.file_count
				elif (f_dset_type == 'image'):
					f_length = f_dataset.datasets.count()
			feature_lengths.append(f_length)
		if (len(set(feature_lengths)) != 1):
			raise Exception("Yikes - List of Features you provided contain different amounts of samples.")
		sample_count = feature_lengths[0]		
		arr_idx = np.arange(sample_count)


		# --- Prepare for splitting ---
		feature = Feature.get_by_id(feature_ids[0])
		f_dataset = feature.dataset
		f_dset_type = f_dataset.dataset_type
		"""
		- Simulate an index to be split alongside features and labels
		  in order to keep track of the samples being used in the resulting splits.
		- If it's windowed, the samples become the windows instead of the rows.
		- Images just use the index for stratification.
		"""
		feature_array = feature.preprocess(encoderset_id='skip')
		samples = {}
		sizes = {}

		if (size_test is not None):
			# ------ Stratification prep ------
			if (label_id is not None):
				if (unsupervised_stratify_col is not None):
					raise Exception("\nYikes - `unsupervised_stratify_col` cannot be present is there is a Label.\n")
				if (len(feature.windows)>0):
					raise Exception("\nYikes - At this point in time, AIQC does not support the use of windowed Features with Labels.\n")

				has_test = True
				supervision = "supervised"

				# We don't need to prevent duplicate Label/Feature combos because Splits generate different samples each time.
				label = Label.get_by_id(label_id)
				stratify_arr = label.preprocess(labelcoder_id='skip')

				# Check number of samples in Label vs Feature, because they can come from different Datasets.
				l_length = label.dataset.get_main_file().shape['rows']				
				if (label.dataset.id != f_dataset.id):
					if (l_length != sample_count):
						raise Exception("\nYikes - The Datasets of your Label and Feature do not contains the same number of samples.\n")

				# check for OHE cols and reverse them so we can still stratify ordinally.
				if (stratify_arr.shape[1] > 1):
					stratify_arr = np.argmax(stratify_arr, axis=1)
				# OHE dtype returns as int64
				stratify_dtype = stratify_arr.dtype


			elif (label_id is None):
				if (len(feature_ids) > 1):
					# Mainly because it would require multiple labels.
					raise Exception("\nYikes - Sorry, at this time, AIQC does not support unsupervised learning on multiple Features.\n")

				has_test = False
				supervision = "unsupervised"
				label = None

				indices_lst_train = arr_idx.tolist()

				if (unsupervised_stratify_col is not None):
					# Get the column for stratification.
					column_names = f_dataset.get_main_file().columns
					col_index = utils.wrangle.colIndices_from_colNames(column_names=column_names, desired_cols=[unsupervised_stratify_col])[0]

					dimensions = feature_array.ndim
					if (dimensions==2):
						stratify_arr = feature_array[:,col_index]
					elif (dimensions==3):
						# Used by sequence and tabular-windowed. Reduces to 2D w data from 1 column.
						stratify_arr = feature_array[:,:,col_index]
					elif (dimensions==4):
						# Used by image and sequence-windowed. Reduces to 3D w data from 1 column.
						stratify_arr = feature_array[:,:,:,col_index]
					elif (dimensions==5):
						# Used by image-windowed. Reduces to 4D w data from 1 column.
						stratify_arr = feature_array[:,:,:,:,col_index]
					stratify_dtype = stratify_arr.dtype
					
					dimensions = stratify_arr.ndim
					# In order to stratify, we need a single value from each sample.
					if ((stratify_arr.shape[-1] > 1) and (dimensions > 1)):
						# We want a 2D array where each 1D array reprents the desired column across all dimensions.
						# 1D already has a single value and 2D just works.
						if (dimensions==3):
							shape = stratify_arr.shape
							stratify_arr = stratify_arr.reshape(shape[0], shape[1]*shape[2])
						elif (dimensions==4):
							shape = stratify_arr.shape
							stratify_arr = stratify_arr.reshape(shape[0], shape[1]*shape[2]*shape[3])

						if (np.issubdtype(stratify_dtype, np.number) == True):
							stratify_arr = np.median(stratify_arr, axis=1)
						elif (np.issubdtype(stratify_dtype, np.number) == False):
							stratify_arr = np.array([mode(arr1D)[0][0] for arr1D in stratify_arr])
						
						# Now its 1D so reshape to 2D for the rest of the process.
						stratify_arr = stratify_arr.reshape(stratify_arr.shape[0], 1)

				elif (unsupervised_stratify_col is None):
					if (bin_count is not None):
			 			raise Exception("\nYikes - `bin_count` cannot be set if `unsupervised_stratify_col is None` and `label_id is None`.\n")
					stratify_arr = None#Used in if statements below.

			# ------ Stratified vs Unstratified ------		
			if (stratify_arr is not None):
				"""
				- `sklearn.model_selection.train_test_split` = https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.train_test_split.html
				- `shuffle` happens before the split. Although preserves a df's original index, we don't need to worry about that because we are providing our own indices.
				- Don't include the Dataset.Image.feature pixel arrays in stratification.
				"""
				# `bin_count` is only returned so that we can persist it.
				stratifier1, bin_count = utils.wrangle.stratifier_by_dtype_binCount(
					stratify_dtype = stratify_dtype,
					stratify_arr = stratify_arr,
					bin_count = bin_count
				)

				features_train, features_test, stratify_train, stratify_test, indices_train, indices_test = train_test_split(
					feature_array, stratify_arr, arr_idx
					, test_size = size_test
					, stratify = stratifier1
					, shuffle = True
				)

				if (size_validation is not None):
					stratifier2, bin_count = utils.wrangle.stratifier_by_dtype_binCount(
						stratify_dtype = stratify_dtype,
						stratify_arr = stratify_train, #This split is different from stratifier1.
						bin_count = bin_count
					)

					features_train, features_validation, stratify_train, stratify_validation, indices_train, indices_validation = train_test_split(
						features_train, stratify_train, indices_train
						, test_size = pct_for_2nd_split
						, stratify = stratifier2
						, shuffle = True
					)

			elif (stratify_arr is None):
				features_train, features_test, indices_train, indices_test = train_test_split(
					feature_array, arr_idx
					, test_size = size_test
					, shuffle = True
				)

				if (size_validation is not None):
					features_train, features_validation, indices_train, indices_validation = train_test_split(
						features_train, indices_train
						, test_size = pct_for_2nd_split
						, shuffle = True
					)

			if (size_validation is not None):
				indices_lst_validation = indices_validation.tolist()
				samples["validation"] = indices_lst_validation	

			indices_lst_train, indices_lst_test  = indices_train.tolist(), indices_test.tolist()
			samples["train"] = indices_lst_train
			samples["test"] = indices_lst_test

			size_train = 1.0 - size_test
			if (size_validation is not None):
				size_train -= size_validation
				count_validation = len(indices_lst_validation)
				sizes["validation"] =  {"percent": size_validation, "count": count_validation}
			
			count_test = len(indices_lst_test)
			count_train = len(indices_lst_train)
			sizes["test"] = {"percent": size_test, "count": count_test}
			sizes["train"] = {"percent": size_train, "count": count_train}

		# This is used by inference where we just want all of the samples.
		elif(size_test is None):
			if (unsupervised_stratify_col is not None):
				raise Exception("\nYikes - `unsupervised_stratify_col` present without a `size_test`.\n")

			has_test = False
			samples["train"] = arr_idx.tolist()
			sizes["train"] = {"percent": 1.0, "count":sample_count}
			# Remaining attributes are already `None`.

			# Unsupervised inference.
			if (label_id is None):
				label = None
				supervision = "unsupervised"
			# Supervised inference with metrics.
			elif (label_id is not None):
				label = Label.get_by_id(label_id)
				supervision = "supervised"

		# Sort the indices for easier human inspection and potentially faster seeking?
		for split, indices in samples.items():
			samples[split] = sorted(indices)
		# Sort splits by logical order. We'll rely on this during 2D interpolation.
		keys = list(samples.keys())
		if (("validation" in keys) and ("test" in keys)):
			ordered_names = ["train", "validation", "test"]
			samples = {k: samples[k] for k in ordered_names}
		elif ("test" in keys):
			ordered_names = ["train", "test"]
			samples = {k: samples[k] for k in ordered_names}

		splitset = Splitset.create(
			label = label
			, samples = samples
			, sizes = sizes
			, supervision = supervision
			, has_test = has_test
			, has_validation = has_validation
			, bin_count = bin_count
			, unsupervised_stratify_col = unsupervised_stratify_col
			, name = name
			, description = description
		)

		try:
			for f_id in feature_ids:
				feature = Feature.get_by_id(f_id)
				Featureset.create(splitset=splitset, feature=feature)
		except:
			splitset.delete_instance() # Orphaned.
			raise

		return splitset


	def get_features(id:int):
		splitset = Splitset.get_by_id(id)
		features = list(Feature.select().join(Featureset).where(Featureset.splitset==splitset))
		return features


class Featureset(BaseModel):
	"""Featureset is a many-to-many relationship between Splitset and Feature."""
	splitset = ForeignKeyField(Splitset, backref='featuresets')
	feature = ForeignKeyField(Feature, backref='featuresets')


class Foldset(BaseModel):
	"""
	- Contains aggregate summary statistics and evaluate metrics for all Folds.
	- Works the same for all dataset types because only the labels are used for stratification.
	"""
	fold_count = IntegerField()
	random_state = IntegerField()
	bin_count = IntegerField(null=True) # For stratifying continuous features.
	#ToDo: max_samples_per_bin = IntegerField()
	#ToDo: min_samples_per_bin = IntegerField()

	splitset = ForeignKeyField(Splitset, backref='foldsets')

	def from_splitset(
		splitset_id:int
		, fold_count:int = None
		, bin_count:int = None
	):
		splitset = Splitset.get_by_id(splitset_id)
		
		if (fold_count is None):
			fold_count = 5 # More likely than 4 to be evenly divisible.
		else:
			if (fold_count < 2):
				raise Exception(dedent(f"""
				Yikes - Cross validation requires multiple folds.
				But you provided `fold_count`: <{fold_count}>.
				"""))
			elif (fold_count == 2):
				print("\nWarning - Instead of two folds, why not just use a validation split?\n")

		# From the training indices, we'll derive indices for 'folds_train_combined' and 'fold_validation'.
		arr_train_indices = splitset.samples["train"]
		# Figure out what data, if any, is needed for stratification.
		if (splitset.supervision=="supervised"):
			# The actual values of the features don't matter, only label values needed for stratification.
			label = splitset.label
			stratify_arr = label.preprocess(labelcoder_id='skip')
			stratify_arr = stratify_arr[arr_train_indices]
			stratify_dtype = stratify_arr.dtype

		elif (splitset.supervision=="unsupervised"):
			if (splitset.unsupervised_stratify_col is not None):
				feature = splitset.get_features()[0]
				_, stratify_arr = feature.preprocess(
					supervision = 'unsupervised'
					, encoderset_id = 'skip'
				)
				# Only take the training samples.
				stratify_arr = stratify_arr[arr_train_indices]

				stratify_col = splitset.unsupervised_stratify_col
				column_names = feature.dataset.get_main_file().columns
				col_index = utils.wrangle.colIndices_from_colNames(column_names=column_names, desired_cols=[stratify_col])[0]
				
				dimensions = stratify_arr.ndim
				if (dimensions==2):
					stratify_arr = stratify_arr[:,col_index]
				elif (dimensions==3):
					# Used by sequence and tabular-windowed. Reduces to 2D w data from 1 column.
					stratify_arr = stratify_arr[:,:,col_index]
				elif (dimensions==4):
					# Used by image and sequence-windowed. Reduces to 3D w data from 1 column.
					stratify_arr = stratify_arr[:,:,:,col_index]
				elif (dimensions==5):
					# Used by image-windowed. Reduces to 4D w data from 1 column.
					stratify_arr = stratify_arr[:,:,:,:,col_index]
				stratify_dtype = stratify_arr.dtype

				dimensions = stratify_arr.ndim
				# In order to stratify, we need a single value from each sample.
				if ((stratify_arr.shape[-1] > 1) and (dimensions > 1)):
					# We want a 2D array where each 1D array reprents the desired column across all dimensions.
					# 1D already has a single value and 2D just works.
					if (dimensions==3):
						shape = stratify_arr.shape
						stratify_arr = stratify_arr.reshape(shape[0], shape[1]*shape[2])
					elif (dimensions==4):
						shape = stratify_arr.shape
						stratify_arr = stratify_arr.reshape(shape[0], shape[1]*shape[2]*shape[3])

					if (np.issubdtype(stratify_dtype, np.number) == True):
						stratify_arr = np.median(stratify_arr, axis=1)
					elif (np.issubdtype(stratify_dtype, np.number) == False):
						stratify_arr = np.array([mode(arr1D)[0][0] for arr1D in stratify_arr])
					
					# Now its 1D so reshape to 2D for the rest of the process.
					stratify_arr = stratify_arr.reshape(stratify_arr.shape[0], 1)

			elif (splitset.unsupervised_stratify_col is None):
				if (bin_count is not None):
					raise Exception("\nYikes - `bin_count` cannot be set if `unsupervised_stratify_col is None` and `label_id is None`.\n")
				stratify_arr = None#Used in if statements below.

		# If the Labels are binned *overwite* the values w bin numbers. Otherwise untouched.
		if (stratify_arr is not None):
			# Bin the floats.
			if (np.issubdtype(stratify_dtype, np.floating)):
				if (bin_count is None):
					bin_count = splitset.bin_count #Inherit. 
				stratify_arr = utils.wrangle.values_to_bins(
					array_to_bin = stratify_arr
					, bin_count = bin_count
				)
			# Allow ints to pass either binned or unbinned.
			elif (
				(np.issubdtype(stratify_dtype, np.signedinteger))
				or
				(np.issubdtype(stratify_dtype, np.unsignedinteger))
			):
				if (bin_count is not None):
					if (splitset.bin_count is None):
						print(dedent("""
							Warning - Previously you set `Splitset.bin_count is None`
							but now you are trying to set `Foldset.bin_count is not None`.
							
							This can result in incosistent stratification processes being 
							used for training samples versus validation and test samples.
						\n"""))
					stratify_arr = utils.wrangle.values_to_bins(
						array_to_bin = stratify_arr
						, bin_count = bin_count
					)
				elif (bin_count is not None):
					print("\nInfo - Attempting to use ordinal bins as no `bin_count` was provided. This may fail if there are to many unique ordinal values.\n")
			else:
				if (bin_count is not None):
					raise Exception(dedent("""
						Yikes - The column you are stratifying by is not a numeric dtype (neither `np.floating`, `np.signedinteger`, `np.unsignedinteger`).
						Therefore, you cannot provide a value for `bin_count`.
					\n"""))

		train_count = len(arr_train_indices)
		remainder = train_count % fold_count
		if (remainder != 0):
			print(
				f"Warning - The number of samples <{train_count}> in your training Split\n" \
				f"is not evenly divisible by the `fold_count` <{fold_count}> you specified.\n" \
				f"This can result in misleading performance metrics for the last Fold.\n"
			)

		new_random = False
		while new_random == False:
			random_state = randint(0, 4294967295) #2**32 - 1 inclusive
			matching_randoms = splitset.foldsets.select().where(Foldset.random_state==random_state)
			if (matching_randoms.count()==0):
				new_random = True

		# Create first because need to attach the Folds.
		foldset = Foldset.create(
			fold_count = fold_count
			, random_state = random_state
			, bin_count = bin_count
			, splitset = splitset
		)

		try:
			# Stratified vs Unstratified.
			if (stratify_arr is None):
				# https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.KFold.html
				kf = KFold(
					n_splits = fold_count
					, shuffle = True
					, random_state = random_state
				)
				splitz_gen = kf.split(arr_train_indices)
			elif (stratify_arr is not None):
				# https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedKFold.html
				skf = StratifiedKFold(
					n_splits = fold_count
					, shuffle = True
					, random_state = random_state
				)
				splitz_gen = skf.split(arr_train_indices, stratify_arr)

			i = -1
			for index_folds_train, index_fold_validation in splitz_gen:
				# ^ These are new zero-based indices that must be used to access the real data.
				i+=1
				fold_samples = {}

				index_folds_train = sorted(index_folds_train)
				index_fold_validation = sorted(index_fold_validation)

				# Python 3.7+ insertion order of dict keys assured.
				fold_samples["folds_train_combined"] = [arr_train_indices[idx] for idx in index_folds_train]
				fold_samples["fold_validation"] = [arr_train_indices[idx] for idx in index_fold_validation]

				Fold.create(
					fold_index = i
					, samples = fold_samples 
					, foldset = foldset
				)
		except:
			foldset.delete_instance() # Orphaned.
			raise
		return foldset


class Fold(BaseModel):
	"""
	- A Fold is 1 of many cross-validation sets generated as part of a Foldset.
	- The `samples` attribute contains the indices of `folds_train_combined` and `fold_validation`, 
	  where `fold_validation` is the rotating fold that gets left out.
	"""
	fold_index = IntegerField() # order within the Foldset.
	samples = JSONField()
	# contains_all_classes = BooleanField()
	
	foldset = ForeignKeyField(Foldset, backref='folds')


class LabelInterpolater(BaseModel):
	"""
	- Label cannot be of `dataset_type=='sequence'` so don't need to worry about 3D data.
	- Based on `pandas.DataFrame.interpolate
	- Only works on floats.
	- `proces_separately` is for 2D time series. Where splits are rows and processed separately.
	"""
	process_separately = BooleanField()# use False if you have few evaluate samples.
	interpolate_kwargs = JSONField()
	matching_columns = JSONField()

	label = ForeignKeyField(Label, backref='labelinterpolaters')

	def from_label(
		label_id:int
		, process_separately:bool = True
		, interpolate_kwargs:dict = None
	):
		label = Label.get_by_id(label_id)
		utils.wrangle.floats_only(label)

		if (interpolate_kwargs is None):
			interpolate_kwargs = dict(
				method = 'linear'
				, limit_direction = 'both'
				, limit_area = None
				, axis = 0
				, order = 1
			)
		elif (interpolate_kwargs is not None):
			utils.wrangle.verify_attributes(interpolate_kwargs)

		# Check that the arguments actually work.
		try:
			df = label.to_pandas()
			utils.wrangle.run_interpolate(dataframe=df, interpolate_kwargs=interpolate_kwargs)
		except:
			raise Exception("\nYikes - `pandas.DataFrame.interpolate(**interpolate_kwargs)` failed.\n")
		else:
			print("\n=> Tested interpolation of Label successfully.\n")

		lp = LabelInterpolater.create(
			process_separately = process_separately
			, interpolate_kwargs = interpolate_kwargs
			, matching_columns = label.columns
			, label = label
		)
		return lp


	def interpolate(id:int, array:object, samples:dict=None):
		lp = LabelInterpolater.get_by_id(id)
		label = lp.label
		interpolate_kwargs = lp.interpolate_kwargs

		if ((lp.process_separately==False) or (samples is None)):
			df_labels = pd.DataFrame(array, columns=label.columns)
			df_labels = utils.wrangle.run_interpolate(df_labels, interpolate_kwargs)	
		elif ((lp.process_separately==True) and (samples is not None)):
			# Augment our evaluation splits/folds with training data.
			for split, indices in samples.items():
				if ('train' in split):
					array_train = array[indices]
					df_train = pd.DataFrame(array_train).set_index([indices], drop=True)
					df_train = utils.wrangle.run_interpolate(df_train, interpolate_kwargs)
					df_labels = df_train

			# We need `df_train` from above.
			for split, indices in samples.items():
				if ('train' not in split):
					arr = array[indices]
					df = pd.DataFrame(arr).set_index([indices], drop=True)					# Does not need to be sorted for interpolate.
					df = pd.concat([df_train, df])
					df = utils.wrangle.run_interpolate(df, interpolate_kwargs)
					# Only keep the indices from the split of interest.
					df = df.loc[indices]
					df_labels = pd.concat([df_labels, df])
			# df doesn't need to be sorted if it is going back to numpy.
		else:
			raise Exception("\nYikes - Internal error. Could not perform Label interpolation given the arguments provided.\n")
		arr_labels = df_labels.to_numpy()
		return arr_labels


class Interpolaterset(BaseModel):
	interpolater_count = IntegerField()

	feature = ForeignKeyField(Feature, backref='interpolatersets')


	def from_feature(
		feature_id:int, interpolater_count:int=0
	):
		feature = Feature.get_by_id(feature_id)
		interpolaterset = Interpolaterset.create(
			interpolater_count=interpolater_count, feature=feature
		)
		return interpolaterset


	def interpolate(
		id:int
		, array:object
		, window:object=None
		, samples:dict=None
	):
		"""
		- `array` is assumed to be the output of `feature.to_numpy()`.
		- I was originally calling `feature.to_pandas` but all preprocesses take in and return arrays.
		- `samples` is used for indices only. It does not get repacked into a dict.
		- Extremely tricky to interpolate windowed splits separately.
		"""
		ip = Interpolaterset.get_by_id(id)
		fps = ip.featureinterpolaters
		if (fps.count()==0):
			raise Exception("\nYikes - This Interpolaterset has no FeatureInterpolaters yet.\n")
		dataset_type = ip.feature.dataset.dataset_type
		columns = ip.feature.columns# Used for naming not slicing cols.

		if (dataset_type=='tabular'):
			dataframe = pd.DataFrame(array, columns=columns)

			if ((window is not None) and (samples is not None)):
				for fp in fps:
					matching_cols = fp.matching_columns
					# We need to overwrite this df.
					df_fp = dataframe[matching_cols]
					windows_unshifted = window.samples_unshifted
					
					"""
					- Augment our evaluation splits/folds with training data.
					- At this point, indices are groups of rows (windows), not raw rows.
					  We need all of the rows from all of the windows. 
					"""
					for split, indices in samples.items():
						if ('train' in split):
							split_windows = [windows_unshifted[idx] for idx in indices]
							windows_flat = [item for sublist in split_windows for item in sublist]
							rows_train = list(set(windows_flat))
							df_train = df_fp.loc[rows_train].set_index([rows_train], drop=True)
							# Interpolate will write its own index so coerce it back.
							df_train = fp.interpolate(dataframe=df_train).set_index([rows_train], drop=True)
							
							for row in rows_train:
								df_fp.loc[row] = df_train.loc[row]

					# We need `df_train` from above.
					for split, indices in samples.items():
						if ('train' not in split):
							split_windows = [windows_unshifted[idx] for idx in indices]
							windows_flat = [item for sublist in split_windows for item in sublist]
							rows_split = list(set(windows_flat))
							# Train windows and split windows may overlap, so take train's duplicates.
							rows_split = [r for r in rows_split if r not in rows_train]
							df_split = df_fp.loc[rows_split].set_index([rows_split], drop=True)
							df_merge = pd.concat([df_train, df_split]).set_index([rows_train + rows_split], drop=True)
							# Interpolate will write its own index so coerce it back,
							# but have to cut out the split_rows before it can be reindexed.
							df_merge = fp.interpolate(dataframe=df_merge).set_index([rows_train + rows_split], drop=True)
							df_split = df_merge.loc[rows_split]

							for row in rows_split:
								df_fp.loc[row] = df_split.loc[row]
					"""
					At this point there may still be leading/ lagging nulls outside the splits
					that are within the reach of a shift.
					"""
					df_fp = fp.interpolate(dataframe=df_fp)

					for c in matching_cols:
						dataframe[c] = df_fp[c]

			elif ((window is None) or (samples is None)):
				# The underlying window data only needs to be processed separately is when there are split involved.
				for fp in fps:
					# Interpolate that slice. Don't need to process each column separately.
					df = dataframe[fp.matching_columns]
					# This method handles a few things before interpolation.
					df = fp.interpolate(dataframe=df, samples=samples)#handles `samples=None`
					# Overwrite the original column with the interpolated column.
					if (dataframe.index.size != df.index.size):
						raise Exception("Yikes - Internal error. Index sizes inequal.")
					for c in fp.matching_columns:
						dataframe[c] = df[c]
			array = dataframe.to_numpy()

		elif ((dataset_type=='sequence') or (dataset_type=='image')):
			if (dataset_type=='image'):
				# Sequences are nested. Multiply number of 4D by number of 3D.
				shape = array.shape
				array = array.reshape(shape[0]*shape[1], shape[2], shape[3])

			# One df per sequence array.
			dataframes = [pd.DataFrame(arr, columns=columns) for arr in array]
			# Each one needs to be interpolated separately.
			for i, dataframe in enumerate(dataframes):
				for fp in fps:
					df_cols = dataframe[fp.matching_columns]
					# Don't need to parse anything. Straight to DVD.
					df_cols = fp.interpolate(dataframe=df_cols, samples=None)
					# Overwrite columns.
					if (dataframe.index.size != df_cols.index.size):
						raise Exception("Yikes - Internal error. Index sizes inequal.")
					for c in fp.matching_columns:
						dataframe[c] = df_cols[c]
				# Update the list. Might as well array it while accessing it.
				dataframes[i] = dataframe.to_numpy()
			array = np.array(dataframes)
			if (dataset_type=='image'):
				array = array.reshape(shape[0],shape[1],shape[2],shape[3])
		return array


class FeatureInterpolater(BaseModel):
	"""
	- No need to stack 3D into 2D because each sequence has to be processed independently.
	- Due to the fact that interpolation only applies to floats and can be applied multiple columns,
	  I am not going to offer Interpolaterset for now.
	"""
	index = IntegerField()
	process_separately = BooleanField()# use False if you have few evaluate samples.
	interpolate_kwargs = JSONField()
	matching_columns = JSONField()
	leftover_columns = JSONField()
	leftover_dtypes = JSONField()
	original_filter = JSONField()

	interpolaterset = ForeignKeyField(Interpolaterset, backref='featureinterpolaters')


	def from_interpolaterset(
		interpolaterset_id:int
		, process_separately:bool = True
		, interpolate_kwargs:dict = None
		, dtypes:list = None
		, columns:list = None
		, verbose:bool = True
		, samples:dict = None #used for processing 2D separately.
	):
		"""
		- By default it takes all of the float columns, but you can include columns manually too.
		- Only `include=True` is allowed so that the dtype can be included.
		"""
		interpolaterset = Interpolaterset.get_by_id(interpolaterset_id)
		existing_preprocs = interpolaterset.featureinterpolaters
		feature = interpolaterset.feature

		if (interpolate_kwargs is None):
			interpolate_kwargs = dict(
				method = 'linear'
				, limit_direction = 'both'
				, limit_area = None
				, axis = 0
				, order = 1
			)
		elif (interpolate_kwargs is not None):
			utils.wrangle.verify_attributes(interpolate_kwargs)

		dtypes = utils.wrangle.listify(dtypes)
		columns = utils.wrangle.listify(columns)
		if (dtypes is not None):
			for typ in dtypes:
				if (not np.issubdtype(typ, np.floating)):
					raise Exception("\nYikes - All `dtypes` must match `np.issubdtype(dtype, np.floating`.\nYour dtype: <{typ}>")
		if (columns is not None):
			feature_dtypes = feature.get_dtypes()
			for c in columns:
				if (not np.issubdtype(feature_dtypes[c], np.floating)):
					raise Exception(f"\nYikes - The column <{c}> that you provided is not of dtype `np.floating`.\n")

		index, matching_columns, leftover_columns, original_filter, initial_dtypes = feature.preprocess_remaining_cols(
			existing_preprocs = existing_preprocs
			, include = True
			, columns = columns
			, dtypes = dtypes
			, verbose = verbose
		)		

		# Check that it actually works.
		fp = FeatureInterpolater.create(
			index = index
			, process_separately = process_separately
			, interpolate_kwargs = interpolate_kwargs
			, matching_columns = matching_columns
			, leftover_columns = leftover_columns
			, leftover_dtypes = initial_dtypes
			, original_filter = original_filter
			, interpolaterset = interpolaterset
		)
		try:
			test_arr = feature.to_numpy()#Don't pass matching cols.
			interpolaterset.interpolate(array=test_arr, samples=samples)
		except:
			fp.delete_instance() # Orphaned.
			raise
		return fp


	def interpolate(id:int, dataframe:object, samples:dict=None):
		"""
		- Called by the `Interpolaterset.interpolate` loop.
		- Assuming that matching cols have already been sliced from main array before this is called.
		"""
		fp = FeatureInterpolater.get_by_id(id)
		interpolate_kwargs = fp.interpolate_kwargs
		dataset_type = fp.interpolaterset.feature.dataset.dataset_type

		if (dataset_type=='tabular'):
			# Single dataframe.
			if ((fp.process_separately==False) or (samples is None)):

				df_interp = utils.wrangle.run_interpolate(dataframe, interpolate_kwargs)
			
			elif ((fp.process_separately==True) and (samples is not None)):
				df_interp = None
				for split, indices in samples.items():
					# Fetch those samples.
					df = dataframe.loc[indices]

					df = utils.wrangle.run_interpolate(df, interpolate_kwargs)
					# Stack them up.
					if (df_interp is None):
						df_interp = df
					elif (df_interp is not None):
						df_interp = pd.concat([df_interp, df])
				df_interp = df_interp.sort_index()
			else:
				raise Exception("\nYikes - Internal error. Unable to process FeatureInterpolater with arguments provided.\n")
		elif ((dataset_type=='sequence') or (dataset_type=='image')):
			df_interp = utils.wrangle.run_interpolate(dataframe, interpolate_kwargs)
		else:
			raise Exception("\nYikes - Internal error. Unable to process FeatureInterpolater with dataset_type provided.\n")
		# Back within the loop these will (a) overwrite the matching columns, and (b) ultimately get converted back to numpy.
		return df_interp



class Encoderset(BaseModel):
	"""
	- Preprocessing should not happen prior to Dataset ingestion because you need to do it after the split to avoid bias.
	  For example, encoder.fit() only on training split - then .transform() train, validation, and test. 
	- Don't restrict a preprocess to a specific Algorithm. Many algorithms are created as different hyperparameters are tried.
	  Also, Preprocess is somewhat predetermined by the dtypes present in the Label and Feature.
	- Although Encoderset seems uneccessary, you need something to sequentially group the FeatureCoders onto.
	- In future, maybe LabelCoder gets split out from Encoderset and it becomes FeatureCoderset.
	"""
	encoder_count = IntegerField()

	feature = ForeignKeyField(Feature, backref='encodersets')

	def from_feature(feature_id:int, encoder_count:int=0):
		feature = Feature.get_by_id(feature_id)
		encoderset = Encoderset.create(encoder_count=encoder_count, feature=feature)
		return encoderset


class LabelCoder(BaseModel):
	"""
	- Warning: watchout for conflict with `sklearn.preprocessing.LabelEncoder()`
	- `is_fit_train` toggles if the encoder is either `.fit(<training_split/fold>)` to 
	  avoid bias or `.fit(<entire_dataset>)`.
	- Categorical (ordinal and OHE) encoders are best applied to entire dataset in case 
	  there are classes missing in the split/folds of validation/ test data.
	- Whereas numerical encoders are best fit only to the training data.
	- Because there's only 1 encoder that runs and it uses all columns, LabelCoder 
	  is much simpler to validate and run in comparison to FeatureCoder.
	"""
	only_fit_train = BooleanField()
	is_categorical = BooleanField()
	sklearn_preprocess = PickleField()
	matching_columns = JSONField() # kinda unecessary, but maybe multi-label future.
	encoding_dimension = CharField()

	label = ForeignKeyField(Label, backref='labelcoders')


	def from_label(label_id:int, sklearn_preprocess:object):
		label = Label.get_by_id(label_id)

		sklearn_preprocess, only_fit_train, is_categorical = utils.encoding.check_sklearn_attributes(
			sklearn_preprocess, is_label=True
		)

		samples_to_encode = label.to_numpy()
		# 2. Test Fit.
		try:
			fitted_encoders, encoding_dimension = utils.encoding.fit_dynamicDimensions(
				sklearn_preprocess = sklearn_preprocess
				, samples_to_fit = samples_to_encode
			)
		except:
			print(f"\nYikes - During a test encoding, failed to `fit()` instantiated `{sklearn_preprocess}` on `label.to_numpy())`.\n")
			raise

		# 3. Test Transform/ Encode.
		try:
			"""
			- During `Job.run`, it will touch every split/fold regardless of what it was fit on
			  so just validate it on whole dataset.
			"""
			utils.encoding.transform_dynamicDimensions(
				fitted_encoders = fitted_encoders
				, encoding_dimension = encoding_dimension
				, samples_to_transform = samples_to_encode
			)
		except:
			raise Exception(dedent("""
			During testing, the encoder was successfully `fit()` on the labels,
			but, it failed to `transform()` labels of the dataset as a whole.
			"""))
		else:
			pass    
		lc = LabelCoder.create(
			only_fit_train = only_fit_train
			, sklearn_preprocess = sklearn_preprocess
			, encoding_dimension = encoding_dimension
			, matching_columns = label.columns
			, is_categorical = is_categorical
			, label = label
		)
		return lc


class FeatureCoder(BaseModel):
	"""
	- An Encoderset can have a chain of FeatureCoders.
	- Encoders are applied sequentially, meaning the columns encoded by `index=0` 
	  are not available to `index=1`.
	- Lots of validation here because real-life encoding errors are cryptic and deep for beginners.
	"""
	index = IntegerField()
	sklearn_preprocess = PickleField()
	matching_columns = JSONField()
	encoded_column_names = JSONField()
	leftover_columns = JSONField()
	leftover_dtypes = JSONField()
	original_filter = JSONField()
	encoding_dimension = CharField()
	only_fit_train = BooleanField()
	is_categorical = BooleanField()

	encoderset = ForeignKeyField(Encoderset, backref='featurecoders')


	def from_encoderset(
		encoderset_id:int
		, sklearn_preprocess:object
		, include:bool = True
		, dtypes:list = None
		, columns:list = None
		, verbose:bool = True
	):
		encoderset = Encoderset.get_by_id(encoderset_id)
		feature = encoderset.feature
		existing_featurecoders = encoderset.featurecoders

		dataset = feature.dataset
		dataset_type = dataset.dataset_type
		
		sklearn_preprocess, only_fit_train, is_categorical = utils.encoding.check_sklearn_attributes(
			sklearn_preprocess, is_label=False
		)

		index, matching_columns, leftover_columns, original_filter, initial_dtypes = feature.preprocess_remaining_cols(
			existing_preprocs = existing_featurecoders
			, include = include
			, dtypes = dtypes
			, columns = columns
			, verbose = verbose
		)

		stringified_encoder = str(sklearn_preprocess)
		if (
			(
				(stringified_encoder.startswith("LabelBinarizer"))
				or 
				(stringified_encoder.startswith("LabelCoder"))
			)
			and
			(len(matching_columns) > 1)
		):
			raise Exception(dedent("""
				Yikes - `LabelBinarizer` or `LabelCoder` cannot be run on 
				multiple columns at once.

				We have frequently observed inconsistent behavior where they 
				often ouput incompatible array shapes that cannot be scalable 
				concatenated, or they succeed in fitting, but fail at transforming.
				
				We recommend you either use these with 1 column at a 
				time or switch to another encoder.
			"""))

		# Test fitting the encoder to matching columns.
		samples_to_encode = feature.to_numpy(columns=matching_columns)
		# Handles `Dataset.Sequence` by stacking the 2D arrays into a single tall 2D array.
		f_shape = samples_to_encode.shape
		if (len(f_shape)==3):
			rows_2D = f_shape[0] * f_shape[1]
			samples_to_encode = samples_to_encode.reshape(rows_2D, f_shape[2])
		elif (len(f_shape)==4):
			rows_2D = f_shape[0] * f_shape[1] * f_shape[2]
			samples_to_encode = samples_to_encode.reshape(rows_2D, f_shape[3])

		fitted_encoders, encoding_dimension = utils.encoding.fit_dynamicDimensions(
			sklearn_preprocess = sklearn_preprocess
			, samples_to_fit = samples_to_encode
		)

		# Test transforming the whole dataset using fitted encoder on matching columns.
		try:
			utils.encoding.transform_dynamicDimensions(
				fitted_encoders = fitted_encoders
				, encoding_dimension = encoding_dimension
				, samples_to_transform = samples_to_encode
			)
		except:
			raise Exception(dedent("""
			During testing, the encoder was successfully `fit()` on the features,
			but, it failed to `transform()` features of the dataset as a whole.\n
			"""))
		else:
			pass
		
		# Record the names of OHE generated columns for feature importance.
		if (stringified_encoder.startswith("OneHotEncoder")):
			# Assumes OHE fits 2D.
			encoder = fitted_encoders[0]
			encoded_column_names = []

			for i, mc in enumerate(matching_columns):
				# Each column the encoder fits on has a different array of categories. 
				values = encoder.categories_[i]
				for v in values:
					col_name = f"{mc}={v}"
					encoded_column_names.append(col_name)
		else:
			encoded_column_names = matching_columns 

		featurecoder = FeatureCoder.create(
			index = index
			, only_fit_train = only_fit_train
			, is_categorical = is_categorical
			, sklearn_preprocess = sklearn_preprocess
			, matching_columns = matching_columns
			, encoded_column_names = encoded_column_names
			, leftover_columns = leftover_columns
			, leftover_dtypes = initial_dtypes#pruned
			, original_filter = original_filter
			, encoderset = encoderset
			, encoding_dimension = encoding_dimension
		)
		return featurecoder


class FeatureShaper(BaseModel):
	reshape_indices = PickleField()#tuple has no json equivalent
	column_position = IntegerField()#the dimension used for columns aka width. etymologically, dimensions aren't zero-based.
	feature = ForeignKeyField(Feature, backref='featureshapers')

	def from_feature(feature_id:int, reshape_indices:tuple):
		feature = Feature.get_by_id(feature_id)
		# Determines the `column_position`, which gets confirmed during preprocess().
		# Flatten the `reshape_indices` tuple.
		reshape_flat = []
		for elem in reshape_indices:
			if (type(elem)==tuple):
				for i in elem:
					reshape_flat.append(i)
			else:
				reshape_flat.append(elem)
		# Only keep the ints.
		ints = [i for i in reshape_flat if type(i)==int]
		error_msg = "\nYikes - The highest integer-based index in `reshape_indices` is assumed to represent columns (aka width). If the highest integer-based index is found in a nested tuple or not found at all, then downstream processes (e.g. feature importance permutations) won't know which position represents columns.\n"
		if (len(ints)>0):
			# Also prevents a completely empty tuple.
			max_int = max(ints)
			if (max_int==0):
				raise Exception(error_msg)
			else:
				try:
					# This will fail if the highest int was in a nested tuple.
					column_position = reshape_indices.index(max_int)
				except:
					raise Exception(error_msg)
		else:
			raise Exception(error_msg)

		featureshaper = FeatureShaper.create(
			reshape_indices = reshape_indices
			, column_position = column_position
			, feature = feature
		)
		return featureshaper


class Algorithm(BaseModel):
	"""
	- Remember, pytorch and mxnet handle optimizer/loss outside the model definition as part of the train.
	- Could do a `.py` file as an alternative to Pickle.

	- Currently waiting for coleifer to accept prospect of a DillField
	https://github.com/coleifer/peewee/issues/2385
	"""
	library = CharField()
	analysis_type = CharField()#classification_multi, classification_binary, regression, clustering.
	
	fn_build = BlobField()
	fn_lose = BlobField() # null? do unsupervised algs have loss?
	fn_optimize = BlobField()
	fn_train = BlobField()
	fn_predict = BlobField()


	def make(
		library:str
		, analysis_type:str
		, fn_build:object
		, fn_train:object
		, fn_predict:object = None
		, fn_lose:object = None
		, fn_optimize:object = None
		, description:str = None
		, name:str = None
	):
		library = library.lower()
		if ((library != 'keras') and (library != 'pytorch')):
			raise Exception("\nYikes - Right now, the only libraries we support are 'keras' and 'pytorch'\nMore to come soon!\n")

		analysis_type = analysis_type.lower()
		supported_analyses = ['classification_multi', 'classification_binary', 'regression']
		if (analysis_type not in supported_analyses):
			raise Exception(f"\nYikes - Right now, the only analytics we support are:\n{supported_analyses}\n")

		if (fn_predict is None):
			fn_predict = utils.modeling.select_fn_predict(
				library=library, analysis_type=analysis_type
			)
		if (fn_optimize is None):
			fn_optimize = utils.modeling.select_fn_optimize(library=library)
		if (fn_lose is None):
			fn_lose = utils.modeling.select_fn_lose(
				library=library, analysis_type=analysis_type
			)

		funcs = [fn_build, fn_optimize, fn_train, fn_predict, fn_lose]
		for i, f in enumerate(funcs):
			is_func = callable(f)
			if (not is_func):
				raise Exception(f"\nYikes - The following variable is not a function, it failed `callable(variable)==True`:\n\n{f}\n")

		fn_build = utils.dill.serialize(fn_build)
		fn_optimize = utils.dill.serialize(fn_optimize)
		fn_train = utils.dill.serialize(fn_train)
		fn_predict = utils.dill.serialize(fn_predict)
		fn_lose = utils.dill.serialize(fn_lose)

		algorithm = Algorithm.create(
			library = library
			, analysis_type = analysis_type
			, fn_build = fn_build
			, fn_optimize = fn_optimize
			, fn_train = fn_train
			, fn_predict = fn_predict
			, fn_lose = fn_lose
			, description = description
			, name = name
		)
		return algorithm


	def get_code(id:int):
		alg = Algorithm.get_by_id(id)
		funcs = dict(
			fn_build 	  = utils.dill.reveal_code(alg.fn_build)
			, fn_lose 	  = utils.dill.reveal_code(alg.fn_lose)
			, fn_optimize = utils.dill.reveal_code(alg.fn_optimize)
			, fn_train 	  = utils.dill.reveal_code(alg.fn_train)
			, fn_predict  = utils.dill.reveal_code(alg.fn_predict)
		)
		return funcs




class Hyperparamset(BaseModel):
	"""
	- Not glomming this together with Algorithm and Preprocess because you can keep the Algorithm the same,
	  while running many different queues of hyperparams.
	- An algorithm does not have to have a hyperparamset. It can used fixed parameters.
	- `repeat_count` is the number of times to run a model, sometimes you just get stuck at local minimas.
	- `param_count` is the number of paramets that are being hypertuned.
	- `possible_combos_count` is the number of possible combinations of parameters.

	- On setting kwargs with `**` and a dict: https://stackoverflow.com/a/29028601/5739514
	"""
	hyperparamcombo_count = IntegerField()
	#strategy = CharField() # set to all by default #all/ random. this would generate a different dict with less params to try that should be persisted for transparency.
	hyperparameters = JSONField()

	algorithm = ForeignKeyField(Algorithm, backref='hyperparamsets')


	def from_algorithm(
		algorithm_id:int
		, hyperparameters:dict
		, description:str = None
		, search_count:int = None
		, search_percent:float = None
	):
		if ((search_count is not None) and (search_percent is not None)):
			raise Exception("Yikes - Either `search_count` or `search_percent` can be provided, but not both.")

		algorithm = Algorithm.get_by_id(algorithm_id)

		# Construct the hyperparameter combinations
		params_names = list(hyperparameters.keys())
		params_lists = list(hyperparameters.values())

		# Make sure they are actually lists.
		for i, pl in enumerate(params_lists):
			params_lists[i] = utils.wrangle.listify(pl)

		# From multiple lists, come up with every unique combination.
		params_combos = list(product(*params_lists))
		hyperparamcombo_count = len(params_combos)

		params_combos_dicts = []
		# Dictionary comprehension for making a dict from two lists.
		for params in params_combos:
			params_combos_dict = {params_names[i]: params[i] for i in range(len(params_names))} 
			params_combos_dicts.append(params_combos_dict)
		
		# These are the random selection strategies.
		if (search_count is not None):
			if (search_count < 1):
				raise Exception(f"\nYikes - search_count:<{search_count}> cannot be less than 1.\n")
			elif (search_count > hyperparamcombo_count):
				print(f"\nInfo - search_count:<{search_count}> greater than the number of hyperparameter combinations:<{hyperparamcombo_count}>.\nProceeding with all combinations.\n")
			else:
				# `sample` handles replacement.
				params_combos_dicts = sample(params_combos_dicts, search_count)
				hyperparamcombo_count = len(params_combos_dicts)
		elif (search_percent is not None):
			if ((search_percent > 1.0) or (search_percent <= 0.0)):
				raise Exception(f"\nYikes - search_percent:<{search_percent}> must be between 0.0 and 1.0.\n")
			else:
				select_count = math.ceil(hyperparamcombo_count * search_percent)
				params_combos_dicts = sample(params_combos_dicts, select_count)
				hyperparamcombo_count = len(params_combos_dicts)

		# Now that we have the metadata about combinations
		hyperparamset = Hyperparamset.create(
			algorithm = algorithm
			, description = description
			, hyperparameters = hyperparameters
			, hyperparamcombo_count = hyperparamcombo_count
		)

		for i, c in enumerate(params_combos_dicts):
			Hyperparamcombo.create(
				combination_index = i
				, favorite = False
				, hyperparameters = c
				, hyperparamset = hyperparamset
			)
		return hyperparamset


class Hyperparamcombo(BaseModel):
	combination_index = IntegerField()
	favorite = BooleanField()
	hyperparameters = JSONField()

	hyperparamset = ForeignKeyField(Hyperparamset, backref='hyperparamcombos')


	def get_hyperparameters(id:int, as_pandas:bool=False):
		hyperparamcombo = Hyperparamcombo.get_by_id(id)
		hyperparameters = hyperparamcombo.hyperparameters
		
		params = []
		for k,v in hyperparameters.items():
			param = {"param":k, "value":v}
			params.append(param)
		
		if (as_pandas==True):
			df = pd.DataFrame.from_records(params, columns=['param','value'])
			return df
		elif (as_pandas==False):
			return hyperparameters


class Queue(BaseModel):
	repeat_count = IntegerField()
	run_count = IntegerField()
	hide_test = BooleanField()
	permute_count = IntegerField()
	runs_completed = IntegerField()

	algorithm = ForeignKeyField(Algorithm, backref='queues') 
	splitset = ForeignKeyField(Splitset, backref='queues')
	hyperparamset = ForeignKeyField(Hyperparamset, deferrable='INITIALLY DEFERRED', null=True, backref='queues')
	foldset = ForeignKeyField(Foldset, deferrable='INITIALLY DEFERRED', null=True, backref='queues')


	def from_algorithm(
		algorithm_id:int
		, splitset_id:int
		, repeat_count:int = 1
		, permute_count:int = 3
		, hide_test:bool = False
		, hyperparamset_id:int = None
		, foldset_id:int = None
	):
		algorithm = Algorithm.get_by_id(algorithm_id)
		library = algorithm.library
		splitset = Splitset.get_by_id(splitset_id)

		if (foldset_id is not None):
			foldset = Foldset.get_by_id(foldset_id)
		# Future: since unsupervised won't have a Label for flagging the analysis type, I am going to keep the `Algorithm.analysis_type` attribute for now.
		if (splitset.supervision == 'supervised'):
			# Validate combinations of alg.analysis_type, lbl.col_count, lbl.dtype, split/fold.bin_count
			analysis_type = algorithm.analysis_type
			label = splitset.label
			label_col_count = label.column_count
			label_dtypes = list(label.get_dtypes().values())
			

			if (label.labelcoders.count() > 0):
				labelcoder = label.labelcoders[-1]
				stringified_labelcoder = str(labelcoder.sklearn_preprocess)
			else:
				labelcoder = None
				stringified_labelcoder = None

			if (label_col_count == 1):
				label_dtype = label_dtypes[0]

				if ('classification' in analysis_type): 
					if (np.issubdtype(label_dtype, np.floating)):
						raise Exception("Yikes - Cannot have `Algorithm.analysis_type!='regression`, when Label dtype falls under `np.floating`.")

					if (labelcoder is not None):
						if (labelcoder.is_categorical == False):
							raise Exception(dedent(f"""
								Yikes - `Algorithm.analysis_type=='classification_*'`, but 
								`LabelCoder.sklearn_preprocess={stringified_labelcoder}` was not found in known 'classification' encoders:
								{utils.categorical_encoders}
							"""))

						if ('_binary' in analysis_type):
							# Prevent OHE w classification_binary
							if (stringified_labelcoder.startswith("OneHotEncoder")):
								raise Exception(dedent("""
								Yikes - `Algorithm.analysis_type=='classification_binary', but 
								`LabelCoder.sklearn_preprocess.startswith('OneHotEncoder')`.
								This would result in a multi-column output, but binary classification
								needs a single column output.
								Go back and make a LabelCoder with single column output preprocess like `Binarizer()` instead.
								"""))
						elif ('_multi' in analysis_type):
							if (library == 'pytorch'):
								# Prevent OHE w pytorch.
								if (stringified_labelcoder.startswith("OneHotEncoder")):
									raise Exception(dedent("""
									Yikes - `(analysis_type=='classification_multi') and (library == 'pytorch')`, 
									but `LabelCoder.sklearn_preprocess.startswith('OneHotEncoder')`.
									This would result in a multi-column OHE output.
									However, neither `nn.CrossEntropyLoss` nor `nn.NLLLoss` support multi-column input.
									Go back and make a LabelCoder with single column output preprocess like `OrdinalEncoder()` instead.
									"""))
								elif (not stringified_labelcoder.startswith("OrdinalEncoder")):
									print(dedent("""
										Warning - When `(analysis_type=='classification_multi') and (library == 'pytorch')`
										We recommend you use `sklearn.preprocessing.OrdinalEncoder()` as a LabelCoder.
									"""))
							else:
								if (not stringified_labelcoder.startswith("OneHotEncoder")):
									print(dedent("""
										Warning - When performing non-PyTorch, multi-label classification on a single column,
										we recommend you use `sklearn.preprocessing.OneHotEncoder()` as a LabelCoder.
									"""))
					elif (
						(labelcoder is None) and ('_multi' in analysis_type) and (library != 'pytorch')
					):
						print(dedent("""
							Warning - When performing non-PyTorch, multi-label classification on a single column 
							without using a LabelCoder, Algorithm must have user-defined `fn_lose`, 
							`fn_optimize`, and `fn_predict`. We recommend you use 
							`sklearn.preprocessing.OneHotEncoder()` as a LabelCoder instead.
						"""))

					if (splitset.bin_count is not None):
						print(dedent("""
							Warning - `'classification' in Algorithm.analysis_type`, but `Splitset.bin_count is not None`.
							`bin_count` is meant for `Algorithm.analysis_type=='regression'`.
						"""))               
					if (foldset_id is not None):
						# Not doing an `and` because foldset can't be accessed if it doesn't exist.
						if (foldset.bin_count is not None):
							print(dedent("""
								Warning - `'classification' in Algorithm.analysis_type`, but `Foldset.bin_count is not None`.
								`bin_count` is meant for `Algorithm.analysis_type=='regression'`.
							"""))
				elif (analysis_type == 'regression'):
					if (labelcoder is not None):
						if (labelcoder.is_categorical == True):
							raise Exception(dedent(f"""
								Yikes - `Algorithm.analysis_type=='regression'`, but 
								`LabelCoder.sklearn_preprocess={stringified_labelcoder}` was found in known categorical encoders:
								{utils.categorical_encoders}
							"""))

					if (
						(not np.issubdtype(label_dtype, np.floating))
						and
						(not np.issubdtype(label_dtype, np.unsignedinteger))
						and
						(not np.issubdtype(label_dtype, np.signedinteger))
					):
						raise Exception("Yikes - `Algorithm.analysis_type == 'regression'`, but label dtype was neither `np.floating`, `np.unsignedinteger`, nor `np.signedinteger`.")
					
					if (splitset.bin_count is None):
						print("Warning - `Algorithm.analysis_type == 'regression'`, but `bin_count` was not set when creating Splitset.")                   
					if (foldset_id is not None):
						if (foldset.bin_count is None):
							print("Warning - `Algorithm.analysis_type == 'regression'`, but `bin_count` was not set when creating Foldset.")
							if (splitset.bin_count is not None):
								print("Warning - `bin_count` was set for Splitset, but not for Foldset. This leads to inconsistent stratification across samples.")
						elif (foldset.bin_count is not None):
							if (splitset.bin_count is None):
								print("Warning - `bin_count` was set for Foldset, but not for Splitset. This leads to inconsistent stratification across samples.")
				
			# We already know these are OHE based on Label creation, so skip dtype, bin, and encoder checks.
			elif (label_col_count > 1):
				if (analysis_type != 'classification_multi'):
					raise Exception("Yikes - `Label.column_count > 1` but `Algorithm.analysis_type != 'classification_multi'`.")

		elif ((splitset.supervision=='unsupervised') and (algorithm.analysis_type!='regression')):
			raise Exception("\nYikes - AIQC only supports unsupervised analysis with `analysis_type=='regression'`.\n")


		if (foldset_id is not None):
			foldset =  Foldset.get_by_id(foldset_id)
			foldset_splitset = foldset.splitset
			if (foldset_splitset != splitset):
				raise Exception(f"\nYikes - The Foldset <id:{foldset_id}> and Splitset <id:{splitset_id}> you provided are not related.\n")
			folds = list(foldset.folds)
		else:
			# Just so we have an item to loop over as a null condition when creating Jobs.
			folds = [None]
			foldset = None

		if (hyperparamset_id is not None):
			hyperparamset = Hyperparamset.get_by_id(hyperparamset_id)
			combos = list(hyperparamset.hyperparamcombos)
		else:
			# Just so we have an item to loop over as a null condition when creating Jobs.
			combos = [None]
			hyperparamset = None

		# The null conditions set above (e.g. `[None]`) ensure multiplication by 1.
		run_count = len(combos) * len(folds) * repeat_count

		queue = Queue.create(
			run_count = run_count
			, repeat_count = repeat_count
			, algorithm = algorithm
			, splitset = splitset
			, permute_count = permute_count
			, foldset = foldset
			, hyperparamset = hyperparamset
			, hide_test = hide_test
			, runs_completed = 0
		)
		try:
			for c in combos:
				if (foldset is not None):
					# Jobset can probably be replaced w a query after the fact using the objects below.
					jobset = Jobset.create(
						repeat_count = repeat_count
						, queue = queue
						, hyperparamcombo = c
						, foldset = foldset
					)
				elif (foldset is None):
					jobset = None

				try:
					for f in folds:
						Job.create(
							queue = queue
							, hyperparamcombo = c
							, fold = f
							, repeat_count = repeat_count
							, jobset = jobset
						)
				except:
					if (foldset is not None):
						jobset.delete_instance() # Orphaned.
						raise
		except:
			queue.delete_instance() # Orphaned.
			raise
		return queue


	def run_jobs(id:int):
		"""
		- Preprocessed data is cached across jobs.
		- It must be read from disk each time because post-processing alters the data.
		"""
		queue = Queue.get_by_id(id)
		hide_test = queue.hide_test
		splitset = queue.splitset
		foldset = queue.foldset
		library = queue.algorithm.library

		if (foldset is None):
			cache_path = f"{app_dir}queue-{id}_cached_samples.gzip"
			jobs = list(queue.jobs)
			# Repeat count means that a single Job can have multiple Predictors.
			repeated_jobs = [] #tuple:(repeat_index, job)
			for r in range(queue.repeat_count):
				for j in jobs:
					repeated_jobs.append((r,j))

			samples = {}
			ordered_names = [] 
			
			if (hide_test == False):
				samples['test'] = splitset.samples['test']
				key_evaluation = 'test'
				ordered_names.append('test')
			elif (hide_test == True):
				key_evaluation = None

			if (splitset.has_validation):
				samples['validation'] = splitset.samples['validation']
				key_evaluation = 'validation'
				ordered_names.insert(0, 'validation')

			samples['train'] = splitset.samples['train']
			key_train = "train"
			ordered_names.insert(0, 'train')
			samples = {k: samples[k] for k in ordered_names}
			# Fetch the data once for all jobs. Encoder fits still need to be tied to job.
			job = list(queue.jobs)[0]
			samples, input_shapes = utils.wrangle.stage_data(
				splitset=splitset, job=job
				, samples=samples, library=library
				, key_train=key_train
			)
			with gzopen(cache_path,'wb') as f:
				dump(samples,f)

			try:
				for i, rj in enumerate(tqdm(
					repeated_jobs
					, desc = "🔮 Training Models 🔮"
					, ncols = 100
				)):
					# See if this job has already completed. Keeps the tqdm intact.
					matching_predictor = Predictor.select().join(Job).where(
						Predictor.repeat_index==rj[0], Job.id==rj[1].id
					)
					if (matching_predictor.count()==0):
						if (i>0):
							with gzopen(cache_path,'rb') as f:
								samples = load(f)
						Job.run(
							id=rj[1].id, repeat_index=rj[0]
							, samples=samples, input_shapes=input_shapes
							, key_train=key_train, key_evaluation=key_evaluation
						)
				remove(cache_path)
			except (KeyboardInterrupt):
				# Attempts to prevent irrelevant errors, but sometimes they still slip through.
				remove(cache_path)
				print("\nQueue was gracefully interrupted.\n")
			except:
				# Other training related errors.
				remove(cache_path)
				raise

		elif (foldset is not None):
			folds = list(foldset.folds)
			# Each fold will contain unique, reusable data.
			for e, fold in enumerate(folds):
				print(f"\nRunning Jobs for Fold {e+1} out of {foldset.fold_count}:\n", flush=True)
				cache_path = f"{app_dir}queue-{id}_fold-{e}_cached_samples.gzip"
				jobs = [j for j in queue.jobs if j.fold==fold]
				repeated_jobs = [] #tuple:(repeat_index, job, fold)
				for r in range(queue.repeat_count):
					for j in jobs:
						repeated_jobs.append((r,j))

				samples = {}
				ordered_names = []

				if (hide_test == False):
					ordered_names.append('test')
					samples['test'] = splitset.samples['test']
				if (splitset.has_validation):
					ordered_names.insert(0, 'validation')
					samples['validation'] = splitset.samples['validation']

				key_train = "folds_train_combined"
				key_evaluation = "fold_validation"
				ordered_names.insert(0, 'fold_validation')
				ordered_names.insert(0, 'folds_train_combined')

				# Still need to fetch the underlying samples for a folded job.
				samples['folds_train_combined'] = fold.samples['folds_train_combined']
				samples['fold_validation'] = fold.samples['fold_validation']
				samples = {k: samples[k] for k in ordered_names}

				# Fetch the data once for all jobs. Encoder fits still need to be tied to job.
				job = list(queue.jobs)[0]
				samples, input_shapes = utils.wrangle.stage_data(
					splitset=splitset, job=job
					, samples=samples, library=library
					, key_train=key_train
				)

				with gzopen(cache_path,'wb') as f:
					dump(samples,f)
				try:
					for i, rj in enumerate(tqdm(
						repeated_jobs
						, desc = "🔮 Training Models 🔮"
						, ncols = 100
					)):
						# See if this job has already completed. Keeps the tqdm intact.
						matching_predictor = Predictor.select().join(Job).where(
							Predictor.repeat_index==rj[0], Job.id==rj[1].id
						)

						if (matching_predictor.count()==0):
							if (i>0):
								with gzopen(cache_path,'rb') as f:
									samples = load(f)
							Job.run(
								id=rj[1].id, repeat_index=rj[0]
								, samples=samples, input_shapes=input_shapes
								, key_train=key_train, key_evaluation=key_evaluation
							)
					remove(cache_path)
				except (KeyboardInterrupt):
					# So that we don't get nasty error messages when interrupting a long running loop.
					remove(cache_path)
					print("\nQueue was gracefully interrupted.\n")
				except:
					remove(cache_path)
					raise


	def metrics_to_pandas(
		id:int
		, selected_metrics:list=None
		, sort_by:list=None
		, ascending:bool=False
	):
		queue = Queue.get_by_id(id)
		selected_metrics = utils.wrangle.listify(selected_metrics)
		sort_by = utils.wrangle.listify(sort_by)
		
		queue_predictions = Prediction.select().join(
			Predictor).join(Job).where(Job.queue==id
		).order_by(Prediction.id)
		queue_predictions = list(queue_predictions)

		if (not queue_predictions):
			raise Exception("\nSorry - None of the Jobs in this Queue have completed yet.\n")

		split_metrics = list(queue_predictions[0].metrics.values())
		metric_names = list(split_metrics[0].keys())
		if (selected_metrics is not None):
			for m in selected_metrics:
				if (m not in metric_names):
					raise Exception(dedent(f"""
					Yikes - The metric '{m}' does not exist in `Predictor.metrics`.
					Note: the metrics available depend on the `Queue.analysis_type`.
					"""))
		elif (selected_metrics is None):
			selected_metrics = metric_names

		# Unpack the split data from each Predictor and tag it with relevant Queue metadata.
		split_metrics = []
		for prediction in queue_predictions:
			predictor = prediction.predictor
			for split_name,metrics in prediction.metrics.items():

				split_metric = {}
				if (predictor.job.hyperparamcombo is not None):
					split_metric['hyperparamcombo_id'] = predictor.job.hyperparamcombo.id
				elif (predictor.job.hyperparamcombo is None):
					split_metric['hyperparamcombo_id'] = None

				if (queue.foldset is not None):
					split_metric['jobset_id'] = predictor.job.jobset.id
					split_metric['fold_index'] = predictor.job.fold.fold_index
				split_metric['job_id'] = predictor.job.id
				if (predictor.job.repeat_count > 1):
					split_metric['repeat_index'] = predictor.repeat_index

				split_metric['predictor_id'] = predictor.id
				split_metric['split'] = split_name

				for metric_name,metric_value in metrics.items():
					# Check whitelist.
					if metric_name in selected_metrics:
						split_metric[metric_name] = metric_value

				split_metrics.append(split_metric)

		column_names = list(split_metrics[0].keys())
		if (sort_by is not None):
			for name in sort_by:
				if (name not in column_names):
					raise Exception(f"\nYikes - Column '{name}' not found in metrics dataframe.\n")
			df = pd.DataFrame.from_records(split_metrics).sort_values(
				by=sort_by, ascending=ascending
			)
		elif (sort_by is None):
			df = pd.DataFrame.from_records(split_metrics).sort_values(
				by=['predictor_id'], ascending=ascending
			)
		return df


	def metrics_aggregate_to_pandas(
		id:int
		, ascending:bool=False
		, selected_metrics:list=None
		, selected_stats:list=None
		, sort_by:list=None
	):
		selected_metrics = utils.wrangle.listify(selected_metrics)
		selected_stats = utils.wrangle.listify(selected_stats)
		sort_by = utils.wrangle.listify(sort_by)

		queue_predictions = Prediction.select().join(
			Predictor).join(Job).where(Job.queue==id
		).order_by(Prediction.id)
		queue_predictions = list(queue_predictions)

		if (not queue_predictions):
			print("\n~:: Patience, young Padawan ::~\n\nThe Jobs have not completed yet, so there are no Predictors to be had.\n")
			return None

		metrics_aggregate = queue_predictions[0].metrics_aggregate
		metric_names = list(metrics_aggregate.keys())
		stat_names = list(list(metrics_aggregate.values())[0].keys())

		if (selected_metrics is not None):
			for m in selected_metrics:
				if (m not in metric_names):
					raise Exception(dedent(f"""
					Yikes - The metric '{m}' does not exist in `Predictor.metrics_aggregate`.
					Note: the metrics available depend on the `Queue.analysis_type`.
					"""))
		elif (selected_metrics is None):
			selected_metrics = metric_names

		if (selected_stats is not None):
			for s in selected_stats:
				if (s not in stat_names):
					raise Exception(f"\nYikes - The statistic '{s}' does not exist in `Predictor.metrics_aggregate`.\n")
		elif (selected_stats is None):
			selected_stats = stat_names

		predictions_stats = []
		for prediction in queue_predictions:
			predictor = prediction.predictor
			for metric, stats in prediction.metrics_aggregate.items():
				# Check whitelist.
				if (metric in selected_metrics):
					stats['metric'] = metric
					stats['predictor_id'] = prediction.id
					if (predictor.job.repeat_count > 1):
						stats['repeat_index'] = predictor.repeat_index
					if (predictor.job.fold is not None):
						stats['jobset_id'] = predictor.job.jobset.id
						stats['fold_index'] = predictor.job.fold.fold_index
					else:
						stats['job_id'] = predictor.job.id
					stats['hyperparamcombo_id'] = predictor.job.hyperparamcombo.id

					predictions_stats.append(stats)

		# Cannot edit dictionary while key-values are being accessed.
		for stat in stat_names:
			if (stat not in selected_stats):
				for s in predictions_stats:
					s.pop(stat)# Errors if not found.

		#Reverse the order of the dictionary keys.
		predictions_stats = [dict(reversed(list(d.items()))) for d in predictions_stats]
		column_names = list(predictions_stats[0].keys())

		if (sort_by is not None):
			for name in sort_by:
				if (name not in column_names):
					raise Exception(f"\nYikes - Column '{name}' not found in aggregate metrics dataframe.\n")
			df = pd.DataFrame.from_records(predictions_stats).sort_values(
				by=sort_by, ascending=ascending
			)
		elif (sort_by is None):
			df = pd.DataFrame.from_records(predictions_stats)
		return df


	def plot_performance(
		id:int, call_display:bool=True,
		max_loss:float=None, min_score:float=None, score_type:str=None, height:int=None
	):
		"""
		- `score` is the non-loss metric.
		- `call_display` True is for IDE whereas False is for the Dash UI.
		"""
		queue = Queue.get_by_id(id)
		analysis_type = queue.algorithm.analysis_type
		
		if ("classification" in analysis_type):
			if (score_type is None):
				score_type = "accuracy"
			else:
				if (score_type not in utils.meter.metrics_classify_cols):
					raise Exception(f"\nYikes - `score_type={score_type}` not found in classification metrics:\n{utils.meter.metrics_classify}\n")
		elif (analysis_type == 'regression'):
			if (score_type is None):
				score_type = "r2"
			else:
				if (score_type not in utils.meter.metrics_regress_cols):
					raise Exception(f"\nYikes - `score_type={score_type}` not found in regression metrics:\n{utils.meter.metrics_regress}\n")
		score_display = utils.meter.metrics_all[score_type]

		if (min_score is None):
			if (score_type=="r2"):
				# I've observed r2 scores below -1.7 somehow.
				min_score = float('-inf')
			else:
				min_score = 0
		elif (min_score is not None):
			if (min_score > 1.0):
				raise Exception("\nYikes - `min_score` must be <= 1\n")

		if (max_loss is None):
			max_loss = float('inf')
		elif (max_loss < 0):
			raise Exception("\nYikes - `max_loss` must be >= 0\n")

		df = queue.metrics_to_pandas()#handles empty
		qry_str = "(loss >= {}) | ({} <= {})".format(max_loss, score_type, min_score)
		failed = df.query(qry_str)
		failed_runs = failed['predictor_id'].to_list()
		failed_runs_unique = list(set(failed_runs))
		# Here the `~` inverts it to mean `.isNotIn()`
		df_passed = df[~df['predictor_id'].isin(failed_runs_unique)]
		dataframe = df_passed[['predictor_id', 'split', 'loss', score_type]]

		if (dataframe.empty):
			msg = "\nSorry - There are no models that meet the criteria specified.\n"
			if (call_display==True):
				print(msg)
			elif (call_display==False):
				raise Exception(msg)
		else:
			if (height is None):
				height=560
			fig = Plot().performance(
				dataframe=dataframe, call_display=call_display,
				score_type=score_type, score_display=score_display,
				height=height
			)
			if (call_display==False):
				return fig


class Jobset(BaseModel):
	"""
	- Used to group cross-fold Jobs.
	- Union of Hyperparamcombo, Foldset, and Queue.
	"""
	repeat_count = IntegerField()

	foldset = ForeignKeyField(Foldset, backref='jobsets')
	hyperparamcombo = ForeignKeyField(Hyperparamcombo, deferrable='INITIALLY DEFERRED', null=True, backref='jobsets')
	queue = ForeignKeyField(Queue, backref='jobsets')


class Job(BaseModel):
	"""
	- Gets its Algorithm through the Queue.
	- Saves its Model to a Predictor.
	"""
	repeat_count = IntegerField()
	#log = CharField() #catch & record stacktrace of failures and warnings?

	queue = ForeignKeyField(Queue, backref='jobs')
	hyperparamcombo = ForeignKeyField(Hyperparamcombo, deferrable='INITIALLY DEFERRED', null=True, backref='jobs')
	fold = ForeignKeyField(Fold, deferrable='INITIALLY DEFERRED', null=True, backref='jobs')
	jobset = ForeignKeyField(Jobset, deferrable='INITIALLY DEFERRED', null=True, backref='jobs')


	def predict(samples:dict, predictor_id:int, splitset_id:int=None, key_train:str=None):
		"""
		Evaluation: predictions, metrics, charts for each split/fold.
		- Metrics are run against encoded data because they won't accept string data.
		- `splitset_id` refers to a splitset provided for inference, not training.
		- `has_labels=False` is used during pure inference. Unsupervised also uses it for self-supervision.
		- `key_train` is used during permutation, but not during inference.
		"""
		predictor = Predictor.get_by_id(predictor_id)
		job = predictor.job
		hyperparamcombo = job.hyperparamcombo
		queue = job.queue
		permute_count = queue.permute_count
		algorithm = queue.algorithm
		library = algorithm.library
		analysis_type = algorithm.analysis_type
		splitset = queue.splitset
		features = splitset.get_features()
		supervision = splitset.supervision
		# Access the 2nd level of the `samples:dict` to determine if it actually has Labels in it.
		# During inference it is optional to provide labels.
		first_key = list(samples.keys())[0]
		if ('labels' in samples[first_key].keys()):
			has_labels = True
		else:
			has_labels = False

		# Prepare the logic.
		model = predictor.get_model()
		if (algorithm.library == 'pytorch'):
			# Returns tuple(model,optimizer)
			model = model[0].eval()
		fn_predict = utils.dill.deserialize(algorithm.fn_predict)
		
		if (hyperparamcombo is not None):
			hp = hyperparamcombo.hyperparameters
		elif (hyperparamcombo is None):
			hp = {} #`**` cannot be None.

		predictions = {}
		probabilities = {}
		if (has_labels==True):
			"""
			In the future, if you want to do per-class feature importance call start by calling `predictor.get_label_names()` here.
			"""
			fn_lose = utils.dill.deserialize(algorithm.fn_lose)
			loser = fn_lose(**hp)
			if (loser is None):
				raise Exception("\nYikes - `fn_lose` returned `None`.\nDid you include `return loser` at the end of the function?\n")

			metrics = {}
			plot_data = {}

		# Used by supervised, but not unsupervised.
		if ("classification" in analysis_type):
			for split, data in samples.items():
				preds, probs = fn_predict(model, data)
				predictions[split] = preds
				probabilities[split] = probs
				# Outputs numpy.

				if (has_labels == True):
					# Reassigning so that permutation can use original data.
					data_labels = data['labels']
					# https://keras.io/api/losses/probabilistic_losses/
					if (library == 'keras'):
						loss = loser(data_labels, probs)
					elif (library == 'pytorch'):
						tz_probs = FloatTensor(probs)
						if (analysis_type == 'classification_binary'):
							loss = loser(tz_probs, data_labels)
							# convert back to numpy for metrics and plots.
							data_labels = data_labels.detach().numpy()
						elif (analysis_type == 'classification_multi'):				
							flat_labels = data_labels.flatten().to(long)
							loss = loser(tz_probs, flat_labels)
							# Convert back to *OHE* numpy for metrics and plots. 
							# Reassigning so that permutes can use original data.
							data_labels = data_labels.detach().numpy()
							from sklearn.preprocessing import OneHotEncoder
							OHE = OneHotEncoder(sparse=False)
							data_labels = OHE.fit_transform(data_labels)

					metrics[split] = utils.meter.split_classification_metrics(
						data_labels, preds, probs, analysis_type
					)
					metrics[split]['loss'] = float(loss)

					plot_data[split] = utils.meter.split_classification_plots(
						data_labels, preds, probs, analysis_type
					)
				
				# During prediction Keras OHE output gets made ordinal for metrics.
				# Use the probabilities to recreate the OHE so they can be inverse_transform'ed.
				if (("multi" in analysis_type) and (library=='keras')):
					predictions[split] = []
					for p in probs:
						marker_position = np.argmax(p, axis=-1)
						empty_arr = np.zeros(len(p))
						empty_arr[marker_position] = 1
						predictions[split].append(empty_arr)
					predictions[split] = np.array(predictions[split])
				
		# Used by both supervised and unsupervised.
		elif (analysis_type=="regression"):
			# The raw output values *is* the continuous prediction itself.
			probs = None
			for split, data in samples.items():
				preds = fn_predict(model, data)
				predictions[split] = preds
				# Outputs numpy.

				#https://keras.io/api/losses/regression_losses/
				if (has_labels==True):
					# Reassigning so that permutation can use original data.
					data_labels = data['labels']
					if (library == 'keras'):
						loss = loser(data_labels, preds)
					elif (library == 'pytorch'):
						tz_preds = FloatTensor(preds)
						loss = loser(tz_preds, data_labels)
						# After obtaining loss, make labels numpy again for metrics.
						data_labels = data_labels.detach().numpy()
						# `preds` object is still numpy.

					# These take numpy inputs.
					metrics[split] = utils.meter.split_regression_metrics(data_labels, preds)
					metrics[split]['loss'] = float(loss)
				plot_data = None
		
		# Feature Importance - code is similar to loss above, but different enough not to refactor.
		# Warning - tf cant be imported on multiple Py processes. Making parallel permutation challening.
		nonImage_features = [f for f in features if (f.dataset.dataset_type!='image')]
		if (
			(permute_count>0) and (has_labels==True) and 
			(key_train is not None) and (len(nonImage_features)>0)
		):
			# Only 'train' because permutation is expensive and the learned patterns.
			loss_baseline = metrics[key_train]['loss']
			feature_importance = {}#['feature_id']['feature_column']
			if (library == 'pytorch'):
				if (analysis_type=='classification_multi'):
					flat_labels = samples[key_train]['labels'].flatten().to(long)

			for fi, feature in enumerate(features):
				if (feature.dataset.dataset_type=='image'):
					continue #preserves the index for accessing `samples[split]['features']`
				feature_id = str(feature.id)
				feature_importance[feature_id] = {}
				# `feature_data` is copied out for shuffling.
				if (len(features)==1):
					feature_data = samples[key_train]['features']
				else:
					feature_data = samples[key_train]['features'][fi]
				if (library == 'pytorch'):
					feature_data = feature_data.detach().numpy()
					# The source is still tensor, but the copy is numpy.
				
				# Figure out which dimension contains the feature column.
				ndim = feature_data.ndim
				feature_shapers = feature.featureshapers
				if (feature_shapers.count()>0):
					dimension = feature_shapers[-1].column_position
				else:
					dimension = ndim-1
				
				# Expands the categorical columns.
				encoded_column_names = feature.get_encoded_column_names()
				for ci, col in enumerate(encoded_column_names):
					# Stores the losses of each permutation before taking the mean.
					permutations_feature = []
					# Dynamically access dimension and column: 
					# https://stackoverflow.com/a/70511277/5739514
					col_index = (slice(None),) * dimension + ([ci], Ellipsis)
					# This is never reassigned aka clean for reuse.
					feature_subset = feature_data[col_index]
					subset_shape = feature_subset.shape

					for pi in range(permute_count):
						# Fetch the fresh subset and shuffle it.
						subset_shuffled = feature_subset.flatten()
						np.random.shuffle(subset_shuffled)#don't assign
						subset_shuffled = subset_shuffled.reshape(subset_shape)
						# Overwrite source feature column with shuffled data. Torch can be accessed via slicing.
						if (library == 'pytorch'): 
							subset_shuffled = FloatTensor(subset_shuffled)
						if (len(features)==1):
							samples[key_train]['features'][col_index] = subset_shuffled
						else:
							samples[key_train]['features'][fi][col_index] = subset_shuffled

						if (library == 'keras'):
							if ("classification" in analysis_type):
								preds_shuffled, probs_shuffled = fn_predict(model, samples[key_train])
								loss = loser(samples[key_train]['labels'], probs_shuffled)
							elif (analysis_type == "regression"):
								preds_shuffled = fn_predict(model, samples[key_train])
								loss = loser(samples[key_train]['labels'], preds_shuffled)
						elif (library == 'pytorch'):
							feature_data = FloatTensor(feature_data)
							if ("classification" in analysis_type):
								preds_shuffled, probs_shuffled = fn_predict(model, samples[key_train])						
								probs_shuffled = FloatTensor(probs_shuffled)
								if (analysis_type == 'classification_binary'):
									loss = loser(probs_shuffled, samples[key_train]['labels'])
								elif (analysis_type == 'classification_multi'):
									loss = loser(probs_shuffled, flat_labels)#defined above
							elif (analysis_type == 'regression'):
								preds_shuffled = fn_predict(model, samples[key_train])
								preds_shuffled = FloatTensor(preds_shuffled)
								loss = loser(preds_shuffled, samples[key_train]['labels'])
							# Convert tensors back to numpy for permuting again.
							feature_data = feature_data.detach().numpy()
						loss = float(loss)
						permutations_feature.append(loss)
					if (library == 'pytorch'): 
						feature_subset = FloatTensor(feature_subset)
					# Restore the unshuffled feature column back to source.				
					if (len(features)==1):
						samples[key_train]['features'][col_index] = feature_subset
					else:
						samples[key_train]['features'][fi][col_index]= feature_subset
					med_loss = statistics.median(permutations_feature)
					med_loss = med_loss - loss_baseline
					loss_impacts = [loss - loss_baseline for loss in permutations_feature]
					feature_importance[feature_id][col] = {}
					feature_importance[feature_id][col]['median'] = med_loss
					feature_importance[feature_id][col]['loss_impacts'] = loss_impacts
		else:
			feature_importance = None
		# plot data.
			
		"""
		Format predictions for saving:
		- Decode predictions before saving.
		- Doesn't use any Label data, but does use LabelCoder fit on the original Labels.
		"""
		if (supervision=='supervised'):
			labelcoder, fitted_encoders = Predictor.get_fitted_labelcoder(
				job=job, label=splitset.label
			)

			if ((fitted_encoders is not None) and (hasattr(fitted_encoders, 'inverse_transform'))):
				for split, data in predictions.items():
					# OHE is arriving here as ordinal, not OHE.
					data = utils.wrangle.if_1d_make_2d(data)
					predictions[split] = fitted_encoders.inverse_transform(data)
			elif((fitted_encoders is not None) and (not hasattr(fitted_encoders, 'inverse_transform'))):
				print(dedent("""
					Warning - `Predictor.predictions` are encoded. 
					They cannot be decoded because the `sklearn.preprocessing`
					encoder used does not have `inverse_transform`.
				"""))
		
		elif (supervision=='unsupervised'):
			"""
			- Decode the unsupervised predictions back into original (reshaped) Features.
			- Unsupervised prevents multiple features.
			"""
			# Remember `fitted_encoders` is a list of lists.
			encoderset, fitted_encoders = Predictor.get_fitted_encoderset(
				job = job
				, feature = features[0]
			)

			if (encoderset is not None):
				for split, data in predictions.items():
					# Make sure it is 2D.
					data_shape = data.shape
					if (len(data_shape)==3):
						data = data.reshape(data_shape[0]*data_shape[1], data_shape[2])
						originally_3d = True
						originally_4d = False
					elif (len(data_shape)==4):
						originally_4d = True
						originally_3d = False
						data = data.reshape(data_shape[0]*data_shape[1]*data_shape[2], data_shape[3])

					encoded_column_names = []
					for i, fc in enumerate(encoderset.featurecoders):
						# Figure out the order in which columns were encoded.
						# This `[0]` assumes that there is only 1 fitted encoder in the list; that 2D fit succeeded.
						fitted_encoder = fitted_encoders[i][0]
						stringified_encoder = str(fitted_encoder)
						matching_columns = fc.matching_columns
						[encoded_column_names.append(mc) for mc in matching_columns]
						# Figure out how many columns they account for in the encoded data.
						if ("OneHotEncoder" in stringified_encoder):
							num_matching_columns = 0
							# One array per orginal column.
							for c in fitted_encoder.categories_:
								num_matching_columns += len(c)
						else:
							num_matching_columns = len(matching_columns)
						data_subset = data[:,:num_matching_columns]
						
						# Decode that slice.
						data_subset = utils.wrangle.if_1d_make_2d(data_subset)
						data_subset = fitted_encoder.inverse_transform(data_subset)
						# Then concatenate w previously decoded columns.
						if (i==0):
							decoded_data = data_subset
						elif (i>0):
							decoded_data = np.concatenate((decoded_data, data_subset), axis=1)
						# Delete those columns from the original data.
						# So we can continue to access the next cols via `num_matching_columns`.
						data = np.delete(data, np.s_[0:num_matching_columns], axis=1)
					# Check for and merge any leftover columns.
					leftover_columns = encoderset.featurecoders[-1].leftover_columns
					if (len(leftover_columns)>0):
						[encoded_column_names.append(c) for c in leftover_columns]
						decoded_data = np.concatenate((decoded_data, data), axis=1)
					
					# Now we have `decoded_data` but its columns needs to be reordered to match original.
					# OHE, text extraction are condensed at this point.
					# Mapping of original col names {0:"first_column_name"}
					original_col_names = features[0].dataset.get_main_file().columns
					original_dict = {}
					for i, name in enumerate(original_col_names):
						original_dict[i] = name
					# Lookup encoded indices against this map: [4,2,0,1]
					encoded_indices = []
					for name in encoded_column_names:
						for idx, n in original_dict.items():
							if (name == n):
								encoded_indices.append(idx)
								break
					# Result is original columns indices, but still out of order.
					# Based on columns selected, the indices may not be incremental: [4,2,0,1] --> [3,2,0,1]
					ranked = sorted(encoded_indices)
					encoded_indices = [ranked.index(i) for i in encoded_indices]
					# Rearrange columns by index: [0,1,2,3]
					placeholder = np.empty_like(encoded_indices)
					placeholder[encoded_indices] = np.arange(len(encoded_indices))
					decoded_data = decoded_data[:, placeholder]

					# Restore original shape.
					# Due to inverse OHE or text extraction, the number of columns may have decreased.
					new_col_count = decoded_data.shape[1]
					if (originally_3d==True):
						decoded_data = decoded_data.reshape(data_shape[0], data_shape[1], new_col_count)
					elif (originally_4d==True):
						decoded_data = decoded_data.reshape(data_shape[0], data_shape[1], data_shape[2], new_col_count)

					predictions[split] = decoded_data

		# Flatten.
		if (supervision=='supervised'):
			for split, data in predictions.items():
				if (data.ndim > 1):
					predictions[split] = data.flatten()

		if (has_labels==True):
			for split,stats in metrics.items():
				# Alphabetize by metric name.
				metrics[split] = dict(natsorted(stats.items()))
				# Round the values for presentation.
				for name,decimal in stats.items():
					metrics[split][name] = round(decimal,3)

			# Aggregate metrics across splits (e.g. mean, pstdev).
			metric_names = list(list(metrics.values())[0].keys())
			metrics_aggregate = {}
			for metric in metric_names:
				split_values = []
				for split, split_metrics in metrics.items():
					# ran into obscure errors with `pstdev` when not `float(value)`
					value = float(split_metrics[metric])
					split_values.append(value)

				mean = statistics.mean(split_values)
				median = statistics.median(split_values)
				pstdev = statistics.pstdev(split_values)
				minimum = min(split_values)
				maximum = max(split_values)

				metrics_aggregate[metric] = {
					"mean":mean, "median":median, "pstdev":pstdev, 
					"minimum":minimum, "maximum":maximum 
				}
		elif (has_labels==False):
			metrics = None
			metrics_aggregate = None
			plot_data = None
		
		if ((probs is not None) and ("multi" not in algorithm.analysis_type)):
			# Don't flatten the softmax probabilities.
			probabilities[split] = probabilities[split].flatten()

		if (splitset_id is not None):
			splitset = Splitset.get_by_id(splitset_id)
		else:
			splitset = None

		prediction = Prediction.create(
			predictions = predictions
			, probabilities = probabilities
			, feature_importance = feature_importance
			, metrics = metrics
			, metrics_aggregate = metrics_aggregate
			, plot_data = plot_data
			, predictor = predictor
			, splitset = splitset
		)
		del samples
		return prediction


	def run(
		id:int
		, repeat_index:int
		, samples:dict
		, input_shapes:dict
		, key_train:str
		, key_evaluation:str=None
	):
		job = Job.get_by_id(id)
		queue = job.queue
		splitset = queue.splitset
		algorithm = queue.algorithm
		analysis_type = algorithm.analysis_type
		library = algorithm.library
		hyperparamcombo = job.hyperparamcombo
		time_started = timezone_now()

		if (key_evaluation is not None):
			samples_eval = samples[key_evaluation]
		elif (key_evaluation is None):
			samples_eval = None

		if (hyperparamcombo is not None):
			hp = hyperparamcombo.hyperparameters
		elif (hyperparamcombo is None):
			hp = {} #`**` cannot be None.

		fn_build = utils.dill.deserialize(algorithm.fn_build)
		# pytorch multiclass has a single ordinal label.
		if ((analysis_type == 'classification_multi') and (library == 'pytorch')):
			num_classes = len(splitset.label.unique_classes)
			model = fn_build(input_shapes['features_shape'], num_classes, **hp)
		else:
			model = fn_build(
				input_shapes['features_shape'], input_shapes['label_shape'], **hp
			)
		if (model is None):
			raise Exception("\nYikes - `fn_build` returned `None`.\nDid you include `return model` at the end of the function?\n")
		# The model and optimizer get combined during training.
		fn_lose = utils.dill.deserialize(algorithm.fn_lose)
		fn_optimize = utils.dill.deserialize(algorithm.fn_optimize)
		fn_train = utils.dill.deserialize(algorithm.fn_train)

		loser = fn_lose(**hp)
		if (loser is None):
			raise Exception("\nYikes - `fn_lose` returned `None`.\nDid you include `return loser` at the end of the function?\n")

		if (library == 'keras'):
			optimizer = fn_optimize(**hp)
		elif (library == 'pytorch'):
			optimizer = fn_optimize(model, **hp)
		if (optimizer is None):
			raise Exception("\nYikes - `fn_optimize` returned `None`.\nDid you include `return optimizer` at the end of the function?\n")

		if (library == "keras"):
			model = fn_train(
				model = model
				, loser = loser
				, optimizer = optimizer
				, samples_train = samples[key_train]
				, samples_evaluate = samples_eval
				, **hp
			)
			if (model is None):
				raise Exception("\nYikes - `fn_train` returned `model==None`.\nDid you include `return model` at the end of the function?\n")

			# Save the artifacts of the trained model.
			# If blank this value is `{}` not None.
			history = model.history.history
			"""
			- As of: Python(3.8.7), h5py(2.10.0), Keras(2.4.3), tensorflow(2.4.1)
			  model.save(buffer) working for neither `io.BytesIO()` nor `tempfile.TemporaryFile()`
			  https://github.com/keras-team/keras/issues/14411
			- So let's switch to a real file in appdirs.
			- Assuming `model.save()` will trigger OS-specific h5 drivers.
			"""
			# Write it.
			temp_file_name = f"{app_dir}temp_keras_model.h5"# flag - make unique for concurrency.
			model.save(
				temp_file_name
				, include_optimizer = True
				, save_format = 'h5'
			)
			# Fetch the bytes ('rb': read binary)
			with open(temp_file_name, 'rb') as file:
				model_blob = file.read()
			remove(temp_file_name)

		elif (library == "pytorch"):
			model, history = fn_train(
				model = model
				, loser = loser
				, optimizer = optimizer
				, samples_train = samples[key_train]
				, samples_evaluate = samples_eval
				, **hp
			)
			if (model is None):
				raise Exception("\nYikes - `fn_train` returned `model==None`.\nDid you include `return model` at the end of the function?\n")
			if (history is None):
				raise Exception("\nYikes - `fn_train` returned `history==None`.\nDid you include `return model, history` the end of the function?\n")
			# Save the artifacts of the trained model.
			# https://pytorch.org/tutorials/beginner/saving_loading_models.html#saving-loading-a-general-checkpoint-for-inference-and-or-resuming-training
			model_blob = BytesIO()
			torch_save(
				{
					'model_state_dict': model.state_dict(),
					'optimizer_state_dict': optimizer.state_dict()
				},
				model_blob
			)
			model_blob = model_blob.getvalue()
		
		# Save everything to Predictor object.
		time_succeeded = timezone_now()
		time_duration = (time_succeeded - time_started).seconds

		# There's a chance that a duplicate job was running elsewhere and finished first.
		matching_predictor = Predictor.select().join(Job).join(Queue).where(
			Queue.id==queue.id, Job.id==job.id, Predictor.repeat_index==repeat_index)
		if (matching_predictor.count() > 0):
			raise Exception(f"""
				Yikes - Duplicate run detected for Job<{job.id}> repeat_index<{repeat_index}>.
				Cancelling this instance of `run_jobs()` as there is another `run_jobs()` ongoing.
			""")

		predictor = Predictor.create(
			time_started = time_started
			, time_succeeded = time_succeeded
			, time_duration = time_duration
			, model_file = model_blob
			, input_shapes = input_shapes
			, history = history
			, job = job
			, repeat_index = repeat_index
		)

		# Use the Predictor object to make Prediction and its metrics.
		try:
			Job.predict(samples=samples, predictor_id=predictor.id, key_train=key_train)
		except:
			predictor.delete_instance()
			raise

		# Don't force delete samples because we need it for runs with duplicated data.
		del model
		# Used by UI progress bar.
		queue.runs_completed+=1
		queue.save()
		return job


class FittedEncoderset(BaseModel):
	"""
	- Job uses this to save the fitted_encoders, which are later used for inference.
	- Useful for accessing featurecoders for matching_columns, dimensions.
	- When I added support for multiple Features, updating `Job.fitted_encoders` during
	  `Job.run()` started to get unmanageable. Especially when you consider that not every
	  Feature type is guaranteed to have an Encoderset.
	"""
	fitted_encoders = PickleField()

	job = ForeignKeyField(Job, backref='fittedencodersets')
	encoderset = ForeignKeyField(Encoderset, backref='fittedencodersets')


class FittedLabelCoder(BaseModel):
	"""
	- See notes about FittedEncoderset.
	"""
	fitted_encoders = PickleField()

	job = ForeignKeyField(Job, backref='fittedlabelcoders')
	labelcoder = ForeignKeyField(LabelCoder, backref='fittedlabelcoders')


class Predictor(BaseModel):
	"""
	- Regarding metrics, the label encoder was fit on training split labels.
	"""
	repeat_index = IntegerField()
	time_started = DateTimeField()
	time_succeeded = DateTimeField()
	time_duration = IntegerField()
	model_file = BlobField()
	input_shapes = JSONField() # used by get_model()
	history = JSONField()

	job = ForeignKeyField(Job, backref='predictors')


	def get_model(id:int):
		predictor = Predictor.get_by_id(id)
		algorithm = predictor.job.queue.algorithm
		model_blob = predictor.model_file

		if (algorithm.library == "keras"):
			#https://www.tensorflow.org/guide/keras/save_and_serialize
			temp_file_name = f"{app_dir}temp_keras_model.h5"
			# Workaround: write bytes to file so keras can read from path instead of buffer.
			with open(temp_file_name, 'wb') as f:
				f.write(model_blob)
			h5 = h5_File(temp_file_name, 'r')
			model = load_model(h5, compile=True)
			remove(temp_file_name)
			# Unlike pytorch, it's doesn't look like you need to initialize the optimizer or anything.
			return model

		elif (algorithm.library == 'pytorch'):
			# https://pytorch.org/tutorials/beginner/saving_loading_models.html#load
			# Need to initialize the classes first, which requires reconstructing them.
			if (predictor.job.hyperparamcombo is not None):
				hp = predictor.job.hyperparamcombo.hyperparameters
			elif (predictor.job.hyperparamcombo is None):
				hp = {}
			features_shape = predictor.input_shapes['features_shape']
			label_shape = predictor.input_shapes['label_shape']

			fn_build = utils.dill.deserialize(algorithm.fn_build)
			fn_optimize = utils.dill.deserialize(algorithm.fn_optimize)

			if (algorithm.analysis_type == 'classification_multi'):
				num_classes = len(predictor.job.queue.splitset.label.unique_classes)
				model = fn_build(features_shape, num_classes, **hp)
			else:
				model = fn_build(features_shape, label_shape, **hp)
			
			optimizer = fn_optimize(model, **hp)

			model_bytes = BytesIO(model_blob)
			checkpoint = torch_load(model_bytes)
			# Don't assign them: `model = model.load_state_dict ...`
			model.load_state_dict(checkpoint['model_state_dict'])
			optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
			# "must call model.eval() to set dropout & batchNorm layers to evaluation mode before prediction." 
			# ^ but you don't need to pass any data into eval()
			return model, optimizer


	def export_model(id:int, file_path:str=None):
		predictor = Predictor.get_by_id(id)
		algorithm = predictor.job.queue.algorithm
		
		if (file_path is None):
			dtime = timezone_now(as_str=True)
			if (algorithm.library == "keras"):
				ext = '.h5'
			elif (algorithm.library == 'pytorch'):
				ext = '.pt'
			file_path = f"{app_dir}/models/predictor{predictor.id}_model({dtime}){ext}"
		
		file_path = path.abspath(file_path)
		folder = f"{app_dir}/models"
		makedirs(folder, exist_ok=True)

		# We already have the bytes of the file we need to write.
		model_blob = predictor.model_file
		# trying `+` because directory may not exist yet.
		with open(file_path, 'wb+') as f:
			f.write(model_blob)
			f.close()

		path.exists(file_path)
		print(dedent(
			f"\nModel exported to the following absolute path:" \
			f"\n{file_path}\n"
		))
		return file_path


	def get_hyperparameters(id:int, as_pandas:bool=False):
		"""This is a proxy for `Hyperparamcombo.get_hyperparameters`"""
		predictor = Predictor.get_by_id(id)
		hyperparamcombo = predictor.job.hyperparamcombo
		if (hyperparamcombo is not None):
			hp = hyperparamcombo.get_hyperparameters(as_pandas=as_pandas)
		else:
			hp = None
		return hp

		
	def plot_learning_curve(id:int, skip_head:bool=False, call_display:bool=True):
		predictor = Predictor.get_by_id(id)
		history = predictor.history
		if (history=={}): raise Exception("\nYikes - Predictor.history is empty.\n")
		dataframe = pd.DataFrame.from_dict(history, orient='index').transpose()
		
		# Get the`{train_*:validation_*}` matching pairs for plotting.
		keys = list(history.keys())
		# dict comprehension is for `return`ing. Don't use w `list.remove()`.
		valz = []
		trainz = []
		for k in keys:
			if isinstance(k, str):
				original_k = k
				if (k.startswith('val_')):
					k = sub("val_","validation_",k)
					valz.append(k)
				else:
					k = f"train_{k}"
					trainz.append(k)
				dataframe = dataframe.rename(columns={original_k:k})
			else:
				raise Exception("\nYikes - Predictor.history keys must be strings.\n")
		if (len(valz)!=len(trainz)):
			raise Exception("\nYikes - Number of history keys starting with 'val_' must match number of keys that don't start with 'val_'.\n")
		if (len(keys)!=len(valz)+len(trainz)):
			raise Exception("\nYikes - User defined history contains keys that are not string type.\n")
		history_pairs = {}
		for t in trainz:
			for v in valz:
				if (sub("train_","validation_",t)==v):
					history_pairs[t] = v

		figs = Plot().learning_curve(
			dataframe=dataframe, history_pairs=history_pairs,
			skip_head=skip_head, call_display=call_display
		)
		if (call_display==False): return figs


	def get_fitted_encoderset(job:object, feature:object):
		"""
		Given a Feature, you want to know if it needs to be transformed,
		and, if so, how to transform it.
		"""
		fittedencodersets = FittedEncoderset.select().join(Encoderset).where(
			FittedEncoderset.job==job, FittedEncoderset.encoderset.feature==feature
		)

		if (not fittedencodersets):
			return None, None
		else:
			encoderset = fittedencodersets[0].encoderset
			fitted_encoders = fittedencodersets[0].fitted_encoders
			return encoderset, fitted_encoders


	def get_fitted_labelcoder(job:object, label:object):
		"""
		- Given a Feature, you want to know if it needs to be transformed,
		  and, if so, how to transform it.
		"""
		fittedlabelcoders = FittedLabelCoder.select().join(LabelCoder).where(
			FittedLabelCoder.job==job, FittedLabelCoder.labelcoder.label==label
		)
		if (not fittedlabelcoders):
			return None, None
		else:
			labelcoder = fittedlabelcoders[0].labelcoder
			fitted_encoders = fittedlabelcoders[0].fitted_encoders
			return labelcoder, fitted_encoders

			
	def infer(id:int, splitset_id:int):
		"""
		- Splitset is used because Labels and Features can come from different types of Datasets.
		- Verifies both Features and Labels match original schema.
		"""
		splitset_new = Splitset.get_by_id(splitset_id)
		predictor = Predictor.get_by_id(id)
		splitset_old = predictor.job.queue.splitset

		utils.wrangle.schemaNew_matches_schemaOld(splitset_new, splitset_old)
		library = predictor.job.queue.algorithm.library

		featureset_new = splitset_new.get_features()
		featureset_old = splitset_old.get_features()
		feature_count = len(featureset_new)
		features = []# expecting different array shapes so it has to be list, not array.
		
		# Right now only 1 Feature can be windowed.
		for i, feature_new in enumerate(featureset_new):
			if (splitset_new.supervision=='supervised'):
				arr_features = feature_new.preprocess(
					# These arguments are used to get the old encoders.
					supervision = 'supervised'
					, _job=predictor.job
					, _fitted_feature=featureset_old[i]
					, _library=library
				)
			elif (splitset_new.supervision=='unsupervised'):
				arr_features, arr_labels = feature_new.preprocess(
					supervision = 'unsupervised'
					, _job=predictor.job
					, _fitted_feature=featureset_old[i]
					, _library=library
				)

			if (feature_count > 1):
				features.append(arr_features)
			else:
				# We don't need to do any row filtering so it can just be overwritten.
				features = arr_features
		"""
		- Pack into samples for the Algorithm functions.
		- This is two levels deep to mirror how the training samples were structured 
		  e.g. `samples[<trn,val,tst>]`
		"""
		samples = {'infer': {'features':features}}

		if (splitset_new.label is not None):
			label_new = splitset_new.label
			label_old = splitset_old.label
		else:
			label_new = None
			label_old = None

		if (label_new is not None):
			arr_labels = label_new.preprocess(
				_job = predictor.job
				, _fitted_label = label_old 
				, _library=library
			)
			samples['infer']['labels'] = arr_labels
		
		elif ((splitset_new.supervision=='unsupervised') and (arr_labels is not None)):
			# An example of `None` would be `window.samples_shifted is None`
			samples['infer']['labels'] = arr_labels

		prediction = Job.predict(
			samples=samples, predictor_id=id, splitset_id=splitset_id
		)
		return prediction
	

	def get_label_names(id:int):
		predictor = Predictor.get_by_id(id)
		job = predictor.job
		label = job.queue.splitset.label
		if (label is not None):
			labelcoder, fitted_encoders = Predictor.get_fitted_labelcoder(job=job, label=label)
			if (labelcoder is not None):
				if hasattr(fitted_encoders,'categories_'):
					labels = list(fitted_encoders.categories_[0])
				elif hasattr(fitted_encoders,'classes_'):
					# Used by LabelBinarizer, LabelCoder, MultiLabelBinarizer
					labels = fitted_encoders.classes_.tolist()
				else:
					labels = predictor.job.queue.splitset.label.unique_classes
			else:
				labels = predictor.job.queue.splitset.label.unique_classes
		else:
			labels = None
		return labels




class Prediction(BaseModel):
	"""
	- Many-to-Many for making predictions after of the training experiment.
	- We use the low level API to create a Dataset because there's a lot of formatting 
	  that happens during Dataset creation that we would lose out on with raw numpy/pandas 
	  input: e.g. columns may need autocreation, and who knows what connectors we'll have 
	  in the future. This forces us to  validate dtypes and columns after the fact.
	"""
	predictions = PickleField()
	feature_importance = JSONField(null=True)#['feature_id']['feature_column']{'median':float,'loss_impacts':list}
	probabilities = PickleField(null=True) # Not used for regression.
	metrics = PickleField(null=True) #Not used for inference
	metrics_aggregate = PickleField(null=True) #Not used for inference.
	plot_data = PickleField(null=True) # No regression-specific plots yet.

	predictor = ForeignKeyField(Predictor, backref='predictions')
	# dataset present if created for inference, v.s. null if from Original training set.
	splitset = ForeignKeyField(Splitset, deferrable='INITIALLY DEFERRED', null=True, backref='dataset') 

	"""
	- I moved these plots out of Predictor into Prediction because it felt weird to access the
	  Prediction via `predictions[0]`.
	- If we ever do non-deterministic algorithms then we would not have a 1-1 mapping 
	  between Predictor and Prediction.
	"""
	def plot_confusion_matrix(id:int, call_display:bool=True):
		prediction = Prediction.get_by_id(id)
		predictor = prediction.predictor
		prediction_plot_data = prediction.plot_data
		analysis_type = predictor.job.queue.algorithm.analysis_type
		labels = predictor.get_label_names()
		
		if (analysis_type == "regression"):
			raise Exception("\nYikes - <Algorithm.analysis_type> of 'regression' does not support this chart.\n")
		cm_by_split = {}

		for split, data in prediction_plot_data.items():
			cm_by_split[split] = data['confusion_matrix']

		figs = Plot().confusion_matrix(
			cm_by_split=cm_by_split, labels=labels, call_display=call_display
		)
		if (call_display==False): return figs


	def plot_precision_recall(id:int, call_display:bool=True):
		prediction = Prediction.get_by_id(id)
		predictor_plot_data = prediction.plot_data
		analysis_type = prediction.predictor.job.queue.algorithm.analysis_type
		if (analysis_type == "regression"):
			raise Exception("\nYikes - <Algorith.analysis_type> of 'regression' does not support this chart.\n")

		pr_by_split = {}
		for split, data in predictor_plot_data.items():
			pr_by_split[split] = data['precision_recall_curve']

		dfs = []
		for split, data in pr_by_split.items():
			df = pd.DataFrame()
			df['precision'] = pd.Series(pr_by_split[split]['precision'])
			df['recall'] = pd.Series(pr_by_split[split]['recall'])
			df['split'] = split
			dfs.append(df)
		dataframe = pd.concat(dfs, ignore_index=True)

		fig = Plot().precision_recall(dataframe=dataframe, call_display=call_display)
		if (call_display==False): return fig


	def plot_roc_curve(id:int, call_display:bool=True):
		prediction = Prediction.get_by_id(id)
		predictor_plot_data = prediction.plot_data
		analysis_type = prediction.predictor.job.queue.algorithm.analysis_type
		if (analysis_type == "regression"):
			raise Exception("\nYikes - <Algorith.analysis_type> of 'regression' does not support this chart.\n")

		roc_by_split = {}
		for split, data in predictor_plot_data.items():
			roc_by_split[split] = data['roc_curve']

		dfs = []
		for split, data in roc_by_split.items():
			df = pd.DataFrame()
			df['fpr'] = pd.Series(roc_by_split[split]['fpr'])
			df['tpr'] = pd.Series(roc_by_split[split]['tpr'])
			df['split'] = split
			dfs.append(df)

		dataframe = pd.concat(dfs, ignore_index=True)

		fig = Plot().roc_curve(dataframe=dataframe, call_display=call_display)
		if (call_display==False): return fig
	

	def plot_feature_importance(
		id:int, call_display:bool=True, top_n:int=10, height:int=None, margin_left:int=None
	):
		# Forcing `top_n` so that it doesn't load a billion features in the UI. 
		# `top_n` Silently returns all features if `top_n` > features.
		prediction = Prediction.get_by_id(id)
		feature_importance = prediction.feature_importance
		if (feature_importance is None):
			raise Exception("\nYikes - Feature importance was not originally calculated for this analysis.\n")
		else:
			permute_count = prediction.predictor.job.queue.permute_count
			# Remember the Featureset may contain multiple Features.
			figs = []
			for feature_id, feature_cols in feature_importance.items():
				medians = [v['median'] for k,v in feature_cols.items()]
				loss_impacts = [v['loss_impacts'] for k,v in feature_cols.items()]
				# Sort lists together using 1st list as index = stackoverflow.com/a/9764364/5739514
				medians, feature_cols, loss_impacts = (list(t) for t in zip(*sorted(
					zip(medians, feature_cols, loss_impacts),
					reverse=True				
				)))
				
				if (top_n is not None):
					if (top_n <= 0):
						raise Exception("\nYikes - `top_n` must be greater than or equal to 0.\n")
					# Silently returns all rows if `top_n` > rows.
					feature_cols, loss_impacts = feature_cols[:top_n], loss_impacts[:top_n]
				if (height is None):
					height = len(feature_cols)*25+300
				if (margin_left is None):
					longest_col = len(max(feature_cols))
					margin_left = longest_col*10
					if (margin_left<100): margin_left=100
				# zip them after top_n applied.
				feature_impacts = dict(zip(feature_cols, loss_impacts))

				fig = Plot().feature_importance(
					feature_impacts = feature_impacts
					, feature_id = feature_id
					, permute_count = permute_count
					, height = height
					, margin_left = margin_left
					, top_n = top_n
					, call_display = call_display
				)
				if (call_display==False): figs.append(fig)
			if (call_display==False): return figs
