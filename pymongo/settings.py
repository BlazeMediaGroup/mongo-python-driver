# Copyright 2014 MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you
# may not use this file except in compliance with the License.  You
# may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.  See the License for the specific language governing
# permissions and limitations under the License.

"""Represent MongoClient's configuration."""

import threading

from pymongo import common, monitor, pool
from pymongo.cluster_description import CLUSTER_TYPE
from pymongo.pool import PoolOptions
from pymongo.server_description import ServerDescription


class ClusterSettings(object):
    def __init__(
        self,
        seeds=None,
        set_name=None,
        server_wait_time=None,
        pool_class=None,
        pool_options=None,
        monitor_class=monitor.Monitor,
        condition_class=threading.Condition,
        heartbeat_frequency=common.HEARTBEAT_FREQUENCY,
    ):
        """Represent MongoClient's configuration.

        Take a list of (host, port) pairs and optional replica set name.
        """
        self._seeds = seeds or [('localhost', 27017)]
        self._set_name = set_name
        self._server_wait_time = server_wait_time or 5  # Seconds.
        self._pool_class = pool_class or pool.Pool
        self._pool_options = pool_options or PoolOptions()
        self._monitor_class = monitor_class or monitor.Monitor
        self._condition_class = condition_class or threading.Condition
        self._heartbeat_frequency = heartbeat_frequency
        self._direct = (len(self._seeds) == 1 and not set_name)

    @property
    def seeds(self):
        """List of server addresses."""
        return self._seeds

    @property
    def set_name(self):
        return self._set_name

    @property
    def server_wait_time(self):
        return self._server_wait_time

    @property
    def pool_class(self):
        return self._pool_class

    @property
    def pool_options(self):
        return self._pool_options

    @property
    def monitor_class(self):
        return self._monitor_class

    @property
    def condition_class(self):
        return self._condition_class

    @property
    def heartbeat_frequency(self):
        return self._heartbeat_frequency

    @property
    def direct(self):
        """Connect directly to a single server, or use a set of servers?

        True if there is one seed and no set_name.
        """
        return self._direct

    def get_cluster_type(self):
        if self.direct:
            return CLUSTER_TYPE.Single
        elif self.set_name is not None:
            return CLUSTER_TYPE.ReplicaSetNoPrimary
        else:
            return CLUSTER_TYPE.Unknown

    def get_server_descriptions(self):
        """Initial dict of (address, ServerDescription) for all seeds."""
        return dict([
            (address, ServerDescription(address))
            for address in self.seeds])


class SocketSettings(object):
    # TODO.
    pass
