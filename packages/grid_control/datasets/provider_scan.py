# | Copyright 2010-2016 Karlsruhe Institute of Technology
# |
# | Licensed under the Apache License, Version 2.0 (the "License");
# | you may not use this file except in compliance with the License.
# | You may obtain a copy of the License at
# |
# |     http://www.apache.org/licenses/LICENSE-2.0
# |
# | Unless required by applicable law or agreed to in writing, software
# | distributed under the License is distributed on an "AS IS" BASIS,
# | WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# | See the License for the specific language governing permissions and
# | limitations under the License.

import os, sys
from grid_control.config import TriggerResync, add_config_suffix, create_config
from grid_control.datasets.provider_base import DataProvider
from grid_control.datasets.scanner_base import InfoScanner
from grid_control.utils import filter_dict, get_user_bool, intersect_first_dict, replace_with_dict, split_opt  # pylint:disable=line-too-long
from grid_control.utils.data_structures import UniqueList
from hpfwk import Plugin, PluginError
from python_compat import ifilter, imap, itemgetter, lchain, lmap, lsmap, md5_hex, sorted


class ScanProviderBase(DataProvider):
	def __init__(self, config, datasource_name, dataset_expr,
			dataset_nick, dataset_proc, scanner_list_default):
		DataProvider.__init__(self, config, datasource_name, dataset_expr, dataset_nick, dataset_proc)
		# Configure scanners
		scanner_config = config.change_view(default_on_change=TriggerResync(['datasets', 'parameters']))
		self._interactive_assignment = config.is_interactive('dataset name assignment', True)

		def create_scanner(scanner_name):
			return InfoScanner.create_instance(scanner_name, scanner_config, datasource_name)
		scanner_list = config.get_list('scanner', scanner_list_default) + ['NullScanner']
		self._scanner_list = lmap(create_scanner, scanner_list)

		# Configure dataset / block naming and selection
		def _setup(prefix):
			selected_hash_list = config.get_list(add_config_suffix(prefix, 'key select'), [])
			name = config.get(add_config_suffix(prefix, 'name pattern'), '')
			return (selected_hash_list, name)
		(self._selected_hash_list_dataset, self._dataset_pattern) = _setup('dataset')
		(self._selected_hash_list_block, self._block_pattern) = _setup('block')

		# Configure hash input for separation of files into datasets / blocks
		def _get_active_hash_input(prefix, guard_entry_idx):
			hash_input_list_user = config.get_list(add_config_suffix(prefix, 'hash keys'), [])
			hash_input_list_guard = config.get_list(add_config_suffix(prefix, 'guard override'),
				lchain(imap(lambda scanner: scanner.get_guard_keysets()[guard_entry_idx], self._scanner_list)))
			return hash_input_list_user + hash_input_list_guard
		self._hash_input_set_dataset = _get_active_hash_input('dataset', 0)
		self._hash_input_set_block = _get_active_hash_input('block', 1)

	def _assign_dataset_block(self, map_key2fm_list, map_key2metadata_dict, file_metadata_iter):
		# Split files into blocks/datasets via key functions and determine metadata intersection
		for (url, metadata_dict, entries, location_list, obj_dict) in file_metadata_iter:
			# Dataset hash always includes dataset expr and nickname override
			hash_dataset = self._get_hash(self._hash_input_set_dataset, metadata_dict,
				md5_hex(repr(self._dataset_expr)) + md5_hex(repr(self._dataset_nick_override)))
			# Block hash always includes the dataset hash and location list
			hash_block = self._get_hash(self._hash_input_set_block, metadata_dict,
				hash_dataset + md5_hex(repr(location_list)))

			if not self._selected_hash_list_dataset or (hash_dataset in self._selected_hash_list_dataset):
				if not self._selected_hash_list_block or (hash_block in self._selected_hash_list_block):
					metadata_dict.update({'DS_KEY': hash_dataset, 'BLOCK_KEY': hash_block})
					self._assign_dataset_block_selected(map_key2fm_list, map_key2metadata_dict,
						(url, metadata_dict, entries, location_list, obj_dict),
						hash_dataset, hash_block, metadata_dict)

	def _assign_dataset_block_selected(self, map_key2fm_list, map_key2metadata_dict,
			file_metadata, hash_dataset, hash_block, metadata_dict):
		key_dataset = (hash_dataset,)  # The used key conventions are very important!
		key_block = (hash_dataset, hash_block)
		fm_list = map_key2fm_list.setdefault(key_block, [])
		fm_list.append(file_metadata)
		# prune metadata dict down to infos common for all hashes
		metadata_dict_dataset = map_key2metadata_dict.setdefault(key_dataset, dict(metadata_dict))
		metadata_dict_block = map_key2metadata_dict.setdefault(key_block, dict(metadata_dict))
		intersect_first_dict(metadata_dict_dataset, metadata_dict)
		intersect_first_dict(metadata_dict_block, metadata_dict)

	def _build_blocks(self, map_key2fm_list, map_key2name, map_key2metadata_dict):
		# Return named dataset
		for key in sorted(map_key2fm_list):
			result = {
				DataProvider.Dataset: map_key2name[key[:1]],
				DataProvider.BlockName: map_key2name[key[:2]],
			}
			fm_list = map_key2fm_list[key]

			# Determine location_list
			location_list = None
			for file_location_list in ifilter(lambda s: s is not None, imap(itemgetter(3), fm_list)):
				location_list = location_list or []
				location_list.extend(file_location_list)
			if location_list is not None:
				result[DataProvider.Locations] = list(UniqueList(location_list))

			# use first file [0] to get the initial metadata_dict [1]
			metadata_name_list = list(fm_list[0][1].keys())
			result[DataProvider.Metadata] = metadata_name_list

			# translate file metadata into data provider file info entries
			def _translate_fm2fi(url, metadata_dict, entries, location_list, obj_dict):
				if entries is None:
					entries = -1
				return {DataProvider.URL: url, DataProvider.NEntries: entries,
					DataProvider.Metadata: lmap(metadata_dict.get, metadata_name_list)}
			result[DataProvider.FileList] = lsmap(_translate_fm2fi, fm_list)
			yield result

	def _check_map_name2key(self, map_key2name, map_key2metadata_dict):
		# Find name <-> key collisions
		map_type2name2key_list = {}
		for (key, name) in map_key2name.items():
			if len(key) == 1:
				key_type = 'dataset'
			else:
				key_type = 'block'
			map_type2name2key_list.setdefault(key_type, {}).setdefault(name, []).append(key)
		collision = False
		map_key_type2vn_list = {
			'dataset': self._hash_input_set_dataset,
			'block': self._hash_input_set_dataset + self._hash_input_set_block
		}
		for (key_type, vn_list) in map_key_type2vn_list.items():
			for (name, key_list) in map_type2name2key_list.get(key_type, {}).items():
				if len(key_list) > 1:
					self._log.warn('Multiple %s keys are mapped to the name %s!', key_type, repr(name))
					for idx, key in enumerate(sorted(key_list)):
						self._log.warn('\tCandidate #%d with key %r:', idx + 1, str.join('#', key))
						metadata_dict = map_key2metadata_dict[key]
						for (vn, value) in filter_dict(metadata_dict, key_filter=vn_list.__contains__).items():
							self._log.warn('\t\t%s = %s', vn, value)
					collision = True
		if self._interactive_assignment and collision:
			if not get_user_bool('Do you want to continue?', False):
				sys.exit(os.EX_OK)

	def _get_block_name(self, metadata_dict, hash_block):
		return replace_with_dict(self._block_pattern or hash_block[:8], metadata_dict)

	def _get_dataset_name(self, metadata_dict, hash_dataset):
		dataset_pattern_default = '/PRIVATE/Dataset_%s' % hash_dataset
		if 'SE_OUTPUT_BASE' in metadata_dict:
			dataset_pattern_default = '/PRIVATE/@SE_OUTPUT_BASE@'
		return replace_with_dict(self._dataset_pattern or dataset_pattern_default, metadata_dict)

	def _get_hash(self, keys, metadata_dict, hash_seed):
		return md5_hex(repr(hash_seed) + repr(lmap(metadata_dict.get, keys)))

	def _iter_blocks_raw(self):
		# Handling dataset and block information separately leads to nasty, nested code
		(map_key2fm_list, map_key2metadata_dict) = ({}, {})
		self._assign_dataset_block(map_key2fm_list, map_key2metadata_dict,
			ifilter(itemgetter(0), self._iter_file_infos()))
		# Generate names for blocks/datasets using common metadata - creating map id -> name
		map_key2name = {}
		for (key, metadata_dict) in map_key2metadata_dict.items():
			if len(key) == 1:
				map_key2name[key] = self._get_dataset_name(metadata_dict, hash_dataset=key[0])
			else:
				map_key2name[key] = self._get_block_name(metadata_dict, hash_block=key[1])
		# Check for bijective mapping id <-> name:
		self._check_map_name2key(map_key2name, map_key2metadata_dict)
		# Yield finished dataset blocks
		for block in self._build_blocks(map_key2fm_list, map_key2name, map_key2metadata_dict):
			yield block

	def _iter_file_infos(self):
		def _recurse(level, scanner_iter_list, args):
			if scanner_iter_list:
				for data in _recurse(level - 1, scanner_iter_list[:-1], args):
					for (path, metadata, entries, location_list, obj_dict) in scanner_iter_list[-1](level, *data):
						yield (path, dict(metadata), entries, location_list, obj_dict)
						self._raise_on_abort()
			else:
				yield args
		scanner_iter_list_start = lmap(lambda x: x.iter_datasource_items, self._scanner_list)
		return _recurse(len(self._scanner_list), scanner_iter_list_start, (None, {}, None, None, {}))


