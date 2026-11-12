# -*- coding: utf-8 -*-
"""
Colab 远端图生视频服务引擎 (remote_cogvideox.py)
基于 CogVideoX-5b-I2V / Diffusers 官方管道实现：
- 接收静态分镜图片与运镜提示词，生成电影级 5~6 秒动态视频切片
- 针对 24GB L4 GPU 启用 bfloat16 + model_cpu_offload + vae_tiling
- 输出标准 H.264 MP4 视频，兼容所有主流播放器与剪辑软件
"""

import argparse
import os
import sys
import time
from pathlib import Path
from PIL import Image
import torch

DRIVE_CACHE = "/content/drive/MyDrive/.cache/huggingface"
os.makedirs(DRIVE_CACHE, exist_ok=True)
os.environ["HF_HOME"] = DRIVE_CACHE


def ensure_video_environment():
    """检查并安装视频生成必须的依赖包"""
    try:
        import imageio_ffmpeg
        import torchvision
    except ImportError:
        print("[*] 正在安装视频处理依赖包 (imageio-ffmpeg, torchvision)...")
        os.system("pip install -q imageio-ffmpeg opencv-python-headless torchvision")


def generate_video(
    image_path: str,
    prompt: str,
    output_path: str,
    model_id: str = "THUDM/CogVideoX-5b-I2V",
    width: int = 480,
    height: int = 720,
    num_frames: int = 49,
    steps: int = 35,
    guidance_scale: float = 6.0,
    seed: int = 42,
    fps: int = 8,
):
    ensure_video_environment()
    from diffusers import CogVideoXImageToVideoPipeline
    from diffusers.utils import export_to_video

    print("=" * 60)
    print("🎬 CogVideoX-5B 图生视频生成引擎启动")
    print("=" * 60)
    print(f"• 输入分镜图 : {image_path}")
    print(f"• 运镜提示词 : {prompt}")
    print(f"• 输出视频   : {output_path}")
    print(f"• 视频规格   : {width} x {height} ({num_frames} 帧 / {fps} FPS)")
    print(f"• 推理步数   : {steps} 步 (CFG: {guidance_scale})")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "cuda":
        print("[!] 警告: 未检测到 GPU，视频推理需要 GPU 加速！")

    # 处理输入图像并缩放到模型标准分辨率
    input_image = Image.open(image_path).convert("RGB")
    input_image = input_image.resize((width, height), Image.Resampling.LANCZOS)
    print(f"[*] 分镜图像预处理完成: 已适配目标分辨率 {width}x{height}")

    print(f"\n[*] 正在加载 {model_id} 模型管道至 GPU (bfloat16)...")
    pipe = CogVideoXImageToVideoPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        cache_dir=DRIVE_CACHE,
    )

    print("[*] 启用显存优化保护 (model_cpu_offload + vae_tiling + vae_slicing)...")
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    pipe.vae.enable_slicing()

    generator = torch.Generator(device="cpu").manual_seed(seed) if seed >= 0 else None
    if seed >= 0:
        print(f"[*] 设定随机种子: {seed}")

    print("\n⚡ 开始执行图生视频推理 (每秒约 1~2 帧，请稍候)...")
    start_time = time.time()
    video_frames = pipe(
        prompt=prompt,
        image=input_image,
        num_videos_per_prompt=1,
        num_inference_steps=steps,
        num_frames=num_frames,
        guidance_scale=guidance_scale,
        generator=generator,
    ).frames[0]
    elapsed = time.time() - start_time
    print(f"[+] 视频张量生成完成！总耗时: {elapsed:.2f} 秒")

    # 输出目录准备
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    raw_mp4 = output_path.replace(".mp4", "_raw.mp4")
    export_to_video(video_frames, raw_mp4, fps=fps)
    print(f"[*] 原始视频已暂存: {raw_mp4}")

    # 使用 ffmpeg 转码为标准 H.264 16fps 丝滑视频
    print(f"[*] 正在执行 H.264 编码与倍帧平滑优化...")
    convert_cmd = (
        f"ffmpeg -y -loglevel error -i '{raw_mp4}' "
        f"-vf 'minterpolate=fps=16:mi_mode=blend' "
        f"-c:v libx264 -pix_fmt yuv420p -crf 19 '{output_path}'"
    )
    res = os.system(convert_cmd)
    if res != 0 or not os.path.exists(output_path):
        # 回退为直接转封装
        fallback_cmd = f"ffmpeg -y -loglevel error -i '{raw_mp4}' -c:v libx264 -pix_fmt yuv420p '{output_path}'"
        os.system(fallback_cmd)

    # 清理临时文件
    if os.path.exists(raw_mp4) and os.path.exists(output_path):
        try:
            os.remove(raw_mp4)
        except Exception:
            pass

    file_size_mb = os.path.getsize(output_path) / 1024**2
    print(f"\n✅ 动态视频渲染成功！耗时: {elapsed:.2f} 秒")
    print(f"📁 视频文件: {output_path} ({file_size_mb:.2f} MB)")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="CogVideoX-5B 图生视频生成引擎")
    parser.add_argument("--image", type=str, required=True, help="输入分镜图片路径")
    parser.add_argument("--prompt", type=str, required=True, help="运镜与动作提示词")
    parser.add_argument("--output", type=str, default="/content/workspace/output_video.mp4", help="输出视频路径")
    parser.add_argument("--width", type=int, default=480, help="视频宽度 (默认 480)")
    parser.add_argument("--height", type=int, default=720, help="视频高度 (默认 720)")
    parser.add_argument("--frames", type=int, default=49, help="生成帧数 (默认 49 帧)")
    parser.add_argument("--steps", type=int, default=30, help="推理步数 (默认 30 步)")
    parser.add_argument("--fps", type=int, default=8, help="原生基础帧率 (默认 8 FPS)")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")

    args = parser.parse_args()
    generate_video(
        image_path=args.image,
        prompt=args.prompt,
        output_path=args.output,
        width=args.width,
        height=args.height,
        num_frames=args.frames,
        steps=args.steps,
        fps=args.fps,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
