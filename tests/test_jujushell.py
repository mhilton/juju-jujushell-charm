# Copyright 2017 Canonical Ltd.
# Licensed under the AGPLv3, see LICENCE file for details.

import base64
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import (
    call,
    Mock,
    patch,
)

import yaml

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_layer = os.path.join(_root, 'lib', 'charms', 'layer')
sys.path.insert(0, _layer)

# jujushell can only be imported after the layer directory has been added to
# the python path.
import jujushell  # noqa: E402


@patch('charmhelpers.core.hookenv.log')
class TestCall(unittest.TestCase):

    def test_success(self, mock_log):
        # A command suceeds.
        jujushell.call('echo')
        self.assertEqual(2, mock_log.call_count)
        mock_log.assert_has_calls([
            call("running the following: 'echo'"),
            call("command 'echo' succeeded: '\\n'"),
        ])

    def test_multiple_arguments(self, mock_log):
        # A command with multiple arguments succeeds.
        jujushell.call('echo', 'we are the borg')
        self.assertEqual(2, mock_log.call_count)
        mock_log.assert_has_calls([
            call('running the following: "echo \'we are the borg\'"'),
            call('command "echo \'we are the borg\'" succeeded: '
                 '\'we are the borg\\n\''),
        ])

    def test_failure(self, mock_log):
        # An OSError is raise when the command fails.
        with self.assertRaises(OSError) as ctx:
            jujushell.call('ls', 'no-such-file')
        expected_error = 'command \'ls no-such-file\' failed with retcode 2:'
        obtained_error = str(ctx.exception)
        self.assertTrue(obtained_error.startswith(expected_error))
        mock_log.assert_has_calls([
            call("running the following: 'ls no-such-file'"),
            call(obtained_error),
        ])

    def test_invalid_command(self, mock_log):
        # An OSError is raised if the subprocess fails to find the provided
        # command in the PATH.
        with self.assertRaises(OSError) as ctx:
            jujushell.call('no-such-command')
        expected_error = (
            "command 'no-such-command' not found: [Errno 2] "
            "No such file or directory: 'no-such-command'"
        )
        self.assertTrue(str(ctx.exception).startswith(expected_error))
        mock_log.assert_has_calls([
            call("running the following: 'no-such-command'"),
        ])


class TestUpdateLXCQuotas(unittest.TestCase):

    def test_update_lxc_quotas(self):
        cfg = {
            'lxc-quota-cpu-cores': 1,
            'lxc-quota-cpu-allowance': '100%',
            'lxc-quota-ram': '256MB',
            'lxc-quota-processes': 100,
        }
        with patch('jujushell.call') as mock_call:
            jujushell.update_lxc_quotas(cfg)
        expected_calls = [
            call(jujushell.LXC, 'profile', 'set', jujushell.PROFILE_TERMSERVER,
                 'limits.cpu', '1'),
            call(jujushell.LXC, 'profile', 'set', jujushell.PROFILE_TERMSERVER,
                 'limits.cpu.allowance', '100%'),
            call(jujushell.LXC, 'profile', 'set', jujushell.PROFILE_TERMSERVER,
                 'limits.memory', '256MB'),
            call(jujushell.LXC, 'profile', 'set', jujushell.PROFILE_TERMSERVER,
                 'limits.processes', '100'),
        ]
        mock_call.assert_has_calls(expected_calls)
        self.assertEqual(mock_call.call_count, len(expected_calls))


class TestTermserverPath(unittest.TestCase):

    def test_termserver_path(self):
        self.assertEqual(
            jujushell.termserver_path(),
            '/var/tmp/termserver.tar.gz')
        self.assertEqual(
            jujushell.termserver_path(limited=True),
            '/var/tmp/termserver-limited.tar.gz')


