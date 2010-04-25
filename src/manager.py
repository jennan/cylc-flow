#!/usr/bin/python

#         __________________________
#         |____C_O_P_Y_R_I_G_H_T___|
#         |                        |
#         |  (c) NIWA, 2008-2010   |
#         | Contact: Hilary Oliver |
#         |  h.oliver@niwa.co.nz   |
#         |    +64-4-386 0461      |
#         |________________________|


import datetime
import cycle_time
import pimp_my_logger
import logging
import traceback
import sys, os, re
from dynamic_instantiation import get_object
from Pyro.errors import NamingError
from broker import broker

def ns_obj_name( name, groupname ):
    return groupname + '.' + name

class manager:
    def __init__( self, config, groupname, dummy_mode, startup ):

        self.config = config
        self.dummy_mode = dummy_mode
        self.groupname = groupname

        # TO DO: use self.config.get('foo') throughout
        self.clock = config.get('clock')
        self.pyro = config.get('daemon')  
        self.submit = config.get('job submit class' )

        self.system_hold_now = False
        self.system_hold_ctime = None
        self.stop_time = None

        # initialise the dependency broker
        self.broker = broker()

        if 'start_time' in startup:
            self.start_time = startup[ 'start_time' ]
        if 'restart' in startup:
            self.restart = startup[ 'restart' ]
        if 'exclude' in startup:
            self.exclude = startup[ 'exclude' ]
        if 'include' in startup:
            self.include = startup[ 'include' ]
        if 'no_reset' in startup:
            self.no_reset = startup[ 'no_reset' ]
        if 'initial_state_dump' in startup:
            self.initial_state_dump = startup[ 'initial_state_dump' ]
         
        self.tasks = []

        # create main logger
        self.create_main_log()


    def load( self ):
        # instantiate the initial task list and create loggers 
        if self.start_time:
            self.load_from_config( self.start_time, self.exclude, self.include )

        elif self.restart:
            self.load_from_state_dump( self.initial_state_dump, self.no_reset )
        else:
            raise SystemExit( "No startup method defined!" )

    def create_main_log( self ):
        log = logging.getLogger( 'main' )
        pimp_my_logger.pimp_it( \
             log, 'main', self.config.get('logging_dir'), \
                self.config.get('logging_level'), self.dummy_mode, self.clock )
        # manager logs to the main log
        self.log = log

    def create_task_log( self, name ):
        log = logging.getLogger( 'main.' + name )
        pimp_my_logger.pimp_it( \
             log, name, self.config.get('logging_dir'), \
                self.config.get('logging_level'), self.dummy_mode, self.clock )

    def get_tasks( self ):
        return self.tasks

    def set_stop_time( self, stop_time ):
        self.log.debug( "Setting stop time: " + stop_time )
        self.stop_time = stop_time

    def set_system_hold( self, ctime = None ):
        self.log.warning( 'pre-hold state dump: ' + self.dump_state( new_file = True ))
        if ctime:
            self.system_hold_ctime = ctime
            self.log.critical( "HOLD: no new tasks will run from " + ctime )
        else:
            self.system_hold_now = True
            self.log.critical( "HOLD: no more tasks will run")

    def unset_system_hold( self ):
        self.log.critical( "UNHOLD: new tasks will run when ready")
        self.system_hold_now = False
        self.system_hold_ctime = None

    def will_stop_at( self ):
        return self.stop_time

    def paused( self ):
        return self.system_hold_now

    def will_pause_at( self ):
        return self.system_hold_ctime

    def get_oldest_c_time( self ):
        # return the cycle time of the oldest task
        oldest = 9999887766
        for itask in self.tasks:
            #if itask.state.is_failed():  # uncomment for earliest NON-FAILED 
            #    continue
            if int( itask.c_time ) < int( oldest ):
                oldest = itask.c_time
        return oldest

    def load_from_config ( self, start_time, exclude, include ):
        # load initial system state from configured tasks and start time
        #--
        
        # set clock before using log (affects dummy mode only)
        self.clock.set( start_time )

        #print '\nSTARTING AT ' + start_time + ' FROM CONFIGURED TASK LIST\n'
        self.log.info( 'Loading state from configured task list' )
        # config.task_list = [ taskname1, taskname2, ...]

        task_list = self.config.get('task_list')
        # uniquify in case of accidental duplicates
        task_list = list( set( task_list ) )

        for name in task_list:

            if name in exclude:
                continue

            if len( include ) > 0:
                if name not in include:
                    continue
            
            # create the task-specific logger
            self.create_task_log( name )

            # instantiate the task
            itask = get_object( 'task_classes', name )\
                    ( start_time, self.dummy_mode, self.config, 'waiting', self.submit[ name ], startup=True )

            # check stop time in case the user has set a very quick stop
            if self.stop_time and int( itask.c_time ) > int( self.stop_time ):
                # we've reached the stop time already: delete the new task 
                itask.log( 'WARNING', "STOPPING at configured stop time " + self.stop_time )
                itask.prepare_for_death()
                del itask
 
            else:
                itask.log( 'DEBUG', "connected" )
                self.pyro.connect( itask, ns_obj_name( itask.get_identity(), self.groupname) )
                self.tasks.append( itask )


    def load_from_state_dump( self, filename, no_reset_val ):
        # load initial system state from the configured state dump file
        #--

        print '\nLOADING INITIAL STATE FROM ' + filename + '\n'
        self.log.info( 'Loading initial state from ' + filename )

        # The state dump file format is:
        # system time : <time>
        # OR
        # dummy time : <time>,rate
        # class <classname>: item1=value1, item2=value2, ... 
        # <c_time> : <taskname> : <state>
        # <c_time> : <taskname> : <state>
        #    (and so on)
        # The time format is defined by the clock.reset()
        # task <state> format is defined by task_state.dump()

        FILE = open( filename, 'r' )
        lines = FILE.readlines()
        FILE.close()

        # RESET THE TIME TO THE LATEST DUMPED TIME
        # The state dump file first line is:
        # system time : <time>
        #   OR
        # dummy time : <time>,rate
        line1 = lines[0]
        line1 = line1.rstrip()
        [ time_type, time_string ] = line1.split(' : ')
        if time_type == 'dummy time':
            if not self.dummy_mode:
                raise SystemExit("You can't restart in dummy mode from a real mode state dump")
            
            [ time, rate ] = time_string.split( ',' )
            self.clock.reset( time, rate )

        log_created = {}

        mod = __import__( 'task_classes' )

        # parse each line and create the task it represents
        for line in lines[1:]:
            # strip trailing newlines
            line = line.rstrip( '\n' )

            if re.match( '^class', line ):
                # class variables
                [ left, right ] = line.split( ' : ' )
                [ junk, classname ] = left.split( ' ' ) 
                cls = getattr( mod, classname )
                pairs = right.split( ', ' )
                for pair in pairs:
                    [ item, value ] = pair.split( '=' )
                    cls.set_class_var( item, value )
                 
                continue

            # instance variables
            [ c_time, name, state ] = line.split(' : ')

            # create the task log
            if name not in log_created.keys():
                self.create_task_log( name )
                log_created[ name ] = True

            # instantiate the task object
            itask = get_object( 'task_classes', name )\
                    ( c_time, self.dummy_mode, self.config, state, self.submit[ name ], no_reset=no_reset_val )

            # check stop time in case the user has set a very quick stop
            if self.stop_time and int( itask.c_time ) > int( self.stop_time ):
                # we've reached the stop time already: delete the new task 
                itask.log( 'WARNING', "STOPPING at configured stop time " + self.stop_time )
                itask.prepare_for_death()
                del itask
 
            else:
                itask.log( 'DEBUG', "connected" )
                self.pyro.connect( itask, ns_obj_name( itask.get_identity(), self.groupname) )
                self.tasks.append( itask )

    def no_tasks_running( self ):
        # return True if no tasks are submitted or running
        #--
        for itask in self.tasks:
            if itask.state.is_running() or itask.state.is_submitted():
                return False
        return True

    def all_tasks_finished( self ):
        # return True if all tasks have finished AND spawned
        #--
        for itask in self.tasks:
            if not itask.state.is_finished() or not itask.state.has_spawned():
                return False
        return True


    def negotiate( self ):
        # run time dependency negotiation: tasks attempt to get their
        # prerequisites satisfied by other tasks' outputs.
        #--
    
        # Instead: O(n) BROKERED NEGOTIATION

        self.broker.reset()

        for itask in self.tasks:
            # register task outputs
            self.broker.register( itask )

        # for debugging;            
        # self.broker.dump()

        for itask in self.tasks:
            # get the broker to satisfy tasks prerequisites
            self.broker.negotiate( itask )

        for itask in self.tasks:
            itask.check_requisites()

    def run_tasks( self ):
        # tell each task to run if it is ready
        # unless the system is on hold
        #--
        if self.system_hold_now:
            # general system hold
            self.log.debug( 'not asking any tasks to run (general system hold in place)' )
            return

        for itask in self.tasks:
                if self.system_hold_ctime:
                    if int( itask.c_time ) > int( self.system_hold_ctime ):
                        self.log.debug( 'not asking ' + itask.get_identity() + ' to run (' + self.system_hold_ctime + ' hold in place)' )
                        continue

                current_time = self.clock.get_datetime()
                itask.run_if_ready( current_time )

    def spawn( self ):
        # create new tasks foo(T+1) if foo has not got too far ahead of
        # the slowest task, and if foo(T) spawns
        #--

        # update oldest system cycle time
        oldest_c_time = self.get_oldest_c_time()

        for itask in self.tasks:

            tdiff = cycle_time.decrement( itask.c_time, self.config.get('max_runahead_hours') )
            if int( tdiff ) > int( oldest_c_time ):
                # too far ahead: don't spawn this task.
                itask.log( 'DEBUG', "delaying spawning (too far ahead)" )
                continue

            if itask.spawn():
                itask.log( 'DEBUG', 'spawning')

                # dynamic task object creation by task and module name
                new_task = get_object( 'task_classes', itask.name )\
                        ( itask.next_c_time(), self.dummy_mode, self.config, 'waiting', self.submit[ itask.name ] )
                if self.stop_time and int( new_task.c_time ) > int( self.stop_time ):
                    # we've reached the stop time: delete the new task 
                    new_task.log( 'WARNING', "STOPPING at configured stop time " + self.stop_time )
                    new_task.prepare_for_death()
                    del new_task
                else:
                    # no stop time, or we haven't reached it yet.
                    try:
                        self.pyro.connect( new_task, ns_obj_name( new_task.get_identity(), self.groupname) )
                    except Exception, x:
                        # THIS WILL BE A Pyro NamingError IF THE NEW TASK
                        # ALREADY EXISTS IN THE SYSTEM.
                        print x
                        self.log.critical( new_task.get_identity() + ' cannot be added!' )

                    else:
                        new_task.log('DEBUG', "connected" )
                        self.tasks.append( new_task )


    def dump_state( self, new_file = False ):
        filename = self.config.get('state_dump_file') 
        if new_file:
            filename += '.' + self.clock.dump_to_str()

        # system time
        FILE = open( filename, 'w' )
        if self.dummy_mode:
            FILE.write( 'dummy time : ' + self.clock.dump_to_str() + ',' + str( self.clock.get_rate()) + '\n' )
        else:
            FILE.write( 'system time : ' + self.clock.dump_to_str() + '\n' )

        # task class variables
        for name in self.config.get('task_list'):
            mod = __import__( 'task_classes' )
            cls = getattr( mod, name )
            cls.dump_class_vars( FILE )
            
        # task instance variables
        for itask in self.tasks:
            itask.dump_state( FILE )
        FILE.close()
        # return the filename (minus path)
        return os.path.basename( filename )

    def earliest_unspawned( self ):
        all_spawned = True
        earliest_unspawned = None
        for itask in self.tasks:
            if not itask.state.has_spawned():
                all_spawned = False
                if not earliest_unspawned:
                    earliest_unspawned = itask.c_time
                elif int( itask.c_time ) < int( earliest_unspawned ):
                    earliest_unspawned = itask.c_time

        return [ all_spawned, earliest_unspawned ]

    def earliest_unsatisfied( self ):
        # find the earliest unsatisfied task
        all_satisfied = True
        earliest_unsatisfied = None
        for itask in self.tasks:
            if not itask.prerequisites.all_satisfied():
                all_satisfied = False
                if not earliest_unsatisfied:
                    earliest_unsatisfied = itask.c_time
                elif int( itask.c_time ) < int( earliest_unsatisfied ):
                    earliest_unsatisfied = itask.c_time

        return [ all_satisfied, earliest_unsatisfied ]

    def earliest_unfinished( self ):
        # find the earliest unfinished task
        all_finished = True
        earliest_unfinished = None
        for itask in self.tasks:
            #if itask.state.is_failed():  # uncomment for earliest NON-FAILED
            #    continue
            if not itask.state.is_finished():
                all_finished = False
                if not earliest_unfinished:
                    earliest_unfinished = itask.c_time
                elif int( itask.c_time ) < int( earliest_unfinished ):
                    earliest_unfinished = itask.c_time

        return [ all_finished, earliest_unfinished ]

    def cleanup( self ):
        # Delete tasks that are no longer needed, i.e. those that:
        #   (1) have finished and spawned, 
        #       AND
        #   (2) are no longer needed to satisfy prerequisites.

        # Also, do not delete any task with the same cycle time as a
        # failed task, because these could be needed to satisfy the
        # failed task's prerequisites when it is re-submitted after the
        # problem is fixed.
        #--

        # A/ QUICK DEATH TASKS
        # Tasks with 'quick_death = True', by definition they have only
        # cotemporal downstream dependents, so they are no longer needed
        # to satisfy prerequisites once all their cotemporal peers have
        # finished. The only complication is that new cotemporal peers
        # can appear, in principle, so long as there are unspawned
        # tasks with earlier cycle times. Therefore they are spent 
        # IF finished-and-spawned AND there are no unspawned tasks
        # at the same cycle time or earlier.
        #--

        # times of any failed tasks. 
        failed_rt = {}
        for itask in self.tasks:
            if itask.state.is_failed():
                failed_rt[ itask.c_time ] = True

        # time of the earliest unspawned task
        [all_spawned, earliest_unspawned] = self.earliest_unspawned()
        if all_spawned:
            self.log.debug( "all tasks spawned")
        else:
            self.log.debug( "earliest unspawned task at: " + earliest_unspawned )

        # find the spent quick death tasks
        spent = []
        for itask in self.tasks:
            if not itask.done():
                continue
            if itask.c_time in failed_rt.keys():
                continue

            if itask.quick_death and not all_spawned: 
                if all_spawned or int( itask.c_time ) < int( earliest_unspawned ):
                    spent.append( itask )
 
        # delete the spent quick death tasks
        for itask in spent:
            self.tasks.remove( itask )
            self.pyro.disconnect( itask )
            itask.log( 'NORMAL', "disconnected (spent; quickdeath)" )
            itask.prepare_for_death()

        del spent

        # B/ THE GENERAL CASE
        # No finished-and-spawned task that is later than the earliest
        # unsatisfied task can be deleted yet because it may still be
        # needed to satisfy new tasks that may appear when earlier (but
        # currently unsatisfied) tasks spawn. Therefore only
        # finished-and-spawned tasks that are earlier than the
        # earliest unsatisfied task are candidates for deletion. Of
        # these, we can delete a task only IF another spent instance of
        # it exists at a later time (but still earlier than the earliest
        # unsatisfied task) 

        # BUT while the above paragraph is correct, the method can fail
        # at restart: just before shutdown, when all running tasks have
        # finished, we briefly have 'all tasks satisfied', which allows 
        # deletion without the 'earliest unsatisfied' limit, and can
        # result in deletion of finished tasks that are still required
        # to satisfy others after a restart.

        # THEREFORE the correct deletion cutoff is 'earliest unfinished'
        # (members of which will remain in, or be reset to, the waiting
        # state on a restart. The only way to use 'earliest unsatisfied'
        # over a restart would be to record the state of all
        # prerequisites for each task in the state dump (which may be a
        # good thing to do?)

        [ all_finished, earliest_unfinished ] = self.earliest_unfinished()
        if all_finished:
            self.log.debug( "all tasks finished" )
        else:
            self.log.debug( "earliest unfinished: " + earliest_unfinished )

         # find candidates for deletion
        candidates = {}
        for itask in self.tasks:

            if not itask.done():
                continue

            if itask.c_time in failed_rt.keys():
                continue

            #if not all_satisfied:
            #    if int( itask.c_time ) >= int( earliest_unsatisfied ):
            #        continue
            if not all_finished:
                if int( itask.c_time ) >= int( earliest_unfinished ):
                    continue
            
            if itask.c_time in candidates.keys():
                candidates[ itask.c_time ].append( itask )
            else:
                candidates[ itask.c_time ] = [ itask ]

        # searching from newest tasks to oldest, after the earliest
        # unsatisfied task, find any done task types that appear more
        # than once - the second or later occurrences can be deleted.
        ctimes = candidates.keys()
        ctimes.sort( key = int, reverse = True )
        seen = {}
        spent = []
        for rt in ctimes:
            #if not all_satisfied:
            #    if int( rt ) >= int( earliest_unsatisfied ):
            #        continue
            if not all_finished:
                if int( rt ) >= int( earliest_unfinished ):
                    continue
            
            for itask in candidates[ rt ]:
                try:
                    # oneoff non quick death tasks need to nominate 
                    # the task type that will take over from them.
                    # (otherwise they will never be deleted).
                    name = itask.oneoff_follow_on
                except AttributeError:
                    name = itask.name

                if name in seen.keys():
                    # already seen this guy, so he's spent
                    spent.append( itask )
                else:
                    # first occurence
                    seen[ name ] = True
            
        # now delete the spent tasks
        for itask in spent:
            self.tasks.remove( itask )
            self.pyro.disconnect( itask )
            itask.log( 'NORMAL', "disconnected (spent; general)" )
            itask.prepare_for_death()

        del spent


    def reset_task( self, task_id ):
        self.log.warning( 'pre-reset state dump: ' + self.dump_state( new_file = True ))
        found = False
        for itask in self.tasks:
            if itask.get_identity() == task_id:
                found = True
                break

        if found:
            itask.log( 'WARNING', "resetting to waiting state" )
            itask.state.set_status( 'waiting' )
            itask.prerequisites.set_all_unsatisfied()
            itask.outputs.set_all_incomplete()
        else:
            self.log.warning( "task to reset not found: " + task_id )

    def reset_task_to_ready( self, task_id ):
        self.log.warning( 'pre-reset state dump: ' + self.dump_state( new_file = True ))
        found = False
        for itask in self.tasks:
            if itask.get_identity() == task_id:
                found = True
                break

        if found:
            itask.log( 'WARNING', "resetting to ready state" )
            itask.state.set_status( 'waiting' )
            itask.prerequisites.set_all_satisfied()
            itask.outputs.set_all_incomplete()
        else:
            self.log.warning( "task to reset not found: " + task_id )

    def reset_task_to_finished( self, task_id ):
        self.log.warning( 'pre-reset state dump: ' + self.dump_state( new_file = True ))
        found = False
        for itask in self.tasks:
            if itask.get_identity() == task_id:
                found = True
                break

        if found:
            itask.log( 'WARNING', "resetting to finished state" )
            itask.state.set_status( 'finished' )
            itask.prerequisites.set_all_satisfied()
            itask.outputs.set_all_complete()
        else:
            self.log.warning( "task to reset not found: " + task_id )

    def insertion( self, ins_id ):
        # for remote insertion of a new task, or task group
        self.log.warning( 'pre-insertion state dump: ' + self.dump_state( new_file = True ))
        try:

            ( ins_name, ins_ctime ) = ins_id.split( '%' )

            print
            if ins_name in self.config.get( 'task_list' ):
                print "INSERTING A TASK"
                ids = [ ins_id ]

            elif ins_name in ( self.config.get( 'task_groups' ) ).keys():
                print "INSERTING A GROUP OF TASKS"

                tasknames = self.config.get( 'task_groups')[ins_name]

                ids = []
                for name in tasknames:
                    ids.append( name + '%' + ins_ctime )
            else:
                # THIS WILL BE CAUGHT BY THE TRY BLOCK
                raise SystemExit("no such task or group")


            for task_id in ids:
                [ name, c_time ] = task_id.split( '%' )

                # instantiate the task object
                itask = get_object( 'task_classes', name )\
                        ( c_time, self.dummy_mode, self.config, 'waiting', self.submit[ name ] )

                if itask.instance_count == 1:
                    # first task of its type, so create the log
                    log = logging.getLogger( 'main.' + name )
                    pimp_my_logger.pimp_it( log, name, self.config.get('logging_dir'), \
                            self.config.get('logging_level'), self.dummy_mode, self.clock )
 
                # the initial task cycle time can be altered during
                # creation, so we have to create the task before
                # checking if stop time has been reached.
                skip = False
                if self.stop_time:
                    if int( itask.c_time ) > int( self.stop_time ):
                        itask.log( 'WARNING', " STOPPING at " + self.stop_time )
                        itask.prepare_for_death()
                        del itask
                        skip = True

                if not skip:
                    itask.log( 'DEBUG', "connected" )
                    self.pyro.connect( itask, ns_obj_name( itask.get_identity(), self.groupname) )
                    self.tasks.append( itask )

        #except NamingError, e:
        except Exception, e:
            # A failed remote insertion should not bring the system
            # down.  This catches requests to insert undefined tasks and
            # task groups. Is there any reason to use the more specific
            # Pyro.errors.NamingError here?
            print 'INSERTION FAILED:', e
            print 
            # now carry one operating!

    def find_cotemporal_dependees( self, parent ):
        # recursively find the group of all cotemporal tasks that depend
        # directly or indirectly on parent

        deps = {}
        for itask in self.tasks:
            if itask.c_time != parent.c_time:
                # not cotemporal
                continue

            if itask.prerequisites.will_satisfy_me( parent.outputs, parent.get_identity() ):
                #print 'dependee: ' + itask.get_identity()
                deps[ itask ] = True

        for itask in deps:
            res = self.find_cotemporal_dependees( itask )
            deps = self.addDicts( res, deps ) 

        deps[ parent ] = True

        return deps


    def addDicts(self, a, b):
        c = {}
        for item in a:
            c[item] = a[item]
            for item in b:
                c[item] = b[item]
        return c


    def purge( self, id, stop ):
        # get a task and, recursively, its dependants down to the given
        # stop time, to spawn and die.

        self.log.warning( 'pre-purge state dump: ' + self.dump_state( new_file = True ))

        # find the task
        found = False
        for itask in self.tasks:
            if itask.get_identity() == id:
                found = True
                next = itask.next_c_time()
                name = itask.name
                break

        if not found:
            self.log.warning( 'task to purge not found: ' + id )
            return

        # find then spawn and kill all cotemporal dependees
        condemned = self.find_cotemporal_dependees( itask )
        # this returns tasks, we want task names
        # TO DO: GET RID OF THE MIDDLE MAN HERE
        cond = {}
        for itask in condemned:
            cond[ itask.get_identity() ] = True
        
        self.spawn_and_die( cond )

        # now do the same for the next instance of the task
        if int( next ) <= int( stop ):
            self.purge( name + '%' + next, stop )

    def waiting_contact_task_ready( self, current_time ):
        result = False
        for itask in self.tasks:
            #print itask.get_identity(), current_time
            if itask.ready_to_run(current_time):
                result = True
                break
        return result

    def kill_cycle( self, ctime ):
        # kill all tasks currently at ctime
        task_ids = []
        for itask in self.tasks:
            if itask.c_time == ctime: # and itask.get_status() == 'waiting':
                task_ids.append( itask.get_identity() )

        self.kill( task_ids )

    def spawn_and_die_cycle( self, ctime ):
        # spawn and kill all tasks currently at ctime
        task_ids = {}
        for itask in self.tasks:
            if itask.c_time == ctime: # and itask.get_status() == 'waiting':
                task_ids[ itask.get_identity() ] = True

        self.spawn_and_die( task_ids )

    def spawn_and_die( self, task_ids ):
        # spawn and kill all tasks in task_ids.keys()

        self.log.warning( 'pre-spawn-and-die state dump: ' + self.dump_state( new_file = True ))

        for id in task_ids:
            # find the task
            found = False
            itask = None
            for t in self.tasks:
                if t.get_identity() == id:
                    found = True
                    itask = t
                    break

            if not found:
                self.log.warning( "task to kill not found: " + id )
                return

            itask.log( 'DEBUG', "killing myself by remote request" )

            if not itask.state.has_spawned():
                # forcibly spawn the task and create its successor
                itask.state.set_spawned()
                itask.log( 'DEBUG', 'forced spawning' )
                # TO DO: the following should reuse code in spawn()?
                # dynamic task object creation by task and module name
                new_task = get_object( 'task_classes', itask.name )\
                        ( itask.next_c_time(), self.dummy_mode, self.config, 'waiting', self.submit[ itask.name ] )
                if self.stop_time and int( new_task.c_time ) > int( self.stop_time ):
                    # we've reached the stop time: delete the new task 
                    new_task.log( 'WARNING', 'STOPPING at configured stop time' )
                    new_task.prepare_for_death()
                    del new_task
                else:
                    # no stop time, or we haven't reached it yet.
                    self.pyro.connect( new_task, ns_obj_name( new_task.get_identity(), self.groupname) )
                    new_task.log( 'DEBUG', 'connected' )
                    self.tasks.append( new_task )

            else:
                # already spawned: the successor already exists
                pass

            # now kill the task
            self.tasks.remove( itask )
            self.pyro.disconnect( itask )
            itask.log( 'WARNING', "disconnected (remote request)" )
            itask.prepare_for_death()
            del itask

    def kill( self, task_ids ):
        # kill without spawning all tasks in task_ids
        self.log.warning( 'pre-kill state dump: ' + self.dump_state( new_file = True ))
        for id in task_ids:
            # find the task
            found = False
            itask = None
            for t in self.tasks:
                if t.get_identity() == id:
                    found = True
                    itask = t
                    break

            if not found:
                self.log.warning( "task to kill not found: " + id )
                return

            itask.log( 'DEBUG', "killing myself by remote request" )

            # now kill the task
            self.tasks.remove( itask )
            self.pyro.disconnect( itask )
            itask.log( 'WARNING', "disconnected (remote request)" )
            itask.prepare_for_death()
            del itask
