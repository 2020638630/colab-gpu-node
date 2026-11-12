import os
import sys
from colab_executor import ColabExecutor

def main():
    hostname = "host-optional-certainly-icq.trycloudflare.com"
    e = ColabExecutor(hostname=hostname)

    remote_img = "/content/workspace/shot1_gemini_awakening_v2.png"
    remote_video = "/content/workspace/shot1_awakening.mp4"
    local_video_dir = os.path.abspath("./videos")
    os.makedirs(local_video_dir, exist_ok=True)
    local_video_file = os.path.join(local_video_dir, "shot1_awakening.mp4")

    # 镜头 1 动态运镜指令：镜头缓慢平稳拉远，猫耳灵动微颤，发丝与金色赐福微尘飘散浮动，丁达尔光束流转
    motion_prompt = (
        "Cinematic slow camera zoom out, cute anime catgirl waking up from deep sleep, "
        "gentle subtle breathing motion, purple cat ears twitching slightly with curiosity, "
        "long purple and cyan hair strands floating softly in atmospheric draft, "
        "golden motes of grace light gently swirling in midair, volumetric sunbeams shifting slowly through cathedral cracks, "
        "smooth high quality cinematic animation, 4k master"
    )

    with e:
        cmd = (
            f'python3 /content/workspace/remote_cogvideox.py '
            f'--image "{remote_img}" '
            f'--prompt "{motion_prompt}" '
            f'--output "{remote_video}" '
            f'--width 480 --height 720 --frames 49 --steps 30'
        )
        print("[*] 正在向 Colab 下发【首部处女作：镜头 1 动态视频渲染】任务 (CogVideoX-5B)...")
        # 视频生成预计耗时 3~6 分钟，设置 1200 秒高宽容度超时
        res = e.run_command(cmd, timeout=1200)
        print(res['stdout'])
        if res['stderr']:
            print("STDERR:\n", res['stderr'])

        print(f"[*] 正在将生成的 MP4 视频拉取至本地: {local_video_file}...")
        e.download(remote_video, local_video_file)
        print(f"🎉 处女作视频已成功下载至本地: {local_video_file}")

if __name__ == '__main__':
    main()
