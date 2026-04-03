import os
import random
from PIL import Image, ImageDraw, ImageFont

# Pathing
TARGET_DIR = "main_menu/gfx/coat_of_arms/colored_emblems/"
SCRIPT_DIR = "main_menu/common/coat_of_arms/coat_of_arms/"
os.makedirs(TARGET_DIR, exist_ok=True)
os.makedirs(SCRIPT_DIR, exist_ok=True)

# Data Mapping: (Name, Char, Tag, Culture, Color2_RGB)
data = [
    ("qi", "齊", "CQI", "jiaodong_culture", "153 51 51"),
    ("shun", "順", "CSH", "qin_culture", "102 51 0"),
    ("shu", "蜀", "CUN", "shu_culture", "102 0 0"),
    ("wei", "魏", "CWI", "zhongyuan_culture", "64 64 64"),
    ("wu", "吳", "WUU", "wu_culture", "0 102 0"),
    ("qin", "秦", "CQN", "qin_culture", "80 40 0"),
    ("chu", "楚", "CHU", "chu_culture", "153 102 0"),
    ("tang", "唐", "CTA", "huaihai_culture", "120 120 0"),
    ("yan", "燕", "CYN", "yan_culture", "102 102 153"),
    ("jin", "晉", "CJN", "jin_culture", "80 80 120"),
    ("min", "閩", "CMM", "fuzhou_culture", "80 80 120"),
    ("yue", "越", "CYU", "yuehai_culture", "80 80 120"),
    ("miao", "苗", "CMI", "hmong_culture", "80 80 120"),
    ("liang", "梁", "CLN", "liang_culture", "80 80 120"),
    ("ning", "寧", "CNG", "guibei_culture", "80 80 120"),
    ("xi", "西", "CXI", "bai_culture", "80 80 120"),
    ("yi", "夷", "CYI", "yi_culture", "80 80 120"),
    ("huai", "淮", "CAI", "gan_culture", "80 80 120"),
    ("tungning", "鄭", "CTU", "minnan_culture", "80 80 120"),
    ("liao", "遼", "CLY", "jurchen_culture", "80 80 120"),
    ("han", "韓", "CCC", "zhongyuan_culture", "80 80 120"),
    ("zhao", "趙", "CZA", "jin_culture", "80 80 120")
]

# Culture to Border texture mapping
SPECIFIC_BORDERS = {
    "hmong_culture": "ce_border_corners_hmong.dds",
    "yi_culture": "ce_border_corners_yi.dds",
    "jurchen_culture": "ce_border_tungusic.dds",
}

CHINA_POOL = [
    "ce_border_china.dds",
    "ce_border_china_02.dds",
    "ce_border_china_03.dds",
    "ce_border_china_04.dds"
]

FONTS_CONFIG = {
    "seal_primary": "/home/rick/Downloads/BaiZhouZhuanShu.ttf",
    "seal_fallback": "/home/rick/Downloads/ebas927.ttf",
    "calligraphy": "/home/rick/Downloads/NotoSerifTC-VariableFont_wght.ttf"
}

COLOR = (0, 0, 129, 255)
CANVAS_SIZE = (384, 256)

def has_glyph(font, char):
    return font.getmask(char).getbbox() is not None

def get_fitted_font(char, font_path):
    if not os.path.exists(font_path): return None, 0, 0, None
    try:
        base_font = ImageFont.truetype(font_path, 100)
        if not has_glyph(base_font, char): return None, 0, 0, None
    except: return None, 0, 0, None
    font_size = 300
    temp_img = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    temp_draw = ImageDraw.Draw(temp_img)
    while font_size > 10:
        font = ImageFont.truetype(font_path, font_size)
        bbox = temp_draw.textbbox((0, 0), char, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if h <= CANVAS_SIZE[1] * 0.95 and w <= CANVAS_SIZE[0] * 0.95:
            return font, w, h, bbox
        font_size -= 2
    return None, 0, 0, None

def save_character(name, char, font, w, h, bbox, style):
    img = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = (CANVAS_SIZE[0] - w) // 2 - bbox[0]
    y = (CANVAS_SIZE[1] - h) // 2 - bbox[1]
    draw.text((x, y), char, font=font, fill=COLOR)
    filename = f"ce_{name}_{style}_character.dds"
    img.save(os.path.join(TARGET_DIR, filename))
    return filename

def generate():
    coa_entries = []
    for name, char, tag, culture, color2 in data:
        # Image Generation Logic
        seal_font, w, h, bbox = get_fitted_font(char, FONTS_CONFIG["seal_primary"])
        if not seal_font:
            seal_font, w, h, bbox = get_fitted_font(char, FONTS_CONFIG["seal_fallback"])
        
        if seal_font:
            tex_name = save_character(name, char, seal_font, w, h, bbox, "seal")
            
            # Border Selection
            if culture in SPECIFIC_BORDERS:
                border = SPECIFIC_BORDERS[culture]
            else:
                border = random.choice(CHINA_POOL)

            # Paradox Config Construction
            # Logic: If it's Hmong or Yi, both color1 and color2 are black.
            if culture in ["hmong_culture", "yi_culture"]:
                border_colors = "        color1 = black\n        color2 = black"
            else:
                border_colors = "        color1 = black"

            entry = (
                f"{tag} = {{\n"
                f"    pattern = \"pattern_solid.dds\"\n"
                f"    color1 = map_{tag}\n"
                f"    color2 = rgb {{ {color2} }}\n"
                f"    colored_emblem = {{\n"
                f"        texture = \"{border}\"\n"
                f"{border_colors}\n"
                f"        instance = {{ position = {{ 0.500 0.500 }} scale = {{ 1.000 1.000 }} }}\n"
                f"    }}\n"
                f"    colored_emblem = {{\n"
                f"        texture = \"{tex_name}\"\n"
                f"        color1 = black\n"
                f"        instance = {{ position = {{ 0.500 0.500 }} scale = {{ 0.800 0.800 }} }}\n"
                f"    }}\n"
                f"}}\n"
            )
            coa_entries.append(entry)

        # Calligraphy generation
        # cal_font, cw, ch, cbbox = get_fitted_font(char, FONTS_CONFIG["calligraphy"])
        # if cal_font: save_character(name, char, cal_font, cw, ch, cbbox, "calligraphy")

    # Output file
    txt_path = os.path.join(SCRIPT_DIR, "zz_1444_chinese_revolters.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(coa_entries))
    
    print(f"Success. Borders for Hmong and Yi configured with double black colors.")

if __name__ == "__main__":
    generate()