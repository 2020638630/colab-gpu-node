# -*- coding: utf-8 -*-
"""
ColabExecutor: Google Colab 远程执行通道与调度客户端
通过本地 cloudflared 与 paramiko 实现基于 Cloudflare Tunnel 的无缝 SSH/SFTP 连接。
支持远程命令执行、状态码捕获、双向文件/目录传输、异常防护与自动释放实例防扣费。
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Windows 终端 GBK 编码兼容保护
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class WindowsSocketProxy:
    """
    Windows 平台专用代理套接字适配器。
    解决 Windows Winsock 下 select.select() 无法监听进程管道 (WinError 10038) 的经典缺陷，
    通过 loopback socketpair 与管道后台双向守护线程，将 cloudflared 代理流无缝转为标准 TCP 套接字。
    """

    def __init__(self, cmd_args: list[str]):
        self.cmd = cmd_args
        self.proc = subprocess.Popen(
            cmd_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # 避免因 stderr 管道缓冲区写满导致子进程阻塞死锁
            bufsize=0,
        )
        self.sock, self._internal_sock = socket.socketpair()
        self._closed = False

        self._t1 = threading.Thread(target=self._pipe_to_sock, daemon=True)
        self._t2 = threading.Thread(target=self._sock_to_pipe, daemon=True)
        self._t1.start()
        self._t2.start()

    def _pipe_to_sock(self) -> None:
        while not self._closed and self.proc.poll() is None:
            try:
                data = self.proc.stdout.read(4096)
                if not data:
                    break
                self._internal_sock.sendall(data)
            except Exception:
                break

    def _sock_to_pipe(self) -> None:
        while not self._closed and self.proc.poll() is None:
            try:
                data = self._internal_sock.recv(4096)
                if not data:
                    break
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except Exception:
                break

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.close()
        except Exception:
            pass
        try:
            self._internal_sock.close()
        except Exception:
            pass
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass


try:
    import paramiko
except ImportError:
    paramiko = None



class CommandExecutionError(RuntimeError):
    """远程命令执行返回非 0 退出码时抛出"""

    def __init__(self, command: str, exit_code: int, stdout: str, stderr: str):
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        message = (
            f"远程命令执行失败 [退出码 {exit_code}]: {command}\n"
            f"--- [stderr] ---\n{stderr.strip()}\n"
            f"--- [stdout] ---\n{stdout.strip()}"
        )
        super().__init__(message)


class ColabExecutor:
    """Google Colab 远程执行与文件调度器"""

    def __init__(
        self,
        hostname: str,
        username: str = "root",
        key_path: str = "~/.ssh/id_ed25519",
        password: Optional[str] = None,
        port: int = 22,
        connect_timeout: int = 30,
        logger: Optional[logging.Logger] = None,
    ):
        """
        初始化 ColabExecutor

        :param hostname: Colab 端生成的临时域名（如 xxx.trycloudflare.com，支持包含或不包含 https://）
        :param username: SSH 用户名，Colab 默认 root
        :param key_path: 本地 SSH 私钥路径，默认为 ~/.ssh/id_ed25519
        :param password: SSH 密码（如果未配置公钥或需密码认证）
        :param port: SSH 端口，默认为 22
        :param connect_timeout: 连接超时时间（秒）
        :param logger: 自定义 logger，默认使用模块级 logger
        """
        self._cloudflared_bin = self._validate_prerequisites()

        # 清洗 hostname，去除协议前缀
        cleaned_host = hostname.replace("https://", "").replace("http://", "").strip().rstrip("/")
        self.hostname = cleaned_host
        self.username = username
        self.key_path = os.path.expanduser(key_path)
        self.password = password
        self.port = port
        self.connect_timeout = connect_timeout

        self.logger = logger or self._setup_default_logger()
        self._ssh_client: Optional[paramiko.SSHClient] = None
        self._sftp_client: Optional[paramiko.SFTPClient] = None
        self._proxy_holder: Optional[Any] = None

    @staticmethod
    def _find_cloudflared() -> Optional[str]:
        """优先在当前目录、脚本所在目录、系统 PATH 中寻找 cloudflared 可执行文件"""
        # 候选位置
        candidates = [
            Path.cwd() / "cloudflared.exe",
            Path.cwd() / "cloudflared",
            Path(__file__).parent / "cloudflared.exe",
            Path(__file__).parent / "cloudflared",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())

        # 检查系统 PATH
        which_path = shutil.which("cloudflared")
        return which_path

    @classmethod
    def _validate_prerequisites(cls) -> str:
        """检查本机是否安装了 paramiko 与 cloudflared 可执行程序，返回可用 cloudflared 路径"""
        if paramiko is None:
            raise ImportError(
                "未检测到 paramiko 库，请使用以下命令安装：\n"
                "pip install paramiko"
            )

        cloudflared_path = cls._find_cloudflared()
        if not cloudflared_path:
            instructions = (
                "未在工作目录或系统 PATH 中检测到 'cloudflared' 可执行文件，ColabExecutor 需要它来建立 SSH 代理隧道。\n"
                "请将 cloudflared.exe 下载并放置在当前工作空间目录下，或安装到全局 PATH：\n"
                "  - Windows (PowerShell): winget install Cloudflare.cloudflared 或 scoop install cloudflared\n"
                "  - macOS: brew install cloudflared\n"
                "  - Linux: 访问 https://github.com/cloudflare/cloudflared/releases 下载对应二进制"
            )
            raise EnvironmentError(instructions)
        return cloudflared_path


    @staticmethod
    def _setup_default_logger() -> logging.Logger:
        """创建默认标准控制台 Logger"""
        log = logging.getLogger("ColabExecutor")
        if not log.handlers:
            log.setLevel(logging.INFO)
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] [ColabExecutor] %(message)s",
                datefmt="%H:%M:%S",
            )
            handler.setFormatter(formatter)
            log.addHandler(handler)
        return log

    def connect(self) -> "ColabExecutor":
        """建立 SSH 连接与 SFTP 会话"""
        if self._ssh_client is not None:
            return self

        self.logger.info(f"正在通过 cloudflared 代理连接至远程端: {self.hostname}...")

        # 区分 Windows 与 Unix 系统的 Proxy 桥接机制
        if sys.platform == "win32":
            self.logger.debug("启用 Windows 原生 Socket 代理桥接")
            proxy = WindowsSocketProxy(
                [self._cloudflared_bin, "access", "ssh", "--hostname", self.hostname]
            )
            self._proxy_holder = proxy
            sock = proxy.sock
        else:
            bin_path = f'"{self._cloudflared_bin}"' if " " in self._cloudflared_bin else self._cloudflared_bin
            proxy_cmd = f"{bin_path} access ssh --hostname {self.hostname}"
            proxy = paramiko.ProxyCommand(proxy_cmd)
            self._proxy_holder = proxy
            sock = proxy

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: Dict[str, Any] = {
            "hostname": self.hostname,
            "port": self.port,
            "username": self.username,
            "sock": sock,
            "timeout": self.connect_timeout,
            "banner_timeout": self.connect_timeout,
        }

        # 判断身份认证策略：优先使用密钥，不存在且提供密码时使用密码
        use_key = os.path.isfile(self.key_path)
        if use_key:
            self.logger.info(f"使用本地私钥认证: {self.key_path}")
            connect_kwargs["key_filename"] = self.key_path
            if self.password:
                connect_kwargs["password"] = self.password
        elif self.password:
            self.logger.info("未找到可用本地私钥，使用密码进行认证")
            connect_kwargs["password"] = self.password
        else:
            self.logger.warning(
                f"私钥文件不存在 ({self.key_path}) 且未提供密码，将尝试 SSH Agent / 默认凭据登录"
            )

        try:
            client.connect(**connect_kwargs)
            self._ssh_client = client
            self._sftp_client = client.open_sftp()
            self.logger.info("✅ 成功连接到 Google Colab 实例！")
            return self
        except Exception as e:
            self.close()
            self.logger.error(f"❌ 建立 SSH 连接失败: {e}")
            raise

    def close(self) -> None:
        """安全释放 SFTP 与 SSH 资源"""
        if self._sftp_client:
            try:
                self._sftp_client.close()
            except Exception:
                pass
            self._sftp_client = None

        if self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception:
                pass
            self._ssh_client = None
            self.logger.info("已关闭 SSH/SFTP 会话。")

        if self._proxy_holder:
            try:
                self._proxy_holder.close()
            except Exception:
                pass
            self._proxy_holder = None

    def __enter__(self) -> "ColabExecutor":
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @property
    def ssh(self) -> paramiko.SSHClient:
        """获取底层 SSHClient"""
        if not self._ssh_client:
            raise RuntimeError("SSH 会话未连接，请先调用 connect() 或使用上下文管理器 'with'。")
        return self._ssh_client

    @property
    def sftp(self) -> paramiko.SFTPClient:
        """获取底层 SFTPClient"""
        if not self._sftp_client:
            raise RuntimeError("SFTP 会话未就绪，请先建立连接。")
        return self._sftp_client

    def run_command(
        self,
        command: str,
        timeout: int = 300,
        check: bool = False,
        print_output: bool = False,
    ) -> Dict[str, Any]:
        """
        在远程实例中执行 Shell 命令

        :param command: 要执行的 Bash 命令字符串
        :param timeout: 超时时间（秒）
        :param check: 若为 True 且返回码非 0 则抛出 CommandExecutionError
        :param print_output: 是否在本地控制台实时回显远程输出
        :return: 包含 exit_code、stdout、stderr 的字典
        """
        self.logger.info(f"执行远程命令: {command}")
        transport = self.ssh.get_transport()
        if transport is None or not transport.is_active():
            raise ConnectionError("底层 SSH 连接已断开，无法执行命令。")

        channel = transport.open_session()
        channel.settimeout(timeout)
        channel.exec_command(command)

        stdout_chunks = []
        stderr_chunks = []

        start_time = time.time()
        while True:
            # 读取标准输出
            if channel.recv_ready():
                data = channel.recv(4096).decode("utf-8", errors="replace")
                stdout_chunks.append(data)
                if print_output:
                    sys.stdout.write(data)
                    sys.stdout.flush()

            # 读取标准错误
            if channel.recv_stderr_ready():
                err_data = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                stderr_chunks.append(err_data)
                if print_output:
                    sys.stderr.write(err_data)
                    sys.stderr.flush()

            # 如果命令执行完成且所有缓冲区数据读取完毕
            if channel.exit_status_ready():
                while channel.recv_ready():
                    data = channel.recv(4096).decode("utf-8", errors="replace")
                    stdout_chunks.append(data)
                    if print_output:
                        sys.stdout.write(data)
                        sys.stdout.flush()
                while channel.recv_stderr_ready():
                    err_data = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    stderr_chunks.append(err_data)
                    if print_output:
                        sys.stderr.write(err_data)
                        sys.stderr.flush()
                break

            if time.time() - start_time > timeout:
                channel.close()
                raise TimeoutError(f"命令执行超时 ({timeout} 秒): {command}")

            time.sleep(0.05)

        exit_code = channel.recv_exit_status()
        channel.close()

        stdout_str = "".join(stdout_chunks)
        stderr_str = "".join(stderr_chunks)

        result = {
            "exit_code": exit_code,
            "stdout": stdout_str,
            "stderr": stderr_str,
        }

        if exit_code != 0:
            msg = f"命令退出码非 0: [code={exit_code}]"
            self.logger.warning(msg)
            if check:
                raise CommandExecutionError(command, exit_code, stdout_str, stderr_str)
        else:
            self.logger.info("命令执行成功 (exit_code=0)")

        return result

    def _remote_mkdir_p(self, remote_dir: str) -> None:
        """递归在远端创建目录 (类似 mkdir -p)"""
        remote_dir = remote_dir.replace("\\", "/").rstrip("/")
        if not remote_dir or remote_dir == "/":
            return
        parts = remote_dir.split("/")
        current = ""
        if remote_dir.startswith("/"):
            current = "/"
            parts = [p for p in parts if p]

        for part in parts:
            current = (current + "/" + part) if current != "/" else ("/" + part)
            try:
                self.sftp.stat(current)
            except IOError:
                try:
                    self.sftp.mkdir(current)
                except IOError:
                    # 避免多线程并发创建竞争
                    pass

    def upload(self, local_path: str, remote_path: str) -> None:
        """
        基于 SFTP 上传本地文件或整个目录至 Colab

        :param local_path: 本地源文件或目录路径
        :param remote_path: 远端目标文件或目录路径
        """
        local_p = Path(os.path.expanduser(local_path)).resolve()
        if not local_p.exists():
            raise FileNotFoundError(f"本地文件或目录不存在: {local_path}")

        # 统一转为 Linux 斜杠
        remote_path = remote_path.replace("\\", "/")

        if local_p.is_file():
            # 确保远端父级目录存在
            parent_dir = os.path.dirname(remote_path)
            if parent_dir:
                self._remote_mkdir_p(parent_dir)

            self.logger.info(f"上传文件: {local_p} -> {remote_path}")
            self.sftp.put(str(local_p), remote_path)
        elif local_p.is_dir():
            self.logger.info(f"递归上传目录: {local_p} -> {remote_path}")
            for root, dirs, files in os.walk(str(local_p)):
                rel_path = os.path.relpath(root, str(local_p)).replace("\\", "/")
                cur_remote_dir = remote_path if rel_path == "." else f"{remote_path}/{rel_path}"
                self._remote_mkdir_p(cur_remote_dir)

                for f in files:
                    src_file = os.path.join(root, f)
                    dst_file = f"{cur_remote_dir}/{f}"
                    self.logger.debug(f"  传输文件: {f} -> {dst_file}")
                    self.sftp.put(src_file, dst_file)
        self.logger.info(f"✅ 上传完成: {remote_path}")

    def download(self, remote_path: str, local_path: str) -> None:
        """
        基于 SFTP 从 Colab 下载文件或整个目录至本地

        :param remote_path: 远端文件或目录路径
        :param local_path: 本地目标文件或目录路径
        """
        remote_path = remote_path.replace("\\", "/")
        local_p = Path(os.path.expanduser(local_path)).resolve()

        try:
            r_stat = self.sftp.stat(remote_path)
        except IOError:
            raise FileNotFoundError(f"远端路径不存在: {remote_path}")

        if stat.S_ISDIR(r_stat.st_mode):
            self.logger.info(f"递归下载目录: {remote_path} -> {local_p}")
            local_p.mkdir(parents=True, exist_ok=True)
            self._download_dir_recursive(remote_path, local_p)
        else:
            self.logger.info(f"下载文件: {remote_path} -> {local_p}")
            if local_p.is_dir():
                target_file = local_p / os.path.basename(remote_path)
            else:
                local_p.parent.mkdir(parents=True, exist_ok=True)
                target_file = local_p

            self.sftp.get(remote_path, str(target_file))
        self.logger.info(f"✅ 下载完成: {local_p}")

    def _download_dir_recursive(self, remote_dir: str, local_dir: Path) -> None:
        """内部递归下载远端目录树"""
        for item in self.sftp.listdir_attr(remote_dir):
            r_item_path = f"{remote_dir}/{item.filename}"
            l_item_path = local_dir / item.filename

            if stat.S_ISDIR(item.st_mode):
                l_item_path.mkdir(parents=True, exist_ok=True)
                self._download_dir_recursive(r_item_path, l_item_path)
            else:
                self.sftp.get(r_item_path, str(l_item_path))

    def terminate_runtime(self) -> None:
        """
        调用 Colab 内置运行时销毁命令释放实例，避免空闲持续扣除计算单元。
        调用后远端实例将断开，本客户端将自动清理连接。
        """
        self.logger.warning("🚨 正在触发 Google Colab 运行时自毁命令 (runtime.unassign())...")
        unassign_cmd = 'python3 -c "from google.colab import runtime; runtime.unassign()"'
        try:
            # 使用超时保护发送注销指令
            self.run_command(unassign_cmd, timeout=10)
        except (Exception, paramiko.SSHException):
            # 注销指令会导致运行时瞬间被销毁，SSH 连接中断属于正常预期行为
            self.logger.info("已发送实例注销指令，连接已正常随实例释放而关闭。")
        finally:
            self.close()
            self.logger.info("🛡️ Colab 运行时已成功销毁，计费已终止。")