class GCProvider(ScanProviderBase):
	# Get dataset information just from grid-control instance
	# required format: <path to config file / workdir> [%<job selector]
	alias_list = ['gc']

	def __init__(self, config, datasource_name, dataset_expr, dataset_nick=None, dataset_proc=None):
		ds_config = config.change_view(view_class='TaggedConfigView', addNames=[md5_hex(dataset_expr)])
		if os.path.isdir(dataset_expr):
			scanner_list = ['OutputDirsFromWork']
			ds_config.set('source directory', dataset_expr)
			dataset_expr = os.path.join(dataset_expr, 'work.conf')
		else:
			scanner_list = ['OutputDirsFromConfig', 'MetadataFromTask']
			dataset_expr, selector = split_opt(dataset_expr, '%')
			ds_config.set('source config', dataset_expr)
			ds_config.set('source job selector', selector)
		ext_config = create_config(dataset_expr)
		ext_task_name = ext_config.change_view(setSections=['global']).get(['module', 'task'])
		if 'ParaMod' in ext_task_name:  # handle old config files
			ext_task_name = ext_config.change_view(setSections=['ParaMod']).get('module')
		ext_task_cls = Plugin.get_class(ext_task_name)
		for ext_task_cls in Plugin.get_class(ext_task_name).iter_class_bases():
			try:
				scan_holder = GCProviderSetup.get_class('GCProviderSetup_' + ext_task_cls.__name__)
			except PluginError:
				continue
			scanner_list += scan_holder.scanner_list
			break
		ScanProviderBase.__init__(self, ds_config, datasource_name, dataset_expr,
			dataset_nick, dataset_proc, scanner_list)


