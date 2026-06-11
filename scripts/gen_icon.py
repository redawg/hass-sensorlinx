#!/usr/bin/env python3
"""Generate brand icon for SensorLinx integration (256x256 PNG)."""
from PIL import Image, ImageDraw, ImageFont
import math
import os

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Background: rounded rectangle with HBX blue gradient feel
bg_color = (41, 98, 168)  # HBX-style blue
draw.rounded_rectangle(
    [(16, 16), (240, 240)],
    radius=40,
    fill=bg_color,
)

# Draw radiant floor heat waves (3 curved lines from bottom)
wave_color = (255, 140, 40)  # orange heat
for i, y_base in enumerate([180, 155, 130]):
    points = []
    amplitude = 8 + i * 2
    for x in range(60, 197):
        y = y_base - amplitude * math.sin((x - 60) * math.pi * 2 / 136)
        points.append((x, y))
    draw.line(points, fill=wave_color, width=4)

# Draw a simple thermostat circle in the upper portion
center_x, center_y = 128, 95
radius = 35
draw.ellipse(
    [(center_x - radius, center_y - radius),
     (center_x + radius, center_y + radius)],
    outline=(255, 255, 255), width=4,
)

# Temperature indicator inside circle
inner_r = 20
draw.ellipse(
    [(center_x - inner_r, center_y - inner_r),
     (center_x + inner_r, center_y + inner_r)],
    fill=(255, 140, 40),
)

# Small "S" or dot for SensorLinx brand mark
draw.ellipse(
    [(center_x - 6, center_y - 6),
     (center_x + 6, center_y + 6)],
    fill=(255, 255, 255),
)

out_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components", "sensorlinx", "brand", "icon.png"
)
img.save(out_path, "PNG", optimize=True)
print(f"Saved: {out_path} ({os.path.getsize(out_path)} bytes)")

# Also generate icon@2x.png (512x512)
img_2x = img.resize((512, 512), Image.LANCZOS)
out_2x = out_path.replace("icon.png", "icon@2x.png")
img_2x.save(out_2x, "PNG", optimize=True)
print(f"Saved: {out_2x} ({os.path.getsize(out_2x)} bytes)")
