# -*- coding: utf-8 -*-
"""
本机立绘调度客户端 (generate_portrait_cli.py)
在本地运行此脚本：
1. 自动连接 Colab 远程算力节点
2. 下发立绘生成指令与提示词 (Prompt)
3. 远程 GPU (A100) 渲染完成后，自动通过 SFTP 将生成的立绘图片下发回本地电脑保存
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Windows 终端 GBK 编码保护
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from colab_executor import ColabExecutor, CommandExecutionError


def main():
    parser = argparse.ArgumentParser(description="Google Colab 远程立绘生成调度客户端 (Qwen-Image-2.1)")
    parser.add_argument("--host", "-H", type=str, required=True, help="Colab 端生成的 TryCloudflare 域名 (例如: xxx.trycloudflare.com)")
    parser.add_argument(
        "--prompt",
        "-p",
        type=str,
        default="1girl, solo, anime girl, beautiful detailed eyes, silver hair, elegant fantasy dress, dynamic pose, full body portrait, transparent background",
        help="角色立绘生成提示词 (英文/中文均可)",
    )
    parser.add_argument("--width", type=int, default=896, help="立绘宽度，默认 896")
    parser.add_argument("--height", type=int, default=1152, help="立绘高度，默认 1152")
    parser.add_argument("--steps", type=int, default=35, help="推理步数，默认 35")
    parser.add_argument("--model", type=str, default="Qwen/Qwen-Image-2.1", help="模型版本，可切换为加速版 Qwen/Qwen-Image-2.1-Turbo")
    parser.add_argument("--output-dir", "-o", type=str, default="./portraits", help="本地图片保存目录")
    parser.add_argument("--key", "-k", type=str, default="~/.ssh/id_ed25519", help="本机 SSH 私钥路径")
    parser.add_argument("--terminate", action="store_true", help="生图完成后自动销毁 Colab 实例防扣费")

    args = parser.parse_args()

    # 规范化本地保存目录
    local_out_dir = Path(args.output_dir).resolve()
    local_out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    local_image_path = local_out_dir / f"portrait_{timestamp}.png"
    remote_image_path = f"/content/workspace/portrait_{timestamp}.png"

    print("=" * 65)
    print("🚀 启动 Google Colab 远程角色立绘调度任务")
    print("=" * 65)
    print(f"• 远端主机   : {args.host}")
    print(f"• 提示词     : {args.prompt}")
    print(f"• 输出分辨率 : {args.width} x {args.height}")
    print(f"• 本地保存至 : {local_image_path}")
    print("=" * 65)

    try:
        with ColabExecutor(hostname=args.host, key_path=args.key) as executor:
            # 1. 确保远端推理脚本同步最新
            local_engine_script = Path(__file__).parent / "remote_qwen_image.py"
            remote_engine_dest = "/content/workspace/remote_qwen_image.py"

            if local_engine_script.exists():
                print("\n[*] 正在同步立绘推理引擎到 Colab...")
                executor.upload(str(local_engine_script), remote_engine_dest)

            # 2. 构造远程生图命令
            # 转义提示词中的双引号
            escaped_prompt = args.prompt.replace('"', '\\"')
            remote_cmd = (
                f'python3 {remote_engine_dest} '
                f'--prompt "{escaped_prompt}" '
                f'--output "{remote_image_path}" '
                f'--width {args.width} '
                f'--height {args.height} '
                f'--steps {args.steps} '
                f'--model "{args.model}"'
            )

            # 3. 执行远程推理并实时捕获日志
            print("\n⚡ 正在调用远端 GPU 算力执行立绘渲染...")
            executor.run_command(remote_cmd, timeout=1200, check=True, print_output=True)

            # 4. 下载生成的立绘图片回本地
            print(f"\n[*] 正在通过 SFTP 将生成的立绘下发至本地电脑...")
            executor.download(remote_image_path, str(local_image_path))

            print("\n" + "=" * 65)
            print(f"🎉 恭喜！角色立绘已成功下载至本地：")
            print(f"🖼️  文件路径: {local_image_path}")
            print("=" * 65)

            # 5. 可选防扣费停机
            if args.terminate:
                print("\n[!] 检测到 --terminate 参数，正在注销 Colab 实例防止扣费...")
                executor.terminate_runtime()

    except CommandExecutionError as e:
        print(f"\n❌ 远程执行报错:\n{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 调度过程发生错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
