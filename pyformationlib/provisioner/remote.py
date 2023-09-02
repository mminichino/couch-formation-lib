##
##

import attr
import concurrent.futures
import logging
import paramiko
import socket
import time
import os
import jinja2
from io import StringIO
from typing import Optional, List
from pyformationlib.exception import NonFatalError
from pyformationlib.config import NodeList, NodeEntry
import pyformationlib.constants as C

logger = logging.getLogger('pyformationlib.provisioner.remote')
logger.addHandler(logging.NullHandler())
logging.getLogger("paramiko").setLevel(logging.ERROR)


class ProvisionerError(NonFatalError):
    pass


class CustomLogFormatter(logging.Formatter):
    FORMATS = {
        logging.DEBUG: f"{C.FORMAT_TIMESTAMP} [{C.FORMAT_LEVEL}] {C.FORMAT_MESSAGE}",
        logging.INFO: f"{C.FORMAT_TIMESTAMP} [{C.FORMAT_LEVEL}] {C.FORMAT_MESSAGE}",
        logging.WARNING: f"{C.FORMAT_TIMESTAMP} [{C.FORMAT_LEVEL}] {C.FORMAT_MESSAGE}",
        logging.ERROR: f"{C.FORMAT_TIMESTAMP} [{C.FORMAT_LEVEL}] {C.FORMAT_MESSAGE}",
        logging.CRITICAL: f"{C.FORMAT_TIMESTAMP} [{C.FORMAT_LEVEL}] {C.FORMAT_MESSAGE}"
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        if logging.DEBUG >= logging.root.level:
            log_fmt += C.FORMAT_EXTRA
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


@attr.s
class ProvisionSet:
    pre_install_cmd: Optional[List[str]] = attr.ib(default=[])
    install_cmd: Optional[List[str]] = attr.ib(default=[])
    post_install_cmd: Optional[List[str]] = attr.ib(default=[])
    nodes: Optional[NodeList] = attr.ib(default=None)

    @classmethod
    def create(cls):
        return cls(
            [],
            [],
            [],
            None
        )

    def add_pre_install(self, pre_install_cmds: List[str]):
        self.pre_install_cmd.extend(pre_install_cmds)

    def add_install(self, install_cmds: List[str]):
        self.install_cmd.extend(install_cmds)

    def add_post_install(self, post_install_cmds: List[str]):
        self.post_install_cmd.extend(post_install_cmds)

    def add_nodes(self, node_list: NodeList):
        self.nodes = node_list


class RemoteProvisioner(object):

    def __init__(self, config: ProvisionSet):
        self.config = config
        self.executor = concurrent.futures.ThreadPoolExecutor()
        self.tasks = set()

        self.file_output = logging.getLogger('pyformationlib.provisioner.output')
        self.file_output.propagate = False
        if self.config.nodes.working_dir:
            self.log_file = os.path.join(self.config.nodes.working_dir, 'provision.log')
            file_handler = logging.FileHandler(self.log_file)
            file_handler.setFormatter(CustomLogFormatter())
            self.file_output.addHandler(file_handler)
            self.file_output.setLevel(logging.DEBUG)
        else:
            self.log_file = None
            self.file_output.addHandler(logging.NullHandler())

    def run(self):
        if self.config.pre_install_cmd:
            self.exec(self.config.pre_install_cmd)
            self.join()
        self.exec(self.config.install_cmd)
        self.join()
        if self.config.post_install_cmd:
            self.exec(self.config.post_install_cmd)
            self.join()

    def dispatch(self, node: NodeEntry, command_list: List[str]):
        output = StringIO()
        last_exit = 0

        if node.use_private_ip:
            hostname = node.private_ip
        else:
            hostname = node.public_ip

        if not self.wait_port(hostname):
            raise ProvisionerError(f"Host {hostname} is not reachable")

        logger.info(f"Connection to {hostname} successful")

        for command in command_list:
            username = self.config.nodes.username
            ssh_key_file = self.config.nodes.ssh_key
            _command = self.resolve_variables(node, command)

            logger.info(f"Connecting to {hostname} as {username}")
            logger.debug(f"Using SSH key {ssh_key_file}")
            logger.debug(f"Running command: {_command}")

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname, username=username, key_filename=ssh_key_file)
            stdin, stdout, stderr = ssh.exec_command(_command)

            last_exit = stdout.channel.recv_exit_status()
            for line in stdout.readlines():
                output.write(line)
            for line in stderr.readlines():
                output.write(line)
            if last_exit != 0:
                break
        output.seek(0)
        return hostname, output, last_exit

    def exec(self, command_list: List[str]):
        for node in self.config.nodes.node_list:
            self.tasks.add(self.executor.submit(self.dispatch, node, command_list))

    def join(self):
        cmd_failed = False
        while self.tasks:
            done, self.tasks = concurrent.futures.wait(self.tasks, return_when=concurrent.futures.FIRST_COMPLETED)
            for task in done:
                try:
                    hostname, output, last_exit = task.result()
                    for line in output.readlines():
                        line_out = line.strip()
                        log_out = f"{hostname}: {line_out}"
                        logger.info(log_out)
                        self.file_output.info(log_out)
                    if last_exit != 0:
                        cmd_failed = True
                        logger.error("command returned non-zero result, see log for details")
                except Exception as err:
                    raise ProvisionerError(err)
        if cmd_failed:
            raise ProvisionerError("command returned non-zero result")

    @staticmethod
    def wait_port(address: str, port: int = 22, retry_count=300, factor=0.1):
        for retry_number in range(retry_count + 1):
            socket.setdefaulttimeout(1)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((address, port))
            sock.close()
            if result == 0:
                return True
            else:
                if retry_number == retry_count:
                    return False
                logger.info(f"Waiting for {address} to become reachable")
                wait = factor
                wait *= (2 ** (retry_number + 1))
                time.sleep(wait)

    def resolve_variables(self, node: NodeEntry, line: str):
        env = jinja2.Environment(undefined=jinja2.DebugUndefined)
        raw_template = env.from_string(line)
        formatted_value = raw_template.render(
            PRIVATE_IP_LIST=self.config.nodes.ip_csv_list(),
            NODE_ZONE=node.availability_zone,
        )
        return formatted_value