@patch('charmhelpers.core.hookenv.open_port')
@patch('charmhelpers.core.hookenv.close_port')
@patch('os.path.exists', lambda _: True)
class TestBuildConfig(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory where to execute the test.
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        # Switch to the temporary directory.
        cwd = os.getcwd()
        os.chdir(directory)
        self.addCleanup(os.chdir, cwd)
        # Also make charm files live in the temp dir.
        files = os.path.join(directory, 'files')
        os.mkdir(files)
        os.environ['CHARM_DIR'] = directory
        self.addCleanup(os.environ.pop, 'CHARM_DIR')
        # Add juju addresses as an environment variable.
        os.environ['JUJU_API_ADDRESSES'] = '1.2.3.4:17070 4.3.2.1:17070'
        self.addCleanup(os.environ.pop, 'JUJU_API_ADDRESSES')

    def get_config(self):
        """Return the YAML decoded configuration file that has been created."""
        with open('files/config.yaml') as configfile:
            return yaml.safe_load(configfile)

    def make_cert(self):
        """Make a testing key pair in the current directory."""
        with open('cert.pem', 'w') as certfile:
            certfile.write('my cert')
        with open('key.pem', 'w') as keyfile:
            keyfile.write('my key')

    def test_no_tls(self, mock_close_port, mock_open_port):
        # The configuration file is created correctly without TLS.
        jujushell.build_config({
            'log-level': 'info',
            'port': 4247,
            'tls': False,
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_tls_provided(self, mock_close_port, mock_open_port):
        # Provided TLS keys are properly used.
        jujushell.build_config({
            'log-level': 'debug',
            'port': 80,
            'tls': True,
            'tls-cert': base64.b64encode(b'provided cert'),
            'tls-key': base64.b64encode(b'provided key'),
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'debug',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 80,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'tls-cert': 'provided cert',
            'tls-key': 'provided key',
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(80)

    def test_tls_keys_provided_but_tls_not_enabled(
            self, mock_close_port, mock_open_port):
        # Provided TLS keys are ignored when security is not enabled.
        jujushell.build_config({
            'log-level': 'debug',
            'port': 80,
            'tls': False,
            'tls-cert': base64.b64encode(b'provided cert'),
            'tls-key': base64.b64encode(b'provided key'),
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'debug',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 80,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(80)

    def test_dns_name_provided_but_tls_not_enabled(
            self, mock_close_port, mock_open_port):
        # The provided DNS name is ignored when security is not enabled.
        jujushell.build_config({
            'dns-name': 'shell.example.com',
            'log-level': 'debug',
            'port': 8080,
            'tls': False,
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'debug',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 8080,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(8080)

    def test_tls_generated(self, mock_close_port, mock_open_port):
        # TLS keys are generated if not provided.
        self.make_cert()
        with patch('jujushell.call') as mock_call:
            jujushell.build_config({
                'log-level': 'trace',
                'port': 4247,
                'tls': True,
                'tls-cert': '',
                'tls-key': '',
            })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'trace',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'tls-cert': 'my cert',
            'tls-key': 'my key',
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        # The right command has been executed.
        mock_call.assert_called_once_with(
            'openssl', 'req',
            '-x509',
            '-newkey', 'rsa:4096',
            '-keyout', 'key.pem',
            '-out', 'cert.pem',
            '-days', '365',
            '-nodes',
            '-subj', '/C=GB/ST=London/L=London/O=Canonical/OU=JAAS/CN=0.0.0.0')
        # Key files has been removed.
        self.assertEqual(['files'], os.listdir('.'))
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_tls_generated_when_key_is_missing(
            self, mock_close_port, mock_open_port):
        # TLS keys are generated if only one key is provided, not both.
        self.make_cert()
        with patch('jujushell.call'):
            jujushell.build_config({
                'log-level': 'trace',
                'port': 4247,
                'tls': True,
                'tls-cert': base64.b64encode(b'provided cert'),
                'tls-key': '',
            })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'trace',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'tls-cert': 'my cert',
            'tls-key': 'my key',
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_dns_name_provided(self, mock_close_port, mock_open_port):
        # The DNS name is propagated to the service when provided.
        jujushell.build_config({
            'dns-name': 'shell.example.com',
            'log-level': 'debug',
            'port': 443,
            'tls': True,
        })
        expected_config = {
            'allowed-users': [],
            'dns-name': 'shell.example.com',
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'debug',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 443,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertFalse(mock_close_port.called)
        mock_open_port.assert_called_once_with(443)

    def test_tls_keys_ignored_when_dns_name_provided(
            self, mock_close_port, mock_open_port):
        # The TLS keys and port options are ignored when a DNS name is set.
        jujushell.build_config({
            'dns-name': 'example.com',
            'log-level': 'debug',
            'port': 80,
            'tls': True,
            'tls-cert': base64.b64encode(b'provided cert'),
            'tls-key': base64.b64encode(b'provided key'),
        })
        expected_config = {
            'allowed-users': [],
            'dns-name': 'example.com',
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'debug',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 443,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertFalse(mock_close_port.called)
        mock_open_port.assert_called_once_with(443)

    def test_provided_juju_cert(self, mock_close_port, mock_open_port):
        # The configuration file is created with the provided Juju certificate.
        jujushell.build_config({
            'log-level': 'info',
            'juju-cert': 'provided cert',
            'port': 4247,
            'tls': False,
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': 'provided cert',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_juju_cert_from_agent_file(self, mock_close_port, mock_open_port):
        # A Juju certificate can be retrieved from the agent file in the unit.
        # Make agent file live in the temp dir.
        agent = os.path.join(os.environ['CHARM_DIR'], '..', 'agent.conf')
        with open(agent, 'w') as agentfile:
            yaml.safe_dump({'cacert': 'agent cert'}, agentfile)
        jujushell.build_config({
            'log-level': 'info',
            'juju-cert': 'from-unit',
            'port': 4247,
            'tls': False,
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': 'agent cert',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_provided_juju_addresses(self, mock_close_port, mock_open_port):
        # Juju addresses can be provided via the configuration.
        jujushell.build_config({
            'juju-addrs': '1.2.3.4/provided 4.3.2.1/provided',
            'log-level': 'info',
            'port': 4247,
            'tls': False,
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4/provided', '4.3.2.1/provided'],
            'juju-cert': '',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_previous_port_closed(self, mock_close_port, mock_open_port):
        # When changing between ports, the previous one is properly closed.
        Config = type('Config', (dict,), {'_prev_dict': None})
        config = Config({
            'dns-name': 'shell.example.com',
            'log-level': 'debug',
            'port': 443,
            'tls': True,
        })
        config._prev_dict = {
            'log-level': 'debug',
            'port': 8042,
            'tls': True,
        }
        jujushell.build_config(config)
        mock_close_port.assert_called_once_with(8042)
        mock_open_port.assert_called_once_with(443)

    def test_error_no_juju_addresses(self, mock_close_port, mock_open_port):
        # A ValueError is raised if no Juju addresses can be retrieved.
        os.environ['JUJU_API_ADDRESSES'] = ''
        with self.assertRaises(ValueError) as ctx:
            jujushell.build_config({
                'log-level': 'info',
                'port': 4247,
                'tls': False,
            })
        self.assertEqual('could not find API addresses', str(ctx.exception))
        self.assertEqual(0, mock_close_port.call_count)
        self.assertEqual(0, mock_open_port.call_count)

    def test_allowed_users(self, mock_close_port, mock_open_port):
        # The list of allowed users is properly generated.
        jujushell.build_config({
            'allowed-users': 'who dalek rose@external',
            'log-level': 'info',
            'port': 4247,
            'tls': False,
        })
        expected_config = {
            'allowed-users': ['who', 'dalek', 'rose@external'],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_session_timeout(self, mock_close_port, mock_open_port):
        # The session timeout value is properly generated.
        jujushell.build_config({
            'log-level': 'info',
            'port': 4247,
            'session-timeout': 42,
            'tls': False,
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 42,
            'welcome-message': '',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)

    def test_welcome_message(self, mock_close_port, mock_open_port):
        # The welcome message is properly handled.
        jujushell.build_config({
            'log-level': 'info',
            'port': 4247,
            'tls': False,
            'welcome-message': '  these are\nthe voyages\n\n',
        })
        expected_config = {
            'allowed-users': [],
            'image-name': 'termserver',
            'juju-addrs': ['1.2.3.4:17070', '4.3.2.1:17070'],
            'juju-cert': '',
            'log-level': 'info',
            'lxd-socket-path': '/var/lib/lxd/unix.socket',
            'port': 4247,
            'profiles': [
                jujushell.PROFILE_TERMSERVER,
                jujushell.PROFILE_TERMSERVER_LIMITED,
            ],
            'session-timeout': 0,
            'welcome-message': 'these are\nthe voyages',
        }
        self.assertEqual(expected_config, self.get_config())
        self.assertEqual(0, mock_close_port.call_count)
        mock_open_port.assert_called_once_with(4247)


class TestGetPorts(unittest.TestCase):

    def test_with_dns_name(self):
        # Ports 443 and 80 are returned if a DNS name has been provided.
        ports = jujushell.get_ports({
            'dns-name': 'example.com', 'port': 4247, 'tls': True})
        self.assertEqual((443,), ports)

    def test_with_invalid_dns_name(self):
        # The DNS name is ignored if not valid.
        ports = jujushell.get_ports({'dns-name': ' ', 'port': 47, 'tls': True})
        self.assertEqual((47,), ports)

    def test_without_dns_name(self):
        # The port specified in the config is returned if no DNS name is set.
        ports = jujushell.get_ports({'port': 8000, 'tls': True})
        self.assertEqual((8000,), ports)

    def test_without_dns_name_no_tls(self):
        # The port specified in the config is returned if security is disabled.
        ports = jujushell.get_ports({'port': 8080, 'tls': False})
        self.assertEqual((8080,), ports)

    def test_with_dns_name_no_tls(self):
        # The port specified in the config is returned if security is disabled,
        # even when a DNS name has been provided.
        ports = jujushell.get_ports({
            'dns-name': 'example.com', 'port': 4247, 'tls': False})
        self.assertEqual((4247,), ports)


@patch('charmhelpers.core.hookenv.log')
class TestSaveResource(unittest.TestCase):

    def test_resource_retrieved(self, mock_log):
        # A resource can be successfully retrieved and stored.
        with patch('charmhelpers.core.hookenv.resource_get') as mock_get:
            mock_get.return_value = ''
            with self.assertRaises(OSError) as ctx:
                jujushell.save_resource('bad-resource', 'mypath')
        self.assertEqual(
            "cannot retrieve resource 'bad-resource'", str(ctx.exception))
        mock_get.assert_called_once_with('bad-resource')

    def test_error_getting_resource(self, mock_log):
        # An OSError is raised if it's not possible to get a resource.
        # Create a directory for storing the resource.
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        resource = os.path.join(directory, 'resource')
        with open(resource, 'w') as resource_file:
            resource_file.write('resource content')
        # Create a target file where to save the resource.
        path = os.path.join(directory, 'target')
        with patch('charmhelpers.core.hookenv.resource_get') as mock_get:
            mock_get.return_value = resource
            jujushell.save_resource('myresource', path)
        # The target has been created with the right content.
        self.assertTrue(os.path.isfile(path))
        with open(path) as target_file:
            self.assertEqual('resource content', target_file.read())
        # The original resource file is no more.
        self.assertFalse(os.path.isfile(resource))
        mock_get.assert_called_once_with('myresource')


@patch('charmhelpers.core.hookenv.log')
class TestImportLXDImage(unittest.TestCase):

    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.path = os.path.join(directory, 'image')
        with open(self.path, 'wb') as f:
            f.write(b'AAAAAAAAAA')

    def test_no_images(self, mock_log):
        with patch('jujushell._lxd_client') as mock_client:
            mock_client().images.all.return_value = ()
            jujushell.import_lxd_image('test', self.path)
        mock_client().images.create.assert_called_once_with(
            b'AAAAAAAAAA',
            wait=True)
        mock_client().images.create().add_alias.assert_called_once_with(
            'test',
            '')

    def test_image_exists(self, mock_log):
        image = Mock()
        image.fingerprint = \
            '1d65bf29403e4fb1767522a107c827b8884d16640cf0e3b18c4c1dd107e0d49d'
        image.aliases = [{'name': 'test', 'description': ''}]
        with patch('jujushell._lxd_client') as mock_client:
            mock_client().images.all.return_value = [image]
            jujushell.import_lxd_image('test', self.path)
        mock_client().images.create.assert_not_called()

    def test_image_exists_no_alias(self, mock_log):
        image = Mock()
        image.fingerprint = \
            '1d65bf29403e4fb1767522a107c827b8884d16640cf0e3b18c4c1dd107e0d49d'
        image.aliases = []
        with patch('jujushell._lxd_client') as mock_client:
            mock_client().images.all.return_value = [image]
            jujushell.import_lxd_image('test', self.path)
        mock_client().images.create.assert_not_called()
        image.add_alias.assert_called_once_with('test', '')

    def test_image_with_alias_exists(self, mock_log):
        image = Mock()
        image.fingerprint = \
            '2d65bf29403e4fb1767522a107c827b8884d16640cf0e3b18c4c1dd107e0d49d'
        image.aliases = [{'name': 'test', 'description': ''}]
        with patch('jujushell._lxd_client') as mock_client:
            mock_client().images.all.return_value = [image]
            jujushell.import_lxd_image('test', self.path)
        mock_client().images.create.assert_called_once_with(
            b'AAAAAAAAAA',
            wait=True)
        mock_client().images.create().add_alias.assert_called_once_with(
            'test',
            '')
        image.delete_alias.assert_called_once_with('test')


@patch('charmhelpers.core.hookenv.log')
class TestSetupLXD(unittest.TestCase):

    def test_not_initialized(self, mock_log):
        with patch('jujushell._lxd_client') as mock_client:
            mock_client().networks.all.return_value = ()
            with patch('jujushell.call') as mock_call:
                jujushell.setup_lxd()
        self.assertEqual(2, mock_call.call_count)
        mock_call.assert_has_calls([
            call(jujushell._LXD_INIT_COMMAND, shell=True, cwd='/'),
            call(jujushell._LXD_WAIT_COMMAND, shell=True, cwd='/'),
        ])

    def test_initialized(self, mock_log):
        with patch('jujushell._lxd_client') as mock_client:
            net = Mock()
            net.name = 'jujushellbr0'
            mock_client().networks.all.return_value = [net]
            with patch('jujushell.call') as mock_call:
                jujushell.setup_lxd()
        mock_call.assert_called_once_with(
            jujushell._LXD_WAIT_COMMAND, shell=True, cwd='/')


class TestExterminateContainers(unittest.TestCase):

    def test_all(self):
        # Exterminate all existing containers.
        containers = [
            ('c1', True),
            ('c2', False),
            ('c3', True),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers()
        self.assertEqual(removed, ('c1', 'c2', 'c3'))
        c1, c2, c3 = client.containers.all()
        c1.stop.assert_called_once_with(wait=True)
        c1.delete.assert_called_once_with()
        self.assertFalse(c2.stop.called)
        c2.delete.assert_called_once_with()
        c3.stop.assert_called_once_with(wait=True)
        c3.delete.assert_called_once_with()

    def test_all_dry(self):
        # Exterminate all existing containers (dry run).
        containers = [
            ('c1', True),
            ('c2', False),
            ('c3', True),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(dry=True)
        self.assertEqual(removed, ('c1', 'c2', 'c3'))
        c1, c2, c3 = client.containers.all()
        self.assertFalse(c1.stop.called)
        self.assertFalse(c1.delete.called)
        self.assertFalse(c2.stop.called)
        self.assertFalse(c2.delete.called)
        self.assertFalse(c3.stop.called)
        self.assertFalse(c3.delete.called)

    def test_all_none_existing(self):
        # There is nothing to exterminate if no containers exist.
        with self.patch_lxd_client([]):
            removed = jujushell.exterminate_containers()
        self.assertEqual(removed, ())

    def test_name(self):
        # Exterminate a specific container.
        containers = [
            ('c-good', False),
            ('c-bad', True),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(name='c-bad')
        self.assertEqual(removed, ('c-bad',))
        cgood, cbad = client.containers.all()
        self.assertFalse(cgood.stop.called)
        self.assertFalse(cgood.delete.called)
        cbad.stop.assert_called_once_with(wait=True)
        cbad.delete.assert_called_once_with()

    def test_name_dry(self):
        # Exterminate a specific container (dry run).
        containers = [
            ('c-bad', True),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(name='c-bad', dry=True)
        self.assertEqual(removed, ('c-bad',))
        [cbad] = client.containers.all()
        self.assertFalse(cbad.stop.called)
        self.assertFalse(cbad.delete.called)

    def test_name_not_found(self):
        # There is nothing to exterminate if the container does not exist.
        containers = [
            ('c1', True),
            ('c2', False),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(name='no-such')
        self.assertEqual(removed, ())
        c1, c2 = client.containers.all()
        self.assertFalse(c1.stop.called)
        self.assertFalse(c1.delete.called)
        self.assertFalse(c2.stop.called)
        self.assertFalse(c2.delete.called)

    def test_only_stopped(self):
        # Exterminate stopped containers.
        containers = [
            ('c1', False),
            ('c2', True),
            ('c3', False),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(only_stopped=True)
        self.assertEqual(removed, ('c1', 'c3'))
        c1, c2, c3 = client.containers.all()
        self.assertFalse(c1.stop.called)
        c1.delete.assert_called_once_with()
        self.assertFalse(c2.stop.called)
        self.assertFalse(c2.delete.called)
        self.assertFalse(c3.stop.called)
        c3.delete.assert_called_once_with()

    def test_only_stopped_dry(self):
        # Exterminate stopped containers (dry run).
        containers = [
            ('c1', False),
            ('c2', True),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(
                only_stopped=True, dry=True)
        self.assertEqual(removed, ('c1',))
        c1, c2 = client.containers.all()
        self.assertFalse(c1.stop.called)
        self.assertFalse(c1.delete.called)
        self.assertFalse(c2.stop.called)
        self.assertFalse(c2.delete.called)

    def test_only_stopped_none_stopped(self):
        # No containers are removed if they are all running.
        containers = [
            ('c1', True),
            ('c2', True),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(only_stopped=True)
        self.assertEqual(removed, ())
        c1, c2 = client.containers.all()
        self.assertFalse(c1.stop.called)
        self.assertFalse(c1.delete.called)
        self.assertFalse(c2.stop.called)
        self.assertFalse(c2.delete.called)

    def test_name_only_stopped_found(self):
        # Exterminate a stopped container with the given name.
        containers = [
            ('mylxc', False),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(
                name='mylxc', only_stopped=True)
        self.assertEqual(removed, ('mylxc',))
        [mylxc] = client.containers.all()
        self.assertFalse(mylxc.stop.called)
        mylxc.delete.assert_called_once_with()

    def test_name_only_stopped_not_found(self):
        # A stopped container with the given name does not exist.
        containers = [
            ('mylxc', False),
        ]
        with self.patch_lxd_client(containers) as client:
            removed = jujushell.exterminate_containers(
                name='no-such', only_stopped=True)
        self.assertEqual(removed, ())
        [mylxc] = client.containers.all()
        self.assertFalse(mylxc.stop.called)
        self.assertFalse(mylxc.delete.called)

    def patch_lxd_client(self, containers):
        """Patch the LXD client and make it return the given containers.

        Containers are expressed as tuples (name: str, running: bool).
        """
        results = [
            type('Container', (object,), {
                'name': name,
                'status': 'Running' if running else 'Stopped',
                'stop': Mock(),
                'delete': Mock(),
            }) for name, running in containers
        ]
        return patch('jujushell._lxd_client', type('Client', (object, ), {
            'containers': type('Containers', (object,), {
                'all': lambda: results,
            }),
        }))


class TestServiceURL(unittest.TestCase):

    tests = [{
        'about': 'insecure ip',
        'config': {'port': 8042},
        'want_url': 'http://localhost:8042/metrics',
    }, {
        'about': 'dns name provided',
        'config': {'dns-name': 'example.com', 'port': 443},
        'want_url': 'https://example.com:443/metrics',
    }, {
        'about': 'certs provided',
        'config': {'port': 4242, 'tls-cert': 'cert'},
        'want_url': 'https://localhost:4242/metrics',
    }, {
        'about': 'dns name and certs provided',
        'config': {'dns-name': 'example.com', 'port': 443, 'tls-cert': 'cert'},
        'want_url': 'https://example.com:443/metrics',
    }]

    def test_service_url(self):
        # The service URL is inferred from its configuration.
        for test in self.tests:
            with self.subTest(test['about']):
                url = jujushell.service_url(test['config'])
                self.assertEqual(url, test['want_url'])


if __name__ == '__main__':
    unittest.main()
