from PIL import Image, ImageDraw, ImageFont
import os

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
GREEN = (11, 59, 44)      # deep green
EMERALD = (52, 211, 153)  # emerald
AMBER = (245, 165, 36)    # amber


def make(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.24), fill=GREEN)
    fs = int(size * 0.52)
    font = ImageFont.truetype(FONT, fs)
    txt = "s"
    bbox = d.textbbox((0, 0), txt, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - tw) / 2 - bbox[0],
            (size - th) / 2 - bbox[1] - size * 0.02),
           txt, font=font, fill=EMERALD)
    r = int(size * 0.075)
    cx, cy = int(size * 0.70), int(size * 0.30)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=AMBER)
    return img


base = "/home/hatch/workspace/sauda/mobile/android/app/src/main/res"
for name, px in [("mipmap-mdpi", 48), ("mipmap-hdpi", 72),
                 ("mipmap-xhdpi", 96), ("mipmap-xxhdpi", 144),
                 ("mipmap-xxxhdpi", 192)]:
    os.makedirs(f"{base}/{name}", exist_ok=True)
    make(px).save(f"{base}/{name}/ic_launcher.png")
    print("wrote", name, px)
