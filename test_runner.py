# -*- coding: utf-8 -*-
"""
Google Colab 远程通道端到端验证脚本 (test_runner.py)
工作流演示：
1. 初始化 ColabExecutor 建立隧道连接
2. 远程检查 GPU 状态 (nvidia-smi)
3. 动态生成本地测试脚本 verify_env.py 并上传至 Colab 执行
4. 捕获输出并输出验证结果，支持按需触发停机销毁以防扣费
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Windows 终端 GBK 编码兼容保护
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from colab_executor import ColabExecutor, CommandExecutionError


def generate_local_test_script() -> str:
    """生成用于检验 Colab 硬件算力与深度学习环境的测试脚本代码"""
    test_code = """# -*- coding: utf-8 -*-
import sys
import platform
import time

print("=" * 60)
print("[*] Google Colab 远端环境与算力检测")
print("=" * 60)
print(f"操作系统   : {platform.platform()}")
print(f"Python版本 : {sys.version.split()[0]}")

try:
    import torch
    print(f"PyTorch版本: {torch.__version__}")
    cuda_available = torch.cuda.is_available()
    print(f"CUDA可用性 : {'[OK] 支持' if cuda_available else '[NO] 未开启'}")

    if cuda_available:
        device_count = torch.cuda.device_count()
        current_device = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(current_device)
        total_mem_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        print(f"GPU数量    : {device_count}")
        print(f"当前设备   : {device_name}")
        print(f"显存总容量 : {total_mem_gb:.2f} GB")

        # 快速矩阵乘法性能测试
        print('[*] 正在执行 GPU 矩阵乘法基准测试 (4096 x 4096 FP32)...')
        a = torch.randn(4096, 4096, device='cuda')
        b = torch.randn(4096, 4096, device='cuda')
        torch.cuda.synchronize()
        start = time.perf_counter()
        c = torch.matmul(a, b)
        torch.cuda.synchronize()
        duration_ms = (time.perf_counter() - start) * 1000
        print(f"[+] 矩阵乘法计算完成，耗时: {duration_ms:.2f} ms")
    else:
        print("[!] 警告: 当前运行时未启用 GPU 硬件加速，请在 Colab 菜单切换运行时类型为 GPU。")
except ImportError:
    print("[-] 未检测到 PyTorch 模块")

print("=" * 60)
print("[+] 远端环境验证完毕，执行通道运作正常！")
"""
    return test_code


def main():
    parser = argparse.ArgumentParser(
        description="Google Colab 远程执行验证工具 (基于 ColabExecutor)"
    )
    parser.add_argument(
        "--host",
        "-H",
        type=str,
        default=os.getenv("COLAB_HOST", ""),
        help="Colab 端生成的 TryCloudflare 域名 (例如: xxx.trycloudflare.com)",
    )
    parser.add_argument(
        "--key",
        "-k",
        type=str,
        default="~/.ssh/id_ed25519",
        help="本机 SSH 私钥路径，默认为 ~/.ssh/id_ed25519",
    )
    parser.add_argument(
        "--password",
        "-p",
        type=str,
        default=os.getenv("COLAB_PASSWORD", None),
        help="SSH 密码（若未配置私钥认证或需双因素认证时提供）",
    )
    parser.add_argument(
        "--terminate",
        action="store_true",
        help="测试完成后自动注销并销毁 Colab 实例，停止计费",
    )
    args = parser.parse_args()

    hostname = args.host.strip()
    if not hostname:
        hostname = input("请输入 Colab 端生成的临时域名 (*.trycloudflare.com): ").strip()
        if not hostname:
            print("错误: 域名不能为空！", file=sys.stderr)
            sys.exit(1)

    print(f"\n🚀 开始执行端到端验证流程，目标主机: {hostname}\n")

    # 1. 实例化执行器并建立连接（使用上下文管理器）
    try:
        with ColabExecutor(
            hostname=hostname,
            key_path=args.key,
            password=args.password,
        ) as executor:
            # 2. 检查 GPU 基础硬件状态
            print("\n" + "=" * 60)
            print("📊 步骤 1: 检查远程 GPU 状态 (nvidia-smi)")
            print("=" * 60)
            gpu_res = executor.run_command("nvidia-smi")
            if gpu_res["exit_code"] == 0:
                print(gpu_res["stdout"])
            else:
                print(f"⚠️ 无法获取 GPU 状态或非 GPU 实例: {gpu_res['stderr']}")

            # 3. 本地动态生成测试脚本
            print("=" * 60)
            print("📝 步骤 2: 动态生成本地验证脚本并上传至 Colab")
            print("=" * 60)

            with tempfile.TemporaryDirectory() as tmp_dir:
                local_script = Path(tmp_dir) / "verify_env.py"
                local_script.write_text(generate_local_test_script(), encoding="utf-8", newline="\n")
                remote_dest = "/content/workspace/verify_env.py"

                # 上传脚本
                executor.upload(str(local_script), remote_dest)

                # 4. 远程执行上传的脚本
                print("\n" + "=" * 60)
                print("⚡ 步骤 3: 远程调用 Python 执行验证脚本")
                print("=" * 60)
                exec_res = executor.run_command(
                    f"python3 {remote_dest}",
                    check=True,
                    print_output=True,
                )

            print("\n" + "=" * 60)
            print("🎯 步骤 4: 结果总结")
            print("=" * 60)
            print("✅ 恭喜！远程通道与执行调度器所有功能测试均已成功通过！")

            # 5. 可选停机防扣费
            if args.terminate:
                print("\n[!] 检测到 --terminate 参数，正在执行安全停机操作以防扣费...")
                executor.terminate_runtime()
            else:
                print("\n💡 提示: 实例仍然保持运行。如需释放 Colab 实例防止扣费，可在调用时追加 --terminate 参数，")
                print("   或在 Python 代码中执行 `executor.terminate_runtime()`。")

    except CommandExecutionError as e:
        print(f"\n❌ 命令执行发生异常:\n{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 执行器运行失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
