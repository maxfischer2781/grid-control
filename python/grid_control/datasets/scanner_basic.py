import os, sys
from grid_control import QM, utils, ConfigError, storage, JobSelector, AbstractObject, Config, Module, JobDB, JobSelector, Job, RethrowError
from python_compat import *

def splitParse(opt):
	(delim, ds, de) = utils.optSplit(opt, '::')
	return (delim, utils.parseInt(ds), utils.parseInt(de))


class InfoScanner(AbstractObject):
	def __init__(self, setup, config, section):
		pass

	def getGuards(self):
		return ([], [])

	def getEntriesVerbose(self, *args):
		utils.vprint('%s: Collecting information with %s' % (self.__class__.__name__, args), 3)
		return self.getEntries(*args)

	def getEntries(self, path, metadata, events, seList, objStore):
		raise AbstractError
InfoScanner.dynamicLoaderPath()


# Get output directories from external config file
class OutputDirsFromConfig(InfoScanner):
	def __init__(self, setup, config, section):
		newVerbosity = utils.verbosity(utils.verbosity() - 3)
		extConfig = Config(setup(config.getPath, section, 'source config'))
		self.extWorkDir = extConfig.getPath('global', 'workdir', extConfig.workDirDefault)
		extConfig.opts = type('DummyType', (), {'init': False, 'resync': False})
		extConfig.workDir = self.extWorkDir
		self.extModule = Module.open(extConfig.get('global', 'module'), extConfig)
		selector = setup(config.get, section, 'source job selector', '')
		extJobDB = JobDB(extConfig, jobSelector = lambda jobNum, jobObj: jobObj.state == Job.SUCCESS)
		self.selected = sorted(extJobDB.getJobs(JobSelector.create(selector, module = self.extModule)))
		utils.verbosity(newVerbosity + 3)

	def getEntries(self, path, metadata, events, seList, objStore):
		log = None
		for jobNum in self.selected:
			del log
			log = utils.ActivityLog('Reading job logs - [%d / %d]' % (jobNum, self.selected[-1]))
			metadata['GC_JOBNUM'] = jobNum
			objStore.update({'GC_MODULE': self.extModule, 'GC_WORKDIR': self.extWorkDir})
			yield (os.path.join(self.extWorkDir, 'output', 'job_%d' % jobNum), metadata, events, seList, objStore)


class OutputDirsFromWork(InfoScanner):
	def __init__(self, setup, config, section):
		self.extWorkDir = setup(config.get, section, 'source directory')
		self.extOutputDir = os.path.join(self.extWorkDir, 'output')

	def getEntries(self, path, metadata, events, seList, objStore):
		for dirName in filter(lambda fn: fn.startswith('job_'), sorted(os.listdir(self.extOutputDir))):
			try:
				metadata['GC_JOBNUM'] = int(dirName.split('_')[1])
				objStore['GC_WORKDIR'] = self.extWorkDir
				yield (os.path.join(self.extOutputDir, dirName), metadata, events, seList, objStore)
			except:
				pass


class MetadataFromModule(InfoScanner):
	def __init__(self, setup, config, section):
		ignoreDef = map(lambda x: 'SEED_%d' % x, range(10)) + ['FILE_NAMES',
			'SB_INPUT_FILES', 'SE_INPUT_FILES', 'SE_INPUT_PATH', 'SE_INPUT_PATTERN',
			'SB_OUTPUT_FILES', 'SE_OUTPUT_FILES', 'SE_OUTPUT_PATH', 'SE_OUTPUT_PATTERN',
			'SE_MINFILESIZE', 'DOBREAK', 'MY_RUNTIME', 'MY_JOBID',
			'GC_VERSION', 'GC_DEPFILES', 'SUBST_FILES', 'SEEDS',
			'SCRATCH_LL', 'SCRATCH_UL', 'LANDINGZONE_LL', 'LANDINGZONE_UL']
		self.ignoreVars = setup(config.getList, section, 'ignore module vars', ignoreDef)

	def getEntries(self, path, metadata, events, seList, objStore):
		if 'GC_MODULE' in objStore:
			tmp = objStore['GC_MODULE'].getTaskConfig()
			if 'GC_JOBNUM' in metadata:
				tmp.update(objStore['GC_MODULE'].getJobConfig(metadata['GC_JOBNUM']))
			metadata.update(utils.filterDict(tmp, kF = lambda k: k not in self.ignoreVars))
		yield (path, metadata, events, seList, objStore)


