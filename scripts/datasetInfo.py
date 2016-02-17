#!/usr/bin/env python
#-#  Copyright 2009-2016 Karlsruhe Institute of Technology
#-#
#-#  Licensed under the Apache License, Version 2.0 (the "License");
#-#  you may not use this file except in compliance with the License.
#-#  You may obtain a copy of the License at
#-#
#-#      http://www.apache.org/licenses/LICENSE-2.0
#-#
#-#  Unless required by applicable law or agreed to in writing, software
#-#  distributed under the License is distributed on an "AS IS" BASIS,
#-#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#-#  See the License for the specific language governing permissions and
#-#  limitations under the License.

import os, sys, optparse
from gcSupport import getConfig, parseOptions, utils
from grid_control.datasets import DataProvider, DatasetError
from grid_control.utils import thread_tools
from python_compat import imap, itemgetter, izip, lmap, lzip, set, sort_inplace, sorted

usage = '%s [OPTIONS] <DBS dataset path> | <dataset cache file>' % sys.argv[0]
parser = optparse.OptionParser(usage=usage)
parser.add_option('-l', '--list-datasets', dest='listdatasets', default=False, action='store_true',
	help='Show list of all datasets in query / file')
parser.add_option('-f', '--list-files',    dest='listfiles',    default=False, action='store_true',
	help='Show list of all files grouped according to blocks')
parser.add_option('-s', '--list-storage',  dest='liststorage',  default=False, action='store_true',
	help='Show list of locations where data is stored')
parser.add_option('-b', '--list-blocks',   dest='listblocks',   default=False, action='store_true',
	help='Show list of blocks of the dataset(s)')
parser.add_option('-c', '--config-entry',  dest='configentry',  default=False, action='store_true',
	help='Gives config file entries to run over given dataset(s)')
parser.add_option('-i', '--info',          dest='info',         default=False, action='store_true',
	help='Gives machine readable info of given dataset(s)')
parser.add_option('-n', '--config-nick',   dest='confignick',   default=False, action='store_true',
	help='Use dataset path to derive nickname in case it it undefined')
parser.add_option('-m', '--metadata',      dest='metadata',     default=False, action='store_true',
	help='Get metadata infomation of dataset files')
parser.add_option('-M', '--block-metadata', dest='blockmetadata', default=False, action='store_true',
	help='Get common metadata infomation of dataset blocks')
parser.add_option('-L', '--location',      dest='locationfmt',  default='hostname',
	help='Format of location information')
parser.add_option('-p', '--provider',      dest='provider',     default='dbs',
	help='Default dataset provider')
parser.add_option('', '--sort',            dest='sort',         default=False, action='store_true',
	help='Sort dataset blocks and files')
parser.add_option('', '--settings',        dest='settings',     default=None,
	help='Specify config file as source of detailed dataset settings')
parser.add_option('-S', '--save',          dest='save',
	help='Saves dataset information to specified file')
(opts, args) = parseOptions(parser)

# we need exactly one positional argument (dataset path)
if len(args) != 1:
	utils.exitWithUsage(usage)

# Disable threaded queries
def noThread(desc, fun, *args, **kargs):
	fun(*args, **kargs)
	return type('DummyThread', (), {'join': lambda self: None})()
thread_tools.start_thread = noThread

