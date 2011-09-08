#!/usr/bin/env python

#C: THIS FILE IS PART OF THE CYLC FORECAST SUITE METASCHEDULER.
#C: Copyright (C) 2008-2011 Hilary Oliver, NIWA
#C:
#C: This program is free software: you can redistribute it and/or modify
#C: it under the terms of the GNU General Public License as published by
#C: the Free Software Foundation, either version 3 of the License, or
#C: (at your option) any later version.
#C:
#C: This program is distributed in the hope that it will be useful,
#C: but WITHOUT ANY WARRANTY; without even the implied warranty of
#C: MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#C: GNU General Public License for more details.
#C:
#C: You should have received a copy of the GNU General Public License
#C: along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Job submission base class.

Writes a temporary "job file" that exports the cylc environment (so the
executing task can access cylc commands), suite environment, and then  
executes the task command scripting. Derived job submission classes
define the means by which the job file itself is executed.

If OWNER@REMOTE_HOST is not equivalent to whoami@localhost:
   ssh OWNER@HOST submit(FILE)
so passwordless ssh must be configured.
"""

import pwd
import re, os, sys
from cylc.mkdir_p import mkdir_p
import stat
import string
from jobfile import jobfile
import socket
import subprocess
import time
 
class job_submit(object):
    REMOTE_COMMAND_TEMPLATE = ( " '"
            + "mkdir -p $(dirname %(jobfile_path)s)"
            + " && cat >%(jobfile_path)s"
            + " && chmod +x %(jobfile_path)s"
            + " && (%(command)s)"
            + "'" )
 
    # class variables that are set remotely at startup:
    # (e.g. 'job_submit.simulation_mode = True')
    simulation_mode = False
    failout_id = None
    cylc_env = None

    def __init__( self, task_id, task_command, task_env, directives, 
            manual_messaging, logfiles, joblog_dir, task_owner,
            host, remote_cylc_dir, remote_suite_dir,
            remote_shell_template, job_submit_command_template,
            job_submission_shell ): 

        self.task_id = task_id
        self.task_command = task_command
        if self.__class__.simulation_mode and self.__class__.failout_id == self.task_id:
            self.task_command = '/bin/false'

        self.task_env = task_env
        self.directives  = directives
        self.logfiles = logfiles
 
        self.suite_owner = os.environ['USER']
        if task_owner:
            self.task_owner = task_owner
            self.other_owner = True
        else:
            self.task_owner = self.suite_owner
            self.other_owner = False

        self.remote_shell_template = remote_shell_template
        self.job_submit_command_template = job_submit_command_template
        self.job_submission_shell = job_submission_shell

        self.remote_cylc_dir = remote_cylc_dir
        self.remote_suite_dir = remote_suite_dir

        self.host = "localhost"
        self.local = True
        if host and host != socket.gethostname():
            if not self.__class__.simulation_mode:
                # Ignore remote hosting in simulation mode, so we can
                # dummy-run these suites outside of normal environment.
                self.local = False
                self.host = host

        if manual_messaging != None:  # boolean, must distinguish None from False
            self.manual_messaging = manual_messaging

        self.set_logfile_names( joblog_dir )

        # Overrideable methods
        self.set_directives()
        self.set_scripting()
        self.set_environment()
 
    def set_logfile_names( self, dir ):
        # Set the file paths for the task job script and the stdout and
        # stderr logs generated on executing the job script.

        # Tag file names with microseconds since epoch
        now = time.time()
        key = self.task_id + "-%.6f" % now
        self.jobfile_path = os.path.join( dir, key )

        # Remote tasks still need a to write the job file locally first
        self.local_jobfile_path = self.expand_local( self.jobfile_path )

        if self.local:
            self.jobfile_path = self.local_jobfile_path
        else:
            # Expand suite identity variables leaving others to be interpreted on the host machine.
            for var in self.__class__.cylc_env:
                self.jobfile_path = re.sub( '\${'+var+'}' + r'\b', self.__class__.cylc_env[var], self.jobfile_path )
                self.jobfile_path = re.sub( '\$'+var+r'\b',   self.__class__.cylc_env[var], self.jobfile_path )

        # Note that derived classes may have to deal with other environment
        # variables in the stdout/stderr file paths too. In loadleveler
        # directives, for instance, environment variables are not interpolated.
        self.stdout_file = self.jobfile_path + ".out"
        self.stderr_file = self.jobfile_path + ".err"

        self.logfiles.add_path( self.local_jobfile_path )
        if self.local:
            # Record paths of local log files for access by gcylc
            self.logfiles.add_path( self.stdout_file)
            self.logfiles.add_path( self.stderr_file)
        else:
            # Record remote paths (currently not accessible by gcylc):
            self.logfiles.add_path( self.task_owner + '@' + self.host + ':' + self.stdout_file)
            self.logfiles.add_path( self.task_owner + '@' + self.host + ':' + self.stderr_file)

    def expand_local( self, var ):
        return os.path.expandvars( os.path.expanduser(var))

    def set_directives( self ):
        # OVERRIDE IN DERIVED CLASSES IF NECESSARY
        # self.directives['name'] = value

        # Prefix, e.g. '#QSUB ' (qsub), or '#@ ' (loadleveler)
        self.directive_prefix = "# FOO "
        # Final directive, WITH PREFIX, e.g. '#@ queue' for loadleveler
        self.final_directive = " # FINAL"

    def set_scripting( self ):
        # OVERRIDE IN DERIVED CLASSES IF NECESSARY
        # to modify pre- and post-command scripting
        return

    def set_environment( self ):
        # OVERRIDE IN DERIVED CLASSES IF NECESSARY
        # to modify global or task-specific environment
        return

    def construct_jobfile_submission_command( self ):
        # DERIVED CLASSES MUST OVERRIDE.
        # Construct self.command, a command to submit the job file to
        # run by the derived job submission method.
        raise SystemExit( 'ERROR: no job submission command defined!' )

    def submit( self, dry_run ):
        try: 
            os.chdir( pwd.getpwnam(self.suite_owner).pw_dir )
        except OSError, e:
            print >> sys.stderr, "Failed to change to suite owner's home directory"
            print >> sys.stderr, e
            return False

        jf = jobfile( self.task_id, 
                self.__class__.cylc_env, self.task_env, 
                self.directive_prefix, self.directives, self.final_directive, 
                self.manual_messaging, self.task_command, 
                self.remote_cylc_dir, self.remote_suite_dir, 
                self.job_submission_shell, 
                self.__class__.simulation_mode,
                self.__class__.__name__ )
        # create local job log directory
        mkdir_p( os.path.dirname( self.local_jobfile_path ))
        # write the job file
        jf.write( self.local_jobfile_path )
        # make it executable
        os.chmod( self.local_jobfile_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO )
        print "> GENERATED JOBFILE: " + self.local_jobfile_path

        # Construct self.command, the command to submit the jobfile to run
        self.construct_jobfile_submission_command()
    
        if self.local:
            stdin = None
            jobfile_path = self.local_jobfile_path
            command = self.command
        else:
            command = self.__class__.REMOTE_COMMAND_TEMPLATE % { "jobfile_path": self.jobfile_path, "command": self.command }
            stdin = subprocess.PIPE
            destination = self.task_owner + "@" + self.host
            remote_shell_template = self.remote_shell_template
            command = remote_shell_template % destination + command
            jobfile_path = destination + ":" + self.jobfile_path

        # execute the local command to submit the job
        if dry_run:
            print "> THIS IS A DRY RUN. HERE'S HOW I WOULD SUBMIT THE TASK:"
            print command
            return True

        print " > SUBMITTING TASK: " + command
        try:
            popen = subprocess.Popen( command, shell=True, stdin=stdin )
            if not self.local:
                f = open(self.local_jobfile_path)
                popen.communicate(f.read())
                f.close()
            res = popen.wait()
            if res < 0:
                print >> sys.stderr, "command terminated by signal", res
                success = False
            elif res > 0:
                print >> sys.stderr, "command failed", res
                success = False
            else:
                # res == 0
                success = True
        except OSError, e:
            # THIS DOES NOT CATCH BACKGROUND EXECUTION FAILURE
            # (i.e. cylc's simplest "background" job submit method)
            # because a background job returns immediately and the failure
            # occurs in the background sub-shell.
            print >> sys.stderr, "Job submission failed", e
            success = False
            raise

        return success

