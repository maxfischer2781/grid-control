import os, tarfile, time, copy, cStringIO
from grid_control import AbstractObject, RuntimeError, utils, ConfigError
from provider_base import DataProvider

class DataSplitter(AbstractObject):
	splitInfos = ('Dataset', 'SEList', 'NEvents', 'Skipped', 'FileList', 'Nickname', 'DatasetID', 'CommonPrefix')
	for id, splitInfo in enumerate(splitInfos):
		locals()[splitInfo] = id

	def __init__(self, parameters = {}):
		self._jobFiles = None
		self._jobCache = None
		self._jobCacheNum = None
		for var, value in parameters.iteritems():
			setattr(self, var, value)


	def splitDatasetInternal(self, blocks, firstEvent = 0):
		raise AbstractError


	def splitDataset(self, blocks):
		log = utils.ActivityLog('Splitting dataset into jobs')
		self._jobFiles = self.splitDatasetInternal(blocks)


	def getSplitInfo(self, jobNum):
		if self._jobCacheNum != jobNum:
			if jobNum >= self.getNumberOfJobs():
				raise ConfigError("Job %d out of range for available dataset"  % jobNum)	
			self._jobCacheNum = jobNum
			del self._jobCache
			self._jobCache = self._jobFiles[jobNum]
		return self._jobCache


	def getNumberOfJobs(self):
		return len(self._jobFiles)


	def printInfoForJob(job):
		print "Dataset:", job[DataSplitter.Dataset],
		if job.get(DataSplitter.Nickname, '') != '':
			print "\tNick:", job.get(DataSplitter.Nickname, ''),
		print "\tID:", job.get(DataSplitter.DatasetID, 0)
		print "Events :", job[DataSplitter.NEvents]
		print "Skip   :", job[DataSplitter.Skipped]
		seArray = map(lambda x: str.join(', ', x), utils.lenSplit(job[DataSplitter.SEList], 70))
		print "SEList :", str.join('\n         ', seArray)
		print "Files  :",
		if utils.verbosity() > 2:
			print str.join("\n         ", job[DataSplitter.FileList])
		else:
			print "%d files selected" % len(job[DataSplitter.FileList])
	printInfoForJob = staticmethod(printInfoForJob)


	def printAllJobInfo(self):
		jobNum = 0
		while jobNum < self.getNumberOfJobs():
			entry = self.getSplitInfo(jobNum)
			print "Job number: ", jobNum
			DataSplitter.printInfoForJob(entry)
			jobNum += 1
			print "------------"			


	def resyncMapping(self, path, oldBlocks, newBlocks):
		def addDict(x, y):
			if x:
				return x.update(y)
			else:
				return y

		log = utils.ActivityLog('Resynchronization of dataset blocks')
		oldFileInfoMap = reduce(addDict, map(lambda x: dict(map(lambda x: (x[DataProvider.lfn], x), x[DataProvider.FileList])), oldBlocks))
		newFileInfoMap = reduce(addDict, map(lambda x: dict(map(lambda x: (x[DataProvider.lfn], x), x[DataProvider.FileList])), newBlocks))
		(blocksAdded, blocksMissing, blocksChanged) = DataProvider.resyncSources(oldBlocks, newBlocks)
		del log

		def removeFilesFromMapping(oldMap, fileList, oldBlocks):
			for file in fileList:
				try:
					idx = oldMap[DataSplitter.FileList].index(file[DataProvider.lfn])
				except:
					break

				if idx == 0:
					oldMap[DataSplitter.NEvents] += oldMap[DataSplitter.Skipped]
					oldMap[DataSplitter.Skipped] = 0
				elif idx == len(oldMap[DataSplitter.FileList]) - 1:
					events = sum(map(lambda x: oldFileInfoMap[file][DataProvider.NEvents], oldMap[DataSplitter.FileList]))
					oldMap[DataSplitter.NEvents] = events
					oldMap[DataSplitter.NEvents] -= oldMap[DataSplitter.Skipped]

				oldMap[DataSplitter.NEvents] -= file[DataProvider.NEvents]
				oldMap[DataSplitter.FileList].pop(idx)

				if not oldMap[DataSplitter.FileList]:
					return oldMap
			return oldMap

		def getCorrespondingBlock(blockA, blocks):
			for blockB in blocks:
				if blockA[DataProvider.Dataset] == blockB[DataProvider.Dataset] and \
					blockA[DataProvider.BlockName] == blockB[DataProvider.BlockName]:
					return blockB
			raise RuntimeError("Block %s not found!" % str(blockA))

		# Return lists with expanded / shrunk files
		# Each list contains (newBlock, oldEvents) where newBlock contains exactly one file
		def splitAndClassifyChanges(oldBlocks, newBlocks):
			shrinked = []
			expanded = []
			print oldBlocks
			print newBlocks
			
			for oldBlock in oldBlocks:
				newBlock = getCorrespondingBlock(oldBlock, newBlocks)

				for oldFileInfo in oldBlock[DataProvider.FileList]:
					copyBlock = copy.copy(newBlock)
					newFileInfo = newFileInfoMap[oldFileInfo[DataProvider.lfn]]
					copyBlock[DataProvider.FileList] = [newFileInfo]

					if oldFileInfo[DataProvider.NEvents] < newFileInfo[DataProvider.NEvents]:
						expanded.append((copyBlock, oldFileInfo[DataProvider.NEvents]))
					elif oldFileInfo[DataProvider.NEvents] > newFileInfo[DataProvider.NEvents]:
						shrinked.append((copyBlock, oldFileInfo[DataProvider.NEvents]))
			return (shrinked, expanded)

		def getJobsWithFiles(filelist):
			result = set()
			for jobNum in range(self.getNumberOfJobs()):
				jobFiles = self.getSplitInfo(jobNum)[DataSplitter.FileList]
				for file in filelist:
					if file[DataProvider.lfn] in jobFiles:
						result.add(jobNum)
			return result

		def printJobs(jobs):
			print
			for job in jobs:
				DataSplitter.printInfoForJob(job)
				print
			print

		if blocksAdded or blocksMissing or blocksChanged:
			print "The following changes in the dataset information were detected:"

		if blocksChanged:
			print "="*70
			# TODO: print statistics (status: completely new / just added files)
			fileList = reduce(lambda x,y: x+y, map(lambda x: x[DataProvider.FileList], blocksChanged), [])
			affectedJobs = getJobsWithFiles(fileList)
			print "%d blocks consisting of %d files have changed their length" % (len(blocksChanged), len(fileList))
			print "This affects the following %d jobs:" % len(affectedJobs), list(affectedJobs)

			# Did the file shrink or expand?
			shrinkedBlocks, expandedBlocks = splitAndClassifyChanges(blocksChanged, newBlocks)

			shrinkedFiles = reduce(lambda x,y: x+y, map(lambda x: x[0][DataProvider.FileList], shrinkedBlocks), [])
			print "%d files have decreased in size" % (len(shrinkedFiles))
			if utils.boolUserInput('Do you want to treat the shrunken files as missing files?', False):
				blocksMissing.extend(map(lambda x: x[0], shrinkedBlocks))

			expandedFiles = reduce(lambda x,y: x+y, map(lambda x: x[0][DataProvider.FileList], expandedBlocks), [])
			print "%d files have expanded in size" % (len(expandedFiles))
			if utils.boolUserInput('Do you want exclude these jobs from processing?', False):
				jobLock = open(os.path.join(path, 'joblock.dat'), 'a')
				jobLock.writelines(map(lambda x: str(x) + '\n', getJobsWithFiles(expandedFiles)))

			addedJobs = []
			for block, skip in expandedBlocks:
				addedJobs.extend(self.splitDatasetInternal(block, skip))

			print "Processing the expanded part of these files would result in %d new jobs." % len(addedJobs)
			if utils.boolUserInput('Do you want submit these jobs with the expanded blocks/files?', False):
				printJobs(addedJobs)
				self._jobFiles.extend(addedJobs)

		if blocksAdded:
			print "="*70
			# TODO: print statistics (status: completely new / just added files)
			NFiles = sum(map(lambda x: len(x[DataProvider.FileList]), blocksAdded))
			addedJobs = self.splitDatasetInternal(blocksAdded)
			print "%d files in %d blocks were added." % (NFiles, len(blocksAdded))
			print "This would result in %d new jobs." % len(addedJobs)
			if utils.boolUserInput('Do you want submit these jobs with the added blocks/files?', False):
				printJobs(addedJobs)
				self._jobFiles.extend(addedJobs)

		if blocksMissing:
			print "="*70
			# TODO: print statistics (status: missing)
			fileList = reduce(lambda x,y: x+y, map(lambda x: x[DataProvider.FileList], blocksMissing))
			affectedJobs = getJobsWithFiles(fileList)
			print "%d files in %d blocks are missing." % (NFiles, len(blocksMissing))
			print "This affects the following %d jobs:" % len(affectedJobs), list(affectedJobs)
			if utils.boolUserInput('Do you want exclude these jobs from processing?', False):
				jobLock = open(os.path.join(path, 'joblock.dat'), 'a')
				jobLock.writelines(map(lambda x: str(x) + '\n',affectedJobs))

			for jobNum in affectedJobs:
				newMap = removeFilesFromMapping(self.getSplitInfo(jobNum), fileList, oldBlocks)
				if not newMap[DataSplitter.FileList]:
					continue
				addedJobs.append(newMap)

			print 'Reprocessing the unaffected parts of these jobs would result in %d new jobs.' % len(addedJobs)
			if utils.boolUserInput('Do you want reprocess the unaffected parts of these jobs?', False):
				printJobs(addedJobs)
				self._jobFiles.extend(addedJobs)


	def saveJobMapping(tar, fmt, entry, jobNum):
		def flat((x,y,z)):
			if isinstance(z, list):
				return (x,y,str.join(',', z))
			return (x,y,z)

		tmp = entry.pop(DataSplitter.FileList)

		commonprefix = os.path.commonprefix(tmp)
		commonprefix = str.join('/', commonprefix.split('/')[:-1])
		if len(commonprefix) > 6:
			entry[DataSplitter.CommonPrefix] = commonprefix
			savelist = map(lambda x: x.replace(commonprefix + '/', ''), tmp)
		else:
			savelist = tmp

		for name, data in [('list', str.join('\n', savelist)), ('info', fmt.format(entry, fkt = flat))]:
			info, file = utils.VirtualFile(os.path.join("%05d" % jobNum, name), data).getTarInfo()
			tar.addfile(info, file)
			file.close()

		if entry.has_key(DataSplitter.CommonPrefix):
			entry.pop(DataSplitter.CommonPrefix)
		entry[DataSplitter.FileList] = tmp
	saveJobMapping = staticmethod(saveJobMapping)


	# Save as tar file to allow random access to mapping data with little memory overhead
	def saveState(self, path):
		tar = tarfile.open(os.path.join(path, 'datamap.tar'), 'w:')
		fmt = utils.DictFormat()

		meta = {
			'ClassName': self.__class__.__name__,
			'MaxJobs': len(self._jobFiles),
		}
		log = None
		meta.update(dict(filter(lambda (x,y): x != '_jobFiles', self.__dict__.iteritems())))
		info, file = utils.VirtualFile('Metadata', fmt.format(meta)).getTarInfo()
		tar.addfile(info, file)
		file.close()

		subTarFiles = {}
		for jobNum, entry in enumerate(self._jobFiles):
			if not subTarFiles.has_key(jobNum / 100):
				subTarFileObj = cStringIO.StringIO()
				subTarFile = tarfile.open(mode = "w:gz", fileobj = subTarFileObj)
				subTarFiles[jobNum / 100] = (subTarFile, subTarFileObj)
				del log
				log = utils.ActivityLog('Writing job mapping file [%d / %d]' % (jobNum, len(self._jobFiles)))
			DataSplitter.saveJobMapping(subTarFiles[jobNum / 100][0], fmt, entry, jobNum)

		for (name, (subTarFile, subTarFileObj)) in subTarFiles.iteritems():
			subTarFile.close()
			subTarFileObj.seek(0)
			subTarFileInfo = tarfile.TarInfo("%03dXX.tgz" % name)
			subTarFileInfo.size = len(subTarFileObj.getvalue())
			tar.addfile(subTarFileInfo, subTarFileObj)

		tar.close()
		del log


	def loadState(path):
		# TODO: Merge JobFileTarAdaptor into SplitterBase
		class JobFileTarAdaptor(object):
			def __init__(self, path):
				log = utils.ActivityLog('Reading job mapping file')
				self._fmt = utils.DictFormat()
				self._path = os.path.join(path, 'datamap.tar')
				self._tar = tarfile.open(self._path, 'r:')
				metadata = self._tar.extractfile('Metadata').readlines()
				self._metadata = self._fmt.parse(metadata, lowerCaseKey = False)
				self._maxJobs = self._metadata.pop('MaxJobs')
				self._classname = self._metadata.pop('ClassName')
				self._cacheKey = None
				self._cacheTar = None
				del log

			def __getitem__(self, key):
				if not self._cacheKey == key / 100:
					self._cacheKey = key / 100
					subTarFileObj = self._tar.extractfile('%03dXX.tgz' % (key / 100))
					self._cacheTar = tarfile.open(mode = 'r:gz', fileobj = subTarFileObj)
				data = self._fmt.parse(self._cacheTar.extractfile('%05d/info' % key).readlines())
				data[DataSplitter.SEList] = data[DataSplitter.SEList].split(',')
				list = self._cacheTar.extractfile('%05d/list' % key).readlines()
				if data.has_key(DataSplitter.CommonPrefix):
					list = map(lambda x: "%s/%s" % (data[DataSplitter.CommonPrefix], x), list)
				data[DataSplitter.FileList] = map(str.strip, list)
				return data

			def __len__(self):
				return self._maxJobs

			def append(self, entry):
				# TODO: Fixme
				self._tar.close()
				self._tar = tarfile.open(self._path, 'a:')
				DataSplitter.saveJobMapping(self._tar, self._fmt, entry, self._maxJobs)
				self._maxJobs += 1
				self._tar.close()
				self._tar = tarfile.open(self._path, 'r:')

			def extend(self, entries):
				for x in entries:
					self.append(x)

			def getDataSplitter(self):
				instance = DataSplitter.open(self._classname, self._metadata)
				instance._jobFiles = self
				return instance

		return JobFileTarAdaptor(path).getDataSplitter()
	loadState = staticmethod(loadState)