class ScanProvider(ScanProviderBase):
	# Get dataset information from storage url
	# required format: <storage url>
	alias_list = ['scan']

	def __init__(self, config, datasource_name, dataset_expr, dataset_nick=None, dataset_proc=None):
		ds_config = config.change_view(view_class='TaggedConfigView', addNames=[md5_hex(dataset_expr)])
		basename = os.path.basename(dataset_expr)
		scanner_first = 'FilesFromLS'
		if '*' in basename:
			ds_config.set('source directory', dataset_expr.replace(basename, ''))
			ds_config.set('filename filter', basename)
		elif not dataset_expr.endswith('.dbs'):
			ds_config.set('source directory', dataset_expr)
		else:
			ds_config.set('source dataset path', dataset_expr)
			ds_config.set('filename filter', '')
			scanner_first = 'FilesFromDataProvider'
		scanner_list_default = [scanner_first, 'MatchOnFilename', 'MatchDelimeter',
			'DetermineEvents', 'AddFilePrefix']
		ScanProviderBase.__init__(self, ds_config, datasource_name, dataset_expr,
			dataset_nick, dataset_proc, scanner_list_default)


class GCProviderSetup(Plugin):
	# This class is used to disentangle the TaskModule and GCProvider class
	#  - without any direct dependencies / imports
	alias_list = ['GCProviderSetup_TaskModule']
	scanner_list = ['JobInfoFromOutputDir', 'FilesFromJobInfo',
		'MatchOnFilename', 'MatchDelimeter', 'DetermineEvents', 'AddFilePrefix']
