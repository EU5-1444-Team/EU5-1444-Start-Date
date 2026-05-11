from PIL import Image, ImageDraw, ImageFont
import subprocess
import os

# =========================================================
# Output directory
# =========================================================
OUTPUT_DIR = "/home/rick/Paradox/EU5-1444-Start-Date/main_menu/gfx/coat_of_arms/colored_emblems"

# =========================================================
# Image settings
# =========================================================
WIDTH = 384
HEIGHT = 256

TEXT_COLOR = (0, 0, 129, 255)
BACKGROUND = (0, 0, 0, 0)

FONT_SIZE = 72

# =========================================================
# Dynasty names ONLY
# =========================================================
IMAGES = {
    "ce_ilyas_shahi.dds": "الیاس شاهی",
    "ce_muzaffarid.dds": "مظفری",
    "ce_sayyid.dds": "سید",
    "ce_lodi.dds": "لودی",
    "ce_sharqi.dds": "شرقی",
}

# =========================================================
# Nastaliq fonts
# =========================================================
FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf",
    "/usr/share/fonts/truetype/awami/AwamiNastaliq-Regular.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Urdu_Typesetting.ttf",
    "NotoNastaliqUrdu-Regular.ttf",
    "Jameel Noori Nastaleeq.ttf",
]

# =========================================================
# Find installed font
# =========================================================
font_path = None

for path in FONT_PATHS:
    if os.path.exists(path):
        font_path = path
        break

if not font_path:
    raise FileNotFoundError(
        "No Nastaliq font found."
    )

font = ImageFont.truetype(font_path, FONT_SIZE)

# =========================================================
# Ensure output directory exists
# =========================================================
os.makedirs(OUTPUT_DIR, exist_ok=True)

# =========================================================
# Generate images
# =========================================================
for filename, text in IMAGES.items():

    img = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(img)

    # Measure true glyph bounds
    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
        direction="rtl"
    )

    left, top, right, bottom = bbox

    # Perfect visual centering
    x = (WIDTH / 2) - ((left + right) / 2)
    y = (HEIGHT / 2) - ((top + bottom) / 2)

    # Draw text
    draw.text(
        (x, y),
        text,
        font=font,
        fill=TEXT_COLOR,
        direction="rtl"
    )

    # Temp PNG
    temp_png = f"/tmp/{filename.replace('.dds', '.png')}"

    img.save(temp_png)

    # DDS output
    output_dds = os.path.join(OUTPUT_DIR, filename)

    subprocess.run([
        "magick",
        temp_png,
        "-define", "dds:compression=dxt5",
        output_dds
    ], check=True)

    print(f"Created: {output_dds}")

print("\nAll dynasty emblems generated successfully.")