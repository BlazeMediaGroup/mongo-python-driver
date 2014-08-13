# Copyright 2009-2014 MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test the mongo_client module."""

import contextlib
import datetime
import os
import threading
import socket
import sys
import time

sys.path[0:0] = [""]

from bson.py3compat import thread, u
from bson.son import SON
from bson.tz_util import utc
from pymongo.mongo_client import MongoClient
from pymongo.database import Database
from pymongo.pool import SocketInfo
from pymongo import auth
from pymongo.errors import (AutoReconnect,
                            ConfigurationError,
                            ConnectionFailure,
                            InvalidName,
                            OperationFailure,
                            CursorNotFound)
from test import (client_context,
                  connection_string,
                  host,
                  pair,
                  port,
                  SkipTest,
                  unittest,
                  IntegrationTest,
                  db_pwd,
                  db_user)
from test.pymongo_mocks import MockClient
from test.utils import (assertRaisesExactly,
                        delay,
                        get_client,
                        remove_all_users,
                        server_is_master_with_slave,
                        TestRequestMixin,
                        _TestLazyConnectMixin,
                        lazy_client_trial,
                        get_pool,
                        one,
                        connected,
                        wait_until)


class ClientUnitTest(unittest.TestCase, TestRequestMixin):
    """MongoClient tests that don't require a server."""

    @classmethod
    def setUpClass(cls):
        cls.client = MongoClient(host, port, _connect=False)

    def test_types(self):
        self.assertRaises(TypeError, MongoClient, 1)
        self.assertRaises(TypeError, MongoClient, 1.14)
        self.assertRaises(TypeError, MongoClient, "localhost", "27017")
        self.assertRaises(TypeError, MongoClient, "localhost", 1.14)
        self.assertRaises(TypeError, MongoClient, "localhost", [])

        self.assertRaises(ConfigurationError, MongoClient, [])

    def test_get_db(self):
        def make_db(base, name):
            return base[name]

        self.assertRaises(InvalidName, make_db, self.client, "")
        self.assertRaises(InvalidName, make_db, self.client, "te$t")
        self.assertRaises(InvalidName, make_db, self.client, "te.t")
        self.assertRaises(InvalidName, make_db, self.client, "te\\t")
        self.assertRaises(InvalidName, make_db, self.client, "te/t")
        self.assertRaises(InvalidName, make_db, self.client, "te st")

        self.assertTrue(isinstance(self.client.test, Database))
        self.assertEqual(self.client.test, self.client["test"])
        self.assertEqual(self.client.test, Database(self.client, "test"))

    def test_iteration(self):
        def iterate():
            [a for a in self.client]

        self.assertRaises(TypeError, iterate)

    def test_get_default_database(self):
        c = MongoClient("mongodb://%s:%d/foo" % (host, port), _connect=False)
        self.assertEqual(Database(c, 'foo'), c.get_default_database())

    def test_get_default_database_error(self):
        # URI with no database.
        c = MongoClient("mongodb://%s:%d/" % (host, port), _connect=False)
        self.assertRaises(ConfigurationError, c.get_default_database)

    def test_get_default_database_with_authsource(self):
        # Ensure we distinguish database name from authSource.
        uri = "mongodb://%s:%d/foo?authSource=src" % (host, port)
        c = MongoClient(uri, _connect=False)
        self.assertEqual(Database(c, 'foo'), c.get_default_database())


