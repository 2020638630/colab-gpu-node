# -*- coding: utf-8 -*-
"""
Colab 远端立绘与史诗场景生成服务引擎 (remote_qwen_image.py)
基于 Qwen-Image-2.1 / Diffusers 官方管道实现：
- 自动检测并重建 Google Drive 持久化缓存至本地高速 POSIX 文件系统，彻底杜绝 FUSE 软链接 79 字节损坏问题
- 支持史诗横屏场景（1280x768 / 1024x1024）与角色立绘（896x1152）
- 启用 bfloat16 与 enable_model_cpu_offload()，在 24GB L4 GPU 上稳定高效推理
"""

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
import torch

DRIVE_HF_ROOT = "/content/drive/MyDrive/.cache/huggingface"
LOCAL_MODEL_ROOT = "/content/models"


def ensure_environment():
    """检查并确保必要的深度学习依赖库"""
    try:
        import diffusers
        import transformers
        import accelerate
    except ImportError:
        print("[*] 正在安装最新版 Diffusers 与 Transformers 依赖包...")
        os.system("pip install -q accelerate git+https://github.com/huggingface/diffusers.git transformers>=4.48.0")


def prepare_qwen_model(model_id: str) -> str:
    """
    检查模型持久化目录，必要时自适应解决 Google Drive FUSE 软链接指针问题，
    在本地 /content/models/ 建立真实 POSIX 符号链接，零耗时秒级挂载。
    """
    model_name = model_id.split("/")[-1]
    local_dir = os.path.join(LOCAL_MODEL_ROOT, model_name)
    
    # 检查本地是否已经完成准备
    test_st = os.path.join(local_dir, "transformer", "diffusion_pytorch_model-00001-of-00002.safetensors")
    if os.path.exists(test_st) and os.path.getsize(test_st) > 1024 * 1024:
        print(f"[+] 本地模型路径已就绪: {local_dir}")
        return local_dir

    drive_repo = os.path.join(DRIVE_HF_ROOT, f"models--{model_id.replace('/', '--')}")
    snapshots_dir = os.path.join(drive_repo, "snapshots")
    blobs_root = os.path.join(DRIVE_HF_ROOT, "blobs")

    if os.path.exists(snapshots_dir):
        # 寻找最新快照哈希目录
        snaps = [os.path.join(snapshots_dir, s) for s in os.listdir(snapshots_dir) if os.path.isdir(os.path.join(snapshots_dir, s))]
        if snaps:
            latest_snap = snaps[0]
            print(f"[*] 检测到 Google Drive 永久存储的模型权重: {latest_snap}")
            print(f"[*] 正在为本地 NVMe 磁盘挂载 POSIX 链接至: {local_dir}...")
            os.makedirs(local_dir, exist_ok=True)
            
            for root, dirs, files in os.walk(latest_snap):
                rel_dir = os.path.relpath(root, latest_snap)
                target_dir = os.path.join(local_dir, rel_dir) if rel_dir != "." else local_dir
                os.makedirs(target_dir, exist_ok=True)
                
                for f in files:
                    src_f = os.path.join(root, f)
                    dst_f = os.path.join(target_dir, f)
                    if os.path.exists(dst_f) or os.path.islink(dst_f):
                        os.remove(dst_f)
                    
                    sz = os.path.getsize(src_f)
                    is_pointer = False
                    blob_rel = None
                    if sz < 200:
                        try:
                            with open(src_f, "r", errors="ignore") as fp:
                                txt = fp.read().strip()
                            if "blobs/" in txt:
                                is_pointer = True
                                blob_rel = txt.split("blobs/")[-1]
                        except Exception:
                            pass
                            
                    if is_pointer and blob_rel:
                        real_blob = os.path.join(blobs_root, blob_rel)
                        if os.path.exists(real_blob):
                            os.symlink(real_blob, dst_f)
                        else:
                            print(f"[!] 警告: 未找到 blob 目标: {real_blob}")
                    else:
                        shutil.copy2(src_f, dst_f)
                        
            print(f"[+] 成功秒级构建本地符号映射: {local_dir}")
            return local_dir

    print(f"[*] 未在 Google Drive 发现完整本地模型，将通过 Hugging Face Hub 加载: {model_id}...")
    return model_id


def generate(
    prompt: str,
    output_path: str,
    model_id: str = "Qwen/Qwen-Image-2.1",
    width: int = 1280,
    height: int = 768,
    steps: int = 30,
    seed: int = -1,
):
    ensure_environment()
    print("=" * 60)
    print("🎨 史诗场景与角色渲染引擎启动 (Qwen-Image-2.1)")
    print("=" * 60)
    print(f"• 输出分辨率 : {width} x {height}")
    print(f"• 采样步数   : {steps}")
    print(f"• 提示词     : {prompt}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "cuda":
        print("[!] 警告: 未检测到 GPU，建议在 Colab 启用 GPU 运行时。")

    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16

    load_target = prepare_qwen_model(model_id)

    from diffusers import QwenImage21Pipeline

    print(f"\n[*] 正在从 {load_target} 加载 QwenImage21Pipeline (dtype={dtype})...")
    pipe = QwenImage21Pipeline.from_pretrained(
        load_target,
        torch_dtype=dtype,
    )
    print("[*] 启用 Model CPU Offload 显存调度保护...")
    pipe.enable_model_cpu_offload()

    generator = None
    if seed >= 0:
        generator = torch.Generator(device=device).manual_seed(seed)
        print(f"[*] 设定随机种子: {seed}")

    print("\n⚡ 开始执行生成渲染...")
    start_time = time.time()
    result = pipe(
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        generator=generator,
    )
    image = result.images[0]
    elapsed = time.time() - start_time

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    image.save(output_path)
    print(f"\n✅ 图像渲染成功！耗时: {elapsed:.2f} 秒")
    print(f"📁 保存路径: {output_path}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Qwen-Image-2.1 渲染引擎")
    parser.add_argument("--prompt", type=str, required=True, help="图像生成提示词")
    parser.add_argument("--output", type=str, default="/content/workspace/output.png", help="输出图片路径")
    parser.add_argument("--model", type=str, default="Qwen/Qwen-Image-2.1", help="模型 ID")
    parser.add_argument("--width", type=int, default=1280, help="图像宽度 (默认 1280)")
    parser.add_argument("--height", type=int, default=768, help="图像高度 (默认 768)")
    parser.add_argument("--steps", type=int, default=30, help="推理步数 (默认 30)")
    parser.add_argument("--seed", type=int, default=-1, help="随机种子")

    args = parser.parse_args()
    generate(
        prompt=args.prompt,
        output_path=args.output,
        model_id=args.model,
        width=args.width,
        height=args.height,
        steps=args.steps,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
