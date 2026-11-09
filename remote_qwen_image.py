# -*- coding: utf-8 -*-
"""
Colab 远端立绘生成服务引擎 (remote_qwen_image.py)
基于 Qwen-Image-2.1 / Diffusers 官方管道实现：
- 自动将模型缓存重定向到 Google Drive (/content/drive/MyDrive/.cache/huggingface)，实现永久持久化存储，无需重复下载
- 支持立绘专属比例（默认 896x1152 黄金竖屏立绘分辨率）
- 支持原生透明通道（RGBA）与全精度 bfloat16 快速推理
"""

import argparse
import os
import sys
import time
from pathlib import Path

# 确保持久化缓存路径指向已挂载的 Google Drive
drive_cache = "/content/drive/MyDrive/.cache/huggingface"
os.makedirs(drive_cache, exist_ok=True)
os.environ["HF_HOME"] = drive_cache
os.environ["TRANSFORMERS_CACHE"] = drive_cache

import torch


def ensure_environment():
    """检查并确保必要的深度学习依赖库"""
    try:
        import diffusers
        import transformers
        import accelerate
    except ImportError:
        print("[*] 正在安装最新版 Diffusers 与 Transformers 依赖包...")
        os.system("pip install -q accelerate git+https://github.com/huggingface/diffusers.git transformers>=4.48.0")


def generate(
    prompt: str,
    output_path: str,
    model_id: str = "Qwen/Qwen-Image-2.1",
    width: int = 896,
    height: int = 1152,
    steps: int = 35,
    seed: int = -1,
):
    ensure_environment()
    from diffusers import QwenImage21Pipeline

    print("=" * 60)
    print("🎨 Qwen-Image-2.1 角色立绘生成引擎启动")
    print("=" * 60)
    print(f"• 目标模型   : {model_id}")
    print(f"• 缓存目录   : {drive_cache} (Google 云端硬盘永久存储)")
    print(f"• 输出分辨率 : {width} x {height} (立绘竖屏比例)")
    print(f"• 采样步数   : {steps}")
    print(f"• 提示词     : {prompt}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "cuda":
        print("[!] 警告: 未检测到 GPU，生成速度可能会非常缓慢！建议在 Colab 切换为 A100 GPU。")

    # 优先使用 bfloat16 精度（A100 原生支持）
    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16

    print("\n[*] 正在加载 Qwen-Image-2.1 模型管道（若首次运行将自动下载并沉淀至网盘）...")
    pipe = QwenImage21Pipeline.from_pretrained(
        model_id,
        torch_dtype=dtype,
        cache_dir=drive_cache,
    ).to(device)

    generator = None
    if seed >= 0:
        generator = torch.Generator(device=device).manual_seed(seed)
        print(f"[*] 设定随机种子: {seed}")

    print("\n⚡ 开始执行立绘渲染生成...")
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

    # 保存立绘图片（支持带透明通道的 PNG 格式）
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    image.save(output_path)
    print(f"✅ 立绘生成成功！耗时: {elapsed:.2f} 秒")
    print(f"📁 保存路径: {output_path}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Qwen-Image-2.1 立绘生成引擎")
    parser.add_argument("--prompt", type=str, required=True, help="角色立绘生成提示词")
    parser.add_argument("--output", type=str, default="/content/workspace/output_portrait.png", help="输出图片路径")
    parser.add_argument("--model", type=str, default="Qwen/Qwen-Image-2.1", help="HuggingFace 模型 ID")
    parser.add_argument("--width", type=int, default=896, help="图像宽度 (默认 896)")
    parser.add_argument("--height", type=int, default=1152, help="图像高度 (默认 1152)")
    parser.add_argument("--steps", type=int, default=35, help="推理步数 (默认 35)")
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
