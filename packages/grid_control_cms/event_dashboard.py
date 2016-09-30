# | Copyright 2009-2016 Karlsruhe Institute of Technology
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

import os, time
from grid_control.job_db import Job
from grid_control.monitoring import Monitoring
from grid_control.utils import filter_dict, get_path_share, get_version, merge_dict_list
from grid_control.utils.thread_tools import GCThreadPool
from grid_control_cms.DashboardAPI.DashboardAPI import DashboardAPI
from python_compat import identity


class DashBoard(Monitoring):
	config_section_list = Monitoring.config_section_list + ['dashboard']

	def __init__(self, config, name, task):
		Monitoring.__init__(self, config, name, task)
		jobDesc = task.getDescription(None) # TODO: use the other variables for monitoring
		self._app = config.get('application', 'shellscript', on_change = None)
		self._runningMax = config.get_time('dashboard timeout', 5, on_change = None)
		self._tasktype = config.get('task', jobDesc.jobType or 'analysis', on_change = None)
		self._taskname = config.get('task name', '@GC_TASK_ID@_@DATASETNICK@', on_change = None)
		self._statusMap = {Job.DONE: 'DONE', Job.FAILED: 'DONE', Job.SUCCESS: 'DONE',
			Job.RUNNING: 'RUNNING', Job.ABORTED: 'ABORTED', Job.CANCELLED: 'CANCELLED'}
		self._tp = GCThreadPool()


	def getScript(self):
		yield get_path_share('mon.dashboard.sh', pkg = 'grid_control_cms')


	def get_task_dict(self):
		result = {'TASK_NAME': self._taskname, 'DB_EXEC': self._app, 'DATASETNICK': ''}
		result.update(Monitoring.get_task_dict(self))
		return result


	def getFiles(self):
		yield get_path_share('mon.dashboard.sh', pkg = 'grid_control_cms')
		for fn in ('DashboardAPI.py', 'Logger.py', 'apmon.py', 'report.py'):
			yield get_path_share('..', 'DashboardAPI', fn, pkg = 'grid_control_cms')


	def _publish(self, jobObj, jobnum, taskId, usermsg):
		(_, backend, rawId) = jobObj.gcID.split('.', 2)
		dashId = '%s_%s' % (jobnum, rawId)
		if 'http' not in jobObj.gcID:
			dashId = '%s_https://%s:/%s' % (jobnum, backend, rawId)
		msg = merge_dict_list([{'taskId': taskId, 'jobId': dashId, 'sid': rawId}] + usermsg)
		DashboardAPI(taskId, dashId).publish(**filter_dict(msg, value_filter = identity))


	def _start_publish(self, jobObj, jobnum, desc, message):
		taskId = self._task.substVars('dashboard task id', self._taskname, jobnum,
			addDict = {'DATASETNICK': ''}).strip('_')
		self._tp.start_daemon('Notifying dashboard about %s of job %d' % (desc, jobnum),
			self._publish, jobObj, jobnum, taskId, message)


	# Called on job submission
	def onJobSubmit(self, wms, jobObj, jobnum):
		token = wms.getAccessToken(jobObj.gcID)
		jobInfo = self._task.get_job_dict(jobnum)
		self._start_publish(jobObj, jobnum, 'submission', [{
			'user': os.environ['LOGNAME'], 'GridName': '/CN=%s' % token.getUsername(), 'CMSUser': token.getUsername(),
			'tool': 'grid-control', 'JSToolVersion': get_version(),
			'SubmissionType':'direct', 'tool_ui': os.environ.get('HOSTNAME', ''),
			'application': jobInfo.get('SCRAM_PROJECTVERSION', self._app),
			'exe': jobInfo.get('CMSSW_EXEC', 'shellscript'), 'taskType': self._tasktype,
			'scheduler': wms.getObjectName(), 'vo': token.getGroup(),
			'nevtJob': jobInfo.get('MAX_EVENTS', 0),
			'datasetFull': jobInfo.get('DATASETPATH', 'none')}])


	# Called on job status update and output
	def _updateDashboard(self, wms, jobObj, jobnum, data, addMsg):
		# Translate status into dashboard status message
		statusDashboard = self._statusMap.get(jobObj.state, 'PENDING')
		self._start_publish(jobObj, jobnum, 'status', [{'StatusValue': statusDashboard,
			'StatusValueReason': data.get('reason', statusDashboard).upper(),
			'StatusEnterTime': data.get('timestamp', time.strftime('%Y-%m-%d_%H:%M:%S', time.localtime())),
			'StatusDestination': data.get('dest', '') }, addMsg])


	def onJobUpdate(self, wms, jobObj, jobnum, data):
		self._updateDashboard(wms, jobObj, jobnum, jobObj, {})


	def onJobOutput(self, wms, jobObj, jobnum, retCode):
		self._updateDashboard(wms, jobObj, jobnum, jobObj, {'ExeExitCode': retCode})


	def onFinish(self):
		self._tp.wait_and_drop(self._runningMax)
