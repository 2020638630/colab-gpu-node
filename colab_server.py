# -*- coding: utf-8 -*-
"""
Google Colab 远程端初始化脚本 (colab_server.py)
在 Google Colab 单元格中直接运行：
1. 静默安装 OpenSSH 服务与基础网络工具
2. 配置 SSH 密码认证与免密公钥
3. 下载并运行 Cloudflare Tunnel 进行反向穿透
4. 正则捕获分配的 trycloudflare.com 隧道地址并打印本机配置指引
"""

import os
import re
import secrets
import string
import subprocess
import sys
import time

# ==================== 用户配置区 ====================
# 已自动填入本机 SSH 公钥 (~/.ssh/id_ed25519.pub)，可直接免密连接
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBS14khO1tuKMQZU8dSzRg+ais7yeZRJeGqlNzPxF6KS aegis-y700-20261106"
SSH_PORT = 2222  # Colab 环境默认 SSH 端口为 2222

# 【进阶：固定域名模式】若在 Cloudflare Zero Trust 创建了命名隧道，在此处填入 Token
# 留空则自动降级为使用临时 Quick Tunnel (trycloudflare.com)
TUNNEL_TOKEN = "eyJhIjoiOTUxZjkwZmQ4MzkyNDA3MWNjMTY5NjQzMzZlYjlkMjMiLCJ0IjoiMjM2NjAzN2YtMmNjNC00OGMwLWI4OTMtNzcxYmZiMTFlNTEyIiwicyI6IllXWXdZVGxpTVRJdE1EVXdNaTAwTVRSakxUbGtPVFV0WXpnNE56RmxZbVE0WlRkbCJ9"
FIXED_HOSTNAME = ""  # 配合 Token 绑定的固定域名（例如 colab.yourdomain.com）
# ===================================================


def generate_random_password(length: int = 12) -> str:
    """生成包含大小写字母和数字的高强度随机密码"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_cmd(command: str, description: str = "") -> None:
    """静默执行 Shell 命令并检查状态"""
    if description:
        print(f"[*] {description}...")
    res = subprocess.run(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        print(f"[!] 错误: {description} 失败: {res.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


def setup_sshd(password: str, public_key: str, port: int = SSH_PORT) -> None:
    """安装并配置 OpenSSH 服务，生成 Host Keys 并确保端口本地存活"""
    run_cmd(
        "apt-get update -qq && apt-get install -y -qq openssh-server curl psmisc",
        "安装 OpenSSH Server 和工具包",
    )
    # 创建权限严格为 755 的权限隔离目录
    run_cmd("mkdir -p /var/run/sshd && chmod 0755 /var/run/sshd", "创建并配置 SSHD 权限隔离目录")

    # 关键：生成 Host Keys，避免因缺少主机密钥导致 sshd 启动失败
    run_cmd("ssh-keygen -A", "生成 SSH 服务端主机密钥 (Host Keys)")

    # 设置 root 密码
    run_cmd(f'echo "root:{password}" | chpasswd', "设置 root 用户临时密码")

    # 配置公钥免密登录
    authorized_keys_path = "/root/.ssh/authorized_keys"
    run_cmd("mkdir -p /root/.ssh && chmod 700 /root/.ssh", "配置 SSH 密钥目录")

    if public_key.strip():
        with open(authorized_keys_path, "w", encoding="utf-8") as f:
            f.write(public_key.strip() + "\n")
        run_cmd(f"chmod 600 {authorized_keys_path}", "写入免密公钥并设置 600 权限")
        print("[+] 成功写入指定的公钥，免密登录已就绪。")
    else:
        print("[-] 未提供 PUBLIC_KEY，将仅使用随机密码进行身份认证。")

    # 写入专属单独配置文件，并直接覆盖/清空旧冲突配置
    sshd_custom_config = f"""
