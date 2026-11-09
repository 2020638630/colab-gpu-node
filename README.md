# Google Colab 远程执行通道与调度客户端

本方案将 Google Colab（Pro / Free 运行时，具备 GPU 算力）转化为本机的安全远程计算节点，支持：
- **免公网 IP / 端口映射**：借助 Cloudflare Tunnel 快速建立反向 SSH 隧道，无需公网 IP 和复杂配置。
- **免密安全认证**：支持本机的 Ed25519 / RSA 公钥免密认证，兼顾随机 12 位密码认证。
- **Python 调度客户端 (`ColabExecutor`)**：类似标准执行引擎，支持命令执行、状态码与日志回显、SFTP 双向文件/目录递归传输。
- **自动防扣费停机保护**：支持脚本任务完成后调用 Colab 原生 `runtime.unassign()` 立即销毁实例，停止扣除计算单元。

---

## 目录结构

```text
Google-Colab/
├── colab_server.py      # 【Colab 端】运行在 Colab Notebook 中的服务初始化与隧道启动脚本
├── colab_executor.py    # 【本机端】生产级 Python 执行器与调度客户端
├── test_runner.py       # 【本机端】端到端连通性与硬件性能验证脚本
├── requirements.txt     # 本机 Python 依赖 (paramiko)
└── README.md            # 项目使用说明
```

---

## 一、本机前置准备

### 1. 安装 Cloudflare CLI (`cloudflared`)
`cloudflared` 负责将 SSH 请求通过 Cloudflare 边缘节点代理转发至 Colab 端：

- **Windows**:
  ```powershell
  winget install Cloudflare.cloudflared
  # 或者使用 scoop
  scoop install cloudflared
  ```
  > 安装后打开新的终端窗口，运行 `cloudflared --version` 确保其已进入系统 `PATH`。

- **macOS**:
  ```bash
  brew install cloudflared
  ```

- **Linux (Ubuntu/Debian)**:
  ```bash
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
  sudo apt-get update && sudo apt-get install cloudflared
  ```

### 2. 生成本机 SSH 密钥对（若已有可跳过）
```bash
ssh-keygen -t ed25519 -C "colab_executor"
# 默认私钥位于 ~/.ssh/id_ed25519，公钥为 ~/.ssh/id_ed25519.pub
```
查看并复制你的公钥文本：
- **Windows**: `type ~\.ssh\id_ed25519.pub`
- **Linux / macOS**: `cat ~/.ssh/id_ed25519.pub`

### 3. 安装本地 Python 依赖
```bash
pip install -r requirements.txt
```

---

## 二、Colab 远程端部署

1. 在浏览器打开 [Google Colab](https://colab.research.google.com/) 并新建 Notebook。
2. 在菜单栏选择 **代码执行程序 -> 更改运行时类型**，选择 **GPU**（如 T4、V100 或 A100）并保存。
3. 新建一个代码单元格，将 `colab_server.py` 的内容复制进去。
4. 将你的公钥文本粘贴在脚本顶部的 `PUBLIC_KEY = "ssh-ed25519 ..."` 变量中。
5. 点击运行单元格，约 15~20 秒后输出如下信息：
   ```text
   ======================================================================
   🚀 Google Colab 远程通道就绪 (OpenSSH + Cloudflare Tunnel)
   ======================================================================
   • 远端主机域名 : random-words-xxx.trycloudflare.com
   • 登录用户名   : root
   • 临时认证密码 : xxxx-xxxx
   • 公钥免密认证 : 已启用
   ...
   ```
   > ⚠️ **注意**：请保持该 Colab 单元格持续运行，关闭单元格将停止隧道服务。

---

## 三、本机端使用指南

### 1. 运行端到端验证脚本 (`test_runner.py`)
使用提取到的 `trycloudflare.com` 域名执行测试：

```bash
# 基本验证（保持实例运行）
python test_runner.py --host random-words-xxx.trycloudflare.com

# 测试完成后自动销毁实例防扣费
python test_runner.py --host random-words-xxx.trycloudflare.com --terminate
```

验证脚本将自动：
1. 测试底层 SSH 隧道通畅性。
2. 远程调用 `nvidia-smi` 打印 GPU 状态。
3. 在本地动态生成 `verify_env.py`，上传至 Colab `/content/workspace/verify_env.py`。
4. 运行 GPU 矩阵乘法性能测试并实时捕获输出。

### 2. 在自己的 Python 任务中集成 `ColabExecutor`

```python
from colab_executor import ColabExecutor

# 1. 初始化执行器
executor = ColabExecutor(
    hostname="random-words-xxx.trycloudflare.com",
    username="root",
    key_path="~/.ssh/id_ed25519",  # 本机私钥
)

# 2. 使用上下文管理器执行任务
with executor:
    # (a) 检查显卡
    res = executor.run_command("nvidia-smi", print_output=True)

    # (b) 上传本地训练代码及数据文件夹
    executor.upload("./my_model", "/content/workspace/my_model")

    # (c) 执行远程训练脚本（带超时保护与异常检查）
    executor.run_command(
        "cd /content/workspace/my_model && python3 train.py --epochs 20",
        timeout=3600,
        check=True,
        print_output=True,
    )

    # (d) 下载远端产物 (权重权重文件或日志目录)
    executor.download("/content/workspace/my_model/checkpoints", "./output_weights")

    # (e) 任务完成，主动销毁 Colab 运行时，杜绝多余计费
    executor.terminate_runtime()
```

---

## 四、核心 API 接口说明

| 方法 | 参数 | 描述 |
| :--- | :--- | :--- |
| `ColabExecutor(...)` | `hostname`, `username='root'`, `key_path='~/.ssh/id_ed25519'`, `password=None` | 初始化执行器并配置 Cloudflare 代理参数 |
| `run_command(...)` | `command`, `timeout=300`, `check=False`, `print_output=False` | 执行远端 Bash 命令，返回 `{'exit_code', 'stdout', 'stderr'}` |
| `upload(...)` | `local_path`, `remote_path` | 通过 SFTP 上传单个文件或递归上传整个文件夹，自动在远端创建缺失目录 |
| `download(...)` | `remote_path`, `local_path` | 通过 SFTP 下载单个文件或递归下载整个文件夹到本地 |
| `terminate_runtime()` | 无 | 触发 Colab 内置注销命令释放运行时，防止持续扣除计算单元 |

---

## 五、常见问题与调试建议

1. **`cloudflared: command not found` 错误**
   - 确保安装后关闭并重新启动当前终端或 IDE，以使环境变量生效。
   - 在终端直接输入 `cloudflared --version` 验证。

2. **连接超时或被拒绝**
   - 检查 Colab 页面中的执行单元格是否仍在运行；Colab 页面刷新或掉线可能会重置临时域名。
   - Cloudflare Quick Tunnel 域名为临时分配，每次重新运行单元格都会产生一个全新的域名。

3. **长时间训练断网防护**
   - 对于耗时数小时的超大任务，建议在远端结合 `nohup` 或 `tmux` 运行：
     ```python
     executor.run_command("nohup python3 train.py > train.log 2>&1 &")
     ```
   - 可在训练脚本末尾写入调用 `from google.colab import runtime; runtime.unassign()`，使训练正常完成或失败异常时都能自动停机。