class FilesFromLS(InfoScanner):
	def __init__(self, setup, config, section):
		self.path = setup(config.getPath, section, 'source directory', '.')

	def getEntries(self, path, metadata, events, seList, objStore):
		metadata['GC_SOURCE_DIR'] = self.path
		for fn in storage.se_ls(self.path).iter():
			yield (os.path.join(self.path, fn.strip()), metadata, events, seList, objStore)


class FilesFromJobInfo(InfoScanner):
	def getGuards(self):
		return (['SE_OUTPUT_FILE'], ['SE_OUTPUT_PATH'])

	def getEntries(self, path, metadata, events, seList, objStore):
		jobInfoPath = os.path.join(path, 'job.info')
		try:
			jobInfo = utils.DictFormat('=').parse(open(jobInfoPath))
			files = filter(lambda x: x[0].startswith('file'), jobInfo.items())
			fileInfos = map(lambda (x, y): tuple(y.strip('"').split('  ')), files)
			for (hashMD5, name_local, name_dest, pathSE) in fileInfos:
				metadata.update({'SE_OUTPUT_HASH_MD5': hashMD5, 'SE_OUTPUT_FILE': name_local,
					'SE_OUTPUT_BASE': os.path.splitext(name_local)[0], 'SE_OUTPUT_PATH': pathSE})
				yield (os.path.join(pathSE, name_dest), metadata, events, seList, objStore)
		except KeyboardInterrupt:
			sys.exit(0)
		except:
			raise RethrowError('Unable to read job results from %s!' % jobInfoPath)


class MatchOnFilename(InfoScanner):
	def __init__(self, setup, config, section):
		self.match = setup(config.getList, section, 'filename filter', ['*.root'])

	def getEntries(self, path, metadata, events, seList, objStore):
		if utils.matchFileName(path, self.match):
			yield (path, metadata, events, seList, objStore)


class MatchDelimeter(InfoScanner):
	def __init__(self, setup, config, section):
		self.matchDelim = setup(config.get, section, 'delimeter match', '').split(':')
		self.delimDS = setup(config.get, section, 'delimeter dataset key', '')
		self.delimB = setup(config.get, section, 'delimeter block key', '')

	def getGuards(self):
		return (QM(self.delimDS, ['DELIMETER_DS'], []), QM(self.delimB, ['DELIMETER_B'], []))

	def getEntries(self, path, metadata, events, seList, objStore):
		if len(self.matchDelim) == 2:
			if os.path.basename(path).count(self.matchDelim[0]) != self.matchDelim[1]:
				raise StopIteration
		getVar = lambda (d, s, e): str.join(d, os.path.basename(path).split(d)[s:e])
		if self.delimDS:
			metadata['DELIMETER_DS'] = getVar(splitParse(self.delimDS))
		if self.delimB:
			metadata['DELIMETER_B'] = getVar(splitParse(self.delimB))
		yield (path, metadata, events, seList, objStore)


class DetermineEvents(InfoScanner):
	def __init__(self, setup, config, section):
		self.eventsCmd = setup(config.get, section, 'events command', '')
		self.eventsKey = setup(config.get, section, 'events key', '')
		self.ignoreEmpty = setup(config.getBool, section, 'events ignore empty', True)
		self.eventsDefault = setup(config.get, section, 'events default', -1)

	def getEntries(self, path, metadata, events, seList, objStore):
		events = int(metadata.get(self.eventsKey, QM(events >= 0, events, self.eventsDefault)))
		try:
			if opts.eventsCmd:
				events = int(os.popen('%s %s' % (opts.eventsCmd, path)).readlines()[-1])
		except:
			pass
		if (not self.ignoreEmpty) or events != 0:
			yield (path, metadata, events, seList, objStore)