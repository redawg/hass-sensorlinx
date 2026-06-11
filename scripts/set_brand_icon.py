#!/usr/bin/env python3
"""Replace brand icons with the HBX logo."""
from PIL import Image
import os

SRC = r"C:\Users\andre\.cursor\projects\c-Users-andre-Projects-hbx-sensorlinx-ha\assets\c__Users_andre_AppData_Roaming_Cursor_User_workspaceStorage_empty-window_images_image-b54412a7-b352-466e-8317-0088f17266e8.png"
BRAND_DIR = r"C:\Users\andre\Projects\hbx-sensorlinx-ha\custom_components\sensorlinx\brand"

img = Image.open(SRC).convert("RGBA")
print(f"Source: {img.size}")

# Resize to 256x256 for icon.png (use LANCZOS for high quality)
icon = img.resize((256, 256), Image.LANCZOS)
icon.save(os.path.join(BRAND_DIR, "icon.png"))
print("Saved icon.png (256x256)")

# Resize to 512x512 for icon@2x.png
icon2x = img.resize((512, 512), Image.LANCZOS)
icon2x.save(os.path.join(BRAND_DIR, "icon@2x.png"))
print("Saved icon@2x.png (512x512)")

# Also save as logo.png (used by some HACS themes)
logo = img.resize((256, 256), Image.LANCZOS)
logo.save(os.path.join(BRAND_DIR, "logo.png"))
print("Saved logo.png (256x256)")

logo2x = img.resize((512, 512), Image.LANCZOS)
logo2x.save(os.path.join(BRAND_DIR, "logo@2x.png"))
print("Saved logo@2x.png (512x512)")

print("\nDone! Brand icons updated to HBX logo.")