Port {port}
ListenAddress 0.0.0.0
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding yes
PrintMotd no
StrictModes no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
"""
    os.makedirs("/etc/ssh/sshd_config.d", exist_ok=True)
    with open("/etc/ssh/sshd_config.d/colab_custom.conf", "w", encoding="utf-8") as f:
        f.write(sshd_custom_config.strip() + "\n")

    # 确保主配置文件中也有 Port 定义
    with open("/etc/ssh/sshd_config", "a", encoding="utf-8") as f:
        f.write(f"\nPort {port}\nPermitRootLogin yes\nPasswordAuthentication yes\nPubkeyAuthentication yes\nStrictModes no\n")

    # 强制杀死正在占用端口的旧进程
    subprocess.run(f"fuser -k {port}/tcp || true", shell=True)
    subprocess.run("pkill -9 sshd || true", shell=True)
    time.sleep(1)

    # 在 Colab / IPython 环境下以 system_raw 启动，防止被子进程捕获器回收
    started = False
    try:
        from IPython import get_ipython
        ipython = get_ipython()
        if ipython:
            ipython.system_raw(f"/usr/sbin/sshd -D -p {port} &")
            started = True
    except Exception:
        pass

    if not started:
        subprocess.Popen(["/usr/sbin/sshd", "-D", "-p", str(port)], start_new_session=True)

    # 本地自测：验证指定端口是否成功响应 SSH Banner
    print(f"[*] 正在执行本地 {port} 端口自检...")
    import socket
    banner_ok = False
    for attempt in range(10):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
                banner = s.recv(1024).decode(errors="ignore")
                if "SSH" in banner:
                    print(f"[+] 本地 SSH 服务已成功就绪并响应 (端口 {port}): {banner.strip()}")
                    banner_ok = True
                    break
        except Exception:
            time.sleep(0.5)

    if not banner_ok:
        # 如果依然未启动，执行诊断命令并打印详细错误
        diag = subprocess.run(f"/usr/sbin/sshd -d -p {port}", shell=True, capture_output=True, text=True)
        raise RuntimeError(
            f"本地 SSH 服务未能成功监听 {port} 端口！诊断日志:\n{diag.stderr.strip()}"
        )


def ensure_cloudflared() -> str:
    """下载并准备 cloudflared 二进制执行文件"""
    cloudflared_path = "/usr/local/bin/cloudflared"
    if not os.path.isfile(cloudflared_path):
        print("[*] 下载 Cloudflare Tunnel (cloudflared-linux-amd64)...")
        download_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        run_cmd(
            f"curl -fsSL -o {cloudflared_path} {download_url} && chmod +x {cloudflared_path}",
            "安装 cloudflared 到系统路径",
        )
    return cloudflared_path


def start_named_tunnel(
    cloudflared_path: str, token: str, timeout: int = 45
) -> subprocess.Popen:
    """启动 Cloudflare 命名隧道 (基于 Token 的永久固定域名模式)"""
    print("[*] 正在连接 Cloudflare 命名隧道 (Token 永久固定域名模式)...")
    log_path = "/tmp/cloudflared.log"
    log_file = open(log_path, "w", encoding="utf-8")

    cmd = [
        cloudflared_path,
        "tunnel",
        "--protocol",
        "http2",
        "--edge-ip-version",
        "4",
        "run",
        "--token",
        token.strip(),
    ]

    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    start_time = time.time()
    while time.time() - start_time < timeout:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if "Registered tunnel connection" in content or "registered" in content.lower():
                    print("[+] Cloudflare 命名隧道已成功连接并完成边缘注册！")
                    return process
        if process.poll() is not None:
            break
        time.sleep(0.5)

    print("[*] 隧道已发起启动连接...")
    return process


def start_tunnel_and_extract_url(
    cloudflared_path: str, port: int = SSH_PORT, timeout: int = 45
) -> tuple[str, subprocess.Popen]:
    """启动 cloudflared 临时 SSH 隧道并等待边缘注册完成（强制 HTTP2 + IPv4 以适配 Colab 网络环境）"""
    print(f"[*] 正在启动 Cloudflare Quick Tunnel 穿透本地 {port} 端口 (强制 HTTP/2 + IPv4)...")
    log_path = "/tmp/cloudflared.log"
    log_file = open(log_path, "w", encoding="utf-8")

    # 关键参数：--protocol http2 规避 Google Cloud 对 UDP/QUIC 的丢包限制
    # --edge-ip-version 4 规避 IPv6 双栈挂起
    cmd = [
        cloudflared_path,
        "tunnel",
        "--protocol",
        "http2",
        "--edge-ip-version",
        "4",
        "--url",
        f"tcp://127.0.0.1:{port}",
    ]

    # 将 stdout 和 stderr 全部重定向到磁盘文件，彻底杜绝管道缓冲区写满导致的死锁
    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    url_pattern = re.compile(r"https://([a-zA-Z0-9-]+\.trycloudflare\.com)")
    extracted_host = None
    start_time = time.time()

    while time.time() - start_time < timeout:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if not extracted_host:
                    match = url_pattern.search(content)
                    if match:
                        extracted_host = match.group(1)
                        print(f"[+] 捕获到隧道临时域名: {extracted_host}")
                        print("[*] 正在等待 Cloudflare 边缘节点路由注册生效 (Registered connection)...")

                # 确认边缘连接已成功注册
                if extracted_host and ("Registered tunnel connection" in content or "registered" in content.lower()):
                    print("[+] Cloudflare 边缘路由注册成功，通道已全面就绪！")
                    break

        if process.poll() is not None:
            break
        time.sleep(0.5)

    if not extracted_host:
        process.kill()
        error_details = ""
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                error_details = f.read()
        raise TimeoutError(
            f"在 {timeout} 秒内未能成功解析 Cloudflare Tunnel 分配的域名。\n"
            f"--- 错误日志 ---\n{error_details}"
        )

    return extracted_host, process


def print_connection_guide(hostname: str, password: str, has_pubkey: bool) -> None:
    """打印美化后的连接指引与本机配置模板"""
    banner = "=" * 70
    print("\n" + banner)
    print("🚀 Google Colab 远程通道就绪 (OpenSSH + Cloudflare Tunnel)")
    print(banner)
    print(f"• 远端主机域名 : {hostname}")
    print(f"• 登录用户名   : root")
    print(f"• 临时认证密码 : {password}")
    print(f"• 公钥免密认证 : {'已启用' if has_pubkey else '未启用（仅密码）'}")
    print("-" * 70)
    print("【方法 1】本机快速命令行直连测试：")
    print(
        f'ssh -o ProxyCommand="cloudflared access ssh --hostname {hostname}" root@{hostname}'
    )
    print("-" * 70)
    print("【方法 2】本机 ~/.ssh/config 配置模板（推荐，可直接 ssh colab 登录）：")
    config_tpl = f"""