class TestClient(IntegrationTest, TestRequestMixin):

    @classmethod
    def setUpClass(cls):
        super(TestClient, cls).setUpClass()
        cls.client = client_context.client

    def test_constants(self):
        # Set bad defaults.
        MongoClient.HOST = "somedomainthatdoesntexist.org"
        MongoClient.PORT = 123456789
        with self.assertRaises(AutoReconnect):
            connected(MongoClient(serverWaitTimeMS=100))

        # Override the defaults. No error.
        connected(MongoClient(host, port))

        # Set good defaults.
        MongoClient.HOST = host
        MongoClient.PORT = port

        # No error.
        connected(MongoClient())

    def assertIsInstance(self, obj, cls, msg=None):
        """Backport from Python 2.7."""
        if not isinstance(obj, cls):
            standardMsg = '%r is not an instance of %r' % (obj, cls)
            self.fail(self._formatMessage(msg, standardMsg))

    def test_init_disconnected(self):
        c = MongoClient(host, port, _connect=False)

        self.assertIsInstance(c.is_primary, bool)
        self.assertIsInstance(c.is_mongos, bool)
        self.assertIsInstance(c.max_pool_size, int)
        self.assertIsInstance(c.nodes, frozenset)
        self.assertEqual(dict, c.get_document_class())
        self.assertIsInstance(c.tz_aware, bool)
        self.assertIsInstance(c.max_bson_size, int)
        self.assertIsInstance(c.min_wire_version, int)
        self.assertIsInstance(c.max_wire_version, int)
        self.assertIsInstance(c.max_write_batch_size, int)
        self.assertEqual(None, c.host)
        self.assertEqual(None, c.port)

        c.pymongo_test.command('ismaster')  # Auto-connect.
        self.assertEqual(host, c.host)
        self.assertEqual(port, c.port)

        if client_context.version.at_least(2, 5, 4, -1):
            self.assertTrue(c.max_wire_version > 0)
        else:
            self.assertEqual(c.max_wire_version, 0)
        self.assertTrue(c.min_wire_version >= 0)

        bad_host = "somedomainthatdoesntexist.org"
        c = MongoClient(bad_host, port, serverWaitTimeMS=1)
        self.assertRaises(ConnectionFailure, c.pymongo_test.test.find_one)

    def test_init_disconnected_with_auth(self):
        uri = "mongodb://user:pass@somedomainthatdoesntexist"
        c = MongoClient(uri, serverWaitTimeMS=1)
        self.assertRaises(ConnectionFailure, c.pymongo_test.test.find_one)

    def test_equality(self):
        c = connected(MongoClient(host, port))

        # ClientContext.client is constructed as MongoClient(host, port)
        self.assertEqual(self.client, c)

        # Explicitly test inequality
        self.assertFalse(self.client != c)

    def test_host_w_port(self):
        with self.assertRaises(AutoReconnect):
            connected(MongoClient("%s:1234567", serverWaitTimeMS=1))

    def test_repr(self):
        # Making host a str avoids the 'u' prefix in Python 2, so the repr is
        # the same in Python 2 and 3.
        self.assertEqual(repr(MongoClient(str(host), port)),
                         "MongoClient('%s', %d)" % (host, port))

    def test_getters(self):
        self.assertEqual(self.client.host, host)
        self.assertEqual(self.client.port, port)
        self.assertEqual(set([(host, port)]), self.client.nodes)

    def test_database_names(self):
        self.client.pymongo_test.test.save({"dummy": u("object")})
        self.client.pymongo_test_mike.test.save({"dummy": u("object")})

        dbs = self.client.database_names()
        self.assertTrue("pymongo_test" in dbs)
        self.assertTrue("pymongo_test_mike" in dbs)

    def test_drop_database(self):
        self.assertRaises(TypeError, self.client.drop_database, 5)
        self.assertRaises(TypeError, self.client.drop_database, None)

        raise SkipTest("This test often fails due to SERVER-2329")

        self.client.pymongo_test.test.save({"dummy": u("object")})
        dbs = self.client.database_names()
        self.assertTrue("pymongo_test" in dbs)
        self.client.drop_database("pymongo_test")
        dbs = self.client.database_names()
        self.assertTrue("pymongo_test" not in dbs)

        self.client.pymongo_test.test.save({"dummy": u("object")})
        dbs = self.client.database_names()
        self.assertTrue("pymongo_test" in dbs)
        self.client.drop_database(self.client.pymongo_test)
        dbs = self.client.database_names()
        self.assertTrue("pymongo_test" not in dbs)

    def test_copy_db(self):
        c = self.client
        # Due to SERVER-2329, databases may not disappear
        # from a master in a master-slave pair.
        if server_is_master_with_slave(c):
            raise SkipTest("SERVER-2329")
        if (client_context.version.at_least(2, 6, 0) and
                client_context.is_mongos and client_context.auth_enabled):
            raise SkipTest("Need mongos >= 2.6.0 to test with authentication")
        # We test copy twice; once starting in a request and once not. In
        # either case the copy should succeed (because it starts a request
        # internally) and should leave us in the same state as before the copy.
        c.start_request()

        self.assertRaises(TypeError, c.copy_database, 4, "foo")
        self.assertRaises(TypeError, c.copy_database, "foo", 4)

        self.assertRaises(InvalidName, c.copy_database, "foo", "$foo")

        c.pymongo_test.test.drop()
        c.drop_database("pymongo_test1")
        c.drop_database("pymongo_test2")
        self.assertFalse("pymongo_test1" in c.database_names())
        self.assertFalse("pymongo_test2" in c.database_names())

        c.pymongo_test.test.insert({"foo": "bar"})

        c.copy_database("pymongo_test", "pymongo_test1")
        # copy_database() didn't accidentally end the request
        self.assertTrue(c.in_request())

        self.assertTrue("pymongo_test1" in c.database_names())
        self.assertEqual("bar", c.pymongo_test1.test.find_one()["foo"])

        c.end_request()
        self.assertFalse(c.in_request())

        c.copy_database("pymongo_test", "pymongo_test2")
        # copy_database() didn't accidentally restart the request
        self.assertFalse(c.in_request())

        self.assertTrue("pymongo_test2" in c.database_names())
        self.assertEqual("bar", c.pymongo_test2.test.find_one()["foo"])

        # See SERVER-6427 for mongos
        if not client_context.is_mongos and client_context.auth_enabled:
            c.drop_database("pymongo_test1")

            c.admin.add_user("admin", "password")
            auth_c = MongoClient(host, port)
            auth_c.admin.authenticate("admin", "password")
            try:
                auth_c.pymongo_test.add_user("mike", "password")

                self.assertRaises(OperationFailure, auth_c.copy_database,
                                  "pymongo_test", "pymongo_test1",
                                  username="foo", password="bar")
                self.assertFalse("pymongo_test1" in auth_c.database_names())

                self.assertRaises(OperationFailure, auth_c.copy_database,
                                  "pymongo_test", "pymongo_test1",
                                  username="mike", password="bar")
                self.assertFalse("pymongo_test1" in auth_c.database_names())

                auth_c.copy_database("pymongo_test", "pymongo_test1",
                                     username="mike", password="password")
                self.assertTrue("pymongo_test1" in auth_c.database_names())
                self.assertEqual("bar",
                                 auth_c.pymongo_test1.test.find_one()["foo"])
            finally:
                # Cleanup
                remove_all_users(c.pymongo_test)
                c.admin.remove_user("admin")

    def test_disconnect(self):
        coll = self.client.pymongo_test.bar

        self.client.disconnect()
        self.client.disconnect()

        coll.count()

        self.client.disconnect()
        self.client.disconnect()

        coll.count()

    def test_from_uri(self):
        self.assertEqual(
            self.client,
            connected(MongoClient("mongodb://%s:%d" % (host, port))))

    @client_context.require_auth
    def test_auth_from_uri(self):
        self.client.admin.add_user("admin", "pass")
        try:
            self.client.pymongo_test.add_user(
                "user", "pass", roles=['userAdmin', 'readWrite'])

            self.assertRaises(ConfigurationError, MongoClient,
                              "mongodb://foo:bar@%s:%d" % (host, port))
            self.assertRaises(ConfigurationError, MongoClient,
                              "mongodb://admin:bar@%s:%d" % (host, port))
            self.assertRaises(ConfigurationError, MongoClient,
                              "mongodb://user:pass@%s:%d" % (host, port))
            MongoClient("mongodb://admin:pass@%s:%d" % (host, port))

            self.assertRaises(ConfigurationError, MongoClient,
                              "mongodb://admin:pass@%s:%d/pymongo_test" %
                              (host, port))
            self.assertRaises(ConfigurationError, MongoClient,
                              "mongodb://user:foo@%s:%d/pymongo_test" %
                              (host, port))
            MongoClient("mongodb://user:pass@%s:%d/pymongo_test" %
                       (host, port))

            # Auth with lazy connection.
            MongoClient(
                "mongodb://user:pass@%s:%d/pymongo_test" % (host, port),
                _connect=False).pymongo_test.test.find_one()

            # Wrong password.
            bad_client = MongoClient(
                "mongodb://user:wrong@%s:%d/pymongo_test" % (host, port),
                _connect=False)

            self.assertRaises(OperationFailure,
                              bad_client.pymongo_test.test.find_one)

        finally:
            # Clean up.
            remove_all_users(self.client.pymongo_test)
            self.client.admin.remove_user('admin')

    @client_context.require_auth
    def test_lazy_auth_raises_operation_failure(self):
        lazy_client = MongoClient(
            "mongodb://user:wrong@%s:%d/pymongo_test" % (host, port),
            _connect=False)

        assertRaisesExactly(
            OperationFailure, lazy_client.test.collection.find_one)

    def test_unix_socket(self):
        if not hasattr(socket, "AF_UNIX"):
            raise SkipTest("UNIX-sockets are not supported on this system")
        if (sys.platform == 'darwin' and
                client_context.auth_enabled and
                not client_context.version.at_least(2, 7, 1)):
            raise SkipTest("SERVER-8492")

        mongodb_socket = '/tmp/mongodb-27017.sock'
        if not os.access(mongodb_socket, os.R_OK):
            raise SkipTest("Socket file is not accessible")

        # No error.
        connected(MongoClient("mongodb://%s" % mongodb_socket))

        client = MongoClient("mongodb://%s" % mongodb_socket)
        client.pymongo_test.test.save({"dummy": "object"})

        # Confirm we can read via the socket
        dbs = client.database_names()
        self.assertTrue("pymongo_test" in dbs)

        # Confirm it fails with a missing socket
        self.assertRaises(
            ConnectionFailure,
            connected, MongoClient("mongodb:///tmp/non-existent.sock"))

    def test_fork(self):
        # Test using a client before and after a fork.
        if sys.platform == "win32":
            raise SkipTest("Can't fork on windows")

        try:
            from multiprocessing import Process, Pipe
        except ImportError:
            raise SkipTest("No multiprocessing module")

        db = self.client.pymongo_test

        # Failure occurs if the client is used before the fork
        db.test.find_one()
        db.connection.end_request()

        def loop(pipe):
            while True:
                try:
                    db.test.insert({"a": "b"})
                    for _ in db.test.find():
                        pass
                except:
                    pipe.send(True)
                    os._exit(1)

        cp1, cc1 = Pipe()
        cp2, cc2 = Pipe()

        p1 = Process(target=loop, args=(cc1,))
        p2 = Process(target=loop, args=(cc2,))

        p1.start()
        p2.start()

        p1.join(1)
        p2.join(1)

        p1.terminate()
        p2.terminate()

        p1.join()
        p2.join()

        cc1.close()
        cc2.close()

        # recv will only have data if the subprocess failed
        try:
            cp1.recv()
            self.fail()
        except EOFError:
            pass
        try:
            cp2.recv()
            self.fail()
        except EOFError:
            pass

    def test_document_class(self):
        c = self.client
        db = c.pymongo_test
        db.test.insert({"x": 1})

        self.assertEqual(dict, c.document_class)
        self.assertTrue(isinstance(db.test.find_one(), dict))
        self.assertFalse(isinstance(db.test.find_one(), SON))

        c = get_client(pair, document_class=SON)
        db = c.pymongo_test

        self.assertEqual(SON, c.document_class)
        self.assertTrue(isinstance(db.test.find_one(), SON))
        self.assertFalse(isinstance(db.test.find_one(as_class=dict), SON))

        # document_class is read-only in PyMongo 3.0.
        with self.assertRaises(AttributeError):
            c.document_class = dict

    def test_timeouts(self):
        client = MongoClient(host, port, connectTimeoutMS=10500)
        self.assertEqual(10.5, get_pool(client).opts.connect_timeout)
        client = MongoClient(host, port, socketTimeoutMS=10500)
        self.assertEqual(10.5, get_pool(client).opts.socket_timeout)

    def test_socket_timeout_ms_validation(self):
        c = get_client(pair, socketTimeoutMS=10 * 1000)
        self.assertEqual(10, get_pool(c).opts.socket_timeout)

        c = connected(get_client(pair, socketTimeoutMS=None))
        self.assertEqual(None, get_pool(c).opts.socket_timeout)

        self.assertRaises(ConfigurationError,
                          get_client, pair, socketTimeoutMS=0)

        self.assertRaises(ConfigurationError,
                          get_client, pair, socketTimeoutMS=-1)

        self.assertRaises(ConfigurationError,
                          get_client, pair, socketTimeoutMS=1e10)

        self.assertRaises(ConfigurationError,
                          get_client, pair, socketTimeoutMS='foo')

    def test_socket_timeout(self):
        no_timeout = self.client
        timeout_sec = 1
        timeout = get_client(pair, socketTimeoutMS=1000 * timeout_sec)

        no_timeout.pymongo_test.drop_collection("test")
        no_timeout.pymongo_test.test.insert({"x": 1})

        # A $where clause that takes a second longer than the timeout
        where_func = delay(timeout_sec + 1)

        def get_x(db):
            doc = next(db.test.find().where(where_func))
            return doc["x"]
        self.assertEqual(1, get_x(no_timeout.pymongo_test))
        self.assertRaises(ConnectionFailure, get_x, timeout.pymongo_test)

    def test_waitQueueTimeoutMS(self):
        client = MongoClient(host, port, waitQueueTimeoutMS=2000)
        self.assertEqual(get_pool(client).opts.wait_queue_timeout, 2)

    def test_waitQueueMultiple(self):
        client = MongoClient(host, port, max_pool_size=3, waitQueueMultiple=2)
        pool = get_pool(client)
        self.assertEqual(pool.opts.wait_queue_multiple, 2)
        self.assertEqual(pool._socket_semaphore.waiter_semaphore.counter, 6)

    def test_tz_aware(self):
        self.assertRaises(ConfigurationError, MongoClient, tz_aware='foo')

        aware = get_client(pair, tz_aware=True)
        naive = self.client
        aware.pymongo_test.drop_collection("test")

        now = datetime.datetime.utcnow()
        aware.pymongo_test.test.insert({"x": now})

        self.assertEqual(None, naive.pymongo_test.test.find_one()["x"].tzinfo)
        self.assertEqual(utc, aware.pymongo_test.test.find_one()["x"].tzinfo)
        self.assertEqual(
                aware.pymongo_test.test.find_one()["x"].replace(tzinfo=None),
                naive.pymongo_test.test.find_one()["x"])

    def test_ipv6(self):
        try:
            connected(MongoClient("[::1]", serverWaitTimeMS=100))
        except:
            # Either mongod was started without --ipv6
            # or the OS doesn't support it (or both).
            raise SkipTest("No IPv6")

        if client_context.auth_enabled:
            auth_str = "%s:%s@" % (db_user, db_pwd)
        else:
            auth_str = ""

        uri = "mongodb://%s[::1]:%d" % (auth_str, port)
        client = MongoClient(uri)
        client.pymongo_test.test.save({"dummy": u("object")})
        client.pymongo_test_bernie.test.save({"dummy": u("object")})

        dbs = client.database_names()
        self.assertTrue("pymongo_test" in dbs)
        self.assertTrue("pymongo_test_bernie" in dbs)

    @client_context.require_no_mongos
    def test_fsync_lock_unlock(self):
        if (not client_context.version.at_least(2, 0) and
                client_context.auth_enabled):
            raise SkipTest('Requires server >= 2.0 to test with auth')
        if (server_is_master_with_slave(client_context.client) and
                client_context.version.at_least(2, 3, 0)):
            raise SkipTest('SERVER-7714')

        self.assertFalse(self.client.is_locked)
        # async flushing not supported on windows...
        if sys.platform not in ('cygwin', 'win32'):
            self.client.fsync(async=True)
            self.assertFalse(self.client.is_locked)
        self.client.fsync(lock=True)
        self.assertTrue(self.client.is_locked)
        locked = True
        self.client.unlock()
        for _ in range(5):
            locked = self.client.is_locked
            if not locked:
                break
            time.sleep(1)
        self.assertFalse(locked)

    def test_contextlib(self):
        client = get_client(pair)
        client.pymongo_test.drop_collection("test")
        client.pymongo_test.test.insert({"foo": "bar"})

        # The socket used for the previous commands has been returned to the
        # pool
        self.assertEqual(1, len(get_pool(client).sockets))

        with contextlib.closing(client):
            self.assertEqual("bar", client.pymongo_test.test.find_one()["foo"])
            self.assertEqual(1, len(get_pool(client).sockets))
        self.assertEqual(0, len(get_pool(client).sockets))

        with self.client as client:
            self.assertEqual("bar", client.pymongo_test.test.find_one()["foo"])
            self.assertEqual(1, len(get_pool(client).sockets))
        self.assertEqual(0, len(get_pool(client).sockets))

    def test_with_start_request(self):
        pool = get_pool(self.client)

        # No request started
        self.assertNoRequest(pool)
        self.assertDifferentSock(pool)

        # Start a request
        request_context_mgr = self.client.start_request()
        self.assertTrue(
            isinstance(request_context_mgr, object)
        )

        self.assertNoSocketYet(pool)
        self.assertSameSock(pool)
        self.assertRequestSocket(pool)

        # End request
        request_context_mgr.__exit__(None, None, None)
        self.assertNoRequest(pool)
        self.assertDifferentSock(pool)

        # Test the 'with' statement
        with self.client.start_request() as request:
            self.assertEqual(self.client, request.connection)
            self.assertNoSocketYet(pool)
            self.assertSameSock(pool)
            self.assertRequestSocket(pool)

        # Request has ended
        self.assertNoRequest(pool)
        self.assertDifferentSock(pool)

    def test_auto_start_request(self):
        # Option removed in PyMongo 3.0.
        with self.assertRaises(ConfigurationError):
            MongoClient(auto_start_request=True)

    def test_nested_request(self):
        # auto_start_request is False
        pool = get_pool(self.client)
        self.assertFalse(self.client.in_request())

        # Start and end request
        self.client.start_request()
        self.assertInRequestAndSameSock(self.client, pool)
        self.client.end_request()
        self.assertNotInRequestAndDifferentSock(self.client, pool)

        # Double-nesting
        self.client.start_request()
        self.client.start_request()
        self.client.end_request()
        self.assertInRequestAndSameSock(self.client, pool)
        self.client.end_request()
        self.assertNotInRequestAndDifferentSock(self.client, pool)

        # Extra end_request calls have no effect - count stays at zero
        self.client.end_request()
        self.assertNotInRequestAndDifferentSock(self.client, pool)

        self.client.start_request()
        self.assertInRequestAndSameSock(self.client, pool)
        self.client.end_request()
        self.assertNotInRequestAndDifferentSock(self.client, pool)

    def test_request_threads(self):
        client = self.client
        pool = get_pool(client)
        self.assertNotInRequestAndDifferentSock(client, pool)

        started_request, ended_request = threading.Event(), threading.Event()
        checked_request = threading.Event()
        thread_done = [False]

        # Starting a request in one thread doesn't put the other thread in a
        # request
        def f():
            self.assertNotInRequestAndDifferentSock(client, pool)
            client.start_request()
            self.assertInRequestAndSameSock(client, pool)
            started_request.set()
            checked_request.wait()
            checked_request.clear()
            self.assertInRequestAndSameSock(client, pool)
            client.end_request()
            self.assertNotInRequestAndDifferentSock(client, pool)
            ended_request.set()
            checked_request.wait()
            thread_done[0] = True

        t = threading.Thread(target=f)
        t.setDaemon(True)
        t.start()
        # It doesn't matter in what order the main thread or t initially get
        # to started_request.set() / wait(); by waiting here we ensure that t
        # has called client.start_request() before we assert on the next line.
        started_request.wait()
        self.assertNotInRequestAndDifferentSock(client, pool)
        checked_request.set()
        ended_request.wait()
        self.assertNotInRequestAndDifferentSock(client, pool)
        checked_request.set()
        t.join()
        self.assertNotInRequestAndDifferentSock(client, pool)
        self.assertTrue(thread_done[0], "Thread didn't complete")

    def test_interrupt_signal(self):
        if sys.platform.startswith('java'):
            # We can't figure out how to raise an exception on a thread that's
            # blocked on a socket, whether that's the main thread or a worker,
            # without simply killing the whole thread in Jython. This suggests
            # PYTHON-294 can't actually occur in Jython.
            raise SkipTest("Can't test interrupts in Jython")

        # Test fix for PYTHON-294 -- make sure MongoClient closes its
        # socket if it gets an interrupt while waiting to recv() from it.
        db = self.client.pymongo_test

        # A $where clause which takes 1.5 sec to execute
        where = delay(1.5)

        # Need exactly 1 document so find() will execute its $where clause once
        db.drop_collection('foo')
        db.foo.insert({'_id': 1})

        def interrupter():
            # Raises KeyboardInterrupt in the main thread
            time.sleep(0.25)
            thread.interrupt_main()

        thread.start_new_thread(interrupter, ())

        raised = False
        try:
            # Will be interrupted by a KeyboardInterrupt.
            next(db.foo.find({'$where': where}))
        except KeyboardInterrupt:
            raised = True

        # Can't use self.assertRaises() because it doesn't catch system
        # exceptions
        self.assertTrue(raised, "Didn't raise expected KeyboardInterrupt")

        # Raises AssertionError due to PYTHON-294 -- Mongo's response to the
        # previous find() is still waiting to be read on the socket, so the
        # request id's don't match.
        self.assertEqual(
            {'_id': 1},
            next(db.foo.find())
        )

    def test_operation_failure_without_request(self):
        # Ensure MongoClient doesn't close socket after it gets an error
        # response to getLastError. PYTHON-395.
        pool = get_pool(self.client)
        socket_count = len(pool.sockets)
        self.assertGreaterEqual(socket_count, 1)
        old_sock_info = next(iter(pool.sockets))
        self.client.pymongo_test.test.drop()
        self.client.pymongo_test.test.insert({'_id': 'foo'})
        self.assertRaises(
            OperationFailure,
            self.client.pymongo_test.test.insert, {'_id': 'foo'})

        self.assertEqual(socket_count, len(pool.sockets))
        new_sock_info = next(iter(pool.sockets))
        self.assertEqual(old_sock_info, new_sock_info)

    def test_operation_failure_with_request(self):
        # Ensure MongoClient doesn't close socket after it gets an error
        # response to getLastError. PYTHON-395.
        c = get_client(pair)
        c.start_request()
        pool = get_pool(c)

        # Pool reserves a socket for this thread.
        c.pymongo_test.test.find_one()
        self.assertTrue(isinstance(pool._get_request_state(), SocketInfo))

        old_sock_info = pool._get_request_state()
        c.pymongo_test.test.drop()
        c.pymongo_test.test.insert({'_id': 'foo'})
        self.assertRaises(
            OperationFailure,
            c.pymongo_test.test.insert, {'_id': 'foo'})

        # OperationFailure doesn't affect the request socket
        self.assertEqual(old_sock_info, pool._get_request_state())

    def test_alive(self):
        self.assertTrue(self.client.alive())

        client = MongoClient('doesnt exist', _connect=False)
        self.assertFalse(client.alive())

    def test_wire_version(self):
        c = MockClient(
            standalones=[],
            members=['a:1', 'b:2', 'c:3'],
            mongoses=[],
            host='b:2',  # Pass a secondary.
            replicaSet='rs',
            _connect=False)

        c.set_wire_version_range('a:1', 1, 5)
        c.db.command('ismaster')  # Connect.
        self.assertEqual(c.min_wire_version, 1)
        self.assertEqual(c.max_wire_version, 5)

        c.set_wire_version_range('a:1', 10, 11)
        c.disconnect()
        self.assertRaises(ConfigurationError, c.db.collection.find_one)

    def test_max_wire_version(self):
        c = MockClient(
            standalones=[],
            members=['a:1', 'b:2', 'c:3'],
            mongoses=[],
            host='b:2',  # Pass a secondary.
            replicaSet='rs',
            _connect=False)

        c.set_max_write_batch_size('a:1', 1)
        c.set_max_write_batch_size('b:2', 2)

        # Starts with default max batch size.
        self.assertEqual(1000, c.max_write_batch_size)
        c.db.command('ismaster')  # Connect.
        # Uses primary's max batch size.
        self.assertEqual(c.max_write_batch_size, 1)

        # b becomes primary.
        c.mock_primary = 'b:2'
        c.disconnect()
        self.assertEqual(1000, c.max_write_batch_size)
        c.db.command('ismaster')  # Connect.
        self.assertEqual(c.max_write_batch_size, 2)

    def test_wire_version_mongos_ha(self):
        # TODO: Reimplement Mongos HA with PyMongo 3's MongoClient.
        raise SkipTest('Mongos HA must be reimplemented in PyMongo 3')

        c = MockClient(
            standalones=[],
            members=[],
            mongoses=['a:1', 'b:2', 'c:3'],
            host='a:1,b:2,c:3',
            _connect=False)

        c.set_wire_version_range('a:1', 2, 5)
        c.set_wire_version_range('b:2', 2, 2)
        c.set_wire_version_range('c:3', 1, 1)
        c.db.command('ismaster')  # Connect.

        # Which member did we use?
        used_host = '%s:%s' % (c.host, c.port)
        expected_min, expected_max = c.mock_wire_versions[used_host]
        self.assertEqual(expected_min, c.min_wire_version)
        self.assertEqual(expected_max, c.max_wire_version)

        c.set_wire_version_range('a:1', 0, 0)
        c.set_wire_version_range('b:2', 0, 0)
        c.set_wire_version_range('c:3', 0, 0)
        c.disconnect()
        c.db.command('ismaster')
        used_host = '%s:%s' % (c.host, c.port)
        expected_min, expected_max = c.mock_wire_versions[used_host]
        self.assertEqual(expected_min, c.min_wire_version)
        self.assertEqual(expected_max, c.max_wire_version)
        
    def test_kill_cursors(self):
        self.collection = self.client.pymongo_test.test
        self.collection.remove()
        
        # Ensure two batches.
        self.collection.insert({'_id': i} for i in range(200))

        cursor = self.collection.find()
        next(cursor)
        self.client.kill_cursors([cursor.cursor_id])
        
        with self.assertRaises(CursorNotFound):
            list(cursor)

    @client_context.require_replica_set
    def test_replica_set(self):
        name = client_context.setname
        connected(MongoClient(host, port, replicaSet=name))  # No error.

        client = MongoClient(
            host, port, replicaSet='bad' + name, serverWaitTimeMS=100)

        with self.assertRaises(AutoReconnect):
            connected(client)

    def test_lazy_connect_w0(self):
        client = get_client(connection_string(), _connect=False)
        client.pymongo_test.test.insert({}, w=0)

        client = get_client(connection_string(), _connect=False)
        client.pymongo_test.test.update({}, {'$set': {'x': 1}}, w=0)

        client = get_client(connection_string(), _connect=False)
        client.pymongo_test.test.remove(w=0)

    @client_context.require_no_mongos
    def test_exhaust_network_error(self):
        # When doing an exhaust query, the socket stays checked out on success
        # but must be checked in on error to avoid semaphore leaks.
        client = get_client(max_pool_size=1)
        collection = client.pymongo_test.test
        pool = get_pool(client)
        pool._check_interval_seconds = None  # Never check.

        # Ensure a socket.
        connected(client)

        # Cause a network error.
        sock_info = one(pool.sockets)
        sock_info.sock.close()
        cursor = collection.find(exhaust=True)
        with self.assertRaises(ConnectionFailure):
            next(cursor)

        self.assertTrue(sock_info.closed)

        # The semaphore was decremented despite the error.
        self.assertTrue(pool._socket_semaphore.acquire(blocking=False))

    @client_context.require_auth
    def test_auth_network_error(self):
        # Make sure there's no semaphore leak if we get a network error
        # when authenticating a new socket with cached credentials.

        # Get a client with one socket so we detect if it's leaked.
        c = get_client(max_pool_size=1, waitQueueTimeoutMS=1)

        # Simulate an authenticate() call on a different socket.
        credentials = auth._build_credentials_tuple(
            'MONGODB-CR', 'admin', db_user, db_pwd, {})

        c._cache_credentials('test', credentials, connect=False)

        # Cause a network error on the actual socket.
        pool = get_pool(c)
        socket_info = one(pool.sockets)
        socket_info.sock.close()

        # In __check_auth, the client authenticates its socket with the
        # new credential, but gets a socket.error. Should be reraised as
        # AutoReconnect.
        self.assertRaises(AutoReconnect, c.test.collection.find_one)

        # No semaphore leak, the pool is allowed to make a new socket.
        c.test.collection.find_one()


