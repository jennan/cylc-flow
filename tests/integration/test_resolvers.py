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

import logging
from typing import AsyncGenerator, Callable
from unittest.mock import Mock

import pytest

from cylc.flow.data_store_mgr import EDGES, TASK_PROXIES
from cylc.flow.id import Tokens
from cylc.flow.network.resolvers import Resolvers
from cylc.flow.scheduler import Scheduler
from cylc.flow.workflow_status import StopMode


@pytest.fixture
def flow_args():
    return {
        'workflows': [],
        'exworkflows': [],
    }


@pytest.fixture
def node_args():
    return {
        'workflows': [],
        'exworkflows': [],
        'ids': [],
        'exids': [],
        'states': [],
        'exstates': [],
        'mindepth': -1,
        'maxdepth': -1,
    }


@pytest.fixture(scope='module')
async def mock_flow(
    mod_flow: Callable[..., str],
    mod_scheduler: Callable[..., Scheduler],
    mod_start,
) -> AsyncGenerator[Scheduler, None]:
    ret = Mock()
    ret.reg = mod_flow({
        'scheduler': {
            'allow implicit tasks': True
        },
        'scheduling': {
            'initial cycle point': '2000',
            'dependencies': {
                'R1': 'prep => foo',
                'PT12H': 'foo[-PT12H] => foo => bar'
            }
        }
    })

    ret.schd = mod_scheduler(ret.reg, paused_start=True)
    async with mod_start(ret.schd):
        ret.schd.pool.release_runahead_tasks()
        ret.schd.data_store_mgr.initiate_data_model()

        ret.owner = ret.schd.owner
        ret.name = ret.schd.workflow
        ret.id = list(ret.schd.data_store_mgr.data.keys())[0]
        ret.resolvers = Resolvers(
            ret.schd.data_store_mgr,
            schd=ret.schd
        )
        ret.data = ret.schd.data_store_mgr.data[ret.id]
        ret.node_ids = [
            node.id
            for node in ret.data[TASK_PROXIES].values()
        ]
        ret.edge_ids = [
            edge.id
            for edge in ret.data[EDGES].values()
        ]

        yield ret


async def test_get_workflows(mock_flow, flow_args):
    """Test method returning workflow messages satisfying filter args."""
    flow_args['workflows'].append({
        'user': mock_flow.owner,
        'workflow': mock_flow.name,
        'workflow_sel': None,
    })
    flow_msgs = await mock_flow.resolvers.get_workflows(flow_args)
    assert len(flow_msgs) == 1


async def test_get_nodes_all(mock_flow, node_args):
    """Test method returning workflow(s) node message satisfying filter args.
    """
    node_args['workflows'].append({
        'user': mock_flow.owner,
        'workflow': mock_flow.name,
        'workflow_sel': None,
    })
    node_args['states'].append('failed')
    nodes = await mock_flow.resolvers.get_nodes_all(TASK_PROXIES, node_args)
    assert len(nodes) == 0
    node_args['states'] = []
    node_args['ids'].append(Tokens(mock_flow.node_ids[0]))
    nodes = [
        n for n in await mock_flow.resolvers.get_nodes_all(
            TASK_PROXIES, node_args)
        if n in mock_flow.data[TASK_PROXIES].values()
    ]
    assert len(nodes) == 1


async def test_get_nodes_by_ids(mock_flow, node_args):
    """Test method returning workflow(s) node messages
    who's ID is a match to any given."""
    node_args['workflows'].append({
        'user': mock_flow.owner,
        'workflow': mock_flow.name,
        'workflow_sel': None
    })
    nodes = await mock_flow.resolvers.get_nodes_by_ids(TASK_PROXIES, node_args)
    assert len(nodes) == 0

    node_args['native_ids'] = mock_flow.node_ids
    nodes = [
        n
        for n in await mock_flow.resolvers.get_nodes_by_ids(
            TASK_PROXIES, node_args
        )
        if n in mock_flow.data[TASK_PROXIES].values()
    ]
    assert len(nodes) > 0


async def test_get_node_by_id(mock_flow, node_args):
    """Test method returning a workflow node message
    who's ID is a match to that given."""
    node_args['id'] = Tokens(
        user='me',
        workflow='mine',
        cycle='20500808T00',
        task='jin',
    ).id
    node_args['workflows'].append({
        'user': mock_flow.owner,
        'workflow': mock_flow.name,
        'workflow_sel': None
    })
    node = await mock_flow.resolvers.get_node_by_id(TASK_PROXIES, node_args)
    assert node is None
    node_args['id'] = mock_flow.node_ids[0]
    node = await mock_flow.resolvers.get_node_by_id(TASK_PROXIES, node_args)
    assert node in mock_flow.data[TASK_PROXIES].values()


async def test_get_edges_all(mock_flow, flow_args):
    """Test method returning all workflow(s) edges."""
    edges = [
        e
        for e in await mock_flow.resolvers.get_edges_all(flow_args)
        if e in mock_flow.data[EDGES].values()
    ]
    assert len(edges) > 0


async def test_get_edges_by_ids(mock_flow, node_args):
    """Test method returning workflow(s) edge messages
    who's ID is a match to any given edge IDs."""
    edges = await mock_flow.resolvers.get_edges_by_ids(node_args)
    assert len(edges) == 0
    node_args['native_ids'] = mock_flow.edge_ids
    edges = [
        e
        for e in await mock_flow.resolvers.get_edges_by_ids(node_args)
        if e in mock_flow.data[EDGES].values()
    ]
    assert len(edges) > 0


async def test_mutator(mock_flow, flow_args):
    """Test the mutation method."""
    flow_args['workflows'].append({
        'user': mock_flow.owner,
        'workflow': mock_flow.name,
        'workflow_sel': None
    })
    args = {}
    meta = {}
    response = await mock_flow.resolvers.mutator(
        None,
        'pause',
        flow_args,
        args,
        meta
    )
    assert response[0]['id'] == mock_flow.id


async def test_mutation_mapper(mock_flow):
    """Test the mapping of mutations to internal command methods."""
    meta = {}
    response = await mock_flow.resolvers._mutation_mapper('pause', {}, meta)
    assert response is None
    with pytest.raises(ValueError):
        await mock_flow.resolvers._mutation_mapper('non_exist', {}, meta)


@pytest.mark.asyncio
async def test_stop(
    one: Scheduler, run: Callable, log_filter: Callable,
):
    """Test the stop resolver."""
    async with run(one) as log:
        resolvers = Resolvers(
            one.data_store_mgr,
            schd=one
        )
        resolvers.stop(StopMode.REQUEST_CLEAN)
        one.process_command_queue()
        assert log_filter(
            log, level=logging.INFO, contains="Command succeeded: stop"
        )
        assert one.stop_mode == StopMode.REQUEST_CLEAN
