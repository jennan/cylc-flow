#!/usr/bin/env python3

# THIS FILE IS PART OF THE CYLC WORKFLOW ENGINE.
# Copyright (C) NIWA & British Crown (Met Office) & Contributors.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""cylc set-outputs [OPTIONS] ARGS

Artificially mark task outputs as completed and spawn downstream tasks that
depend on those outputs. By default it marks tasks as succeeded.

This allows you to manually intervene with Cylc's scheduling algorithm by
artificially satisfying outputs of tasks.

If a flow number is given, the child tasks will start (or continue) that flow,
otherwise no reflow will occur.

Examples:
  # For example, for the following dependency graph:

  R1 = '''
     a => b & c => d
     foo:x => bar => baz
  '''

  # spawn b.1 and c.1, but d.1 will not subsequently run
  $ cylc set-outputs my_flow//1/a

  # spawn b.1 and c.1 as flow 2, followed by d.1
  $ cylc set-outputs --flow=2 my_flow//1/a

  # spawn bar.1 as flow 3, followed by baz.1
  $ cylc set-outputs --flow=3 --output=x my_flow//1/foo

Use --output multiple times to spawn off of several outputs at once.

"""

from functools import partial
from optparse import Values

from cylc.flow.network.client_factory import get_client
from cylc.flow.network.multi import call_multi
from cylc.flow.option_parsers import CylcOptionParser as COP
from cylc.flow.terminal import cli_function

MUTATION = '''
mutation (
  $wFlows: [WorkflowID]!,
  $tasks: [NamespaceIDGlob]!,
  $outputs: [String],
  $flowNum: Int,
) {
  setOutputs (
    workflows: $wFlows,
    tasks: $tasks,
    outputs: $outputs,
    flowNum: $flowNum,
  ) {
    result
  }
}
'''


def get_option_parser():
    parser = COP(
        __doc__,
        comms=True,
        multitask_nocycles=True,
        argdoc=[('ID [ID ...]', 'Cycle/Family/Task ID(s)')],
    )

    parser.add_option(
        "-o", "--output", metavar="OUTPUT",
        help="Set OUTPUT (default \"succeeded\") completed.",
        action="append", default=None, dest="outputs")

    parser.add_option(
        "-f", "--flow", metavar="FLOW",
        help="Number of the flow to attribute the outputs.",
        action="store", default=None, dest="flow_num")

    return parser


async def run(options: 'Values', workflow: str, *ids) -> None:
    pclient = get_client(workflow, timeout=options.comms_timeout)

    mutation_kwargs = {
        'request_string': MUTATION,
        'variables': {
            'wFlows': [workflow],
            'tasks': list(ids),
            'outputs': options.outputs,
            'flowNum': options.flow_num
        }
    }

    await pclient.async_request('graphql', mutation_kwargs)


@cli_function(get_option_parser)
def main(parser: COP, options: 'Values', *ids) -> None:
    call_multi(
        partial(run, options),
        *ids,
    )