def main():
	dataset = args[0].strip()
	cfgSettings = {'dbs blacklist T1 *': 'False', 'remove empty blocks *': 'False',
		'remove empty files *': 'False', 'location format *': opts.locationfmt,
		'nickname check collision *': 'False'}
	if opts.metadata or opts.blockmetadata:
		cfgSettings['lumi filter *'] = '-'
		cfgSettings['keep lumi metadata *'] = 'True'

	config = getConfig(configFile = opts.settings, configDict = {'dataset': cfgSettings})

	if os.path.exists(dataset):
		opts.provider = 'ListProvider'
	provider = DataProvider.createInstance(opts.provider, config, dataset)
	blocks = provider.getBlocks()
	if len(blocks) == 0:
		raise DatasetError('No blocks!')

	datasets = set(imap(itemgetter(DataProvider.Dataset), blocks))
	if len(datasets) > 1 or opts.info:
		headerbase = [(DataProvider.Dataset, 'Dataset')]
	else:
		print('Dataset: %s' % blocks[0][DataProvider.Dataset])
		headerbase = []

	if opts.configentry:
		print('')
		print('dataset =')
		infos = {}
		order = []
		maxnick = 5
		for block in blocks:
			dsName = block[DataProvider.Dataset]
			if not infos.get(dsName, None):
				order.append(dsName)
				infos[dsName] = dict([(DataProvider.Dataset, dsName)])
				if DataProvider.Nickname not in block and opts.confignick:
					try:
						if '/' in dsName: 
							block[DataProvider.Nickname] = dsName.lstrip('/').split('/')[1]
						else:
							block[DataProvider.Nickname] = dsName
					except Exception:
						pass
				if DataProvider.Nickname in block:
					nick = block[DataProvider.Nickname]
					infos[dsName][DataProvider.Nickname] = nick
					maxnick = max(maxnick, len(nick))
				if len(block[DataProvider.FileList]):
					infos[dsName][DataProvider.URL] = block[DataProvider.FileList][0][DataProvider.URL]
		for dsID, dsName in enumerate(order):
			info = infos[dsName]
			providerName = sorted(provider.getClassNames(), key = len)[0]
			nickname = info.get(DataProvider.Nickname, 'nick%d' % dsID).rjust(maxnick)
			filterExpr = utils.QM(providerName == 'list', ' %% %s' % info[DataProvider.Dataset], '')
			print('\t%s : %s : %s%s' % (nickname, providerName, provider._datasetExpr, filterExpr))


	if opts.listdatasets:
		# Add some enums for consistent access to info dicts
		DataProvider.NFiles = -1
		DataProvider.NBlocks = -2

		print('')
		infos = {}
		order = []
		infosum = {DataProvider.Dataset : 'Sum'}
		for block in blocks:
			dsName = block.get(DataProvider.Dataset, '')
			if not infos.get(dsName, None):
				order.append(dsName)
				infos[dsName] = {DataProvider.Dataset: block[DataProvider.Dataset]}
			def updateInfos(target):
				target[DataProvider.NBlocks]  = target.get(DataProvider.NBlocks, 0) + 1
				target[DataProvider.NFiles]   = target.get(DataProvider.NFiles, 0) + len(block[DataProvider.FileList])
				target[DataProvider.NEntries] = target.get(DataProvider.NEntries, 0) + block[DataProvider.NEntries]
			updateInfos(infos[dsName])
			updateInfos(infosum)
		head = [(DataProvider.Dataset, 'Dataset'), (DataProvider.NEntries, '#Events'),
			(DataProvider.NBlocks, '#Blocks'), (DataProvider.NFiles, '#Files')]
		utils.printTabular(head, lmap(lambda x: infos[x], order) + ['=', infosum])

	if opts.listblocks:
		print('')
		utils.printTabular(headerbase + [(DataProvider.BlockName, 'Block'), (DataProvider.NEntries, 'Events')], blocks)

	if opts.listfiles:
		print('')
		for block in blocks:
			if len(datasets) > 1:
				print('Dataset: %s' % block[DataProvider.Dataset])
			print('Blockname: %s' % block[DataProvider.BlockName])
			utils.printTabular([(DataProvider.URL, 'Filename'), (DataProvider.NEntries, 'Events')], block[DataProvider.FileList])
			print('')

	def printMetadata(src, maxlen):
		for (mk, mv) in src:
			if len(str(mv)) > 200:
				mv = '<metadata entry size: %s> %s...' % (len(str(mv)), repr(mv)[:200])
			print('\t%s: %s' % (mk.rjust(maxlen), mv))
		if src:
			print('')

	if opts.metadata and not opts.save:
		print('')
		for block in blocks:
			if len(datasets) > 1:
				print('Dataset: %s' % block[DataProvider.Dataset])
			print('Blockname: %s' % block[DataProvider.BlockName])
			mk_len = max(imap(len, block.get(DataProvider.Metadata, [''])))
			for f in block[DataProvider.FileList]:
				print('%s [%d events]' % (f[DataProvider.URL], f[DataProvider.NEntries]))
				printMetadata(lzip(block.get(DataProvider.Metadata, []), f.get(DataProvider.Metadata, [])), mk_len)
			print('')

	if opts.blockmetadata and not opts.save:
		for block in blocks:
			if len(datasets) > 1:
				print('Dataset: %s' % block[DataProvider.Dataset])
			print('Blockname: %s' % block[DataProvider.BlockName])
			mkdict = lambda x: dict(izip(block[DataProvider.Metadata], x[DataProvider.Metadata]))
			metadata = utils.QM(block[DataProvider.FileList], mkdict(block[DataProvider.FileList][0]), {})
			for fileInfo in block[DataProvider.FileList]:
				utils.intersectDict(metadata, mkdict(fileInfo))
			printMetadata(metadata.items(), max(imap(len, metadata.keys())))

	if opts.liststorage:
		print('')
		infos = {}
		print('Storage elements:')
		for block in blocks:
			dsName = block[DataProvider.Dataset]
			if len(headerbase) > 0:
				print('Dataset: %s' % dsName)
			if block.get(DataProvider.BlockName, None):
				print('Blockname: %s' % block[DataProvider.BlockName])
			if block[DataProvider.Locations] is None:
				print('\tNo location contraint specified')
			elif block[DataProvider.Locations] == []:
				print('\tNot located at anywhere')
			else:
				for se in block[DataProvider.Locations]:
					print('\t%s' % se)
			print('')

	if opts.info:
		evSum = 0
		for block in blocks:
			blockId = '%s %s' % (block.get(DataProvider.Dataset, '-'), block.get(DataProvider.BlockName, '-'))
			blockStorage = '-'
			if block.get(DataProvider.Locations, None):
				blockStorage = str.join(',', block.get(DataProvider.Locations, '-'))
			evSum += block.get(DataProvider.NEntries, 0)
			print('%s %s %d %d' % (blockId, blockStorage, block.get(DataProvider.NEntries, 0), evSum))

	if opts.save:
		print('')
		blocks = provider.getBlocks()
		if opts.sort:
			sort_inplace(blocks, key = itemgetter(DataProvider.Dataset, DataProvider.BlockName))
			for b in blocks:
				sort_inplace(b[DataProvider.FileList], key = itemgetter(DataProvider.URL))
		DataProvider.saveToFile(opts.save, blocks)
		print('Dataset information saved to ./%s' % opts.save)

sys.exit(main())
