import os
import sys
from colab_executor import ColabExecutor

def main():
    e = ColabExecutor(hostname="trend-gloves-zus-clearly.trycloudflare.com")
    prompt = (
        "Elden Ring dark fantasy style, majestic ancient dragon Lansseax with stone wings and red lightning crackling, "
        "standing on crumbling gothic cathedral ruins of Farum Azula, giant luminous Erdtree glowing gold in the background sky, "
        "atmospheric fog, floating debris, cinematic lighting, epic composition, ultra detailed 8k"
    )
    remote_out = "/content/workspace/elden_ring_dragon.png"
    local_out_dir = os.path.abspath("./portraits")
    os.makedirs(local_out_dir, exist_ok=True)
    local_out_file = os.path.join(local_out_dir, "elden_ring_dragon.png")

    with e:
        cmd = (
            f'python3 /content/workspace/remote_qwen_image.py '
            f'--prompt "{prompt}" '
            f'--output "{remote_out}" '
            f'--width 1280 --height 768 --steps 30'
        )
        print("[*] 正在向 Colab 下发渲染任务...")
        res = e.run_command(cmd)
        print(res['stdout'])
        if res['stderr']:
            print("STDERR:\n", res['stderr'])

        print(f"[*] 正在拉取生成的图片至本地: {local_out_file}...")
        e.download(remote_out, local_out_file)
        print(f"✅ 图片已成功下载至本地: {local_out_file}")

if __name__ == '__main__':
    main()