class TestClientLazyConnect(IntegrationTest, _TestLazyConnectMixin):

    def _get_client(self, **kwargs):
        return get_client(connection_string(), **kwargs)


class TestClientLazyConnectBadSeeds(IntegrationTest):

    def _get_client(self, **kwargs):
        kwargs.setdefault('connectTimeoutMS', 100)

        # Assume there are no open mongods listening on a.com, b.com, ....
        bad_seeds = ['%s.com' % chr(ord('a') + i) for i in range(10)]
        return get_client(bad_seeds, serverWaitTimeMS=100, **kwargs)

    def test_connect(self):
        def reset(dummy):
            pass

        def connect(collection, dummy):
            self.assertRaises(AutoReconnect, collection.find_one)

        def test(collection):
            client = collection.database.connection
            self.assertEqual(0, len(client.nodes))

        lazy_client_trial(reset, connect, test, self._get_client)


class TestMongoClientFailover(IntegrationTest):

    def test_discover_primary(self):
        c = MockClient(
            standalones=[],
            members=['a:1', 'b:2', 'c:3'],
            mongoses=[],
            host='b:2',  # Pass a secondary.
            replicaSet='rs')

        wait_until(lambda: len(c.nodes) == 3, 'connect')
        self.assertEqual('a', c.host)
        self.assertEqual(1, c.port)

        # Fail over.
        c.kill_host('a:1')
        c.mock_primary = 'b:2'

        c.disconnect()
        self.assertEqual(0, len(c.nodes))

        # Force reconnect.
        c.db.command('ismaster')
        self.assertEqual('b', c.host)
        self.assertEqual(2, c.port)

        # a:1 not longer in nodes.
        self.assertLess(len(c.nodes), 3)
        wait_until(lambda: len(c.nodes) == 2, 'discover node "c"')

    def test_reconnect(self):
        # Verify the node list isn't forgotten during a network failure.
        c = MockClient(
            standalones=[],
            members=['a:1', 'b:2', 'c:3'],
            mongoses=[],
            host='b:2',  # Pass a secondary.
            replicaSet='rs')

        # Connect to "b" and discover the other members.
        connected(c)

        # Total failure.
        c.kill_host('a:1')
        c.kill_host('b:2')
        c.kill_host('c:3')

        # MongoClient discovers it's alone.
        self.assertRaises(AutoReconnect, c.db.collection.find_one)

        # But it can reconnect.
        c.revive_host('a:1')
        c.db.command('ismaster')
        self.assertEqual('a', c.host)
        self.assertEqual(1, c.port)


if __name__ == "__main__":
    unittest.main()
