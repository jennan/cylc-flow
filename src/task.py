#!/usr/bin/python

#         __________________________
#         |____C_O_P_Y_R_I_G_H_T___|
#         |                        |
#         |  (c) NIWA, 2008-2010   |
#         | Contact: Hilary Oliver |
#         |  h.oliver@niwa.co.nz   |
#         |    +64-4-386 0461      |
#         |________________________|


# TASK BASE CLASS:

import sys
import task_state
import logging
import Pyro.core
from copy import deepcopy

global state_changed
#state_changed = False
state_changed = True

# NOTE ON TASK STATE INFORMATION---------------------------------------

# task attributes required for a system cold start are:
#  state ('waiting', 'submitted', 'running', and 'finished' or 'failed')

# The 'state' variable is initialised by the base class, and written to
# the state dump file by the base class dump_state() method.

# For a restart from previous state some tasks may require additional
# state information to be stored in the state dump file.

# To handle this difference in initial state information (between normal
# start and restart) task initialisation must use a default value of
# 'None' for the additional variables, and for a restart the task
# manager must instantiate each task with a flattened list of all the
# state values found in the state dump file.

# The task abdication mechanism ASSUMES that the task manager creates
# the successor task as soon as the current task spawns.

class task( Pyro.core.ObjBase ):
    
    # this only needed in cycling tasks?
    quick_death = True

    @classmethod
    def describe( cls ):
        return cls.description 
        #for line in cls.description:
        #    print line


    @classmethod
    def set_class_var( cls, item, value ):
        # set the value of a class variable 
        # that will be written to the state dump file
        try:
            cls.class_vars[ item ] = value
        except AttributeError:
            cls.class_vars = {}
            cls.class_vars[ item ] = value


    @classmethod
    def get_class_var( cls, item ):
        # get the value of a class variable that is
        # written to the state dump file
        try:
            return cls.class_vars[ item ]
        except:
            raise AttributeError

    @classmethod
    def dump_class_vars( cls, FILE ):
        # dump special class variables to the state dump file
        try:
            result = ''
            for key in cls.class_vars:
                result += key + '=' + str( cls.class_vars[ key ] ) + ', '
            result = result.rstrip( ', ' )
            FILE.write( 'class ' + cls.__name__ + ' : ' + result + '\n')
        except AttributeError:
            # class has no class_vars defined
            pass


    def __init__( self, state, no_reset ):
        # Call this AFTER derived class initialisation

        # Derived class init MUST define:
        #  * unique identity
        #  * prerequisites and outputs
        #  * self.env_vars 

        class_vars = {}
        self.state = task_state.task_state( state, no_reset )

        # count instances of each top level object derived from task
        # top level derived classes must define:
        #   <class>.instance_count = 0
        #   <class>.upward_instance_count = 0
        self.__class__.instance_count += 1
        self.__class__.upward_instance_count += 1

        Pyro.core.ObjBase.__init__(self)

        # set state_changed True if any task's state changes 
        # as a result of a remote method call
        global state_changed 
        state_changed = True

        self.latest_message = ""

        if self.state.is_running():  
            # running tasks must have satisfied prerequisites
            self.log( 'WARNING', " starting in RUNNING state: MANUAL RESET REQUIRED!" )
            self.outputs.set_all_unsatisfied()
            self.prerequisites.set_all_satisfied()

        if self.state.is_finished():  
            # finished tasks must have satisfied prerequisites
            # and completed outputs
            self.log( 'NORMAL', " starting in FINISHED state" )
            self.outputs.set_all_satisfied()
            self.prerequisites.set_all_satisfied()

        #elif self.state.is_satisfied():
        #    # a waiting state can be satisfied (i.e. ready to go)
        #    # particular if the system has been paused before 
        #    # shutdown (which happens in normal shutdown).
        #    self.log( 'WARNING', " starting in SATISFIED state" )
        #    self.prerequisites.set_all_satisfied()

    def get_identity( self ):
        # return unique task id
        return self.id

    def register_run_length( self, run_len_minutes ):
        # automatically define special 'started' and 'finished' outputs
        self.outputs.add( 0, self.get_identity() + ' started' )
        # and 'completed' for dependant tasks that don't care about
        # success or failure of this task, only completion
        self.outputs.add( run_len_minutes - 0.01, self.get_identity() + ' completed' )
        self.outputs.add( run_len_minutes, self.get_identity() + ' finished' )

    def log( self, priority, message ):
        # task-specific log file

        # is it better to "get" this each call as here, or to get a
        # 'self.logger' once in __init__?
        logger = logging.getLogger( "main." + self.name ) 

        # task logs are specific to task type
        message = '[' + self.tag + ']' + message

        if priority == "WARNING":
            logger.warning( message )
        elif priority == "NORMAL":
            logger.info( message )
        elif priority == "DEBUG":
            logger.debug( message )
        elif priority == "CRITICAL":
            logger.critical( message )
        else:
            logger.warning( 'UNKNOWN PRIORITY: ' + priority )
            logger.warning( '-> ' + message )


    def prepare_for_death( self ):
        # The task manager MUST call this immediately before deleting a
        # task object. It decrements the instance count of top level
        # objects derived from task base. It would be nice to use Python's
        # __del__() function for this, but that is only called when a
        # deleted object is about to be garbage collected (which is not
        # guaranteed to be right away). This was once used for
        # constraining the number of instances of each task type. 
        self.__class__.instance_count -= 1

    def ready_to_run( self, current_time ):
        # ready if 'waiting' AND all prequisites satisfied
        ready = False
        if self.state.is_waiting() and self.prerequisites.all_satisfied(): 
            ready = True
        return ready

    def run_if_ready( self, current_time ):
        if self.ready_to_run( current_time ):
            self.run_external_task()

    def run_external_task( self ):
        self.log( 'DEBUG',  'launching external task' )
        self.launcher.submit()
        self.state.set_status( 'submitted' )

    def get_timed_outputs( self ):
        # NOT USED?
        return self.outputs.get_timed_requisites()

    def set_all_internal_outputs_completed( self ):
        # used by the task wrapper 
        self.log( 'DEBUG', 'setting all internal outputs completed' )
        for message in self.outputs.satisfied.keys():
            if message != self.get_identity() + ' started' and \
                    message != self.get_identity() + ' finished' and \
                    message != self.get_identity() + ' completed':
                #self.outputs.set_satisfied( message )
                #self.latest_message = message
                self.incoming( 'NORMAL', message )

    def is_complete( self ):  # not needed?
        if self.outputs.all_satisfied():
            return True
        else:
            return False

    def get_postrequisite_list( self ):
        return self.outputs.get_list()

    def incoming( self, priority, message ):
        # receive all incoming pyro messages for this task 
        self.latest_message = message

        # setting state_change results in task processing loop
        # invocation. We should really only do this when the
        # incoming message results in a state change that matters to
        # scheduling ... but system monitor may need latest message, and
        # we don't yet have a separate state-summary-update invocation
        # flag. 
        
        # new round of dependency renegotiations)
        global state_changed
        state_changed = True

        if message == self.get_identity() + ' started':
            self.state.set_status( 'running' )

        if not self.state.is_running():
            # my external task should not be running!
            self.log( 'WARNING', "UNEXPECTED MESSAGE (task should not be running)" )
            self.log( 'WARNING', '-> ' + message )

        # prefix task id to special messages.
        #raw_message = message
        #if message == 'started' or message == 'finished' or message == 'failed' or message == 'completed':
        #    message = self.get_identity() + ' ' + message
 
        if self.outputs.exists( message ):
            # registered output messages

            if not self.outputs.is_satisfied( message ):
                # message indicates completion of a registered output.
                self.log( priority,  message )
                self.outputs.set_satisfied( message )

                if message == self.get_identity() + ' finished':
                    # TASK HAS FINISHED
                    if not self.outputs.all_satisfied():
                        self.log( 'CRITICAL', 'finished before all outputs were completed' )
                        self.state.set_status( 'failed' )
                    else:
                        self.state.set_status( 'finished' )
            else:
                # this output has already been satisfied
                self.log( 'WARNING', "UNEXPECTED OUTPUT (already satisfied):" )
                self.log( 'WARNING', "-> " + message )

        elif message == self.get_identity() + ' failed':
            # process task failure messages

            state_changed = True
            if priority != 'CRITICAL':
                self.log( 'WARNING', 'non-critical priority for task failure' )
            self.log( 'CRITICAL',  message )
            self.state.set_status( 'failed' )

        else:
            # log other (non-failed) unregistered messages with a '*' prefix
            message = '*' + message
            self.log( priority, message )

    def update( self, reqs ):
        for req in reqs.get_list():
            if req in self.prerequisites.get_list():
                # req is one of my prerequisites
                if reqs.is_satisfied(req):
                    self.prerequisites.set_satisfied( req )

    def dump_state( self, FILE ):
        # Write state information to the state dump file
        # This must be compatible with __init__() on reload

        FILE.write( 'BASE'     + ' : ' + 
                    self.name         + ' : ' + 
                    self.state.dump() + '\n' )

    def spawn( self ):
        if self.state.has_spawned():
            return False

        if self.ready_to_spawn():
            self.state.set_spawned()
            return True
        else:
            return False

    def has_spawned( self ):
        # this exists because the oneoff modifier needs to override it.
        return self.state.has_spawned()

    def ready_to_spawn( self ):
        self.log( 'CRITICAL', 'derived classes must override ready_to_spawn()')
        sys.exit(1)

    def done( self ):
        # return True if task has finished and spawned
        if self.state.is_finished() and self.state.has_spawned():
            return True
        else:
            return False

    def check_requisites( self ):
        # overridden by asynchronous tasks
        pass

    def get_state_summary( self ):
        # derived classes can call this method and then 
        # add more information to the summary if necessary.

        n_total = self.outputs.count()
        n_satisfied = self.outputs.count_satisfied()

        summary = {}
        summary[ 'name' ] = self.name
        summary[ 'short_name' ] = self.short_name
        summary[ 'state' ] = self.state.get_status()
        summary[ 'n_total_outputs' ] = n_total
        summary[ 'n_completed_outputs' ] = n_satisfied
        summary[ 'spawned' ] = self.state.has_spawned()
        summary[ 'latest_message' ] = self.latest_message
 
        return summary