Host colab
    HostName {hostname}
    User root
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    ProxyCommand cloudflared access ssh --hostname %h
"""
    print(config_tpl.strip())
    print("-" * 70)
    print("【方法 3】使用 ColabExecutor Python 客户端接入：")
    print("```python")
    print("from colab_executor import ColabExecutor")
    print(f'executor = ColabExecutor(hostname="{hostname}")')
    print("with executor:")
    print('    res = executor.run_command("nvidia-smi")')
    print("    print(res['stdout'])")
    print("```")
    print(banner)
    print("💡 提示: 请保持当前 Colab 单元格持续运行，中断该单元格将关闭隧道连接。\n")


def main():
    temp_password = generate_random_password(12)
    has_pubkey = bool(PUBLIC_KEY.strip())

    # 优先装载 Google Drive，实现模型文件持久化永不丢失
    try:
        from google.colab import drive
        print("[*] 正在装载 Google 云端硬盘 (实现大模型持久化存储)...")
        drive.mount('/content/drive')
        print("[+] Google 云端硬盘已成功挂载至 /content/drive/MyDrive")
    except Exception as e:
        print(f"[-] 跳过 Google 云端硬盘挂载: {e}")

    setup_sshd(temp_password, PUBLIC_KEY, port=SSH_PORT)
    cloudflared_path = ensure_cloudflared()

    try:
        if TUNNEL_TOKEN.strip():
            tunnel_proc = start_named_tunnel(cloudflared_path, TUNNEL_TOKEN)
            hostname = FIXED_HOSTNAME.strip() or "自定义固定域名(在Cloudflare绑定的域名)"
        else:
            hostname, tunnel_proc = start_tunnel_and_extract_url(
                cloudflared_path, port=SSH_PORT, timeout=45
            )

        print_connection_guide(hostname, temp_password, has_pubkey)

        # 持续监控日志文件保持前台运行，并实时回显关键连接状态
        log_path = "/tmp/cloudflared.log"
        last_pos = os.path.getsize(log_path) if os.path.exists(log_path) else 0

        while tunnel_proc.poll() is None:
            if os.path.exists(log_path):
                curr_size = os.path.getsize(log_path)
                if curr_size > last_pos:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        new_data = f.read()
                        last_pos = curr_size
                        for line in new_data.splitlines():
                            # 仅打印连接握手与关键事件
                            if any(k in line.lower() for k in ["registered", "connection", "error", "failed"]):
                                print(f"[tunnel] {line.strip()}", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] 接收到中断信号，正在关闭 Cloudflare Tunnel...")
        if "tunnel_proc" in locals() and tunnel_proc:
            tunnel_proc.terminate()
    except Exception as e:
        print(f"\n[!] 初始化发生异常: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
